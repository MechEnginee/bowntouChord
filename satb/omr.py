"""
OMR (Optical Music Recognition) — 악보 이미지 → MusicXML.

이미지에서 음표를 자동 인식하는 부분은 딥러닝 모델이 필요하다.
여기서는 오픈소스 OMR 엔진 `oemer` 를 감싸서 사용한다.

    설치:  pip install oemer
    (최초 실행 시 인식 모델을 자동으로 내려받는다.)

oemer 가 없거나 인식에 실패하면, 대신 MusicXML/MIDI/텍스트로 직접
입력하도록 안내한다.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile


class OMRNotAvailable(RuntimeError):
    pass


def is_available() -> bool:
    """oemer 사용 가능 여부."""
    if shutil.which("oemer"):
        return True
    try:
        import oemer  # noqa: F401
        return True
    except Exception:
        return False


def image_to_musicxml(image_path: str, out_dir: str | None = None) -> str:
    """
    악보 이미지를 MusicXML 로 변환하고 그 경로를 돌려준다.

    Raises:
        OMRNotAvailable : oemer 미설치
        RuntimeError    : 변환 실패
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(image_path)

    if not is_available():
        raise OMRNotAvailable(
            "OMR 엔진(oemer)이 설치되어 있지 않습니다.\n"
            "  설치:  pip install oemer\n"
            "또는 악보를 MusicXML/MIDI/텍스트로 변환해 직접 입력하세요.\n"
            "  (MuseScore 등으로 이미지를 열어 MusicXML 로 저장하면 정확도가 높습니다.)"
        )

    work = out_dir or tempfile.mkdtemp(prefix="omr_")
    os.makedirs(work, exist_ok=True)

    # oemer 는 CLI 로 실행하는 것이 가장 안정적이다.
    cmd = _oemer_cmd() + ["-o", work, image_path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError:
        raise OMRNotAvailable("oemer 실행 파일을 찾지 못했습니다. pip install oemer 로 설치하세요.")

    if proc.returncode != 0:
        raise RuntimeError(
            "OMR 변환에 실패했습니다.\n"
            f"stdout: {proc.stdout[-800:]}\n"
            f"stderr: {proc.stderr[-800:]}"
        )

    # 결과 .musicxml 찾기
    base = os.path.splitext(os.path.basename(image_path))[0]
    candidates = (
        glob.glob(os.path.join(work, base + "*.musicxml"))
        + glob.glob(os.path.join(work, "*.musicxml"))
        + glob.glob(os.path.join(work, base + "*.xml"))
        + glob.glob(os.path.join(work, "*.xml"))
    )
    if not candidates:
        raise RuntimeError(f"OMR 결과 MusicXML 을 찾지 못했습니다. (출력 폴더: {work})")
    return candidates[0]


def _oemer_cmd() -> list[str]:
    if shutil.which("oemer"):
        return ["oemer"]
    # python -m oemer
    return [sys.executable, "-m", "oemer"]
