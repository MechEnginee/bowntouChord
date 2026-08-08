"""
리드시트(코드만 적힌 악보) → 테너 성부 뽑기 + 소리/MIDI.

    python -m satb.leadsheet examples/maranatha_chords.txt -o out

'적힌 코드'를 그대로 사용한다(자동 화성이 아님).
소프라노(멜로디)가 없으므로 코드로부터 4성부 블록을 만들어 테너를 들려준다.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .chords import (Chord, parse_chord, tenor_note,
                     TENOR_LO, TENOR_HI, _pcs_in_range)
from .harmony import Harmonization
from .synth import render_wav
from . import export as export_mod

# 성부 음역(MIDI)
_RANGES = {
    "soprano": (60, 79),
    "alto":    (55, 74),
    "tenor":   (TENOR_LO, TENOR_HI),
    "bass":    (40, 60),
}

_FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F",
               "Gb", "G", "Ab", "A", "Bb", "B"]


def midi_name(m: int) -> str:
    return f"{_FLAT_NAMES[m % 12]}{m // 12 - 1}"


# --------------------------------------------------------------------------- #
# 리드시트 파싱
# --------------------------------------------------------------------------- #
@dataclass
class LeadSheet:
    title: str = ""
    key_name: str = "C major"
    tempo_bpm: float = 90.0
    # (마디번호, 섹션, 코드심볼) 순서열
    entries: List[Tuple[int, str, str]] = field(default_factory=list)


def parse_leadsheet(path: str) -> LeadSheet:
    ls = LeadSheet()
    section = ""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            low = s.lower()
            if low.startswith("title:"):
                ls.title = s.split(":", 1)[1].strip(); continue
            if low.startswith("key:"):
                ls.key_name = s.split(":", 1)[1].strip(); continue
            if low.startswith("tempo:"):
                try:
                    ls.tempo_bpm = float(s.split(":", 1)[1].strip())
                except ValueError:
                    pass
                continue
            m = re.match(r"^\[(.+)\]$", s)
            if m:
                section = m.group(1).strip(); continue
            m = re.match(r"^(\d+)\s*:\s*(.+)$", s)
            if m:
                measure = int(m.group(1))
                for sym in m.group(2).split():
                    ls.entries.append((measure, section, sym))
    if not ls.entries:
        raise ValueError(f"{path}: 코드를 찾지 못했습니다.")
    return ls


# --------------------------------------------------------------------------- #
# 코드 → 4성부 블록 (테너 강조)
# --------------------------------------------------------------------------- #
def _nearest_in_range(pc: int, lo: int, hi: int, prev: Optional[int],
                      above: Optional[int] = None) -> int:
    opts = _pcs_in_range(pc, lo, hi)
    if above is not None:
        higher = [o for o in opts if o >= above]
        if higher:
            opts = higher
    if not opts:
        return max(lo, min(hi, pc + 60))
    if prev is None:
        target = (lo + hi) // 2
        return min(opts, key=lambda o: abs(o - target))
    return min(opts, key=lambda o: abs(o - prev))


def voice_satb(chords: List[Chord]) -> Harmonization:
    harm = Harmonization(key_name="", tempo_bpm=90.0)
    pt = pa = ps = pb = None
    for ch in chords:
        # 테너: 매끄러운 성부진행
        t = tenor_note(ch, pt)
        # 베이스: 슬래시/근음 베이스
        b = _nearest_in_range(ch.bass_pc, *_RANGES["bass"], pb)
        # 알토: 테너 위의 코드톤
        atone = _pick_tone_above(ch, t, "alto", pa)
        # 소프라노: 알토 위의 코드톤(패드 상단)
        stone = _pick_tone_above(ch, atone, "soprano", ps)
        harm.soprano.append(stone)
        harm.alto.append(atone)
        harm.tenor.append(t)
        harm.bass.append(b)
        harm.chords.append(ch.symbol)
        pt, pa, ps, pb = t, atone, stone, b
    return harm


def _pick_tone_above(ch: Chord, floor: int, voice: str,
                     prev: Optional[int]) -> int:
    lo, hi = _RANGES[voice]
    cand = []
    for i in ch.intervals:
        pc = (ch.root_pc + i) % 12
        for midi in _pcs_in_range(pc, lo, hi):
            if midi >= floor:
                cand.append(midi)
    if not cand:
        return _nearest_in_range(ch.root_pc, lo, hi, prev, above=floor)
    if prev is None:
        return min(cand)
    return min(cand, key=lambda m: abs(m - prev))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _print_table(ls: LeadSheet, chords: List[Chord], tenor: List[int]) -> None:
    print(f"\n♪ {ls.title}")
    print(f"  조성 {ls.key_name} · 템포 {ls.tempo_bpm:.0f} BPM · 코드 {len(chords)}개\n")
    print("  마디 | 섹션        | 코드      | 테너 음")
    print("  -----+-------------+-----------+---------")
    last_section = None
    for (measure, section, _sym), ch, t in zip(ls.entries, chords, tenor):
        sec = section if section != last_section else ""
        last_section = section
        print(f"  {measure:>4} | {sec:<11} | {ch.symbol:<9} | {midi_name(t)}")
    print()
    # 테너 음만 한 줄로
    print("  테너 라인:", " ".join(midi_name(t) for t in tenor), "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="satb.leadsheet",
        description="리드시트 코드로부터 테너 성부를 뽑고 소리로 재생합니다.")
    ap.add_argument("input", help="코드 진행 파일 (.txt)")
    ap.add_argument("-o", "--out", help="출력 접두어 (기본: 입력 이름)")
    ap.add_argument("--no-wav", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.exists(args.input):
        print(f"파일을 찾을 수 없습니다: {args.input}", file=sys.stderr)
        return 2

    ls = parse_leadsheet(args.input)
    chords = [parse_chord(sym) for (_m, _s, sym) in ls.entries]
    tenor = []
    prev = None
    for ch in chords:
        t = tenor_note(ch, prev); tenor.append(t); prev = t

    _print_table(ls, chords, tenor)

    out = args.out or os.path.splitext(args.input)[0]

    # 마디당 4박을 코드 수로 나눠 길이 배정
    harm = voice_satb(chords)
    harm.key_name = ls.key_name
    harm.tempo_bpm = ls.tempo_bpm
    # durations: 같은 마디의 코드끼리 4박을 균등 분할
    from collections import Counter
    per_measure = Counter(m for (m, _s, _sym) in ls.entries)
    harm.durations = [4.0 / per_measure[m] for (m, _s, _sym) in ls.entries]

    # 전체 SATB 패드
    midi_path = out + "_satb.mid"
    export_mod.write_midi(harm, midi_path)
    print(f"SATB MIDI 저장: {midi_path}")

    # 테너만 따로 (다른 성부는 무음)
    tenor_only = Harmonization(key_name=ls.key_name, tempo_bpm=ls.tempo_bpm)
    n = len(harm.durations)
    tenor_only.soprano = [None] * n
    tenor_only.alto = [None] * n
    tenor_only.tenor = list(harm.tenor)
    tenor_only.bass = [None] * n
    tenor_only.durations = list(harm.durations)
    tenor_only.chords = list(harm.chords)
    tmid = out + "_tenor.mid"
    export_mod.write_midi(tenor_only, tmid)
    print(f"테너 MIDI 저장: {tmid}")

    if not args.no_wav:
        render_wav(harm, out + "_satb.wav")
        print(f"SATB 소리 저장: {out}_satb.wav")
        render_wav(tenor_only, out + "_tenor.wav")
        print(f"테너 소리 저장: {out}_tenor.wav")

    print("\n완료! 🎵")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
