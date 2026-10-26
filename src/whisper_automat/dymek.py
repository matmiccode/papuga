"""Dymek: małe okno w rogu ekranu, na wierzchu wszystkiego.

Na pytania w trakcie nagrania, gdy program leży w tle — udostępniasz ekran
w Zoomie albo Teams i pokazujesz prezentację. Zwykły messagebox pojawiłby
się za innymi oknami (Windows nie oddaje pierwszego planu programowi
w tle) i zabrałby klawiaturę prezentacji. Dymek stoi na wierzchu, nie
bierze fokusu (WS_EX_NOACTIVATE, a gdyby i tak go wziął — fokus wraca do
okna, które go miało), klik w przycisk działa normalnie.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional, Sequence, Tuple

from . import theme
from .theme import ACCENT, BG_CARD, FG, FG_DIM

#: Odstęp od krawędzi obszaru roboczego (nad paskiem zadań).
MARGINES = 16


class Dymek:
    """`przyciski`: (napis, akcja, wyróżniony). Klik — akcja i zamknięcie.

    Zamyka się sam po `czas_s` (wtedy `po_czasie()`), na ✕ i na `zamknij()`.
    """

    def __init__(self, root: tk.Misc, tytul: str, tekst: str,
                 przyciski: Sequence[Tuple[str, Callable[[], None], bool]],
                 czas_s: float = 60, po_czasie: Callable[[], None] = lambda: None):
        self._root = root
        self._po_czasie = po_czasie
        okno = self.okno = tk.Toplevel(root)
        okno.withdraw()
        okno.overrideredirect(True)
        okno.attributes("-topmost", True)
        okno.configure(bg=ACCENT)  # ramka 1 px w kolorze akcentu
        tresc = tk.Frame(okno, bg=BG_CARD, padx=16, pady=12)
        tresc.pack(padx=1, pady=1, fill="both", expand=True)

        gora = tk.Frame(tresc, bg=BG_CARD)
        gora.pack(fill="x")
        tk.Label(gora, text=tytul, bg=BG_CARD, fg=FG, font=(theme.FONT_SEMI, 10),
                 anchor="w").pack(side="left")
        krzyzyk = tk.Label(gora, text="✕", bg=BG_CARD, fg=FG_DIM, cursor="hand2",
                           font=(theme.FONT, 10))
        krzyzyk.pack(side="right")
        krzyzyk.bind("<Button-1>", lambda _e: self.zamknij())
        tk.Label(tresc, text=tekst, bg=BG_CARD, fg=FG_DIM, font=(theme.FONT, 9),
                 justify="left", anchor="w", wraplength=340).pack(fill="x", pady=(6, 10))

        rzad = tk.Frame(tresc, bg=BG_CARD)
        rzad.pack(fill="x")
        # Dwa przyciski obok siebie, więcej (kilka monitorów) — jeden pod drugim.
        pionowo = len(przyciski) > 2
        for i, (napis, akcja, wyrozniony) in enumerate(przyciski):
            przycisk = ttk.Button(rzad, text=napis,
                                  style="Accent.TButton" if wyrozniony else "TButton",
                                  command=lambda a=akcja: self._klik(a))
            if pionowo:
                przycisk.pack(fill="x", pady=(0 if i == 0 else 4, 0))
            else:
                przycisk.pack(side="left", padx=(0 if i == 0 else 6, 0))

        okno.update_idletasks()
        self._ustaw_w_rogu()
        poprzedni = self._na_pierwszym_planie()
        self._bez_fokusu()
        okno.deiconify()
        okno.update_idletasks()
        self._oddaj_fokus(poprzedni)
        self._zegar: Optional[str] = (okno.after(int(czas_s * 1000), self._minal_czas)
                                      if czas_s else None)

    @property
    def otwarty(self) -> bool:
        try:
            return bool(self.okno.winfo_exists())
        except tk.TclError:
            return False

    def zamknij(self) -> None:
        if not self.otwarty:
            return
        if self._zegar is not None:
            try:
                self.okno.after_cancel(self._zegar)
            except tk.TclError:
                pass
        self.okno.destroy()

    def _klik(self, akcja: Callable[[], None]) -> None:
        self.zamknij()
        akcja()

    def _minal_czas(self) -> None:
        self._zegar = None
        self.zamknij()
        self._po_czasie()

    # -- Windows -----------------------------------------------------------

    def _hwnd(self) -> int:
        import ctypes

        return int(ctypes.windll.user32.GetParent(self.okno.winfo_id()))

    def _ustaw_w_rogu(self) -> None:
        """Prawy dolny róg obszaru roboczego monitora, na którym stoi program."""
        szer, wys = self.okno.winfo_reqwidth(), self.okno.winfo_reqheight()
        prawo, dol = self._root.winfo_screenwidth(), self._root.winfo_screenheight()
        if os.name == "nt":
            try:
                import ctypes
                import ctypes.wintypes as wt

                class MONITORINFO(ctypes.Structure):
                    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                                ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

                u32 = ctypes.windll.user32
                u32.MonitorFromWindow.restype = ctypes.c_void_p
                glowne = u32.GetParent(self._root.winfo_id())
                ekran = u32.MonitorFromWindow(wt.HWND(glowne), 2)  # NEAREST
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(info)
                if ekran and u32.GetMonitorInfoW(ctypes.c_void_p(ekran), ctypes.byref(info)):
                    prawo, dol = info.rcWork.right, info.rcWork.bottom
            except (AttributeError, OSError):
                pass
        self.okno.geometry(f"+{prawo - szer - MARGINES}+{dol - wys - MARGINES}")

    @staticmethod
    def _na_pierwszym_planie() -> int:
        if os.name != "nt":
            return 0
        import ctypes

        return int(ctypes.windll.user32.GetForegroundWindow() or 0)

    def _bez_fokusu(self) -> None:
        if os.name != "nt":
            return
        try:
            import ctypes
            import ctypes.wintypes as wt

            u32 = ctypes.windll.user32
            pobierz = getattr(u32, "GetWindowLongPtrW", u32.GetWindowLongW)
            ustaw = getattr(u32, "SetWindowLongPtrW", u32.SetWindowLongW)
            pobierz.restype = ctypes.c_ssize_t
            ustaw.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
            GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x80
            hwnd = wt.HWND(self._hwnd())
            ustaw(hwnd, GWL_EXSTYLE, pobierz(hwnd, GWL_EXSTYLE) | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        except (AttributeError, OSError):
            pass

    def _oddaj_fokus(self, poprzedni: int) -> None:
        if os.name != "nt" or not poprzedni:
            return
        try:
            import ctypes

            u32 = ctypes.windll.user32
            if int(u32.GetForegroundWindow() or 0) == self._hwnd():
                u32.SetForegroundWindow(poprzedni)
        except (AttributeError, OSError):
            pass
