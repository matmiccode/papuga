"""Sonda: co Teams, Zoom i Google Meet (przeglądarka) robią z oknami przy udostępnianiu ekranu.

Pytanie: czy program może sam wykryć, że ktoś udostępnia ekran albo okno,
i który monitor (które okno). Programy do spotkań rysują wtedy ramkę wokół
udostępnianego obszaru i pływający pasek — jeśli to są zwykłe okna, widać je
przez EnumWindows razem z prostokątem.

Uruchomienie (w trakcie prawdziwego spotkania, okienko prowadzi krok po kroku):
    .venv\\Scripts\\python.exe tools\\proby\\sonda_udostepniania.py

Co 0,5 s spisuje okna najwyższego poziomu (widoczne oraz wszystkie okna
programów do spotkań i przeglądarek, także ukryte) i zapisuje zmiany do
`dziennik.txt`. Przy każdym „Dalej” robi zdjęcie stanu, a na końcu
`raport.txt`: okna, które pojawiły się albo zmieniły względem stanu bez
udostępniania, z zaznaczeniem, czy pokrywają cały monitor albo inne okno.
Wyniki w %TEMP%\\whisper-automat-proby\\sonda-udostepniania (tytuły okien
zostają na tym komputerze).
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import tempfile
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from whisper_automat.core import wideo as W  # noqa: E402

TMP = Path(tempfile.gettempdir()) / "whisper-automat-proby" / "sonda-udostepniania"
TMP.mkdir(parents=True, exist_ok=True)
DZIENNIK = TMP / "dziennik.txt"
RAPORT = TMP / "raport.txt"

u32 = ctypes.windll.user32
dwm = ctypes.windll.dwmapi
pobierz_styl = getattr(u32, "GetWindowLongPtrW", u32.GetWindowLongW)
pobierz_styl.restype = ctypes.c_ssize_t
u32.GetWindow.restype = wt.HWND

SPOTKANIA = set(W.PROGRAMY) | W.PRZEGLADARKI | {"msedgewebview2.exe", "zoomshare.exe",
                                                 "cpthost.exe", "aomhost64.exe"}
WS_EX = {0x8: "TOPMOST", 0x20: "TRANSPARENT", 0x80: "TOOLWINDOW", 0x80000: "LAYERED",
         0x8000000: "NOACTIVATE", 0x200000: "NOREDIRECTIONBITMAP"}
#: Tolerancja przy porównaniu prostokątów (ramki bywają o kilka pikseli większe).
LUZ = 24

KROKI = [
    ("bez", "Dołącz do spotkania w Teams, Zoomie albo Google Meet\n"
            "(wystarczy spotkanie z samym sobą).\n"
            "Jeszcze NIC nie udostępniaj.\n\nKliknij „Dalej”."),
    ("ekran", "Udostępnij teraz CAŁY EKRAN.\n"
              "Jeśli masz dwa monitory — wybierz ten, na którym NIE ma okna spotkania.\n\n"
              "Poczekaj, aż udostępnianie ruszy, i kliknij „Dalej”."),
    ("ekran2", "Przestań udostępniać.\n"
               "Udostępnij teraz CAŁY EKRAN, ale DRUGI monitor —\n"
               "inny niż przed chwilą.\n\n"
               "Poczekaj, aż ruszy, i kliknij „Dalej” (jeden monitor — „Pomiń”)."),
    ("okno", "Przestań udostępniać ekran.\n"
             "Udostępnij teraz JEDNO OKNO (np. Notatnik albo Eksplorator),\n"
             "najlepiej takie, które jest pod innymi oknami.\n"
             "Po wybraniu odczekaj 5 s, nic nie klikając.\n\n"
             "Poczekaj, aż ruszy, i kliknij „Dalej” (albo „Pomiń”, jeśli się nie da)."),
    ("karta", "Przestań udostępniać okno.\n"
              "Tylko Google Meet: udostępnij teraz KARTĘ przeglądarki\n"
              "(np. inną otwartą stronę).\n\n"
              "Poczekaj, aż ruszy, i kliknij „Dalej” (w Teams i Zoomie — „Pomiń”)."),
    ("po", "Przestań udostępniać.\nOkno spotkania może zostać otwarte.\n\n"
           "Kliknij „Dalej” — sonda zapisze raport."),
]


@dataclass(frozen=True)
class Stan:
    pid: int
    program: str
    klasa: str
    tytul: str
    rect: Tuple[int, int, int, int]
    widoczne: bool
    zminimalizowane: bool
    ukryte_dwm: int
    exstyl: int
    wlasciciel: int
    #: SetWindowDisplayAffinity: 0 — zwykłe, 0x11 — wykluczone z przechwytywania
    #: (nagranie takiego okna wychodzi czarne).
    wda: int = 0

    def opis(self) -> str:
        l, t_, r, b = self.rect
        flagi = [n for bit, n in WS_EX.items() if self.exstyl & bit]
        if not self.widoczne:
            flagi.append("NIEWIDOCZNE")
        if self.zminimalizowane:
            flagi.append("ZMINIMALIZOWANE")
        if self.ukryte_dwm:
            flagi.append(f"CLOAKED={self.ukryte_dwm}")
        if self.wlasciciel:
            flagi.append(f"wlasciciel={self.wlasciciel:#x}")
        if self.wda:
            flagi.append(f"WYKLUCZONE_Z_NAGRYWANIA={self.wda:#x}")
        return (f"{self.program} [{self.klasa}] „{self.tytul[:70]}” "
                f"{r - l}×{b - t_} @({l},{t_}) {' '.join(flagi)}")


_programy: Dict[int, str] = {}


def program(pid: int) -> str:
    if pid not in _programy:
        _programy[pid] = W._program(pid)
    return _programy[pid]


def spis() -> Dict[int, Stan]:
    """Okna najwyższego poziomu: widoczne oraz wszystkie programów do spotkań."""
    wynik: Dict[int, Stan] = {}
    wlasny = os.getpid()
    PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def zbierz(hwnd, _):
        try:
            h = wt.HWND(hwnd)
            pid = wt.DWORD(0)
            u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value == wlasny:
                return True
            widoczne = bool(u32.IsWindowVisible(h))
            nazwa = program(pid.value)
            if not widoczne and nazwa.lower() not in SPOTKANIA:
                return True
            dl = u32.GetWindowTextLengthW(h)
            bufor = ctypes.create_unicode_buffer(max(dl, 0) + 1)
            u32.GetWindowTextW(h, bufor, dl + 1)
            klasa = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(h, klasa, 256)
            if klasa.value in W.KLASY_POMIJANE:
                return True
            r = wt.RECT()
            u32.GetWindowRect(h, ctypes.byref(r))
            ukryte = wt.DWORD(0)
            dwm.DwmGetWindowAttribute(h, 14, ctypes.byref(ukryte), ctypes.sizeof(ukryte))
            wl = u32.GetWindow(h, 4)  # GW_OWNER
            wda = wt.DWORD(0)
            u32.GetWindowDisplayAffinity(h, ctypes.byref(wda))
            wynik[int(hwnd)] = Stan(
                pid=pid.value, program=nazwa, klasa=klasa.value, tytul=bufor.value.strip(),
                rect=(r.left, r.top, r.right, r.bottom), widoczne=widoczne,
                zminimalizowane=bool(u32.IsIconic(h)), ukryte_dwm=ukryte.value,
                exstyl=int(pobierz_styl(h, -20)) & 0xFFFFFFFF, wlasciciel=int(wl or 0),
                wda=wda.value)
        except Exception as exc:  # sonda nie może paść na jednym dziwnym oknie
            print("okno", hwnd, exc)
        return True

    u32.EnumWindows(PROC(zbierz), 0)
    return wynik


def monitory() -> List[Tuple[str, Tuple[int, int, int, int]]]:
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                    ("dwFlags", wt.DWORD)]

    wynik = []
    for e in W.ekrany():
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        u32.GetMonitorInfoW(ctypes.c_void_p(e.hmonitor), ctypes.byref(info))
        r = info.rcMonitor
        wynik.append((f"monitor {e.numer}{' (główny)' if e.glowny else ''} {e.urzadzenie}",
                      (r.left, r.top, r.right, r.bottom)))
    return wynik


def blisko(a, b) -> bool:
    return all(abs(x - y) <= LUZ for x, y in zip(a, b))


def co_pokrywa(stan: Stan, ekr, okna: Dict[int, Stan]) -> str:
    """„= monitor 2” albo „≈ okno Notatnik”, gdy prostokąt pasuje."""
    if not stan.widoczne or stan.zminimalizowane:
        return ""
    trafienia = [f"= {nazwa}" for nazwa, r in ekr if blisko(stan.rect, r)]
    for hwnd, inne in okna.items():
        if inne is stan or not inne.widoczne or inne.zminimalizowane or inne.pid == stan.pid:
            continue
        w = inne.rect[2] - inne.rect[0]
        if w > 200 and blisko(stan.rect, inne.rect):
            trafienia.append(f"≈ okno {inne.program} „{inne.tytul[:40]}”")
    return "  <<< " + "; ".join(trafienia) if trafienia else ""


class Sonda:
    def __init__(self):
        self.zdjecia: Dict[str, Dict[int, Stan]] = {}
        self.koniec = threading.Event()
        self.krok = "start"
        self.dziennik = DZIENNIK.open("w", encoding="utf-8")
        self.watek = threading.Thread(target=self._obserwuj, daemon=True)
        #: Okno na wierzchu (GetForegroundWindow) przy każdej zmianie: (krok, czas, opis).
        self.wierzch: List[Tuple[str, str, str]] = []

    def _pisz(self, linia: str) -> None:
        self.dziennik.write(f"{time.strftime('%H:%M:%S')} [{self.krok}] {linia}\n")
        self.dziennik.flush()

    def _obserwuj(self) -> None:
        W._dpi_fizyczne()
        poprzedni: Dict[int, Stan] = {}
        wierzch_przed = None
        while not self.koniec.is_set():
            teraz = spis()
            h = int(u32.GetForegroundWindow() or 0)
            if h != wierzch_przed:
                wierzch_przed = h
                opis = teraz[h].opis() if h in teraz else ("sonda" if h else "brak")
                self.wierzch.append((self.krok, time.strftime("%H:%M:%S"), f"{h:#x} {opis}"))
                self._pisz(f"* na wierzchu: {h:#x} {opis}")
            for h in teraz.keys() - poprzedni.keys():
                self._pisz(f"+ {h:#x} {teraz[h].opis()}")
            for h in poprzedni.keys() - teraz.keys():
                self._pisz(f"- {h:#x} {poprzedni[h].program} [{poprzedni[h].klasa}]")
            for h in teraz.keys() & poprzedni.keys():
                if teraz[h] != poprzedni[h]:
                    self._pisz(f"~ {h:#x} {teraz[h].opis()}")
            poprzedni = teraz
            self.koniec.wait(0.5)

    def zdjecie(self, nazwa: str) -> None:
        """Suma trzech spisów w 1,5 s — okno, które mignie raz, też się liczy."""
        W._dpi_fizyczne()
        razem: Dict[int, Stan] = {}
        for _ in range(3):
            razem.update(spis())
            time.sleep(0.5)
        self.zdjecia[nazwa] = razem
        self._pisz(f"=== zdjęcie „{nazwa}”: {len(razem)} okien")

    def raport(self) -> str:
        W._dpi_fizyczne()
        ekr = monitory()
        linie = ["Sonda udostępniania ekranu — " + time.strftime("%Y-%m-%d %H:%M"), "",
                 "Monitory (piksele fizyczne):"]
        linie += [f"  {n}: {r}" for n, r in ekr]
        baza = self.zdjecia.get("bez", {})
        linie += ["", "Okna programów do spotkań BEZ udostępniania:"]
        linie += [f"  {h:#x} {s.opis()}" for h, s in baza.items()
                  if s.program.lower() in W.PROGRAMY or (s.widoczne and s.program.lower()
                                                         in SPOTKANIA)]
        for nazwa in ("ekran", "ekran2", "okno", "karta", "po"):
            zdj = self.zdjecia.get(nazwa)
            linie += ["", f"=== Krok „{nazwa}” względem „bez” ==="]
            if zdj is None:
                linie.append("  (pominięty)")
                continue
            nowe = [h for h in zdj if h not in baza]
            zmienione = [h for h in zdj if h in baza and zdj[h] != baza[h]]
            zniknely = [h for h in baza if h not in zdj]
            linie.append("Na wierzchu w tym kroku (kolejno):")
            linie += [f"  {czas} {opis}" for krok, czas, opis in self.wierzch
                      if krok == nazwa] or ["  (bez zmian)"]
            linie.append("Nowe okna:")
            linie += [f"  + {h:#x} {zdj[h].opis()}{co_pokrywa(zdj[h], ekr, zdj)}"
                      for h in nowe] or ["  (brak)"]
            linie.append("Zmienione okna (było → jest):")
            for h in zmienione:
                linie.append(f"  ~ {h:#x} {baza[h].opis()}")
                linie.append(f"        → {zdj[h].opis()}{co_pokrywa(zdj[h], ekr, zdj)}")
            if not zmienione:
                linie.append("  (brak)")
            linie.append("Zniknęły:")
            linie += [f"  - {h:#x} {baza[h].opis()}" for h in zniknely] or ["  (brak)"]
        return "\n".join(linie) + "\n"


def main() -> None:
    # Konsola Windows (cp1250) nie zna „≈” — bez tego raport padał na print.
    sys.stdout.reconfigure(errors="replace")
    sonda = Sonda()
    sonda.watek.start()
    root = tk.Tk()
    root.title("Sonda udostępniania ekranu")
    root.attributes("-topmost", True)
    root.geometry("+40+40")
    napis = tk.Label(root, justify="left", font=("Segoe UI", 11), wraplength=460, padx=16, pady=12)
    napis.pack(fill="both")
    stan = tk.Label(root, fg="#666", font=("Segoe UI", 9))
    stan.pack()
    przyciski = tk.Frame(root, pady=10)
    przyciski.pack()
    i = {"n": 0}

    def pokaz():
        nazwa, tekst = KROKI[i["n"]]
        sonda.krok = nazwa
        napis.config(text=f"Krok {i['n'] + 1} z {len(KROKI)}\n\n{tekst}")
        pomin.config(state="normal" if nazwa in ("ekran2", "okno", "karta") else "disabled")

    def dalej(zrob_zdjecie=True):
        nazwa = KROKI[i["n"]][0]
        dalej_b.config(state="disabled")
        stan.config(text="Zapisuję…")
        root.update()
        if zrob_zdjecie:
            sonda.zdjecie(nazwa)
        stan.config(text="")
        dalej_b.config(state="normal")
        i["n"] += 1
        if i["n"] < len(KROKI):
            pokaz()
            return
        sonda.koniec.set()
        tekst = sonda.raport()
        RAPORT.write_text(tekst, encoding="utf-8")
        print(tekst)
        print(f"Raport: {RAPORT}\nDziennik: {DZIENNIK}")
        napis.config(text=f"Gotowe. Raport zapisany:\n{RAPORT}\n\nMożesz zamknąć to okno.")
        dalej_b.config(text="Zamknij", command=root.destroy)
        pomin.pack_forget()

    dalej_b = tk.Button(przyciski, text="Dalej", width=12, command=dalej)
    dalej_b.pack(side="left", padx=6)
    pomin = tk.Button(przyciski, text="Pomiń", width=12, command=lambda: dalej(False))
    pomin.pack(side="left", padx=6)
    pokaz()
    root.mainloop()
    sonda.koniec.set()


if __name__ == "__main__":
    main()
