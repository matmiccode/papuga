"""Okno „Dodaj mój głos do bazy” (wydanie firmowe — core/glosy.py).

Jedno okno robi wszystko: mówi, po co baza i że udział jest dobrowolny,
pokazuje stan (ile głosów, czy mój jest), nagrywa próbkę z mikrofonu
(ten sam tor, co nagrywanie spotkań — więc działa zestaw słuchawkowy
wybrany w „Zmień…”), liczy odcisk i zapisuje go do folderu firmowego.
Przycisk „Usuń mój głos” cofa udział.

Nagranie próbki idzie przez `nagrywanie.Nagrywarka` do katalogu
tymczasowego; z pliku FLAC bierzemy sam lewy kanał (mikrofon). Nic z tego
nie zostaje na dysku — po policzeniu odcisku pliki są kasowane.
"""

from __future__ import annotations

import queue
import shutil
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Optional

from . import theme
from .core import glosy, media, nagrywanie
from .core.config import APP_NAME, Settings, imie_uzytkownika
from .teksty import t
from .theme import BG, BG_INPUT, FG, FG_DIM, FG_FAINT, OK_COLOR
from .theme import ERR_COLOR as ERR

#: Ile sekund nagrywamy próbkę. Tekst do przeczytania zajmuje 20–25 s.
PROBKA_S = 25


def tekst_do_czytania() -> str:
    return t(
        "Dzień dobry, nazywam się … i pracuję w dziale … . Dziś omówimy plan na "
        "najbliższy kwartał: terminy dostaw, nowy cennik oraz przygotowania do targów. "
        "Proszę o uwagi do protokołu z ostatniego spotkania. Jeśli coś jest niejasne, "
        "pytajcie śmiało, chętnie wyjaśnię. Na koniec ustalimy, kto odpowiada za "
        "kolejne kroki i do kiedy."
    )


class OknoGlosu:
    def __init__(self, parent: tk.Misc, settings: Settings, log=None):
        self.settings = settings
        self.log = log or (lambda _m: None)
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._nagrywarka = nagrywanie.Nagrywarka()
        self._katalog_tmp: Optional[Path] = None
        self._odcisk: Optional[list] = None
        self._probka_s = 0.0
        self._zamkniete = False

        self.win = tk.Toplevel(parent)
        self.win.title(t("Dodaj mój głos do bazy"))
        self.win.configure(bg=BG)
        self.win.resizable(False, False)
        self.win.transient(parent)
        self._zbuduj()
        self.win.update_idletasks()
        theme.ciemny_pasek_tytulu(self.win)
        try:
            x = parent.winfo_rootx() + (parent.winfo_width() - self.win.winfo_reqwidth()) // 2
            y = parent.winfo_rooty() + 80
            self.win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        except tk.TclError:
            pass
        self.win.protocol("WM_DELETE_WINDOW", self._zamknij)
        self.win.bind("<Escape>", lambda _e: self._zamknij())
        self._pompuj()
        threading.Thread(target=self._wczytaj_stan, daemon=True).start()

    # -- budowa ------------------------------------------------------------

    def _zbuduj(self) -> None:
        r = tk.Frame(self.win, bg=BG, padx=24, pady=18)
        r.pack(fill="both", expand=True)
        r.columnconfigure(0, weight=1)

        tk.Label(r, text=t("Dodaj mój głos do bazy"), bg=BG, fg=FG,
                 font=(theme.FONT_SEMI, 13), anchor="w").grid(row=0, column=0, sticky="w")
        tk.Label(
            r, bg=BG, fg=FG_DIM, font=(theme.FONT, 9), justify="left", wraplength=560, anchor="w",
            text=t("Udział jest dobrowolny i można go w każdej chwili cofnąć przyciskiem "
                   "„Usuń mój głos”. Pomaga wszystkim użytkownikom {app} w firmie: gdy "
                   "weźmiesz udział w nagrywanym spotkaniu, Twoje imię pojawi się w ich "
                   "transkrypcji samo, zamiast „MÓWCA 2”.\n\n"
                   "Do bazy trafia odcisk głosu (zestaw liczb opisujących brzmienie, nie "
                   "nagranie), Twoje imię, login i data. Baza leży w folderze firmowym "
                   "obok instalatora i korzysta z niej tylko ten program. Głosów innych "
                   "osób program nigdy nie zapisuje sam.").format(app=APP_NAME),
        ).grid(row=1, column=0, sticky="w", pady=(6, 12))

        tk.Label(r, text=t("Imię i nazwisko w transkrypcjach"), bg=BG, fg=FG_DIM,
                 font=(theme.FONT, 9), anchor="w").grid(row=2, column=0, sticky="w")
        self.imie_var = tk.StringVar(value=self.settings.nagranie_imie.strip() or imie_uzytkownika())
        theme.pole(r, self.imie_var).grid(row=3, column=0, sticky="ew", ipady=5, pady=(2, 10))

        self.stan = tk.Label(r, text=t("Sprawdzam bazę…"), bg=BG, fg=FG_FAINT,
                             font=(theme.FONT, 9), anchor="w", justify="left", wraplength=560)
        self.stan.grid(row=4, column=0, sticky="w")

        tk.Label(r, text=t("Przeczytaj na głos, zwyczajnie, jak na spotkaniu (około 25 s):"),
                 bg=BG, fg=FG_DIM, font=(theme.FONT, 9), anchor="w").grid(
            row=5, column=0, sticky="w", pady=(14, 2))
        tk.Label(r, text=tekst_do_czytania(), bg=BG_INPUT, fg=FG, font=(theme.FONT, 10),
                 justify="left", wraplength=540, anchor="w", padx=12, pady=10).grid(
            row=6, column=0, sticky="ew")

        wiersz = tk.Frame(r, bg=BG)
        wiersz.grid(row=7, column=0, sticky="ew", pady=(12, 0))
        self.nagraj_btn = ttk.Button(wiersz, text=t("Nagraj próbkę ({s} s)").format(s=PROBKA_S),
                                     command=self._nagraj)
        self.nagraj_btn.pack(side="left")
        self.pasek = theme.PasekPostepu(wiersz, maximum=PROBKA_S * 10, grubosc=4, podloze=BG)
        self.pasek.pack(side="left", fill="x", expand=True, padx=(12, 0))

        self.status = tk.Label(r, text="", bg=BG, fg=FG_DIM, font=(theme.FONT, 9),
                               anchor="w", justify="left", wraplength=560)
        self.status.grid(row=8, column=0, sticky="w", pady=(8, 0))

        przyciski = tk.Frame(r, bg=BG)
        przyciski.grid(row=9, column=0, sticky="ew", pady=(16, 0))
        self.usun_btn = ttk.Button(przyciski, text=t("Usuń mój głos"), command=self._usun,
                                   state="disabled")
        self.usun_btn.pack(side="left")
        ttk.Button(przyciski, text=t("Zamknij"), command=self._zamknij).pack(side="right")
        self.zapisz_btn = ttk.Button(przyciski, text=t("Zapisz do bazy"), style="Accent.TButton",
                                     command=self._zapisz, state="disabled")
        self.zapisz_btn.pack(side="right", padx=(0, 8))

    # -- stan bazy ---------------------------------------------------------

    def _wczytaj_stan(self) -> None:
        try:
            baza = glosy.wczytaj()
            moj = glosy.moj_wpis()
            self._q.put(("stan", len(baza), moj, ""))
        except glosy.BladBazy as exc:
            self._q.put(("stan", 0, None, str(exc)))

    def _pokaz_stan(self, ile: int, moj, blad: str) -> None:
        if blad:
            self.stan.configure(text=t("Baza niedostępna: {blad}").format(blad=blad), fg=ERR)
            self.nagraj_btn.configure(state="disabled")
            return
        if moj is not None:
            data = (moj.data or "")[:10]
            tekst = t("W bazie firmowej: {n} głos(ów). Twój głos jest zapisany ({data}) jako "
                      "„{imie}” — nowa próbka go zastąpi.").format(n=ile, data=data, imie=moj.imie)
            self.usun_btn.configure(state="normal")
            if moj.imie and not self.settings.nagranie_imie.strip():
                self.imie_var.set(moj.imie)
        else:
            tekst = t("W bazie firmowej: {n} głos(ów). Twojego głosu jeszcze nie ma.").format(n=ile)
            self.usun_btn.configure(state="disabled")
        self.stan.configure(text=tekst, fg=FG_FAINT)

    # -- nagranie próbki ---------------------------------------------------

    def _nagraj(self) -> None:
        if self._nagrywarka.trwa:
            return
        self._odcisk = None
        self.zapisz_btn.configure(state="disabled")
        self._katalog_tmp = Path(tempfile.mkdtemp(prefix="wa_glos_"))
        put = self._q.put
        zd = nagrywanie.ZdarzeniaNagrywania(
            poziomy=lambda m, _s: put(("poziom", m)),
            czas=lambda s: put(("czas", s)),
            blad=lambda b: put(("blad", b)),
            zakonczono=lambda p, s: put(("nagrane", str(p), s)),
            anulowano=lambda: put(("blad", t("Nagranie próbki przerwane."))),
        )
        try:
            self._nagrywarka.start(self._katalog_tmp, self.settings.nagranie_mikrofon,
                                   self.settings.nagranie_glosniki, zd)
        except nagrywanie.BladNagrywania as exc:
            self._powiedz(str(exc), blad=True)
            return
        self.nagraj_btn.configure(state="disabled", text=t("Nagrywam…"))
        self.pasek["value"] = 0
        self._powiedz(t("Nagrywam. Czytaj tekst powyżej…"))
        self._cicho = True
        self.win.after(PROBKA_S * 1000, self._stop)

    def _stop(self) -> None:
        if self._nagrywarka.trwa:
            self._nagrywarka.stop()
            self._powiedz(t("Liczę odcisk głosu…"))

    def _po_nagraniu(self, plik: Path, sekundy: float) -> None:
        def praca() -> None:
            try:
                wav = media.extract_audio(plik, plik.with_suffix(".lewy.wav"), kanal="lewy")
                odcisk, mowa = glosy.odcisk_z_wav(wav)
                self._q.put(("odcisk", odcisk, mowa, ""))
            except (glosy.BladBazy, media.MediaError) as exc:
                self._q.put(("odcisk", None, 0.0, str(exc)))
            except Exception as exc:  # noqa: BLE001
                self._q.put(("odcisk", None, 0.0, f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=praca, daemon=True).start()

    def _po_odcisku(self, odcisk, mowa: float, blad: str) -> None:
        self._sprzatnij()
        self.nagraj_btn.configure(state="normal", text=t("Nagraj próbkę ({s} s)").format(s=PROBKA_S))
        self.pasek["value"] = 0
        if blad:
            self._powiedz(blad, blad=True)
            return
        self._odcisk, self._probka_s = odcisk, mowa
        self.zapisz_btn.configure(state="normal")
        self._powiedz(t("Próbka gotowa: {s:.0f} s mowy. Sprawdź imię i kliknij „Zapisz do bazy”.")
                      .format(s=mowa), ok=True)

    # -- zapis / usunięcie -------------------------------------------------

    def _zapisz(self) -> None:
        imie = self.imie_var.get().strip()
        if not imie:
            self._powiedz(t("Wpisz imię — tak podpisze Cię transkrypcja."), blad=True)
            return
        if self._odcisk is None:
            return
        try:
            cel = glosy.zapisz(glosy.nowy_wpis(imie, self._odcisk, self._probka_s))
        except glosy.BladBazy as exc:
            self._powiedz(str(exc), blad=True)
            return
        self.settings.nagranie_imie = imie
        self.settings.save()
        self.log(t("Głos zapisany w bazie firmowej jako „{imie}” ({plik}).").format(
            imie=imie, plik=cel.name))
        self._powiedz(t("Zapisano. Od teraz w transkrypcjach kolegów będziesz „{imie}”.")
                      .format(imie=imie), ok=True)
        self.zapisz_btn.configure(state="disabled")
        self._odcisk = None
        threading.Thread(target=self._wczytaj_stan, daemon=True).start()

    def _usun(self) -> None:
        if not messagebox.askyesno(APP_NAME, t("Usunąć Twój głos z bazy firmowej?"), parent=self.win):
            return
        try:
            glosy.usun_moj()
        except glosy.BladBazy as exc:
            self._powiedz(str(exc), blad=True)
            return
        self.log(t("Głos usunięty z bazy firmowej."))
        self._powiedz(t("Usunięto. Twój głos nie będzie już rozpoznawany."), ok=True)
        threading.Thread(target=self._wczytaj_stan, daemon=True).start()

    # -- pomocnicze --------------------------------------------------------

    def _powiedz(self, tekst: str, blad: bool = False, ok: bool = False) -> None:
        try:
            self.status.configure(text=tekst, fg=ERR if blad else (OK_COLOR if ok else FG_DIM))
        except tk.TclError:
            pass

    def _pompuj(self) -> None:
        if self._zamkniete:
            return
        try:
            while True:
                zd = self._q.get_nowait()
                rodzaj = zd[0]
                if rodzaj == "stan":
                    self._pokaz_stan(zd[1], zd[2], zd[3])
                elif rodzaj == "czas":
                    self.pasek["value"] = min(zd[1], PROBKA_S) * 10
                elif rodzaj == "poziom":
                    if zd[1] > -40.0:
                        self._cicho = False
                    elif self._cicho and zd[1] <= -60.0 and self.pasek["value"] > 50:
                        self._powiedz(t("Nie słychać Cię — sprawdź mikrofon w „Zmień…” pod "
                                        "przyciskiem nagrywania."), blad=True)
                elif rodzaj == "blad":
                    self._sprzatnij()
                    self.nagraj_btn.configure(state="normal",
                                              text=t("Nagraj próbkę ({s} s)").format(s=PROBKA_S))
                    self._powiedz(zd[1], blad=True)
                elif rodzaj == "nagrane":
                    self._po_nagraniu(Path(zd[1]), zd[2])
                elif rodzaj == "odcisk":
                    self._po_odcisku(zd[1], zd[2], zd[3])
        except queue.Empty:
            pass
        self.win.after(100, self._pompuj)

    def _sprzatnij(self) -> None:
        if self._katalog_tmp is not None:
            shutil.rmtree(self._katalog_tmp, ignore_errors=True)
            self._katalog_tmp = None

    def _zamknij(self) -> None:
        if self._nagrywarka.trwa:
            self._nagrywarka.anuluj()
            self._nagrywarka.czekaj(5)
        self._zamkniete = True
        self._sprzatnij()
        try:
            self.win.destroy()
        except tk.TclError:
            pass


def otworz(parent: tk.Misc, settings: Settings, log=None) -> OknoGlosu:
    return OknoGlosu(parent, settings, log)
