[Setup]
AppName=World Conquest
AppVersion=3.0
DefaultDirName={autopf}\World Conquest
DefaultGroupName=World Conquest
OutputDir=installer
OutputBaseFilename=WorldConquest-Setup
SetupIconFile=assets\icons\game.ico
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\WorldConquest.exe

[Files]
Source: "dist\WorldConquest.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\World Conquest"; Filename: "{app}\WorldConquest.exe"
Name: "{autodesktop}\World Conquest"; Filename: "{app}\WorldConquest.exe"

[Run]
Filename: "{app}\WorldConquest.exe"; Description: "Запустить игру"; Flags: nowait postinstall skipifsilent
