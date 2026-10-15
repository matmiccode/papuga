"""Baza głosów pracowników — tylko wydanie firmowe.

Po co: diaryzacja rozdziela głosy w obrębie jednego pliku i numeruje je
MÓWCA 1, MÓWCA 2… — za każdym razem od nowa. Kto wpisał swój głos do bazy,
ten w transkrypcjach kolegów podpisuje się sam: program liczy odcisk głosu
każdego rozpoznanego mówcy i porównuje z bazą, a okno „Kto jest kim?”
dostaje imię jako podpowiedź.

Zasady (decyzja autora 2026-10-15 po analizie prawnej w PROGRESS.md):

* Tylko wydanie firmowe — Papuga nie ma tego modułu w interfejsie
  (`dostepna()` daje False). Odcisk głosu to dane biometryczne (RODO art. 9),
  a rozpoznawanie przez porównanie z bazą to „identyfikacja biometryczna”
  w rozumieniu AI Act; w firmie decyduje inspektor ochrony danych.
* Każdy dodaje wyłącznie **własny** głos, sam, przyciskiem „Dodaj mój głos
  do bazy” — dobrowolnie (Kodeks pracy art. 22^1b: dane biometryczne
  pracownika tylko z jego inicjatywy). Program **nigdy** nie zapisuje do bazy
  głosów rozpoznanych w nagraniach — obcy zostają „MÓWCA N”.
* Do bazy trafia odcisk (wektor liczb), imię, login, komputer i data — nie
  nagranie. Własny wpis można usunąć w każdej chwili („Usuń mój głos”).
* Baza leży w folderze firmowym obok instalatora
  (`<folder aktualizacji>\\Glosy\\<login>.json`, jeden plik na osobę — zapisy
  różnych osób nie kolidują). Brak dostępu do folderu nie psuje transkrypcji:
  podpowiedzi po prostu nie ma, dziennik mówi dlaczego.

Odciski liczy ten sam model, co diaryzacja (`diarization.MODELE["odciski"]`,
3D-Speaker ERes2Net, 512 wymiarów). Wpisy policzone innym modelem (po
podmianie modelu) są pomijane — trzeba dodać głos jeszcze raz.
Podobieństwo: cosinus; próg `PROG` sprawdzony na głosach syntetycznych
(tools/ab_odciski.py zostaje do pomiarów na prawdziwych nagraniach).
"""

from __future__ import annotations

import json
import math
import os
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .wydanie import biezace
from ..teksty import t

WERSJA = 1
#: Minimalne podobieństwo cosinusowe, od którego imię jest podpowiadane.
PROG = 0.55
#: Drugi kandydat nie może być równie dobry — inaczej nie podpowiadamy.
MARGINES = 0.03
#: Tyle mowy (bez ciszy) trzeba, żeby odcisk z próbki coś znaczył.
MIN_PROBKA_S = 8.0
#: Tyle najdłuższych odcinków jednego mówcy bierzemy do odcisku (sekund).
MAX_PROBKA_S = 30.0
#: Ramki cichsze niż to nie wchodzą do odcisku (cisza, oddech).
CISZA_DBFS = -45.0
RAMKA_S = 0.025
ZMIENNA = "WHISPER_AUTOMAT_GLOSY"
PODFOLDER = "Glosy"


class BladBazy(RuntimeError):
    """Baza głosów nie działa (brak dostępu, zły plik, brak modelu)."""


@dataclass
class Glos:
    imie: str
    login: str
    odcisk: List[float]
    model: str = ""
    komputer: str = ""
    data: str = ""
    probka_s: float = 0.0
    plik: Optional[Path] = field(default=None, compare=False)

    def do_json(self) -> dict:
        return {
            "wersja": WERSJA, "imie": self.imie, "login": self.login,
            "komputer": self.komputer, "data": self.data, "model": self.model,
            "probka_s": round(float(self.probka_s), 1),
            "odcisk": [round(float(x), 6) for x in self.odcisk],
        }


# ---------------------------------------------------------------------------
# Folder i pliki
# ---------------------------------------------------------------------------


def katalog() -> Optional[Path]:
    """Folder bazy: zmienna (testy) albo `<folder aktualizacji>\\Glosy`.

    None, gdy wydanie nie jest firmowe albo folder aktualizacji to adres
    http (tam program nie zapisze) lub go nie ma.
    """
    ze_zmiennej = os.environ.get(ZMIENNA, "").strip()
    if ze_zmiennej:
        return Path(ze_zmiennej)
    wyd = biezace()
    if wyd.kod != "firma":
        return None
    folder = (wyd.aktualizacje_folder or "").strip()
    if not folder or folder.lower().startswith(("http://", "https://")):
        return None
    return Path(folder) / PODFOLDER


def dostepna() -> bool:
    """Czy to wydanie ma bazę głosów (nie: czy folder jest w tej chwili osiągalny)."""
    return katalog() is not None


def login() -> str:
    try:
        nazwa = os.getlogin()
    except OSError:
        nazwa = os.environ.get("USERNAME", "")
    return nazwa.strip().lower()


def komputer() -> str:
    return (os.environ.get("COMPUTERNAME") or socket.gethostname() or "").strip().lower()


def nazwa_pliku(login_: str) -> str:
    """`jan.kowalski` → `jan.kowalski.json`; znaki spoza [a-z0-9._-] → `_`."""
    czysty = re.sub(r"[^a-z0-9._-]+", "_", login_.strip().lower()).strip("._") or "glos"
    return f"{czysty}.json"


def moj_plik(folder: Optional[Path] = None) -> Optional[Path]:
    folder = folder if folder is not None else katalog()
    if folder is None:
        return None
    return folder / nazwa_pliku(login())


def model_id() -> str:
    """Identyfikator modelu odcisków — zmiana modelu unieważnia wpisy."""
    from .diarization import SUMY

    return "eres2net-" + SUMY["odciski"][:12]


def wczytaj(folder: Optional[Path] = None, log: Optional[Callable[[str], None]] = None) -> List[Glos]:
    """Wszystkie wpisy z bazy policzone bieżącym modelem. Pusta lista, gdy brak."""
    folder = folder if folder is not None else katalog()
    log = log or (lambda _m: None)
    if folder is None:
        return []
    try:
        pliki = sorted(folder.glob("*.json"))
    except OSError as exc:
        raise BladBazy(t("Folder bazy głosów niedostępny: {blad}").format(blad=exc)) from exc
    model = model_id()
    wynik: List[Glos] = []
    pominiete = 0
    for plik in pliki:
        try:
            dane = json.loads(plik.read_text(encoding="utf-8"))
            glos = Glos(
                imie=str(dane["imie"]).strip(), login=str(dane.get("login", "")).strip().lower(),
                odcisk=[float(x) for x in dane["odcisk"]], model=str(dane.get("model", "")),
                komputer=str(dane.get("komputer", "")), data=str(dane.get("data", "")),
                probka_s=float(dane.get("probka_s", 0.0) or 0.0), plik=plik,
            )
        except (OSError, ValueError, KeyError, TypeError):
            pominiete += 1
            continue
        if not glos.imie or not glos.odcisk or glos.model != model:
            pominiete += 1
            continue
        wynik.append(glos)
    if pominiete:
        log(t("Baza głosów: pominięto {n} wpis(ów) — uszkodzone albo z innego modelu.").format(
            n=pominiete))
    return wynik


def zapisz(glos: Glos, folder: Optional[Path] = None) -> Path:
    """Zapis własnego wpisu (plik tymczasowy + podmiana — nikt nie przeczyta połowy)."""
    folder = folder if folder is not None else katalog()
    if folder is None:
        raise BladBazy(t("To wydanie nie ma bazy głosów."))
    try:
        folder.mkdir(parents=True, exist_ok=True)
        cel = folder / nazwa_pliku(glos.login or login())
        tymczasowy = cel.with_name(cel.name + f".{os.getpid()}.part")
        tymczasowy.write_text(json.dumps(glos.do_json(), ensure_ascii=False, indent=1),
                              encoding="utf-8")
        os.replace(tymczasowy, cel)
    except OSError as exc:
        raise BladBazy(t("Nie udało się zapisać głosu w folderze firmowym: {blad}").format(
            blad=exc)) from exc
    glos.plik = cel
    return cel


def usun_moj(folder: Optional[Path] = None) -> bool:
    plik = moj_plik(folder)
    if plik is None or not plik.is_file():
        return False
    try:
        plik.unlink()
    except OSError as exc:
        raise BladBazy(t("Nie udało się usunąć wpisu: {blad}").format(blad=exc)) from exc
    return True


def moj_wpis(folder: Optional[Path] = None) -> Optional[Glos]:
    plik = moj_plik(folder)
    if plik is None or not plik.is_file():
        return None
    for glos in wczytaj(folder):
        if glos.plik == plik:
            return glos
    return None


# ---------------------------------------------------------------------------
# Odciski
# ---------------------------------------------------------------------------

_ekstraktor = None


def _ekstraktor_glosu():
    """Model odcisków (ten sam plik, co w diaryzacji), ładowany raz."""
    global _ekstraktor
    if _ekstraktor is not None:
        return _ekstraktor
    from . import diarization

    if not diarization.dostepne():
        raise BladBazy(t("Brak biblioteki sherpa-onnx. Uruchom setup.bat, żeby ją doinstalować."))
    diarization.pobierz_modele()
    _segmentacja, odciski = diarization._sciezki()

    import sherpa_onnx

    cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(odciski), num_threads=min(os.cpu_count() or 4, 8))
    if not cfg.validate():
        raise BladBazy(t("Błędna konfiguracja modelu odcisków głosu."))
    _ekstraktor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
    return _ekstraktor


def bez_ciszy(probki, rate: int = 16000):
    """Zostawia tylko ramki, w których ktoś mówi (głośniejsze niż CISZA_DBFS)."""
    import numpy as np

    probki = np.asarray(probki, dtype=np.float32)
    ramka = max(1, int(rate * RAMKA_S))
    n = (probki.size // ramka) * ramka
    if n == 0:
        return probki[:0]
    ramki = probki[:n].reshape(-1, ramka)
    rms = np.sqrt((ramki * ramki).mean(axis=1))
    prog = 10 ** (CISZA_DBFS / 20.0)
    return ramki[rms > prog].reshape(-1)


def odcisk(probki, rate: int = 16000) -> List[float]:
    """Odcisk głosu z próbek float32 [-1, 1] (po `bez_ciszy`), unormowany."""
    import numpy as np

    probki = np.ascontiguousarray(probki, dtype=np.float32)
    if probki.size < rate:
        raise BladBazy(t("Za mało mowy, żeby policzyć odcisk głosu."))
    e = _ekstraktor_glosu()
    strumien = e.create_stream()
    strumien.accept_waveform(rate, probki)
    strumien.input_finished()
    if not e.is_ready(strumien):
        raise BladBazy(t("Za mało mowy, żeby policzyć odcisk głosu."))
    wektor = np.asarray(e.compute(strumien), dtype=np.float64)
    norma = float(np.linalg.norm(wektor))
    if not norma:
        raise BladBazy(t("Model odcisków zwrócił pusty wektor."))
    return [float(x) for x in wektor / norma]


def odcisk_z_wav(wav: Path) -> Tuple[List[float], float]:
    """(odcisk, sekundy mowy) z WAV 16 kHz mono — próbka do rejestracji."""
    from .diarization import _wczytaj_wav

    probki, rate = _wczytaj_wav(Path(wav))
    mowa = bez_ciszy(probki, rate)
    sekundy = float(mowa.size) / rate
    if sekundy < MIN_PROBKA_S:
        raise BladBazy(t("W próbce jest tylko {s:.0f} s mowy — potrzeba co najmniej {min:.0f} s. "
                         "Mów bliżej mikrofonu albo nagraj jeszcze raz.").format(
            s=sekundy, min=MIN_PROBKA_S))
    return odcisk(mowa, rate), sekundy


def podobienstwo(a: List[float], b: List[float]) -> float:
    """Cosinus między odciskami (oba unormowane → iloczyn skalarny)."""
    if len(a) != len(b) or not a:
        return -1.0
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return s / (na * nb)


def dopasuj(odcisk_: List[float], baza: List[Glos], prog: float = PROG,
            margines: float = MARGINES) -> Optional[Tuple[Glos, float]]:
    """Najbliższy wpis z bazy, jeśli wystarczająco podobny i wyraźnie najlepszy."""
    oceny = sorted(((podobienstwo(odcisk_, g.odcisk), g) for g in baza),
                   key=lambda p: p[0], reverse=True)
    if not oceny or oceny[0][0] < prog:
        return None
    if len(oceny) > 1 and oceny[0][0] - oceny[1][0] < margines \
            and oceny[1][1].login != oceny[0][1].login:
        return None
    return oceny[0][1], oceny[0][0]


def rozpoznaj_mowcow(wav: Path, odcinki, log: Optional[Callable[[str], None]] = None,
                     baza: Optional[List[Glos]] = None,
                     licz_odcisk: Optional[Callable] = None) -> Dict[int, Tuple[str, float]]:
    """{numer mówcy: (imię z bazy, podobieństwo)} dla mówców z `odcinki`.

    `wav` to plik 16 kHz mono, na którym liczyła diaryzacja (dla własnego
    nagrania — sam tor rozmówców). Dla każdego mówcy bierze najdłuższe
    odcinki do `MAX_PROBKA_S`, usuwa ciszę i liczy jeden odcisk.
    Nic nie zapisuje do bazy.
    """
    log = log or (lambda _m: None)
    if baza is None:
        baza = wczytaj(log=log)
    if not baza:
        log(t("Baza głosów jest pusta — bez podpowiedzi imion."))
        return {}
    from .diarization import _wczytaj_wav

    import numpy as np

    probki, rate = _wczytaj_wav(Path(wav))
    licz = licz_odcisk or odcisk
    wynik: Dict[int, Tuple[str, float]] = {}
    mowcy = sorted({o.mowca for o in odcinki if o.mowca >= 0})
    for numer in mowcy:
        wybrane = sorted((o for o in odcinki if o.mowca == numer),
                         key=lambda o: o.koniec - o.start, reverse=True)
        kawalki, razem = [], 0.0
        for o in wybrane:
            a, b = int(o.start * rate), int(o.koniec * rate)
            if b <= a:
                continue
            kawalki.append(probki[a:b])
            razem += (b - a) / rate
            if razem >= MAX_PROBKA_S:
                break
        if not kawalki:
            continue
        mowa = bez_ciszy(np.concatenate(kawalki), rate)
        if mowa.size < rate * 2:
            continue
        try:
            trafienie = dopasuj(licz(mowa, rate), baza)
        except BladBazy as exc:
            log(t("Odcisk mówcy {n}: {blad}").format(n=numer + 1, blad=exc))
            continue
        if trafienie is not None:
            glos, ocena = trafienie
            wynik[numer] = (glos.imie, float(ocena))
    if wynik:
        log(t("Z bazy głosów rozpoznano: {kto}").format(
            kto=", ".join(f"{imie} ({ocena:.0%})" for imie, ocena in wynik.values())))
    else:
        log(t("Baza głosów ({n} wpisów): nikogo nie rozpoznano.").format(n=len(baza)))
    return wynik


def nowy_wpis(imie: str, odcisk_: List[float], probka_s: float) -> Glos:
    return Glos(imie=imie.strip(), login=login(), odcisk=odcisk_, model=model_id(),
                komputer=komputer(), data=datetime.now().isoformat(timespec="seconds"),
                probka_s=probka_s)
