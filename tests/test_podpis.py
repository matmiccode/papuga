"""Testy podpisu wydań: zgodność z RFC 8032 i format pliku podpisu.

Uruchomienie:  .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whisper_automat.core import podpis  # noqa: E402

#: Wektory z RFC 8032, §7.1 (TEST 1, 2, 3): klucz prywatny, publiczny,
#: wiadomość, podpis — wszystko szesnastkowo.
WEKTORY = [
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
    (
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    ),
]


class Ed25519(unittest.TestCase):
    def test_wektory_rfc8032(self):
        for prywatny, publiczny, wiadomosc, oczekiwany in WEKTORY:
            with self.subTest(publiczny=publiczny[:8]):
                klucz = bytes.fromhex(prywatny)
                self.assertEqual(podpis.klucz_publiczny(klucz).hex(), publiczny)
                sig = podpis.podpisz(klucz, bytes.fromhex(wiadomosc))
                self.assertEqual(sig.hex(), oczekiwany)
                self.assertTrue(podpis.sprawdz(bytes.fromhex(publiczny),
                                               bytes.fromhex(wiadomosc), sig))

    def test_zmieniona_wiadomosc_nie_przechodzi(self):
        klucz = podpis.nowy_klucz()
        publiczny = podpis.klucz_publiczny(klucz)
        sig = podpis.podpisz(klucz, b"Papuga-1.1.1-Setup.exe")
        self.assertTrue(podpis.sprawdz(publiczny, b"Papuga-1.1.1-Setup.exe", sig))
        self.assertFalse(podpis.sprawdz(publiczny, b"Papuga-1.1.2-Setup.exe", sig))
        self.assertFalse(podpis.sprawdz(publiczny, b"Papuga-1.1.1-Setup.exe",
                                        sig[:-1] + bytes([sig[-1] ^ 1])))
        self.assertFalse(podpis.sprawdz(podpis.klucz_publiczny(podpis.nowy_klucz()),
                                        b"Papuga-1.1.1-Setup.exe", sig))

    def test_odrzuca_zle_dlugosci(self):
        klucz = podpis.nowy_klucz()
        self.assertFalse(podpis.sprawdz(b"\x00" * 31, b"x", b"\x00" * 64))
        self.assertFalse(podpis.sprawdz(podpis.klucz_publiczny(klucz), b"x", b"\x00" * 63))
        with self.assertRaises(ValueError):
            podpis.podpisz(b"za krotki", b"x")


class PlikPodpisu(unittest.TestCase):
    SUMA = "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"

    def setUp(self):
        self.klucz = podpis.nowy_klucz()
        self.publiczny_hex = podpis.klucz_publiczny(self.klucz).hex()
        self.tekst = podpis.zapisz_podpis("Papuga-1.1.1-Setup.exe", self.SUMA, self.klucz)

    def test_zapis_i_odczyt(self):
        self.assertTrue(self.tekst.startswith("papuga-podpis 1\nplik: Papuga-1.1.1-Setup.exe\n"))
        suma = podpis.zweryfikuj_wydanie(self.tekst, self.publiczny_hex, "Papuga-1.1.1-Setup.exe")
        self.assertEqual(suma, self.SUMA)

    def test_inny_plik(self):
        with self.assertRaises(podpis.BladPodpisu):
            podpis.zweryfikuj_wydanie(self.tekst, self.publiczny_hex, "Papuga-1.1.2-Setup.exe")

    def test_podmieniona_suma(self):
        podmieniony = self.tekst.replace(self.SUMA, "0" * 64)
        with self.assertRaises(podpis.BladPodpisu):
            podpis.zweryfikuj_wydanie(podmieniony, self.publiczny_hex, "Papuga-1.1.1-Setup.exe")

    def test_obcy_klucz(self):
        obcy = podpis.klucz_publiczny(podpis.nowy_klucz()).hex()
        with self.assertRaises(podpis.BladPodpisu):
            podpis.zweryfikuj_wydanie(self.tekst, obcy, "Papuga-1.1.1-Setup.exe")

    def test_zepsuty_plik(self):
        for tekst in ("", "cos innego\nplik: a\n", "papuga-podpis 1\nplik: a\nsha256: zz\npodpis: AAAA\n",
                      "papuga-podpis 1\nplik: a\nsha256: " + "0" * 64 + "\npodpis: nie-base64!\n",
                      "papuga-podpis 1\nplik: a\nsha256: " + "0" * 64 + "\npodpis: "
                      + base64.b64encode(b"\x00" * 10).decode() + "\n"):
            with self.subTest(tekst=tekst[:30]):
                with self.assertRaises(podpis.BladPodpisu):
                    podpis.zweryfikuj_wydanie(tekst, self.publiczny_hex, "a")

    def test_zly_klucz_programu(self):
        with self.assertRaises(podpis.BladPodpisu):
            podpis.zweryfikuj_wydanie(self.tekst, "nie-hex", "Papuga-1.1.1-Setup.exe")
        with self.assertRaises(podpis.BladPodpisu):
            podpis.zweryfikuj_wydanie(self.tekst, "abcd", "Papuga-1.1.1-Setup.exe")


if __name__ == "__main__":
    unittest.main()
