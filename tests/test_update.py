"""Testy aktualizacji: wybór instalatora, adresy, podpis wydania.

GitHub jest udawany przez podmianę `update._get` — żadnych połączeń.
"""

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whisper_automat.core import podpis, update  # noqa: E402
from whisper_automat.core.wydanie import WYDANIA  # noqa: E402

KLUCZ = podpis.nowy_klucz()
WYD = replace(WYDANIA["papuga"], klucz_publiczny=podpis.klucz_publiczny(KLUCZ).hex())
BEZ_KLUCZA = replace(WYDANIA["papuga"], klucz_publiczny="")
SUMA = "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"
BAZA = f"https://github.com/{WYD.repo}/releases/download/v1.2.0/"


def wydanie_github(zalaczniki, tag="v1.2.0"):
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/{WYD.repo}/releases/tag/{tag}",
        "body": "Co nowego",
        "assets": zalaczniki,
    }


def zalacznik(nazwa, digest=None):
    dane = {"name": nazwa, "size": 1000, "browser_download_url": BAZA + nazwa}
    if digest:
        dane["digest"] = "sha256:" + digest
    return dane


class UdawanyGitHub:
    """Podmienia update._get: zwraca JSON wydania i treści załączników."""

    def __init__(self, wydanie_json, pliki=None):
        self.wydanie_json = wydanie_json
        self.pliki = pliki or {}
        self.zapytania = []

    def __call__(self, url, accept="application/vnd.github+json"):
        self.zapytania.append(url)
        if url.endswith("/releases/latest"):
            return json.dumps(self.wydanie_json).encode("utf-8")
        nazwa = url.rsplit("/", 1)[-1]
        if nazwa in self.pliki:
            return self.pliki[nazwa].encode("utf-8")
        raise AssertionError(f"nieoczekiwane zapytanie: {url}")


class Adresy(unittest.TestCase):
    def test_strona_wydan_wlasnego_repo(self):
        self.assertTrue(update.adres_z_wydan(BAZA + "Papuga-1.2.0-Setup.exe", WYD))
        self.assertTrue(update.adres_z_wydan(
            "https://objects.githubusercontent.com/github-production-release-asset/abc", WYD))

    def test_odrzuca_obce(self):
        for url in (
            "http://github.com/" + WYD.repo + "/releases/download/v1/x.exe",   # bez TLS
            "https://github.com/ktos-inny/papuga/releases/download/v1/x.exe",  # obce repo
            "https://github.com/" + WYD.repo + "/blob/main/x.exe",             # nie wydanie
            "https://example.com/x.exe",
            "https://github.com.example.com/" + WYD.repo + "/releases/download/v1/x.exe",
            "",
        ):
            with self.subTest(url=url):
                self.assertFalse(update.adres_z_wydan(url, WYD))


class Wersje(unittest.TestCase):
    def test_porownanie(self):
        self.assertTrue(update.nowsza("1.2.0", "1.1.9"))
        self.assertTrue(update.nowsza("v1.10.0", "1.9.0"))
        self.assertFalse(update.nowsza("1.1.0", "1.1.0"))
        self.assertFalse(update.nowsza("1.1.0-beta", "1.1.0"))
        self.assertTrue(update.nowsza("1.1", "1.0.9"))

    def test_wybor_instalatora(self):
        zal = [zalacznik("Papuga-1.2.0-Setup-offline.exe"), zalacznik("Papuga-1.2.0-Setup-cpu.exe"),
               zalacznik("Papuga-1.2.0-Setup.exe"), zalacznik("Papuga-1.2.0-Setup.exe.podpis")]
        self.assertEqual(update._wybierz_instalator(zal, WYD)["name"], "Papuga-1.2.0-Setup.exe")


class Sprawdzanie(unittest.TestCase):
    def setUp(self):
        self._get = update._get

    def tearDown(self):
        update._get = self._get

    def _sprawdz(self, zalaczniki, pliki=None, wydanie=WYD):
        update._get = UdawanyGitHub(wydanie_github(zalaczniki), pliki)
        return update.sprawdz("1.1.0", wydanie)

    def test_podpisane_wydanie_do_instalacji(self):
        nazwa = "Papuga-1.2.0-Setup.exe"
        tekst = podpis.zapisz_podpis(nazwa, SUMA, KLUCZ)
        akt = self._sprawdz([zalacznik(nazwa, SUMA), zalacznik(nazwa + ".podpis")],
                            {nazwa + ".podpis": tekst})
        self.assertEqual(akt.wersja, "1.2.0")
        self.assertTrue(akt.podpisana)
        self.assertEqual(akt.instalator.sha256, SUMA)
        self.assertTrue(update.mozna_zainstalowac(akt, WYD))
        self.assertEqual(akt.uwaga, "")

    def test_brak_podpisu_nie_instaluje(self):
        nazwa = "Papuga-1.2.0-Setup.exe"
        akt = self._sprawdz([zalacznik(nazwa, SUMA)])
        self.assertFalse(akt.podpisana)
        self.assertFalse(update.mozna_zainstalowac(akt, WYD))
        self.assertIn("podpis", akt.uwaga.lower())
        # Strona wydania zostaje — da się pobrać ręcznie.
        self.assertTrue(akt.strona.startswith("https://github.com/"))

    def test_podpis_obcym_kluczem(self):
        nazwa = "Papuga-1.2.0-Setup.exe"
        tekst = podpis.zapisz_podpis(nazwa, SUMA, podpis.nowy_klucz())
        akt = self._sprawdz([zalacznik(nazwa, SUMA), zalacznik(nazwa + ".podpis")],
                            {nazwa + ".podpis": tekst})
        self.assertFalse(update.mozna_zainstalowac(akt, WYD))

    def test_digest_githuba_rozny_od_podpisanego(self):
        nazwa = "Papuga-1.2.0-Setup.exe"
        tekst = podpis.zapisz_podpis(nazwa, SUMA, KLUCZ)
        akt = self._sprawdz([zalacznik(nazwa, "0" * 64), zalacznik(nazwa + ".podpis")],
                            {nazwa + ".podpis": tekst})
        self.assertFalse(update.mozna_zainstalowac(akt, WYD))
        self.assertIn("GitHub", akt.uwaga)

    def test_adres_spoza_wydan_odrzucony(self):
        nazwa = "Papuga-1.2.0-Setup.exe"
        tekst = podpis.zapisz_podpis(nazwa, SUMA, KLUCZ)
        zly = zalacznik(nazwa, SUMA)
        zly["browser_download_url"] = "https://example.com/" + nazwa
        akt = self._sprawdz([zly, zalacznik(nazwa + ".podpis")], {nazwa + ".podpis": tekst})
        self.assertEqual(akt.url, "")
        self.assertFalse(update.mozna_zainstalowac(akt, WYD))

    def test_bez_klucza_wystarcza_digest(self):
        nazwa = "Papuga-1.2.0-Setup.exe"
        akt = self._sprawdz([zalacznik(nazwa, SUMA)], wydanie=BEZ_KLUCZA)
        self.assertEqual(akt.instalator.sha256, SUMA)
        self.assertTrue(update.mozna_zainstalowac(akt, BEZ_KLUCZA))

    def test_bez_klucza_plik_sha256(self):
        nazwa = "Papuga-1.2.0-Setup.exe"
        akt = self._sprawdz([zalacznik(nazwa), zalacznik(nazwa + ".sha256")],
                            {nazwa + ".sha256": f"{SUMA}  {nazwa}\n"}, wydanie=BEZ_KLUCZA)
        self.assertEqual(akt.instalator.sha256, SUMA)

    def test_obca_strona_wydania_zastapiona(self):
        nazwa = "Papuga-1.2.0-Setup.exe"
        dane = wydanie_github([zalacznik(nazwa, SUMA)])
        dane["html_url"] = "https://example.com/zlo"
        update._get = UdawanyGitHub(dane)
        akt = update.sprawdz("1.1.0", BEZ_KLUCZA)
        self.assertEqual(akt.strona, BEZ_KLUCZA.strona_url)

    def test_brak_nowszej(self):
        update._get = UdawanyGitHub(wydanie_github([], tag="v1.1.0"))
        self.assertIsNone(update.sprawdz("1.1.0", WYD))


if __name__ == "__main__":
    unittest.main()
