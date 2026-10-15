"""Testy bazy głosów bez modelu: folder, pliki, dopasowanie, zbieranie odcinków.

Odcisk jest tu udawany (`licz_odcisk`), więc testy nie potrzebują sherpa-onnx.
Sprawdzenie na prawdziwym modelu i głosach SAPI: PROGRESS.md, sesja 2026-10-15.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whisper_automat.core import glosy as G  # noqa: E402
from whisper_automat.core.diarization import Odcinek  # noqa: E402

RATE = 16000


def jednostkowy(*skladowe) -> list:
    v = np.zeros(8)
    for i, w in skladowe:
        v[i] = w
    v /= np.linalg.norm(v)
    return [float(x) for x in v]


class FolderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="glosy-"))
        self._env = os.environ.get(G.ZMIENNA)
        os.environ[G.ZMIENNA] = str(self.tmp / "Glosy")

    def tearDown(self):
        if self._env is None:
            os.environ.pop(G.ZMIENNA, None)
        else:
            os.environ[G.ZMIENNA] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_katalog_ze_zmiennej(self):
        self.assertEqual(G.katalog(), self.tmp / "Glosy")
        self.assertTrue(G.dostepna())

    def test_nazwa_pliku(self):
        self.assertEqual(G.nazwa_pliku("Jan.Kowalski"), "jan.kowalski.json")
        self.assertEqual(G.nazwa_pliku("FIRMA\\jan kowalski"), "firma_jan_kowalski.json")
        self.assertEqual(G.nazwa_pliku("..."), "glos.json")

    def test_zapis_i_odczyt(self):
        g = G.nowy_wpis("Anna Nowak", jednostkowy((0, 1.0)), 21.5)
        plik = G.zapisz(g)
        self.assertEqual(plik, self.tmp / "Glosy" / G.nazwa_pliku(G.login()))
        dane = json.loads(plik.read_text(encoding="utf-8"))
        self.assertEqual(dane["imie"], "Anna Nowak")
        self.assertEqual(dane["model"], G.model_id())
        self.assertNotIn("nagranie", dane)
        baza = G.wczytaj()
        self.assertEqual([b.imie for b in baza], ["Anna Nowak"])
        self.assertEqual(G.moj_wpis().imie, "Anna Nowak")
        self.assertTrue(G.usun_moj())
        self.assertFalse(G.usun_moj())
        self.assertEqual(G.wczytaj(), [])

    def test_pomija_obcy_model_i_smieci(self):
        folder = self.tmp / "Glosy"
        folder.mkdir()
        (folder / "stary.json").write_text(json.dumps({
            "imie": "Stary", "login": "stary", "model": "inny", "odcisk": [1.0]}), encoding="utf-8")
        (folder / "zepsuty.json").write_text("{", encoding="utf-8")
        (folder / "dobry.json").write_text(json.dumps({
            "imie": "Dobry", "login": "dobry", "model": G.model_id(), "odcisk": [1.0, 0.0]}),
            encoding="utf-8")
        komunikaty = []
        baza = G.wczytaj(log=komunikaty.append)
        self.assertEqual([b.imie for b in baza], ["Dobry"])
        self.assertEqual(len(komunikaty), 1)

    def test_brak_folderu_to_pusta_baza(self):
        self.assertEqual(G.wczytaj(), [])


class DopasowanieTest(unittest.TestCase):
    def setUp(self):
        self.baza = [
            G.Glos("Anna", "anna", jednostkowy((0, 1.0))),
            G.Glos("Piotr", "piotr", jednostkowy((1, 1.0))),
        ]

    def test_podobienstwo(self):
        self.assertAlmostEqual(G.podobienstwo([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(G.podobienstwo([1, 0], [0, 1]), 0.0)
        self.assertEqual(G.podobienstwo([1, 0], [1]), -1.0)

    def test_dopasuj(self):
        blisko_anny = jednostkowy((0, 1.0), (1, 0.3))
        glos, ocena = G.dopasuj(blisko_anny, self.baza)
        self.assertEqual(glos.imie, "Anna")
        self.assertGreater(ocena, 0.9)
        self.assertIsNone(G.dopasuj(jednostkowy((5, 1.0)), self.baza))
        # Dwaj równie dobrzy kandydaci — nie zgadujemy.
        self.assertIsNone(G.dopasuj(jednostkowy((0, 1.0), (1, 1.0)), self.baza))
        self.assertIsNone(G.dopasuj([1.0], []))


class RozpoznajTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="glosy-"))
        # Mówca 0: ton 220 Hz w 0–4 s; mówca 1: ton 880 Hz w 5–9 s; cisza między.
        t = np.arange(RATE * 10) / RATE
        sygnal = np.zeros(RATE * 10, np.float32)
        sygnal[: 4 * RATE] = 0.3 * np.sin(2 * np.pi * 220 * t[: 4 * RATE])
        sygnal[5 * RATE: 9 * RATE] = 0.3 * np.sin(2 * np.pi * 880 * t[: 4 * RATE])
        self.wav = self.tmp / "a.wav"
        with wave.open(str(self.wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes((sygnal * 32767).astype(np.int16).tobytes())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def udawany_odcisk(probki, rate):
        """„Odcisk” = udział energii w niskich i wysokich częstotliwościach."""
        widmo = np.abs(np.fft.rfft(probki))
        f = np.fft.rfftfreq(probki.size, 1.0 / rate)
        niskie = float(widmo[f < 500].sum())
        wysokie = float(widmo[f >= 500].sum())
        v = np.array([niskie, wysokie])
        return [float(x) for x in v / (np.linalg.norm(v) or 1.0)]

    def test_rozpoznaje_po_odcinkach(self):
        baza = [G.Glos("Niski", "niski", [1.0, 0.0]), G.Glos("Wysoki", "wysoki", [0.0, 1.0])]
        odcinki = [Odcinek(0.0, 4.0, 0), Odcinek(5.0, 9.0, 1), Odcinek(9.5, 9.8, 2)]
        komunikaty = []
        wynik = G.rozpoznaj_mowcow(self.wav, odcinki, log=komunikaty.append, baza=baza,
                                   licz_odcisk=self.udawany_odcisk)
        self.assertEqual({n: imie for n, (imie, _o) in wynik.items()}, {0: "Niski", 1: "Wysoki"})
        self.assertTrue(all(o > 0.9 for _i, o in wynik.values()))

    def test_pusta_baza(self):
        self.assertEqual(G.rozpoznaj_mowcow(self.wav, [Odcinek(0.0, 4.0, 0)], baza=[]), {})

    def test_bez_ciszy(self):
        probki = np.zeros(RATE, np.float32)
        probki[: RATE // 2] = 0.2
        self.assertEqual(G.bez_ciszy(probki, RATE).size, RATE // 2)


if __name__ == "__main__":
    unittest.main()
