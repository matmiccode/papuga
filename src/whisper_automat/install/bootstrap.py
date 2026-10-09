"""Instalator środowiska.

Uruchamiany systemowym Pythonem (przez setup.bat), zanim istnieje jakiekolwiek
środowisko wirtualne. Wykrywa sprzęt, dobiera zestaw pakietów, buduje `.venv`
w katalogu projektu i weryfikuje wynik.

Nie importuje niczego spoza biblioteki standardowej.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from typing import List, Optional, Sequence

# Instalator leży w src/whisper_automat/install/ — korzeń projektu jest 3 poziomy wyżej.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = PROJECT_ROOT / "src"
VENV_DIR = PROJECT_ROOT / ".venv"
MODELS_DIR = PROJECT_ROOT / "models"

MIN_PYTHON = (3, 9)

#: Pakiety wspólne dla każdej konfiguracji.
BASE_PACKAGES = [
    "faster-whisper>=1.1.0",
    "tkinterdnd2>=0.4.0",
    # Bez tego pobieranie modelu pada w sieciach firmowych z inspekcją TLS.
    "truststore>=0.9",
    # Rozpoznawanie mówców — wariant procesorowy, bo na GPU jest wolniej.
    "sherpa-onnx>=1.12",
    # Nagrywanie spotkań: mikrofon + dźwięk systemowy (WASAPI loopback).
    "PyAudioWPatch>=0.2.12.9",
]

#: Biblioteki CUDA, których CTranslate2 potrzebuje na Windows.
CUDA_PACKAGES = [
    "nvidia-cublas-cu12",
    "nvidia-cudnn-cu12>=9.0",
]

FFMPEG_WINGET_ID = "Gyan.FFmpeg"

_step = 0
_total_steps = 7


# ---------------------------------------------------------------------------
# Wypisywanie
# ---------------------------------------------------------------------------


def step(text: str) -> None:
    global _step
    _step += 1
    print(f"\n[{_step}/{_total_steps}] {text}", flush=True)


def ok(text: str) -> None:
    print(f"      OK   {text}", flush=True)


def warn(text: str) -> None:
    print(f"      !    {text}", flush=True)


def fail(text: str) -> None:
    print(f"      X    {text}", flush=True)


def info(text: str) -> None:
    print(f"           {text}", flush=True)


def header() -> None:
    print("=" * 70)
    print("  WHISPER AUTOMAT — instalacja środowiska")
    print("=" * 70)
    print(f"  Katalog projektu: {PROJECT_ROOT}")


# ---------------------------------------------------------------------------
# Narzędzia
# ---------------------------------------------------------------------------


def run(cmd: Sequence[str], check: bool = True, quiet: bool = False) -> int:
    """Uruchamia komendę, przepuszczając wyjście na konsolę."""
    if not quiet:
        info(f"$ {' '.join(str(c) for c in cmd)}")
    try:
        proc = subprocess.run(list(cmd))
    except FileNotFoundError:
        if check:
            raise
        return 127
    if check and proc.returncode != 0:
        raise RuntimeError(f"Komenda zakończyła się kodem {proc.returncode}")
    return proc.returncode


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def load_probe():
    """Wciąga moduł detekcji sprzętu bez instalowania pakietu."""
    sys.path.insert(0, str(SRC_DIR))
    from whisper_automat.core import probe  # noqa: E402

    return probe


# ---------------------------------------------------------------------------
# Kroki instalacji
# ---------------------------------------------------------------------------


def check_python() -> bool:
    step("Sprawdzam Pythona")
    version = sys.version_info
    text = f"{version.major}.{version.minor}.{version.micro}"
    if version[:2] < MIN_PYTHON:
        fail(f"Python {text} jest za stary (wymagany {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+).")
        info("Pobierz nowszą wersję z https://www.python.org/downloads/windows/")
        return False
    ok(f"Python {text} — {sys.executable}")

    try:
        import tkinter  # noqa: F401

        ok("tkinter dostępny (interfejs graficzny zadziała).")
    except ImportError:
        fail("Brak modułu tkinter — bez niego okno programu się nie otworzy.")
        info("Zainstaluj Pythona z python.org zaznaczając komponent 'tcl/tk'.")
        return False
    return True


def ensure_ffmpeg(auto_install: bool = True) -> bool:
    step("Sprawdzam ffmpeg")
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        ok(shutil.which("ffmpeg"))
        return True

    warn("Nie znaleziono ffmpeg w PATH.")
    if not auto_install:
        info("Zainstaluj ręcznie: winget install Gyan.FFmpeg")
        return False

    if os.name != "nt" or not shutil.which("winget"):
        fail("Brak wingeta — zainstaluj ffmpeg ręcznie.")
        info("Windows : winget install Gyan.FFmpeg")
        info("Ręcznie : https://www.gyan.dev/ffmpeg/builds/ (dodaj bin/ do PATH)")
        return False

    info("Instaluję ffmpeg przez winget — może pojawić się pytanie o zgodę.")
    code = run(
        [
            "winget", "install", "--id", FFMPEG_WINGET_ID, "-e",
            "--source", "winget",
            "--accept-source-agreements", "--accept-package-agreements",
        ],
        check=False,
    )
    if code != 0:
        fail(f"winget zwrócił kod {code}.")
        info("Zainstaluj ffmpeg ręcznie i uruchom setup.bat ponownie.")
        return False

    if _refresh_path() and shutil.which("ffmpeg"):
        ok("ffmpeg zainstalowany i widoczny.")
        return True

    warn("ffmpeg zainstalowany, ale PATH w tym oknie jest jeszcze stary.")
    info("Zamknij to okno i uruchom setup.bat ponownie — to wystarczy.")
    return False


def _refresh_path() -> bool:
    """Odświeża PATH procesu na podstawie rejestru (winget zmienia go globalnie)."""
    if os.name != "nt":
        return False
    try:
        import winreg
    except ImportError:
        return False

    parts: List[str] = []
    for root, key in (
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    ):
        try:
            with winreg.OpenKey(root, key) as handle:
                value, _type = winreg.QueryValueEx(handle, "Path")
                parts.append(os.path.expandvars(value))
        except OSError:
            continue

    if not parts:
        return False
    os.environ["PATH"] = os.pathsep.join(parts + [os.environ.get("PATH", "")])
    return True


def detect_hardware():
    step("Wykrywam sprzęt i dobieram model")
    probe = load_probe()
    hw = probe.probe(str(PROJECT_ROOT))
    rec = probe.recommend(hw)

    ok(f"Procesor : {hw.cpu_name} ({hw.cpu_threads} wątków)")
    ok(f"RAM      : {hw.ram_gb:g} GB")
    ok(f"Dysk     : {hw.disk_free_gb:g} GB wolnego")
    if hw.gpus:
        for gpu in hw.gpus:
            ok(f"GPU      : {gpu.name} — {gpu.vram_gb:g} GB VRAM "
               f"(sterownik {gpu.driver})")
    else:
        warn("Brak karty NVIDIA — transkrypcja pójdzie na procesorze.")

    print()
    info(f"Zalecany model : {rec.model}")
    info(f"Urządzenie     : {rec.device} / {rec.compute_type}")
    info(f"Dlaczego       : {rec.reason}")
    for w in rec.warnings:
        warn(w)

    if hw.disk_free_gb < 5:
        warn(f"Zostało tylko {hw.disk_free_gb:g} GB — modele mogą się nie zmieścić.")
    return hw, rec


def venv_healthy() -> bool:
    """Czy istniejące środowisko faktycznie się uruchamia.

    Sama obecność python.exe nic nie znaczy: środowisko skopiowane z innego
    komputera ma w pyvenv.cfg ścieżkę do bazowego Pythona z cudzą nazwą
    użytkownika i wywala się komunikatem „No Python at ...".
    """
    exe = venv_python()
    if not exe.is_file():
        return False
    try:
        proc = subprocess.run(
            [str(exe), "-c", "import sys; sys.exit(0)"],
            capture_output=True,
            timeout=60,
        )
    except Exception:
        return False
    return proc.returncode == 0


def create_venv(recreate: bool = False) -> bool:
    step("Tworzę środowisko wirtualne")
    if VENV_DIR.exists() and recreate:
        info(f"Usuwam poprzednie środowisko: {VENV_DIR}")
        shutil.rmtree(VENV_DIR, ignore_errors=True)

    if venv_python().is_file():
        if venv_healthy():
            ok(f"Środowisko już istnieje i działa: {VENV_DIR}")
            return True
        warn("Znalezione środowisko nie uruchamia się.")
        info("Zwykle znaczy to, że folder .venv przyjechał z innego komputera.")
        info("Buduję je od nowa — to jedyne sensowne wyjście.")
        shutil.rmtree(VENV_DIR, ignore_errors=True)
        if VENV_DIR.exists():
            fail(f"Nie udało się usunąć {VENV_DIR} — usuń ten folder ręcznie.")
            return False

    info(f"Buduję {VENV_DIR} …")
    try:
        venv.EnvBuilder(with_pip=True, clear=False, upgrade_deps=False).create(VENV_DIR)
    except Exception as exc:
        fail(f"Nie udało się utworzyć środowiska: {exc}")
        return False

    if not venv_python().is_file():
        fail("Środowisko powstało, ale brakuje w nim interpretera.")
        return False
    ok(f"Gotowe: {VENV_DIR}")
    return True


def install_packages(hw, force_cpu: bool = False) -> bool:
    step("Instaluję pakiety")
    python = venv_python()

    packages = list(BASE_PACKAGES)
    gpu = hw.gpu
    use_gpu = gpu is not None and gpu.supports_cuda12 and not force_cpu

    if use_gpu:
        packages += CUDA_PACKAGES
        info(f"Wykryto {gpu.name} — dokładam biblioteki CUDA (cuBLAS + cuDNN).")
        info("To około 1,3 GB; bez nich karta nie zostałaby użyta.")
    elif gpu is not None and not force_cpu:
        # Nie ma sensu ciągnąć 1,3 GB bibliotek, których stary sterownik
        # i tak nie uruchomi.
        warn(f"Sterownik {gpu.driver} jest za stary dla CUDA 12 — pomijam "
             f"biblioteki CUDA.")
        info("Po aktualizacji sterownika uruchom setup.bat ponownie.")
    else:
        info("Konfiguracja procesorowa — bez bibliotek CUDA (1,3 GB mniej).")

    try:
        run([str(python), "-m", "pip", "install", "--upgrade", "pip", "wheel"],
            check=False)
        run([str(python), "-m", "pip", "install", "--upgrade"] + packages)
    except Exception as exc:
        fail(f"Instalacja pakietów nie powiodła się: {exc}")
        info("Sprawdź połączenie z internetem i uruchom setup.bat ponownie.")
        return False

    ok(f"Zainstalowano {len(packages)} pakiet(ów).")
    return True


def download_model(model: str, skip: bool = False) -> bool:
    step("Pobieram model")
    if skip:
        info("Pominięto na życzenie — model pobierze się przy pierwszej transkrypcji.")
        return True

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    info(f"Model {model} trafi do {MODELS_DIR}")
    info("Przy pierwszym razie to 1-3 GB — może chwilę potrwać.")

    code = run(
        [
            str(venv_python()), "-c",
            "import sys;"
            "from faster_whisper import download_model;"
            "p = download_model(sys.argv[1], cache_dir=sys.argv[2]);"
            "print('   ->', p)",
            model,
            str(MODELS_DIR),
        ],
        check=False,
    )
    if code != 0:
        warn("Nie udało się pobrać modelu teraz.")
        info("To nie blokuje instalacji — program pobierze go przy pierwszym użyciu.")
        return True
    ok(f"Model {model} gotowy.")
    return True


def verify() -> bool:
    step("Sprawdzam wynik instalacji")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR)
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        proc = subprocess.run(
            [str(venv_python()), "-m", "whisper_automat", "--doctor"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        fail(f"Nie udało się uruchomić diagnostyki: {exc}")
        return False

    print(proc.stdout or "")
    if (proc.stderr or "").strip():
        print(proc.stderr)
    return proc.returncode == 0


# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="setup",
        description="Instaluje środowisko Whisper Automat.",
    )
    parser.add_argument("--cpu", action="store_true",
                        help="Wymuś konfigurację bez CUDA.")
    parser.add_argument("--recreate", action="store_true",
                        help="Zbuduj środowisko od zera.")
    parser.add_argument("--no-model", action="store_true",
                        help="Nie pobieraj modelu podczas instalacji.")
    parser.add_argument("--no-ffmpeg-install", action="store_true",
                        help="Nie próbuj instalować ffmpeg przez winget.")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    header()

    if not check_python():
        return 1

    ffmpeg_ok = ensure_ffmpeg(auto_install=not args.no_ffmpeg_install)
    hw, rec = detect_hardware()

    if not create_venv(recreate=args.recreate):
        return 1
    if not install_packages(hw, force_cpu=args.cpu):
        return 1

    download_model(rec.model, skip=args.no_model)
    healthy = verify()

    print("\n" + "=" * 70)
    if healthy and ffmpeg_ok:
        print("  INSTALACJA ZAKOŃCZONA — uruchom program plikiem Uruchom.bat")
    elif not ffmpeg_ok:
        print("  PRAWIE GOTOWE — brakuje jeszcze ffmpeg (zobacz komunikaty wyżej).")
    else:
        print("  INSTALACJA ZAKOŃCZONA Z UWAGAMI — zobacz diagnostykę powyżej.")
    print("=" * 70)
    return 0 if (healthy and ffmpeg_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
