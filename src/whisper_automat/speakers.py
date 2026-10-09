"""Okno przypisania imion rozpoznanym mówcom.

Program nie pyta o każde zdanie z osobna — to byłaby katorga. Pyta raz,
po rozpoznaniu głosów: pokazuje listę mówców, przy każdym przycisk
odsłuchania krótkiej próbki i pole na imię. Wpisane nazwy trafiają do
wszystkich formatów zapisu.

Numeracja mówców jest inna w każdym nagraniu, bo bierze się z porównania
głosów wewnątrz tego jednego pliku. Dlatego pytamy osobno dla każdego
nagrania, a nie raz na całą kolejkę.

Dwie rzeczy, które już raz kosztowały czas:

1. Pytamy **tylko o mówców, którzy mają w transkrypcji jakiś tekst** — imię
   wpisane komukolwiek innemu nie miałoby gdzie się pojawić, a taki wiersz
   w oknie wygląda na uczestnika, którego w nagraniu nie było. O to, żeby
   tekst dostali wszyscy naprawdę mówiący, dba `diarization.przypisz()`:
   dzieli długie segmenty Whispera tam, gdzie zmienia się osoba.

2. Każde wywołanie ffmpeg musi mieć przekierowane strumienie i `-nostdin`.
   Spakowana wersja jest budowana jako aplikacja okienkowa, więc nie ma
   prawidłowych uchwytów stdin/stdout — bez przekierowania `subprocess`
   przewraca się na „WinError 6: uchwyt jest nieprawidłowy" i próbka nigdy
   nie powstaje. Reszta programu robi to poprawnie, tylko to okno nie
   robiło — i przycisk „Posłuchaj" milczał bez słowa wyjaśnienia.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Dict, List, Tuple

from .core.config import APP_NAME
from .teksty import t

from . import theme
from .theme import BG, FG, FG_DIM
from .theme import BG_INPUT as BG_PANEL
from .theme import ERR_COLOR as ERR

#: Ile sekund próbki odtwarzamy. Tyle wystarcza, żeby rozpoznać osobę.
PROBKA_S = 5.0

#: Nagłówek pliku WAV waży 44 bajty — mniejszy plik znaczy, że ffmpeg
#: wyprodukował pustkę, choć nie zgłosił błędu.
MIN_WAV_BAJTOW = 100

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_MCI_ALIAS = "whisper_automat_probka"


def _czas(sekundy: float) -> str:
    minuty, reszta = divmod(int(sekundy), 60)
    return f"{minuty}:{reszta:02d}"


def _probki_mowcow(result) -> List[Tuple[int, float, float]]:
    """(numer mówcy, start próbki, długość próbki) dla każdego mówcy z tekstem.

    Pytamy tylko o osoby, które mają w transkrypcji choć jeden fragment —
    imię wpisane komukolwiek innemu nie miałoby gdzie się pojawić. Wcześniej
    okno pokazywało też takie widmo („MÓWCA 3 (bez tekstu)") i wyglądało to
    na trzeciego uczestnika, którego w nagraniu nie było.

    Próbka to najdłuższa nieprzerwana wypowiedź z odcinków diaryzacji —
    w krótkich odcinkach częściej trafia się urwane słowo albo dwa głosy
    naraz. Bez odcinków zostają segmenty tekstu.
    """
    z_tekstem = {
        s.speaker for s in result.segments if getattr(s, "speaker", -1) >= 0
    }
    if not z_tekstem:
        return []

    najdluzsze: Dict[int, Tuple[float, float]] = {}

    def rozwaz(numer: int, start: float, dlugosc: float) -> None:
        if numer in z_tekstem and dlugosc > najdluzsze.get(numer, (0.0, 0.0))[1]:
            najdluzsze[numer] = (start, dlugosc)

    for odcinek in getattr(result, "speaker_turns", None) or []:
        rozwaz(odcinek.mowca, odcinek.start, odcinek.koniec - odcinek.start)

    if not najdluzsze:
        for segment in result.segments:
            rozwaz(
                getattr(segment, "speaker", -1),
                segment.start,
                segment.end - segment.start,
            )

    return [
        (numer, start, dlugosc)
        for numer, (start, dlugosc) in sorted(najdluzsze.items())
    ]


# ---------------------------------------------------------------------------
# Odtwarzanie
# ---------------------------------------------------------------------------


def _zagraj_winsound(plik: Path) -> None:
    import winsound

    winsound.PlaySound(str(plik), winsound.SND_FILENAME | winsound.SND_ASYNC)


def _zagraj_mci(plik: Path) -> None:
    """Droga zapasowa przez Media Control Interface.

    Odtwarza w sesji dźwiękowej programu, więc bywa słyszalna tam, gdzie
    starsze `PlaySound` milczy.
    """
    import ctypes

    wyslij = ctypes.windll.winmm.mciSendStringW
    wyslij(f"close {_MCI_ALIAS}", None, 0, 0)
    if wyslij(f'open "{plik}" type waveaudio alias {_MCI_ALIAS}', None, 0, 0):
        raise RuntimeError(t("MCI nie otworzyło pliku próbki"))
    if wyslij(f"play {_MCI_ALIAS} from 0", None, 0, 0):
        wyslij(f"close {_MCI_ALIAS}", None, 0, 0)
        raise RuntimeError(t("MCI nie rozpoczęło odtwarzania"))


def _cisza() -> None:
    """Przerywa odtwarzanie obiema drogami i zwalnia plik próbki."""
    try:
        import winsound

        winsound.PlaySound(None, 0)
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.winmm.mciSendStringW(f"close {_MCI_ALIAS}", None, 0, 0)
    except Exception:
        pass


class SpeakerDialog:
    """Modalne okno: kto jest kim."""

    def __init__(self, parent: tk.Misc, result, audio: Path):
        self.result = result
        self.audio = Path(audio)
        self.nazwy: Dict[int, str] = {}
        self._pola: Dict[int, tk.StringVar] = {}
        self._probki = _probki_mowcow(result)
        self._tymczasowe: List[Path] = []

        self.win = tk.Toplevel(parent)
        self.win.title(t("Kto jest kim?"))
        self.win.configure(bg=BG)
        self.win.resizable(False, False)
        self.win.transient(parent)

        self._zbuduj()
        self._wysrodkuj(parent)
        theme.ciemny_pasek_tytulu(self.win)

        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW", self._pomin)
        self.win.wait_window()

    # -- budowa ------------------------------------------------------------

    def _zbuduj(self) -> None:
        ramka = ttk.Frame(self.win, style="App.TFrame", padding=(20, 16, 20, 16))
        ramka.pack(fill="both", expand=True)

        ttk.Label(
            ramka,
            text=t("{plik} — rozpoznane głosy: {n}").format(
                plik=self.result.source.name, n=len(self._probki)),
            style="Status.TLabel",
        ).grid(row=0, column=0, columnspan=3, sticky="w")

        ttk.Label(
            ramka,
            text=t("Posłuchaj próbki i wpisz imię. Puste pole zostawia "
                   "oznaczenie MÓWCA 1, MÓWCA 2…"),
            style="Dim.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 14))

        for i, (numer, start, _dlugosc) in enumerate(self._probki):
            wiersz = 2 + i
            ttk.Label(ramka, text=t("MÓWCA {n}").format(n=numer + 1)).grid(
                row=wiersz, column=0, sticky="w", pady=4, padx=(0, 10)
            )
            ttk.Button(
                ramka,
                text=t("▶  Posłuchaj  ({czas})").format(czas=_czas(start)),
                command=lambda n=numer: self._odtworz(n),
            ).grid(row=wiersz, column=1, sticky="w", padx=(0, 10))

            zmienna = tk.StringVar()
            self._pola[numer] = zmienna
            pole = tk.Entry(
                ramka,
                textvariable=zmienna,
                bg=BG_PANEL,
                fg=FG,
                insertbackground=FG,
                relief="flat",
                font=("Segoe UI", 10),
                width=28,
            )
            pole.grid(row=wiersz, column=2, sticky="ew", ipady=4, pady=4)
            if i == 0:
                pole.focus_set()

        wiersz_statusu = 2 + len(self._probki)
        self.status = tk.Label(
            ramka,
            text="",
            bg=BG,
            fg=FG_DIM,
            font=("Segoe UI", 9),
            justify="left",
            anchor="w",
            wraplength=460,
        )
        self.status.grid(
            row=wiersz_statusu, column=0, columnspan=3, sticky="w", pady=(10, 0)
        )

        przyciski = ttk.Frame(ramka, style="App.TFrame")
        przyciski.grid(
            row=wiersz_statusu + 1, column=0, columnspan=3, sticky="ew", pady=(14, 0)
        )
        przyciski.columnconfigure(1, weight=1)
        ttk.Button(przyciski, text=t("Pomiń"), command=self._pomin).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(przyciski, text=t("⏹  Zatrzymaj"), command=self._zatrzymaj).grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )
        ttk.Button(
            przyciski, text=t("Zastosuj"), style="Accent.TButton", command=self._zastosuj
        ).grid(row=0, column=2, sticky="e")

        self.win.bind("<Return>", lambda _e: self._zastosuj())
        self.win.bind("<Escape>", lambda _e: self._pomin())

    def _wysrodkuj(self, parent: tk.Misc) -> None:
        self.win.update_idletasks()
        try:
            x = parent.winfo_rootx() + (parent.winfo_width() - self.win.winfo_width()) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - self.win.winfo_height()) // 3
            self.win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        except tk.TclError:
            pass

    # -- odsłuch -----------------------------------------------------------

    def _powiedz(self, tekst: str, blad: bool = False) -> None:
        try:
            self.status.configure(text=tekst, fg=ERR if blad else FG_DIM)
        except tk.TclError:
            pass

    def _odtworz(self, mowca: int) -> None:
        """Wycina i odtwarza próbkę głosu danej osoby.

        Każde niepowodzenie ląduje na pasku statusu. Milczący przycisk nie
        daje żadnej wskazówki, co poprawić — a dokładnie to się zdarzyło.
        """
        wybrany = next((p for p in self._probki if p[0] == mowca), None)
        if wybrany is None:
            self._powiedz(t("Brak próbki dla MÓWCY {n}.").format(n=mowca + 1), blad=True)
            return

        _, start, dlugosc = wybrany
        _cisza()

        try:
            plik = self._wytnij(mowca, start, min(PROBKA_S, dlugosc))
        except Exception as exc:
            self._powiedz(t("Nie udało się wyciąć próbki: {blad}").format(blad=exc), blad=True)
            return

        ostatni: Exception = RuntimeError(t("brak metody odtwarzania"))
        for zagraj in (_zagraj_winsound, _zagraj_mci):
            try:
                zagraj(plik)
            except Exception as exc:
                ostatni = exc
                continue
            # Nie da się sprawdzić z programu, czy dźwięk doszło do uszu —
            # więc od razu podpowiadamy, gdzie szukać, gdy nie doszedł.
            self._powiedz(
                t("Odtwarzam MÓWCĘ {n} — fragment od {czas}, {s:.0f} s. Nie słyszysz? "
                  "Sprawdź głośność programu {app} w mikserze Windows.").format(
                    n=mowca + 1, czas=_czas(start), s=min(PROBKA_S, dlugosc), app=APP_NAME)
            )
            return

        self._powiedz(t("Nie udało się odtworzyć próbki: {blad}").format(blad=ostatni), blad=True)

    def _zatrzymaj(self) -> None:
        _cisza()
        self._powiedz("")

    def _wytnij(self, mowca: int, start: float, dlugosc: float) -> Path:
        from .core.probe import find_ffmpeg

        ffmpeg, _ = find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError(t("nie znaleziono ffmpeg"))
        if not self.audio.is_file():
            raise RuntimeError(t("nie ma już pliku {plik}").format(plik=self.audio.name))

        cel = Path(tempfile.gettempdir()) / (
            f"wa_probka_{mowca + 1}_{int(start * 1000)}.wav"
        )
        # Strumienie muszą być przekierowane, a stdin odcięty: w wersji
        # okienkowej dziedziczone uchwyty są nieprawidłowe i subprocess pada.
        wynik = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                "-ss", f"{start:.3f}", "-t", f"{max(dlugosc, 1.0):.3f}",
                "-i", str(self.audio),
                "-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le",
                str(cel),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            creationflags=_NO_WINDOW,
            timeout=30,
        )
        if wynik.returncode != 0:
            powod = (wynik.stderr or "").strip().splitlines()
            raise RuntimeError(
                powod[-1][:200] if powod else t("ffmpeg zwrócił {kod}").format(kod=wynik.returncode)
            )
        if not cel.is_file() or cel.stat().st_size < MIN_WAV_BAJTOW:
            raise RuntimeError(t("ffmpeg zapisał pusty plik"))

        self._tymczasowe.append(cel)
        return cel

    # -- zamknięcie --------------------------------------------------------

    def _zastosuj(self) -> None:
        self.nazwy = {
            numer: zmienna.get().strip()
            for numer, zmienna in self._pola.items()
            if zmienna.get().strip()
        }
        self._zamknij()

    def _pomin(self) -> None:
        self.nazwy = {}
        self._zamknij()

    def _zamknij(self) -> None:
        _cisza()  # zwolnij plik, zanim spróbujemy go usunąć
        for plik in self._tymczasowe:
            try:
                plik.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            self.win.grab_release()
            self.win.destroy()
        except tk.TclError:
            pass


def zapytaj(parent: tk.Misc, result, audio: Path) -> Dict[int, str]:
    """Pokazuje okno i zwraca {numer mówcy: imię}. Pusty słownik = pominięto."""
    if not _probki_mowcow(result):
        return {}
    return SpeakerDialog(parent, result, audio).nazwy
