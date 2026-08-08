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
    raise SystemExit(main())
