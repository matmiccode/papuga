; Instalator programu — wydanie firmowe (Whisper Automat) albo publiczne
; (Papuga). Wywoływany przez tools/build_exe.py, który podaje nazwę,
; identyfikator i ścieżki przez /D...

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef AppName
  #define AppName "Whisper Automat"
#endif
; Nazwa techniczna: plik .exe i katalog danych w %LOCALAPPDATA%.
#ifndef AppFullName
  #define AppFullName AppName
#endif
#ifndef AppFile
  #define AppFile "WhisperAutomat"
#endif
; Identyfikator bez nawiasów klamrowych. To on decyduje, czy Windows uznaje
; instalację za tę samą aplikację — każde wydanie ma własny, więc wydanie
; firmowe i publiczne mogą stać obok siebie.
#ifndef AppGuid
  #define AppGuid "7C2F1A64-5D3B-4E82-9A17-6B0E4C9D2F31"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\" + AppFile
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef AssetsDir
  #define AssetsDir "..\assets"
#endif
#ifndef OutputName
  #define OutputName AppFile + "-" + AppVersion + "-Setup"
#endif
; "lekki" (model pobierany przy pierwszym uruchomieniu) albo "offline"
; (model w środku). Trafia do opisu pliku, żeby było widać, co jest czym.
#ifndef Wariant
  #define Wariant "lekki"
#endif
; 1 = instalacja dla bieżącego użytkownika, bez uprawnień administratora
; (od 2026-10-09 oba wydania; do 1.3.0 wydanie firmowe szło do Program
; Files). Papuga aktualizuje się sama, a bez tego każda aktualizacja
; pytałaby o zgodę administratora; w firmie pracownik hasła administratora
; nie ma wcale. 0 = domyślnie dla wszystkich użytkowników (Program Files).
#ifndef PerUser
  #define PerUser "1"
#endif

#define AppExe AppFile + ".exe"

[Setup]
AppId={{{#AppGuid}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppCopyright={#AppCopyright}
#ifdef AppUrl
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
#endif
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} — instalator (wersja {#Wariant})
VersionInfoCopyright={#AppCopyright}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayName={#AppFullName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
SetupIconFile={#AssetsDir}\icon.ico
WizardStyle=modern
; Język instalatora: polski Windows → polski, każdy inny → angielski,
; bez pytania (jak w programie: pigułka PL/EN w oknie).
ShowLanguageDialog=auto
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Biblioteki CUDA to ponad 2 GB DLL-i. lzma2/max ściska je mocno przy
; sensownym czasie budowy; ultra64 zyskuje jeszcze kilka procent, ale
; kompresja trwa wtedy wielokrotnie dłużej.
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4

; Oba wydania instalują się w profilu użytkownika ({localappdata}\Programs)
; i o nic nie pytają: bez okienka „dla mnie / dla wszystkich” nikt nie
; wybierze przypadkiem Program Files, a z nim pytania UAC przy instalacji
; i każdej aktualizacji. Bez `dialog` Inno nie zagląda też do trybu
; poprzedniej instalacji (UsePreviousPrivileges działa tylko z dialogiem),
; więc stara kopia firmowa w Program Files nie wciąga instalatora z powrotem
; w tryb administratora — zdejmuje ją PrepareToInstall w [Code].
; `commandline` pozwala wymusić tryb przełącznikiem /ALLUSERS (Program
; Files, UAC), co przydaje się przy cichym wdrożeniu przez dział IT:
;   <instalator>.exe /VERYSILENT /ALLUSERS /NORESTART
#if PerUser == "1"
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
#else
PrivilegesRequiredOverridesAllowed=commandline dialog
#endif

[Languages]
Name: "polski"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
#ifdef AppDescription
polski.WelcomeLabel2={#AppDescription}%n%nAplikacja [name/ver] zostanie teraz zainstalowana na komputerze.%n%nZalecane jest zamknięcie wszystkich innych uruchomionych programów przed rozpoczęciem procesu instalacji.
#endif
#ifdef AppDescriptionEn
english.WelcomeLabel2={#AppDescriptionEn}%n%nThis will install [name/ver] on your computer.%n%nIt is recommended that you close all other applications before continuing.
#endif

; Napisy instalatora w obu językach; {cm:...} wybiera według języka.
[CustomMessages]
polski.SkrotPulpit=Utwórz skrót na pulpicie
english.SkrotPulpit=Create a desktop shortcut
polski.Skroty=Skróty:
english.Skroty=Shortcuts:
polski.Diagnostyka=Diagnostyka {#AppName}
english.Diagnostyka={#AppName} diagnostics
polski.Odinstaluj=Odinstaluj {#AppName}
english.Odinstaluj=Uninstall {#AppName}
polski.Uruchom=Uruchom {#AppName}
english.Uruchom=Launch {#AppName}
polski.MigracjaPytanie=Na tym komputerze jest wcześniejsza instalacja programu {#AppName} dla wszystkich użytkowników (w Program Files). Nowe wersje instalują się w profilu użytkownika i aktualizują bez uprawnień administratora, więc stara kopia zostanie teraz odinstalowana. Windows poprosi o zgodę administratora ten jeden raz.%n%nUstawienia, modele i transkrypcje zostają. Zamknij program {#AppName}, jeśli jest uruchomiony, i kliknij OK.
english.MigracjaPytanie=This computer has an earlier installation of {#AppName} for all users (in Program Files). New versions install in the user profile and update without administrator rights, so the old copy will be uninstalled now. Windows will ask for administrator consent this one time.%n%nSettings, models and transcripts stay. Close {#AppName} if it is running, then click OK.
polski.MigracjaPrzerwana=Instalacja przerwana — stara kopia w Program Files zostaje.
english.MigracjaPrzerwana=Installation cancelled — the old copy in Program Files stays.
polski.MigracjaBlad=Nie udało się odinstalować starej kopii z Program Files (brak zgody administratora?). Odinstaluj „{#AppName}” w Ustawieniach systemu Windows (Aplikacje) i uruchom ten instalator ponownie.
english.MigracjaBlad=Could not uninstall the old copy from Program Files (administrator consent denied?). Uninstall “{#AppName}” in Windows Settings (Apps) and run this installer again.
polski.UsunDane=Usunąć także pobrane modele i ustawienia programu?%n%n%1%n%nWybierz „Nie”, jeśli zamierzasz zainstalować program ponownie — model nie będzie wtedy pobierany drugi raz. Twoje transkrypcje nie zostaną usunięte.
english.UsunDane=Also remove the downloaded models and the app settings?%n%n%1%n%nChoose “No” if you plan to reinstall the app — the model will not be downloaded again. Your transcripts will not be removed.

[Tasks]
Name: "desktopicon"; Description: "{cm:SkrotPulpit}"; \
    GroupDescription: "{cm:Skroty}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:Diagnostyka}"; Filename: "{app}\{#AppExe}"; \
    Parameters: "--doctor"; IconFilename: "{app}\{#AppExe}"
Name: "{group}\{cm:Odinstaluj}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; \
    Description: "{cm:Uruchom}"; \
    Flags: nowait postinstall skipifsilent
; Aktualizacja z programu idzie w trybie cichym, więc powyższy wpis jest
; pomijany. Ten uruchamia program z powrotem — jako zwykły użytkownik,
; nawet gdy instalator działał z uprawnieniami administratora.
Filename: "{app}\{#AppExe}"; \
    Flags: nowait runasoriginaluser; Check: PoAktualizacji

[UninstallDelete]
; Katalog roboczy w profilu użytkownika (modele, ustawienia, pliki tymczasowe)
; nie jest usuwany automatycznie — pobrany model waży 1,6 GB i przy ponownej
; instalacji albo aktualizacji nie trzeba go ściągać jeszcze raz. O jego
; usunięcie pytamy niżej, w [Code].
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

[Code]
// Instalator uruchomiony przez program z przełącznikiem /AKTUALIZACJA=1.
function PoAktualizacji: Boolean;
begin
  Result := ExpandConstant('{param:AKTUALIZACJA|0}') = '1';
end;

// Tekst z [CustomMessages] w języku instalatora, z %n jako nową linią
// i %1 podmienionym na argument (MsgBox nie rozumie %n).
function Komunikat(Nazwa, Arg: String): String;
begin
  Result := CustomMessage(Nazwa);
  StringChangeEx(Result, '%n', #13#10, True);
  StringChangeEx(Result, '%1', Arg, True);
end;

// Wydanie firmowe do 1.3.0 instalowało się do Program Files (tryb
// administratora). Od 2026-10-09 oba wydania stoją w profilu użytkownika,
// więc stara kopia z Program Files musi zejść — inaczej zostałyby dwie
// (dwa wpisy w Aplikacjach, dwa komplety skrótów). Windows pyta o zgodę
// administratora ten jeden raz; kolejne instalacje i aktualizacje już nie.
// Ustawień i modeli w %LOCALAPPDATA% stary deinstalator po cichu nie rusza.
function StaraKopiaDlaWszystkich(var Deinstalator: String): Boolean;
var
  Klucz, Polecenie: String;
begin
  Result := False;
  Klucz := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{'
           + '{#AppGuid}' + '}_is1';
  if not RegQueryStringValue(HKLM64, Klucz, 'UninstallString', Polecenie) then
    Exit;
  Deinstalator := RemoveQuotes(Trim(Polecenie));
  // Wpis bez pliku to sierota po ręcznym skasowaniu katalogu — nie ma
  // czego odinstalowywać.
  Result := (Deinstalator <> '') and FileExists(Deinstalator);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Deinstalator: String;
  Kod: Integer;
begin
  Result := '';
  // /ALLUSERS: instalacja dalej w Program Files, zwykła aktualizacja w miejscu.
  if IsAdminInstallMode then
    Exit;
  if not StaraKopiaDlaWszystkich(Deinstalator) then
    Exit;
  if not WizardSilent then
    if MsgBox(Komunikat('MigracjaPytanie', ''), mbConfirmation, MB_OKCANCEL) <> IDOK then
    begin
      Result := Komunikat('MigracjaPrzerwana', '');
      Exit;
    end;
  Log('Odinstalowuję starą kopię dla wszystkich użytkowników: ' + Deinstalator);
  if not ShellExec('runas', Deinstalator, '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES',
                   ExtractFileDir(Deinstalator), SW_HIDE, ewWaitUntilTerminated, Kod) then
  begin
    Result := Komunikat('MigracjaBlad', '');
    Exit;
  end;
  if Kod <> 0 then
    Log(Format('Stary deinstalator zakończył się kodem %d', [Kod]));
  // Deinstalator Inno pracuje z kopii w TEMP i chwilę po powrocie jeszcze
  // sprząta — krótka pauza, zanim zaczniemy kopiować pliki.
  Sleep(1500);
end;

// Po odinstalowaniu pyta, czy usunąć też pobrane modele i ustawienia.
// Domyślnie „Nie” — ktoś, kto odinstalowuje przed instalacją nowej wersji,
// nie powinien przypadkiem stracić modelu. Ciche odinstalowanie nie pyta
// i niczego z profilu nie rusza. Transkrypcje leżą w Dokumentach i nigdy
// nie są tu usuwane.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Dane: String;
begin
  if (CurUninstallStep <> usPostUninstall) or UninstallSilent then
    Exit;
  Dane := ExpandConstant('{localappdata}\{#AppFile}');
  if not DirExists(Dane) then
    Exit;
  if MsgBox(Komunikat('UsunDane', Dane),
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    DelTree(Dane, True, True, True);
end;
