"""Klucz do podpisywania wydań Papugi.

Klucz prywatny leży POZA repozytorium, w profilu autora:
    %USERPROFILE%\\.matcode\\papuga-klucz-wydan.txt   (64 znaki szesnastkowe)
Inną ścieżkę wskazuje zmienna PAPUGA_KLUCZ_WYDAN albo opcja --plik.

    .venv\\Scripts\\python.exe tools\\klucz_wydan.py nowy
        Tworzy klucz, jeśli go jeszcze nie ma, i wypisuje klucz publiczny —
        ten trafia do `klucz_publiczny` wydania w core/wydanie.py.
        Istniejącego klucza nigdy nie nadpisuje.
    .venv\\Scripts\\python.exe tools\\klucz_wydan.py publiczny
        Wypisuje klucz publiczny istniejącego klucza.

Skrypt nie wypisuje klucza prywatnego. ZRÓB KOPIĘ pliku z kluczem w bezpiecznym
miejscu (menedżer haseł, zaszyfrowany dysk): bez niego nie da się podpisać
kolejnego wydania, a program u ludzi nie zainstaluje sam wydania podpisanego
innym kluczem — każdy musiałby pobrać je ręcznie.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whisper_automat.core import podpis  # noqa: E402

DOMYSLNY = Path(os.environ.get("PAPUGA_KLUCZ_WYDAN")
                or Path.home() / ".matcode" / "papuga-klucz-wydan.txt")


def main() -> int:
    parser = argparse.ArgumentParser(description="Klucz do podpisywania wydań.")
    parser.add_argument("polecenie", choices=("nowy", "publiczny"))
    parser.add_argument("--plik", type=Path, default=DOMYSLNY)
    args = parser.parse_args()

    if args.polecenie == "nowy":
        if args.plik.is_file():
            print(f"Klucz już istnieje: {args.plik} — nie nadpisuję.")
        else:
            args.plik.parent.mkdir(parents=True, exist_ok=True)
            args.plik.write_text(podpis.nowy_klucz().hex() + "\n", encoding="ascii")
            print(f"Utworzono klucz prywatny: {args.plik}")
            print("ZRÓB JEGO KOPIĘ w bezpiecznym miejscu — bez niego nie podpiszesz "
                  "kolejnego wydania.")
    elif not args.plik.is_file():
        print(f"Brak klucza: {args.plik}. Utwórz go poleceniem: klucz_wydan.py nowy")
        return 1

    klucz = podpis.wczytaj_klucz_prywatny(args.plik)
    print("\nKlucz publiczny (do core/wydanie.py, pole klucz_publiczny):")
    print(podpis.klucz_publiczny(klucz).hex())
    return 0


if __name__ == "__main__":
    sys.exit(main())
