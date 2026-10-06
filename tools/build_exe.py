"""Buduje samodzielną wersję programu i instalator .exe.

Dwa warianty instalatora:
  * lekki (domyślny) — bez modelu, ~0,9 GB. Model (~1,6 GB) program pobiera
    przy pierwszym uruchomieniu. Wersja do rozdawania przez internet.
  * offline (--z-modelem) — model w środku, ~2,4 GB. Nic nie pobiera, więc
    działa także za firewallem, który blokuje huggingface.co.

Etapy:
  1. pobranie lekkiej wersji ffmpeg (raz, potem z pamięci podręcznej)
  2. PyInstaller — katalog `dist/WhisperAutomat` z całą zawartością
  3. spis licencji składników (tools/licencje.py) i LICENSE obok pliku .exe
  4. Inno Setup — jeden plik `dist/WhisperAutomat-<wersja>-Setup[-offline].exe`

Uruchomienie:
    .venv\\Scripts\\python.exe tools\\build_exe.py            wariant lekki
    .venv\\Scripts\\python.exe tools\\build_exe.py --z-modelem  wariant offline
    .venv\\Scripts\\python.exe tools\\build_exe.py --no-installer
    .venv\\Scripts\\python.exe tools\\build_exe.py --cpu       bez bibliotek CUDA

Potrzebne narzędzia: pip install -r requirements-dev.txt oraz Inno Setup
(winget install JRSoftware.InnoSetup).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Optional
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
ASSETS = ROOT / "assets"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
CACHE = ROOT / "build" / "cache"
VENV_SITE = ROOT / ".venv" / "Lib" / "site-packages"

from whisper_automat.core.wydanie import WYDANIA, Wydanie  # noqa: E402
from whisper_automat.core import wydanie as wydania  # noqa: E402

#: Wydanie, które budujemy — ustawiane w main() z --wydanie.
WYD: Wydanie = WYDANIA["firma"]

#: Podpis wydania firmowego z imieniem i nazwiskiem leży POZA repozytorium:
#: w tools/podpis_firmy.local.txt (plik ignorowany przez gita) albo w zmiennej
#: WHISPER_AUTOMAT_PODPIS_FIRMY. Kod repozytorium jest publikowany, a imię
#: i nazwisko ma być tylko w paczce firmowej — idzie tam przez wydanie.json.
PLIK_PODPISU = Path(__file__).resolve().parent / "podpis_firmy.local.txt"


def podpis_firmy() -> str:
    ze_srodowiska = os.environ.get("WHISPER_AUTOMAT_PODPIS_FIRMY", "").strip()
    if ze_srodowiska:
        return ze_srodowiska
    try:
        tekst = PLIK_PODPISU.read_text(encoding="utf-8").strip()
    except OSError:
        tekst = ""
    if not tekst:
        print(f"  UWAGA: brak {PLIK_PODPISU.name} — wydanie firmowe podpisze się samym MATCODE.")
    return tekst or "MATCODE"

#: Wersja „essentials" waży ułamek pełnej (~90 MB zamiast ~370 MB za oba pliki),
#: a zawiera wszystkie kodeki, których używamy do wyciągania ścieżki audio.
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

#: Pakiety, których PyInstaller sam nie wykryje w całości — mają dane,
#: biblioteki natywne albo importy rozwiązywane dopiero w czasie działania.
COLLECT_ALL = [
    "faster_whisper",   # modele VAD (.onnx) w podkatalogu assets
    "ctranslate2",      # biblioteki natywne silnika
    "onnxruntime",      # DLL-e w capi/
    "av",               # biblioteki FFmpeg używane przez dekoder
    "tokenizers",
    "tkinterdnd2",      # katalog tkdnd z rozszerzeniem Tcl
    "huggingface_hub",
    "truststore",     # weryfikacja certyfikatów przez magazyn Windows
    "certifi",
    "sherpa_onnx",    # rozpoznawanie mówców (biblioteki natywne + modele)
]

#: Biblioteki CUDA. Kopiujemy je z zachowaniem układu nvidia/<pakiet>/bin,
#: bo engine.prepare_cuda_libraries() właśnie takiej struktury szuka.
CUDA_PACKAGES = ["cublas", "cudnn", "cuda_nvrtc"]


def log(tekst: str) -> None:
    print(f"\n=== {tekst}", flush=True)


def run(cmd, **kwargs) -> None:
    print("  $ " + " ".join(str(c) for c in cmd), flush=True)
    proc = subprocess.run([str(c) for c in cmd], **kwargs)
    if proc.returncode != 0:
        raise SystemExit(f"Komenda zakończyła się kodem {proc.returncode}")


# ---------------------------------------------------------------------------


def pobierz_ffmpeg() -> Path:
    """Zwraca katalog z ffmpeg.exe i ffprobe.exe, pobierając je w razie potrzeby."""
    log("ffmpeg")
    cel = CACHE / "ffmpeg"
    if (cel / "ffmpeg.exe").is_file() and (cel / "ffprobe.exe").is_file():
        print(f"  z pamięci podręcznej: {cel}")
        return cel

    CACHE.mkdir(parents=True, exist_ok=True)
    archiwum = CACHE / "ffmpeg.zip"
    if not archiwum.is_file():
        print(f"  pobieram {FFMPEG_URL}")
        with urlopen(FFMPEG_URL) as odpowiedz, open(archiwum, "wb") as plik:
            shutil.copyfileobj(odpowiedz, plik)
        print(f"  pobrano {archiwum.stat().st_size / 1024 / 1024:.0f} MB")

    cel.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archiwum) as zip_plik:
        for wpis in zip_plik.namelist():
            nazwa = Path(wpis).name
            if nazwa in {"ffmpeg.exe", "ffprobe.exe"}:
                with zip_plik.open(wpis) as zrodlo, open(cel / nazwa, "wb") as plik:
                    shutil.copyfileobj(zrodlo, plik)
                print(f"  wypakowano {nazwa} "
                      f"({(cel / nazwa).stat().st_size / 1024 / 1024:.0f} MB)")

    if not (cel / "ffmpeg.exe").is_file():
        raise SystemExit("W archiwum ffmpeg nie było pliku ffmpeg.exe")
    return cel


def przygotuj_model(nazwa: str) -> Path:
    """Wypakowuje model do zwyklego folderu, gotowego do wlozenia w paczke.

    W pamieci podrecznej Hugging Face pliki leza w strukturze
    models--<org>--<repo>/snapshots/<sha>/. Do paczki wkladamy je jako
    zwykly folder <nazwa>/, bo program wlasnie takiego szuka.
    """
    log(f"Model do wbudowania: {nazwa}")
    cel = CACHE / "model" / nazwa
    if (cel / "model.bin").is_file():
        print(f"  z pamieci podrecznej: {cel}")
        return CACHE / "model"

    zrodla = [
        Path(os.environ.get("LOCALAPPDATA", "")) / WYD.plik / "models",
        Path(os.environ.get("LOCALAPPDATA", "")) / "WhisperAutomat" / "models",
        ROOT / "models",
    ]
    snapshot = None
    for zrodlo in zrodla:
        if not zrodlo.is_dir():
            continue
        # Pierwszy kompletny snapshot dowolnego repozytorium z ta nazwa.
        for kandydat in zrodlo.glob(f"models--*{nazwa}/snapshots/*"):
            if (kandydat / "model.bin").is_file():
                snapshot = kandydat
                break
        prosty = zrodlo / nazwa
        if snapshot is None and (prosty / "model.bin").is_file():
            snapshot = prosty
        if snapshot:
            break

    if snapshot is None:
        raise SystemExit(
            f"Nie znaleziono modelu {nazwa} na tym komputerze.\n"
            f"Pobierz go najpierw (uruchom program albo "
            f"tools\\pobierz_model.ps1), potem powtorz budowe."
        )

    print(f"  zrodlo: {snapshot}")
    cel.mkdir(parents=True, exist_ok=True)
    for plik in snapshot.iterdir():
        if plik.is_file():
            shutil.copy2(plik, cel / plik.name)
            print(f"  + {plik.name} ({plik.stat().st_size / 1024**2:.0f} MB)")
    return CACHE / "model"


def przygotuj_modele_mowcow() -> Optional[Path]:
    """Katalog z modelami rozpoznawania mowcow albo None, gdy ich nie ma."""
    from whisper_automat.core import diarization

    zrodlo = diarization.katalog_modeli()
    if (zrodlo / "odciski.onnx").is_file() and (
        zrodlo / "segmentacja" / "model.onnx"
    ).is_file():
        rozmiar = sum(p.stat().st_size for p in zrodlo.rglob("*") if p.is_file())
        print(f"  modele mowcow: {zrodlo} ({rozmiar / 1024**2:.0f} MB)")
        return zrodlo
    print("  modele mowcow: brak - funkcja pobierze je przy pierwszym uzyciu")
    return None


def katalogi_cuda() -> list:
    """Katalogi bin/ pakietów nvidia-* obecnych w środowisku."""
    znalezione = []
    for pakiet in CUDA_PACKAGES:
        katalog = VENV_SITE / "nvidia" / pakiet / "bin"
        if katalog.is_dir():
            rozmiar = sum(p.stat().st_size for p in katalog.glob("*.dll"))
            znalezione.append((pakiet, katalog, rozmiar))
    return znalezione


def zbuduj_aplikacje(ffmpeg_dir: Path, z_cuda: bool,
                     katalog_modelu: Optional[Path] = None) -> Path:
    log("PyInstaller")

    if BUILD.is_dir():
        for element in BUILD.iterdir():
            if element != CACHE:
                shutil.rmtree(element, ignore_errors=True) if element.is_dir() \
                    else element.unlink()
    shutil.rmtree(DIST / WYD.plik, ignore_errors=True)

    import version_info

    metadane = odczytaj_metadane()
    plik_wersji = version_info.zbuduj(metadane, BUILD / "wersja.txt", WYD)
    # Paczka musi wiedzieć, którym wydaniem jest — patrz core/wydanie.py.
    plik_wydania = wydania.zapisz(WYD, BUILD / wydania.PLIK)
    print(f"  wydanie: {WYD.nazwa} ({WYD.kod})")
    print(f"  metadane: {metadane['publisher']} / wersja {metadane['version']}")

    cmd = [
        ROOT / ".venv" / "Scripts" / "python.exe", "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", WYD.plik,
        "--windowed",                       # bez okna konsoli
        "--icon", zasoby_wydania() / "icon.ico",
        "--version-file", plik_wersji,
        "--paths", SRC,
        "--distpath", DIST,
        "--workpath", BUILD / "pyinstaller",
        "--specpath", BUILD,
        "--add-data", f"{ASSETS}{os.pathsep}assets",
        "--add-data", f"{plik_wydania}{os.pathsep}.",
        "--add-data", f"{ffmpeg_dir}{os.pathsep}bin",
    ]

    if katalog_modelu is not None:
        cmd += ["--add-data", f"{katalog_modelu}{os.pathsep}models"]

    # Modele rozpoznawania mowcow waza lacznie 44 MB - doklada sie je zawsze,
    # gdy sa na dysku, zeby funkcja dzialala takze bez internetu.
    mowcy = przygotuj_modele_mowcow()
    if mowcy is not None:
        cmd += ["--add-data", f"{mowcy}{os.pathsep}modele-mowcow"]

    for pakiet in COLLECT_ALL:
        cmd += ["--collect-all", pakiet]

    if z_cuda:
        for pakiet, katalog, rozmiar in katalogi_cuda():
            print(f"  CUDA: {pakiet} ({rozmiar / 1024 / 1024:.0f} MB)")
            cmd += ["--add-binary", f"{katalog}{os.pathsep}nvidia/{pakiet}/bin"]
    else:
        print("  budowa bez bibliotek CUDA (wariant procesorowy)")

    # Część modułów (app, pipeline) jest importowana dopiero w środku funkcji,
    # żeby przyspieszyć start — trzeba je zebrać jawnie.
    cmd += ["--collect-submodules", "whisper_automat"]

    cmd.append(ROOT / "tools" / "entry_point.py")
    run(cmd, cwd=ROOT)

    wynik = DIST / WYD.plik
    if not (wynik / f"{WYD.plik}.exe").is_file():
        raise SystemExit("PyInstaller nie wyprodukował pliku .exe")

    # Licencja programu i spis licencji składników leżą obok pliku .exe —
    # stopka okna otwiera je odnośnikiem „Licencje”.
    log("Licencje")
    import licencje

    licencje.zapisz(wynik / "LICENCJE.txt", paczka=wynik, kod_wydania=WYD.kod)
    shutil.copy2(ROOT / "LICENSE", wynik / "LICENSE.txt")
    return wynik


def rozmiar_katalogu(katalog: Path) -> float:
    return sum(p.stat().st_size for p in katalog.rglob("*") if p.is_file()) / 1024**3


def znajdz_iscc() -> Path:
    """Kompilator Inno Setup — winget instaluje go poza PATH.

    Miejsce zależy od tego, czy instalacja poszła dla wszystkich użytkowników
    (Program Files), czy tylko dla bieżącego (LOCALAPPDATA\\Programs) — winget
    domyślnie wybiera to drugie.
    """
    z_path = shutil.which("iscc")
    if z_path:
        return Path(z_path)

    bazy = [
        os.environ.get("LOCALAPPDATA", ""),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramFiles", r"C:\Program Files"),
    ]
    for baza in bazy:
        if not baza or not os.path.isdir(baza):
            continue
        # Nazwa katalogu zawiera numer wersji, którego nie chcemy zaszywać.
        for katalog in sorted(Path(baza).glob("Inno Setup*"), reverse=True):
            kandydat = katalog / "ISCC.exe"
            if kandydat.is_file():
                return kandydat

    raise SystemExit(
        "Nie znaleziono ISCC.exe (kompilator Inno Setup).\n"
        "Zainstaluj: winget install JRSoftware.InnoSetup"
    )


def ma_wbudowany_model(katalog_aplikacji: Path) -> bool:
    """Czy gotowa budowa zawiera model — tak rozpoznajemy wariant offline."""
    return any((katalog_aplikacji / "_internal" / "models").glob("*/model.bin"))


def nazwa_instalatora(wersja: str, offline: bool, cpu: bool) -> str:
    """WhisperAutomat-1.1.0-Setup(-cpu)(-offline) — bez rozszerzenia.

    Numer wersji w nazwie, bo na stronie z wydaniami obok siebie leżą
    pliki z różnych wersji, a pobrany plik ma mówić sam za siebie.
    """
    nazwa = f"{WYD.plik}-{wersja}-Setup"
    if cpu:
        nazwa += "-cpu"
    if offline:
        nazwa += "-offline"
    return nazwa


def zbuduj_instalator(katalog_aplikacji: Path) -> Path:
    log("Inno Setup")
    iscc = znajdz_iscc()
    wersja = odczytaj_wersje()
    offline = ma_wbudowany_model(katalog_aplikacji)
    # Wariant procesorowy rozpoznajemy po samej budowie, żeby --installer-only
    # nadał tę samą nazwę, co budowa, z której powstał katalog.
    cpu = not (katalog_aplikacji / "_internal" / "nvidia").is_dir()
    nazwa = nazwa_instalatora(wersja, offline, cpu)
    print(f"  wariant: {'offline (model w środku)' if offline else 'lekki (model pobierany)'}"
          f"{', tylko procesor' if cpu else ''}")

    metadane = odczytaj_metadane()
    run(
        [
            iscc,
            f"/DAppVersion={wersja}",
            f"/DAppPublisher={metadane['publisher']}",
            f"/DAppAuthor={metadane['author']}",
            f"/DAppCopyright={metadane['copyright']}",
            f"/DSourceDir={katalog_aplikacji}",
            f"/DOutputDir={DIST}",
            f"/DAssetsDir={zasoby_wydania()}",
            f"/DAppName={WYD.nazwa}",
            f"/DAppFullName={WYD.pelna_nazwa}",
            *([f"/DAppDescription={WYD.opis}"] if WYD.opis else []),
            f"/DAppFile={WYD.plik}",
            f"/DAppGuid={WYD.inno_id.strip('{}')}",
            # Wydanie z aktualizacjami instaluje się w profilu — inaczej
            # każda aktualizacja pytałaby o zgodę administratora.
            f"/DPerUser={1 if WYD.aktualizacje else 0}",
            *([f"/DAppUrl={WYD.strona_url}"] if WYD.strona_url else []),
            f"/DOutputName={nazwa}",
            f"/DWariant={'offline' if offline else 'lekki'}",
            ROOT / "tools" / "installer.iss",
        ],
        cwd=ROOT,
    )

    instalator = DIST / f"{nazwa}.exe"
    if not instalator.is_file():
        raise SystemExit("Inno Setup nie wyprodukował instalatora")
    return instalator


def zasoby_wydania() -> Path:
    """assets/<wydanie>/, gdy wydanie ma własną ikonę — inaczej wspólne assets/."""
    wlasne = ASSETS / WYD.kod
    return wlasne if (wlasne / "icon.ico").is_file() else ASSETS


def odczytaj_metadane() -> dict:
    """Wyciaga wersje, autora i firme z __init__.py pakietu.

    Czytamy plik tekstowo zamiast importowac pakiet, bo build_exe.py dziala
    takze wtedy, gdy zaleznosci programu nie sa zainstalowane.
    """
    tekst = (SRC / "whisper_automat" / "__init__.py").read_text(encoding="utf-8")
    dane = {}
    for linia in tekst.splitlines():
        for klucz in ("version", "author", "company", "publisher", "copyright"):
            if linia.startswith(f"__{klucz}__"):
                dane[klucz] = linia.split("=", 1)[1].strip().strip('"').strip("'")
    dane.setdefault("version", "1.0.0")
    dane.setdefault("author", "")
    dane.setdefault("company", "")
    dane.setdefault("copyright", "")
    dane.setdefault("publisher", dane["company"])
    # Podpis zależy od wydania: publiczne podpisuje się samą marką.
    dane["publisher"] = WYD.wydawca
    dane["copyright"] = WYD.prawa
    return dane


def odczytaj_wersje() -> str:
    return odczytaj_metadane()["version"]


def main() -> int:
    global WYD
    parser = argparse.ArgumentParser(description="Buduje instalator programu.")
    parser.add_argument("--wydanie", choices=sorted(WYDANIA), default="firma",
                        help="firma = Whisper Automat (domyslnie), "
                             "papuga = wersja publiczna z aktualizacjami.")
    parser.add_argument("--cpu", action="store_true",
                        help="Bez bibliotek CUDA — mniejsza paczka, tylko procesor.")
    parser.add_argument("--no-installer", action="store_true",
                        help="Zatrzymaj się na katalogu dist/, bez Inno Setup.")
    parser.add_argument("--installer-only", action="store_true",
                        help="Zbuduj tylko instalator z gotowego dist/.")
    parser.add_argument("--z-modelem", metavar="NAZWY", nargs="?",
                        const="large-v3-turbo", default=None,
                        help="Wariant offline: wbuduj model(e) w paczke - program "
                             "zadziala bez internetu. Domyslnie large-v3-turbo "
                             "(+1,6 GB); kilka po przecinku, np. "
                             "large-v3-turbo,small. Bez tej opcji powstaje wariant "
                             "lekki, ktory pobiera model przy pierwszym uruchomieniu.")
    args = parser.parse_args()
    WYD = WYDANIA[args.wydanie]
    if WYD.kod == "firma":
        WYD = replace(WYD, wydawca=podpis_firmy())

    if os.name != "nt":
        raise SystemExit("Budowanie działa tylko na Windows.")

    if args.installer_only:
        katalog = DIST / WYD.plik
        if not (katalog / f"{WYD.plik}.exe").is_file():
            raise SystemExit(
                f"Brak gotowej budowy w {katalog} — uruchom najpierw bez "
                f"--installer-only."
            )
        print(f"\n  Używam istniejącej budowy: {katalog}")
    else:
        ffmpeg_dir = pobierz_ffmpeg()
        katalog_modelu = None
        wybrane = getattr(args, "z_modelem")
        if wybrane:
            for nazwa in [n.strip() for n in wybrane.split(",") if n.strip()]:
                katalog_modelu = przygotuj_model(nazwa)
        katalog = zbuduj_aplikacje(
            ffmpeg_dir, z_cuda=not args.cpu, katalog_modelu=katalog_modelu
        )

    log("Wynik")
    print(f"  katalog : {katalog}")
    print(f"  rozmiar : {rozmiar_katalogu(katalog):.2f} GB")

    if args.no_installer:
        print("\n  Pominięto budowę instalatora (--no-installer).")
        return 0

    instalator = zbuduj_instalator(katalog)
    print(f"\n  INSTALATOR: {instalator}")
    print(f"  rozmiar   : {instalator.stat().st_size / 1024**3:.2f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
