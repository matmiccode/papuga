"""Wydania programu — jeden kod, dwie marki.

  * firmowe   — z modelem w instalatorze, aktualizacje z folderu firmowego
                (udział sieciowy; adres tylko w paczce, tak jak podpis
                autora — patrz niżej), baza głosów.
  * publiczne — z GitHuba, model pobierany przy pierwszym uruchomieniu,
                aktualizacje z wydań repozytorium, link do wsparcia.

Dane każdego wydania (nazwa, identyfikatory, marka, klucz) leżą w jego
module w pakiecie `wydania`; ten plik ich nie zna. Paczka wydania ma
tylko swój moduł (tools/build_exe.py), więc nazwa i marka drugiego
wydania do niej nie trafiają.

Wydania różnią się nazwą, plikiem .exe, identyfikatorem instalatora
i katalogiem danych, więc oba programy mogą stać obok siebie na jednym
komputerze i nie nadpisują się nawzajem.

Które wydanie działa, wybiera budowa (`build_exe.py --wydanie`): zapisuje
`wydanie.json` do paczki. Uruchomione z kodu bierze zmienną
WHISPER_AUTOMAT_WYDANIE, a bez niej — wydanie firmowe (gdy jego moduł
jest), inaczej pierwsze dostępne.

Spakowany program bierze z `wydanie.json` wszystkie pola, nie tylko kod.
Dzięki temu podpis z imieniem i nazwiskiem autora (wydanie firmowe) żyje
tylko w lokalnym pliku `tools/podpis_firmy.local.txt` — poza repozytorium —
i trafia wyłącznie do paczki firmowej. Uruchomione z kodu wydanie firmowe
bierze ten podpis ze zmiennej WHISPER_AUTOMAT_PODPIS_FIRMY (ustawia ją
np. tools/zrzut_okna.py), a bez niej nie ma wydawcy. Tak samo folder
z aktualizacjami: w paczce z tools/aktualizacje_firmy.local.txt, z kodu ze
zmiennej WHISPER_AUTOMAT_AKTUALIZACJE, a bez niej wydanie firmowe
aktualizacji nie sprawdza (przy przeprowadzce folderu: kilka ścieżek,
nowa pierwsza). I adres do zgłoszeń („Zgłoś błąd lub pomysł” w stopce):
w paczce z tools/kontakt_firmy.local.txt, z kodu ze zmiennej
WHISPER_AUTOMAT_KONTAKT_FIRMY, a bez niej stopka nie ma tego odnośnika.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Dict, Optional

from .. import teksty

PLIK = "wydanie.json"


@dataclass(frozen=True)
class Wydanie:
    kod: str
    #: Krótka nazwa pokazywana ludziom: menu Start, komunikaty, instalator.
    nazwa: str
    #: Nazwa techniczna bez spacji: plik .exe, katalog danych, instalator.
    plik: str
    #: Identyfikator dla paska zadań Windows.
    app_id: str
    #: AppId instalatora Inno Setup. To on decyduje, czy Windows uznaje
    #: instalację za tę samą aplikację — dlatego każde wydanie ma własny.
    inno_id: str
    #: Repozytorium z wydaniami („właściciel/nazwa”) — źródło aktualizacji
    #: wydania publicznego (API GitHuba). Puste = nie z GitHuba.
    repo: str = ""
    #: Folder z wydaniami dla sieci firmowej — źródło aktualizacji wydania
    #: firmowego: ścieżka UNC (\\serwer\udział\…) albo adres http(s). Leżą
    #: w nim `najnowsza.json`, instalator i jego `.podpis`
    #: (tools/publikuj_wydanie.py --wydanie firma). Z folderu program
    #: instaluje wyłącznie wydania podpisane kluczem autora. Puste = nie
    #: z folderu. Bez `repo` i bez folderu wydanie aktualizacji nie sprawdza.
    #: Kilka folderów rozdziela się średnikiem: przy przeprowadzce folderu
    #: wersja przejściowa zna nowy i stary, nowy pierwszy — liczy się
    #: pierwszy, który istnieje (`osiagalny_folder`).
    aktualizacje_folder: str = ""
    #: Strona wsparcia autora. Pusta = brak odnośnika w nagłówku okna.
    wsparcie_url: str = ""
    #: Napis odnośnika wsparcia (przez `t()`, tłumaczenie w module wydania)
    #: i ikonka przed nim (plik z `asset()`).
    wsparcie_tekst: str = ""
    wsparcie_ikona: str = ""
    #: Adres do kontaktu. Pusty = brak w oknie „O programie”.
    kontakt_email: str = ""
    #: Klucz publiczny Ed25519 (szesnastkowo) do sprawdzania podpisu wydań
    #: (core/podpis.py). Z kluczem program instaluje sam tylko wydania
    #: podpisane kluczem prywatnym autora (tools/klucz_wydan.py) — przejęte
    #: konto GitHub nie wystarczy, żeby podsunąć ludziom obcy plik.
    klucz_publiczny: str = ""
    #: Pierwsza linia pliku `.podpis`, którą program przyjmuje (core/podpis.py).
    naglowek_podpisu: str = "podpis-wydania 1"
    #: Podpis wydawcy: okno, właściwości pliku .exe, instalator. Wydanie
    #: publiczne podpisuje się samą marką, bez imienia i nazwiska autora.
    wydawca: str = ""
    #: Dopisek po myślniku w tytule okna, np. „transkrypcje offline”.
    haslo: str = ""
    #: Jedno-dwa zdania o programie: opis na GitHubie, ekran powitalny
    #: instalatora, opis pliku .exe.
    opis: str = ""
    #: Podpis autora w stopce okna (np. imię, nazwisko i firma). Gdy pusty,
    #: nagłówek pokazuje samego wydawcę. Wydanie firmowe dostaje go z paczki
    #: (patrz opis modułu), publiczne nie ma go wcale.
    autor: str = ""
    #: Nagrywanie spotkań w oknie: mikrofon i dźwięk systemowy prosto do
    #: kolejki transkrypcji (core/nagrywanie.py).
    nagrywanie: bool = False
    #: Podkatalog assets/ z grafikami wydania (ikona, logo, ikonka wsparcia).
    #: Pusty = wspólne assets/. Paczka innego wydania go nie dostaje.
    zasoby: str = ""
    #: Barwy pasów paska postępu pliku. Puste = jeden kolor akcentu.
    kolory_postepu: tuple = ()

    @property
    def pelna_nazwa(self) -> str:
        """„Nazwa – hasło” — tytuł okna i wpis w Aplikacjach."""
        return f"{self.nazwa} – {self.haslo}" if self.haslo else self.nazwa

    @property
    def osoba(self) -> str:
        """Imię i nazwisko z podpisu autora („Jan Kowalski – Firma” → „Jan
        Kowalski”) — w stopce jako adresat zgłoszeń, nie jako podpis."""
        return self.autor.partition(" – ")[0].strip()

    @property
    def firma(self) -> str:
        """Firma z podpisu autora („Jan Kowalski – Firma” → „Firma”). Pusta
        w wydaniu publicznym; niepusta — okno „Licencja” mówi, że to program
        firmowy. Nazwa żyje tylko w paczce, jak cały podpis."""
        return self.autor.partition(" – ")[2].strip()

    @property
    def prawa(self) -> str:
        return f"© 2026 {self.wydawca}" if self.wydawca else "© 2026"

    @property
    def strona_url(self) -> str:
        return f"https://github.com/{self.repo}" if self.repo else ""

    @property
    def zgloszenia_url(self) -> str:
        return f"https://github.com/{self.repo}/issues/new" if self.repo else ""

    @property
    def aktualizacje(self) -> bool:
        return bool(self.repo or self.foldery_aktualizacji)

    @property
    def foldery_aktualizacji(self) -> tuple:
        """Foldery wydań w kolejności: nowy (po przeprowadzce) przed starym."""
        return tuple(f.strip() for f in re.split(r"[;\r\n]", self.aktualizacje_folder)
                     if f.strip())


def jest_url(folder: str) -> bool:
    return folder.lower().startswith(("http://", "https://"))


def osiagalny_folder(foldery) -> str:
    """Pierwszy folder, który istnieje (adres http liczy się zawsze), albo "".

    Folder przenosi się w całości, więc gdy nowy już jest, stary się nie
    liczy — nawet jeśli jeszcze leży. Sprawdzenie ścieżki UNC poza siecią
    firmy potrafi trwać kilka sekund: nie wołać z wątku okna.
    """
    for folder in foldery:
        if jest_url(folder) or Path(folder).is_dir():
            return folder
    return ""


def _z_danych(dane: dict) -> Wydanie:
    """Wydanie ze słownika (moduł wydania, `wydanie.json`); pola spoza
    `Wydanie` pomija, listy z JSON zamienia na krotki."""
    znane = {pole.name for pole in fields(Wydanie)}
    return Wydanie(**{k: tuple(v) if isinstance(v, list) else v
                      for k, v in dane.items() if k in znane})


#: Moduły pakietu `wydania`, które program próbuje wczytać, w tej kolejności.
#: Paczka zawiera tylko moduł swojego wydania — brak pozostałych to norma.
MODULY = ("firmowe", "publiczne")
WYDANIA: Dict[str, Wydanie] = {}
#: kod wydania → nazwa jego modułu (tools/build_exe.py pomija pozostałe).
MODUL: Dict[str, str] = {}


def _importuj(nazwa: str):
    """Moduł wydania albo None, gdy paczka go nie ma.

    Importy są napisane wprost (nie po nazwie z napisu), bo tylko takie
    widzi PyInstaller — budowa dokłada moduł swojego wydania, a pozostałe
    wyklucza (`--exclude-module`). Brak czegoś innego niż sam moduł wydania
    to błąd i leci dalej.
    """
    pakiet = __package__.rpartition(".")[0] + ".wydania"
    try:
        if nazwa == "firmowe":
            from ..wydania import firmowe as modul
        elif nazwa == "publiczne":
            from ..wydania import publiczne as modul
        else:
            raise ValueError(f"Nieznany moduł wydania: {nazwa}")
    except ImportError as exc:
        # `from pakiet import brak` → ImportError z name=pakiet i nazwą w treści.
        brak_modulu = (exc.name == f"{pakiet}.{nazwa}"
                       or (exc.name == pakiet and f"'{nazwa}'" in str(exc)))
        if not brak_modulu:
            raise
        return None
    return modul


def _wczytaj_moduly() -> None:
    for nazwa in MODULY:
        modul = _importuj(nazwa)
        if modul is None:
            continue
        wyd = _z_danych(modul.WYDANIE)
        WYDANIA[wyd.kod] = wyd
        MODUL[wyd.kod] = nazwa
        teksty.EN.update(getattr(modul, "EN", {}))


_wczytaj_moduly()

DOMYSLNE = "firma" if "firma" in WYDANIA else next(iter(WYDANIA), "")

_biezace: Optional[Wydanie] = None


def _z_paczki() -> Optional[Wydanie]:
    """Wydanie zapisane w paczce przez build_exe.py."""
    baza = getattr(sys, "_MEIPASS", None)
    if not baza:
        return None
    try:
        dane = json.loads((Path(baza) / PLIK).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    wzor = WYDANIA.get(str(dane.get("kod", "")).strip().lower())
    if wzor is None:
        return None
    # Brakujące pola (np. ze starszej paczki) bierzemy ze wzoru.
    return _z_danych({**asdict(wzor), **dane})


def biezace() -> Wydanie:
    global _biezace
    if _biezace is None:
        _biezace = _z_paczki()
    if _biezace is None:
        kod = os.environ.get("WHISPER_AUTOMAT_WYDANIE") or DOMYSLNE
        _biezace = WYDANIA.get(kod.strip().lower(), WYDANIA[DOMYSLNE])
        podpis = os.environ.get("WHISPER_AUTOMAT_PODPIS_FIRMY", "").strip()
        if podpis and _biezace.kod == "firma":
            _biezace = replace(_biezace, wydawca=podpis, autor=podpis)
        folder = os.environ.get("WHISPER_AUTOMAT_AKTUALIZACJE", "").strip()
        if folder and _biezace.kod == "firma":
            _biezace = replace(_biezace, aktualizacje_folder=folder)
        kontakt = os.environ.get("WHISPER_AUTOMAT_KONTAKT_FIRMY", "").strip()
        if kontakt and _biezace.kod == "firma":
            _biezace = replace(_biezace, kontakt_email=kontakt)
    return _biezace


def zapisz(wydanie: Wydanie, cel: Path) -> Path:
    """Zapisuje plik wydania do włożenia w paczkę (używa build_exe.py)."""
    cel.parent.mkdir(parents=True, exist_ok=True)
    cel.write_text(
        json.dumps(asdict(wydanie), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return cel
