; ══════════════════════════════════════════════════════
; LinguaTaxi Installer — Inno Setup Script
;
; Everything is pre-built by build.bat. This installer
; copies files, fixes venv paths, and optionally
; pre-downloads the speech recognition model.
;
; Editions:
;   Full — GPU (faster-whisper + CUDA) + CPU (Vosk)
;   Lite — CPU only (Vosk)
;
; build.bat compiles both automatically. To compile
; manually:  ISCC /DEDITION=Lite installer.iss
;            ISCC /DEDITION=Full installer.iss
; ══════════════════════════════════════════════════════

; ── Edition selector (passed by build.bat via /DEDITION=...) ──
#ifndef EDITION
  #define EDITION "Full"
#endif

#define MyAppName "LinguaTaxi - Live Caption and Translation"
#define MyAppShortName "LinguaTaxi"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "LinguaTaxi"
#define MyAppURL "https://github.com/linguataxi"

; ── Edition-specific output filename and venv source ──
#if EDITION == "Lite"
  #define OutputName "LinguaTaxi-Lite-Setup-" + MyAppVersion
  #define VenvSrc "venv_lite"
#else
  #define OutputName "LinguaTaxi-Setup-" + MyAppVersion
  #define VenvSrc "venv_full"
#endif

[Setup]
AppId={{B8A5C2E1-4F3D-4A7B-9E2C-1D3F5A6B7C8D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppShortName}
DisableProgramGroupPage=yes
OutputDir=..\..\dist
OutputBaseFilename={#OutputName}
#ifexist "..\..\assets\linguataxi.ico"
SetupIconFile=..\..\assets\linguataxi.ico
#endif
UninstallDisplayIcon={app}\assets\linguataxi.ico
UninstallDisplayName={#MyAppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce
Name: "downloadmodel"; Description: "Download voice recognition model during installation (~40 MB, recommended)"; GroupDescription: "Model setup:"; Flags: checkedonce

[Files]
; ── Core application ──
Source: "..\..\server.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\launcher.pyw"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\download_models.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\display.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\operator.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dictation.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

; ── NVIDIA notice (Full edition only) ──
#if EDITION == "Full"
Source: "..\..\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
#endif

; ── Assets ──
Source: "..\..\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs

; ── Pre-built Python runtime (from build.bat) ──
Source: ".\python_dist\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs

; ── Pre-built venv (edition-specific: venv_lite or venv_full) ──
Source: ".\{#VenvSrc}\*"; DestDir: "{app}\venv"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{app}\uploads"
Name: "{app}\models"

[Icons]
Name: "{group}\{#MyAppShortName}"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\linguataxi.ico"; Comment: "Launch LinguaTaxi"
Name: "{group}\Uninstall {#MyAppShortName}"; Filename: "{uninstallexe}"; IconFilename: "{app}\assets\linguataxi.ico"
Name: "{autodesktop}\{#MyAppShortName}"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\linguataxi.ico"; Tasks: desktopicon

[Run]
; Download model during install (shows console with download progress)
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\download_models.py"""; WorkingDir: "{app}"; Tasks: downloadmodel; StatusMsg: "Downloading voice recognition model — this may take a few minutes..."
; Launch after install
Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; Description: "Launch {#MyAppShortName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM pythonw.exe /FI ""WINDOWTITLE eq LinguaTaxi*"" 2>nul"; Flags: runhidden; RunOnceId: "KillLinguaTaxi"

[Code]
// ── Fix venv paths after file copy ──
// The venv was built on the build machine with different paths.
// We rewrite pyvenv.cfg to point to the installed Python location.
// This takes milliseconds and is invisible to the user.

procedure CurStepChanged(CurStep: TSetupStep);
var
  CfgPath: String;
  PythonHome: String;
begin
  if CurStep = ssPostInstall then
  begin
    CfgPath := ExpandConstant('{app}\venv\pyvenv.cfg');
    PythonHome := ExpandConstant('{app}\python');
    SaveStringToFile(CfgPath,
      'home = ' + PythonHome + #13#10 +
      'include-system-site-packages = false' + #13#10 +
      'version = 3.11.9' + #13#10,
      False);
  end;
end;

// ── Uninstall: preserve checkboxes ──

var
  KeepTranscriptsCheck: TNewCheckBox;
  KeepModelsCheck: TNewCheckBox;

procedure InitializeUninstallProgressForm();
var
  InfoLabel: TNewStaticText;
begin
  InfoLabel := TNewStaticText.Create(UninstallProgressForm);
  InfoLabel.Parent := UninstallProgressForm.InstallingPage;
  InfoLabel.Top := 10;
  InfoLabel.Left := 0;
  InfoLabel.Width := UninstallProgressForm.InstallingPage.Width;
  InfoLabel.WordWrap := True;
  InfoLabel.Caption :=
    'Choose which data to preserve for future reinstallation:';

  KeepTranscriptsCheck := TNewCheckBox.Create(UninstallProgressForm);
  KeepTranscriptsCheck.Parent := UninstallProgressForm.InstallingPage;
  KeepTranscriptsCheck.Top := InfoLabel.Top + InfoLabel.Height + 16;
  KeepTranscriptsCheck.Left := 8;
  KeepTranscriptsCheck.Width := UninstallProgressForm.InstallingPage.Width - 16;
  KeepTranscriptsCheck.Caption := 'Keep transcript files (in Documents\LinguaTaxi Transcripts)';
  KeepTranscriptsCheck.Checked := True;

  KeepModelsCheck := TNewCheckBox.Create(UninstallProgressForm);
  KeepModelsCheck.Parent := UninstallProgressForm.InstallingPage;
  KeepModelsCheck.Top := KeepTranscriptsCheck.Top + KeepTranscriptsCheck.Height + 8;
  KeepModelsCheck.Left := 8;
  KeepModelsCheck.Width := UninstallProgressForm.InstallingPage.Width - 16;
  KeepModelsCheck.Caption := 'Keep downloaded voice recognition models (~40MB - 1.8GB)';
  KeepModelsCheck.Checked := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  TranscriptsDir, ModelsDir, AppDataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if not KeepModelsCheck.Checked then
    begin
      ModelsDir := ExpandConstant('{app}\models');
      if DirExists(ModelsDir) then
        DelTree(ModelsDir, True, True, True);
    end;

    if not KeepTranscriptsCheck.Checked then
    begin
      TranscriptsDir := ExpandConstant('{userdocs}\LinguaTaxi Transcripts');
      if DirExists(TranscriptsDir) then
        DelTree(TranscriptsDir, True, True, True);
    end;

    AppDataDir := ExpandConstant('{userappdata}\LinguaTaxi');
    if DirExists(AppDataDir) then
      DelTree(AppDataDir, True, True, True);

    if DirExists(ExpandConstant('{app}\venv')) then
      DelTree(ExpandConstant('{app}\venv'), True, True, True);

    if DirExists(ExpandConstant('{app}\python')) then
      DelTree(ExpandConstant('{app}\python'), True, True, True);

    if DirExists(ExpandConstant('{app}\__pycache__')) then
      DelTree(ExpandConstant('{app}\__pycache__'), True, True, True);
  end;
end;
