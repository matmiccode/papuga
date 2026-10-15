"""Testy „co jest czym” wśród urządzeń audio — bez COM, na udawanych punktach.

Prawdziwe wywołanie COM sprawdza tylko jeden test dymny na Windows: ma
oddać listę (albo pustą, gdy maszyna nie ma dźwięku), nigdy wyjątek.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whisper_automat.core import urzadzenia as U  # noqa: E402
from whisper_automat import teksty  # noqa: E402

WBUDOWANY_MIK = U.PunktAudio("Zestaw mikrofonów (Intel Smart Sound)", True, U.MIKROFON,
                             U.WBUDOWANE, domyslne=True, komunikacyjne=True, id="m1")
GLOSNIKI = U.PunktAudio("Głośniki (Realtek(R) Audio)", False, U.GLOSNIKI, U.WBUDOWANE,
                        domyslne=True, id="g1")
MONITOR = U.PunktAudio("BenQ G2420HDB (HD Audio Driver for Display Audio)", False,
                       U.MONITOR, U.WBUDOWANE, komunikacyjne=True, id="g2")
POLY_MIK = U.PunktAudio("Mikrofon zestawu słuchawkowego (Poly Blackwire 3320 Series)", True,
                        U.ZESTAW, U.USB, id="m2")
POLY_WY = U.PunktAudio("Zestaw słuchawkowy (Poly Blackwire 3320 Series)", False,
                       U.ZESTAW, U.USB, id="g3")
BT_MIK = U.PunktAudio("Zestaw słuchawkowy (JBL Hands-Free AG Audio)", True, U.ZESTAW,
                      U.BLUETOOTH, komunikacyjne=True, id="m3")


class Rodzaj(unittest.TestCase):
    def test_form_factor_i_enumerator(self):
        self.assertEqual(U.rodzaj_z(1, "INTELAUDIO"), (U.GLOSNIKI, U.WBUDOWANE))
        self.assertEqual(U.rodzaj_z(9, "HDAUDIO"), (U.MONITOR, U.WBUDOWANE))
        self.assertEqual(U.rodzaj_z(4, "INTELAUDIO"), (U.MIKROFON, U.WBUDOWANE))
        self.assertEqual(U.rodzaj_z(5, "USB"), (U.ZESTAW, U.USB))
        self.assertEqual(U.rodzaj_z(3, "BTHENUM"), (U.SLUCHAWKI, U.BLUETOOTH))
        self.assertEqual(U.rodzaj_z(5, "BTHHFENUM"), (U.ZESTAW, U.BLUETOOTH))
        self.assertEqual(U.rodzaj_z(None, "SWD"), (U.INNE, ""))
        self.assertEqual(U.rodzaj_z(10, ""), (U.INNE, ""))

    def test_opisy_po_polsku(self):
        teksty.ustaw("pl")
        self.assertEqual(U.opis_rodzaju(WBUDOWANY_MIK), "mikrofon wbudowany")
        self.assertEqual(U.opis_rodzaju(WBUDOWANY_MIK, krotko=True), "wbudowany")
        self.assertEqual(U.opis_rodzaju(GLOSNIKI), "głośniki komputera")
        self.assertEqual(U.opis_rodzaju(MONITOR), "monitor / telewizor (HDMI)")
        self.assertEqual(U.opis_rodzaju(POLY_MIK), "mikrofon zestawu słuchawkowego USB")
        self.assertEqual(U.opis_rodzaju(POLY_MIK, krotko=True), "zestaw słuchawkowy USB")
        self.assertEqual(U.opis_rodzaju(POLY_WY), "zestaw słuchawkowy USB")
        self.assertEqual(U.opis_rodzaju(BT_MIK, krotko=True), "zestaw słuchawkowy Bluetooth")
        nieznane = U.PunktAudio("Coś", False, U.INNE, "")
        self.assertEqual(U.opis_rodzaju(nieznane), "")
        mik_usb = U.PunktAudio("Yeti", True, U.MIKROFON, U.USB)
        self.assertEqual(U.opis_rodzaju(mik_usb), "mikrofon USB")
        self.assertEqual(U.opis_rodzaju(mik_usb, krotko=True), "USB")

    def test_opisy_po_angielsku(self):
        teksty.ustaw("en")
        try:
            self.assertEqual(U.opis_rodzaju(POLY_MIK), "headset microphone USB")
            self.assertEqual(U.opis_rodzaju(GLOSNIKI), "computer speakers")
        finally:
            teksty.ustaw("pl")

    def test_zewnetrzne(self):
        self.assertFalse(WBUDOWANY_MIK.zewnetrzne)
        self.assertFalse(GLOSNIKI.zewnetrzne)
        self.assertFalse(MONITOR.zewnetrzne)
        self.assertTrue(POLY_MIK.zewnetrzne)
        self.assertTrue(BT_MIK.zewnetrzne)


class Znajdz(unittest.TestCase):
    PUNKTY = [GLOSNIKI, MONITOR, WBUDOWANY_MIK, POLY_MIK, POLY_WY]

    def test_po_nazwie_i_domyslne(self):
        self.assertIs(U.znajdz(self.PUNKTY, POLY_MIK.nazwa, True), POLY_MIK)
        self.assertIs(U.znajdz(self.PUNKTY, "", True), WBUDOWANY_MIK)
        self.assertIs(U.znajdz(self.PUNKTY, "", False), GLOSNIKI)
        # Nazwa z ustawień, której już nie ma — None, nie domyślne (o tym
        # decyduje nagrywanie, a okno ma powiedzieć „odłączone”).
        self.assertIsNone(U.znajdz(self.PUNKTY, "Nieistniejące", True))
        # Mikrofon nie jest urządzeniem odtwarzającym.
        self.assertIsNone(U.znajdz(self.PUNKTY, POLY_MIK.nazwa, False))


class Propozycja(unittest.TestCase):
    def test_bez_zestawu_nic(self):
        self.assertEqual(U.propozycja_zestawu([GLOSNIKI, MONITOR, WBUDOWANY_MIK], "", ""),
                         (None, None))

    def test_zestaw_usb_gdy_ustawienia_domyslne(self):
        # Prawdziwy przypadek: Windows zostawił domyślne na wbudowanych,
        # a zestaw USB wisi obok.
        mik, wy = U.propozycja_zestawu([GLOSNIKI, MONITOR, WBUDOWANY_MIK, POLY_MIK, POLY_WY],
                                       "", "")
        self.assertIs(mik, POLY_MIK)
        self.assertIs(wy, POLY_WY)

    def test_zestaw_gdy_wbudowany_wskazany_z_nazwy(self):
        mik, wy = U.propozycja_zestawu([GLOSNIKI, WBUDOWANY_MIK, POLY_MIK, POLY_WY],
                                       WBUDOWANY_MIK.nazwa, "")
        self.assertIs(mik, POLY_MIK)
        self.assertIs(wy, POLY_WY)

    def test_zestaw_juz_wybrany_nie_pyta(self):
        self.assertEqual(
            U.propozycja_zestawu([GLOSNIKI, WBUDOWANY_MIK, POLY_MIK, POLY_WY],
                                 POLY_MIK.nazwa, POLY_WY.nazwa), (None, None))

    def test_zestaw_domyslny_w_windows_nie_pyta(self):
        # Windows sam przełączył domyślne na zestaw — nagranie i tak z niego pójdzie.
        glosniki = U.PunktAudio(GLOSNIKI.nazwa, False, U.GLOSNIKI, U.WBUDOWANE, id="g1")
        wbudowany = U.PunktAudio(WBUDOWANY_MIK.nazwa, True, U.MIKROFON, U.WBUDOWANE, id="m1")
        poly_mik = U.PunktAudio(POLY_MIK.nazwa, True, U.ZESTAW, U.USB, domyslne=True, id="m2")
        poly_wy = U.PunktAudio(POLY_WY.nazwa, False, U.ZESTAW, U.USB, domyslne=True, id="g3")
        self.assertEqual(U.propozycja_zestawu([glosniki, wbudowany, poly_mik, poly_wy],
                                              "", ""), (None, None))

    def test_wygrywa_komunikacyjne(self):
        # Dwa zestawy: Bluetooth jest urządzeniem do rozmów (Teams), USB nie.
        mik, _wy = U.propozycja_zestawu([WBUDOWANY_MIK, POLY_MIK, BT_MIK], "", "")
        self.assertIs(mik, BT_MIK)

    def test_monitor_nie_jest_zestawem(self):
        _mik, wy = U.propozycja_zestawu([GLOSNIKI, MONITOR, WBUDOWANY_MIK], "", "")
        self.assertIsNone(wy)


@unittest.skipUnless(os.name == "nt", "COM tylko na Windows")
class Com(unittest.TestCase):
    def test_punkty_nie_wybuchaja(self):
        punkty = U.punkty()
        self.assertIsInstance(punkty, list)
        for p in punkty:
            self.assertTrue(p.nazwa)
            self.assertIn(p.rodzaj, (U.GLOSNIKI, U.SLUCHAWKI, U.ZESTAW, U.MIKROFON, U.MONITOR,
                                     U.LINIOWE, U.CYFROWE, U.INNE))
        # Co najwyżej jedno domyślne na kierunek.
        for wejscie in (True, False):
            self.assertLessEqual(sum(1 for p in punkty if p.wejscie == wejscie and p.domyslne), 1)

    def test_z_watku_i_powtornie(self):
        import threading

        wyniki = []
        w = threading.Thread(target=lambda: wyniki.append(U.punkty()))
        w.start()
        w.join()
        self.assertEqual(len(wyniki), 1)
        self.assertEqual([p.nazwa for p in wyniki[0]], [p.nazwa for p in U.punkty()])


if __name__ == "__main__":
    unittest.main()
