"""Punkt wejścia: `python -m whisper_automat`.

Bez argumentów otwiera okno programu. Przekazane pliki trafiają od razu
do kolejki, a `--doctor` i `--cli` działają bez GUI.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List

from .teksty import t


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="whisper-automat",
        description=t("Szybka transkrypcja audio i wideo silnikiem Whisper."),
    )
    p.add_argument(
        "files",
        nargs="*",
        help=t("Pliki lub foldery do transkrypcji (bez nich otwiera się okno)."),
    )
    p.add_argument(
        "--doctor",
        action="store_true",
        help=t("Wypisz raport o środowisku i zakończ."),
    )
    p.add_argument(
        "--cli",
        action="store_true",
        help=t("Przetwórz pliki w konsoli, bez otwierania okna."),
    )
    p.add_argument("--model", default=None, help=t("Wymuś model, np. large-v3-turbo."))
    p.add_argument("--language", default=None, help=t("Kod języka, np. pl. Pusty = auto."))
    p.add_argument(
        "--formats",
        default=None,
        help=t("Formaty po przecinku: txt,txt_plain,srt,vtt,json"),
    )
    p.add_argument("--output", default=None, help=t("Folder na transkrypcje."))
    return p


def _bez_konsoli() -> bool:
    """Czy nie mamy gdzie wypisać tekstu.

    Spakowana wersja jest budowana jako aplikacja okienkowa (--windowed),
    więc uruchomiona ze skrótu nie ma żadnego standardowego wyjścia.
    Ale gdy ktoś przekieruje wynik do pliku (`WhisperAutomat.exe --doctor
    > raport.txt`), uchwyt jest prawidłowy — i właśnie tam ma pójść raport.
    Wcześniej program porywał konsolę nadrzędną także w tym przypadku
    i plik zostawał pusty.
    """
    from .core.config import is_frozen

    return is_frozen() and sys.stdout is None


def _przejmij_konsole() -> bool:
    """Podpina się pod konsolę procesu nadrzędnego, jeśli jakaś jest.

    Dzięki temu `WhisperAutomat.exe --cli plik.mp4` uruchomione z wiersza
    poleceń pisze tam, gdzie użytkownik tego oczekuje.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        # -1 = ATTACH_PARENT_PROCESS
        if not ctypes.windll.kernel32.AttachConsole(-1):
            return False
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        return True
    except Exception:
        return False


def main(argv: List[str] = None) -> int:
    args = _parser().parse_args(argv if argv is not None else sys.argv[1:])

    # Zanim cokolwiek pójdzie w sieć: w sieciach firmowych z inspekcją TLS
    # certyfikaty da się zweryfikować tylko przez magazyn systemowy.
    from .core.network import enable_system_certificates

    enable_system_certificates()

    # Język interfejsu także dla --doctor i --cli: ustawienie programu,
    # a bez niego język Windows (zmienna WHISPER_AUTOMAT_JEZYK wygrywa).
    from . import teksty
    from .core.config import Settings

    teksty.ustaw_z_ustawien(Settings.load().jezyk)

    if args.doctor:
        from .core.doctor import diagnose, format_diagnosis

        diag = diagnose()
        raport = format_diagnosis(diag)

        if _bez_konsoli() and not _przejmij_konsole():
            from .app import show_report_window

            show_report_window(t("Diagnostyka środowiska"), raport)
            return 0 if diag.ready else 1

        print(raport)
        return 0 if diag.ready else 1

    if args.cli:
        if _bez_konsoli():
            _przejmij_konsole()
        return _run_cli(args)

    from .app import run

    return run(initial_files=[Path(f) for f in args.files] if args.files else None)


def _run_cli(args) -> int:
    from .core.config import Settings
    from .core.media import collect_media
    from .core.pipeline import Callbacks, Runner

    files = collect_media(args.files)
    if not files:
        print(t("Nie podano żadnego pliku audio ani wideo."))
        return 2

    settings = Settings.load()
    if args.model:
        settings.model = args.model
    if args.language is not None:
        settings.language = args.language
    if args.formats:
        settings.formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    if args.output:
        settings.output_dir = args.output
        settings.output_next_to_source = False
    settings.normalize()

    callbacks = Callbacks(
        log=lambda m: print(m, flush=True),
        status=lambda s: print(s, flush=True),
    )
    results = Runner(settings, callbacks).run(files)

    failed = [r for r in results if not r.ok]
    print("\n" + t("Gotowe: {ok} / {n}").format(ok=len(results) - len(failed), n=len(results)))
    for job in failed:
        print("  " + t("BŁĄD {plik}: {blad}").format(plik=job.source.name, blad=job.error))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
