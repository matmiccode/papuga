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
; 1 = domyślnie instalacja dla bieżącego użytkownika, bez uprawnień
; administratora. Wydanie publiczne aktualizuje się samo, a bez tego każda
; aktualizacja pytałaby o zgodę administratora.
#ifndef PerUser
  #define PerUser "0"
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
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Biblioteki CUDA to ponad 2 GB DLL-i. lzma2/max ściska je mocno przy
; sensownym czasie budowy; ultra64 zyskuje jeszcze kilka procent, ale
; kompresja trwa wtedy wielokrotnie dłużej.
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4

; Wydanie firmowe instaluje się domyślnie do Program Files (wymaga
; uprawnień administratora); użytkownik bez nich dostanie propozycję
; instalacji w swoim profilu.
; Wydanie publiczne instaluje się zawsze w profilu i o nic nie pyta: bez
; okienka „dla mnie / dla wszystkich” nikt nie wybierze przypadkiem
; Program Files, a z nim pytania UAC przy instalacji i każdej aktualizacji.
; `commandline` pozwala wymusić tryb przełącznikiem /CURRENTUSER albo
; /ALLUSERS, co przydaje się przy cichym wdrożeniu:
;   <instalator>.exe /VERYSILENT /CURRENTUSER /NORESTART
#if PerUser == "1"
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
#else
PrivilegesRequiredOverridesAllowed=commandline dialog
#endif

[Languages]
Name: "polski"; MessagesFile: "compiler:Languages\Polish.isl"

#ifdef AppDescription
[Messages]
WelcomeLabel2={#AppDescription}%n%nAplikacja [name/ver] zostanie teraz zainstalowana na komputerze.%n%nZalecane jest zamknięcie wszystkich innych uruchomionych programów przed rozpoczęciem procesu instalacji.
#endif

[Tasks]
Name: "desktopicon"; Description: "Utwórz skrót na pulpicie"; \
    GroupDescription: "Skróty:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Diagnostyka {#AppName}"; Filename: "{app}\{#AppExe}"; \
    Parameters: "--doctor"; IconFilename: "{app}\{#AppExe}"
Name: "{group}\Odinstaluj {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; \
    Description: "Uruchom {#AppName}"; \
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
  if MsgBox('Usunąć także pobrane modele i ustawienia programu?' + #13#10 + #13#10 +
            Dane + #13#10 + #13#10 +
            'Wybierz „Nie”, jeśli zamierzasz zainstalować program ponownie — ' +
            'model nie będzie wtedy pobierany drugi raz. ' +
            'Twoje transkrypcje nie zostaną usunięte.',
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    DelTree(Dane, True, True, True);
end;
