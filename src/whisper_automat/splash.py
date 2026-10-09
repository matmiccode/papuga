"""Ekran powitalny pokazywany podczas uruchamiania programu.

Bez niego przez kilka sekund widać na pasku zadań bezimienne okno „tk":
Tk tworzy okno główne od razu, a tytuł i wygląd nadajemy mu dopiero po
wykryciu sprzętu. Rozwiązanie jest proste — okno główne chowamy do czasu,
aż będzie gotowe, a użytkownikowi pokazujemy to okienko.
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional

from .core.config import APP_NAME, WYDANIE, asset

from .theme import ACCENT, BG, FG, FG_DIM, FONT, FONT_SEMI
from .theme import BG_INPUT as BG_TOR
from .theme import BORDER_DROP as OBRAMOWANIE

SZEROKOSC = 440
WYSOKOSC = 260

#: Szerokość biegnącego segmentu paska postępu, w pikselach.
SEGMENT = 130
#: Odstęp między klatkami animacji. 16 ms to około 60 klatek na sekundę.
KLATKA_MS = 16


class Splash:
    """Okno powitalne z animowanym paskiem oczekiwania."""

    def __init__(self, root: tk.Tk, wersja: str = "", podpis: str = ""):
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)  # bez ramki i bez wpisu na pasku zadań
        self.win.configure(bg=OBRAMOWANIE)
        self.win.attributes("-topmost", True)

        self._wysrodkuj()

        # Cienka ramka: zewnętrzne tło prześwituje na 1 px dookoła.
        wnetrze = tk.Frame(self.win, bg=BG)
        wnetrze.pack(fill="both", expand=True, padx=1, pady=1)

        self._ikona = self._wczytaj_ikone()
        if self._ikona is not None:
            tk.Label(wnetrze, image=self._ikona, bg=BG).pack(pady=(30, 12))
        else:
            tk.Label(wnetrze, text="", bg=BG).pack(pady=(30, 0))

        # Te same kroje co w oknie głównym (theme.Title/Subtitle).
        tk.Label(
            wnetrze,
            text=APP_NAME,
            bg=BG,
            fg=FG,
            font=(FONT_SEMI, 16),
        ).pack()

        if WYDANIE.haslo:
            tk.Label(
                wnetrze, text=WYDANIE.haslo, bg=BG, fg=FG_DIM, font=(FONT, 10)
            ).pack()

        if podpis:
            tk.Label(
                wnetrze, text=podpis, bg=BG, fg=FG_DIM, font=(FONT, 8)
            ).pack(pady=(2, 0))

        self._status = tk.StringVar(value="Uruchamianie…")
        tk.Label(
            wnetrze,
            textvariable=self._status,
            bg=BG,
            fg=FG_DIM,
            font=(FONT, 9),
        ).pack(pady=(14, 8))

        self._canvas = tk.Canvas(
            wnetrze, width=SZEROKOSC - 80, height=4,
            bg=BG_TOR, highlightthickness=0,
        )
        self._canvas.pack()
        self._pasek = self._canvas.create_rectangle(
            0, 0, SEGMENT, 4, fill=ACCENT, width=0
        )

        self._x = -SEGMENT
        self._zywy = True
        self._animuj()

        self.win.update_idletasks()
        self.win.update()

    # -- wygląd ------------------------------------------------------------

    def _wysrodkuj(self) -> None:
        ekran_x = self.win.winfo_screenwidth()
        ekran_y = self.win.winfo_screenheight()
        x = (ekran_x - SZEROKOSC) // 2
        y = (ekran_y - WYSOKOSC) // 2
        self.win.geometry(f"{SZEROKOSC}x{WYSOKOSC}+{x}+{y}")

    def _wczytaj_ikone(self) -> Optional[tk.PhotoImage]:
        plik = asset("icon-96.png")
        if plik is None:
            return None
        try:
            return tk.PhotoImage(master=self.win, file=str(plik))
        except tk.TclError:
            return None

    def _animuj(self) -> None:
        """Segment przesuwa się w prawo i wraca zza lewej krawędzi."""
        if not self._zywy:
            return
        szerokosc = SZEROKOSC - 80
        self._x += 6
        if self._x > szerokosc:
            self._x = -SEGMENT
        self._canvas.coords(self._pasek, self._x, 0, self._x + SEGMENT, 4)
        self.win.after(KLATKA_MS, self._animuj)

    # -- sterowanie --------------------------------------------------------

    def status(self, tekst: str) -> None:
        """Zmienia opis pod nazwą programu i odświeża okno."""
        if not self._zywy:
            return
        self._status.set(tekst)
        try:
            self.win.update_idletasks()
            self.win.update()
        except tk.TclError:
            pass

    def close(self) -> None:
        if not self._zywy:
            return
        self._zywy = False
        try:
            self.win.destroy()
        except tk.TclError:
            pass
