"""Dymek na prawdziwym ekranie: ukryty przed przechwytywaniem? gdzie stoi?

Pokazuje dymek na ~2 s, robi zrzut ekranu w jego miejscu (BitBlt — tak
łapią ekran programy do spotkań), potem to samo z dymkiem widocznym i bez
dymku. Ukryty działa, gdy „ukryty vs bez dymku” ≈ 0, a „widoczny vs bez
dymku” wyraźnie więcej. Ekran musi być odblokowany (na ekranie blokady
wszystko wychodzi 0).

    .venv\\Scripts\\python.exe tools\\proby\\proba_dymku.py
"""
import ctypes
import sys
import time
import tkinter as tk
from pathlib import Path

import tempfile

REPO = Path(__file__).resolve().parents[2]
OUT = Path(tempfile.gettempdir()) / "whisper-automat-proby" / "dymek"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(REPO / "src"))
from PIL import ImageChops, ImageGrab, ImageStat  # noqa: E402

from whisper_automat import dymek as D, theme  # noqa: E402

root = tk.Tk()
root.withdraw()
u32 = ctypes.windll.user32
print("na wierzchu przed:", hex(u32.GetForegroundWindow()))

# 1) Gdzie stanie i jak duży jest — zrzut tła PRZED pokazaniem.
d = D.Dymek(root, "Co nagrywać przy udostępnianiu?",
            "Program nie wie, który monitor udostępniasz. Wybierz go, a w nagraniu będzie to, "
            "co widzą uczestnicy.", [("Cały ekran — główny monitor (1920×1080)", lambda: None, True),
                                     ("Cały ekran — BenQ (1920×1080)", lambda: None, False),
                                     ("Nie teraz", lambda: None, False)], czas_s=0)
root.update()
x, y = d.okno.winfo_rootx(), d.okno.winfo_rooty()
w, h = d.okno.winfo_width(), d.okno.winfo_height()
print("ukryty przed nagraniem:", d.ukryty_przed_nagraniem)
print(f"położenie: {x},{y}  rozmiar: {w}×{h}")
ramka = (x, y, x + w, y + h)
time.sleep(0.5)
z_ukrytym = ImageGrab.grab(bbox=ramka, all_screens=True)
z_ukrytym.save(OUT / "ekran-z-ukrytym.png")

# 2) Ten sam dymek bez wyłączenia — powinien być na zrzucie.
hwnd = d._hwnd()
u32.SetWindowDisplayAffinity(hwnd, 0)
root.update()
time.sleep(0.5)
widoczny = ImageGrab.grab(bbox=ramka, all_screens=True)
widoczny.save(OUT / "ekran-z-widocznym.png")
d.zamknij()
root.update()
time.sleep(0.5)
bez = ImageGrab.grab(bbox=ramka, all_screens=True)


def roznica(a, b):
    return sum(ImageStat.Stat(ImageChops.difference(a.convert("RGB"), b.convert("RGB"))).mean) / 3


print(f"różnica: ukryty vs bez dymku {roznica(z_ukrytym, bez):.1f}, "
      f"widoczny vs bez dymku {roznica(widoczny, bez):.1f}")
print("na wierzchu po:", hex(u32.GetForegroundWindow()), "(ma być to samo co przed)")
print("zrzuty:", OUT)
root.destroy()
