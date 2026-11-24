"""WAV 音频后处理：滤波降噪 + 峰值限制 + 响度归一化。

容器内没有 ffmpeg / pydub，但有 numpy + scipy，因此直接操作 16-bit PCM WAV。

处理链（按顺序）：
1. 高通 160Hz 四阶 Butterworth —— 去掉喷麦低频爆破
2. 高频搁架 6kHz -6dB —— 压下声码器产生的电流音（6-12kHz 宽带噪声）
3. 低通 11.5kHz 四阶 —— 砍掉 Nyquist 附近的毛刺
4. 峰值限制 + 响度归一化（tanh 软限幅，不引入新失真）
"""

import io
import wave

import numpy as np
from scipy import signal

# ---- 滤波参数 ----
HIGHPASS_HZ = 160.0
HIGHPASS_ORDER = 4
HIGHPASS_Q = 0.7071067811865476  # Butterworth
LOWSHELF_HZ = 6000.0
LOWSHELF_GAIN_DB = -6.0
LOWSHELF_Q = 0.7071067811865476
LOWPASS_HZ = 11500.0
LOWPASS_ORDER = 4

# 太短的音频不做滤波（避免边缘瞬态），直接只做增益
MIN_FILTER_SAMPLES = 480  # 20ms @ 24kHz


def _db_to_lin(db: float) -> float:
    return 10.0 ** (db / 20.0)


def _lin_to_db(value: float) -> float:
    return 20.0 * float(np.log10(max(value, 1e-9)))


def _highshelf_coeffs(rate: int, freq: float, gain_db: float, q: float):
    """二阶高频搁架（RBJ cookbook）。gain_db 为负则衰减高频。"""
    a_amp = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * freq / rate
    alpha = np.sin(w0) / (2.0 * q)
    cos_w0 = np.cos(w0)
    two_sqrt_a_alpha = 2.0 * np.sqrt(a_amp) * alpha
    b = np.array([
        a_amp * ((a_amp + 1.0) + (a_amp - 1.0) * cos_w0 + two_sqrt_a_alpha),
        -2.0 * a_amp * ((a_amp - 1.0) + (a_amp + 1.0) * cos_w0),
        a_amp * ((a_amp + 1.0) + (a_amp - 1.0) * cos_w0 - two_sqrt_a_alpha),
    ])
    a = np.array([
        (a_amp + 1.0) - (a_amp - 1.0) * cos_w0 + two_sqrt_a_alpha,
        2.0 * ((a_amp - 1.0) - (a_amp + 1.0) * cos_w0),
        (a_amp + 1.0) - (a_amp - 1.0) * cos_w0 - two_sqrt_a_alpha,
    ])
    return b, a


def _apply_filters(pcm: np.ndarray, rate: int) -> np.ndarray:
    """高通 + 高频搁架 + 低通。全部零相位（sosfiltfilt / filtfilt），不留相位失真。"""
    if pcm.size < MIN_FILTER_SAMPLES:
        return pcm
    nyq = rate / 2.0
    out = pcm
    # 1. 高通：去喷麦
    if HIGHPASS_HZ < nyq * 0.9:
        sos = signal.butter(HIGHPASS_ORDER, HIGHPASS_HZ / nyq, btype="highpass", output="sos")
        out = signal.sosfiltfilt(sos, out)
    # 2. 高频搁架：压电流音
    if LOWSHELF_GAIN_DB != 0.0 and LOWSHELF_HZ < nyq * 0.9:
        b, a = _highshelf_coeffs(rate, LOWSHELF_HZ, LOWSHELF_GAIN_DB, LOWSHELF_Q)
        out = signal.filtfilt(b, a, out)
    # 3. 低通：砍毛刺
    if LOWPASS_HZ < nyq * 0.95:
        sos = signal.butter(LOWPASS_ORDER, LOWPASS_HZ / nyq, btype="lowpass", output="sos")
        out = signal.sosfiltfilt(sos, out)
    return out


def normalize_wav_bytes(
    raw: bytes,
    peak_db: float = -1.5,
    rms_db: float = -18.0,
    rms_tolerance_db: float = 1.5,
    denoise: bool = True,
) -> tuple[bytes, dict]:
    """滤波 + 峰值压到 peak_db 以下 + 响度拉到 rms_db 附近。

    返回 (处理后的 WAV 字节, 统计信息)。无法解析或格式不支持时原样返回。
    """
    info: dict = {"applied": False}

    # peak_db >= 0 表示显式关闭后处理
    if peak_db >= 0:
        info["reason"] = "peak_db >= 0，后处理已关闭"
        return raw, info

    try:
        with wave.open(io.BytesIO(raw), "rb") as src:
            channels = src.getnchannels()
            width = src.getsampwidth()
            rate = src.getframerate()
            frames = src.getnframes()
            data = src.readframes(frames)
    except Exception as exc:
        info["error"] = f"not a readable wav: {exc}"
        return raw, info

    if width != 2 or not data:
        info["error"] = f"unsupported format: sampwidth={width}, bytes={len(data)}"
        return raw, info

    pcm = np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0
    if pcm.size == 0:
        info["error"] = "empty pcm"
        return raw, info

    info["rate"] = rate
    info["channels"] = channels
    info["duration_s"] = round(pcm.size / max(channels, 1) / rate, 3)

    # ---- 滤波（按声道分别处理）----
    if denoise and channels > 0:
        try:
            if channels == 1:
                pcm = _apply_filters(pcm, rate)
            else:
                shaped = pcm.reshape(-1, channels)
                for ch in range(channels):
                    shaped[:, ch] = _apply_filters(shaped[:, ch], rate)
                pcm = shaped.reshape(-1)
            info["denoised"] = True
        except Exception as exc:  # 滤波失败不影响后续增益
            info["denoise_error"] = f"{type(exc).__name__}: {exc}"

    peak_before = float(np.max(np.abs(pcm)))
    rms_before = float(np.sqrt(np.mean(pcm ** 2)))
    peak_limit = _db_to_lin(peak_db)
    rms_target = _db_to_lin(rms_db)

    gain = 1.0
    if rms_before > 0 and abs(_lin_to_db(rms_before) - rms_db) > rms_tolerance_db:
        gain = rms_target / rms_before
    # 响度拉起来之后峰值可能越界，这时以峰值为准
    if peak_before > 0 and peak_before * gain > peak_limit:
        gain = peak_limit / peak_before

    out = pcm * gain

    # 软限幅：膝盖以上用 tanh 平滑压入 [knee, peak_limit]
    knee = 0.95
    over = np.abs(out) > knee
    if bool(np.any(over)):
        sign = np.sign(out[over])
        excess = np.abs(out[over]) - knee
        span = max(peak_limit - knee, 1e-6)
        out[over] = sign * (knee + span * np.tanh(excess / span))

    out = np.clip(out, -1.0, 1.0)
    ints = np.rint(out * 32767.0).astype("<i2")

    buf = io.BytesIO()
    with wave.open(buf, "wb") as dst:
        dst.setnchannels(channels)
        dst.setsampwidth(2)
        dst.setframerate(rate)
        dst.writeframes(ints.tobytes())

    info.update(
        applied=True,
        peak_before_db=round(_lin_to_db(peak_before), 2),
        peak_after_db=round(_lin_to_db(float(np.max(np.abs(out)))), 2),
        rms_before_db=round(_lin_to_db(rms_before), 2),
        rms_after_db=round(_lin_to_db(float(np.sqrt(np.mean(out ** 2)))), 2),
        gain=round(gain, 3),
    )
    return buf.getvalue(), info
