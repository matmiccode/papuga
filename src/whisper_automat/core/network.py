"""Dostęp do sieci — w tym w sieciach firmowych z inspekcją TLS.

Model pobiera się z Hugging Face przez HTTPS. Domyślnie Python weryfikuje
certyfikaty listą z pakietu `certifi`, która zna tylko publiczne urzędy
certyfikacji. W firmach, gdzie ruch przechodzi przez proxy podmieniające
certyfikaty (Zscaler, Fortinet, Palo Alto i podobne), serwer przedstawia
certyfikat podpisany prywatnym urzędem firmy. Taki urząd jest w magazynie
Windows, ale nie ma go w `certifi` — stąd błąd:

    [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate

Rozwiązaniem jest weryfikacja przez magazyn systemowy, który firmowy urząd
zna, bo dodał go tam dział IT.
"""

from __future__ import annotations

import os
import ssl
import urllib.error
import urllib.request

from .wydanie import biezace

#: Gdzie wersja zainstalowana trzyma modele — do podpowiedzi dla użytkownika.
FOLDER_MODELI = f"%LOCALAPPDATA%\\{biezace().plik}\\models"

#: Lekki zasób do sprawdzenia, czy Hub jest osiągalny.
HUB_PROBE_URL = "https://huggingface.co/api/models/mobiuslabsgmbh/faster-whisper-large-v3-turbo"

_wstrzykniete = False


def enable_system_certificates() -> str:
    """Przełącza weryfikację certyfikatów na magazyn systemu operacyjnego.

    Wywoływane raz, przy starcie programu, zanim cokolwiek pójdzie w sieć.
    Zwraca opis dla dziennika — pusty, gdy nic nie trzeba było zmieniać.
    """
    global _wstrzykniete
    if _wstrzykniete:
        return ""

    # Gdy ktoś jawnie wskazał własny zestaw certyfikatów — na przykład dział IT
    # wyeksportował firmowy urząd do pliku .pem — to on ma pierwszeństwo przed
    # magazynem systemowym.
    for zmienna in ("WHISPER_AUTOMAT_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        sciezka = os.environ.get(zmienna)
        if sciezka and os.path.isfile(sciezka):
            os.environ["SSL_CERT_FILE"] = sciezka
            _wstrzykniete = True
            return f"Certyfikaty z pliku wskazanego przez {zmienna}: {sciezka}"

    try:
        import truststore
    except ImportError:
        _wstrzykniete = True
        return ""

    try:
        truststore.inject_into_ssl()
        _wstrzykniete = True
        return "Certyfikaty weryfikowane przez magazyn systemowy."
    except Exception as exc:
        _wstrzykniete = True
        return f"Nie udało się włączyć systemowego magazynu certyfikatów: {exc}"


def opisz_blad_sieci(exc: BaseException) -> str:
    """Zamienia techniczny błąd sieciowy na wskazówkę, co z tym zrobić.

    Zwraca pusty string, jeśli błąd nie wygląda na sieciowy.
    """
    tekst = f"{type(exc).__name__}: {exc}"
    niski = tekst.lower()

    if "certificate_verify_failed" in niski or "certificate verify failed" in niski:
        return (
            "Nie udało się zweryfikować certyfikatu serwera. Zwykle znaczy to, "
            "że sieć firmowa podmienia certyfikaty własnym urzędem.\n"
            "Co można zrobić:\n"
            "  1. Poproś dział IT o dodanie firmowego urzędu certyfikacji do "
            "magazynu Windows (zwykle już tam jest — program go użyje).\n"
            "  2. Albo skopiuj gotowy model z komputera, na którym już działa: "
            f"cały folder {FOLDER_MODELI}.\n"
            "  3. Albo wskaż folder z modelem zmienną WHISPER_AUTOMAT_MODELS."
        )

    if any(s in niski for s in ("proxy", "407")):
        return (
            "Połączenie blokuje serwer proxy wymagający logowania. Poproś dział "
            "IT o dostęp do huggingface.co albo skopiuj gotowy model z innego "
            f"komputera (folder {FOLDER_MODELI})."
        )

    if any(
        s in niski
        for s in ("connecterror", "connectionerror", "timeout", "getaddrinfo",
                  "temporary failure in name resolution", "name or service not known")
    ):
        return (
            "Brak połączenia z serwerem modeli (huggingface.co). Sprawdź "
            "internet albo skopiuj gotowy model z innego komputera "
            f"(folder {FOLDER_MODELI})."
        )

    return ""


def sprawdz_hub(timeout: float = 10.0):
    """Sprawdza, czy da się pobrać model. Zwraca (czy_ok, opis)."""
    enable_system_certificates()
    try:
        zadanie = urllib.request.Request(
            HUB_PROBE_URL, headers={"User-Agent": biezace().plik}
        )
        with urllib.request.urlopen(zadanie, timeout=timeout) as odpowiedz:
            return True, f"huggingface.co osiągalne (HTTP {odpowiedz.status})"
    except urllib.error.HTTPError as exc:
        # Sam kod błędu HTTP oznacza, że połączenie i TLS zadziałały.
        return True, f"huggingface.co osiągalne (HTTP {exc.code})"
    except ssl.SSLError as exc:
        return False, f"błąd certyfikatu: {exc}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
