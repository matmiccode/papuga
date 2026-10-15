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

    def test_przeplot(self):
        m = np.array([1, 2, 3], dtype=np.int16)
        s = np.array([-1, -2, -3, -4], dtype=np.int16)
        np.testing.assert_array_equal(N.przeplot(m, s), [1, -1, 2, -2, 3, -3])


class ZapisTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nagr-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def dekoduj(self, plik: Path) -> np.ndarray:
        """Próbki int16 — dla stereo przeplecione, jak w `przeplot`."""
        import av

        out = []
        with av.open(str(plik)) as k:
            try:
                for r in k.decode(audio=0):
                    out.append(r.to_ndarray()[0])
            except av.error.FFmpegError:
                pass
        return np.concatenate(out) if out else np.zeros(0, np.int16)

    def test_stereo_z_tagiem(self):
        import av

        plik = self.tmp / "Spotkanie 2026-10-15 09-00.flac"
        z = N.ZapisFlac(plik, kanaly=2, tagi=N.tagi_nagrania(plik))
        m = (np.sin(np.arange(N.RATE * 2) / 20.0) * 10000).astype(np.int16)
        s = (np.sin(np.arange(N.RATE * 2) / 7.0) * 8000).astype(np.int16)
        for i in range(0, m.size, N.BLOK):
            z.zapisz(N.przeplot(m[i:i + N.BLOK], s[i:i + N.BLOK]))
        z.zamknij()
        self.assertEqual(z.probki, m.size)
        with av.open(str(plik)) as k:
            st = k.streams.audio[0]
            self.assertEqual(st.channels, 2)
            self.assertEqual(st.rate, N.RATE)
            self.assertEqual(k.metadata.get(N.TAG), N.TAG_WARTOSC)
            self.assertEqual(k.metadata.get("title"), plik.stem)
        dane = self.dekoduj(plik)
        np.testing.assert_array_equal(dane[0::2], m)
        np.testing.assert_array_equal(dane[1::2], s)

    def test_urwany_stereo_zachowuje_kanaly_i_tag(self):
        import av

        plik = self.tmp / "Spotkanie 2026-10-15 09-30.flac"
        z = N.ZapisFlac(plik, kanaly=2, tagi=N.tagi_nagrania(plik))
        m = (np.sin(np.arange(N.RATE * 3) / 20.0) * 10000).astype(np.int16)
        for i in range(0, m.size, N.BLOK):
            z.zapisz(N.przeplot(m[i:i + N.BLOK], -m[i:i + N.BLOK]))
        z.fsync()
        z._plik.close()
        plik.write_bytes(plik.read_bytes()[:-700])
        N.sciezka_znacznika(plik).write_text(json.dumps({"pid": 999999999}), encoding="utf-8")
        cel = N.napraw_nagranie(plik)
        with av.open(str(cel)) as k:
            self.assertEqual(k.streams.audio[0].channels, 2)
            self.assertEqual(k.metadata.get(N.TAG), N.TAG_WARTOSC)
            self.assertEqual(k.metadata.get("title"), cel.stem)
        dane = self.dekoduj(cel)
        n = dane.size // 2
        self.assertGreaterEqual(n, m.size - N.RATE // 2)
        np.testing.assert_array_equal(dane[0::2][:n], m[:n])
        np.testing.assert_array_equal(dane[1::2][:n], -m[:n])

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

    def test_znaczniki_obok_nagrania(self):
        plik = self.tmp / "Spotkanie 2026-10-15 11-00.flac"
        self.assertIsNone(N.zapisz_znaczniki(plik, []))
        cel = N.zapisz_znaczniki(plik, [{"czas": 734.2, "opis": ""}, {"czas": 12.0, "opis": "decyzja"}])
        self.assertEqual(cel.name, "Spotkanie 2026-10-15 11-00.znaczniki.json")
        self.assertEqual(N.wczytaj_znaczniki(plik),
                         [{"czas": 12.0, "opis": "decyzja"}, {"czas": 734.2, "opis": ""}])
        self.assertEqual(N.wczytaj_znaczniki(self.tmp / "nie ma.flac"), [])
        cel.write_text("to nie json", encoding="utf-8")
        self.assertEqual(N.wczytaj_znaczniki(plik), [])

    def test_naprawa_przenosi_znaczniki(self):
        plik = self.tmp / "Spotkanie 2026-10-15 11-30.flac"
        z = N.ZapisFlac(plik, kanaly=2, tagi=N.tagi_nagrania(plik))
        dane = (np.sin(np.arange(N.RATE * 2) / 20.0) * 10000).astype(np.int16)
        for i in range(0, dane.size, N.BLOK):
            z.zapisz(N.przeplot(dane[i:i + N.BLOK], dane[i:i + N.BLOK]))
        z.fsync()
        z._plik.close()
        plik.write_bytes(plik.read_bytes()[:-500])
        N.zapisz_znaczniki(plik, [{"czas": 1.0, "opis": ""}])
        N.sciezka_znacznika(plik).write_text(json.dumps({"pid": 999999999}), encoding="utf-8")
        cel = N.napraw_nagranie(plik)
        self.assertFalse(N.sciezka_znacznikow(plik).exists())
        self.assertEqual(N.wczytaj_znaczniki(cel), [{"czas": 1.0, "opis": ""}])

    def test_znacznik_tylko_w_trakcie(self):
        n = N.Nagrywarka()
        self.assertIsNone(n.znacznik())
        n.stan = n.NAGRYWA
        n.czas_trwania = 12.345
        self.assertEqual(n.znacznik("x"), {"czas": 12.345, "opis": "x"})
        n.stan = n.PAUZA
        self.assertEqual(n.znacznik()["czas"], 12.345)
        self.assertEqual(len(n.znaczniki), 2)
        n.stan = n.BEZCZYNNA

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

    def test_czy_przepiac(self):
        # Tylko „domyślne” w ustawieniach i inna nazwa domyślnego Windows.
        self.assertTrue(N._czy_przepiac("", "Mikrofon wbudowany", "Mikrofon zestawu"))
        self.assertFalse(N._czy_przepiac("", "Mikrofon zestawu", "Mikrofon zestawu"))
        self.assertFalse(N._czy_przepiac("Mikrofon wbudowany", "Mikrofon wbudowany", "Mikrofon zestawu"))
        self.assertFalse(N._czy_przepiac("", "Mikrofon wbudowany", ""))
        self.assertFalse(N._czy_przepiac("", "", "Mikrofon zestawu"))

    def test_domyslne_z_punktow(self):
        from whisper_automat.core import urzadzenia as U

        punkty = [U.PunktAudio("Mik A", True, U.MIKROFON, U.WBUDOWANE, False, False, "a"),
                  U.PunktAudio("Mik B", True, U.MIKROFON, U.USB, True, True, "b"),
                  U.PunktAudio("Głośniki", False, U.GLOSNIKI, U.WBUDOWANE, True, False, "c")]
        self.assertEqual(N.Nagrywarka._domyslne(punkty, wejscie=True), "Mik B")
        self.assertEqual(N.Nagrywarka._domyslne(punkty, wejscie=False), "Głośniki")
        self.assertEqual(N.Nagrywarka._domyslne([], wejscie=True), "")


if __name__ == "__main__":
    unittest.main()
