"""Okno aplikacji: przeciągnij plik, dostań transkrypcję."""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

from . import __version__, teksty
from .core import doctor, download, glosy, media, nagrywanie, probe, update, urzadzenia, wideo
from .core.config import (
    APP_ID, APP_NAME, LANGUAGES, WYDANIE, Settings, asset, default_output_dir,
    imie_uzytkownika, is_frozen, models_dir, project_root,
)
from .core.engine import Cancelled
from .core.pipeline import (
    Callbacks, JobResult, Runner, clear_cache, model_do_pobrania,
)
from .core.writers import FORMAT_LABELS, FORMATS
from .teksty import t

APP_TITLE = APP_NAME

from . import theme
from .theme import (
    ACCENT, ACCENT_HOVER, ACCENT_SOFT, BANER_BG, BANER_OK_BG, BANER_WARN_BG, BG, BG_CARD,
    BG_DROP, BG_INPUT,
    BG_DROP_HOVER,
    BORDER_DROP, ERR_COLOR, FG, FG_DIM, FG_FAINT, LOG_BG, LOG_FG, OK_COLOR, WARN_COLOR,
)

#: Znak w kolumnie kolejki, którym usuwa się plik.
USUN = "✕"
#: Wybór na liście „Obraz”: okno spotkania, które program znajdzie sam
#: przy starcie nagrania (w ustawieniach: obraz bez programu i ekranu).
OBRAZ_AUTO = "auto"
#: Podgląd obrazu w dymku pod listą „Obraz”: rozmiar (piksele, 16:9) i ile
#: sekund widać go po wyborze na liście i na starcie nagrania.
PODGLAD = (320, 180)
PODGLAD_S = 8.0


def _klucz_zrodla(zrodlo):
    """Okno po uchwycie, ekran po monitorze — tytuł okna (Teams z licznikiem
    czasu) zmienia się w trakcie, a to wciąż to samo źródło."""
    if isinstance(zrodlo, wideo.Okno):
        return ("okno", zrodlo.hwnd)
    if isinstance(zrodlo, wideo.Ekran):
        return ("ekran", zrodlo.hmonitor)
    return zrodlo

#: Wiersze lewej kolumny okna: baner nowej wersji, karta nagrywania (tylko
#: wydania z nagrywaniem), pole upuszczania, kolejka, dziennik. Panel
#: ustawień po prawej rozciąga się na wszystkie. Komunikat „transkrypcja
#: zakończona” nie ma wiersza — leży nakładką na kolejce (_build_queue).
W_BANER, W_NAGRYWANIE, W_DROP, W_KOLEJKA, W_DZIENNIK = 0, 1, 2, 3, 4


def _hms(sekundy: float) -> str:
    """Licznik nagrania: 0:47:12."""
    h, reszta = divmod(int(sekundy), 3600)
    m, s = divmod(reszta, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _skroc(tekst: str, limit: int = 42) -> str:
    return tekst if len(tekst) <= limit else tekst[: limit - 1] + "…"


def _skroc_srodek(tekst: str, limit: int = 50) -> str:
    """Skraca od środka: w nazwach urządzeń Windows marka stoi na końcu,
    w nawiasie („… (Poly Blackwire 3320 Series)”) — ma zostać widoczna."""
    if len(tekst) <= limit:
        return tekst
    przod = max(8, limit * 2 // 5)
    return tekst[:przod] + "…" + tekst[-(limit - przod - 1):]


def _model_urzadzenia(nazwa: str) -> str:
    """„Głośniki (3 — Realtek(R) Audio)” → „Realtek(R) Audio”.

    W nazwie Windows po rodzaju urządzenia stoi w nawiasie sterownik albo
    model; numer przed myślnikiem Windows dokleja, gdy nazwy się powtarzają.
    """
    _rodzaj, nawias, reszta = nazwa.partition(" (")
    if not nawias or not reszta.endswith(")"):
        return nazwa
    return re.sub(r"^\d+\s*[—–-]\s*", "", reszta[:-1]) or nazwa


def _wpis_urzadzenia(p: urzadzenia.PunktAudio) -> str:
    """Wpis na liście urządzeń: „Zestaw słuchawkowy USB · Poly Blackwire 3320
    Series  · używa Teams” — najpierw co to jest (laik czyta początek), potem
    model z nazwy Windows."""
    opis = urzadzenia.opis_rodzaju(p)
    wpis = f"{opis[:1].upper()}{opis[1:]} · {_model_urzadzenia(p.nazwa)}" if opis else p.nazwa
    if p.domyslne:
        wpis += "  · " + t("domyślne w Windows")
    if p.komunikacyjne:
        wpis += "  · " + t("używa Teams")
    return wpis


def _tytul_okna() -> str:
    """„Papuga – offline transcription”: nazwa z hasłem w języku okna."""
    return f"{APP_NAME} – {t(WYDANIE.haslo)}" if WYDANIE.haslo else APP_NAME


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
        teksty.ustaw_z_ustawien(self.settings.jezyk)
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

        krok(t("Wykrywam sprzęt…"))
        self.hw = probe.probe()
        self.rec = probe.recommend(self.hw)

        krok(t("Sprawdzam kartę graficzną…"))
        self._gpu_gotowe = doctor.cuda_usable()

        krok(t("Buduję interfejs…"))
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
            # Zawsze: lista „Obraz” musi wiedzieć, czy ffmpeg umie nagrywać
            # okna; koder próbujemy tylko wtedy, gdy obraz jest włączony.
            self._rozgrzej_obraz(koder=self.settings.nagranie_obraz)

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
        r.title(_tytul_okna())
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
            marka, text=t(WYDANIE.haslo or "transkrypcja audio i wideo"),
            style="Subtitle.TLabel",
        ).grid(row=1, column=1, sticky="nw")

        # Karta graficzna: kropka niesie stan, napis jest cichy. Kolor
        # ostrzeżenia dostaje cały napis, bo wtedy jest co przeczytać.
        gpu = self.hw.gpu
        if gpu and self._gpu_gotowe:
            badge, kropka, napis = gpu.name, OK_COLOR, FG_DIM
        elif gpu:
            badge = t("{gpu}: nieaktywna, liczy procesor").format(gpu=gpu.name)
            kropka, napis = WARN_COLOR, WARN_COLOR
        else:
            badge, kropka, napis = t("Procesor, brak karty NVIDIA"), WARN_COLOR, WARN_COLOR
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
        # Pigułka z drugim językiem („EN” w polskim oknie, „PL” w angielskim)
        # stoi obok „Jak to działa?” — cicho, w tym samym rzędzie odnośników.
        self._pigulka_jezyka(podpis).pack(side="left", padx=(0, 14))
        self._link(podpis, t("Jak to działa?"), self.pokaz_pomoc).pack(side="left")
        if WYDANIE.wsparcie_url:
            self._przycisk_kawy(podpis).pack(side="left", padx=(16, 0))
        # Wydanie z podpisem autora w stopce pokazuje tu sam numer wersji —
        # nazwisko ma stać w jednym miejscu, nie w dwóch.
        ttk.Label(
            podpis,
            text=(t("wersja {w}").format(w=__version__) if WYDANIE.autor
                  else t("{wydawca}, wersja {w}").format(wydawca=WYDANIE.wydawca, w=__version__)),
            style="Dim.TLabel",
        ).pack(side="left", padx=(16, 0))


    def _pigulka_jezyka(self, parent) -> tk.Canvas:
        """Mała pigułka „EN” (w polskim oknie) albo „PL” (w angielskim).

        Klik zapisuje język w ustawieniach i uruchamia program ponownie —
        okno jest zbudowane z napisami na stałe (wzór: Nutka).
        """
        napis = teksty.drugi_jezyk().upper()
        szer, wys = 34, 20
        c = tk.Canvas(parent, width=szer, height=wys, bg=BG, highlightthickness=0,
                      cursor="hand2")

        def rysuj(nad: bool = False) -> None:
            c.delete("all")
            theme.zaokraglony(c, 1, 1, szer - 1, wys - 1, 9, fill=BG_INPUT,
                              outline=ACCENT if nad else BORDER_DROP)
            c.create_text(szer // 2, wys // 2, text=napis, fill=FG if nad else FG_DIM,
                          font=(theme.FONT_SEMI, 8))

        rysuj()
        c.bind("<Enter>", lambda _e: rysuj(True))
        c.bind("<Leave>", lambda _e: rysuj(False))
        c.bind("<Button-1>", lambda _e: self._zmien_jezyk())
        return c

    def _poczekaj_na_obraz(self) -> None:
        """Przed zamknięciem: obraz spotkania składa się jeszcze w tle (~20 s
        na godzinę). Czego nie zdąży, dokończy następny start programu."""
        nagrywarka = getattr(self, "nagrywarka", None)
        if nagrywarka is None or not nagrywarka.sklada_obraz:
            return
        self.status_var.set(t("Kończę zapisywanie obrazu spotkania…"))
        self.root.update_idletasks()
        nagrywarka.czekaj_na_obraz(90)

    def _zmien_jezyk(self) -> None:
        """Przełącznik PL/EN: zapis w ustawieniach i ponowne uruchomienie."""
        if self._nagrywa() or self._busy():
            if not messagebox.askyesno(
                APP_TITLE, t("Program jeszcze pracuje. Przerwać pracę i zmienić język teraz?"),
                parent=self.root,
            ):
                return
            if self._nagrywa():
                self.nagrywarka.stop()
                self.nagrywarka.czekaj(15)
            if self._busy():
                self.cancel_flag.set()
        self._poczekaj_na_obraz()
        self.settings.jezyk = teksty.drugi_jezyk()
        self.settings.save()
        # Nowy proces ma wziąć język z ustawień, nie ze zmiennej testowej.
        srodowisko = {k: v for k, v in os.environ.items() if k != teksty.ZMIENNA}
        polecenie = [sys.executable] if is_frozen() else [sys.executable, "-m", "whisper_automat"]
        flagi = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                 if os.name == "nt" else 0)
        try:
            # Wersja okienkowa nie ma prawidłowych strumieni — bez przekierowania
            # Popen przewraca się na „uchwyt jest nieprawidłowy”.
            subprocess.Popen(
                polecenie, env=srodowisko, cwd=str(project_root()), close_fds=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=flagi,
            )
        except OSError as exc:
            self.log(t("Nie udało się uruchomić programu ponownie: {blad}").format(blad=exc))
            return
        self._zamknij()

    def _przycisk_kawy(self, parent) -> tk.Label:
        """Odnośnik wsparcia: żółta filiżanka i napis, bez wypełnienia.

        Żółć z piór zostaje znakiem rozpoznawczym, ale po cichu. Wcześniej
        żółty kafelek był najgłośniejszym punktem okna i konkurował
        z przyciskiem Transkrybuj oraz z paskiem postępu.
        """
        przycisk = tk.Label(
            parent, text=t("Postaw kawę autorowi"), bg=BG, fg=theme.KAWA,
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
        """Zgłoszenia, diagnostyka, aktualizacje, kontakt, licencja.

        Wszystko poza diagnostyką i licencjami tylko wtedy, gdy wydanie to ma.
        """
        linki = []
        kontakt = self._napisz_do_autora if WYDANIE.kontakt_email else None
        linki.append((t("Diagnostyka"), self.show_diagnosis))
        if WYDANIE.aktualizacje:
            linki.append((t("Sprawdź aktualizacje"), self.sprawdz_aktualizacje))
        if WYDANIE.zgloszenia_url:
            linki.append((t("Zgłoś problem"), lambda: webbrowser.open(WYDANIE.zgloszenia_url)))
            if kontakt:
                linki.append((t("Kontakt"), kontakt))
        linki.append((t("Licencja") if WYDANIE.firma else t("Licencje"), self.pokaz_licencje))

        rzad = tk.Frame(stopka, bg=BG)
        rzad.grid(row=0, column=2, sticky="e")
        if kontakt and not WYDANIE.zgloszenia_url:
            # Wydanie bez repozytorium (firmowe): zgłoszenia idą mailem do
            # autora. Do 1.6.1 stał tu sam podpis „imię nazwisko – firma”
            # i ludzie czytali go jak zastrzeżenie praw do programu; teraz
            # nazwisko mówi, do kogo pisać, a klik otwiera wiadomość.
            ttk.Label(rzad, text=t("Zgłoś błąd lub pomysł:"), style="Dim.TLabel").pack(
                side="left", padx=(0, 6))
            self._link(rzad, "✉ " + (WYDANIE.osoba or WYDANIE.kontakt_email), kontakt).pack(
                side="left", padx=(0, 24))
        for i, (tekst, akcja) in enumerate(linki):
            self._link(rzad, tekst, akcja).pack(side="left", padx=(18 if i else 0, 0))

    def _napisz_do_autora(self) -> None:
        """Nowa wiadomość do autora w programie pocztowym: adres i temat
        z wersją, treść pisze użytkownik.

        Temat bez polskich znaków: klasyczny Outlook czyta adres mailto
        w stronie kodowej Windows, nie w UTF-8, i „błąd” wyszedłby
        krzaczkami. Bez programu pocztowego — adres trafia do schowka.
        """
        from urllib.parse import quote

        adres = WYDANIE.kontakt_email
        temat = quote(t("{app} {w}: problem lub sugestia").format(app=APP_TITLE, w=__version__))
        if webbrowser.open(f"mailto:{adres}?subject={temat}"):
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(adres)
        messagebox.showinfo(APP_TITLE, t(
            "Nie udało się otworzyć programu pocztowego. Napisz na adres {adres} — "
            "jest już w schowku, wystarczy wkleić.").format(adres=adres), parent=self.root)

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
            (t("Zaktualizuj teraz"), self.zaktualizuj),
            (t("Co nowego"), self.co_nowego),
            (t("Pomiń tę wersję"), self.pomin_wersje),
            (t("Później"), self._ukryj_baner),
        ]
        for i, (tekst, akcja) in enumerate(przyciski):
            ttk.Button(rzad, text=tekst, command=akcja,
                       style="Accent.TButton" if i == 0 else "TButton").grid(
                row=0, column=i, padx=(8 if i else 0, 0))
        self.baner.grid_remove()

    # -- nakładka „Transkrypcja zakończona” -------------------------------

    #: Szerokość nakładki: taka część kolejki, nie więcej niż tyle pikseli.
    WYNIK_UDZIAL, WYNIK_MAX = 0.82, 640

    def _build_result(self, wrap) -> None:
        """Komunikat o końcu pracy leży na środku kolejki — tam się patrzy
        i czeka, aż się skończy. Pasek nad kolejką (do 1.5.0) ginął u góry
        okna, a w węższym oknie ucinał tekst przed przyciskami. Gdy okno
        jest w tle, dodatkowo miga na pasku zadań (_zwroc_uwage).
        """
        self.baner_wynik = tk.Frame(wrap, bg=BANER_OK_BG, padx=22, pady=18,
                                    highlightthickness=1, highlightbackground=OK_COLOR)
        self.baner_wynik.columnconfigure(1, weight=1)
        self.baner_wynik_znak = tk.Label(self.baner_wynik, text="✓", bg=BANER_OK_BG,
                                         fg=OK_COLOR, font=(theme.FONT_SEMI, 22))
        self.baner_wynik_znak.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 16))
        self.baner_wynik_tekst = tk.Label(self.baner_wynik, text="", bg=BANER_OK_BG, fg=FG,
                                          font=(theme.FONT_SEMI, 11), anchor="w", justify="left")
        self.baner_wynik_tekst.grid(row=0, column=1, sticky="ew")
        self.baner_wynik_przyciski = tk.Frame(self.baner_wynik, bg=BANER_OK_BG)
        self.baner_wynik_przyciski.grid(row=1, column=1, sticky="w", pady=(14, 0))
        ttk.Button(self.baner_wynik_przyciski, text=t("Otwórz wyniki"), style="Accent.TButton",
                   command=self.open_output).grid(row=0, column=0)
        ttk.Button(self.baner_wynik_przyciski, text=t("OK"),
                   command=self._schowaj_wynik).grid(row=0, column=1, padx=(8, 0))
        # Szerokość idzie za kolejką, tekst łamie się do szerokości nakładki —
        # nic nie jest ucięte, w żadnym rozmiarze okna.
        self._wynik_lamanie = 0
        self.tree.bind("<Configure>", self._ustaw_wynik, add="+")
        self.baner_wynik.bind("<Configure>", self._lam_wynik)

    def _pokaz_wynik(self, tekst: str, ok: bool) -> None:
        kolor, tlo = (OK_COLOR, BANER_OK_BG) if ok else (WARN_COLOR, BANER_WARN_BG)
        self.baner_wynik.configure(bg=tlo, highlightbackground=kolor)
        self.baner_wynik_znak.configure(text="✓" if ok else "!", fg=kolor, bg=tlo)
        self.baner_wynik_tekst.configure(text=tekst, bg=tlo)
        self.baner_wynik_przyciski.configure(bg=tlo)
        self.baner_wynik.place(in_=self.tree, relx=0.5, rely=0.5, anchor="center")
        self.baner_wynik.lift()
        self._ustaw_wynik()
        self._zwroc_uwage()

    def _schowaj_wynik(self) -> None:
        self.baner_wynik.place_forget()

    def _ustaw_wynik(self, _zdarzenie=None) -> None:
        """Szerokość nakładki po zmianie rozmiaru kolejki (tylko gdy widoczna)."""
        if self.baner_wynik.winfo_manager() != "place":
            return
        szer = min(int(self.tree.winfo_width() * self.WYNIK_UDZIAL), self.WYNIK_MAX)
        self.baner_wynik.place_configure(width=max(szer, 320))

    def _lam_wynik(self, zdarzenie) -> None:
        """Łamanie tekstu do miejsca obok znaku — przy tej samej szerokości nic nie robi."""
        szer = zdarzenie.width - 2 * 22 - 2 - self.baner_wynik_znak.winfo_reqwidth() - 16
        if szer > 80 and szer != self._wynik_lamanie:
            self._wynik_lamanie = szer
            self.baner_wynik_tekst.configure(wraplength=szer)

    def _zwroc_uwage(self, dzwiek: bool = True) -> None:
        """Okno w tle: mignięcie na pasku zadań i dźwięk; na wierzchu — nic.

        Bez dźwięku w trakcie spotkania (dymek o udostępnianiu ekranu).
        """
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = int(self.root.wm_frame(), 16)
            if ctypes.windll.user32.GetForegroundWindow() == hwnd:
                return

            class FLASHWINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND),
                            ("dwFlags", wintypes.DWORD), ("uCount", wintypes.UINT),
                            ("dwTimeout", wintypes.DWORD)]

            # FLASHW_ALL | FLASHW_TIMERNOFG: miga, aż okno wróci na wierzch.
            info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x3 | 0xC, 0, 0)
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
            if dzwiek:
                self.root.bell()
        except Exception:
            pass

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

        glowny = (t("Przeciągnij tutaj nagrania lub folder") if DND_AVAILABLE
                  else t("Kliknij, aby wybrać nagrania"))
        dodatek = (t("albo kliknij i wybierz z dysku: audio lub wideo")
                   if DND_AVAILABLE else t("audio lub wideo"))
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
                      text=t("MP4, MKV, MOV, MP3, WAV, M4A i inne. Wiele plików naraz."),
                      font=(theme.FONT, 9), fill=FG_FAINT)

    def _drop_color(self, color: str) -> None:
        self._drop_nad = color == BG_DROP_HOVER
        self._rysuj_drop()

    # -- nagrywanie spotkań ------------------------------------------------

    def _build_recorder(self, parent) -> None:
        """Karta nagrywania: przycisk i licznik, pod nimi trzy źródła.

        Każde źródło ma podpis i listę, na której widać, co jest wybrane;
        wybór zapisuje się od razu. Do 1.6.1 źródła stały szarą linią
        z odnośnikiem „Zmień…” do osobnego okna — użytkownicy nie wiedzieli,
        co on zmienia, i nie wyglądał jak przycisk. Wskaźnik poziomu stoi pod
        swoim urządzeniem: najczęstszy błąd to Teams grający na inne
        urządzenie niż nagrywane, a wtedy widać, który pasek stoi.
        """
        self.nagrywarka = nagrywanie.Nagrywarka()
        self._nagranie_plik: Optional[Path] = None
        self._nagranie_kropka = False
        self._nagranie_sys_cicho_od: Optional[float] = None
        self._nagranie_podpowiedziano = False
        self._po_pracy_transkrybuj = False
        #: Co Windows wie o urządzeniach (typ, złącze, domyślne) — odświeżane
        #: w tle, gdy nic się nie nagrywa (core/urzadzenia.py).
        self._punkty: List[urzadzenia.PunktAudio] = []
        self._odswiezam_punkty = False
        #: Zestaw słuchawkowy, którego użytkownik nie chciał w tej sesji —
        #: nie pytamy o niego drugi raz.
        self._zestaw_odrzucony = ""
        #: W trakcie nagrania: skąd idzie obraz teraz (okno albo ekran, None —
        #: sam dźwięk), okno spotkania do powrotu z całego ekranu, ostatni
        #: zgłoszony stan tego okna i otwarty dymek z pytaniem.
        self._obraz_teraz = None
        self._okno_nagrania = None
        self._okno_schowane: Optional[bool] = None
        self._dymek = None
        #: Czy ffmpeg umie nagrywać okna — None, dopóki sprawdzanie w tle
        #: (_rozgrzej_obraz, kilka sekund przy pierwszym razie) nie skończy.
        self._obraz_dostepny: Optional[bool] = None
        #: Próbka mikrofonu („Sprawdź”): plik WAV, przerwanie, wątek.
        self._probka = {"plik": None, "przerwij": None, "watek": None}
        #: Wpis na liście → wybór. Urządzenia: nazwa ("" = domyślne
        #: w Windows). Obraz: None (sam dźwięk), OBRAZ_AUTO, Ekran, Okno albo
        #: (program, tytuł) okna zapamiętanego, a teraz zamkniętego.
        self._mapy_zrodel = {"mikrofon": {}, "glosniki": {}, "obraz": {}}
        self._czcionka_listy = tkfont.Font(family=theme.FONT, size=10)

        karta = theme.karta(parent, row=W_NAGRYWANIE, column=0, sticky="ew",
                            pady=(0, 12), padx=(0, 14))
        karta.columnconfigure(0, weight=1)
        wnetrze = ttk.Frame(karta, style="Card.TFrame", padding=(16, 12, 16, 12))
        wnetrze.grid(row=0, column=0, sticky="ew")
        wnetrze.columnconfigure(1, weight=1)

        # Pierwszy wiersz: nagrywanie, licznik z kropką, która miga w trakcie,
        # i to, co działa w trakcie: pauza (przerwa, rozmowa prywatna — plik
        # bez luki, licznik stoi) i fragment (F8) — znacznik, który po
        # transkrypcji stoi w tekście tam, gdzie padło coś ważnego.
        gora = tk.Frame(wnetrze, bg=BG_CARD)
        gora.grid(row=0, column=0, columnspan=4, sticky="ew")
        gora.columnconfigure(2, weight=1)
        self.rec_btn = ttk.Button(gora, text=t("Nagrywaj spotkanie"),
                                  command=self.przelacz_nagrywanie)
        self.rec_btn.grid(row=0, column=0, sticky="w")
        licznik = tk.Frame(gora, bg=BG_CARD)
        licznik.grid(row=0, column=1, sticky="w", padx=(18, 10))
        self.rec_kropka = tk.Label(licznik, text="●", bg=BG_CARD, fg=BG_CARD,
                                   font=(theme.FONT, 10))
        self.rec_kropka.pack(side="left", padx=(0, 4))
        self.rec_czas = tk.Label(licznik, text="0:00:00", bg=BG_CARD, fg=FG_DIM,
                                 font=(theme.FONT_SEMI, 15), width=7, anchor="w")
        self.rec_czas.pack(side="left")
        akcje = tk.Frame(gora, bg=BG_CARD)
        akcje.grid(row=0, column=3, sticky="e")
        self.rec_pauza_btn = ttk.Button(akcje, text=t("Pauza"), width=9, state="disabled",
                                        command=self.przelacz_pauze)
        self.rec_pauza_btn.pack(side="left")
        self.rec_znacznik_btn = ttk.Button(akcje, text=t("⚑ Zaznacz (F8)"),
                                           state="disabled", command=self.zaznacz_moment)
        self.rec_znacznik_btn.pack(side="left", padx=(6, 0))
        self.root.bind("<F8>", lambda _e: self.zaznacz_moment())

        tk.Frame(wnetrze, bg=theme.BORDER, height=1).grid(
            row=1, column=0, columnspan=4, sticky="ew", pady=(12, 10))

        # Źródła: podpis, lista (styl jak „Język nagrania” w panelu), pod
        # urządzeniami cienki wskaźnik poziomu. Od 1.6.4 listy działają też
        # w trakcie nagrania (wybór tylko dla tego nagrania), a paski chodzą
        # już przed nim (podsłuch, gdy okno jest na wierzchu).
        self.rec_paski = {}
        self.rec_listy = {}
        for i, (klucz, tekst, pasek) in enumerate((
                ("mikrofon", t("Mikrofon"), "mikrofon"),
                ("glosniki", t("Dźwięk spotkania"), "system"),
                ("obraz", t("Obraz"), None))):
            wiersz = 2 + 2 * i
            odstep = (0 if i == 0 else 8, 0)
            ttk.Label(wnetrze, text=tekst, style="Card.Dim.TLabel").grid(
                row=wiersz, column=0, sticky="w", padx=(0, 14), pady=odstep)
            var = tk.StringVar()
            # Własny styl każdej listy — _poszerz_liste ustawia w nim szerokość
            # rozwiniętej listy.
            box = ttk.Combobox(wnetrze, textvariable=var, state="readonly", width=10,
                               style=f"Zrodlo{i}.TCombobox",
                               postcommand=lambda k=klucz: self._rozwin_zrodlo(k))
            # Mikrofon ma obok „Sprawdź” i „Wycisz”, reszta sięga do prawego brzegu karty.
            szer = 1 if klucz == "mikrofon" else 3
            box.grid(row=wiersz, column=1, columnspan=szer, sticky="ew", pady=odstep)
            box.bind("<<ComboboxSelected>>", lambda _e, k=klucz: self._po_wyborze_zrodla(k))
            # Kółko myszy nad listą zmieniałoby po cichu mikrofon albo obraz.
            box.bind("<MouseWheel>", lambda _e: "break")
            self.rec_listy[klucz] = (var, box)
            if pasek:
                p = theme.PasekPostepu(wnetrze, maximum=1000, grubosc=3, podloze=BG_CARD)
                p.grid(row=wiersz + 1, column=1, columnspan=szer, sticky="ew", pady=(3, 0))
                self.rec_paski[pasek] = p
        self.rec_sprawdz_btn = ttk.Button(wnetrze, text=t("Sprawdź"), width=7,
                                          command=self.sprawdz_mikrofon)
        self.rec_sprawdz_btn.grid(row=2, column=2, sticky="e", padx=(8, 0))
        # Wyciszenie mikrofonu („jesteśmy tylko słuchaczami”): przed nagraniem
        # i w trakcie. Wyciszony — czerwony „Wyciszony”, w lewym kanale cisza.
        # Nie zapamiętuje się: po każdym nagraniu mikrofon wraca (decyzja autora).
        self._wyciszony = False
        self.rec_mik_btn = ttk.Button(wnetrze, text=t("Wycisz"), width=9,
                                      command=self.przelacz_wyciszenie)
        self.rec_mik_btn.grid(row=2, column=3, sticky="e", padx=(6, 0))
        # Okno ⇄ cały ekran w trakcie nagrania (udostępnianie ekranu) —
        # przy liście obrazu, widoczny tylko przy nagraniu z obrazem.
        self.rec_obraz_btn = ttk.Button(wnetrze, text=t("⇄ Cały ekran"),
                                        command=self.przelacz_obraz)
        self.rec_obraz_btn.grid(row=6, column=2, columnspan=2, sticky="e", padx=(8, 0),
                                pady=(8, 0))
        self.rec_obraz_btn.grid_remove()
        #: W trakcie nagrania: mikrofon i urządzenie spotkania wybrane na
        #: listach — tylko dla tego nagrania, ustawienia zostają.
        self._wybor_w_trakcie: dict = {}
        #: Czy bieżące nagranie ma obraz (tylko wtedy lista „Obraz” działa w trakcie).
        self._nagranie_z_obrazem = False
        self.podsluch = (nagrywanie.Podsluch(
            lambda m, s: self.events.put(("podsluch_poziomy", m, s)))
            if nagrywanie.dostepne() else None)
        self._odswiez_zrodla()
        self._odswiez_punkty()
        self._build_podglad()
        self._pilnuj_podsluchu()

    # -- podgląd obrazu, paski przed nagraniem, wyciszenie -----------------------

    def _build_podglad(self) -> None:
        """Dymek z podglądem pod listą „Obraz”: jak teraz wygląda wybrane okno
        albo ekran, a w trakcie nagrania — to, co się nagrywa.

        Autor (2026-10-27): „brakuje mi podglądu wideo, jak się kliknie, żeby
        się upewnić, czy to właściwe okno zaznaczone”. Dymek (nie wiersz
        w karcie — decyzja autora po zrzutach: karta urosłaby o 100 px kosztem
        kolejki) pokazuje się po wyborze na liście i na starcie nagrania
        z obrazem (na `PODGLAD_S`), po najechaniu myszą na listę i znika po
        kliknięciu. Dopóki widać — nowa klatka co 2 s (wideo.podglad, w tle).
        """
        szer, wys = PODGLAD
        #: Wątek łapiący klatkę, bieżące zdjęcie Tk, źródło, do kiedy pokazywać
        #: (monotonic), czy mysz jest nad listą, kiedy złapano ostatnią klatkę.
        self._podglad = {"watek": None, "zdjecie": None, "zrodlo": None, "do": 0.0,
                         "mysz": False, "klatka_t": 0.0}
        ramka = self.rec_podglad = tk.Frame(self.root, bg=BG_CARD, padx=10, pady=10,
                                            highlightthickness=1,
                                            highlightbackground=theme.BORDER_DROP)
        pudlo = tk.Frame(ramka, bg=BG_INPUT, width=szer, height=wys)
        pudlo.grid(row=0, column=0, sticky="nw")
        pudlo.pack_propagate(False)
        self.rec_podglad_obraz = tk.Label(pudlo, bg=BG_INPUT, bd=0)
        self.rec_podglad_obraz.pack(fill="both", expand=True)
        self.rec_podglad_tytul = tk.Label(ramka, bg=BG_CARD, fg=FG, anchor="nw", justify="left",
                                          font=(theme.FONT_SEMI, 10), wraplength=szer)
        self.rec_podglad_tytul.grid(row=1, column=0, sticky="new", pady=(8, 0))
        self.rec_podglad_opis = tk.Label(ramka, bg=BG_CARD, fg=FG_DIM, anchor="nw",
                                         justify="left", font=(theme.FONT, 9), wraplength=szer)
        self.rec_podglad_opis.grid(row=2, column=0, sticky="new", pady=(2, 0))
        for w in (ramka, pudlo, self.rec_podglad_obraz, self.rec_podglad_tytul,
                  self.rec_podglad_opis):
            w.bind("<Button-1>", lambda _e: self._schowaj_podglad())
        _var, box = self.rec_listy["obraz"]
        box.bind("<Enter>", lambda _e: self._mysz_nad_obrazem(True), add="+")
        box.bind("<Leave>", lambda _e: self._mysz_nad_obrazem(False), add="+")
        self._tik_podgladu()

    def _pokaz_podglad_na(self, sekundy: float = 0.0) -> None:
        """Dymek z podglądem od razu, na `sekundy` (domyślnie PODGLAD_S)."""
        self._podglad["do"] = time.monotonic() + (sekundy or PODGLAD_S)
        self._odswiez_podglad(nowa_klatka=True)

    def _schowaj_podglad(self) -> None:
        self._podglad["do"] = 0.0
        self._podglad["mysz"] = False
        self.rec_podglad.place_forget()

    def _mysz_nad_obrazem(self, nad: bool) -> None:
        self._podglad["mysz"] = nad
        if nad:
            self._odswiez_podglad(nowa_klatka=True)

    def _tik_podgladu(self) -> None:
        """Co 0,5 s: chowa dymek, gdy minął czas albo mysz zeszła z listy;
        póki widać — nowa klatka co 2 s."""
        self.root.after(500, self._tik_podgladu)
        p = self._podglad
        if not self.rec_podglad.winfo_ismapped() and not (
                p["mysz"] or time.monotonic() < p["do"]):
            return
        self._odswiez_podglad(nowa_klatka=time.monotonic() - p["klatka_t"] >= 2.0)

    def _zrodlo_podgladu(self):
        """(źródło do podglądu albo None, tytuł, opis) — według listy „Obraz”.

        None jako całość: podglądu nie ma (sam dźwięk albo ffmpeg nie umie obrazu).
        """
        if self._nagrywa():
            z = self._obraz_teraz
            if z is None:
                return None
            return z, t("Nagrywa się: {zrodlo}").format(zrodlo=wideo.opis_zrodla(z)), ""
        var, _box = self.rec_listy["obraz"]
        wybor = self._mapy_zrodel["obraz"].get(var.get())
        if wybor is None or self._obraz_dostepny is False:
            return None
        if wybor is OBRAZ_AUTO:
            okno = next((o for o in wideo.okna() if wideo.czy_spotkanie(o)), None)
            if okno is None:
                return (None, t("Teraz nie widzę okna spotkania"),
                        t("Otwórz Zoom, Teams albo Meet. Przy starcie nagrania program sam "
                          "znajdzie okno — a jeśli go nie będzie, zapyta, co nagrywać."))
            return okno, t("Program sam wybrał: {okno}").format(okno=wideo.opis_okna(okno)), ""
        if isinstance(wybor, tuple):
            return (None, t("{program} jest teraz zamknięty").format(
                program=wideo.nazwa_programu(wybor[0])),
                t("Okno wezmę przy starcie nagrania."))
        return wybor, wideo.opis_zrodla(wybor), ""

    def _odswiez_podglad(self, nowa_klatka: bool = False) -> None:
        """Dymek według listy „Obraz”: pokazany albo schowany, podpis, klatka w tle."""
        p = self._podglad
        widac = p["mysz"] or time.monotonic() < p["do"]
        try:
            stan = self._zrodlo_podgladu() if widac else None
        except Exception:
            stan = None
        if stan is None:
            self.rec_podglad.place_forget()
            p["zrodlo"] = None
            return
        zrodlo, tytul, opis = stan
        _var, box = self.rec_listy["obraz"]
        # Pod listą, wyrównany do jej prawego brzegu — nad kolejką, nie w karcie.
        self.rec_podglad.place(in_=box, relx=1.0, rely=1.0, y=8, anchor="ne")
        self.rec_podglad.lift()
        self.rec_podglad_tytul.configure(text=tytul)
        if zrodlo is None:
            self._pokaz_klatke(None)
            self.rec_podglad_opis.configure(text=opis)
            p["zrodlo"] = None
            return
        if _klucz_zrodla(zrodlo) != _klucz_zrodla(p["zrodlo"]):
            self._pokaz_klatke(None)
            self.rec_podglad_opis.configure(text=t("Łapię obraz…"))
            nowa_klatka = True
            p["klatka_t"] = 0.0  # łapanie poprzedniego jeszcze trwa? Następny tik złapie nowe.
        p["zrodlo"] = zrodlo
        watek = p["watek"]
        if not nowa_klatka or (watek is not None and watek.is_alive()):
            return
        p["klatka_t"] = time.monotonic()
        put = self.events.put

        def praca() -> None:
            try:
                klatka = wideo.podglad(zrodlo, PODGLAD)
            except Exception:
                klatka = None
            put(("podglad", zrodlo, klatka))

        p["watek"] = threading.Thread(target=praca, daemon=True, name="podglad")
        p["watek"].start()

    def _po_klatce(self, zrodlo, klatka: Optional[bytes]) -> None:
        if _klucz_zrodla(zrodlo) != _klucz_zrodla(self._podglad["zrodlo"]):
            return  # przyszła klatka poprzedniego wyboru
        self._pokaz_klatke(klatka)
        if klatka is not None:
            tekst = (t("Tak wygląda teraz nagrywany obraz. Podgląd pokazuje się też po "
                       "najechaniu myszą na listę „Obraz”; kliknięcie go chowa.")
                     if self._nagrywa() else
                     t("Tak wygląda teraz to źródło. Podgląd pokazuje się też po najechaniu "
                       "myszą na listę „Obraz”; kliknięcie go chowa."))
        elif isinstance(zrodlo, wideo.Okno) and wideo.okno_istnieje(zrodlo.hwnd):
            tekst = t("Okno jest zminimalizowane — przywróć je, inaczej obraz w nagraniu "
                      "będzie stał.")
        else:
            tekst = t("Tego okna już nie ma — wybierz inne z listy.")
        self.rec_podglad_opis.configure(text=tekst)

    def _pokaz_klatke(self, klatka: Optional[bytes]) -> None:
        if klatka is None:
            self._podglad["zdjecie"] = None
            self.rec_podglad_obraz.configure(image="")
            return
        try:
            zdjecie = tk.PhotoImage(data=klatka)
        except tk.TclError:
            return
        self._podglad["zdjecie"] = zdjecie  # Tk nie trzyma odwołania sam
        self.rec_podglad_obraz.configure(image=zdjecie)

    def _na_wierzchu(self) -> bool:
        """Czy główne okno programu jest teraz na pierwszym planie — tylko wtedy
        działa podsłuch. Każde okienko (głos do bazy, „Kto jest kim?”, pytanie)
        go wyłącza: okno głosu nagrywa przez własną Nagrywarkę, a dwa wątki
        z PyAudio naraz na tych samych urządzeniach to proszenie się o kłopot."""
        try:
            if self.root.state() in ("iconic", "withdrawn"):
                return False
            glowne = int(self.root.wm_frame(), 16)
        except (tk.TclError, ValueError):
            return False
        if os.name != "nt":
            return True
        import ctypes

        u32 = ctypes.WinDLL("user32")  # własna kopia: typ wyniku nie przecieka dalej
        u32.GetForegroundWindow.restype = ctypes.c_void_p
        return (u32.GetForegroundWindow() or 0) == glowne

    def _pilnuj_podsluchu(self) -> None:
        """Paski przed nagraniem. Mikrofon otwarty bez potrzeby zapala ikonę
        w zasobniku i psuje dźwięk w słuchawkach Bluetooth, więc podsłuch działa
        tylko przy oknie na wierzchu, bez nagrania i bez „Sprawdź”."""
        self.root.after(300, self._pilnuj_podsluchu)
        if self.podsluch is None:
            return
        if not self._nagrywa() and not self._probka_trwa() and self._na_wierzchu():
            s = self.settings
            self.podsluch.ustaw(None if self._wyciszony else s.nagranie_mikrofon,
                                s.nagranie_glosniki)
        elif self.podsluch.dziala:
            self.podsluch.zatrzymaj()
            if not self._nagrywa() and not self._probka_trwa():
                for pasek in self.rec_paski.values():
                    pasek["value"] = 0

    def _zatrzymaj_podsluch(self) -> None:
        """Przed nagraniem i „Sprawdź”: urządzenia mają być wolne."""
        if self.podsluch is not None:
            self.podsluch.zatrzymaj(czekaj_s=2.0)

    def _po_podsluchu(self, mik: float, system: float) -> None:
        if self._nagrywa() or self._probka_trwa():
            return
        self._ustaw_paski(-120.0 if self._wyciszony else mik, system)

    def _ustaw_paski(self, mik: float, system: float) -> None:
        for klucz, dbfs in (("mikrofon", mik), ("system", system)):
            self.rec_paski[klucz]["value"] = max(0.0, min(1.0, (dbfs + 60.0) / 60.0)) * 1000

    def przelacz_wyciszenie(self) -> None:
        """„Wycisz” przy mikrofonie: przed nagraniem i w trakcie."""
        self._wyciszony = not self._wyciszony
        if self._nagrywa():
            self.nagrywarka.wycisz(self._wyciszony)
        self._ustaw_przycisk_mikrofonu()
        if self._wyciszony:
            self.rec_paski["mikrofon"]["value"] = 0
            tekst = (t("Mikrofon wyciszony — w nagraniu jest teraz tylko dźwięk spotkania.")
                     if self._nagrywa() else
                     t("Mikrofon wyciszony — nagranie pójdzie bez Ciebie, sam dźwięk spotkania. "
                       "Po nagraniu mikrofon wróci."))
        else:
            tekst = (t("Mikrofon włączony — znów nagrywa się Twój głos.") if self._nagrywa()
                     else t("Mikrofon włączony."))
        self.status_var.set(tekst)

    def _ustaw_przycisk_mikrofonu(self) -> None:
        """Wyciszony — czerwony „Wyciszony” (widać z daleka); włączony — „Wycisz”."""
        if self._wyciszony:
            self.rec_mik_btn.configure(text=t("Wyciszony"), style="Stop.TButton")
        else:
            self.rec_mik_btn.configure(text=t("Wycisz"), style="TButton")

    def _po_wyciszeniu(self, wyciszony: bool) -> None:
        """Wątek nagrywania potwierdził wyciszenie albo włączenie mikrofonu."""
        if not self._nagrywa():
            return
        czas = _hms(self.nagrywarka.czas_trwania)
        self.log(t("Mikrofon wyciszony ({czas}).").format(czas=czas) if wyciszony
                 else t("Mikrofon włączony ({czas}).").format(czas=czas))

    def _nagrywa(self) -> bool:
        nagrywarka = getattr(self, "nagrywarka", None)
        return nagrywarka is not None and nagrywarka.trwa

    def _odswiez_zrodla(self) -> None:
        """Listy źródeł na karcie: wpisy i to, co wybrane.

        Poza nagraniem — ustawienia, w trakcie — to, z czego nagranie idzie
        naprawdę (wybór w trakcie, obraz przełączony na cały ekran i z
        powrotem). Od 1.6.4 listy działają też w trakcie; „Obraz” tylko
        w nagraniu z obrazem — obrazu nie da się dołączyć w połowie.
        """
        self._wypelnij_urzadzenie("mikrofon")
        self._wypelnij_urzadzenie("glosniki")
        nagrywa = self._nagrywa()
        if nagrywa:
            self._wypelnij_obraz_w_trakcie(wideo.okna() if self._nagranie_z_obrazem else [],
                                           wideo.ekrany() if self._nagranie_z_obrazem else [])
        else:
            self._wypelnij_obraz(wideo.okna(), wideo.ekrany())
        for klucz, (_var, box) in self.rec_listy.items():
            wylaczona = klucz == "obraz" and (
                self._obraz_dostepny is False or (nagrywa and not self._nagranie_z_obrazem))
            box.configure(state="disabled" if wylaczona else "readonly")

    def _wypelnij_urzadzenie(self, klucz: str) -> None:
        """Mikrofony albo urządzenia odtwarzania, które Windows teraz widzi.

        Nazwy Windows (core/urzadzenia.py) są tymi samymi nazwami, po których
        nagrywanie szuka urządzenia w PortAudio — ustawienie zapisuje nazwę.
        """
        var, box = self.rec_listy[klucz]
        wejscie = klucz == "mikrofon"
        zapisane = getattr(self.settings, f"nagranie_{klucz}")
        if self._nagrywa():
            zapisane = self._wybor_w_trakcie.get(klucz, zapisane)
        mapa = self._mapy_zrodel[klucz]
        mapa.clear()
        domyslne = urzadzenia.znajdz(self._punkty, "", wejscie)
        nazwa_dom = t("Domyślny w Windows") if wejscie else t("Domyślne w Windows")
        wpis_dom = nazwa_dom if domyslne is None else t("{domyslne} — teraz: {co}").format(
            domyslne=nazwa_dom,
            co=urzadzenia.opis_rodzaju(domyslne) or _skroc_srodek(domyslne.nazwa))
        mapa[wpis_dom] = ""
        wpisy, wybrany = [wpis_dom], wpis_dom
        for p in self._punkty:
            if p.wejscie != wejscie:
                continue
            wpis = _wpis_urzadzenia(p)
            if wpis in mapa:  # dwa takie same urządzenia — pełna nazwa rozróżni
                wpis += "  · " + p.nazwa
            mapa[wpis] = p.nazwa
            wpisy.append(wpis)
            if p.nazwa == zapisane:
                wybrany = wpis
        if zapisane and wybrany == wpis_dom:
            # Wybrane kiedyś, teraz odłączone: nagranie pójdzie z domyślnego,
            # a wybór zostaje na liście, dopóki ktoś go nie zmieni.
            wpis = t("{nazwa} (odłączone — pójdzie domyślne)").format(nazwa=_skroc(zapisane, 30))
            mapa[wpis] = zapisane
            wpisy.insert(1, wpis)
            wybrany = wpis
        box.configure(values=wpisy)
        var.set(wybrany)

    def _wypelnij_obraz(self, okna, ekrany) -> None:
        """Sam dźwięk, okno spotkania samo, cały ekran albo konkretne okno."""
        var, box = self.rec_listy["obraz"]
        s = self.settings
        mapa = self._mapy_zrodel["obraz"]
        mapa.clear()
        sam = t("Sam dźwięk — bez obrazu")
        mapa[sam] = None
        if self._obraz_dostepny is False:
            box.configure(values=[sam])
            var.set(sam)
            return
        auto = t("Okno spotkania — samo znajdzie Zoom, Teams, Meet…")
        mapa[auto] = OBRAZ_AUTO
        wpisy = [sam, auto]
        wybrany = auto if s.nagranie_obraz else sam
        ekran_zapamietany = (wideo.wybierz_ekran(ekrany, s.nagranie_ekran)
                             if s.nagranie_ekran else None)
        for ekran in ekrany:
            wpis = wideo.opis_ekranu(ekran, len(ekrany))
            mapa[wpis] = ekran
            wpisy.append(wpis)
            if s.nagranie_obraz and ekran is ekran_zapamietany:
                wybrany = wpis
        okno_zapamietane = wideo.wybierz_okno(okna, s.nagranie_okno_program,
                                              s.nagranie_okno_tytul)
        okno_wybrane = s.nagranie_obraz and not s.nagranie_ekran
        if okno_wybrane and s.nagranie_okno_program and okno_zapamietane is None:
            # Program zapamiętany, ale teraz zamknięty — wybór zostaje,
            # okno weźmiemy przy starcie nagrania.
            wpis = t("{program} — teraz zamknięty; okno wezmę przy starcie nagrania").format(
                program=wideo.nazwa_programu(s.nagranie_okno_program))
            mapa[wpis] = (s.nagranie_okno_program, s.nagranie_okno_tytul)
            wpisy.append(wpis)
            wybrany = wpis
        for okno in okna:
            wpis = wideo.opis_okna(okno)
            while wpis in mapa:  # dwa okna o tym samym tytule
                wpis += " "
            mapa[wpis] = okno
            wpisy.append(wpis)
            if okno_wybrane and okno is okno_zapamietane:
                wybrany = wpis
        box.configure(values=wpisy)
        var.set(wybrany)

    def _wypelnij_obraz_w_trakcie(self, okna, ekrany) -> None:
        """W trakcie nagrania z obrazem: okna i ekrany, na które da się przełączyć.

        Bez „Sam dźwięk” i „samo znajdzie” — obrazu nie da się w połowie
        wyłączyć ani zgadywać od nowa. Wybrane: to, co nagrywa się teraz.
        """
        var, box = self.rec_listy["obraz"]
        mapa = self._mapy_zrodel["obraz"]
        mapa.clear()
        teraz = self._obraz_teraz
        if not self._nagranie_z_obrazem:
            sam = t("Sam dźwięk — bez obrazu")
            mapa[sam] = None
            box.configure(values=[sam])
            var.set(sam)
            return
        wpisy, wybrany = [], ""
        for zrodlo in list(ekrany) + list(okna):
            wpis = (wideo.opis_ekranu(zrodlo, len(ekrany)) if isinstance(zrodlo, wideo.Ekran)
                    else wideo.opis_okna(zrodlo))
            while wpis in mapa:
                wpis += " "
            mapa[wpis] = zrodlo
            wpisy.append(wpis)
            if teraz is not None and _klucz_zrodla(zrodlo) == _klucz_zrodla(teraz):
                wybrany = wpis
        if not wybrany:
            # Nagrywane okno zamknięte albo spoza listy — zostaje na górze.
            wybrany = (wideo.opis_zrodla(teraz) if teraz is not None
                       else t("Okna spotkania już nie ma — wybierz inne"))
            mapa[wybrany] = teraz
            wpisy.insert(0, wybrany)
        box.configure(values=wpisy)
        var.set(wybrany)

    def _rozwin_zrodlo(self, klucz: str) -> None:
        """Lista się rozwija: świeże urządzenia albo okna (zestaw podłączony
        przed chwilą ma już na niej być) i szerokość na najdłuższy wpis."""
        if klucz == "obraz" and self._nagrywa():
            self._wypelnij_obraz_w_trakcie(wideo.okna(), wideo.ekrany())
        elif klucz == "obraz":
            self._wypelnij_obraz(wideo.okna(), wideo.ekrany())
        else:
            punkty = urzadzenia.punkty()
            if punkty:
                self._punkty = punkty
            self._wypelnij_urzadzenie(klucz)
        self._poszerz_liste(klucz)

    def _poszerz_liste(self, klucz: str) -> None:
        """Rozwinięta lista ttk ma szerokość pola i ucięłaby długie nazwy
        urządzeń i okien. `postoffset` w stylu pola mówi, o ile ma być
        szersza (ttk::combobox::PlacePopdown dolicza go przy każdym
        rozwinięciu) — nie dalej niż do prawej krawędzi ekranu."""
        _var, box = self.rec_listy[klucz]
        potrzeba = max((self._czcionka_listy.measure(w) for w in box["values"]), default=0) + 40
        do_krawedzi = box.winfo_screenwidth() - box.winfo_rootx() - 8
        dodatek = max(0, min(potrzeba, do_krawedzi) - box.winfo_width())
        ttk.Style(self.root).configure(box.cget("style"), postoffset=(0, 0, dodatek, 0))

    def _po_wyborze_zrodla(self, klucz: str) -> None:
        """Wybór z listy na karcie zapisuje się od razu — bez okna i „Zapisz”."""
        var, box = self.rec_listy[klucz]
        box.selection_clear()
        mapa = self._mapy_zrodel[klucz]
        if var.get() not in mapa:
            return
        wybor = mapa[var.get()]
        if self._nagrywa():
            self._zmien_w_trakcie(klucz, wybor)
            return
        s = self.settings
        podpowiedz = ""
        if klucz != "obraz":
            setattr(s, f"nagranie_{klucz}", wybor)
        else:
            s.nagranie_obraz = wybor is not None
            # Ekran i okno się wykluczają; „samo” — oba puste.
            s.nagranie_ekran = wybor.urzadzenie if isinstance(wybor, wideo.Ekran) else ""
            if isinstance(wybor, wideo.Okno):
                s.nagranie_okno_program, s.nagranie_okno_tytul = wybor.program, wybor.tytul
            elif isinstance(wybor, tuple):
                s.nagranie_okno_program, s.nagranie_okno_tytul = wybor
            else:
                s.nagranie_okno_program = s.nagranie_okno_tytul = ""
            if isinstance(wybor, wideo.Ekran):
                podpowiedz = t("Nagra się wszystko, co widać na ekranie — także powiadomienia. "
                               "Obraz trafi do pliku MKV obok nagrania.")
            elif wybor is OBRAZ_AUTO:
                podpowiedz = t("Przy starcie nagrania program sam znajdzie okno spotkania. "
                               "Obraz trafi do pliku MKV obok nagrania.")
            elif wybor is not None:
                podpowiedz = t("Okno nagrywa się także zasłonięte innymi oknami. "
                               "Obraz trafi do pliku MKV obok nagrania.")
        s.save()
        if klucz == "obraz" and s.nagranie_obraz:
            self._rozgrzej_obraz()
        self._odswiez_zrodla()
        if klucz == "obraz":
            self._pokaz_podglad_na()
        if podpowiedz and not self._busy():
            self.status_var.set(podpowiedz)

    def _zmien_w_trakcie(self, klucz: str, wybor) -> None:
        """Wybór na liście w trakcie nagrania — tylko dla tego nagrania,
        ustawienia zostają (następne nagranie zacznie się jak dotąd)."""
        if klucz == "obraz":
            if (isinstance(wybor, (wideo.Okno, wideo.Ekran))
                    and _klucz_zrodla(wybor) != _klucz_zrodla(self._obraz_teraz)):
                self._na_zrodlo(wybor)
            self._odswiez_zrodla()
            return
        if wybor == self._wybor_w_trakcie.get(klucz):
            return
        self._wybor_w_trakcie[klucz] = wybor
        self.nagrywarka.przelacz_urzadzenie("mikrofon" if klucz == "mikrofon" else "system", wybor)
        self.status_var.set(t("Przełączam mikrofon…") if klucz == "mikrofon"
                            else t("Przełączam dźwięk spotkania…"))
        self._odswiez_zrodla()

    def _po_przepieciu(self, tor: str, urzadzenie: str) -> None:
        """Wątek nagrywania potwierdził: tor idzie z urządzenia wybranego w trakcie."""
        tekst = (t("Mikrofon przełączony na: {urzadzenie}") if tor == "mikrofon"
                 else t("Dźwięk spotkania przełączony na: {urzadzenie}")).format(
            urzadzenie=urzadzenie)
        self.log(tekst)
        self.status_var.set(tekst)

    def _opis_zrodla(self, nazwa: str, wejscie: bool) -> str:
        p = urzadzenia.znajdz(self._punkty, nazwa, wejscie)
        if p is None:
            if nazwa and self._punkty:
                return t("{nazwa} (odłączone — pójdzie domyślne)").format(nazwa=_skroc(nazwa, 30))
            if nazwa:
                return _skroc(nazwa)
            return t("domyślny") if wejscie else t("domyślne urządzenie odtwarzania")
        opis = urzadzenia.opis_rodzaju(p, krotko=True)
        return opis or _skroc(p.nazwa)

    def _odswiez_punkty(self) -> None:
        """Co kilka sekund, w tle: lista urządzeń Windows do linii źródeł
        i do pytania o zestaw słuchawkowy. W trakcie nagrania tory są już
        otwarte, więc linia stoi."""
        if not self._odswiezam_punkty and not self._nagrywa():
            self._odswiezam_punkty = True
            threading.Thread(
                target=lambda: self.events.put(("urzadzenia_okno", urzadzenia.punkty())),
                daemon=True).start()
        self.root.after(5000, self._odswiez_punkty)

    def _po_punktach(self, punkty) -> None:
        self._odswiezam_punkty = False
        if punkty != self._punkty:
            self._punkty = punkty
            # W trakcie nagrania listę przysyła wątek nagrywania (co 3 s),
            # więc linia źródeł nadąża za przepiętym urządzeniem.
            self._odswiez_zrodla()

    def przelacz_nagrywanie(self) -> None:
        if self._nagrywa():
            self.zatrzymaj_nagrywanie()
        else:
            self.rozpocznij_nagrywanie()

    def _katalog_nagran(self) -> Path:
        return Path(self.settings.output_dir or str(default_output_dir())) / "Nagrania"

    def rozpocznij_nagrywanie(self) -> None:
        if self._probka_trwa():
            messagebox.showinfo(APP_TITLE, t("Poczekaj, aż skończy się próbka mikrofonu."),
                                parent=self.root)
            return
        settings = self._collect_settings()
        settings.save()
        self._zaproponuj_zestaw(settings)
        zrodlo = self._zrodlo_obrazu(settings)
        if zrodlo is False:
            return
        # Urządzenia mają być wolne, zanim otworzy je nagrywanie.
        self._zatrzymaj_podsluch()
        put = self.events.put
        zdarzenia = nagrywanie.ZdarzeniaNagrywania(
            poziomy=lambda m, s: put(("nagranie_poziomy", m, s)),
            czas=lambda s: put(("nagranie_czas", s)),
            ostrzezenie=lambda t: put(("nagranie_ostrzezenie", t)),
            blad=lambda t: put(("nagranie_blad", t)),
            zakonczono=lambda p, s: put(("nagranie_koniec", str(p), s)),
            anulowano=lambda: put(("nagranie_anulowane",)),
            pauza=lambda p: put(("nagranie_pauza", p)),
            urzadzenia=lambda u: put(("urzadzenia_okno", u)),
            obraz=lambda p, b: put(("nagranie_obraz", str(p) if p else "", b)),
            okno_spotkania=lambda s: put(("nagranie_okno_spotkania", s)),
            zrodlo_obrazu=lambda z: put(("nagranie_zrodlo_obrazu", z)),
            przepiety=lambda tor, u: put(("nagranie_przepiety", tor, u)),
            mikrofon=lambda w: put(("nagranie_mikrofon", w)),
        )
        try:
            plik = self.nagrywarka.start(
                self._katalog_nagran(), settings.nagranie_mikrofon,
                settings.nagranie_glosniki, zdarzenia, zrodlo_obrazu=zrodlo,
                wyciszony=self._wyciszony)
        except nagrywanie.BladNagrywania as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self.root)
            return
        self._nagranie_plik = plik
        self._nagranie_sys_cicho_od = None
        self._nagranie_podpowiedziano = False
        self._obraz_teraz = zrodlo
        self._nagranie_z_obrazem = zrodlo is not None
        self._wybor_w_trakcie = {"mikrofon": settings.nagranie_mikrofon,
                                 "glosniki": settings.nagranie_glosniki}
        self._okno_nagrania = zrodlo if isinstance(zrodlo, wideo.Okno) else None
        self._okno_schowane = None
        self._ustaw_stan_nagrywania(True)
        self.log(t("Nagrywam spotkanie: {plik}").format(plik=plik.name))
        if zrodlo is not None:
            self.log(t("Nagrywam też obraz: {zrodlo}").format(zrodlo=wideo.opis_zrodla(zrodlo)))
        if self._wyciszony:
            self.log(t("Mikrofon wyciszony od początku — nagrywa się sam dźwięk spotkania."))
            self.status_var.set(t("Nagrywam z wyciszonym mikrofonem — Twojego głosu nie będzie. "
                                  "„Wyciszony” włącza mikrofon."))
        else:
            self.status_var.set(t("Nagrywam. Poinformuj uczestników, że spotkanie jest nagrywane."))

    def zatrzymaj_nagrywanie(self) -> None:
        self.rec_btn.configure(state="disabled", text=t("Zapisuję…"))
        self.rec_pauza_btn.configure(state="disabled")
        self.rec_znacznik_btn.configure(state="disabled")
        self.status_var.set(t("Kończę nagranie…"))
        self.nagrywarka.stop()

    def przelacz_pauze(self) -> None:
        if not self._nagrywa():
            return
        if self.nagrywarka.stan == self.nagrywarka.PAUZA:
            self.nagrywarka.wznow()
        elif self.nagrywarka.stan == self.nagrywarka.NAGRYWA:
            self.nagrywarka.pauza()
        self.rec_pauza_btn.configure(state="disabled")

    def _po_pauzie(self, pauza: bool) -> None:
        """Wątek roboczy potwierdził pauzę albo wznowienie."""
        if not self._nagrywa():
            return
        self.rec_pauza_btn.configure(state="normal", text=t("Wznów") if pauza else t("Pauza"))
        if pauza:
            self.rec_kropka.configure(fg=FG_DIM)
            self.status_var.set(t("Pauza — nagranie wstrzymane. „Wznów” nagrywa dalej do tego samego pliku."))
            self.log(t("Pauza w nagraniu ({czas})").format(czas=_hms(self.nagrywarka.czas_trwania)))
        else:
            self.status_var.set(t("Nagrywam. Poinformuj uczestników, że spotkanie jest nagrywane."))
            self.log(t("Nagranie wznowione."))
            self._migaj_kropka()

    def zaznacz_moment(self) -> None:
        """„Zaznacz” (F8): pierwsze naciśnięcie zaczyna fragment, drugie go kończy.

        Dwa szybkie naciśnięcia (krócej niż 2 s) to pojedynczy moment. Przycisk
        w trakcie fragmentu jest wyróżniony i mówi „Zakończ” — widać, że coś
        się dzieje (w 1.6.0 zostawał bez zmian i nie było wiadomo, czy działa).
        """
        if not self._nagrywa():
            return
        wpis = self.nagrywarka.zaznacz()
        if wpis is None:
            return
        od = _hms(wpis["czas"])
        if wpis["otwarty"]:
            self.rec_znacznik_btn.configure(text=t("⚑ Zakończ (F8)"), style="Accent.TButton")
            self.log(t("Fragment {n}: początek {czas}").format(n=wpis["nr"], czas=od))
            self.status_var.set(t("Zaznaczasz fragment {n} od {czas} — naciśnij „Zakończ” (F8), "
                                  "gdy się skończy.").format(n=wpis["nr"], czas=od))
            return
        self.rec_znacznik_btn.configure(text=t("⚑ Zaznacz (F8)"), style="TButton")
        if wpis["koniec"] is None:
            self.log(t("Znacznik {czas}").format(czas=od))
            self.status_var.set(t("Zaznaczono moment {czas} — w transkrypcji będzie ⚑.").format(czas=od))
            return
        do = _hms(wpis["koniec"])
        self.log(t("Fragment {n}: {od}–{do}").format(n=wpis["nr"], od=od, do=do))
        self.status_var.set(t("Fragment {n}: {od}–{do} — w transkrypcji będzie oznaczony ⚑.").format(
            n=wpis["nr"], od=od, do=do))

    def _ustaw_stan_nagrywania(self, nagrywa: bool) -> None:
        if nagrywa:
            self.rec_btn.configure(text=t("Zatrzymaj nagranie"), style="Stop.TButton",
                                   state="normal")
            self.rec_pauza_btn.configure(state="normal", text=t("Pauza"))
            self.rec_znacznik_btn.configure(state="normal", text=t("⚑ Zaznacz (F8)"),
                                            style="TButton")
            self.rec_sprawdz_btn.configure(state="disabled")
            self.rec_czas.configure(fg=FG)
            self.start_btn.configure(state="disabled")
            self._migaj_kropka()
            # Listy pokazują, z czego nagranie idzie teraz, i dają się zmienić
            # (tylko dla tego nagrania); obok obrazu — „⇄ Cały ekran”.
            self._odswiez_zrodla()
            self._ustaw_przycisk_obrazu()
            self._pokaz_podglad_na()
        else:
            self.rec_btn.configure(text=t("Nagrywaj spotkanie"), style="TButton", state="normal")
            self.rec_pauza_btn.configure(state="disabled", text=t("Pauza"))
            self.rec_znacznik_btn.configure(state="disabled", text=t("⚑ Zaznacz (F8)"),
                                            style="TButton")
            self.rec_sprawdz_btn.configure(state="normal")
            self.rec_czas.configure(text="0:00:00", fg=FG_DIM)
            self.rec_kropka.configure(fg=BG_CARD)
            for pasek in self.rec_paski.values():
                pasek["value"] = 0
            if not self._busy():
                self.start_btn.configure(state="normal")
            self._zamknij_dymek()
            self._obraz_teraz = self._okno_nagrania = self._okno_schowane = None
            self._nagranie_z_obrazem = False
            self._wybor_w_trakcie = {}
            if self._wyciszony:
                # Wyciszenie się nie zapamiętuje (decyzja autora 2026-10-27):
                # następne nagranie zaczyna się z mikrofonem.
                self._wyciszony = False
                self._ustaw_przycisk_mikrofonu()
                self.log(t("Mikrofon znów włączony — następne nagranie zacznie się z nim."))
            self._ustaw_przycisk_obrazu()
            self._odswiez_zrodla()
            self._schowaj_podglad()

    def _migaj_kropka(self) -> None:
        if not self._nagrywa():
            self.rec_kropka.configure(fg=BG_CARD)
            return
        if self.nagrywarka.stan == self.nagrywarka.PAUZA:
            # W pauzie kropka stoi, przygaszona.
            self.rec_kropka.configure(fg=FG_DIM)
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
            tekst = t("Nie słychać dźwięku spotkania. Sprawdź, na jakie urządzenie gra "
                      "Teams, i po nagraniu wybierz je w polu „Dźwięk spotkania”.")
            self.status_var.set(tekst)
            self.log(t("UWAGA (nagrywanie): {tekst}").format(tekst=tekst))

    def _po_nagraniu(self, plik: Path, sekundy: float) -> None:
        self._ustaw_stan_nagrywania(False)
        self._nagranie_plik = None
        opis = media.format_duration(sekundy)
        self.log(t("Nagranie zapisane: {plik} ({czas}).").format(plik=plik.name, czas=opis))
        if self.nagrywarka.znaczniki:
            self.log(t("Zaznaczone momenty: {n}").format(n=len(self.nagrywarka.znaczniki)))
        for nazwa, st in self.nagrywarka.statystyki.items():
            if st["luki"] or st["korekty"] or st["odrzucone"] or st["wyprzedzenia"]:
                self.log(t("  {tor}: luki {luki}, korekty dryfu {korekty}, "
                           "odrzucone próbki {odrzucone}, wyprzedzenia {wyprzedzenia}").format(
                    tor=nazwa, luki=st["luki"], korekty=st["korekty"],
                    odrzucone=st["odrzucone"], wyprzedzenia=st["wyprzedzenia"]))
        self.add_files([plik])
        if not self.settings.nagranie_transkrybuj:
            self.status_var.set(t("Nagranie zapisane ({czas}). Czeka w kolejce.").format(czas=opis))
        elif self._busy():
            self._po_pracy_transkrybuj = True
            self.status_var.set(t("Nagranie zapisane ({czas}). Transkrypcja ruszy po bieżącej pracy.")
                                .format(czas=opis))
        else:
            self.status_var.set(t("Nagranie zapisane ({czas}). Zaczynam transkrypcję…").format(czas=opis))
            self.root.after(300, lambda: self.start(tylko_nowe=True))

    def _zrodlo_obrazu(self, settings: Settings):
        """Co idzie do MKV: cały ekran albo okno zapamiętanego programu teraz.

        None — nagrywamy sam dźwięk; False — użytkownik woli nie zaczynać
        (otworzy spotkanie i kliknie jeszcze raz).
        """
        program = settings.nagranie_okno_program
        if not settings.nagranie_obraz:
            return None
        if not wideo.dostepne():
            self.log(t("UWAGA (nagrywanie): {tekst}").format(
                tekst=t("Ten ffmpeg nie umie nagrywać okien — nagrywam sam dźwięk.")))
            return None
        if settings.nagranie_ekran:
            return wideo.wybierz_ekran(wideo.ekrany(), settings.nagranie_ekran)
        lista = wideo.okna()
        if not program:
            # Automatycznie: okno programu do spotkań (lista ma rozmowy na
            # początku — okno spotkania Teams przed czatem).
            okno = next((o for o in lista if wideo.czy_spotkanie(o)), None)
            if okno is not None:
                return okno
            odpowiedz = messagebox.askyesnocancel(APP_TITLE, t(
                "Nie widzę okna spotkania (Zoom, Teams, Meet…).\n\n"
                "Tak — nagrywaj cały ekran\nNie — nagrywaj sam dźwięk\n"
                "Anuluj — nie zaczynaj: otwórz spotkanie i kliknij „Nagrywaj spotkanie” "
                "jeszcze raz"), parent=self.root)
            if odpowiedz is None:
                return False
            return wideo.wybierz_ekran(wideo.ekrany(), "") if odpowiedz else None
        okno = wideo.wybierz_okno(lista, program, settings.nagranie_okno_tytul)
        if okno is not None:
            return okno
        pytanie = t("Okno programu {program} nie jest otwarte, więc obrazu nie ma skąd wziąć.\n\n"
                    "Nagrywać sam dźwięk?\n„Nie” — otwórz spotkanie i kliknij "
                    "„Nagrywaj spotkanie” jeszcze raz.").format(
            program=wideo.nazwa_programu(program))
        if messagebox.askyesno(APP_TITLE, pytanie, parent=self.root):
            return None
        return False

    # -- obraz w trakcie nagrania: okno ⇄ cały ekran --------------------------

    def _ustaw_przycisk_obrazu(self) -> None:
        """„⇄ Cały ekran” przy nagrywaniu okna, „⇄ Okno” przy nagrywaniu ekranu."""
        z = self._obraz_teraz
        _var, lista = self.rec_listy["obraz"]
        if z is None or not self._nagrywa():
            self.rec_obraz_btn.grid_remove()
            lista.grid_configure(columnspan=3)
        else:
            self.rec_obraz_btn.configure(
                state="normal",
                text=t("⇄ Okno") if isinstance(z, wideo.Ekran) else t("⇄ Cały ekran"))
            lista.grid_configure(columnspan=1)
            self.rec_obraz_btn.grid()

    def _ekrany_po_kolei(self, ekrany):
        """Monitor okna spotkania pierwszy — ten zwykle się udostępnia."""
        okno = self._okno_nagrania
        pierwszy = wideo.ekran_okna(okno.hwnd, ekrany) if okno is not None else None
        return sorted(ekrany, key=lambda e: e is not pierwszy)

    def przelacz_obraz(self) -> None:
        """Przycisk na karcie: okno ⇄ cały ekran w trakcie nagrania.

        Przy kilku monitorach — menu, który ekran (udostępniany).
        """
        if not self._nagrywa() or self._obraz_teraz is None:
            return
        if isinstance(self._obraz_teraz, wideo.Ekran):
            self._wroc_do_okna()
            return
        ekrany = self._ekrany_po_kolei(wideo.ekrany())
        if len(ekrany) <= 1:
            if ekrany:
                self._na_zrodlo(ekrany[0])
            return
        menu = tk.Menu(self.root, tearoff=0)
        for ekran in ekrany:
            menu.add_command(label=wideo.opis_ekranu(ekran, len(ekrany)),
                             command=lambda e=ekran: self._na_zrodlo(e))
        przycisk = self.rec_obraz_btn
        menu.tk_popup(przycisk.winfo_rootx(), przycisk.winfo_rooty() + przycisk.winfo_height())

    def _wroc_do_okna(self) -> None:
        okno = self._okno_nagrania
        if okno is None or not wideo.okno_istnieje(okno.hwnd):
            s = self.settings
            lista = wideo.okna()
            okno = (wideo.wybierz_okno(lista, s.nagranie_okno_program, s.nagranie_okno_tytul)
                    or next((o for o in lista if wideo.czy_spotkanie(o)), None))
        if okno is None:
            self.status_var.set(t("Nie widzę okna spotkania — obraz zostaje z całego ekranu."))
            return
        self._na_zrodlo(okno)

    def _na_zrodlo(self, zrodlo) -> None:
        if not self._nagrywa():
            return
        self._zamknij_dymek()
        self.nagrywarka.przelacz_obraz(zrodlo)
        self.rec_obraz_btn.configure(state="disabled")
        self.status_var.set(t("Przełączam obraz…"))

    def _po_zmianie_obrazu(self, zrodlo) -> None:
        """Wątek nagrywania potwierdził: obraz idzie z nowego źródła."""
        if not self._nagrywa():
            return
        self._obraz_teraz = zrodlo
        if isinstance(zrodlo, wideo.Okno):
            self._okno_nagrania = zrodlo
        self._ustaw_przycisk_obrazu()
        self._odswiez_zrodla()
        self._pokaz_podglad_na()
        tekst = t("Obraz przełączony na: {zrodlo}").format(zrodlo=wideo.opis_zrodla(zrodlo))
        self.log(tekst)
        self.status_var.set(tekst)

    def _po_oknie_spotkania(self, schowane: bool) -> None:
        """Okno spotkania schowało się albo wróciło. Pytamy, gdy stan
        utrzyma się 1,5 s — Zoom przy starcie udostępniania potrafi mignąć."""
        self._okno_schowane = schowane
        self.root.after(1500, lambda: self._zapytaj_o_obraz(schowane))

    def _zapytaj_o_obraz(self, schowane: bool) -> None:
        if not self._nagrywa() or self._okno_schowane != schowane or self._obraz_teraz is None:
            return
        ekran_teraz = isinstance(self._obraz_teraz, wideo.Ekran)
        if schowane and not ekran_teraz:
            self._dymek_caly_ekran()
        elif not schowane and ekran_teraz:
            self._dymek_okno()
        else:
            self._zamknij_dymek()

    def _dymek_caly_ekran(self) -> None:
        ekrany = self._ekrany_po_kolei(wideo.ekrany())
        if not ekrany:
            return
        if len(ekrany) == 1:
            przyciski = [(t("Przełącz na cały ekran"), lambda: self._na_zrodlo(ekrany[0]), True)]
        else:
            przyciski = [(wideo.opis_ekranu(e, len(ekrany)), lambda e=e: self._na_zrodlo(e), i == 0)
                         for i, e in enumerate(ekrany)]
        przyciski.append((t("Nie teraz"), lambda: None, False))
        self.log(t("Okno spotkania się schowało — pytam, czy nagrywać cały ekran."))
        self._pokaz_dymek(
            t("Okno spotkania się schowało"),
            t("Udostępniasz ekran? Żeby było go widać w nagraniu, przełącz obraz na cały ekran. "
              "Nagra się wszystko, co na nim widać — także powiadomienia."),
            przyciski)

    def _dymek_okno(self) -> None:
        self.log(t("Okno spotkania wróciło — pytam, czy wrócić do nagrywania okna."))
        self._pokaz_dymek(
            t("Okno spotkania wróciło"),
            t("Udostępnianie skończone? Przełącz obraz z powrotem na okno spotkania — cały "
              "ekran nagrywa też wszystko inne, co na nim robisz."),
            [(t("Wróć do okna spotkania"), self._wroc_do_okna, True),
             (t("Zostaw cały ekran"), lambda: None, False)])

    def _pokaz_dymek(self, tytul: str, tekst: str, przyciski) -> None:
        """Dymek w rogu ekranu, na wierzchu (program jest wtedy zwykle w tle)."""
        from .dymek import Dymek

        self._zamknij_dymek()
        self.status_var.set(tekst)
        self._dymek = Dymek(self.root, tytul, tekst, przyciski, czas_s=60)
        self._zwroc_uwage(dzwiek=False)

    def _zamknij_dymek(self) -> None:
        if self._dymek is not None:
            self._dymek.zamknij()
            self._dymek = None

    def _po_obrazie(self, sciezka: str, blad: str) -> None:
        """Obraz spotkania złożony w tle, już po zapisaniu dźwięku."""
        if blad:
            self.log(t("Nie udało się złożyć obrazu spotkania: {blad}. Odcinki obrazu "
                       "(.ts) zostały w folderze Nagrania — każdy da się obejrzeć osobno.")
                     .format(blad=blad))
            return
        self.log(t("Obraz spotkania zapisany: {plik}").format(plik=Path(sciezka).name))
        if not self._busy() and not self._nagrywa():
            self.status_var.set(t("Obraz spotkania zapisany: {plik}").format(plik=Path(sciezka).name))

    def _rozgrzej_obraz(self, koder: bool = True) -> None:
        """W tle: ffmpeg z gfxcapture i próba koderów (~1 s), zanim padnie
        „Nagrywaj spotkanie” — obraz rusza wtedy od razu. Przy okazji
        składa obraz, którego składanie przerwało zamknięcie programu,
        i mówi liście „Obraz”, czy ten komputer w ogóle nagra obraz."""
        katalog = self._katalog_nagran()

        def praca() -> None:
            sciezka = wideo.ffmpeg()
            self.events.put(("obraz_dostepny", bool(sciezka)))
            if sciezka and koder:
                wideo.koder(sciezka)
            for plik in wideo.dokoncz_skladanie(katalog):
                self.events.put(("nagranie_obraz", str(plik), ""))

        threading.Thread(target=praca, daemon=True).start()

    def _po_sprawdzeniu_obrazu(self, dostepny: bool) -> None:
        if dostepny == self._obraz_dostepny:
            return
        self._obraz_dostepny = dostepny
        if not dostepny:
            self.log(t("UWAGA (nagrywanie): {tekst}").format(tekst=t(
                "Nagrywanie obrazu wymaga ffmpeg 9 lub nowszego — jest w instalatorze programu.")))
        self._odswiez_zrodla()

    def _zaproponuj_zestaw(self, settings: Settings) -> None:
        """Podłączony zestaw słuchawkowy (USB, Bluetooth, słuchawki), a nagranie
        poszłoby z mikrofonu i głośników komputera — pytamy przed startem.

        To był prawdziwy przypadek: mikrofon wbudowany wskazany kiedyś
        z nazwy zostawał na stałe, choć do rozmowy podłączono
        zestaw. „Tak” zapamiętuje zestaw w ustawieniach; gdy go nie będzie,
        nagranie wróci do domyślnego (z wpisem w dzienniku). „Nie” — nie
        pytamy o ten zestaw do końca sesji.
        """
        punkty = urzadzenia.punkty()
        if punkty:
            self._po_punktach(punkty)
        mik, wy = urzadzenia.propozycja_zestawu(
            punkty, settings.nagranie_mikrofon, settings.nagranie_glosniki)
        if mik is None and wy is None:
            return
        zestaw = "\n".join(dict.fromkeys(p.nazwa for p in (mik, wy) if p is not None))
        if zestaw == self._zestaw_odrzucony:
            return
        obecne = ", ".join(
            self._opis_zrodla(nazwa, wejscie) for nazwa, wejscie, p in (
                (settings.nagranie_mikrofon, True, mik), (settings.nagranie_glosniki, False, wy))
            if p is not None)
        pytanie = t("Podłączony jest zestaw słuchawkowy:\n{zestaw}\n\n"
                    "Nagranie poszłoby z: {obecne}.\n\n"
                    "Nagrywać przez zestaw słuchawkowy? Zapamiętam go na karcie nagrywania."
                    ).format(zestaw=zestaw, obecne=obecne)
        if messagebox.askyesno(APP_TITLE, pytanie, parent=self.root):
            if mik is not None:
                settings.nagranie_mikrofon = mik.nazwa
            if wy is not None:
                settings.nagranie_glosniki = wy.nazwa
            settings.save()
            self.log(t("Nagranie przez zestaw słuchawkowy: {zestaw}").format(
                zestaw=zestaw.replace("\n", ", ")))
        else:
            self._zestaw_odrzucony = zestaw
        self._odswiez_zrodla()

    def dodaj_moj_glos(self) -> None:
        """Okno „Dodaj mój głos do bazy” (wydanie firmowe)."""
        if self._nagrywa():
            messagebox.showinfo(APP_TITLE, t("Próbkę głosu nagrasz po zatrzymaniu nagrania."),
                                parent=self.root)
            return
        from . import glosy_okno

        self._collect_settings()
        glosy_okno.otworz(self.root, self.settings, log=self.log)

    # -- „Sprawdź” przy mikrofonie: próbka i odsłuch ---------------------------

    def sprawdz_mikrofon(self) -> None:
        """5 s z mikrofonu wybranego na liście, potem od razu odsłuch.

        Poziom idzie na pasek pod mikrofonem, ocena — na pasek stanu.
        """
        if self._nagrywa() or self._probka_trwa():
            return
        from . import speakers

        speakers.cisza()
        self._zatrzymaj_podsluch()
        p = self._probka
        self._usun_probke()
        uchwyt, sciezka = tempfile.mkstemp(prefix="probka-mikrofonu-", suffix=".wav")
        os.close(uchwyt)
        plik = p["plik"] = Path(sciezka)
        przerwij = p["przerwij"] = threading.Event()
        nazwa = self.settings.nagranie_mikrofon
        self.rec_sprawdz_btn.configure(state="disabled", text=t("● Mów…"))
        self.status_var.set(t("Mów normalnie przez 5 sekund — potem usłyszysz, jak Cię słychać."))
        put = self.events.put

        def praca() -> None:
            try:
                urzadzenie, szczyt = nagrywanie.nagraj_probke_mikrofonu(
                    nazwa, plik, 5.0, poziom=lambda db: put(("probka_poziom", db)),
                    przerwij=przerwij)
            except Exception as exc:
                put(("probka_koniec", "", -120.0, str(exc) or type(exc).__name__))
                return
            put(("probka_koniec", urzadzenie, szczyt, ""))

        p["watek"] = threading.Thread(target=praca, daemon=True, name="probka-mikrofonu")
        p["watek"].start()

    def _probka_trwa(self) -> bool:
        p = getattr(self, "_probka", None)
        return bool(p and p["watek"] is not None and p["watek"].is_alive())

    def _po_poziomie_probki(self, dbfs: float) -> None:
        if not self._nagrywa():
            self.rec_paski["mikrofon"]["value"] = max(0.0, min(1.0, (dbfs + 60.0) / 60.0)) * 1000

    def _po_probce(self, urzadzenie: str, szczyt: float, blad: str) -> None:
        self.rec_sprawdz_btn.configure(state="disabled" if self._nagrywa() else "normal",
                                       text=t("Sprawdź"))
        if not self._nagrywa():
            self.rec_paski["mikrofon"]["value"] = 0
        if blad:
            tekst = t("Nie udało się nagrać próbki: {blad}").format(blad=blad)
            self.status_var.set(tekst)
            self.log(tekst)
            return
        if szczyt < -50.0:
            tekst = t("Prawie cisza ({db:.0f} dB) z: {urzadzenie}. Sprawdź, czy mikrofon "
                      "nie jest wyciszony i czy to właściwe urządzenie.")
        elif szczyt > -3.0:
            tekst = t("Bardzo głośno ({db:.0f} dB) z: {urzadzenie} — mogą być przestery. "
                      "Odsuń mikrofon albo zmniejsz czułość w ustawieniach Windows.")
        else:
            tekst = t("Nagrano z: {urzadzenie}. Poziom {db:.0f} dB — w porządku. Odtwarzam próbkę.")
        tekst = tekst.format(db=szczyt, urzadzenie=_skroc_srodek(urzadzenie, 40))
        self.status_var.set(tekst)
        self.log(t("Próbka mikrofonu: {tekst}").format(tekst=tekst))
        self._odsluchaj_probke()

    def _odsluchaj_probke(self) -> None:
        from . import speakers

        p = self._probka
        if not p["plik"] or not Path(p["plik"]).is_file():
            return
        try:
            speakers.zagraj(p["plik"])
        except Exception as exc:
            self.status_var.set(t("Nie udało się odtworzyć próbki: {blad}").format(blad=exc))

    def _usun_probke(self) -> None:
        p = getattr(self, "_probka", None)
        if not p or not p["plik"]:
            return
        try:
            Path(p["plik"]).unlink()
        except OSError:
            pass
        p["plik"] = None

    def _zatrzymaj_probke(self) -> None:
        """Program się zamyka: koniec nagrywania i odtwarzania próbki."""
        from . import speakers

        p = getattr(self, "_probka", None)
        if not p:
            return
        if p["przerwij"] is not None:
            p["przerwij"].set()
        speakers.cisza()
        if not self._probka_trwa():
            self._usun_probke()

    def _odzyskaj_nagrania(self) -> None:
        """Po awarii: niedokończone nagrania z folderu wracają do kolejki."""
        try:
            pliki = nagrywanie.znajdz_niedokonczone(self._katalog_nagran())
        except Exception:
            return
        if not pliki:
            return
        opis = "\n".join(f"•  {p.name}" for p in pliki)
        pytanie = t("Poprzednie nagrywanie nie zostało poprawnie zakończone:\n\n{pliki}\n\n"
                    "Odzyskać nagranie i dodać je do kolejki?").format(pliki=opis)
        if not messagebox.askyesno(APP_TITLE, pytanie, parent=self.root):
            for p in pliki:
                nagrywanie.usun_znacznik(p)
            self.log(t("Niedokończone nagranie zostaje w folderze Nagrania bez zmian."))
            return
        self.status_var.set(t("Odzyskuję nagranie…"))

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
            self.log(t("Nie udało się odzyskać {plik}: {blad}").format(plik=Path(sciezka).name, blad=blad))
            self.status_var.set(t("Odzyskanie nagrania nie powiodło się."))
            return
        self.log(t("Odzyskano niedokończone nagranie: {plik}").format(plik=Path(sciezka).name))
        self.status_var.set(t("Odzyskane nagranie czeka w kolejce."))
        self.add_files([Path(sciezka)])

    def _build_queue(self, parent) -> None:
        wrap = self._karta_kolejki = theme.karta(parent, row=W_KOLEJKA, column=0,
                                                 sticky="nsew", padx=(0, 14))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(1, weight=1)

        bar = ttk.Frame(wrap, style="Card.TFrame", padding=(16, 12, 10, 6))
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text=t("Kolejka"), style="Card.Heading.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.podsumowanie = ttk.Label(bar, text="", style="Card.Dim.TLabel")
        self.podsumowanie.grid(row=0, column=1, sticky="w", padx=(10, 0), pady=(2, 0))
        ttk.Button(bar, text=t("Usuń zaznaczone"), style="Ghost.TButton",
                   command=self.remove_selected).grid(row=0, column=2, padx=(6, 0))
        ttk.Button(bar, text=t("Wyczyść"), style="Ghost.TButton",
                   command=self.clear_queue).grid(row=0, column=3, padx=(2, 0))

        self.tree = ttk.Treeview(
            wrap, columns=("dlugosc", "status", "usun"), show="tree headings", height=8
        )
        self.tree.heading("#0", text=t("Plik"), anchor="w")
        self.tree.heading("dlugosc", text=t("Długość"))
        self.tree.heading("status", text=t("Status"), anchor="w")
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
        self._menu.add_command(label=t("Usuń z kolejki"), command=self.remove_selected)
        self._menu.add_command(label=t("Wyczyść kolejkę"), command=self.clear_queue)
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(0, 8))

        # Gotowe pliki cichną — uwaga zostaje przy tych, które jeszcze czekają.
        self.tree.tag_configure("ok", foreground=FG_DIM)
        self.tree.tag_configure("err", foreground=ERR_COLOR)
        self.tree.tag_configure("run", foreground=ACCENT_HOVER)

        # Pasek przewijania tylko wtedy, gdy jest co przewijać.
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=theme.pasek_gdy_trzeba(
            scroll, row=1, column=1, sticky="ns", padx=(0, 4), pady=(0, 6)))

        self._build_result(wrap)
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
                # Karta znika razem z nakładką — po dodaniu plików nie ma
                # wracać stary komunikat.
                self._schowaj_wynik()
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
            tekst += t(", łącznie {czas}").format(czas=media.format_duration(sum(dlugosci)))
        self.podsumowanie.configure(text=tekst)

    def _build_settings(self, parent) -> None:
        karta = theme.karta(parent, row=0, column=1, rowspan=W_DZIENNIK + 1, sticky="nsew")
        # W niskim oknie panel się przewija, zamiast chować dolne sekcje.
        przewijany = theme.Przewijany(karta, tlo=BG_CARD)
        przewijany.pack(fill="both", expand=True)
        panel = przewijany.wnetrze
        panel.configure(padding=(18, 12, 18, 16))
        panel.columnconfigure(0, weight=1)

        # Panel ma się mieścić w zwykłym oknie bez przewijania (autor: „nie chcę
        # przewijania”) — stąd ciaśniejsze sekcje i etykiety obok pól, nie nad nimi.
        def sekcja(wiersz: int, tekst: str) -> None:
            ttk.Label(panel, text=tekst, style="Card.Section.TLabel").grid(
                row=wiersz, column=0, sticky="w", pady=(10, 4))

        ttk.Label(panel, text=t("Ustawienia"), style="Card.Heading.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 6))

        # Modelu nie wybiera się w oknie: program dobiera go sam do sprzętu,
        # a na każdym rozsądnym komputerze jest to large-v3-turbo — mniejsze
        # na procesorze nie są szybsze, a gubią słowa (pomiary
        # w dokumentacja/technika.md). Inny model można wymusić w trybie
        # konsolowym: --model.
        jezyk = ttk.Frame(panel, style="Card.TFrame")
        jezyk.grid(row=3, column=0, sticky="ew")
        jezyk.columnconfigure(1, weight=1)
        ttk.Label(jezyk, text=t("Język nagrania"), style="Card.Dim.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        self.lang_var = tk.StringVar()
        # Lista pokazuje napisy w języku okna, logika porównuje kody (config).
        self.lang_box = ttk.Combobox(
            jezyk, textvariable=self.lang_var,
            values=[t(label) for _c, label in LANGUAGES], state="readonly",
        )
        self.lang_box.grid(row=0, column=1, sticky="ew")

        # Formaty.
        sekcja(7, t("Zapisz jako"))
        fmt = ttk.Frame(panel, style="Card.TFrame")
        fmt.grid(row=8, column=0, sticky="ew")
        self.format_vars = {}
        for i, nazwa in enumerate(FORMATS):
            var = tk.BooleanVar(value=nazwa in self.settings.formats)
            self.format_vars[nazwa] = var
            ttk.Checkbutton(fmt, text=t(FORMAT_LABELS[nazwa]), variable=var,
                            style="Card.TCheckbutton").grid(
                row=i, column=0, sticky="w")

        # Miejsce zapisu.
        sekcja(9, t("Folder wyników"))
        out_row = ttk.Frame(panel, style="Card.TFrame")
        out_row.grid(row=10, column=0, sticky="ew")
        out_row.columnconfigure(0, weight=1)
        self.outdir_var = tk.StringVar()
        self.outdir_entry = theme.pole(out_row, self.outdir_var)
        self.outdir_entry.grid(row=0, column=0, sticky="ew", ipady=5, padx=(0, 6))
        ttk.Button(out_row, text=t("Zmień…"), command=self.choose_output).grid(
            row=0, column=1)

        self.next_to_source = tk.BooleanVar()
        ttk.Checkbutton(
            panel, text=t("Zapisuj obok pliku źródłowego"), variable=self.next_to_source,
            command=self._toggle_outdir, style="Card.TCheckbutton",
        ).grid(row=11, column=0, sticky="w", pady=(6, 0))

        # Rozpoznawanie mówców.
        sekcja(12, t("Mówcy"))
        mowcy = ttk.Frame(panel, style="Card.TFrame")
        mowcy.grid(row=13, column=0, sticky="ew")
        mowcy.columnconfigure(0, weight=1)
        self.diarize_var = tk.BooleanVar()
        ttk.Checkbutton(
            mowcy, text=t("Rozpoznaj, kto co powiedział"), variable=self.diarize_var,
            command=self._toggle_diarize, style="Card.TCheckbutton",
        ).grid(row=0, column=0, sticky="w")

        # Liczba osób pod polem, wcięta jak jego napis — w jednym wierszu
        # z nim nie mieściła się w panelu i zasłaniała „powiedział”.
        ile = ttk.Frame(mowcy, style="Card.TFrame")
        ile.grid(row=1, column=0, sticky="w", padx=(26, 0), pady=(2, 0))
        ttk.Label(ile, text=t("Ile osób:"), style="Card.TLabel").pack(side="left", padx=(0, 6))
        self.speakers_var = tk.StringVar(value="2")
        # Pole jest edytowalne. Stan "readonly" zostawiał działające tylko
        # strzałki: liczba wpisana z klawiatury była po cichu ignorowana
        # i program liczył dalej ze starą wartością.
        sprawdz = mowcy.register(self._tylko_liczba_osob)
        self.speakers_box = ttk.Spinbox(
            ile, from_=0, to=20, width=4, textvariable=self.speakers_var,
            validate="key", validatecommand=(sprawdz, "%P"),
        )
        self.speakers_box.pack(side="left")
        if WYDANIE.nagrywanie and glosy.dostepna():
            # Baza głosów firmy (core/glosy.py) — przy mówcach, bo to im
            # podpowiada imiona; o dobrowolności mówi okno po kliknięciu.
            # Było na samym dole panelu i trzeba było przewijać.
            ttk.Button(mowcy, text=t("Dodaj mój głos do bazy…"),
                       command=self.dodaj_moj_glos).grid(
                row=2, column=0, sticky="w", pady=(8, 0))

        # Wskazówka, nie ostrzeżenie — w kolorze ostrzeżenia czytała się jak błąd.
        self.diarize_hint = tk.Label(
            panel, text="", bg=BG_CARD, fg=FG_DIM, font=(theme.FONT, 9),
            justify="left", anchor="w", wraplength=310,
        )
        self.diarize_hint.grid(row=14, column=0, sticky="w", pady=(6, 0))
        self.diarize_hint.grid_remove()  # pokazuje ją _toggle_diarize

        # Nagrywanie spotkań: źródła wybiera się na karcie nagrywania, tu
        # zostaje tylko to, co dzieje się po zatrzymaniu.
        if WYDANIE.nagrywanie:
            sekcja(15, t("Nagrywanie"))
            # Lewy kanał własnego nagrania to mikrofon użytkownika — tak
            # podpisze go transkrypcja, bez zgadywania.
            imie = ttk.Frame(panel, style="Card.TFrame")
            imie.grid(row=16, column=0, sticky="ew")
            imie.columnconfigure(1, weight=1)
            ttk.Label(imie, text=t("Twoje imię w transkrypcji"),
                      style="Card.Dim.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
            self.imie_var = tk.StringVar()
            theme.pole(imie, self.imie_var, podpowiedz=t("np. Anna")).grid(
                row=0, column=1, sticky="ew", ipady=4)
            self.rec_auto_var = tk.BooleanVar(value=True)
            ttk.Checkbutton(
                panel, text=t("Transkrybuj od razu po zatrzymaniu"),
                variable=self.rec_auto_var, style="Card.TCheckbutton",
            ).grid(row=18, column=0, sticky="w", pady=(8, 0))

    def _build_actions(self, parent) -> None:
        """Pasek na dole: postęp po lewej, przyciski po prawej."""
        row = ttk.Frame(parent, style="App.TFrame")
        row.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        row.columnconfigure(0, weight=1)

        postep = ttk.Frame(row, style="App.TFrame")
        postep.grid(row=0, column=0, sticky="ew", padx=(0, 20))
        postep.columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value=t("Gotowy."))
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
        ttk.Button(przyciski, text=t("Otwórz wyniki"), command=self.open_output).grid(
            row=0, column=0, padx=(0, 8))
        self.cancel_btn = ttk.Button(
            przyciski, text=t("Przerwij"), command=self.cancel, state="disabled"
        )
        self.cancel_btn.grid(row=0, column=1, padx=(0, 8))
        self.start_btn = ttk.Button(
            przyciski, text=t("Transkrybuj"), style="Accent.TButton", command=self.start
        )
        self.start_btn.grid(row=0, column=2)

    def _build_footer(self, parent) -> None:
        stopka = ttk.Frame(parent, style="App.TFrame")
        stopka.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        stopka.columnconfigure(1, weight=1)

        self._przelacznik = self._link(stopka, t("▸  Pokaż dziennik"), self._przelacz_dziennik)
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
            self._przelacznik.configure(text=t("▾  Ukryj dziennik"))
            self.log_text.see("end")
        else:
            self._dziennik.grid_remove()
            self._przelacznik.configure(text=t("▸  Pokaż dziennik"))

    # -- ustawienia <-> widżety --------------------------------------------

    def _apply_settings(self) -> None:
        s = self.settings
        # Model wybrany kiedyś ręcznie (gdy okno jeszcze na to pozwalało)
        # nie może dalej działać po cichu — wracamy do doboru automatycznego.
        s.model = s.device = s.compute_type = ""

        self.lang_var.set(
            t(dict(LANGUAGES).get(s.language, "polski"))
        )
        self.next_to_source.set(s.output_next_to_source)
        self.diarize_var.set(s.diarize)
        self.speakers_var.set(str(s.speakers))
        self.outdir_var.set(s.output_dir or str(default_output_dir()))
        if WYDANIE.nagrywanie:
            self.rec_auto_var.set(s.nagranie_transkrybuj)
            self.imie_var.set(s.nagranie_imie or imie_uzytkownika())
        self._pokaz_koniec_sciezki()
        self._toggle_outdir()
        self._toggle_diarize()

        self.log(t("{app} — gotowy.").format(app=APP_TITLE))
        self.log(t("Rekomendacja sprzętowa: {model} ({urzadzenie}, {precyzja})").format(
            model=self.rec.model, urzadzenie=self.rec.device, precyzja=self.rec.compute_type))
        for warning in self.rec.warnings:
            self.log(t("UWAGA: {tekst}").format(tekst=warning))
        if not DND_AVAILABLE:
            self.log(t(
                "Brak tkinterdnd2 — przeciąganie plików wyłączone, "
                "użyj kliknięcia w pole powyżej."
            ))

    def _collect_settings(self) -> Settings:
        s = self.settings
        s.model = s.device = s.compute_type = ""

        chosen_lang = self.lang_var.get()
        for code, text in LANGUAGES:
            if t(text) == chosen_lang:
                s.language = code
                break

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
            s.nagranie_imie = self.imie_var.get().strip()
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
                text=t("Wydłuża pracę mniej więcej o długość nagrania. Dokładna "
                       "liczba osób dzieli wyraźnie lepiej niż 0 („zgadnij”)."),
            )
            self.diarize_hint.grid()
        else:
            # Pusta etykieta też zajmowała wiersz — panel przewijał się bez potrzeby.
            self.diarize_hint.configure(text="")
            self.diarize_hint.grid_remove()

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
            title=t("Wybierz pliki audio lub wideo"),
            filetypes=[
                (t("Pliki audio i wideo"), patterns),
                (t("Wszystkie pliki"), "*.*"),
            ],
        )
        if paths:
            self.add_files([Path(p) for p in paths])

    def add_files(self, paths) -> None:
        found = media.collect_media(paths)
        if not found:
            self.log(t("Przeciągnięte pliki nie zawierają obsługiwanych formatów."))
            return

        added = 0
        known = {str(p).lower() for p in self.files}
        for path in found:
            if str(path).lower() in known:
                continue
            self.files.append(path)
            self.tree.insert("", "end", iid=str(path), text=path.name,
                             values=("…", t("oczekuje"), USUN))
            added += 1

        self._odswiez_pusta_kolejke()
        if added:
            self.log(t("Dodano {n} plik(ów) do kolejki.").format(n=added))
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
                self.events.put(("row_error", str(path), t("nieczytelny: {blad}").format(blad=exc)))
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
                APP_TITLE, t("Trwa nagrywanie — transkrypcja ruszy po jego zatrzymaniu."),
                parent=self.root,
            )
            return
        if not self.files:
            messagebox.showinfo(
                APP_TITLE, t("Najpierw dodaj pliki — przeciągnij je w pole u góry.")
            )
            return

        settings = self._collect_settings()
        if not settings.formats:
            messagebox.showwarning(
                APP_TITLE, t("Zaznacz przynajmniej jeden format zapisu.")
            )
            return
        settings.save()
        self._schowaj_wynik()

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
                pytanie = t("Wszystkie pliki w kolejce są już przetworzone. Przetworzyć je "
                            "jeszcze raz?\n\nNowe pliki wyników dostaną numer w nazwie, "
                            "stare zostaną.")
            else:
                pytanie = t("Gotowe pliki w kolejce: {n}. Przetworzyć je jeszcze "
                            "raz razem z nowymi?\n\n„Nie” przetworzy tylko te, które "
                            "jeszcze czekają.").format(n=len(gotowe))
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
                self.tree.item(iid, values=(self.tree.item(iid, "values")[0], t("oczekuje"), USUN),
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
            put(("log", t("BŁĄD KRYTYCZNY: {blad}").format(blad=f"{type(exc).__name__}: {exc}")))
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
        self.log(t(
            "Pierwsze uruchomienie: pobieram model rozpoznawania mowy {model} "
            "(ok. {rozmiar}). To jednorazowe — potem "
            "program działa bez internetu, a nagrania nigdy nie opuszczają "
            "komputera."
        ).format(model=model, rozmiar=download.rozmiar_opis(model)))
        self.pobierz_model(model)

    def pobierz_model(self, model: str) -> None:
        if self._busy():
            return
        self.cancel_flag.clear()
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.file_progress["value"] = 0
        self.overall_var.set("")
        self.status_var.set(t("Pobieram model {model}…").format(model=model))
        put = self.events.put
        co = t("model {model}").format(model=model)

        def praca() -> None:
            try:
                download.pobierz_model(
                    model,
                    models_dir(),
                    on_progress=lambda b, w, v: put(("download_progress", co, b, w, v)),
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
            self.status_var.set(t("Pobieranie przerwane — następna próba ruszy od tego miejsca."))
            self.log(self.status_var.get())
        elif blad:
            self.status_var.set(t("Nie udało się pobrać modelu."))
            self.log(t("BŁĄD: {blad}").format(blad=blad))
            messagebox.showerror(APP_TITLE, blad, parent=self.root)
        else:
            self.file_progress["value"] = 1000
            self.status_var.set(t("Model {model} gotowy. Możesz przeciągać nagrania.").format(model=model))

    # -- aktualizacje (GitHub albo folder firmowy) -------------------------

    def sprawdz_aktualizacje(self, reczne: bool = True) -> None:
        """Pyta o nową wersję (GitHub albo folder firmowy), w osobnym wątku.

        Sprawdzenie w tle przy starcie milczy, gdy nic nie znajdzie albo nie
        ma sieci. Ręczne (z odnośnika) zawsze odpowiada.
        """
        if self._sprawdzam:
            return
        self._sprawdzam = True
        if reczne and not self._busy():
            self.status_var.set(t("Sprawdzam, czy jest nowa wersja…"))

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
                self.status_var.set(t("Nie udało się sprawdzić aktualizacji."))
                messagebox.showwarning(
                    APP_TITLE, t("Nie udało się sprawdzić, czy jest nowa wersja.\n\n{blad}").format(blad=blad),
                    parent=self.root,
                )
            else:
                self.log(t("Sprawdzanie aktualizacji nie powiodło się: {blad}").format(blad=blad))
            return

        self.settings.aktualizacje_sprawdzone = time.time()
        self.settings.save()

        if akt is None:
            if reczne:
                if not self._busy():
                    self.status_var.set(t("Masz najnowszą wersję ({w}).").format(w=__version__))
                messagebox.showinfo(
                    APP_TITLE, t("Masz najnowszą wersję {app} ({w}).").format(app=APP_TITLE, w=__version__),
                    parent=self.root,
                )
            return
        if not reczne and akt.wersja == self.settings.pominieta_wersja:
            self.log(t("Dostępna jest wersja {w} (pominięta na Twoje życzenie).").format(w=akt.wersja))
            return
        self._pokaz_baner(akt)

    def _pokaz_baner(self, akt) -> None:
        self._aktualizacja = akt
        self.baner_tekst.configure(
            text=t("Dostępna nowa wersja {app} {nowa}   (masz {obecna})").format(
                app=APP_TITLE, nowa=akt.wersja, obecna=__version__)
        )
        self.baner.grid()
        self.log(t("Dostępna nowa wersja: {w}. Kliknij „Zaktualizuj teraz”.").format(w=akt.wersja))
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
            self.log(t("Wersja {w} pominięta — „Sprawdź aktualizacje” pokaże ją ponownie.")
                     .format(w=self._aktualizacja.wersja))
        self._ukryj_baner()

    def co_nowego(self) -> None:
        akt = self._aktualizacja
        if akt is None:
            return
        if akt.opis and not akt.strona.lower().startswith(("http://", "https://")):
            # Wydanie z folderu firmowego nie ma strony — opis pokazujemy w oknie.
            _show_report(self.root, t("Co nowego w wersji {w}").format(w=akt.wersja), akt.opis)
        else:
            self._otworz_strone_wydania(akt)

    def _otworz_strone_wydania(self, akt) -> None:
        """Strona wydania na GitHubie albo folder firmowy w Eksploratorze."""
        if not akt.strona:
            return
        if akt.strona.lower().startswith(("http://", "https://")):
            webbrowser.open(akt.strona)
        else:
            _open_folder(Path(akt.strona))

    def zaktualizuj(self) -> None:
        akt = self._aktualizacja
        if akt is None:
            return
        if self._busy():
            messagebox.showinfo(
                APP_TITLE,
                t("Program jeszcze pracuje. Zaktualizuj, gdy skończy — pasek "
                  "z nową wersją zostanie na miejscu."),
                parent=self.root,
            )
            return
        if not update.mozna_zainstalowac(akt):
            # Bez instalatora, sumy albo prawidłowego podpisu nie uruchamiamy
            # niczego — zostaje pobranie ręczne ze strony wydania.
            self.log(t("Wersji {w} program nie zainstaluje sam: {powod}. Otwieram stronę wydania.")
                     .format(w=akt.wersja,
                             powod=akt.uwaga or t("wydanie nie ma sprawdzalnego instalatora")))
            self._otworz_strone_wydania(akt)
            return

        self._ukryj_baner()
        self.cancel_flag.clear()
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.file_progress["value"] = 0
        self.status_var.set(t("Pobieram wersję {w}…").format(w=akt.wersja))
        put = self.events.put
        co = t("wersję {w}").format(w=akt.wersja)

        def praca() -> None:
            try:
                sciezka = update.pobierz(
                    akt,
                    on_progress=lambda b, w, v: put(
                        ("download_progress", co, b, w, v)),
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
            self.status_var.set(t("Pobieranie aktualizacji przerwane."))
            self.baner.grid()
            return
        if blad:
            self.status_var.set(t("Nie udało się pobrać aktualizacji."))
            self.log(t("BŁĄD: {blad}").format(blad=blad))
            self.baner.grid()
            messagebox.showerror(APP_TITLE, blad, parent=self.root)
            return

        self.file_progress["value"] = 1000
        self.status_var.set(t("Instaluję nową wersję — program uruchomi się ponownie sam."))
        try:
            update.uruchom_instalator(sciezka)
        except Exception as exc:
            self.log(t("BŁĄD: nie udało się uruchomić instalatora: {blad}").format(blad=exc))
            messagebox.showerror(
                APP_TITLE,
                t("Nie udało się uruchomić instalatora:\n{blad}\n\nPlik: {plik}").format(
                    blad=exc, plik=sciezka),
                parent=self.root,
            )
            return
        # Instalator podmienia pliki programu — trzeba mu je zwolnić.
        self.root.after(300, self._zamknij)

    def cancel(self) -> None:
        if self._busy():
            self.cancel_flag.set()
            self.status_var.set(t("Przerywam po bieżącym fragmencie…"))
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
            self._opis_pliku = t("plik {i} z {n}").format(i=index + 1, n=total)
            self.overall_var.set(self._opis_pliku)
            self.file_progress["value"] = 0
            self._postep_biezacy = 0.0
            if self._praca_start is None:
                # Zegar rusza z pierwszym plikiem, nie z ładowaniem modelu —
                # inaczej pierwsze szacunki byłyby zawyżone o start silnika.
                self._praca_start = time.monotonic()
            self._biezacy = path
            self._set_row(path, t("przetwarzanie…"), "run")
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
                self.log(t("Nie udało się otworzyć okna mówców: {blad}").format(blad=exc))
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
            self.log(t("UWAGA (nagrywanie): {tekst}").format(tekst=event[1]))
            self.status_var.set(event[1])
        elif kind == "nagranie_blad":
            self._ustaw_stan_nagrywania(False)
            self.log(t("BŁĄD nagrywania: {blad}").format(blad=event[1]))
            self.status_var.set(t("Nagrywanie przerwane."))
            messagebox.showerror(APP_TITLE, event[1], parent=self.root)
        elif kind == "nagranie_koniec":
            self._po_nagraniu(Path(event[1]), event[2])
        elif kind == "nagranie_anulowane":
            self._ustaw_stan_nagrywania(False)
            self.status_var.set(t("Nagranie odrzucone — nic nie zostało zapisane."))
        elif kind == "nagranie_pauza":
            self._po_pauzie(bool(event[1]))
        elif kind == "urzadzenia_okno":
            self._po_punktach(event[1])
        elif kind == "nagranie_odzyskane":
            self._po_odzyskaniu(event[1], event[2])
        elif kind == "nagranie_obraz":
            self._po_obrazie(event[1], event[2])
        elif kind == "nagranie_okno_spotkania":
            self._po_oknie_spotkania(bool(event[1]))
        elif kind == "nagranie_zrodlo_obrazu":
            self._po_zmianie_obrazu(event[1])
        elif kind == "nagranie_przepiety":
            self._po_przepieciu(event[1], event[2])
        elif kind == "nagranie_mikrofon":
            self._po_wyciszeniu(bool(event[1]))
        elif kind == "podsluch_poziomy":
            self._po_podsluchu(event[1], event[2])
        elif kind == "podglad":
            self._po_klatce(event[1], event[2])
        elif kind == "probka_poziom":
            self._po_poziomie_probki(event[1])
        elif kind == "probka_koniec":
            self._po_probce(event[1], event[2], event[3])
        elif kind == "obraz_dostepny":
            self._po_sprawdzeniu_obrazu(bool(event[1]))
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
                iid, t("✓  gotowe, {x:.1f}× szybciej").format(x=job.result.speed_ratio), "ok"
            )
            if job.outputs:
                self.last_output_dir = next(iter(job.outputs.values())).parent
        else:
            self._set_row(iid, job.error[:60] or t("błąd"), "err")

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
            self.status_var.set(t("Przerwano. Ukończono {pliki}.").format(pliki=_liczba_plikow(done)))
        elif failed:
            self.status_var.set(t("Zakończono: {ok} OK, {zle} z błędem.").format(ok=done, zle=failed))
            self._pokaz_wynik(t("Zakończono z błędami: {ok} OK, {zle} z błędem — "
                                "szczegóły w dzienniku.").format(ok=done, zle=failed), ok=False)
        else:
            self.status_var.set(t("Gotowe — przetworzono {pliki}.").format(pliki=_liczba_plikow(done)))
            self.overall_progress["value"] = 1000
            if done:
                self._pokaz_wynik(t("Transkrypcja zakończona — {pliki} w folderze wyników.")
                                  .format(pliki=_liczba_plikow(done)), ok=True)

        self.log(self.status_var.get())

        if done and self.settings.open_output_when_done and not self.cancel_flag.is_set():
            self.open_output()

    # -- akcje pomocnicze --------------------------------------------------

    def choose_output(self) -> None:
        path = filedialog.askdirectory(
            title=t("Gdzie zapisywać transkrypcje?"),
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
            messagebox.showinfo(APP_TITLE, t("Folder nie istnieje: {folder}").format(folder=target))
            return
        _open_folder(Path(target))

    def pokaz_pomoc(self) -> None:
        """Krótko: jak używać i co program potrafi."""
        if getattr(self, "_pomoc", None) is not None and self._pomoc.winfo_exists():
            self._pomoc.lift()
            return
        okno = self._pomoc = tk.Toplevel(self.root)
        okno.title(t("Jak to działa — {app}").format(app=APP_NAME))
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

        naglowek(t("Trzy kroki"), 0)
        for i, tekst in enumerate((
            t("Przeciągnij nagrania albo cały folder w pole po lewej. "
              "Audio i wideo, ile chcesz naraz."),
            t("Zaznacz, w jakiej postaci zapisać tekst: z czasem, sam tekst "
              "albo napisy do filmu."),
            t("Kliknij Transkrybuj. Gotowe pliki trafią do folderu wyników."),
        ), start=1):
            punkt(str(i), tekst, ACCENT_HOVER)

        naglowek(t("Co jeszcze potrafi"), 18)
        o_nagrywaniu = (
            t("Nagrywa spotkania. „Nagrywaj spotkanie” zbiera Twój mikrofon i dźwięk "
              "z głośników (np. Teams) do jednego pliku, a po zatrzymaniu od razu go "
              "transkrybuje. Najlepiej w słuchawkach."),
            t("Na karcie nagrywania wybierasz mikrofon, urządzenie, na którym słychać "
              "spotkanie, i obraz: sam dźwięk, okno spotkania albo cały ekran (do pliku "
              "MKV) — także w trakcie nagrania. „Sprawdź” nagra 5 sekund z mikrofonu "
              "i od razu je odtworzy, a „Wycisz” nagrywa samych rozmówców, gdy jesteś "
              "tylko słuchaczem. Słuchawki na zwykłym jacku grają przez „głośniki "
              "komputera” — Windows ich nie odróżnia."),
        ) if WYDANIE.nagrywanie else ()
        for tekst in o_nagrywaniu + (
            t("Podpisuje, kto mówi. Włącz „Rozpoznaj, kto co powiedział” "
              "i wpisz liczbę osób — po nagraniu nadasz im imiona."),
            t("Rozpoznaje mowę na Twoim komputerze — nagrania nigdy nie trafiają do internetu."),
            t("Plik z kolejki usuniesz krzyżykiem przy nim, klawiszem Delete "
              "albo prawym przyciskiem myszy."),
        ):
            punkt("•", tekst, FG_FAINT)

        przyciski = tk.Frame(tresc, bg=BG)
        przyciski.pack(fill="x", pady=(22, 0))
        ttk.Button(przyciski, text=t("Zamknij"), style="Accent.TButton",
                   command=okno.destroy).pack(side="right")
        okno.bind("<Escape>", lambda _e: okno.destroy())
        okno.update_idletasks()
        theme.ciemny_pasek_tytulu(okno)
        x = self.root.winfo_rootx() + (self.root.winfo_width() - okno.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + 80
        okno.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        okno.focus_set()

    def pokaz_licencje(self) -> None:
        """Wydanie firmowe: okno „Licencja” (czyj to program, kto może z niego
        korzystać, skąd aktualizacje). Publiczne: od razu licencje składników."""
        if not WYDANIE.firma:
            self._licencje_skladnikow()
            return
        if getattr(self, "_licencja", None) is not None and self._licencja.winfo_exists():
            self._licencja.lift()
            return
        firma = WYDANIE.firma
        okno = self._licencja = tk.Toplevel(self.root)
        okno.title(t("Licencja — {app}").format(app=APP_NAME))
        okno.configure(bg=BG)
        okno.resizable(False, False)
        okno.transient(self.root)
        tresc = tk.Frame(okno, bg=BG, padx=28, pady=22)
        tresc.pack(fill="both", expand=True)

        def naglowek(tekst: str, odstep: int) -> None:
            tk.Label(tresc, text=tekst, bg=BG, fg=FG, font=(theme.FONT_SEMI, 12),
                     anchor="w").pack(fill="x", pady=(odstep, 8))

        def akapit(tekst: str) -> tk.Frame:
            rzad = tk.Frame(tresc, bg=BG)
            rzad.pack(fill="x", pady=3)
            tk.Label(rzad, text="•", bg=BG, fg=FG_FAINT, width=2, anchor="nw",
                     font=(theme.FONT_SEMI, 10)).pack(side="left", anchor="n")
            tk.Label(rzad, text=tekst, bg=BG, fg=FG_DIM, font=(theme.FONT, 10),
                     justify="left", anchor="w", wraplength=440).pack(side="left", fill="x")
            return rzad

        # Sama nazwa firmy — „program firmowy” brzmiało autorowi źle.
        naglowek(firma, 0)
        tk.Label(tresc, text=t("{app}, wersja {w}").format(app=APP_NAME, w=__version__),
                 bg=BG, fg=FG_FAINT, font=(theme.FONT, 9), anchor="w").pack(
            fill="x", pady=(0, 8))
        for tekst in (
            t("{app} to program {firma}. Mogą z niego korzystać wszyscy pracownicy "
              "firmy."),
            t("Wolno go udostępniać i przekazywać dalej."),
            t("Aktualizacje przychodzą same z folderu firmowego, gdy komputer jest "
              "w sieci firmy. Na komputerze poza firmą program działa normalnie, "
              "ale nie dostaje automatycznych aktualizacji."),
            t("Nagrania i transkrypcje zostają na komputerze — program działa bez internetu."),
        ):
            akapit(tekst.format(app=APP_NAME, firma=firma))

        if WYDANIE.kontakt_email:
            naglowek(t("Błędy i pomysły"), 18)
            rzad = tk.Frame(tresc, bg=BG)
            rzad.pack(fill="x")
            tk.Label(rzad, text=t("Opiekun programu:"), bg=BG, fg=FG_DIM,
                     font=(theme.FONT, 10)).pack(side="left", padx=(0, 6))
            self._link(rzad, "✉ " + (WYDANIE.osoba or WYDANIE.kontakt_email),
                       self._napisz_do_autora).pack(side="left")

        naglowek(t("Składniki open source"), 18)
        tk.Label(tresc, text=t("Program korzysta z bibliotek open source (m.in. faster-whisper "
                               "i FFmpeg) na ich własnych licencjach."),
                 bg=BG, fg=FG_DIM, font=(theme.FONT, 10), justify="left", anchor="w",
                 wraplength=460).pack(fill="x")

        przyciski = tk.Frame(tresc, bg=BG)
        przyciski.pack(fill="x", pady=(22, 0))
        ttk.Button(przyciski, text=t("Zamknij"), style="Accent.TButton",
                   command=okno.destroy).pack(side="right")
        ttk.Button(przyciski, text=t("Licencje składników"),
                   command=self._licencje_skladnikow).pack(side="right", padx=(0, 8))
        okno.bind("<Escape>", lambda _e: okno.destroy())
        okno.update_idletasks()
        theme.ciemny_pasek_tytulu(okno)
        x = self.root.winfo_rootx() + (self.root.winfo_width() - okno.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + 80
        okno.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        okno.focus_set()

    def _licencje_skladnikow(self) -> None:
        """Otwiera spis licencji składników — LICENCJE.txt obok programu."""
        plik = project_root() / "LICENCJE.txt"
        if not plik.is_file():
            messagebox.showinfo(
                APP_TITLE,
                t("Nie znaleziono pliku {plik}. W wersji uruchamianej z kodu "
                  "tworzy go polecenie: python tools\\licencje.py").format(plik=plik.name),
                parent=self.root,
            )
            return
        try:
            os.startfile(str(plik))  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror(APP_TITLE, t("Nie udało się otworzyć {plik}:\n{blad}").format(plik=plik, blad=exc),
                                 parent=self.root)

    def show_diagnosis(self) -> None:
        self.status_var.set(t("Sprawdzam środowisko…"))
        self.root.update_idletasks()
        report = doctor.format_diagnosis(doctor.diagnose())
        self.status_var.set(t("Gotowy."))
        _show_report(self.root, t("Diagnostyka środowiska"), report)

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def on_close(self) -> None:
        if self._nagrywa():
            if not messagebox.askyesno(
                APP_TITLE, t("Trwa nagrywanie. Zatrzymać je, zapisać nagranie i zamknąć program?"),
                parent=self.root,
            ):
                return
            self.nagrywarka.stop()
            # Plik ma zostać domknięty (nagłówek z długością) zanim znikniemy.
            self.nagrywarka.czekaj(15)
        self._poczekaj_na_obraz()
        if self._busy():
            if not messagebox.askyesno(
                APP_TITLE, t("Program jeszcze pracuje. Na pewno zamknąć?")
            ):
                return
            self.cancel_flag.set()
        self._zamknij()

    def _zamknij(self) -> None:
        if getattr(self, "podsluch", None) is not None:
            self.podsluch.zatrzymaj(czekaj_s=1.0)
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
        return t("1 plik")
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return t("{n} pliki").format(n=n)
    return t("{n} plików").format(n=n)


def _opis_pozostalego(sekundy: float) -> str:
    """„pozostało ok. 6 min” — zaokrąglone, bo to szacunek, nie pomiar."""
    if sekundy < 45:
        return t("pozostało mniej niż minuta")
    minuty = round(sekundy / 60)
    if minuty < 60:
        return t("pozostało ok. {min} min").format(min=minuty)
    godziny, minuty = divmod(minuty, 60)
    return t("pozostało ok. {godz} godz. {min:02d} min").format(godz=godziny, min=minuty)


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
        text=t("Kopiuj do schowka"),
        command=lambda: (parent.clipboard_clear(), parent.clipboard_append(text)),
    ).pack(side="left")
    ttk.Button(row, text=t("Zamknij"), command=win.destroy).pack(side="right")
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
        text=t("Kopiuj do schowka"),
        command=lambda: (root.clipboard_clear(), root.clipboard_append(text)),
    ).pack(side="left")
    ttk.Button(row, text=t("Zamknij"), command=root.destroy).pack(side="right")
    theme.ciemny_pasek_tytulu(root)

    root.mainloop()
    return 0


def run(initial_files: Optional[List[Path]] = None) -> int:
    from .splash import Splash

    # Język przed pierwszym napisem: ustawienie programu, a bez niego język
    # Windows. Ekran powitalny jest jeden — napisy bierze z t().
    teksty.ustaw_z_ustawien(Settings.load().jezyk)
    root = make_root(ukryj=True)
    # Wydanie firmowe: na ekranie powitalnym sama firma, nie podpis autora
    # (ludzie czytali go jak zastrzeżenie praw).
    podpis = t("wersja {w}  ·  {wydawca}").format(
        w=__version__, wydawca=WYDANIE.firma or WYDANIE.wydawca)
    ekran = Splash(root, wersja=__version__, podpis=podpis)
    try:
        App(root, initial_files=initial_files, splash=ekran)
    finally:
        ekran.close()

    root.deiconify()
    root.lift()
    root.focus_force()
    root.mainloop()
    return 0
