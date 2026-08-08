"""
satb — 멜로디 악보를 읽어 4성부(SATB) 화성을 붙이고 소리로 재생하는 패키지.

파이프라인:
    악보 이미지 ──(OMR)──▶ MusicXML ──▶ 멜로디 ──(자동 화성)──▶ SATB ──▶ MIDI / WAV
"""

__version__ = "0.1.0"

from .melody import Melody, MelodyNote, load_melody
from .harmony import harmonize, Harmonization
from .synth import render_wav

__all__ = [
    "Melody",
    "MelodyNote",
    "load_melody",
    "harmonize",
    "Harmonization",
    "render_wav",
]
