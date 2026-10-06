<#
.SYNOPSIS
    Pobiera model Whispera bez udziału programu.

.DESCRIPTION
    Przydatne w sieciach firmowych, w których proxy podmienia certyfikaty
    TLS. Python weryfikuje certyfikaty własną listą (certifi) i takie
    połączenie odrzuca, natomiast wbudowany w Windows curl.exe korzysta
    ze Schannel, czyli z systemowego magazynu certyfikatów — a tam firmowy
    urząd certyfikacji już jest, bo dodał go dział IT.

    Pliki lądują w zwykłym folderze, bez wewnętrznej struktury pamięci
    podręcznej Hugging Face. Program rozpoznaje taki folder po nazwie
    modelu i używa go wprost.

.PARAMETER Model
    Nazwa modelu. Domyślnie large-v3-turbo.

.PARAMETER Katalog
    Katalog modeli. Domyślnie ten, którego używa wersja instalacyjna.
    Dla wersji uruchamianej z kodu wskaż podkatalog `models` projektu.

.EXAMPLE
    .\pobierz_model.ps1
    .\pobierz_model.ps1 -Model small
    .\pobierz_model.ps1 -Katalog "C:\whisper-automat\models"
#>

[CmdletBinding()]
param(
    [ValidateSet("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")]
    [string]$Model = "large-v3-turbo",

    [string]$Katalog = "$env:LOCALAPPDATA\WhisperAutomat\models",

    # Pomija sprawdzanie certyfikatu serwera. Potrzebne tam, gdzie firewall
    # rozcina ruch HTTPS (FortiGate, Zscaler), a jego urzedu certyfikacji nie
    # ma w magazynie Windows. Bezpieczenstwo nie znika: pobrane pliki sa
    # porownywane z suma kontrolna wpisana ponizej na sztywno, wiec podmiana
    # pliku zostanie wykryta.
    [switch]$BezWeryfikacjiTLS
)

$ErrorActionPreference = "Stop"

# Sumy kontrolne policzone na sprawdzonym komputerze i przekazane poza siecia.
# To one, a nie certyfikat, gwarantuja autentycznosc przy -BezWeryfikacjiTLS.
$sumyKontrolne = @{
    "tiny" = @{
        "config.json"    = "a73a28cdfe1c43ccc7202fa333d1f89c202477271407ae9a7f19afa52039cac8"
        "model.bin"      = "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1"
        "tokenizer.json" = "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"
        "vocabulary.txt" = "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"
    }
    "large-v3-turbo" = @{
        "config.json"              = "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e"
        "model.bin"                = "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"
        "preprocessor_config.json" = "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"
        "tokenizer.json"           = "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd"
        "vocabulary.json"          = "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"
    }
}

$repozytoria = @{
    "tiny"           = "Systran/faster-whisper-tiny"
    "base"           = "Systran/faster-whisper-base"
    "small"          = "Systran/faster-whisper-small"
    "medium"         = "Systran/faster-whisper-medium"
    "large-v3"       = "Systran/faster-whisper-large-v3"
    "large-v3-turbo" = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
}

$repo = $repozytoria[$Model]
$cel = Join-Path $Katalog $Model

Write-Host ""
Write-Host "======================================================================"
Write-Host "  Pobieranie modelu Whispera"
Write-Host "======================================================================"
Write-Host "  Model      : $Model"
Write-Host "  Zrodlo     : huggingface.co/$repo"
Write-Host "  Zapis do   : $cel"
Write-Host ""

$curl = Join-Path $env:SystemRoot "System32\curl.exe"
$opcjeTLS = @()
if ($BezWeryfikacjiTLS) {
    $opcjeTLS = @("-k")
    Write-Host "  UWAGA: weryfikacja certyfikatu pominieta." -ForegroundColor Yellow
    Write-Host "         Autentycznosc sprawdzimy suma kontrolna po pobraniu."
    Write-Host ""
}
if (-not (Test-Path $curl)) {
    Write-Host "  Nie znaleziono curl.exe (jest w Windows 10 od wersji 1803)." -ForegroundColor Red
    Write-Host "  Zaktualizuj system albo skopiuj folder modelu z innego komputera."
    exit 1
}

New-Item -ItemType Directory -Force -Path $cel | Out-Null

# Zestaw plikow rozni sie miedzy modelami: tiny ma vocabulary.txt i nie ma
# preprocessor_config.json, wieksze modele odwrotnie. Zamiast zgadywac,
# pytamy repozytorium, co w nim jest.
Write-Host "  Sprawdzam zawartosc repozytorium..."
$odpowiedz = & $curl -L --fail -s @opcjeTLS "https://huggingface.co/api/models/$repo"
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Nie udalo sie polaczyc z huggingface.co (kod $LASTEXITCODE)." -ForegroundColor Red
    Write-Host "  Sprawdz polaczenie i ustawienia firewalla."
    exit 1
}

$pliki = ($odpowiedz | ConvertFrom-Json).siblings.rfilename |
    Where-Object { $_ -notlike ".*" -and $_ -ne "README.md" }

if (-not $pliki -or $pliki.Count -eq 0) {
    Write-Host "  Repozytorium nie zwrocilo listy plikow." -ForegroundColor Red
    exit 1
}
Write-Host "  Do pobrania: $($pliki.Count) plik(ow)"
Write-Host ""

$licznik = 0
foreach ($plik in $pliki) {
    $licznik++
    $url = "https://huggingface.co/$repo/resolve/main/$plik"
    $wyjscie = Join-Path $cel $plik

    Write-Host "  [$licznik/$($pliki.Count)] $plik"
    & $curl -L --fail --progress-bar --retry 3 --retry-delay 2 @opcjeTLS -o $wyjscie $url
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "  Nie udalo sie pobrac $plik (kod $LASTEXITCODE)." -ForegroundColor Red
        Write-Host "  Sprawdz, czy huggingface.co nie jest zablokowane przez firewall."
        exit 1
    }
}

Write-Host ""
Write-Host "  Pobrane pliki:"
$suma = 0
foreach ($plik in $pliki) {
    $f = Get-Item (Join-Path $cel $plik)
    $suma += $f.Length
    "    {0,-28} {1,10:N1} MB" -f $f.Name, ($f.Length / 1MB) | Write-Host
}

Write-Host ""
$oczekiwane = $sumyKontrolne[$Model]
if ($oczekiwane) {
    Write-Host "  Sprawdzam sumy kontrolne..."
    $bledy = 0
    foreach ($plik in $pliki) {
        if (-not $oczekiwane.ContainsKey($plik)) { continue }
        $sciezka = Join-Path $cel $plik
        $hash = (Get-FileHash -Path $sciezka -Algorithm SHA256).Hash.ToLower()
        if ($hash -eq $oczekiwane[$plik]) {
            Write-Host "    OK    $plik" -ForegroundColor Green
        } else {
            Write-Host "    BLAD  $plik - suma sie nie zgadza!" -ForegroundColor Red
            Write-Host "          oczekiwano: $($oczekiwane[$plik])"
            Write-Host "          otrzymano : $hash"
            $bledy++
        }
    }
    if ($bledy -gt 0) {
        Write-Host ""
        Write-Host "  Pliki NIE sa zgodne z oryginalem. Usun folder i sprobuj ponownie." -ForegroundColor Red
        Write-Host "  Jesli blad sie powtarza, pobierz model na innym komputerze i skopiuj."
        exit 1
    }
    Write-Host ""
} elseif ((Get-Item (Join-Path $cel "model.bin")).Length -lt 30MB) {
    Write-Host "  UWAGA: model.bin jest podejrzanie maly - pobieranie moglo sie" -ForegroundColor Yellow
    Write-Host "  urwac. Usun folder i uruchom skrypt ponownie."
    exit 1
}

Write-Host "======================================================================"
Write-Host ("  GOTOWE - {0:N2} GB. Uruchom program, model jest juz na miejscu." -f ($suma / 1GB))
Write-Host "======================================================================"
Write-Host ""
