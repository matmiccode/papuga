"""1.6.4 przez okno programu: podgląd obrazu, paski przed nagraniem, wyciszenie,
zmiana okna i mikrofonu w trakcie nagrania.

Okno testowe (błyska i piszczy co 2 s) gra rolę spotkania. Program uważa się
za okno na wierzchu (podsłuch działa, choć próba idzie w tle). config.json
projektu kopiowany przed próbą i przywracany po niej. Wyniki do
%TEMP%\\whisper-automat-proby\\gui-164.
"""
import atexit
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.gettempdir()) / "whisper-automat-proby"
TMP.mkdir(exist_ok=True)
WYNIKI = TMP / "gui-164"
shutil.rmtree(WYNIKI, ignore_errors=True)
WYNIKI.mkdir()
sys.path.insert(0, str(REPO / "src"))
konfig, kopia = REPO / "config.json", TMP / "config.json.kopia"
if konfig.is_file():
    shutil.copy2(konfig, kopia)
okp = subprocess.Popen([sys.executable, str(Path(__file__).with_name("okno_testowe.py")), "40",
                        "blyski"], stdout=subprocess.DEVNULL)
atexit.register(okp.kill)
przebieg = []
try:
    from whisper_automat import app as A
    from whisper_automat.core import update, wideo as W

    A.App._pobierz_przy_starcie = lambda self: None
    A.App._na_wierzchu = lambda self: True
    update.pora_sprawdzic = lambda *a, **k: False
    for _ in range(50):
        time.sleep(0.2)
        okno = next((o for o in W.okna() if o.tytul == "Okno testowe spotkania"), None)
        if okno:
            break
    root = A.make_root()
    aplikacja = A.App(root)
    aplikacja.next_to_source.set(False)
    aplikacja.outdir_var.set(str(WYNIKI))
    aplikacja.rec_auto_var.set(False)
    s = aplikacja.settings
    przed = (s.nagranie_mikrofon, s.nagranie_glosniki)
    obraz_var, obraz_lista = aplikacja.rec_listy["obraz"]
    mik_var, mik_lista = aplikacja.rec_listy["mikrofon"]

    def wybierz(klucz, warunek):
        var, lista = aplikacja.rec_listy[klucz]
        aplikacja._rozwin_zrodlo(klucz)
        wpis = next(w for w in lista["values"] if warunek(w, aplikacja._mapy_zrodel[klucz][w]))
        var.set(wpis)
        aplikacja._po_wyborze_zrodla(klucz)
        return wpis

    def zapisz(co):
        p = aplikacja._podglad
        przebieg.append(
            f"{co}: obraz={obraz_var.get()[:34]!r} mik={mik_var.get()[:30]!r} "
            f"listy={[l.cget('state') for _v, l in aplikacja.rec_listy.values()]} "
            f"wycisz={aplikacja.rec_mik_btn.cget('text')!r} "
            f"dymek={'tak' if aplikacja.rec_podglad.winfo_ismapped() else 'nie'}"
            f"/klatka={'tak' if p['zdjecie'] is not None else 'nie'} "
            f"paski={[int(x['value']) for x in aplikacja.rec_paski.values()]} "
            f"podsluch={'tak' if aplikacja.podsluch and aplikacja.podsluch.dziala else 'nie'} "
            f"| {aplikacja.status_var.get()[:60]}")

    kroki = [
        (500, lambda: (wybierz("obraz", lambda w, z: isinstance(z, W.Okno)
                                and z.tytul == "Okno testowe spotkania"), zapisz("okno wybrane"))),
        (4000, lambda: zapisz("po 3,5 s (podsłuch, klatka)")),
        (4100, lambda: (aplikacja.przelacz_wyciszenie(), zapisz("wyciszony przed startem"))),
        (4500, lambda: (aplikacja.rozpocznij_nagrywanie(), zapisz("start"))),
        (8000, lambda: zapisz("nagrywa")),
        (8100, lambda: wybierz("obraz", lambda w, z: isinstance(z, W.Ekran))),
        (10000, lambda: zapisz("po wyborze ekranu")),
        (10100, lambda: wybierz("obraz", lambda w, z: isinstance(z, W.Okno)
                                 and z.tytul == "Okno testowe spotkania")),
        (12000, lambda: zapisz("po powrocie do okna")),
        (12100, lambda: (aplikacja.przelacz_wyciszenie(), zapisz("włączony w trakcie"))),
        (12200, lambda: wybierz("mikrofon", lambda w, z: bool(z) and z != przed[0])),
        (15000, lambda: zapisz("po zmianie mikrofonu")),
        (15100, lambda: aplikacja.zatrzymaj_nagrywanie()),
    ]
    for kiedy, co in kroki:
        root.after(kiedy, co)
    #: Najwyższe paski (mikrofon, dźwięk spotkania) przed nagraniem i w trakcie.
    szczyty = {"przed": [0, 0], "w trakcie": [0, 0]}

    def probkuj():
        faza = "w trakcie" if aplikacja._nagrywa() else "przed"
        for i, pasek in enumerate(aplikacja.rec_paski.values()):
            szczyty[faza][i] = max(szczyty[faza][i], int(pasek["value"]))
        root.after(100, probkuj)

    root.after(100, probkuj)

    def czekaj(n=[0]):
        n[0] += 1
        if (not aplikacja.nagrywarka.trwa and not aplikacja.nagrywarka.sklada_obraz) or n[0] > 80:
            zapisz("po zatrzymaniu")
            przebieg.append(f"ustawienia przed {przed}, po {(s.nagranie_mikrofon, s.nagranie_glosniki)}")
            przebieg.append(f"najwyższe paski [mikrofon, spotkanie] (0–1000): {szczyty}")
            przebieg.append("DZIENNIK:\n" + aplikacja.log_text.get("1.0", "end").strip())
            root.after(300, aplikacja._zamknij)
        else:
            root.after(500, czekaj)

    root.after(15600, czekaj)
    root.mainloop()
finally:
    if kopia.is_file():
        shutil.copy2(kopia, konfig)
        kopia.unlink()

for linia in przebieg:
    print(linia)
print("pliki:", sorted(p.name for p in WYNIKI.rglob("*")))
