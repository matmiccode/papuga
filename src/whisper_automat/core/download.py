"""Pobieranie modelu Whispera — z postępem, wznawianiem i sumami kontrolnymi.

Z tego korzysta wersja lekka instalatora, która nie ma modelu w środku:
model (~1,6 GB) ściąga się raz, przy pierwszym uruchomieniu, a potem
program działa bez internetu.

Dlaczego własny kod zamiast pobierania wbudowanego w faster-whisper:
  * postęp — faster-whisper pobiera po cichu, a przy 1,6 GB użytkownik
    musi widzieć, że coś się dzieje i ile jeszcze zostało,
  * wznawianie — zerwane połączenie w 90% nie zaczyna wszystkiego od nowa,
  * pewność — każdy plik jest sprawdzany sumą kontrolną, a do folderu
    modelu trafia dopiero komplet. Niedokończone pobieranie nie może
    udawać gotowego modelu, bo silnik rozpoznaje model po samym model.bin.

Pliki lądują w zwykłym folderze `<katalog modeli>/<nazwa>/` — tym samym,
którego używa model wgrany ręcznie (`engine._reczny_katalog`).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from .engine import Cancelled
from .network import enable_system_certificates, opisz_blad_sieci
from .wydanie import biezace

#: Serwer modeli. HF_ENDPOINT to standardowa zmienna Hugging Face — pozwala
#: wskazać lustro, gdy huggingface.co jest niedostępne.
HUB = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")

#: Repozytoria CTranslate2 — te same, których używa faster-whisper.
REPOZYTORIA = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}

#: Przybliżony rozmiar do pokazania, zanim zapytamy serwer o dokładny.
ROZMIARY_MB = {
    "tiny": 75,
    "base": 145,
    "small": 485,
    "medium": 1530,
    "large-v3": 3090,
    "large-v3-turbo": 1620,
}

#: Pliki, których potrzebuje faster-whisper — reszta repozytorium (README,
#: .gitattributes) nie jest do niczego potrzebna.
POTRZEBNE = ("config.json", "preprocessor_config.json", "model.bin", "tokenizer.json")
PREFIKS_SLOWNIKA = "vocabulary."

#: Sumy SHA-256 policzone na sprawdzonym komputerze. Mają pierwszeństwo przed
#: tym, co poda serwer: podmieniony plik z podmienioną sumą w odpowiedzi API
#: i tak nie przejdzie. Te same wartości są w tools/pobierz_model.ps1.
SUMY = {
    "tiny": {
        "config.json": "a73a28cdfe1c43ccc7202fa333d1f89c202477271407ae9a7f19afa52039cac8",
        "model.bin": "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1",
        "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
        "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
    },
    "large-v3-turbo": {
        "config.json": "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e",
        "model.bin": "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da",
        "preprocessor_config.json": "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
        "tokenizer.json": "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd",
        "vocabulary.json": "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
    },
}

#: Porcja czytana z sieci. 1 MB to rozsądny kompromis między liczbą
#: wywołań a tym, jak szybko reagujemy na „Przerwij”.
PORCJA = 1024 * 1024

#: Ile razy wznawiać jeden plik po zerwanym połączeniu.
PROBY = 5

TIMEOUT_S = 30


class DownloadError(RuntimeError):
    """Nie udało się pobrać modelu — komunikat nadaje się dla użytkownika."""


@dataclass
class Plik:
    nazwa: str
    rozmiar: int
    #: SHA-256 zawartości — dla plików trzymanych w Git LFS (duże pliki).
    sha256: str = ""
    #: Suma git blob (SHA-1) — dla małych plików trzymanych wprost w gicie.
    git_sha1: str = ""


#: (pobrane bajty, wszystkie bajty, prędkość w B/s)
Postep = Callable[[int, int, float], None]


def repozytorium(model: str) -> str:
    try:
        return REPOZYTORIA[model]
    except KeyError:
        raise DownloadError(f"Nieznany model: {model}") from None


def rozmiar_opis(model: str) -> str:
    """„1,6 GB” / „485 MB” — do komunikatów przed pobraniem."""
    mb = ROZMIARY_MB.get(model, 0)
    if mb >= 1000:
        return f"{mb / 1000:.1f} GB".replace(".", ",")
    return f"{mb} MB"


def opis_postepu(co: str, pobrane: int, wszystkie: int, predkosc: float) -> str:
    """„Pobieram model large-v3-turbo — 612 z 1620 MB · 8,4 MB/s · zostało ok. 2 min”.

    `co` to dopełnienie: „model large-v3-turbo”, „wersję 1.2.0”.
    """
    mb = 1024 ** 2
    tekst = f"Pobieram {co} — {pobrane / mb:.0f} z {wszystkie / mb:.0f} MB"
    if predkosc > 0:
        tekst += f" · {predkosc / mb:.1f} MB/s".replace(".", ",")
        zostalo = (wszystkie - pobrane) / predkosc
        if zostalo >= 90:
            tekst += f" · zostało ok. {round(zostalo / 60)} min"
        elif zostalo > 0:
            tekst += f" · zostało ok. {max(5, round(zostalo / 5) * 5)} s"
    return tekst


def katalog_pobierania(models_dir: Path, model: str) -> Path:
    """Miejsce na niedokończone pobieranie — poza folderem modelu."""
    return Path(models_dir) / ".pobieranie" / model


def _zadanie(url: str, naglowki: Optional[dict] = None) -> urllib.request.Request:
    return urllib.request.Request(
        url, headers={"User-Agent": biezace().plik, **(naglowki or {})}
    )


def lista_plikow(model: str) -> List[Plik]:
    """Pyta serwer o pliki modelu. Zestaw różni się między modelami:
    `tiny` ma vocabulary.txt i nie ma preprocessor_config.json, większe
    odwrotnie — dlatego nie zgadujemy."""
    repo = repozytorium(model)
    url = f"{HUB}/api/models/{repo}/tree/main"
    with urllib.request.urlopen(_zadanie(url), timeout=TIMEOUT_S) as odpowiedz:
        wpisy = json.loads(odpowiedz.read().decode("utf-8"))

    znane = SUMY.get(model, {})
    pliki = []
    for wpis in wpisy:
        nazwa = wpis.get("path", "")
        if wpis.get("type") != "file":
            continue
        if nazwa not in POTRZEBNE and not nazwa.startswith(PREFIKS_SLOWNIKA):
            continue
        if "/" in nazwa or "\\" in nazwa or ".." in nazwa:
            # Nazwa ze ścieżką nie jest plikiem modelu, a zapis pod nią
            # mógłby wyjść poza katalog pobierania.
            continue
        lfs = wpis.get("lfs") or {}
        pliki.append(Plik(
            nazwa=nazwa,
            rozmiar=int(lfs.get("size") or wpis.get("size") or 0),
            sha256=znane.get(nazwa) or lfs.get("oid", ""),
            # Poza LFS pole oid to suma git blob samej zawartości pliku.
            # Przy LFS opisuje tylko wskaźnik, więc do niczego się nie nada.
            git_sha1="" if lfs else wpis.get("oid", ""),
        ))

    if not any(p.nazwa == "model.bin" for p in pliki):
        raise DownloadError(
            f"Repozytorium {repo} nie zawiera pliku model.bin — "
            f"serwer zwrócił nieoczekiwaną odpowiedź."
        )
    return pliki


def zgodny(sciezka: Path, plik: Plik) -> bool:
    """Czy plik na dysku jest dokładnie tym, który jest na serwerze."""
    if not sciezka.is_file() or (plik.rozmiar and sciezka.stat().st_size != plik.rozmiar):
        return False
    if plik.sha256:
        h = hashlib.sha256()
        with open(sciezka, "rb") as f:
            for porcja in iter(lambda: f.read(PORCJA * 8), b""):
                h.update(porcja)
        return h.hexdigest() == plik.sha256.lower()
    if plik.git_sha1:
        dane = sciezka.read_bytes()
        h = hashlib.sha1(b"blob %d\0" % len(dane))
        h.update(dane)
        return h.hexdigest() == plik.git_sha1.lower()
    # Bez żadnej sumy zostaje sam rozmiar — sprawdzony wyżej.
    return True


def pobierz_model(
    model: str,
    models_dir: Path,
    on_progress: Optional[Postep] = None,
    log: Optional[Callable[[str], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> Path:
    """Pobiera model do `<models_dir>/<model>/` i zwraca ten folder.

    Przerwane pobieranie zostaje w `.pobieranie/` i przy następnej próbie
    rusza od miejsca, w którym stanęło. Rzuca `DownloadError` (z opisem
    dla użytkownika) albo `Cancelled`.
    """
    log = log or (lambda _m: None)
    cancel = cancel or (lambda: False)
    models_dir = Path(models_dir)
    cel = models_dir / model
    if (cel / "model.bin").is_file():
        return cel

    enable_system_certificates()
    try:
        pliki = lista_plikow(model)
    except DownloadError:
        raise
    except Exception as exc:
        raise DownloadError(_opis_bledu(model, exc)) from exc

    wszystkie = sum(p.rozmiar for p in pliki)
    tymczasowy = katalog_pobierania(models_dir, model)
    tymczasowy.mkdir(parents=True, exist_ok=True)

    juz_jest = sum(_na_dysku(tymczasowy, p) for p in pliki)
    wolne = shutil.disk_usage(tymczasowy).free
    brakuje = wszystkie - juz_jest
    # Zapas na pliki tymczasowe audio, które powstają przy transkrypcji.
    if wolne < brakuje + 512 * 1024 ** 2:
        raise DownloadError(
            f"Za mało miejsca na dysku: model potrzebuje "
            f"{brakuje / 1024 ** 3:.1f} GB, wolne jest {wolne / 1024 ** 3:.1f} GB "
            f"({models_dir.anchor})."
        )

    log(
        f"Pobieram model {model} ({wszystkie / 1024 ** 3:.2f} GB) "
        f"z {HUB.split('://')[-1]}…"
        + (f" Wznawiam — {juz_jest / 1024 ** 2:.0f} MB było już na dysku." if juz_jest >= 1024 ** 2 else "")
    )

    licznik = _Licznik(wszystkie, juz_jest, on_progress)
    for plik in pliki:
        url = f"{HUB}/{repozytorium(model)}/resolve/main/{plik.nazwa}"
        _pobierz_plik(url, plik, tymczasowy, licznik, cancel, log,
                      lambda exc: _opis_bledu(model, exc))

    log("Sprawdzam sumy kontrolne…")
    for plik in pliki:
        if cancel():
            raise Cancelled("Pobieranie przerwane przez użytkownika.")
        if not zgodny(tymczasowy / plik.nazwa, plik):
            (tymczasowy / plik.nazwa).unlink(missing_ok=True)
            raise DownloadError(
                f"Plik {plik.nazwa} różni się od oryginału (niezgodna suma "
                f"kontrolna). Został usunięty — spróbuj pobrać ponownie."
            )

    # Komplet sprawdzony — dopiero teraz staje się modelem widocznym dla
    # silnika. Przeniesienie w obrębie jednego dysku jest natychmiastowe.
    if cel.exists():
        shutil.rmtree(cel, ignore_errors=True)
    os.replace(tymczasowy, cel)
    try:
        tymczasowy.parent.rmdir()
    except OSError:
        pass
    log(f"Model {model} gotowy — kolejne uruchomienia nie potrzebują internetu.")
    return cel


def _na_dysku(katalog: Path, plik: Plik) -> int:
    """Ile bajtów pliku już pobrano (gotowego albo częściowego)."""
    for nazwa in (plik.nazwa, plik.nazwa + ".part"):
        sciezka = katalog / nazwa
        if sciezka.is_file():
            return min(sciezka.stat().st_size, plik.rozmiar or sciezka.stat().st_size)
    return 0


class _Licznik:
    """Zbiera postęp ze wszystkich plików i ogranicza częstotliwość zgłoszeń."""

    def __init__(self, wszystkie: int, start: int, on_progress: Optional[Postep]):
        self.wszystkie = wszystkie
        self.pobrane = start
        self.on_progress = on_progress
        self._ostatnie = 0.0
        # Prędkość liczona z ostatnich kilku sekund, żeby nie skakała.
        self._probki = [(time.monotonic(), start)]

    def dodaj(self, ile: int, wymus: bool = False) -> None:
        self.pobrane += ile
        teraz = time.monotonic()
        if not self.on_progress or (not wymus and teraz - self._ostatnie < 0.25):
            return
        self._ostatnie = teraz
        self._probki.append((teraz, self.pobrane))
        while len(self._probki) > 2 and teraz - self._probki[0][0] > 5:
            self._probki.pop(0)
        t0, b0 = self._probki[0]
        predkosc = (self.pobrane - b0) / (teraz - t0) if teraz > t0 else 0.0
        self.on_progress(self.pobrane, self.wszystkie, predkosc)


def pobierz_plik(
    url: str,
    katalog: Path,
    plik: Plik,
    on_progress: Optional[Postep] = None,
    log: Optional[Callable[[str], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
    co: str = "pliku",
) -> Path:
    """Pobiera jeden plik do `katalog`, ze wznawianiem i sprawdzeniem sumy.

    Tego samego mechanizmu co model używa aktualizacja programu — przerwane
    pobieranie instalatora też rusza od miejsca, w którym stanęło.
    """
    log = log or (lambda _m: None)
    cancel = cancel or (lambda: False)
    katalog = Path(katalog)
    katalog.mkdir(parents=True, exist_ok=True)
    enable_system_certificates()

    licznik = _Licznik(plik.rozmiar, _na_dysku(katalog, plik), on_progress)

    def opis(exc: BaseException) -> str:
        wskazowka = opisz_blad_sieci(exc) or f"{type(exc).__name__}: {exc}"
        return f"Nie udało się pobrać {co}.\n\n{wskazowka}"

    _pobierz_plik(url, plik, katalog, licznik, cancel, log, opis)
    sciezka = katalog / plik.nazwa
    if not zgodny(sciezka, plik):
        sciezka.unlink(missing_ok=True)
        raise DownloadError(
            f"Pobrany plik {plik.nazwa} różni się od oryginału (niezgodna suma "
            f"kontrolna). Został usunięty — spróbuj ponownie."
        )
    return sciezka


def _pobierz_plik(url, plik: Plik, katalog: Path, licznik: _Licznik, cancel, log,
                  opis_bledu: Callable[[BaseException], str]):
    gotowy = katalog / plik.nazwa
    czesc = katalog / (plik.nazwa + ".part")
    if gotowy.is_file() and (not plik.rozmiar or gotowy.stat().st_size == plik.rozmiar):
        return

    for proba in range(1, PROBY + 1):
        if cancel():
            raise Cancelled("Pobieranie przerwane przez użytkownika.")
        mam = czesc.stat().st_size if czesc.is_file() else 0
        try:
            _pobierz_od(url, czesc, mam, plik, licznik, cancel)
            break
        except Cancelled:
            raise
        except DownloadError:
            raise
        except Exception as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 416:
                # Serwer nie uznał zakresu — część na dysku jest do niczego.
                licznik.dodaj(-mam)
                czesc.unlink(missing_ok=True)
            if proba == PROBY or _trwaly(exc):
                raise DownloadError(opis_bledu(exc)) from exc
            log(f"Połączenie przerwane ({type(exc).__name__}) — wznawiam "
                f"{plik.nazwa}, próba {proba + 1} z {PROBY}…")
            time.sleep(min(2 * proba, 10))

    os.replace(czesc, gotowy)
    licznik.dodaj(0, wymus=True)


def _pobierz_od(url, czesc: Path, mam: int, plik: Plik, licznik: _Licznik, cancel):
    if plik.rozmiar and mam >= plik.rozmiar:
        return
    naglowki = {"Range": f"bytes={mam}-"} if mam else {}
    with urllib.request.urlopen(_zadanie(url, naglowki), timeout=TIMEOUT_S) as odp:
        if mam and odp.status != 206:
            # Serwer nie obsłużył wznowienia i wysyła całość — zaczynamy
            # od zera, cofając to, co było już policzone w postępie.
            licznik.dodaj(-mam)
            mam = 0
        with open(czesc, "ab" if mam else "wb") as f:
            while True:
                if cancel():
                    raise Cancelled("Pobieranie przerwane przez użytkownika.")
                porcja = odp.read(PORCJA)
                if not porcja:
                    break
                f.write(porcja)
                licznik.dodaj(len(porcja))

    if plik.rozmiar and czesc.stat().st_size < plik.rozmiar:
        # Serwer zamknął połączenie przed końcem — to przypadek do wznowienia.
        raise ConnectionError("połączenie zakończone przed końcem pliku")
    if plik.rozmiar and czesc.stat().st_size > plik.rozmiar:
        czesc.unlink(missing_ok=True)
        raise DownloadError(
            f"Serwer przysłał więcej danych niż zapowiadał ({plik.nazwa}). "
            f"Spróbuj ponownie za chwilę."
        )


def _trwaly(exc: BaseException) -> bool:
    """Błędy, których ponawianie nic nie da: blokada, brak pliku, certyfikat."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (401, 403, 404, 407, 410, 451)
    tekst = str(exc).lower()
    return "certificate" in tekst or "getaddrinfo" in tekst


def _opis_bledu(model: str, exc: BaseException) -> str:
    wskazowka = opisz_blad_sieci(exc)
    if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 407, 451):
        wskazowka = wskazowka or (
            f"Serwer odmówił dostępu (HTTP {exc.code}). Zwykle znaczy to, że "
            f"huggingface.co blokuje firewall albo filtr treści w sieci. "
            f"W takiej sieci użyj wersji instalatora z modelem w środku "
            f"(„offline”)."
        )
    if not wskazowka:
        wskazowka = f"{type(exc).__name__}: {exc}"
    return f"Nie udało się pobrać modelu {model}.\n\n{wskazowka}"
