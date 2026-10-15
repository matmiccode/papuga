"""Testy rozpoznawania mówców bez modeli: tag własnego nagrania, tor „Ja”,
przypisanie tekstu z numerem użytkownika, numeracja i sklejanie."""

import shutil
import sys
import tempfile
import unittest
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whisper_automat.core import diarization as D  # noqa: E402

RATE = 16000


@dataclass
class Slowo:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: List[Slowo] = field(default_factory=list)
    speaker: int = -1


def ton(sekundy: float, f: float, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(sekundy * RATE)) / RATE
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)


def zapisz_stereo(plik: Path, lewy: np.ndarray, prawy: np.ndarray) -> Path:
    n = max(lewy.size, prawy.size)
    l = np.zeros(n, np.float32)
    r = np.zeros(n, np.float32)
    l[: lewy.size] = lewy
    r[: prawy.size] = prawy
    dane = np.empty(2 * n, np.int16)
    dane[0::2] = np.clip(l * 32767, -32768, 32767).astype(np.int16)
    dane[1::2] = np.clip(r * 32767, -32768, 32767).astype(np.int16)
    with wave.open(str(plik), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(dane.tobytes())
    return plik


class TagTest(unittest.TestCase):
    def test_tag_wlasnego_nagrania(self):
        tag = D.tag_nagrania({"whisper_automat": "nagranie-spotkania;kanaly=mikrofon,system;wersja=1.5",
                              "title": "Spotkanie"})
        self.assertEqual(tag["rodzaj"], "nagranie-spotkania")
        self.assertEqual(tag["kanaly"], ["mikrofon", "system"])
        self.assertEqual(tag["wersja"], "1.5")

    def test_klucz_dowolna_wielkoscia_liter(self):
        self.assertIsNotNone(D.tag_nagrania({"WHISPER_AUTOMAT": "nagranie-spotkania"}))

    def test_brak_tagu(self):
        self.assertIsNone(D.tag_nagrania(None))
        self.assertIsNone(D.tag_nagrania({}))
        self.assertIsNone(D.tag_nagrania({"title": "x"}))
        self.assertIsNone(D.tag_nagrania({"whisper_automat": "cos-innego"}))


class OdcinkiWlasneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="diar-"))
        # Lewy (mikrofon): ton w sekundach 0–3. Prawy (rozmówcy): ton w 4–8.
        lewy = np.concatenate([ton(3.0, 220.0), np.zeros(5 * RATE, np.float32)])
        prawy = np.concatenate([np.zeros(4 * RATE, np.float32), ton(4.0, 330.0)])
        # Echo rozmówców w mikrofonie (głośniki): -20 dB w sekundach 4–8.
        lewy[4 * RATE:] += 0.1 * prawy[4 * RATE:]
        self.stereo = zapisz_stereo(self.tmp / "stereo.wav", lewy, prawy)
        self.segmenty = [
            Segment(0.0, 3.0, " dzień dobry wszystkim", [
                Slowo(0.1, 0.8, " dzień"), Slowo(0.9, 1.6, " dobry"), Slowo(1.7, 2.9, " wszystkim")]),
            Segment(4.0, 8.0, " witam państwa serdecznie", [
                Slowo(4.2, 5.0, " witam"), Slowo(5.1, 6.4, " państwa"), Slowo(6.5, 7.8, " serdecznie")]),
        ]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ja_tylko_tam_gdzie_mikrofon(self):
        odcinki = D.odcinki_wlasne(self.stereo, self.segmenty, ja=1)
        self.assertEqual(len(odcinki), 1)
        self.assertEqual(odcinki[0].mowca, 1)
        self.assertAlmostEqual(odcinki[0].start, 0.1)
        self.assertAlmostEqual(odcinki[0].koniec, 2.9)

    def test_bez_slow_bierze_segmenty(self):
        segmenty = [Segment(0.0, 3.0, "a"), Segment(4.0, 8.0, "b")]
        odcinki = D.odcinki_wlasne(self.stereo, segmenty, ja=0)
        self.assertEqual([(o.start, o.koniec) for o in odcinki], [(0.0, 3.0)])

    def test_cisza_to_nie_ja(self):
        cichy = zapisz_stereo(self.tmp / "cisza.wav", np.zeros(RATE * 3, np.float32),
                              np.zeros(RATE * 3, np.float32))
        self.assertEqual(D.odcinki_wlasne(cichy, [Segment(0.0, 2.0, "x")], ja=0), [])

    def test_przypisz_z_numerem_ja(self):
        # Diaryzacja prawego kanału dała jednego rozmówcę (0) w 4–8 s;
        # użytkownik dostaje numer 1 i jego odcinki stoją pierwsze.
        prawe = [D.Odcinek(4.0, 8.0, 0)]
        ja = D.odcinki_wlasne(self.stereo, self.segmenty, ja=1)
        segmenty = D.przypisz(self.segmenty, ja + prawe)
        self.assertEqual([s.speaker for s in segmenty], [1, 0])
        self.assertEqual(D.etykieta(1, {1: "Marek"}), "Marek")
        self.assertEqual(D.etykieta(0, {1: "Marek"}), "MÓWCA 1")

    def test_mono_odrzucone(self):
        plik = self.tmp / "mono.wav"
        with wave.open(str(plik), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(np.zeros(RATE, np.int16).tobytes())
        with self.assertRaises(D.DiarizationError):
            D.odcinki_wlasne(plik, self.segmenty, ja=0)


class NumeracjaTest(unittest.TestCase):
    def test_przenumeruj_po_pierwszym_wystapieniu(self):
        odcinki = [D.Odcinek(5.0, 6.0, 2), D.Odcinek(0.0, 1.0, 0), D.Odcinek(2.0, 3.0, 2)]
        D.przenumeruj(odcinki)
        self.assertEqual([o.mowca for o in odcinki], [1, 0, 1])

    def test_odrzuc_szum(self):
        odcinki = [D.Odcinek(0.0, 5.0, 0), D.Odcinek(5.0, 5.3, 1), D.Odcinek(6.0, 9.0, 2)]
        zostale, szum = D.odrzuc_szum(odcinki)
        self.assertEqual(szum, 1)
        self.assertEqual({o.mowca for o in zostale}, {0, 2})


if __name__ == "__main__":
    unittest.main()
