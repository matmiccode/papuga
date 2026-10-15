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

Kopia zapasowa (firma): gdy jest tools/kopia_wydan.local.txt (ścieżka
folderu, np. na dysku Google Drive dla komputerów) albo --kopia, po
publikacji instalator, suma, podpis i najnowsza.json trafiają do
`<kopia>\\<wersja>\\`, a baza głosów (`Glosy\\`) do `<kopia>\\Glosy\\`.
Zostają trzy ostatnie wersje (--zachowaj). Awaria serwera: `--przywroc`
kopiuje najnowszą (albo --przywroc 1.5.0) z powrotem do folderu firmowego,
razem z bazą głosów — bez budowania i bez podpisywania (pliki są już
podpisane). `--tylko-kopia` robi samą kopię tego, co leży w folderze.
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
PLIK_KOPII = ROOT / "tools" / "kopia_wydan.local.txt"
GLOSY = "Glosy"
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


def folder_kopii(podany: Optional[Path]) -> Optional[Path]:
    """Folder kopii zapasowej wydań firmowych — None, gdy nie skonfigurowano."""
    if podany:
        return podany
    try:
        tekst = PLIK_KOPII.read_text(encoding="utf-8").strip()
    except OSError:
        tekst = ""
    return Path(tekst) if tekst else None


def _kopiuj_folder(zrodlo: Path, cel: Path, wzorzec: str = "*") -> int:
    """Kopiuje pliki (bez podfolderów) pasujące do wzorca. Zwraca liczbę."""
    if not zrodlo.is_dir():
        return 0
    cel.mkdir(parents=True, exist_ok=True)
    n = 0
    for plik in sorted(zrodlo.glob(wzorzec)):
        if plik.is_file():
            shutil.copy2(plik, cel / plik.name)
            n += 1
    return n


def kopia_zapasowa(folder: Path, kopia: Path, ver: str, zachowaj: int) -> None:
    """`<kopia>\\<wersja>\\` z plikami wydania i `<kopia>\\Glosy\\` z bazą głosów."""
    cel = kopia / ver
    print(f"  kopia  : {cel}")
    n = 0
    for plik in sorted(folder.iterdir()):
        if plik.is_file():
            cel.mkdir(parents=True, exist_ok=True)
            shutil.copy2(plik, cel / plik.name)
            n += 1
    print(f"  skopiowano {n} plik(ów) wydania")
    glosy = _kopiuj_folder(folder / GLOSY, kopia / GLOSY, "*.json")
    if glosy:
        print(f"  baza głosów: {glosy} wpis(ów) → {kopia / GLOSY}")
    # Starsze kopie wersji schodzą — zostaje `zachowaj` najnowszych.
    wersje = sorted((k for k in kopia.iterdir() if k.is_dir() and k.name != GLOSY),
                    key=lambda k: [int(x) if x.isdigit() else 0 for x in k.name.split(".")])
    for stara in wersje[:-zachowaj] if zachowaj > 0 else []:
        shutil.rmtree(stara, ignore_errors=True)
        print(f"  usunięto starą kopię: {stara.name}")


def przywroc(kopia: Path, folder: Path, ver: str, wyd: Wydanie) -> int:
    """Odtwarza folder firmowy z kopii: pliki wydania + baza głosów."""
    wersje = sorted((k.name for k in kopia.iterdir() if k.is_dir() and k.name != GLOSY),
                    key=lambda v: [int(x) if x.isdigit() else 0 for x in v.split(".")])
    if not wersje:
        raise SystemExit(f"W {kopia} nie ma żadnej kopii wydania.")
    wybrana = ver if ver and ver != "latest" else wersje[-1]
    if wybrana not in wersje:
        raise SystemExit(f"Brak kopii wersji {wybrana}; są: {', '.join(wersje)}")
    zrodlo = kopia / wybrana
    print(f"  przywracam {wybrana}: {zrodlo} → {folder}")
    folder.mkdir(parents=True, exist_ok=True)
    manifest = None
    for plik in sorted(zrodlo.iterdir()):
        if not plik.is_file():
            continue
        if plik.name == update.MANIFEST:
            manifest = plik
            continue
        tymczasowy = folder / (plik.name + ".part")
        shutil.copyfile(plik, tymczasowy)
        os.replace(tymczasowy, folder / plik.name)
        print(f"  {plik.name}")
    if manifest is not None:
        # Manifest na końcu — program u ludzi widzi wydanie dopiero w komplecie.
        tymczasowy = folder / (manifest.name + ".part")
        shutil.copyfile(manifest, tymczasowy)
        os.replace(tymczasowy, folder / manifest.name)
        print(f"  {manifest.name}")
    glosy = _kopiuj_folder(kopia / GLOSY, folder / GLOSY, "*.json")
    if glosy:
        print(f"  baza głosów: {glosy} wpis(ów)")
    akt = update.sprawdz("0.0.0", replace(wyd, aktualizacje_folder=str(folder)))
    if akt is None or akt.wersja != wybrana or not akt.podpisana:
        raise SystemExit(f"Program nie uznałby przywróconego wydania: {akt and akt.uwaga}")
    print(f"\n  Gotowe. Folder firmowy ma znów wersję {wybrana} z podpisem.")
    return 0


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
    parser.add_argument("--kopia", type=Path,
                        help="Firma: folder kopii zapasowej (domyślnie z tools/kopia_wydan.local.txt).")
    parser.add_argument("--zachowaj", type=int, default=3,
                        help="Ile ostatnich wersji trzymać w kopii zapasowej (domyślnie 3).")
    parser.add_argument("--tylko-kopia", action="store_true",
                        help="Firma: tylko skopiuj to, co leży w folderze firmowym, do kopii zapasowej.")
    parser.add_argument("--przywroc", nargs="?", const="latest", metavar="WERSJA",
                        help="Firma: odtwórz folder firmowy z kopii zapasowej (najnowszej albo podanej).")
    args = parser.parse_args()

    wyd = WYDANIA[args.wydanie]
    if args.przywroc or args.tylko_kopia:
        if wyd.kod != "firma":
            raise SystemExit("Kopia zapasowa i przywracanie dotyczą wydania firmowego (--wydanie firma).")
        kopia = folder_kopii(args.kopia)
        if kopia is None:
            raise SystemExit(f"Nie wiem, gdzie jest kopia: brak {PLIK_KOPII.name} i --kopia.")
        folder = folder_firmy(args.folder)
        if args.przywroc:
            return przywroc(kopia, folder, args.przywroc, wyd)
        biezaca = update.sprawdz("0.0.0", replace(wyd, aktualizacje_folder=str(folder)))
        kopia_zapasowa(folder, kopia, biezaca.wersja if biezaca else "nieznana", args.zachowaj)
        return 0

    ver = wersja()
    instalator = znajdz_instalator(wyd, ver)
    suma, plik_sumy, plik_podpisu = podpisz(instalator, wyd, args.klucz)

    if wyd.kod == "firma":
        folder = folder_firmy(args.folder)
        kod = publikuj_folder(wyd, ver, instalator, suma, plik_sumy, plik_podpisu,
                              args.opis, folder)
        kopia = folder_kopii(args.kopia)
        if kod == 0 and kopia is not None:
            print("\nKopia zapasowa")
            kopia_zapasowa(folder, kopia, ver, args.zachowaj)
        elif kod == 0:
            print(f"\n  (bez kopii zapasowej — wpisz folder do {PLIK_KOPII.name}, "
                  f"np. na dysku Google Drive)")
        return kod
    return publikuj_github(wyd, ver, instalator, plik_sumy, plik_podpisu,
                           args.opis, args.od_razu)


if __name__ == "__main__":
    sys.exit(main())
