"""Etap E: porównanie modeli odcisków głosu na prawdziwych nagraniach.

Na każdym nagraniu: jedna transkrypcja (ta sama droga co program, ze
znacznikami słów), potem diaryzacja z obecnym modelem odcisków i z każdym
kandydatem z wydań sherpa-onnx. Kod produkcyjny się nie zmienia — model
podstawiamy tylko na czas próby (`diarization._sciezki`).

Wyniki w `%TEMP%\\whisper-automat-proby\\ab_odciski\\<nagranie>\\`:
  * `asr.txt` — transkrypcja bez mówców (strażnik ASR „przed”);
  * `<n>_<model>.txt` — TXT z podziałem na mówców, jak z programu;
  * `porownanie.txt` — każdy segment tekstu z mówcą według każdego modelu,
    numery kandydatów dopasowane do obecnego modelu; `≠` przy niezgodzie;
  * `statystyki.txt` / `statystyki.json` — liczba głosów, czas mowy, tury,
    wtrącenia poniżej 2 s, zgodność z obecnym, czas liczenia;
  * `do-odsluchu.txt` — dla ucha autora: tylko zdania, które obecny model
    i `DO_ODSLUCHU` przypisują różnym osobom, z próbkami „kto jest kim”.

Przerwane? Uruchom jeszcze raz: transkrypcja i gotowe modele są pomijane
(na policzonych nagraniach całość trwa kilka sekund).

    .venv\\Scripts\\python.exe tools\\ab_odciski.py "C:\\...\\a.mkv=3" "b.mkv=3"

Liczba po `=` to „Ile osób”, jak w oknie programu (0 = zgadnij).
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import pickle
import statistics
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from whisper_automat.core import diarization, media, probe, writers  # noqa: E402
from whisper_automat.core.config import Settings, models_dir  # noqa: E402
from whisper_automat.core.engine import Transcriber  # noqa: E402

WYNIKI = Path(tempfile.gettempdir()) / "whisper-automat-proby" / "ab_odciski"
MODELE_DIR = WYNIKI / "modele"

#: Obecny model na pierwszym miejscu — do niego dopasowujemy numery pozostałych.
OBECNY = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k"
KANDYDACI = [
    "3dspeaker_speech_eres2net_sv_en_voxceleb_16k",
    "wespeaker_en_voxceleb_resnet34_LM",
    "wespeaker_en_voxceleb_CAM++_LM",
    "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced",
]
#: Kandydat, którego autor porównuje uchem z obecnym (`do-odsluchu.txt`).
DO_ODSLUCHU = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced"
KROTKA_TURA_S = 2.0


def log(tekst: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {tekst}", flush=True)


def sha256(plik: Path) -> str:
    skrot = hashlib.sha256()
    with open(plik, "rb") as f:
        for porcja in iter(lambda: f.read(1 << 20), b""):
            skrot.update(porcja)
    return skrot.hexdigest()


def model_odciskow(nazwa: str) -> Path:
    if nazwa == OBECNY:
        return diarization._sciezki()[1]
    cel = MODELE_DIR / f"{nazwa}.onnx"
    if not cel.is_file():
        log(f"Pobieram {nazwa}…")
        url = (f"{diarization.BAZA}/speaker-recongition-models/"
               f"{urllib.parse.quote(nazwa)}.onnx")
        diarization._pobierz(url, cel)
    return cel


def transkrypcja(zrodlo: Path, katalog: Path, silnik: dict):
    """(wynik, audio 16 kHz) — z pamięci podręcznej, gdy już liczone."""
    audio = katalog / f"{zrodlo.stem}.16k.wav"
    zapis = katalog / "transkrypcja.pickle"
    if not audio.is_file():
        log(f"{zrodlo.name}: wyciągam dźwięk…")
        audio, _info, _tymczasowy = media.prepare_audio(zrodlo, workdir=katalog)
    if zapis.is_file():
        return pickle.loads(zapis.read_bytes()), audio

    if "transcriber" not in silnik:
        ustawienia = Settings()
        model, device, compute = ustawienia.resolve_model(probe.recommend(probe.probe()))
        tr = Transcriber(models_dir=models_dir())
        tr.load(model=model, device=device, compute_type=compute, log=log)
        silnik.update(transcriber=tr, ustawienia=ustawienia)
    ustawienia = silnik["ustawienia"]
    info = media.probe_media(zrodlo)
    log(f"{zrodlo.name}: transkrybuję {media.format_duration(info.duration)}…")
    wynik = silnik["transcriber"].transcribe(
        audio_path=audio, source_path=zrodlo, duration=info.duration,
        language=ustawienia.language or None, beam_size=ustawienia.beam_size,
        vad_filter=ustawienia.vad_filter, word_timestamps=True,
    )
    log(f"{zrodlo.name}: tekst gotowy w {wynik.elapsed:.0f} s "
        f"({wynik.model}, {wynik.device}, {wynik.compute_type})")
    writers.write_txt(wynik, katalog / "asr.txt")
    zapis.write_bytes(pickle.dumps(wynik))
    return wynik, audio


def diaryzuj(audio: Path, ilu: int, odciski: Path):
    segmentacja, _obecny = diarization._sciezki()
    oryginal = diarization._sciezki
    diarization._sciezki = lambda: (segmentacja, odciski)
    try:
        start = time.time()
        odcinki = diarization.rozpoznaj_mowcow(audio, ilu_mowcow=ilu, log=log)
        return odcinki, time.time() - start
    finally:
        diarization._sciezki = oryginal


def nakladanie(a, b) -> dict:
    """{(mówca a, mówca b): wspólny czas} dla dwóch list odcinków."""
    wynik: dict = {}
    for x in a:
        for y in b:
            wspolne = min(x.koniec, y.koniec) - max(x.start, y.start)
            if wspolne > 0:
                klucz = (x.mowca, y.mowca)
                wynik[klucz] = wynik.get(klucz, 0.0) + wspolne
    return wynik


def dopasuj(kandydat, wzor):
    """(mapowanie numerów kandydata na numery wzoru, zgodność 0–1)."""
    wspolne = nakladanie(kandydat, wzor)
    k = sorted({o.mowca for o in kandydat})
    w = sorted({o.mowca for o in wzor})
    # Brakujące miejsca dopełniamy „nikim” (None), żeby permutacja objęła wszystkich.
    miejsca = w + [None] * max(0, len(k) - len(w))
    najlepsze, mapowanie = -1.0, {}
    for perm in itertools.permutations(miejsca, len(k)):
        suma = sum(wspolne.get((a, b), 0.0) for a, b in zip(k, perm) if b is not None)
        if suma > najlepsze:
            najlepsze, mapowanie = suma, dict(zip(k, perm))
    nowy = max(w, default=-1) + 1
    for a in k:
        if mapowanie.get(a) is None:
            mapowanie[a], nowy = nowy, nowy + 1
    mowa_wzoru = sum(o.koniec - o.start for o in wzor)
    return mapowanie, (najlepsze / mowa_wzoru if mowa_wzoru else 0.0)


def statystyki(odcinki, czas_s: float, dlugosc: float) -> dict:
    mowcy = sorted({o.mowca for o in odcinki})
    tury = [o.koniec - o.start for o in odcinki]
    return {
        "glosy": len(mowcy),
        "czas_mowy_s": {diarization.etykieta(m): round(sum(
            o.koniec - o.start for o in odcinki if o.mowca == m), 1) for m in mowcy},
        "tury": {diarization.etykieta(m): sum(1 for o in odcinki if o.mowca == m)
                 for m in mowcy},
        "tur_razem": len(tury),
        "mediana_tury_s": round(statistics.median(tury), 2) if tury else 0.0,
        "tur_ponizej_2s": sum(1 for d in tury if d < KROTKA_TURA_S),
        "czas_liczenia_s": round(czas_s, 1),
        "x_nagrania": round(czas_s / dlugosc, 2) if dlugosc else 0.0,
    }


def zapisz_porownanie(katalog: Path, wynik, wyniki_modeli: dict) -> None:
    nazwy = list(wyniki_modeli)
    wiersze = ["Mówca według każdego modelu; numery dopasowane do obecnego (kol. 1).",
               "≠ = modele się nie zgadzają.", ""]
    for i, nazwa in enumerate(nazwy, 1):
        wiersze.append(f"  {i}: {nazwa}")
    wiersze.append("")
    for seg in wynik.segments:
        numery = [mowca(seg, *wyniki_modeli[nazwa]) for nazwa in nazwy]
        kolumny = " ".join(osoba(n) for n in numery)
        znak = "≠" if len(set(numery)) > 1 else " "
        wiersze.append(f"[{writers._clock(seg.start)}] {znak} {kolumny}  {seg.text.strip()}")
    (katalog / "porownanie.txt").write_text("\n".join(wiersze) + "\n", encoding="utf-8")


def mowca(seg, odcinki, mapowanie) -> int:
    """Mówca segmentu w numeracji obecnego modelu, -1 = nikt."""
    m = diarization._dominujacy(seg.start, seg.end, odcinki)
    return mapowanie.get(m, m) if m >= 0 else -1


def osoba(n: int) -> str:
    return f"{n + 1}" if n >= 0 else "?"


def zapisz_do_odsluchu(katalog: Path, zrodlo: Path, wynik, wyniki_modeli: dict) -> None:
    """Lista dla ucha autora: zdania, które obecny model i DO_ODSLUCHU
    przypisują różnym osobom, a przed nimi próbki „kto jest kim”."""
    nr = 2 + KANDYDACI.index(DO_ODSLUCHU)
    zdania = [(writers._clock(seg.start)[:8], mowca(seg, *wyniki_modeli[OBECNY]),
               mowca(seg, *wyniki_modeli[DO_ODSLUCHU]), seg.text.strip())
              for seg in wynik.segments]
    zgodne: dict = {}
    for czas, a, b, tekst in zdania:
        if a == b and a >= 0:
            zgodne.setdefault(a, []).append((czas, tekst))
    spory = [z for z in zdania if z[1] != z[2]]

    wiersze = [f"DO ODSŁUCHU — {zrodlo.name}",
               f"Zdań: {len(zdania)}, z tego obecny model i nr {nr} różnią się w {len(spory)}.",
               "",
               f"KROK 1. Kto jest kim — tu obecny i nr {nr} są zgodne. Posłuchaj i dopisz imię:",
               ""]
    for n in sorted(zgodne):
        wiersze.append(f"  Osoba {osoba(n)} = ____________")
        # dwie najdłuższe wypowiedzi, w kolejności czasu
        for czas, tekst in sorted(sorted(zgodne[n], key=lambda z: -len(z[1]))[:2]):
            wiersze.append(f"      [{czas}]  {tekst[:110]}")
        wiersze.append("")
    wiersze += ["KROK 2. Te miejsca puść w VLC (Ctrl+T = przejdź do czasu) i wpisz,",
                "kto mówił naprawdę (numer osoby z kroku 1, „?” gdy nie wiesz).",
                "",
                f"  czas        obecny  nr {nr}   naprawdę   tekst"]
    for czas, a, b, tekst in spory:
        wiersze.append(f"  [{czas}]    {osoba(a)}      {osoba(b)}     ____      {tekst[:90]}")
    wiersze += ["",
                f"KROK 3. Policz: obecny miał rację ___ razy, nr {nr} ___ razy.",
                "Wszystkie pozostałe zdania oba modele przypisały tak samo."]
    (katalog / "do-odsluchu.txt").write_text("\n".join(wiersze) + "\n", encoding="utf-8")


def zapisz_statystyki(katalog: Path, zrodlo: Path, ilu: int, staty: dict) -> None:
    (katalog / "statystyki.json").write_text(
        json.dumps(staty, ensure_ascii=False, indent=2), encoding="utf-8")
    wiersze = [f"{zrodlo.name} — ile osób: {ilu or 'zgadnij'}", ""]
    for nazwa, s in staty.items():
        wiersze.append(nazwa)
        wiersze.append(
            f"  głosy {s['glosy']}, tur {s['tur_razem']} (mediana {s['mediana_tury_s']} s, "
            f"poniżej 2 s: {s['tur_ponizej_2s']}), zgodność z obecnym "
            f"{s['zgodnosc_z_obecnym']:.0%}, liczenie {s['czas_liczenia_s']:.0f} s "
            f"({s['x_nagrania']}× nagrania)")
        for mowca, sek in s["czas_mowy_s"].items():
            wiersze.append(f"    {mowca}: {sek / 60:.1f} min, tur {s['tury'][mowca]}")
        wiersze.append(f"  SHA-256 {s['sha256']}")
        wiersze.append("")
    (katalog / "statystyki.txt").write_text("\n".join(wiersze), encoding="utf-8")


def nagranie(zrodlo: Path, ilu: int, silnik: dict) -> None:
    katalog = WYNIKI / zrodlo.stem
    katalog.mkdir(parents=True, exist_ok=True)
    wynik, audio = transkrypcja(zrodlo, katalog, silnik)
    dlugosc = wynik.duration

    staty_plik = katalog / "statystyki.json"
    staty = json.loads(staty_plik.read_text(encoding="utf-8")) if staty_plik.is_file() else {}
    wyniki_modeli: dict = {}
    wzor = None
    for n, nazwa in enumerate([OBECNY] + KANDYDACI, 1):
        odciski = model_odciskow(nazwa)
        zapis = katalog / f"{n}_{nazwa}.odcinki.pickle"
        if zapis.is_file():
            odcinki, czas_s = pickle.loads(zapis.read_bytes())
        else:
            log(f"{zrodlo.name}: diaryzacja {n}/{1 + len(KANDYDACI)} — {nazwa}…")
            odcinki, czas_s = diaryzuj(audio, ilu, odciski)
            zapis.write_bytes(pickle.dumps((odcinki, czas_s)))
        if wzor is None:
            wzor = odcinki
        mapowanie, zgodnosc = dopasuj(odcinki, wzor)
        wyniki_modeli[nazwa] = (odcinki, mapowanie)

        kopia = copy.deepcopy(wynik)
        kopia.segments = diarization.przypisz(kopia.segments, odcinki)
        kopia.speaker_turns = odcinki
        writers.write_txt(kopia, katalog / f"{n}_{nazwa}.txt")

        s = statystyki(odcinki, czas_s, dlugosc)
        s.update(zgodnosc_z_obecnym=round(zgodnosc, 3), sha256=sha256(odciski))
        staty[nazwa] = s
        zapisz_statystyki(katalog, zrodlo, ilu, staty)
        zapisz_porownanie(katalog, wynik, wyniki_modeli)
        log(f"{zrodlo.name}: {nazwa} — głosy {s['glosy']}, zgodność {zgodnosc:.0%}, "
            f"{czas_s:.0f} s")
    zapisz_do_odsluchu(katalog, zrodlo, wynik, wyniki_modeli)


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 2
    WYNIKI.mkdir(parents=True, exist_ok=True)
    silnik: dict = {}
    for arg in argv:
        sciezka, _, ilu = arg.rpartition("=") if "=" in arg else (arg, "", "0")
        nagranie(Path(sciezka), int(ilu), silnik)
    log(f"Gotowe. Wyniki: {WYNIKI}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
