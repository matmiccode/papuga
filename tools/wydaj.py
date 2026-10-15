"""Całe wydanie jednym poleceniem — żeby nie klikać krok po kroku.

    .venv\\Scripts\\python.exe tools\\wydaj.py 1.5.0 --wydania firma,papuga

Kroki (każdy wypisuje jedną linię; przy błędzie — ogon wyjścia i stop):
  1. drzewo git czyste (albo --brudne), testy (albo --bez-testow),
  2. numer wersji w src/whisper_automat/__init__.py i w stopce INSTRUKCJA.txt,
     commit „Wersja X.Y.Z” i tag vX.Y.Z (albo --bez-commitu),
  3. firma:  build_exe.py --z-modelem → publikuj_wydanie.py --wydanie firma
             (z kopią zapasową, gdy jest tools/kopia_wydan.local.txt),
  4. papuga: build_exe.py --wydanie papuga → sprawdz_paczke.py →
             publikuj_wydanie.py (szkic na GitHubie; --od-razu publikuje)
             → publikuj_kod.py (migawka kodu MIT).
Opisy wydań: publikacja/opis-firma-<wersja>.md i publikacja/opis-<wersja>.md
(gdy ich nie ma, firma dostaje pusty opis, Papuga notatki generowane).

Strona Papugi (docs/) i README profilu nie zmieniają się przy wydaniu —
numer wersji bierze się z API GitHuba. Zmiany w tekstach strony robi się
osobno (RAMA.md w matmiccode.github.io).

--tylko-krok build|publikuj|kod pozwala powtórzyć jeden krok po poprawce.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
INIT = ROOT / "src" / "whisper_automat" / "__init__.py"
INSTRUKCJA = ROOT / "INSTRUKCJA.txt"
PUBLIKACJA = ROOT / "publikacja"
WERSJA_RE = re.compile(r"^\d+\.\d+\.\d+$")


def krok(nazwa: str) -> None:
    print(f"\n== {nazwa}", flush=True)


def uruchom(cmd: list, co: str, ogon: int = 25) -> None:
    """Uruchamia polecenie cicho; wypisuje czas, przy błędzie ogon wyjścia."""
    start = time.perf_counter()
    wynik = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    czas = time.perf_counter() - start
    if wynik.returncode != 0:
        linie = (wynik.stdout + "\n" + wynik.stderr).strip().splitlines()
        print(f"   BŁĄD: {co} ({czas:.0f} s), kod {wynik.returncode}")
        print("   " + "\n   ".join(linie[-ogon:]))
        raise SystemExit(wynik.returncode)
    print(f"   {co}: OK ({czas:.0f} s)")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout.strip()


def wersja_biezaca() -> str:
    for linia in INIT.read_text(encoding="utf-8").splitlines():
        if linia.startswith("__version__"):
            return linia.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("Brak __version__ w __init__.py")


def ustaw_wersje(nowa: str) -> None:
    stara = wersja_biezaca()
    if stara == nowa:
        print(f"   wersja już {nowa}")
        return
    tekst = INIT.read_text(encoding="utf-8").replace(
        f'__version__ = "{stara}"', f'__version__ = "{nowa}"')
    INIT.write_text(tekst, encoding="utf-8", newline="\n")
    if INSTRUKCJA.is_file():
        t = INSTRUKCJA.read_text(encoding="utf-8")
        INSTRUKCJA.write_text(re.sub(r"Whisper Automat \d+\.\d+\.\d+", f"Whisper Automat {nowa}", t),
                              encoding="utf-8", newline="\n")
    print(f"   wersja {stara} → {nowa}")


def opis(nazwa: str) -> list:
    plik = PUBLIKACJA / nazwa
    return ["--opis", str(plik)] if plik.is_file() else []


def main() -> int:
    parser = argparse.ArgumentParser(description="Wydanie jednym poleceniem.")
    parser.add_argument("wersja", help="np. 1.5.0")
    parser.add_argument("--wydania", default="firma,papuga",
                        help="firma, papuga albo oba po przecinku (domyślnie oba).")
    parser.add_argument("--bez-testow", action="store_true")
    parser.add_argument("--bez-commitu", action="store_true", help="Nie commituj i nie taguj.")
    parser.add_argument("--brudne", action="store_true", help="Pozwól na niezacommitowane zmiany.")
    parser.add_argument("--od-razu", action="store_true",
                        help="Papuga: opublikuj wydanie na GitHubie od razu, nie jako szkic.")
    parser.add_argument("--tylko-krok", choices=("build", "publikuj", "kod"),
                        help="Powtórz jeden krok (po poprawce), bez zmiany wersji.")
    args = parser.parse_args()

    if not WERSJA_RE.match(args.wersja):
        raise SystemExit("Wersja ma być w postaci X.Y.Z")
    wydania = [w.strip() for w in args.wydania.split(",") if w.strip()]
    for w in wydania:
        if w not in ("firma", "papuga"):
            raise SystemExit(f"Nieznane wydanie: {w}")
    if not PY.is_file():
        raise SystemExit("Brak .venv — uruchom setup.bat")

    if args.tylko_krok is None:
        krok("Sprawdzenie")
        brudne = git("status", "--porcelain")
        if brudne and not args.brudne:
            raise SystemExit("Drzewo git nie jest czyste — zacommituj zmiany albo --brudne.\n" + brudne)
        if not args.bez_testow:
            uruchom([str(PY), "-m", "unittest", "discover", "-s", "tests"], "testy")

        krok("Wersja")
        ustaw_wersje(args.wersja)
        if not args.bez_commitu:
            if git("status", "--porcelain"):
                uruchom(["git", "add", "-A"], "git add")
                uruchom(["git", "commit", "-q", "-m", f"Wersja {args.wersja}"], "commit")
            if f"v{args.wersja}" not in git("tag", "--list", f"v{args.wersja}"):
                uruchom(["git", "tag", f"v{args.wersja}"], f"tag v{args.wersja}")

    if "firma" in wydania:
        krok("Whisper Automat (firma)")
        if args.tylko_krok in (None, "build"):
            uruchom([str(PY), "tools/build_exe.py", "--z-modelem"], "budowa offline", ogon=40)
        if args.tylko_krok in (None, "publikuj"):
            uruchom([str(PY), "tools/publikuj_wydanie.py", "--wydanie", "firma",
                     *opis(f"opis-firma-{args.wersja}.md")], "publikacja do folderu firmowego + kopia")

    if "papuga" in wydania:
        krok("Papuga")
        if args.tylko_krok in (None, "build"):
            uruchom([str(PY), "tools/build_exe.py", "--wydanie", "papuga"], "budowa", ogon=40)
            uruchom([str(PY), "tools/sprawdz_paczke.py"], "sprawdzenie paczki (słowa zakazane)")
        if args.tylko_krok in (None, "publikuj"):
            uruchom([str(PY), "tools/publikuj_wydanie.py", "--wydanie", "papuga",
                     *opis(f"opis-{args.wersja}.md"), *(["--od-razu"] if args.od_razu else [])],
                    "wydanie na GitHubie" + ("" if args.od_razu else " (szkic)"))
        if args.tylko_krok in (None, "kod"):
            uruchom([str(PY), "tools/publikuj_kod.py"], "migawka kodu (matmiccode/papuga)")

    krok("Gotowe")
    print(f"   {args.wersja}: " + ", ".join(wydania))
    if "papuga" in wydania and not args.od_razu and args.tylko_krok in (None, "publikuj"):
        print("   Papuga jest SZKICEM — opublikuj go na stronie wydania (Publish release).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
