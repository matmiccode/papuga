"""Wydania programu — jeden kod, dwie marki.

  * firma    — „Whisper Automat”: wersja firmowa, z modelem w instalatorze,
               aktualizacje z folderu firmowego (udział sieciowy; adres
               tylko w paczce, tak jak podpis autora — patrz niżej).
  * papuga   — „Papuga – transkrypcje offline”: wersja publiczna z GitHuba,
               model pobierany przy pierwszym uruchomieniu, aktualizacje,
               link do wsparcia.

Oba wydania nagrywają spotkania prosto w oknie (Papuga od 2026-10-09 —
sprawdzone w firmie rozwiązania mają przechodzić do wydania publicznego
i z powrotem; pole `nagrywanie` zostaje jako przełącznik).

Wydania różnią się nazwą, plikiem .exe, identyfikatorem instalatora
i katalogiem danych, więc oba programy mogą stać obok siebie na jednym
komputerze i nie nadpisują się nawzajem.

Które wydanie działa, wybiera budowa (`build_exe.py --wydanie`): zapisuje
`wydanie.json` do paczki. Uruchomione z kodu bierze zmienną
WHISPER_AUTOMAT_WYDANIE, a bez niej — wydanie firmowe.

Spakowany program bierze z `wydanie.json` wszystkie pola, nie tylko kod.
Dzięki temu podpis z imieniem i nazwiskiem autora (wydanie firmowe) żyje
tylko w lokalnym pliku `tools/podpis_firmy.local.txt` — poza repozytorium —
i trafia wyłącznie do paczki firmowej, nie do kodu wkładanego w Papugę.
Uruchomione z kodu wydanie firmowe bierze ten podpis ze zmiennej
WHISPER_AUTOMAT_PODPIS_FIRMY (ustawia ją np. tools/zrzut_okna.py), a bez
niej podpisuje się samym MATCODE. Tak samo folder z aktualizacjami:
w paczce z tools/aktualizacje_firmy.local.txt, z kodu ze zmiennej
WHISPER_AUTOMAT_AKTUALIZACJE, a bez niej wydanie firmowe aktualizacji
nie sprawdza.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Optional

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
    #: Papugi (API GitHuba). Puste = nie z GitHuba.
    repo: str = ""
    #: Folder z wydaniami dla sieci firmowej — źródło aktualizacji Whisper
    #: Automat: ścieżka UNC (\\serwer\udział\…) albo adres http(s). Leżą
    #: w nim `najnowsza.json`, instalator i jego `.podpis`
    #: (tools/publikuj_wydanie.py --wydanie firma). Z folderu program
    #: instaluje wyłącznie wydania podpisane kluczem autora. Puste = nie
    #: z folderu. Bez `repo` i bez folderu wydanie aktualizacji nie sprawdza.
    aktualizacje_folder: str = ""
    #: Strona wsparcia autora (np. buycoffee.to). Pusta = brak przycisku.
    wsparcie_url: str = ""
    #: Adres do kontaktu. Pusty = brak w oknie „O programie”.
    kontakt_email: str = ""
    #: Klucz publiczny Ed25519 (szesnastkowo) do sprawdzania podpisu wydań
    #: (core/podpis.py). Z kluczem program instaluje sam tylko wydania
    #: podpisane kluczem prywatnym autora (tools/klucz_wydan.py) — przejęte
    #: konto GitHub nie wystarczy, żeby podsunąć ludziom obcy plik.
    klucz_publiczny: str = ""
    #: Podpis wydawcy: okno, właściwości pliku .exe, instalator. Wydanie
    #: publiczne podpisuje się samą marką, bez imienia i nazwiska autora.
    wydawca: str = "MATCODE"
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

    @property
    def pelna_nazwa(self) -> str:
        """„Papuga – transkrypcje offline” — tytuł okna i wpis w Aplikacjach."""
        return f"{self.nazwa} – {self.haslo}" if self.haslo else self.nazwa

    @property
    def prawa(self) -> str:
        return f"© 2026 {self.wydawca}"

    @property
    def strona_url(self) -> str:
        return f"https://github.com/{self.repo}" if self.repo else ""

    @property
    def zgloszenia_url(self) -> str:
        return f"https://github.com/{self.repo}/issues/new" if self.repo else ""

    @property
    def aktualizacje(self) -> bool:
        return bool(self.repo or self.aktualizacje_folder)


#: Klucz publiczny Ed25519 autora (MATCODE) — jeden dla obu wydań.
#: Prywatny: %USERPROFILE%\.matcode\papuga-klucz-wydan.txt (tools/klucz_wydan.py).
KLUCZ_WYDAN = "5889fe93bba0dc4ee0df9a4be7c5f3ad908cce307f7d664f0046773f335656b4"

WYDANIA = {
    "firma": Wydanie(
        kod="firma",
        nazwa="Whisper Automat",
        plik="WhisperAutomat",
        app_id="WhisperAutomat.App",
        # Ten sam, którego używały wersje 1.0.x — aktualizacja wersji
        # firmowej ma dalej nadpisywać istniejącą instalację.
        inno_id="{7C2F1A64-5D3B-4E82-9A17-6B0E4C9D2F31}",
        # Pełny podpis (wydawca i autor) i folder aktualizacji dokłada
        # build_exe.py — patrz opis modułu.
        klucz_publiczny=KLUCZ_WYDAN,
        haslo="transkrypcje i nagrania spotkań",
        opis=("Nagrywa spotkania i zamienia nagrania w tekst, rozpoznając, "
              "kto mówi. Działa na komputerze, bez internetu."),
        nagrywanie=True,
    ),
    "papuga": Wydanie(
        kod="papuga",
        nazwa="Papuga",
        plik="Papuga",
        app_id="MATCODE.Papuga",
        inno_id="{7CDF7F7F-B74F-4415-970D-792FE960F44A}",
        repo="matmiccode/papuga",
        # Skrzynka kontaktowa do uzupełnienia, gdy powstanie.
        wsparcie_url="https://buycoffee.to/matcode",
        kontakt_email="",
        klucz_publiczny=KLUCZ_WYDAN,
        haslo="transkrypcje offline",
        opis=("Nagrywa spotkania, zamienia nagrania w tekst i rozpoznaje, "
              "kto mówi. Działa na Twoim komputerze, bez internetu i bez chmury."),
        nagrywanie=True,
    ),
}

DOMYSLNE = "firma"

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
    # Pola spoza wzoru (np. ze starszej paczki) pomijamy, brakujące
    # bierzemy ze wzoru.
    znane = {pole.name for pole in fields(Wydanie)}
    return Wydanie(**{**asdict(wzor),
                      **{k: v for k, v in dane.items() if k in znane}})


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
    return _biezace


def zapisz(wydanie: Wydanie, cel: Path) -> Path:
    """Zapisuje plik wydania do włożenia w paczkę (używa build_exe.py)."""
    cel.parent.mkdir(parents=True, exist_ok=True)
    cel.write_text(
        json.dumps(asdict(wydanie), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return cel
