"""Testy zapisu: zaznaczone fragmenty i momenty w TXT (oba warianty) i JSON, nie w SRT."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whisper_automat.core import writers as W  # noqa: E402
from whisper_automat.core.engine import Segment, TranscriptionResult  # noqa: E402


def wynik(znaczniki=None) -> TranscriptionResult:
    r = TranscriptionResult(source=Path("Spotkanie.flac"), duration=30.0, elapsed=1.0)
    r.segments = [
        Segment(0.0, 4.0, " Dzień dobry wszystkim.", speaker=1),
        Segment(5.0, 9.0, " Witam, mam pytanie o cennik.", speaker=0),
        Segment(10.0, 14.0, " Cennik wysyłam jutro.", speaker=1),
        Segment(20.0, 24.0, " Dziękuję, to wszystko.", speaker=0),
    ]
    r.speaker_names = {1: "Marek"}
    r.ja = 1
    r.znaczniki = znaczniki or []
    return r


class ZnacznikiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="writers-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_txt_ze_znacznikami_czasu(self):
        r = wynik([{"czas": 9.5, "opis": ""}, {"czas": 28.0, "opis": "decyzja"}])
        tekst = W.write_txt(r, self.tmp / "a.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(tekst[2], "[00:00:09.500]  ⚑ zaznaczony moment")
        self.assertTrue(tekst[3].startswith("[00:00:10.000 --> 00:00:14.000]  Marek: Cennik"))
        self.assertEqual(tekst[-1], "[00:00:28.000]  ⚑ zaznaczony moment: decyzja")
        self.assertEqual(len(tekst), 6)

    def test_txt_czysty(self):
        r = wynik([{"czas": 9.5, "opis": ""}, {"czas": 3700.0, "opis": "koniec"}])
        akapity = W.write_txt_plain(r, self.tmp / "a.tekst.txt").read_text(
            encoding="utf-8").strip().split("\n\n")
        self.assertEqual(akapity[0], "Marek: Dzień dobry wszystkim.")
        self.assertEqual(akapity[1], "MÓWCA 1: Witam, mam pytanie o cennik.")
        self.assertEqual(akapity[2], "⚑ 0:09")
        # Po znaczniku mówca podpisany na nowo.
        self.assertEqual(akapity[3], "Marek: Cennik wysyłam jutro.")
        self.assertEqual(akapity[-1], "⚑ 1:01:40 — koniec")

    def test_bez_znacznikow_jak_dotad(self):
        r = wynik()
        tekst = W.write_txt(r, self.tmp / "b.txt").read_text(encoding="utf-8")
        self.assertNotIn("⚑", tekst)
        self.assertEqual(len(tekst.splitlines()), 4)
        dane = json.loads(W.write_json(r, self.tmp / "b.json").read_text(encoding="utf-8"))
        self.assertNotIn("markers", dane)

    def test_json_i_srt(self):
        r = wynik([{"czas": 9.5, "opis": "ważne"}])
        dane = json.loads(W.write_json(r, self.tmp / "c.json").read_text(encoding="utf-8"))
        self.assertEqual(dane["markers"], [{"time": 9.5, "end": None, "label": "ważne"}])
        self.assertEqual(dane["segments"][0]["speaker_name"], "Marek")
        srt = W.write_srt(r, self.tmp / "c.srt").read_text(encoding="utf-8")
        self.assertNotIn("⚑", srt)
        self.assertIn("Marek: Dzień dobry wszystkim.", srt)


class FragmentyTest(unittest.TestCase):
    """„Zaznacz” — początek, drugie naciśnięcie — koniec (od 1.6.1)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="writers-"))
        self.r = wynik([{"czas": 4.5, "koniec": 15.0, "opis": ""},
                        {"czas": 22.0, "koniec": None, "opis": ""}])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_txt_ze_znacznikami_czasu(self):
        tekst = W.write_txt(self.r, self.tmp / "a.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(tekst[1], "[00:00:04.500]  ⚑ " + W.t("Fragment {n} — początek").format(n=1))
        self.assertTrue(tekst[2].startswith("[00:00:05.000 --> 00:00:09.000]"))
        self.assertEqual(tekst[4], "[00:00:15.000]  ⚑ " + W.t("Fragment {n} — koniec").format(n=1))
        self.assertEqual(tekst[-1], "[00:00:22.000]  ⚑ " + W.t("zaznaczony moment"))

    def test_txt_czysty(self):
        akapity = W.write_txt_plain(self.r, self.tmp / "a.tekst.txt").read_text(
            encoding="utf-8").strip().split("\n\n")
        self.assertEqual(akapity[1], "⚑ " + W.t("Fragment {n}: {od}–{do}").format(
            n=1, od="0:04", do="0:15"))
        self.assertIn("⚑ " + W.t("koniec fragmentu {n}").format(n=1), akapity)
        self.assertEqual(akapity[-1], "⚑ 0:22")

    def test_json(self):
        dane = json.loads(W.write_json(self.r, self.tmp / "a.json").read_text(encoding="utf-8"))
        self.assertEqual(dane["markers"], [{"time": 4.5, "end": 15.0, "label": ""},
                                           {"time": 22.0, "end": None, "label": ""}])


if __name__ == "__main__":
    unittest.main()
