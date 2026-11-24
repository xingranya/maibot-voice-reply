"""core/accent_guard.py 单元测试。"""

from __future__ import annotations

import io
import wave

import numpy as np

from core import accent_guard as AG


def wav_bytes(
    segments: list[tuple[float, float]],
    rate: int = 24000,
    amplitude: float = 0.3,
    gap_s: float = 0.25,
) -> bytes:
    """把若干 (频率Hz, 时长s) 段拼成 WAV，段间插静音。"""
    chunks: list[np.ndarray] = []
    for freq, dur in segments:
        n = int(rate * dur)
        t = np.arange(n) / rate
        chunks.append(np.sin(2 * np.pi * freq * t) * amplitude)
        chunks.append(np.zeros(int(rate * gap_s)))
    data = np.concatenate(chunks)
    pcm = (data * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class TestAnalyze:
    def test_too_short_returns_none(self) -> None:
        assert AG.analyze(wav_bytes([(220, 0.05)], gap_s=0.0)) is None

    def test_not_wav_returns_none(self) -> None:
        assert AG.analyze(b"garbage") is None

    def test_empty_returns_none(self) -> None:
        assert AG.analyze(b"") is None

    def test_flat_tone_has_fields(self) -> None:
        result = AG.analyze(wav_bytes([(220, 1.0), (220, 1.0), (220, 1.0)]))
        assert result is not None
        for key in ("risk", "f0_med", "f0_range_st", "tail_slope_st", "tail_rising_ratio"):
            assert key in result

    def test_flat_tone_low_range(self) -> None:
        """同一个频率反复，音域应接近 0。"""
        result = AG.analyze(wav_bytes([(220, 0.8)] * 4))
        assert result is not None
        assert result["f0_range_st"] < 3.0

    def test_wide_range_detected(self) -> None:
        """频率跨度大时音域应显著变大。"""
        segs = [(150, 0.6), (300, 0.6), (180, 0.6), (400, 0.6)]
        result = AG.analyze(wav_bytes(segs))
        assert result is not None
        assert result["f0_range_st"] > 3.0

    def test_risk_is_non_negative(self) -> None:
        result = AG.analyze(wav_bytes([(220, 1.0)] * 3))
        assert result is not None
        assert result["risk"] >= 0.0

    def test_stereo_rejected(self) -> None:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(np.zeros(24000 * 2, dtype=np.int16).tobytes())
        assert AG.analyze(buf.getvalue()) is None


def chirp_wav(f0_start: float, f0_end: float, seconds: float = 2.0, rate: int = 24000) -> bytes:
    """生成频率从 f0_start 连续滑到 f0_end 的正弦波，用于测句尾斜率。"""
    n = int(rate * seconds)
    t = np.arange(n) / rate
    freq = np.linspace(f0_start, f0_end, n)
    phase = 2 * np.pi * np.cumsum(freq) / rate
    data = np.sin(phase) * 0.3
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((data * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


class TestRiskFormula:
    def test_rising_tail_slope_positive(self) -> None:
        """频率连续上扬时尾斜率应为正。"""
        result = AG.analyze(chirp_wav(180, 320))
        assert result is not None
        assert result["tail_slope_st"] > 0

    def test_falling_tail_slope_negative(self) -> None:
        """频率连续下沉时尾斜率应为负。"""
        result = AG.analyze(chirp_wav(320, 180))
        assert result is not None
        assert result["tail_slope_st"] < 0

    def test_rising_risk_higher_than_falling(self) -> None:
        rise = AG.analyze(chirp_wav(180, 320))
        fall = AG.analyze(chirp_wav(320, 180))
        assert rise is not None and fall is not None
        assert rise["risk"] > fall["risk"]
