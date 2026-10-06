<#
.SYNOPSIS
    Sprawdza, dlaczego nie da się pobrać modelu.

.DESCRIPTION
    Komunikat "certyfikat glowny, ktory nie nalezy do zaufanych" ma dwie
    zupelnie rozne przyczyny, wymagajace roznych dzialan:

      1. Siec firmowa podmienia certyfikaty (Fortinet, Zscaler, Sophos,
         program antywirusowy). Wtedy wystawca jest firmowy, a rozwiazaniem
         jest dodanie firmowego urzedu certyfikacji do magazynu Windows.

      2. Windows nie zna publicznego urzedu Amazona, bo automatyczna
         aktualizacja certyfikatow glownych jest wylaczona zasadami grupy.
         Zdarza sie na zablokowanych obrazach firmowych. Przegladarka
         dziala, bo Chrome i Edge maja wlasna liste, ale wszystko, co
         korzysta z magazynu Windows, odmawia.

    Skrypt pokazuje wystawce, cala sciezke certyfikacji i miejsce, w ktorym
    sie urywa, a potem sprawdza, czy cokolwiek na tym komputerze potrafi
    pobrac plik.
#>

$ErrorActionPreference = "Continue"
$url = "https://huggingface.co/api/models/Systran/faster-whisper-tiny"

Write-Host ""
Write-Host "======================================================================"
Write-Host "  Sprawdzanie polaczenia z serwerem modeli"
Write-Host "======================================================================"
Write-Host ""

# --- 1. Jaki certyfikat przedstawia serwer -----------------------------------
Write-Host "  [1] Certyfikat przedstawiany dla huggingface.co"
$cert = $null
try {
    $req = [System.Net.HttpWebRequest]::Create($url)
    $req.Timeout = 20000
    try { $req.GetResponse().Close() } catch { }
    $cert = $req.ServicePoint.Certificate
} catch { }

if (-not $cert) {
    Write-Host "      Nie udalo sie odczytac certyfikatu (brak polaczenia?)."
} else {
    $x = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 $cert
    Write-Host "      Podmiot  : $($x.Subject)"
    Write-Host "      Wystawca : $($x.Issuer)"

    if ($x.Issuer -match "Amazon|DigiCert|Let's Encrypt|Google Trust|Sectigo") {
        Write-Host "      -> Certyfikat prawdziwy, ruch NIE jest podmieniany." -ForegroundColor Green
        Write-Host "         Przyczyna lezy po stronie listy urzedow w Windows."
    } else {
        Write-Host "      -> Wystawca nie jest publicznym urzedem." -ForegroundColor Yellow
        Write-Host "         Ruch przechodzi przez inspekcje TLS (proxy lub antywirus)."
    }

    # --- 2. Gdzie urywa sie sciezka certyfikacji -----------------------------
    Write-Host ""
    Write-Host "  [2] Sciezka certyfikacji"
    $chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
    $chain.ChainPolicy.RevocationMode = "NoCheck"
    $wynik = $chain.Build($x)
    $i = 0
    foreach ($el in $chain.ChainElements) {
        $i++
        Write-Host "      $i. $($el.Certificate.Subject)"
    }
    if ($wynik) {
        Write-Host "      -> Windows uznaje ten lancuch za zaufany." -ForegroundColor Green
    } else {
        Write-Host "      -> Windows odrzuca ten lancuch:" -ForegroundColor Red
        foreach ($s in $chain.ChainStatus) {
            Write-Host "         $($s.Status): $($s.StatusInformation.Trim())"
        }
    }
}

# --- 3. Co faktycznie potrafi pobrac -----------------------------------------
Write-Host ""
Write-Host "  [3] Proby pobrania"

$curl = Join-Path $env:SystemRoot "System32\curl.exe"
if (Test-Path $curl) {
    $kod = & $curl -s -o NUL -w "%{http_code}" --max-time 25 $url 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "      curl.exe          : DZIALA (HTTP $kod)" -ForegroundColor Green
    } else {
        Write-Host "      curl.exe          : odmowa (exit $LASTEXITCODE)" -ForegroundColor Red
    }
} else {
    Write-Host "      curl.exe          : brak w systemie"
}

try {
    $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 25 -ErrorAction Stop
    Write-Host "      Invoke-WebRequest : DZIALA (HTTP $($r.StatusCode))" -ForegroundColor Green
} catch {
    Write-Host "      Invoke-WebRequest : odmowa" -ForegroundColor Red
}

Write-Host ""
Write-Host "======================================================================"
Write-Host "  Co dalej"
Write-Host "======================================================================"
Write-Host "  Jesli cokolwiek w punkcie [3] dziala  -> uruchom pobierz_model.ps1"
Write-Host "  Jesli nic nie dziala                  -> skopiuj gotowy model"
Write-Host "     z komputera, na ktorym program dziala, caly folder:"
Write-Host "     %LOCALAPPDATA%\WhisperAutomat\models"
Write-Host ""
Write-Host "  Wynik punktow [1] i [2] powiedz dzialowi IT - z niego wynika,"
Write-Host "  ktorego certyfikatu brakuje w magazynie Windows."
Write-Host ""
