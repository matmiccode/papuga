"""Nagrywanie spotkań: mikrofon i dźwięk systemowy prosto do pliku FLAC.

Co powstaje: jeden plik `Spotkanie RRRR-MM-DD GG-MM.flac`, 16 kHz, 16 bitów,
dwa kanały: lewy to mikrofon (Ty), prawy to dźwięk spotkania (rozmówcy).
Transkrypcja idzie na miksie obu kanałów (media.extract_audio sprowadza
każde wejście do 16 kHz mono), a rozpoznawanie mówców dostaje każdy tor
osobno: lewy kanał to na pewno Ty, więc diaryzacja zgaduje tylko na prawym
(pipeline, diarization.odcinki_wlasne). Tag FLAC `WHISPER_AUTOMAT` mówi
programowi, że to własne nagranie i co leży w którym kanale — plik bez
tagu albo mono (nagrania sprzed 1.5) idzie starą drogą. Rozmiar: około
80 MB na godzinę, kodowanie poniżej 1 % jednego rdzenia.

Skąd dźwięk: PyAudioWPatch (PortAudio z łatą na WASAPI loopback). Mikrofon
to zwykłe urządzenie wejściowe, dźwięk spotkania (Teams w głośnikach albo
słuchawkach) to „loopback” urządzenia wyjściowego. Oba strumienie idą
w swoich natywnych formatach (zwykle 48 kHz, 2 kanały, float32), tu są
sprowadzane do 16 kHz mono (PyAV, swresample) i sumowane.

Trzy rzeczy, o które trzeba zadbać — wszystkie sprawdzone w spike'u
(PROGRESS.md, „Wydanie firmowe 1.3”):

* Loopback milczy, gdy nic nie gra: WASAPI nie dostarcza wtedy żadnych
  pakietów, więc strumień nie ma czym tykać. Lekarstwo: równoległy strumień
  wyjściowy grający ciszę na tym samym urządzeniu — loopback dostaje wtedy
  równe 50 pakietów na sekundę.
* Jeden wątek-właściciel: PyAudio() i wszystkie open()/close() muszą być
  w tym samym wątku, inaczej WASAPI odpowiada „Unanticipated host error”.
  Callbacki PortAudio tylko wkładają bajty do kolejek; całą resztę robi
  wątek roboczy.
* Podążanie za urządzeniem (od 1.5): gdy w ustawieniach jest „domyślne”
  (pusta nazwa), a w trakcie nagrania Windows zmieni domyślny mikrofon albo
  urządzenie odtwarzania (podłączony zestaw USB, połączone słuchawki
  Bluetooth), tor jest przepinany na nowe urządzenie — nagranie idzie dalej
  do tego samego pliku, bez luki. Sprawdzane co 3 s przez COM
  (urzadzenia.punkty, ~20 ms), bez tworzenia PyAudio. Urządzenie wskazane
  z nazwy nigdy nie jest przełączane. PortAudio zna tylko domyślne
  urządzenie multimedialne; Teams używa „komunikacyjnego”, które bywa inne
  — wtedy zostaje lista „Dźwięk spotkania” i podpowiedź o ciszy w torze spotkania.
* Dwa zegary: mikrofon i głośniki mają osobne kwarce, a każdy z nich dryfuje
  względem zegara systemowego. Każde źródło kładzie swoje próbki na wspólną
  oś czasu (OsCzasu) według czasu, który PortAudio podał przy pakiecie
  (time_info, liczone natywnie — nie cierpi na opóźnienia GIL). Luki
  dostają ciszę, dryf jest korygowany tylko w ciszy, małymi krokami.

Odporność: FLAC jest pisany na bieżąco, ramka po ramce; plik urwany przez
awarię jest czytelny dla ffmpeg (brak tylko łącznej długości w nagłówku).
Obok pliku leży znacznik `.nagrywanie` — jeśli program startuje i znajduje
znacznik bez żywego procesu, nagranie da się odzyskać (napraw_nagranie).

Obraz spotkania (od 1.6, gdy wybrano okno albo cały ekran): osobny proces ffmpeg
(core/wideo.py), odcinek na każdy kawałek między pauzami, sklejany z tym
plikiem do `Spotkanie ….mkv` po zatrzymaniu — w tle, transkrypcja nie
czeka. Stan odcinków siedzi w znaczniku `.nagrywanie`, więc po awarii
odzyskanie składa też obraz.
"""

from __future__ import annotations

import json
import math
import os
import queue
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np

from ..teksty import t
from . import wideo

#: Częstotliwość pliku i osi czasu. Taka sama jak wejście modelu.
RATE = 16000
#: Blok miksera: 100 ms.
BLOK = RATE // 10
#: Ile czasu czekamy, zanim blok trafi do pliku — spóźnione pakiety
#: (np. po chwilowym zatorze GIL) mają gdzie wylądować.
BUFOR_S = 0.5
#: Rozjazd między zegarem a próbkami, od którego mówimy o luce, nie dryfie.
PROG_LUKI_S = 0.2
#: Luka dłuższa niż to (uśpienie komputera) zostaje w pliku jako 1 s ciszy.
MAX_LUKA_S = 60.0
#: Dryf korygujemy tylko w ciszy źródła, krokami do 20 ms, gdy przekroczy 10 ms.
CISZA_DBFS = -55.0
KROK_MIN = RATE // 100
KROK_MAX = RATE // 50
#: Bufor PortAudio: 100 ms. Mniejsze bufory to więcej callbacków i więcej CPU,
#: a opóźnienie nie ma tu znaczenia — nagrywamy, nie rozmawiamy.
BUFOR_PA_MS = 100
#: Minimalne wolne miejsce na dysku przy starcie.
MIN_WOLNE_B = 1 << 30
#: Co ile sekund sprawdzamy, czy Windows zmienił domyślne urządzenie.
CO_ILE_URZADZENIA_S = 3.0
ROZSZERZENIE = ".flac"
ZNACZNIK = ".nagrywanie"
#: Tag w pliku FLAC: po nim program poznaje własne nagranie i układ kanałów.
#: Czytany przez media.probe_media (ffprobe, format.tags) i parsowany
#: w diarization.tag_nagrania.
TAG = "WHISPER_AUTOMAT"
TAG_WARTOSC = "nagranie-spotkania;kanaly=mikrofon,system;wersja=1.5"
#: Kanały pliku: 0 = mikrofon, 1 = system.
KANALY = 2
#: Zaznaczone fragmenty („Zaznacz”/F8: początek, drugie naciśnięcie — koniec)
#: siedzą od 1.6.1 w tagu samego nagrania: JSON [[początek, koniec], …],
#: koniec null = pojedynczy moment. Dopisywany po zamknięciu pliku w miejsce
#: wypełnienia nagłówka FLAC (dopisz_tagi_flac) — bez osobnego pliku obok.
TAG_ZNACZNIKI = "WHISPER_AUTOMAT_ZNACZNIKI"
#: Fragment krótszy niż tyle to pojedynczy moment (dwa szybkie naciśnięcia).
MOMENT_S = 2.0
#: Do 1.6.0 znaczniki leżały w pliku obok nagrania, `Spotkanie ….znaczniki.json`
#: ([{"czas", "opis"}]) — czytany dalej dla starych nagrań i jako zapas, gdy
#: tag się nie zmieści.
ZNACZNIKI = ".znaczniki.json"


# ---------------------------------------------------------------------------
# Urządzenia
# ---------------------------------------------------------------------------


def dostepne() -> bool:
    """Czy jest biblioteka przechwytywania (PyAudioWPatch)."""
    import importlib.util

    return importlib.util.find_spec("pyaudiowpatch") is not None


def _pa():
    import pyaudiowpatch as pyaudio

    return pyaudio


@dataclass(frozen=True)
class UrzadzenieAudio:
    index: int
    nazwa: str
    kanaly: int
    rate: int
    #: Dla urządzenia wyjściowego: numer jego loopbacku (None = brak).
    loopback_index: Optional[int] = None
    domyslne: bool = False


def lista_urzadzen() -> Tuple[List[UrzadzenieAudio], List[UrzadzenieAudio]]:
    """(mikrofony, urządzenia wyjściowe) widziane przez WASAPI.

    Na świeżej instancji PyAudio — lista urządzeń jest zamrażana w chwili
    jej utworzenia, więc podłączony przed chwilą zestaw słuchawkowy widać
    dopiero w nowej.
    """
    pa = _pa()
    p = pa.PyAudio()
    try:
        return _lista(p, pa)
    finally:
        p.terminate()


def _lista(p, pa) -> Tuple[List[UrzadzenieAudio], List[UrzadzenieAudio]]:
    def domyslne(**kw) -> Optional[int]:
        try:
            return int(p.get_default_wasapi_device(**kw)["index"])
        except (OSError, LookupError, KeyError):
            return None

    dom_we, dom_wy = domyslne(d_in=True), domyslne(d_out=True)
    wejscia: List[UrzadzenieAudio] = []
    wyjscia: List[UrzadzenieAudio] = []
    for d in p.get_device_info_generator_by_host_api(host_api_type=pa.paWASAPI):
        if d.get("isLoopbackDevice"):
            continue
        if d["maxInputChannels"] > 0:
            wejscia.append(UrzadzenieAudio(
                int(d["index"]), str(d["name"]), int(d["maxInputChannels"]),
                int(d["defaultSampleRate"]), None, int(d["index"]) == dom_we))
        if d["maxOutputChannels"] > 0:
            try:
                loopback = int(p.get_wasapi_loopback_analogue_by_dict(d)["index"])
            except (OSError, LookupError, KeyError):
                loopback = None
            wyjscia.append(UrzadzenieAudio(
                int(d["index"]), str(d["name"]), int(d["maxOutputChannels"]),
                int(d["defaultSampleRate"]), loopback, int(d["index"]) == dom_wy))
    return wejscia, wyjscia


def _znajdz(lista: List[UrzadzenieAudio], nazwa: str) -> Optional[UrzadzenieAudio]:
    """Urządzenie po nazwie z ustawień; pusta nazwa albo brak = domyślne."""
    if nazwa:
        for u in lista:
            if u.nazwa == nazwa:
                return u
    for u in lista:
        if u.domyslne:
            return u
    return lista[0] if lista else None


def nagraj_probke_mikrofonu(nazwa: str, plik: Path, sekundy: float = 5.0,
                            poziom: Callable[[float], None] = lambda _d: None,
                            przerwij: Optional[threading.Event] = None) -> Tuple[str, float]:
    """Kilka sekund z mikrofonu do WAV — „Sprawdź” przy mikrofonie na karcie nagrywania.

    Ten sam wybór urządzenia co w nagraniu (pusta nazwa = domyślny
    w Windows), więc odsłuch pokazuje, jak zabrzmi spotkanie. `poziom`
    dostaje dBFS co 100 ms (z wątku tła). Zwraca (nazwa urządzenia,
    najgłośniejsze 100 ms w dBFS) — po tym okno mówi „prawie cisza”.
    """
    import wave

    pa = _pa()
    p = pa.PyAudio()
    try:
        wejscia, _ = _lista(p, pa)
        u = _znajdz(wejscia, nazwa)
        if u is None:
            raise BladNagrywania(t("Nie znaleziono żadnego mikrofonu."))
        kanaly = max(1, min(u.kanaly, 2))
        blok = max(1, u.rate // 10)
        strumien = p.open(format=pa.paFloat32, channels=kanaly, rate=u.rate, input=True,
                          input_device_index=u.index, frames_per_buffer=blok)
        czesci: list = []
        szczyt = -120.0
        try:
            for _ in range(max(1, int(sekundy * 10))):
                if przerwij is not None and przerwij.is_set():
                    break
                dane = np.frombuffer(strumien.read(blok, exception_on_overflow=False),
                                     dtype=np.float32)
                mono = dane.reshape(-1, kanaly).mean(axis=1) if kanaly > 1 else dane
                czesci.append(mono)
                db = rms_dbfs(mono)
                szczyt = max(szczyt, db)
                poziom(db)
        finally:
            strumien.stop_stream()
            strumien.close()
        probki = np.concatenate(czesci) if czesci else np.zeros(0, dtype=np.float32)
        with wave.open(str(plik), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(u.rate)
            w.writeframes((np.clip(probki, -1.0, 1.0) * 32767).astype("<i2").tobytes())
        return u.nazwa, szczyt
    finally:
        p.terminate()


def _czy_przepiac(ustawienie: str, otwarte: str, domyslne: str) -> bool:
    """Czy tor otwarty na `otwarte` ma przejść na nowe domyślne urządzenie.

    Tylko gdy użytkownik zostawił „domyślne” (puste ustawienie), Windows
    ma teraz inne domyślne i jego nazwa jest znana. Wskazane z nazwy — nigdy.
    """
    return not ustawienie and bool(domyslne) and bool(otwarte) and domyslne != otwarte


# ---------------------------------------------------------------------------
# Zdarzenia dla okna
# ---------------------------------------------------------------------------


@dataclass
class ZdarzeniaNagrywania:
    """Punkty zaczepienia dla interfejsu. Wołane z wątku roboczego."""

    #: Poziomy mikrofonu i dźwięku systemowego w dBFS (-120 = cisza), ~10 Hz.
    poziomy: Callable[[float, float], None] = lambda _m, _s: None
    #: Czas nagrania w sekundach, co sekundę.
    czas: Callable[[float], None] = lambda _s: None
    #: Coś poszło nie tak, ale nagrywamy dalej (np. mikrofon zniknął).
    ostrzezenie: Callable[[str], None] = lambda _t: None
    #: Nagrywanie nie mogło ruszyć albo padło; plik (jeśli jest) został domknięty.
    blad: Callable[[str], None] = lambda _t: None
    #: Zatrzymane przez użytkownika: ścieżka pliku i długość w sekundach.
    zakonczono: Callable[[Path, float], None] = lambda _p, _s: None
    anulowano: Callable[[], None] = lambda: None
    #: Pauza weszła (True) albo nagranie wznowione (False).
    pauza: Callable[[bool], None] = lambda _p: None
    #: Lista urządzeń Windows (urzadzenia.PunktAudio) odświeżona w trakcie
    #: nagrania, co kilka sekund — okno pokazuje z niej linię źródeł.
    urzadzenia: Callable[[list], None] = lambda _u: None
    #: Obraz spotkania złożony po `zakonczono`: ścieżka MKV i pusty błąd,
    #: albo None i opis błędu (odcinki zostają obok nagrania).
    obraz: Callable[[Optional[Path], str], None] = lambda _p, _b: None
    #: Okno spotkania schowało się (True — zwykle udostępnianie ekranu) albo
    #: wróciło (False); okno programu pyta wtedy o przełączenie obrazu.
    okno_spotkania: Callable[[bool], None] = lambda _s: None
    #: Obraz idzie już z nowego źródła (po `Nagrywarka.przelacz_obraz`).
    zrodlo_obrazu: Callable[[object], None] = lambda _z: None


# ---------------------------------------------------------------------------
# Oś czasu, źródła, mikser, zapis
# ---------------------------------------------------------------------------


def rms_dbfs(x: np.ndarray) -> float:
    if x.size == 0:
        return -120.0
    if x.dtype == np.int16:
        x = x.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(x * x)))
    return max(-120.0, 20.0 * math.log10(rms)) if rms > 0 else -120.0


class Zegar:
    """Wspólny zegar nagrania: sekundy od startu → pozycja próbki 16 kHz.

    `przesuniecie` rośnie, gdy komputer zasnął: długa luka zostaje w pliku
    jako jedna sekunda ciszy zamiast pół godziny.
    """

    def __init__(self, t0: float):
        self.t0 = t0
        self.przesuniecie = 0.0

    def pozycja(self, t: float) -> int:
        return int((t - self.t0 - self.przesuniecie) * RATE)


class OsCzasu:
    """Bufor pierścieniowy int16 adresowany bezwzględną pozycją próbki.

    Źródła wpisują dane tam, gdzie według swojego czasu należą; mikser
    pobiera bloki po kolei. Tam, gdzie nikt nic nie wpisał, są zera — luka
    w loopbacku nie wymaga żadnej osobnej obsługi.
    """

    def __init__(self, pojemnosc: int = RATE * 60):
        self._buf = np.zeros(pojemnosc, dtype=np.int16)
        self.pojemnosc = pojemnosc
        #: Pozycja, do której mikser już pobrał dane.
        self.wydane = 0
        #: Najdalsza pozycja, pod którą coś wpisano.
        self.najdalej = 0
        #: Próbki odrzucone: spóźnione (pod już wydaną pozycję) albo za daleko.
        self.odrzucone = 0

    def wpisz(self, poz: int, dane: np.ndarray) -> None:
        n = int(dane.size)
        if n == 0:
            return
        if poz < self.wydane:
            k = self.wydane - poz
            if k >= n:
                self.odrzucone += n
                return
            dane, poz, n = dane[k:], self.wydane, n - k
            self.odrzucone += k
        granica = self.wydane + self.pojemnosc
        if poz + n > granica:
            k = poz + n - granica
            if k >= n:
                self.odrzucone += n
                return
            dane, n = dane[: n - k], n - k
            self.odrzucone += k
        start = poz % self.pojemnosc
        koniec = start + n
        if koniec <= self.pojemnosc:
            self._buf[start:koniec] = dane
        else:
            pierwsza = self.pojemnosc - start
            self._buf[start:] = dane[:pierwsza]
            self._buf[: n - pierwsza] = dane[pierwsza:]
        self.najdalej = max(self.najdalej, poz + n)

    def utnij(self, poz: int) -> None:
        """Kasuje to, co wpisano od `poz` dalej (jeszcze nie wydane).

        Pauza: dźwięk z chwil po jej naciśnięciu, dobrany z kolejek przy
        zamykaniu torów, po wznowieniu leżałby tuż za miejscem pauzy —
        przed nowym dźwiękiem, którego tory jeszcze nie oddały.
        """
        poz = max(poz, self.wydane)
        n = min(self.najdalej - poz, self.pojemnosc)
        if n <= 0:
            return
        start = poz % self.pojemnosc
        koniec = start + n
        if koniec <= self.pojemnosc:
            self._buf[start:koniec] = 0
        else:
            self._buf[start:] = 0
            self._buf[: koniec - self.pojemnosc] = 0
        self.najdalej = poz

    def pobierz(self, od: int, n: int) -> np.ndarray:
        start = od % self.pojemnosc
        koniec = start + n
        if koniec <= self.pojemnosc:
            out = self._buf[start:koniec].copy()
            self._buf[start:koniec] = 0
        else:
            pierwsza = self.pojemnosc - start
            out = np.concatenate([self._buf[start:], self._buf[: n - pierwsza]])
            self._buf[start:] = 0
            self._buf[: n - pierwsza] = 0
        self.wydane = max(self.wydane, od + n)
        return out


class Zrodlo:
    """Jeden tor (mikrofon albo system): float32 → 16 kHz mono → oś czasu."""

    def __init__(self, nazwa: str, rate: int, kanaly: int, zegar: Zegar,
                 os_czasu: OsCzasu, opoznienie: float = 0.0):
        import av

        self.nazwa = nazwa
        self.rate = rate
        self.kanaly = kanaly
        self.zegar = zegar
        self.os = os_czasu
        self.opoznienie = opoznienie
        self._res = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        self._av = av
        self._pts = 0
        #: Pozycja (16 kHz) następnej próbki tego źródła; None przed pierwszym pakietem.
        self.pozycja: Optional[int] = None
        self.rms_dbfs = -120.0
        self.luki = 0
        self.korekty = 0
        #: Pakiety, które przyszły „z przyszłości” (źródło przed zegarem o ponad próg).
        self.wyprzedzenia = 0

    def przyjmij(self, t: float, dane: bytes) -> None:
        """Jeden pakiet z callbacku; `t` to czas jego końca (domena zegara)."""
        self.przyjmij_wiele([(t, dane)])

    def przyjmij_wiele(self, pakiety) -> None:
        """Pakiety zebrane z kolejki od ostatniego razu, w kolejności nadejścia.

        Po zatorze (np. GIL zajęty przez transkrypcję) PortAudio oddaje kilka
        pakietów hurtem, wszystkie z tym samym czasem. Takiej grupie
        rozdajemy czasy wstecz — przedostatni pakiet skończył się o jeden
        pakiet wcześniej niż ostatni — i dopiero wtedy kładziemy je na osi.
        """
        pakiety = list(pakiety)
        if not pakiety:
            return
        czasy = [t for t, _d in pakiety]
        poprawione = list(czasy)
        i = len(pakiety) - 1
        while i > 0:
            j = i
            while j > 0 and abs(czasy[j - 1] - czasy[i]) < 0.001:
                j -= 1
            if j < i:
                for k in range(j, i):
                    dlugosc = sum(len(pakiety[m][1]) for m in range(k + 1, i + 1)) \
                        / (4 * self.kanaly * self.rate)
                    poprawione[k] = czasy[i] - dlugosc
            i = j - 1
        for t, dane in zip(poprawione, (d for _t, d in pakiety)):
            flt = np.frombuffer(dane, dtype=np.float32)
            if self.kanaly > 1:
                flt = flt.reshape(-1, self.kanaly).mean(axis=1, dtype=np.float32)
            self.rms_dbfs = rms_dbfs(flt)
            self._uloz(t, self._resample(flt))

    def zakoncz(self) -> None:
        """Dopycha resztę z resamplera. Drugie wywołanie nic nie robi."""
        if self._res is None:
            return
        reszta = self._resample(None)
        self._res = None
        if reszta.size and self.pozycja is not None:
            self.os.wpisz(self.pozycja, reszta)
            self.pozycja += int(reszta.size)

    def _resample(self, mono: Optional[np.ndarray]) -> np.ndarray:
        if mono is None:
            ramki = self._res.resample(None)
        else:
            if mono.size == 0:
                return np.zeros(0, dtype=np.int16)
            ramka = self._av.AudioFrame.from_ndarray(
                np.ascontiguousarray(mono).reshape(1, -1), format="flt", layout="mono")
            ramka.sample_rate = self.rate
            ramka.pts = self._pts
            ramka.time_base = Fraction(1, self.rate)
            self._pts += int(mono.size)
            ramki = self._res.resample(ramka)
        if not ramki:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate([r.to_ndarray()[0] for r in ramki]).astype(np.int16, copy=False)

    def _uloz(self, t: float, s16: np.ndarray) -> None:
        n = int(s16.size)
        if n == 0:
            return
        # PortAudio podaje czas wywołania callbacku, czyli koniec pakietu
        # (WASAPI nie odejmuje opóźnienia); początek pakietu jest o n wcześniej.
        oczekiwana = self.zegar.pozycja(t - self.opoznienie) - n
        if self.pozycja is None:
            # Może być ujemna (pakiet sprzed startu zegara) — oś czasu
            # odrzuci to, co leży przed zerem, a reszta trafi na miejsce.
            self.pozycja = oczekiwana
        roznica = oczekiwana - self.pozycja
        if roznica > PROG_LUKI_S * RATE:
            # Źródło milczało (loopback bez dźwięku, zator, sen) — zera
            # w osi czasu powstają same, my tylko przeskakujemy.
            self.luki += 1
            self.pozycja = oczekiwana
        else:
            if roznica < -PROG_LUKI_S * RATE:
                # Źródło oddało więcej, niż upłynęło czasu. Niczego nie
                # ucinamy — to prawdziwy dźwięk; nadmiar zniknie w ciszy
                # (niżej), a bufor pierścieniowy i tak ogranicza
                # wyprzedzenie do minuty.
                self.wyprzedzenia += 1
            if abs(roznica) > KROK_MIN and self.rms_dbfs < CISZA_DBFS:
                # Dryf kwarcu — nadrabiamy po cichu, gdy nikt nie mówi.
                self.pozycja += int(max(-KROK_MAX, min(KROK_MAX, roznica)))
                self.korekty += 1
        self.os.wpisz(self.pozycja, s16)
        self.pozycja += int(s16.size)


def miksuj(m: np.ndarray, s: np.ndarray) -> np.ndarray:
    """Suma obu torów (nie średnia — strony rzadko mówią naraz), miękki limiter.

    Od 1.5 plik jest dwukanałowy (`przeplot`), a miks robi dopiero ffmpeg
    przy transkrypcji; ta funkcja zostaje dla testów i narzędzi.
    """
    y = m.astype(np.float32) / 32768.0 + s.astype(np.float32) / 32768.0
    if y.size and float(np.max(np.abs(y))) > 0.99:
        y = np.tanh(y)
    return np.clip(np.rint(y * 32768.0), -32768, 32767).astype(np.int16)


def przeplot(m: np.ndarray, s: np.ndarray) -> np.ndarray:
    """Dwa tory w jeden strumień stereo: [m0, s0, m1, s1, …] (lewy = mikrofon)."""
    n = min(int(m.size), int(s.size))
    out = np.empty(2 * n, dtype=np.int16)
    out[0::2] = m[:n]
    out[1::2] = s[:n]
    return out


def tagi_nagrania(plik: Path) -> dict:
    """Tagi wpisywane do pliku FLAC własnego nagrania."""
    return {TAG: TAG_WARTOSC, "title": Path(plik).stem}


class ZapisFlac:
    """FLAC 16 kHz pisany na bieżąco, własnym uchwytem pliku (fsync).

    `kanaly=2` przyjmuje w `zapisz` próbki przeplecione (`przeplot`).
    Tagi (słownik) trafiają do nagłówka — PyAV wymaga ich przed
    `add_stream`, ffprobe oddaje je w `format.tags`.
    """

    def __init__(self, sciezka: Path, kanaly: int = 1, tagi: Optional[dict] = None):
        import av

        self.sciezka = Path(sciezka)
        self.kanaly = int(kanaly)
        self._plik = open(self.sciezka, "wb", buffering=0)
        self._kont = av.open(self._plik, "w", format="flac")
        for klucz, wartosc in (tagi or {}).items():
            self._kont.metadata[str(klucz)] = str(wartosc)
        self._st = self._kont.add_stream("flac", rate=RATE)
        self._st.layout = "stereo" if self.kanaly == 2 else "mono"
        self._st.format = "s16"
        self._av = av
        self._pts = 0
        #: Liczba ramek (próbek na kanał).
        self.probki = 0

    def zapisz(self, s16: np.ndarray) -> None:
        """Próbki int16: mono wprost, stereo przeplecione (2 na ramkę)."""
        s16 = np.ascontiguousarray(s16, dtype=np.int16).reshape(-1)
        if s16.size == 0:
            return
        ramek = int(s16.size) // self.kanaly
        r = self._av.AudioFrame.from_ndarray(
            s16[: ramek * self.kanaly].reshape(1, -1), format="s16",
            layout="stereo" if self.kanaly == 2 else "mono")
        r.sample_rate = RATE
        r.pts = self._pts
        r.time_base = Fraction(1, RATE)
        self._pts += ramek
        self.probki += ramek
        self._kont.mux(self._st.encode(r))

    def fsync(self) -> None:
        try:
            os.fsync(self._plik.fileno())
        except OSError:
            pass

    def zamknij(self) -> None:
        # Ostatni pakiet niesie uzupełniony nagłówek (łączna liczba próbek).
        self._kont.mux(self._st.encode(None))
        self._kont.close()
        self.fsync()
        self._plik.close()

    def porzuc(self) -> None:
        try:
            self._kont.close()
        except Exception:
            pass
        try:
            self._plik.close()
        except Exception:
            pass
        try:
            self.sciezka.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Znacznik niedokończonego nagrania
# ---------------------------------------------------------------------------


def sciezka_znacznika(plik: Path) -> Path:
    return plik.with_name(plik.name + ZNACZNIK)


def zapisz_znacznik(plik: Path, **dane) -> None:
    tresc = {"pid": os.getpid(), "start": datetime.now().isoformat(timespec="seconds"), **dane}
    sciezka_znacznika(plik).write_text(
        json.dumps(tresc, ensure_ascii=False, indent=2), encoding="utf-8")


def usun_znacznik(plik: Path) -> None:
    try:
        sciezka_znacznika(plik).unlink()
    except OSError:
        pass


def sciezka_znacznikow(plik: Path) -> Path:
    """`Spotkanie ….flac` → `Spotkanie ….znaczniki.json` (obok nagrania)."""
    plik = Path(plik)
    return plik.with_name(plik.stem + ZNACZNIKI)


def zapisz_znaczniki(plik: Path, znaczniki: list) -> Optional[Path]:
    """Zapas: znaczniki w pliku obok nagrania, gdy tag się nie zmieścił."""
    if not znaczniki:
        return None
    cel = sciezka_znacznikow(plik)
    cel.write_text(json.dumps(
        [{"czas": round(float(z.get("czas", 0.0)), 3),
          "koniec": None if z.get("koniec") is None else round(float(z["koniec"]), 3),
          "opis": str(z.get("opis", ""))}
         for z in znaczniki],
        ensure_ascii=False, indent=2), encoding="utf-8")
    return cel


def znaczniki_do_tagu(znaczniki: list) -> str:
    """[{"czas", "koniec"}] → zwarty JSON do tagu: [[734.2,812.0],[900.1,null]]."""
    return json.dumps([[round(float(z["czas"]), 3),
                        None if z.get("koniec") is None else round(float(z["koniec"]), 3)]
                       for z in znaczniki], separators=(",", ":"))


def _znaczniki_z_listy(dane) -> list:
    wynik = []
    for wpis in dane if isinstance(dane, list) else []:
        try:
            if isinstance(wpis, (list, tuple)):
                czas, koniec = float(wpis[0]), (None if len(wpis) < 2 or wpis[1] is None
                                                else float(wpis[1]))
                opis = ""
            else:
                czas = float(wpis.get("czas", 0.0))
                koniec = None if wpis.get("koniec") is None else float(wpis["koniec"])
                opis = str(wpis.get("opis", "") or "")
        except (AttributeError, TypeError, ValueError, IndexError):
            continue
        wynik.append({"czas": czas, "koniec": koniec if koniec is None or koniec > czas else None,
                      "opis": opis})
    return sorted(wynik, key=lambda z: z["czas"])


def wczytaj_znaczniki(plik: Path, tagi: Optional[dict] = None) -> list:
    """Zaznaczone fragmenty nagrania: z tagu pliku (od 1.6.1), a gdy go nie
    ma — ze starego pliku `.znaczniki.json` obok (dowolnego nagrania).

    `tagi` — `MediaInfo.tags` (klucze małymi literami), żeby nie pytać
    ffprobe drugi raz. Zwraca [{"czas", "koniec" (None = moment), "opis"}]
    posortowane po czasie; pustą listę, gdy nic nie ma.
    """
    tekst = (tagi or {}).get(TAG_ZNACZNIKI.lower())
    if tekst:
        try:
            return _znaczniki_z_listy(json.loads(tekst))
        except ValueError:
            pass
    sciezka = sciezka_znacznikow(plik)
    if not sciezka.is_file():
        return []
    try:
        dane = json.loads(sciezka.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(dane, dict):
        dane = dane.get("znaczniki", [])
    return _znaczniki_z_listy(dane)


def dopisz_tagi_flac(plik: Path, tagi: dict) -> bool:
    """Dopisuje (albo podmienia) tagi gotowego FLAC-a w miejscu, bez
    przepisywania dźwięku — tak jak metaflac.

    Nowy blok VORBIS_COMMENT rośnie kosztem bloku PADDING (ffmpeg zostawia
    8 KB), więc dźwięk nie przesuwa się ani o bajt. False — inny układ
    pliku albo za mało miejsca; plik zostaje wtedy nietknięty.
    """
    import struct

    with open(plik, "r+b") as f:
        if f.read(4) != b"fLaC":
            return False
        bloki = []
        while True:
            naglowek = f.read(4)
            if len(naglowek) < 4:
                return False
            dlugosc = int.from_bytes(naglowek[1:], "big")
            bloki.append([naglowek[0] & 0x7F, f.read(dlugosc)])
            if naglowek[0] & 0x80:
                break
        rozmiar = f.tell()
        komentarz = next((b for b in bloki if b[0] == 4), None)
        wypelnienie = next((b for b in bloki if b[0] == 1), None)
        if komentarz is None or wypelnienie is None:
            return False
        dane = komentarz[1]
        dl_dostawcy = struct.unpack_from("<I", dane, 0)[0]
        dostawca = dane[4:4 + dl_dostawcy]
        pozycja = 4 + dl_dostawcy
        ile = struct.unpack_from("<I", dane, pozycja)[0]
        pozycja += 4
        wpisy = []
        klucze = {k.upper() for k in tagi}
        for _ in range(ile):
            dl = struct.unpack_from("<I", dane, pozycja)[0]
            wpis = dane[pozycja + 4:pozycja + 4 + dl]
            pozycja += 4 + dl
            if wpis.split(b"=", 1)[0].decode("utf-8", "replace").upper() not in klucze:
                wpisy.append(wpis)
        wpisy += [f"{k}={v}".encode("utf-8") for k, v in tagi.items()]
        nowy = (struct.pack("<I", len(dostawca)) + dostawca + struct.pack("<I", len(wpisy))
                + b"".join(struct.pack("<I", len(w)) + w for w in wpisy))
        nowe_wypelnienie = len(wypelnienie[1]) - (len(nowy) - len(dane))
        if nowe_wypelnienie < 0:
            return False
        komentarz[1] = nowy
        wypelnienie[1] = bytes(nowe_wypelnienie)
        wynik = bytearray(b"fLaC")
        for i, (typ, tresc) in enumerate(bloki):
            ostatni = 0x80 if i == len(bloki) - 1 else 0
            wynik += bytes([typ | ostatni]) + len(tresc).to_bytes(3, "big") + tresc
        if len(wynik) != rozmiar:
            return False
        f.seek(0)
        f.write(wynik)
    return True


def _proces_zyje(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    k32 = ctypes.windll.kernel32
    uchwyt = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not uchwyt:
        return False
    try:
        kod = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(uchwyt, ctypes.byref(kod)):
            return False
        return kod.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(uchwyt)


def znajdz_niedokonczone(katalog: Path) -> List[Path]:
    """Nagrania ze znacznikiem, których proces już nie żyje (awaria)."""
    katalog = Path(katalog)
    if not katalog.is_dir():
        return []
    wynik = []
    for znacznik in sorted(katalog.glob(f"*{ROZSZERZENIE}{ZNACZNIK}")):
        plik = znacznik.with_name(znacznik.name[: -len(ZNACZNIK)])
        try:
            pid = int(json.loads(znacznik.read_text(encoding="utf-8")).get("pid", 0))
        except (OSError, ValueError, AttributeError):
            pid = 0
        if pid and _proces_zyje(pid):
            continue
        if plik.is_file() and plik.stat().st_size > 0:
            wynik.append(plik)
        else:
            # Pusty plik po awarii na samym starcie — nic do odzyskania.
            try:
                plik.unlink()
            except OSError:
                pass
            usun_znacznik(plik)
    return wynik


def napraw_nagranie(plik: Path) -> Path:
    """Przepisuje urwany FLAC do pliku z poprawnym nagłówkiem (długością).

    Dekoduje, ile się da (ostatni, urwany blok pomija), koduje na nowo do
    `<nazwa> (odzyskane).flac` — z tą samą liczbą kanałów i tagami, żeby
    odzyskane nagranie dalej było „własne” dla transkrypcji — usuwa
    uszkodzony plik i znacznik. Obraz spotkania, jeśli był (odcinki
    w znaczniku), składa z odzyskanym dźwiękiem; gdy się nie da, odcinki
    zostają obok — każdy da się obejrzeć osobno.
    """
    import av

    plik = Path(plik)
    try:
        stan = json.loads(sciezka_znacznika(plik).read_text(encoding="utf-8"))
        odcinki_obrazu = stan.get("obraz") or []
        znaczniki = _znaczniki_z_listy(stan.get("znaczniki") or [])
    except (OSError, ValueError, AttributeError):
        odcinki_obrazu, znaczniki = [], []
    cel = plik.with_name(f"{plik.stem} (odzyskane){plik.suffix}")
    licznik = 2
    while cel.exists():
        cel = plik.with_name(f"{plik.stem} (odzyskane) ({licznik}){plik.suffix}")
        licznik += 1

    zapis = None
    try:
        with av.open(str(plik)) as zrodlo:
            strumien = zrodlo.streams.audio[0]
            kanaly = 2 if int(strumien.channels or 1) >= 2 else 1
            tagi = dict(zrodlo.metadata or {})
            if TAG in tagi:
                tagi["title"] = cel.stem
            if znaczniki:
                # Fragment otwarty w chwili awarii zostaje momentem.
                tagi[TAG_ZNACZNIKI] = znaczniki_do_tagu(znaczniki)
            zapis = ZapisFlac(cel, kanaly=kanaly, tagi=tagi)
            res = av.AudioResampler(format="s16", layout="stereo" if kanaly == 2 else "mono",
                                    rate=RATE)
            try:
                for ramka in zrodlo.decode(strumien):
                    for r in res.resample(ramka):
                        zapis.zapisz(r.to_ndarray()[0].astype(np.int16, copy=False))
            except av.error.FFmpegError:
                # Urwany ostatni blok — bierzemy to, co się zdekodowało.
                pass
        for r in res.resample(None):
            zapis.zapisz(r.to_ndarray()[0].astype(np.int16, copy=False))
        zapis.zamknij()
    except Exception:
        if zapis is not None:
            zapis.porzuc()
        raise
    try:
        plik.unlink()
    except OSError:
        pass
    usun_znacznik(plik)
    stare_znaczniki = sciezka_znacznikow(plik)
    if stare_znaczniki.is_file():
        try:
            stare_znaczniki.replace(sciezka_znacznikow(cel))
        except OSError:
            pass
    sciezka_ff = wideo.ffmpeg() if odcinki_obrazu else None
    if sciezka_ff:
        try:
            wideo.zloz_odcinki(sciezka_ff, cel, odcinki_obrazu, kanaly=kanaly,
                               znaczniki=znaczniki or wczytaj_znaczniki(cel))
        except Exception:
            pass
    return cel


def nazwa_pliku(katalog: Path, kiedy: Optional[datetime] = None) -> Path:
    """`Spotkanie RRRR-MM-DD GG-MM.flac`; przy kolizji dopisek ` (2)`."""
    kiedy = kiedy or datetime.now()
    baza = f"Spotkanie {kiedy:%Y-%m-%d %H-%M}"
    cel = Path(katalog) / f"{baza}{ROZSZERZENIE}"
    licznik = 2
    while cel.exists() or sciezka_znacznika(cel).exists():
        cel = Path(katalog) / f"{baza} ({licznik}){ROZSZERZENIE}"
        licznik += 1
    return cel


# ---------------------------------------------------------------------------
# Nagrywarka
# ---------------------------------------------------------------------------


class BladNagrywania(RuntimeError):
    """Nagrywanie nie mogło ruszyć."""


class _Tor:
    """Strumień wejściowy PortAudio w trybie callback — callback tylko kolejkuje."""

    def __init__(self, p, pa, urzadzenie_index: int, rate: int, kanaly: int,
                 fallback_czas: Callable[[], float]):
        self.rate = rate
        self.kanaly = kanaly
        self.q: "queue.SimpleQueue" = queue.SimpleQueue()
        self._fallback = fallback_czas
        self.stream = p.open(
            format=pa.paFloat32, channels=kanaly, rate=rate, input=True,
            input_device_index=urzadzenie_index,
            frames_per_buffer=max(1, rate * BUFOR_PA_MS // 1000),
            stream_callback=self._cb,
        )
        self.opoznienie = float(self.stream.get_input_latency() or 0.0)
        self._kontynuuj = pa.paContinue

    def _cb(self, in_data, _frame_count, time_info, status):
        t = float(time_info.get("current_time") or 0.0) if time_info else 0.0
        if t <= 0.0:
            t = self._fallback()
        self.q.put((t, in_data, int(status or 0)))
        return (None, self._kontynuuj)

    def zbierz(self) -> list:
        out = []
        while True:
            try:
                out.append(self.q.get_nowait())
            except queue.Empty:
                return out

    def aktywny(self) -> bool:
        try:
            return bool(self.stream.is_active())
        except Exception:
            return False

    def zamknij(self) -> None:
        for akcja in (self.stream.stop_stream, self.stream.close):
            try:
                akcja()
            except Exception:
                pass


class Nagrywarka:
    """Nagrywa mikrofon i dźwięk systemowy do jednego pliku FLAC.

    `start()` wraca od razu; wszystko dzieje się w wątku roboczym, który
    jest też jedynym właścicielem PyAudio. `stop()` i `anuluj()` tylko
    dają znak — wynik przychodzi przez `zdarzenia.zakonczono/anulowano`.
    """

    BEZCZYNNA, STARTUJE, NAGRYWA, PAUZA, KONCZY = (
        "bezczynna", "startuje", "nagrywa", "pauza", "konczy")

    def __init__(self) -> None:
        self.stan = self.BEZCZYNNA
        self.sciezka: Optional[Path] = None
        self.czas_trwania = 0.0
        #: Po zakończeniu: luki, obcięcia, korekty dryfu i odrzucone próbki
        #: na każde źródło — do dziennika i diagnostyki.
        self.statystyki: dict = {}
        #: Zaznaczone momenty bieżącego nagrania: {"czas": s, "opis": ""}.
        self.znaczniki: list = []
        self._stop = threading.Event()
        self._anuluj = threading.Event()
        self._pauza = threading.Event()
        self._watek: Optional[threading.Thread] = None
        self._zdarzenia = ZdarzeniaNagrywania()
        #: Zegar i funkcja czasu bieżącego nagrania — do znaczników.
        self._zegar: Optional[Zegar] = None
        self._teraz: Optional[Callable[[], float]] = None
        #: Pozycja w pliku (s), w której weszła pauza — znacznik w pauzie
        #: wskazuje ją, nie licznik, który idzie z opóźnieniem bufora.
        self._pozycja_pauzy = 0.0
        #: Okno albo ekran nagrywany obok dźwięku (None = sam dźwięk).
        self._zrodlo_obrazu: Optional[wideo.ZrodloObrazu] = None
        #: Wątek składający obraz po zatrzymaniu (patrz czekaj_na_obraz).
        self._skladanie: Optional[threading.Thread] = None
        #: Prośba o inne źródło obrazu w trakcie nagrania (przelacz_obraz).
        self._zmiana_obrazu: Optional[wideo.ZrodloObrazu] = None
        #: Fragment zaczęty „Zaznacz”, jeszcze bez końca (wpis z self.znaczniki).
        self._otwarty: Optional[dict] = None
        #: Treść znacznika `.nagrywanie` (do odzyskania po awarii) — pisana
        #: z wątku nagrywania (urządzenia, obraz) i z okna (zaznaczenia).
        self._stan_pliku: dict = {}
        self._blokada_stanu = threading.Lock()

    # -- sterowanie --------------------------------------------------------

    def start(self, katalog: Path, mikrofon: str = "", glosniki: str = "",
              zdarzenia: Optional[ZdarzeniaNagrywania] = None,
              zrodlo_obrazu: Optional[wideo.ZrodloObrazu] = None) -> Path:
        if self.stan != self.BEZCZYNNA:
            raise BladNagrywania(t("Nagrywanie już trwa."))
        if not dostepne():
            raise BladNagrywania(t(
                "Brak biblioteki do nagrywania (PyAudioWPatch). Uruchom setup.bat."))
        katalog = Path(katalog)
        katalog.mkdir(parents=True, exist_ok=True)
        try:
            wolne = shutil.disk_usage(katalog).free
        except OSError:
            wolne = MIN_WOLNE_B
        if wolne < MIN_WOLNE_B:
            raise BladNagrywania(t(
                "Za mało miejsca na dysku ({mb:.0f} MB wolne). "
                "Godzina nagrania to około 80 MB.").format(mb=wolne / 1e6))
        if zrodlo_obrazu is not None and wolne < wideo.MIN_WOLNE_B:
            raise BladNagrywania(t(
                "Za mało miejsca na dysku na nagranie z obrazem ({mb:.0f} MB wolne, "
                "potrzeba co najmniej 3 GB). Zwolnij miejsce albo w polu „Obraz” "
                "wybierz „Sam dźwięk — bez obrazu”.").format(mb=wolne / 1e6))

        self._zdarzenia = zdarzenia or ZdarzeniaNagrywania()
        self._stop.clear()
        self._anuluj.clear()
        self._pauza.clear()
        self.sciezka = nazwa_pliku(katalog)
        self.czas_trwania = 0.0
        self.znaczniki = []
        self._otwarty = None
        self._stan_pliku = {}
        self._zrodlo_obrazu = zrodlo_obrazu
        self._zmiana_obrazu = None
        self.stan = self.STARTUJE
        self._watek = threading.Thread(
            target=self._praca, args=(mikrofon, glosniki), daemon=True,
            name="nagrywanie")
        self._watek.start()
        return self.sciezka

    def stop(self) -> None:
        if self.stan in (self.STARTUJE, self.NAGRYWA, self.PAUZA):
            self.stan = self.KONCZY
            self._stop.set()

    def anuluj(self) -> None:
        if self.stan in (self.STARTUJE, self.NAGRYWA, self.PAUZA):
            self.stan = self.KONCZY
            self._anuluj.set()
            self._stop.set()

    def pauza(self) -> None:
        """Wstrzymuje nagranie: tory zamknięte, licznik stoi, plik bez luki.

        Wątek roboczy potwierdza przez `zdarzenia.pauza(True)`.
        """
        if self.stan == self.NAGRYWA:
            self._pauza.set()

    def wznow(self) -> None:
        if self.stan == self.PAUZA:
            self._pauza.clear()

    def przelacz_obraz(self, zrodlo: "wideo.ZrodloObrazu") -> None:
        """Obraz z innego okna albo ekranu, bez przerywania nagrania.

        Wątek roboczy zamyka odcinek i otwiera nowy, dźwięk idzie dalej;
        potwierdza przez `zdarzenia.zrodlo_obrazu`. Nagranie bez obrazu —
        nic (obrazu nie da się dołączyć w trakcie).
        """
        if self.stan in (self.NAGRYWA, self.PAUZA):
            self._zmiana_obrazu = zrodlo

    def _teraz_w_pliku(self) -> float:
        czas = self.czas_trwania
        if self.stan == self.PAUZA:
            czas = max(czas, self._pozycja_pauzy)
        elif self._zegar is not None and self._teraz is not None:
            # Dokładniej niż licznik, który idzie z opóźnieniem bufora.
            czas = max(czas, self._zegar.pozycja(self._teraz()) / RATE)
        return round(float(czas), 3)

    def zaznacz(self) -> Optional[dict]:
        """„Zaznacz” (F8): pierwsze naciśnięcie zaczyna fragment, drugie go kończy.

        Zwraca wpis {"czas", "koniec", "opis", "nr", "otwarty"}: `otwarty`
        True — fragment trwa; False — zamknięty (`koniec` None, gdy trwał
        krócej niż MOMENT_S — wtedy to pojedynczy moment). `nr` — numer
        fragmentu od 1 (momenty się nie liczą). None, gdy nic się nie nagrywa.
        """
        # Także w trakcie startu (do kilku sekund: urządzenia, obraz) — wtedy
        # to początek nagrania; inaczej F8 zaraz po „Nagrywaj” ginął bez śladu.
        if self.stan not in (self.STARTUJE, self.NAGRYWA, self.PAUZA):
            return None
        teraz = self._teraz_w_pliku()
        if self._otwarty is None:
            wpis = {"czas": teraz, "koniec": None, "opis": ""}
            self.znaczniki.append(wpis)
            self._otwarty = wpis
            otwarty = True
        else:
            wpis = self._zamknij_fragment(teraz)
            otwarty = False
        self._zapisz_stan(znaczniki=self.znaczniki)
        # Numer jak w transkrypcji (writers): liczą się tylko fragmenty, nie momenty.
        nr = 1 + sum(1 for z in self.znaczniki if z is not wpis and z.get("koniec") is not None)
        return dict(wpis, nr=nr, otwarty=otwarty)

    def _zamknij_fragment(self, koniec: float) -> dict:
        wpis = self._otwarty
        assert wpis is not None
        self._otwarty = None
        wpis["koniec"] = koniec if koniec - wpis["czas"] >= MOMENT_S else None
        return wpis

    def _zapisz_stan(self, **zmiany) -> None:
        """Znacznik `.nagrywanie` od nowa, z tym co wiadomo — z dowolnego wątku."""
        plik = self.sciezka
        if plik is None:
            return
        with self._blokada_stanu:
            self._stan_pliku.update(zmiany)
            try:
                zapisz_znacznik(plik, **self._stan_pliku)
            except (OSError, TypeError, ValueError):
                pass

    def czekaj(self, timeout: Optional[float] = None) -> bool:
        """Czeka na koniec wątku (do zamykania okna). True, gdy skończył."""
        if self._watek is None:
            return True
        self._watek.join(timeout)
        return not self._watek.is_alive()

    @property
    def sklada_obraz(self) -> bool:
        return self._skladanie is not None and self._skladanie.is_alive()

    def czekaj_na_obraz(self, timeout: Optional[float] = None) -> bool:
        """Czeka, aż obraz spotkania zostanie złożony (zamykanie programu).

        Godzina składa się w ~20 s. Gdy czas minie, odcinki i lista zostają
        obok nagrania — `wideo.dokoncz_skladanie` dokończy przy następnym starcie.
        """
        if self._skladanie is None:
            return True
        self._skladanie.join(timeout)
        return not self._skladanie.is_alive()

    def _zloz_obraz(self, obraz: "wideo.ObrazSpotkania", zd: ZdarzeniaNagrywania) -> None:
        znaczniki = [dict(z) for z in self.znaczniki]

        def praca() -> None:
            try:
                cel = obraz.zloz(znaczniki)
            except Exception as exc:
                zd.obraz(None, str(exc))
                return
            if cel is not None:
                zd.obraz(cel, "")

        self._skladanie = threading.Thread(target=praca, daemon=True, name="obraz-skladanie")
        self._skladanie.start()

    @property
    def trwa(self) -> bool:
        return self.stan != self.BEZCZYNNA

    # -- wątek roboczy -----------------------------------------------------

    def _praca(self, mikrofon_nazwa: str, glosniki_nazwa: str) -> None:
        zd = self._zdarzenia
        pa = _pa()
        instancje: list = []
        tory = {}
        cichy = None
        zapis = None
        obraz: Optional[wideo.ObrazSpotkania] = None
        plik = self.sciezka
        assert plik is not None
        try:
            p = pa.PyAudio()
            instancje.append(p)
            wejscia, wyjscia = _lista(p, pa)
            mik = _znajdz(wejscia, mikrofon_nazwa)
            wy = _znajdz(wyjscia, glosniki_nazwa)
            if mik is None and (wy is None or wy.loopback_index is None):
                raise BladNagrywania(t("Nie znaleziono ani mikrofonu, ani urządzenia "
                                       "odtwarzającego dźwięk."))
            if mikrofon_nazwa and (mik is None or mik.nazwa != mikrofon_nazwa):
                zd.ostrzezenie(t("Mikrofonu „{nazwa}” nie ma — używam domyślnego.").format(
                    nazwa=mikrofon_nazwa))
            if glosniki_nazwa and (wy is None or wy.nazwa != glosniki_nazwa):
                zd.ostrzezenie(t("Urządzenia „{nazwa}” nie ma — używam domyślnego.").format(
                    nazwa=glosniki_nazwa))

            # Zegar: domena PortAudio (QPC). perf_counter na Windows to ten
            # sam licznik, więc wystarczy jednorazowe przesunięcie.
            przesuniecie = [0.0]
            teraz = lambda: time.perf_counter() + przesuniecie[0]  # noqa: E731

            if wy is not None and wy.loopback_index is not None:
                # Cisza na głośnikach, żeby loopback nie zasypiał.
                ch, rate = wy.kanaly, wy.rate
                cichy = p.open(
                    format=pa.paFloat32, channels=ch, rate=rate, output=True,
                    output_device_index=wy.index,
                    frames_per_buffer=max(1, rate * BUFOR_PA_MS // 1000),
                    stream_callback=lambda _i, fc, _ti, _st: (bytes(fc * ch * 4), pa.paContinue),
                )
                przesuniecie[0] = float(cichy.get_time()) - time.perf_counter()
            elif wy is not None:
                zd.ostrzezenie(t("To urządzenie nie udostępnia dźwięku systemowego — "
                                 "nagrywam sam mikrofon."))
            # Zegar rusza przed otwarciem torów: pakiety sprzed zera oś czasu
            # odrzuca, a każdy następny ląduje tam, gdzie był naprawdę.
            # Otwarcie mikrofonu potrafi trwać 0,3 s — bez tego loopback
            # byłby na stałe „do przodu” o tyle, ile zebrał w tym czasie.
            zegar: Optional[Zegar] = Zegar(teraz()) if przesuniecie[0] else None
            if cichy is not None:
                tory["system"] = _Tor(p, pa, wy.loopback_index, wy.rate,
                                      2 if wy.kanaly >= 2 else 1, teraz)
            if mik is not None:
                tory["mikrofon"] = _Tor(p, pa, mik.index, mik.rate, min(mik.kanaly, 2), teraz)
                if not przesuniecie[0]:
                    przesuniecie[0] = float(tory["mikrofon"].stream.get_time()) - time.perf_counter()
            else:
                zd.ostrzezenie(t("Brak mikrofonu — nagrywam sam dźwięk systemowy."))
            if zegar is None:
                zegar = Zegar(teraz())
            self._zegar, self._teraz = zegar, teraz
            osie = {k: OsCzasu() for k in ("mikrofon", "system")}
            zrodla = {
                k: Zrodlo(k, t.rate, t.kanaly, zegar, osie[k], t.opoznienie)
                for k, t in tory.items()
            }

            self._zapisz_stan(mikrofon=mik.nazwa if mik else "", system=wy.nazwa if wy else "")
            zapis = ZapisFlac(plik, kanaly=KANALY, tagi=tagi_nagrania(plik))
            #: Na jakim urządzeniu stoi każdy tor — do podążania za domyślnym.
            otwarte = {"mikrofon": mik.nazwa if mik else "", "system": wy.nazwa if wy else ""}
            ustawienia = {"mikrofon": mikrofon_nazwa, "system": glosniki_nazwa}
            self.stan = self.NAGRYWA

            #: Czas QPC (perf_counter) → sekundy pliku: tak obraz spotkania
            #: dostaje miejsce swojej pierwszej klatki (core/wideo.py).
            def pozycja_s(perf: float) -> float:
                return zegar.pozycja(perf + przesuniecie[0]) / RATE

            if self._zrodlo_obrazu is not None:
                sciezka_ff = wideo.ffmpeg()
                if sciezka_ff is None:
                    zd.ostrzezenie(t("Ten ffmpeg nie umie nagrywać okien — nagrywam sam dźwięk."))
                else:
                    obraz = wideo.ObrazSpotkania(
                        self._zrodlo_obrazu, plik, sciezka_ff, ostrzezenie=zd.ostrzezenie,
                        zapisz_stan=lambda odcinki: self._zapisz_stan(obraz=odcinki),
                        zmiana_okna=zd.okno_spotkania)
                    obraz.ruszaj(0.0)

            wydane = 0
            ostatni_fsync = ostatnie_poziomy = ostatni_czas = teraz()
            ostatnia_petla = teraz()
            ostatnie_urzadzenia = teraz()
            martwe: dict = {}
            #: Pauza: kiedy weszła (czas zegara) — przy wznowieniu zegar
            #: przeskakuje o tyle, ile trwała, więc w pliku nie ma luki.
            t_pauzy: Optional[float] = None

            while not self._stop.is_set():
                time.sleep(0.1)
                chwila = teraz()
                if chwila - ostatnia_petla > MAX_LUKA_S and t_pauzy is None:
                    # Komputer spał. Zamiast pół godziny ciszy — sekunda.
                    # Obraz jak przy pauzie: odcinek do zaśnięcia, nowy od teraz.
                    if obraz is not None:
                        obraz.stoj(zegar.pozycja(ostatnia_petla) / RATE)
                    zegar.przesuniecie += (chwila - ostatnia_petla) - 1.0
                    zd.ostrzezenie(t("Komputer był uśpiony — w nagraniu zostaje sekunda przerwy."))
                    if obraz is not None:
                        obraz.ruszaj(zegar.pozycja(chwila) / RATE)
                ostatnia_petla = chwila

                if self._zmiana_obrazu is not None:
                    nowe, self._zmiana_obrazu = self._zmiana_obrazu, None
                    if obraz is not None:
                        obraz.przelacz(nowe, zegar.pozycja(chwila) / RATE,
                                       w_pauzie=t_pauzy is not None)
                        zd.zrodlo_obrazu(nowe)

                if self._pauza.is_set() and t_pauzy is None:
                    # Wejście w pauzę: dobierz resztki, zamknij tory i ciszę.
                    for nazwa, tor in tory.items():
                        tor.zamknij()
                        zrodla[nazwa].przyjmij_wiele((tp, d) for tp, d, _s in tor.zbierz())
                        zrodla[nazwa].zakoncz()
                    if cichy is not None:
                        try:
                            cichy.stop_stream()
                            cichy.close()
                        except Exception:
                            pass
                        cichy = None
                    t_pauzy = chwila
                    self._pozycja_pauzy = zegar.pozycja(chwila) / RATE
                    for os_ in osie.values():
                        os_.utnij(zegar.pozycja(chwila))
                    martwe.clear()
                    self.stan = self.PAUZA
                    zd.poziomy(-120.0, -120.0)
                    zd.pauza(True)
                    if obraz is not None:
                        # Klatki złapane po tej chwili ucina składanie (outpoint).
                        obraz.stoj(self._pozycja_pauzy)
                    continue
                if t_pauzy is not None:
                    if self._pauza.is_set():
                        continue
                    # Wznowienie: zegar przeskakuje o długość pauzy, tory od nowa.
                    zegar.przesuniecie += chwila - t_pauzy
                    t_pauzy = None
                    for nazwa in list(tory):
                        nowy = self._wznow(pa, nazwa, mikrofon_nazwa, glosniki_nazwa, teraz)
                        if nowy is None:
                            zd.ostrzezenie(t(
                                "{kto} nie dał się otworzyć po pauzie — nagrywam dalej bez niego."
                            ).format(kto=t("Mikrofon") if nazwa == "mikrofon" else t("Dźwięk systemowy")))
                            del tory[nazwa]
                            continue
                        p2, tor_nowy, cichy_nowy, urzadzenie = nowy
                        instancje.append(p2)
                        if cichy_nowy is not None:
                            cichy = cichy_nowy
                        tory[nazwa] = tor_nowy
                        otwarte[nazwa] = urzadzenie
                        zrodla[nazwa] = Zrodlo(nazwa, tor_nowy.rate, tor_nowy.kanaly,
                                               zegar, osie[nazwa], tor_nowy.opoznienie)
                    self.stan = self.NAGRYWA
                    zd.pauza(False)
                    if not tory:
                        raise BladNagrywania(t("Po pauzie nie ma z czego nagrywać."))
                    if obraz is not None:
                        obraz.ruszaj(zegar.pozycja(chwila) / RATE)

                if obraz is not None:
                    obraz.dogon(pozycja_s)

                for nazwa, tor in list(tory.items()):
                    zrodla[nazwa].przyjmij_wiele((tp, d) for tp, d, _s in tor.zbierz())
                    if not tor.aktywny() and nazwa not in martwe:
                        martwe[nazwa] = chwila
                        zd.ostrzezenie(t(
                            "{kto} przestał odpowiadać (odłączone urządzenie?) — nagrywam "
                            "dalej to, co zostało, i próbuję wznowić."
                        ).format(kto=t("Mikrofon") if nazwa == "mikrofon" else t("Dźwięk systemowy")))
                    elif nazwa in martwe and chwila - martwe[nazwa] > 2.0:
                        martwe[nazwa] = chwila
                        nowy = self._wznow(pa, nazwa, mikrofon_nazwa, glosniki_nazwa, teraz)
                        if nowy is not None:
                            cichy = self._przepnij(nazwa, nowy, tory, zrodla, osie, otwarte,
                                                   instancje, cichy, zegar)
                            del martwe[nazwa]
                            zd.ostrzezenie(t("{kto} wznowiony.").format(
                                kto=t("Mikrofon") if nazwa == "mikrofon" else t("Dźwięk systemowy")))

                if chwila - ostatnie_urzadzenia >= CO_ILE_URZADZENIA_S:
                    # Podążanie za domyślnym urządzeniem Windows (patrz opis modułu).
                    ostatnie_urzadzenia = chwila
                    punkty = self._punkty()
                    if punkty:
                        zd.urzadzenia(punkty)
                    for nazwa in list(tory):
                        if nazwa in martwe or not punkty:
                            continue
                        domyslne = self._domyslne(punkty, wejscie=(nazwa == "mikrofon"))
                        if not _czy_przepiac(ustawienia[nazwa], otwarte[nazwa], domyslne):
                            continue
                        nowy = self._wznow(pa, nazwa, "", "", teraz)
                        if nowy is None or nowy[3] == otwarte[nazwa]:
                            # PortAudio jeszcze nie widzi zmiany — spróbujemy za chwilę.
                            if nowy is not None:
                                nowy[1].zamknij()
                                self._zamknij_cichy(nowy[2])
                                nowy[0].terminate()
                            continue
                        cichy = self._przepnij(nazwa, nowy, tory, zrodla, osie, otwarte,
                                               instancje, cichy, zegar)
                        zd.ostrzezenie(t("{kto} przełączony na: {urzadzenie}").format(
                            kto=t("Mikrofon") if nazwa == "mikrofon" else t("Dźwięk spotkania"),
                            urzadzenie=otwarte[nazwa]))

                cel = zegar.pozycja(chwila) - int(BUFOR_S * RATE)
                while wydane + BLOK <= cel:
                    zapis.zapisz(przeplot(osie["mikrofon"].pobierz(wydane, BLOK),
                                          osie["system"].pobierz(wydane, BLOK)))
                    wydane += BLOK
                self.czas_trwania = wydane / RATE

                if chwila - ostatnie_poziomy >= 0.1:
                    ostatnie_poziomy = chwila
                    zd.poziomy(zrodla["mikrofon"].rms_dbfs if "mikrofon" in zrodla else -120.0,
                               zrodla["system"].rms_dbfs if "system" in zrodla else -120.0)
                if chwila - ostatni_czas >= 1.0:
                    ostatni_czas = chwila
                    zd.czas(self.czas_trwania)
                if chwila - ostatni_fsync >= 30.0:
                    ostatni_fsync = chwila
                    zapis.fsync()

            # Koniec: zatrzymaj strumienie, dobierz resztki, domknij plik.
            if obraz is not None:
                if self._anuluj.is_set():
                    obraz.porzuc()
                else:
                    obraz.stoj(zegar.pozycja(teraz()) / RATE)
            for tor in tory.values():
                tor.zamknij()
            if cichy is not None:
                cichy.stop_stream()
                cichy.close()
                cichy = None
            if not self._anuluj.is_set():
                for nazwa, tor in tory.items():
                    zrodla[nazwa].przyjmij_wiele((tp, d) for tp, d, _s in tor.zbierz())
                    zrodla[nazwa].zakoncz()
                koniec = max([o.najdalej for o in osie.values()] + [wydane])
                while wydane < koniec:
                    n = min(BLOK, koniec - wydane)
                    zapis.zapisz(przeplot(osie["mikrofon"].pobierz(wydane, n),
                                          osie["system"].pobierz(wydane, n)))
                    wydane += n
                self.czas_trwania = wydane / RATE
                if wydane == 0:
                    # Stop zaraz po starcie — nie ma czego zapisywać.
                    zapis.porzuc()
                    zapis = None
                    if obraz is not None:
                        obraz.porzuc()
                    usun_znacznik(plik)
                    self.stan = self.BEZCZYNNA
                    zd.anulowano()
                    return
                zapis.zamknij()
                zapis = None
                if self._otwarty is not None:
                    # Fragment zaczęty, a nagranie się skończyło — koniec tu.
                    self._zamknij_fragment(self.czas_trwania)
                if self.znaczniki:
                    # Do samego nagrania, nie do pliku obok (od 1.6.1).
                    try:
                        if not dopisz_tagi_flac(plik, {TAG_ZNACZNIKI: znaczniki_do_tagu(self.znaczniki)}):
                            zapisz_znaczniki(plik, self.znaczniki)
                    except OSError as exc:
                        zd.ostrzezenie(t("Nie udało się zapisać zaznaczonych momentów: {blad}")
                                       .format(blad=exc))
                usun_znacznik(plik)
                self.statystyki = {
                    nazwa: {"luki": z.luki, "wyprzedzenia": z.wyprzedzenia,
                            "korekty": z.korekty, "odrzucone": z.os.odrzucone}
                    for nazwa, z in zrodla.items()
                }
                self.stan = self.BEZCZYNNA
                zd.zakonczono(plik, self.czas_trwania)
                if obraz is not None and obraz.odcinki:
                    self._zloz_obraz(obraz, zd)
            else:
                zapis.porzuc()
                zapis = None
                usun_znacznik(plik)
                self.stan = self.BEZCZYNNA
                zd.anulowano()
        except Exception as exc:
            komunikat = str(exc) if isinstance(exc, BladNagrywania) else (
                t("Nagrywanie przerwane: {blad}").format(blad=f"{type(exc).__name__}: {exc}"))
            for tor in tory.values():
                tor.zamknij()
            if cichy is not None:
                try:
                    cichy.stop_stream()
                    cichy.close()
                except Exception:
                    pass
            zachowany = False
            if zapis is not None:
                try:
                    if zapis.probki > 0:
                        zapis.zamknij()
                        zachowany = True
                        if self._otwarty is not None:
                            self._zamknij_fragment(self.czas_trwania)
                        if self.znaczniki and not dopisz_tagi_flac(
                                plik, {TAG_ZNACZNIKI: znaczniki_do_tagu(self.znaczniki)}):
                            zapisz_znaczniki(plik, self.znaczniki)
                        usun_znacznik(plik)
                    else:
                        zapis.porzuc()
                        usun_znacznik(plik)
                except Exception:
                    pass
            if obraz is not None:
                try:
                    obraz.stoj(self.czas_trwania)
                    if zachowany and obraz.odcinki:
                        self._zloz_obraz(obraz, zd)
                    elif not zachowany:
                        obraz.porzuc()
                except Exception:
                    pass
            self.stan = self.BEZCZYNNA
            zd.blad(komunikat)
        finally:
            if obraz is not None:
                # Żaden ffmpeg nie może przeżyć nagrania (składanie ma własny).
                for odcinek in obraz.odcinki:
                    odcinek.zabij()
            self._zegar = self._teraz = None
            for inst in instancje:
                try:
                    inst.terminate()
                except Exception:
                    pass

    @staticmethod
    def _punkty() -> list:
        """Urządzenia Windows (COM) — pusta lista, gdy się nie da."""
        try:
            from . import urzadzenia

            return urzadzenia.punkty()
        except Exception:
            return []

    @staticmethod
    def _domyslne(punkty: list, wejscie: bool) -> str:
        """Nazwa domyślnego (multimedialnego) urządzenia Windows w danym kierunku."""
        for p in punkty:
            if p.wejscie == wejscie and p.domyslne:
                return p.nazwa
        return ""

    @staticmethod
    def _zamknij_cichy(cichy) -> None:
        if cichy is None:
            return
        try:
            cichy.stop_stream()
            cichy.close()
        except Exception:
            pass

    def _przepnij(self, nazwa: str, nowy, tory: dict, zrodla: dict, osie: dict,
                  otwarte: dict, instancje: list, cichy, zegar: Zegar):
        """Stary tor zamknięty, nowy (z `_wznow`) na jego miejsce. Zwraca cichy strumień."""
        p2, tor_nowy, cichy_nowy, urzadzenie = nowy
        instancje.append(p2)
        stary = tory.get(nazwa)
        if stary is not None:
            stary.zamknij()
            zrodla[nazwa].przyjmij_wiele((tp, d) for tp, d, _s in stary.zbierz())
            zrodla[nazwa].zakoncz()
        if cichy_nowy is not None:
            self._zamknij_cichy(cichy)
            cichy = cichy_nowy
        tory[nazwa] = tor_nowy
        otwarte[nazwa] = urzadzenie
        zrodla[nazwa] = Zrodlo(nazwa, tor_nowy.rate, tor_nowy.kanaly,
                               zegar, osie[nazwa], tor_nowy.opoznienie)
        return cichy

    def _wznow(self, pa, nazwa: str, mikrofon_nazwa: str, glosniki_nazwa: str, teraz):
        """Próbuje otworzyć od nowa tor, który padł — na świeżej instancji PyAudio.

        Zwraca (instancja, tor, cichy strumień wyjściowy albo None, nazwa
        urządzenia) lub None.
        """
        p2 = None
        try:
            p2 = pa.PyAudio()
            wejscia, wyjscia = _lista(p2, pa)
            if nazwa == "mikrofon":
                u = _znajdz(wejscia, mikrofon_nazwa)
                if u is None:
                    raise LookupError(nazwa)
                return p2, _Tor(p2, pa, u.index, u.rate, min(u.kanaly, 2), teraz), None, u.nazwa
            u = _znajdz(wyjscia, glosniki_nazwa)
            if u is None or u.loopback_index is None:
                raise LookupError(nazwa)
            ch, rate = u.kanaly, u.rate
            cichy = p2.open(
                format=pa.paFloat32, channels=ch, rate=rate, output=True,
                output_device_index=u.index,
                frames_per_buffer=max(1, rate * BUFOR_PA_MS // 1000),
                stream_callback=lambda _i, fc, _ti, _st: (bytes(fc * ch * 4), pa.paContinue),
            )
            return (p2, _Tor(p2, pa, u.loopback_index, rate, 2 if ch >= 2 else 1, teraz),
                    cichy, u.nazwa)
        except Exception:
            if p2 is not None:
                try:
                    p2.terminate()
                except Exception:
                    pass
            return None
