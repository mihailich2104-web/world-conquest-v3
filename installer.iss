; Inno Setup: установщик World Conquest (берёт dist\WorldConquest\*)
#define AppName "World Conquest"
#define AppVer "1.0.0"

[Setup]
AppId={{B7C2E1A4-5D3F-4E8A-9A61-7C0F2D4B8E15}
AppName={#AppName}
AppVersion={#AppVer}
DefaultDirName={autopf}\WorldConquest
DefaultGroupName={#AppName}
OutputDir=installer
OutputBaseFilename=WorldConquest-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
UninstallDisplayIcon={app}\WorldConquest.exe
#ifexist "assets\icons\game.ico"
SetupIconFile=assets\icons\game.ico
#endif

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; Flags: unchecked

[Files]
Source: "dist\WorldConquest\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\WorldConquest.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\WorldConquest.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\WorldConquest.exe"; Description: "Запустить игру"; Flags: nowait postinstall skipifsilent
