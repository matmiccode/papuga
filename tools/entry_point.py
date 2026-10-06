"""Punkt wejścia spakowanej wersji programu.

PyInstaller uruchamia wskazany plik jako moduł `__main__`, a nie jako część
pakietu. Gdyby wskazać wprost `whisper_automat/__main__.py`, wszystkie importy
względne w środku (`from .core import ...`) skończyłyby się błędem
„attempted relative import with no known parent package". Dlatego wejściem
jest ten plik: importuje pakiet normalnie, po nazwie.
"""

from __future__ import annotations

import multiprocessing
import sys

from whisper_automat.__main__ import main

if __name__ == "__main__":
    # Bez tego biblioteki, które tworzą procesy potomne, uruchamiałyby
    # w spakowanej wersji kolejne kopie okna programu.
    multiprocessing.freeze_support()
    sys.exit(main())
