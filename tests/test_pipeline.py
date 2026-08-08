"""
기본 파이프라인 테스트. 실행:  python -m pytest -q   또는  python tests/test_pipeline.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from satb.melody import load_melody, Melody, MelodyNote          # noqa: E402
from satb.harmony import harmonize, RANGES                        # noqa: E402
from satb.synth import render_wav                                 # noqa: E402
from satb import export as export_mod                             # noqa: E402

EX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "examples", "twinkle.txt")


def _harm():
    return harmonize(load_melody(EX))


def test_key_and_cadence():
    h = _harm()
    assert h.key_name == "C major"
    chords = [c for c in h.chords if c]
    assert chords[0] == "I"          # 토닉으로 시작
    assert chords[-1] in ("I",)      # 토닉으로 종지
    assert chords[-2] in ("V", "viio", "IV")  # 종지 직전은 도미넌트/서브도미넌트


def test_no_voice_crossing_and_ranges():
    h = _harm()
    for i in range(len(h.soprano)):
        s, a, t, b = h.soprano[i], h.alto[i], h.tenor[i], h.bass[i]
        if s is None:
            continue
        assert s >= a >= t >= b, f"성부 교차 @ {i}"
        for name, v in (("soprano", s), ("alto", a), ("tenor", t), ("bass", b)):
            lo, hi = RANGES[name]
            assert lo <= v <= hi, f"음역 위반 {name} @ {i}: {v}"


def test_chords_contain_melody_or_are_triads():
    """각 화음이 실제 3화음 구성음을 담고 있는지 (근/3/5음 3종류)."""
    h = _harm()
    for i in range(len(h.soprano)):
        if h.soprano[i] is None:
            continue
        pcs = {v % 12 for v in (h.soprano[i], h.alto[i], h.tenor[i], h.bass[i])}
        assert len(pcs) == 3, f"3화음 아님 @ {i}: {pcs}"


def test_rest_handling():
    mel = Melody(notes=[MelodyNote(60, 1.0, "C4"),
                        MelodyNote(None, 1.0, "rest"),
                        MelodyNote(67, 1.0, "G4")],
                 key_name="C major")
    h = harmonize(mel)
    assert h.soprano[1] is None and h.bass[1] is None


def test_outputs_created():
    h = _harm()
    d = tempfile.mkdtemp()
    wav = render_wav(h, os.path.join(d, "o.wav"))
    midi = export_mod.write_midi(h, os.path.join(d, "o.mid"))
    assert os.path.getsize(wav) > 1000
    assert os.path.getsize(midi) > 100


if __name__ == "__main__":
    for fn in list(globals()):
        if fn.startswith("test_"):
            globals()[fn]()
            print("ok:", fn)
    print("\n모든 테스트 통과 ✅")
