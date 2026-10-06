"""Silnik transkrypcji.

Domyślnie używa `faster-whisper` (CTranslate2) — na tym samym modelu jest
kilkukrotnie szybszy i zużywa dużo mniej VRAM niż referencyjny `openai-whisper`,
co ma znaczenie na kartach z 4-6 GB pamięci. Gdy `faster-whisper` nie jest
dostępny, moduł spada na `openai-whisper`.
"""

from __future__ import annotations

import glob
import os
import site
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from .media import format_duration

#: Nazwy repozytoriów CTranslate2 dla modeli, które mają własny odpowiednik.
#: faster-whisper rozwiązuje standardowe nazwy sam, ale trzymamy mapowanie
#: jawnie, żeby dało się je nadpisać bez ruszania reszty kodu.
CT2_MODELS = {
    "tiny": "tiny",
    "base": "base",
    "small": "small",
    "medium": "medium",
    "large-v3": "large-v3",
    "large-v3-turbo": "large-v3-turbo",
}


class EngineError(RuntimeError):
    """Błąd ładowania modelu lub transkrypcji."""


class Cancelled(RuntimeError):
    """Użytkownik przerwał transkrypcję."""


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    #: Znaczniki poszczególnych słów — wypełniane tylko, gdy potrzebne
    #: są napisy; pozwalają pociąć długie segmenty na krótkie linijki.
    words: List[Word] = field(default_factory=list)
    #: Numer mówcy (od zera) albo -1, gdy nie rozpoznawano mówców.
    speaker: int = -1


@dataclass
class TranscriptionResult:
    source: Path
    segments: List[Segment] = field(default_factory=list)
    language: str = ""
    language_probability: float = 0.0
    duration: float = 0.0
    model: str = ""
    device: str = ""
    compute_type: str = ""
    engine: str = ""
    elapsed: float = 0.0
    #: Numer mówcy -> nazwa wpisana przez użytkownika.
    speaker_names: dict = field(default_factory=dict)
    #: Odcinki z rozpoznawania mówców — lista `diarization.Odcinek`.
    #: Trzymamy je obok segmentów tekstu, bo to one są pełną listą
    #: rozpoznanych głosów: osoba wtrącająca krótkie zdania może nie
    #: wygrać żadnego segmentu tekstu, a wciąż ma zostać nazwana.
    speaker_turns: list = field(default_factory=list)

    @property
    def has_speakers(self) -> bool:
        return any(s.speaker >= 0 for s in self.segments)

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())

    @property
    def speed_ratio(self) -> float:
        """Ile razy szybciej niż czas rzeczywisty (np. 6.0 = 6x realtime)."""
        return self.duration / self.elapsed if self.elapsed > 0 else 0.0


# ---------------------------------------------------------------------------
# CUDA na Windows — biblioteki cuBLAS/cuDNN leżą w pakietach pip
# ---------------------------------------------------------------------------

_dll_dirs_ready = False


def prepare_cuda_libraries() -> List[str]:
    """Dokłada katalogi z DLL-ami CUDA do ścieżki wyszukiwania bibliotek.

    CTranslate2 na Windows potrzebuje cuBLAS i cuDNN. Trafiają one na dysk
    razem z pakietami `nvidia-*-cu12` albo z torchem, ale Python nie szuka
    tam DLL-i sam — trzeba je zarejestrować przed importem ctranslate2.
    """
    global _dll_dirs_ready
    if _dll_dirs_ready or os.name != "nt":
        _dll_dirs_ready = True
        return []

    bazy = set()
    try:
        bazy.update(site.getsitepackages())
        bazy.add(site.getusersitepackages())
    except AttributeError:
        # W spakowanej aplikacji modułu site bywa okrojony.
        pass

    # Wersja spakowana nie ma site-packages — biblioteki CUDA leżą wtedy
    # obok programu, w tej samej strukturze katalogów nvidia/*/bin.
    try:
        from .config import bundled_root

        bazy.add(str(bundled_root()))
    except Exception:
        pass

    roots: List[str] = []
    for base in bazy:
        if not base or not os.path.isdir(base):
            continue
        roots.extend(glob.glob(os.path.join(base, "nvidia", "*", "bin")))
        roots.extend(glob.glob(os.path.join(base, "nvidia", "*", "lib")))
        torch_lib = os.path.join(base, "torch", "lib")
        if os.path.isdir(torch_lib):
            roots.append(torch_lib)

    added: List[str] = []
    for root in roots:
        try:
            os.add_dll_directory(root)
            added.append(root)
        except (OSError, AttributeError):
            continue

    if added:
        # Część bibliotek szuka zależności przez PATH, nie przez DLL directory.
        os.environ["PATH"] = os.pathsep.join(added) + os.pathsep + os.environ["PATH"]

    _dll_dirs_ready = True
    return added


def available_engines() -> List[str]:
    """Które silniki da się w tym środowisku zaimportować."""
    import importlib.util

    engines = []
    if importlib.util.find_spec("faster_whisper") is not None:
        engines.append("faster-whisper")
    if importlib.util.find_spec("whisper") is not None:
        engines.append("openai-whisper")
    return engines


#: Na co schodzić, gdy karta nie obsługuje żądanej precyzji. Kolejność jest
#: ułożona od najmniejszej straty jakości i szybkości.
_FALLBACK = {
    "float16": ["int8_float16", "float32", "int8_float32", "int8"],
    "int8_float16": ["int8_float32", "int8", "float32"],
    "float32": ["int8_float32", "int8", "float16"],
    "int8_float32": ["int8", "float32"],
    "int8": ["int8_float32", "float32"],
    "bfloat16": ["float16", "float32", "int8_float32"],
}


def resolve_compute_type(device: str, requested: str):
    """Dopasowuje precyzję do tego, co dane urządzenie realnie potrafi.

    Tabela w `probe` opiera się na modelu karty, a to zawsze będzie
    przybliżenie. Tutaj pytamy wprost CTranslate2, więc nietypowy sprzęt
    dostaje poprawne ustawienie zamiast błędu przy ładowaniu modelu.

    Zwraca (precyzja, komunikat) — komunikat jest pusty, gdy nic nie zmieniono.
    """
    try:
        import ctranslate2

        supported = ctranslate2.get_supported_compute_types(device)
    except Exception:
        return requested, ""

    if not supported or requested in supported:
        return requested, ""

    gdzie = "Ta karta" if device == "cuda" else "Procesor"
    for candidate in _FALLBACK.get(requested, []):
        if candidate in supported:
            return candidate, (
                f"{gdzie} nie obsługuje precyzji {requested} — "
                f"przechodzę na {candidate}."
            )

    fallback = sorted(supported)[0]
    return fallback, (
        f"{gdzie} nie obsługuje precyzji {requested} — przechodzę na {fallback}."
    )


def modele_lokalne(models_dir: Optional[Path] = None) -> List[str]:
    """Modele dostępne bez sieci — wgrane ręcznie, pobrane wcześniej lub w paczce."""
    bazy: List[Path] = []
    if models_dir:
        bazy.append(Path(models_dir))
    try:
        from .config import bundled_root

        bazy.append(bundled_root() / "models")
    except Exception:
        pass

    znalezione = set()
    for baza in bazy:
        if not baza.is_dir():
            continue

        # Zwykłe foldery: <baza>/<nazwa>/model.bin
        try:
            for katalog in baza.iterdir():
                if katalog.is_dir() and (katalog / "model.bin").is_file():
                    znalezione.add(katalog.name)
        except OSError:
            pass

        # Pamięć podręczna Hugging Face: models--<org>--faster-whisper-<nazwa>
        for sciezka in baza.glob("models--*/snapshots/*/model.bin"):
            repo = sciezka.parents[2].name.split("--")[-1]
            znalezione.add(repo.replace("faster-whisper-", "", 1))

    return sorted(znalezione)


def cuda_ready() -> bool:
    """Czy CTranslate2 faktycznie widzi działające GPU."""
    prepare_cuda_libraries()
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        pass
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Transkryber
# ---------------------------------------------------------------------------


class Transcriber:
    """Trzyma załadowany model między plikami — przeładowuje tylko przy zmianie."""

    def __init__(self, models_dir: Optional[Path] = None):
        self.models_dir = Path(models_dir) if models_dir else None
        self._model = None
        self._key = None
        self._engine = ""

    # -- ładowanie ---------------------------------------------------------

    def load(
        self,
        model: str,
        device: str = "cuda",
        compute_type: str = "float16",
        engine: str = "auto",
        log: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Ładuje model. Zwraca nazwę faktycznie użytego silnika."""
        log = log or (lambda _m: None)

        engines = available_engines()
        if not engines:
            raise EngineError(
                "Brak silnika transkrypcji. Uruchom setup.bat, żeby zainstalować "
                "faster-whisper."
            )

        if engine == "auto":
            engine = engines[0]
        elif engine not in engines:
            raise EngineError(
                f"Silnik {engine} nie jest zainstalowany (dostępne: "
                f"{', '.join(engines)})."
            )

        if device == "cuda" and not cuda_ready():
            log("GPU niedostępne dla silnika — przechodzę na CPU.")
            device, compute_type = "cpu", "int8"

        if engine == "faster-whisper":
            prepare_cuda_libraries()
            compute_type, note = resolve_compute_type(device, compute_type)
            if note:
                log(note)

        key = (engine, model, device, compute_type)
        if key == self._key and self._model is not None:
            return self._engine

        self._model = None  # zwolnij VRAM przed załadowaniem nowego modelu
        self._key = None

        log(f"Ładuję model {model} ({engine}, {device}, {compute_type})…")
        start = time.time()

        try:
            if engine == "faster-whisper":
                self._model = self._load_faster(model, device, compute_type)
            else:
                self._model = self._load_openai(model, device)
        except Exception as exc:
            from .network import opisz_blad_sieci

            # Gdy nie udało się pobrać modelu, próba na procesorze skończy się
            # dokładnie tak samo — i tylko zaciemni prawdziwą przyczynę.
            if opisz_blad_sieci(exc):
                raise EngineError(_blad_ladowania(model, exc)) from exc

            if device == "cuda":
                log(f"Nie udało się użyć GPU ({_short(exc)}). Próbuję na CPU…")
                device, compute_type = "cpu", "int8"
                if engine == "faster-whisper":
                    compute_type, _note = resolve_compute_type(device, compute_type)
                try:
                    if engine == "faster-whisper":
                        self._model = self._load_faster(model, device, compute_type)
                    else:
                        self._model = self._load_openai(model, device)
                except Exception as cpu_exc:
                    raise EngineError(_blad_ladowania(model, cpu_exc)) from cpu_exc
            else:
                raise EngineError(_blad_ladowania(model, exc)) from exc

        self._key = (engine, model, device, compute_type)
        self._engine = engine
        log(f"Model gotowy w {time.time() - start:.1f} s.")
        return engine

    def _load_faster(self, model: str, device: str, compute_type: str):
        prepare_cuda_libraries()
        from faster_whisper import WhisperModel

        kwargs = {"device": device, "compute_type": compute_type}
        if self.models_dir:
            kwargs["download_root"] = str(self.models_dir)
        if device == "cpu":
            kwargs["cpu_threads"] = min(os.cpu_count() or 4, 16)

        nazwa = CT2_MODELS.get(model, model)

        # Model wgrany ręcznie — zwykły folder z plikami, bez wewnętrznej
        # struktury cache'u Hugging Face. Najprostsza droga, gdy sieć firmowa
        # nie pozwala pobrać modelu z poziomu programu.
        reczny = self._reczny_katalog(model)
        if reczny:
            return WhisperModel(reczny, **kwargs)

        # Potem próba bez sieci. Gdy model już leży w pamięci podręcznej, nie
        # ma powodu pytać serwera — a w sieciach firmowych z inspekcją TLS to
        # właśnie to pytanie potrafi wywrócić całą operację. Przy okazji start
        # jest szybszy, bo odpada odpytywanie Huba o wersję.
        try:
            return WhisperModel(nazwa, local_files_only=True, **kwargs)
        except Exception:
            pass

        return WhisperModel(nazwa, **kwargs)

    def _reczny_katalog(self, model: str) -> Optional[str]:
        """Folder `<katalog modeli>/<nazwa>` z gotowymi plikami modelu.

        Sprawdzamy dwa miejsca: katalog danych użytkownika (model wgrany
        ręcznie albo skopiowany z innego komputera) oraz wnętrze paczki
        (model dołączony do instalatora). Dzięki temu na komputerze bez
        dostępu do internetu program działa od razu po instalacji.
        """
        kandydaci = []
        if self.models_dir:
            kandydaci.append(Path(self.models_dir))
        try:
            from .config import bundled_root

            kandydaci.append(bundled_root() / "models")
        except Exception:
            pass

        for baza in kandydaci:
            katalog = baza / model
            if (katalog / "model.bin").is_file():
                return str(katalog)
        return None

    def _load_openai(self, model: str, device: str):
        import whisper

        # openai-whisper nie zna nazwy "large-v3-turbo" — używa aliasu "turbo".
        name = "turbo" if model == "large-v3-turbo" else model
        kwargs = {"device": device}
        if self.models_dir:
            kwargs["download_root"] = str(self.models_dir)
        return whisper.load_model(name, **kwargs)

    @property
    def current(self):
        """(engine, model, device, compute_type) albo None."""
        return self._key

    def unload(self) -> None:
        self._model = None
        self._key = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    # -- transkrypcja ------------------------------------------------------

    def transcribe(
        self,
        audio_path: Path,
        source_path: Optional[Path] = None,
        duration: float = 0.0,
        language: Optional[str] = "pl",
        initial_prompt: str = "",
        beam_size: int = 5,
        vad_filter: bool = True,
        word_timestamps: bool = False,
        on_progress: Optional[Callable[[float], None]] = None,
        on_segment: Optional[Callable[[Segment], None]] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> TranscriptionResult:
        if self._model is None or self._key is None:
            raise EngineError("Model nie został załadowany — wywołaj load().")

        engine, model_name, device, compute_type = self._key
        result = TranscriptionResult(
            source=Path(source_path or audio_path),
            duration=duration,
            model=model_name,
            device=device,
            compute_type=compute_type,
            engine=engine,
        )

        start = time.time()
        if engine == "faster-whisper":
            self._run_faster(
                result, audio_path, language, initial_prompt, beam_size,
                vad_filter, word_timestamps, on_progress, on_segment, cancel,
            )
        else:
            self._run_openai(
                result, audio_path, language, initial_prompt, device,
                word_timestamps, on_progress, on_segment, cancel,
            )
        result.elapsed = time.time() - start

        if not result.duration and result.segments:
            result.duration = result.segments[-1].end
        if on_progress:
            on_progress(1.0)
        return result

    def _run_faster(
        self, result, audio_path, language, initial_prompt, beam_size,
        vad_filter, word_timestamps, on_progress, on_segment, cancel,
    ):
        segments, info = self._model.transcribe(
            str(audio_path),
            language=language or None,
            initial_prompt=initial_prompt or None,
            beam_size=beam_size,
            vad_filter=vad_filter,
            word_timestamps=word_timestamps,
            condition_on_previous_text=False,
        )
        result.language = info.language or (language or "")
        result.language_probability = float(info.language_probability or 0.0)
        total = result.duration or float(info.duration or 0.0)
        result.duration = total

        for seg in segments:
            if cancel is not None and cancel():
                raise Cancelled("Transkrypcja przerwana przez użytkownika.")
            item = Segment(
                start=float(seg.start),
                end=float(seg.end),
                text=seg.text,
                words=[
                    Word(float(w.start), float(w.end), w.word)
                    for w in (getattr(seg, "words", None) or [])
                ],
            )
            result.segments.append(item)
            if on_segment:
                on_segment(item)
            if on_progress and total > 0:
                on_progress(min(item.end / total, 0.999))

    def _run_openai(
        self, result, audio_path, language, initial_prompt, device,
        word_timestamps, on_progress, on_segment, cancel,
    ):
        # openai-whisper nie udostępnia strumienia segmentów ani anulowania
        # w trakcie — postęp raportujemy dopiero po zakończeniu przebiegu.
        if cancel is not None and cancel():
            raise Cancelled("Transkrypcja przerwana przez użytkownika.")

        raw = self._model.transcribe(
            str(audio_path),
            language=language or None,
            initial_prompt=initial_prompt or None,
            fp16=(device == "cuda"),
            word_timestamps=word_timestamps,
            condition_on_previous_text=False,
        )
        result.language = raw.get("language", language or "")
        result.language_probability = 1.0

        for seg in raw.get("segments", []):
            item = Segment(
                start=float(seg["start"]),
                end=float(seg["end"]),
                text=seg["text"],
                words=[
                    Word(float(w["start"]), float(w["end"]), w["word"])
                    for w in (seg.get("words") or [])
                ],
            )
            result.segments.append(item)
            if on_segment:
                on_segment(item)


def _blad_ladowania(model: str, exc: BaseException) -> str:
    """Komunikat o nieudanym załadowaniu modelu.

    Problemy sieciowe dostają własne wyjaśnienie — surowy błąd SSL nic
    użytkownikowi nie mówi, a przyczyna prawie zawsze jest ta sama.
    """
    from .network import opisz_blad_sieci

    wskazowka = opisz_blad_sieci(exc)
    if wskazowka:
        return f"Nie udało się pobrać modelu {model}.\n\n{wskazowka}"
    return f"Nie udało się załadować modelu {model}: {_short(exc)}"


def _short(exc: Exception, limit: int = 200) -> str:
    text = str(exc).strip().replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


def summarize(result: TranscriptionResult) -> str:
    """Jednolinijkowe podsumowanie do logu."""
    return (
        f"{len(result.segments)} segmentów, "
        f"{format_duration(result.duration)} materiału w "
        f"{format_duration(result.elapsed)} "
        f"({result.speed_ratio:.1f}x realtime, {result.device})"
    )
