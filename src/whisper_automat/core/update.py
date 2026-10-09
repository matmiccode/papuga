"""Nowe wersje programu: z wydań (Releases) na GitHubie albo z folderu firmowego.

Źródło wybiera wydanie: Papuga ma repozytorium (`wydanie.repo`) i pyta
API GitHuba; Whisper Automat ma folder (`wydanie.aktualizacje_folder` —
udział sieciowy w firmie albo adres http) i czyta z niego `najnowsza.json`.
Wydanie bez jednego i drugiego nigdy tu nie zagląda. Mechanizm dalej jest
jeden: ten sam pasek w oknie, to samo sprawdzenie podpisu Ed25519, ten
sam cichy instalator. Z GitHuba do firmy nie trafia nic — adresy, klucz
i nazwy biorą się z `Wydanie`, nie z kodu.

Przebieg:
  1. `sprawdz()` pyta API GitHuba o najnowsze wydanie (bez logowania; limit
     60 zapytań na godzinę z jednego adresu, a program pyta raz na dobę),
  2. `pobierz()` ściąga instalator tym samym mechanizmem co model — ze
     wznawianiem i sprawdzeniem SHA-256,
  3. `uruchom_instalator()` odpala go w trybie cichym i program się zamyka.
     Instalator ma ten sam AppId, więc instaluje się na wierzchu, a model
     i ustawienia w profilu użytkownika zostają nietknięte.

Instalator bez sumy kontrolnej nie zostanie uruchomiony. Wydanie z kluczem
publicznym (`Wydanie.klucz_publiczny`) bierze sumę wyłącznie z podpisanego
pliku `<instalator>.podpis` (core/podpis.py) — brak podpisu albo zły podpis
oznacza, że program nie zainstaluje wydania sam, tylko otworzy jego stronę.
Pole `digest`, które GitHub sam liczy, służy wtedy za dodatkowe sprawdzenie.
Bez klucza suma pochodzi z `digest` albo z pliku `<instalator>.sha256`.

Instalator pobierany jest tylko z adresu strony wydań własnego repozytorium
(albo magazynu plików GitHuba, do którego ta strona przekierowuje) — adres
z odpowiedzi API, który prowadzi gdzie indziej, jest odrzucany.

Folder firmowy: `najnowsza.json` ({"wersja", "instalator", "rozmiar",
"opis"}) obok instalatora i pliku `<instalator>.podpis` (pisze je
tools/publikuj_wydanie.py --wydanie firma). Udział sieciowy bywa
zapisywalny dla wielu osób, więc z folderu program instaluje WYŁĄCZNIE
wydania podpisane kluczem autora — bez furtki `.sha256`. Niedostępny
folder (laptop poza firmą) to zwykły błąd sprawdzania: ręczne go pokaże,
automatyczne zapisze w dzienniku.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple
from urllib.parse import urlparse

from ..teksty import t
from . import podpis as podpisy
from .download import DownloadError, Plik, Postep, kopiuj_plik, pobierz_plik
from .network import enable_system_certificates, opisz_blad_sieci
from .wydanie import Wydanie, biezace

API = "https://api.github.com"

#: Opis najnowszego wydania w folderze firmowym.
MANIFEST = "najnowsza.json"

#: Jak często sprawdzać przy starcie. Doba to dość, żeby nowa wersja
#: dotarła szybko, i dość rzadko, żeby nikt nie poczuł, że program „dzwoni”.
CO_ILE_S = 20 * 3600

TIMEOUT_S = 10

#: Skąd wolno pobierać pliki wydania: strona wydań GitHuba i magazyn plików,
#: do którego przekierowuje. Nic innego, nawet gdy API tak każe.
HOSTY_PLIKOW = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


class UpdateError(RuntimeError):
    """Nie udało się sprawdzić albo pobrać aktualizacji."""


@dataclass
class Aktualizacja:
    wersja: str
    #: Opis wydania z GitHuba (Markdown) — pokazywany w „Co nowego”.
    opis: str
    #: Strona wydania — dla tych, którzy wolą pobrać ręcznie.
    strona: str
    #: Bezpośredni adres instalatora i jego opis (nazwa, rozmiar, SHA-256).
    url: str = ""
    instalator: Optional[Plik] = None
    #: Czy suma pochodzi z podpisu sprawdzonego kluczem programu.
    podpisana: bool = False
    #: Dlaczego program nie zainstaluje tego wydania sam (gdy nie może).
    uwaga: str = ""


def wersja_jako_krotka(tekst: str) -> Tuple[int, ...]:
    """„v1.2.10” -> (1, 2, 10). Przyrostki typu „-beta” są pomijane."""
    liczby = re.findall(r"\d+", (tekst or "").split("-")[0])
    return tuple(int(x) for x in liczby[:4]) or (0,)


def nowsza(kandydat: str, obecna: str) -> bool:
    a, b = wersja_jako_krotka(kandydat), wersja_jako_krotka(obecna)
    dlugosc = max(len(a), len(b))
    return a + (0,) * (dlugosc - len(a)) > b + (0,) * (dlugosc - len(b))


def pora_sprawdzic(ostatnio: float, teraz: Optional[float] = None) -> bool:
    teraz = time.time() if teraz is None else teraz
    # Zegar cofnięty (ostatnio w przyszłości) też jest powodem, żeby sprawdzić.
    return not ostatnio or teraz - ostatnio >= CO_ILE_S or ostatnio > teraz


def _get(url: str, accept: str = "application/vnd.github+json") -> bytes:
    zadanie = urllib.request.Request(url, headers={
        "Accept": accept,
        "User-Agent": biezace().plik,
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(zadanie, timeout=TIMEOUT_S) as odpowiedz:
        return odpowiedz.read()


def adres_z_wydan(url: str, wydanie: Wydanie) -> bool:
    """Czy adres prowadzi do pliku wydania naszego repozytorium przez HTTPS."""
    try:
        czesci = urlparse(url)
    except ValueError:
        return False
    if czesci.scheme != "https" or not czesci.hostname or czesci.hostname not in HOSTY_PLIKOW:
        return False
    if czesci.hostname == "github.com":
        return czesci.path.lower().startswith(f"/{wydanie.repo.lower()}/releases/download/")
    return True


def _wybierz_instalator(zalaczniki: List[dict], wydanie: Wydanie) -> Optional[dict]:
    """Instalator lekkiej wersji: <Plik>-<wersja>-Setup.exe.

    Warianty -cpu i -offline są pomijane — aktualizacja ma przynieść ten sam
    wariant, który ludzie pobierają ze strony.
    """
    wzor = re.compile(
        rf"^{re.escape(wydanie.plik)}-[\d.]+-Setup\.exe$", re.IGNORECASE
    )
    for zalacznik in zalaczniki:
        if wzor.match(zalacznik.get("name", "")):
            return zalacznik
    return None


def _digest(instalator: dict) -> str:
    """SHA-256 policzony przez GitHub (pole `digest`) albo pusty napis."""
    digest = str(instalator.get("digest") or "")
    if digest.lower().startswith("sha256:"):
        return digest.split(":", 1)[1].strip().lower()
    return ""


def _zalacznik(zalaczniki: List[dict], nazwa: str, wydanie: Wydanie) -> Optional[str]:
    """Adres załącznika o danej nazwie — tylko ze strony wydań."""
    for zalacznik in zalaczniki:
        if zalacznik.get("name") == nazwa:
            url = str(zalacznik.get("browser_download_url") or "")
            return url if adres_z_wydan(url, wydanie) else None
    return None


def _suma(instalator: dict, zalaczniki: List[dict], wydanie: Wydanie) -> str:
    """Suma bez podpisu: `digest` GitHuba albo plik `<instalator>.sha256`."""
    suma = _digest(instalator)
    if suma:
        return suma
    url = _zalacznik(zalaczniki, instalator.get("name", "") + ".sha256", wydanie)
    if url:
        tekst = _get(url, "application/octet-stream")
        pierwsze = tekst.decode("utf-8", "replace").strip().split()
        if pierwsze and re.fullmatch(r"[0-9a-fA-F]{64}", pierwsze[0]):
            return pierwsze[0].lower()
    return ""


def _suma_podpisana(instalator: dict, zalaczniki: List[dict], wydanie: Wydanie) -> str:
    """Suma z pliku `<instalator>.podpis` po sprawdzeniu podpisu kluczem programu.

    Gdy GitHub podaje własny `digest`, musi się zgadzać z podpisanym —
    różnica znaczy, że plik na serwerze nie jest tym, który autor podpisał.
    """
    nazwa = str(instalator.get("name") or "")
    url = _zalacznik(zalaczniki, nazwa + ".podpis", wydanie)
    if not url:
        raise podpisy.BladPodpisu(t(
            "Wydanie nie ma pliku podpisu (.podpis), więc program nie zainstaluje go sam."
        ))
    tekst = _get(url, "application/octet-stream").decode("utf-8", "replace")
    suma = podpisy.zweryfikuj_wydanie(tekst, wydanie.klucz_publiczny, nazwa)
    digest = _digest(instalator)
    if digest and digest != suma:
        raise podpisy.BladPodpisu(t(
            "Suma instalatora policzona przez GitHub różni się od podpisanej przez autora."
        ))
    return suma


def sprawdz(obecna: str, wydanie: Optional[Wydanie] = None) -> Optional[Aktualizacja]:
    """Najnowsze wydanie, jeśli jest nowsze od `obecna`. Inaczej None.

    Rzuca UpdateError, gdy nie udało się zapytać — wywołujący decyduje,
    czy to pokazać (ręczne sprawdzenie), czy przemilczeć (sprawdzenie
    w tle przy starcie).
    """
    wydanie = wydanie or biezace()
    if wydanie.repo:
        return _sprawdz_github(obecna, wydanie)
    if wydanie.aktualizacje_folder:
        return _sprawdz_folder(obecna, wydanie)
    return None


def _sprawdz_github(obecna: str, wydanie: Wydanie) -> Optional[Aktualizacja]:
    """Najnowsze wydanie z API GitHuba (Papuga)."""
    enable_system_certificates()
    try:
        dane = json.loads(_get(f"{API}/repos/{wydanie.repo}/releases/latest"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # Repozytorium bez żadnego wydania (albo jeszcze nie istnieje).
            return None
        if exc.code == 403:
            raise UpdateError(t(
                "GitHub chwilowo ograniczył liczbę zapytań. Spróbuj za godzinę."
            )) from exc
        raise UpdateError(
            t("GitHub odpowiedział błędem HTTP {kod}.").format(kod=exc.code)
        ) from exc
    except Exception as exc:
        raise UpdateError(
            opisz_blad_sieci(exc) or f"{type(exc).__name__}: {exc}"
        ) from exc

    wersja = str(dane.get("tag_name", "")).lstrip("vV")
    if not wersja or not nowsza(wersja, obecna):
        return None

    strona = str(dane.get("html_url") or "")
    if not strona.lower().startswith(f"https://github.com/{wydanie.repo.lower()}/"):
        strona = wydanie.strona_url
    akt = Aktualizacja(
        wersja=wersja,
        opis=str(dane.get("body") or "").strip(),
        strona=strona,
    )

    zalaczniki = dane.get("assets") or []
    instalator = _wybierz_instalator(zalaczniki, wydanie)
    if not instalator:
        akt.uwaga = t("Wydanie nie zawiera instalatora.")
        return akt

    suma = ""
    try:
        if wydanie.klucz_publiczny:
            suma = _suma_podpisana(instalator, zalaczniki, wydanie)
            akt.podpisana = True
        else:
            suma = _suma(instalator, zalaczniki, wydanie)
            if not suma:
                akt.uwaga = t("Wydanie nie ma sumy kontrolnej instalatora.")
    except Exception as exc:
        suma, akt.podpisana = "", False
        akt.uwaga = str(exc) or type(exc).__name__

    akt.url = str(instalator.get("browser_download_url") or "")
    if not adres_z_wydan(akt.url, wydanie):
        akt.uwaga = t("Adres instalatora nie prowadzi do strony wydań tego programu.")
        akt.url = ""
    akt.instalator = Plik(
        nazwa=str(instalator.get("name") or ""),
        rozmiar=int(instalator.get("size") or 0),
        sha256=suma,
    )
    return akt


def _jest_url(sciezka: str) -> bool:
    return sciezka.lower().startswith(("http://", "https://"))


def _w_folderze(folder: str, nazwa: str) -> str:
    """Adres pliku w folderze wydań — URL albo ścieżka (także UNC)."""
    if _jest_url(folder):
        return folder.rstrip("/") + "/" + nazwa
    return str(Path(folder) / nazwa)


def _czytaj_z_folderu(folder: str, nazwa: str) -> bytes:
    """Treść małego pliku z folderu wydań. FileNotFoundError, gdy go nie ma."""
    if _jest_url(folder):
        try:
            return _get(_w_folderze(folder, nazwa), "application/octet-stream")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FileNotFoundError(nazwa) from exc
            raise
    return Path(_w_folderze(folder, nazwa)).read_bytes()


def _nazwa_instalatora_ok(nazwa: str, wydanie: Wydanie) -> bool:
    """`<Plik>-<wersja>-Setup(-cpu)(-offline).exe`, bez ścieżki.

    Z folderu firmowego idzie ten wariant, który tam leży (zwykle offline,
    z modelem w środku) — inaczej niż z GitHuba, gdzie liczy się tylko lekki.
    """
    return bool(re.fullmatch(
        rf"{re.escape(wydanie.plik)}-[\d.]+-Setup(-cpu)?(-offline)?\.exe",
        nazwa, re.IGNORECASE,
    ))


def _sprawdz_folder(obecna: str, wydanie: Wydanie) -> Optional[Aktualizacja]:
    """Najnowsze wydanie z folderu firmowego (`najnowsza.json`).

    Folder niedostępny (laptop poza siecią firmową) to UpdateError — ręczne
    sprawdzenie powie o tym wprost, automatyczne zapisze w dzienniku. Folder
    pusty (nic jeszcze nie opublikowano) to po prostu brak nowej wersji.
    """
    folder = wydanie.aktualizacje_folder
    if _jest_url(folder):
        enable_system_certificates()
    elif not Path(folder).is_dir():
        # Windows zgłasza niedostępny udział tak samo jak brak pliku, więc
        # sprawdzamy sam folder, zanim spytamy o plik.
        raise UpdateError(t(
            "Folder z aktualizacjami jest niedostępny (poza siecią firmową?):\n{folder}"
        ).format(folder=folder))
    try:
        dane = json.loads(_czytaj_z_folderu(folder, MANIFEST).decode("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise UpdateError(
            t("Nie udało się odczytać {plik} z folderu aktualizacji.").format(plik=MANIFEST)
            + f"\n{type(exc).__name__}: {exc}"
        ) from exc
    except Exception as exc:
        raise UpdateError(opisz_blad_sieci(exc) or f"{type(exc).__name__}: {exc}") from exc
    if not isinstance(dane, dict):
        raise UpdateError(
            t("Plik {plik} w folderze aktualizacji ma zły format.").format(plik=MANIFEST)
        )

    wersja = str(dane.get("wersja", "")).lstrip("vV")
    if not wersja or not nowsza(wersja, obecna):
        return None
    akt = Aktualizacja(
        wersja=wersja,
        opis=str(dane.get("opis") or "").strip(),
        strona=folder,
    )

    nazwa = str(dane.get("instalator") or "")
    if not _nazwa_instalatora_ok(nazwa, wydanie):
        akt.uwaga = t("Wpis o wydaniu nie wskazuje instalatora tego programu.")
        return akt

    # Udział sieciowy bywa zapisywalny dla wielu osób, więc tu nie ma drogi
    # „sama suma wystarczy” — tylko podpis kluczem autora.
    suma = ""
    try:
        if not wydanie.klucz_publiczny:
            raise podpisy.BladPodpisu(t(
                "Program nie ma klucza do sprawdzania podpisów wydań."
            ))
        try:
            tekst = _czytaj_z_folderu(folder, nazwa + ".podpis").decode("utf-8", "replace")
        except FileNotFoundError:
            raise podpisy.BladPodpisu(t(
                "Wydanie nie ma pliku podpisu (.podpis), więc program nie zainstaluje go sam."
            )) from None
        suma = podpisy.zweryfikuj_wydanie(tekst, wydanie.klucz_publiczny, nazwa)
        akt.podpisana = True
    except Exception as exc:
        suma, akt.podpisana = "", False
        akt.uwaga = str(exc) or type(exc).__name__

    akt.url = _w_folderze(folder, nazwa)
    akt.instalator = Plik(
        nazwa=nazwa, rozmiar=int(dane.get("rozmiar") or 0), sha256=suma,
    )
    return akt


def katalog_pobran() -> Path:
    from .config import data_root

    return data_root() / ".cache" / "aktualizacje"


def mozna_zainstalowac(akt: Aktualizacja, wydanie: Optional[Wydanie] = None) -> bool:
    """Czy da się zaktualizować z programu: instalator, suma i — gdy wydanie
    ma klucz — podpis sprawdzony tym kluczem."""
    wydanie = wydanie or biezace()
    if not (akt.url and akt.instalator and akt.instalator.sha256):
        return False
    return akt.podpisana or not wydanie.klucz_publiczny


def pobierz(
    akt: Aktualizacja,
    on_progress: Optional[Postep] = None,
    log: Optional[Callable[[str], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> Path:
    if not mozna_zainstalowac(akt):
        raise DownloadError(
            (akt.uwaga or t("To wydanie nie ma instalatora ze sprawdzalną sumą kontrolną."))
            + " " + t("Program nie zainstaluje go sam — pobierz je ze strony wydania.")
        )
    co = t("wersji {wersja}").format(wersja=akt.wersja)
    if _jest_url(akt.url):
        return pobierz_plik(
            akt.url, katalog_pobran(), akt.instalator,
            on_progress=on_progress, log=log, cancel=cancel, co=co,
        )
    # Folder firmowy: zwykła kopia z udziału, z tym samym sprawdzeniem sumy.
    return kopiuj_plik(
        Path(akt.url), katalog_pobran(), akt.instalator,
        on_progress=on_progress, log=log, cancel=cancel, co=co,
    )


def uruchom_instalator(sciezka: Path) -> None:
    """Startuje instalator w trybie cichym, niezależnie od programu.

    /SILENT pokazuje sam pasek postępu — bez kreatora, ale człowiek widzi,
    że coś się dzieje. /AKTUALIZACJA=1 każe instalatorowi uruchomić program
    po skończonej pracy (patrz installer.iss).
    """
    flagi = 0
    if os.name == "nt":
        # Instalator ma przeżyć zamknięcie programu, który go uruchomił.
        flagi = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    # Wersja okienkowa nie ma prawidłowych strumieni — bez przekierowania
    # Popen przewraca się na „uchwyt jest nieprawidłowy”.
    subprocess.Popen(
        # /CLOSEAPPLICATIONS — gdyby program nie zdążył się zamknąć, instalator
        # zamknie go sam zamiast pytać o pliki w użyciu.
        [str(sciezka), "/SILENT", "/NORESTART", "/CLOSEAPPLICATIONS", "/AKTUALIZACJA=1"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flagi,
        close_fds=True,
    )


def posprzataj(obecna: str) -> None:
    """Usuwa instalatory wersji, które są już zainstalowane (albo starsze)."""
    katalog = katalog_pobran()
    if not katalog.is_dir():
        return
    for plik in katalog.iterdir():
        dopasowanie = re.search(r"-(\d+(?:\.\d+)+)-Setup\.exe(\.part)?$", plik.name)
        if dopasowanie and not nowsza(dopasowanie.group(1), obecna):
            try:
                plik.unlink()
            except OSError:
                pass
