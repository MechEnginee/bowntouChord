"""
멜로디 입력 로딩.

여러 입력 형식을 하나의 단순한 멜로디 표현(`Melody`)으로 변환한다.

지원 형식:
    - MusicXML : .xml / .musicxml / .mxl   (MuseScore, OMR 결과 등)
    - MIDI     : .mid / .midi
    - 텍스트   : .txt  (아래 '텍스트 표기법' 참고)

텍스트 표기법 (사람이 직접 쓰거나 OMR 실패 시 대체 입력)
------------------------------------------------------------
    # 로 시작하는 줄은 주석.
    key: C major            # (선택) 조성. 생략하면 자동 추정.
    tempo: 96               # (선택) BPM. 생략하면 90.
    C4:1 D4:1 E4:2 R:1 G4:1 # 음표들.  '계이름옥타브:박자'

    - 음이름: C D E F G A B, 임시표 # 또는 b (예: F#4, Bb3)
    - 옥타브: 가온다 = C4
    - 박자  : 4분음표 = 1, 2분음표 = 2, 8분음표 = 0.5, 점4분 = 1.5
    - 쉼표  : R:박자
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

from music21 import converter, note, stream, key as m21key, tempo as m21tempo


@dataclass
class MelodyNote:
    """멜로디의 한 음(또는 쉼표)."""
    midi: Optional[int]        # MIDI 번호(쉼표면 None). 가온다 C4 = 60
    quarter_length: float      # 길이(4분음표 = 1.0)
    name: str = ""             # 표시용 이름 (예: 'C4', 'rest')

    @property
    def is_rest(self) -> bool:
        return self.midi is None


@dataclass
class Melody:
    notes: List[MelodyNote] = field(default_factory=list)
    key_name: str = ""          # 예: 'C major', 'A minor'
    tempo_bpm: float = 90.0

    def __len__(self) -> int:
        return len(self.notes)

    @property
    def pitched_notes(self) -> List[MelodyNote]:
        return [n for n in self.notes if not n.is_rest]


# --------------------------------------------------------------------------- #
# 공개 진입점
# --------------------------------------------------------------------------- #
def load_melody(path: str) -> Melody:
    """확장자를 보고 알맞은 로더로 멜로디를 읽어들인다."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt",):
        return _load_text(path)
    if ext in (".xml", ".musicxml", ".mxl", ".mid", ".midi"):
        return _load_music21(path)
    raise ValueError(
        f"지원하지 않는 형식입니다: {ext}\n"
        "  지원: .xml .musicxml .mxl .mid .midi .txt"
    )


# --------------------------------------------------------------------------- #
# 텍스트 로더
# --------------------------------------------------------------------------- #
_TOKEN_RE = re.compile(r"^([A-Ga-g])([#b]?)(-?\d+)?:(\d+(?:\.\d+)?)$")


def _load_text(path: str) -> Melody:
    mel = Melody()
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        low = line.lower()
        if low.startswith("key:"):
            mel.key_name = line.split(":", 1)[1].strip()
            continue
        if low.startswith("tempo:"):
            try:
                mel.tempo_bpm = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass
            continue

        for tok in line.split():
            mel.notes.append(_parse_token(tok))

    if not mel.notes:
        raise ValueError(f"{path}: 읽어들일 음표가 없습니다.")
    return mel


def _parse_token(tok: str) -> MelodyNote:
    # 쉼표
    if tok[0] in "Rr":
        parts = tok.split(":")
        ql = float(parts[1]) if len(parts) > 1 else 1.0
        return MelodyNote(midi=None, quarter_length=ql, name="rest")

    m = _TOKEN_RE.match(tok)
    if not m:
        raise ValueError(
            f"음표 표기를 이해할 수 없습니다: '{tok}'  (예: C4:1, F#4:0.5, R:1)"
        )
    letter, accidental, octave, dur = m.groups()
    octave = int(octave) if octave is not None else 4
    step = letter.upper()
    n = note.Note()
    n.pitch.step = step
    n.pitch.octave = octave
    if accidental == "#":
        n.pitch.accidental = "#"
    elif accidental == "b":
        n.pitch.accidental = "-"
    return MelodyNote(midi=int(n.pitch.midi),
                      quarter_length=float(dur),
                      name=f"{step}{accidental}{octave}")


# --------------------------------------------------------------------------- #
# music21 로더 (MusicXML / MIDI)
# --------------------------------------------------------------------------- #
def _load_music21(path: str) -> Melody:
    score = converter.parse(path)
    mel = Melody()

    # 조성
    try:
        k = score.recurse().getElementsByClass(m21key.Key).first()
        if k is None:
            ks = score.recurse().getElementsByClass(m21key.KeySignature).first()
            k = ks.asKey() if ks is not None else None
        if k is not None:
            mel.key_name = f"{k.tonic.name} {k.mode}"
    except Exception:
        pass

    # 템포
    try:
        mm = score.recurse().getElementsByClass(m21tempo.MetronomeMark).first()
        if mm is not None and mm.number:
            mel.tempo_bpm = float(mm.number)
    except Exception:
        pass

    # 멜로디 = 가장 위쪽 성부. 첫 파트를 취하고, 화음이면 최고음을 사용.
    part = score.parts[0] if score.parts else score
    flat = part.flatten().notesAndRests

    for el in flat:
        if isinstance(el, note.Rest):
            mel.notes.append(MelodyNote(None, float(el.quarterLength), "rest"))
        elif isinstance(el, note.Note):
            mel.notes.append(MelodyNote(int(el.pitch.midi),
                                        float(el.quarterLength),
                                        el.nameWithOctave))
        elif hasattr(el, "pitches") and el.pitches:  # Chord → 최고음
            top = max(el.pitches, key=lambda p: p.midi)
            mel.notes.append(MelodyNote(int(top.midi),
                                        float(el.quarterLength),
                                        top.nameWithOctave))

    if not mel.notes:
        raise ValueError(f"{path}: 멜로디 음표를 찾지 못했습니다.")
    return mel


def melody_from_stream(part: stream.Stream) -> Melody:
    """이미 파싱된 music21 Stream 에서 멜로디를 뽑아낸다 (내부용)."""
    mel = Melody()
    for el in part.flatten().notesAndRests:
        if isinstance(el, note.Rest):
            mel.notes.append(MelodyNote(None, float(el.quarterLength), "rest"))
        elif isinstance(el, note.Note):
            mel.notes.append(MelodyNote(int(el.pitch.midi),
                                        float(el.quarterLength),
                                        el.nameWithOctave))
    return mel
