"""Testy obrazu spotkania bez okien i kart graficznych.

Wybór okna, rozmiar klatki i lista odcinków — czyste funkcje. Składanie
na prawdziwym ffmpeg: odcinki robione z `testsrc` (MPEG-TS jak z nagrania),
dźwięk z `sine`; sprawdzamy, gdzie w gotowym MKV wypadają klatki.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whisper_automat.core import wideo as W  # noqa: E402
from whisper_automat.core.probe import find_ffmpeg  # noqa: E402

FFMPEG, FFPROBE = find_ffmpeg()


def okno(tytul, program, szer=1200, wys=800, zminimalizowane=False, hwnd=1):
    return W.Okno(hwnd, tytul, program, szer, wys, zminimalizowane)


class OknaTest(unittest.TestCase):
    def test_spotkanie(self):
        self.assertTrue(W.czy_spotkanie(okno("Spotkanie Zoom", "Zoom.exe")))
        self.assertTrue(W.czy_spotkanie(okno("Czat | Microsoft Teams", "ms-teams.exe")))
        self.assertTrue(W.czy_spotkanie(okno("Meet - abc-defg-hij - Google Chrome", "chrome.exe")))
        self.assertFalse(W.czy_spotkanie(okno("Poczta - Google Chrome", "chrome.exe")))
        self.assertFalse(W.czy_spotkanie(okno("Notatki — Notatnik", "notepad.exe")))

    def test_opis(self):
        self.assertEqual(W.opis_okna(okno("Spotkanie Zoom", "Zoom.exe")),
                         "Zoom — Spotkanie Zoom  (Zoom.exe)")
        opis = W.opis_okna(okno("x" * 80, "notepad.exe", zminimalizowane=True))
        self.assertIn("…", opis)
        self.assertTrue(opis.endswith(W.t("zminimalizowane")))
        self.assertEqual(W.nazwa_programu("MS-TEAMS.EXE"), "Teams")
        self.assertEqual(W.nazwa_programu("obs64.exe"), "obs64")

    def test_wybor_zapamietanego(self):
        lista = [okno("Czat | Microsoft Teams", "ms-teams.exe", 1600, 1000, hwnd=1),
                 okno("Przegląd projektu | Spotkanie | Microsoft Teams", "ms-teams.exe",
                      1200, 800, hwnd=2),
                 okno("Spotkanie Zoom", "Zoom.exe", hwnd=3)]
        # Ten sam tytuł wygrywa…
        self.assertEqual(W.wybierz_okno(lista, "ms-teams.exe", "Czat | Microsoft Teams").hwnd, 1)
        # …a nowe spotkanie (inny tytuł) — okno, które wygląda na rozmowę.
        self.assertEqual(W.wybierz_okno(lista, "MS-TEAMS.exe", "Stare spotkanie").hwnd, 2)
        self.assertIsNone(W.wybierz_okno(lista, "webex.exe", ""))
        self.assertIsNone(W.wybierz_okno(lista, "", ""))
        # Zminimalizowane przegrywa z widocznym.
        lista = [okno("Zoom", "Zoom.exe", zminimalizowane=True, hwnd=1),
                 okno("Zoom", "Zoom.exe", 800, 600, hwnd=2)]
        self.assertEqual(W.wybierz_okno(lista, "Zoom.exe", "Zoom").hwnd, 2)

    def test_dopasuj(self):
        self.assertEqual(W.dopasuj(1920, 1080), (1920, 1080))
        self.assertEqual(W.dopasuj(3840, 2160), (1920, 1080))
        self.assertEqual(W.dopasuj(2560, 1600), (1728, 1080))
        self.assertEqual(W.dopasuj(1366, 768), (1366, 768))
        self.assertEqual(W.dopasuj(1365, 767), (1364, 766))  # parzyste dla NV12
        self.assertEqual(W.dopasuj(0, 0), (1920, 1080))


class EkranyTest(unittest.TestCase):
    def setUp(self):
        self.glowny = W.Ekran(11, "\\\\.\\DISPLAY1", 1, 1920, 1080, glowny=True)
        self.benq = W.Ekran(22, "\\\\.\\DISPLAY2", 2, 2560, 1440, nazwa="BenQ G2420HDB")
        self.trzeci = W.Ekran(33, "\\\\.\\DISPLAY3", 3, 1280, 1024)

    def test_opis(self):
        self.assertEqual(W.opis_ekranu(self.glowny, 1), W.t("Cały ekran ({rozmiar})").format(
            rozmiar="1920×1080"))
        self.assertIn("1920×1080", W.opis_ekranu(self.glowny, 2))
        self.assertIn("BenQ G2420HDB", W.opis_ekranu(self.benq, 2))
        self.assertIn("3", W.opis_ekranu(self.trzeci, 3))

    def test_wybor(self):
        lista = [self.glowny, self.benq]
        self.assertIs(W.wybierz_ekran(lista, "\\\\.\\DISPLAY2"), self.benq)
        # Monitor odłączony od czasu wyboru — nagrywamy główny.
        self.assertIs(W.wybierz_ekran(lista, "\\\\.\\DISPLAY3"), self.glowny)
        self.assertIsNone(W.wybierz_ekran([], "\\\\.\\DISPLAY1"))

    def test_cel_i_rozmiar(self):
        self.assertEqual(W.cel(self.benq), "hmonitor=22")
        self.assertEqual(W.cel(okno("Zoom", "Zoom.exe", hwnd=7)), "hwnd=7")
        self.assertEqual(W.rozmiar_zrodla(self.benq), (1920, 1080))
        self.assertEqual(W.rozmiar_zrodla(self.trzeci), (1280, 1024))
        self.assertIn("gfxcapture=hmonitor=22:", W.filtr_obrazu(W.cel(self.benq), 1920, 1080))


class ListaTest(unittest.TestCase):
    def test_odcinki_z_pauza(self):
        lista = W.lista_concat([
            {"plik": "a.obraz-1.ts", "poczatek": 0.0, "pierwsza": 0.5, "koniec": 5.0},
            {"plik": "a.obraz-2.ts", "poczatek": 5.0, "pierwsza": 5.4, "koniec": 8.0},
        ])
        self.assertEqual(lista.splitlines(), [
            "ffconcat version 1.0",
            "file 'a.obraz-1.ts'", "inpoint -0.500000", "outpoint 4.500000", "duration 5.000000",
            "file 'a.obraz-2.ts'", "inpoint -0.400000", "outpoint 2.600000",
        ])

    def test_odcinek_bez_klatek_pominiety(self):
        # Okno zminimalizowane przez cały drugi odcinek: trzeci dosuwa się
        # na swoje miejsce długością pierwszego wpisu.
        lista = W.lista_concat([
            {"plik": "1.ts", "poczatek": 0.0, "pierwsza": 1.0, "koniec": 3.0},
            {"plik": "2.ts", "poczatek": 3.0, "pierwsza": None, "koniec": 6.0},
            {"plik": "3.ts", "poczatek": 6.0, "pierwsza": 6.5, "koniec": None},
        ])
        self.assertIn("duration 6.000000", lista)
        self.assertNotIn("2.ts", lista)
        self.assertTrue(lista.rstrip().endswith("inpoint -0.500000"))

    def test_apostrof_w_nazwie(self):
        lista = W.lista_concat([{"plik": "O'Brien.obraz-1.ts", "poczatek": 0.0,
                                 "pierwsza": 0.2, "koniec": None}])
        self.assertIn("file 'O'\\''Brien.obraz-1.ts'", lista)


class PodgladTest(unittest.TestCase):
    """Podgląd na karcie: zmniejszenie z zachowaniem proporcji, PPM dla Tk."""

    def test_zmniejsz_proporcje_i_srednia(self):
        import numpy as np

        # 1936×1048 (okno z ramką) w 320×180: szerokość pełna, wysokość z proporcji.
        obraz = np.zeros((1048, 1936, 3), dtype=np.uint8)
        obraz[:, ::2] = 200  # paski co piksel: średnia, nie co n-ty piksel
        maly = W.zmniejsz(obraz, (320, 180))
        self.assertEqual(maly.shape, (173, 320, 3))
        self.assertEqual(maly.dtype, np.uint8)
        self.assertTrue(80 <= int(maly.mean()) <= 120)

    def test_maly_obraz_bez_powiekszania(self):
        import numpy as np

        obraz = np.full((50, 80, 3), 7, dtype=np.uint8)
        self.assertEqual(W.zmniejsz(obraz, (320, 180)).shape, (50, 80, 3))

    def test_ppm(self):
        import numpy as np

        dane = W.ppm(np.zeros((2, 3, 3), dtype=np.uint8))
        self.assertTrue(dane.startswith(b"P6 3 2 255\n"))
        self.assertEqual(len(dane), len(b"P6 3 2 255\n") + 18)


@unittest.skipUnless(FFMPEG and FFPROBE, "brak ffmpeg")
class SkladanieTest(unittest.TestCase):
    def setUp(self):
        self.katalog = Path(tempfile.mkdtemp(prefix="test-wideo-"))
        self.audio = self.katalog / "Spotkanie test.flac"
        self._ffmpeg("-f", "lavfi", "-i", "sine=f=440:d=8:r=16000", "-ac", "2", "-c:a", "flac",
                     str(self.audio))
        for n, dlugosc in ((1, 3), (2, 2)):
            self._ffmpeg("-f", "lavfi", "-i", f"testsrc=s=320x240:r=15:d={dlugosc}",
                         "-c:v", "libx264", "-bf", "0", "-muxdelay", "0", "-muxpreload", "0",
                         "-f", "mpegts", str(self.katalog / f"Spotkanie test.obraz-{n}.ts"))
        self.odcinki = [
            {"plik": "Spotkanie test.obraz-1.ts", "poczatek": 0.0, "pierwsza": 0.5, "koniec": 5.0},
            {"plik": "Spotkanie test.obraz-2.ts", "poczatek": 5.0, "pierwsza": 5.4, "koniec": None},
        ]

    def tearDown(self):
        shutil.rmtree(self.katalog, ignore_errors=True)

    def _ffmpeg(self, *argumenty):
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *argumenty],
                       check=True, stdin=subprocess.DEVNULL)

    def _klatki(self, plik):
        wynik = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v", "-show_entries", "packet=pts_time",
             "-of", "csv=p=0", str(plik)], capture_output=True, text=True, check=True)
        return sorted(float(x.strip(",")) for x in wynik.stdout.split())

    def _sprawdz(self, mkv):
        self.assertEqual(mkv.name, "Spotkanie test.mkv")
        klatki = self._klatki(mkv)
        self.assertEqual(len(klatki), 75)
        self.assertAlmostEqual(klatki[0], 0.5, places=2)       # pierwsza klatka odcinka 1
        self.assertAlmostEqual(klatki[44], 0.5 + 44 / 15, places=2)
        self.assertAlmostEqual(klatki[45], 5.4, places=2)      # odcinek 2 po pauzie
        wynik = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries",
             "stream=codec_name,channels", "-of", "csv=p=0", str(mkv)],
            capture_output=True, text=True, check=True)
        self.assertEqual(wynik.stdout.strip().strip(","), "aac,1")
        # Po złożeniu zostają tylko nagranie i obraz.
        self.assertEqual(sorted(p.name for p in self.katalog.iterdir()),
                         ["Spotkanie test.flac", "Spotkanie test.mkv"])

    def test_zloz(self):
        self._sprawdz(W.zloz_odcinki(FFMPEG, self.audio, self.odcinki))

    def test_dokoncz_po_przerwanym_skladaniu(self):
        lista = self.katalog / f"Spotkanie test{W.LISTA}"
        lista.write_text(W.lista_concat(self.odcinki), encoding="utf-8")
        stary_ffmpeg = W.ffmpeg
        W.ffmpeg = lambda: FFMPEG
        try:
            wynik = W.dokoncz_skladanie(self.katalog)
        finally:
            W.ffmpeg = stary_ffmpeg
        self.assertEqual(len(wynik), 1)
        self._sprawdz(wynik[0])

    def test_fragmenty_jako_rozdzialy(self):
        mkv = W.zloz_odcinki(FFMPEG, self.audio, self.odcinki,
                             znaczniki=[{"czas": 1.0, "koniec": 4.0}, {"czas": 6.0, "koniec": None}])
        wynik = subprocess.run(
            [FFPROBE, "-v", "error", "-show_chapters", "-of", "json", str(mkv)],
            capture_output=True, text=True, check=True)
        import json

        rozdzialy = json.loads(wynik.stdout)["chapters"]
        self.assertEqual([(round(float(r["start_time"]), 2), round(float(r["end_time"]), 2),
                           r["tags"]["title"]) for r in rozdzialy],
                         [(1.0, 4.0, W.t("Fragment {n}").format(n=1)),
                          (6.0, 7.0, W.t("Zaznaczony moment"))])
        self._sprawdz(mkv)

    def test_bez_klatek_nic(self):
        odcinki = [dict(o, pierwsza=None) for o in self.odcinki]
        self.assertIsNone(W.zloz_odcinki(FFMPEG, self.audio, odcinki))
        self.assertEqual([p.name for p in self.katalog.iterdir()], ["Spotkanie test.flac"])


if __name__ == "__main__":
    unittest.main()
