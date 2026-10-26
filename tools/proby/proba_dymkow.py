"""Dymki przy udostępnianiu przez okno programu (z kodu).

Nagranie okna testowego → minimalizacja (jak Zoom przy udostępnianiu) →
dymek „Okno spotkania się schowało” → klik pierwszego przycisku → obraz
z ekranu → przywrócenie okna → dymek „wróciło” → „Wróć do okna” → stop.
Sprawdza też, czy dymek nie zabrał fokusu oknu na pierwszym planie.
config.json projektu kopiowany przed próbą i przywracany po niej.
"""
import ctypes
import shutil
import tempfile
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SP = Path(__file__).resolve().parent
#: Wyniki prób i kopia config.json poza repozytorium.
TMP = Path(tempfile.gettempdir()) / "whisper-automat-proby"
TMP.mkdir(exist_ok=True)
WYNIKI = TMP / "gui-dymki"
shutil.rmtree(WYNIKI, ignore_errors=True)
WYNIKI.mkdir()
sys.path.insert(0, str(REPO / "src"))
u32 = ctypes.windll.user32

konfig, kopia = REPO / "config.json", TMP / "config.json.kopia"
if konfig.is_file():
    shutil.copy2(konfig, kopia)
okno_proc = subprocess.Popen([sys.executable, str(SP / "okno_testowe.py"), "60", "ruch"])
przebieg = []
try:
    from whisper_automat import app as A
    from whisper_automat.core import update, wideo as W

    A.App._pobierz_przy_starcie = lambda self: None
    update.pora_sprawdzic = lambda *a, **k: False
    root = A.make_root()
    aplikacja = A.App(root)
    aplikacja.next_to_source.set(False)
    aplikacja.outdir_var.set(str(WYNIKI))
    aplikacja.rec_auto_var.set(False)
    aplikacja.settings.nagranie_okno_program = "python.exe"
    aplikacja.settings.nagranie_okno_tytul = "Okno testowe spotkania"
    aplikacja.settings.nagranie_ekran = ""
    stan = {}

    def zapisz(co):
        przebieg.append(f"{co}: obraz={type(aplikacja._obraz_teraz).__name__}, "
                        f"przycisk={aplikacja.rec_obraz_btn.cget('text') if aplikacja.rec_obraz_btn.winfo_manager() else '-'}, "
                        f"dymek={'tak' if aplikacja._dymek is not None and aplikacja._dymek.otwarty else 'nie'}")

    def krok1():
        aplikacja.rozpocznij_nagrywanie()
        stan["okno"] = aplikacja._okno_nagrania
        zapisz("start")
        root.after(2500, krok2)

    def krok2():  # „udostępnianie”: okno spotkania się chowa, na wierzchu inne okno
        u32.ShowWindow(stan["okno"].hwnd, 6)
        root.after(2500, krok3)

    def krok3():
        zapisz("po schowaniu okna")
        d = aplikacja._dymek
        if d is not None and d.otwarty:
            stan["fokus_przed"] = u32.GetForegroundWindow()
            stan["hwnd_dymka"] = d._hwnd()
            stan["dymek_na_wierzchu"] = bool(u32.GetWindowLongW(stan["hwnd_dymka"], -20) & 0x8)
            przyciski = [w for w in d.okno.winfo_children()[0].winfo_children()[-1].winfo_children()]
            stan["przyciski"] = [p.cget("text") for p in przyciski]
            przyciski[0].invoke()
        root.after(2500, krok4)

    def krok4():
        zapisz("po kliknięciu")
        u32.ShowWindow(stan["okno"].hwnd, 9)  # koniec udostępniania
        root.after(2500, krok5)

    def krok5():
        zapisz("po powrocie okna")
        d = aplikacja._dymek
        if d is not None and d.otwarty:
            przyciski = d.okno.winfo_children()[0].winfo_children()[-1].winfo_children()
            stan["przyciski2"] = [p.cget("text") for p in przyciski]
            przyciski[0].invoke()
        root.after(2500, krok6)

    def krok6():
        zapisz("po drugim kliknięciu")
        aplikacja.zatrzymaj_nagrywanie()
        root.after(500, czekaj)

    def czekaj(n=[0]):
        n[0] += 1
        if not aplikacja.nagrywarka.trwa and not aplikacja.nagrywarka.sklada_obraz or n[0] > 60:
            zapisz("po zatrzymaniu")
            root.after(300, root.destroy)
        else:
            root.after(500, czekaj)

    root.after(1500, krok1)
    root.mainloop()
finally:
    okno_proc.kill()
    if kopia.is_file():
        shutil.copy2(kopia, konfig)
        kopia.unlink()

for linia in przebieg:
    print(linia)
print("przyciski dymka 1:", stan.get("przyciski"))
print("przyciski dymka 2:", stan.get("przyciski2"))
print("dymek ma WS_EX_TOPMOST:", stan.get("dymek_na_wierzchu"),
      "| fokus na dymku:", stan.get("fokus_przed") == stan.get("hwnd_dymka"))
print("pliki:", sorted(str(p.relative_to(WYNIKI)) for p in WYNIKI.rglob("*")))
