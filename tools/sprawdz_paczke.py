"""Przeszukuje zbudowaną paczkę pod kątem danych, których nie ma prawa w niej być.

Przed każdym publicznym wydaniem: imię i nazwisko autora, nazwa firmy,
firmowe adresy, ścieżki z profilu użytkownika (`C:\\Users\\<login>`). Szuka
w każdym pliku paczki (tekst UTF-8 i UTF-16) oraz **wewnątrz pliku .exe**:
PyInstaller trzyma skompilowane moduły w archiwum PYZ zaszytym w pliku
wykonywalnym, więc zwykły grep po katalogu ich nie widzi.

Skąd słowa:
  * tools/podpis_firmy.local.txt — podpis firmowy bez marki MATCODE,
  * tools/zakazane.local.txt — po jednym słowie w linii (plik ignorowany
    przez gita: nazwa firmy, domena poczty itp.),
  * `Users\\<login>` i `Users/<login>` bieżącego użytkownika,
  * --slowa a,b,c z wiersza poleceń.

Uruchomienie:
    .venv\\Scripts\\python.exe tools\\sprawdz_paczke.py                 # dist\\Papuga
    .venv\\Scripts\\python.exe tools\\sprawdz_paczke.py dist\\WhisperAutomat
Kod wyjścia 1, gdy coś znaleziono.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
PLIK_PODPISU = ROOT / "tools" / "podpis_firmy.local.txt"
PLIK_ZAKAZANYCH = ROOT / "tools" / "zakazane.local.txt"


def slowa_zakazane(dodatkowe: Iterable[str]) -> List[str]:
    slowa: List[str] = []
    if PLIK_PODPISU.is_file():
        slowa += [s for s in PLIK_PODPISU.read_text(encoding="utf-8").split()
                  if s.upper() != "MATCODE" and len(s) >= 3]
    if PLIK_ZAKAZANYCH.is_file():
        slowa += [s.strip() for s in PLIK_ZAKAZANYCH.read_text(encoding="utf-8").splitlines()
                  if s.strip() and not s.startswith("#")]
    login = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if login:
        slowa += [f"Users\\{login}\\", f"Users/{login}/"]
    slowa += [s for s in dodatkowe if s]
    # Bez powtórzeń, z zachowaniem kolejności.
    return list(dict.fromkeys(slowa))


def _wzorce(slowa: List[str]) -> List[Tuple[str, bytes, bytes]]:
    return [(s, s.lower().encode("utf-8"), s.lower().encode("utf-16-le")) for s in slowa]


def _trafienia(dane: bytes, wzorce) -> List[str]:
    niskie = dane.lower()
    return [slowo for slowo, u8, u16 in wzorce if u8 in niskie or u16 in niskie]


def przeszukaj_pliki(paczka: Path, wzorce) -> List[str]:
    wynik = []
    for plik in sorted(paczka.rglob("*")):
        if not plik.is_file():
            continue
        try:
            dane = plik.read_bytes()
        except OSError:
            continue
        for slowo in _trafienia(dane, wzorce):
            wynik.append(f"{plik.relative_to(paczka)}: „{slowo}”")
    return wynik


def przeszukaj_exe(exe: Path, wzorce) -> Tuple[List[str], int, int]:
    """Wnętrze archiwum PyInstallera: wpisy CArchive i moduły z PYZ.

    Zwraca (trafienia, liczba wpisów, liczba modułów).
    """
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError:
        return [f"{exe.name}: brak PyInstallera w środowisku — wnętrza .exe nie sprawdzono"], 0, 0

    wynik = []
    archiwum = CArchiveReader(str(exe))
    wpisy = 0
    moduly = 0
    for nazwa, (_off, _dl, _ul, _flag, typ) in archiwum.toc.items():
        wpisy += 1
        if typ == "z":  # zaszyte archiwum PYZ ze skompilowanymi modułami
            pyz = archiwum.open_embedded_archive(nazwa)
            for modul in pyz.toc:
                moduly += 1
                try:
                    dane = pyz.extract(modul, raw=True)
                except Exception:
                    continue
                if not dane:
                    continue
                for slowo in _trafienia(dane, wzorce):
                    wynik.append(f"{exe.name} › {nazwa} › moduł {modul}: „{slowo}”")
            continue
        try:
            dane = archiwum.extract(nazwa)
        except Exception:
            continue
        if not dane:
            continue
        for slowo in _trafienia(dane, wzorce):
            wynik.append(f"{exe.name} › {nazwa}: „{slowo}”")
    return wynik, wpisy, moduly


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="Szuka danych osobowych i firmowych w paczce.")
    parser.add_argument("paczka", nargs="?", type=Path, default=ROOT / "dist" / "Papuga")
    parser.add_argument("--slowa", default="", help="Dodatkowe słowa po przecinku.")
    args = parser.parse_args()

    paczka = args.paczka
    if not paczka.is_dir():
        raise SystemExit(f"Brak katalogu paczki: {paczka}")
    slowa = slowa_zakazane(s.strip() for s in args.slowa.split(","))
    if not slowa:
        raise SystemExit("Brak słów do szukania — uzupełnij tools/zakazane.local.txt.")
    wzorce = _wzorce(slowa)
    print(f"  paczka : {paczka}")
    print(f"  szukam : {len(slowa)} słów (w tym ścieżki profilu użytkownika)")

    trafienia = przeszukaj_pliki(paczka, wzorce)
    pliki = sum(1 for p in paczka.rglob("*") if p.is_file())
    for exe in sorted(paczka.glob("*.exe")):
        z_exe, wpisy, moduly = przeszukaj_exe(exe, wzorce)
        print(f"  {exe.name}: {wpisy} wpisów archiwum, {moduly} modułów w PYZ")
        trafienia += z_exe
    print(f"  plików w paczce: {pliki}")

    if trafienia:
        print("\n  ZNALEZIONO:")
        for t in trafienia:
            print("   - " + t)
        return 1
    print("\n  Czysto: żadnego z szukanych słów nie ma w paczce ani w kodzie w pliku .exe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
