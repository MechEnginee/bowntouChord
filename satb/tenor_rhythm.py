"""
박자에 맞춘 테너 성부.

멜로디(리듬 포함)와 마디별 코드 진행을 정렬해, **멜로디와 같은 리듬으로**
각 음마다 테너 음을 붙인다(합창 테너처럼 동형 리듬).

    python -m satb.tenor_rhythm 멜로디.musicxml 코드.txt -o out

- 멜로디: MusicXML/MIDI (마디·박자 정보 필요). OMR 결과를 그대로 사용 가능.
- 코드  : leadsheet 형식 파일 (마디번호: 코드 ...).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from music21 import converter, note as m21note, stream

from .chords import Chord, parse_chord, _pcs_in_range, TENOR_LO, TENOR_HI
from .harmony import Harmonization
from .synth import render_wav
from . import export as export_mod
from .leadsheet import parse_leadsheet, midi_name


@dataclass
class MNote:
    measure: int          # 우리가 매기는 마디번호(악보의 인쇄 번호에 맞춤)
    beat: float           # 마디 안에서의 박 위치(0=첫박)
    dur: float            # 길이(4분음표=1)
    midi: Optional[int]   # 쉼표면 None


def read_melody_measures(path: str) -> Tuple[List[MNote], int]:
    """
    MusicXML/MIDI 에서 (마디, 박, 길이, 음) 목록을 읽는다.
    반환: (음목록, 첫 정규마디의 우리번호)
    악보에 여린내기(pickup)가 있으면 그 마디를 m1 로 보고 이후를 m2..로 맞춘다.
    """
    score = converter.parse(path)
    part = score.parts[0] if score.parts else score
    measures = list(part.getElementsByClass(stream.Measure))

    notes: List[MNote] = []
    if not measures:
        # 마디 구분이 없으면 4/4 로 잘라서 부여
        off = 0.0
        for el in part.flatten().notesAndRests:
            measure = int(off // 4) + 1
            beat = off % 4
            midi = None if el.isRest else int(el.pitch.midi)
            notes.append(MNote(measure, beat, float(el.quarterLength), midi))
            off += float(el.quarterLength)
        return notes, 1

    # 여린내기 판정: 첫 마디의 채워진 길이가 한 마디보다 짧으면 pickup
    def measure_len(m):
        return sum(float(e.quarterLength) for e in m.notesAndRests)
    bar_ql = 4.0
    try:
        ts = part.recurse().getElementsByClass('TimeSignature').first()
        if ts is not None:
            bar_ql = ts.barDuration.quarterLength
    except Exception:
        pass

    first_full_our_number = 2  # 인쇄 악보가 m2 부터 코드가 시작
    our_num = 1
    for idx, m in enumerate(measures):
        for el in m.notesAndRests:
            if isinstance(el, m21note.Note):
                midi = int(el.pitch.midi)
            elif hasattr(el, "pitches") and el.pitches:
                midi = int(max(el.pitches, key=lambda p: p.midi).midi)
            else:
                midi = None
            notes.append(MNote(our_num, float(el.offset), float(el.quarterLength), midi))
        our_num += 1
    return notes, first_full_our_number


def read_rhythm_measures(path: str) -> Tuple[List[MNote], str, float]:
    """
    '리듬 파일'을 읽는다.  각 줄: "마디번호: 길이 길이 ..."
    길이는 4분음표=1 단위. 쉼표는 앞에 R (예: R0.5).
    멜로디 음정은 없으므로 midi 는 음표=0(무음정), 쉼표=None 으로 표시.
    반환: (음목록, 조성, 템포)
    """
    import re
    key_name, tempo = "C major", 90.0
    notes: List[MNote] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            low = s.lower()
            if low.startswith("key:"):
                key_name = s.split(":", 1)[1].strip(); continue
            if low.startswith("tempo:"):
                try:
                    tempo = float(s.split(":", 1)[1].strip())
                except ValueError:
                    pass
                continue
            m = re.match(r"^(\d+)\s*:\s*(.+)$", s)
            if not m:
                continue
            measure = int(m.group(1))
            beat = 0.0
            for tok in m.group(2).split():
                is_rest = tok[0] in "Rr"
                val = float(tok[1:] if is_rest else tok)
                notes.append(MNote(measure, beat, val, None if is_rest else 0))
                beat += val
    return notes, key_name, tempo


def build_chord_map(ls_path: str) -> Dict[int, List[str]]:
    """마디번호 -> 그 마디의 코드 심볼 목록(순서대로)."""
    ls = parse_leadsheet(ls_path)
    cmap: Dict[int, List[str]] = {}
    for (measure, _sec, sym) in ls.entries:
        cmap.setdefault(measure, []).append(sym)
    return cmap, ls


def chord_at(cmap: Dict[int, List[str]], measure: int, beat: float,
             bar_ql: float = 4.0) -> Optional[Chord]:
    syms = cmap.get(measure)
    if not syms:
        return None
    n = len(syms)
    span = bar_ql / n
    i = min(int(beat // span), n - 1)
    return parse_chord(syms[i])


def tenor_below(chord: Chord, melody_midi: Optional[int],
                prev: Optional[int]) -> int:
    """
    멜로디 아래에서 부를 테너 음: 코드 구성음 중
    - 멜로디보다 낮고(가능하면)
    - 직전 테너음과 가깝고
    - 3음/7음을 근음보다 약간 선호
    """
    pref = {}
    for i in chord.intervals:
        pc = (chord.root_pc + i) % 12
        if i in (3, 4):
            pref[pc] = 0.0
        elif i in (10, 11):
            pref[pc] = 0.5
        elif i in (6, 7, 8):
            pref[pc] = 0.9
        else:
            pref[pc] = 1.2

    ceiling = (melody_midi - 2) if melody_midi is not None else TENOR_HI
    cand = []
    for pc, p in pref.items():
        for midi in _pcs_in_range(pc, TENOR_LO, TENOR_HI):
            if melody_midi is None or midi <= melody_midi:  # 멜로디 넘지 않기
                cand.append((midi, p))
    if not cand:  # 다 넘으면 그냥 음역 안에서
        for pc, p in pref.items():
            for midi in _pcs_in_range(pc, TENOR_LO, TENOR_HI):
                cand.append((midi, p))

    if prev is None:
        target = min(ceiling, 58)  # 시작 목표 대략 A#3/Bb3
        return min(cand, key=lambda c: abs(c[0] - target) + c[1] * 2)[0]
    return min(cand, key=lambda c: abs(c[0] - prev) + c[1] * 1.5)[0]


def make_rhythmic_tenor(melody_path: str, chords_path: str):
    notes, _first = read_melody_measures(melody_path)
    cmap, ls = build_chord_map(chords_path)

    harm = Harmonization(key_name=ls.key_name, tempo_bpm=ls.tempo_bpm)
    prev = None
    cur_chord: Optional[Chord] = None
    for nt in notes:
        harm.durations.append(nt.dur)
        if nt.midi is None:
            harm.soprano.append(None)
            harm.tenor.append(None)
            harm.alto.append(None)
            harm.bass.append(None)
            harm.chords.append(None)
            continue
        ch = chord_at(cmap, nt.measure, nt.beat) or cur_chord
        cur_chord = ch or cur_chord
        if cur_chord is None:
            # 코드 없는 여린내기 등: 테너 쉼
            harm.soprano.append(nt.midi)
            harm.tenor.append(None)
            harm.alto.append(None)
            harm.bass.append(None)
            harm.chords.append(None)
            continue
        t = tenor_below(cur_chord, nt.midi, prev)
        prev = t
        harm.soprano.append(nt.midi)
        harm.tenor.append(t)
        harm.alto.append(None)
        harm.bass.append(None)
        harm.chords.append(cur_chord.symbol)
    return harm, notes, cmap


_BRANGE = (40, 60)   # 베이스
_ARANGE = (55, 74)   # 알토
_SRANGE = (60, 81)   # 소프라노(패드 상단)


def _tone_above(chord: Chord, floor: int, lo: int, hi: int,
                prev: Optional[int]) -> int:
    cand = []
    for i in chord.intervals:
        pc = (chord.root_pc + i) % 12
        for midi in _pcs_in_range(pc, lo, hi):
            if midi >= floor:
                cand.append(midi)
    if not cand:
        for i in chord.intervals:
            pc = (chord.root_pc + i) % 12
            cand += _pcs_in_range(pc, lo, hi)
    if not cand:
        return floor
    return min(cand) if prev is None else min(cand, key=lambda m: abs(m - prev))


def make_from_rhythm(rhythm_path: str, chords_path: str):
    """리듬 파일 + 코드 → (4성부 화음+테너) Harmonization, (테너만) Harmonization."""
    notes, key_name, tempo = read_rhythm_measures(rhythm_path)
    cmap, _ls = build_chord_map(chords_path)

    full = Harmonization(key_name=key_name, tempo_bpm=tempo)
    tonly = Harmonization(key_name=key_name, tempo_bpm=tempo)
    pt = pb = pa = ps = None
    cur: Optional[Chord] = None
    for nt in notes:
        full.durations.append(nt.dur)
        tonly.durations.append(nt.dur)
        if nt.midi is None:   # 쉼표
            for h in (full, tonly):
                h.soprano.append(None); h.alto.append(None)
                h.tenor.append(None); h.bass.append(None); h.chords.append(None)
            continue
        ch = chord_at(cmap, nt.measure, nt.beat) or cur
        cur = ch or cur
        if cur is None:       # 코드 없는 여린내기: 테너 쉼
            for h in (full, tonly):
                h.soprano.append(None); h.alto.append(None)
                h.tenor.append(None); h.bass.append(None); h.chords.append(None)
            continue
        t = tenor_below(cur, None, pt); pt = t
        b = _tone_above(cur, _BRANGE[0], *_BRANGE, pb); pb = b
        # 베이스는 슬래시/근음을 우선
        bb = min(_pcs_in_range(cur.bass_pc, *_BRANGE) or [b],
                 key=lambda m: abs(m - (pb or 48)))
        b = bb; pb = b
        a = _tone_above(cur, t + 1, *_ARANGE, pa); pa = a
        s = _tone_above(cur, a + 1, *_SRANGE, ps); ps = s
        full.soprano.append(s); full.alto.append(a)
        full.tenor.append(t); full.bass.append(b); full.chords.append(cur.symbol)
        tonly.soprano.append(None); tonly.alto.append(None)
        tonly.tenor.append(t); tonly.bass.append(None); tonly.chords.append(cur.symbol)
    return full, tonly, notes, cmap


def main_rhythm(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="satb.tenor_rhythm rhythm",
        description="리듬 파일 + 코드로 박자에 맞춘 테너를 만듭니다.")
    ap.add_argument("rhythm", help="리듬 파일 (마디번호: 길이 ...)")
    ap.add_argument("chords", help="코드 진행 파일 (leadsheet 형식)")
    ap.add_argument("-o", "--out", help="출력 접두어")
    ap.add_argument("--no-wav", action="store_true")
    args = ap.parse_args(argv)
    for p in (args.rhythm, args.chords):
        if not os.path.exists(p):
            print(f"파일을 찾을 수 없습니다: {p}", file=sys.stderr); return 2

    full, tonly, notes, _ = make_from_rhythm(args.rhythm, args.chords)
    out = args.out or os.path.splitext(args.rhythm)[0] + "_tenor"

    # 마디별 테너(리듬 포함) 표
    print(f"\n박자에 맞춘 테너  (조성 {full.key_name}, 템포 {full.tempo_bpm:.0f})")
    cur_m = None; line = []
    for nt, t in zip(notes, tonly.tenor):
        if nt.midi is None and t is None and nt.measure != cur_m:
            pass
        if nt.measure != cur_m:
            if line: print("  m%-3d %s" % (cur_m, " ".join(line)))
            line = []; cur_m = nt.measure
        if t is None:
            line.append(f"쉼({_dur_label(nt.dur)})")
        else:
            line.append(f"{midi_name(t)}({_dur_label(nt.dur)})")
    if line: print("  m%-3d %s" % (cur_m, " ".join(line)))
    print()

    export_mod.write_midi(full, out + "_satb.mid"); print(f"4성부 MIDI: {out}_satb.mid")
    export_mod.write_midi(tonly, out + "_only.mid"); print(f"테너 MIDI : {out}_only.mid")
    if not args.no_wav:
        render_wav(full, out + "_satb.wav"); print(f"4성부 소리: {out}_satb.wav")
        render_wav(tonly, out + "_only.wav"); print(f"테너 소리 : {out}_only.wav")
    print("\n완료! 🎵")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="satb.tenor_rhythm",
        description="멜로디 리듬에 맞춰 테너 성부를 붙입니다.")
    ap.add_argument("melody", help="멜로디 MusicXML/MIDI (박자 정보 포함)")
    ap.add_argument("chords", help="코드 진행 파일 (leadsheet 형식)")
    ap.add_argument("-o", "--out", help="출력 접두어")
    ap.add_argument("--no-wav", action="store_true")
    args = ap.parse_args(argv)

    for p in (args.melody, args.chords):
        if not os.path.exists(p):
            print(f"파일을 찾을 수 없습니다: {p}", file=sys.stderr)
            return 2

    harm, notes, _cmap = make_rhythmic_tenor(args.melody, args.chords)
    out = args.out or os.path.splitext(args.melody)[0] + "_tenor_rhythm"

    # 표: 마디별 테너 음(음표 리듬 포함)
    print(f"\n박자에 맞춘 테너 (음 개수 {sum(1 for t in harm.tenor if t)})")
    line = []
    cur_m = None
    for nt, t in zip(notes, harm.tenor):
        if nt.midi is None:
            continue
        if nt.measure != cur_m:
            if line:
                print("  m%-3d %s" % (cur_m, " ".join(line)))
            line = []; cur_m = nt.measure
        line.append(f"{midi_name(t)}({_dur_label(nt.dur)})")
    if line:
        print("  m%-3d %s" % (cur_m, " ".join(line)))

    # 멜로디+테너 두 성부 오디오
    midi_path = out + ".mid"
    export_mod.write_midi(harm, midi_path)
    print(f"\nMIDI 저장: {midi_path}")

    # 테너만
    n = len(harm.durations)
    tonly = Harmonization(key_name=harm.key_name, tempo_bpm=harm.tempo_bpm)
    tonly.soprano = [None] * n
    tonly.alto = [None] * n
    tonly.tenor = list(harm.tenor)
    tonly.bass = [None] * n
    tonly.durations = list(harm.durations)
    tonly.chords = list(harm.chords)
    export_mod.write_midi(tonly, out + "_only.mid")
    print(f"테너 MIDI 저장: {out}_only.mid")

    if not args.no_wav:
        render_wav(harm, out + ".wav")
        print(f"멜로디+테너 소리 저장: {out}.wav")
        render_wav(tonly, out + "_only.wav")
        print(f"테너만 소리 저장: {out}_only.wav")

    print("\n완료! 🎵")
    return 0


def _dur_label(ql: float) -> str:
    table = {4.0: "온", 3.0: "점2분", 2.0: "2분", 1.5: "점4분",
             1.0: "4분", 0.75: "점8분", 0.5: "8분", 0.25: "16분"}
    return table.get(round(ql, 2), f"{ql}")


if __name__ == "__main__":
    # 사용법:
    #   python -m satb.tenor_rhythm 멜로디.musicxml 코드.txt        (멜로디 파일 사용)
    #   python -m satb.tenor_rhythm --rhythm 리듬.txt 코드.txt      (리듬만 옮긴 파일 사용)
    import sys as _sys
    if "--rhythm" in _sys.argv:
        _argv = [a for a in _sys.argv[1:] if a != "--rhythm"]
        raise SystemExit(main_rhythm(_argv))
    raise SystemExit(main())
