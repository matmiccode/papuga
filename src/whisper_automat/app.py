"""Okno aplikacji: przeciągnij plik, dostań transkrypcję."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

from . import __version__
from .core import doctor, download, media, nagrywanie, probe, update
from .core.config import (
    APP_FULL_NAME, APP_ID, APP_NAME, LANGUAGES, WYDANIE, Settings, asset, default_output_dir,
    models_dir, project_root,
)
from .core.engine import Cancelled
from .core.pipeline import (
    Callbacks, JobResult, Runner, clear_cache, model_do_pobrania,
)
from .core.writers import FORMAT_LABELS, FORMATS

APP_TITLE = APP_NAME

from . import theme
from .theme import (
    ACCENT, ACCENT_HOVER, ACCENT_SOFT, BANER_BG, BG, BG_CARD, BG_DROP, BG_INPUT, BG_DROP_HOVER,
    BORDER_DROP, ERR_COLOR, FG, FG_DIM, FG_FAINT, LOG_BG, LOG_FG, OK_COLOR, WARN_COLOR,
)

#: Znak w kolumnie kolejki, którym usuwa się plik.
USUN = "✕"

#: Wiersze lewej kolumny okna: baner nowej wersji, karta nagrywania (tylko
#: wydania z nagrywaniem), pole upuszczania, kolejka, dziennik. Panel
#: ustawień po prawej rozciąga się na wszystkie.
W_BANER, W_NAGRYWANIE, W_DROP, W_KOLEJKA, W_DZIENNIK = 0, 1, 2, 3, 4


def _hms(sekundy: float) -> str:
    """Licznik nagrania: 0:47:12."""
    h, reszta = divmod(int(sekundy), 3600)
    m, s = divmod(reszta, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _skroc(tekst: str, limit: int = 42) -> str:
    return tekst if len(tekst) <= limit else tekst[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Drag & drop jest opcjonalne — bez tkinterdnd2 zostaje wybór przyciskiem.
# ---------------------------------------------------------------------------
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore

    DND_AVAILABLE = True
except ImportError:  # pragma: no cover
    DND_FILES = None  # type: ignore
    TkinterDnD = None  # type: ignore
    DND_AVAILABLE = False


def _zglos_sie_paskowi_zadan() -> None:
    """Nadaje procesowi własną tożsamość w pasku zadań Windows.

    Bez tego system widzi tylko proces pythonw.exe i pokazuje ikonę Pythona,
    niezależnie od tego, jaką ikonę ma samo okno. Trzeba to zrobić przed
    utworzeniem pierwszego okna.
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def _ustaw_ikone(root: tk.Tk) -> None:
    """Podpina ikonę do okna głównego i wszystkich okien potomnych."""
    ikona = asset("icon.ico")
    if ikona is None:
        return
    try:
        # default=... ustawia ikonę aplikacji, dziedziczoną przez okna Toplevel;
        # samo okno główne trzeba oznaczyć osobno.
        root.iconbitmap(default=str(ikona))
        root.iconbitmap(str(ikona))
    except tk.TclError:
        pass


def make_root(ukryj: bool = False) -> tk.Tk:
    _zglos_sie_paskowi_zadan()
    root = None
    if DND_AVAILABLE:
        try:
            root = TkinterDnD.Tk()
        except Exception:
            root = None
    if root is None:
        root = tk.Tk()
    if ukryj:
        # Tk tworzy okno od razu, a tytuł i wygląd nadajemy mu dopiero po
        # wykryciu sprzętu. Bez ukrycia widać w tym czasie bezimienne
        # okienko „tk" na pasku zadań.
        root.withdraw()
    _ustaw_ikone(root)
    return root


class App:
    def __init__(
        self,
        root: tk.Tk,
        initial_files: Optional[List[Path]] = None,
        splash=None,
    ):
        self.root = root
        self.settings = Settings.load()
        self.files: List[Path] = []
        self.durations = {}
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.cancel_flag = threading.Event()
        self.last_output_dir: Optional[Path] = None
        #: Plik, który właśnie się przetwarza (iid w kolejce), i pliki
        #: usunięte z kolejki w trakcie pracy — wątek roboczy je pominie.
        self._biezacy: Optional[str] = None
        self._usuniete: set = set()
        #: Do szacowania pozostałego czasu: kiedy ruszył pierwszy plik,
        #: które pliki są w tej turze, ile z nich skończono, postęp bieżącego.
        self._praca_start: Optional[float] = None
        self._w_toku: List[str] = []
        self._zrobione_w_toku: set = set()
        self._postep_biezacy = 0.0
        self._ostatni_opis = 0.0
        self._opis_pliku = ""

        def krok(tekst: str) -> None:
            if splash is not None:
                splash.status(tekst)

        krok("Wykrywam sprzęt…")
        self.hw = probe.probe()
        self.rec = probe.recommend(self.hw)

        krok("Sprawdzam kartę graficzną…")
        self._gpu_gotowe = doctor.cuda_usable()

        krok("Buduję interfejs…")
        self._build_ui()
        self._apply_settings()
        self._pump_events()

        if initial_files:
            self.add_files(initial_files)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # Po pokazaniu okna — postęp pobierania ma być widać na pasku.
        self.root.after(400, self._pobierz_przy_starcie)
        if WYDANIE.nagrywanie:
            # Nagranie urwane awarią wraca do kolejki — pytanie dopiero,
            # gdy okno już stoi, nie zza ekranu powitalnego.
            self.root.after(1500, self._odzyskaj_nagrania)

        self._aktualizacja = None
        self._sprawdzam = False
        if WYDANIE.aktualizacje:
            update.posprzataj(__version__)
            if self.settings.aktualizacje_auto and update.pora_sprawdzic(
                self.settings.aktualizacje_sprawdzone
            ):
                # Po pobraniu modelu albo w trakcie — zapytanie jest
                # krótkie i idzie osobnym wątkiem, niczego nie blokuje.
                self.root.after(2500, lambda: self.sprawdz_aktualizacje(reczne=False))

    # -- budowa interfejsu -------------------------------------------------

    def _build_ui(self) -> None:
        r = self.root
        r.title(APP_FULL_NAME)
        r.configure(bg=BG)
        r.minsize(980, 660)
        if self.settings.window_geometry:
            try:
                r.geometry(self.settings.window_geometry)
            except tk.TclError:
                r.geometry("1100x820")
        else:
            r.geometry("1100x820")

        theme.zastosuj(r)
        theme.ciemny_pasek_tytulu(r)

        outer = ttk.Frame(r, style="App.TFrame", padding=(20, 16, 20, 14))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        self._build_header(outer)

        # Środek: po lewej to, co się robi (pliki), po prawej — jak.
        srodek = self._srodek = ttk.Frame(outer, style="App.TFrame")
        srodek.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        srodek.columnconfigure(0, weight=1)
        srodek.columnconfigure(1, weight=0, minsize=340)
        # Który wiersz lewej kolumny rośnie, zależy od kolejki: pusta —
        # rośnie pole upuszczania, z plikami — kolejka (_odswiez_pusta_kolejke).
        srodek.rowconfigure(W_KOLEJKA, weight=1)

        # Pasek o nowej wersji stoi nad polem upuszczania, w lewej kolumnie —
        # gdy się pojawi, ściska kolejkę, a nie panel ustawień.
        self._build_banner(srodek)
        if WYDANIE.nagrywanie:
            self._build_recorder(srodek)
        self._build_dropzone(srodek)
        self._build_queue(srodek)
        self._build_settings(srodek)
        # Dziennik w lewej kolumnie, pod kolejką: rozwinięty zabiera miejsce
        # kolejce, a nie ustawieniom.
        self._build_log_panel(srodek)

        self._build_actions(outer)
        self._build_footer(outer)

    def _build_header(self, parent) -> None:
        head = ttk.Frame(parent, style="App.TFrame")
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)

        marka = ttk.Frame(head, style="App.TFrame")
        marka.grid(row=0, column=0, rowspan=2, sticky="w")
        self._logo = self._wczytaj_logo()
        if self._logo is not None:
            tk.Label(marka, image=self._logo, bg=BG).grid(
                row=0, column=0, rowspan=2, padx=(0, 12))
        ttk.Label(marka, text=APP_NAME, style="Title.TLabel").grid(
            row=0, column=1, sticky="sw")
        ttk.Label(
            marka, text=WYDANIE.haslo or "transkrypcja audio i wideo",
            style="Subtitle.TLabel",
        ).grid(row=1, column=1, sticky="nw")

        # Karta graficzna: kropka niesie stan, napis jest cichy. Kolor
        # ostrzeżenia dostaje cały napis, bo wtedy jest co przeczytać.
        gpu = self.hw.gpu
        if gpu and self._gpu_gotowe:
            badge, kropka, napis = gpu.name, OK_COLOR, FG_DIM
        elif gpu:
            badge, kropka, napis = f"{gpu.name}: nieaktywna, liczy procesor", WARN_COLOR, WARN_COLOR
        else:
            badge, kropka, napis = "Procesor, brak karty NVIDIA", WARN_COLOR, WARN_COLOR
        prawy_gorny = tk.Frame(head, bg=BG)
        prawy_gorny.grid(row=0, column=2, sticky="e")
        tk.Label(prawy_gorny, text="●", bg=BG, fg=kropka,
                 font=(theme.FONT, 8)).pack(side="left", padx=(0, 6))
        self.gpu_label = tk.Label(
            prawy_gorny, text=badge, bg=BG, fg=napis, font=(theme.FONT, 9)
        )
        self.gpu_label.pack(side="left")

        # Druga linia: odnośniki. Kawa stoi tu, cicho, a nie jako żółty
        # przycisk wyżej — jedynym głośnym elementem okna ma być „Transkrybuj”,
        # a w trakcie pracy pasek z piór.
        podpis = tk.Frame(head, bg=BG)
        podpis.grid(row=1, column=2, sticky="e", pady=(6, 0))
        self._link(podpis, "Jak to działa?", self.pokaz_pomoc).pack(side="left")
        if WYDANIE.wsparcie_url:
            self._przycisk_kawy(podpis).pack(side="left", padx=(16, 0))
        # Wydanie z podpisem autora w stopce pokazuje tu sam numer wersji —
        # nazwisko ma stać w jednym miejscu, nie w dwóch.
        ttk.Label(
            podpis,
            text=(f"wersja {__version__}" if WYDANIE.autor
                  else f"{WYDANIE.wydawca}, wersja {__version__}"),
            style="Dim.TLabel",
        ).pack(side="left", padx=(16, 0))


    def _przycisk_kawy(self, parent) -> tk.Label:
        """Odnośnik wsparcia: żółta filiżanka i napis, bez wypełnienia.

        Żółć z piór zostaje znakiem rozpoznawczym, ale po cichu. Wcześniej
        żółty kafelek był najgłośniejszym punktem okna i konkurował
        z przyciskiem Transkrybuj oraz z paskiem postępu.
        """
        przycisk = tk.Label(
            parent, text="Postaw kawę autorowi", bg=BG, fg=theme.KAWA,
            cursor="hand2", font=(theme.FONT, 9),
        )
        plik = asset("ui/kawa-zolta.png")
        if plik is not None:
            try:
                self._ikona_kawy = tk.PhotoImage(master=self.root, file=str(plik))
                przycisk.configure(image=self._ikona_kawy, compound="left",
                                   text="  " + przycisk.cget("text"))
            except tk.TclError:
                pass
        przycisk.bind("<Button-1>", lambda _e: webbrowser.open(WYDANIE.wsparcie_url))
        przycisk.bind("<Enter>", lambda _e: przycisk.configure(
            fg=theme.KAWA_HOVER, font=(theme.FONT, 9, "underline")))
        przycisk.bind("<Leave>", lambda _e: przycisk.configure(
            fg=theme.KAWA, font=(theme.FONT, 9)))
        return przycisk

    def _wczytaj_logo(self) -> Optional[tk.PhotoImage]:
        plik = asset("logo-40.png")
        if plik is None:
            return None
        try:
            return tk.PhotoImage(master=self.root, file=str(plik))
        except tk.TclError:
            return None

    def _link(self, parent, tekst: str, akcja, tlo: str = BG) -> tk.Label:
        """Napis, który zachowuje się jak odnośnik na stronie."""
        etykieta = tk.Label(
            parent, text=tekst, bg=tlo, fg=ACCENT_HOVER, cursor="hand2",
            font=(theme.FONT, 9),
        )
        etykieta.bind("<Button-1>", lambda _e: akcja())
        etykieta.bind("<Enter>", lambda _e: etykieta.configure(
            font=(theme.FONT, 9, "underline")))
        etykieta.bind("<Leave>", lambda _e: etykieta.configure(font=(theme.FONT, 9)))
        return etykieta

    def _build_links(self, stopka) -> None:
        """Podpis autora, diagnostyka, aktualizacje, zgłoszenia, kontakt.

        Wszystko poza diagnostyką i licencjami tylko wtedy, gdy wydanie to ma.
        """
        linki = [("Diagnostyka", self.show_diagnosis)]
        if WYDANIE.aktualizacje:
            linki.append(("Sprawdź aktualizacje", self.sprawdz_aktualizacje))
        if WYDANIE.zgloszenia_url:
            linki.append(("Zgłoś problem", lambda: webbrowser.open(WYDANIE.zgloszenia_url)))
        if WYDANIE.kontakt_email:
            linki.append(("Kontakt", lambda: webbrowser.open(
                f"mailto:{WYDANIE.kontakt_email}?subject={APP_TITLE}%20{__version__}")))
        linki.append(("Licencje", self.pokaz_licencje))
        if not linki:
            return

        rzad = tk.Frame(stopka, bg=BG)
        rzad.grid(row=0, column=2, sticky="e")
        if WYDANIE.autor:
            # Cicho, tym samym szarym co numer wersji w nagłówku — podpis,
            # nie reklama.
            ttk.Label(rzad, text=WYDANIE.autor, style="Dim.TLabel").pack(
                side="left", padx=(0, 24))
        for i, (tekst, akcja) in enumerate(linki):
            self._link(rzad, tekst, akcja).pack(side="left", padx=(18 if i else 0, 0))

    def _build_banner(self, parent) -> None:
        """Pasek „Dostępna nowa wersja” — ukryty, dopóki jej nie ma."""
        self.baner = tk.Frame(parent, bg=BANER_BG, padx=14, pady=10,
                              highlightthickness=1, highlightbackground=ACCENT)
        self.baner.grid(row=W_BANER, column=0, sticky="ew", pady=(0, 12), padx=(0, 14))
        self.baner.columnconfigure(0, weight=1)
        self.baner_tekst = tk.Label(
            self.baner, text="", bg=BANER_BG, fg=FG,
            font=(theme.FONT_SEMI, 10), anchor="w",
        )
        self.baner_tekst.grid(row=0, column=0, sticky="w")
        rzad = tk.Frame(self.baner, bg=BANER_BG)
        rzad.grid(row=1, column=0, sticky="w", pady=(8, 0))
        przyciski = [
            ("Zaktualizuj teraz", self.zaktualizuj),
            ("Co nowego", self.co_nowego),
            ("Pomiń tę wersję", self.pomin_wersje),
            ("Później", self._ukryj_baner),
        ]
        for i, (tekst, akcja) in enumerate(przyciski):
            ttk.Button(rzad, text=tekst, command=akcja,
                       style="Accent.TButton" if i == 0 else "TButton").grid(
                row=0, column=i, padx=(8 if i else 0, 0))
        self.baner.grid_remove()

    def _build_dropzone(self, parent) -> None:
        """Pole z przerywaną ramką — wygląda na miejsce, w które coś się kładzie."""
        self.drop = tk.Canvas(parent, bg=BG, height=104, highlightthickness=0,
                              borderwidth=0, cursor="hand2")
        self.drop.grid(row=W_DROP, column=0, sticky="ew", pady=(0, 12), padx=(0, 14))
        self._drop_nad = False
        #: Pusta kolejka: pole zajmuje całą lewą kolumnę i jest zaproszeniem,
        #: a nie wąskim paskiem nad pustą tabelą. Z plikami kurczy się do paska.
        self._drop_duzy = False
        self.drop.bind("<Configure>", lambda _e: self._rysuj_drop())
        self.drop.bind("<Button-1>", lambda _e: self.browse_files())
        self.drop.bind("<Enter>", lambda _e: self._drop_color(BG_DROP_HOVER))
        self.drop.bind("<Leave>", lambda _e: self._drop_color(BG_DROP))

        if DND_AVAILABLE:
            try:
                self.drop.drop_target_register(DND_FILES)
                self.drop.dnd_bind("<<Drop>>", self._on_drop)
                self.drop.dnd_bind("<<DragEnter>>", lambda _e: self._drop_color(BG_DROP_HOVER))
                self.drop.dnd_bind("<<DragLeave>>", lambda _e: self._drop_color(BG_DROP))
            except Exception:
                pass

    def _rysuj_drop(self) -> None:
        c = self.drop
        c.delete("all")
        szer, wys = c.winfo_width(), c.winfo_height()
        if szer <= 1:
            return
        nad = self._drop_nad
        theme.zaokraglony(c, 1, 1, szer - 2, wys - 2, 10,
                          fill=BG_DROP_HOVER if nad else BG_DROP,
                          outline=ACCENT if nad else BORDER_DROP, dash=(6, 4))

        glowny = ("Przeciągnij tutaj nagrania lub folder" if DND_AVAILABLE
                  else "Kliknij, aby wybrać nagrania")
        dodatek = ("albo kliknij i wybierz z dysku: audio lub wideo"
                   if DND_AVAILABLE else "audio lub wideo")
        if self._drop_duzy:
            self._rysuj_drop_duzy(c, szer, wys, nad, glowny, dodatek)
            return
        f_glowny, f_dodatek = (theme.FONT_SEMI, 12), (theme.FONT, 9)
        tekst_szer = max(tkfont.Font(font=f_glowny).measure(glowny),
                         tkfont.Font(font=f_dodatek).measure(dodatek))
        ikona, odstep = 40, 16
        x = (szer - ikona - odstep - tekst_szer) // 2
        srodek = wys // 2
        c.create_oval(x, srodek - ikona // 2, x + ikona, srodek + ikona // 2,
                      fill=ACCENT_SOFT if nad else BG_INPUT, width=0)
        c.create_text(x + ikona // 2, srodek, text=theme.STRZALKA[0],
                      font=(theme.STRZALKA[1], 14), fill=FG if nad else ACCENT_HOVER)
        x += ikona + odstep
        c.create_text(x, srodek - 10, text=glowny, font=f_glowny, fill=FG, anchor="w")
        c.create_text(x, srodek + 12, text=dodatek, font=f_dodatek, fill=FG_DIM, anchor="w")

    def _rysuj_drop_duzy(self, c, szer: int, wys: int, nad: bool,
                         glowny: str, dodatek: str) -> None:
        """Układ pionowy na pustą kolejkę: ikona, zaproszenie, co można wrzucić."""
        ikona = 72
        x, y = szer // 2, max(wys // 2 - 40, ikona)
        c.create_oval(x - ikona // 2, y - ikona // 2, x + ikona // 2, y + ikona // 2,
                      fill=ACCENT_SOFT if nad else BG_INPUT, width=0)
        c.create_text(x, y, text=theme.STRZALKA[0], font=(theme.STRZALKA[1], 26),
                      fill=FG if nad else ACCENT_HOVER)
        c.create_text(x, y + ikona // 2 + 30, text=glowny,
                      font=(theme.FONT_SEMI, 15), fill=FG)
        c.create_text(x, y + ikona // 2 + 58, text=dodatek,
                      font=(theme.FONT, 10), fill=FG_DIM)
        c.create_text(x, y + ikona // 2 + 90,
                      text="MP4, MKV, MOV, MP3, WAV, M4A i inne. Wiele plików naraz.",
                      font=(theme.FONT, 9), fill=FG_FAINT)

    def _drop_color(self, color: str) -> None:
        self._drop_nad = color == BG_DROP_HOVER
        self._rysuj_drop()

    # -- nagrywanie spotkań ------------------------------------------------

    def _build_recorder(self, parent) -> None:
        """Karta nagrywania: przycisk, licznik, dwa wskaźniki poziomu, źródła.

        Jeden klik rusza z zapamiętanymi (albo domyślnymi) urządzeniami.
        Wskaźniki są po to, żeby od razu było widać, że oba źródła żyją —
        najczęstszy błąd to Teams grający na inne urządzenie niż nagrywane.
        """
        self.nagrywarka = nagrywanie.Nagrywarka()
        self._nagranie_plik: Optional[Path] = None
        self._nagranie_kropka = False
        self._nagranie_sys_cicho_od: Optional[float] = None
        self._nagranie_podpowiedziano = False
        self._po_pracy_transkrybuj = False
        self._okno_urzadzen = None

        karta = theme.karta(parent, row=W_NAGRYWANIE, column=0, sticky="ew",
                            pady=(0, 12), padx=(0, 14))
        karta.columnconfigure(0, weight=1)
        wnetrze = ttk.Frame(karta, style="Card.TFrame", padding=(16, 12, 16, 10))
        wnetrze.grid(row=0, column=0, sticky="ew")
        wnetrze.columnconfigure(3, weight=1)

        self.rec_btn = ttk.Button(wnetrze, text="Nagrywaj spotkanie",
                                  command=self.przelacz_nagrywanie)
        self.rec_btn.grid(row=0, column=0, rowspan=2, sticky="w")

        # Licznik z kropką, która miga w trakcie nagrywania.
        licznik = tk.Frame(wnetrze, bg=BG_CARD)
        licznik.grid(row=0, column=1, rowspan=2, sticky="w", padx=(18, 10))
        self.rec_kropka = tk.Label(licznik, text="●", bg=BG_CARD, fg=BG_CARD,
                                   font=(theme.FONT, 10))
        self.rec_kropka.pack(side="left", padx=(0, 4))
        self.rec_czas = tk.Label(licznik, text="0:00:00", bg=BG_CARD, fg=FG_DIM,
                                 font=(theme.FONT_SEMI, 15), width=7, anchor="w")
        self.rec_czas.pack(side="left")

        # Dwa cienkie wskaźniki poziomu: mikrofon i dźwięk spotkania.
        self.rec_paski = {}
        for i, (klucz, tekst) in enumerate((("mikrofon", "Mikrofon"),
                                            ("system", "Dźwięk spotkania"))):
            tk.Label(wnetrze, text=tekst, bg=BG_CARD, fg=FG_DIM, font=(theme.FONT, 9),
                     anchor="w", width=15).grid(row=i, column=2, sticky="w", padx=(10, 8))
            pasek = theme.PasekPostepu(wnetrze, maximum=1000, grubosc=4, podloze=BG_CARD)
            pasek.grid(row=i, column=3, sticky="ew", pady=(6, 6) if i == 0 else (2, 2))
            self.rec_paski[klucz] = pasek

        # Źródła: cicha linia z odnośnikiem do zmiany.
        zrodla = tk.Frame(wnetrze, bg=BG_CARD)
        zrodla.grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))
        self.rec_zrodla = tk.Label(zrodla, text="", bg=BG_CARD, fg=FG_FAINT,
                                   font=(theme.FONT, 9), anchor="w")
        self.rec_zrodla.pack(side="left")
        self._link(zrodla, "Zmień…", self.wybierz_urzadzenia, tlo=BG_CARD).pack(
            side="left", padx=(8, 0))
        self._odswiez_zrodla()

    def _nagrywa(self) -> bool:
        nagrywarka = getattr(self, "nagrywarka", None)
        return nagrywarka is not None and nagrywarka.trwa

    def _odswiez_zrodla(self) -> None:
        s = self.settings
        mik = s.nagranie_mikrofon or "domyślny"
        system = s.nagranie_glosniki or "domyślne urządzenie odtwarzania"
        self.rec_zrodla.configure(
            text=f"Mikrofon: {_skroc(mik)}  ·  Dźwięk spotkania z: {_skroc(system)}")

    def przelacz_nagrywanie(self) -> None:
        if self._nagrywa():
            self.zatrzymaj_nagrywanie()
        else:
            self.rozpocznij_nagrywanie()

    def _katalog_nagran(self) -> Path:
        return Path(self.settings.output_dir or str(default_output_dir())) / "Nagrania"

    def rozpocznij_nagrywanie(self) -> None:
        settings = self._collect_settings()
        settings.save()
        put = self.events.put
        zdarzenia = nagrywanie.ZdarzeniaNagrywania(
            poziomy=lambda m, s: put(("nagranie_poziomy", m, s)),
            czas=lambda s: put(("nagranie_czas", s)),
            ostrzezenie=lambda t: put(("nagranie_ostrzezenie", t)),
            blad=lambda t: put(("nagranie_blad", t)),
            zakonczono=lambda p, s: put(("nagranie_koniec", str(p), s)),
            anulowano=lambda: put(("nagranie_anulowane",)),
        )
        try:
            plik = self.nagrywarka.start(
                self._katalog_nagran(), settings.nagranie_mikrofon,
                settings.nagranie_glosniki, zdarzenia)
        except nagrywanie.BladNagrywania as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self.root)
            return
        self._nagranie_plik = plik
        self._nagranie_sys_cicho_od = None
        self._nagranie_podpowiedziano = False
        self._ustaw_stan_nagrywania(True)
        self.log(f"Nagrywam spotkanie: {plik.name}")
        self.status_var.set("Nagrywam. Poinformuj uczestników, że spotkanie jest nagrywane.")

    def zatrzymaj_nagrywanie(self) -> None:
        self.rec_btn.configure(state="disabled", text="Zapisuję…")
        self.status_var.set("Kończę nagranie…")
        self.nagrywarka.stop()

    def _ustaw_stan_nagrywania(self, nagrywa: bool) -> None:
        if nagrywa:
            self.rec_btn.configure(text="Zatrzymaj nagranie", style="Stop.TButton",
                                   state="normal")
            self.rec_czas.configure(fg=FG)
            self.start_btn.configure(state="disabled")
            self._migaj_kropka()
        else:
            self.rec_btn.configure(text="Nagrywaj spotkanie", style="TButton", state="normal")
            self.rec_czas.configure(text="0:00:00", fg=FG_DIM)
            self.rec_kropka.configure(fg=BG_CARD)
            for pasek in self.rec_paski.values():
                pasek["value"] = 0
            if not self._busy():
                self.start_btn.configure(state="normal")

    def _migaj_kropka(self) -> None:
        if not self._nagrywa():
            self.rec_kropka.configure(fg=BG_CARD)
            return
        self._nagranie_kropka = not self._nagranie_kropka
        self.rec_kropka.configure(fg=ERR_COLOR if self._nagranie_kropka else BG_CARD)
        self.root.after(700, self._migaj_kropka)

    def _pokaz_poziomy(self, mik: float, system: float) -> None:
        for klucz, dbfs in (("mikrofon", mik), ("system", system)):
            self.rec_paski[klucz]["value"] = max(0.0, min(1.0, (dbfs + 60.0) / 60.0)) * 1000
        # Mikrofon żyje, a dźwięk spotkania milczy od pół minuty — Teams gra
        # pewnie na inne urządzenie niż to, z którego nagrywamy.
        teraz = time.monotonic()
        if system > -60.0:
            self._nagranie_sys_cicho_od = None
        elif self._nagranie_sys_cicho_od is None:
            self._nagranie_sys_cicho_od = teraz
        elif (not self._nagranie_podpowiedziano and mik > -50.0
              and teraz - self._nagranie_sys_cicho_od > 30.0):
            self._nagranie_podpowiedziano = True
            tekst = ("Nie słychać dźwięku spotkania. Sprawdź, na jakie urządzenie gra "
                     "Teams, i wskaż je w „Zmień…”.")
            self.status_var.set(tekst)
            self.log(f"UWAGA (nagrywanie): {tekst}")

    def _po_nagraniu(self, plik: Path, sekundy: float) -> None:
        self._ustaw_stan_nagrywania(False)
        self._nagranie_plik = None
        opis = media.format_duration(sekundy)
        self.log(f"Nagranie zapisane: {plik.name} ({opis}).")
        for nazwa, st in self.nagrywarka.statystyki.items():
            if st["luki"] or st["korekty"] or st["odrzucone"] or st["wyprzedzenia"]:
                self.log(f"  {nazwa}: luki {st['luki']}, korekty dryfu {st['korekty']}, "
                         f"odrzucone próbki {st['odrzucone']}, wyprzedzenia {st['wyprzedzenia']}")
        self.add_files([plik])
        if not self.settings.nagranie_transkrybuj:
            self.status_var.set(f"Nagranie zapisane ({opis}). Czeka w kolejce.")
        elif self._busy():
            self._po_pracy_transkrybuj = True
            self.status_var.set(f"Nagranie zapisane ({opis}). Transkrypcja ruszy po bieżącej pracy.")
        else:
            self.status_var.set(f"Nagranie zapisane ({opis}). Zaczynam transkrypcję…")
            self.root.after(300, lambda: self.start(tylko_nowe=True))

    def wybierz_urzadzenia(self) -> None:
        """Małe okno: mikrofon i urządzenie, z którego bierzemy dźwięk spotkania."""
        if self._nagrywa():
            messagebox.showinfo(APP_TITLE, "Źródła zmienisz po zatrzymaniu nagrania.",
                                parent=self.root)
            return
        if self._okno_urzadzen is not None and self._okno_urzadzen[0].winfo_exists():
            self._okno_urzadzen[0].lift()
            return
        okno = tk.Toplevel(self.root)
        okno.title("Źródła nagrania")
        okno.configure(bg=BG)
        okno.resizable(False, False)
        okno.transient(self.root)
        tresc = tk.Frame(okno, bg=BG, padx=24, pady=18)
        tresc.pack(fill="both", expand=True)
        tresc.columnconfigure(0, weight=1)

        DOMYSLNE = "Domyślne (ustawienie Windows)"
        pola = {}
        opisy = (("mikrofon", "Mikrofon"),
                 ("glosniki", "Dźwięk spotkania z urządzenia (tego, na którym gra Teams)"))
        for i, (klucz, tekst) in enumerate(opisy):
            tk.Label(tresc, text=tekst, bg=BG, fg=FG_DIM, font=(theme.FONT, 9),
                     anchor="w").grid(row=2 * i, column=0, sticky="w",
                                      pady=((0 if i == 0 else 12), 2))
            var = tk.StringVar(value="Wczytuję listę urządzeń…")
            box = ttk.Combobox(tresc, textvariable=var, state="readonly", width=56)
            box.grid(row=2 * i + 1, column=0, sticky="ew")
            pola[klucz] = (var, box)
        tk.Label(
            tresc, bg=BG, fg=FG_FAINT, font=(theme.FONT, 9), justify="left",
            wraplength=440, anchor="w",
            text="Najlepiej w słuchawkach: przy głośnikach mikrofon zbiera rozmówców "
                 "drugi raz, z opóźnieniem. Zmiany działają od następnego nagrania.",
        ).grid(row=4, column=0, sticky="w", pady=(14, 0))

        def zapisz() -> None:
            for klucz, (var, _box) in pola.items():
                wybor = var.get()
                if wybor == DOMYSLNE or wybor.startswith("Wczytuję") or not wybor:
                    wybor = ""
                setattr(self.settings, f"nagranie_{klucz}", wybor)
            self.settings.save()
            self._odswiez_zrodla()
            okno.destroy()

        przyciski = tk.Frame(tresc, bg=BG)
        przyciski.grid(row=5, column=0, sticky="e", pady=(18, 0))
        ttk.Button(przyciski, text="Anuluj", command=okno.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(przyciski, text="Zapisz", style="Accent.TButton", command=zapisz).pack(
            side="right")
        okno.bind("<Escape>", lambda _e: okno.destroy())
        okno.update_idletasks()
        theme.ciemny_pasek_tytulu(okno)
        x = self.root.winfo_rootx() + (self.root.winfo_width() - okno.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + 120
        okno.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self._okno_urzadzen = (okno, pola, DOMYSLNE)

        def wczytaj() -> None:
            try:
                wejscia, wyjscia = nagrywanie.lista_urzadzen()
            except Exception as exc:
                self.events.put(("nagranie_urzadzenia", None, None, str(exc)))
                return
            self.events.put(("nagranie_urzadzenia",
                             [u.nazwa for u in wejscia],
                             [u.nazwa for u in wyjscia if u.loopback_index is not None],
                             ""))

        threading.Thread(target=wczytaj, daemon=True).start()

    def _wypelnij_urzadzenia(self, wejscia, wyjscia, blad: str) -> None:
        if self._okno_urzadzen is None or not self._okno_urzadzen[0].winfo_exists():
            return
        _okno, pola, DOMYSLNE = self._okno_urzadzen
        if blad:
            for var, box in pola.values():
                var.set(f"Nie udało się odczytać urządzeń: {blad[:60]}")
            return
        for klucz, lista, zapisane in (("mikrofon", wejscia, self.settings.nagranie_mikrofon),
                                       ("glosniki", wyjscia, self.settings.nagranie_glosniki)):
            var, box = pola[klucz]
            box.configure(values=[DOMYSLNE] + list(lista))
            var.set(zapisane if zapisane in lista else DOMYSLNE)

    def _odzyskaj_nagrania(self) -> None:
        """Po awarii: niedokończone nagrania z folderu wracają do kolejki."""
        try:
            pliki = nagrywanie.znajdz_niedokonczone(self._katalog_nagran())
        except Exception:
            return
        if not pliki:
            return
        opis = "\n".join(f"•  {p.name}" for p in pliki)
        pytanie = (f"Poprzednie nagrywanie nie zostało poprawnie zakończone:\n\n{opis}\n\n"
                   f"Odzyskać nagranie i dodać je do kolejki?")
        if not messagebox.askyesno(APP_TITLE, pytanie, parent=self.root):
            for p in pliki:
                nagrywanie.usun_znacznik(p)
            self.log("Niedokończone nagranie zostaje w folderze Nagrania bez zmian.")
            return
        self.status_var.set("Odzyskuję nagranie…")

        def praca() -> None:
            for p in pliki:
                try:
                    cel = nagrywanie.napraw_nagranie(p)
                    self.events.put(("nagranie_odzyskane", str(cel), ""))
                except Exception as exc:
                    self.events.put(("nagranie_odzyskane", str(p), str(exc)))

        threading.Thread(target=praca, daemon=True).start()

    def _po_odzyskaniu(self, sciezka: str, blad: str) -> None:
        if blad:
            self.log(f"Nie udało się odzyskać {Path(sciezka).name}: {blad}")
            self.status_var.set("Odzyskanie nagrania nie powiodło się.")
            return
        self.log(f"Odzyskano niedokończone nagranie: {Path(sciezka).name}")
        self.status_var.set("Odzyskane nagranie czeka w kolejce.")
        self.add_files([Path(sciezka)])

    def _build_queue(self, parent) -> None:
        wrap = self._karta_kolejki = theme.karta(parent, row=W_KOLEJKA, column=0,
                                                 sticky="nsew", padx=(0, 14))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(1, weight=1)

        bar = ttk.Frame(wrap, style="Card.TFrame", padding=(16, 12, 10, 6))
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="Kolejka", style="Card.Heading.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.podsumowanie = ttk.Label(bar, text="", style="Card.Dim.TLabel")
        self.podsumowanie.grid(row=0, column=1, sticky="w", padx=(10, 0), pady=(2, 0))
        ttk.Button(bar, text="Usuń zaznaczone", style="Ghost.TButton",
                   command=self.remove_selected).grid(row=0, column=2, padx=(6, 0))
        ttk.Button(bar, text="Wyczyść", style="Ghost.TButton",
                   command=self.clear_queue).grid(row=0, column=3, padx=(2, 0))

        self.tree = ttk.Treeview(
            wrap, columns=("dlugosc", "status", "usun"), show="tree headings", height=8
        )
        self.tree.heading("#0", text="Plik", anchor="w")
        self.tree.heading("dlugosc", text="Długość")
        self.tree.heading("status", text="Status", anchor="w")
        # Nazwa pliku zabiera resztę miejsca — szerokość startowa jest mała,
        # żeby krzyżyk na końcu wiersza mieścił się także w wąskim oknie.
        self.tree.column("#0", width=200, minwidth=160, anchor="w")
        self.tree.column("dlugosc", width=80, anchor="center", stretch=False)
        self.tree.column("status", width=210, anchor="w", stretch=False)
        self.tree.heading("usun", text="")
        self.tree.column("usun", width=34, anchor="center", stretch=False)
        self.tree.bind("<Button-1>", self._klik_w_kolejce)
        self.tree.bind("<Motion>", self._ruch_w_kolejce)
        self.tree.bind("<Delete>", lambda _e: self.remove_selected())
        self.tree.bind("<BackSpace>", lambda _e: self.remove_selected())
        self.tree.bind("<Button-3>", self._menu_kolejki)
        self._menu = tk.Menu(self.root, tearoff=False, bg=BG_INPUT, fg=FG,
                             activebackground=ACCENT, activeforeground="#ffffff",
                             borderwidth=0, font=(theme.FONT, 10))
        self._menu.add_command(label="Usuń z kolejki", command=self.remove_selected)
        self._menu.add_command(label="Wyczyść kolejkę", command=self.clear_queue)
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(0, 8))

        # Gotowe pliki cichną — uwaga zostaje przy tych, które jeszcze czekają.
        self.tree.tag_configure("ok", foreground=FG_DIM)
        self.tree.tag_configure("err", foreground=ERR_COLOR)
        self.tree.tag_configure("run", foreground=ACCENT_HOVER)

        # Pasek przewijania tylko wtedy, gdy jest co przewijać.
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=theme.pasek_gdy_trzeba(
            scroll, row=1, column=1, sticky="ns", padx=(0, 4), pady=(0, 6)))

        self._odswiez_pusta_kolejke()

    def _odswiez_pusta_kolejke(self) -> None:
        """Pusta kolejka: karta kolejki znika, a pole upuszczania rośnie na całą kolumnę.

        Nowy użytkownik widział wąski pasek nad wielką pustą tabelą z nagłówkami
        kolumn i paskiem przewijania bez treści. Pusty ekran ma zapraszać.
        """
        pusta = not self.tree.get_children()
        if pusta != self._drop_duzy:
            self._drop_duzy = pusta
            if pusta:
                self._karta_kolejki.grid_remove()
                self._srodek.rowconfigure(W_DROP, weight=1)
                self._srodek.rowconfigure(W_KOLEJKA, weight=0)
                self.drop.grid(sticky="nsew")
            else:
                self._karta_kolejki.grid()
                self._srodek.rowconfigure(W_DROP, weight=0)
                self._srodek.rowconfigure(W_KOLEJKA, weight=1)
                self.drop.grid(sticky="ew")
            self._rysuj_drop()
        self._odswiez_podsumowanie()

    def _odswiez_podsumowanie(self) -> None:
        """„4 pliki, łącznie 2:53:48” obok nagłówka kolejki."""
        n = len(self.files)
        if not n:
            self.podsumowanie.configure(text="")
            return
        tekst = _liczba_plikow(n)
        dlugosci = [self.durations[str(f)] for f in self.files if str(f) in self.durations]
        if dlugosci:
            tekst += f", łącznie {media.format_duration(sum(dlugosci))}"
        self.podsumowanie.configure(text=tekst)

    def _build_settings(self, parent) -> None:
        karta = theme.karta(parent, row=0, column=1, rowspan=W_DZIENNIK + 1, sticky="nsew")
        # W niskim oknie panel się przewija, zamiast chować dolne sekcje.
        przewijany = theme.Przewijany(karta, tlo=BG_CARD)
        przewijany.pack(fill="both", expand=True)
        panel = przewijany.wnetrze
        panel.configure(padding=(18, 12, 18, 16))
        panel.columnconfigure(0, weight=1)

        def sekcja(wiersz: int, tekst: str) -> None:
            ttk.Label(panel, text=tekst, style="Card.Section.TLabel").grid(
                row=wiersz, column=0, sticky="w", pady=(16, 5))

        ttk.Label(panel, text="Ustawienia", style="Card.Heading.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8))

        # Modelu nie wybiera się w oknie: program dobiera go sam do sprzętu,
        # a na każdym rozsądnym komputerze jest to large-v3-turbo — mniejsze
        # na procesorze nie są szybsze, a gubią słowa (pomiary w PROGRESS.md).
        # Inny model można wymusić w trybie konsolowym: --model.
        ttk.Label(panel, text="Język nagrania", style="Card.Dim.TLabel").grid(
            row=3, column=0, sticky="w")
        self.lang_var = tk.StringVar()
        self.lang_box = ttk.Combobox(
            panel, textvariable=self.lang_var,
            values=[label for _c, label in LANGUAGES], state="readonly",
        )
        self.lang_box.grid(row=4, column=0, sticky="ew", pady=(2, 6))

        ttk.Label(panel, text="Kontekst (nazwy, skróty, terminy)",
                  style="Card.Dim.TLabel").grid(row=5, column=0, sticky="w")
        self.prompt_var = tk.StringVar()
        theme.pole(panel, self.prompt_var,
                   podpowiedz="np. Kowalski, KSeF, Allegro").grid(
            row=6, column=0, sticky="ew", ipady=5, pady=(2, 0))

        # Formaty.
        sekcja(7, "Zapisz jako")
        fmt = ttk.Frame(panel, style="Card.TFrame")
        fmt.grid(row=8, column=0, sticky="ew")
        self.format_vars = {}
        for i, nazwa in enumerate(FORMATS):
            var = tk.BooleanVar(value=nazwa in self.settings.formats)
            self.format_vars[nazwa] = var
            ttk.Checkbutton(fmt, text=FORMAT_LABELS[nazwa], variable=var,
                            style="Card.TCheckbutton").grid(
                row=i, column=0, sticky="w")

        # Miejsce zapisu.
        sekcja(9, "Folder wyników")
        out_row = ttk.Frame(panel, style="Card.TFrame")
        out_row.grid(row=10, column=0, sticky="ew")
        out_row.columnconfigure(0, weight=1)
        self.outdir_var = tk.StringVar()
        self.outdir_entry = theme.pole(out_row, self.outdir_var)
        self.outdir_entry.grid(row=0, column=0, sticky="ew", ipady=5, padx=(0, 6))
        ttk.Button(out_row, text="Zmień…", command=self.choose_output).grid(
            row=0, column=1)

        self.next_to_source = tk.BooleanVar()
        ttk.Checkbutton(
            panel, text="Zapisuj obok pliku źródłowego", variable=self.next_to_source,
            command=self._toggle_outdir, style="Card.TCheckbutton",
        ).grid(row=11, column=0, sticky="w", pady=(6, 0))

        # Rozpoznawanie mówców.
        sekcja(12, "Mówcy")
        mowcy = ttk.Frame(panel, style="Card.TFrame")
        mowcy.grid(row=13, column=0, sticky="ew")
        mowcy.columnconfigure(0, weight=1)
        self.diarize_var = tk.BooleanVar()
        ttk.Checkbutton(
            mowcy, text="Rozpoznaj, kto co powiedział", variable=self.diarize_var,
            command=self._toggle_diarize, style="Card.TCheckbutton",
        ).grid(row=0, column=0, sticky="w")

        ttk.Label(mowcy, text="Ile osób:", style="Card.TLabel").grid(
            row=0, column=1, padx=(8, 6))
        self.speakers_var = tk.StringVar(value="2")
        # Pole jest edytowalne. Stan "readonly" zostawiał działające tylko
        # strzałki: liczba wpisana z klawiatury była po cichu ignorowana
        # i program liczył dalej ze starą wartością.
        sprawdz = mowcy.register(self._tylko_liczba_osob)
        self.speakers_box = ttk.Spinbox(
            mowcy, from_=0, to=20, width=4, textvariable=self.speakers_var,
            validate="key", validatecommand=(sprawdz, "%P"),
        )
        self.speakers_box.grid(row=0, column=2)

        # Wskazówka, nie ostrzeżenie — w kolorze ostrzeżenia czytała się jak błąd.
        self.diarize_hint = tk.Label(
            panel, text="", bg=BG_CARD, fg=FG_DIM, font=(theme.FONT, 9),
            justify="left", anchor="w", wraplength=310,
        )
        self.diarize_hint.grid(row=14, column=0, sticky="w", pady=(6, 0))

        # Nagrywanie spotkań: urządzenia wybiera się w karcie nagrywania
        # („Zmień…”), tu zostaje tylko to, co dzieje się po zatrzymaniu.
        if WYDANIE.nagrywanie:
            sekcja(15, "Nagrywanie")
            self.rec_auto_var = tk.BooleanVar(value=True)
            ttk.Checkbutton(
                panel, text="Transkrybuj od razu po zatrzymaniu",
                variable=self.rec_auto_var, style="Card.TCheckbutton",
            ).grid(row=16, column=0, sticky="w")

    def _build_actions(self, parent) -> None:
        """Pasek na dole: postęp po lewej, przyciski po prawej."""
        row = ttk.Frame(parent, style="App.TFrame")
        row.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        row.columnconfigure(0, weight=1)

        postep = ttk.Frame(row, style="App.TFrame")
        postep.grid(row=0, column=0, sticky="ew", padx=(0, 20))
        postep.columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value="Gotowy.")
        ttk.Label(postep, textvariable=self.status_var, style="Status.TLabel").grid(
            row=0, column=0, sticky="w")
        self.overall_var = tk.StringVar(value="")
        ttk.Label(postep, textvariable=self.overall_var, style="Dim.TLabel").grid(
            row=0, column=1, sticky="e")
        # Górny pasek: bieżący plik. Dolny, cieńszy: cała kolejka.
        self.file_progress = theme.PasekPostepu(
            postep, maximum=1000, grubosc=6,
            kolory=theme.PIORA if WYDANIE.kod == "papuga" else None)
        self.file_progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.overall_progress = theme.PasekPostepu(
            postep, maximum=1000, grubosc=3, kolor=theme.FG_FAINT)
        self.overall_progress.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        przyciski = ttk.Frame(row, style="App.TFrame")
        przyciski.grid(row=0, column=1, sticky="e")
        ttk.Button(przyciski, text="Otwórz wyniki", command=self.open_output).grid(
            row=0, column=0, padx=(0, 8))
        self.cancel_btn = ttk.Button(
            przyciski, text="Przerwij", command=self.cancel, state="disabled"
        )
        self.cancel_btn.grid(row=0, column=1, padx=(0, 8))
        self.start_btn = ttk.Button(
            przyciski, text="Transkrybuj", style="Accent.TButton", command=self.start
        )
        self.start_btn.grid(row=0, column=2)

    def _build_footer(self, parent) -> None:
        stopka = ttk.Frame(parent, style="App.TFrame")
        stopka.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        stopka.columnconfigure(1, weight=1)

        self._przelacznik = self._link(stopka, "▸  Pokaż dziennik", self._przelacz_dziennik)
        self._przelacznik.grid(row=0, column=0, sticky="w")
        self._build_links(stopka)

    def _build_log_panel(self, parent) -> None:
        """Dziennik — zwinięty, bo zwykle wystarcza status nad paskiem."""
        self._dziennik_widoczny = False
        self._dziennik = theme.karta(parent)
        self._dziennik.columnconfigure(0, weight=1)
        self._dziennik.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            self._dziennik, height=8, bg=LOG_BG, fg=LOG_FG, insertbackground=FG,
            relief="flat", wrap="word", font=(theme.FONT_MONO, 9), state="disabled",
            padx=10, pady=8, highlightthickness=0, borderwidth=0,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self._dziennik, orient="vertical",
                               command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=theme.pasek_gdy_trzeba(
            scroll, row=0, column=1, sticky="ns"))

    def _przelacz_dziennik(self) -> None:
        self._dziennik_widoczny = not self._dziennik_widoczny
        if self._dziennik_widoczny:
            self._dziennik.grid(row=W_DZIENNIK, column=0, sticky="nsew", pady=(12, 0),
                                padx=(0, 14))
            self._przelacznik.configure(text="▾  Ukryj dziennik")
            self.log_text.see("end")
        else:
            self._dziennik.grid_remove()
            self._przelacznik.configure(text="▸  Pokaż dziennik")

    # -- ustawienia <-> widżety --------------------------------------------

    def _apply_settings(self) -> None:
        s = self.settings
        # Model wybrany kiedyś ręcznie (gdy okno jeszcze na to pozwalało)
        # nie może dalej działać po cichu — wracamy do doboru automatycznego.
        s.model = s.device = s.compute_type = ""

        self.lang_var.set(
            dict(LANGUAGES).get(s.language, "polski")
        )
        self.prompt_var.set(s.initial_prompt)
        self.next_to_source.set(s.output_next_to_source)
        self.diarize_var.set(s.diarize)
        self.speakers_var.set(str(s.speakers))
        self.outdir_var.set(s.output_dir or str(default_output_dir()))
        if WYDANIE.nagrywanie:
            self.rec_auto_var.set(s.nagranie_transkrybuj)
        self._pokaz_koniec_sciezki()
        self._toggle_outdir()
        self._toggle_diarize()

        self.log(f"{APP_TITLE} — gotowy.")
        self.log(f"Rekomendacja sprzętowa: {self.rec.model} "
                 f"({self.rec.device}, {self.rec.compute_type})")
        for warning in self.rec.warnings:
            self.log(f"UWAGA: {warning}")
        if not DND_AVAILABLE:
            self.log(
                "Brak tkinterdnd2 — przeciąganie plików wyłączone, "
                "użyj kliknięcia w pole powyżej."
            )

    def _collect_settings(self) -> Settings:
        s = self.settings
        s.model = s.device = s.compute_type = ""

        chosen_lang = self.lang_var.get()
        for code, text in LANGUAGES:
            if text == chosen_lang:
                s.language = code
                break

        s.initial_prompt = self.prompt_var.get().strip()
        s.formats = [f for f, var in self.format_vars.items() if var.get()]
        s.output_next_to_source = bool(self.next_to_source.get())
        s.diarize = bool(self.diarize_var.get())
        try:
            s.speakers = int(self.speakers_var.get())
        except ValueError:
            pass  # puste pole: zostaw ostatnią sensowną wartość
        s.output_dir = "" if s.output_next_to_source else self.outdir_var.get().strip()
        if WYDANIE.nagrywanie:
            s.nagranie_transkrybuj = bool(self.rec_auto_var.get())
        s.normalize()
        return s

    @staticmethod
    def _tylko_liczba_osob(tekst: str) -> bool:
        """Wpuści pustą zawartość albo liczbę z zakresu 0-20."""
        return tekst == "" or (tekst.isdigit() and int(tekst) <= 20)

    def _toggle_diarize(self) -> None:
        """Pokazuje wskazówkę o czasie i blokuje pole liczby osób."""
        wlaczone = bool(self.diarize_var.get())
        self.speakers_box.configure(state="normal" if wlaczone else "disabled")
        if wlaczone:
            self.diarize_hint.configure(
                text="Wydłuża pracę mniej więcej o długość nagrania. Dokładna "
                     "liczba osób dzieli wyraźnie lepiej niż 0 („zgadnij”).",
            )
        else:
            self.diarize_hint.configure(text="")

    def _pokaz_koniec_sciezki(self) -> None:
        """Długa ścieżka nie mieści się w polu — ważniejszy jest jej koniec."""
        self.outdir_entry.icursor("end")
        self.outdir_entry.after_idle(lambda: self.outdir_entry.xview_moveto(1.0))

    def _toggle_outdir(self) -> None:
        state = "disabled" if self.next_to_source.get() else "normal"
        self.outdir_entry.configure(state=state)

    # -- kolejka plików ----------------------------------------------------

    def _on_drop(self, event) -> None:
        self._drop_color(BG_DROP)
        try:
            raw = self.root.tk.splitlist(event.data)
        except tk.TclError:
            raw = [event.data]
        self.add_files([Path(p) for p in raw])

    def browse_files(self) -> None:
        if self._busy():
            return
        patterns = " ".join(f"*{e}" for e in sorted(media.MEDIA_EXT))
        paths = filedialog.askopenfilenames(
            title="Wybierz pliki audio lub wideo",
            filetypes=[
                ("Pliki audio i wideo", patterns),
                ("Wszystkie pliki", "*.*"),
            ],
        )
        if paths:
            self.add_files([Path(p) for p in paths])

    def add_files(self, paths) -> None:
        found = media.collect_media(paths)
        if not found:
            self.log("Przeciągnięte pliki nie zawierają obsługiwanych formatów.")
            return

        added = 0
        known = {str(p).lower() for p in self.files}
        for path in found:
            if str(path).lower() in known:
                continue
            self.files.append(path)
            self.tree.insert("", "end", iid=str(path), text=path.name,
                             values=("…", "oczekuje", USUN))
            added += 1

        self._odswiez_pusta_kolejke()
        if added:
            self.log(f"Dodano {added} plik(ów) do kolejki.")
            threading.Thread(target=self._probe_durations, daemon=True).start()

    def _probe_durations(self) -> None:
        for path in list(self.files):
            if str(path) in self.durations:
                continue
            try:
                info = media.probe_media(path)
                text = media.format_duration(info.duration)
                self.durations[str(path)] = info.duration
            except Exception as exc:
                text = "—"
                self.events.put(("row_error", str(path), f"nieczytelny: {exc}"))
                continue
            self.events.put(("row_duration", str(path), text))

    def remove_selected(self) -> None:
        self._usun(self.tree.selection())

    def clear_queue(self) -> None:
        self._usun(self.tree.get_children())
        if not self.files:
            self.durations.clear()

    def _usun(self, iids) -> None:
        """Usuwa pliki z kolejki — także w trakcie pracy.

        Plik, który właśnie się przetwarza, zostaje (do tego jest „Przerwij”).
        Pozostałe wątek roboczy pominie, gdy do nich dojdzie.
        """
        for iid in list(iids):
            if iid == self._biezacy or not self.tree.exists(iid):
                continue
            self.tree.delete(iid)
            self.files = [f for f in self.files if str(f) != iid]
            if self._busy():
                self._usuniete.add(iid)
        self._odswiez_pusta_kolejke()

    def _klik_w_kolejce(self, zdarzenie):
        if self.tree.identify_column(zdarzenie.x) != "#3":
            return None
        iid = self.tree.identify_row(zdarzenie.y)
        if iid:
            self._usun([iid])
        return "break"

    def _ruch_w_kolejce(self, zdarzenie) -> None:
        nad_krzyzykiem = (self.tree.identify_column(zdarzenie.x) == "#3"
                          and self.tree.identify_row(zdarzenie.y) not in ("", self._biezacy))
        self.tree.configure(cursor="hand2" if nad_krzyzykiem else "")

    def _menu_kolejki(self, zdarzenie) -> None:
        iid = self.tree.identify_row(zdarzenie.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        stan = "normal" if self.tree.selection() else "disabled"
        self._menu.entryconfigure(0, state=stan)
        self._menu.entryconfigure(1, state="normal" if self.tree.get_children() else "disabled")
        try:
            self._menu.tk_popup(zdarzenie.x_root, zdarzenie.y_root)
        finally:
            self._menu.grab_release()

    # -- uruchomienie ------------------------------------------------------

    def _busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def start(self, tylko_nowe: bool = False) -> None:
        """Rusza z kolejką. `tylko_nowe` (po nagraniu) pomija gotowe pliki bez pytania."""
        if self._busy():
            return
        if self._nagrywa():
            messagebox.showinfo(
                APP_TITLE, "Trwa nagrywanie — transkrypcja ruszy po jego zatrzymaniu.",
                parent=self.root,
            )
            return
        if not self.files:
            messagebox.showinfo(
                APP_TITLE, "Najpierw dodaj pliki — przeciągnij je w pole u góry."
            )
            return

        settings = self._collect_settings()
        if not settings.formats:
            messagebox.showwarning(
                APP_TITLE, "Zaznacz przynajmniej jeden format zapisu."
            )
            return
        settings.save()

        # Pliki, które już przeszły. Zmienione ustawienia (np. włączeni
        # mówcy) są powodem, żeby policzyć je jeszcze raz; samo dorzucenie
        # nowego pliku do kolejki — nie. Wcześniej program po cichu liczył
        # wszystko od nowa i dokładał wyniki z numerem w nazwie.
        files = list(self.files)
        gotowe = {iid for iid in self.tree.get_children()
                  if "ok" in self.tree.item(iid, "tags")}
        if gotowe and tylko_nowe:
            files = [f for f in files if str(f) not in gotowe]
            if not files:
                return
        elif gotowe:
            if len(gotowe) == len(files):
                pytanie = ("Wszystkie pliki w kolejce są już przetworzone. Przetworzyć je "
                           "jeszcze raz?\n\nNowe pliki wyników dostaną numer w nazwie, "
                           "stare zostaną.")
            else:
                pytanie = (f"Gotowe pliki w kolejce: {len(gotowe)}. Przetworzyć je jeszcze "
                           f"raz razem z nowymi?\n\n„Nie” przetworzy tylko te, które "
                           f"jeszcze czekają.")
            if not messagebox.askyesno(APP_TITLE, pytanie, parent=self.root):
                files = [f for f in files if str(f) not in gotowe]
                if not files:
                    return

        self.cancel_flag.clear()
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.file_progress["value"] = 0
        self.overall_progress["value"] = 0

        self._usuniete = set()
        wybrane = {str(f) for f in files}
        for iid in self.tree.get_children():
            if iid in wybrane:
                self.tree.item(iid, values=(self.tree.item(iid, "values")[0], "oczekuje", USUN),
                               tags=())

        self._w_toku = [str(f) for f in files]
        self._zrobione_w_toku = set()
        self._praca_start = None
        self._ostatni_opis = 0.0
        self.worker = threading.Thread(
            target=self._work, args=(files, settings), daemon=True
        )
        self.worker.start()

    def _work(self, files, settings) -> None:
        put = self.events.put
        callbacks = Callbacks(
            log=lambda m: put(("log", m)),
            file_started=lambda i, n, p: put(("file_started", i, n, str(p))),
            file_progress=lambda f: put(("file_progress", f)),
            file_finished=lambda r: put(("file_finished", r)),
            overall_progress=lambda f: put(("overall_progress", f)),
            status=lambda s: put(("status", s)),
            cancelled=self.cancel_flag.is_set,
            skipped=lambda p: str(p) in self._usuniete,
            ask_speakers=self._zapytaj_o_mowcow,
        )
        try:
            results = Runner(settings, callbacks).run(files)
            put(("done", results))
        except Exception as exc:  # ostatnia linia obrony wątku roboczego
            put(("log", f"BŁĄD KRYTYCZNY: {type(exc).__name__}: {exc}"))
            put(("done", []))

    def _zapytaj_o_mowcow(self, result, audio) -> dict:
        """Wywoływane z wątku roboczego: prosi okno o pokazanie dialogu.

        Tkinter wolno obsługiwać wyłącznie z wątku głównego, więc wątek
        roboczy tylko zgłasza prośbę i czeka, aż okno odeśle odpowiedź.
        """
        if self.cancel_flag.is_set():
            return {}
        gotowe = threading.Event()
        odpowiedz: dict = {}
        self.events.put(("ask_speakers", result, audio, odpowiedz, gotowe))
        # Limit chroni przed zawieszeniem kolejki, gdyby okno zniknęło. Jest
        # długi, bo po jego upływie imiona wpisane w otwartym jeszcze oknie
        # nie miałyby dokąd trafić — a człowiek mógł odejść od komputera.
        gotowe.wait(timeout=4 * 3600)
        return odpowiedz

    # -- pobieranie modelu (wersja lekka) ---------------------------------

    def _model_do_pracy(self) -> str:
        return self.settings.model or self.rec.model

    def _pobierz_przy_starcie(self) -> None:
        """Pierwsze uruchomienie wersji bez modelu: pobierz go od razu.

        Bez pytania — model jest niezbędnym składnikiem programu, nie
        dodatkiem do wyboru. Odłożony do pierwszej transkrypcji kazałby
        czekać akurat wtedy, gdy człowiek chce już pracować.
        """
        model = self._model_do_pracy()
        if self._busy() or not model_do_pobrania(model):
            return
        self.log(
            f"Pierwsze uruchomienie: pobieram model rozpoznawania mowy {model} "
            f"(ok. {download.rozmiar_opis(model)}). To jednorazowe — potem "
            f"program działa bez internetu, a nagrania nigdy nie opuszczają "
            f"komputera."
        )
        self.pobierz_model(model)

    def pobierz_model(self, model: str) -> None:
        if self._busy():
            return
        self.cancel_flag.clear()
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.file_progress["value"] = 0
        self.overall_var.set("")
        self.status_var.set(f"Pobieram model {model}…")
        put = self.events.put

        def praca() -> None:
            try:
                download.pobierz_model(
                    model,
                    models_dir(),
                    on_progress=lambda b, w, v: put(("download_progress", f"model {model}", b, w, v)),
                    log=lambda m: put(("log", m)),
                    cancel=self.cancel_flag.is_set,
                )
                put(("download_done", model, ""))
            except Cancelled:
                put(("download_done", model, None))
            except Exception as exc:  # błąd ma dotrzeć do człowieka, nie zniknąć
                put(("download_done", model, str(exc) or type(exc).__name__))

        self.worker = threading.Thread(target=praca, daemon=True)
        self.worker.start()

    def _po_pobraniu(self, model: str, blad) -> None:
        self.worker = None
        self.start_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        if blad is None:
            self.status_var.set("Pobieranie przerwane — następna próba ruszy od tego miejsca.")
            self.log(self.status_var.get())
        elif blad:
            self.status_var.set("Nie udało się pobrać modelu.")
            self.log(f"BŁĄD: {blad}")
            messagebox.showerror(APP_TITLE, blad, parent=self.root)
        else:
            self.file_progress["value"] = 1000
            self.status_var.set(f"Model {model} gotowy. Możesz przeciągać nagrania.")

    # -- aktualizacje (wydania z repozytorium) -----------------------------

    def sprawdz_aktualizacje(self, reczne: bool = True) -> None:
        """Pyta GitHuba o nową wersję, w osobnym wątku.

        Sprawdzenie w tle przy starcie milczy, gdy nic nie znajdzie albo nie
        ma sieci. Ręczne (z odnośnika) zawsze odpowiada.
        """
        if self._sprawdzam:
            return
        self._sprawdzam = True
        if reczne and not self._busy():
            self.status_var.set("Sprawdzam, czy jest nowa wersja…")

        def praca() -> None:
            try:
                akt = update.sprawdz(__version__)
                self.events.put(("update_checked", reczne, akt, ""))
            except Exception as exc:
                self.events.put(("update_checked", reczne, None, str(exc) or type(exc).__name__))

        threading.Thread(target=praca, daemon=True).start()

    def _po_sprawdzeniu(self, reczne: bool, akt, blad: str) -> None:
        self._sprawdzam = False
        if blad:
            if reczne:
                self.status_var.set("Nie udało się sprawdzić aktualizacji.")
                messagebox.showwarning(
                    APP_TITLE, f"Nie udało się sprawdzić, czy jest nowa wersja.\n\n{blad}",
                    parent=self.root,
                )
            else:
                self.log(f"Sprawdzanie aktualizacji nie powiodło się: {blad}")
            return

        self.settings.aktualizacje_sprawdzone = time.time()
        self.settings.save()

        if akt is None:
            if reczne:
                if not self._busy():
                    self.status_var.set(f"Masz najnowszą wersję ({__version__}).")
                messagebox.showinfo(
                    APP_TITLE, f"Masz najnowszą wersję {APP_TITLE} ({__version__}).",
                    parent=self.root,
                )
            return
        if not reczne and akt.wersja == self.settings.pominieta_wersja:
            self.log(f"Dostępna jest wersja {akt.wersja} (pominięta na Twoje życzenie).")
            return
        self._pokaz_baner(akt)

    def _pokaz_baner(self, akt) -> None:
        self._aktualizacja = akt
        self.baner_tekst.configure(
            text=f"Dostępna nowa wersja {APP_TITLE} {akt.wersja}   (masz {__version__})"
        )
        self.baner.grid()
        self.log(f"Dostępna nowa wersja: {akt.wersja}. Kliknij „Zaktualizuj teraz”.")
        try:
            self.root.bell()
        except tk.TclError:
            pass

    def _ukryj_baner(self) -> None:
        self.baner.grid_remove()

    def pomin_wersje(self) -> None:
        if self._aktualizacja is not None:
            self.settings.pominieta_wersja = self._aktualizacja.wersja
            self.settings.save()
            self.log(f"Wersja {self._aktualizacja.wersja} pominięta — "
                     f"„Sprawdź aktualizacje” pokaże ją ponownie.")
        self._ukryj_baner()

    def co_nowego(self) -> None:
        if self._aktualizacja is not None and self._aktualizacja.strona:
            webbrowser.open(self._aktualizacja.strona)

    def zaktualizuj(self) -> None:
        akt = self._aktualizacja
        if akt is None:
            return
        if self._busy():
            messagebox.showinfo(
                APP_TITLE,
                "Program jeszcze pracuje. Zaktualizuj, gdy skończy — pasek "
                "z nową wersją zostanie na miejscu.",
                parent=self.root,
            )
            return
        if not update.mozna_zainstalowac(akt):
            # Bez instalatora, sumy albo prawidłowego podpisu nie uruchamiamy
            # niczego — zostaje pobranie ręczne ze strony wydania.
            self.log(f"Wersji {akt.wersja} program nie zainstaluje sam: "
                     f"{akt.uwaga or 'wydanie nie ma sprawdzalnego instalatora'}. "
                     f"Otwieram stronę wydania.")
            webbrowser.open(akt.strona)
            return

        self._ukryj_baner()
        self.cancel_flag.clear()
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.file_progress["value"] = 0
        self.status_var.set(f"Pobieram wersję {akt.wersja}…")
        put = self.events.put

        def praca() -> None:
            try:
                sciezka = update.pobierz(
                    akt,
                    on_progress=lambda b, w, v: put(
                        ("download_progress", f"wersję {akt.wersja}", b, w, v)),
                    log=lambda m: put(("log", m)),
                    cancel=self.cancel_flag.is_set,
                )
                put(("update_downloaded", sciezka, ""))
            except Cancelled:
                put(("update_downloaded", None, None))
            except Exception as exc:
                put(("update_downloaded", None, str(exc) or type(exc).__name__))

        self.worker = threading.Thread(target=praca, daemon=True)
        self.worker.start()

    def _po_pobraniu_aktualizacji(self, sciezka, blad) -> None:
        self.worker = None
        self.start_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        if blad is None:
            self.status_var.set("Pobieranie aktualizacji przerwane.")
            self.baner.grid()
            return
        if blad:
            self.status_var.set("Nie udało się pobrać aktualizacji.")
            self.log(f"BŁĄD: {blad}")
            self.baner.grid()
            messagebox.showerror(APP_TITLE, blad, parent=self.root)
            return

        self.file_progress["value"] = 1000
        self.status_var.set("Instaluję nową wersję — program uruchomi się ponownie sam.")
        try:
            update.uruchom_instalator(sciezka)
        except Exception as exc:
            self.log(f"BŁĄD: nie udało się uruchomić instalatora: {exc}")
            messagebox.showerror(
                APP_TITLE,
                f"Nie udało się uruchomić instalatora:\n{exc}\n\nPlik: {sciezka}",
                parent=self.root,
            )
            return
        # Instalator podmienia pliki programu — trzeba mu je zwolnić.
        self.root.after(300, self._zamknij)

    def cancel(self) -> None:
        if self._busy():
            self.cancel_flag.set()
            self.status_var.set("Przerywam po bieżącym fragmencie…")
            self.cancel_btn.configure(state="disabled")

    # -- pompa zdarzeń z wątku roboczego -----------------------------------

    def _pump_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                self._handle(event)
        except queue.Empty:
            pass
        self.root.after(80, self._pump_events)

    def _handle(self, event) -> None:
        kind = event[0]

        if kind == "log":
            self.log(event[1])
        elif kind == "status":
            self.status_var.set(event[1])
        elif kind == "file_progress":
            self._postep_biezacy = max(0.0, min(event[1], 1.0))
            self.file_progress["value"] = self._postep_biezacy * 1000
            self._odswiez_pozostaly()
        elif kind == "overall_progress":
            self.overall_progress["value"] = max(0.0, min(event[1], 1.0)) * 1000
        elif kind == "file_started":
            index, total, path = event[1], event[2], event[3]
            self._opis_pliku = f"plik {index + 1} z {total}"
            self.overall_var.set(self._opis_pliku)
            self.file_progress["value"] = 0
            self._postep_biezacy = 0.0
            if self._praca_start is None:
                # Zegar rusza z pierwszym plikiem, nie z ładowaniem modelu —
                # inaczej pierwsze szacunki byłyby zawyżone o start silnika.
                self._praca_start = time.monotonic()
            self._biezacy = path
            self._set_row(path, "przetwarzanie…", "run")
        elif kind == "file_finished":
            self._on_file_finished(event[1])
        elif kind == "row_duration":
            self._set_duration(event[1], event[2])
        elif kind == "row_error":
            self._set_row(event[1], event[2], "err")
        elif kind == "ask_speakers":
            _, result, audio, odpowiedz, gotowe = event
            try:
                from .speakers import zapytaj

                odpowiedz.update(zapytaj(self.root, result, audio))
            except Exception as exc:
                self.log(f"Nie udało się otworzyć okna mówców: {exc}")
            finally:
                gotowe.set()
        elif kind == "download_progress":
            _, co, pobrane, wszystkie, predkosc = event
            self.status_var.set(
                download.opis_postepu(co, pobrane, wszystkie, predkosc)
            )
            if wszystkie:
                self.file_progress["value"] = pobrane / wszystkie * 1000
        elif kind == "download_done":
            self._po_pobraniu(event[1], event[2])
        elif kind == "update_checked":
            self._po_sprawdzeniu(event[1], event[2], event[3])
        elif kind == "update_downloaded":
            self._po_pobraniu_aktualizacji(event[1], event[2])
        elif kind == "nagranie_poziomy":
            self._pokaz_poziomy(event[1], event[2])
        elif kind == "nagranie_czas":
            self.rec_czas.configure(text=_hms(event[1]))
        elif kind == "nagranie_ostrzezenie":
            self.log(f"UWAGA (nagrywanie): {event[1]}")
            self.status_var.set(event[1])
        elif kind == "nagranie_blad":
            self._ustaw_stan_nagrywania(False)
            self.log(f"BŁĄD nagrywania: {event[1]}")
            self.status_var.set("Nagrywanie przerwane.")
            messagebox.showerror(APP_TITLE, event[1], parent=self.root)
        elif kind == "nagranie_koniec":
            self._po_nagraniu(Path(event[1]), event[2])
        elif kind == "nagranie_anulowane":
            self._ustaw_stan_nagrywania(False)
            self.status_var.set("Nagranie odrzucone — nic nie zostało zapisane.")
        elif kind == "nagranie_urzadzenia":
            self._wypelnij_urzadzenia(event[1], event[2], event[3])
        elif kind == "nagranie_odzyskane":
            self._po_odzyskaniu(event[1], event[2])
        elif kind == "done":
            self._on_done(event[1])

    def _set_row(self, iid: str, status: str, tag: str = "") -> None:
        if not self.tree.exists(iid):
            return
        duration = self.tree.item(iid, "values")[0]
        # Pliku w trakcie pracy nie da się usunąć — nie ma przy nim krzyżyka.
        krzyzyk = "" if tag == "run" else USUN
        self.tree.item(iid, values=(duration, status, krzyzyk), tags=(tag,) if tag else ())
        self.tree.see(iid)

    def _set_duration(self, iid: str, text: str) -> None:
        if not self.tree.exists(iid):
            return
        _stara, status, krzyzyk = (tuple(self.tree.item(iid, "values")) + ("", "", ""))[:3]
        self.tree.item(iid, values=(text, status, krzyzyk))
        self._odswiez_podsumowanie()

    def _odswiez_pozostaly(self) -> None:
        """„plik 2 z 4 · pozostało ok. 6 min” — z tempa dotychczasowej pracy.

        Tempo to sekundy nagrania na sekundę pracy, liczone od startu
        pierwszego pliku. Pierwsze sekundy pomijamy, a przy nieznanej długości
        któregoś pliku nie zgadujemy wcale.
        """
        teraz = time.monotonic()
        if self._praca_start is None or teraz - self._ostatni_opis < 1.0:
            return
        self._ostatni_opis = teraz
        uplynelo = teraz - self._praca_start
        if uplynelo < 8.0:
            return
        w_toku = [iid for iid in self._w_toku if iid not in self._usuniete]
        dlugosci = {iid: self.durations.get(iid) for iid in w_toku}
        if not dlugosci or any(d is None for d in dlugosci.values()):
            return
        zrobione = sum(d for iid, d in dlugosci.items() if iid in self._zrobione_w_toku)
        if self._biezacy in dlugosci:
            zrobione += dlugosci[self._biezacy] * self._postep_biezacy
        wszystko = sum(dlugosci.values())
        if zrobione <= 0 or wszystko <= zrobione:
            return
        pozostalo = (wszystko - zrobione) / (zrobione / uplynelo)
        self.overall_var.set(f"{self._opis_pliku} · {_opis_pozostalego(pozostalo)}")

    def _on_file_finished(self, job: JobResult) -> None:
        iid = str(job.source)
        self._biezacy = None
        self._zrobione_w_toku.add(iid)
        if job.ok and job.result:
            self._set_row(
                iid, f"✓  gotowe, {job.result.speed_ratio:.1f}× szybciej", "ok"
            )
            if job.outputs:
                self.last_output_dir = next(iter(job.outputs.values())).parent
        else:
            self._set_row(iid, job.error[:60] or "błąd", "err")

    def _on_done(self, results) -> None:
        self.worker = None
        self._biezacy = None
        self.start_btn.configure(state="disabled" if self._nagrywa() else "normal")
        self.cancel_btn.configure(state="disabled")
        if getattr(self, "_po_pracy_transkrybuj", False):
            # Nagranie skończyło się w trakcie poprzedniej pracy — teraz jego kolej.
            self._po_pracy_transkrybuj = False
            self.root.after(500, lambda: self.start(tylko_nowe=True))

        done = sum(1 for r in results if r.ok)
        failed = len(results) - done

        self.overall_var.set("")
        if self.cancel_flag.is_set():
            self.status_var.set(f"Przerwano. Ukończono {_liczba_plikow(done)}.")
        elif failed:
            self.status_var.set(f"Zakończono: {done} OK, {failed} z błędem.")
        else:
            self.status_var.set(f"Gotowe — przetworzono {_liczba_plikow(done)}.")
            self.overall_progress["value"] = 1000

        self.log(self.status_var.get())

        if done and self.settings.open_output_when_done and not self.cancel_flag.is_set():
            self.open_output()

    # -- akcje pomocnicze --------------------------------------------------

    def choose_output(self) -> None:
        path = filedialog.askdirectory(
            title="Gdzie zapisywać transkrypcje?",
            initialdir=self.outdir_var.get() or str(default_output_dir()),
        )
        if path:
            self.outdir_var.set(path)
            self.next_to_source.set(False)
            self._toggle_outdir()
            self._pokaz_koniec_sciezki()

    def open_output(self) -> None:
        target = self.last_output_dir
        if target is None:
            target = (
                Path(self.outdir_var.get())
                if self.outdir_var.get() and not self.next_to_source.get()
                else default_output_dir()
            )
        if not Path(target).is_dir():
            messagebox.showinfo(APP_TITLE, f"Folder nie istnieje: {target}")
            return
        _open_folder(Path(target))

    def pokaz_pomoc(self) -> None:
        """Krótko: jak używać i co program potrafi."""
        if getattr(self, "_pomoc", None) is not None and self._pomoc.winfo_exists():
            self._pomoc.lift()
            return
        okno = self._pomoc = tk.Toplevel(self.root)
        okno.title(f"Jak to działa — {APP_NAME}")
        okno.configure(bg=BG)
        okno.resizable(False, False)
        okno.transient(self.root)
        tresc = tk.Frame(okno, bg=BG, padx=28, pady=22)
        tresc.pack(fill="both", expand=True)

        def naglowek(tekst: str, odstep: int) -> None:
            tk.Label(tresc, text=tekst, bg=BG, fg=FG, font=(theme.FONT_SEMI, 12),
                     anchor="w").pack(fill="x", pady=(odstep, 8))

        def punkt(znak: str, tekst: str, kolor: str) -> None:
            rzad = tk.Frame(tresc, bg=BG)
            rzad.pack(fill="x", pady=3)
            tk.Label(rzad, text=znak, bg=BG, fg=kolor, width=2, anchor="nw",
                     font=(theme.FONT_SEMI, 10)).pack(side="left", anchor="n")
            tk.Label(rzad, text=tekst, bg=BG, fg=FG_DIM, font=(theme.FONT, 10),
                     justify="left", anchor="w", wraplength=400).pack(
                side="left", fill="x")

        naglowek("Trzy kroki", 0)
        for i, tekst in enumerate((
            "Przeciągnij nagrania albo cały folder w pole po lewej. "
            "Audio i wideo, ile chcesz naraz.",
            "Zaznacz, w jakiej postaci zapisać tekst: z czasem, sam tekst "
            "albo napisy do filmu.",
            "Kliknij Transkrybuj. Gotowe pliki trafią do folderu wyników.",
        ), start=1):
            punkt(str(i), tekst, ACCENT_HOVER)

        naglowek("Co jeszcze potrafi", 18)
        o_nagrywaniu = (
            "Nagrywa spotkania. „Nagrywaj spotkanie” zbiera Twój mikrofon i dźwięk "
            "z głośników (np. Teams) do jednego pliku, a po zatrzymaniu od razu go "
            "transkrybuje. Najlepiej w słuchawkach.",
        ) if WYDANIE.nagrywanie else ()
        for tekst in o_nagrywaniu + (
            "Podpisuje, kto mówi. Włącz „Rozpoznaj, kto co powiedział” "
            "i wpisz liczbę osób — po nagraniu nadasz im imiona.",
            "Lepiej pisze nazwiska i skróty, jeśli wpiszesz je w pole Kontekst.",
            "Rozpoznaje mowę na Twoim komputerze — nagrania nigdy nie trafiają do internetu.",
            "Plik z kolejki usuniesz krzyżykiem przy nim, klawiszem Delete "
            "albo prawym przyciskiem myszy.",
        ):
            punkt("•", tekst, FG_FAINT)

        przyciski = tk.Frame(tresc, bg=BG)
        przyciski.pack(fill="x", pady=(22, 0))
        ttk.Button(przyciski, text="Zamknij", style="Accent.TButton",
                   command=okno.destroy).pack(side="right")
        okno.bind("<Escape>", lambda _e: okno.destroy())
        okno.update_idletasks()
        theme.ciemny_pasek_tytulu(okno)
        x = self.root.winfo_rootx() + (self.root.winfo_width() - okno.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + 80
        okno.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        okno.focus_set()

    def pokaz_licencje(self) -> None:
        """Otwiera spis licencji składników — LICENCJE.txt obok programu."""
        plik = project_root() / "LICENCJE.txt"
        if not plik.is_file():
            messagebox.showinfo(
                APP_TITLE,
                f"Nie znaleziono pliku {plik.name}. W wersji uruchamianej z kodu "
                f"tworzy go polecenie: python tools\\licencje.py",
                parent=self.root,
            )
            return
        try:
            os.startfile(str(plik))  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Nie udało się otworzyć {plik}:\n{exc}",
                                 parent=self.root)

    def show_diagnosis(self) -> None:
        self.status_var.set("Sprawdzam środowisko…")
        self.root.update_idletasks()
        report = doctor.format_diagnosis(doctor.diagnose())
        self.status_var.set("Gotowy.")
        _show_report(self.root, "Diagnostyka środowiska", report)

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def on_close(self) -> None:
        if self._nagrywa():
            if not messagebox.askyesno(
                APP_TITLE, "Trwa nagrywanie. Zatrzymać je, zapisać nagranie i zamknąć program?",
                parent=self.root,
            ):
                return
            self.nagrywarka.stop()
            # Plik ma zostać domknięty (nagłówek z długością) zanim znikniemy.
            self.nagrywarka.czekaj(15)
        if self._busy():
            if not messagebox.askyesno(
                APP_TITLE, "Program jeszcze pracuje. Na pewno zamknąć?"
            ):
                return
            self.cancel_flag.set()
        self._zamknij()

    def _zamknij(self) -> None:
        try:
            settings = self._collect_settings()
            settings.window_geometry = self.root.geometry()
            settings.save()
        except Exception:
            pass
        clear_cache()
        self.root.destroy()


# ---------------------------------------------------------------------------


def _liczba_plikow(n: int) -> str:
    """1 plik, 2 pliki, 5 plików, 22 pliki."""
    if n == 1:
        return "1 plik"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} pliki"
    return f"{n} plików"


def _opis_pozostalego(sekundy: float) -> str:
    """„pozostało ok. 6 min” — zaokrąglone, bo to szacunek, nie pomiar."""
    if sekundy < 45:
        return "pozostało mniej niż minuta"
    minuty = round(sekundy / 60)
    if minuty < 60:
        return f"pozostało ok. {minuty} min"
    godziny, minuty = divmod(minuty, 60)
    return f"pozostało ok. {godziny} godz. {minuty:02d} min"


def _open_folder(path: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError:
        pass


def _show_report(parent, title: str, text: str) -> None:
    win = tk.Toplevel(parent)
    win.title(title)
    win.configure(bg=BG)
    win.geometry("820x620")

    box = tk.Text(
        win,
        bg=LOG_BG,
        fg=LOG_FG,
        relief="flat",
        wrap="none",
        font=("Consolas", 9),
        padx=14,
        pady=12,
    )
    box.pack(fill="both", expand=True, padx=12, pady=(12, 6))
    box.insert("1.0", text)
    box.configure(state="disabled")

    row = ttk.Frame(win, style="App.TFrame")
    row.pack(fill="x", padx=12, pady=(0, 12))
    ttk.Button(
        row,
        text="Kopiuj do schowka",
        command=lambda: (parent.clipboard_clear(), parent.clipboard_append(text)),
    ).pack(side="left")
    ttk.Button(row, text="Zamknij", command=win.destroy).pack(side="right")
    theme.ciemny_pasek_tytulu(win)


def show_report_window(title: str, text: str) -> int:
    """Pokazuje raport w samodzielnym oknie.

    Wersja spakowana nie ma konsoli, więc `--doctor` nie miałby gdzie
    wypisać wyniku — a to właśnie ten tryb uruchamia skrót „Diagnostyka".
    """
    root = make_root()
    root.title(title)
    root.configure(bg=BG)
    root.geometry("860x660")

    theme.zastosuj(root)

    box = tk.Text(
        root, bg=LOG_BG, fg=LOG_FG, relief="flat", wrap="none",
        font=("Consolas", 9), padx=14, pady=12,
    )
    box.pack(fill="both", expand=True, padx=12, pady=(12, 6))
    box.insert("1.0", text)
    box.configure(state="disabled")

    row = ttk.Frame(root, style="App.TFrame")
    row.pack(fill="x", padx=12, pady=(0, 12))
    ttk.Button(
        row,
        text="Kopiuj do schowka",
        command=lambda: (root.clipboard_clear(), root.clipboard_append(text)),
    ).pack(side="left")
    ttk.Button(row, text="Zamknij", command=root.destroy).pack(side="right")
    theme.ciemny_pasek_tytulu(root)

    root.mainloop()
    return 0


def run(initial_files: Optional[List[Path]] = None) -> int:
    from .splash import Splash

    root = make_root(ukryj=True)
    ekran = Splash(
        root,
        wersja=__version__,
        podpis=f"wersja {__version__}  ·  {WYDANIE.wydawca}",
    )
    try:
        App(root, initial_files=initial_files, splash=ekran)
    finally:
        ekran.close()

    root.deiconify()
    root.lift()
    root.focus_force()
    root.mainloop()
    return 0
