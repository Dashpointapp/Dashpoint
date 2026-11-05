#define MyAppName "Dashpoint"
#define MyAppVersion "3.0"
#define MyAppPublisher "Michal Medvecký"
#define MyAppExeName "Dashpoint.exe"

[Setup]
AppId={{A57BD011-9DBB-4DBE-954D-D2B099A7A761}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}

; Instalace pro aktuálního uživatele
DefaultDirName={localappdata}\Programs\{#MyAppName}
DisableDirPage=no
PrivilegesRequired=lowest

; Vzhled
WizardStyle=modern

; Výstup instalátoru
OutputDir=C:\DashpointInstaller\Output
OutputBaseFilename=Dashpoint_Setup_v3.0_NEW

; Ikona instalátoru
SetupIconFile=C:\DashpointInstaller\ikonka.ico

; Komprese
Compression=lzma2
SolidCompression=yes

; Odinstalace
Uninstallable=yes
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}

; Nabídka Start
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes


[Languages]
Name: "czech"; MessagesFile: "compiler:Languages\Czech.isl"


[Tasks]
Name: "desktopicon"; Description: "Vytvořit zástupce na ploše"; GroupDescription: "Další možnosti:"; Flags: checkedonce


[Files]

; DŮLEŽITÉ:
; Použij přesně EXE vytvořené aktuálním PyInstaller buildem
Source: "C:\DashpointInstaller\dist\Dashpoint.exe"; DestDir: "{app}"; DestName: "Dashpoint.exe"; Flags: ignoreversion


[Icons]

; Nabídka Start
Name: "{autoprograms}\Dashpoint"; Filename: "{app}\Dashpoint.exe"; WorkingDir: "{app}"

; Plocha
Name: "{autodesktop}\Dashpoint"; Filename: "{app}\Dashpoint.exe"; WorkingDir: "{app}"; Tasks: desktopicon


[Run]

; Po dokončení instalace nabídnout spuštění programu
Filename: "{app}\Dashpoint.exe"; Description: "Spustit Dashpoint"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent