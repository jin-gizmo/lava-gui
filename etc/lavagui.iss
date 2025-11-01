; Lava GUI installer script for Inno Setup
; ex: nu ft=iss

; Version should be defined on command line with /Dversion=...
#ifndef Version
#define public Version '0.0.0'
#endif
#ifndef BaseDir
; This is relative to the scriptdir
#define public BaseDir '..'
#endif

#define public Exe 'lavagui'
#define public Product 'Lava'

[Setup]
AppName={#Product}
AppVersion={#Version}
DefaultDirName={autopf}\{#Product}
DefaultGroupName={#Product}
OutputDir={#BaseDir}
SourceDir={#BaseDir}\build
OutputBaseFilename={#Exe}-{#Version}-windows-x64
Compression=lzma2/ultra
SolidCompression=yes
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#Exe}.exe
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

[Files]
; Include everything from the current build directory recursively
Source: "*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#Product}"; Filename: "{app}\{#Exe}.exe"
Name: "{commondesktop}\{#Product}"; Filename: "{app}\{#Exe}.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Run]
Filename: "{app}\{#Exe}.exe"; Description: "Launch {#Product} GUI"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpWelcome then
  begin
    MsgBox(
    'The Flet based version of the {#Product} GUI was built by '#13#10 +
    '  - Art Dorokhov'#13#10 +
    '  - Nyan Min Khant'#13#10 +
    '  - Alex Boul'#13#10 +
    ''#13#10#13#10 +
    'It is based on the epically ugly PySimpleGui original, but is a lot better.',
      mbInformation, MB_OK);
  end;
  if CurPageID = wpFinished then
  begin
    MsgBox('⚠️ The first launch of the {#Product} GUI will be *very slow* as it performs initial setup. Be patient.',
    mbInformation, MB_OK)
  end;
end;
