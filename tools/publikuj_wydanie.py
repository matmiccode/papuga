"""Wystawia zbudowany instalator jako wydanie: Papugę na GitHubie, Whisper
Automat w folderze firmowym (udział sieciowy).

Papuga:
    .venv\\Scripts\\python.exe tools\\build_exe.py --wydanie papuga
    .venv\\Scripts\\python.exe tools\\publikuj_wydanie.py --opis opis.md

Firma:
    .venv\\Scripts\\python.exe tools\\build_exe.py --z-modelem
    .venv\\Scripts\\python.exe tools\\publikuj_wydanie.py --wydanie firma --opis opis.md

Wspólne dla obu:
  1. znajduje instalator w dist/ (Papuga: lekki `Papuga-<wersja>-Setup.exe`,
     firma: `WhisperAutomat-<wersja>-Setup-offline.exe` albo inny wariant),
  2. liczy SHA-256 i zapisuje go obok jako `<instalator>.sha256`,
  3. podpisuje nazwę i sumę kluczem autora (tools/klucz_wydan.py) do pliku
     `<instalator>.podpis` — bez niego program u ludzi nie zainstaluje
     wydania sam (core/update.py) — i sprawdza ten podpis tak, jak zrobi to
     program.

Papuga: zakłada wydanie `v<wersja>` w publicznym repozytorium z instalatorem,
sumą i podpisem — domyślnie jako SZKIC. Na stronie wydania można jeszcze
poprawić opis i dopiero wtedy kliknąć „Publish release”. Program widzi
wyłącznie wydania opublikowane, więc szkic nikomu niczego nie zaproponuje.
Potrzebny jest `gh` zalogowany na konto właściciela repozytorium, `matmiccode`.

Firma: kopiuje pliki do folderu z tools/aktualizacje_firmy.local.txt (albo
--folder, albo WHISPER_AUTOMAT_AKTUALIZACJE): najpierw instalator pod
tymczasową nazwą (po skopiowaniu suma jest liczona jeszcze raz, z udziału),
potem suma i podpis, na końcu `najnowsza.json` — program u ludzi widzi nową
wersję dopiero, gdy wszystko leży na miejscu. Starsze instalatory z folderu
są usuwane. Publikacja jest natychmiastowa: kto ma program otwarty, dostanie
pasek przy najbliższym sprawdzeniu (raz na dobę albo „Sprawdź aktualizacje”).
Na koniec skrypt sprawdza folder tak, jak zrobi to program (`update.sprawdz`).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whisper_automat.core import podpis, update  # noqa: E402
from whisper_automat.core.wydanie import WYDANIA, Wydanie  # noqa: E402

KLUCZ_DOMYSLNY = Path(os.environ.get("PAPUGA_KLUCZ_WYDAN")
                      or Path.home() / ".matcode" / "papuga-klucz-wydan.txt")

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PLIK_FOLDERU = ROOT / "tools" / "aktualizacje_firmy.local.txt"
LIMIT_GITHUBA = 2 * 1024 ** 3
MB = 1024 ** 2


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
        for porcja in iter(lambda: f.read(8 * MB), b""):
            h.update(porcja)
    return h.hexdigest()


def znajdz_instalator(wyd: Wydanie, ver: str) -> Path:
    """Papuga: tylko lekki (ten pobiera program). Firma: offline, a gdy go
    nie ma — lekki albo procesorowy; z folderu idzie to, co tam leży."""
    if wyd.kod == "papuga":
        kandydaci = [f"{wyd.plik}-{ver}-Setup.exe"]
        rada = f"  .venv\\Scripts\\python.exe tools\\build_exe.py --wydanie papuga"
    else:
        kandydaci = [f"{wyd.plik}-{ver}-Setup-offline.exe",
                     f"{wyd.plik}-{ver}-Setup.exe",
                     f"{wyd.plik}-{ver}-Setup-cpu.exe"]
        rada = f"  .venv\\Scripts\\python.exe tools\\build_exe.py --z-modelem"
    for nazwa in kandydaci:
        if (DIST / nazwa).is_file():
            return DIST / nazwa
    raise SystemExit(f"Brak {kandydaci[0]} w dist\\. Zbuduj go najpierw:\n{rada}")


def folder_firmy(podany: Optional[Path]) -> Path:
    if podany:
        return podany
    ze_srodowiska = os.environ.get("WHISPER_AUTOMAT_AKTUALIZACJE", "").strip()
    if ze_srodowiska:
        return Path(ze_srodowiska)
    try:
        tekst = PLIK_FOLDERU.read_text(encoding="utf-8").strip()
    except OSError:
        tekst = ""
    if not tekst:
        raise SystemExit(
            f"Nie wiem, dokąd publikować: brak {PLIK_FOLDERU.name} i --folder.\n"
            f"  Wpisz do pliku ścieżkę udziału, np. \\\\serwer\\udzial\\Whisper Automat"
        )
    if tekst.lower().startswith(("http://", "https://")):
        raise SystemExit("Publikacja na serwer http wymaga własnego sposobu wgrywania — "
                         "ten skrypt kopiuje tylko do folderu (UNC albo dysk).")
    return Path(tekst)


def podpisz(instalator: Path, wyd: Wydanie, plik_klucza: Path) -> tuple[str, Path, Path]:
    """Suma i podpis obok instalatora; podpis sprawdzony tak, jak zrobi to program."""
    if not plik_klucza.is_file():
        raise SystemExit(
            f"Brak klucza do podpisu wydań: {plik_klucza}\n"
            f"  Utwórz go: .venv\\Scripts\\python.exe tools\\klucz_wydan.py nowy\n"
            f"  i wklej wypisany klucz publiczny do core/wydanie.py (KLUCZ_WYDAN)."
        )
    klucz = podpis.wczytaj_klucz_prywatny(plik_klucza)
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
    if podpis.zweryfikuj_wydanie(plik_podpisu.read_text(encoding="utf-8"),
                                 wyd.klucz_publiczny, instalator.name) != suma:
        raise SystemExit("Podpis nie przeszedł własnej weryfikacji.")
    print(f"  {instalator.name}: {instalator.stat().st_size / MB:.0f} MB")
    print(f"  SHA-256: {suma}")
    print(f"  podpis : {plik_podpisu.name}")
    return suma, plik_sumy, plik_podpisu


def publikuj_github(wyd: Wydanie, ver: str, instalator: Path, plik_sumy: Path,
                    plik_podpisu: Path, opis: Optional[Path], od_razu: bool) -> int:
    rozmiar = instalator.stat().st_size
    if rozmiar >= LIMIT_GITHUBA:
        raise SystemExit(
            f"{instalator.name} ma {rozmiar / 1024 ** 3:.2f} GB — GitHub przyjmuje "
            f"pliki do 2 GB. Czy to nie wariant z modelem w środku?"
        )
    if not shutil.which("gh"):
        raise SystemExit("Brak programu gh (GitHub CLI): winget install GitHub.cli")
    cmd = [
        "gh", "release", "create", f"v{ver}",
        str(instalator), str(plik_sumy), str(plik_podpisu),
        "--repo", wyd.repo,
        "--title", f"{wyd.nazwa} {ver}",
    ]
    cmd += ["--notes-file", str(opis)] if opis else ["--generate-notes"]
    if not od_razu:
        cmd.append("--draft")
    print("  $ " + " ".join(cmd))
    wynik = subprocess.run(cmd)
    if wynik.returncode != 0:
        return wynik.returncode
    print(
        "\n  Gotowe." + ("" if od_razu else
                         " To SZKIC — opublikuj go na stronie wydania, gdy opis będzie dobry.")
    )
    return 0


def publikuj_folder(wyd: Wydanie, ver: str, instalator: Path, suma: str, plik_sumy: Path,
                    plik_podpisu: Path, opis: Optional[Path], folder: Path) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    rozmiar = instalator.stat().st_size
    print(f"  folder : {folder}")
    print(f"  kopiuję {instalator.name} ({rozmiar / MB:.0f} MB)…", flush=True)
    tymczasowy = folder / (instalator.name + ".part")
    shutil.copyfile(instalator, tymczasowy)
    # Suma liczona z udziału: to, co przeczytają ludzie, ma być tym, co podpisano.
    if sha256(tymczasowy) != suma:
        tymczasowy.unlink(missing_ok=True)
        raise SystemExit("Kopia na udziale ma inną sumę niż oryginał — przerwano.")
    os.replace(tymczasowy, folder / instalator.name)
    shutil.copyfile(plik_sumy, folder / plik_sumy.name)
    shutil.copyfile(plik_podpisu, folder / plik_podpisu.name)

    wpis = {
        "program": wyd.nazwa,
        "wersja": ver,
        "instalator": instalator.name,
        "rozmiar": rozmiar,
        "data": dt.date.today().isoformat(),
        "opis": opis.read_text(encoding="utf-8").strip() if opis else "",
    }
    tmp = folder / (update.MANIFEST + ".tmp")
    tmp.write_text(json.dumps(wpis, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, folder / update.MANIFEST)
    print(f"  {update.MANIFEST}: wersja {ver}")

    # Starsze instalatory (z sumami i podpisami) schodzą — folder ma być czytelny.
    for stary in sorted(folder.glob(f"{wyd.plik}-*-Setup*.exe*")):
        if not stary.name.startswith(instalator.name):
            stary.unlink(missing_ok=True)
            print(f"  usunięto starsze: {stary.name}")

    # To samo sprawdzenie, które zrobi program u ludzi.
    akt = update.sprawdz("0.0.0", replace(wyd, aktualizacje_folder=str(folder)))
    if akt is None or akt.wersja != ver or not akt.podpisana:
        raise SystemExit(f"Program nie uznałby tego wydania: {akt and akt.uwaga}")
    print("\n  Gotowe. Program u pracowników pokaże pasek przy najbliższym sprawdzeniu "
          "(raz na dobę albo „Sprawdź aktualizacje”).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publikuje wydanie: Papugę na GitHubie, Whisper Automat w folderze firmowym.")
    parser.add_argument("--wydanie", choices=("papuga", "firma"), default="papuga",
                        help="papuga = wydanie na GitHubie (domyślnie), "
                             "firma = kopia do folderu firmowego.")
    parser.add_argument("--opis", type=Path, help="Plik Markdown z opisem wydania.")
    parser.add_argument("--od-razu", action="store_true",
                        help="Papuga: opublikuj od razu, bez szkicu — program u ludzi "
                             "zobaczy nową wersję przy najbliższym sprawdzeniu.")
    parser.add_argument("--folder", type=Path,
                        help="Firma: folder docelowy (domyślnie z tools/aktualizacje_firmy.local.txt).")
    parser.add_argument("--klucz", type=Path, default=KLUCZ_DOMYSLNY,
                        help="Plik z kluczem prywatnym do podpisu wydań.")
    args = parser.parse_args()

    wyd = WYDANIA[args.wydanie]
    ver = wersja()
    instalator = znajdz_instalator(wyd, ver)
    suma, plik_sumy, plik_podpisu = podpisz(instalator, wyd, args.klucz)

    if wyd.kod == "firma":
        return publikuj_folder(wyd, ver, instalator, suma, plik_sumy, plik_podpisu,
                               args.opis, folder_firmy(args.folder))
    return publikuj_github(wyd, ver, instalator, plik_sumy, plik_podpisu,
                           args.opis, args.od_razu)


if __name__ == "__main__":
    sys.exit(main())
