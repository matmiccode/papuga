"""Rozdział wydań: marka tylko w module swojego wydania.

Warunek odbioru (zasada MATCODE, WYDANIA.md): w paczce wydania firmowego nie
ma żadnego śladu marki MATCODE ani nazwy wydania publicznego — nie tylko
ukrytego przycisku, ale samego tekstu. Paczka bierze wspólny kod i wspólne
grafiki, a pomija moduł i katalog grafik drugiego wydania
(tools/build_exe.py). Ten test pilnuje, żeby marka nie wróciła do wspólnego
kodu; gotową paczkę sprawdza potem tools/wydaj.py (sprawdz-firmowe.js).
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whisper_automat import teksty  # noqa: E402
from whisper_automat.core import wydanie as W  # noqa: E402

PAKIET = ROOT / "src" / "whisper_automat"
#: Słowa marki publicznej, których nie może mieć paczka firmowa (jak w
#: sprawdz-firmowe.js plus nazwa wydania publicznego).
MARKA = ("matcode", "matmiccode", "buycoffee", "postaw kaw", "buy me a coffee",
         "coffee for the author", "papuga")
#: Grafiki marki rozpoznaje się po nazwie pliku.
MARKA_W_NAZWACH = ("kawa", "coffee", "papuga", "matcode")


def _modul_wydania(plik: Path) -> bool:
    return plik.parent == PAKIET / "wydania" and plik.stem in W.MODULY


#: Kod publiczny (migawka, tools/publikuj_kod.py) nie ma modułu wydania firmowego.
FIRMA = W.WYDANIA.get("firma")
BEZ_FIRMY = unittest.skipIf(FIRMA is None, "kod publiczny: bez modułu wydania firmowego")


def _slowa_firmy() -> tuple:
    """Nazwa, plik i identyfikatory wydania firmowego — z jego modułu."""
    if FIRMA is None:
        return ()
    return tuple(s.lower() for s in (FIRMA.nazwa, FIRMA.plik, FIRMA.app_id,
                                     FIRMA.inno_id.strip("{}")))


class WspolnyKod(unittest.TestCase):
    def test_bez_marki_w_kodzie_wspolnym(self):
        trafienia = []
        for plik in sorted(PAKIET.rglob("*.py")):
            if _modul_wydania(plik):
                continue
            tekst = plik.read_text(encoding="utf-8").lower()
            for slowo in MARKA:
                if slowo in tekst:
                    trafienia.append(f"{plik.relative_to(ROOT)}: {slowo}")
        self.assertEqual(trafienia, [], "marka we wspólnym kodzie:\n" + "\n".join(trafienia))

    def test_grafiki_marki_tylko_w_swoim_katalogu(self):
        assets = ROOT / "assets"
        katalogi = {w.zasoby for w in W.WYDANIA.values() if w.zasoby}
        trafienia = [
            str(p.relative_to(ROOT)) for p in assets.rglob("*")
            if p.relative_to(assets).parts[0] not in katalogi
            and any(s in p.name.lower() for s in MARKA_W_NAZWACH)
        ]
        self.assertEqual(trafienia, [])

    def test_instalator_bez_marki_i_nazw(self):
        tekst = (ROOT / "tools" / "installer.iss").read_text(encoding="utf-8").lower()
        self.assertEqual([s for s in (*MARKA, *_slowa_firmy()) if s in tekst], [])

    @BEZ_FIRMY
    def test_kod_publiczny_bez_nazwy_firmowej(self):
        """Pliki migawki publicznej bez nazwy i identyfikatorów wydania firmowego.

        Ten sam podział co tools/publikuj_kod.py (pliki gita bez WYKLUCZONE);
        pełną kontrolę migawki robi tam `sprawdz-firmowe.js --publiczne`.
        """
        import subprocess

        sys.path.insert(0, str(ROOT / "tools"))
        import publikuj_kod

        pliki = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                               encoding="utf-8").stdout.splitlines()
        trafienia = []
        for nazwa in pliki:
            if any(nazwa == w or nazwa.startswith(w + "/") for w in publikuj_kod.WYKLUCZONE):
                continue
            plik = ROOT / nazwa
            if not plik.is_file() or plik.suffix.lower() in publikuj_kod.BINARNE:
                continue
            tekst = plik.read_bytes().decode("utf-8", "ignore").lower()
            trafienia += [f"{nazwa}: {s}" for s in _slowa_firmy() if s in tekst]
        self.assertEqual(trafienia, [], "nazwa firmowa w kodzie publicznym:\n" + "\n".join(trafienia))


class DaneWydan(unittest.TestCase):
    @BEZ_FIRMY
    def test_oba_wydania_z_modulow(self):
        self.assertEqual(sorted(W.WYDANIA), ["firma", "papuga"])
        self.assertEqual(W.MODUL, {"firma": "firmowe", "papuga": "publiczne"})
        self.assertEqual(W.DOMYSLNE, "firma")

    @BEZ_FIRMY
    def test_firmowe_bez_wsparcia_i_marki(self):
        firma = W.WYDANIA["firma"]
        self.assertEqual((firma.wsparcie_url, firma.wsparcie_tekst, firma.wsparcie_ikona), ("", "", ""))
        self.assertEqual((firma.zasoby, firma.kolory_postepu, firma.repo), ("", (), ""))
        # Wydawcę dokłada paczka z pliku lokalnego — w kodzie nie ma żadnego.
        self.assertEqual(firma.wydawca, "")

    def test_publiczne_ma_wsparcie_z_tlumaczeniem(self):
        pub = W.WYDANIA["papuga"]
        self.assertTrue(pub.wsparcie_url.startswith("https://"))
        self.assertIn(pub.wsparcie_tekst, teksty.EN)
        self.assertTrue((ROOT / "assets" / pub.zasoby / pub.wsparcie_ikona).is_file())
        self.assertEqual(len(pub.kolory_postepu), 4)

    @BEZ_FIRMY
    def test_paczka_bez_modulu_drugiego_wydania(self):
        # Tak jak w paczce: moduł wydania publicznego wykluczony z budowy.
        from unittest import mock

        import whisper_automat.wydania as pakiet

        zapamietany = pakiet.__dict__.pop("publiczne", None)
        try:
            with mock.patch.dict(sys.modules, {"whisper_automat.wydania.publiczne": None}):
                self.assertIsNone(W._importuj("publiczne"))
                self.assertIsNotNone(W._importuj("firmowe"))
        finally:
            if zapamietany is not None:
                pakiet.publiczne = zapamietany

    def test_budowa_firmowa_bez_podpisu_staje(self):
        # Brak tools/podpis_firmy.local.txt nie może dać paczki podpisanej
        # marką publiczną ani paczki bez wydawcy.
        import os
        import tempfile
        from unittest import mock

        sys.path.insert(0, str(ROOT / "tools"))
        import build_exe

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"WHISPER_AUTOMAT_PODPIS_FIRMY": ""}), \
                mock.patch.object(build_exe, "PLIK_PODPISU", Path(tmp) / "brak.local.txt"):
            with self.assertRaises(SystemExit):
                build_exe.podpis_firmy()
            (Path(tmp) / "brak.local.txt").write_text("Anna Nowak – FIRMA\n", encoding="utf-8")
            self.assertEqual(build_exe.podpis_firmy(), "Anna Nowak – FIRMA")

    def test_paczka_z_json_zachowuje_krotki(self):
        import json
        from dataclasses import asdict

        dane = json.loads(json.dumps(asdict(W.WYDANIA["papuga"])))
        self.assertEqual(W._z_danych(dane), W.WYDANIA["papuga"])


if __name__ == "__main__":
    unittest.main()
