"""Obsługa plików multimedialnych: sondowanie i ekstrakcja audio przez ffmpeg."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from ..teksty import t
from .probe import find_ffmpeg

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

VIDEO_EXT = {
    ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts", ".vob", ".3gp", ".mts",
}

AUDIO_EXT = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus",
    ".wma", ".aiff", ".aif", ".alac", ".amr", ".mp2", ".ac3", ".caf",
}

MEDIA_EXT = VIDEO_EXT | AUDIO_EXT

#: Whisper i tak resample'uje do 16 kHz mono — robimy to od razu w ffmpeg.
TARGET_RATE = 16_000
TARGET_CHANNELS = 1


class MediaError(RuntimeError):
    """Błąd sondowania lub konwersji pliku multimedialnego."""


@dataclass
class MediaInfo:
    path: Path
    duration: float = 0.0  # sekundy
    has_audio: bool = False
    has_video: bool = False
    audio_codec: str = ""
    size_bytes: int = 0

    @property
    def is_video(self) -> bool:
        return self.has_video


def is_media_file(path) -> bool:
    return Path(path).suffix.lower() in MEDIA_EXT


def _ffmpeg_bin() -> str:
    ffmpeg, _ = find_ffmpeg()
    if not ffmpeg:
        raise MediaError(t(
            "Nie znaleziono ffmpeg w systemie. Uruchom setup.bat albo zainstaluj "
            "ffmpeg ręcznie (winget install Gyan.FFmpeg)."
        ))
    return ffmpeg


def _ffprobe_bin() -> str:
    _, ffprobe = find_ffmpeg()
    if not ffprobe:
        raise MediaError(t(
            "Nie znaleziono ffprobe (składnik ffmpeg). Uruchom setup.bat."
        ))
    return ffprobe


def probe_media(path) -> MediaInfo:
    """Odczytuje długość i strumienie pliku przy pomocy ffprobe."""
    p = Path(path)
    if not p.is_file():
        raise MediaError(t("Plik nie istnieje: {plik}").format(plik=p))

    cmd = [
        _ffprobe_bin(),
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(p),
    ]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError(
            t("ffprobe nie odpowiedział dla pliku {plik}").format(plik=p.name)
        ) from exc

    if out.returncode != 0:
        raise MediaError(
            t("ffprobe nie potrafi odczytać pliku {plik}: {blad}").format(
                plik=p.name, blad=(out.stderr or "").strip()[:300]
            )
        )

    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        raise MediaError(
            t("Nieczytelna odpowiedź ffprobe dla {plik}").format(plik=p.name)
        ) from exc

    info = MediaInfo(path=p, size_bytes=p.stat().st_size)

    duration = data.get("format", {}).get("duration")
    if duration:
        try:
            info.duration = float(duration)
        except ValueError:
            pass

    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "audio":
            info.has_audio = True
            info.audio_codec = info.audio_codec or stream.get("codec_name", "")
            if not info.duration and stream.get("duration"):
                try:
                    info.duration = float(stream["duration"])
                except ValueError:
                    pass
        elif kind == "video":
            # Okładka w MP3 to też "strumień wideo" — pomijamy obrazki.
            if stream.get("codec_name") not in {"mjpeg", "png", "bmp", "gif"}:
                info.has_video = True

    if not info.has_audio:
        raise MediaError(t(
            "Plik {plik} nie zawiera ścieżki dźwiękowej — nie ma czego "
            "transkrybować."
        ).format(plik=p.name))
    return info


# ffmpeg raportuje out_time_us w mikrosekundach; out_time_ms mimo nazwy też
# jest w mikrosekundach, więc bierzemy to pierwsze i tylko awaryjnie drugie.
_TIME_RE = re.compile(r"out_time_(?:us|ms)=(\d+)")


def extract_audio(
    src,
    dst: Optional[Path] = None,
    duration: float = 0.0,
    on_progress: Optional[Callable[[float], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> Path:
    """Wyciąga ścieżkę audio do WAV 16 kHz mono.

    Zwraca ścieżkę pliku wynikowego. `on_progress` dostaje ułamek 0.0-1.0.
    """
    src = Path(src)
    if dst is None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="whisper_automat_"))
        dst = tmp_dir / (src.stem + ".wav")
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        _ffmpeg_bin(),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i", str(src),
        "-vn",                      # bez wideo
        "-sn",                      # bez napisów
        "-dn",                      # bez danych
        "-map", "0:a:0",            # pierwsza ścieżka audio
        "-ac", str(TARGET_CHANNELS),
        "-ar", str(TARGET_RATE),
        "-c:a", "pcm_s16le",
        "-progress", "pipe:1",
        "-loglevel", "error",
        str(dst),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=_NO_WINDOW,
    )

    try:
        if proc.stdout is not None:
            for line in proc.stdout:
                if cancel is not None and cancel():
                    proc.kill()
                    raise MediaError(t("Ekstrakcja audio przerwana przez użytkownika."))
                if on_progress and duration > 0:
                    match = _TIME_RE.search(line)
                    if match:
                        done = int(match.group(1)) / 1_000_000.0
                        on_progress(min(done / duration, 1.0))
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise MediaError(t("ffmpeg zawiesił się przy zamykaniu pliku."))

    if proc.returncode != 0:
        stderr = (proc.stderr.read() if proc.stderr else "").strip()
        raise MediaError(
            t("ffmpeg nie zdołał wyciągnąć audio z {plik}: {blad}").format(
                plik=src.name, blad=stderr[:400]
            )
        )

    if not dst.is_file() or dst.stat().st_size == 0:
        raise MediaError(
            t("ffmpeg wyprodukował pusty plik audio dla {plik}").format(plik=src.name)
        )

    if on_progress:
        on_progress(1.0)
    return dst


def prepare_audio(
    src,
    workdir: Optional[Path] = None,
    on_progress: Optional[Callable[[float], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
):
    """Przygotowuje plik do transkrypcji.

    Zwraca (ścieżka_audio, MediaInfo, czy_plik_tymczasowy). Wideo i formaty
    stratne przechodzą przez ffmpeg; gotowy WAV 16 kHz mono jest brany wprost.
    """
    info = probe_media(src)
    src = Path(src)

    if _is_ready_wav(info):
        if on_progress:
            on_progress(1.0)
        return src, info, False

    dst = None
    if workdir is not None:
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        dst = workdir / (src.stem + ".16k.wav")

    audio = extract_audio(
        src, dst=dst, duration=info.duration, on_progress=on_progress, cancel=cancel
    )
    return audio, info, True


def _is_ready_wav(info: MediaInfo) -> bool:
    """WAV PCM 16 kHz mono nie wymaga konwersji — oszczędzamy czas i dysk."""
    if info.has_video or info.path.suffix.lower() != ".wav":
        return False
    try:
        out = subprocess.run(
            [
                _ffprobe_bin(),
                "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=sample_rate,channels,codec_name",
                "-of", "csv=p=0",
                str(info.path),
            ],
            capture_output=True, text=True, timeout=30,
            creationflags=_NO_WINDOW,
        )
    except Exception:
        return False
    parts = [p.strip() for p in (out.stdout or "").strip().split(",")]
    if len(parts) < 3:
        return False
    codec, rate, channels = parts[0], parts[1], parts[2]
    return (
        codec == "pcm_s16le"
        and rate == str(TARGET_RATE)
        and channels == str(TARGET_CHANNELS)
    )


def collect_media(paths) -> List[Path]:
    """Rozwija listę ścieżek (pliki + katalogi) do listy plików medialnych."""
    found: List[Path] = []
    for raw in paths:
        p = Path(str(raw).strip().strip('"'))
        if p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file() and is_media_file(child):
                    found.append(child)
        elif p.is_file() and is_media_file(p):
            found.append(p)
    # Deduplikacja z zachowaniem kolejności.
    seen = set()
    unique: List[Path] = []
    for p in found:
        key = str(p.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def format_duration(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"
