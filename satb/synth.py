"""
4성부(SATB)를 실제 소리(WAV)로 합성한다.  외부 신디사이저/사운드폰트 불필요.

각 음을 몇 개의 배음(harmonics)을 더한 파형 + ADSR 엔벨로프로 만들고,
네 성부를 섞어 하나의 스테레오 WAV로 기록한다.
numpy 가 있으면 사용하고, 없으면 순수 파이썬으로 대체한다.
"""

from __future__ import annotations

import math
import struct
import wave
from typing import Dict, List, Optional

try:
    import numpy as np
    _HAVE_NUMPY = True
except Exception:  # pragma: no cover
    _HAVE_NUMPY = False

SAMPLE_RATE = 44100

# 성부별 음색(배음 세기)과 좌우 정위(pan) — 합창처럼 살짝 벌려 배치.
_VOICE_TIMBRE = {
    #             (배음 진폭들),                        게인,  pan(-1좌 ~ +1우)
    "soprano": ([1.0, 0.35, 0.18, 0.08],               0.9,  +0.4),
    "alto":    ([1.0, 0.28, 0.12, 0.05],               0.8,  +0.15),
    "tenor":   ([1.0, 0.30, 0.16, 0.07],               0.8,  -0.15),
    "bass":    ([1.0, 0.45, 0.22, 0.10],               0.95, -0.4),
}


def midi_to_hz(m: int) -> float:
    return 440.0 * (2.0 ** ((m - 69) / 12.0))


# --------------------------------------------------------------------------- #
# 파형 + 엔벨로프
# --------------------------------------------------------------------------- #
def _adsr(n_samples: int, sr: int) -> "np.ndarray":
    a = int(0.010 * sr)                     # attack
    d = int(0.040 * sr)                     # decay
    r = int(0.060 * sr)                     # release
    sustain = 0.75
    env = np.ones(n_samples, dtype=np.float64)
    a = min(a, n_samples)
    if a > 0:
        env[:a] = np.linspace(0.0, 1.0, a)
    d = min(d, max(0, n_samples - a))
    if d > 0:
        env[a:a + d] = np.linspace(1.0, sustain, d)
    if a + d < n_samples:
        env[a + d:] = sustain
    r = min(r, n_samples)
    if r > 0:
        env[-r:] *= np.linspace(1.0, 0.0, r)
    return env


def _render_note_np(midi: int, n_samples: int, harmonics: List[float],
                    sr: int) -> "np.ndarray":
    t = np.arange(n_samples, dtype=np.float64) / sr
    f = midi_to_hz(midi)
    wave_arr = np.zeros(n_samples, dtype=np.float64)
    for i, amp in enumerate(harmonics, start=1):
        if f * i > sr / 2:                  # 나이퀴스트 초과 배음은 제외
            break
        wave_arr += amp * np.sin(2 * math.pi * f * i * t)
    peak = sum(harmonics) or 1.0
    wave_arr /= peak
    wave_arr *= _adsr(n_samples, sr)
    return wave_arr


# --------------------------------------------------------------------------- #
# 성부 → 신호
# --------------------------------------------------------------------------- #
def _render_part_np(midis: List[Optional[int]], durations: List[float],
                    sec_per_quarter: float, harmonics: List[float],
                    total_samples: int, sr: int) -> "np.ndarray":
    buf = np.zeros(total_samples, dtype=np.float64)
    pos = 0
    for midi, ql in zip(midis, durations):
        n = int(round(ql * sec_per_quarter * sr))
        if midi is not None and n > 0:
            seg = _render_note_np(midi, n, harmonics, sr)
            end = min(pos + n, total_samples)
            buf[pos:end] += seg[:end - pos]
        pos += n
    return buf


def render_wav(harm, path: str, sr: int = SAMPLE_RATE) -> str:
    """Harmonization 을 스테레오 WAV 파일로 렌더링."""
    if not _HAVE_NUMPY:
        return _render_wav_pure(harm, path, sr)

    tempo = harm.tempo_bpm or 90.0
    sec_per_quarter = 60.0 / tempo

    total_samples = int(round(sum(harm.durations) * sec_per_quarter * sr)) + sr // 2
    left = np.zeros(total_samples, dtype=np.float64)
    right = np.zeros(total_samples, dtype=np.float64)

    parts = harm.parts()
    for name, midis in parts.items():
        harmonics, gain, pan = _VOICE_TIMBRE[name]
        sig = _render_part_np(midis, harm.durations, sec_per_quarter,
                              harmonics, total_samples, sr) * gain
        # 등청력 팬(pan)
        lgain = math.cos((pan + 1) * math.pi / 4)
        rgain = math.sin((pan + 1) * math.pi / 4)
        left += sig * lgain
        right += sig * rgain

    # 정규화 (클리핑 방지)
    peak = max(float(np.max(np.abs(left))), float(np.max(np.abs(right))), 1e-9)
    norm = 0.89 / peak
    left *= norm
    right *= norm

    stereo = np.empty(total_samples * 2, dtype=np.float64)
    stereo[0::2] = left
    stereo[1::2] = right
    pcm = (stereo * 32767.0).astype("<i2")

    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


# --------------------------------------------------------------------------- #
# numpy 없는 환경용 대체 구현 (모노, 단순)
# --------------------------------------------------------------------------- #
def _render_wav_pure(harm, path: str, sr: int) -> str:  # pragma: no cover
    tempo = harm.tempo_bpm or 90.0
    spq = 60.0 / tempo
    total = int(round(sum(harm.durations) * spq * sr)) + sr // 4
    buf = [0.0] * total

    for name, midis in harm.parts().items():
        harmonics, gain, _pan = _VOICE_TIMBRE[name]
        pos = 0
        for midi, ql in zip(midis, harm.durations):
            n = int(round(ql * spq * sr))
            if midi is not None and n > 0:
                f = midi_to_hz(midi)
                peak = sum(harmonics) or 1.0
                for i in range(min(n, total - pos)):
                    t = i / sr
                    s = 0.0
                    for h, amp in enumerate(harmonics, start=1):
                        s += amp * math.sin(2 * math.pi * f * h * t)
                    env = 1.0
                    if i < int(0.01 * sr):
                        env = i / (0.01 * sr)
                    elif i > n - int(0.06 * sr):
                        env = max(0.0, (n - i) / (0.06 * sr))
                    buf[pos + i] += gain * env * s / peak
            pos += n

    peak = max((abs(x) for x in buf), default=1.0) or 1.0
    norm = 0.85 / peak
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = b"".join(struct.pack("<h", int(x * norm * 32767)) for x in buf)
        w.writeframes(frames)
    return path
