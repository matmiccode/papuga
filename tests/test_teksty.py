"""Testy dwóch języków: słownik EN i wybór języka.

Każdy klucz w `teksty.EN` to dokładny polski tekst z kodu; tłumaczenie musi
mieć te same znaczniki `{…}` (inaczej `.format()` wybuchnie dopiero
u użytkownika po angielsku) i nie może być puste. Dodatkowo każdy
jednoliniowy literał `t("…")` w kodzie musi mieć wpis w słowniku — tekst bez
wpisu zostałby po polsku w angielskim oknie.
"""

import ast
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whisper_automat import teksty  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src" / "whisper_automat"


class Slownik(unittest.TestCase):
    def test_znaczniki_i_puste(self):
        for klucz, tlumaczenie in teksty.EN.items():
            with self.subTest(klucz=klucz):
                self.assertTrue(tlumaczenie.strip(), "puste tłumaczenie")
                self.assertEqual(teksty.znaczniki(klucz), teksty.znaczniki(tlumaczenie))

    def test_kazdy_literal_w_kodzie_ma_wpis(self):
        """Literały `t("…")` (także sklejane z kilku linii) z całego pakietu."""
        brak = []
        for plik in SRC.rglob("*.py"):
            drzewo = ast.parse(plik.read_text(encoding="utf-8"), str(plik))
            for wezel in ast.walk(drzewo):
                if (isinstance(wezel, ast.Call) and isinstance(wezel.func, ast.Name)
                        and wezel.func.id == "t" and wezel.args
                        and isinstance(wezel.args[0], ast.Constant)
                        and isinstance(wezel.args[0].value, str)):
                    tekst = wezel.args[0].value
                    if tekst not in teksty.EN:
                        brak.append(f"{plik.name}:{wezel.lineno}: {tekst[:60]}")
        self.assertEqual(brak, [], "teksty bez tłumaczenia:\n" + "\n".join(brak))

    def test_format_dziala_po_angielsku(self):
        teksty.ustaw("en")
        try:
            for klucz in teksty.EN:
                nazwy = {re.sub(r"[:!].*", "", z.strip("{}")) for z in teksty.znaczniki(klucz)}
                wartosci = {n: 1 for n in nazwy}
                teksty.t(klucz).format(**wartosci)
        finally:
            teksty.ustaw("pl")


class WyborJezyka(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.pop(teksty.ZMIENNA, None)
        self._jezyk = teksty.jezyk()

    def tearDown(self):
        if self._env is not None:
            os.environ[teksty.ZMIENNA] = self._env
        teksty.ustaw(self._jezyk)

    def test_pl_en(self):
        teksty.ustaw("en")
        self.assertEqual(teksty.jezyk(), "en")
        self.assertEqual(teksty.t("Transkrybuj"), "Transcribe")
        self.assertEqual(teksty.t("tekst spoza słownika"), "tekst spoza słownika")
        self.assertEqual(teksty.drugi_jezyk(), "pl")
        teksty.ustaw("pl")
        self.assertEqual(teksty.t("Transkrybuj"), "Transkrybuj")
        self.assertEqual(teksty.drugi_jezyk(), "en")

    def test_nieznany_to_jezyk_systemu(self):
        teksty.ustaw("de")
        self.assertEqual(teksty.jezyk(), teksty.jezyk_systemu())
        teksty.ustaw(None)
        self.assertEqual(teksty.jezyk(), teksty.jezyk_systemu())

    def test_ustawienie_programu_i_zmienna(self):
        teksty.ustaw_z_ustawien("en")
        self.assertEqual(teksty.jezyk(), "en")
        teksty.ustaw_z_ustawien("")
        self.assertEqual(teksty.jezyk(), teksty.jezyk_systemu())
        # Zmienna środowiskowa (testy, zrzuty) wygrywa z ustawieniem programu.
        os.environ[teksty.ZMIENNA] = "pl"
        teksty.ustaw(os.environ[teksty.ZMIENNA])
        teksty.ustaw_z_ustawien("en")
        self.assertEqual(teksty.jezyk(), "pl")


if __name__ == "__main__":
    unittest.main()
