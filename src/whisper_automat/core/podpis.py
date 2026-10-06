"""Podpis cyfrowy wydań — Ed25519 w czystym Pythonie.

Po co: program pobiera z GitHuba instalator nowej wersji i go uruchamia.
Suma SHA-256 chroni przed uszkodzonym pobraniem, ale nie przed podmianą
pliku przez kogoś, kto przejął konto GitHub. Podpis kluczem, którego
połówka publiczna jest wbudowana w program (`Wydanie.klucz_publiczny`),
sprawia, że samo konto nie wystarczy — trzeba mieć też klucz prywatny,
który leży wyłącznie na komputerze autora (patrz tools/klucz_wydan.py).

Dlaczego własna implementacja: w paczce nie ma biblioteki `cryptography`
(dokładałaby kilkanaście MB i zależność od OpenSSL), a Ed25519 to algorytm
na tyle prosty, że RFC 8032 podaje kod referencyjny w Pythonie. Ten moduł
jest jego odpowiednikiem w współrzędnych rozszerzonych (szybsze mnożenie
punktu); sprawdzenie podpisu trwa ułamek sekundy. Zgodność z RFC 8032
potwierdzają wektory testowe w tests/test_podpis.py.

Plik podpisu wydania (`<instalator>.podpis`) to tekst:

    papuga-podpis 1
    plik: Papuga-1.1.1-Setup.exe
    sha256: <64 znaki szesnastkowe>
    podpis: <base64, 64 bajty>

Podpisane są pierwsze trzy linie — nazwa pliku i suma. Program najpierw
sprawdza podpis, a dopiero potem pobiera instalator i porównuje jego sumę
z podpisaną. Bez prawidłowego podpisu nie uruchamia niczego.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

# --- arytmetyka krzywej (RFC 8032, §5.1) ------------------------------------

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)

#: Punkt w współrzędnych rozszerzonych (X, Y, Z, T), gdzie x = X/Z, y = Y/Z, T = XY/Z.
Punkt = Tuple[int, int, int, int]


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _odzyskaj_x(y: int, znak: int) -> Optional[int]:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if znak else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _I % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != znak:
        x = _P - x
    return x


_GY = 4 * _inv(5) % _P
_GX = _odzyskaj_x(_GY, 0)
_G: Punkt = (_GX, _GY, 1, _GX * _GY % _P)
_ZERO: Punkt = (0, 1, 1, 0)


def _dodaj(p: Punkt, q: Punkt) -> Punkt:
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = 2 * t1 * t2 * _D % _P
    d = 2 * z1 * z2 % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mnoz(s: int, p: Punkt) -> Punkt:
    wynik = _ZERO
    while s > 0:
        if s & 1:
            wynik = _dodaj(wynik, p)
        p = _dodaj(p, p)
        s >>= 1
    return wynik


def _rowne(p: Punkt, q: Punkt) -> bool:
    x1, y1, z1, _ = p
    x2, y2, z2, _ = q
    return (x1 * z2 - x2 * z1) % _P == 0 and (y1 * z2 - y2 * z1) % _P == 0


def _spakuj(p: Punkt) -> bytes:
    zinv = _inv(p[2])
    x, y = p[0] * zinv % _P, p[1] * zinv % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _rozpakuj(dane: bytes) -> Optional[Punkt]:
    if len(dane) != 32:
        return None
    y = int.from_bytes(dane, "little")
    znak = y >> 255
    y &= (1 << 255) - 1
    x = _odzyskaj_x(y, znak)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _sha512_mod_l(*czesci: bytes) -> int:
    return int.from_bytes(hashlib.sha512(b"".join(czesci)).digest(), "little") % _L


def _rozwin(klucz_prywatny: bytes) -> Tuple[int, bytes]:
    if len(klucz_prywatny) != 32:
        raise ValueError("Klucz prywatny Ed25519 ma 32 bajty.")
    skrot = hashlib.sha512(klucz_prywatny).digest()
    a = int.from_bytes(skrot[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, skrot[32:]


# --- klucze i podpisy -------------------------------------------------------


def nowy_klucz() -> bytes:
    """Losowy klucz prywatny (32 bajty)."""
    return secrets.token_bytes(32)


def klucz_publiczny(klucz_prywatny: bytes) -> bytes:
    a, _ = _rozwin(klucz_prywatny)
    return _spakuj(_mnoz(a, _G))


def podpisz(klucz_prywatny: bytes, wiadomosc: bytes) -> bytes:
    """Podpis Ed25519 (64 bajty) wiadomości."""
    a, prefiks = _rozwin(klucz_prywatny)
    publiczny = _spakuj(_mnoz(a, _G))
    r = _sha512_mod_l(prefiks, wiadomosc)
    rb = _spakuj(_mnoz(r, _G))
    h = _sha512_mod_l(rb, publiczny, wiadomosc)
    s = (r + h * a) % _L
    return rb + s.to_bytes(32, "little")


def sprawdz(klucz_publiczny_: bytes, wiadomosc: bytes, podpis: bytes) -> bool:
    """Czy `podpis` jest prawidłowym podpisem `wiadomosc` dla tego klucza."""
    if len(klucz_publiczny_) != 32 or len(podpis) != 64:
        return False
    a = _rozpakuj(klucz_publiczny_)
    r = _rozpakuj(podpis[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(podpis[32:], "little")
    if s >= _L:
        return False
    h = _sha512_mod_l(podpis[:32], klucz_publiczny_, wiadomosc)
    return _rowne(_mnoz(s, _G), _dodaj(r, _mnoz(h, a)))


# --- plik podpisu wydania ---------------------------------------------------

NAGLOWEK = "papuga-podpis 1"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class BladPodpisu(ValueError):
    """Podpis wydania jest nieprawidłowy albo nie do odczytania."""


@dataclass(frozen=True)
class Podpis:
    plik: str
    sha256: str
    podpis: bytes


def tresc_do_podpisu(plik: str, sha256: str) -> bytes:
    return f"{NAGLOWEK}\nplik: {plik}\nsha256: {sha256.lower()}\n".encode("utf-8")


def zapisz_podpis(plik: str, sha256: str, klucz_prywatny: bytes) -> str:
    """Treść pliku `<instalator>.podpis`."""
    if not _HEX64.match(sha256.lower()):
        raise ValueError("Suma SHA-256 ma mieć 64 znaki szesnastkowe.")
    if "\n" in plik or not plik.strip():
        raise ValueError("Nieprawidłowa nazwa pliku.")
    tresc = tresc_do_podpisu(plik, sha256)
    podpis = podpisz(klucz_prywatny, tresc)
    return tresc.decode("utf-8") + f"podpis: {base64.b64encode(podpis).decode('ascii')}\n"


def odczytaj_podpis(tekst: str) -> Podpis:
    pola = {}
    linie = [linia.strip() for linia in tekst.strip().splitlines() if linia.strip()]
    if not linie or linie[0] != NAGLOWEK:
        raise BladPodpisu("To nie jest plik podpisu wydania (brak nagłówka).")
    for linia in linie[1:]:
        klucz, sep, wartosc = linia.partition(":")
        if not sep:
            raise BladPodpisu(f"Niezrozumiała linia w pliku podpisu: {linia[:60]}")
        pola[klucz.strip().lower()] = wartosc.strip()
    plik, suma, podpis64 = pola.get("plik", ""), pola.get("sha256", "").lower(), pola.get("podpis", "")
    if not plik or not _HEX64.match(suma) or not podpis64:
        raise BladPodpisu("W pliku podpisu brakuje nazwy pliku, sumy albo podpisu.")
    try:
        podpis = base64.b64decode(podpis64, validate=True)
    except (ValueError, TypeError) as exc:
        raise BladPodpisu("Podpis nie jest poprawnym base64.") from exc
    if len(podpis) != 64:
        raise BladPodpisu("Podpis ma niewłaściwą długość.")
    return Podpis(plik=plik, sha256=suma, podpis=podpis)


def zweryfikuj_wydanie(tekst: str, klucz_publiczny_hex: str, oczekiwany_plik: str) -> str:
    """Sprawdza plik podpisu i zwraca podpisaną sumę SHA-256 instalatora.

    Rzuca BladPodpisu, gdy podpis nie pasuje do klucza, dotyczy innego pliku
    albo jest nieczytelny. Komunikaty nadają się do pokazania użytkownikowi.
    """
    try:
        klucz = bytes.fromhex(klucz_publiczny_hex.strip())
    except ValueError as exc:
        raise BladPodpisu("Program ma nieprawidłowy klucz publiczny wydań.") from exc
    if len(klucz) != 32:
        raise BladPodpisu("Program ma nieprawidłowy klucz publiczny wydań.")
    dane = odczytaj_podpis(tekst)
    if dane.plik != oczekiwany_plik:
        raise BladPodpisu(
            f"Podpis dotyczy pliku {dane.plik}, a wydanie zawiera {oczekiwany_plik}."
        )
    if not sprawdz(klucz, tresc_do_podpisu(dane.plik, dane.sha256), dane.podpis):
        raise BladPodpisu(
            "Podpis wydania nie zgadza się z kluczem wbudowanym w program. "
            "Instalator nie zostanie uruchomiony."
        )
    return dane.sha256


# --- klucz prywatny autora --------------------------------------------------


def wczytaj_klucz_prywatny(sciezka: Path) -> bytes:
    """Klucz z pliku tekstowego (64 znaki szesnastkowe)."""
    tekst = Path(sciezka).read_text(encoding="utf-8").strip()
    try:
        klucz = bytes.fromhex(tekst)
    except ValueError as exc:
        raise ValueError(f"Plik {sciezka} nie zawiera klucza szesnastkowego.") from exc
    if len(klucz) != 32:
        raise ValueError(f"Klucz w {sciezka} ma {len(klucz)} bajtów, oczekiwano 32.")
    return klucz
