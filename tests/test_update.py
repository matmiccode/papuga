"""Testy aktualizacji: wybór instalatora, adresy, podpis wydania, folder firmowy.

GitHub jest udawany przez podmianę `update._get`, udział sieciowy — katalogiem
tymczasowym. Żadnych połączeń.
"""

import hashlib
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whisper_automat.core import download, podpis, update  # noqa: E402
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


FIRMA = replace(WYDANIA["firma"], klucz_publiczny=podpis.klucz_publiczny(KLUCZ).hex())


class Folder(unittest.TestCase):
    """Aktualizacje z folderu firmowego — udział sieciowy udawany katalogiem."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "wydania"
        self.folder.mkdir()
        self.wyd = replace(FIRMA, aktualizacje_folder=str(self.folder))
        self.nazwa = "WhisperAutomat-1.4.0-Setup-offline.exe"
        self.tresc = b"udawany instalator " * 1000
        self.suma = hashlib.sha256(self.tresc).hexdigest()

    def tearDown(self):
        self.tmp.cleanup()

    def _opublikuj(self, podpisz=True, klucz=KLUCZ, wersja="1.4.0", instalator=None):
        nazwa = instalator or self.nazwa
        (self.folder / self.nazwa).write_bytes(self.tresc)
        if podpisz:
            (self.folder / (nazwa + ".podpis")).write_text(
                podpis.zapisz_podpis(nazwa, self.suma, klucz), encoding="utf-8")
        (self.folder / update.MANIFEST).write_text(json.dumps({
            "wersja": wersja, "instalator": nazwa, "rozmiar": len(self.tresc),
            "opis": "Co nowego",
        }), encoding="utf-8")

    def test_zrodlo_wybiera_wydanie(self):
        self.assertFalse(WYDANIA["firma"].aktualizacje)   # folder dopiero z paczki
        self.assertTrue(self.wyd.aktualizacje)
        self.assertTrue(WYDANIA["papuga"].aktualizacje)   # GitHub
        self.assertEqual(WYDANIA["papuga"].aktualizacje_folder, "")

    def test_podpisane_do_instalacji_i_kopia(self):
        self._opublikuj()
        akt = update.sprawdz("1.3.0", self.wyd)
        self.assertEqual(akt.wersja, "1.4.0")
        self.assertTrue(akt.podpisana)
        self.assertEqual(akt.instalator.sha256, self.suma)
        self.assertEqual(akt.opis, "Co nowego")
        self.assertEqual(akt.strona, str(self.folder))
        self.assertEqual(akt.uwaga, "")
        self.assertTrue(update.mozna_zainstalowac(akt, self.wyd))

        cel = Path(self.tmp.name) / "pobrane"
        postepy = []
        sciezka = download.kopiuj_plik(
            Path(akt.url), cel, akt.instalator,
            on_progress=lambda b, w, v: postepy.append((b, w)))
        self.assertEqual(sciezka, cel / self.nazwa)
        self.assertEqual(sciezka.read_bytes(), self.tresc)
        self.assertEqual(postepy[-1], (len(self.tresc), len(self.tresc)))
        self.assertFalse((cel / (self.nazwa + ".part")).exists())
        # Druga kopia nie czyta źródła drugi raz — gotowy plik ze zgodną sumą zostaje.
        (self.folder / self.nazwa).unlink()
        self.assertEqual(download.kopiuj_plik(Path(akt.url), cel, akt.instalator), sciezka)

    def test_bez_podpisu_nie_instaluje(self):
        self._opublikuj(podpisz=False)
        akt = update.sprawdz("1.3.0", self.wyd)
        self.assertEqual(akt.wersja, "1.4.0")
        self.assertFalse(update.mozna_zainstalowac(akt, self.wyd))
        self.assertIn("podpis", akt.uwaga.lower())

    def test_obcy_klucz(self):
        self._opublikuj(klucz=podpis.nowy_klucz())
        akt = update.sprawdz("1.3.0", self.wyd)
        self.assertFalse(update.mozna_zainstalowac(akt, self.wyd))

    def test_bez_klucza_w_programie_nie_instaluje(self):
        # Z folderu nie ma furtki „sama suma wystarczy” — udział bywa zapisywalny.
        self._opublikuj()
        bez = replace(self.wyd, klucz_publiczny="")
        self.assertFalse(update.mozna_zainstalowac(update.sprawdz("1.3.0", bez), bez))

    def test_pusty_folder_i_brak_nowszej(self):
        self.assertIsNone(update.sprawdz("1.3.0", self.wyd))
        self._opublikuj(wersja="1.3.0")
        self.assertIsNone(update.sprawdz("1.3.0", self.wyd))

    def test_folder_niedostepny(self):
        wyd = replace(self.wyd, aktualizacje_folder=str(self.folder / "nie-ma"))
        with self.assertRaises(update.UpdateError):
            update.sprawdz("1.3.0", wyd)

    def test_instalator_ze_sciezka_odrzucony(self):
        self._opublikuj(instalator="..\\" + self.nazwa)
        akt = update.sprawdz("1.3.0", self.wyd)
        self.assertEqual(akt.url, "")
        self.assertFalse(update.mozna_zainstalowac(akt, self.wyd))

    def test_kopia_z_niezgodna_suma(self):
        plik = download.Plik(nazwa=self.nazwa, rozmiar=len(self.tresc), sha256="0" * 64)
        (self.folder / self.nazwa).write_bytes(self.tresc)
        cel = Path(self.tmp.name) / "pobrane"
        with self.assertRaises(download.DownloadError):
            download.kopiuj_plik(self.folder / self.nazwa, cel, plik)
        self.assertFalse((cel / self.nazwa).exists())
        self.assertFalse((cel / (self.nazwa + ".part")).exists())


if __name__ == "__main__":
    unittest.main()
