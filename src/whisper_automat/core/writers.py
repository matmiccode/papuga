"""Zapis transkrypcji do plików: TXT, SRT, VTT, JSON."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Dict, List

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


def _mowca(result, seg) -> str:
    """Prefiks z nazwą mówcy albo pusty, gdy mówców nie rozpoznawano."""
    if result is None or getattr(seg, "speaker", -1) < 0:
        return ""
    from .diarization import etykieta

    return etykieta(seg.speaker, getattr(result, "speaker_names", None)) + ": "


def write_txt(result, path: Path) -> Path:
    """TXT z zakresem czasu przed każdym segmentem."""
    lines = []
    for seg in result.segments:
        start = _clock(seg.start)
        end = _clock(seg.end)
        lines.append(f"[{start} --> {end}]  {_mowca(result, seg)}{seg.text.strip()}")
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

    for seg in result.segments:
        text = seg.text.strip()
        if not text:
            continue
        speaker = getattr(seg, "speaker", -1)

        # Nowy akapit przy zmianie mówcy albo po dłuższej przerwie w mowie.
        zmiana_mowcy = prev_speaker is not None and speaker != prev_speaker
        przerwa = prev_end is not None and seg.start - prev_end > 2.0
        if buffer and (zmiana_mowcy or przerwa):
            paragraphs.append(speaker_label + " ".join(buffer))
            buffer = []
            speaker_label = ""

        if not buffer and speaker >= 0 and speaker != podpisany:
            speaker_label = _mowca(result, seg)
            podpisany = speaker

        buffer.append(text)
        prev_end = seg.end
        prev_speaker = speaker

    if buffer:
        paragraphs.append(speaker_label + " ".join(buffer))

    wrapped = [textwrap.fill(p, width=width) for p in paragraphs]
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
