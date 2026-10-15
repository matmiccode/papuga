<#
Zabezpiecza folder firmowy z wydaniami (udział sieciowy) uprawnieniami NTFS.

Cel: każdy pracownik może uruchomić instalator, przeczytać najnowsza.json
i bazę głosów, dodać WŁASNY głos do bazy — ale nie może usunąć instalatora,
podmienić go ani niczego innego w folderze.

Schemat (SID-y są niezależne od języka Windows):
  <folder>            Wszyscy (S-1-1-0): odczyt + wykonanie, dziedziczone
                      autor (SID z -Autor): pełna kontrola, dziedziczona
                      administrator (SID z -Admin, opcjonalnie): pełna kontrola
                      dziedziczenie z udziału wyłączone (bez „Wszyscy: pełna”)
  <folder>\Glosy      jak wyżej, plus:
                      Wszyscy: tworzenie plików w tym folderze
                      TWÓRCA-WŁAŚCICIEL (S-1-3-0): modyfikacja własnych plików
                        (dziedziczone tylko na pliki)

Uprawnienia idą przez SDDL (.NET DirectorySecurity.SetSecurityDescriptorSddlForm),
bo icacls i Set-Acl na komputerze spoza domeny nie potrafią „przetłumaczyć”
domenowych SID-ów i odmawiają (sprawdzone 2026-10-15). SDDL przyjmuje surowe
SID-y. icacls zostaje do kopii uprawnień i do resetu plików w środku.

Uwaga (zmierzone 2026-10-15 na NAS-ie firmy): serwer dokłada do „tworzenie
plików” w Glosy także „usuwanie plików w folderze” (semantyka POSIX: zapis
w katalogu = dodawanie i usuwanie wpisów). Cudzy wpis w Glosy da się więc
skasować — stąd kopia bazy głosów przy każdym wydaniu (publikuj_wydanie.py).
Instalatora w folderze nadrzędnym to nie dotyczy: tam Wszyscy mają sam odczyt.

Przed zmianą zapisuje bieżące uprawnienia do pliku (icacls /save), żeby dało
się wrócić:  icacls "<nadrzędny>" /restore <plik>

Uruchomienie (konto, które ma pełną kontrolę nad folderem — autor):
  powershell -ExecutionPolicy Bypass -File tools\zabezpiecz_folder.ps1 `
      -Folder "\\serwer\udzial\Whisper Automat" `
      -Autor  "S-1-5-21-…-1145" [-Admin "S-1-5-21-…-1002"] [-TylkoPokaz]

-TylkoPokaz wypisuje, co by zrobił, niczego nie zmienia.
#>
param(
    [Parameter(Mandatory = $true)] [string] $Folder,
    [Parameter(Mandatory = $true)] [string] $Autor,
    [string] $Admin = "",
    [switch] $TylkoPokaz
)

$ErrorActionPreference = "Stop"
$glosy = Join-Path $Folder "Glosy"
$kopia = Join-Path $env:LOCALAPPDATA ("WhisperAutomat-acl-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".txt")

function Icacls([string[]] $argumenty) {
    Write-Host ("  icacls " + ($argumenty -join " "))
    if (-not $TylkoPokaz) {
        $wynik = & icacls @argumenty 2>&1
        if ($LASTEXITCODE -ne 0) { throw ("icacls zwrocil " + $LASTEXITCODE + ": " + ($wynik -join " ")) }
    }
}

function UstawDacl([string] $sciezka, [string] $sddl) {
    Write-Host ("  DACL " + $sciezka)
    Write-Host ("       " + $sddl)
    if (-not $TylkoPokaz) {
        $acl = New-Object System.Security.AccessControl.DirectorySecurity
        $acl.SetSecurityDescriptorSddlForm($sddl, "Access")
        (Get-Item -LiteralPath $sciezka).SetAccessControl($acl)
    }
}

if (-not (Test-Path -LiteralPath $Folder)) { throw "Nie ma folderu: $Folder" }
Write-Host "Folder : $Folder"
Write-Host "Autor  : $Autor"
if ($Admin) { Write-Host "Admin  : $Admin" }
Write-Host "Kopia uprawnien: $kopia"
Write-Host ""

# 1. Kopia zapasowa uprawnien (folder + wszystko w srodku).
Icacls @($Folder, "/save", $kopia, "/T", "/C")

# 2. Folder wydan: wlasny zestaw bez dziedziczenia z udzialu (P = protected).
#    0x1200a9 = odczyt + wykonanie; FA = pelna kontrola; OICI = dziedziczone.
$sddl = "D:PAI(A;OICI;0x1200a9;;;WD)(A;OICI;FA;;;$Autor)"
if ($Admin) { $sddl += "(A;OICI;FA;;;$Admin)" }
UstawDacl $Folder $sddl

# 3. Wszystko w srodku: tylko to, co dziedziczy z folderu (reset dzieci,
#    nie samego folderu).
Icacls @((Join-Path $Folder "*"), "/reset", "/T", "/C")

# 4. Glosy: 0x2 = tworzenie plikow (ten folder); CO = TWORCA-WLASCICIEL,
#    0x1301bf = modyfikacja, OIIO = tylko na pliki w srodku. AI = plus to,
#    co dziedziczy z folderu nadrzednego.
if (-not (Test-Path -LiteralPath $glosy)) {
    Write-Host "  mkdir $glosy"
    if (-not $TylkoPokaz) { New-Item -ItemType Directory -Path $glosy | Out-Null }
}
UstawDacl $glosy "D:AI(A;;0x2;;;WD)(A;OIIO;0x1301bf;;;CO)"

Write-Host ""
Write-Host "Gotowe. Sprawdz:"
Write-Host "  (Get-Acl '$Folder').Access | Format-Table"
Write-Host "  (Get-Acl '$glosy').Access | Format-Table"
Write-Host "Powrot do poprzednich uprawnien:"
Write-Host "  icacls `"$(Split-Path -Parent $Folder)`" /restore `"$kopia`""
