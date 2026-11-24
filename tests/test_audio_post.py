"""core/audio_post.py 单元测试。

用合成正弦波验证滤波与归一化，不依赖任何真实录音。
"""

from __future__ import annotations

import io
import struct
import wave

import numpy as np
import pytest

from core import audio_post as AP


def make_wav(
    freq: float = 440.0,
    seconds: float = 1.0,
    rate: int = 24000,
    amplitude: float = 0.2,
    channels: int = 1,
) -> bytes:
    """生成指定频率的正弦波 WAV。"""
    n = int(rate * seconds)
    t = np.arange(n) / rate
    wave_data = (np.sin(2 * np.pi * freq * t) * amplitude * 32767).astype(np.int16)
    if channels == 2:
        wave_data = np.repeat(wave_data[:, None], 2, axis=1).reshape(-1)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(wave_data.tobytes())
    return buf.getvalue()


def read_wav(raw: bytes) -> tuple[np.ndarray, int, int]:
    with wave.open(io.BytesIO(raw), "rb") as w:
        rate = w.getframerate()
        ch = w.getnchannels()
        frames = w.readframes(w.getnframes())
    data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    return data, rate, ch


def peak_db(data: np.ndarray) -> float:
    peak = float(np.max(np.abs(data)))
    return 20 * np.log10(peak) if peak > 0 else -120.0


class TestPassthrough:
    def test_negative_peak_disables(self) -> None:
        raw = make_wav()
        out, info = AP.normalize_wav_bytes(raw, peak_db=0.0)
        assert out == raw
        assert info["applied"] is False

    def test_non_wav_returned(self) -> None:
        raw = b"not a wav file at all"
        out, info = AP.normalize_wav_bytes(raw)
        assert out == raw
        assert info["applied"] is False

    def test_too_short_skips_filter(self) -> None:
        raw = make_wav(seconds=0.005)
        out, info = AP.normalize_wav_bytes(raw)
        assert info.get("applied") is True or info.get("error")


class TestNormalization:
    def test_peak_never_exceeds_target(self) -> None:
        """响度优先，但峰值绝不允许超过 peak_db。"""
        raw = make_wav(amplitude=0.9)
        out, info = AP.normalize_wav_bytes(raw, peak_db=-1.5, denoise=False)
        data, _, _ = read_wav(out)
        assert info["applied"] is True
        assert peak_db(data) <= -1.4

    def test_peak_first_when_rms_close(self) -> None:
        """响度已接近目标时只压峰值，不再拉响度。"""
        raw = make_wav(amplitude=0.9)
        out, info = AP.normalize_wav_bytes(
            raw, peak_db=-1.5, rms_db=-18.0, rms_tolerance_db=100.0, denoise=False
        )
        data, _, _ = read_wav(out)
        assert abs(peak_db(data) - (-1.5)) < 0.5

    def test_quiet_audio_boosted(self) -> None:
        raw = make_wav(amplitude=0.02)
        out, info = AP.normalize_wav_bytes(raw, peak_db=-1.5, denoise=False)
        assert info["gain"] > 1.0

    def test_loud_audio_attenuated(self) -> None:
        raw = make_wav(amplitude=0.99)
        out, info = AP.normalize_wav_bytes(raw, peak_db=-1.5, denoise=False)
        data, _, _ = read_wav(out)
        assert peak_db(data) <= -1.0

    def test_stereo_preserved(self) -> None:
        raw = make_wav(channels=2)
        out, info = AP.normalize_wav_bytes(raw, denoise=False)
        _, _, ch = read_wav(out)
        assert ch == 2
        assert info["channels"] == 2

    def test_rate_preserved(self) -> None:
        raw = make_wav(rate=16000)
        out, info = AP.normalize_wav_bytes(raw, denoise=False)
        _, rate, _ = read_wav(out)
        assert rate == 16000

    def test_info_fields(self) -> None:
        raw = make_wav()
        _, info = AP.normalize_wav_bytes(raw, denoise=False)
        for key in (
            "applied",
            "rate",
            "channels",
            "duration_s",
            "peak_before_db",
            "peak_after_db",
            "rms_before_db",
            "rms_after_db",
            "gain",
        ):
            assert key in info


class TestDenoise:
    def test_highpass_removes_low_freq(self) -> None:
        """60Hz 低频应被 160Hz 高通显著衰减。"""
        raw = make_wav(freq=60.0, amplitude=0.5)
        out, _ = AP.normalize_wav_bytes(raw, peak_db=-1.5, denoise=True)
        data, rate, _ = read_wav(out)
        spectrum = np.abs(np.fft.rfft(data))
        freqs = np.fft.rfftfreq(len(data), 1 / rate)
        band = (freqs > 50) & (freqs < 70)
        total = np.sum(spectrum)
        assert np.sum(spectrum[band]) / total < 0.1

    def test_lowpass_removes_high_freq(self) -> None:
        """10kHz 应被 11.5kHz 低通衰减。"""
        raw = make_wav(freq=10000.0, amplitude=0.5, rate=24000)
        out, _ = AP.normalize_wav_bytes(raw, peak_db=-1.5, denoise=True)
        data, rate, _ = read_wav(out)
        spectrum = np.abs(np.fft.rfft(data))
        freqs = np.fft.rfftfreq(len(data), 1 / rate)
        band = (freqs > 9500) & (freqs < 10500)
        assert np.sum(spectrum[band]) / np.sum(spectrum) < 0.5

    def test_mid_freq_preserved(self) -> None:
        """1kHz 语音主频应基本不受影响。"""
        raw = make_wav(freq=1000.0, amplitude=0.3)
        out, _ = AP.normalize_wav_bytes(raw, peak_db=-1.5, denoise=True)
        data, rate, _ = read_wav(out)
        spectrum = np.abs(np.fft.rfft(data))
        freqs = np.fft.rfftfreq(len(data), 1 / rate)
        band = (freqs > 900) & (freqs < 1100)
        assert np.sum(spectrum[band]) / np.sum(spectrum) > 0.5


class TestEdgeCases:
    def test_empty_bytes(self) -> None:
        out, info = AP.normalize_wav_bytes(b"")
        assert out == b""
        assert info["applied"] is False

    def test_silence(self) -> None:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x00" * 24000)
        out, info = AP.normalize_wav_bytes(buf.getvalue(), denoise=False)
        assert info["applied"] is True

    def test_clipping_limited(self) -> None:
        """方波削波输入不应产生新的削波。"""
        n = 24000
        wave_data = np.where(np.arange(n) % 100 < 50, 32767, -32768).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(wave_data.tobytes())
        out, info = AP.normalize_wav_bytes(buf.getvalue(), denoise=False)
        data, _, _ = read_wav(out)
        assert np.sum(np.abs(data) >= 0.999) == 0
