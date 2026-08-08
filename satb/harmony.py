"""
자동 화성(SATB) 엔진.

멜로디를 받아
  1) 각 음마다 어울리는 다이어토닉 코드를 고르고 (비터비 DP로 진행을 자연스럽게),
  2) 그 코드를 소프라노(멜로디)/알토/테너/베이스 4성부로 배치한다.

결과는 4개의 성부(각각 MIDI 번호 리스트)와 코드 진행 라벨.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from music21 import key as m21key, note as m21note, roman, stream

from .melody import Melody, MelodyNote


# --------------------------------------------------------------------------- #
# 성부별 음역 (MIDI 번호). 합창 관례상의 일반적 범위.
# --------------------------------------------------------------------------- #
RANGES = {
    "soprano": (60, 81),   # C4 ~ A5
    "alto":    (55, 74),   # G3 ~ D5
    "tenor":   (48, 67),   # C3 ~ G4
    "bass":    (40, 62),   # E2 ~ D4
}


@dataclass
class ChordChoice:
    """한 시점에 선택된 코드."""
    figure: str            # 로마숫자 라벨 (예: 'I', 'V', 'ii')
    root_pc: int           # 근음 피치클래스 0~11
    pcs: Tuple[int, ...]   # 코드 구성음 피치클래스들
    function: str          # 'T'(토닉) / 'S'(서브도미넌트) / 'D'(도미넌트)


@dataclass
class Harmonization:
    key_name: str
    soprano: List[Optional[int]] = field(default_factory=list)
    alto:    List[Optional[int]] = field(default_factory=list)
    tenor:   List[Optional[int]] = field(default_factory=list)
    bass:    List[Optional[int]] = field(default_factory=list)
    durations: List[float] = field(default_factory=list)
    chords:  List[Optional[str]] = field(default_factory=list)  # 코드 라벨
    tempo_bpm: float = 90.0

    def parts(self) -> Dict[str, List[Optional[int]]]:
        return {"soprano": self.soprano, "alto": self.alto,
                "tenor": self.tenor, "bass": self.bass}


# --------------------------------------------------------------------------- #
# 조성별 다이어토닉 코드 정의
# --------------------------------------------------------------------------- #
# 각 코드의 (기능, 코드 우선순위 가중치). 1차 3화음(I·IV·V)에 높은 가중.
_MAJOR_CHORDS = [
    ("I",   "T", 3.0),
    ("ii",  "S", 1.6),
    ("iii", "T", 1.0),
    ("IV",  "S", 2.2),
    ("V",   "D", 3.0),
    ("vi",  "T", 1.6),
    ("viio","D", 0.8),
]
# 단조는 화성단음계 기준(V, viio 를 장화음/감화음으로).
_MINOR_CHORDS = [
    ("i",   "T", 3.0),
    ("iio", "S", 1.2),
    ("III", "T", 1.0),
    ("iv",  "S", 2.2),
    ("V",   "D", 3.0),
    ("VI",  "T", 1.6),
    ("viio","D", 0.8),
]

# 기능 간 진행 점수(앞기능 → 뒤기능). 값이 클수록 자연스러운 진행.
_FUNC_TRANSITION = {
    ("T", "T"): 0.5, ("T", "S"): 1.0, ("T", "D"): 0.9,
    ("S", "T"): 0.4, ("S", "S"): 0.3, ("S", "D"): 1.2,
    ("D", "T"): 1.4, ("D", "S"): -0.6, ("D", "D"): 0.3,  # D→S 는 피함
}


def _diatonic_chords(k: m21key.Key) -> List[ChordChoice]:
    table = _MAJOR_CHORDS if k.mode == "major" else _MINOR_CHORDS
    out: List[ChordChoice] = []
    for figure, func, _w in table:
        rn = roman.RomanNumeral(figure, k)
        pcs = tuple(sorted({p.pitchClass for p in rn.pitches}))
        out.append(ChordChoice(figure=figure,
                               root_pc=rn.root().pitchClass,
                               pcs=pcs,
                               function=func))
    return out


def _chord_prior(figure: str, mode: str) -> float:
    table = _MAJOR_CHORDS if mode == "major" else _MINOR_CHORDS
    for f, _func, w in table:
        if f == figure:
            return w
    return 1.0


# --------------------------------------------------------------------------- #
# 조성 결정
# --------------------------------------------------------------------------- #
def _resolve_key(mel: Melody) -> m21key.Key:
    if mel.key_name:
        try:
            toks = mel.key_name.split()
            tonic = toks[0]
            mode = toks[1] if len(toks) > 1 else "major"
            return m21key.Key(tonic, mode)
        except Exception:
            pass
    # 멜로디로부터 자동 추정
    s = stream.Stream()
    for n in mel.notes:
        if n.is_rest:
            s.append(m21note.Rest(quarterLength=n.quarter_length))
        else:
            s.append(m21note.Note(n.midi, quarterLength=n.quarter_length))
    try:
        return s.analyze("key")
    except Exception:
        return m21key.Key("C")


# --------------------------------------------------------------------------- #
# 1단계: 코드 진행 선택 (Viterbi DP)
# --------------------------------------------------------------------------- #
def _choose_chords(pitched_pcs: List[int],
                   positions: List[int],
                   k: m21key.Key) -> List[ChordChoice]:
    """
    pitched_pcs : 음이 있는 위치들의 멜로디 피치클래스
    positions   : (참고용) 원본 인덱스
    반환        : 각 음 위치에 대한 코드 선택
    """
    chords = _diatonic_chords(k)
    mode = k.mode
    n = len(pitched_pcs)
    if n == 0:
        return []

    # 각 시점의 후보 = 멜로디음을 코드톤으로 포함하는 코드.
    # (없으면 비화성음으로 간주하고 모든 코드 허용)
    def candidates(pc: int) -> List[int]:
        cand = [i for i, c in enumerate(chords) if pc in c.pcs]
        return cand if cand else list(range(len(chords)))

    # 멜로디음이 코드에서 어떤 역할인지에 따른 보너스(근음/3음 선호).
    def melody_bonus(pc: int, c: ChordChoice) -> float:
        if pc == c.root_pc:
            return 0.5
        if pc in c.pcs:
            third = c.pcs[1] if len(c.pcs) > 1 else -1
            if pc == third:
                return 0.4
            return 0.2
        return -0.8  # 비화성음

    NEG = -1e9
    score = [[NEG] * len(chords) for _ in range(n)]
    back = [[-1] * len(chords) for _ in range(n)]

    # 첫 음: 토닉으로 시작하면 보너스
    for ci in candidates(pitched_pcs[0]):
        c = chords[ci]
        s = math.log(_chord_prior(c.figure, mode)) + melody_bonus(pitched_pcs[0], c)
        if c.function == "T" and pitched_pcs[0] == c.root_pc:
            s += 0.6
        score[0][ci] = s

    for i in range(1, n):
        cur_cands = candidates(pitched_pcs[i])
        prev_cands = [ci for ci in range(len(chords)) if score[i - 1][ci] > NEG / 2]
        for ci in cur_cands:
            c = chords[ci]
            base = math.log(_chord_prior(c.figure, mode)) + melody_bonus(pitched_pcs[i], c)
            best, bp = NEG, -1
            for pi in prev_cands:
                p = chords[pi]
                trans = _FUNC_TRANSITION.get((p.function, c.function), 0.0)
                if p.figure == c.figure:
                    trans -= 0.3  # 같은 코드 반복은 약간 감점
                val = score[i - 1][pi] + trans
                if val > best:
                    best, bp = val, pi
            score[i][ci] = base + best
            back[i][ci] = bp

    # 마지막 음: 종지 유도 — 토닉으로 끝나도록 보너스
    last = n - 1
    for ci in range(len(chords)):
        if score[last][ci] <= NEG / 2:
            continue
        if chords[ci].function == "T":
            score[last][ci] += 1.2
        if chords[ci].figure in ("I", "i"):
            score[last][ci] += 0.8

    # 역추적
    end = max(range(len(chords)), key=lambda ci: score[last][ci])
    path = [end]
    for i in range(last, 0, -1):
        path.append(back[i][path[-1]])
    path.reverse()
    return [chords[ci] for ci in path]


# --------------------------------------------------------------------------- #
# 2단계: SATB 성부 배치
# --------------------------------------------------------------------------- #
def _pcs_in_range(pc: int, lo: int, hi: int) -> List[int]:
    """음역 [lo,hi] 안에서 해당 피치클래스를 갖는 MIDI 번호들."""
    start = lo + ((pc - lo) % 12)
    return list(range(start, hi + 1, 12))


def _voice_chord(sop: int,
                 chord: ChordChoice,
                 prev: Optional[Tuple[int, int, int]]
                 ) -> Tuple[int, int, int]:
    """
    소프라노(sop, 고정)와 코드를 받아 (alto, tenor, bass) 를 정한다.
    prev = 직전 (alto, tenor, bass) — 성부진행을 매끄럽게 하기 위해 사용.
    """
    root, third, fifth = _chord_tones(chord)

    # 채워야 할 음들: 코드 3음. 소프라노가 그 중 하나를 이미 냄.
    # 4성부이므로 하나를 중복. 근음 중복을 우선(3음 중복은 회피).
    needed = [root, third, fifth]
    sop_pc = sop % 12
    if sop_pc in needed:
        remaining = list(needed)
        remaining.remove(sop_pc)          # 소프라노가 낸 음 제거
        double = root if root != sop_pc else fifth
        inner_pcs = remaining + [double]  # 3개 (alto, tenor, bass 로)
    else:
        # 소프라노가 비화성음: 3음 + 근음중복
        inner_pcs = [root, third, fifth, root]
        inner_pcs = inner_pcs[:3] + [inner_pcs[3]]  # 4개지만 아래 3성부는 3개만
        inner_pcs = [root, third, fifth]            # 단순화: 3음 그대로

    # 베이스는 근음을 우선.
    bass_lo, bass_hi = RANGES["bass"]
    bass_candidates = _pcs_in_range(root, bass_lo, bass_hi) or \
        _pcs_in_range(root, bass_lo - 12, bass_hi)

    best = None
    best_cost = 1e18

    # inner_pcs 중 하나(근음)를 베이스로, 나머지 둘을 alto/tenor 로.
    for bpc_note in bass_candidates:
        # alto/tenor 에 배치할 두 피치클래스
        pool = list(inner_pcs)
        # 베이스가 낸 음(근음) 하나 제거
        if root in pool:
            pool2 = list(pool)
            pool2.remove(root)
        else:
            pool2 = list(pool)
        # pool2 는 보통 [third, fifth] (소프라노가 근음일 때) 또는
        # [잔여음, 중복음] 형태. alto/tenor 두 성부에 순서를 바꿔가며 배치.
        two = _pick_two(pool2)
        for a_pc, t_pc in two:
            a_lo, a_hi = RANGES["alto"]
            t_lo, t_hi = RANGES["tenor"]
            for a in _pcs_in_range(a_pc, a_lo, a_hi):
                for t in _pcs_in_range(t_pc, t_lo, t_hi):
                    cost = _voicing_cost(sop, a, t, bpc_note, prev)
                    if cost < best_cost:
                        best_cost = cost
                        best = (a, t, bpc_note)

    if best is None:
        # 최후의 안전장치
        a = _clamp_pc(third, "alto")
        t = _clamp_pc(fifth, "tenor")
        b = _clamp_pc(root, "bass")
        return a, t, b
    return best


def _pick_two(pool: List[int]) -> List[Tuple[int, int]]:
    """두 성부에 넣을 (pc_a, pc_t) 조합 목록."""
    if len(pool) >= 2:
        a, b = pool[0], pool[1]
        return [(a, b), (b, a)]
    if len(pool) == 1:
        return [(pool[0], pool[0])]
    return [(0, 0)]


def _chord_tones(chord: ChordChoice) -> Tuple[int, int, int]:
    """코드에서 (근음, 3음, 5음) 피치클래스."""
    root = chord.root_pc
    pcs = list(chord.pcs)
    others = [p for p in pcs if p != root]
    # 근음 기준 음정으로 3음/5음 추정
    def interval(p):
        return (p - root) % 12
    others.sort(key=interval)
    third = others[0] if others else (root + 4) % 12
    fifth = others[1] if len(others) > 1 else (root + 7) % 12
    return root, third, fifth


def _voicing_cost(sop: int, alto: int, tenor: int, bass: int,
                  prev: Optional[Tuple[int, int, int]]) -> float:
    cost = 0.0
    # 성부 교차 금지: soprano >= alto >= tenor >= bass
    if not (sop >= alto >= tenor >= bass):
        cost += 50.0
    # 위 세 성부는 서로 한 옥타브 이내가 이상적
    if sop - alto > 12:
        cost += 4.0
    if alto - tenor > 12:
        cost += 4.0
    # 테너-베이스는 12도까지 허용
    if tenor - bass > 19:
        cost += 3.0
    # 너무 붙는 것도 약간 감점(같은 음 겹침)
    if sop == alto:
        cost += 1.0
    if alto == tenor:
        cost += 0.5
    # 성부진행: 직전 성부에서의 이동량 최소화
    if prev is not None:
        pa, pt, pb = prev
        cost += 0.10 * abs(alto - pa)
        cost += 0.10 * abs(tenor - pt)
        cost += 0.08 * abs(bass - pb)
    return cost


def _clamp_pc(pc: int, voice: str) -> int:
    lo, hi = RANGES[voice]
    opts = _pcs_in_range(pc, lo, hi)
    if opts:
        mid = (lo + hi) / 2
        return min(opts, key=lambda m: abs(m - mid))
    return max(lo, min(hi, pc + 60))


# --------------------------------------------------------------------------- #
# 공개 진입점
# --------------------------------------------------------------------------- #
def harmonize(mel: Melody) -> Harmonization:
    k = _resolve_key(mel)
    key_name = f"{k.tonic.name} {k.mode}"

    # 음이 있는 위치만 골라 코드 진행 결정
    pitched_idx = [i for i, n in enumerate(mel.notes) if not n.is_rest]
    pitched_pcs = [mel.notes[i].midi % 12 for i in pitched_idx]
    chord_seq = _choose_chords(pitched_pcs, pitched_idx, k)

    harm = Harmonization(key_name=key_name, tempo_bpm=mel.tempo_bpm)

    prev_atb: Optional[Tuple[int, int, int]] = None
    chord_at: Dict[int, ChordChoice] = dict(zip(pitched_idx, chord_seq))

    for i, n in enumerate(mel.notes):
        harm.durations.append(n.quarter_length)
        if n.is_rest:
            harm.soprano.append(None)
            harm.alto.append(None)
            harm.tenor.append(None)
            harm.bass.append(None)
            harm.chords.append(None)
            continue

        chord = chord_at[i]
        sop = n.midi
        # 소프라노가 음역을 벗어나면 옥타브 이동
        s_lo, s_hi = RANGES["soprano"]
        while sop > s_hi:
            sop -= 12
        while sop < s_lo:
            sop += 12

        alto, tenor, bass = _voice_chord(sop, chord, prev_atb)
        prev_atb = (alto, tenor, bass)

        harm.soprano.append(sop)
        harm.alto.append(alto)
        harm.tenor.append(tenor)
        harm.bass.append(bass)
        harm.chords.append(chord.figure)

    return harm
