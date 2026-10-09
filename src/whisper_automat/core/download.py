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

from ..teksty import jezyk, t
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
        raise DownloadError(t("Nieznany model: {model}").format(model=model)) from None


def _ulamek(tekst: str) -> str:
    """Przecinek dziesiętny po polsku („1,6 GB”), kropka po angielsku („1.6 GB”)."""
    return tekst if jezyk() == "en" else tekst.replace(".", ",")


def rozmiar_opis(model: str) -> str:
    """„1,6 GB” / „485 MB” — do komunikatów przed pobraniem."""
    mb = ROZMIARY_MB.get(model, 0)
    if mb >= 1000:
        return _ulamek(f"{mb / 1000:.1f} GB")
    return f"{mb} MB"


def opis_postepu(co: str, pobrane: int, wszystkie: int, predkosc: float) -> str:
    """„Pobieram model large-v3-turbo — 612 z 1620 MB · 8,4 MB/s · zostało ok. 2 min”.

    `co` to dopełnienie: „model large-v3-turbo”, „wersję 1.2.0”.
    """
    mb = 1024 ** 2
    tekst = t("Pobieram {co} — {pobrane:.0f} z {wszystkie:.0f} MB").format(
        co=co, pobrane=pobrane / mb, wszystkie=wszystkie / mb
    )
    if predkosc > 0:
        tekst += " · " + _ulamek(f"{predkosc / mb:.1f} MB/s")
        zostalo = (wszystkie - pobrane) / predkosc
        if zostalo >= 90:
            tekst += " · " + t("zostało ok. {n} min").format(n=round(zostalo / 60))
        elif zostalo > 0:
            tekst += " · " + t("zostało ok. {n} s").format(n=max(5, round(zostalo / 5) * 5))
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
        raise DownloadError(t(
            "Repozytorium {repo} nie zawiera pliku model.bin — "
            "serwer zwrócił nieoczekiwaną odpowiedź."
        ).format(repo=repo))
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
        raise DownloadError(t(
            "Za mało miejsca na dysku: model potrzebuje "
            "{brakuje:.1f} GB, wolne jest {wolne:.1f} GB "
            "({dysk})."
        ).format(brakuje=brakuje / 1024 ** 3, wolne=wolne / 1024 ** 3, dysk=models_dir.anchor))

    log(
        t("Pobieram model {model} ({gb:.2f} GB) z {serwer}…").format(
            model=model, gb=wszystkie / 1024 ** 3, serwer=HUB.split("://")[-1]
        )
        + (
            " " + t("Wznawiam — {mb:.0f} MB było już na dysku.").format(mb=juz_jest / 1024 ** 2)
            if juz_jest >= 1024 ** 2 else ""
        )
    )

    licznik = _Licznik(wszystkie, juz_jest, on_progress)
    for plik in pliki:
        url = f"{HUB}/{repozytorium(model)}/resolve/main/{plik.nazwa}"
        _pobierz_plik(url, plik, tymczasowy, licznik, cancel, log,
                      lambda exc: _opis_bledu(model, exc))

    log(t("Sprawdzam sumy kontrolne…"))
    for plik in pliki:
        if cancel():
            raise Cancelled(t("Pobieranie przerwane przez użytkownika."))
        if not zgodny(tymczasowy / plik.nazwa, plik):
            (tymczasowy / plik.nazwa).unlink(missing_ok=True)
            raise DownloadError(t(
                "Plik {plik} różni się od oryginału (niezgodna suma "
                "kontrolna). Został usunięty — spróbuj pobrać ponownie."
            ).format(plik=plik.nazwa))

    # Komplet sprawdzony — dopiero teraz staje się modelem widocznym dla
    # silnika. Przeniesienie w obrębie jednego dysku jest natychmiastowe.
    if cel.exists():
        shutil.rmtree(cel, ignore_errors=True)
    os.replace(tymczasowy, cel)
    try:
        tymczasowy.parent.rmdir()
    except OSError:
        pass
    log(t("Model {model} gotowy — kolejne uruchomienia nie potrzebują internetu.").format(
        model=model
    ))
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
    co: str = "",
) -> Path:
    """Pobiera jeden plik do `katalog`, ze wznawianiem i sprawdzeniem sumy.

    Tego samego mechanizmu co model używa aktualizacja programu — przerwane
    pobieranie instalatora też rusza od miejsca, w którym stanęło.
    `co` to dopełnienie do komunikatów („wersji 1.2.0”); puste = „pliku”.
    """
    log = log or (lambda _m: None)
    cancel = cancel or (lambda: False)
    co = co or t("pliku")
    katalog = Path(katalog)
    katalog.mkdir(parents=True, exist_ok=True)
    enable_system_certificates()

    licznik = _Licznik(plik.rozmiar, _na_dysku(katalog, plik), on_progress)

    def opis(exc: BaseException) -> str:
        wskazowka = opisz_blad_sieci(exc) or f"{type(exc).__name__}: {exc}"
        return t("Nie udało się pobrać {co}.").format(co=co) + "\n\n" + wskazowka

    _pobierz_plik(url, plik, katalog, licznik, cancel, log, opis)
    sciezka = katalog / plik.nazwa
    if not zgodny(sciezka, plik):
        sciezka.unlink(missing_ok=True)
        raise DownloadError(t(
            "Pobrany plik {plik} różni się od oryginału (niezgodna suma "
            "kontrolna). Został usunięty — spróbuj ponownie."
        ).format(plik=plik.nazwa))
    return sciezka


def kopiuj_plik(
    zrodlo: Path,
    katalog: Path,
    plik: Plik,
    on_progress: Optional[Postep] = None,
    log: Optional[Callable[[str], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
    co: str = "",
) -> Path:
    """Kopiuje plik z dysku albo udziału sieciowego do `katalog`, z postępem
    i sprawdzeniem sumy — odpowiednik `pobierz_plik` dla folderu firmowego
    z aktualizacjami (core/update.py). Kopia idzie do `<nazwa>.part`,
    nazwę docelową dostaje dopiero cała i zgodna z sumą.
    """
    cancel = cancel or (lambda: False)
    co = co or t("pliku")
    zrodlo, katalog = Path(zrodlo), Path(katalog)
    katalog.mkdir(parents=True, exist_ok=True)
    gotowy = katalog / plik.nazwa
    if gotowy.is_file() and zgodny(gotowy, plik):
        return gotowy
    czesc = katalog / (plik.nazwa + ".part")
    try:
        rozmiar = plik.rozmiar or zrodlo.stat().st_size
        licznik = _Licznik(rozmiar, 0, on_progress)
        with open(zrodlo, "rb") as we, open(czesc, "wb") as wy:
            while True:
                if cancel():
                    raise Cancelled(t("Pobieranie przerwane przez użytkownika."))
                porcja = we.read(PORCJA * 8)
                if not porcja:
                    break
                wy.write(porcja)
                licznik.dodaj(len(porcja))
        licznik.dodaj(0, wymus=True)
    except Cancelled:
        czesc.unlink(missing_ok=True)
        raise
    except OSError as exc:
        czesc.unlink(missing_ok=True)
        raise DownloadError(
            t("Nie udało się skopiować {co} z {folder}.").format(co=co, folder=zrodlo.parent)
            + f"\n\n{type(exc).__name__}: {exc}"
        ) from exc
    os.replace(czesc, gotowy)
    if not zgodny(gotowy, plik):
        gotowy.unlink(missing_ok=True)
        raise DownloadError(t(
            "Skopiowany plik {plik} różni się od oryginału (niezgodna suma "
            "kontrolna). Został usunięty — spróbuj ponownie."
        ).format(plik=plik.nazwa))
    return gotowy


def _pobierz_plik(url, plik: Plik, katalog: Path, licznik: _Licznik, cancel, log,
                  opis_bledu: Callable[[BaseException], str]):
    gotowy = katalog / plik.nazwa
    czesc = katalog / (plik.nazwa + ".part")
    if gotowy.is_file() and (not plik.rozmiar or gotowy.stat().st_size == plik.rozmiar):
        return

    for proba in range(1, PROBY + 1):
        if cancel():
            raise Cancelled(t("Pobieranie przerwane przez użytkownika."))
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
            log(t(
                "Połączenie przerwane ({blad}) — wznawiam {plik}, próba {proba} z {prob}…"
            ).format(blad=type(exc).__name__, plik=plik.nazwa, proba=proba + 1, prob=PROBY))
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
                    raise Cancelled(t("Pobieranie przerwane przez użytkownika."))
                porcja = odp.read(PORCJA)
                if not porcja:
                    break
                f.write(porcja)
                licznik.dodaj(len(porcja))

    if plik.rozmiar and czesc.stat().st_size < plik.rozmiar:
        # Serwer zamknął połączenie przed końcem — to przypadek do wznowienia.
        raise ConnectionError(t("połączenie zakończone przed końcem pliku"))
    if plik.rozmiar and czesc.stat().st_size > plik.rozmiar:
        czesc.unlink(missing_ok=True)
        raise DownloadError(t(
            "Serwer przysłał więcej danych niż zapowiadał ({plik}). "
            "Spróbuj ponownie za chwilę."
        ).format(plik=plik.nazwa))


def _trwaly(exc: BaseException) -> bool:
    """Błędy, których ponawianie nic nie da: blokada, brak pliku, certyfikat."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (401, 403, 404, 407, 410, 451)
    tekst = str(exc).lower()
    return "certificate" in tekst or "getaddrinfo" in tekst


def _opis_bledu(model: str, exc: BaseException) -> str:
    wskazowka = opisz_blad_sieci(exc)
    if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 407, 451):
        wskazowka = wskazowka or t(
            "Serwer odmówił dostępu (HTTP {kod}). Zwykle znaczy to, że "
            "huggingface.co blokuje firewall albo filtr treści w sieci. "
            "W takiej sieci użyj wersji instalatora z modelem w środku "
            "(„offline”)."
        ).format(kod=exc.code)
    if not wskazowka:
        wskazowka = f"{type(exc).__name__}: {exc}"
    return t("Nie udało się pobrać modelu {model}.").format(model=model) + "\n\n" + wskazowka
