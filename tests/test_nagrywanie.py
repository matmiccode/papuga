"""Testy nagrywania bez urządzeń: oś czasu, wyrównywanie, miks, FLAC, naprawa.

PortAudio nie jest tu potrzebny — sprawdzamy to, co leży między callbackiem
a plikiem. PyAV (resampling, FLAC) jest prawdziwy.
"""

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whisper_automat.core import nagrywanie as N  # noqa: E402


def sinus(n: int, rate: int = 48000, kanaly: int = 2, amp: float = 0.3, f: float = 440.0) -> bytes:
    t = np.arange(n, dtype=np.float32) / rate
    fala = (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)
    return np.repeat(fala, kanaly).tobytes()


def cisza(n: int, kanaly: int = 2) -> bytes:
    return np.zeros(n * kanaly, dtype=np.float32).tobytes()


class OsCzasuTest(unittest.TestCase):
    def test_zapis_i_odczyt(self):
        os_ = N.OsCzasu(pojemnosc=N.RATE)
        dane = np.arange(1, 1601, dtype=np.int16)
        os_.wpisz(0, dane)
        np.testing.assert_array_equal(os_.pobierz(0, 1600), dane)
        self.assertEqual(os_.wydane, 1600)
        self.assertEqual(os_.najdalej, 1600)

    def test_luka_to_zera(self):
        os_ = N.OsCzasu(pojemnosc=N.RATE * 5)
        os_.wpisz(0, np.full(1600, 7, dtype=np.int16))
        os_.wpisz(N.RATE * 3, np.full(1600, 9, dtype=np.int16))
        self.assertTrue((os_.pobierz(1600, 1600) == 0).all())
        self.assertTrue((os_.pobierz(N.RATE * 3, 1600) == 9).all())

    def test_spoznione_odrzucane(self):
        os_ = N.OsCzasu(pojemnosc=N.RATE)
        os_.pobierz(0, 3200)
        os_.wpisz(1000, np.ones(1000, dtype=np.int16))
        self.assertEqual(os_.odrzucone, 1000)
        os_.wpisz(3000, np.ones(400, dtype=np.int16))
        self.assertEqual(os_.odrzucone, 1200)
        self.assertTrue((os_.pobierz(3200, 200) == 1).all())

    def test_zawijanie(self):
        os_ = N.OsCzasu(pojemnosc=4000)
        os_.pobierz(0, 3000)
        os_.wpisz(3000, np.full(2000, 5, dtype=np.int16))
        self.assertTrue((os_.pobierz(3000, 2000) == 5).all())


class ZrodloTest(unittest.TestCase):
    def setUp(self):
        self.zegar = N.Zegar(t0=100.0)
        self.os = N.OsCzasu(pojemnosc=N.RATE * 20)
        self.z = N.Zrodlo("test", 48000, 2, self.zegar, self.os)

    def blok_ms(self):
        return 48000 * 50 // 1000  # 50 ms jak w _Tor

    def test_rowne_pakiety_leza_po_kolei(self):
        n = self.blok_ms()
        for i in range(40):  # 2 s; czas pakietu to czas jego końca
            self.z.przyjmij(100.05 + i * 0.05, sinus(n))
        self.z.zakoncz()
        self.assertEqual(self.z.luki, 0)
        self.assertEqual(self.z.wyprzedzenia, 0)
        # ~2 s audio w 16 kHz, z dokładnością do opóźnienia resamplera.
        self.assertGreater(self.os.najdalej, N.RATE * 2 - 200)
        self.assertLessEqual(self.os.najdalej, N.RATE * 2 + 50)
        self.assertEqual(self.os.odrzucone, 0)
        sygnal = self.os.pobierz(N.RATE // 2, N.RATE)
        self.assertGreater(N.rms_dbfs(sygnal), -20.0)

    def test_luka_przeskakuje(self):
        n = self.blok_ms()
        for i in range(10):
            self.z.przyjmij(100.05 + i * 0.05, sinus(n))
        self.z.przyjmij(100.05 + 3.0, sinus(n))  # 3 s przerwy w pakietach
        self.assertEqual(self.z.luki, 1)
        self.assertGreaterEqual(self.os.najdalej, 3 * N.RATE)
        # Między pakietami zostały zera.
        self.assertTrue((self.os.pobierz(N.RATE, N.RATE) == 0).all())

    def test_dryf_korygowany_tylko_w_ciszy(self):
        n = self.blok_ms()
        # Zegar ucieka o 1 ms na pakiet (20 ms/s): sygnał — brak korekt.
        for i in range(20):
            self.z.przyjmij(100.051 + i * 0.051, sinus(n))
        self.assertEqual(self.z.korekty, 0)
        # Cisza — korekty zaczynają nadrabiać.
        for i in range(20, 60):
            self.z.przyjmij(100.051 + i * 0.051, cisza(n))
        self.assertGreater(self.z.korekty, 0)
        self.assertEqual(self.z.luki, 0)

    def test_pakiety_hurtem_po_zatorze(self):
        n = self.blok_ms()
        self.z.przyjmij_wiele([(100.05 + i * 0.05, sinus(n)) for i in range(10)])
        # Zator 0,4 s: osiem pakietów przychodzi naraz z tym samym czasem
        # (końca ostatniego). Grupa dostaje czasy wstecz — bez luki
        # i bez wyprzedzenia, dźwięk ląduje tam, gdzie był naprawdę.
        self.z.przyjmij_wiele([(100.55 + 0.35, sinus(n))] * 8)
        self.assertEqual(self.z.luki, 0)
        self.assertEqual(self.z.wyprzedzenia, 0)
        self.assertEqual(self.os.odrzucone, 0)
        # 18 pakietów po 50 ms = 0,9 s ciągłego sygnału.
        self.assertGreater(self.os.najdalej, int(N.RATE * 0.9) - 200)

    def test_pojedynczy_pakiet_z_przyszlosci_nie_jest_ucinany(self):
        n = self.blok_ms()
        self.z.przyjmij_wiele([(100.05 + i * 0.05, sinus(n)) for i in range(10)])
        self.z.przyjmij(100.55 - 0.3, sinus(n))  # zegar „cofnięty” o 0,3 s
        self.assertEqual(self.z.wyprzedzenia, 1)
        self.assertEqual(self.os.odrzucone, 0)


class MiksTest(unittest.TestCase):
    def test_suma_bez_przesteru(self):
        m = np.full(100, 6000, dtype=np.int16)
        s = np.full(100, 4000, dtype=np.int16)
        self.assertTrue((N.miksuj(m, s) == 10000).all())

    def test_limiter(self):
        m = np.full(100, 25000, dtype=np.int16)
        y = N.miksuj(m, m)
        self.assertTrue((y <= 32767).all())
        self.assertGreater(int(y[0]), 25000)

    def test_rms(self):
        self.assertEqual(N.rms_dbfs(np.zeros(100, dtype=np.int16)), -120.0)
        self.assertAlmostEqual(N.rms_dbfs(np.full(100, 32767, dtype=np.int16)), 0.0, places=1)


class ZapisTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nagr-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def dekoduj(self, plik: Path) -> np.ndarray:
        import av

        out = []
        with av.open(str(plik)) as k:
            try:
                for r in k.decode(audio=0):
                    out.append(r.to_ndarray()[0])
            except av.error.FFmpegError:
                pass
        return np.concatenate(out) if out else np.zeros(0, np.int16)

    def test_flac_zamkniety_ma_dlugosc(self):
        import av

        plik = self.tmp / "a.flac"
        z = N.ZapisFlac(plik)
        dane = (np.sin(np.arange(N.RATE * 2) / 20.0) * 10000).astype(np.int16)
        for i in range(0, dane.size, N.BLOK):
            z.zapisz(dane[i:i + N.BLOK])
        z.zamknij()
        with av.open(str(plik)) as k:
            s = k.streams.audio[0]
            self.assertEqual(s.rate, N.RATE)
            self.assertEqual(s.channels, 1)
            self.assertAlmostEqual(float(s.duration * s.time_base), 2.0, places=2)
        np.testing.assert_array_equal(self.dekoduj(plik), dane)

    def test_urwany_flac_da_sie_odzyskac(self):
        plik = self.tmp / "Spotkanie 2026-10-08 10-00.flac"
        z = N.ZapisFlac(plik)
        dane = (np.sin(np.arange(N.RATE * 3) / 20.0) * 10000).astype(np.int16)
        for i in range(0, dane.size, N.BLOK):
            z.zapisz(dane[i:i + N.BLOK])
        z.fsync()
        # Awaria: proces ginie bez zamknięcia; plik dodatkowo ucięty.
        z._plik.close()
        bajty = plik.read_bytes()
        plik.write_bytes(bajty[:-700])
        N.sciezka_znacznika(plik).write_text(json.dumps({"pid": 999999999}), encoding="utf-8")

        self.assertEqual(N.znajdz_niedokonczone(self.tmp), [plik])
        cel = N.napraw_nagranie(plik)
        self.assertTrue(cel.name.endswith("(odzyskane).flac"))
        self.assertFalse(plik.exists())
        self.assertFalse(N.sciezka_znacznika(plik).exists())
        odzyskane = self.dekoduj(cel)
        # Ginie najwyżej urwany blok FLAC (4096 próbek) plus to, co ucięliśmy.
        self.assertGreaterEqual(odzyskane.size, dane.size - N.RATE // 2)
        self.assertEqual(N.znajdz_niedokonczone(self.tmp), [])

    def test_znacznik_zywego_procesu_zostaje(self):
        plik = self.tmp / "Spotkanie 2026-10-08 11-00.flac"
        plik.write_bytes(b"x" * 10)
        N.zapisz_znacznik(plik)  # pid bieżącego procesu
        self.assertEqual(N.znajdz_niedokonczone(self.tmp), [])
        N.usun_znacznik(plik)

    def test_nazwa_pliku(self):
        kiedy = datetime(2026, 10, 8, 14, 30)
        a = N.nazwa_pliku(self.tmp, kiedy)
        self.assertEqual(a.name, "Spotkanie 2026-10-08 14-30.flac")
        a.write_bytes(b"")
        b = N.nazwa_pliku(self.tmp, kiedy)
        self.assertEqual(b.name, "Spotkanie 2026-10-08 14-30 (2).flac")


class UrzadzeniaTest(unittest.TestCase):
    def test_znajdz(self):
        a = N.UrzadzenieAudio(1, "Słuchawki", 2, 48000, 5, False)
        b = N.UrzadzenieAudio(2, "Głośniki", 2, 48000, 6, True)
        self.assertIs(N._znajdz([a, b], "Słuchawki"), a)
        self.assertIs(N._znajdz([a, b], ""), b)
        self.assertIs(N._znajdz([a, b], "nie ma"), b)
        self.assertIs(N._znajdz([a], ""), a)
        self.assertIsNone(N._znajdz([], ""))


if __name__ == "__main__":
    unittest.main()
