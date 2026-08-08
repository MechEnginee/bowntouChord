"""
명령줄 진입점.

    python -m satb 악보.png              # 이미지 → 자동인식 → 화성 → mid/wav/xml
    python -m satb melody.txt -o out     # 텍스트 멜로디로 바로 화성
    python -m satb score.musicxml --play # 만들고 바로 재생 시도

출력물(기본 접두어 = 입력 파일 이름):
    <out>.mid        4성부 MIDI
    <out>.wav        4성부 소리 (바로 재생 가능)
    <out>.musicxml   4성부 악보 (MuseScore 등에서 열기)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

from . import melody as melody_mod
from .harmony import harmonize
from .synth import render_wav
from . import export as export_mod
from . import omr as omr_mod

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif", ".webp"}


def _print_progression(harm) -> None:
    print(f"\n조성: {harm.key_name}    템포: {harm.tempo_bpm:.0f} BPM")
    print("코드 진행:")
    labels = [c for c in harm.chords if c]
    line, count = [], 0
    for lab in labels:
        line.append(f"{lab:<5}")
        count += 1
        if count % 8 == 0:
            print("  " + " ".join(line))
            line = []
    if line:
        print("  " + " ".join(line))
    print()


def _play(path: str) -> None:
    for player in ("ffplay", "aplay", "afplay", "paplay", "play"):
        exe = shutil.which(player)
        if exe:
            args = [exe, path]
            if player == "ffplay":
                args = [exe, "-nodisp", "-autoexit", path]
            print(f"재생: {player} {path}")
            subprocess.run(args)
            return
    print(f"(오디오 플레이어를 찾지 못했습니다. 파일을 직접 열어 재생하세요: {path})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="satb",
        description="멜로디 악보를 읽어 4성부(SATB) 화성을 붙이고 소리로 재생합니다.",
    )
    ap.add_argument("input", help="악보 이미지 / MusicXML / MIDI / 텍스트(.txt)")
    ap.add_argument("-o", "--out", help="출력 파일 접두어 (기본: 입력 이름)")
    ap.add_argument("--play", action="store_true", help="완성된 WAV 를 바로 재생 시도")
    ap.add_argument("--no-wav", action="store_true", help="WAV 소리 파일 생성 생략")
    ap.add_argument("--no-xml", action="store_true", help="MusicXML 악보 생성 생략")
    ap.add_argument("--tempo", type=float, default=None, help="템포(BPM) 강제 지정")
    ap.add_argument("--key", type=str, default=None,
                    help="조성 강제 지정 (예: 'C major', 'A minor')")
    args = ap.parse_args(argv)

    inp = args.input
    if not os.path.exists(inp):
        print(f"입력 파일을 찾을 수 없습니다: {inp}", file=sys.stderr)
        return 2

    out = args.out or os.path.splitext(inp)[0]
    ext = os.path.splitext(inp)[1].lower()

    # 1) 입력이 이미지면 OMR 로 MusicXML 생성
    src = inp
    if ext in _IMAGE_EXT:
        print("악보 이미지에서 음표를 인식하는 중... (OMR)")
        try:
            src = omr_mod.image_to_musicxml(inp, out_dir=os.path.dirname(os.path.abspath(out)) or ".")
            print(f"  인식 완료 → {src}")
        except omr_mod.OMRNotAvailable as e:
            print("\n[OMR 사용 불가]\n" + str(e), file=sys.stderr)
            return 3
        except Exception as e:
            print(f"\n[OMR 실패] {e}", file=sys.stderr)
            return 3

    # 2) 멜로디 로드
    try:
        mel = melody_mod.load_melody(src)
    except Exception as e:
        print(f"멜로디를 읽는 중 오류: {e}", file=sys.stderr)
        return 4

    if args.tempo:
        mel.tempo_bpm = args.tempo
    if args.key:
        mel.key_name = args.key

    print(f"멜로디 음표 수: {len(mel.pitched_notes)}")

    # 3) 자동 화성
    harm = harmonize(mel)
    _print_progression(harm)

    # 4) 내보내기
    midi_path = out + ".mid"
    export_mod.write_midi(harm, midi_path)
    print(f"MIDI  저장: {midi_path}")

    if not args.no_xml:
        try:
            xml_path = out + ".musicxml"
            export_mod.write_musicxml(harm, xml_path)
            print(f"악보  저장: {xml_path}")
        except Exception as e:
            print(f"(MusicXML 저장 생략: {e})")

    wav_path = out + ".wav"
    if not args.no_wav:
        render_wav(harm, wav_path)
        print(f"소리  저장: {wav_path}")

    if args.play and not args.no_wav:
        _play(wav_path)

    print("\n완료! 🎵")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
