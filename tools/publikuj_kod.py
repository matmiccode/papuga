"""Publikuje kod źródłowy do publicznego repozytorium (matmiccode/papuga).

Kod rozwijany jest w prywatnym repozytorium whisper-automat, którego
historia zawiera dane firmowe i osobowe (dawne podpisy, instrukcja firmowa,
notatki z wdrożenia). Publiczne repozytorium dostaje więc migawkę: bieżące
pliki bez tych elementów, jeden commit na publikację. Kod jest otwarty
(MIT), a prywatna historia zostaje prywatna.

Do publicznego repo trafia wszystko, co śledzi git, poza:
  * PROGRESS.md i INSTRUKCJA.txt (dokumenty firmowe),
  * README.md — zastępuje go publikacja/README.md,
  * katalogiem publikacja/ (szablony dla tego skryptu).
Do tego generowany LICENCJE.txt (tools/licencje.py).

Przed wysłaniem skrypt przeszukuje migawkę pod kątem zakazanych słów —
z tools/podpis_firmy.local.txt (wszystko poza marką MATCODE) i z pliku
--zakazane — i przerywa, gdy coś znajdzie.

Uruchomienie (gh zalogowany na konto właściciela repozytorium):
    .venv\\Scripts\\python.exe tools\\publikuj_kod.py             # commit + push do ..\\papuga
    .venv\\Scripts\\python.exe tools\\publikuj_kod.py --utworz    # najpierw załóż repo na GitHubie
    .venv\\Scripts\\python.exe tools\\publikuj_kod.py --bez-push  # tylko lokalny commit w ..\\papuga
    .venv\\Scripts\\python.exe tools\\publikuj_kod.py --pages     # włącz GitHub Pages z katalogu docs/
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from whisper_automat.core.wydanie import WYDANIA  # noqa: E402

WYD = WYDANIA["papuga"]
KLON = ROOT.parent / "papuga"
WYKLUCZONE = ["PROGRESS.md", "INSTRUKCJA.txt", "README.md", "publikacja"]
PLIK_PODPISU = ROOT / "tools" / "podpis_firmy.local.txt"
BINARNE = {".png", ".ico", ".onnx", ".bin", ".exe", ".dll", ".pyd", ".zip", ".jpg", ".woff2"}


def uruchom(cmd: List[str], cwd: Path, cicho: bool = False) -> str:
    if not cicho:
        print("  $ " + " ".join(cmd))
    wynik = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    if wynik.returncode != 0:
        raise SystemExit(f"Polecenie nie powiodło się ({wynik.returncode}):\n"
                         f"{wynik.stdout}{wynik.stderr}")
    return wynik.stdout.strip()


def wersja() -> str:
    for linia in (ROOT / "src" / "whisper_automat" / "__init__.py").read_text(
        encoding="utf-8"
    ).splitlines():
        if linia.startswith("__version__"):
            return linia.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("Brak __version__ w __init__.py")


def migawka(tmp: Path) -> Path:
    """Pliki śledzone przez git w HEAD, bez elementów firmowych, plus licencje."""
    archiwum = tmp / "kod.tar"
    uruchom(["git", "archive", "--format=tar", "-o", str(archiwum), "HEAD"], cwd=ROOT)
    kod = tmp / "kod"
    kod.mkdir()
    with tarfile.open(archiwum) as paczka:
        paczka.extractall(kod, filter="data")

    for nazwa in WYKLUCZONE:
        cel = kod / nazwa
        if cel.is_dir():
            shutil.rmtree(cel)
        elif cel.exists():
            cel.unlink()
    shutil.copy2(ROOT / "publikacja" / "README.md", kod / "README.md")

    import licencje

    licencje.zapisz(kod / "LICENCJE.txt", kod_wydania="papuga")
    return kod


def zakazane_slowa(dodatkowe: Path | None) -> List[str]:
    slowa: List[str] = []
    if PLIK_PODPISU.is_file():
        slowa += [s for s in PLIK_PODPISU.read_text(encoding="utf-8").split()
                  if s.upper() != "MATCODE" and len(s) >= 3]
    if dodatkowe and dodatkowe.is_file():
        slowa += [s.strip() for s in dodatkowe.read_text(encoding="utf-8").splitlines()
                  if s.strip()]
    return slowa


def przeszukaj(kod: Path, slowa: List[str]) -> List[str]:
    """Pliki migawki zawierające którekolwiek z zakazanych słów."""
    if not slowa:
        return []
    niskie = [s.lower() for s in slowa]
    trafienia = []
    for plik in kod.rglob("*"):
        if not plik.is_file() or plik.suffix.lower() in BINARNE:
            continue
        try:
            tekst = plik.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if any(s in tekst for s in niskie):
            trafienia.append(str(plik.relative_to(kod)))
    return trafienia


def przygotuj_klon(utworz: bool) -> Path:
    if (KLON / ".git").is_dir():
        return KLON
    if not shutil.which("gh"):
        raise SystemExit("Brak programu gh (GitHub CLI): winget install GitHub.cli")
    istnieje = subprocess.run(["gh", "repo", "view", WYD.repo], capture_output=True).returncode == 0
    if not istnieje:
        if not utworz:
            raise SystemExit(
                f"Repozytorium {WYD.repo} nie istnieje (albo gh nie ma do niego dostępu).\n"
                f"  Sprawdź konto: gh auth status\n"
                f"  Załóż je tym skryptem: publikuj_kod.py --utworz"
            )
        uruchom(["gh", "repo", "create", WYD.repo, "--public",
                 "--description", WYD.opis,
                 "--homepage", f"https://{WYD.repo.split('/')[0]}.github.io/{WYD.repo.split('/')[1]}/"],
                cwd=ROOT)
    uruchom(["git", "clone", f"https://github.com/{WYD.repo}.git", str(KLON)], cwd=ROOT)
    uruchom(["git", "checkout", "-B", "main"], cwd=KLON)
    # Tożsamość z repozytorium deweloperskiego (adres noreply), nie globalna.
    for klucz in ("user.name", "user.email"):
        wartosc = uruchom(["git", "config", klucz], cwd=ROOT, cicho=True)
        uruchom(["git", "config", klucz, wartosc], cwd=KLON, cicho=True)
    return KLON


def synchronizuj(kod: Path, klon: Path) -> None:
    for element in klon.iterdir():
        if element.name == ".git":
            continue
        shutil.rmtree(element) if element.is_dir() else element.unlink()
    for element in kod.iterdir():
        cel = klon / element.name
        shutil.copytree(element, cel) if element.is_dir() else shutil.copy2(element, cel)


def wlacz_pages() -> None:
    """GitHub Pages z katalogu docs/ gałęzi main — najpierw próba założenia, potem zmiany."""
    for metoda in ("POST", "PUT"):
        wynik = subprocess.run(
            ["gh", "api", "-X", metoda, f"repos/{WYD.repo}/pages",
             "-f", "build_type=legacy", "-f", "source[branch]=main", "-f", "source[path]=/docs"],
            capture_output=True, text=True,
        )
        if wynik.returncode == 0:
            print(f"  Pages: włączone ({metoda}) — https://{WYD.repo.split('/')[0]}.github.io/"
                  f"{WYD.repo.split('/')[1]}/")
            return
    print("  Pages: nie udało się włączyć przez API — włącz w Settings → Pages "
          "(Deploy from a branch, main, /docs).")


def main() -> int:
    parser = argparse.ArgumentParser(description="Publikuje kod do publicznego repozytorium.")
    parser.add_argument("--utworz", action="store_true", help="Załóż repozytorium, gdy go nie ma.")
    parser.add_argument("--bez-push", action="store_true", help="Zostaw commit lokalnie.")
    parser.add_argument("--pages", action="store_true", help="Włącz GitHub Pages z docs/.")
    parser.add_argument("--zakazane", type=Path, default=None,
                        help="Plik z dodatkowymi słowami, których nie wolno opublikować.")
    parser.add_argument("--tylko-sprawdz", action="store_true",
                        help="Zbuduj migawkę i przeszukaj ją, bez dotykania repozytorium publicznego.")
    args = parser.parse_args()

    brudne = uruchom(["git", "status", "--porcelain"], cwd=ROOT, cicho=True)
    if brudne:
        print("  UWAGA: repozytorium ma niezacommitowane zmiany — publikuję stan HEAD, nie dysku.")
    sha = uruchom(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, cicho=True)
    ver = wersja()

    with tempfile.TemporaryDirectory(prefix="papuga-publikacja-") as tmp:
        kod = migawka(Path(tmp))
        trafienia = przeszukaj(kod, zakazane_slowa(args.zakazane))
        if trafienia:
            raise SystemExit("PRZERWANO — zakazane słowa w plikach:\n  " + "\n  ".join(trafienia))
        if args.tylko_sprawdz:
            pliki = sorted(str(p.relative_to(kod)) for p in kod.rglob("*") if p.is_file())
            print(f"  migawka: {len(pliki)} plików, zakazanych słów nie znaleziono")
            print("  " + "\n  ".join(pliki))
            return 0
        klon = przygotuj_klon(args.utworz)
        synchronizuj(kod, klon)

    uruchom(["git", "add", "-A"], cwd=klon)
    if not uruchom(["git", "status", "--porcelain"], cwd=klon, cicho=True):
        print("  Publiczne repozytorium jest już aktualne.")
    else:
        uruchom(["git", "commit", "-q", "-m", f"Papuga {ver}: kod zrodlowy (stan {sha})"], cwd=klon)
        print(f"  commit w {klon}")
        if not args.bez_push:
            uruchom(["git", "push", "-u", "origin", "main"], cwd=klon)
            print(f"  wysłano: https://github.com/{WYD.repo}")

    if args.pages:
        wlacz_pages()
    return 0


if __name__ == "__main__":
    sys.exit(main())
