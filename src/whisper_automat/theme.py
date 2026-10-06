"""Wygląd: paleta i style ttk wspólne dla wszystkich okien programu.

Spokojny grafit i jeden kolor akcentu. Barwy ary niesie logo i — w Papudze —
pasek postępu bieżącego pliku: na niego człowiek patrzy, czekając na wynik.
Reszta interfejsu ma nie odciągać uwagi od tego, co program robi.
"""

from __future__ import annotations

import os
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# --- paleta ---------------------------------------------------------------
BG = "#15171c"          # tło okna
BG_CARD = "#1d2026"     # karty: kolejka, ustawienia, dziennik
BG_INPUT = "#262a32"    # pola, listy rozwijane, przyciski drugorzędne
BG_HOVER = "#2e333d"
BG_DROP = "#1d2026"
BG_DROP_HOVER = "#22314a"
BORDER = "#2c3039"
BORDER_DROP = "#3a414d"

FG = "#e8eaee"
FG_DIM = "#959dab"
FG_FAINT = "#6b7280"

#: Niebieski z piór ary, o ton spokojniejszy.
ACCENT = "#2f74d0"
ACCENT_HOVER = "#3b82e0"
ACCENT_PRESSED = "#2763b5"
ACCENT_SOFT = "#233a5e"  # zaznaczenie w kolejce

OK_COLOR = "#4fb47a"
WARN_COLOR = "#e3a83a"
ERR_COLOR = "#e05a5a"

#: Przycisk „Postaw kawę” — żółć z piór Papugi (theme.PIORA).
KAWA = "#f7ba1c"
KAWA_HOVER = "#ffcb45"
KAWA_FG = "#231a05"

#: Pasek o nowej wersji — widać go od razu, ale nie krzyczy kolorem błędu.
BANER_BG = "#22314a"

#: Tor pustego paska postępu — ledwie widoczny, żeby bez pracy nie udawał
#: linii oddzielającej.
TOR = "#22262d"

#: Pasy piór z logo Papugi (tools/make_icon.py), od skrzydła do głowy.
PIORA = ("#1c64c4", "#2c965c", "#f7ba1c", "#ce202f")

LOG_BG = "#121418"
LOG_FG = "#c3c9d2"

FONT = "Segoe UI"
#: Półgruby krój do nagłówków — lżejszy od pogrubienia, jest w Windows 10 i 11.
FONT_SEMI = "Segoe UI Semibold"
FONT_MONO = "Consolas"
#: Ikona w polu upuszczania: znak i krój. Glif „pobierz” z Segoe MDL2 Assets
#: (Windows 10 i 11), a bez tego kroju — zwykła strzałka. Ustala zastosuj().
STRZALKA = ("↓", FONT)


def zastosuj(root: tk.Misc) -> ttk.Style:
    """Konfiguruje style ttk. Wywołać raz, przed zbudowaniem okien."""
    global STRZALKA
    style = ttk.Style(root)
    try:
        if "Segoe MDL2 Assets" in tkfont.families(root):
            STRZALKA = ("", "Segoe MDL2 Assets")
    except tk.TclError:
        pass
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    # clam rysuje wypukłe krawędzie jasnym i ciemnym kolorem; ustawione na
    # kolor tła dają płaskie, nowoczesne kontrolki.
    def plaski(nazwa: str, tlo: str, **reszta) -> None:
        style.configure(nazwa, background=tlo, lightcolor=tlo, darkcolor=tlo,
                        bordercolor=reszta.pop("bordercolor", tlo), **reszta)

    style.configure(".", background=BG, foreground=FG, font=(FONT, 10),
                    troughcolor=BG_INPUT, focuscolor=BG, selectbackground=ACCENT,
                    selectforeground="#ffffff", insertcolor=FG)

    for przyrostek, tlo in (("", BG), ("Card.", BG_CARD)):
        style.configure(f"{przyrostek}TFrame" if przyrostek else "App.TFrame", background=tlo)
        style.configure(f"{przyrostek}TLabel", background=tlo, foreground=FG, font=(FONT, 10))
        style.configure(f"{przyrostek}Dim.TLabel", background=tlo, foreground=FG_DIM,
                        font=(FONT, 9))
        style.configure(f"{przyrostek}Section.TLabel", background=tlo, foreground=FG,
                        font=(FONT_SEMI, 10))
        style.configure(f"{przyrostek}TCheckbutton", background=tlo, foreground=FG,
                        font=(FONT, 10), indicatorbackground=BG_INPUT,
                        indicatorforeground="#ffffff", upperbordercolor=BORDER_DROP,
                        lowerbordercolor=BORDER_DROP, indicatormargin=(0, 0, 8, 0))
        style.map(f"{przyrostek}TCheckbutton",
                  background=[("active", tlo)],
                  foreground=[("disabled", FG_FAINT)],
                  indicatorbackground=[("selected", ACCENT), ("active", BG_HOVER)],
                  upperbordercolor=[("selected", ACCENT)],
                  lowerbordercolor=[("selected", ACCENT)])

    style.configure("Card.TFrame", background=BG_CARD)
    style.configure("Title.TLabel", background=BG, foreground=FG, font=(FONT_SEMI, 17))
    style.configure("Card.Heading.TLabel", background=BG_CARD, foreground=FG,
                    font=(FONT_SEMI, 11))
    style.configure("Subtitle.TLabel", background=BG, foreground=FG_DIM, font=(FONT, 10))
    style.configure("Status.TLabel", background=BG, foreground=FG, font=(FONT_SEMI, 10))
    style.configure("CardStatus.TLabel", background=BG_CARD, foreground=FG,
                    font=(FONT, 10, "bold"))

    # Przyciski: drugorzędne w kolorze pól, główny w kolorze akcentu.
    plaski("TButton", BG_INPUT, foreground=FG, font=(FONT, 10), padding=(14, 7),
           bordercolor=BORDER, borderwidth=1, focusthickness=0)
    style.map("TButton",
              background=[("disabled", BG_CARD), ("pressed", BG_INPUT), ("active", BG_HOVER)],
              lightcolor=[("active", BG_HOVER)], darkcolor=[("active", BG_HOVER)],
              foreground=[("disabled", FG_FAINT)],
              bordercolor=[("disabled", BORDER)])
    # Cichy przycisk bez ramki — dla czynności pobocznych, np. nad kolejką.
    plaski("Ghost.TButton", BG_CARD, foreground=FG_DIM, font=(FONT, 9), padding=(10, 4),
           borderwidth=0, focusthickness=0)
    style.map("Ghost.TButton",
              background=[("disabled", BG_CARD), ("active", BG_HOVER)],
              lightcolor=[("active", BG_HOVER)], darkcolor=[("active", BG_HOVER)],
              foreground=[("disabled", FG_FAINT), ("active", FG)])
    plaski("Accent.TButton", ACCENT, foreground="#ffffff", font=(FONT_SEMI, 11),
           padding=(22, 9), borderwidth=0, focusthickness=0)
    style.map("Accent.TButton",
              background=[("disabled", BG_INPUT), ("pressed", ACCENT_PRESSED),
                          ("active", ACCENT_HOVER)],
              lightcolor=[("disabled", BG_INPUT), ("active", ACCENT_HOVER)],
              darkcolor=[("disabled", BG_INPUT), ("active", ACCENT_HOVER)],
              bordercolor=[("disabled", BG_INPUT)],
              foreground=[("disabled", FG_FAINT)])

    # Pola wyboru i liczby.
    for nazwa in ("TCombobox", "TSpinbox"):
        plaski(nazwa, BG_INPUT, foreground=FG, fieldbackground=BG_INPUT,
               arrowcolor=FG_DIM, bordercolor=BORDER, padding=(8, 5),
               selectbackground=BG_INPUT, selectforeground=FG, insertcolor=FG)
        style.map(nazwa,
                  fieldbackground=[("readonly", BG_INPUT), ("disabled", BG_CARD)],
                  foreground=[("disabled", FG_FAINT)],
                  background=[("active", BG_HOVER)],
                  bordercolor=[("focus", ACCENT)],
                  arrowcolor=[("disabled", FG_FAINT)])
    # Rozwijana lista comboboxa to zwykły Listbox — ttk go nie stylizuje.
    root.option_add("*TCombobox*Listbox.background", BG_INPUT)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.font", (FONT, 10))
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    # Paski przewijania: wąskie, w kolorze kart.
    for nazwa in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        plaski(nazwa, BG_INPUT, troughcolor=BG_CARD, bordercolor=BG_CARD,
               arrowcolor=FG_DIM, arrowsize=12, gripcount=0)
        style.map(nazwa, background=[("active", BG_HOVER)])

    _pola_wyboru(root, style)

    # Kolejka plików.
    style.configure("Treeview", background=BG_CARD, fieldbackground=BG_CARD,
                    foreground=FG, borderwidth=0, rowheight=30, font=(FONT, 10))
    style.map("Treeview", background=[("selected", ACCENT_SOFT)],
              foreground=[("selected", FG)])
    plaski("Treeview.Heading", BG_CARD, foreground=FG_FAINT, font=(FONT, 9),
           padding=(8, 6), relief="flat")
    style.map("Treeview.Heading", background=[("active", BG_CARD)])
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    # Kolejka jest płaska — bez miejsca na strzałkę rozwijania nazwa pliku
    # stoi równo pod nagłówkiem kolumny.
    style.layout("Treeview.Item", [("Treeitem.padding", {"sticky": "nswe", "children": [
        ("Treeitem.image", {"side": "left", "sticky": ""}),
        ("Treeitem.text", {"side": "left", "sticky": ""})]})])
    style.configure("Treeview.Item", padding=(8, 0, 0, 0))

    return style


def _pola_wyboru(root: tk.Misc, style: ttk.Style) -> None:
    """Pole wyboru z obrazków z assets/ui (tools/make_icon.py).

    Bez obrazków zostaje rysowane przez clam — działa, tylko wygląda gorzej.
    """
    from .core.config import asset

    def obraz(nazwa: str):
        plik = asset(f"ui/{nazwa}")
        return tk.PhotoImage(master=root, file=str(plik)) if plik else None

    try:
        obrazy = {n: obraz(f"check-{n}.png") for n in
                  ("off", "off-hover", "on", "on-hover", "off-disabled", "on-disabled")}
    except tk.TclError:
        return
    if any(o is None for o in obrazy.values()):
        return
    # Tk zwalnia obrazek, gdy w Pythonie nie zostanie do niego odwołanie.
    root._pola_wyboru = obrazy  # type: ignore[attr-defined]
    try:
        style.element_create(
            "Pole.indicator", "image", obrazy["off"],
            ("disabled", "selected", obrazy["on-disabled"]),
            ("disabled", obrazy["off-disabled"]),
            ("active", "selected", obrazy["on-hover"]),
            ("selected", obrazy["on"]),
            ("active", obrazy["off-hover"]),
            border=0, sticky="w",
        )
    except tk.TclError:
        return  # element już jest — drugie okno w tym samym procesie
    for nazwa in ("TCheckbutton", "Card.TCheckbutton"):
        style.layout(nazwa, [("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Pole.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})]})]})])
        style.configure(nazwa, padding=(0, 2, 0, 2))
        style.configure(nazwa, indicatormargin=0)


class PasekPostepu(tk.Canvas):
    """Cienki pasek postępu. ttk w motywie clam nie daje go zwęzić.

    Zachowuje się jak ttk.Progressbar tam, gdzie program go używa:
    pasek["value"] = 500 przy maximum=1000. Z kilkoma kolorami w `kolory`
    wypełnienie przechodzi płynnie od pierwszego do ostatniego — rozpięte
    na całą szerokość, więc w miarę postępu odsłania kolejne barwy.
    """

    def __init__(self, parent, maximum: float = 100, grubosc: int = 6,
                 tlo: str = TOR, kolor: str = ACCENT, kolory=None, podloze: str = BG):
        super().__init__(parent, height=grubosc, bg=podloze, highlightthickness=0,
                         borderwidth=0)
        self._maximum = float(maximum)
        self._value = 0.0
        self._tlo = tlo
        self._kolory = [_rgb(k) for k in (kolory or (kolor,))]
        self.bind("<Configure>", lambda _e: self._rysuj())

    def _kolor(self, czesc: float) -> str:
        """Barwa gradientu w danym miejscu (0..1)."""
        if len(self._kolory) == 1:
            r, g, b = self._kolory[0]
        else:
            odcinki = len(self._kolory) - 1
            i = min(int(czesc * odcinki), odcinki - 1)
            u = czesc * odcinki - i
            (r1, g1, b1), (r2, g2, b2) = self._kolory[i], self._kolory[i + 1]
            r, g, b = r1 + (r2 - r1) * u, g1 + (g2 - g1) * u, b1 + (b2 - b1) * u
        return f"#{int(r):02x}{int(g):02x}{int(b):02x}"

    def _rysuj(self) -> None:
        self.delete("all")
        szer, wys = self.winfo_width(), int(self["height"])
        if szer <= 1:
            return
        self.create_rectangle(0, 0, szer, wys, fill=self._tlo, width=0)
        pelne = round(szer * max(0.0, min(self._value / self._maximum, 1.0)))
        if pelne <= 0:
            return
        if len(self._kolory) == 1:
            self.create_rectangle(0, 0, pelne, wys, fill=self._kolor(0), width=0)
            return
        krok = 3  # paski po 3 px — gołym okiem gradient i tak jest płynny
        for x in range(0, pelne, krok):
            self.create_rectangle(x, 0, min(x + krok, pelne), wys,
                                  fill=self._kolor(x / szer), width=0)

    def __setitem__(self, klucz, wartosc):
        if klucz == "value":
            self._value = float(wartosc)
            self._rysuj()
        elif klucz == "maximum":
            self._maximum = float(wartosc)
            self._rysuj()
        else:
            super().__setitem__(klucz, wartosc)

    def __getitem__(self, klucz):
        if klucz == "value":
            return self._value
        if klucz == "maximum":
            return self._maximum
        return super().__getitem__(klucz)


def _rgb(kolor: str) -> tuple:
    kolor = kolor.lstrip("#")
    return tuple(int(kolor[i:i + 2], 16) for i in (0, 2, 4))


def pole(parent, zmienna: tk.Variable, tlo: str = BG_INPUT,
         podpowiedz: str = "") -> tk.Entry:
    """Pole tekstowe w stylu programu — z obwódką, która świeci przy fokusie.

    `podpowiedz` to szary przykład w pustym polu. Leży na polu jako osobny
    napis, więc nigdy nie trafia do zmiennej ani do ustawień.
    """
    pole_ = tk.Entry(
        parent, textvariable=zmienna, bg=tlo, fg=FG, insertbackground=FG,
        disabledbackground=BG_CARD, disabledforeground=FG_FAINT,
        relief="flat", font=(FONT, 10), highlightthickness=1,
        highlightbackground=BORDER, highlightcolor=ACCENT,
    )
    if podpowiedz:
        napis = tk.Label(pole_, text=podpowiedz, bg=tlo, fg=FG_FAINT,
                         font=(FONT, 10), cursor="xterm")
        napis.bind("<Button-1>", lambda _e: pole_.focus_set())

        def odswiez(*_):
            try:
                ma_fokus = pole_.focus_get() is pole_
            except (KeyError, tk.TclError):  # fokus w rozwiniętej liście comboboxa
                ma_fokus = False
            if zmienna.get() or ma_fokus:
                napis.place_forget()
            else:
                napis.place(x=2, rely=0.5, anchor="w")

        zmienna.trace_add("write", odswiez)
        pole_.bind("<FocusIn>", odswiez, add="+")
        pole_.bind("<FocusOut>", odswiez, add="+")
        pole_.after_idle(odswiez)
    return pole_


class Przewijany(tk.Frame):
    """Ramka, której zawartość przewija się, gdy nie mieści się w pionie.

    Pasek przewijania pojawia się tylko wtedy — w zwykłym oknie go nie ma.
    Zawartość wkłada się do `wnetrze`.
    """

    def __init__(self, parent, tlo: str = BG_CARD):
        super().__init__(parent, bg=tlo)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._plotno = tk.Canvas(self, bg=tlo, height=1, highlightthickness=0,
                                 borderwidth=0)
        self._plotno.grid(row=0, column=0, sticky="nsew")
        self._pasek = ttk.Scrollbar(self, orient="vertical", command=self._plotno.yview)
        self._plotno.configure(yscrollcommand=self._pasek.set)
        self.wnetrze = ttk.Frame(self._plotno, style="Card.TFrame")
        self._okno = self._plotno.create_window(0, 0, window=self.wnetrze, anchor="nw")
        self._przewija = False
        self.wnetrze.bind("<Configure>", self._uklad, add="+")
        self._plotno.bind("<Configure>", self._uklad, add="+")
        # Kółko myszy przewija panel, gdy kursor jest nad nim (także nad
        # polami w środku) i jest co przewijać.
        self.bind_all("<MouseWheel>", self._kolko, add="+")

    def _uklad(self, _e=None) -> None:
        # Canvas nie przekazuje rozmiaru zawartości dalej — szerokość panelu
        # trzeba mu podać wprost.
        self._plotno.configure(width=self.wnetrze.winfo_reqwidth())
        szer = self._plotno.winfo_width()
        wys = self.wnetrze.winfo_reqheight()
        self._plotno.itemconfigure(self._okno, width=szer)
        self._plotno.configure(scrollregion=(0, 0, szer, wys))
        przewija = wys > self._plotno.winfo_height() > 1
        if przewija != self._przewija:
            self._przewija = przewija
            if przewija:
                self._pasek.grid(row=0, column=1, sticky="ns")
            else:
                self._pasek.grid_remove()
                self._plotno.yview_moveto(0)

    def _kolko(self, zdarzenie) -> None:
        if not self._przewija:
            return
        try:
            pod = self.winfo_containing(zdarzenie.x_root, zdarzenie.y_root)
        except (KeyError, tk.TclError):
            return
        while pod is not None and pod is not self:
            pod = pod.master
        if pod is self:
            self._plotno.yview_scroll(int(-zdarzenie.delta / 120), "units")


def karta(parent, **grid) -> tk.Frame:
    """Prostokąt z cienką obwódką — grupuje części okna."""
    ramka = tk.Frame(parent, bg=BG_CARD, highlightthickness=1,
                     highlightbackground=BORDER, highlightcolor=BORDER)
    if grid:
        ramka.grid(**grid)
    return ramka


def zaokraglony(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, promien: int,
                **opcje) -> int:
    """Prostokąt o zaokrąglonych rogach na Canvasie (wygładzony wielokąt).

    Punkty narożników są zdublowane — dzięki temu boki zostają proste,
    a wygładzane są tylko rogi.
    """
    r = promien
    punkty = [
        x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1,
        x2, y1 + r, x2, y1 + r, x2, y2 - r, x2, y2 - r, x2, y2,
        x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2, x1, y2,
        x1, y2 - r, x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(punkty, smooth=True, **opcje)


def ciemny_pasek_tytulu(okno: tk.Misc) -> None:
    """Pasek tytułu Windows w kolorze okna zamiast białego.

    Bez tego nad ciemnym oknem wisi jasny pasek systemowy — najbardziej
    widoczny szew na każdym zrzucie. Windows 11 maluje go kolorem tła
    (DWMWA_CAPTION_COLOR), Windows 10 od 20H1 zna tylko tryb ciemny
    (DWMWA_USE_IMMERSIVE_DARK_MODE). Starsze systemy ignorują oba wywołania
    i zostają przy swoim wyglądzie. Wołać po zbudowaniu okna, także dla
    okien Toplevel.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        okno.update_idletasks()
        # winfo_id to okno klienta Tk; ramka z paskiem tytułu jest jego rodzicem.
        hwnd = ctypes.windll.user32.GetParent(okno.winfo_id()) or okno.winfo_id()
        dwm = ctypes.windll.dwmapi

        def ustaw(atrybut: int, wartosc: int) -> bool:
            dana = ctypes.c_int(wartosc)
            return dwm.DwmSetWindowAttribute(
                wintypes.HWND(hwnd), wintypes.DWORD(atrybut),
                ctypes.byref(dana), ctypes.sizeof(dana),
            ) == 0

        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE (19 w kompilacjach sprzed 20H1).
        ustaw(20, 1) or ustaw(19, 1)
        # 35 = DWMWA_CAPTION_COLOR, 34 = DWMWA_BORDER_COLOR — tylko Windows 11.
        ustaw(35, _colorref(BG))
        ustaw(34, _colorref(BORDER))
        # Ramka przerysowuje się dopiero po sygnale o zmianie stylu okna
        # (SWP_FRAMECHANGED bez ruszania położenia, rozmiaru i kolejności).
        ctypes.windll.user32.SetWindowPos(
            wintypes.HWND(hwnd), None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)
    except Exception:
        pass


def _colorref(kolor: str) -> int:
    """Kolor „#rrggbb” jako COLORREF Windows (0x00BBGGRR)."""
    r, g, b = _rgb(kolor)
    return (b << 16) | (g << 8) | r


def pasek_gdy_trzeba(pasek: ttk.Scrollbar, **grid):
    """Zwraca yscrollcommand, który chowa pasek, gdy wszystko mieści się w oknie.

    Pasek przewijania przy czterech wierszach kolejki tylko udaje, że jest co
    przewijać. Ustawia pasek w siatce podanymi opcjami i od razu go chowa;
    pokaże się przy pierwszym zgłoszeniu, że zawartość wystaje.
    """
    pasek.grid(**grid)
    pasek.grid_remove()

    def ustaw(pierwszy, ostatni) -> None:
        pasek.set(pierwszy, ostatni)
        if float(pierwszy) <= 0.0 and float(ostatni) >= 1.0:
            pasek.grid_remove()
        else:
            pasek.grid()

    return ustaw
