"""1.6.4: wyciszenie mikrofonu i zmiana urządzenia w trakcie nagrania (sam dźwięk).

Przebieg (s): 0 start z wyciszonym mikrofonem → 3 włącz → 7 wycisz →
9 inny mikrofon (z nazwy, w wyciszeniu — nie może się otworzyć) → 10 pauza →
11 włącz w pauzie → 12 wznów → 15 dźwięk spotkania na „domyślne” (przepięcie)
→ 18 stop. Potem lewy kanał co 0,5 s: wyciszony mikrofon to same zera, otwarty —
sygnał od chwili włączenia. Domyślne urządzenia Windows, nic nie piszczy.

Uwaga: mikrofon Intel Smart Sound (laptop autora) w cichym pokoju sam daje
cyfrowe zera po ~3 s od otwarcia (bramka szumów; zmierzone 2026-10-28, także
bez wyciszenia). Liczy się więc początek każdego odcinka: zera od chwili
„wycisz”, sygnał zaraz po „włącz”. Najlepiej mówić przez całą próbę.

    .venv\\Scripts\\python.exe tools\\proby\\proba_164.py [katalog]
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

import av
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from whisper_automat.core import nagrywanie as N  # noqa: E402

kat = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    Path(tempfile.gettempdir()) / "whisper-automat-proby" / "proba-164")
kat.mkdir(parents=True, exist_ok=True)
for p in kat.glob("*"):
    p.unlink()

t0 = time.perf_counter()
koniec = threading.Event()
wynik, zdarzenia = {}, []


def zapisz(nazwa):
    return lambda *a: zdarzenia.append((round(time.perf_counter() - t0, 2), nazwa, a))


zd = N.ZdarzeniaNagrywania(
    ostrzezenie=zapisz("ostrzezenie"), blad=lambda b: (wynik.update(blad=b), koniec.set()),
    zakonczono=lambda p, s: (wynik.update(plik=p, s=s), koniec.set()),
    pauza=zapisz("pauza"), przepiety=zapisz("przepiety"), mikrofon=zapisz("mikrofon"))
wejscia, _ = N.lista_urzadzen()
mik = next((u for u in wejscia if u.domyslne), wejscia[0])

n = N.Nagrywarka()
plik = n.start(kat, "", "", zd, wyciszony=True)
plan = [(3, "włącz", lambda: n.wycisz(False)), (7, "wycisz", lambda: n.wycisz(True)),
        (9, "inny mikrofon", lambda: n.przelacz_urzadzenie("mikrofon", mik.nazwa)),
        (10, "pauza", n.pauza), (11, "włącz w pauzie", lambda: n.wycisz(False)),
        (12, "wznów", n.wznow), (15, "system: domyślne", lambda: n.przelacz_urzadzenie("system", "")),
        (18, "stop", n.stop)]
for kiedy, opis, akcja in plan:
    while time.perf_counter() - t0 < kiedy:
        time.sleep(0.05)
    zdarzenia.append((round(time.perf_counter() - t0, 2), "AKCJA", opis,
                      f"(w pliku {n._teraz_w_pliku():.1f} s)"))
    akcja()
koniec.wait(20)
for z in zdarzenia:
    print(*z)
if "blad" in wynik:
    sys.exit(f"BŁĄD: {wynik['blad']}")

kontener = av.open(str(plik))
probki = np.concatenate([r.to_ndarray().reshape(-1, 2) if r.to_ndarray().ndim == 1 or
                         r.format.is_packed else r.to_ndarray().T
                         for r in kontener.decode(audio=0)])
kontener.close()
lewy = probki[:, 0]
blok = N.RATE // 2
print(f"\nplik {plik.name}: {len(lewy) / N.RATE:.1f} s (w pliku bez pauzy)")
print("lewy kanał co 0,5 s: Z = same zera (wyciszony), inaczej poziom w dBFS")
for i in range(0, len(lewy), blok):
    kawalek = lewy[i:i + blok].astype(np.float32) / 32768
    print(f"  {i / N.RATE:5.1f} s  " + ("Z" if not np.any(kawalek) else f"{N.rms_dbfs(kawalek):.0f} dB"))
