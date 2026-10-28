"""Obraz spotkania: wybrane okno (Zoom, Teams…) albo cały ekran do MKV obok dźwięku.

Cały ekran jest na udostępnianie ekranu: Zoom i Teams chowają wtedy okno
spotkania, więc nagranie okna by stało (klasa Ekran).

Skąd obraz: filtr `gfxcapture` w ffmpeg (od wersji 9 w paczce; Windows
Graphics Capture). Łapie okno po uchwycie, także zasłonięte innymi oknami
i rysowane przez kartę graficzną — `gdigrab` dawał tam czarne klatki. Okno
zminimalizowane nie daje klatek: obraz stoi, dopóki okno nie wróci.

Zgranie z dźwiękiem: ffmpeg liczy czas klatek od pierwszej (QPC, 100 ns),
a pierwsza klatka dociera do programu kilka milisekund po zrobieniu —
zmierzone 2026-10-21: przesunięcie stałe co do 1 ms przez całe nagranie.
Moment jej przyjścia na zegarze nagrania (`nagrywanie.Zegar`, też QPC)
wyznacza jej miejsce w pliku dźwiękowym. Oba tory liczą ten sam czas, więc
nie rozjeżdżają się nawet po godzinie. Pauza zamyka odcinek obrazu,
wznowienie otwiera nowy (w pliku dźwiękowym pauzy nie ma, w obrazie też
nie); uśpienie komputera tak samo.

Na końcu `zloz_odcinki` skleja odcinki demuxerem concat (`inpoint` ujemny
przesuwa odcinek o opóźnienie jego pierwszej klatki, `outpoint` ucina to,
co ffmpeg złapał już po pauzie, `-copyts` zostawia czasy jak są) i dokłada
dźwięk: miks obu kanałów do jednego (lewy to mikrofon, prawy rozmówcy —
w słuchawkach Ty byłbyś tylko w lewym uchu), AAC. Obraz idzie bez
przekodowania; godzina składa się w ~20 s.

Kodowanie: karta graficzna, jeśli jest (NVENC, Quick Sync, AMF), inaczej
libx264 na procesorze. 15 klatek na sekundę, klatka do 1920×1080, nowa
klatka tylko przy zmianie obrazu — rozmiar zależy od tego, co dzieje się
w oknie.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple, Union

from ..teksty import t

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

KLATKI_NA_S = 15
MAKS_SZER, MAKS_WYS = 1920, 1080
#: Gotowy plik: `Spotkanie ….mkv` obok `Spotkanie ….flac`.
ROZSZERZENIE = ".mkv"
#: Odcinek obrazu między pauzami, obok nagrania; znika po złożeniu.
ODCINEK = ".obraz-{n}.ts"
#: Lista odcinków dla ffmpeg (concat), obok nagrania do końca składania.
LISTA = ".obraz.ffconcat"
#: Minimalne wolne miejsce przy starcie nagrania z obrazem.
MIN_WOLNE_B = 3 << 30
#: Klatka kluczowa co tyle sekund — przewijanie w odtwarzaczu.
CO_ILE_KLUCZOWA_S = 10

#: Programy do spotkań: nazwa procesu (małymi literami) → nazwa dla ludzi.
PROGRAMY = {
    "zoom.exe": "Zoom",
    "ms-teams.exe": "Teams",
    "teams.exe": "Teams",
    "webex.exe": "Webex",
    "ciscocollabhost.exe": "Webex",
    "atmgr.exe": "Webex",
    "slack.exe": "Slack",
    "discord.exe": "Discord",
    "skype.exe": "Skype",
}
PRZEGLADARKI = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
#: Tytuł okna rozmowy, nie czatu ani listy kontaktów — po nim wybieramy
#: między kilkoma oknami jednego programu (Teams otwiera spotkanie osobno).
SPOTKANIE = re.compile(r"(?i)spotkani|meeting|połączeni|rozmow|\bcall\b|webinar|\bmeet\b|jitsi")
#: Okna powłoki Windows, których nie ma sensu nagrywać.
KLASY_POMIJANE = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

#: Kodery H.264 w kolejności prób i ich ustawienia jakości. Pierwszy, który
#: przejdzie próbę na tym komputerze, koduje wszystkie nagrania tej sesji.
#: Każdy bez opóźniania klatek (`delay`, `async_depth`, `zerolatency`):
#: odcinek kończony zabiciem procesu (patrz Odcinek.zatrzymaj) nie może
#: mieć ostatnich klatek uwięzionych w koderze.
KODERY: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("h264_nvenc", ("-preset", "p4", "-rc", "vbr", "-cq", "30", "-b:v", "0",
                    "-maxrate", "4M", "-bufsize", "8M", "-delay", "0")),
    ("h264_qsv", ("-preset", "veryfast", "-global_quality", "30", "-async_depth", "1")),
    ("h264_amf", ("-quality", "speed", "-rc", "cqp", "-qp_i", "28", "-qp_p", "30")),
    ("libx264", ("-preset", "veryfast", "-tune", "zerolatency", "-crf", "30",
                 "-maxrate", "4M", "-bufsize", "8M", "-threads", "4")),
)
#: Bez klatek B (odcinki da się uciąć co do klatki), klatka kluczowa co 10 s.
KODER_WSPOLNE = ("-bf", "0", "-force_key_frames", f"expr:gte(t,n_forced*{CO_ILE_KLUCZOWA_S})")


# ---------------------------------------------------------------------------
# Okna
# ---------------------------------------------------------------------------


@dataclass
class Okno:
    hwnd: int
    tytul: str
    #: Nazwa pliku procesu, np. "Zoom.exe".
    program: str
    szer: int = 0
    wys: int = 0
    zminimalizowane: bool = False


def nazwa_programu(program: str) -> str:
    """„Zoom.exe” → „Zoom”; programy spoza listy — nazwa pliku bez .exe."""
    return PROGRAMY.get(program.lower()) or Path(program).stem or program


def czy_spotkanie(okno: Okno) -> bool:
    """Program do spotkań albo przeglądarka z rozmową (Meet, Jitsi…)."""
    program = okno.program.lower()
    return program in PROGRAMY or (program in PRZEGLADARKI and bool(SPOTKANIE.search(okno.tytul)))


def opis_okna(okno: Okno) -> str:
    """Wpis na liście: „Zoom — Spotkanie Zoom  (Zoom.exe)”."""
    tytul = okno.tytul if len(okno.tytul) <= 60 else okno.tytul[:59] + "…"
    program = okno.program.lower()
    opis = f"{PROGRAMY[program]} — {tytul}" if program in PROGRAMY else tytul
    opis += f"  ({okno.program})"
    if okno.zminimalizowane:
        opis += "  · " + t("zminimalizowane")
    return opis


def _klucz_kolejnosci(okno: Okno):
    return (not czy_spotkanie(okno), not SPOTKANIE.search(okno.tytul), okno.zminimalizowane,
            okno.program.lower(), okno.tytul.lower())


def wybierz_okno(lista: Sequence[Okno], program: str, tytul: str = "") -> Optional[Okno]:
    """Okno zapamiętanego programu w tej chwili.

    Uchwyt okna jest inny przy każdym spotkaniu, więc w ustawieniach stoi
    program i tytuł z chwili wyboru. Pierwszeństwo: ten sam tytuł, potem
    tytuł wyglądający na rozmowę, okno niezminimalizowane, największe.
    """
    if not program:
        return None
    kandydaci = [o for o in lista if o.program.lower() == program.lower()]
    if not kandydaci:
        return None
    for o in kandydaci:
        if tytul and o.tytul == tytul and not o.zminimalizowane:
            return o
    return min(kandydaci, key=lambda o: (not SPOTKANIE.search(o.tytul), o.zminimalizowane,
                                         -(o.szer * o.wys)))


def okna() -> List[Okno]:
    """Widoczne okna innych programów; spotkania na początku listy."""
    if os.name != "nt":
        return []
    import ctypes
    import ctypes.wintypes as wt

    u32 = ctypes.windll.user32
    wynik: List[Okno] = []
    wlasny = os.getpid()
    PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def zbierz(hwnd, _lparam):
        try:
            okno = _opisz(int(hwnd or 0), wlasny)
        except Exception:
            okno = None
        if okno is not None:
            wynik.append(okno)
        return True

    poprzedni = _dpi_fizyczne()
    try:
        u32.EnumWindows(PROC(zbierz), 0)
    finally:
        _dpi_przywroc(poprzedni)
    return sorted(wynik, key=_klucz_kolejnosci)


def _opisz(hwnd: int, wlasny_pid: int) -> Optional[Okno]:
    import ctypes
    import ctypes.wintypes as wt

    u32 = ctypes.windll.user32
    if not hwnd or not u32.IsWindowVisible(wt.HWND(hwnd)):
        return None
    dlugosc = u32.GetWindowTextLengthW(wt.HWND(hwnd))
    if dlugosc <= 0:
        return None
    bufor = ctypes.create_unicode_buffer(dlugosc + 1)
    u32.GetWindowTextW(wt.HWND(hwnd), bufor, dlugosc + 1)
    tytul = bufor.value.strip()
    klasa = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(wt.HWND(hwnd), klasa, 256)
    if not tytul or klasa.value in KLASY_POMIJANE:
        return None
    pobierz_styl = getattr(u32, "GetWindowLongPtrW", u32.GetWindowLongW)
    pobierz_styl.restype = ctypes.c_ssize_t
    WS_EX_TOOLWINDOW = 0x80
    if pobierz_styl(wt.HWND(hwnd), -20) & WS_EX_TOOLWINDOW:
        return None
    # Okna „ukryte” przez DWM: aplikacje ze Sklepu w tle, inne pulpity.
    ukryte = wt.DWORD(0)
    DWMWA_CLOAKED = 14
    ctypes.windll.dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), DWMWA_CLOAKED,
                                               ctypes.byref(ukryte), ctypes.sizeof(ukryte))
    if ukryte.value:
        return None
    pid = wt.DWORD(0)
    u32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
    if pid.value == wlasny_pid:
        return None
    zminimalizowane = bool(u32.IsIconic(wt.HWND(hwnd)))
    if zminimalizowane:
        class WINDOWPLACEMENT(ctypes.Structure):
            _fields_ = [("length", wt.UINT), ("flags", wt.UINT), ("showCmd", wt.UINT),
                        ("ptMinPosition", wt.POINT), ("ptMaxPosition", wt.POINT),
                        ("rcNormalPosition", wt.RECT)]

        miejsce = WINDOWPLACEMENT()
        miejsce.length = ctypes.sizeof(miejsce)
        u32.GetWindowPlacement(wt.HWND(hwnd), ctypes.byref(miejsce))
        prost = miejsce.rcNormalPosition
    else:
        prost = wt.RECT()
        u32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(prost))
    szer, wys = prost.right - prost.left, prost.bottom - prost.top
    if szer < 200 or wys < 120:
        return None
    return Okno(hwnd=hwnd, tytul=tytul, program=_program(pid.value), szer=szer, wys=wys,
                zminimalizowane=zminimalizowane)


def _program(pid: int) -> str:
    import ctypes
    import ctypes.wintypes as wt

    k32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    uchwyt = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not uchwyt:
        return "?"
    try:
        bufor = ctypes.create_unicode_buffer(1024)
        rozmiar = wt.DWORD(1024)
        if not k32.QueryFullProcessImageNameW(uchwyt, 0, bufor, ctypes.byref(rozmiar)):
            return "?"
        return Path(bufor.value).name
    finally:
        k32.CloseHandle(uchwyt)


def okno_istnieje(hwnd: int) -> bool:
    if os.name != "nt":
        return False
    import ctypes
    import ctypes.wintypes as wt

    return bool(ctypes.windll.user32.IsWindow(wt.HWND(hwnd)))


def okno_schowane(hwnd: int) -> bool:
    """Zminimalizowane albo ukryte — tak robią Zoom i Teams przy udostępnianiu ekranu."""
    if os.name != "nt":
        return False
    import ctypes
    import ctypes.wintypes as wt

    u32 = ctypes.windll.user32
    return bool(u32.IsIconic(wt.HWND(hwnd))) or not u32.IsWindowVisible(wt.HWND(hwnd))


# ---------------------------------------------------------------------------
# Ekrany
# ---------------------------------------------------------------------------


@dataclass
class Ekran:
    """Cały monitor — na udostępnianie ekranu.

    Zoom i Teams chowają wtedy okno spotkania (zostaje pływający pasek), więc
    nagranie okna stoi; nagrywa się to, co widać na ekranie. Zmierzone
    2026-10-21: pierwsza klatka po ~0,3 s, potem klatki tylko przy zmianie
    obrazu, do 15 na sekundę — jak przy oknie.
    """

    hmonitor: int
    #: Nazwa urządzenia Windows ("\\\\.\\DISPLAY1") — po niej ekran wraca z ustawień.
    urzadzenie: str
    #: Kolejność w Windows, od 1.
    numer: int
    szer: int
    wys: int
    glowny: bool = False
    #: Nazwa monitora („BenQ G2420HDB”); pusta, gdy Windows zna tylko ogólną.
    nazwa: str = ""


#: Ogólne nazwy monitorów, które nic nie mówią (ekran laptopa zwykle taki ma).
_OGOLNA_NAZWA = re.compile(r"(?i)generic|ogólny|standard|pnp")


def ekrany() -> List[Ekran]:
    """Monitory w pikselach ekranu (nie skalowanych); główny pierwszy."""
    if os.name != "nt":
        return []
    import ctypes
    import ctypes.wintypes as wt

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                    ("dwFlags", wt.DWORD), ("szDevice", wt.WCHAR * 32)]

    class DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("DeviceName", wt.WCHAR * 32),
                    ("DeviceString", wt.WCHAR * 128), ("StateFlags", wt.DWORD),
                    ("DeviceID", wt.WCHAR * 128), ("DeviceKey", wt.WCHAR * 128)]

    u32 = ctypes.windll.user32
    wynik: List[Ekran] = []
    PROC = ctypes.WINFUNCTYPE(wt.BOOL, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_void_p)

    def zbierz(hmonitor, _dc, _prostokat, _lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        if hmonitor and u32.GetMonitorInfoW(ctypes.c_void_p(hmonitor), ctypes.byref(info)):
            monitor = DISPLAY_DEVICEW()
            monitor.cb = ctypes.sizeof(monitor)
            nazwa = ""
            if u32.EnumDisplayDevicesW(info.szDevice, 0, ctypes.byref(monitor), 0):
                # „BenQ G2420HDB (Analog)” → „BenQ G2420HDB”: w opisie i tak stoi rozmiar w nawiasie.
                nazwa = re.sub(r"\s*\(.*\)\s*$", "", monitor.DeviceString).strip()
            r = info.rcMonitor
            MONITORINFOF_PRIMARY = 1
            wynik.append(Ekran(
                hmonitor=int(hmonitor), urzadzenie=info.szDevice, numer=len(wynik) + 1,
                szer=r.right - r.left, wys=r.bottom - r.top,
                glowny=bool(info.dwFlags & MONITORINFOF_PRIMARY),
                nazwa="" if _OGOLNA_NAZWA.search(nazwa) else nazwa))
        return True

    poprzedni = _dpi_fizyczne()
    try:
        u32.EnumDisplayMonitors(None, None, PROC(zbierz), 0)
    finally:
        _dpi_przywroc(poprzedni)
    return sorted(wynik, key=lambda e: (not e.glowny, e.numer))


def opis_ekranu(ekran: Ekran, ile: int) -> str:
    """Wpis na liście: „Cały ekran — główny monitor (1920×1080)”."""
    rozmiar = f"{ekran.szer}×{ekran.wys}"
    if ile <= 1:
        return t("Cały ekran ({rozmiar})").format(rozmiar=rozmiar)
    if ekran.glowny:
        return t("Cały ekran — główny monitor ({rozmiar})").format(rozmiar=rozmiar)
    if ekran.nazwa:
        return t("Cały ekran — {nazwa} ({rozmiar})").format(nazwa=ekran.nazwa, rozmiar=rozmiar)
    return t("Cały ekran — monitor {n} ({rozmiar})").format(n=ekran.numer, rozmiar=rozmiar)


def wybierz_ekran(lista: Sequence[Ekran], urzadzenie: str) -> Optional[Ekran]:
    """Zapamiętany monitor, a gdy go nie ma (odłączony) — główny."""
    for ekran in lista:
        if ekran.urzadzenie == urzadzenie:
            return ekran
    return next((e for e in lista if e.glowny), lista[0] if lista else None)


def ekran_okna(hwnd: int, lista: Sequence[Ekran]) -> Optional[Ekran]:
    """Monitor, na którym stoi okno; zminimalizowane — ten, na którym stało."""
    if os.name != "nt":
        return None
    import ctypes
    import ctypes.wintypes as wt

    u32 = ctypes.windll.user32
    u32.MonitorFromWindow.restype = ctypes.c_void_p
    uchwyt = u32.MonitorFromWindow(wt.HWND(hwnd), 2)  # MONITOR_DEFAULTTONEAREST
    return next((e for e in lista if e.hmonitor == int(uchwyt or 0)), None)


#: Co nagrywa obraz spotkania: okno albo cały monitor.
ZrodloObrazu = Union[Okno, Ekran]


def cel(zrodlo: ZrodloObrazu) -> str:
    """Parametr gfxcapture: okno po uchwycie albo monitor po uchwycie."""
    if isinstance(zrodlo, Ekran):
        return f"hmonitor={zrodlo.hmonitor}"
    return f"hwnd={zrodlo.hwnd}"


def opis_zrodla(zrodlo: ZrodloObrazu) -> str:
    if isinstance(zrodlo, Ekran):
        return opis_ekranu(zrodlo, len(ekrany()))
    return opis_okna(zrodlo)


def zrodlo_istnieje(zrodlo: ZrodloObrazu) -> bool:
    if isinstance(zrodlo, Okno):
        return okno_istnieje(zrodlo.hwnd)
    if os.name != "nt":
        return False
    import ctypes
    import ctypes.wintypes as wt

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                    ("dwFlags", wt.DWORD)]

    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    return bool(ctypes.windll.user32.GetMonitorInfoW(ctypes.c_void_p(zrodlo.hmonitor),
                                                     ctypes.byref(info)))


def _dpi_fizyczne():
    """Wątek liczy w pikselach ekranu, nie w skalowanych (125 %, 150 %).

    Program nie deklaruje świadomości DPI, więc bez tego Windows podaje
    rozmiary pomniejszone o skalowanie — a ffmpeg łapie piksele ekranu.
    """
    import ctypes

    try:
        funkcja = ctypes.windll.user32.SetThreadDpiAwarenessContext
        funkcja.restype = ctypes.c_void_p
        funkcja.argtypes = [ctypes.c_void_p]
        return funkcja(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        return None


def _dpi_przywroc(poprzedni) -> None:
    if not poprzedni:
        return
    import ctypes

    try:
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(poprzedni))
    except (AttributeError, OSError):
        pass


def dopasuj(szer: int, wys: int, maks: Tuple[int, int] = (MAKS_SZER, MAKS_WYS)) -> Tuple[int, int]:
    """Rozmiar zmieszczony w `maks` z zachowaniem proporcji, wymiary parzyste."""
    if szer <= 0 or wys <= 0:
        return maks
    skala = min(1.0, maks[0] / szer, maks[1] / wys)
    return max(2, int(szer * skala) // 2 * 2), max(2, int(wys * skala) // 2 * 2)


def rozmiar_obrazu(hwnd: int) -> Tuple[int, int]:
    """Rozmiar klatki: ekran, na którym stoi okno, zmieszczony w 1920×1080.

    Nie rozmiar okna — okno zmaksymalizowane w trakcie dalej się mieści,
    ffmpeg skaluje je z zachowaniem proporcji (resize_mode=scale_aspect).
    """
    if os.name != "nt":
        return MAKS_SZER, MAKS_WYS
    import ctypes
    import ctypes.wintypes as wt

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                    ("dwFlags", wt.DWORD)]

    u32 = ctypes.windll.user32
    u32.MonitorFromWindow.restype = ctypes.c_void_p
    poprzedni = _dpi_fizyczne()
    try:
        ekran = u32.MonitorFromWindow(wt.HWND(hwnd), 2)  # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ekran or not u32.GetMonitorInfoW(ctypes.c_void_p(ekran), ctypes.byref(info)):
            return MAKS_SZER, MAKS_WYS
        r = info.rcMonitor
        return dopasuj(r.right - r.left, r.bottom - r.top)
    finally:
        _dpi_przywroc(poprzedni)


def rozmiar_zrodla(zrodlo: ZrodloObrazu) -> Tuple[int, int]:
    if isinstance(zrodlo, Ekran):
        return dopasuj(zrodlo.szer, zrodlo.wys)
    return rozmiar_obrazu(zrodlo.hwnd)


# ---------------------------------------------------------------------------
# Podgląd na karcie nagrywania
# ---------------------------------------------------------------------------

#: Największy podgląd źródła (piksele), proporcje zostają.
PODGLAD = (320, 180)


def podglad(zrodlo: ZrodloObrazu, maks: Tuple[int, int] = PODGLAD) -> Optional[bytes]:
    """Obraz okna albo ekranu w tej chwili, jako PPM (Tk czyta go bez Pillow).

    Nie przez gfxcapture, choć to on nagrywa: na nieruchomym oknie nie daje
    ani jednej klatki (pulapki.md), a sam start ffmpeg to ~1 s. PrintWindow
    z PW_RENDERFULLCONTENT rysuje okno także zasłonięte i rysowane przez
    kartę graficzną (Chromium, Teams) — zmierzone 2026-10-27: 20–100 ms.
    None — okno zminimalizowane (nagranie też wtedy stoi) albo zamknięte.
    """
    if os.name != "nt":
        return None
    import ctypes
    import ctypes.wintypes as wt

    import numpy as np

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                    ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                    ("dwFlags", wt.DWORD)]

    # Własne kopie bibliotek: typy wyników uchwytów nie przeciekają do reszty programu.
    u32, g32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
    for funkcja in (u32.GetDC, g32.CreateCompatibleDC, g32.CreateCompatibleBitmap,
                    g32.SelectObject):
        funkcja.restype = ctypes.c_void_p
    g32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    g32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    g32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    g32.BitBlt.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_void_p,
                                                                     ctypes.c_int, ctypes.c_int,
                                                                     wt.DWORD]
    g32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.UINT, wt.UINT,
                              ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
    g32.DeleteObject.argtypes = g32.DeleteDC.argtypes = [ctypes.c_void_p]
    u32.ReleaseDC.argtypes = [wt.HWND, ctypes.c_void_p]
    u32.PrintWindow.argtypes = [wt.HWND, ctypes.c_void_p, wt.UINT]

    okno = isinstance(zrodlo, Okno)
    poprzedni = _dpi_fizyczne()
    ekran_dc = dc = bmp = None
    try:
        r = wt.RECT()
        ramka = wt.RECT()
        if okno:
            hwnd = wt.HWND(zrodlo.hwnd)
            if not u32.IsWindow(hwnd) or u32.IsIconic(hwnd) or not u32.GetWindowRect(
                    hwnd, ctypes.byref(r)):
                return None
            # Bez niewidocznej ramki okna (cień DWM, ~8 px z każdej strony).
            DWMWA_EXTENDED_FRAME_BOUNDS = 9
            if ctypes.windll.dwmapi.DwmGetWindowAttribute(
                    hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(ramka),
                    ctypes.sizeof(ramka)) != 0:
                ramka = r
        else:
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(info)
            if not u32.GetMonitorInfoW(ctypes.c_void_p(zrodlo.hmonitor), ctypes.byref(info)):
                return None
            r = ramka = info.rcMonitor
        szer, wys = r.right - r.left, r.bottom - r.top
        if szer <= 0 or wys <= 0:
            return None
        ekran_dc = u32.GetDC(None)
        dc = g32.CreateCompatibleDC(ekran_dc)
        bmp = g32.CreateCompatibleBitmap(ekran_dc, szer, wys)
        if not (ekran_dc and dc and bmp):
            return None
        g32.SelectObject(dc, bmp)
        if okno:
            PW_RENDERFULLCONTENT = 2
            if not u32.PrintWindow(hwnd, dc, PW_RENDERFULLCONTENT):
                return None
        else:
            SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
            if not g32.BitBlt(dc, 0, 0, szer, wys, ekran_dc, r.left, r.top, SRCCOPY | CAPTUREBLT):
                return None
        naglowek = BITMAPINFOHEADER()
        naglowek.biSize = ctypes.sizeof(naglowek)
        # Wysokość ujemna: wiersze od góry, jak w PPM.
        naglowek.biWidth, naglowek.biHeight, naglowek.biPlanes, naglowek.biBitCount = (
            szer, -wys, 1, 32)
        bufor = ctypes.create_string_buffer(szer * wys * 4)
        if not g32.GetDIBits(dc, bmp, 0, wys, bufor, ctypes.byref(naglowek), 0):
            return None
    finally:
        if bmp:
            g32.DeleteObject(bmp)
        if dc:
            g32.DeleteDC(dc)
        if ekran_dc:
            u32.ReleaseDC(None, ekran_dc)
        _dpi_przywroc(poprzedni)
    bgra = np.frombuffer(bufor, dtype=np.uint8).reshape(wys, szer, 4)
    x0, y0 = max(0, ramka.left - r.left), max(0, ramka.top - r.top)
    x1, y1 = min(szer, ramka.right - r.left), min(wys, ramka.bottom - r.top)
    if x1 - x0 < 2 or y1 - y0 < 2:
        x0, y0, x1, y1 = 0, 0, szer, wys
    return ppm(zmniejsz(bgra[y0:y1, x0:x1, 2::-1], maks))


def zmniejsz(rgb, maks: Tuple[int, int]):
    """Obraz RGB (wys × szer × 3) zmieszczony w `maks`; uśrednianie bloków, nie co n-ty piksel
    — drobny tekst okna nie zamienia się w szum."""
    import numpy as np

    wys, szer = rgb.shape[:2]
    skala = min(1.0, maks[0] / szer, maks[1] / wys)
    nowa_szer, nowa_wys = max(1, round(szer * skala)), max(1, round(wys * skala))
    k = max(1, min(szer // nowa_szer, wys // nowa_wys))
    if k > 1:
        w2, s2 = wys // k * k, szer // k * k
        rgb = rgb[:w2, :s2].reshape(w2 // k, k, s2 // k, k, 3).mean(axis=(1, 3), dtype=np.float32)
    ys = (np.arange(nowa_wys) * rgb.shape[0] / nowa_wys).astype(int)
    xs = (np.arange(nowa_szer) * rgb.shape[1] / nowa_szer).astype(int)
    return np.ascontiguousarray(rgb[ys][:, xs], dtype=np.uint8)


def ppm(rgb) -> bytes:
    """Binarny PPM (P6) — tk.PhotoImage(data=…) czyta go bez dodatkowych bibliotek."""
    wys, szer = rgb.shape[:2]
    return b"P6 %d %d 255\n" % (szer, wys) + rgb.tobytes()


# ---------------------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------------------

_zadanie = None


def _przywiaz(proces: subprocess.Popen) -> None:
    """ffmpeg ginie razem z programem (obiekt zadania Windows).

    Bez tego po awarii programu ffmpeg nagrywałby okno dalej, aż ktoś
    zamknie spotkanie — godzinami, do pliku, którego nikt nie złoży.
    """
    global _zadanie
    if os.name != "nt":
        return
    import ctypes
    import ctypes.wintypes as wt

    k32 = ctypes.windll.kernel32
    k32.CreateJobObjectW.restype = wt.HANDLE
    try:
        with _blokada:
            if _zadanie is None:
                class IO(ctypes.Structure):
                    _fields_ = [(n, ctypes.c_ulonglong) for n in (
                        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

                class PODSTAWOWE(ctypes.Structure):
                    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                                ("PerJobUserTimeLimit", ctypes.c_longlong),
                                ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                                ("MaximumWorkingSetSize", ctypes.c_size_t),
                                ("ActiveProcessLimit", wt.DWORD), ("Affinity", ctypes.c_size_t),
                                ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]

                class ROZSZERZONE(ctypes.Structure):
                    _fields_ = [("BasicLimitInformation", PODSTAWOWE), ("IoInfo", IO),
                                ("ProcessMemoryLimit", ctypes.c_size_t),
                                ("JobMemoryLimit", ctypes.c_size_t),
                                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                                ("PeakJobMemoryUsed", ctypes.c_size_t)]

                zadanie = k32.CreateJobObjectW(None, None)
                if not zadanie:
                    return
                info = ROZSZERZONE()
                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                JobObjectExtendedLimitInformation = 9
                k32.SetInformationJobObject(wt.HANDLE(zadanie), JobObjectExtendedLimitInformation,
                                            ctypes.byref(info), ctypes.sizeof(info))
                _zadanie = zadanie
        k32.AssignProcessToJobObject(wt.HANDLE(_zadanie), wt.HANDLE(int(proces._handle)))
    except (AttributeError, OSError, ValueError):
        pass

_blokada = threading.Lock()
_ffmpeg: Optional[Tuple[Optional[str]]] = None
_koder: Optional[Tuple[str, Tuple[str, ...]]] = None


def _umie_okna(ffmpeg: str) -> bool:
    try:
        wynik = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostdin", "-h", "filter=gfxcapture"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    return wynik.returncode == 0 and "hwnd" in wynik.stdout


def ffmpeg() -> Optional[str]:
    """ffmpeg, który umie łapać okna (filtr gfxcapture, ffmpeg 9+), albo None."""
    global _ffmpeg
    with _blokada:
        if _ffmpeg is None:
            from .config import is_frozen, project_root
            from .probe import find_ffmpeg

            kandydaci = [find_ffmpeg()[0]]
            if not is_frozen():
                # Z kodu: ffmpeg z PATH bywa starszy (winget 8.0 nie ma
                # gfxcapture); ten z pamięci budowania idzie do paczki.
                kandydaci.append(str(project_root() / "build" / "cache" / "ffmpeg" / "ffmpeg.exe"))
            _ffmpeg = (next((k for k in kandydaci
                             if k and Path(k).is_file() and _umie_okna(k)), None),)
        return _ffmpeg[0]


def dostepne() -> bool:
    return os.name == "nt" and ffmpeg() is not None


def koder(sciezka_ffmpeg: str) -> Tuple[str, Tuple[str, ...]]:
    """Pierwszy koder z KODERY, który na tym komputerze naprawdę koduje.

    Próba to trzy czarne klatki — sterownik bez karty danej firmy odpada
    w ułamku sekundy. Wynik zapamiętany do końca sesji.
    """
    global _koder
    with _blokada:
        if _koder is not None:
            return _koder
    wybrany = KODERY[-1]
    for nazwa, opcje in KODERY[:-1]:
        try:
            wynik = subprocess.run(
                [sciezka_ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
                 "-f", "lavfi", "-i", f"color=c=black:s=640x360:r={KLATKI_NA_S}",
                 "-frames:v", "3", "-vf", "format=nv12", "-c:v", nazwa, *opcje,
                 *KODER_WSPOLNE, "-f", "null", "-"],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=20,
                creationflags=_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            continue
        if wynik.returncode == 0:
            wybrany = (nazwa, opcje)
            break
    with _blokada:
        _koder = wybrany
    return wybrany


def filtr_obrazu(cel_: str, szer: int, wys: int) -> str:
    """Graf filtrów: okno albo ekran (`cel`) → klatki NV12 [v], każda zgłoszona w dzienniku.

    Przyjście pierwszej linii `showinfo` to chwila startu obrazu na zegarze
    nagrania. Bez sum kontrolnych (`checksum=0`) linia nic nie kosztuje.
    Gałąź tylko dla pierwszej klatki (split → select → nullsink) zatrzymywała
    cały graf — plik miał sam nagłówek.
    """
    return (f"gfxcapture={cel_}:max_framerate={KLATKI_NA_S}:resize_mode=scale_aspect"
            f":width={szer}:height={wys},hwdownload,format=bgra,format=nv12,"
            "showinfo=checksum=0[v]")


# ---------------------------------------------------------------------------
# Nagrywanie
# ---------------------------------------------------------------------------


class Odcinek:
    """Jeden proces ffmpeg: obraz okna od startu albo wznowienia do pauzy.

    Czasy w sekundach pliku dźwiękowego: `poczatek` — od kiedy odcinek się
    liczy, `pierwsza` — gdzie wypada jego pierwsza klatka (znane po jej
    przyjściu), `koniec` — pauza, stop albo zamknięte okno.
    """

    def __init__(self, plik: Path, poczatek: float):
        self.plik = plik
        self.poczatek = poczatek
        self.pierwsza: Optional[float] = None
        self.koniec: Optional[float] = None
        #: perf_counter w chwili przyjścia pierwszej klatki (wątek czytający).
        self.t_pierwszej: Optional[float] = None
        self._proces: Optional[subprocess.Popen] = None
        self._ogon: deque = deque(maxlen=12)
        self._czytnik: Optional[threading.Thread] = None

    def uruchom(self, sciezka_ffmpeg: str, cel_: str, rozmiar: Tuple[int, int],
                koder_: Tuple[str, Tuple[str, ...]]) -> None:
        nazwa, opcje = koder_
        # MPEG-TS, każdy pakiet od razu na dysk: plik jest poprawny w każdej
        # chwili — po zabiciu procesu i po awarii programu też. MKV trzyma
        # ostatnią klatkę w pamięci do przyjścia następnej, a nieruchome okno
        # następnej nie daje: po zabiciu zostawał sam nagłówek. Bez
        # muxdelay czas pierwszej klatki to zero, jak w gfxcapture.
        polecenie = [
            sciezka_ffmpeg, "-hide_banner", "-nostats", "-loglevel", "info", "-y",
            "-filter_complex", filtr_obrazu(cel_, *rozmiar), "-map", "[v]",
            "-c:v", nazwa, *opcje, *KODER_WSPOLNE, "-fps_mode", "passthrough",
            "-flush_packets", "1", "-muxdelay", "0", "-muxpreload", "0",
            "-f", "mpegts", str(self.plik),
        ]
        # stdin — żeby zakończyć łagodnie („q”, plik domknięty z indeksem);
        # wszystkie strumienie przekierowane, bo wersja okienkowa nie ma
        # prawidłowych uchwytów (patrz speakers.py).
        self._proces = subprocess.Popen(
            polecenie, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
        _przywiaz(self._proces)
        self._czytnik = threading.Thread(target=self._czytaj, daemon=True, name="obraz-dziennik")
        self._czytnik.start()

    def _czytaj(self) -> None:
        assert self._proces is not None and self._proces.stderr is not None
        for wiersz in self._proces.stderr:
            if "showinfo" in wiersz and " n:" in wiersz:
                if self.t_pierwszej is None:
                    self.t_pierwszej = time.perf_counter()
                continue
            if wiersz.strip():
                self._ogon.append(wiersz.strip())

    @property
    def zyje(self) -> bool:
        return self._proces is not None and self._proces.poll() is None

    def blad(self) -> str:
        """Ostatnia sensowna linia dziennika ffmpeg — do ostrzeżenia."""
        for wiersz in reversed(self._ogon):
            if re.search(r"(?i)error|failed|invalid|cannot|unable|not ", wiersz):
                return wiersz[-200:]
        return self._ogon[-1][-200:] if self._ogon else ""

    def zatrzymaj(self, czekaj_s: float = 1.5) -> None:
        """„q” dla ffmpeg, a gdy nie zdąży — koniec procesu.

        Przy nieruchomym oknie gfxcapture czeka na następną klatkę i ffmpeg
        nie kończy się na „q” (sprawdzone: wisiał do 10 s i dłużej). Plik
        jest pisany na żywo, więc zabicie niczego nie psuje — brakuje tylko
        indeksu do przewijania, a ten dokłada składanie (zloz_odcinki).
        """
        proces = self._proces
        if proces is None or proces.poll() is not None:
            return
        # Wejścia nie zamykać przed końcem: ffmpeg zagląda do potoku
        # (PeekNamedPipe), a zamknięty potok to dla niego brak klawisza.
        try:
            assert proces.stdin is not None
            proces.stdin.write("q")
            proces.stdin.flush()
        except (OSError, ValueError):
            pass
        try:
            proces.wait(czekaj_s)
        except subprocess.TimeoutExpired:
            proces.kill()
            try:
                proces.wait(5)
            except subprocess.TimeoutExpired:
                pass
        try:
            proces.stdin.close()
        except (OSError, ValueError, AttributeError):
            pass

    def zabij(self) -> None:
        if self._proces is not None and self._proces.poll() is None:
            self._proces.kill()
            try:
                self._proces.wait(5)
            except subprocess.TimeoutExpired:
                pass

    def stan(self) -> dict:
        """Do znacznika niedokończonego nagrania (odzyskanie po awarii)."""
        return {"plik": self.plik.name, "poczatek": self.poczatek,
                "pierwsza": self.pierwsza, "koniec": self.koniec}


class ObrazSpotkania:
    """Obraz okna albo ekranu przez całe nagranie — odcinek na każdy kawałek
    między pauzami.

    Wszystkie metody woła wątek nagrywania (nagrywanie.Nagrywarka._praca).
    """

    def __init__(self, zrodlo: ZrodloObrazu, plik_audio: Path, sciezka_ffmpeg: str,
                 ostrzezenie: Callable[[str], None] = lambda _t: None,
                 zapisz_stan: Callable[[list], None] = lambda _s: None,
                 zmiana_okna: Callable[[bool], None] = lambda _s: None):
        self.zrodlo = zrodlo
        self.plik_audio = Path(plik_audio)
        self.odcinki: List[Odcinek] = []
        self._ffmpeg = sciezka_ffmpeg
        self._ostrzezenie = ostrzezenie
        self._zapisz_stan = zapisz_stan
        self._zmiana_okna = zmiana_okna
        # Rozmiar klatki z pierwszego źródła na całe nagranie: po przełączeniu
        # okno ↔ ekran odcinki dalej skleja się bez przekodowania.
        self._rozmiar = rozmiar_zrodla(zrodlo)
        self._zgloszone_zamkniecie = False
        #: Okno spotkania, którego pilnujemy także wtedy, gdy nagrywa się ekran:
        #: schowało się (udostępnianie) albo wróciło — `zmiana_okna(schowane)`.
        self.okno_spotkania: Optional[Okno] = zrodlo if isinstance(zrodlo, Okno) else None
        self._schowane = False

    def _biezacy(self) -> Optional[Odcinek]:
        if self.odcinki and self.odcinki[-1].koniec is None:
            return self.odcinki[-1]
        return None

    def _zapisz(self) -> None:
        try:
            self._zapisz_stan([o.stan() for o in self.odcinki])
        except OSError:
            pass

    def ruszaj(self, poczatek: float) -> None:
        """Nowy odcinek od `poczatek` (s pliku dźwiękowego): start albo wznowienie."""
        if self._biezacy() is not None:
            return
        if not zrodlo_istnieje(self.zrodlo):
            if not self._zgloszone_zamkniecie:
                self._zgloszone_zamkniecie = True
                self._ostrzezenie(t("Okna spotkania już nie ma — nagrywam sam dźwięk.")
                                  if isinstance(self.zrodlo, Okno) else
                                  t("Ekranu już nie ma (odłączony monitor) — nagrywam sam dźwięk."))
            return
        plik = self.plik_audio.with_name(
            f"{self.plik_audio.stem}{ODCINEK.format(n=len(self.odcinki) + 1)}")
        odcinek = Odcinek(plik, poczatek)
        try:
            odcinek.uruchom(self._ffmpeg, cel(self.zrodlo), self._rozmiar, koder(self._ffmpeg))
        except OSError as exc:
            self._ostrzezenie(t("Obraz spotkania nie ruszył: {blad}").format(blad=exc))
            return
        self.odcinki.append(odcinek)
        self._zapisz()

    def dogon(self, pozycja: Callable[[float], float]) -> None:
        """Wołane w każdej pętli nagrywania.

        `pozycja(perf_counter)` → sekundy pliku dźwiękowego. Pierwsza klatka
        dostaje miejsce w nagraniu; proces, który sam się skończył (okno
        zamknięte, błąd kodera), zamyka odcinek z ostrzeżeniem.
        """
        self._pilnuj_okna()
        odcinek = self._biezacy()
        if odcinek is None:
            return
        if odcinek.pierwsza is None and odcinek.t_pierwszej is not None:
            odcinek.pierwsza = max(odcinek.poczatek, pozycja(odcinek.t_pierwszej))
            self._zapisz()
        if not odcinek.zyje:
            odcinek.koniec = max(odcinek.poczatek, pozycja(time.perf_counter()))
            self._zapisz()
            if odcinek.pierwsza is None:
                self._ostrzezenie(t("Obraz spotkania nie ruszył: {blad}").format(
                    blad=odcinek.blad() or t("ffmpeg zakończył pracę")))
            elif not zrodlo_istnieje(self.zrodlo) and isinstance(self.zrodlo, Okno):
                self._zgloszone_zamkniecie = True
                self._ostrzezenie(t("Okno spotkania zamknięte — obraz zatrzymany, "
                                    "dźwięk nagrywa dalej."))
            else:
                self._ostrzezenie(t("Obraz spotkania przerwany: {blad}").format(
                    blad=odcinek.blad() or "?"))

    def _pilnuj_okna(self) -> None:
        """Zoom i Teams chowają okno spotkania, gdy udostępniasz ekran, i pokazują
        je po skończeniu — każda zmiana idzie raz do `zmiana_okna` (okno pyta
        o przełączenie obrazu). Okno zamknięte to koniec spotkania, nie pytanie."""
        okno = self.okno_spotkania
        if okno is None or not okno_istnieje(okno.hwnd):
            return
        schowane = okno_schowane(okno.hwnd)
        if schowane != self._schowane:
            self._schowane = schowane
            self._zmiana_okna(schowane)

    def przelacz(self, zrodlo: ZrodloObrazu, pozycja: float, w_pauzie: bool = False) -> None:
        """Inne źródło w trakcie nagrania: okno → cały ekran przy udostępnianiu
        i z powrotem.

        Nowy odcinek rusza od `pozycja`, zanim stary się zatrzyma (zatrzymanie
        nieruchomego obrazu trwa do 1,5 s), więc przerwa w obrazie to tylko
        start ffmpeg; klatki starego po `pozycja` ucina składanie. W pauzie
        tylko zmiana źródła — odcinek ruszy przy wznowieniu.
        """
        stary = self._biezacy()
        if stary is not None:
            stary.koniec = pozycja
        self.zrodlo = zrodlo
        if isinstance(zrodlo, Okno):
            self.okno_spotkania = zrodlo
            self._schowane = okno_schowane(zrodlo.hwnd)
        self._zgloszone_zamkniecie = False
        if not w_pauzie:
            self.ruszaj(pozycja)
        if stary is not None:
            stary.zatrzymaj()
        self._zapisz()

    def stoj(self, koniec: float) -> None:
        """Pauza, uśpienie albo stop: bieżący odcinek kończy się w `koniec`."""
        odcinek = self._biezacy()
        if odcinek is None:
            return
        odcinek.koniec = koniec
        odcinek.zatrzymaj()
        self._zapisz()

    def porzuc(self) -> None:
        """Nagranie odrzucone: procesy zabite, odcinki skasowane."""
        for odcinek in self.odcinki:
            odcinek.zabij()
            try:
                odcinek.plik.unlink()
            except OSError:
                pass
        self.odcinki = []

    def zloz(self, znaczniki: Sequence[dict] = ()) -> Optional[Path]:
        """Gotowy `Spotkanie ….mkv` obok nagrania dźwięku; None — nie było obrazu.

        Zaznaczone fragmenty stają się rozdziałami filmu.
        """
        odcinki = [o.stan() for o in self.odcinki]
        return zloz_odcinki(self._ffmpeg, self.plik_audio, odcinki, znaczniki=znaczniki)


def lista_concat(odcinki: Sequence[dict]) -> str:
    """Odcinki z klatkami → lista dla demuxera concat (ffconcat).

    Wpisy leżą jeden za drugim: pierwszy od zera, każdy następny od swojego
    `poczatek`. `inpoint` ujemny przesuwa klatki o opóźnienie pierwszej,
    `outpoint` ucina to, co ffmpeg złapał po pauzie, `duration` dosuwa
    następny wpis na jego miejsce w nagraniu.
    """
    wpisy = [o for o in odcinki if o.get("pierwsza") is not None]
    wiersze = ["ffconcat version 1.0"]
    for i, o in enumerate(wpisy):
        start = 0.0 if i == 0 else float(o["poczatek"])
        pierwsza = max(float(o["pierwsza"]), start)
        nazwa = str(o["plik"]).replace("'", "'\\''")
        wiersze.append(f"file '{nazwa}'")
        wiersze.append(f"inpoint {-(pierwsza - start):.6f}")
        if o.get("koniec") is not None:
            wiersze.append(f"outpoint {max(0.0, float(o['koniec']) - pierwsza):.6f}")
        if i + 1 < len(wpisy):
            wiersze.append(f"duration {max(0.0, float(wpisy[i + 1]['poczatek']) - start):.6f}")
    return "\n".join(wiersze) + "\n"


def sciezka_wyniku(plik_audio: Path) -> Path:
    """`Spotkanie ….flac` → `Spotkanie ….mkv`; przy kolizji ` (2)`."""
    plik_audio = Path(plik_audio)
    cel = plik_audio.with_suffix(ROZSZERZENIE)
    licznik = 2
    while cel.exists():
        cel = plik_audio.with_name(f"{plik_audio.stem} ({licznik}){ROZSZERZENIE}")
        licznik += 1
    return cel


def rozdzialy(znaczniki: Sequence[dict]) -> str:
    """Zaznaczone fragmenty → rozdziały MKV (ffmetadata). W odtwarzaczu
    (VLC: Odtwarzanie → Rozdział) skacze się od razu do ważnego miejsca."""
    wiersze = [";FFMETADATA1"]
    numer = 0
    for z in sorted(znaczniki, key=lambda z: float(z["czas"])):
        start = int(round(float(z["czas"]) * 1000))
        if z.get("koniec") is not None:
            numer += 1
            koniec, tytul = int(round(float(z["koniec"]) * 1000)), t("Fragment {n}").format(n=numer)
        else:
            koniec, tytul = start + 1000, t("Zaznaczony moment")
        tytul = re.sub(r"([=;#\\\n])", r"\\\1", tytul)
        wiersze += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start}", f"END={koniec}",
                    f"title={tytul}"]
    return "\n".join(wiersze) + "\n"


def zloz_odcinki(sciezka_ffmpeg: str, plik_audio: Path, odcinki: Sequence[dict],
                 cel: Optional[Path] = None, kanaly: int = 2,
                 znaczniki: Sequence[dict] = ()) -> Optional[Path]:
    """Skleja odcinki obrazu (leżące obok nagrania) z dźwiękiem w jeden MKV.

    Obraz bez przekodowania, dźwięk — miks kanałów do AAC, zaznaczone
    fragmenty jako rozdziały. Odcinki i lista znikają dopiero po udanym
    złożeniu; przy błędzie zostają (każdy da się obejrzeć osobno) i leci
    wyjątek z ostatnią linią dziennika ffmpeg.
    """
    plik_audio = Path(plik_audio)
    katalog = plik_audio.parent
    obecne = [o for o in odcinki
              if o.get("pierwsza") is not None and (katalog / o["plik"]).is_file()
              and (katalog / o["plik"]).stat().st_size > 0]
    pliki = [katalog / o["plik"] for o in odcinki]
    if not obecne:
        for odcinek in pliki:
            try:
                odcinek.unlink()
            except OSError:
                pass
        return None
    # Lista leży obok nagrania do końca składania: gdy zamknięcie programu
    # je przerwie, `dokoncz_skladanie` przy następnym starcie ma wszystko.
    lista = katalog / f"{plik_audio.stem}{LISTA}"
    with open(lista, "w", encoding="utf-8", newline="\n") as f:
        f.write(lista_concat(obecne))
    return _zloz(sciezka_ffmpeg, plik_audio, lista, pliki, cel, kanaly, znaczniki)


def _zloz(sciezka_ffmpeg: str, plik_audio: Path, lista: Path, pliki: Sequence[Path],
          cel: Optional[Path], kanaly: int, znaczniki: Sequence[dict] = ()) -> Path:
    cel = Path(cel) if cel else sciezka_wyniku(plik_audio)
    czesc = cel.with_name(cel.name + ".part")
    miks = "pan=mono|c0=c0+c1," if kanaly >= 2 else ""
    meta: Optional[Path] = None
    rozdzialy_opcje: List[str] = []
    if znaczniki:
        meta = lista.with_name(lista.name + ".rozdzialy.txt")
        with open(meta, "w", encoding="utf-8", newline="\n") as f:
            f.write(rozdzialy(znaczniki))
        rozdzialy_opcje = ["-i", str(meta), "-map_chapters", "2"]
    polecenie = [
        sciezka_ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-copyts",
        "-f", "concat", "-safe", "0", "-i", str(lista), "-i", str(plik_audio),
        *rozdzialy_opcje,
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
        "-af", f"{miks}alimiter=limit=0.97:level=0:latency=1", "-c:a", "aac", "-b:a", "64k",
        "-metadata", f"title={plik_audio.stem}", "-f", "matroska", str(czesc),
    ]
    proces = subprocess.Popen(polecenie, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, text=True, encoding="utf-8",
                              errors="replace", creationflags=_NO_WINDOW)
    _przywiaz(proces)
    _, bledy = proces.communicate()
    if meta is not None:
        try:
            meta.unlink()
        except OSError:
            pass
    if proces.returncode != 0 or not czesc.is_file():
        try:
            czesc.unlink()
        except OSError:
            pass
        ostatnia = (bledy or "").strip().splitlines()[-1:] or ["?"]
        raise RuntimeError(ostatnia[0][-300:])
    os.replace(czesc, cel)
    for odcinek in list(pliki) + [lista]:
        try:
            odcinek.unlink()
        except OSError:
            pass
    return cel


def dokoncz_skladanie(katalog: Path) -> List[Path]:
    """Obraz, którego składanie przerwało zamknięcie programu.

    Lista `Spotkanie ….obraz.ffconcat` została obok nagrania razem
    z odcinkami — składamy jeszcze raz. Nagrania w toku (znacznik
    `.nagrywanie` obok) i bez pliku dźwiękowego pomijamy.
    """
    katalog = Path(katalog)
    sciezka = ffmpeg() if katalog.is_dir() else None
    if not sciezka:
        return []
    wynik = []
    for lista in sorted(katalog.glob(f"*{LISTA}")):
        audio = lista.with_name(lista.name[: -len(LISTA)] + ".flac")
        # Znacznik nagrania w toku — ta sama nazwa co nagrywanie.ZNACZNIK.
        if not audio.is_file() or audio.with_name(audio.name + ".nagrywanie").exists():
            continue
        try:
            nazwy = re.findall(r"^file '(.*)'$", lista.read_text(encoding="utf-8"), re.M)
            pliki = [katalog / n.replace("'\\''", "'") for n in nazwy]
            from . import nagrywanie  # tu, nie na górze: nagrywanie importuje wideo

            tagi = _tagi_flac(audio)
            wynik.append(_zloz(sciezka, audio, lista, pliki, None, 2,
                               nagrywanie.wczytaj_znaczniki(audio, tagi)))
        except Exception:
            continue
    return wynik


def _tagi_flac(plik: Path) -> dict:
    """Tagi pliku (klucze małymi literami) przez PyAV; pusty słownik przy błędzie."""
    try:
        import av

        with av.open(str(plik)) as kontener:
            return {str(k).lower(): str(v) for k, v in (kontener.metadata or {}).items()}
    except Exception:
        return {}
