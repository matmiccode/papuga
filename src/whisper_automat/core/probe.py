"""Wykrywanie sprzętu i dobór optymalnego modelu Whispera.

Moduł jest celowo wolny od zależności zewnętrznych — korzysta z niego również
instalator, który działa jeszcze przed utworzeniem środowiska.
"""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional

# ---------------------------------------------------------------------------
# Katalog modeli
# ---------------------------------------------------------------------------

#: Kolejność od najlżejszego do najcięższego. VRAM/RAM w GB.
#:
#: Kolumna ram_cpu to zmierzony szczyt zużycia pamięci przy transkrypcji na
#: procesorze w int8, zaokrąglony w górę z zapasem. Pomiar na 2-minutowym
#: nagraniu: base 414 MB, small 1077 MB, large-v3-turbo 1928 MB. Zapotrzebowanie
#: nie rośnie z długością pliku, bo Whisper przetwarza dźwięk 30-sekundowymi
#: oknami. Wcześniejsze wartości były szacunkami i zawyżały je 2-3 krotnie,
#: przez co słabsze komputery dostawały niepotrzebnie gorszy model.
MODELS = [
    # nazwa             jakość  vram_fp16  vram_int8  ram_cpu  szybkość
    ("tiny", 1, 1.0, 0.6, 0.4, 32.0),
    ("base", 2, 1.2, 0.8, 0.6, 16.0),
    ("small", 3, 2.0, 1.2, 1.4, 6.0),
    ("medium", 4, 4.2, 2.4, 2.5, 2.0),
    ("large-v3-turbo", 5, 3.0, 1.8, 2.5, 8.0),
    ("large-v3", 6, 5.6, 3.6, 4.5, 1.0),
]

MODEL_BY_NAME = {m[0]: m for m in MODELS}

#: Opisy pokazywane w GUI.
MODEL_LABELS = {
    "tiny": "tiny — błyskawiczny, niska jakość",
    "base": "base — szybki, podstawowa jakość",
    "small": "small — kompromis",
    "medium": "medium — dobra jakość",
    "large-v3-turbo": "large-v3-turbo — najlepszy stosunek jakość/czas",
    "large-v3": "large-v3 — najwyższa jakość, najwolniejszy",
}

#: Kolejność automatycznego wyboru, od najlepszego. `large-v3` celowo tu nie
#: ma: jest około ośmiu razy wolniejszy od `large-v3-turbo`, a różnica jakości
#: jest niewielka. Zostaje do wyboru ręcznego w oknie programu.
AUTO_PREFERENCE = ["large-v3-turbo", "medium", "small", "base", "tiny"]

#: Ile VRAM zostawiamy systemowi i pulpitowi.
VRAM_HEADROOM_GB = 1.0

#: Ile RAM zostawiamy systemowi przy pracy na CPU.
RAM_HEADROOM_GB = 4.0


# ---------------------------------------------------------------------------
# Struktury danych
# ---------------------------------------------------------------------------


#: Minimalna wersja sterownika NVIDIA, na której działają biblioteki CUDA 12
#: (Windows). Dzięki zgodności wersji pomocniczych wystarczy sterownik dla
#: CUDA 12.0, nawet gdy same biblioteki są w wersji 12.9.
MIN_DRIVER_CUDA12 = 527.41


@dataclass
class GpuInfo:
    name: str
    vram_gb: float
    driver: str = ""
    cuda_runtime: str = ""
    #: Generacja układu, np. 6.1 (Pascal), 7.5 (Turing), 8.6 (Ampere).
    #: 0.0 oznacza, że nvidia-smi tej informacji nie podał.
    compute_capability: float = 0.0

    @property
    def driver_number(self) -> float:
        parts = (self.driver or "").split(".")
        try:
            return float(".".join(parts[:2])) if len(parts) >= 2 else float(parts[0])
        except (ValueError, IndexError):
            return 0.0

    @property
    def supports_cuda12(self) -> bool:
        """Czy sterownik jest na tyle nowy, żeby unieść biblioteki CUDA 12."""
        number = self.driver_number
        # Nieznana wersja sterownika: nie blokujemy, silnik i tak sprawdzi to
        # przy ładowaniu modelu i w razie czego zejdzie na procesor.
        return number == 0.0 or number >= MIN_DRIVER_CUDA12

    @property
    def generation(self) -> float:
        """Compute capability z bezpiecznym domysłem, gdy jest nieznana."""
        # Sterownik nowszy niż 527 zawsze raportuje compute_cap, więc brak tej
        # wartości przy nowym sterowniku to anomalia — zakładamy współczesną
        # kartę, a niezgodność wyłapie silnik przy ładowaniu modelu.
        return self.compute_capability or 7.0


@dataclass
class Hardware:
    os_name: str = ""
    python_version: str = ""
    python_exe: str = ""
    cpu_name: str = ""
    cpu_cores: int = 0
    cpu_threads: int = 0
    ram_gb: float = 0.0
    disk_free_gb: float = 0.0
    gpus: List[GpuInfo] = field(default_factory=list)
    #: Nazwy kart z systemu — wypełniane tylko wtedy, gdy nvidia-smi milczy.
    #: Pozwalają odróżnić „brak karty NVIDIA" od „karta jest, brak sterownika".
    adapters: List[str] = field(default_factory=list)
    ffmpeg: Optional[str] = None
    ffprobe: Optional[str] = None

    @property
    def gpu(self) -> Optional[GpuInfo]:
        """Najmocniejsza (pod względem VRAM) karta NVIDIA, jeśli jest."""
        return max(self.gpus, key=lambda g: g.vram_gb) if self.gpus else None

    @property
    def has_cuda_gpu(self) -> bool:
        return bool(self.gpus)

    @property
    def nvidia_without_driver(self) -> Optional[str]:
        """Nazwa karty NVIDIA widocznej w systemie, ale bez działającego sterownika."""
        if self.gpus:
            return None
        for name in self.adapters:
            if "nvidia" in name.lower() or "geforce" in name.lower():
                return name
        return None


@dataclass
class Recommendation:
    model: str
    device: str  # "cuda" | "cpu"
    compute_type: str  # "float16" | "int8_float16" | "int8"
    reason: str
    alternatives: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Detekcja
# ---------------------------------------------------------------------------

#: CREATE_NO_WINDOW — żeby przy uruchomieniu z GUI nie mrugały czarne okienka.
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _run(cmd, timeout: int = 20) -> str:
    """Uruchamia komendę i zwraca stdout; pusty string przy błędzie."""
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
        return out.stdout or ""
    except Exception:
        return ""


def total_ram_gb() -> float:
    if os.name == "nt":

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return round(stat.ullTotalPhys / (1024**3), 1)
        return 0.0
    try:
        return round(
            os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024**3), 1
        )
    except (ValueError, AttributeError):
        return 0.0


def cpu_name() -> str:
    """Nazwa procesora — z rejestru, bo to natychmiastowe.

    Wcześniej pytaliśmy o to PowerShella, co kosztowało kilka sekund przy
    każdym starcie programu. Ta sama informacja leży w rejestrze i czyta się
    ją w ułamku milisekundy.
    """
    if os.name != "nt":
        return platform.processor() or platform.machine()

    try:
        import winreg

        klucz = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, klucz) as uchwyt:
            nazwa, _typ = winreg.QueryValueEx(uchwyt, "ProcessorNameString")
            if nazwa:
                return str(nazwa).strip()
    except (ImportError, OSError):
        pass

    return os.environ.get("PROCESSOR_IDENTIFIER", "nieznany")


def physical_cores() -> int:
    """Liczba rdzeni fizycznych. Wartość wyłącznie informacyjna — decyzje
    o modelu opierają się na liczbie wątków."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            # GetLogicalProcessorInformation wypelnia tablice rekordow; te
            # z RelationProcessorCore == 0 odpowiadaja rdzeniom fizycznym.
            class _SYSTEM_LOGICAL_PROCESSOR_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("ProcessorMask", ctypes.c_void_p),
                    ("Relationship", wintypes.DWORD),
                    ("Reserved", ctypes.c_byte * 16),
                ]

            fn = ctypes.windll.kernel32.GetLogicalProcessorInformation
            fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
            fn.restype = wintypes.BOOL

            rozmiar = wintypes.DWORD(0)
            fn(None, ctypes.byref(rozmiar))
            ile = rozmiar.value // ctypes.sizeof(
                _SYSTEM_LOGICAL_PROCESSOR_INFORMATION
            )
            if ile:
                bufor = (_SYSTEM_LOGICAL_PROCESSOR_INFORMATION * ile)()
                if fn(ctypes.byref(bufor), ctypes.byref(rozmiar)):
                    return sum(1 for wpis in bufor if wpis.Relationship == 0)
        except Exception:
            pass
    return os.cpu_count() or 0


def _cuda_from_smi() -> str:
    out = _run(["nvidia-smi"])
    for line in out.splitlines():
        if "CUDA Version" in line:
            tail = line.split("CUDA Version:")[-1].strip(" |")
            return tail.split()[0] if tail.split() else ""
    return ""


def detect_gpus() -> List[GpuInfo]:
    """Wykrywa karty NVIDIA przez nvidia-smi (jest z każdym sterownikiem)."""
    # Pole compute_cap pojawiło się dopiero w nvidia-smi z CUDA 11.6. Na
    # starszym sterowniku całe zapytanie by się wywaliło, więc mamy wariant
    # zapasowy bez tego pola.
    out = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version,compute_cap",
            "--format=csv,noheader,nounits",
        ]
    )
    with_cc = bool(out.strip())
    if not with_cc:
        out = _run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ]
        )

    cuda = _cuda_from_smi() if out.strip() else ""
    gpus: List[GpuInfo] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            vram = round(float(parts[1]) / 1024, 1)  # MiB -> GB
        except ValueError:
            continue

        compute_cap = 0.0
        if with_cc and len(parts) > 3:
            try:
                compute_cap = float(parts[3])
            except ValueError:
                compute_cap = 0.0

        gpus.append(
            GpuInfo(
                name=parts[0],
                vram_gb=vram,
                driver=parts[2] if len(parts) > 2 else "",
                cuda_runtime=cuda,
                compute_capability=compute_cap,
            )
        )
    return gpus


def detect_display_adapters() -> List[str]:
    """Nazwy kart graficznych prosto z systemu.

    W przeciwieństwie do nvidia-smi działa też wtedy, gdy karta NVIDIA jest
    w komputerze, ale nie ma zainstalowanego sterownika — a to najczęstsza
    przyczyna „dlaczego liczy na procesorze" na świeżym systemie.
    """
    if os.name != "nt":
        return []
    out = _run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "(Get-CimInstance Win32_VideoController).Name",
        ],
        timeout=30,
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def free_disk_gb(path: str = ".") -> float:
    try:
        return round(shutil.disk_usage(os.path.abspath(path)).free / (1024**3), 1)
    except OSError:
        return 0.0


def _bundled_bin(nazwa: str) -> Optional[str]:
    """Szuka narzędzia w katalogu bin/ dołączonym do spakowanej wersji."""
    try:
        from .config import bundled_root, project_root
    except ImportError:
        return None

    plik = nazwa + (".exe" if os.name == "nt" else "")
    for katalog in (bundled_root() / "bin", project_root() / "bin"):
        kandydat = katalog / plik
        if kandydat.is_file():
            return str(kandydat)
    return None


def find_ffmpeg():
    """Zwraca krotkę (ffmpeg, ffprobe) — ścieżki lub None.

    Wersja spakowana ma własny ffmpeg w środku i on ma pierwszeństwo: dzięki
    temu program działa na komputerze, na którym nikt nic nie instalował.
    """
    ffmpeg = _bundled_bin("ffmpeg") or shutil.which("ffmpeg")
    ffprobe = _bundled_bin("ffprobe") or shutil.which("ffprobe")
    return ffmpeg, ffprobe


def probe(disk_path: str = ".") -> Hardware:
    ffmpeg, ffprobe = find_ffmpeg()
    gpus = detect_gpus()
    # Odpytujemy system o karty tylko wtedy, gdy nvidia-smi nic nie znalazł —
    # w pozostałych przypadkach to zbędna sekunda czekania.
    adapters = detect_display_adapters() if not gpus else []
    return Hardware(
        os_name=f"{platform.system()} {platform.release()}",
        python_version=platform.python_version(),
        python_exe=sys.executable,
        cpu_name=cpu_name(),
        cpu_cores=physical_cores(),
        cpu_threads=os.cpu_count() or 0,
        ram_gb=total_ram_gb(),
        disk_free_gb=free_disk_gb(disk_path),
        gpus=gpus,
        adapters=adapters,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
    )


# ---------------------------------------------------------------------------
# Rekomendacja
# ---------------------------------------------------------------------------


def recommend(hw: Hardware) -> Recommendation:
    """Dobiera model, urządzenie i precyzję do wykrytego sprzętu."""
    warnings: List[str] = []

    if hw.ffmpeg is None:
        warnings.append("Nie znaleziono ffmpeg — bez niego nie da się czytać wideo.")

    bez_sterownika = hw.nvidia_without_driver
    if bez_sterownika:
        warnings.append(
            f"W komputerze jest karta „{bez_sterownika}”, ale nie odpowiada "
            f"nvidia-smi — najpewniej brakuje sterownika NVIDIA. Po jego "
            f"instalacji uruchom setup.bat ponownie, żeby przejść na GPU."
        )

    gpu = hw.gpu
    if gpu is not None and not gpu.supports_cuda12:
        warnings.append(
            f"Sterownik karty {gpu.name} ma wersję {gpu.driver}, a biblioteki "
            f"CUDA 12 wymagają co najmniej {MIN_DRIVER_CUDA12:g}. Zaktualizuj "
            f"sterownik ze strony nvidia.com/drivers i uruchom setup.bat "
            f"ponownie — do tego czasu liczę na procesorze."
        )
        gpu = None

    if gpu is not None:
        usable = max(gpu.vram_gb - VRAM_HEADROOM_GB, 0.0)
        pelna, kwantyzacja, mnoznik = _gpu_compute_types(gpu.generation)

        if gpu.generation < 6.1:
            warnings.append(
                f"{gpu.name} to układ starszej generacji (compute capability "
                f"{gpu.generation:g}) — brakuje mu sprzętowego wsparcia dla "
                f"obliczeń int8, więc przewaga nad procesorem będzie niewielka."
            )

        if gpu.generation >= 7.0:
            # Rdzenie tensorowe: float16 jest zarazem szybki i dokładny,
            # więc przy równym modelu wolimy pełną precyzję.
            proby = [(pelna, True, 0), (kwantyzacja, False, 1)]
        else:
            # Bez rdzeni tensorowych to int8 ma sprzętowe wsparcie, a float16
            # potrafi być kilkadziesiąt razy wolniejszy od float32.
            proby = [(kwantyzacja, False, 0), (pelna, True, 1)]

        # Dla każdej precyzji szukamy najlepszego modelu, jaki się w niej
        # zmieści, a potem wybieramy najlepszy z tych kandydatów. Sama
        # kolejność prób nie wystarcza: na karcie z 2 GB pełna precyzja
        # mieści tylko `tiny`, podczas gdy int8 zmieści już `base`.
        kandydaci = []
        for compute_type, pelna_precyzja, priorytet in proby:
            for ranga, name in enumerate(AUTO_PREFERENCE):
                _n, _q, vram_fp16, vram_int8, _ram, _spd = MODEL_BY_NAME[name]
                potrzeba = vram_fp16 * mnoznik if pelna_precyzja else vram_int8
                if potrzeba <= usable:
                    kandydaci.append(
                        (ranga, priorytet, name, compute_type, pelna_precyzja)
                    )
                    break

        if kandydaci:
            kandydaci.sort(key=lambda k: (k[0], k[1]))
            _ranga, _prio, name, compute_type, pelna_precyzja = kandydaci[0]
            if pelna_precyzja:
                powod = (
                    f"{gpu.name} ma {gpu.vram_gb:g} GB VRAM — model {name} "
                    f"mieści się w pełnej precyzji {compute_type}."
                )
            else:
                powod = (
                    f"{gpu.name} ma {gpu.vram_gb:g} GB VRAM — model {name} "
                    f"w kwantyzacji {compute_type} zmieści się z zapasem "
                    f"i będzie wielokrotnie szybszy niż CPU."
                )
            return Recommendation(
                model=name,
                device="cuda",
                compute_type=compute_type,
                reason=powod,
                alternatives=_alternatives(name),
                warnings=warnings,
            )

        warnings.append(
            f"{gpu.name} ma za mało VRAM ({gpu.vram_gb:g} GB) nawet dla modelu tiny "
            "— przechodzę na CPU."
        )

    # --- CPU ---
    ram = hw.ram_gb or 8.0
    threads = hw.cpu_threads or 4
    usable_ram = max(ram - RAM_HEADROOM_GB, 1.0)

    for name in AUTO_PREFERENCE:
        _n, _q, _vf, _vi, ram_cpu, _spd = MODEL_BY_NAME[name]
        if ram_cpu <= usable_ram and _cpu_fast_enough(name, threads):
            return Recommendation(
                model=name,
                device="cpu",
                compute_type="int8",
                reason=(
                    f"Brak karty NVIDIA. Przy {ram:g} GB RAM i {threads} wątkach "
                    f"model {name} (int8) to rozsądny kompromis — transkrypcja "
                    f"potrwa kilka razy dłużej niż na GPU."
                ),
                alternatives=_alternatives(name),
                warnings=warnings,
            )

    return Recommendation(
        model="base",
        device="cpu",
        compute_type="int8",
        reason="Słaby sprzęt — bezpiecznym wyborem jest mały model base na CPU.",
        alternatives=_alternatives("base"),
        warnings=warnings,
    )


def _gpu_compute_types(generation: float):
    """Dobiera precyzję do generacji układu.

    Zwraca (pełna precyzja, wariant kwantyzowany, mnożnik zapotrzebowania
    na VRAM względem float16).

    Rdzenie tensorowe do float16 pojawiły się dopiero w Volcie (7.0). Na
    Pascalu (6.1) float16 liczy się nawet kilkadziesiąt razy wolniej niż
    float32, więc pełną precyzją jest tam float32 — za to int8 ma sprzętowe
    wsparcie (DP4A) i to on jest właściwym wyborem.
    """
    if generation >= 7.0:
        return "float16", "int8_float16", 1.0
    return "float32", "int8_float32", 1.8


def _cpu_fast_enough(model: str, threads: int) -> bool:
    """Odsiewa kombinacje, które na CPU trwałyby absurdalnie długo.

    Progi wynikają z pomiaru, nie z przeczucia. Na 2-minutowym nagraniu
    large-v3-turbo i small potrzebowały na procesorze praktycznie tyle samo
    czasu (58,4 s wobec 57,6 s przy 16 wątkach; 53,0 s wobec 43,3 s przy
    czterech). Skoro mniejszy model daje najwyżej kilkanaście procent zysku,
    a wyraźnie traci na jakości, nie ma powodu zsyłać na niego użytkownika
    tylko dlatego, że ma mało wątków.
    """
    if model == "large-v3":
        # Jedyny model, który naprawdę jest wielokrotnie wolniejszy.
        return threads >= 16
    return True


def _alternatives(chosen: str) -> List[str]:
    return [m[0] for m in MODELS if m[0] != chosen]


def fits(model: str, hw: Hardware, device: str) -> bool:
    """Czy dany model zmieści się na wskazanym urządzeniu (choćby w int8)."""
    entry = MODEL_BY_NAME.get(model)
    if entry is None:
        return False
    _n, _q, _vf, vram_int8, ram_cpu, _spd = entry
    if device == "cuda":
        gpu = hw.gpu
        return gpu is not None and vram_int8 <= max(gpu.vram_gb - VRAM_HEADROOM_GB, 0.0)
    return ram_cpu <= max((hw.ram_gb or 8.0) - RAM_HEADROOM_GB, 1.0)


def format_report(hw: Hardware, rec: Recommendation) -> str:
    """Czytelny raport diagnostyczny — używany w GUI i w konsoli."""
    lines = [
        "SYSTEM",
        f"  System       : {hw.os_name}",
        f"  Python       : {hw.python_version}",
        f"  Interpreter  : {hw.python_exe}",
        "",
        "SPRZĘT",
        f"  Procesor     : {hw.cpu_name}",
        f"  Rdzenie      : {hw.cpu_cores} fizycznych / {hw.cpu_threads} wątków",
        f"  RAM          : {hw.ram_gb:g} GB",
        f"  Dysk (wolne) : {hw.disk_free_gb:g} GB",
    ]
    if hw.gpus:
        for g in hw.gpus:
            generacja = (
                f", generacja {g.compute_capability:g}"
                if g.compute_capability
                else ""
            )
            lines.append(
                f"  GPU          : {g.name} — {g.vram_gb:g} GB VRAM, "
                f"sterownik {g.driver}, CUDA {g.cuda_runtime}{generacja}"
            )
    elif hw.nvidia_without_driver:
        lines.append(
            f"  GPU          : {hw.nvidia_without_driver} — wykryta, ale bez "
            f"sterownika NVIDIA (transkrypcja na CPU)"
        )
    else:
        lines.append("  GPU          : brak karty NVIDIA (transkrypcja na CPU)")
        for name in hw.adapters:
            lines.append(f"  Karta obrazu : {name}")

    lines += [
        "",
        "NARZĘDZIA",
        f"  ffmpeg       : {hw.ffmpeg or 'BRAK'}",
        f"  ffprobe      : {hw.ffprobe or 'BRAK'}",
        "",
        "REKOMENDACJA",
        f"  Model        : {rec.model}",
        f"  Urządzenie   : {rec.device}",
        f"  Precyzja     : {rec.compute_type}",
        f"  Uzasadnienie : {rec.reason}",
    ]
    for w in rec.warnings:
        lines.append(f"  UWAGA        : {w}")
    return "\n".join(lines)


if __name__ == "__main__":
    _hw = probe()
    print(format_report(_hw, recommend(_hw)))
