"""
화성 결과를 MIDI / MusicXML 로 내보내기 (music21 사용).

- MIDI     : 어떤 미디어 플레이어에서도 4성부로 재생 가능.
- MusicXML : MuseScore 등에서 악보로 열어 확인/편집 가능.
"""

from __future__ import annotations

from typing import List, Optional

from music21 import stream, note, tempo as m21tempo, instrument, metadata

from .harmony import Harmonization

# 성부별 악기(사운드) 배정
_INSTRUMENTS = {
    "soprano": instrument.Flute,
    "alto":    instrument.Oboe,
    "tenor":   instrument.Clarinet,
    "bass":    instrument.Violoncello,
}


def _build_part(name: str, midis: List[Optional[int]],
                durations: List[float]) -> stream.Part:
    p = stream.Part()
    p.partName = name.capitalize()
    p.insert(0, _INSTRUMENTS[name]())
    for midi, ql in zip(midis, durations):
        if midi is None:
            p.append(note.Rest(quarterLength=ql))
        else:
            p.append(note.Note(int(midi), quarterLength=ql))
    return p


def build_score(harm: Harmonization) -> stream.Score:
    sc = stream.Score()
    sc.metadata = metadata.Metadata()
    sc.metadata.title = "SATB Harmonization"
    sc.metadata.composer = f"auto-harmonized ({harm.key_name})"

    for name in ("soprano", "alto", "tenor", "bass"):
        part = _build_part(name, harm.parts()[name], harm.durations)
        if name == "soprano":
            part.insert(0, m21tempo.MetronomeMark(number=harm.tempo_bpm or 90))
        sc.insert(0, part)
    return sc


def write_midi(harm: Harmonization, path: str) -> str:
    build_score(harm).write("midi", fp=path)
    return path


def write_musicxml(harm: Harmonization, path: str) -> str:
    build_score(harm).write("musicxml", fp=path)
    return path
