"""Spis licencji składników paczki — plik LICENCJE.txt.

Program to w większości cudza praca: silnik, modele, biblioteki, ffmpeg.
Każdy z tych składników ma licencję, a prawie każda wymaga, żeby razem
z programem szła informacja o autorach i treść licencji. Ten skrypt zbiera
to w jeden plik, który build_exe.py kładzie obok pliku .exe, a okno programu
otwiera odnośnikiem „Licencje” w stopce.

Skąd biorą się wpisy:
  * pakiety Pythona — z metadanych środowiska .venv: nazwa, wersja, licencja
    i pliki LICENSE/NOTICE. Bierzemy wszystkie poza narzędziami budowy, bo
    PyInstaller pakuje też kod pakietów, które nie zostawiają w paczce
    katalogu dist-info (siedzą w archiwum PYZ) — a one też mają licencje.
  * reszta (Python, Tcl/Tk, ffmpeg, modele, bootloader PyInstallera,
    Inno Setup) jest opisana ręcznie w `skladniki_reczne()`.

Pakiet bez własnego pliku licencji dostaje standardowy tekst z katalogu
teksty-licencji/ (MIT, Apache-2.0, BSD-3-Clause, GPL-3.0).

Uruchomienie:
    .venv\\Scripts\\python.exe tools\\licencje.py
        -> LICENCJE.txt w katalogu repozytorium (do publikacji kodu)
    .venv\\Scripts\\python.exe tools\\licencje.py --cel dist\\Papuga\\LICENCJE.txt --paczka dist\\Papuga
        -> plik do paczki; z --paczka skrypt sprawdza dodatkowo, czy w paczce
           nie leży pakiet, którego spis nie zna. Nowa zależność ma zostać
           zauważona, nie przemilczana.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.metadata as metadata
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / ".venv" / "Lib" / "site-packages"
TEKSTY = Path(__file__).resolve().parent / "teksty-licencji"

sys.path.insert(0, str(ROOT / "src"))

#: Narzędzia budowy — nie trafiają do paczki. Bootloader PyInstallera,
#: który trafia (to on jest początkiem pliku .exe), opisany jest osobno.
NARZEDZIA_BUDOWY = {
    "altgraph", "pefile", "pyinstaller", "pyinstaller-hooks-contrib",
    "pywin32-ctypes", "pip", "wheel",
}

#: Wpisy w _internal, które nie są pakietami Pythona: zasoby programu,
#: dane Tcl/Tk, biblioteki CUDA (te są w spisie jako pakiety nvidia-*).
NIE_PAKIETY = {
    "_tcl_data", "_tk_data", "tcl8", "assets", "bin", "models", "modele-mowcow",
    "nvidia", "base_library.zip", "wydanie.json",
}

WZOR_PLIKU_LICENCJI = re.compile(r"licen[cs]e|copying|notice|authors", re.IGNORECASE)

#: Ujednolicenie nazw licencji z metadanych (kolejność ma znaczenie:
#: „MIT-CMU” przed „MIT”, „Apache” przed resztą).
ODMIANY = [
    ("apache", "Apache-2.0"),
    ("mit-cmu", "MIT-CMU"),
    ("mit", "MIT"),
    ("bsd-3", "BSD-3-Clause"),
    ("3-clause bsd", "BSD-3-Clause"),
    ("bsd license", "BSD-3-Clause"),
    ("mpl-2.0", "MPL-2.0"),
    ("mozilla", "MPL-2.0"),
    ("psf", "PSF-2.0"),
    ("nvidia", "Licencja NVIDIA (własnościowa; biblioteki wolno rozpowszechniać razem z programem)"),
    ("proprietary", "Licencja NVIDIA (własnościowa; biblioteki wolno rozpowszechniać razem z programem)"),
]

STANDARDOWE = {
    "MIT": "MIT.txt",
    "Apache-2.0": "Apache-2.0.txt",
    "BSD-3-Clause": "BSD-3-Clause.txt",
    "GPL-3.0-or-later": "GPL-3.0.txt",
}


@dataclass
class Skladnik:
    nazwa: str
    wersja: str
    licencja: str
    strona: str = ""
    opis: str = ""
    #: (tytuł pliku, treść)
    teksty: List[Tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pakiety Pythona
# ---------------------------------------------------------------------------


def _norm(nazwa: str) -> str:
    return re.sub(r"[-_.]+", "-", nazwa).lower()


def _ujednolic(tekst: str) -> str:
    niski = tekst.lower()
    for fragment, wynik in ODMIANY:
        if fragment in niski:
            return wynik
    return tekst


def _licencja_pakietu(m) -> str:
    wyrazenie = m.get("License-Expression")
    if wyrazenie:
        return wyrazenie.strip()
    pole = (m.get("License") or "").strip()
    pierwsza = pole.splitlines()[0].strip() if pole else ""
    if pierwsza and len(pierwsza) <= 90 and len(pole.splitlines()) <= 2:
        return _ujednolic(pierwsza)
    klasyfikatory = [
        c.split("::")[-1].strip()
        for c in (m.get_all("Classifier") or [])
        if c.startswith("License")
    ]
    if klasyfikatory:
        return _ujednolic(klasyfikatory[0])
    return "nieokreślona w metadanych"


def _strona_pakietu(m) -> str:
    strona = (m.get("Home-page") or "").strip()
    if strona:
        return strona
    kandydaci = m.get_all("Project-URL") or []
    for klucz in ("homepage", "source", "repository", "github"):
        for wpis in kandydaci:
            etykieta, _, adres = wpis.partition(",")
            if klucz in etykieta.lower():
                return adres.strip()
    return kandydaci[0].partition(",")[2].strip() if kandydaci else ""


def _teksty_pakietu(d) -> List[Tuple[str, str]]:
    wynik = []
    for plik in d.files or []:
        czesci = plik.parts
        if len(czesci) < 2 or not czesci[0].endswith(".dist-info"):
            continue
        if not WZOR_PLIKU_LICENCJI.search(czesci[-1]):
            continue
        try:
            tresc = Path(str(plik.locate())).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if tresc.strip():
            wynik.append(("/".join(czesci[1:]), tresc.strip()))
    return wynik


def _standardowy(klucz: str, posiadacz: str) -> List[Tuple[str, str]]:
    plik = STANDARDOWE.get(klucz)
    if not plik or not (TEKSTY / plik).is_file():
        return []
    tresc = (TEKSTY / plik).read_text(encoding="utf-8").replace("{posiadacz}", posiadacz)
    return [(f"{klucz} (tekst standardowy)", tresc.strip())]


def pakiety_pythona(site: Path = SITE) -> Tuple[List[Skladnik], Dict[str, Set[str]]]:
    """Składniki z pakietów środowiska oraz mapa dystrybucja -> moduły."""
    skladniki: List[Skladnik] = []
    moduly: Dict[str, Set[str]] = {}
    for d in metadata.distributions(path=[str(site)]):
        m = d.metadata
        nazwa = m["Name"]
        if _norm(nazwa) in NARZEDZIA_BUDOWY:
            continue
        gorne = set((d.read_text("top_level.txt") or "").split())
        moduly[_norm(nazwa)] = {g.lower() for g in gorne} | {_norm(nazwa).replace("-", "_")}

        licencja = _licencja_pakietu(m)
        teksty = _teksty_pakietu(d)
        if not teksty:
            pole = (m.get("License") or "").strip()
            if len(pole.splitlines()) > 2:
                teksty = [("License (z metadanych)", pole)]
            else:
                teksty = _standardowy(_ujednolic(licencja), f"autorzy pakietu {nazwa}")
        opis = (m.get("Summary") or "").strip()
        skladniki.append(Skladnik(
            nazwa=nazwa, wersja=m["Version"], licencja=licencja,
            strona=_strona_pakietu(m), opis=opis, teksty=teksty,
        ))
    skladniki.sort(key=lambda s: s.nazwa.lower())
    return skladniki, moduly


# ---------------------------------------------------------------------------
# Składniki spoza pip
# ---------------------------------------------------------------------------


def _z_pliku(sciezka: Optional[Path], tytul: str = "") -> List[Tuple[str, str]]:
    if sciezka is None or not Path(sciezka).is_file():
        return []
    return [(tytul or Path(sciezka).name,
             Path(sciezka).read_text(encoding="utf-8", errors="replace").strip())]


def _wersja_ffmpeg(paczka: Optional[Path]) -> str:
    kandydaci = [ROOT / "build" / "cache" / "ffmpeg" / "ffmpeg.exe"]
    if paczka is not None:
        kandydaci.insert(0, Path(paczka) / "_internal" / "bin" / "ffmpeg.exe")
    for exe in kandydaci:
        if not exe.is_file():
            continue
        try:
            out = subprocess.run([str(exe), "-version"], capture_output=True, text=True,
                                 timeout=20, creationflags=0x08000000 if os.name == "nt" else 0)
        except Exception:
            continue
        pierwsza = (out.stdout or "").splitlines()[:1]
        if pierwsza and pierwsza[0].startswith("ffmpeg version"):
            return pierwsza[0].split()[2]
    return ""


def _wersja_tk() -> str:
    try:
        import tkinter

        return str(tkinter.TkVersion)
    except Exception:
        return ""


def _inno_license() -> List[Tuple[str, str]]:
    bazy = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
    ]
    for baza in bazy:
        if not baza.is_dir():
            continue
        for katalog in sorted(baza.glob("Inno Setup*"), reverse=True):
            if (katalog / "license.txt").is_file():
                return _z_pliku(katalog / "license.txt", "license.txt (Inno Setup)")
    return []


def skladniki_reczne(paczka: Optional[Path]) -> List[Skladnik]:
    baza_py = Path(sys.base_prefix)
    lista = [
        Skladnik(
            "Python", platform.python_version(), "PSF-2.0", "https://www.python.org",
            "Interpreter języka, w którym napisany jest program. Jego pakiet dla "
            "Windows zawiera też biblioteki OpenSSL, SQLite, zlib, libffi, bzip2 "
            "i expat — ich warunki są w treści poniżej (rozdział o kompilacji dla Windows).",
            _z_pliku(baza_py / "LICENSE.txt", "LICENSE.txt (Python)"),
        ),
        Skladnik(
            "Tcl/Tk", _wersja_tk(), "TCL (licencja w stylu BSD)", "https://www.tcl.tk",
            "Biblioteka okienek, z której korzysta interfejs programu (tkinter).",
            next((_z_pliku(p, "license.terms (Tk)")
                  for p in baza_py.glob("tcl/tk*/license.terms")), []),
        ),
        Skladnik(
            "FFmpeg", _wersja_ffmpeg(paczka), "GPL-3.0-or-later", "https://ffmpeg.org",
            "Wyciąga ścieżkę dźwiękową z nagrań i wycina próbki głosu (pliki "
            "ffmpeg.exe i ffprobe.exe w katalogu bin). Kompilacja „essentials” "
            "z www.gyan.dev, zbudowana z opcjami --enable-gpl --enable-version3, "
            "uruchamiana przez program jako osobny proces. Kod źródłowy FFmpeg: "
            "https://ffmpeg.org/download.html; skrypty tej kompilacji: "
            "https://github.com/GyanD/codexffmpeg",
            _standardowy("GPL-3.0-or-later", "the FFmpeg developers"),
        ),
        Skladnik(
            "Whisper large-v3-turbo (model rozpoznawania mowy)", "", "MIT",
            "https://github.com/openai/whisper",
            "Wagi modelu OpenAI Whisper w formacie CTranslate2, z repozytorium "
            "mobiuslabsgmbh/faster-whisper-large-v3-turbo na huggingface.co. "
            "W wariancie lekkim pobierane przy pierwszym uruchomieniu, "
            "w wariancie offline dołączone do paczki (katalog models).",
            _standardowy("MIT", "2022 OpenAI"),
        ),
        Skladnik(
            "pyannote segmentation-3.0 (model segmentacji mowy)", "", "MIT",
            "https://github.com/pyannote/pyannote-audio",
            "Dzieli nagranie na odcinki, w których ktoś mówi. Wersja ONNX "
            "z wydań projektu sherpa-onnx (modele-mowcow/segmentacja/model.onnx). "
            "Autor modelu: Hervé Bredin, pyannote.audio.",
            _standardowy("MIT", "pyannote.audio contributors"),
        ),
        Skladnik(
            "3D-Speaker ERes2Net (model odcisków głosu)", "", "Apache-2.0",
            "https://github.com/modelscope/3D-Speaker",
            "Opisuje głos wektorem, po którym program poznaje tę samą osobę "
            "(modele-mowcow/odciski.onnx). Projekt 3D-Speaker, Alibaba DAMO Academy; "
            "plik ONNX z wydań sherpa-onnx.",
            _standardowy("Apache-2.0", "Alibaba DAMO Academy"),
        ),
    ]

    pyinstaller = None
    for d in metadata.distributions(path=[str(SITE)]):
        if _norm(d.metadata["Name"]) == "pyinstaller":
            pyinstaller = d
            break
    lista.append(Skladnik(
        "PyInstaller (bootloader)", pyinstaller.version if pyinstaller else "",
        "GPL-2.0-or-later z wyjątkiem dla bootloadera", "https://pyinstaller.org",
        "Program startowy, który rozpakowuje i uruchamia kod Pythona — od niego "
        "zaczyna się plik .exe. Wyjątek w licencji pozwala łączyć go z programem "
        "na dowolnej licencji.",
        _teksty_pakietu(pyinstaller) if pyinstaller else [],
    ))
    lista.append(Skladnik(
        "Inno Setup", "6", "Licencja Inno Setup (bezpłatna, także do użytku komercyjnego)",
        "https://jrsoftware.org/isinfo.php",
        "Narzędzie, którym zbudowany jest instalator programu.",
        _inno_license(),
    ))
    return lista


# ---------------------------------------------------------------------------
# Spis i zapis
# ---------------------------------------------------------------------------


def _wersja_programu() -> str:
    for linia in (ROOT / "src" / "whisper_automat" / "__init__.py").read_text(
        encoding="utf-8"
    ).splitlines():
        if linia.startswith("__version__"):
            return linia.split("=", 1)[1].strip().strip("\"'")
    return ""


def sprawdz_paczke(paczka: Path, moduly: Dict[str, Set[str]]) -> List[str]:
    """Wpisy w _internal, których spis nie obejmuje — do pokazania budującemu."""
    internal = Path(paczka) / "_internal"
    if not internal.is_dir():
        return []
    znane_moduly = set().union(*moduly.values()) if moduly else set()
    nieznane = []
    for wpis in sorted(internal.iterdir()):
        nazwa = wpis.name
        if nazwa.endswith(".dist-info"):
            if _norm(nazwa[:-len(".dist-info")].rsplit("-", 1)[0]) not in moduly:
                nieznane.append(nazwa)
        elif wpis.suffix.lower() in (".dll", ".pyd", ".txt") or nazwa in NIE_PAKIETY:
            continue
        elif nazwa.split(".")[0].lower() not in znane_moduly:
            nieznane.append(nazwa)
    return nieznane


def zbuduj_tekst(skladniki: List[Skladnik], kod_wydania: str) -> str:
    from whisper_automat.core.wydanie import WYDANIA

    wyd = WYDANIA.get(kod_wydania, WYDANIA["papuga"])
    dzis = dt.date.today().isoformat()
    linie = [
        f"{wyd.pelna_nazwa} {_wersja_programu()} — licencje składników",
        "=" * 72,
        "",
        f"Sam program ({wyd.nazwa}, kod w pakiecie whisper_automat) jest na licencji MIT.",
        "Jej treść jest w pliku LICENSE.txt obok. Autor: MATCODE.",
        "",
        "Poniżej składniki, z których program korzysta i które są rozpowszechniane",
        "razem z nim, wraz z ich licencjami. Biblioteki NVIDIA (nvidia-*) są tylko",
        "w wariancie z obsługą karty graficznej; model Whispera jest w paczce tylko",
        "w wariancie offline, w lekkim pobiera się przy pierwszym uruchomieniu.",
        "",
        f"Spis wygenerowany {dzis} narzędziem tools/licencje.py.",
        "",
        "SPIS",
        "----",
    ]
    for i, s in enumerate(skladniki, 1):
        wersja = f" {s.wersja}" if s.wersja else ""
        linie.append(f"{i:3d}. {s.nazwa}{wersja} — {s.licencja}")
    linie.append("")

    for i, s in enumerate(skladniki, 1):
        linie += ["", "=" * 72, f"{i}. {s.nazwa}" + (f" {s.wersja}" if s.wersja else ""),
                  "=" * 72]
        linie.append(f"Licencja : {s.licencja}")
        if s.strona:
            linie.append(f"Strona   : {s.strona}")
        if s.opis:
            linie.append(f"Opis     : {s.opis}")
        if not s.teksty:
            linie.append("Treść    : patrz strona projektu.")
        for tytul, tresc in s.teksty:
            linie += ["", f"--- {tytul} ---", tresc]
    linie.append("")
    return "\n".join(linie)


def zapisz(cel: Path, paczka: Optional[Path] = None, kod_wydania: str = "papuga") -> Path:
    z_pip, moduly = pakiety_pythona()
    skladniki = skladniki_reczne(paczka) + z_pip
    cel = Path(cel)
    cel.parent.mkdir(parents=True, exist_ok=True)
    cel.write_text(zbuduj_tekst(skladniki, kod_wydania), encoding="utf-8")
    bez_tekstu = [s.nazwa for s in skladniki if not s.teksty]
    print(f"  licencje: {len(skladniki)} składników -> {cel} "
          f"({cel.stat().st_size / 1024:.0f} KB)")
    if bez_tekstu:
        print("  UWAGA: bez treści licencji: " + ", ".join(bez_tekstu))
    if paczka is not None:
        nieznane = sprawdz_paczke(Path(paczka), moduly)
        if nieznane:
            print("  UWAGA: w paczce są wpisy spoza spisu licencji: " + ", ".join(nieznane))
    return cel


def main() -> int:
    parser = argparse.ArgumentParser(description="Generuje LICENCJE.txt.")
    parser.add_argument("--cel", type=Path, default=ROOT / "LICENCJE.txt")
    parser.add_argument("--paczka", type=Path, default=None,
                        help="Katalog zbudowanej aplikacji (dist\\Papuga) do sprawdzenia.")
    parser.add_argument("--wydanie", default="papuga")
    args = parser.parse_args()
    zapisz(args.cel, args.paczka, args.wydanie)
    return 0


if __name__ == "__main__":
    sys.exit(main())
