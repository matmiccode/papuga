"""Diagnostyka środowiska: co jest, czego brakuje i jak to naprawić."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import probe
from .config import APP_NAME, WYDANIE, models_dir
from ..teksty import t

OK = "OK"
WARN = "UWAGA"
FAIL = "BRAK"

#: Minimalna wersja Pythona, na której działa faster-whisper 1.x.
MIN_PYTHON = (3, 9)


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    fix: str = ""

    @property
    def failed(self) -> bool:
        return self.status == FAIL


@dataclass
class Diagnosis:
    checks: List[Check] = field(default_factory=list)
    hardware: Optional[probe.Hardware] = None
    recommendation: Optional[probe.Recommendation] = None

    @property
    def ready(self) -> bool:
        return not any(c.failed for c in self.checks)

    @property
    def problems(self) -> List[Check]:
        return [c for c in self.checks if c.status != OK]


def _version_of(module: str) -> str:
    try:
        from importlib.metadata import version

        return version(module)
    except Exception:
        return "?"


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def diagnose() -> Diagnosis:
    hw = probe.probe()
    rec = probe.recommend(hw)
    checks: List[Check] = []

    # --- Python ---
    py = sys.version_info
    if py[:2] >= MIN_PYTHON:
        checks.append(
            Check(
                "Python",
                OK,
                f"{py.major}.{py.minor}.{py.micro} ({sys.executable})",
            )
        )
    else:
        checks.append(
            Check(
                "Python",
                FAIL,
                t("{wersja} to za stara wersja").format(wersja=f"{py.major}.{py.minor}"),
                t("Zainstaluj Pythona {wersja} lub nowszego.").format(
                    wersja=f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}"
                ),
            )
        )

    # --- ffmpeg ---
    if hw.ffmpeg and hw.ffprobe:
        checks.append(Check("ffmpeg", OK, hw.ffmpeg))
    else:
        missing = "ffmpeg" if not hw.ffmpeg else "ffprobe"
        checks.append(
            Check(
                "ffmpeg",
                FAIL,
                t("nie znaleziono {program} w PATH").format(program=missing),
                t("Uruchom setup.bat albo: winget install Gyan.FFmpeg"),
            )
        )

    # --- silnik ---
    if _installed("faster_whisper"):
        checks.append(
            Check(
                "faster-whisper",
                OK,
                t("wersja {wersja}").format(wersja=_version_of("faster-whisper")),
            )
        )
    else:
        checks.append(
            Check(
                "faster-whisper",
                FAIL,
                t("nie zainstalowano silnika transkrypcji"),
                t("Uruchom setup.bat."),
            )
        )

    if _installed("ctranslate2"):
        checks.append(
            Check(
                "CTranslate2",
                OK,
                t("wersja {wersja}").format(wersja=_version_of("ctranslate2")),
            )
        )

    # --- GPU ---
    if hw.has_cuda_gpu:
        gpu = hw.gpu
        detail = t("{karta}, {vram:g} GB VRAM, sterownik {sterownik}").format(
            karta=gpu.name, vram=gpu.vram_gb, sterownik=gpu.driver
        )
        if cuda_usable():
            checks.append(Check("GPU / CUDA", OK, detail))
        else:
            checks.append(
                Check(
                    "GPU / CUDA",
                    WARN,
                    t(
                        "{opis} — wykryta, ale silnik jej nie widzi "
                        "(brak bibliotek cuBLAS/cuDNN)"
                    ).format(opis=detail),
                    t(
                        "Uruchom setup.bat — doinstaluje nvidia-cublas-cu12 "
                        "i nvidia-cudnn-cu12. Bez tego transkrypcja pójdzie na CPU."
                    ),
                )
            )
    elif hw.nvidia_without_driver:
        checks.append(
            Check(
                "GPU / CUDA",
                WARN,
                t("{karta} — karta jest, ale nie ma sterownika").format(
                    karta=hw.nvidia_without_driver
                ),
                t(
                    "Zainstaluj sterownik ze strony nvidia.com/drivers (albo przez "
                    "GeForce Experience), potem uruchom setup.bat ponownie. "
                    "Transkrypcja przyspieszy kilkukrotnie."
                ),
            )
        )
    else:
        checks.append(
            Check(
                "GPU / CUDA",
                WARN,
                t("brak karty NVIDIA — transkrypcja na CPU (kilka razy wolniej)"),
            )
        )

    # --- drag & drop ---
    if _installed("tkinterdnd2"):
        checks.append(Check("Drag & drop", OK, t("tkinterdnd2 zainstalowane")))
    else:
        checks.append(
            Check(
                "Drag & drop",
                WARN,
                t("brak tkinterdnd2 — pliki trzeba wybierać przyciskiem"),
                "pip install tkinterdnd2",
            )
        )

    # --- rozpoznawanie mówców ---
    from . import diarization

    if not diarization.dostepne():
        checks.append(
            Check(
                t("Rozpoznawanie mówców"),
                WARN,
                t("brak biblioteki sherpa-onnx — funkcja będzie niedostępna"),
                t("Uruchom setup.bat albo: pip install sherpa-onnx"),
            )
        )
    elif diarization.modele_gotowe():
        checks.append(
            Check(
                t("Rozpoznawanie mówców"),
                OK,
                t("sherpa-onnx {wersja}, modele na miejscu").format(
                    wersja=_version_of("sherpa-onnx")
                ),
            )
        )
    else:
        checks.append(
            Check(
                t("Rozpoznawanie mówców"),
                WARN,
                t("sherpa-onnx {wersja}, brak modeli głosów").format(
                    wersja=_version_of("sherpa-onnx")
                ),
                t(
                    "Dwa modele (łącznie 44 MB) pobiorą się przy pierwszym użyciu "
                    "funkcji — potrzebny dostęp do github.com."
                ),
            )
        )

    # --- nagrywanie spotkań (tylko wydania, które je mają) ---
    if WYDANIE.nagrywanie:
        from . import nagrywanie

        if not nagrywanie.dostepne():
            checks.append(
                Check(
                    t("Nagrywanie spotkań"),
                    FAIL,
                    t("brak biblioteki PyAudioWPatch — przycisk nagrywania nie zadziała"),
                    t("Uruchom setup.bat albo: pip install PyAudioWPatch"),
                )
            )
        else:
            try:
                wejscia, wyjscia = nagrywanie.lista_urzadzen()
                mik = nagrywanie._znajdz(wejscia, "")
                wy = nagrywanie._znajdz(wyjscia, "")
                opis = t(
                    "PyAudioWPatch {wersja}; mikrofon: {mikrofon}; "
                    "dźwięk systemowy z: {wyjscie}"
                ).format(
                    wersja=_version_of("PyAudioWPatch"),
                    mikrofon=mik.nazwa if mik else t("brak"),
                    wyjscie=wy.nazwa if wy else t("brak"),
                ) + ("" if wy is None or wy.loopback_index is not None
                     else t(" (bez loopbacku)"))
                stan = OK if mik is not None or (wy and wy.loopback_index is not None) else WARN
                checks.append(Check(t("Nagrywanie spotkań"), stan, opis,
                                    "" if stan == OK else t("Podłącz mikrofon albo słuchawki.")))
            except Exception as exc:
                checks.append(
                    Check(
                        t("Nagrywanie spotkań"),
                        WARN,
                        t("nie udało się odczytać urządzeń WASAPI: {blad}").format(blad=exc),
                        t("Sprawdź w Ustawieniach Windows, czy urządzenia dźwięku działają."),
                    )
                )

    # --- modele ---
    from .download import rozmiar_opis

    cached = _cached_models()
    if cached:
        gdzie = katalogi_modeli()
        opis = ", ".join(sorted(cached))
        if gdzie:
            opis += t(" (w {katalog})").format(katalog=gdzie[0])
        checks.append(Check(t("Pobrane modele"), OK, opis))
    else:
        checks.append(
            Check(
                t("Pobrane modele"),
                WARN,
                t("brak modeli w pamięci podręcznej"),
                t(
                    "Model {model} (ok. {rozmiar}) program zaproponuje pobrać "
                    "przy uruchomieniu albo ściągnie go przed pierwszą transkrypcją."
                ).format(model=rec.model, rozmiar=rozmiar_opis(rec.model)),
            )
        )

        # Skoro modelu nie ma, pobranie go jest warunkiem działania —
        # lepiej sprawdzić łączność teraz niż w połowie pierwszego pliku.
        from .network import sprawdz_hub

        osiagalne, opis = sprawdz_hub()
        if osiagalne:
            checks.append(Check(t("Pobieranie modelu"), OK, opis))
        else:
            checks.append(
                Check(
                    t("Pobieranie modelu"),
                    FAIL,
                    opis,
                    t(
                        "Bez tego model się nie pobierze. W sieci firmowej zwykle "
                        "wystarczy dostęp do huggingface.co; alternatywnie skopiuj "
                        "folder %LOCALAPPDATA%\\{wydanie}\\models z komputera, "
                        "na którym program już działa, albo zainstaluj wersję "
                        "offline (z modelem w instalatorze)."
                    ).format(wydanie=WYDANIE.plik),
                )
            )

    # --- dysk ---
    if hw.disk_free_gb < 5:
        checks.append(
            Check(
                t("Miejsce na dysku"),
                FAIL,
                t("tylko {gb:g} GB wolnego").format(gb=hw.disk_free_gb),
                t(
                    "Zwolnij co najmniej 5 GB — modele i pliki tymczasowe "
                    "potrzebują miejsca."
                ),
            )
        )
    else:
        checks.append(
            Check(
                t("Miejsce na dysku"),
                OK,
                t("{gb:g} GB wolnego").format(gb=hw.disk_free_gb),
            )
        )

    return Diagnosis(checks=checks, hardware=hw, recommendation=rec)


def cuda_usable() -> bool:
    """Czy silnik faktycznie potrafi policzyć cokolwiek na GPU."""
    try:
        from .engine import cuda_ready

        return cuda_ready()
    except Exception:
        return False


def _bez_prefiksu(nazwa: str) -> str:
    return nazwa.replace("faster-whisper-", "", 1)


def katalogi_modeli() -> List[Path]:
    """Niepuste katalogi, w których program szuka modeli Whispera.

    W wersji uruchamianej z kodu jest to jeden katalog; w zainstalowanej
    dwa — dane użytkownika i wnętrze paczki.
    """
    from .config import bundled_root

    znalezione: List[Path] = []
    for kandydat in (models_dir(), bundled_root() / "models"):
        try:
            sciezka = kandydat.resolve()
            if sciezka.is_dir() and sciezka not in znalezione and any(
                sciezka.iterdir()
            ):
                znalezione.append(sciezka)
        except OSError:
            continue
    return znalezione


def _cached_models() -> List[str]:
    """Modele dostępne bez sieci: katalog danych, wnętrze paczki, cache HF.

    Wersja zainstalowana ma model w środku paczki (`_internal/models`),
    a nie w katalogu danych użytkownika. Pytanie tylko o ten drugi dawało
    „brak modeli w pamięci podręcznej” przy działającej transkrypcji —
    i ciągnęło za sobą fałszywy błąd o pobieraniu modelu.
    """
    from .engine import modele_lokalne

    found = set(modele_lokalne(models_dir()))

    hf = Path.home() / ".cache" / "huggingface" / "hub"
    if hf.is_dir():
        for item in hf.glob("models--*whisper*"):
            found.add(_bez_prefiksu(item.name.split("--")[-1]))

    return sorted(n for n in found if n)


def format_diagnosis(diag: Diagnosis) -> str:
    """Pełny raport tekstowy — GUI i konsola pokazują to samo."""
    from .. import __version__

    width = max(len(c.name) for c in diag.checks) if diag.checks else 12
    lines = [
        "=" * 68,
        f"  {APP_NAME.upper()} {__version__} — " + t("DIAGNOSTYKA ŚRODOWISKA"),
        "=" * 68,
        f"  {WYDANIE.wydawca}",
        "=" * 68,
        "",
    ]

    for check in diag.checks:
        mark = {OK: "[+]", WARN: "[!]", FAIL: "[X]"}.get(check.status, "[?]")
        lines.append(f" {mark} {check.name.ljust(width)} : {check.detail}")
        if check.fix:
            lines.append(f"     {' ' * width}   -> {check.fix}")

    if diag.hardware and diag.recommendation:
        lines += ["", "-" * 68, ""]
        lines.append(probe.format_report(diag.hardware, diag.recommendation))

    lines += ["", "=" * 68]
    if diag.ready:
        lines.append("  " + t("Środowisko gotowe do pracy."))
    else:
        lines.append("  " + t("Środowisko NIE jest kompletne — zobacz pozycje [X] powyżej."))
    lines.append("=" * 68)
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_diagnosis(diagnose()))
