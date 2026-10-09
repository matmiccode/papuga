"""Zrzut ekranu okna programu — do sprawdzania wyglądu bez klikania.

Uruchamia okno z kodu z podmienionym LOCALAPPDATA (katalog obok pliku
wynikowego), bez pobierania modelu i bez pytania GitHuba o aktualizacje.
Kolejkę wypełnia udawanymi plikami.

    .venv\\Scripts\\python.exe tools\\zrzut_okna.py zrzut.png [tryb]

Tryby: pusty, kolejka, dziennik, baner, mowcy, pomoc, nagrywanie (tylko firma).
Zmienne: WHISPER_AUTOMAT_WYDANIE=firma|papuga (domyślnie papuga),
ZRZUT_GEOM=980x660 (rozmiar okna).

Wydanie firmowe dostaje podpis autora z tools/podpis_firmy.local.txt (jeśli
plik jest) — tak jak w paczce, więc zrzut pokazuje prawdziwą stopkę.
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
from pathlib import Path

wyjscie = Path(sys.argv[1]).resolve()
tryb = sys.argv[2] if len(sys.argv) > 2 else "pusty"

os.environ["LOCALAPPDATA"] = str(wyjscie.parent / "zrzut-localappdata")
os.environ.setdefault("WHISPER_AUTOMAT_WYDANIE", "papuga")
PLIK_PODPISU = Path(__file__).resolve().parent / "podpis_firmy.local.txt"
if os.environ["WHISPER_AUTOMAT_WYDANIE"] == "firma" and PLIK_PODPISU.is_file():
    os.environ.setdefault("WHISPER_AUTOMAT_PODPIS_FIRMY",
                          PLIK_PODPISU.read_text(encoding="utf-8").strip())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whisper_automat import app as A  # noqa: E402
from whisper_automat.core import update  # noqa: E402

A.App._pobierz_przy_starcie = lambda self: None
update.pora_sprawdzic = lambda *a, **k: False

root = A.make_root()
aplikacja = A.App(root)
if os.environ.get("ZRZUT_GEOM"):
    root.geometry(os.environ["ZRZUT_GEOM"])


def przygotuj():
    if tryb in ("kolejka", "dziennik", "baner"):
        pliki = ["Wywiad z burmistrzem — część 1.mp4", "Spotkanie zespołu 2026-09-28.m4a",
                 "notatka głosowa.ogg", "Wykład 3 — termodynamika.mkv"]
        statusy = ["✓  gotowe, 11.8× szybciej", "przetwarzanie…", "oczekuje", "oczekuje"]
        for i, nazwa in enumerate(pliki):
            iid = aplikacja.tree.insert("", "end", text=nazwa, values=(
                ["12:41", "48:05", "0:52", "1:31:10"][i], statusy[i],
                "" if i == 1 else A.USUN))
            aplikacja.tree.item(iid, tags=(["ok", "run", "", ""][i],))
        aplikacja.files = [Path(n) for n in pliki]
        aplikacja.durations = {n: d for n, d in zip(pliki, [761, 2885, 52, 5470])}
        aplikacja._odswiez_pusta_kolejke()
        aplikacja.status_var.set("Transkrybuję: Spotkanie zespołu 2026-09-28.m4a")
        aplikacja.overall_var.set("plik 2 z 4 · pozostało ok. 6 min")
        aplikacja.file_progress["value"] = 420
        aplikacja.overall_progress["value"] = 350
        aplikacja.start_btn.configure(state="disabled")
        aplikacja.cancel_btn.configure(state="normal")
    if tryb == "dziennik":
        aplikacja._przelacz_dziennik()
    if tryb == "baner":
        aplikacja.baner_tekst.configure(text="Dostępna nowa wersja 1.2.0")
        aplikacja.baner.grid()
    if tryb == "pomoc":
        aplikacja.pokaz_pomoc()
    if tryb == "mowcy":
        aplikacja.diarize_var.set(True)
        aplikacja._toggle_diarize()
    if tryb == "nagrywanie":
        # Karta nagrywania w stanie „nagrywam” (tylko wydanie z nagrywaniem),
        # bez otwierania urządzeń: udawany czas i poziomy.
        aplikacja.rec_btn.configure(text="Zatrzymaj nagranie", style="Stop.TButton")
        aplikacja.rec_kropka.configure(fg=A.ERR_COLOR)
        aplikacja.rec_czas.configure(text="0:47:12", fg=A.FG)
        aplikacja.rec_paski["mikrofon"]["value"] = 620
        aplikacja.rec_paski["system"]["value"] = 410
        aplikacja.start_btn.configure(state="disabled")
        aplikacja.status_var.set("Nagrywam. Poinformuj uczestników, że spotkanie jest nagrywane.")


def przechwyc(hwnd):
    """Obraz okna przez PrintWindow — bez wyciągania go na wierzch.

    Zrzut z ekranu (ImageGrab) wymagał, żeby okno było odsłonięte, więc
    skrypt wpychał je na pierwszy plan i przeszkadzał w pracy; przy kilku
    monitorach potrafił też złapać tapetę. PrintWindow każe oknu narysować
    się do pamięci, choćby stało pod przeglądarką.
    """
    from PIL import Image

    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    calosc, widoczne = wt.RECT(), wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(calosc))
    DWMWA_EXTENDED_FRAME_BOUNDS = 9  # prostokąt okna bez niewidocznego cienia
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                                               ctypes.byref(widoczne), ctypes.sizeof(widoczne))
    szer, wys = calosc.right - calosc.left, calosc.bottom - calosc.top

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                    ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    hdc = u.GetWindowDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, szer, wys)
    stary = g.SelectObject(mdc, bmp)
    PW_RENDERFULLCONTENT = 2
    u.PrintWindow(hwnd, mdc, PW_RENDERFULLCONTENT)
    bmi = BITMAPINFOHEADER()
    bmi.biSize, bmi.biWidth, bmi.biHeight = ctypes.sizeof(bmi), szer, -wys
    bmi.biPlanes, bmi.biBitCount, bmi.biCompression = 1, 32, 0
    bufor = ctypes.create_string_buffer(szer * wys * 4)
    g.GetDIBits(mdc, bmp, 0, wys, bufor, ctypes.byref(bmi), 0)
    g.SelectObject(mdc, stary)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)
    obraz = Image.frombuffer("RGB", (szer, wys), bufor, "raw", "BGRX", 0, 1)
    return obraz.crop((widoczne.left - calosc.left, widoczne.top - calosc.top,
                       widoczne.right - calosc.left, widoczne.bottom - calosc.top))


def zrob():
    root.update()
    if tryb == "pomoc":
        p = aplikacja._pomoc
        p.update()
        hwnd = ctypes.windll.user32.GetParent(p.winfo_id())
    else:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    obraz = przechwyc(hwnd)
    obraz.save(wyjscie)
    print("zapisano", wyjscie, obraz.size)
    root.destroy()


root.deiconify()
root.after(800, przygotuj)
root.after(2200, zrob)
root.mainloop()
