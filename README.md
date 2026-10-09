# Papuga – transkrypcje offline

Nagrywa spotkania, zamienia nagrania w tekst i rozpoznaje, kto mówi. Działa
na Twoim komputerze, bez internetu i bez chmury.

**[Strona programu](https://matmiccode.github.io/papuga/) ·
[Pobierz najnowszą wersję](https://github.com/matmiccode/papuga/releases/latest) ·
[Zgłoś problem](https://github.com/matmiccode/papuga/issues/new)**

## Co robi

- Przeciągasz nagrania audio lub wideo do okna, klikasz *Transkrybuj*, dostajesz tekst.
- Nagrywa spotkania (Teams, Zoom, przeglądarka): jeden przycisk zbiera Twój mikrofon
  i dźwięk rozmówców do jednego pliku FLAC, a po zatrzymaniu od razu go transkrybuje.
- Rozpoznaje mowę modelem Whisper (`large-v3-turbo`) na Twoim komputerze. Z kartą
  NVIDIA kilkanaście razy szybciej niż czas nagrania, bez karty na procesorze.
- Zapisuje TXT ze znacznikami czasu, czysty tekst, napisy SRT i VTT oraz JSON.
- Rozpoznaje, kto co powiedział, i pozwala nadać mówcom imiona po odsłuchaniu próbek.
- Kolejka wielu plików i automatyczne aktualizacje.
- Interfejs po polsku i po angielsku (według języka Windows, przełącznik PL/EN w oknie).

## Instalacja

1. Pobierz `Papuga-<wersja>-Setup.exe` ze [strony wydań](https://github.com/matmiccode/papuga/releases/latest).
2. Uruchom. Instalator nie pyta o nic i nie wymaga uprawnień administratora:
   program trafia do profilu użytkownika (`%LOCALAPPDATA%\Programs\Papuga`).
3. Przy pierwszym uruchomieniu program pobiera model rozpoznawania mowy (ok. 1,6 GB)
   z huggingface.co. To jednorazowe. Potem działa bez internetu.

**Windows ostrzega przy pierwszej instalacji** („System Windows ochronił ten
komputer”), bo instalator nie ma płatnego podpisu cyfrowego wydawcy. Kliknij
*Więcej informacji → Uruchom mimo to*. Kod programu jest tutaj, do wglądu.

Wymagania: Windows 10 lub 11 (64-bit), 8 GB RAM, ok. 4 GB wolnego miejsca.
Karta NVIDIA jest opcjonalna, ale bardzo przyspiesza pracę.

## Prywatność

Nagrania i transkrypcje nigdy nie opuszczają komputera. Program łączy się
z internetem tylko w dwóch sytuacjach:

- raz, przy pierwszym uruchomieniu, pobiera model z huggingface.co
  (i modele rozpoznawania mówców z github.com przy pierwszym użyciu tej funkcji),
- raz na dobę pyta api.github.com, czy jest nowa wersja (bez logowania, bez identyfikatorów).

Aktualizację instaluje sam tylko wtedy, gdy wydanie jest podpisane kluczem
autora wbudowanym w program (`core/podpis.py`). Pliki użytkownika:
ustawienia i modele w `%LOCALAPPDATA%\Papuga`, transkrypcje domyślnie
w `Dokumenty\Transkrypcje`.

## Uruchomienie z kodu

Potrzebny Python 3.9+ z tkinter (instalator z python.org, z zaznaczonym
„Add python.exe to PATH”).

```bat
git clone https://github.com/matmiccode/papuga.git
cd papuga
setup.bat                       :: środowisko .venv, ffmpeg, biblioteki, model
"Uruchom Papuga.bat"            :: okno programu w wydaniu Papuga
```

Tryb konsolowy: `.venv\Scripts\python.exe -m whisper_automat --cli nagranie.mp4`.
Diagnostyka środowiska: `Diagnostyka.bat`.

## Budowa instalatora

```bat
.venv\Scripts\pip.exe install -r requirements-dev.txt
winget install JRSoftware.InnoSetup
.venv\Scripts\python.exe tools\build_exe.py --wydanie papuga
```

Wynik: `dist\Papuga-<wersja>-Setup.exe`. Opis budowy, wydań i podpisywania:
`tools/build_exe.py`, `tools/publikuj_wydanie.py`, `tools/klucz_wydan.py`.

## Jak powstaje to repozytorium

Program jest rozwijany w prywatnym repozytorium (ten sam kod służy też
wydaniu wewnętrznemu pod inną nazwą). Tutaj trafia migawka kodu przy każdym
wydaniu, jednym commitem. Zgłoszenia błędów i propozycje są mile widziane
w *Issues*. Poprawki w *Pull requests* też, z zastrzeżeniem, że zostaną
przeniesione ręcznie do repozytorium źródłowego i wrócą tu z kolejną migawką.

## Licencje

Kod programu: [MIT](LICENSE). Składniki, z których korzysta (Whisper,
faster-whisper, CTranslate2, sherpa-onnx, ffmpeg, modele i biblioteki),
mają własne licencje. Ich pełny spis jest w pliku [LICENCJE.txt](LICENCJE.txt),
który trafia też do każdej instalacji (odnośnik *Licencje* w oknie programu).

---

MATCODE · [Postaw kawę autorowi](https://buycoffee.to/matcode)
