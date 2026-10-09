"""Wystawia zbudowany instalator Papugi jako wydanie na GitHubie.

Kolejność:
    .venv\\Scripts\\python.exe tools\\build_exe.py --wydanie papuga
    .venv\\Scripts\\python.exe tools\\publikuj_wydanie.py --opis opis.md

Skrypt:
  1. sprawdza, że instalator jest i mieści się w limicie GitHuba (2 GiB),
  2. liczy SHA-256 i zapisuje go obok jako `<instalator>.sha256`,
  3. podpisuje nazwę i sumę kluczem autora (tools/klucz_wydan.py) do pliku
     `<instalator>.podpis` — bez niego program u ludzi nie zainstaluje
     wydania sam (core/update.py),
  4. zakłada wydanie `v<wersja>` w publicznym repozytorium z instalatorem,
     sumą i podpisem — domyślnie jako SZKIC. Na stronie wydania można jeszcze
     poprawić opis i dopiero wtedy kliknąć „Publish release”. Program
     widzi wyłącznie wydania opublikowane, więc szkic nikomu niczego nie
     zaproponuje.

Potrzebny jest `gh` zalogowany na konto właściciela repozytorium, `matmiccode`
(`gh auth status`; w razie potrzeby `gh auth login`).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whisper_automat.core import podpis  # noqa: E402
from whisper_automat.core.wydanie import WYDANIA  # noqa: E402

KLUCZ_DOMYSLNY = Path(os.environ.get("PAPUGA_KLUCZ_WYDAN")
                      or Path.home() / ".matcode" / "papuga-klucz-wydan.txt")

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
LIMIT_GITHUBA = 2 * 1024 ** 3


def wersja() -> str:
    for linia in (ROOT / "src" / "whisper_automat" / "__init__.py").read_text(
        encoding="utf-8"
    ).splitlines():
        if linia.startswith("__version__"):
            return linia.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("Nie znaleziono __version__ w __init__.py")


def sha256(plik: Path) -> str:
    h = hashlib.sha256()
    with open(plik, "rb") as f:
        for porcja in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(porcja)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Publikuje wydanie Papugi na GitHubie.")
    parser.add_argument("--opis", type=Path, help="Plik Markdown z opisem wydania.")
    parser.add_argument("--od-razu", action="store_true",
                        help="Opublikuj od razu, bez szkicu — program u ludzi "
                             "zobaczy nową wersję przy najbliższym sprawdzeniu.")
    parser.add_argument("--klucz", type=Path, default=KLUCZ_DOMYSLNY,
                        help="Plik z kluczem prywatnym do podpisu wydań.")
    args = parser.parse_args()

    wyd = WYDANIA["papuga"]
    ver = wersja()
    instalator = DIST / f"{wyd.plik}-{ver}-Setup.exe"
    if not instalator.is_file():
        raise SystemExit(
            f"Brak {instalator.name}. Zbuduj go najpierw:\n"
            f"  .venv\\Scripts\\python.exe tools\\build_exe.py --wydanie papuga"
        )
    rozmiar = instalator.stat().st_size
    if rozmiar >= LIMIT_GITHUBA:
        raise SystemExit(
            f"{instalator.name} ma {rozmiar / 1024 ** 3:.2f} GB — GitHub przyjmuje "
            f"pliki do 2 GB. Czy to nie wariant z modelem w środku?"
        )
    if not shutil.which("gh"):
        raise SystemExit("Brak programu gh (GitHub CLI): winget install GitHub.cli")

    if not args.klucz.is_file():
        raise SystemExit(
            f"Brak klucza do podpisu wydań: {args.klucz}\n"
            f"  Utwórz go: .venv\\Scripts\\python.exe tools\\klucz_wydan.py nowy\n"
            f"  i wklej wypisany klucz publiczny do core/wydanie.py (klucz_publiczny)."
        )
    klucz = podpis.wczytaj_klucz_prywatny(args.klucz)
    if podpis.klucz_publiczny(klucz).hex() != wyd.klucz_publiczny:
        raise SystemExit(
            "Klucz prywatny nie pasuje do klucza publicznego w core/wydanie.py — "
            "program u ludzi odrzuciłby taki podpis."
        )

    suma = sha256(instalator)
    plik_sumy = instalator.with_name(instalator.name + ".sha256")
    plik_sumy.write_text(f"{suma}  {instalator.name}\n", encoding="ascii")
    plik_podpisu = instalator.with_name(instalator.name + ".podpis")
    plik_podpisu.write_text(podpis.zapisz_podpis(instalator.name, suma, klucz), encoding="utf-8")
    # Sprawdzamy to samo, co sprawdzi program u ludzi.
    if podpis.zweryfikuj_wydanie(plik_podpisu.read_text(encoding="utf-8"),
                                 wyd.klucz_publiczny, instalator.name) != suma:
        raise SystemExit("Podpis nie przeszedł własnej weryfikacji.")
    print(f"  {instalator.name}: {rozmiar / 1024 ** 2:.0f} MB")
    print(f"  SHA-256: {suma}")
    print(f"  podpis : {plik_podpisu.name}")

    cmd = [
        "gh", "release", "create", f"v{ver}",
        str(instalator), str(plik_sumy), str(plik_podpisu),
        "--repo", wyd.repo,
        "--title", f"{wyd.nazwa} {ver}",
    ]
    cmd += ["--notes-file", str(args.opis)] if args.opis else ["--generate-notes"]
    if not args.od_razu:
        cmd.append("--draft")

    print("  $ " + " ".join(cmd))
    wynik = subprocess.run(cmd)
    if wynik.returncode != 0:
        return wynik.returncode
    print(
        "\n  Gotowe." + ("" if args.od_razu else
                         " To SZKIC — opublikuj go na stronie wydania, gdy opis będzie dobry.")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
