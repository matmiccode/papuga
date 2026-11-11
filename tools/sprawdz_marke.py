"""Rozdział tożsamości wydań — kontrola narzędziem centrali MATCODE.

`sprawdz-firmowe.js` (C:\\Users\\<użytkownik>\\matcode\\narzedzia, opis
w ...\\matcode\\WYDANIA.md) szuka słów w nazwach plików, w tekście,
w modułach ściśniętych w .exe PyInstallera i w zipach:

  * paczka firmowa — żadnego śladu marki MATCODE ani wydania publicznego
    (tools/build_exe.py, przed instalatorem firmowym);
  * paczka i kod publiczny (`--publiczne`) — żadnego śladu firmy ani nazwy
    wydania firmowego (tools/build_exe.py przed instalatorem publicznym,
    tools/publikuj_kod.py na migawce kodu).

Słowa „drugiego” wydania (nazwa, plik, identyfikatory) bierze z jego modułu
danych, więc w kodzie publicznym nie ma ich nawet tutaj. Wynik musi być
CZYSTO; inaczej — albo bez node czy narzędzia — SystemExit.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whisper_automat.core.wydanie import WYDANIA  # noqa: E402

NARZEDZIE = Path.home() / "matcode" / "narzedzia" / "sprawdz-firmowe.js"


def slowa_wydania(kod: str) -> List[str]:
    """Nazwa, plik, identyfikator paska zadań i AppId instalatora wydania."""
    wyd = WYDANIA.get(kod)
    if wyd is None:
        raise SystemExit(f"Brak modułu danych wydania „{kod}” — nie wiem, czego szukać.")
    slowa: List[str] = []
    for slowo in (wyd.nazwa, wyd.plik, wyd.app_id, wyd.inno_id.strip("{}")):
        if slowo and slowo.lower() not in slowa:
            slowa.append(slowo.lower())
    return slowa


def sprawdz(sciezki: Iterable[Path], tez: List[str], publiczne: bool = False,
            opis: str = "") -> None:
    """Uruchamia narzędzie i wypisuje jego ogon. Bez CZYSTO — SystemExit."""
    node = shutil.which("node")
    if node is None or not NARZEDZIE.is_file():
        raise SystemExit(f"Nie ma czym sprawdzić {opis or 'paczki'}: potrzebny node i {NARZEDZIE}.")
    cmd = [node, str(NARZEDZIE), *(["--publiczne"] if publiczne else []),
           "--tez", ",".join(tez), *(str(s) for s in sciezki)]
    wynik = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    linie = (wynik.stdout + wynik.stderr).strip().splitlines()
    print("  " + "\n  ".join(linie[-40:]))
    if wynik.returncode != 0 or not any("CZYSTO" in linia for linia in linie):
        strona = "firmy albo wydania firmowego" if publiczne else "marki publicznej"
        raise SystemExit(f"{(opis or 'Paczka').capitalize()}: ślady {strona} (lista wyżej). "
                         f"Usuń je u źródła i spróbuj ponownie.")
