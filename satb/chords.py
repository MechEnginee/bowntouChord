"""
코드 심볼 파싱 + 리드시트(멜로디 없이 코드만)로부터 성부 뽑기.

리드시트에 코드가 명시돼 있을 때, 그 코드를 그대로 사용해 테너(및 알토·베이스)
성부를 만든다.  자동 화성(harmony.py)과 달리 '적힌 코드'를 존중한다.

코드 심볼 예:  Bb  Cm7  Bb/D  Eb  F/Eb  Gm7  F/A  Fmaj7  Ddim
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

_LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# 코드 품질 → 근음 기준 반음 간격
_QUALITIES = {
    "":      (0, 4, 7),          # 장3화음
    "maj":   (0, 4, 7),
    "M":     (0, 4, 7),
    "m":     (0, 3, 7),          # 단3화음
    "min":   (0, 3, 7),
    "-":     (0, 3, 7),
    "dim":   (0, 3, 6),          # 감3화음
    "aug":   (0, 4, 8),          # 증3화음
    "7":     (0, 4, 7, 10),      # 속7
    "m7":    (0, 3, 7, 10),      # 단7
    "min7":  (0, 3, 7, 10),
    "-7":    (0, 3, 7, 10),
    "maj7":  (0, 4, 7, 11),      # 장7
    "M7":    (0, 4, 7, 11),
    "m7b5":  (0, 3, 6, 10),      # 반감7
    "dim7":  (0, 3, 6, 9),
    "sus4":  (0, 5, 7),
    "sus2":  (0, 2, 7),
    "6":     (0, 4, 7, 9),
    "m6":    (0, 3, 7, 9),
    "9":     (0, 4, 7, 10, 14),
}


@dataclass
class Chord:
    symbol: str
    root_pc: int
    bass_pc: int
    intervals: Tuple[int, ...]

    @property
    def pcs(self) -> Tuple[int, ...]:
        return tuple(sorted({(self.root_pc + i) % 12 for i in self.intervals}))

    def role_pc(self, semitone: int) -> Optional[int]:
        """근음으로부터 semitone 만큼의 음이 이 코드에 있으면 그 피치클래스."""
        want = (self.root_pc + semitone) % 12
        return want if want in self.pcs else None


_NOTE_RE = re.compile(r"^([A-Ga-g])([#b♯♭]?)(.*)$")


def _note_pc(letter: str, accidental: str) -> int:
    pc = _LETTER_PC[letter.upper()]
    if accidental in ("#", "♯"):
        pc += 1
    elif accidental in ("b", "♭"):
        pc -= 1
    return pc % 12


def parse_chord(symbol: str) -> Chord:
    """코드 심볼 문자열을 Chord 로 파싱."""
    sym = symbol.strip()
    # 슬래시 베이스 분리
    bass_part = None
    if "/" in sym:
        sym, bass_part = sym.split("/", 1)

    m = _NOTE_RE.match(sym)
    if not m:
        raise ValueError(f"코드 심볼을 이해할 수 없습니다: '{symbol}'")
    letter, acc, quality = m.groups()
    root_pc = _note_pc(letter, acc)

    quality = quality.strip()
    if quality not in _QUALITIES:
        # 흔한 변형 정리
        q = quality.replace("min", "m").replace("Maj", "maj")
        if q in _QUALITIES:
            quality = q
        else:
            raise ValueError(f"지원하지 않는 코드 품질: '{quality}'  (심볼 {symbol})")
    intervals = _QUALITIES[quality]

    if bass_part:
        bm = _NOTE_RE.match(bass_part.strip())
        if not bm:
            raise ValueError(f"슬래시 베이스를 이해할 수 없습니다: '{symbol}'")
        bass_pc = _note_pc(bm.group(1), bm.group(2))
    else:
        bass_pc = root_pc

    return Chord(symbol=symbol.strip(), root_pc=root_pc,
                 bass_pc=bass_pc, intervals=intervals)


# --------------------------------------------------------------------------- #
# 테너 성부 뽑기
# --------------------------------------------------------------------------- #
# 테너 음역(MIDI): 대략 B2(47) ~ E4(64). 편안한 합창 테너 범위.
TENOR_LO, TENOR_HI = 47, 64


def _pcs_in_range(pc: int, lo: int, hi: int) -> List[int]:
    start = lo + ((pc - lo) % 12)
    return list(range(start, hi + 1, 12))


def tenor_note(chord: Chord, prev: Optional[int]) -> int:
    """
    코드 하나에 대한 테너 음을 고른다.
    - 코드 구성음 중에서
    - 테너 음역 안, 직전 테너음과 가장 가깝게(성부진행 매끄럽게)
    - 화음 색을 살리도록 3음/7음/5음을 근음보다 약간 선호
    """
    # 각 구성음의 '선호 가중'(작을수록 선호): 3음 > 7음 > 5음 > 근음
    pref = {}
    for i in chord.intervals:
        pc = (chord.root_pc + i) % 12
        if i in (3, 4):        # 3음
            pref[pc] = 0.0
        elif i in (10, 11):    # 7음
            pref[pc] = 0.6
        elif i in (7, 6, 8):   # 5음
            pref[pc] = 0.9
        else:                  # 근음 등
            pref[pc] = 1.2

    candidates = []
    for pc, p in pref.items():
        for midi in _pcs_in_range(pc, TENOR_LO, TENOR_HI):
            candidates.append((midi, p))

    if prev is None:
        # 시작: 테너 중앙(약 D3~A3)에서 3음 선호
        center = 55  # G3
        best = min(candidates, key=lambda c: abs(c[0] - center) + c[1] * 2)
        return best[0]

    def cost(c):
        midi, p = c
        return abs(midi - prev) + p * 1.5   # 이동거리 + 음선호
    best = min(candidates, key=cost)
    return best[0]


def tenor_line(symbols: List[str]) -> List[int]:
    """코드 심볼 목록 → 테너 MIDI 음 목록."""
    line, prev = [], None
    for sym in symbols:
        ch = parse_chord(sym)
        t = tenor_note(ch, prev)
        line.append(t)
        prev = t
    return line
