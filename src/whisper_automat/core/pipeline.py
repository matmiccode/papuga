"""Przebieg transkrypcji: plik wejściowy -> audio -> tekst -> pliki wynikowe.

Cała logika siedzi tutaj, żeby GUI i tryb konsolowy robiły dokładnie to samo.
"""

from __future__ import annotations

import shutil
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import diarization, download, glosy, media, nagrywanie, probe, writers
from .config import Settings, imie_uzytkownika, models_dir, work_dir
from .engine import (
    Cancelled,
    EngineError,
    Transcriber,
    TranscriptionResult,
    engine_available,
    modele_lokalne,
    summarize,
)
from ..teksty import t

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
            self.cb.log(t("Brak plików do przetworzenia."))
            return results

        hw = probe.probe()
        rec = probe.recommend(hw)
        model, device, compute = self.settings.resolve_model(rec)

        if not self.settings.model:
            self.cb.log(
                t("Model dobrany automatycznie: {model} ({powod})").format(
                    model=model, powod=rec.reason
                )
            )

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
                t(
                    "Pobieranie modelu przerwane. Następna próba ruszy od miejsca, "
                    "w którym stanęło."
                )
            )
            return results
        except EngineError as exc:
            # Modelu nie da się pobrać (brak internetu, firewall). Zamiast
            # poddawać się, sprawdź, czy na dysku nie leży już inny — lepszy
            # gorszy model niż żadna transkrypcja.
            zapasowy = self._model_zapasowy(model)
            if zapasowy is None:
                self.cb.log(t("BŁĄD: {blad}").format(blad=exc))
                for f in files:
                    results.append(JobResult(source=f, error=str(exc)))
                return results

            self.cb.log(
                t(
                    "Modelu {model} nie udało się pobrać. Używam modelu "
                    "{zapasowy}, który jest już na dysku."
                ).format(model=model, zapasowy=zapasowy)
            )
            try:
                engine = self.transcriber.load(
                    model=zapasowy,
                    device=device,
                    compute_type=compute,
                    log=self.cb.log,
                )
            except EngineError as zapasowy_exc:
                self.cb.log(t("BŁĄD: {blad}").format(blad=zapasowy_exc))
                for f in files:
                    results.append(JobResult(source=f, error=str(zapasowy_exc)))
                return results

        self.cb.log(t("Silnik: {silnik}").format(silnik=engine))

        for index, source in enumerate(files):
            if self.cb.cancelled():
                self.cb.log(t("Przerwano — pozostałe pliki pominięte."))
                break
            if self.cb.skipped(source):
                self.cb.log(
                    t("Pominięto {plik} — usunięty z kolejki.").format(plik=source.name)
                )
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

        self.cb.status(t("Pobieram model {model}…").format(model=model))
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

    def _wlasne_nagranie(self, info) -> bool:
        """Czy plik nagrał ten program (stereo z tagiem — nagrywanie.py)."""
        return (getattr(info, "channels", 0) == 2
                and diarization.tag_nagrania(getattr(info, "tags", None)) is not None)

    def _rozpoznaj_mowcow(self, audio: Path, result, index: int, total: int,
                          source: Optional[Path] = None, info=None,
                          tymczasowe: Optional[list] = None) -> None:
        """Dokłada wynikowi informację, kto mówi w którym momencie.

        Własne nagranie spotkania (stereo z tagiem): diaryzacja idzie tylko
        po prawym kanale (rozmówcy), a użytkownik dostaje numer `result.ja`
        z porównania kanałów — bez zgadywania. Pole „Ile osób” liczy
        wszystkich razem z użytkownikiem.

        Niepowodzenie nie przekreśla całej pracy — transkrypcja jest już
        gotowa i zostanie zapisana, tyle że bez podziału na mówców.
        """
        zadane = self.settings.speakers
        wlasne = source is not None and info is not None and self._wlasne_nagranie(info)
        stereo: Optional[Path] = None
        if wlasne:
            self.cb.status(t("[{i}/{n}] Rozdzielam kanały nagrania…").format(
                i=index + 1, n=total))
            try:
                prawy = media.extract_audio(
                    source, dst=work_dir() / f"{source.stem}.prawy.16k.wav",
                    kanal="prawy", cancel=self.cb.cancelled)
                stereo = media.extract_audio(
                    source, dst=work_dir() / f"{source.stem}.stereo.16k.wav",
                    kanal="stereo", cancel=self.cb.cancelled)
            except media.MediaError as exc:
                self.cb.log(t("Nie udało się rozdzielić kanałów nagrania: {blad}").format(blad=exc))
                self.cb.log(t("Transkrypcja zostanie zapisana bez podziału na osoby."))
                return
            if tymczasowe is not None:
                tymczasowe.extend([prawy, stereo])
            audio = prawy
            # Jedna osoba to użytkownik; reszta jest w torze rozmówców.
            ilu = max(zadane - 1, 1) if zadane else 0
            self.cb.log(t("Własne nagranie: lewy kanał to Ty, rozmówców szukam w prawym "
                          "({opis}).").format(
                opis=t("Ty + {n} rozm.").format(n=ilu) if ilu
                else t("liczba osób nie podana — algorytm zgaduje.")))
        else:
            ilu = zadane
            self.cb.log(
                t("Rozpoznawanie mówców: {opis}").format(
                    opis=t("szukam {n} różnych głosów.").format(n=zadane) if zadane
                    else t("liczba osób nie podana — algorytm zgaduje.")
                )
            )
        ile = t("{n} os.").format(n=zadane) if zadane else t("liczba nieznana")
        self.cb.status(
            t("[{i}/{n}] Rozpoznaję mówców ({ile})…").format(
                i=index + 1, n=total, ile=ile
            )
        )
        baza = self._waga_audio + self._waga_tekstu
        try:
            odcinki = diarization.rozpoznaj_mowcow(
                audio,
                ilu_mowcow=ilu,
                on_progress=lambda f: self.cb.file_progress(
                    baza + f * DIAR_SPEAKERS_WEIGHT
                ),
                log=self.cb.log,
            )
        except diarization.DiarizationError as exc:
            self.cb.log(t("Nie rozpoznano mówców: {blad}").format(blad=exc))
            self.cb.log(t("Transkrypcja zostanie zapisana bez podziału na osoby."))
            return

        if wlasne and stereo is not None:
            ja = len({o.mowca for o in odcinki})
            try:
                odcinki_ja = diarization.odcinki_wlasne(stereo, result.segments, ja)
            except diarization.DiarizationError as exc:
                self.cb.log(t("Nie udało się rozpoznać Twojego toru: {blad}").format(blad=exc))
                odcinki_ja = []
            if not odcinki:
                self.cb.log(t("Nie wykryto rozmówców w torze spotkania — podpisuję tylko Ciebie."))
            if not odcinki_ja:
                self.cb.log(t("W torze mikrofonu nie wykryto Twojej mowy."))
            result.ja = ja
            result.speaker_names[ja] = (self.settings.nagranie_imie.strip()
                                        or imie_uzytkownika() or t("Ja"))
            # Odcinki „Ja” pierwsze: są dokładnie po słowach i mają wygrywać
            # z przybliżonymi odcinkami diaryzacji przy równym pokryciu.
            odcinki = odcinki_ja + odcinki

        if not odcinki:
            self.cb.log(t("Nie wykryto wyraźnie oddzielonych głosów."))
            return

        przed = len(result.segments)
        result.segments = diarization.przypisz(result.segments, odcinki)
        if len(result.segments) > przed:
            self.cb.log(
                t(
                    "Segmenty podzielone tam, gdzie zmieniał się mówca: "
                    "{przed} -> {po}."
                ).format(przed=przed, po=len(result.segments))
            )
        # Odcinki jadą razem z wynikiem — okno „Kto jest kim?" bierze z nich
        # próbkę głosu, bo najdłuższa wypowiedź jest lepsza niż pierwszy
        # segment tekstu, jaki się trafi.
        result.speaker_turns = odcinki

        rozpoznani = {o.mowca for o in odcinki}
        z_tekstem = {s.speaker for s in result.segments if s.speaker >= 0}
        self.cb.log(
            t("Rozpoznano {glosy} głos(ów) w {odcinki} odcinkach.").format(
                glosy=len(rozpoznani), odcinki=len(odcinki)
            )
        )
        if wlasne and result.ja not in z_tekstem:
            # Użytkownik bez ani jednego słowa w tekście nie ma czego podpisać.
            result.speaker_names.pop(result.ja, None)
            result.ja = -1
        if zadane and len(rozpoznani) != zadane:
            self.cb.log(
                t(
                    "UWAGA: podano {podano} osób, a rozdzieliły się "
                    "{rozpoznano}. Jeśli to nie zgadza się z nagraniem, "
                    "popraw liczbę osób i powtórz."
                ).format(podano=zadane, rozpoznano=len(rozpoznani))
            )
        if len(z_tekstem) < len(rozpoznani):
            self.cb.log(
                t(
                    "Tekst trafił do {n} osób — pozostałe mówiły "
                    "zbyt krótko, żeby wygrać jakikolwiek fragment."
                ).format(n=len(z_tekstem))
            )

        # Baza głosów (wydanie firmowe): kto wpisał swój głos, dostaje imię
        # jako podpowiedź. Nic nie jest zapisywane — tylko porównanie.
        if glosy.dostepna() and z_tekstem - {result.ja}:
            self.cb.status(t("[{i}/{n}] Porównuję głosy z bazą…").format(i=index + 1, n=total))
            try:
                result.speaker_proposals = glosy.rozpoznaj_mowcow(
                    audio, [o for o in odcinki if o.mowca != result.ja and o.mowca in z_tekstem],
                    log=self.cb.log)
            except glosy.BladBazy as exc:
                self.cb.log(t("Baza głosów: {blad}").format(blad=exc))
            except Exception as exc:  # noqa: BLE001 — podpowiedź nie może wywrócić pracy
                self.cb.log(t("Baza głosów: {blad}").format(blad=f"{type(exc).__name__}: {exc}"))

        # Imiona pytamy zanim zapiszemy pliki — inaczej trzeba by je
        # nadpisywać, a numeracja mówców i tak jest inna w każdym nagraniu.
        try:
            nazwy = self.cb.ask_speakers(result, audio) or {}
        except Exception as exc:
            self.cb.log(t("Nie udało się zapytać o imiona: {blad}").format(blad=exc))
            nazwy = {}
        if not nazwy and result.speaker_proposals:
            # Bez okna (konsola) albo „Pomiń” — rozpoznani z bazy i tak są podpisani.
            nazwy = {numer: imie for numer, (imie, _ocena) in result.speaker_proposals.items()}
        if nazwy:
            if result.ja >= 0 and result.ja in result.speaker_names:
                nazwy = {**nazwy, result.ja: result.speaker_names[result.ja]}
            result.speaker_names = nazwy
            self.cb.log(
                t("Podpisano mówców: {imiona}").format(
                    imiona=", ".join(sorted(nazwy.values()))
                )
            )

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
        #: Pliki pomocnicze rozpoznawania mówców (tory własnego nagrania).
        tymczasowe: list = []
        is_temp = False

        try:
            self.cb.status(
                t("[{i}/{n}] Analizuję {plik}…").format(
                    i=index + 1, n=total, plik=source.name
                )
            )
            info = media.probe_media(source)

            if info.is_video:
                self.cb.log(
                    t("{plik}: wideo, {czas} — wyciągam ścieżkę audio.").format(
                        plik=source.name, czas=media.format_duration(info.duration)
                    )
                )
                self.cb.status(
                    t("[{i}/{n}] Wyciągam audio…").format(i=index + 1, n=total)
                )
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
                raise Cancelled(t("Przerwano przed transkrypcją."))

            self.cb.status(
                t("[{i}/{n}] Transkrybuję {plik}…").format(
                    i=index + 1, n=total, plik=source.name
                )
            )
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
                beam_size=self.settings.beam_size,
                vad_filter=self.settings.vad_filter,
                word_timestamps=needs_words,
                on_progress=lambda f: self.cb.file_progress(
                    self._waga_audio + f * self._waga_tekstu
                ),
                cancel=self.cb.cancelled,
            )

            if self.settings.diarize:
                self._rozpoznaj_mowcow(audio, result, index, total,
                                       source=source, info=info, tymczasowe=tymczasowe)

            # Zaznaczone fragmenty z nagrania („Zaznacz”/F8) — z tagu pliku
            # (od 1.6.1) albo ze starego pliku obok; trafiają do TXT i JSON.
            result.znaczniki = nagrywanie.wczytaj_znaczniki(source, info.tags)
            if result.znaczniki:
                self.cb.log(t("Zaznaczone momenty: {n}").format(n=len(result.znaczniki)))

            out_dir = self.settings.resolve_output_dir(source)
            job.outputs = writers.write_all(
                result, out_dir, source.stem, self.settings.formats
            )
            job.result = result
            job.ok = True

            self.cb.file_progress(1.0)
            self.cb.log(
                t("{plik}: gotowe — {podsumowanie}").format(
                    plik=source.name, podsumowanie=summarize(result)
                )
            )
            for fmt, path in job.outputs.items():
                self.cb.log(f"    -> {path}")

        except Cancelled as exc:
            job.error = str(exc)
            self.cb.log(t("{plik}: przerwano.").format(plik=source.name))
        except (media.MediaError, EngineError) as exc:
            job.error = str(exc)
            self.cb.log(t("BŁĄD ({plik}): {blad}").format(plik=source.name, blad=exc))
        except Exception as exc:  # nieprzewidziane — nie wywracaj całej kolejki
            job.error = f"{type(exc).__name__}: {exc}"
            self.cb.log(
                t("BŁĄD ({plik}): {blad}").format(plik=source.name, blad=job.error)
            )
            self.cb.log(traceback.format_exc(limit=3))
        finally:
            if not self.settings.keep_extracted_audio:
                for plik in ([temp_audio] if temp_audio else []) + tymczasowe:
                    _cleanup(plik)

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
