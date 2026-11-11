"""Buduje samodzielną wersję programu i instalator .exe.

Dwa warianty instalatora:
  * lekki (domyślny) — bez modelu, ~0,9 GB. Model (~1,6 GB) program pobiera
    przy pierwszym uruchomieniu. Wersja do rozdawania przez internet.
  * offline (--z-modelem) — model w środku, ~2,4 GB. Nic nie pobiera, więc
    działa także za firewallem, który blokuje huggingface.co.

Etapy:
  1. pobranie lekkiej wersji ffmpeg (raz, potem z pamięci podręcznej)
  2. PyInstaller — katalog `dist/<Wydanie.plik>` z całą zawartością
  3. spis licencji składników (tools/licencje.py) i LICENSE (tylko Papuga)
     obok pliku .exe
  4. Inno Setup — jeden plik `dist/<Wydanie.plik>-<wersja>-Setup[-offline].exe`

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
from whisper_automat import teksty  # noqa: E402

#: Wydanie, które budujemy — ustawiane w main() z --wydanie.
WYD: Wydanie = WYDANIA[wydania.DOMYSLNE]

#: Podpis wydania firmowego z imieniem i nazwiskiem leży POZA repozytorium:
#: w tools/podpis_firmy.local.txt (plik ignorowany przez gita) albo w zmiennej
#: WHISPER_AUTOMAT_PODPIS_FIRMY. Kod repozytorium jest publikowany, a imię
#: i nazwisko ma być tylko w paczce firmowej — idzie tam przez wydanie.json.
PLIK_PODPISU = Path(__file__).resolve().parent / "podpis_firmy.local.txt"


def podpis_firmy() -> str:
    """Podpis wydania firmowego. Bez niego budowa staje — wydanie firmowe
    nie może podpisać się marką publiczną ani zostać bez wydawcy."""
    ze_srodowiska = os.environ.get("WHISPER_AUTOMAT_PODPIS_FIRMY", "").strip()
    if ze_srodowiska:
        return ze_srodowiska
    try:
        tekst = PLIK_PODPISU.read_text(encoding="utf-8").strip()
    except OSError:
        tekst = ""
    if not tekst:
        raise SystemExit(
            f"Brak {PLIK_PODPISU} — wydanie firmowe nie ma czym się podpisać.\n"
            f"  Wpisz do pliku jedną linię: „Imię Nazwisko – Firma”."
        )
    return tekst


#: Adres do zgłoszeń z wydania firmowego („Zgłoś błąd lub pomysł” w stopce)
#: — też poza repozytorium, jak podpis. Bez pliku stopka nie ma odnośnika.
PLIK_KONTAKTU = Path(__file__).resolve().parent / "kontakt_firmy.local.txt"


def kontakt_firmy() -> str:
    ze_srodowiska = os.environ.get("WHISPER_AUTOMAT_KONTAKT_FIRMY", "").strip()
    if ze_srodowiska:
        return ze_srodowiska
    try:
        tekst = PLIK_KONTAKTU.read_text(encoding="utf-8").strip()
    except OSError:
        tekst = ""
    if not tekst:
        print(f"  UWAGA: brak {PLIK_KONTAKTU.name} — stopka bez „Zgłoś błąd lub pomysł”.")
    return tekst


#: Folder z aktualizacjami wydania firmowego (udział sieciowy albo adres
#: http) — też poza repozytorium, bo adres udziału firmy nie ma trafić do
#: publikowanego kodu. Do paczki idzie przez wydanie.json, tak jak podpis.
#: Przy przeprowadzce folderu: kilka ścieżek, każda w osobnym wierszu, nowa
#: pierwsza — program bierze pierwszą istniejącą (core/wydanie.py).
PLIK_AKTUALIZACJI = Path(__file__).resolve().parent / "aktualizacje_firmy.local.txt"


def folder_aktualizacji_firmy() -> str:
    tekst = os.environ.get("WHISPER_AUTOMAT_AKTUALIZACJE", "").strip()
    if not tekst:
        try:
            tekst = PLIK_AKTUALIZACJI.read_text(encoding="utf-8").strip()
        except OSError:
            tekst = ""
    foldery = replace(WYDANIA["firma"], aktualizacje_folder=tekst).foldery_aktualizacji
    if not foldery:
        print(f"  UWAGA: brak {PLIK_AKTUALIZACJI.name} — wydanie firmowe nie będzie "
              f"sprawdzać aktualizacji.")
    for i, folder in enumerate(foldery, 1):
        print(f"  Folder aktualizacji {i}/{len(foldery)}: {folder}")
    return ";".join(foldery)

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
    "pyaudiowpatch",  # nagrywanie spotkań: PortAudio z WASAPI loopback (.pyd)
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

    # Najpierw katalog modeli budowanego wydania, potem pozostałych.
    zrodla = [
        *dict.fromkeys(Path(os.environ.get("LOCALAPPDATA", "")) / w.plik / "models"
                       for w in (WYD, *WYDANIA.values())),
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
        "--add-data", f"{przygotuj_zasoby()}{os.pathsep}assets",
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
    # Uwaga: collect-submodules nie widzi pakietu (zwraca pustą listę, sprawdzone
    # 2026-11-06) — moduły trafiają do paczki z importów. Moduł danych wydania
    # dokładamy więc jawnie, a dane innych wydań (nazwa, marka) wykluczamy.
    cmd += ["--hidden-import", f"whisper_automat.wydania.{wydania.MODUL[WYD.kod]}"]
    for modul in moduly_innych_wydan():
        print(f"  pomijam moduł {modul}")
        cmd += ["--exclude-module", modul]

    cmd.append(ROOT / "tools" / "entry_point.py")
    run(cmd, cwd=ROOT)

    wynik = DIST / WYD.plik
    if not (wynik / f"{WYD.plik}.exe").is_file():
        raise SystemExit("PyInstaller nie wyprodukował pliku .exe")

    # Licencja programu i spis licencji składników leżą obok pliku .exe —
    # stopka okna otwiera je odnośnikiem „Licencje”. Wydanie firmowe nie
    # dostaje LICENSE (MIT z marką MATCODE): nic w nim nie wskazuje autora,
    # zasady korzystania opisuje okno „Licencja”.
    log("Licencje")
    import licencje

    licencje.zapisz(wynik / "LICENCJE.txt", paczka=wynik, kod_wydania=WYD.kod,
                    firma=WYD.firma)
    licencja_mit = wynik / "LICENSE.txt"
    if WYD.kod == "firma":
        licencja_mit.unlink(missing_ok=True)
    else:
        shutil.copy2(ROOT / "LICENSE", licencja_mit)
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
    """<Plik>-1.1.0-Setup(-cpu)(-offline) — bez rozszerzenia.

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
            # Ekran powitalny po angielsku bierze opis ze słownika programu.
            *([f"/DAppDescriptionEn={teksty.EN[WYD.opis]}"]
              if WYD.opis in teksty.EN else []),
            f"/DAppFile={WYD.plik}",
            f"/DAppGuid={WYD.inno_id.strip('{}')}",
            # Oba wydania instalują się w profilu użytkownika, bez hasła
            # administratora (decyzja 2026-10-09; do 1.3.0 wydanie firmowe
            # szło do Program Files). Starą kopię z Program Files zdejmuje
            # sam instalator — PrepareToInstall w installer.iss.
            "/DPerUser=1",
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
    """assets/<Wydanie.zasoby>/, gdy wydanie ma własną ikonę — inaczej wspólne assets/."""
    wlasne = ASSETS / WYD.zasoby if WYD.zasoby else ASSETS
    return wlasne if (wlasne / "icon.ico").is_file() else ASSETS


def przygotuj_zasoby() -> Path:
    """Kopia assets/ do paczki bez katalogów grafik innych wydań.

    Paczka firmowa nie może mieć grafik marki drugiego wydania (logo,
    filiżanka) — nie wystarczy, że program ich nie pokazuje.
    """
    cudze = {w.zasoby for w in WYDANIA.values() if w.zasoby and w.zasoby != WYD.zasoby}
    cel = BUILD / "assets"
    shutil.rmtree(cel, ignore_errors=True)
    shutil.copytree(ASSETS, cel, ignore=lambda katalog, nazwy: [
        n for n in nazwy if Path(katalog) == ASSETS and n in cudze])
    for nazwa in sorted(cudze):
        print(f"  pomijam assets/{nazwa}/ (grafiki innego wydania)")
    return cel


def moduly_innych_wydan() -> list:
    """Moduły z pakietu `wydania`, których ta paczka nie może zawierać."""
    return [f"whisper_automat.wydania.{modul}" for kod, modul in sorted(wydania.MODUL.items())
            if kod != WYD.kod]


def sprawdz_tozsamosc(katalog: Path) -> None:
    """Przed instalatorem: paczka nie może zdradzać drugiego wydania.

    Firma — `sprawdz-firmowe.js` bez śladu MATCODE i nazwy wydania
    publicznego; publiczne — `--publiczne`, bez śladu firmy i nazwy wydania
    firmowego. Obie razem z installer.iss. Bez CZYSTO (albo bez narzędzia)
    budowa staje — niesprawdzonej paczki się nie wydaje (tools/sprawdz_marke.py).
    """
    import sprawdz_marke

    publiczne = WYD.kod != "firma"
    log("Sprawdzenie: " + ("bez śladu firmy" if publiczne else "bez śladu marki publicznej"))
    tez = [s for kod in sorted(WYDANIA) if kod != WYD.kod for s in sprawdz_marke.slowa_wydania(kod)]
    sprawdz_marke.sprawdz([katalog, ROOT / "tools" / "installer.iss"], tez,
                          publiczne=publiczne, opis=f"paczka {WYD.nazwa}")


def odczytaj_metadane() -> dict:
    """Wersja z __init__.py pakietu, wydawca i prawa z wydania.

    Wersję czytamy z pliku tekstowo zamiast importować pakiet, bo build_exe.py
    działa także wtedy, gdy zależności programu nie są zainstalowane.
    """
    tekst = (SRC / "whisper_automat" / "__init__.py").read_text(encoding="utf-8")
    dane = {"version": "1.0.0"}
    for linia in tekst.splitlines():
        if linia.startswith("__version__"):
            dane["version"] = linia.split("=", 1)[1].strip().strip('"').strip("'")
    # Podpis zależy od wydania: publiczne podpisuje się samą marką, firmowe
    # podpisem z tools/podpis_firmy.local.txt.
    dane["author"] = dane["company"] = dane["publisher"] = WYD.wydawca
    dane["copyright"] = WYD.prawa
    return dane


def odczytaj_wersje() -> str:
    return odczytaj_metadane()["version"]


def main() -> int:
    global WYD
    parser = argparse.ArgumentParser(description="Buduje instalator programu.")
    parser.add_argument("--wydanie", choices=sorted(WYDANIA), default=wydania.DOMYSLNE,
                        help="firma = wydanie firmowe (domyslnie), "
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
        # Ten sam tekst podpisuje plik .exe i instalator (wydawca) oraz
        # stopkę okna (autor). Folder aktualizacji — udział sieciowy firmy.
        podpis = podpis_firmy()
        WYD = replace(WYD, wydawca=podpis, autor=podpis,
                      aktualizacje_folder=folder_aktualizacji_firmy(),
                      kontakt_email=kontakt_firmy())

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

    # Przed instalatorem: w paczce firmowej nie może być śladu marki
    # publicznej, w publicznej — firmy. Trafienie zatrzymuje budowę (także
    # z --installer-only).
    sprawdz_tozsamosc(katalog)

    if args.no_installer:
        print("\n  Pominięto budowę instalatora (--no-installer).")
        return 0

    instalator = zbuduj_instalator(katalog)
    print(f"\n  INSTALATOR: {instalator}")
    print(f"  rozmiar   : {instalator.stat().st_size / 1024**3:.2f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
