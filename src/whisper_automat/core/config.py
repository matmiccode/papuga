"""Ustawienia użytkownika i ścieżki projektu.

Konfiguracja i modele leżą w katalogu projektu, nie w profilu użytkownika —
dzięki temu cały folder da się skopiować na inny komputer albo pendrive
i uruchomić bez przenoszenia niczego dodatkowego.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .wydanie import biezace

#: Nazwa i tożsamość zależą od wydania (firmowe / Papuga) — patrz wydanie.py.
WYDANIE = biezace()
APP_NAME = WYDANIE.nazwa
#: Nazwa z hasłem po myślniku — tytuł okna i ekran powitalny.
APP_FULL_NAME = WYDANIE.pelna_nazwa

#: Identyfikator dla paska zadań Windows. Bez niego system grupuje okno pod
#: ikoną Pythona zamiast pod ikoną programu.
APP_ID = WYDANIE.app_id


def is_frozen() -> bool:
    """Czy działamy jako spakowany .exe (PyInstaller), czy z kodu źródłowego."""
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    """Katalog z programem: obok setup.bat albo obok pliku .exe."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # .../src/whisper_automat/core/config.py -> .../
    return Path(__file__).resolve().parents[3]


def bundled_root() -> Path:
    """Katalog z zasobami dołączonymi do paczki (ffmpeg, biblioteki CUDA)."""
    spakowane = getattr(sys, "_MEIPASS", None)
    return Path(spakowane) if spakowane else project_root()


def data_root() -> Path:
    """Gdzie trzymamy modele, ustawienia i pliki tymczasowe.

    Wersja uruchamiana z kodu zapisuje wszystko w katalogu projektu — dzięki
    temu cały folder jest przenośny. Wersja zainstalowana leży w Program
    Files, gdzie zwykły użytkownik nie ma prawa zapisu, więc dane idą do
    profilu użytkownika.
    """
    if is_frozen():
        baza = os.environ.get("LOCALAPPDATA") or str(Path.home())
        # Osobny katalog dla każdego wydania — firmowe i publiczne nie
        # dzielą ustawień, modeli ani plików tymczasowych.
        katalog = Path(baza) / WYDANIE.plik
        katalog.mkdir(parents=True, exist_ok=True)
        return katalog
    return project_root()


def asset(name: str) -> Optional[Path]:
    """Ścieżka do pliku z assets/ albo None, gdy go nie ma.

    Najpierw szuka w katalogu wydania (assets/papuga/…), potem we wspólnym
    assets/ — wydanie bez własnych zasobów dostaje firmowe. Obsługuje też
    wersję spakowaną: PyInstaller rozpakowuje zasoby do katalogu
    tymczasowego wskazywanego przez sys._MEIPASS.
    """
    for katalog in (bundled_root() / "assets", project_root() / "assets"):
        for kandydat in (katalog / WYDANIE.kod / name, katalog / name):
            if kandydat.is_file():
                return kandydat
    return None


def models_dir() -> Path:
    path = Path(os.environ.get("WHISPER_AUTOMAT_MODELS", data_root() / "models"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def work_dir() -> Path:
    """Miejsce na tymczasowe pliki WAV wyciągnięte z wideo."""
    path = data_root() / ".cache" / "audio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_output_dir() -> Path:
    if is_frozen():
        # W zainstalowanej wersji wyniki lądują tam, gdzie użytkownik ich szuka.
        path = Path.home() / "Documents" / "Transkrypcje"
    else:
        path = project_root() / "transkrypcje"
    path.mkdir(parents=True, exist_ok=True)
    return path


CONFIG_PATH = data_root() / "config.json"


LANGUAGES = [
    ("pl", "polski"),
    ("en", "angielski"),
    ("de", "niemiecki"),
    ("uk", "ukraiński"),
    ("cs", "czeski"),
    ("sk", "słowacki"),
    ("fr", "francuski"),
    ("es", "hiszpański"),
    ("it", "włoski"),
    ("ru", "rosyjski"),
    ("", "wykryj automatycznie"),
]

LANGUAGE_LABELS = {code: label for code, label in LANGUAGES}


@dataclass
class Settings:
    model: str = ""              # pusty = użyj rekomendacji sprzętowej
    device: str = ""             # pusty = użyj rekomendacji
    compute_type: str = ""       # pusty = użyj rekomendacji
    language: str = "pl"
    initial_prompt: str = ""
    formats: List[str] = field(default_factory=lambda: ["txt", "srt"])
    output_dir: str = ""         # pusty = obok pliku źródłowego
    output_next_to_source: bool = False
    beam_size: int = 5
    vad_filter: bool = True
    #: Rozpoznawanie mówców. Domyślnie wyłączone, bo wydłuża pracę mniej
    #: więcej o długość nagrania — na każdym sprzęcie, także z kartą.
    diarize: bool = False
    #: Ile osób mówi. Zero oznacza „zgadnij", co wypada wyraźnie gorzej.
    speakers: int = 2
    open_output_when_done: bool = True
    keep_extracted_audio: bool = False
    window_geometry: str = ""
    #: Sprawdzanie nowej wersji przy starcie (tylko wydania z repozytorium).
    aktualizacje_auto: bool = True
    #: Kiedy ostatnio pytano GitHuba (znacznik czasu) — żeby nie co start.
    aktualizacje_sprawdzone: float = 0.0
    #: Wersja, którą użytkownik kazał pominąć — pasek o niej już nie wraca.
    pominieta_wersja: str = ""
    #: Nagrywanie spotkań (wydania z Wydanie.nagrywanie): nazwy urządzeń
    #: z listy WASAPI, puste = domyślne systemowe.
    nagranie_mikrofon: str = ""
    nagranie_glosniki: str = ""
    #: Po zatrzymaniu nagrania od razu uruchom transkrypcję.
    nagranie_transkrybuj: bool = True

    # -- trwałość ----------------------------------------------------------

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Settings":
        if not path.is_file():
            return cls()
        try:
            data: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()

        known = {f for f in cls().__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in data.items() if k in known}
        settings = cls(**clean)
        settings.normalize()
        return settings

    def save(self, path: Path = CONFIG_PATH) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(asdict(self), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass  # brak zapisu ustawień nie może wywrócić aplikacji

    def normalize(self) -> None:
        from .writers import FORMATS

        self.formats = [f for f in self.formats if f in FORMATS] or ["txt"]
        self.beam_size = max(1, min(int(self.beam_size or 5), 10))
        self.speakers = max(0, min(int(self.speakers or 0), 20))
        if self.language not in LANGUAGE_LABELS:
            self.language = "pl"

    # -- rozstrzyganie ustawień --------------------------------------------

    def resolve_model(self, recommendation) -> tuple:
        """Zwraca (model, device, compute_type) — wybór użytkownika lub rekomendacja."""
        model = self.model or recommendation.model
        device = self.device or recommendation.device
        compute = self.compute_type or recommendation.compute_type

        # Zmiana urządzenia bez jawnego wyboru precyzji: dobierz sensowną.
        if self.device and not self.compute_type:
            compute = "int8" if device == "cpu" else "float16"
        return model, device, compute

    def resolve_output_dir(self, source: Path) -> Path:
        if self.output_next_to_source:
            return Path(source).parent
        if self.output_dir:
            path = Path(self.output_dir)
            path.mkdir(parents=True, exist_ok=True)
            return path
        return default_output_dir()
