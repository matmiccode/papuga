"""Zrzut ekranu okna programu — do sprawdzania wyglądu bez klikania.

Uruchamia okno z kodu z podmienionym LOCALAPPDATA (katalog obok pliku
wynikowego), bez pobierania modelu i bez pytania GitHuba o aktualizacje.
Kolejkę wypełnia udawanymi plikami.

    .venv\\Scripts\\python.exe tools\\zrzut_okna.py zrzut.png [tryb]

Tryby: pusty, kolejka, dziennik, baner, mowcy, pomoc.
Zmienne: WHISPER_AUTOMAT_WYDANIE=firma|papuga (domyślnie papuga),
ZRZUT_GEOM=980x660 (rozmiar okna).
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


def zrob():
    from PIL import ImageGrab

    root.update()
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    rect = wt.RECT()
    DWMWA_EXTENDED_FRAME_BOUNDS = 9  # prostokąt okna bez niewidocznego cienia
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                                               ctypes.byref(rect), ctypes.sizeof(rect))
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    root.update()
    if tryb == "pomoc":
        p = aplikacja._pomoc
        p.update()
        ramka = (p.winfo_rootx() - 10, p.winfo_rooty() - 40,
                 p.winfo_rootx() + p.winfo_width() + 10, p.winfo_rooty() + p.winfo_height() + 10)
    else:
        ramka = (rect.left, rect.top, rect.right, rect.bottom)
    obraz = ImageGrab.grab(bbox=ramka, all_screens=True)
    obraz.save(wyjscie)
    print("zapisano", wyjscie, obraz.size)
    root.destroy()


root.deiconify()
root.lift()
root.attributes("-topmost", True)
root.after(800, przygotuj)
root.after(2200, zrob)
root.mainloop()
