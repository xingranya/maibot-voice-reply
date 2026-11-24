"""口音守卫：检测合成音频的「上扬尾音 / 台湾腔」风险，供插件做自动重试。

指标（与 /tmp/accent_metrics.py 一致）：
- tail_slope_st：每个有声段末尾 30% 的 F0 半音斜率中位数，正数=尾音往上翘
- tail_rising_ratio：尾音斜率 > +2 st/s 的有声段占比
- f0_range_st：F0 的 p90-p10 音域（半音），太窄显得嗲
- risk = max(0, tail_slope_st)*3 + tail_rising_ratio*2 + max(0, 8-f0_range_st)*0.5
"""

from __future__ import annotations

import io
import math
import wave
from typing import Any, Dict, Optional

import numpy as np

_RISING_THRESHOLD_ST = 2.0
_MIN_VOICED_FRAMES = 8


def _read_pcm(raw: bytes) -> Optional[tuple]:
    try:
        with wave.open(io.BytesIO(raw)) as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                return None
            sr = w.getframerate()
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    except Exception:
        return None
    if data.size < sr // 10:
        return None
    return sr, data.astype(np.float32) / 32768.0


def _track_f0(samples: "np.ndarray", sr: int) -> tuple:
    frame = int(sr * 0.04)
    hop = int(sr * 0.02)
    lo_lag = int(sr / 400)
    hi_lag = int(sr / 70)
    f0s = []
    times = []
    for start in range(0, max(0, len(samples) - frame), hop):
        seg = samples[start:start + frame]
        if float(np.sqrt(np.mean(seg ** 2))) < 0.01:
            continue
        seg = seg - seg.mean()
        corr = np.correlate(seg, seg, mode="full")[frame - 1:]
        if hi_lag >= len(corr):
            continue
        peak = lo_lag + int(np.argmax(corr[lo_lag:hi_lag]))
        if corr[peak] <= 0.3 * corr[0]:
            continue
        f0s.append(sr / peak)
        times.append(start / sr)
    return f0s, times


def analyze(raw: bytes) -> Optional[Dict[str, Any]]:
    parsed = _read_pcm(raw)
    if not parsed:
        return None
    sr, samples = parsed
    f0s, times = _track_f0(samples, sr)
    if len(f0s) < _MIN_VOICED_FRAMES:
        return None

    semitones = [12.0 * math.log2(f / 100.0) for f in f0s]

    segments = []
    current = [0]
    for idx in range(1, len(times)):
        if times[idx] - times[idx - 1] > 0.15:
            segments.append(current)
            current = []
        current.append(idx)
    segments.append(current)

    slopes = []
    for seg in segments:
        if len(seg) < 5:
            continue
        tail = seg[-max(3, int(len(seg) * 0.3)):]
        if len(tail) < 3:
            continue
        xs = np.array([times[i] for i in tail])
        ys = np.array([semitones[i] for i in tail])
        if xs[-1] - xs[0] < 0.05:
            continue
        slopes.append(float(np.polyfit(xs, ys, 1)[0]))

    tail_slope = float(np.median(slopes)) if slopes else 0.0
    rising = sum(1 for s in slopes if s > _RISING_THRESHOLD_ST) / len(slopes) if slopes else 0.0
    f0_range = float(np.percentile(semitones, 90) - np.percentile(semitones, 10))

    risk = (
        max(0.0, tail_slope) * 3.0
        + rising * 2.0
        + max(0.0, 8.0 - f0_range) * 0.5
    )
    return {
        "risk": round(risk, 2),
        "tail_slope_st": round(tail_slope, 2),
        "tail_rising_ratio": round(rising, 2),
        "f0_range_st": round(f0_range, 2),
        "f0_med": round(float(np.median(f0s)), 1),
        "voiced_segments": len(slopes),
    }
