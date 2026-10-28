"""Teksty interfejsu w dwóch językach (wzór: Nutka 1.4.0, MATCODE).

Kod i teksty w kodzie zostają po polsku; `t("Transkrybuj")` zwraca
„Transcribe”, gdy wybrany jest angielski. Tabela `EN` niżej to jedyne
miejsce z tłumaczeniami — tekst, którego w niej nie ma, zostaje po polsku
(lepsze to niż pusty napis). Teksty ze znacznikami formatuje się PO
przetłumaczeniu: `t("Dodano {n} plik(ów) do kolejki.").format(n=…)` —
klucz i tłumaczenie muszą mieć te same znaczniki (pilnuje tego test).

Język: WHISPER_AUTOMAT_JEZYK (testy, zrzuty) > `jezyk` w ustawieniach
programu (pigułka EN/PL w nagłówku → `App._zmien_jezyk`) > język
interfejsu Windows (polski → pl, każdy inny → en).

Dotyczy obu wydań — kod jest wspólny. Hasło i opis wydania (core/wydanie.py)
też przechodzą przez `t()`.
"""

from __future__ import annotations

import ctypes
import os
import re
from typing import Optional

JEZYKI = ("pl", "en")
ZMIENNA = "WHISPER_AUTOMAT_JEZYK"
_jezyk = "pl"

#: Polski ma identyfikator języka podstawowego 0x15 (LANG_POLISH).
_LANG_POLISH = 0x15


def jezyk_systemu() -> str:
    """pl, gdy Windows mówi po polsku; inaczej en."""
    try:
        return "pl" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == _LANG_POLISH else "en"
    except (AttributeError, OSError):
        return "pl"


def ustaw(jezyk: Optional[str]) -> None:
    """Pusty/nieznany = język systemu."""
    global _jezyk
    _jezyk = jezyk if jezyk in JEZYKI else jezyk_systemu()


def ustaw_z_ustawien(jezyk_ustawien: str) -> None:
    """Ustawienie programu wygrywa z językiem systemu, ale nie ze zmienną
    środowiskową — ta służy testom i zrzutom okna."""
    if os.environ.get(ZMIENNA):
        return
    ustaw(jezyk_ustawien or None)


def jezyk() -> str:
    return _jezyk


def drugi_jezyk() -> str:
    """Kod języka, na który przełącza pigułka w nagłówku."""
    return "en" if _jezyk == "pl" else "pl"


def t(tekst: str) -> str:
    return EN.get(tekst, tekst) if _jezyk == "en" else tekst


def znaczniki(tekst: str) -> set:
    """Zbiór znaczników formatowania `{…}` w tekście — do testu słownika."""
    return set(re.findall(r"\{[^{}]*\}", tekst))


ustaw(os.environ.get(ZMIENNA))

EN = {
    # core/wydanie.py — hasło i opis wydania (tytuł okna, ekran powitalny, instalator)
    "transkrypcje offline":
        "offline transcription",
    "transkrypcje i nagrania spotkań":
        "transcription and meeting recording",
    "transkrypcja audio i wideo":
        "audio and video transcription",
    "Nagrywa spotkania, zamienia nagrania w tekst i rozpoznaje, kto mówi. Działa "
        "na Twoim komputerze, bez internetu i bez chmury.":
        "Records meetings, turns recordings into text and tells who is speaking. "
        "Works on your own computer, without internet and without the cloud.",
    "Nagrywa spotkania i zamienia nagrania w tekst, rozpoznając, kto mówi. "
        "Działa na komputerze, bez internetu.":
        "Records meetings and turns recordings into text, telling who is speaking. "
        "Works on the computer, without internet.",
    # okno (app.py), ekran powitalny, okno „Kto jest kim?”, listy z config.py i writers.py
    "Wykrywam sprzęt…": "Detecting hardware…",
    "Sprawdzam kartę graficzną…": "Checking the graphics card…",
    "Buduję interfejs…": "Building the interface…",
    "Uruchamianie…": "Starting…",
    "wersja {w}  ·  {wydawca}": "version {w}  ·  {wydawca}",
    "wersja {w}": "version {w}",
    "{wydawca}, wersja {w}": "{wydawca}, version {w}",
    "{gpu}: nieaktywna, liczy procesor":
        "{gpu}: inactive, running on the processor",
    "Procesor, brak karty NVIDIA": "Processor, no NVIDIA card",
    "Jak to działa?": "How it works?",
    "Postaw kawę autorowi": "Buy the author a coffee",
    "Program jeszcze pracuje. Przerwać pracę i zmienić język teraz?":
        "The app is still working. Stop it and switch the language now?",
    "Nie udało się uruchomić programu ponownie: {blad}":
        "Could not restart the app: {blad}",
    "Diagnostyka": "Diagnostics",
    "Sprawdź aktualizacje": "Check for updates",
    "Zgłoś problem": "Report a problem",
    "Kontakt": "Contact",
    "Licencje": "Licenses",
    "Zaktualizuj teraz": "Update now",
    "Co nowego": "What's new",
    "Pomiń tę wersję": "Skip this version",
    "Później": "Later",
    "Przeciągnij tutaj nagrania lub folder": "Drag recordings or a folder here",
    "Kliknij, aby wybrać nagrania": "Click to choose recordings",
    "albo kliknij i wybierz z dysku: audio lub wideo":
        "or click and pick from disk: audio or video",
    "audio lub wideo": "audio or video",
    "MP4, MKV, MOV, MP3, WAV, M4A i inne. Wiele plików naraz.":
        "MP4, MKV, MOV, MP3, WAV, M4A and more. Several files at once.",
    "Nagrywaj spotkanie": "Record meeting",
    "Mikrofon": "Microphone",
    "Dźwięk spotkania": "Meeting audio",
    "Zmień…": "Change…",
    "domyślny": "default",
    "domyślne urządzenie odtwarzania": "default playback device",
    "Nagrywam spotkanie: {plik}": "Recording meeting: {plik}",
    "Nagrywam. Poinformuj uczestników, że spotkanie jest nagrywane.":
        "Recording. Let the participants know the meeting is being recorded.",
    "Zapisuję…": "Saving…",
    "Kończę nagranie…": "Finishing the recording…",
    "Zatrzymaj nagranie": "Stop recording",
    "UWAGA (nagrywanie): {tekst}": "NOTE (recording): {tekst}",
    "Nagranie zapisane: {plik} ({czas}).": "Recording saved: {plik} ({czas}).",
    "  {tor}: luki {luki}, korekty dryfu {korekty}, odrzucone próbki "
        "{odrzucone}, wyprzedzenia {wyprzedzenia}":
        "  {tor}: gaps {luki}, drift corrections {korekty}, dropped samples "
        "{odrzucone}, early packets {wyprzedzenia}",
    "Nagranie zapisane ({czas}). Czeka w kolejce.":
        "Recording saved ({czas}). Waiting in the queue.",
    "Nagranie zapisane ({czas}). Transkrypcja ruszy po bieżącej pracy.":
        "Recording saved ({czas}). Transcription starts after the current job.",
    "Nagranie zapisane ({czas}). Zaczynam transkrypcję…":
        "Recording saved ({czas}). Starting transcription…",
    "{domyslne} — teraz: {co}": "{domyslne} — now: {co}",
    "domyślne w Windows": "Windows default",
    "używa Teams": "used by Teams",
    "{nazwa} (odłączone — pójdzie domyślne)": "{nazwa} (unplugged — the default will be used)",
    "Nagranie przez zestaw słuchawkowy: {zestaw}": "Recording through the headset: {zestaw}",
    # core/urzadzenia.py — co to za urządzenie
    "USB": "USB",
    "Bluetooth": "Bluetooth",
    "monitor / telewizor (HDMI)": "monitor / TV (HDMI)",
    "zestaw słuchawkowy": "headset",
    "mikrofon zestawu słuchawkowego": "headset microphone",
    "słuchawki": "headphones",
    "mikrofon słuchawek": "headphones microphone",
    "głośniki komputera": "computer speakers",
    "głośniki": "speakers",
    "wbudowany": "built-in",
    "mikrofon wbudowany": "built-in microphone",
    "mikrofon": "microphone",
    "wejście liniowe": "line in",
    "wyjście liniowe": "line out",
    "wyjście cyfrowe": "digital output",
    "Poprzednie nagrywanie nie zostało poprawnie zakończone:\n\n{pliki}\n\nOdzyskać "
        "nagranie i dodać je do kolejki?":
        "The previous recording did not finish properly:\n\n{pliki}\n\nRecover the "
        "recording and add it to the queue?",
    "Niedokończone nagranie zostaje w folderze Nagrania bez zmian.":
        "The unfinished recording stays in the Recordings folder untouched.",
    "Odzyskuję nagranie…": "Recovering the recording…",
    "Nie udało się odzyskać {plik}: {blad}": "Could not recover {plik}: {blad}",
    "Odzyskanie nagrania nie powiodło się.": "Recovering the recording failed.",
    "Odzyskano niedokończone nagranie: {plik}":
        "Recovered the unfinished recording: {plik}",
    "Odzyskane nagranie czeka w kolejce.":
        "The recovered recording is waiting in the queue.",
    "Kolejka": "Queue",
    "Usuń zaznaczone": "Remove selected",
    "Wyczyść": "Clear",
    "Plik": "File",
    "Długość": "Length",
    "Status": "Status",
    "Usuń z kolejki": "Remove from queue",
    "Wyczyść kolejkę": "Clear the queue",
    ", łącznie {czas}": ", {czas} in total",
    "Ustawienia": "Settings",
    "Język nagrania": "Recording language",
    "Zapisz jako": "Save as",
    "Folder wyników": "Output folder",
    "Zapisuj obok pliku źródłowego": "Save next to the source file",
    "Mówcy": "Speakers",
    "Rozpoznaj, kto co powiedział": "Recognise who said what",
    "Ile osób:": "How many people:",
    "Nagrywanie": "Recording",
    "Transkrybuj od razu po zatrzymaniu": "Transcribe right after stopping",
    "Gotowy.": "Ready.",
    "Otwórz wyniki": "Open results",
    "Przerwij": "Cancel",
    "Transkrybuj": "Transcribe",
    "▸  Pokaż dziennik": "▸  Show log",
    "▾  Ukryj dziennik": "▾  Hide log",
    "{app} — gotowy.": "{app} — ready.",
    "Rekomendacja sprzętowa: {model} ({urzadzenie}, {precyzja})":
        "Hardware recommendation: {model} ({urzadzenie}, {precyzja})",
    "UWAGA: {tekst}": "NOTE: {tekst}",
    "Brak tkinterdnd2 — przeciąganie plików wyłączone, użyj kliknięcia w pole "
        "powyżej.":
        "tkinterdnd2 is missing — drag and drop is off, click the field above "
        "instead.",
    "Wydłuża pracę mniej więcej o długość nagrania. Dokładna liczba osób dzieli "
        "wyraźnie lepiej niż 0 („zgadnij”).":
        "Adds roughly the length of the recording to the job. An exact number of "
        "people separates voices much better than 0 (“guess”).",
    "Wybierz pliki audio lub wideo": "Choose audio or video files",
    "Pliki audio i wideo": "Audio and video files",
    "Wszystkie pliki": "All files",
    "Przeciągnięte pliki nie zawierają obsługiwanych formatów.":
        "The dropped files contain no supported formats.",
    "oczekuje": "waiting",
    "Dodano {n} plik(ów) do kolejki.": "Added {n} file(s) to the queue.",
    "nieczytelny: {blad}": "unreadable: {blad}",
    "Trwa nagrywanie — transkrypcja ruszy po jego zatrzymaniu.":
        "Recording in progress — transcription starts once it stops.",
    "Najpierw dodaj pliki — przeciągnij je w pole u góry.":
        "Add files first — drag them into the field at the top.",
    "Zaznacz przynajmniej jeden format zapisu.":
        "Select at least one output format.",
    "Wszystkie pliki w kolejce są już przetworzone. Przetworzyć je jeszcze "
        "raz?\n\nNowe pliki wyników dostaną numer w nazwie, stare zostaną.":
        "All files in the queue are already done. Process them again?\n\nNew output "
        "files get a number in the name; the old ones stay.",
    "Gotowe pliki w kolejce: {n}. Przetworzyć je jeszcze raz razem z "
        "nowymi?\n\n„Nie” przetworzy tylko te, które jeszcze czekają.":
        "Finished files in the queue: {n}. Process them again together with the new "
        "ones?\n\n“No” processes only the ones still waiting.",
    "BŁĄD KRYTYCZNY: {blad}": "CRITICAL ERROR: {blad}",
    "Pierwsze uruchomienie: pobieram model rozpoznawania mowy {model} (ok. "
        "{rozmiar}). To jednorazowe — potem program działa bez internetu, a nagrania "
        "nigdy nie opuszczają komputera.":
        "First start: downloading the speech recognition model {model} (about "
        "{rozmiar}). This happens once — afterwards the app works offline and "
        "recordings never leave the computer.",
    "Pobieram model {model}…": "Downloading model {model}…",
    "model {model}": "model {model}",
    "Pobieranie przerwane — następna próba ruszy od tego miejsca.":
        "Download cancelled — the next attempt resumes from here.",
    "Nie udało się pobrać modelu.": "Could not download the model.",
    "BŁĄD: {blad}": "ERROR: {blad}",
    "Model {model} gotowy. Możesz przeciągać nagrania.":
        "Model {model} ready. You can drag in recordings.",
    "Sprawdzam, czy jest nowa wersja…": "Checking for a new version…",
    "Nie udało się sprawdzić aktualizacji.": "Could not check for updates.",
    "Nie udało się sprawdzić, czy jest nowa wersja.\n\n{blad}":
        "Could not check whether there is a new version.\n\n{blad}",
    "Sprawdzanie aktualizacji nie powiodło się: {blad}":
        "Update check failed: {blad}",
    "Masz najnowszą wersję ({w}).": "You have the latest version ({w}).",
    "Masz najnowszą wersję {app} ({w}).":
        "You have the latest version of {app} ({w}).",
    "Dostępna jest wersja {w} (pominięta na Twoje życzenie).":
        "Version {w} is available (skipped at your request).",
    "Dostępna nowa wersja {app} {nowa}   (masz {obecna})":
        "New version of {app} available: {nowa}   (you have {obecna})",
    "Dostępna nowa wersja: {w}. Kliknij „Zaktualizuj teraz”.":
        "New version available: {w}. Click “Update now”.",
    "Wersja {w} pominięta — „Sprawdź aktualizacje” pokaże ją ponownie.":
        "Version {w} skipped — “Check for updates” will show it again.",
    "Co nowego w wersji {w}": "What's new in version {w}",
    "Program jeszcze pracuje. Zaktualizuj, gdy skończy — pasek z nową wersją "
        "zostanie na miejscu.":
        "The app is still working. Update when it finishes — the new-version bar "
        "stays in place.",
    "Wersji {w} program nie zainstaluje sam: {powod}. Otwieram stronę wydania.":
        "The app will not install version {w} by itself: {powod}. Opening the "
        "release page.",
    "wydanie nie ma sprawdzalnego instalatora":
        "the release has no verifiable installer",
    "Pobieram wersję {w}…": "Downloading version {w}…",
    "wersję {w}": "version {w}",
    "Pobieranie aktualizacji przerwane.": "Update download cancelled.",
    "Nie udało się pobrać aktualizacji.": "Could not download the update.",
    "Instaluję nową wersję — program uruchomi się ponownie sam.":
        "Installing the new version — the app will restart by itself.",
    "BŁĄD: nie udało się uruchomić instalatora: {blad}":
        "ERROR: could not start the installer: {blad}",
    "Nie udało się uruchomić instalatora:\n{blad}\n\nPlik: {plik}":
        "Could not start the installer:\n{blad}\n\nFile: {plik}",
    "Przerywam po bieżącym fragmencie…": "Stopping after the current segment…",
    "plik {i} z {n}": "file {i} of {n}",
    "przetwarzanie…": "processing…",
    "Nie udało się otworzyć okna mówców: {blad}":
        "Could not open the speakers window: {blad}",
    "BŁĄD nagrywania: {blad}": "Recording ERROR: {blad}",
    "Nagrywanie przerwane.": "Recording stopped.",
    "Nagranie odrzucone — nic nie zostało zapisane.":
        "Recording discarded — nothing was saved.",
    "✓  gotowe, {x:.1f}× szybciej": "✓  done, {x:.1f}× faster",
    "błąd": "error",
    "Przerwano. Ukończono {pliki}.": "Cancelled. Finished {pliki}.",
    "Zakończono: {ok} OK, {zle} z błędem.":
        "Finished: {ok} OK, {zle} with errors.",
    "Gotowe — przetworzono {pliki}.": "Done — processed {pliki}.",
    "Transkrypcja zakończona — {pliki} w folderze wyników.":
        "Transcription finished — {pliki} in the output folder.",
    "Zakończono z błędami: {ok} OK, {zle} z błędem — szczegóły w dzienniku.":
        "Finished with errors: {ok} OK, {zle} failed — see the log.",
    "OK": "OK",
    "Gdzie zapisywać transkrypcje?": "Where to save transcripts?",
    "Folder nie istnieje: {folder}": "The folder does not exist: {folder}",
    "Jak to działa — {app}": "How it works — {app}",
    "Trzy kroki": "Three steps",
    "Przeciągnij nagrania albo cały folder w pole po lewej. Audio i wideo, ile "
        "chcesz naraz.":
        "Drag recordings or a whole folder into the field on the left. Audio and "
        "video, as many as you like.",
    "Zaznacz, w jakiej postaci zapisać tekst: z czasem, sam tekst albo napisy do "
        "filmu.":
        "Choose how to save the text: with timestamps, plain text or subtitles for a "
        "video.",
    "Kliknij Transkrybuj. Gotowe pliki trafią do folderu wyników.":
        "Click Transcribe. The finished files land in the output folder.",
    "Co jeszcze potrafi": "What else it does",
    "Nagrywa spotkania. „Nagrywaj spotkanie” zbiera Twój mikrofon i dźwięk z "
        "głośników (np. Teams) do jednego pliku, a po zatrzymaniu od razu go "
        "transkrybuje. Najlepiej w słuchawkach.":
        "Records meetings. “Record meeting” captures your microphone and the speaker "
        "audio (e.g. Teams) into one file and transcribes it as soon as you stop. "
        "Headphones work best.",
    "Podpisuje, kto mówi. Włącz „Rozpoznaj, kto co powiedział” i wpisz liczbę "
        "osób — po nagraniu nadasz im imiona.":
        "Labels who is speaking. Turn on “Recognise who said what” and enter the "
        "number of people — you name them after the recording.",
    "Rozpoznaje mowę na Twoim komputerze — nagrania nigdy nie trafiają do "
        "internetu.":
        "Recognises speech on your own computer — recordings never go to the "
        "internet.",
    "Plik z kolejki usuniesz krzyżykiem przy nim, klawiszem Delete albo prawym "
        "przyciskiem myszy.":
        "Remove a file from the queue with the cross next to it, the Delete key or "
        "the right mouse button.",
    "Zamknij": "Close",
    "Nie znaleziono pliku {plik}. W wersji uruchamianej z kodu tworzy go "
        "polecenie: python tools\\licencje.py":
        "File {plik} not found. When running from source it is created by: python "
        "tools\\licencje.py",
    "Nie udało się otworzyć {plik}:\n{blad}": "Could not open {plik}:\n{blad}",
    "Sprawdzam środowisko…": "Checking the environment…",
    "Diagnostyka środowiska": "Environment diagnostics",
    "Trwa nagrywanie. Zatrzymać je, zapisać nagranie i zamknąć program?":
        "Recording in progress. Stop it, save the recording and close the app?",
    "Program jeszcze pracuje. Na pewno zamknąć?":
        "The app is still working. Close anyway?",
    "1 plik": "1 file",
    "{n} pliki": "{n} files",
    "{n} plików": "{n} files",
    "pozostało mniej niż minuta": "less than a minute left",
    "pozostało ok. {min} min": "about {min} min left",
    "pozostało ok. {godz} godz. {min:02d} min":
        "about {godz} h {min:02d} min left",
    "Kopiuj do schowka": "Copy to clipboard",
    "Kto jest kim?": "Who is who?",
    "{plik} — rozpoznane głosy: {n}": "{plik} — voices recognised: {n}",
    "Posłuchaj próbki i wpisz imię. Puste pole zostawia oznaczenie MÓWCA 1, "
        "MÓWCA 2…":
        "Listen to a sample and type a name. An empty field keeps the label SPEAKER "
        "1, SPEAKER 2…",
    "MÓWCA {n}": "SPEAKER {n}",
    "▶  Posłuchaj  ({czas})": "▶  Listen  ({czas})",
    "Pomiń": "Skip",
    "⏹  Zatrzymaj": "⏹  Stop",
    "Zastosuj": "Apply",
    "Brak próbki dla MÓWCY {n}.": "No sample for SPEAKER {n}.",
    "Nie udało się wyciąć próbki: {blad}": "Could not cut the sample: {blad}",
    "brak metody odtwarzania": "no playback method",
    "Odtwarzam MÓWCĘ {n} — fragment od {czas}, {s:.0f} s. Nie słyszysz? Sprawdź "
        "głośność programu {app} w mikserze Windows.":
        "Playing SPEAKER {n} — from {czas}, {s:.0f} s. Can't hear it? Check the "
        "volume of {app} in the Windows mixer.",
    "Nie udało się odtworzyć próbki: {blad}":
        "Could not play the sample: {blad}",
    "nie znaleziono ffmpeg": "ffmpeg not found",
    "nie ma już pliku {plik}": "file {plik} no longer exists",
    "ffmpeg zwrócił {kod}": "ffmpeg returned {kod}",
    "ffmpeg zapisał pusty plik": "ffmpeg wrote an empty file",
    "MCI nie otworzyło pliku próbki": "MCI did not open the sample file",
    "MCI nie rozpoczęło odtwarzania": "MCI did not start playback",
    "polski": "Polish",
    "angielski": "English",
    "niemiecki": "German",
    "ukraiński": "Ukrainian",
    "czeski": "Czech",
    "słowacki": "Slovak",
    "francuski": "French",
    "hiszpański": "Spanish",
    "włoski": "Italian",
    "rosyjski": "Russian",
    "wykryj automatycznie": "detect automatically",
    "TXT ze znacznikami czasu": "TXT with timestamps",
    "TXT — sam tekst": "TXT — plain text",
    "SRT — napisy": "SRT — subtitles",
    "VTT — napisy WebVTT": "VTT — WebVTT subtitles",
    "JSON — pełne dane": "JSON — full data",
    # core: doctor, probe, writers, diarization, pipeline
    "{wersja} to za stara wersja": "{wersja} is too old",
    "Zainstaluj Pythona {wersja} lub nowszego.":
        "Install Python {wersja} or newer.",
    "nie znaleziono {program} w PATH": "{program} not found in PATH",
    "Uruchom setup.bat albo: winget install Gyan.FFmpeg":
        "Run setup.bat or: winget install Gyan.FFmpeg",
    "wersja {wersja}": "version {wersja}",
    "nie zainstalowano silnika transkrypcji":
        "transcription engine not installed",
    "Uruchom setup.bat.": "Run setup.bat.",
    "{karta}, {vram:g} GB VRAM, sterownik {sterownik}":
        "{karta}, {vram:g} GB VRAM, driver {sterownik}",
    "{opis} — wykryta, ale silnik jej nie widzi (brak bibliotek cuBLAS/cuDNN)":
        "{opis} — detected, but the engine cannot see it (cuBLAS/cuDNN libraries "
        "missing)",
    "Uruchom setup.bat — doinstaluje nvidia-cublas-cu12 i nvidia-cudnn-cu12. Bez "
        "tego transkrypcja pójdzie na CPU.":
        "Run setup.bat — it installs nvidia-cublas-cu12 and nvidia-cudnn-cu12. "
        "Without them transcription runs on the CPU.",
    "{karta} — karta jest, ale nie ma sterownika":
        "{karta} — the card is there, but it has no driver",
    "Zainstaluj sterownik ze strony nvidia.com/drivers (albo przez GeForce "
        "Experience), potem uruchom setup.bat ponownie. Transkrypcja przyspieszy "
        "kilkukrotnie.":
        "Install the driver from nvidia.com/drivers (or through GeForce Experience), "
        "then run setup.bat again. Transcription will get several times faster.",
    "brak karty NVIDIA — transkrypcja na CPU (kilka razy wolniej)":
        "no NVIDIA card — transcription on the CPU (several times slower)",
    "tkinterdnd2 zainstalowane": "tkinterdnd2 installed",
    "brak tkinterdnd2 — pliki trzeba wybierać przyciskiem":
        "tkinterdnd2 missing — files have to be picked with the button",
    "Rozpoznawanie mówców": "Speaker recognition",
    "brak biblioteki sherpa-onnx — funkcja będzie niedostępna":
        "sherpa-onnx library missing — the feature will be unavailable",
    "Uruchom setup.bat albo: pip install sherpa-onnx":
        "Run setup.bat or: pip install sherpa-onnx",
    "sherpa-onnx {wersja}, modele na miejscu":
        "sherpa-onnx {wersja}, models in place",
    "sherpa-onnx {wersja}, brak modeli głosów":
        "sherpa-onnx {wersja}, voice models missing",
    "Dwa modele (łącznie 44 MB) pobiorą się przy pierwszym użyciu funkcji — "
        "potrzebny dostęp do github.com.":
        "Two models (44 MB in total) will be downloaded the first time the feature "
        "is used — access to github.com is required.",
    "Nagrywanie spotkań": "Meeting recording",
    "brak biblioteki PyAudioWPatch — przycisk nagrywania nie zadziała":
        "PyAudioWPatch library missing — the record button will not work",
    "Uruchom setup.bat albo: pip install PyAudioWPatch":
        "Run setup.bat or: pip install PyAudioWPatch",
    "PyAudioWPatch {wersja}; mikrofon: {mikrofon}; dźwięk systemowy z: {wyjscie}":
        "PyAudioWPatch {wersja}; microphone: {mikrofon}; system audio from: {wyjscie}",
    "brak": "none",
    " (bez loopbacku)": " (no loopback)",
    "Podłącz mikrofon albo słuchawki.": "Connect a microphone or headphones.",
    "nie udało się odczytać urządzeń WASAPI: {blad}":
        "could not read WASAPI devices: {blad}",
    "Sprawdź w Ustawieniach Windows, czy urządzenia dźwięku działają.":
        "Check in Windows Settings that the audio devices work.",
    " (w {katalog})": " (in {katalog})",
    "Pobrane modele": "Downloaded models",
    "brak modeli w pamięci podręcznej": "no models in the cache",
    "Model {model} (ok. {rozmiar}) program zaproponuje pobrać przy uruchomieniu "
        "albo ściągnie go przed pierwszą transkrypcją.":
        "The program will offer to download the {model} model (about {rozmiar}) at "
        "startup, or fetch it before the first transcription.",
    "Pobieranie modelu": "Model download",
    "Bez tego model się nie pobierze. W sieci firmowej zwykle wystarczy dostęp "
        "do huggingface.co; alternatywnie skopiuj folder "
        "%LOCALAPPDATA%\\{wydanie}\\models z komputera, na którym program już działa, "
        "albo zainstaluj wersję offline (z modelem w instalatorze).":
        "Without this the model cannot be downloaded. On a company network access to "
        "huggingface.co is usually enough; alternatively copy the "
        "%LOCALAPPDATA%\\{wydanie}\\models folder from a computer where the program "
        "already works, or install the offline version (with the model in the "
        "installer).",
    "Miejsce na dysku": "Disk space",
    "tylko {gb:g} GB wolnego": "only {gb:g} GB free",
    "Zwolnij co najmniej 5 GB — modele i pliki tymczasowe potrzebują miejsca.":
        "Free up at least 5 GB — models and temporary files need room.",
    "{gb:g} GB wolnego": "{gb:g} GB free",
    "DIAGNOSTYKA ŚRODOWISKA": "ENVIRONMENT DIAGNOSTICS",
    "Środowisko gotowe do pracy.": "Environment ready.",
    "Środowisko NIE jest kompletne — zobacz pozycje [X] powyżej.":
        "Environment is NOT complete — see the [X] items above.",
    "Nie znaleziono ffmpeg — bez niego nie da się czytać wideo.":
        "ffmpeg not found — without it video cannot be read.",
    "W komputerze jest karta „{karta}”, ale nie odpowiada nvidia-smi — "
        "najpewniej brakuje sterownika NVIDIA. Po jego instalacji uruchom setup.bat "
        "ponownie, żeby przejść na GPU.":
        "The computer has a “{karta}” card, but nvidia-smi does not respond — most "
        "likely the NVIDIA driver is missing. After installing it, run setup.bat "
        "again to switch to the GPU.",
    "Sterownik karty {karta} ma wersję {sterownik}, a biblioteki CUDA 12 "
        "wymagają co najmniej {minimum:g}. Zaktualizuj sterownik ze strony "
        "nvidia.com/drivers i uruchom setup.bat ponownie — do tego czasu liczę na "
        "procesorze.":
        "The {karta} driver is version {sterownik}, but the CUDA 12 libraries need "
        "at least {minimum:g}. Update the driver from nvidia.com/drivers and run "
        "setup.bat again — until then the CPU does the work.",
    "{karta} to układ starszej generacji (compute capability {generacja:g}) — "
        "brakuje mu sprzętowego wsparcia dla obliczeń int8, więc przewaga nad "
        "procesorem będzie niewielka.":
        "{karta} is an older-generation chip (compute capability {generacja:g}) — it "
        "lacks hardware support for int8 math, so the advantage over the CPU will be "
        "small.",
    "{karta} ma {vram:g} GB VRAM — model {model} mieści się w pełnej precyzji "
        "{precyzja}.":
        "{karta} has {vram:g} GB VRAM — the {model} model fits in full {precyzja} "
        "precision.",
    "{karta} ma {vram:g} GB VRAM — model {model} w kwantyzacji {precyzja} "
        "zmieści się z zapasem i będzie wielokrotnie szybszy niż CPU.":
        "{karta} has {vram:g} GB VRAM — the {model} model quantized to {precyzja} "
        "fits with room to spare and will be many times faster than the CPU.",
    "{karta} ma za mało VRAM ({vram:g} GB) nawet dla modelu tiny — przechodzę na "
        "CPU.":
        "{karta} has too little VRAM ({vram:g} GB) even for the tiny model — "
        "switching to the CPU.",
    "Brak karty NVIDIA. Przy {ram:g} GB RAM i {watki} wątkach model {model} "
        "(int8) to rozsądny kompromis — transkrypcja potrwa kilka razy dłużej niż na "
        "GPU.":
        "No NVIDIA card. With {ram:g} GB RAM and {watki} threads the {model} model "
        "(int8) is a reasonable compromise — transcription will take several times "
        "longer than on a GPU.",
    "Słaby sprzęt — bezpiecznym wyborem jest mały model base na CPU.":
        "Weak hardware — the small base model on the CPU is the safe choice.",
    "SPRZĘT": "HARDWARE",
    "Procesor": "Processor",
    "Rdzenie": "Cores",
    "{fizyczne} fizycznych / {watki} wątków":
        "{fizyczne} physical / {watki} threads",
    "Dysk (wolne)": "Disk (free)",
    ", generacja {generacja:g}": ", generation {generacja:g}",
    "{karta} — {vram:g} GB VRAM, sterownik {sterownik}, CUDA {cuda}{generacja}":
        "{karta} — {vram:g} GB VRAM, driver {sterownik}, CUDA {cuda}{generacja}",
    "{karta} — wykryta, ale bez sterownika NVIDIA (transkrypcja na CPU)":
        "{karta} — detected, but without an NVIDIA driver (transcription on the CPU)",
    "brak karty NVIDIA (transkrypcja na CPU)":
        "no NVIDIA card (transcription on the CPU)",
    "Karta obrazu": "Video card",
    "NARZĘDZIA": "TOOLS",
    "BRAK": "MISSING",
    "REKOMENDACJA": "RECOMMENDATION",
    "Urządzenie": "Device",
    "Precyzja": "Precision",
    "Uzasadnienie": "Reason",
    "UWAGA": "WARNING",
    "Pobieram model odcisków głosu (38 MB)…":
        "Downloading the voice fingerprint model (38 MB)…",
    "Pobieram model segmentacji (6 MB)…":
        "Downloading the segmentation model (6 MB)…",
    "Nie udało się pobrać modeli rozpoznawania mówców.":
        "Could not download the speaker recognition models.",
    "Pobieranie modeli zakończyło się niekompletnie.":
        "The model download finished incomplete.",
    "Modele rozpoznawania mówców gotowe.": "Speaker recognition models ready.",
    "Pobrany plik {plik} różni się od oczekiwanego (niezgodna suma kontrolna). "
        "Został usunięty — spróbuj ponownie.":
        "The downloaded file {plik} differs from the expected one (checksum "
        "mismatch). It has been deleted — try again.",
    "Oczekiwano 16-bitowego WAV mono — plik nie przeszedł konwersji.":
        "Expected 16-bit mono WAV — the file was not converted.",
    "Brak biblioteki sherpa-onnx. Uruchom setup.bat, żeby ją doinstalować.":
        "The sherpa-onnx library is missing. Run setup.bat to install it.",
    "Błędna konfiguracja rozpoznawania mówców.":
        "Invalid speaker recognition configuration.",
    "Nie udało się uruchomić: {blad}": "Could not start: {blad}",
    "Oczekiwano {oczekiwane} Hz, plik ma {jest} Hz.":
        "Expected {oczekiwane} Hz, the file has {jest} Hz.",
    "Rozpoznawanie mówców nie powiodło się: {blad}":
        "Speaker recognition failed: {blad}",
    "Pominięto {n} grup(y) o łącznym czasie mowy poniżej {prog:g} s — to zwykle "
        "nakładające się głosy albo szum, nie osobny mówca.":
        "Skipped {n} group(s) with less than {prog:g} s of speech in total — usually "
        "overlapping voices or noise, not a separate speaker.",
    "Brak plików do przetworzenia.": "No files to process.",
    "Model dobrany automatycznie: {model} ({powod})":
        "Model chosen automatically: {model} ({powod})",
    "Pobieranie modelu przerwane. Następna próba ruszy od miejsca, w którym "
        "stanęło.":
        "Model download interrupted. The next attempt will resume where it stopped.",
    "Modelu {model} nie udało się pobrać. Używam modelu {zapasowy}, który jest "
        "już na dysku.":
        "Could not download the {model} model. Using the {zapasowy} model, which is "
        "already on disk.",
    "Silnik: {silnik}": "Engine: {silnik}",
    "Przerwano — pozostałe pliki pominięte.":
        "Cancelled — the remaining files were skipped.",
    "Pominięto {plik} — usunięty z kolejki.":
        "Skipped {plik} — removed from the queue.",
    "{n} os.": "{n} people",
    "liczba nieznana": "number unknown",
    "[{i}/{n}] Rozpoznaję mówców ({ile})…":
        "[{i}/{n}] Recognizing speakers ({ile})…",
    "Rozpoznawanie mówców: {opis}": "Speaker recognition: {opis}",
    "szukam {n} różnych głosów.": "looking for {n} different voices.",
    "liczba osób nie podana — algorytm zgaduje.":
        "number of people not given — the algorithm guesses.",
    "Nie rozpoznano mówców: {blad}": "Speakers not recognized: {blad}",
    "Transkrypcja zostanie zapisana bez podziału na osoby.":
        "The transcription will be saved without splitting by person.",
    "Nie wykryto wyraźnie oddzielonych głosów.":
        "No clearly separated voices detected.",
    "Segmenty podzielone tam, gdzie zmieniał się mówca: {przed} -> {po}.":
        "Segments split where the speaker changed: {przed} -> {po}.",
    "Rozpoznano {glosy} głos(ów) w {odcinki} odcinkach.":
        "Recognized {glosy} voice(s) in {odcinki} turns.",
    "UWAGA: podano {podano} osób, a rozdzieliły się {rozpoznano}. Jeśli to nie "
        "zgadza się z nagraniem, popraw liczbę osób i powtórz.":
        "WARNING: {podano} people were given, but {rozpoznano} were separated. If "
        "this does not match the recording, correct the number of people and run "
        "again.",
    "Tekst trafił do {n} osób — pozostałe mówiły zbyt krótko, żeby wygrać "
        "jakikolwiek fragment.":
        "Text went to {n} people — the others spoke too briefly to win any fragment.",
    "Nie udało się zapytać o imiona: {blad}": "Could not ask for names: {blad}",
    "Podpisano mówców: {imiona}": "Speakers named: {imiona}",
    "[{i}/{n}] Analizuję {plik}…": "[{i}/{n}] Analyzing {plik}…",
    "{plik}: wideo, {czas} — wyciągam ścieżkę audio.":
        "{plik}: video, {czas} — extracting the audio track.",
    "[{i}/{n}] Wyciągam audio…": "[{i}/{n}] Extracting audio…",
    "Przerwano przed transkrypcją.": "Cancelled before transcription.",
    "[{i}/{n}] Transkrybuję {plik}…": "[{i}/{n}] Transcribing {plik}…",
    "{plik}: gotowe — {podsumowanie}": "{plik}: done — {podsumowanie}",
    "{plik}: przerwano.": "{plik}: cancelled.",
    "BŁĄD ({plik}): {blad}": "ERROR ({plik}): {blad}",
    "nieznany": "unknown",
    # core: update, download, nagrywanie, engine, media, network; __main__
    "Wydanie nie zawiera instalatora.":
        "The release does not include an installer.",
    "Adres instalatora nie prowadzi do strony wydań tego programu.":
        "The installer address does not point to this program's releases page.",
    "Wpis o wydaniu nie wskazuje instalatora tego programu.":
        "The release entry does not point to an installer of this program.",
    "Wydanie nie ma pliku podpisu (.podpis), więc program nie zainstaluje go sam.":
        "The release has no signature file (.podpis), so the program will not "
        "install it on its own.",
    "Suma instalatora policzona przez GitHub różni się od podpisanej przez "
        "autora.":
        "The installer checksum computed by GitHub differs from the one signed by "
        "the author.",
    "wersji {wersja}": "version {wersja}",
    "Wydanie nie ma sumy kontrolnej instalatora.":
        "The release has no installer checksum.",
    "Program nie ma klucza do sprawdzania podpisów wydań.":
        "The program has no key to verify release signatures.",
    "Program nie zainstaluje go sam — pobierz je ze strony wydania.":
        "The program will not install it on its own — download it from the release "
        "page.",
    "GitHub chwilowo ograniczył liczbę zapytań. Spróbuj za godzinę.":
        "GitHub has temporarily limited the number of requests. Try again in an hour.",
    "Plik {plik} w folderze aktualizacji ma zły format.":
        "The file {plik} in the updates folder has a wrong format.",
    "GitHub odpowiedział błędem HTTP {kod}.":
        "GitHub responded with HTTP error {kod}.",
    "To wydanie nie ma instalatora ze sprawdzalną sumą kontrolną.":
        "This release has no installer with a verifiable checksum.",
    "Nie udało się odczytać {plik} z folderu aktualizacji.":
        "Could not read {plik} from the updates folder.",
    "Sprawdzam sumy kontrolne…": "Verifying checksums…",
    "pliku": "the file",
    "Pobieram {co} — {pobrane:.0f} z {wszystkie:.0f} MB":
        "Downloading {co} — {pobrane:.0f} of {wszystkie:.0f} MB",
    "połączenie zakończone przed końcem pliku":
        "connection closed before the end of the file",
    "Pobieranie przerwane przez użytkownika.":
        "Download cancelled by the user.",
    "Model {model} gotowy — kolejne uruchomienia nie potrzebują internetu.":
        "Model {model} is ready — further runs do not need the internet.",
    "Repozytorium {repo} nie zawiera pliku model.bin — serwer zwrócił "
        "nieoczekiwaną odpowiedź.":
        "Repository {repo} does not contain model.bin — the server returned an "
        "unexpected response.",
    "Za mało miejsca na dysku: model potrzebuje {brakuje:.1f} GB, wolne jest "
        "{wolne:.1f} GB ({dysk}).":
        "Not enough disk space: the model needs {brakuje:.1f} GB, {wolne:.1f} GB is "
        "free ({dysk}).",
    "Pobieram model {model} ({gb:.2f} GB) z {serwer}…":
        "Downloading model {model} ({gb:.2f} GB) from {serwer}…",
    "Pobrany plik {plik} różni się od oryginału (niezgodna suma kontrolna). "
        "Został usunięty — spróbuj ponownie.":
        "The downloaded file {plik} differs from the original (checksum mismatch). "
        "It has been deleted — try again.",
    "Skopiowany plik {plik} różni się od oryginału (niezgodna suma kontrolna). "
        "Został usunięty — spróbuj ponownie.":
        "The copied file {plik} differs from the original (checksum mismatch). It "
        "has been deleted — try again.",
    "Serwer przysłał więcej danych niż zapowiadał ({plik}). Spróbuj ponownie za "
        "chwilę.":
        "The server sent more data than announced ({plik}). Try again in a moment.",
    "Serwer odmówił dostępu (HTTP {kod}). Zwykle znaczy to, że huggingface.co "
        "blokuje firewall albo filtr treści w sieci. W takiej sieci użyj wersji "
        "instalatora z modelem w środku („offline”).":
        "The server refused access (HTTP {kod}). This usually means a firewall or a "
        "content filter in the network is blocking huggingface.co. In such a "
        "network, use the installer that includes the model (“offline”).",
    "Nie udało się pobrać modelu {model}.": "Could not download model {model}.",
    "Nieznany model: {model}": "Unknown model: {model}",
    "zostało ok. {n} min": "about {n} min left",
    "Plik {plik} różni się od oryginału (niezgodna suma kontrolna). Został "
        "usunięty — spróbuj pobrać ponownie.":
        "The file {plik} differs from the original (checksum mismatch). It has been "
        "deleted — try downloading again.",
    "Nie udało się pobrać {co}.": "Could not download {co}.",
    "zostało ok. {n} s": "about {n} s left",
    "Wznawiam — {mb:.0f} MB było już na dysku.":
        "Resuming — {mb:.0f} MB was already on disk.",
    "Nie udało się skopiować {co} z {folder}.":
        "Could not copy {co} from {folder}.",
    "Połączenie przerwane ({blad}) — wznawiam {plik}, próba {proba} z {prob}…":
        "Connection interrupted ({blad}) — resuming {plik}, attempt {proba} of "
        "{prob}…",
    "Nagrywanie już trwa.": "Recording is already in progress.",
    "Brak biblioteki do nagrywania (PyAudioWPatch). Uruchom setup.bat.":
        "The recording library (PyAudioWPatch) is missing. Run setup.bat.",
    "Nie znaleziono ani mikrofonu, ani urządzenia odtwarzającego dźwięk.":
        "Neither a microphone nor an audio playback device was found.",
    "Brak mikrofonu — nagrywam sam dźwięk systemowy.":
        "No microphone — recording system audio only.",
    "Za mało miejsca na dysku ({mb:.0f} MB wolne). Godzina nagrania to około 80 "
        "MB.":
        "Not enough disk space ({mb:.0f} MB free). An hour of recording takes about "
        "80 MB.",
    "To urządzenie nie udostępnia dźwięku systemowego — nagrywam sam mikrofon.":
        "This device does not provide system audio — recording the microphone only.",
    "Komputer był uśpiony — w nagraniu zostaje sekunda przerwy.":
        "The computer was asleep — the recording keeps a one-second gap.",
    "Mikrofonu „{nazwa}” nie ma — używam domyślnego.":
        "Microphone “{nazwa}” is not available — using the default one.",
    "Urządzenia „{nazwa}” nie ma — używam domyślnego.":
        "Device “{nazwa}” is not available — using the default one.",
    "Nagrywanie przerwane: {blad}": "Recording interrupted: {blad}",
    "{kto} przestał odpowiadać (odłączone urządzenie?) — nagrywam dalej to, co "
        "zostało, i próbuję wznowić.":
        "{kto} stopped responding (device unplugged?) — recording what is left and "
        "trying to resume.",
    "Dźwięk systemowy": "System audio",
    "{kto} wznowiony.": "{kto} resumed.",
    "Ta karta": "This graphics card",
    "{gdzie} nie obsługuje precyzji {z} — przechodzę na {na}.":
        "{gdzie} does not support {z} precision — switching to {na}.",
    "Nie udało się załadować modelu {model}: {blad}":
        "Could not load model {model}: {blad}",
    "{n} segmentów, {dlugosc} materiału w {czas} ({x:.1f}x realtime, "
        "{urzadzenie})":
        "{n} segments, {dlugosc} of audio in {czas} ({x:.1f}x realtime, {urzadzenie})",
    "Brak silnika transkrypcji. Uruchom setup.bat, żeby zainstalować "
        "faster-whisper.":
        "The transcription engine is missing. Run setup.bat to install "
        "faster-whisper.",
    "GPU niedostępne dla silnika — przechodzę na CPU.":
        "The GPU is not available to the engine — switching to the CPU.",
    "Model nie został załadowany — wywołaj load().":
        "The model has not been loaded — call load().",
    "Ładuję model {model} ({urzadzenie}, {precyzja})…":
        "Loading model {model} ({urzadzenie}, {precyzja})…",
    "Model gotowy w {s:.1f} s.": "Model ready in {s:.1f} s.",
    "Transkrypcja przerwana przez użytkownika.":
        "Transcription cancelled by the user.",
    "Nie udało się użyć GPU ({blad}). Próbuję na CPU…":
        "Could not use the GPU ({blad}). Trying on the CPU…",
    "Nie znaleziono ffmpeg w systemie. Uruchom setup.bat albo zainstaluj ffmpeg "
        "ręcznie (winget install Gyan.FFmpeg).":
        "ffmpeg was not found on this system. Run setup.bat or install ffmpeg "
        "manually (winget install Gyan.FFmpeg).",
    "Nie znaleziono ffprobe (składnik ffmpeg). Uruchom setup.bat.":
        "ffprobe (part of ffmpeg) was not found. Run setup.bat.",
    "ffmpeg zawiesił się przy zamykaniu pliku.":
        "ffmpeg hung while closing the file.",
    "Plik nie istnieje: {plik}": "File does not exist: {plik}",
    "ffprobe nie potrafi odczytać pliku {plik}: {blad}":
        "ffprobe cannot read the file {plik}: {blad}",
    "Plik {plik} nie zawiera ścieżki dźwiękowej — nie ma czego transkrybować.":
        "The file {plik} has no audio track — there is nothing to transcribe.",
    "ffmpeg nie zdołał wyciągnąć audio z {plik}: {blad}":
        "ffmpeg failed to extract audio from {plik}: {blad}",
    "ffmpeg wyprodukował pusty plik audio dla {plik}":
        "ffmpeg produced an empty audio file for {plik}",
    "ffprobe nie odpowiedział dla pliku {plik}":
        "ffprobe did not respond for the file {plik}",
    "Nieczytelna odpowiedź ffprobe dla {plik}":
        "Unreadable ffprobe response for {plik}",
    "Ekstrakcja audio przerwana przez użytkownika.":
        "Audio extraction cancelled by the user.",
    "Certyfikaty weryfikowane przez magazyn systemowy.":
        "Certificates are verified through the system store.",
    "Nie udało się zweryfikować certyfikatu serwera. Zwykle znaczy to, że sieć "
        "firmowa podmienia certyfikaty własnym urzędem.\nCo można zrobić:\n  1. Poproś "
        "dział IT o dodanie firmowego urzędu certyfikacji do magazynu Windows "
        "(zwykle już tam jest — program go użyje).\n  2. Albo skopiuj gotowy model z "
        "komputera, na którym już działa: cały folder {folder}.\n  3. Albo wskaż "
        "folder z modelem zmienną WHISPER_AUTOMAT_MODELS.":
        "Could not verify the server certificate. This usually means the company "
        "network replaces certificates with its own certificate authority.\nWhat you "
        "can do:\n  1. Ask the IT department to add the company certificate authority "
        "to the Windows store (it is usually there already — the program will use "
        "it).\n  2. Or copy a ready model from a computer where it already works: the "
        "whole folder {folder}.\n  3. Or point to the model folder with the "
        "WHISPER_AUTOMAT_MODELS variable.",
    "Połączenie blokuje serwer proxy wymagający logowania. Poproś dział IT o "
        "dostęp do huggingface.co albo skopiuj gotowy model z innego komputera "
        "(folder {folder}).":
        "The connection is blocked by a proxy server that requires a login. Ask the "
        "IT department for access to huggingface.co or copy a ready model from "
        "another computer (folder {folder}).",
    "Brak połączenia z serwerem modeli (huggingface.co). Sprawdź internet albo "
        "skopiuj gotowy model z innego komputera (folder {folder}).":
        "No connection to the model server (huggingface.co). Check the internet "
        "connection or copy a ready model from another computer (folder {folder}).",
    "Certyfikaty z pliku wskazanego przez {zmienna}: {plik}":
        "Certificates from the file given by {zmienna}: {plik}",
    "Nie udało się włączyć systemowego magazynu certyfikatów: {blad}":
        "Could not enable the system certificate store: {blad}",
    "huggingface.co osiągalne (HTTP {kod})":
        "huggingface.co reachable (HTTP {kod})",
    "błąd certyfikatu: {blad}": "certificate error: {blad}",
    "Szybka transkrypcja audio i wideo silnikiem Whisper.":
        "Fast audio and video transcription with the Whisper engine.",
    "Pliki lub foldery do transkrypcji (bez nich otwiera się okno).":
        "Files or folders to transcribe (without them the window opens).",
    "Wypisz raport o środowisku i zakończ.":
        "Print an environment report and exit.",
    "Przetwórz pliki w konsoli, bez otwierania okna.":
        "Process the files in the console, without opening the window.",
    "Wymuś model, np. large-v3-turbo.": "Force a model, e.g. large-v3-turbo.",
    "Kod języka, np. pl. Pusty = auto.":
        "Language code, e.g. pl. Empty = auto.",
    "Formaty po przecinku: txt,txt_plain,srt,vtt,json":
        "Comma-separated formats: txt,txt_plain,srt,vtt,json",
    "Folder na transkrypcje.": "Folder for transcripts.",
    "Nie podano żadnego pliku audio ani wideo.":
        "No audio or video file was given.",
    "Gotowe: {ok} / {n}": "Done: {ok} / {n}",
    "BŁĄD {plik}: {blad}": "ERROR {plik}: {blad}",
    'Twoje imię w transkrypcji': 'Your name in the transcript',
    'np. Anna': 'e.g. Anna',
    'Ja': 'Me',
    'Twój mikrofon — bez pytania. Imię zmienisz w ustawieniach.':
        'Your microphone — no guessing. Change the name in the settings.',
    'Ty: {imie}': 'You: {imie}',
    'Oczekiwano 16-bitowego WAV stereo — plik nie przeszedł konwersji.':
        'Expected a 16-bit stereo WAV — the file did not go through conversion.',
    'Nieznany kanał: {kanal}': 'Unknown channel: {kanal}',
    'Nie wykryto rozmówców w torze spotkania — podpisuję tylko Ciebie.':
        'No other speakers detected in the meeting track — only you get a label.',
    'W torze mikrofonu nie wykryto Twojej mowy.':
        'No speech of yours was detected in the microphone track.',
    '[{i}/{n}] Rozdzielam kanały nagrania…':
        '[{i}/{n}] Splitting the recording channels…',
    'Własne nagranie: lewy kanał to Ty, rozmówców szukam w prawym ({opis}).':
        'Own recording: the left channel is you, looking for the others in the right one ({opis}).',
    'Nie udało się rozdzielić kanałów nagrania: {blad}':
        'Could not split the recording channels: {blad}',
    'Nie udało się rozpoznać Twojego toru: {blad}':
        'Could not analyse your track: {blad}',
    'Ty + {n} rozm.': 'you + {n} others',
    'Pauza': 'Pause',
    'Wznów': 'Resume',
    '⚑ Zaznacz (F8)': '⚑ Mark (F8)',
    'Pauza — nagranie wstrzymane. „Wznów” nagrywa dalej do tego samego pliku.':
        'Paused — recording on hold. “Resume” continues into the same file.',
    'Nagranie wznowione.': 'Recording resumed.',
    'Znacznik {czas}': 'Marker {czas}',
    'Zaznaczono moment {czas} — w transkrypcji będzie ⚑.':
        'Moment {czas} marked — it will show up as ⚑ in the transcript.',
    'Pauza w nagraniu ({czas})': 'Recording paused ({czas})',
    'Zaznaczone momenty: {n}': 'Marked moments: {n}',
    'Po pauzie nie ma z czego nagrywać.':
        'Nothing left to record from after the pause.',
    'Nie udało się zapisać zaznaczonych momentów: {blad}':
        'Could not save the marked moments: {blad}',
    '{kto} nie dał się otworzyć po pauzie — nagrywam dalej bez niego.':
        '{kto} could not be reopened after the pause — recording continues without it.',
    'zaznaczony moment': 'marked moment',
    '{kto} przełączony na: {urzadzenie}':
        '{kto} switched to: {urzadzenie}',
    'Próbkę głosu nagrasz po zatrzymaniu nagrania.':
        'Record the voice sample after stopping the recording.',
    'Dodaj mój głos do bazy…': 'Add my voice to the database…',
    'Dzień dobry, nazywam się … i pracuję w dziale … . Dziś omówimy plan na najbliższy kwartał: terminy dostaw, nowy cennik oraz przygotowania do targów. Proszę o uwagi do protokołu z ostatniego spotkania. Jeśli coś jest niejasne, pytajcie śmiało, chętnie wyjaśnię. Na koniec ustalimy, kto odpowiada za kolejne kroki i do kiedy.':
        'Good morning, my name is … and I work in the … department. Today we will go through the plan for the coming quarter: delivery dates, the new price list and preparations for the trade fair. Please send me your comments on the minutes from the last meeting. If anything is unclear, just ask, I will gladly explain. At the end we will agree who is responsible for the next steps and by when.',
    'Dodaj mój głos do bazy': 'Add my voice to the database',
    'Nagrywam. Czytaj tekst powyżej…':
        'Recording. Read the text above…',
    'Głos usunięty z bazy firmowej.':
        'Voice removed from the company database.',
    'Usunięto. Twój głos nie będzie już rozpoznawany.':
        'Removed. Your voice will no longer be recognised.',
    'Sprawdzam bazę…': 'Checking the database…',
    'Usuń mój głos': 'Remove my voice',
    'Zapisz do bazy': 'Save to the database',
    'Nagrywam…': 'Recording…',
    'Liczę odcisk głosu…': 'Computing the voiceprint…',
    'Wpisz imię — tak podpisze Cię transkrypcja.':
        'Enter your name — that is how the transcript will label you.',
    'Usunąć Twój głos z bazy firmowej?':
        'Remove your voice from the company database?',
    'W bazie firmowej: {n} głos(ów). Twój głos jest zapisany ({data}) jako „{imie}” — nowa próbka go zastąpi.':
        'Company database: {n} voice(s). Your voice is stored ({data}) as “{imie}” — a new sample will replace it.',
    'W bazie firmowej: {n} głos(ów). Twojego głosu jeszcze nie ma.':
        'Company database: {n} voice(s). Your voice is not there yet.',
    'Próbka gotowa: {s:.0f} s mowy. Sprawdź imię i kliknij „Zapisz do bazy”.':
        'Sample ready: {s:.0f} s of speech. Check the name and click “Save to the database”.',
    'Głos zapisany w bazie firmowej jako „{imie}” ({plik}).':
        'Voice saved in the company database as “{imie}” ({plik}).',
    'Zapisano. Od teraz w transkrypcjach kolegów będziesz „{imie}”.':
        "Saved. From now on you will be “{imie}” in your colleagues' transcripts.",
    'Imię i nazwisko w transkrypcjach': 'Name in the transcripts',
    'Przeczytaj na głos, zwyczajnie, jak na spotkaniu (około 25 s):':
        'Read aloud, naturally, as in a meeting (about 25 s):',
    'Nagraj próbkę ({s} s)': 'Record a sample ({s} s)',
    'Baza niedostępna: {blad}': 'Database unavailable: {blad}',
    'Nagranie próbki przerwane.': 'Sample recording interrupted.',
    'Udział jest dobrowolny i można go w każdej chwili cofnąć przyciskiem „Usuń mój głos”. Pomaga wszystkim użytkownikom {app} w firmie: gdy weźmiesz udział w nagrywanym spotkaniu, Twoje imię pojawi się w ich transkrypcji samo, zamiast „MÓWCA 2”.\n\nDo bazy trafia odcisk głosu (zestaw liczb opisujących brzmienie, nie nagranie), Twoje imię, login i data. Baza leży w folderze firmowym obok instalatora i korzysta z niej tylko ten program. Głosów innych osób program nigdy nie zapisuje sam.':
        "Taking part is voluntary and can be withdrawn at any time with “Remove my voice”. It helps every {app} user in the company: when you take part in a recorded meeting, your name shows up in their transcript by itself instead of “SPEAKER 2”.\n\nThe database stores a voiceprint (a set of numbers describing the sound, not a recording), your name, login and date. It lives in the company folder next to the installer and only this program uses it. The program never stores other people's voices on its own.",
    'z bazy głosów · {p:.0%}': 'from the voice database · {p:.0%}',
    'To wydanie nie ma bazy głosów.':
        'This edition has no voice database.',
    'Błędna konfiguracja modelu odcisków głosu.':
        'Invalid voiceprint model configuration.',
    'Za mało mowy, żeby policzyć odcisk głosu.':
        'Not enough speech to compute a voiceprint.',
    'Model odcisków zwrócił pusty wektor.':
        'The voiceprint model returned an empty vector.',
    'Baza głosów jest pusta — bez podpowiedzi imion.':
        'The voice database is empty — no name suggestions.',
    'Baza głosów: pominięto {n} wpis(ów) — uszkodzone albo z innego modelu.':
        'Voice database: skipped {n} entry(ies) — damaged or from another model.',
    'W próbce jest tylko {s:.0f} s mowy — potrzeba co najmniej {min:.0f} s. Mów bliżej mikrofonu albo nagraj jeszcze raz.':
        'The sample has only {s:.0f} s of speech — at least {min:.0f} s is needed. Speak closer to the microphone or record again.',
    'Z bazy głosów rozpoznano: {kto}':
        'Recognised from the voice database: {kto}',
    'Baza głosów ({n} wpisów): nikogo nie rozpoznano.':
        'Voice database ({n} entries): nobody recognised.',
    'Folder bazy głosów niedostępny: {blad}':
        'Voice database folder unavailable: {blad}',
    'Nie udało się zapisać głosu w folderze firmowym: {blad}':
        'Could not save the voice in the company folder: {blad}',
    'Nie udało się usunąć wpisu: {blad}':
        'Could not remove the entry: {blad}',
    'Odcisk mówcy {n}: {blad}': 'Voiceprint of speaker {n}: {blad}',
    '[{i}/{n}] Porównuję głosy z bazą…':
        '[{i}/{n}] Comparing voices with the database…',
    'Baza głosów: {blad}': 'Voice database: {blad}',
    # -- 1.6: obraz spotkania i próbka mikrofonu ------------------------------
    "Kończę zapisywanie obrazu spotkania…": "Finishing saving the meeting video…",
    "Poczekaj, aż skończy się próbka mikrofonu.":
        "Wait until the microphone sample has finished.",
    "Okno programu {program} nie jest otwarte, więc obrazu nie ma skąd wziąć.\n\n"
    "Nagrywać sam dźwięk?\n„Nie” — otwórz spotkanie i kliknij „Nagrywaj spotkanie” "
    "jeszcze raz.":
        "The {program} window is not open, so there is no video to take.\n\n"
        "Record audio only?\n“No” — open the meeting and click “Record meeting” again.",
    "Prawie cisza ({db:.0f} dB) z: {urzadzenie}. Sprawdź, czy mikrofon nie jest wyciszony "
    "i czy to właściwe urządzenie.":
        "Almost silence ({db:.0f} dB) from: {urzadzenie}. Check that the microphone is not "
        "muted and that it is the right device.",
    "Obraz spotkania zapisany: {plik}": "Meeting video saved: {plik}",
    "Bardzo głośno ({db:.0f} dB) z: {urzadzenie} — mogą być przestery. Odsuń mikrofon "
    "albo zmniejsz czułość w ustawieniach Windows.":
        "Very loud ({db:.0f} dB) from: {urzadzenie} — it may distort. Move the microphone "
        "away or lower its sensitivity in Windows settings.",
    "Nagrywanie obrazu wymaga ffmpeg 9 lub nowszego — jest w instalatorze programu.":
        "Recording video needs ffmpeg 9 or newer — it comes with the program installer.",
    "{program} — teraz zamknięty; okno wezmę przy starcie nagrania":
        "{program} — closed now; I will take its window when recording starts",
    "Nagrywam też obraz: {zrodlo}": "Also recording video: {zrodlo}",
    "Cały ekran ({rozmiar})": "Whole screen ({rozmiar})",
    "Cały ekran — główny monitor ({rozmiar})": "Whole screen — main monitor ({rozmiar})",
    "Cały ekran — monitor {n} ({rozmiar})": "Whole screen — monitor {n} ({rozmiar})",
    "Cały ekran — {nazwa} ({rozmiar})": "Whole screen — {nazwa} ({rozmiar})",
    "Ekranu już nie ma (odłączony monitor) — nagrywam sam dźwięk.":
        "The screen is gone (monitor disconnected) — recording audio only.",
    "⇄ Cały ekran": "⇄ Whole screen",
    "⇄ Okno": "⇄ Window",
    "Nie widzę okna spotkania — obraz zostaje z całego ekranu.":
        "I cannot see the meeting window — the video stays on the whole screen.",
    "Przełączam obraz…": "Switching the video…",
    "Obraz przełączony na: {zrodlo}": "Video switched to: {zrodlo}",
    "Przełącz na cały ekran": "Switch to the whole screen",
    "Nie teraz": "Not now",
    "Okno spotkania się schowało — pytam, czy nagrywać cały ekran.":
        "The meeting window was hidden — asking whether to record the whole screen.",
    "Okno spotkania się schowało": "The meeting window was hidden",
    "Udostępniasz ekran? Żeby było go widać w nagraniu, przełącz obraz na cały ekran. "
    "Nagra się wszystko, co na nim widać — także powiadomienia.":
        "Sharing your screen? To have it in the recording, switch the video to the whole "
        "screen. Everything visible on it gets recorded — notifications too.",
    "Okno spotkania wróciło — pytam, czy wrócić do nagrywania okna.":
        "The meeting window is back — asking whether to go back to recording the window.",
    "Okno spotkania wróciło": "The meeting window is back",
    "Udostępnianie skończone? Przełącz obraz z powrotem na okno spotkania — cały ekran "
    "nagrywa też wszystko inne, co na nim robisz.":
        "Done sharing? Switch the video back to the meeting window — the whole screen also "
        "records everything else you do on it.",
    "Wróć do okna spotkania": "Back to the meeting window",
    "Zostaw cały ekran": "Keep the whole screen",
    "Ten ffmpeg nie umie nagrywać okien — nagrywam sam dźwięk.":
        "This ffmpeg cannot record windows — recording audio only.",
    "Nie udało się złożyć obrazu spotkania: {blad}. Odcinki obrazu (.ts) zostały "
    "w folderze Nagrania — każdy da się obejrzeć osobno.":
        "Could not assemble the meeting video: {blad}. The video parts (.ts) stay in the "
        "Recordings folder — each can be watched on its own.",
    "Nie udało się nagrać próbki: {blad}": "Could not record the sample: {blad}",
    "Nie znaleziono żadnego mikrofonu.": "No microphone found.",
    "zminimalizowane": "minimised",
    "Okna spotkania już nie ma — nagrywam sam dźwięk.":
        "The meeting window is gone — recording audio only.",
    "Okno spotkania zamknięte — obraz zatrzymany, dźwięk nagrywa dalej.":
        "Meeting window closed — video stopped, audio keeps recording.",
    "Obraz spotkania nie ruszył: {blad}": "Meeting video did not start: {blad}",
    "ffmpeg zakończył pracę": "ffmpeg exited",
    "Obraz spotkania przerwany: {blad}": "Meeting video interrupted: {blad}",
    # -- 1.6.1: fragmenty („Zaznacz” — początek i koniec), pole obrazu na karcie --
    "Fragment {n}": "Section {n}",
    "Fragment {n} — koniec": "Section {n} — end",
    "Fragment {n} — początek": "Section {n} — start",
    "Fragment {n}: początek {czas}": "Section {n}: start {czas}",
    "Fragment {n}: {od}–{do}": "Section {n}: {od}–{do}",
    "Fragment {n}: {od}–{do} — w transkrypcji będzie oznaczony ⚑.":
        "Section {n}: {od}–{do} — it will be marked ⚑ in the transcript.",
    "Nie widzę okna spotkania (Zoom, Teams, Meet…).\n\nTak — nagrywaj cały ekran\n"
    "Nie — nagrywaj sam dźwięk\nAnuluj — nie zaczynaj: otwórz spotkanie i kliknij "
    "„Nagrywaj spotkanie” jeszcze raz":
        "I cannot see a meeting window (Zoom, Teams, Meet…).\n\nYes — record the whole "
        "screen\nNo — record audio only\nCancel — do not start: open the meeting and click "
        "“Record meeting” again",
    "Zaznaczasz fragment {n} od {czas} — naciśnij „Zakończ” (F8), gdy się skończy.":
        "Marking section {n} from {czas} — press “End” (F8) when it is over.",
    "Zaznaczony moment": "Marked moment",
    "koniec fragmentu {n}": "end of section {n}",
    "⚑ Zakończ (F8)": "⚑ End (F8)",
    # 1.6.2: źródła na karcie nagrywania (bez okna „Zmień…”), stopka
    # z adresatem zgłoszeń, okno „Licencja” wydania firmowego.
    "Obraz": "Video",
    "Sprawdź": "Test",
    "● Mów…": "● Speak…",
    "Domyślny w Windows": "Windows default",
    "Domyślne w Windows": "Windows default",
    "Sam dźwięk — bez obrazu": "Audio only — no video",
    "Okno spotkania — samo znajdzie Zoom, Teams, Meet…":
        "Meeting window — finds Zoom, Teams, Meet… by itself",
    "Nagra się wszystko, co widać na ekranie — także powiadomienia. Obraz trafi do "
    "pliku MKV obok nagrania.":
        "Everything on the screen will be recorded — notifications too. The video goes "
        "to an MKV file next to the recording.",
    "Przy starcie nagrania program sam znajdzie okno spotkania. Obraz trafi do pliku "
    "MKV obok nagrania.":
        "When recording starts, the program finds the meeting window by itself. The video "
        "goes to an MKV file next to the recording.",
    "Okno nagrywa się także zasłonięte innymi oknami. Obraz trafi do pliku MKV obok "
    "nagrania.":
        "The window is recorded even when other windows cover it. The video goes to an "
        "MKV file next to the recording.",
    "Mów normalnie przez 5 sekund — potem usłyszysz, jak Cię słychać.":
        "Speak normally for 5 seconds — then you will hear how you sound.",
    "Nagrano z: {urzadzenie}. Poziom {db:.0f} dB — w porządku. Odtwarzam próbkę.":
        "Recorded from: {urzadzenie}. Level {db:.0f} dB — fine. Playing the sample.",
    "Próbka mikrofonu: {tekst}": "Microphone sample: {tekst}",
    "Nie słychać dźwięku spotkania. Sprawdź, na jakie urządzenie gra Teams, i po "
    "nagraniu wybierz je w polu „Dźwięk spotkania”.":
        "No meeting audio. Check which device Teams plays on and, after the recording, "
        "choose it in the “Meeting audio” field.",
    "Podłączony jest zestaw słuchawkowy:\n{zestaw}\n\nNagranie poszłoby z: {obecne}.\n\n"
    "Nagrywać przez zestaw słuchawkowy? Zapamiętam go na karcie nagrywania.":
        "A headset is connected:\n{zestaw}\n\nThe recording would use: {obecne}.\n\n"
        "Record through the headset? I will remember it on the recording card.",
    "Nie słychać Cię — sprawdź, który mikrofon jest wybrany na karcie nagrywania.":
        "I cannot hear you — check which microphone is selected on the recording card.",
    "Za mało miejsca na dysku na nagranie z obrazem ({mb:.0f} MB wolne, potrzeba co "
    "najmniej 3 GB). Zwolnij miejsce albo w polu „Obraz” wybierz „Sam dźwięk — bez "
    "obrazu”.":
        "Not enough disk space to record video ({mb:.0f} MB free, at least 3 GB needed). "
        "Free up space or choose “Audio only — no video” in the “Video” field.",
    "Na karcie nagrywania wybierasz mikrofon, urządzenie, na którym słychać spotkanie, "
    "i obraz: sam dźwięk, okno spotkania albo cały ekran (do pliku MKV) — także "
    "w trakcie nagrania. „Sprawdź” nagra 5 sekund z mikrofonu i od razu je odtworzy, "
    "a „Wycisz” nagrywa samych rozmówców, gdy jesteś tylko słuchaczem. Słuchawki na "
    "zwykłym jacku grają przez „głośniki komputera” — Windows ich nie odróżnia.":
        "On the recording card you choose the microphone, the device the meeting plays "
        "on, and the video: audio only, the meeting window or the whole screen (to an MKV "
        "file) — also during a recording. “Test” records 5 seconds from the microphone and "
        "plays them back, and “Mute” records only the others when you are just listening. "
        "Headphones on a regular jack play through the “computer speakers” — Windows "
        "cannot tell them apart.",
    "Ten komputer nie widzi folderu z aktualizacjami — pewnie jest poza siecią firmy. "
    "Program działa normalnie, a nowa wersja przyjdzie sama, gdy komputer wróci do sieci "
    "firmowej.\n\n{folder}":
        "This computer cannot see the updates folder — it is probably outside the company "
        "network. The program works normally, and the new version will arrive by itself "
        "once the computer is back on the company network.\n\n{folder}",
    "Zgłoś błąd lub pomysł:": "Report a bug or idea:",
    "{app} {w}: problem lub sugestia": "{app} {w}: problem or suggestion",
    "Nie udało się otworzyć programu pocztowego. Napisz na adres {adres} — jest już "
    "w schowku, wystarczy wkleić.":
        "Could not open the email program. Write to {adres} — the address is already "
        "on the clipboard, just paste it.",
    "Licencja": "License",
    "Licencja — {app}": "License — {app}",
    "{app}, wersja {w}": "{app}, version {w}",
    "{app} to program {firma}. Mogą z niego korzystać wszyscy pracownicy firmy.":
        "{app} is {firma} software. All {firma} employees may use it.",
    "Wolno go udostępniać i przekazywać dalej.": "It may be shared and passed on.",
    "Aktualizacje przychodzą same z folderu firmowego, gdy komputer jest w sieci "
    "firmy. Na komputerze poza firmą program działa normalnie, ale nie dostaje "
    "automatycznych aktualizacji.":
        "Updates arrive by themselves from the company folder while the computer is on "
        "the company network. On a computer outside the company the program works "
        "normally but gets no automatic updates.",
    "Nagrania i transkrypcje zostają na komputerze — program działa bez internetu.":
        "Recordings and transcripts stay on the computer — the program works offline.",
    "Błędy i pomysły": "Bugs and ideas",
    "Opiekun programu:": "Maintainer:",
    "Składniki open source": "Open-source components",
    "Program korzysta z bibliotek open source (m.in. faster-whisper i FFmpeg) na ich "
    "własnych licencjach.":
        "The program uses open-source libraries (including faster-whisper and FFmpeg) "
        "under their own licenses.",
    "Licencje składników": "Component licenses",
    # 1.6.4: wyciszenie mikrofonu, wybór źródeł w trakcie, podgląd obrazu
    "Wycisz": "Mute",
    "Wyciszony": "Muted",
    "Mikrofon wyciszony — w nagraniu jest teraz tylko dźwięk spotkania.":
        "Microphone muted — the recording now has only the meeting audio.",
    "Mikrofon wyciszony — nagranie pójdzie bez Ciebie, sam dźwięk spotkania. "
    "Po nagraniu mikrofon wróci.":
        "Microphone muted — the recording will go without you, meeting audio only. "
        "The microphone comes back after the recording.",
    "Mikrofon włączony — znów nagrywa się Twój głos.":
        "Microphone on — your voice is being recorded again.",
    "Mikrofon włączony.": "Microphone on.",
    "Mikrofon wyciszony od początku — nagrywa się sam dźwięk spotkania.":
        "Microphone muted from the start — recording the meeting audio only.",
    "Nagrywam z wyciszonym mikrofonem — Twojego głosu nie będzie. "
    "„Wyciszony” włącza mikrofon.":
        "Recording with the microphone muted — your voice will not be in it. "
        "“Muted” turns the microphone on.",
    "Mikrofon wyciszony ({czas}).": "Microphone muted ({czas}).",
    "Mikrofon włączony ({czas}).": "Microphone on ({czas}).",
    "Mikrofon znów włączony — następne nagranie zacznie się z nim.":
        "Microphone on again — the next recording will start with it.",
    "Mikrofon nie dał się otworzyć — w nagraniu dalej cisza z Twojej strony.":
        "The microphone could not be opened — the recording still has silence on your side.",
    "Przełączam mikrofon…": "Switching the microphone…",
    "Przełączam dźwięk spotkania…": "Switching the meeting audio…",
    "Mikrofon przełączony na: {urzadzenie}": "Microphone switched to: {urzadzenie}",
    "Dźwięk spotkania przełączony na: {urzadzenie}": "Meeting audio switched to: {urzadzenie}",
    "{kto}: wybrane urządzenie nie dało się otworzyć — nagrywam dalej z poprzedniego.":
        "{kto}: the chosen device could not be opened — recording continues from the "
        "previous one.",
    "Okna spotkania już nie ma — wybierz inne": "The meeting window is gone — choose another",
    "Nagrywa się: {zrodlo}": "Recording: {zrodlo}",
    "Program sam wybrał: {okno}": "Picked automatically: {okno}",
    "{program} jest teraz zamknięty": "{program} is closed now",
    "Okno wezmę przy starcie nagrania.": "I will take its window when the recording starts.",
    "Teraz nie widzę okna spotkania": "No meeting window right now",
    "Otwórz Zoom, Teams albo Meet. Przy starcie nagrania program sam znajdzie okno — "
    "a jeśli go nie będzie, zapyta, co nagrywać.":
        "Open Zoom, Teams or Meet. When the recording starts, the program finds the window "
        "by itself — and if there is none, it asks what to record.",
    "Łapię obraz…": "Capturing the picture…",
    "Tak wygląda teraz nagrywany obraz. Podgląd pokazuje się też po najechaniu myszą "
    "na listę „Obraz”; kliknięcie go chowa.":
        "This is what is being recorded right now. The preview also shows when you hover "
        "over the “Video” list; click it to hide.",
    "Tak wygląda teraz to źródło. Podgląd pokazuje się też po najechaniu myszą na listę "
    "„Obraz”; kliknięcie go chowa.":
        "This is how this source looks right now. The preview also shows when you hover "
        "over the “Video” list; click it to hide.",
    "Okno jest zminimalizowane — przywróć je, inaczej obraz w nagraniu będzie stał.":
        "The window is minimised — restore it, otherwise the recorded picture will freeze.",
    "Tego okna już nie ma — wybierz inne z listy.":
        "This window is gone — choose another one from the list.",
}
