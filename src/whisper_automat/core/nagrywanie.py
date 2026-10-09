"""Nagrywanie spotkań: mikrofon i dźwięk systemowy prosto do pliku FLAC.

Co powstaje: jeden plik `Spotkanie RRRR-MM-DD GG-MM.flac`, 16 kHz, mono,
16 bitów — dokładnie to, co i tak dostaje model (media.extract_audio robi
z każdego wejścia 16 kHz mono), więc bezstratnie i bez marnowania miejsca:
około 50 MB na godzinę mowy, kodowanie poniżej 1 % jednego rdzenia.

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
* Dwa zegary: mikrofon i głośniki mają osobne kwarce, a każdy z nich dryfuje
  względem zegara systemowego. Każde źródło kładzie swoje próbki na wspólną
  oś czasu (OsCzasu) według czasu, który PortAudio podał przy pakiecie
  (time_info, liczone natywnie — nie cierpi na opóźnienia GIL). Luki
  dostają ciszę, dryf jest korygowany tylko w ciszy, małymi krokami.

Odporność: FLAC jest pisany na bieżąco, ramka po ramce; plik urwany przez
awarię jest czytelny dla ffmpeg (brak tylko łącznej długości w nagłówku).
Obok pliku leży znacznik `.nagrywanie` — jeśli program startuje i znajduje
znacznik bez żywego procesu, nagranie da się odzyskać (napraw_nagranie).
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
ROZSZERZENIE = ".flac"
ZNACZNIK = ".nagrywanie"


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
        reszta = self._resample(None)
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
    """Suma obu torów (nie średnia — strony rzadko mówią naraz), miękki limiter."""
    y = m.astype(np.float32) / 32768.0 + s.astype(np.float32) / 32768.0
    if y.size and float(np.max(np.abs(y))) > 0.99:
        y = np.tanh(y)
    return np.clip(np.rint(y * 32768.0), -32768, 32767).astype(np.int16)


class ZapisFlac:
    """FLAC 16 kHz mono pisany na bieżąco, własnym uchwytem pliku (fsync)."""

    def __init__(self, sciezka: Path):
        import av

        self.sciezka = Path(sciezka)
        self._plik = open(self.sciezka, "wb", buffering=0)
        self._kont = av.open(self._plik, "w", format="flac")
        self._st = self._kont.add_stream("flac", rate=RATE)
        self._st.layout = "mono"
        self._st.format = "s16"
        self._av = av
        self._pts = 0
        self.probki = 0

    def zapisz(self, s16: np.ndarray) -> None:
        if s16.size == 0:
            return
        r = self._av.AudioFrame.from_ndarray(
            np.ascontiguousarray(s16).reshape(1, -1), format="s16", layout="mono")
        r.sample_rate = RATE
        r.pts = self._pts
        r.time_base = Fraction(1, RATE)
        self._pts += int(s16.size)
        self.probki += int(s16.size)
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
    `<nazwa> (odzyskane).flac`, usuwa uszkodzony plik i znacznik.
    """
    import av

    plik = Path(plik)
    cel = plik.with_name(f"{plik.stem} (odzyskane){plik.suffix}")
    licznik = 2
    while cel.exists():
        cel = plik.with_name(f"{plik.stem} (odzyskane) ({licznik}){plik.suffix}")
        licznik += 1

    zapis = ZapisFlac(cel)
    res = av.AudioResampler(format="s16", layout="mono", rate=RATE)
    try:
        with av.open(str(plik)) as zrodlo:
            strumien = zrodlo.streams.audio[0]
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
        zapis.porzuc()
        raise
    try:
        plik.unlink()
    except OSError:
        pass
    usun_znacznik(plik)
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

    BEZCZYNNA, STARTUJE, NAGRYWA, KONCZY = "bezczynna", "startuje", "nagrywa", "konczy"

    def __init__(self) -> None:
        self.stan = self.BEZCZYNNA
        self.sciezka: Optional[Path] = None
        self.czas_trwania = 0.0
        #: Po zakończeniu: luki, obcięcia, korekty dryfu i odrzucone próbki
        #: na każde źródło — do dziennika i diagnostyki.
        self.statystyki: dict = {}
        self._stop = threading.Event()
        self._anuluj = threading.Event()
        self._watek: Optional[threading.Thread] = None
        self._zdarzenia = ZdarzeniaNagrywania()

    # -- sterowanie --------------------------------------------------------

    def start(self, katalog: Path, mikrofon: str = "", glosniki: str = "",
              zdarzenia: Optional[ZdarzeniaNagrywania] = None) -> Path:
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
                "Godzina nagrania to około 50 MB.").format(mb=wolne / 1e6))

        self._zdarzenia = zdarzenia or ZdarzeniaNagrywania()
        self._stop.clear()
        self._anuluj.clear()
        self.sciezka = nazwa_pliku(katalog)
        self.czas_trwania = 0.0
        self.stan = self.STARTUJE
        self._watek = threading.Thread(
            target=self._praca, args=(mikrofon, glosniki), daemon=True,
            name="nagrywanie")
        self._watek.start()
        return self.sciezka

    def stop(self) -> None:
        if self.stan in (self.STARTUJE, self.NAGRYWA):
            self.stan = self.KONCZY
            self._stop.set()

    def anuluj(self) -> None:
        if self.stan in (self.STARTUJE, self.NAGRYWA):
            self.stan = self.KONCZY
            self._anuluj.set()
            self._stop.set()

    def czekaj(self, timeout: Optional[float] = None) -> bool:
        """Czeka na koniec wątku (do zamykania okna). True, gdy skończył."""
        if self._watek is None:
            return True
        self._watek.join(timeout)
        return not self._watek.is_alive()

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
            osie = {k: OsCzasu() for k in ("mikrofon", "system")}
            zrodla = {
                k: Zrodlo(k, t.rate, t.kanaly, zegar, osie[k], t.opoznienie)
                for k, t in tory.items()
            }

            zapisz_znacznik(plik, mikrofon=mik.nazwa if mik else "",
                            system=wy.nazwa if wy else "")
            zapis = ZapisFlac(plik)
            self.stan = self.NAGRYWA

            wydane = 0
            ostatni_fsync = ostatnie_poziomy = ostatni_czas = teraz()
            ostatnia_petla = teraz()
            martwe: dict = {}

            while not self._stop.is_set():
                time.sleep(0.1)
                chwila = teraz()
                if chwila - ostatnia_petla > MAX_LUKA_S:
                    # Komputer spał. Zamiast pół godziny ciszy — sekunda.
                    zegar.przesuniecie += (chwila - ostatnia_petla) - 1.0
                    zd.ostrzezenie(t("Komputer był uśpiony — w nagraniu zostaje sekunda przerwy."))
                ostatnia_petla = chwila

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
                            p2, tor_nowy, cichy_nowy = nowy
                            instancje.append(p2)
                            tor.zamknij()
                            if cichy_nowy is not None:
                                if cichy is not None:
                                    try:
                                        cichy.stop_stream()
                                        cichy.close()
                                    except Exception:
                                        pass
                                cichy = cichy_nowy
                            tory[nazwa] = tor_nowy
                            zrodla[nazwa] = Zrodlo(nazwa, tor_nowy.rate, tor_nowy.kanaly,
                                                   zegar, osie[nazwa], tor_nowy.opoznienie)
                            del martwe[nazwa]
                            zd.ostrzezenie(t("{kto} wznowiony.").format(
                                kto=t("Mikrofon") if nazwa == "mikrofon" else t("Dźwięk systemowy")))

                cel = zegar.pozycja(chwila) - int(BUFOR_S * RATE)
                while wydane + BLOK <= cel:
                    zapis.zapisz(miksuj(osie["mikrofon"].pobierz(wydane, BLOK),
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
                    zapis.zapisz(miksuj(osie["mikrofon"].pobierz(wydane, n),
                                        osie["system"].pobierz(wydane, n)))
                    wydane += n
                self.czas_trwania = wydane / RATE
                if wydane == 0:
                    # Stop zaraz po starcie — nie ma czego zapisywać.
                    zapis.porzuc()
                    zapis = None
                    usun_znacznik(plik)
                    self.stan = self.BEZCZYNNA
                    zd.anulowano()
                    return
                zapis.zamknij()
                zapis = None
                usun_znacznik(plik)
                self.statystyki = {
                    nazwa: {"luki": z.luki, "wyprzedzenia": z.wyprzedzenia,
                            "korekty": z.korekty, "odrzucone": z.os.odrzucone}
                    for nazwa, z in zrodla.items()
                }
                self.stan = self.BEZCZYNNA
                zd.zakonczono(plik, self.czas_trwania)
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
            if zapis is not None:
                try:
                    if zapis.probki > 0:
                        zapis.zamknij()
                        usun_znacznik(plik)
                    else:
                        zapis.porzuc()
                        usun_znacznik(plik)
                except Exception:
                    pass
            self.stan = self.BEZCZYNNA
            zd.blad(komunikat)
        finally:
            for inst in instancje:
                try:
                    inst.terminate()
                except Exception:
                    pass

    def _wznow(self, pa, nazwa: str, mikrofon_nazwa: str, glosniki_nazwa: str, teraz):
        """Próbuje otworzyć od nowa tor, który padł — na świeżej instancji PyAudio.

        Zwraca (instancja, tor, cichy strumień wyjściowy albo None) lub None.
        """
        p2 = None
        try:
            p2 = pa.PyAudio()
            wejscia, wyjscia = _lista(p2, pa)
            if nazwa == "mikrofon":
                u = _znajdz(wejscia, mikrofon_nazwa)
                if u is None:
                    raise LookupError(nazwa)
                return p2, _Tor(p2, pa, u.index, u.rate, min(u.kanaly, 2), teraz), None
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
            return p2, _Tor(p2, pa, u.loopback_index, rate, 2 if ch >= 2 else 1, teraz), cichy
        except Exception:
            if p2 is not None:
                try:
                    p2.terminate()
                except Exception:
                    pass
            return None
