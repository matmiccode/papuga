"""Zgranie obrazu z dźwiękiem na prawdziwej Nagrywarce.

Okno testowe błyska co 2 s i jednocześnie piszczy (1 kHz, przez domyślne
głośniki → loopback). Nagranie ~14 s z pauzą 5.0–7.0 s. Potem: momenty
błysków w obrazie MKV vs początki pisków w prawym kanale FLAC.
"""
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from whisper_automat.core import nagrywanie as N  # noqa: E402
from whisper_automat.core import wideo as W  # noqa: E402

kat = Path(sys.argv[1])
kat.mkdir(parents=True, exist_ok=True)
for p in kat.glob("*"):
    p.unlink()

okno_proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name("okno_testowe.py")), "16", "blyski"],
                             stdout=subprocess.PIPE, text=True)
import atexit  # noqa: E402
atexit.register(okno_proc.kill)
okno = None
for _ in range(50):
    time.sleep(0.2)
    okno = next((o for o in W.okna() if o.tytul == "Okno testowe spotkania"), None)
    if okno:
        break
W.koder(W.ffmpeg())  # jak rozgrzanie w programie

koniec = threading.Event()
wynik = {}
zd = N.ZdarzeniaNagrywania(
    ostrzezenie=lambda s: print("OSTRZ:", s),
    blad=lambda s: (print("BLAD:", s), koniec.set()),
    zakonczono=lambda p, s: wynik.update(plik=p, s=s),
    obraz=lambda p, b: (wynik.update(mkv=p, blad=b), koniec.set()),
)
nag = N.Nagrywarka()
t_start = time.perf_counter()
nag.start(kat, "", "", zd, zrodlo_obrazu=okno)
time.sleep(3.0)
przed = {"t0": nag._zegar.t0, "przes": nag._zegar.przesuniecie}
time.sleep(5.0 - (time.perf_counter() - t_start))
t_pauzy_wall = time.perf_counter()
nag.pauza(); print("pauza przy stanie", nag.stan, round(time.perf_counter() - t_start, 2))
time.sleep(7.0 - (time.perf_counter() - t_start))
nag.wznow(); print("wznow przy stanie", nag.stan, round(time.perf_counter() - t_start, 2))
time.sleep(0.5)
stan_zegara = {"t0": nag._zegar.t0, "po": nag._zegar.przesuniecie, "pa": nag._teraz() - time.perf_counter()}
time.sleep(13.5 - (time.perf_counter() - t_start))
nag.stop()
koniec.wait(60)
print("nagranie:", wynik.get("plik"), round(wynik.get("s", 0), 2), "s; obraz:", wynik.get("mkv"), wynik.get("blad"))
mkv = wynik["mkv"]

import av  # noqa: E402

# Obraz: jasność każdej klatki, czas przejścia ciemno → jasno.
jasnosc = []
with av.open(str(mkv)) as c:
    for ramka in c.decode(video=0):
        y = ramka.to_ndarray(format="gray")
        jasnosc.append((float(ramka.time), float(y.mean())))
blyski_obraz = [t for (t0, j0), (t, j) in zip(jasnosc, jasnosc[1:]) if j0 < 100 <= j]

# Dźwięk: prawy kanał FLAC, energia w oknach 5 ms, start każdego pisku.
with av.open(str(wynik["plik"])) as c:
    ramki = [r.to_ndarray().reshape(-1, 2) for r in c.decode(audio=0)]
dane = np.concatenate(ramki, axis=0)  # s16 przeplatane → (próbki, 2)
prawy = dane[:, 1].astype(np.float32) / 32768.0
okno_n = 80
e = np.sqrt(np.mean(prawy[: len(prawy) // okno_n * okno_n].reshape(-1, okno_n) ** 2, axis=1))
prog = max(e.max() * 0.3, 1e-3)
piski = []
for i in range(1, len(e)):
    if e[i] >= prog > e[i - 1] and (not piski or i * okno_n / 16000 - piski[-1] > 0.5):
        piski.append(i * okno_n / 16000)

print("błyski w obrazie (s):", [round(x, 3) for x in blyski_obraz])
print("piski w dźwięku (s): ", [round(x, 3) for x in piski])
for b in blyski_obraz:
    najblizszy = min(piski, key=lambda p: abs(p - b)) if piski else None
    if najblizszy is not None:
        print(f"  błysk {b:7.3f}  pisk {najblizszy:7.3f}  obraz − dźwięk = {1000 * (b - najblizszy):+6.0f} ms")
print("pliki:", sorted(p.name for p in kat.iterdir()))

okno_proc.kill()
blyski_prawdziwe = [float(w.split()[1]) for w in okno_proc.stdout.read().splitlines() if w.startswith("blysk")]
print("zdarzenia (s pliku) | obraz − prawda | dźwięk − prawda")
for b in blyski_prawdziwe:
    if t_pauzy_wall - 0.1 < b < t_pauzy_wall + 2.2:
        continue
    przes = przed["przes"] if b < t_pauzy_wall else stan_zegara["po"]
    E = (b + stan_zegara["pa"] - stan_zegara["t0"] - przes)
    V = min(blyski_obraz, key=lambda x: abs(x - E)) if blyski_obraz else float("nan")
    A = min(piski, key=lambda x: abs(x - E)) if piski else float("nan")
    print(f"  {E:7.3f} | {1000*(V-E):+6.0f} ms | {1000*(A-E):+6.0f} ms")
