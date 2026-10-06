"""Rozpoznawanie mówców — kto co powiedział.

Whisper zamienia mowę na tekst, ale nie odróżnia osób. Robi to osobny
mechanizm (diaryzacja), oparty na dwóch małych modelach ONNX:

  * segmentacja — dzieli nagranie na odcinki, w których ktoś mówi,
  * odciski głosu — opisuje każdy odcinek wektorem, po którym da się
    poznać, że to ten sam głos.

Potem odcinki grupuje się według podobieństwa głosów.

Dwie rzeczy warto wiedzieć, bo obie wyszły z pomiarów:

1. Bez podania liczby uczestników algorytm dzieli głosy zbyt drobno —
   w rozmowie dwóch osób potrafi znaleźć siedem. Dlatego program pyta
   o liczbę osób. Podana liczba jest jednak tylko wskazówką: gdy dwa
   głosy są dla algorytmu nieodróżnialne, i tak skleja je w jeden.
   Zmierzone: przy żądaniu trzech grup zwrócił dwie. Program mówi
   o tym w dzienniku, zamiast udawać, że dostarczył trzy.

2. Karta graficzna tu nie pomaga, choć wydawałoby się inaczej. Model
   segmentacji przetwarza setki drobnych okien, a przy tak małych
   porcjach narzut na przesyłanie danych do karty przewyższa zysk.
   Zmierzone na 10 minutach mowy: procesor 156 s, karta 212 s.
   Dlatego liczymy na procesorze i wprost uprzedzamy o czasie.
"""

from __future__ import annotations

import os
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from .wydanie import biezace

#: Skąd biorą się modele. Repozytoria projektu sherpa-onnx, bez logowania
#: i bez akceptowania licencji — w przeciwieństwie do modeli pyannote.
BAZA = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
MODELE = {
    "segmentacja": (
        f"{BAZA}/speaker-segmentation-models/"
        f"sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
    ),
    "odciski": (
        f"{BAZA}/speaker-recongition-models/"
        f"3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
    ),
}

#: SHA-256 plików modeli, policzone z plików pobranych spod powyższych adresów.
#: Pobrany plik o innej sumie jest usuwany: przychodzi po HTTPS z GitHuba, ale
#: program nie ma uruchamiać na nagraniach użytkownika niczego, czego nie zna.
#: Gdy projekt sherpa-onnx podmieni plik pod tym samym adresem, trzeba
#: sprawdzić nowy i zaktualizować sumę.
SUMY = {
    "odciski": "1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b",
    "segmentacja": "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079",
}

#: Grupa, która w całym nagraniu mówi krócej niż tyle sekund, to nie mówca,
#: tylko nakładające się głosy albo szum. Wypada z wyniku.
MIN_MOWCA_S = 1.0

#: Krótszego kawałka nie wydzielamy w osobny fragment transkrypcji. Granice
#: odcinków są przybliżone i pojedyncze słowo potrafi wpaść do sąsiada.
MIN_WSTAWKA_S = 0.4

#: Ile razy dłużej trwa praca po włączeniu rozpoznawania mówców. Z pomiaru
#: na prawdziwej mowie: około jednej długości nagrania, niezależnie od tego,
#: czy komputer ma kartę graficzną.
KOSZT_WZGLEDEM_NAGRANIA = 1.0


class DiarizationError(RuntimeError):
    """Nie udało się rozpoznać mówców."""


@dataclass
class Odcinek:
    start: float
    koniec: float
    mowca: int


def dostepne() -> bool:
    """Czy biblioteka jest zainstalowana."""
    import importlib.util

    return importlib.util.find_spec("sherpa_onnx") is not None


def katalog_modeli() -> Path:
    from .config import data_root

    katalog = Path(
        os.environ.get("WHISPER_AUTOMAT_DIARIZATION", data_root() / "modele-mowcow")
    )
    katalog.mkdir(parents=True, exist_ok=True)
    return katalog


def _sciezki():
    """(model segmentacji, model odcisków głosu) — mogą jeszcze nie istnieć."""
    from .config import bundled_root

    for baza in (bundled_root() / "modele-mowcow", katalog_modeli()):
        segmentacja = baza / "segmentacja" / "model.onnx"
        odciski = baza / "odciski.onnx"
        if segmentacja.is_file() and odciski.is_file():
            return segmentacja, odciski

    baza = katalog_modeli()
    return baza / "segmentacja" / "model.onnx", baza / "odciski.onnx"


def modele_gotowe() -> bool:
    segmentacja, odciski = _sciezki()
    return segmentacja.is_file() and odciski.is_file()


def pobierz_modele(log: Optional[Callable[[str], None]] = None) -> None:
    """Ściąga oba modele (~44 MB). Nic nie robi, gdy już są."""
    log = log or (lambda _m: None)
    if modele_gotowe():
        return

    import tarfile
    import tempfile
    import urllib.request

    from .network import enable_system_certificates, opisz_blad_sieci

    enable_system_certificates()
    baza = katalog_modeli()

    try:
        odciski = baza / "odciski.onnx"
        if not odciski.is_file():
            log("Pobieram model odcisków głosu (38 MB)…")
            _pobierz(MODELE["odciski"], odciski)
            _sprawdz_sume(odciski, SUMY["odciski"])

        segmentacja = baza / "segmentacja" / "model.onnx"
        if not segmentacja.is_file():
            log("Pobieram model segmentacji (6 MB)…")
            with tempfile.TemporaryDirectory() as tymczasowy:
                archiwum = Path(tymczasowy) / "seg.tar.bz2"
                _pobierz(MODELE["segmentacja"], archiwum)
                segmentacja.parent.mkdir(parents=True, exist_ok=True)
                with tarfile.open(archiwum, "r:bz2") as paczka:
                    for wpis in paczka.getmembers():
                        if wpis.name.endswith("model.onnx"):
                            zrodlo = paczka.extractfile(wpis)
                            if zrodlo is None:
                                continue
                            segmentacja.write_bytes(zrodlo.read())
                            break
                _sprawdz_sume(segmentacja, SUMY["segmentacja"])
    except DiarizationError:
        raise
    except Exception as exc:
        wskazowka = opisz_blad_sieci(exc)
        raise DiarizationError(
            f"Nie udało się pobrać modeli rozpoznawania mówców.\n\n"
            f"{wskazowka or exc}"
        ) from exc

    if not modele_gotowe():
        raise DiarizationError("Pobieranie modeli zakończyło się niekompletnie.")
    log("Modele rozpoznawania mówców gotowe.")


def _sprawdz_sume(plik: Path, oczekiwana: str) -> None:
    import hashlib

    skrot = hashlib.sha256()
    with open(plik, "rb") as f:
        for porcja in iter(lambda: f.read(1 << 20), b""):
            skrot.update(porcja)
    if skrot.hexdigest() != oczekiwana:
        plik.unlink(missing_ok=True)
        raise DiarizationError(
            f"Pobrany plik {plik.name} różni się od oczekiwanego (niezgodna suma "
            f"kontrolna). Został usunięty — spróbuj ponownie."
        )


def _pobierz(url: str, cel: Path) -> None:
    import shutil
    import urllib.request

    cel.parent.mkdir(parents=True, exist_ok=True)
    tymczasowy = cel.with_suffix(cel.suffix + ".czesciowy")
    zadanie = urllib.request.Request(url, headers={"User-Agent": biezace().plik})
    with urllib.request.urlopen(zadanie, timeout=120) as odpowiedz, \
            open(tymczasowy, "wb") as plik:
        shutil.copyfileobj(odpowiedz, plik)
    tymczasowy.replace(cel)


def _wczytaj_wav(sciezka: Path):
    with wave.open(str(sciezka)) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise DiarizationError(
                "Oczekiwano 16-bitowego WAV mono — plik nie przeszedł konwersji."
            )
        czestotliwosc = w.getframerate()
        ramki = w.readframes(w.getnframes())

    import numpy as np

    return np.frombuffer(ramki, dtype=np.int16).astype(np.float32) / 32768.0, czestotliwosc


def rozpoznaj_mowcow(
    audio: Path,
    ilu_mowcow: int = 0,
    on_progress: Optional[Callable[[float], None]] = None,
    log: Optional[Callable[[str], None]] = None,
) -> List[Odcinek]:
    """Dzieli nagranie na odcinki i przypisuje im numery mówców.

    `ilu_mowcow` równe zeru oznacza „zgadnij" — działa gorzej, więc program
    pyta użytkownika o tę liczbę.
    """
    log = log or (lambda _m: None)
    if not dostepne():
        raise DiarizationError(
            "Brak biblioteki sherpa-onnx. Uruchom setup.bat, żeby ją doinstalować."
        )

    pobierz_modele(log)
    segmentacja, odciski = _sciezki()

    import sherpa_onnx

    watki = min(os.cpu_count() or 4, 16)
    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(segmentacja)
            ),
            num_threads=watki,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(odciski), num_threads=watki
        ),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=ilu_mowcow if ilu_mowcow > 0 else -1,
            threshold=0.5,
        ),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not cfg.validate():
        raise DiarizationError("Błędna konfiguracja rozpoznawania mówców.")

    try:
        silnik = sherpa_onnx.OfflineSpeakerDiarization(cfg)
    except Exception as exc:
        raise DiarizationError(f"Nie udało się uruchomić: {exc}") from exc

    probki, czestotliwosc = _wczytaj_wav(audio)
    if czestotliwosc != silnik.sample_rate:
        raise DiarizationError(
            f"Oczekiwano {silnik.sample_rate} Hz, plik ma {czestotliwosc} Hz."
        )

    def postep(przetworzone: int, wszystkie: int) -> int:
        if on_progress and wszystkie:
            on_progress(min(przetworzone / wszystkie, 1.0))
        return 0

    try:
        wynik = silnik.process(probki, callback=postep).sort_by_start_time()
    except TypeError:
        # Starsze wydania nie przyjmują callbacku postępu.
        wynik = silnik.process(probki).sort_by_start_time()
    except Exception as exc:
        raise DiarizationError(f"Rozpoznawanie mówców nie powiodło się: {exc}") from exc

    odcinki = [Odcinek(s.start, s.end, s.speaker) for s in wynik]

    odcinki, szum = odrzuc_szum(odcinki)
    if szum:
        log(
            f"Pominięto {szum} grup(y) o łącznym czasie mowy poniżej "
            f"{MIN_MOWCA_S:g} s — to zwykle nakładające się głosy albo szum, "
            f"nie osobny mówca."
        )
    return przenumeruj(odcinki)


def odrzuc_szum(odcinki: List[Odcinek]):
    """Usuwa grupy, które w sumie mówią krócej niż `MIN_MOWCA_S`.

    Diaryzacja potrafi wydzielić osobną „osobę" z nakładających się głosów
    albo z szumu. W oknie wyglądało to na trzeciego uczestnika, który nie
    powiedział ani słowa — i tak właśnie zostało zgłoszone.

    Zwraca (odcinki, ile grup odrzucono).
    """
    czas = {}
    for odcinek in odcinki:
        czas[odcinek.mowca] = czas.get(odcinek.mowca, 0.0) + (
            odcinek.koniec - odcinek.start
        )
    prawdziwi = {m for m, suma in czas.items() if suma >= MIN_MOWCA_S}
    if not prawdziwi:
        # Całe nagranie krótsze niż próg — lepiej oddać, co jest.
        return odcinki, 0
    return [o for o in odcinki if o.mowca in prawdziwi], len(czas) - len(prawdziwi)


def przenumeruj(odcinki: List[Odcinek]) -> List[Odcinek]:
    """Numeruje mówców w kolejności pierwszego wystąpienia.

    Dwa powody. sherpa-onnx zwraca numery grup z drzewa scalania, więc
    bywają dziury: przy dwóch rozpoznanych głosach numery wychodzą 0 i 2 —
    w oknie wyglądałoby to na brakującego MÓWCĘ 2. A poza tym kolejność
    grup jest przypadkowa: MÓWCA 1 bywał tym, kto zabrał głos jako drugi,
    co w transkrypcji wygląda dokładnie jak pomylone osoby.
    """
    pierwsze = {}
    for odcinek in odcinki:
        if odcinek.mowca < 0:
            continue
        if odcinek.start < pierwsze.get(odcinek.mowca, float("inf")):
            pierwsze[odcinek.mowca] = odcinek.start

    mapowanie = {
        stary: nowy
        for nowy, stary in enumerate(sorted(pierwsze, key=lambda m: pierwsze[m]))
    }
    for odcinek in odcinki:
        if odcinek.mowca in mapowanie:
            odcinek.mowca = mapowanie[odcinek.mowca]
    return odcinki


# ---------------------------------------------------------------------------
# Przypisanie tekstu do mówców
# ---------------------------------------------------------------------------


def _dominujacy(start: float, koniec: float, odcinki: List[Odcinek]) -> int:
    """Numer mówcy, który zajmuje najwięcej z przedziału. -1, gdy żaden."""
    czasy = {}
    for odcinek in odcinki:
        wspolne = min(koniec, odcinek.koniec) - max(start, odcinek.start)
        if wspolne > 0:
            czasy[odcinek.mowca] = czasy.get(odcinek.mowca, 0.0) + wspolne
    return max(czasy, key=czasy.get) if czasy else -1


def _biegi_slow(slowa, odcinki: List[Odcinek], zapasowy: int):
    """Dzieli słowa na kolejne serie [numer mówcy, słowa]."""
    biegi = []
    poprzedni = zapasowy
    for slowo in slowa:
        mowca = _dominujacy(slowo.start, slowo.end, odcinki)
        if mowca < 0:
            # Oddech albo cisza między odcinkami: zostaw przy tym, kto mówił.
            mowca = poprzedni
        if biegi and biegi[-1][0] == mowca:
            biegi[-1][1].append(slowo)
        else:
            biegi.append([mowca, [slowo]])
        poprzedni = mowca
    return _scal_krotkie(biegi)


def _scal_krotkie(biegi):
    """Skleja z sąsiadem serie krótsze niż `MIN_WSTAWKA_S`.

    Granice odcinków są przybliżone, więc pojedyncze słowo potrafi wpaść do
    sąsiedniej osoby. Cięcie transkrypcji na takich ułamkach sekundy daje
    sieczkę, nie informację.
    """
    i = 0
    while len(biegi) > 1 and i < len(biegi):
        slowa = biegi[i][1]
        if slowa[-1].end - slowa[0].start >= MIN_WSTAWKA_S:
            i += 1
            continue
        if i > 0:
            biegi[i - 1][1].extend(slowa)
            del biegi[i]
            i -= 1
        else:
            biegi[1][1][:0] = slowa
            del biegi[0]

    # Po sklejaniu sąsiedzi mogą mieć tego samego mówcę.
    scalone = []
    for mowca, slowa in biegi:
        if scalone and scalone[-1][0] == mowca:
            scalone[-1][1].extend(slowa)
        else:
            scalone.append([mowca, slowa])
    return scalone


def przypisz(segmenty, odcinki: List[Odcinek]) -> list:
    """Nadaje segmentom numery mówców, dzieląc te, w których mówca się zmienia.

    Whisper zwraca segmenty nawet po kilkanaście sekund i nie wie o mówcach
    nic. Przypisanie całego segmentu jednej osobie — tej, która mówiła
    w nim najdłużej — gubi wszystkie pozostałe: krótkie wtrącenia znikają,
    a bywa i tak, że osoba nie wygrywa ani jednego segmentu i w transkrypcji
    nie ma jej wcale. Zgłoszone z pierwszego prawdziwego nagrania.

    Gdy są znaczniki słów, segment dzieli się po granicy słowa. Bez nich
    zostaje stare przypisanie całości — lepsze to niż nic.

    Zwraca nową listę segmentów; wejściowa nie jest zachowana.
    """
    from dataclasses import replace

    wynik = []
    for segment in segmenty:
        dominujacy = _dominujacy(segment.start, segment.end, odcinki)
        slowa = getattr(segment, "words", None) or []

        if not slowa:
            segment.speaker = dominujacy
            wynik.append(segment)
            continue

        biegi = _biegi_slow(slowa, odcinki, dominujacy)
        if len(biegi) <= 1:
            # Cały segment należy do jednej osoby — nie ruszaj znaczników.
            segment.speaker = biegi[0][0] if biegi else dominujacy
            wynik.append(segment)
            continue

        czesci = []
        for mowca, slowa_biegu in biegi:
            tekst = "".join(s.text for s in slowa_biegu).strip()
            if not tekst:
                continue
            czesci.append(
                replace(
                    segment,
                    start=slowa_biegu[0].start,
                    end=slowa_biegu[-1].end,
                    text=tekst,
                    words=list(slowa_biegu),
                    speaker=mowca,
                )
            )
        if czesci:
            wynik.extend(czesci)
        else:
            segment.speaker = dominujacy
            wynik.append(segment)

    return wynik


def etykieta(numer: int, nazwy: Optional[dict] = None) -> str:
    """Nazwa mówcy: wpisana przez użytkownika albo domyślna."""
    if numer < 0:
        return "?"
    if nazwy:
        wlasna = nazwy.get(numer)
        if wlasna:
            return wlasna
    return f"MÓWCA {numer + 1}"
