"""Przebieg transkrypcji: plik wejściowy -> audio -> tekst -> pliki wynikowe.

Cała logika siedzi tutaj, żeby GUI i tryb konsolowy robiły dokładnie to samo.
"""

from __future__ import annotations

import shutil
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import diarization, download, media, probe, writers
from .config import Settings, models_dir, work_dir
from .engine import (
    Cancelled,
    EngineError,
    Transcriber,
    TranscriptionResult,
    engine_available,
    modele_lokalne,
    summarize,
)

#: Ekstrakcja audio to zwykle ułamek czasu transkrypcji — stąd podział wagi.
EXTRACT_WEIGHT = 0.12
TRANSCRIBE_WEIGHT = 1.0 - EXTRACT_WEIGHT

#: Gdy włączone jest rozpoznawanie mówców, dochodzi trzeci etap — i to on
#: zajmuje najwięcej czasu. Z pomiaru: transkrypcja na GPU idzie kilkanaście
#: razy szybciej niż nagranie, a diaryzacja około jednej jego długości.
DIAR_EXTRACT_WEIGHT = 0.05
DIAR_TRANSCRIBE_WEIGHT = 0.25
DIAR_SPEAKERS_WEIGHT = 0.70


def model_do_pobrania(model: str) -> bool:
    """Czy model trzeba ściągnąć, zanim silnik go załaduje.

    Dotyczy wersji lekkiej, która nie ma modelu w środku. Wersja offline ma
    go w paczce, więc tu dostaje False i nigdy nie idzie do sieci.
    """
    if not engine_available():
        return False
    if model not in download.REPOZYTORIA:
        return False
    return model not in modele_lokalne(models_dir())


@dataclass
class JobResult:
    source: Path
    ok: bool = False
    outputs: Dict[str, Path] = field(default_factory=dict)
    error: str = ""
    result: Optional[TranscriptionResult] = None


@dataclass
class Callbacks:
    """Punkty zaczepienia dla interfejsu. Każdy jest opcjonalny."""

    log: Callable[[str], None] = lambda _m: None
    file_started: Callable[[int, int, Path], None] = lambda _i, _n, _p: None
    file_progress: Callable[[float], None] = lambda _f: None
    file_finished: Callable[[JobResult], None] = lambda _r: None
    overall_progress: Callable[[float], None] = lambda _f: None
    status: Callable[[str], None] = lambda _s: None
    cancelled: Callable[[], bool] = lambda: False
    #: Czy plik usunięto z kolejki w trakcie pracy — wtedy go pomijamy.
    skipped: Callable[[Path], bool] = lambda _p: False
    #: Pyta użytkownika o imiona rozpoznanych mówców. Dostaje wynik i plik
    #: audio (do odsłuchania próbek), zwraca {numer: imię}. Domyślnie nie
    #: pyta o nic — tryb konsolowy zostawia etykiety MÓWCA 1, MÓWCA 2.
    ask_speakers: Callable[[object, Path], dict] = lambda _r, _a: {}


class Runner:
    """Wykonuje transkrypcję listy plików według ustawień."""

    def __init__(self, settings: Settings, callbacks: Optional[Callbacks] = None):
        self.settings = settings
        self.cb = callbacks or Callbacks()
        self.transcriber = Transcriber(models_dir=models_dir())

    def run(self, files: List[Path]) -> List[JobResult]:
        files = [Path(f) for f in files]
        total = len(files)
        results: List[JobResult] = []

        if not total:
            self.cb.log("Brak plików do przetworzenia.")
            return results

        hw = probe.probe()
        rec = probe.recommend(hw)
        model, device, compute = self.settings.resolve_model(rec)

        if not self.settings.model:
            self.cb.log(f"Model dobrany automatycznie: {model} ({rec.reason})")

        try:
            self._pobierz_jesli_brak(model)
            engine = self.transcriber.load(
                model=model,
                device=device,
                compute_type=compute,
                log=self.cb.log,
            )
        except Cancelled:
            self.cb.log(
                "Pobieranie modelu przerwane. Następna próba ruszy od miejsca, "
                "w którym stanęło."
            )
            return results
        except EngineError as exc:
            # Modelu nie da się pobrać (brak internetu, firewall). Zamiast
            # poddawać się, sprawdź, czy na dysku nie leży już inny — lepszy
            # gorszy model niż żadna transkrypcja.
            zapasowy = self._model_zapasowy(model)
            if zapasowy is None:
                self.cb.log(f"BŁĄD: {exc}")
                for f in files:
                    results.append(JobResult(source=f, error=str(exc)))
                return results

            self.cb.log(
                f"Modelu {model} nie udało się pobrać. Używam modelu "
                f"{zapasowy}, który jest już na dysku."
            )
            try:
                engine = self.transcriber.load(
                    model=zapasowy,
                    device=device,
                    compute_type=compute,
                    log=self.cb.log,
                )
            except EngineError as zapasowy_exc:
                self.cb.log(f"BŁĄD: {zapasowy_exc}")
                for f in files:
                    results.append(JobResult(source=f, error=str(zapasowy_exc)))
                return results

        self.cb.log(f"Silnik: {engine}")

        for index, source in enumerate(files):
            if self.cb.cancelled():
                self.cb.log("Przerwano — pozostałe pliki pominięte.")
                break
            if self.cb.skipped(source):
                self.cb.log(f"Pominięto {source.name} — usunięty z kolejki.")
                self.cb.overall_progress((index + 1) / total)
                continue

            self.cb.file_started(index, total, source)
            self.cb.overall_progress(index / total)
            job = self._run_one(source, index, total)
            results.append(job)
            self.cb.file_finished(job)
            self.cb.overall_progress((index + 1) / total)

        return results

    def _pobierz_jesli_brak(self, model: str) -> None:
        """Ściąga model z postępem na pasku, gdy nie ma go na dysku.

        Niepowodzenie zgłasza jako EngineError — wtedy `run` sięga po
        model zapasowy, tak samo jak przy nieudanym ładowaniu.
        """
        if not model_do_pobrania(model):
            return

        def postep(pobrane: int, wszystkie: int, predkosc: float) -> None:
            self.cb.status(
                download.opis_postepu(f"model {model}", pobrane, wszystkie, predkosc)
            )
            if wszystkie:
                self.cb.file_progress(pobrane / wszystkie)

        self.cb.status(f"Pobieram model {model}…")
        try:
            download.pobierz_model(
                model, models_dir(), on_progress=postep,
                log=self.cb.log, cancel=self.cb.cancelled,
            )
        except download.DownloadError as exc:
            raise EngineError(str(exc)) from exc
        self.cb.file_progress(0.0)

    @property
    def _waga_audio(self) -> float:
        return DIAR_EXTRACT_WEIGHT if self.settings.diarize else EXTRACT_WEIGHT

    @property
    def _waga_tekstu(self) -> float:
        return DIAR_TRANSCRIBE_WEIGHT if self.settings.diarize else TRANSCRIBE_WEIGHT

    def _rozpoznaj_mowcow(self, audio: Path, result, index: int, total: int) -> None:
        """Dokłada wynikowi informację, kto mówi w którym momencie.

        Niepowodzenie nie przekreśla całej pracy — transkrypcja jest już
        gotowa i zostanie zapisana, tyle że bez podziału na mówców.
        """
        zadane = self.settings.speakers
        ile = f"{zadane} os." if zadane else "liczba nieznana"
        self.cb.status(f"[{index + 1}/{total}] Rozpoznaję mówców ({ile})…")
        self.cb.log(
            "Rozpoznawanie mówców: "
            + (f"szukam {zadane} różnych głosów." if zadane
               else "liczba osób nie podana — algorytm zgaduje.")
        )
        baza = self._waga_audio + self._waga_tekstu
        try:
            odcinki = diarization.rozpoznaj_mowcow(
                audio,
                ilu_mowcow=self.settings.speakers,
                on_progress=lambda f: self.cb.file_progress(
                    baza + f * DIAR_SPEAKERS_WEIGHT
                ),
                log=self.cb.log,
            )
        except diarization.DiarizationError as exc:
            self.cb.log(f"Nie rozpoznano mówców: {exc}")
            self.cb.log("Transkrypcja zostanie zapisana bez podziału na osoby.")
            return

        if not odcinki:
            self.cb.log("Nie wykryto wyraźnie oddzielonych głosów.")
            return

        przed = len(result.segments)
        result.segments = diarization.przypisz(result.segments, odcinki)
        if len(result.segments) > przed:
            self.cb.log(
                f"Segmenty podzielone tam, gdzie zmieniał się mówca: "
                f"{przed} -> {len(result.segments)}."
            )
        # Odcinki jadą razem z wynikiem — okno „Kto jest kim?" bierze z nich
        # próbkę głosu, bo najdłuższa wypowiedź jest lepsza niż pierwszy
        # segment tekstu, jaki się trafi.
        result.speaker_turns = odcinki

        rozpoznani = {o.mowca for o in odcinki}
        z_tekstem = {s.speaker for s in result.segments if s.speaker >= 0}
        self.cb.log(
            f"Rozpoznano {len(rozpoznani)} głos(ów) w {len(odcinki)} odcinkach."
        )
        if zadane and len(rozpoznani) != zadane:
            self.cb.log(
                f"UWAGA: podano {zadane} osób, a rozdzieliły się "
                f"{len(rozpoznani)}. Jeśli to nie zgadza się z nagraniem, "
                f"popraw liczbę osób i powtórz."
            )
        if len(z_tekstem) < len(rozpoznani):
            self.cb.log(
                f"Tekst trafił do {len(z_tekstem)} osób — pozostałe mówiły "
                f"zbyt krótko, żeby wygrać jakikolwiek fragment."
            )

        # Imiona pytamy zanim zapiszemy pliki — inaczej trzeba by je
        # nadpisywać, a numeracja mówców i tak jest inna w każdym nagraniu.
        try:
            nazwy = self.cb.ask_speakers(result, audio) or {}
        except Exception as exc:
            self.cb.log(f"Nie udało się zapytać o imiona: {exc}")
            nazwy = {}
        if nazwy:
            result.speaker_names = nazwy
            self.cb.log("Podpisano mówców: " + ", ".join(sorted(nazwy.values())))

    def _model_zapasowy(self, odrzucony: str) -> Optional[str]:
        """Najlepszy model leżący już na dysku, inny niż ten, który zawiódł."""
        dostepne = set(modele_lokalne(models_dir())) - {odrzucony}
        for nazwa in probe.AUTO_PREFERENCE:
            if nazwa in dostepne:
                return nazwa
        # Poza listą automatycznego wyboru jest jeszcze large-v3.
        for nazwa in sorted(dostepne):
            if nazwa in probe.MODEL_BY_NAME:
                return nazwa
        return None

    # ------------------------------------------------------------------

    def _run_one(self, source: Path, index: int, total: int) -> JobResult:
        job = JobResult(source=source)
        temp_audio: Optional[Path] = None
        is_temp = False

        try:
            self.cb.status(f"[{index + 1}/{total}] Analizuję {source.name}…")
            info = media.probe_media(source)

            if info.is_video:
                self.cb.log(
                    f"{source.name}: wideo, {media.format_duration(info.duration)} "
                    f"— wyciągam ścieżkę audio."
                )
                self.cb.status(f"[{index + 1}/{total}] Wyciągam audio…")
            else:
                self.cb.log(
                    f"{source.name}: audio {info.audio_codec}, "
                    f"{media.format_duration(info.duration)}"
                )

            audio, info, is_temp = media.prepare_audio(
                source,
                workdir=work_dir(),
                on_progress=lambda f: self.cb.file_progress(f * self._waga_audio),
                cancel=self.cb.cancelled,
            )
            temp_audio = audio if is_temp else None

            if self.cb.cancelled():
                raise Cancelled("Przerwano przed transkrypcją.")

            self.cb.status(f"[{index + 1}/{total}] Transkrybuję {source.name}…")
            # Znaczniki słów kosztują trochę czasu, więc włączamy je tylko
            # wtedy, gdy naprawdę są potrzebne: przy napisach oraz przy
            # rozpoznawaniu mówców, gdzie bez nich nie da się podzielić
            # długiego segmentu tam, gdzie zmienia się osoba.
            needs_words = self.settings.diarize or any(
                f in ("srt", "vtt") for f in self.settings.formats
            )
            result = self.transcriber.transcribe(
                audio_path=audio,
                source_path=source,
                duration=info.duration,
                language=self.settings.language or None,
                initial_prompt=self.settings.initial_prompt,
                beam_size=self.settings.beam_size,
                vad_filter=self.settings.vad_filter,
                word_timestamps=needs_words,
                on_progress=lambda f: self.cb.file_progress(
                    self._waga_audio + f * self._waga_tekstu
                ),
                cancel=self.cb.cancelled,
            )

            if self.settings.diarize:
                self._rozpoznaj_mowcow(audio, result, index, total)

            out_dir = self.settings.resolve_output_dir(source)
            job.outputs = writers.write_all(
                result, out_dir, source.stem, self.settings.formats
            )
            job.result = result
            job.ok = True

            self.cb.file_progress(1.0)
            self.cb.log(f"{source.name}: gotowe — {summarize(result)}")
            for fmt, path in job.outputs.items():
                self.cb.log(f"    -> {path}")

        except Cancelled as exc:
            job.error = str(exc)
            self.cb.log(f"{source.name}: przerwano.")
        except (media.MediaError, EngineError) as exc:
            job.error = str(exc)
            self.cb.log(f"BŁĄD ({source.name}): {exc}")
        except Exception as exc:  # nieprzewidziane — nie wywracaj całej kolejki
            job.error = f"{type(exc).__name__}: {exc}"
            self.cb.log(f"BŁĄD ({source.name}): {job.error}")
            self.cb.log(traceback.format_exc(limit=3))
        finally:
            if temp_audio and not self.settings.keep_extracted_audio:
                _cleanup(temp_audio)

        return job


def _cleanup(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        parent = path.parent
        # Usuń katalog tymczasowy tylko wtedy, gdy sami go zrobiliśmy i jest pusty.
        if parent.name.startswith("whisper_automat_") and not any(parent.iterdir()):
            shutil.rmtree(parent, ignore_errors=True)
    except OSError:
        pass


def clear_cache() -> int:
    """Czyści zostawione pliki WAV. Zwraca liczbę usuniętych plików."""
    removed = 0
    cache = work_dir()
    for item in cache.glob("*.wav"):
        try:
            item.unlink()
            removed += 1
        except OSError:
            pass
    return removed
