"""Zapis transkrypcji do plików: TXT, SRT, VTT, JSON.

Zaznaczone fragmenty i momenty (`result.znaczniki`, z nagrania — przycisk
„Zaznacz”/F8: początek i koniec) trafiają do obu TXT w miejscu, gdzie padły,
i do JSON jako `markers`. Napisy SRT/VTT ich nie dostają — tam byłyby śmieciem na ekranie.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Dict, List

from ..teksty import t

#: Znak zaznaczonego momentu w TXT.
FLAGA = "⚑"

#: Klucze używane w GUI i w konfiguracji.
FORMATS = ["txt", "txt_plain", "srt", "vtt", "json"]

FORMAT_LABELS = {
    "txt": "TXT ze znacznikami czasu",
    "txt_plain": "TXT — sam tekst",
    "srt": "SRT — napisy",
    "vtt": "VTT — napisy WebVTT",
    "json": "JSON — pełne dane",
}

#: Rozszerzenie pliku dla każdego formatu (dwa warianty TXT muszą się różnić).
FORMAT_SUFFIX = {
    "txt": ".txt",
    "txt_plain": ".tekst.txt",
    "srt": ".srt",
    "vtt": ".vtt",
    "json": ".json",
}


def _clock(seconds: float, sep: str = ".", millis: bool = True) -> str:
    seconds = max(float(seconds), 0.0)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if not millis:
        return f"{h:02d}:{m:02d}:{s:02d}"
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:  # zaokrąglenie w górę nie może dać ".1000"
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _znaczniki(result) -> List[dict]:
    """Zaznaczenia z nagrania posortowane po czasie; pusta lista, gdy brak.

    {"czas", "koniec", "opis"}: `koniec` — fragment („Zaznacz” i drugie
    naciśnięcie), None — pojedynczy moment.
    """
    lista = getattr(result, "znaczniki", None) or []
    return sorted((z for z in lista if isinstance(z, dict)), key=lambda z: float(z.get("czas", 0.0)))


def _krotko(sekundy: float) -> str:
    """12:14 albo 1:01:40 — czas dla czytelnika, nie dla maszyny."""
    minuty, reszta = divmod(int(sekundy), 60)
    godziny, minuty = divmod(minuty, 60)
    return f"{godziny}:{minuty:02d}:{reszta:02d}" if godziny else f"{minuty}:{reszta:02d}"


def _zdarzenia(result) -> List[dict]:
    """Zaznaczenia jako zdarzenia w czasie, gotowe do wstawienia w tekst.

    Fragment daje dwa: początek i koniec (w czystym tekście początek mówi
    od razu, ile trwa), moment — jedno. {"czas", "txt", "czysty"}.
    """
    wynik = []
    numer = 0
    for z in _znaczniki(result):
        czas = float(z["czas"])
        opis = str(z.get("opis", "") or "").strip()
        if z.get("koniec") is not None:
            numer += 1
            koniec = float(z["koniec"])
            dopisek = f": {opis}" if opis else ""
            wynik.append({"czas": czas,
                          "txt": f"{FLAGA} " + t("Fragment {n} — początek").format(n=numer) + dopisek,
                          "czysty": f"{FLAGA} " + t("Fragment {n}: {od}–{do}").format(
                              n=numer, od=_krotko(czas), do=_krotko(koniec)) + dopisek})
            wynik.append({"czas": koniec,
                          "txt": f"{FLAGA} " + t("Fragment {n} — koniec").format(n=numer),
                          "czysty": f"{FLAGA} " + t("koniec fragmentu {n}").format(n=numer)})
        else:
            tekst = t("zaznaczony moment")
            wynik.append({"czas": czas,
                          "txt": f"{FLAGA} {tekst}: {opis}" if opis else f"{FLAGA} {tekst}",
                          "czysty": f"{FLAGA} {_krotko(czas)}" + (f" — {opis}" if opis else "")})
    return sorted(wynik, key=lambda z: z["czas"])


def _mowca(result, seg) -> str:
    """Prefiks z nazwą mówcy albo pusty, gdy mówców nie rozpoznawano."""
    if result is None or getattr(seg, "speaker", -1) < 0:
        return ""
    from .diarization import etykieta

    return etykieta(seg.speaker, getattr(result, "speaker_names", None)) + ": "


def write_txt(result, path: Path) -> Path:
    """TXT z zakresem czasu przed każdym segmentem; znaczniki w swoim miejscu."""
    lines = []
    znaczniki = _zdarzenia(result)
    for seg in result.segments:
        while znaczniki and znaczniki[0]["czas"] < seg.start:
            z = znaczniki.pop(0)
            lines.append(f"[{_clock(z['czas'])}]  {z['txt']}")
        start = _clock(seg.start)
        end = _clock(seg.end)
        lines.append(f"[{start} --> {end}]  {_mowca(result, seg)}{seg.text.strip()}")
    for z in znaczniki:
        lines.append(f"[{_clock(z['czas'])}]  {z['txt']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_txt_plain(result, path: Path, width: int = 100) -> Path:
    """Czysty tekst zawinięty do czytelnej szerokości, akapit na segment ciszy."""
    paragraphs: List[str] = []
    buffer: List[str] = []
    prev_end = None
    prev_speaker = None
    speaker_label = ""
    #: Kogo podpisaliśmy ostatnio. Ta sama osoba mówiąca dalej po przerwie
    #: nie potrzebuje nazwy przy każdym akapicie — to tylko szum w tekście.
    podpisany = None

    znaczniki = _zdarzenia(result)
    #: Akapity ze znacznikiem nie są zawijane — to jedna krótka linia.
    bez_zawijania: set = set()

    def flaga(z: dict) -> None:
        paragraphs.append(z["czysty"])
        bez_zawijania.add(len(paragraphs) - 1)

    for seg in result.segments:
        text = seg.text.strip()
        if not text:
            continue
        speaker = getattr(seg, "speaker", -1)

        # Nowy akapit przy zmianie mówcy albo po dłuższej przerwie w mowie.
        zmiana_mowcy = prev_speaker is not None and speaker != prev_speaker
        przerwa = prev_end is not None and seg.start - prev_end > 2.0
        # Zaznaczony moment przed tym segmentem zamyka akapit i staje osobno.
        znacznik_przed = bool(znaczniki) and znaczniki[0]["czas"] < seg.start
        if buffer and (zmiana_mowcy or przerwa or znacznik_przed):
            paragraphs.append(speaker_label + " ".join(buffer))
            buffer = []
            speaker_label = ""
        while znaczniki and znaczniki[0]["czas"] < seg.start:
            flaga(znaczniki.pop(0))
            # Po znaczniku mówca podpisuje się na nowo — czytelnik wrócił tu z daleka.
            podpisany = None

        if not buffer and speaker >= 0 and speaker != podpisany:
            speaker_label = _mowca(result, seg)
            podpisany = speaker

        buffer.append(text)
        prev_end = seg.end
        prev_speaker = speaker

    if buffer:
        paragraphs.append(speaker_label + " ".join(buffer))
    for z in znaczniki:
        flaga(z)

    wrapped = [p if i in bez_zawijania else textwrap.fill(p, width=width)
               for i, p in enumerate(paragraphs)]
    path.write_text("\n\n".join(wrapped) + "\n", encoding="utf-8")
    return path


# --- napisy ---------------------------------------------------------------
#
# Whisper zwraca segmenty nawet po 30 sekund. Jako napisy są bezużyteczne,
# więc przed zapisem tniemy je na krótkie kwestie — po znacznikach słów,
# jeśli są dostępne, a w ostateczności proporcjonalnie po znakach.

MAX_CUE_SECONDS = 6.0
MAX_CUE_CHARS = 84
#: Poniżej tych progów nie łamiemy linii nawet na kropce — byłoby za krótko.
MIN_BREAK_SECONDS = 2.0
MIN_BREAK_CHARS = 32

_SENTENCE_END = (".", "!", "?", "…")
_CLAUSE_END = (",", ";", ":", "—", "–")


def _subtitle_cues(segments, result=None) -> List[tuple]:
    """Zamienia segmenty na listę (start, end, tekst) gotową na napisy."""
    cues: List[tuple] = []
    poprzedni_mowca = None
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        fits = (seg.end - seg.start) <= MAX_CUE_SECONDS and len(text) <= MAX_CUE_CHARS
        if fits:
            czesci = [(seg.start, seg.end, text)]
        elif seg.words:
            czesci = _split_by_words(seg)
        else:
            czesci = _split_by_chars(seg, text)

        # Nazwa mówcy tylko przy pierwszej kwestii i tylko wtedy, gdy mówca
        # się zmienił — powtarzanie jej w każdej linijce byłoby męczące.
        mowca = getattr(seg, "speaker", -1)
        prefiks = (
            _mowca(result, seg)
            if result is not None and mowca != poprzedni_mowca
            else ""
        )
        poprzedni_mowca = mowca
        for i, (start, koniec, tresc) in enumerate(czesci):
            cues.append((start, koniec, (prefiks + tresc) if i == 0 else tresc))
    return cues


def _split_by_words(seg) -> List[tuple]:
    """Dzieli segment na kwestie, respektując granice słów i interpunkcję."""
    cues: List[tuple] = []
    buffer: List = []

    def flush() -> None:
        if not buffer:
            return
        text = "".join(w.text for w in buffer).strip()
        if text:
            cues.append((buffer[0].start, buffer[-1].end, text))
        buffer.clear()

    for word in seg.words:
        buffer.append(word)
        span = buffer[-1].end - buffer[0].start
        chars = sum(len(w.text) for w in buffer)
        stripped = word.text.strip()

        too_long = span >= MAX_CUE_SECONDS or chars >= MAX_CUE_CHARS
        long_enough = span >= MIN_BREAK_SECONDS or chars >= MIN_BREAK_CHARS
        sentence_break = stripped.endswith(_SENTENCE_END) and long_enough
        clause_break = (
            stripped.endswith(_CLAUSE_END)
            and chars >= MAX_CUE_CHARS * 0.6
        )

        if too_long or sentence_break or clause_break:
            flush()

    flush()
    return cues or [(seg.start, seg.end, seg.text.strip())]


def _split_by_chars(seg, text: str) -> List[tuple]:
    """Awaryjny podział, gdy brakuje znaczników słów — czas dzielony liniowo."""
    words = text.split()
    if not words:
        return []

    chunks: List[str] = []
    current: List[str] = []
    for word in words:
        candidate = len(" ".join(current + [word]))
        if current and candidate > MAX_CUE_CHARS:
            chunks.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        chunks.append(" ".join(current))

    total_chars = sum(len(c) for c in chunks) or 1
    span = max(seg.end - seg.start, 0.001)
    cues: List[tuple] = []
    cursor = seg.start
    for chunk in chunks:
        share = span * (len(chunk) / total_chars)
        cues.append((cursor, min(cursor + share, seg.end), chunk))
        cursor += share
    return cues


def write_srt(result, path: Path) -> Path:
    blocks = []
    for idx, (start, end, text) in enumerate(
            _subtitle_cues(result.segments, result), 1):
        blocks.append(
            f"{idx}\n{_clock(start, sep=',')} --> {_clock(end, sep=',')}\n{text}\n"
        )
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def write_vtt(result, path: Path) -> Path:
    blocks = ["WEBVTT\n"]
    for start, end, text in _subtitle_cues(result.segments, result):
        blocks.append(f"{_clock(start)} --> {_clock(end)}\n{text}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def write_json(result, path: Path) -> Path:
    payload = {
        "source": str(result.source),
        "language": result.language,
        "language_probability": round(result.language_probability, 4),
        "duration": round(result.duration, 3),
        "model": result.model,
        "device": result.device,
        "compute_type": result.compute_type,
        "engine": result.engine,
        "elapsed_seconds": round(result.elapsed, 2),
        "text": result.text,
        **({"markers": [{"time": round(float(z["czas"]), 3),
                         "end": None if z.get("koniec") is None else round(float(z["koniec"]), 3),
                         "label": str(z.get("opis", "") or "")}
                        for z in _znaczniki(result)]}
           if _znaczniki(result) else {}),
        "segments": [
            {
                "id": i,
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": seg.text.strip(),
                **(
                    {
                        "speaker": seg.speaker,
                        "speaker_name": _mowca(result, seg).rstrip(": "),
                    }
                    if getattr(seg, "speaker", -1) >= 0
                    else {}
                ),
            }
            for i, seg in enumerate(result.segments)
        ],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


_WRITERS = {
    "txt": write_txt,
    "txt_plain": write_txt_plain,
    "srt": write_srt,
    "vtt": write_vtt,
    "json": write_json,
}


def write_all(result, out_dir: Path, stem: str, formats) -> Dict[str, Path]:
    """Zapisuje wynik we wskazanych formatach. Zwraca {format: ścieżka}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: Dict[str, Path] = {}
    for fmt in formats:
        writer = _WRITERS.get(fmt)
        if writer is None:
            continue
        target = _unique(out_dir / (stem + FORMAT_SUFFIX[fmt]))
        written[fmt] = writer(result, target)
    return written


def _unique(path: Path) -> Path:
    """Nie nadpisuje istniejących transkrypcji — dokłada licznik."""
    if not path.exists():
        return path
    # ".tekst.txt" musi zostać w całości, więc tniemy po znanym sufiksie.
    for suffix in sorted(FORMAT_SUFFIX.values(), key=len, reverse=True):
        if path.name.endswith(suffix):
            stem = path.name[: -len(suffix)]
            break
    else:
        stem, suffix = path.stem, path.suffix

    counter = 2
    while True:
        candidate = path.with_name(f"{stem} ({counter}){suffix}")
        if not candidate.exists():
            return candidate
        counter += 1
