"""1.6.1 przez okno programu: fragmenty F8 i pole „Nagraj też obraz” (automatycznie).

Pytanie „nie widzę okna spotkania” dostaje odpowiedź „Tak” (cały ekran) bez
klikania. config.json projektu kopiowany przed próbą i przywracany po niej.
"""
import shutil
import tempfile
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SP = Path(__file__).resolve().parent
#: Wyniki prób i kopia config.json poza repozytorium.
TMP = Path(tempfile.gettempdir()) / "whisper-automat-proby"
TMP.mkdir(exist_ok=True)
WYNIKI = TMP / "gui-161"
shutil.rmtree(WYNIKI, ignore_errors=True)
WYNIKI.mkdir()
sys.path.insert(0, str(REPO / "src"))
konfig, kopia = REPO / "config.json", TMP / "config.json.kopia"
if konfig.is_file():
    shutil.copy2(konfig, kopia)
przebieg = []
pytania = []
try:
    from whisper_automat import app as A
    from whisper_automat.core import nagrywanie as N, update, wideo as W

    A.App._pobierz_przy_starcie = lambda self: None
    update.pora_sprawdzic = lambda *a, **k: False
    A.messagebox.askyesnocancel = lambda *a, **k: (pytania.append(a[1].splitlines()[0]), True)[1]
    W.okna = lambda: []  # żadnego okna spotkania — program ma zapytać
    root = A.make_root()
    aplikacja = A.App(root)
    aplikacja.next_to_source.set(False)
    aplikacja.outdir_var.set(str(WYNIKI))
    aplikacja.rec_auto_var.set(False)
    s = aplikacja.settings
    s.nagranie_okno_program = s.nagranie_okno_tytul = s.nagranie_ekran = ""
    # Lista „Obraz” na karcie (od 1.6.2): „Okno spotkania — samo znajdzie…”.
    obraz_var, obraz_lista = aplikacja.rec_listy["obraz"]
    obraz_var.set(obraz_lista["values"][1])
    aplikacja._po_wyborze_zrodla("obraz")

    def zapisz(co):
        b = aplikacja.rec_znacznik_btn
        przebieg.append(f"{co}: przycisk „{b.cget('text')}” ({b.cget('style') or 'TButton'}), "
                        f"obraz={obraz_var.get()[:30]} "
                        f"({obraz_lista.instate(['disabled']) and 'zablokowane' or 'aktywne'}), "
                        f"status: {aplikacja.status_var.get()[:70]}")

    zapisz("przed startem")
    kroki = [
        (1500, lambda: (aplikacja.rozpocznij_nagrywanie(), zapisz("start"))),
        (2500, lambda: (aplikacja.zaznacz_moment(), zapisz("F8 #1"))),
        (5500, lambda: (aplikacja.zaznacz_moment(), zapisz("F8 #2 po 3 s"))),
        (6500, lambda: (aplikacja.zaznacz_moment(), zapisz("F8 #3"))),
        (7000, lambda: (aplikacja.zaznacz_moment(), zapisz("F8 #4 po 0,5 s"))),
        (7500, lambda: (aplikacja.zaznacz_moment(), zapisz("F8 #5 (zostaje otwarty)"))),
        (9000, lambda: aplikacja.zatrzymaj_nagrywanie()),
    ]
    for kiedy, co in kroki:
        root.after(kiedy, co)

    def czekaj(n=[0]):
        n[0] += 1
        if (not aplikacja.nagrywarka.trwa and not aplikacja.nagrywarka.sklada_obraz) or n[0] > 80:
            zapisz("po zatrzymaniu")
            root.after(300, root.destroy)
        else:
            root.after(500, czekaj)

    root.after(9500, czekaj)
    root.mainloop()
finally:
    if kopia.is_file():
        shutil.copy2(kopia, konfig)
        kopia.unlink()

for linia in przebieg:
    print(linia)
print("pytania:", pytania)
pliki = sorted(WYNIKI.rglob("*"))
print("pliki:", [str(p.relative_to(WYNIKI)) for p in pliki])
flac = next(p for p in pliki if p.suffix == ".flac")
import av  # noqa: E402

with av.open(str(flac)) as c:
    tagi = {k.lower(): v for k, v in c.metadata.items()}
print("tag w FLAC:", tagi.get(N.TAG_ZNACZNIKI.lower()))
print("znaczniki:", N.wczytaj_znaczniki(flac, tagi))
mkv = [p for p in pliki if p.suffix == ".mkv"]
if mkv:
    import subprocess
    import json
    w = subprocess.run([W.ffmpeg().replace("ffmpeg.exe", "ffprobe.exe"), "-v", "error",
                        "-show_chapters", "-of", "json", str(mkv[0])], capture_output=True, text=True)
    print("rozdziały MKV:", [(round(float(r["start_time"]), 1), round(float(r["end_time"]), 1),
                             r["tags"]["title"]) for r in json.loads(w.stdout)["chapters"]])
