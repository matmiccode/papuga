"""Przełączanie okno → ekran → okno w trakcie nagrania: zgranie w każdym odcinku.

Okno testowe błyska i piszczy co 2 s. Odcinki z okna: jasność środka klatki;
odcinek z ekranu: jasność miejsca, w którym okno stoi na ekranie.
"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import av
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from whisper_automat.core import nagrywanie as N  # noqa: E402
from whisper_automat.core import wideo as W  # noqa: E402

kat = Path(sys.argv[1])
kat.mkdir(parents=True, exist_ok=True)
for p in kat.glob("*"):
    p.unlink()
okp = subprocess.Popen([sys.executable, str(Path(__file__).with_name("okno_testowe.py")), "18",
                        "blyski"], stdout=subprocess.PIPE, text=True)
import atexit  # noqa: E402
atexit.register(okp.kill)
for _ in range(50):
    time.sleep(0.2)
    okno = next((o for o in W.okna() if o.tytul == "Okno testowe spotkania"), None)
    if okno:
        break
ekran = W.ekran_okna(okno.hwnd, W.ekrany())
W.koder(W.ffmpeg())

koniec = threading.Event()
wynik, zdarzenia = {}, []
zostaw = {}
zd = N.ZdarzeniaNagrywania(
    ostrzezenie=lambda s: print("OSTRZ:", s),
    zakonczono=lambda p, s: wynik.update(plik=p, s=s),
    obraz=lambda p, b: (wynik.update(mkv=p, blad=b), koniec.set()),
    zrodlo_obrazu=lambda z: zdarzenia.append((round(time.perf_counter() - t0, 2), type(z).__name__)),
    okno_spotkania=lambda s: zdarzenia.append((round(time.perf_counter() - t0, 2), f"schowane={s}")),
)
nag = N.Nagrywarka()
stary_zloz = nag._zloz_obraz
nag._zloz_obraz = lambda obraz, zd_: (zostaw.update(stan=[o.stan() for o in obraz.odcinki]),
                                      stary_zloz(obraz, zd_))
t0 = time.perf_counter()
nag.start(kat, "", "", zd, zrodlo_obrazu=okno)
time.sleep(4.0 - (time.perf_counter() - t0))
nag.przelacz_obraz(ekran)
time.sleep(8.0 - (time.perf_counter() - t0))
nag.przelacz_obraz(okno)
time.sleep(12.5 - (time.perf_counter() - t0))
nag.stop()
koniec.wait(60)
print("zdarzenia:", zdarzenia)
print("odcinki:", json.dumps([{k: (round(v, 3) if isinstance(v, float) else v) for k, v in o.items()}
                              for o in zostaw["stan"]]))
print("nagranie", round(wynik["s"], 2), "s; obraz", wynik.get("mkv"), wynik.get("blad"))

granice = [o["poczatek"] for o in zostaw["stan"]]
klatki = []
with av.open(str(wynik["mkv"])) as c:
    for r in c.decode(video=0):
        y = r.to_ndarray(format="gray")
        czas = float(r.time)
        nr = max(i for i, g in enumerate(granice) if g <= czas + 1e-6)
        h, w = y.shape
        wycinek = y[140:480, 110:730] if nr == 1 else y[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
        klatki.append((czas, float(wycinek.mean()), nr))
blyski = [(t, nr) for (t0_, j0, _n0), (t, j, nr) in zip(klatki, klatki[1:]) if j0 < 100 <= j]
przerwy = [(round(a[0], 2), round(b[0], 2)) for a, b in zip(klatki, klatki[1:]) if b[0] - a[0] > 0.3]

with av.open(str(wynik["plik"])) as c:
    dane = np.concatenate([r.to_ndarray().reshape(-1, 2) for r in c.decode(audio=0)], axis=0)
prawy = dane[:, 1].astype(np.float32) / 32768.0
n = 80
e = np.sqrt(np.mean(prawy[: len(prawy) // n * n].reshape(-1, n) ** 2, axis=1))
prog = max(e.max() * 0.3, 1e-3)
piski = []
for i in range(1, len(e)):
    if e[i] >= prog > e[i - 1] and (not piski or i * n / 16000 - piski[-1] > 0.5):
        piski.append(i * n / 16000)
print("przerwy w obrazie > 0,3 s:", przerwy)
for t, nr in blyski:
    p = min(piski, key=lambda x: abs(x - t))
    print(f"  odcinek {nr + 1} ({'ekran' if nr == 1 else 'okno'}): błysk {t:6.3f}  pisk {p:6.3f}  "
          f"obraz − dźwięk = {1000 * (t - p):+5.0f} ms")
print(f"dźwięk: {len(prawy) / 16000:.2f} s przy {12.5:.1f} s od startu (bez pauzy) — bez dziur, jeśli różnica to tylko start torów")
