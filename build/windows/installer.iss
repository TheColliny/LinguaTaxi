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
#define MyAppVersion "1.0.1"
#define MyAppPublisher "LinguaTaxi"
#define MyAppURL "https://github.com/linguataxi"

; ── Edition-specific output filename and venv source ──
#if EDITION == "Lite"
  #define OutputName "LinguaTaxi-CPU-Setup-" + MyAppVersion
  #define EditionLabel "CPU Only"
  #define VenvSrc "venv_lite"
#else
  #define OutputName "LinguaTaxi-GPU-Setup-" + MyAppVersion
  #define EditionLabel "CPU+GPU Best Accuracy"
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
Name: "bulgarian"; MessagesFile: "compiler:Languages\Bulgarian.isl"
Name: "czech"; MessagesFile: "compiler:Languages\Czech.isl"
Name: "danish"; MessagesFile: "compiler:Languages\Danish.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "finnish"; MessagesFile: "compiler:Languages\Finnish.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "hungarian"; MessagesFile: "compiler:Languages\Hungarian.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "dutch"; MessagesFile: "compiler:Languages\Dutch.isl"
Name: "norwegian"; MessagesFile: "compiler:Languages\Norwegian.isl"
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "portuguese"; MessagesFile: "compiler:Languages\Portuguese.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "slovak"; MessagesFile: "compiler:Languages\Slovak.isl"
Name: "slovenian"; MessagesFile: "compiler:Languages\Slovenian.isl"
Name: "swedish"; MessagesFile: "compiler:Languages\Swedish.isl"
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"
; L-BD3: Ukrainian removed — no uk.json locale file exists yet

[CustomMessages]
; Task descriptions
DesktopShortcut=Create a desktop shortcut
UpdateModels=Check for updated voice recognition models (requires internet)
DownloadOffline=Download offline translation models (translate without internet)
DownloadTuned=Download language-tuned voice models (optional)
OpusEs=Spanish — OPUS-MT (~310 MB download, ~75 MB on disk)
OpusFr=French — OPUS-MT (~310 MB download, ~75 MB on disk)
OpusDe=German — OPUS-MT (~310 MB download, ~75 MB on disk)
OpusIt=Italian — OPUS-MT (~310 MB download, ~75 MB on disk)
OpusRu=Russian — OPUS-MT (~310 MB download, ~75 MB on disk)
M2m100=M2M-100 Multilingual (~4.8 GB download, ~1.2 GB on disk, 100 languages)
TunedEs=Spanish tuned model (~1.6 GB download, ~1.6 GB on disk)
TunedFr=French tuned model (~3.1 GB download, ~2.9 GB on disk)
TunedDe=German tuned model (~3.1 GB download, ~2.9 GB on disk)
TunedAr=Arabic tuned model (~3.1 GB download, ~2.9 GB on disk)
TunedJa=Japanese tuned model (~1.5 GB download, ~1.5 GB on disk)
TunedZh=Chinese tuned model (~3.1 GB download, ~2.9 GB on disk)
; Vosk CPU language models
DownloadVosk=Download Vosk CPU language models (for bi-directional translation)
VoskDe=German — Vosk (~45 MB download)
VoskFr=French — Vosk (~41 MB download)
VoskEs=Spanish — Vosk (~39 MB download)
VoskRu=Russian — Vosk (~45 MB download)
VoskAr=Arabic — Vosk (~318 MB download)
VoskJa=Japanese — Vosk (~48 MB download)
VoskZh=Chinese — Vosk (~42 MB download)
VoskModels=Vosk CPU Language Models (for bi-directional mode):
DownloadingVoskDe=Downloading German Vosk model (~45 MB)...
DownloadingVoskFr=Downloading French Vosk model (~41 MB)...
DownloadingVoskEs=Downloading Spanish Vosk model (~39 MB)...
DownloadingVoskRu=Downloading Russian Vosk model (~45 MB)...
DownloadingVoskAr=Downloading Arabic Vosk model (~318 MB)...
DownloadingVoskJa=Downloading Japanese Vosk model (~48 MB)...
DownloadingVoskZh=Downloading Chinese Vosk model (~42 MB)...
; Group descriptions
AdditionalShortcuts=Additional shortcuts:
ModelUpdates=Model updates:
OfflineModels=Offline Translation Models:
TunedModels=Language-tuned models (better accuracy for specific languages):
; Status messages
CheckingModels=Checking for updated voice recognition models...
DownloadingTunedEs=Downloading & converting Spanish tuned model (~1.6 GB)...
DownloadingTunedFr=Downloading & converting French tuned model (~3.1 GB)...
DownloadingTunedDe=Downloading & converting German tuned model (~3.1 GB)...
DownloadingTunedAr=Downloading & converting Arabic tuned model (~3.1 GB)...
DownloadingTunedJa=Downloading & converting Japanese tuned model (~1.5 GB)...
DownloadingTunedZh=Downloading & converting Chinese tuned model (~3.1 GB)...
DownloadingOpusEs=Downloading Spanish OPUS-MT translation model (~310 MB)...
DownloadingOpusFr=Downloading French OPUS-MT translation model (~310 MB)...
DownloadingOpusDe=Downloading German OPUS-MT translation model (~310 MB)...
DownloadingOpusIt=Downloading Italian OPUS-MT translation model (~310 MB)...
DownloadingOpusRu=Downloading Russian OPUS-MT translation model (~310 MB)...
DownloadingM2m=Downloading M2M-100 multilingual model (~4.8 GB, this may take 30-60 minutes)...

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopShortcut}"; GroupDescription: "{cm:AdditionalShortcuts}"; Flags: checkedonce
Name: "updatemodels"; Description: "{cm:UpdateModels}"; GroupDescription: "{cm:ModelUpdates}"; Flags: unchecked
#if EDITION == "Full"
Name: "offline"; Description: "{cm:DownloadOffline}"; GroupDescription: "{cm:OfflineModels}"; Flags: unchecked
Name: "offline\opus_es"; Description: "{cm:OpusEs}"; Flags: unchecked
Name: "offline\opus_fr"; Description: "{cm:OpusFr}"; Flags: unchecked
Name: "offline\opus_de"; Description: "{cm:OpusDe}"; Flags: unchecked
Name: "offline\opus_it"; Description: "{cm:OpusIt}"; Flags: unchecked
Name: "offline\opus_ru"; Description: "{cm:OpusRu}"; Flags: unchecked
Name: "offline\m2m100"; Description: "{cm:M2m100}"; Flags: unchecked
Name: "tuned"; Description: "{cm:DownloadTuned}"; GroupDescription: "{cm:TunedModels}"; Flags: unchecked
Name: "tuned\es"; Description: "{cm:TunedEs}"; Flags: unchecked
Name: "tuned\fr"; Description: "{cm:TunedFr}"; Flags: unchecked
Name: "tuned\de"; Description: "{cm:TunedDe}"; Flags: unchecked
Name: "tuned\ar"; Description: "{cm:TunedAr}"; Flags: unchecked
Name: "tuned\ja"; Description: "{cm:TunedJa}"; Flags: unchecked
Name: "tuned\zh"; Description: "{cm:TunedZh}"; Flags: unchecked
#endif
Name: "vosk_lang"; Description: "{cm:DownloadVosk}"; GroupDescription: "{cm:VoskModels}"; Flags: unchecked
Name: "vosk_lang\de"; Description: "{cm:VoskDe}"; Flags: unchecked
Name: "vosk_lang\fr"; Description: "{cm:VoskFr}"; Flags: unchecked
Name: "vosk_lang\es"; Description: "{cm:VoskEs}"; Flags: unchecked
Name: "vosk_lang\ru"; Description: "{cm:VoskRu}"; Flags: unchecked
Name: "vosk_lang\ar"; Description: "{cm:VoskAr}"; Flags: unchecked
Name: "vosk_lang\ja"; Description: "{cm:VoskJa}"; Flags: unchecked
Name: "vosk_lang\zh"; Description: "{cm:VoskZh}"; Flags: unchecked

[Files]
; ── Core application ──
; NOTE: Canonical package list lives in requirements.txt. Keep file list here
; in sync with build/mac/build.sh when adding new files.
Source: "..\..\server.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\launcher.pyw"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\download_models.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\tuned_models.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\offline_translate.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\plugin_loader.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\display.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\operator.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dictation.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\bidirectional.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\lang_detect.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

; ── NVIDIA notice (Full edition only) ──
#if EDITION == "Full"
Source: "..\..\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
#endif

; ── Locale files ──
Source: "..\..\locales\*"; DestDir: "{app}\locales"; Flags: ignoreversion recursesubdirs createallsubdirs

; ── Static files (plugin dispatcher, panel styles) ──
Source: "..\..\static\*"; DestDir: "{app}\static"; Flags: ignoreversion recursesubdirs createallsubdirs

; ── Plugins ──
Source: "..\..\plugins\*"; DestDir: "{app}\plugins"; Flags: ignoreversion recursesubdirs createallsubdirs

; ── Assets ──
Source: "..\..\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs

; ── Pre-built Python runtime (from build.bat) ──
; Exclude __pycache__ and .pyc — Python regenerates bytecode at runtime.
; This avoids "file corrupted" errors during install and reduces installer size.
Source: ".\python_dist\*"; DestDir: "{app}\python"; Excludes: "__pycache__,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs

; ── Pre-built venv (edition-specific: venv_lite or venv_full) ──
Source: ".\{#VenvSrc}\*"; DestDir: "{app}\venv"; Excludes: "__pycache__,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs

; ── Bundled speech models (optional — downloaded on first run if not bundled) ──
#ifexist ".\models_prebuilt\vosk-model-small-en-us-0.15\README"
Source: ".\models_prebuilt\vosk-model-small-en-us-0.15\*"; DestDir: "{app}\models\vosk-model-small-en-us-0.15"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif
#if EDITION == "Full"
  #ifexist ".\models_prebuilt\faster-whisper-large-v3-turbo\model.bin"
Source: ".\models_prebuilt\faster-whisper-large-v3-turbo\*"; DestDir: "{app}\models\faster-whisper-large-v3-turbo"; Flags: ignoreversion recursesubdirs createallsubdirs
  #endif
#endif
; ── Silero language detection (bundled for both editions — 16 MB, MIT licensed) ──
#ifexist ".\models_prebuilt\silero-lang-detect\lang_classifier_95.onnx"
Source: ".\models_prebuilt\silero-lang-detect\*"; DestDir: "{app}\models\silero-lang-detect"; Flags: ignoreversion
#endif

[Dirs]
Name: "{app}\uploads"; Permissions: users-modify
Name: "{app}\models"; Permissions: users-modify
Name: "{app}\models\translate"; Permissions: users-modify
Name: "{app}\models\tuned"; Permissions: users-modify

[Icons]
Name: "{group}\{#MyAppShortName}"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\linguataxi.ico"; Comment: "Launch LinguaTaxi"
Name: "{group}\Uninstall {#MyAppShortName}"; Filename: "{uninstallexe}"; IconFilename: "{app}\assets\linguataxi.ico"
Name: "{autodesktop}\{#MyAppShortName}"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\linguataxi.ico"; Tasks: desktopicon

[Run]
; Optional: check for updated speech models (unchecked by default — bundled models work out of the box)
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\download_models.py"""; WorkingDir: "{app}"; Tasks: updatemodels; StatusMsg: "{cm:CheckingModels}"; Flags: runhidden
#if EDITION == "Full"
; Download language-tuned models (each runs only if its task is selected)
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\tuned_models.py"" --download ES --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: tuned\es; StatusMsg: "{cm:DownloadingTunedEs}"; Flags: runhidden
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\tuned_models.py"" --download FR --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: tuned\fr; StatusMsg: "{cm:DownloadingTunedFr}"; Flags: runhidden
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\tuned_models.py"" --download DE --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: tuned\de; StatusMsg: "{cm:DownloadingTunedDe}"; Flags: runhidden
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\tuned_models.py"" --download AR --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: tuned\ar; StatusMsg: "{cm:DownloadingTunedAr}"; Flags: runhidden
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\tuned_models.py"" --download JA --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: tuned\ja; StatusMsg: "{cm:DownloadingTunedJa}"; Flags: runhidden
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\tuned_models.py"" --download ZH --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: tuned\zh; StatusMsg: "{cm:DownloadingTunedZh}"; Flags: runhidden
; Offline translation models
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\offline_translate.py"" --download-opus ES --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: offline\opus_es; StatusMsg: "{cm:DownloadingOpusEs}"; Flags: runhidden
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\offline_translate.py"" --download-opus FR --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: offline\opus_fr; StatusMsg: "{cm:DownloadingOpusFr}"; Flags: runhidden
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\offline_translate.py"" --download-opus DE --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: offline\opus_de; StatusMsg: "{cm:DownloadingOpusDe}"; Flags: runhidden
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\offline_translate.py"" --download-opus IT --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: offline\opus_it; StatusMsg: "{cm:DownloadingOpusIt}"; Flags: runhidden
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\offline_translate.py"" --download-opus RU --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: offline\opus_ru; StatusMsg: "{cm:DownloadingOpusRu}"; Flags: runhidden
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\offline_translate.py"" --download-m2m --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: offline\m2m100; StatusMsg: "{cm:DownloadingM2m}"; Flags: runhidden
#endif
; Vosk CPU language models (available in both editions)
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\download_models.py"" --vosk-lang de --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: vosk_lang\de; StatusMsg: "{cm:DownloadingVoskDe}"; Flags: runhidden
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\download_models.py"" --vosk-lang fr --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: vosk_lang\fr; StatusMsg: "{cm:DownloadingVoskFr}"; Flags: runhidden
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\download_models.py"" --vosk-lang es --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: vosk_lang\es; StatusMsg: "{cm:DownloadingVoskEs}"; Flags: runhidden
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\download_models.py"" --vosk-lang ru --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: vosk_lang\ru; StatusMsg: "{cm:DownloadingVoskRu}"; Flags: runhidden
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\download_models.py"" --vosk-lang ar --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: vosk_lang\ar; StatusMsg: "{cm:DownloadingVoskAr}"; Flags: runhidden
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\download_models.py"" --vosk-lang ja --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: vosk_lang\ja; StatusMsg: "{cm:DownloadingVoskJa}"; Flags: runhidden
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\download_models.py"" --vosk-lang zh --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: vosk_lang\zh; StatusMsg: "{cm:DownloadingVoskZh}"; Flags: runhidden
; Launch after install
Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; Description: "Launch {#MyAppShortName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; M55: Use plain /IM filter — WINDOWTITLE filter does not match pythonw.exe windows.
; Note: This may terminate other pythonw.exe processes running on this machine.
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM pythonw.exe"; Flags: runhidden skipifdoesntexist; RunOnceId: "KillLinguaTaxi"

[Code]
// ── Fix venv paths after file copy ──
// The venv was built on the build machine with different paths.
// We rewrite pyvenv.cfg to point to the installed Python location.
// This takes milliseconds and is invisible to the user.

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  PrevVersion: String;
begin
  // Check if upgrading from a previous version
  if RegQueryStringValue(HKLM,
       'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1',
       'DisplayVersion', PrevVersion) then
  begin
    if PrevVersion <> '{#MyAppVersion}' then
      Result := 'Upgrading LinguaTaxi from v' + PrevVersion + ' to v{#MyAppVersion}.' + NewLine + NewLine +
                'Your models, transcripts, and settings will be preserved.' + NewLine +
                'Only program files will be updated.' + NewLine + NewLine;
  end;
  // Append standard memo content
  if MemoDirInfo <> '' then
    Result := Result + MemoDirInfo + NewLine + NewLine;
  if MemoGroupInfo <> '' then
    Result := Result + MemoGroupInfo + NewLine + NewLine;
  if MemoTasksInfo <> '' then
    Result := Result + MemoTasksInfo + NewLine;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  CfgPath: String;
  PythonHome: String;
  EditionPath: String;
  PipPath: String;
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    // Fix venv paths (must happen first — pip needs working venv)
    CfgPath := ExpandConstant('{app}\venv\pyvenv.cfg');
    PythonHome := ExpandConstant('{app}\python');
    // M56 IMPORTANT: Update version below when changing Python version in build.bat
    SaveStringToFile(CfgPath,
      'home = ' + PythonHome + #13#10 +
      'include-system-site-packages = false' + #13#10 +
      'version = 3.11.9' + #13#10,
      False);

    // Write edition.txt
    EditionPath := ExpandConstant('{app}\edition.txt');
  #if EDITION == "Full"
    SaveStringToFile(EditionPath, 'GPU', False);
  #else
    SaveStringToFile(EditionPath, 'CPU', False);
  #endif

  #if EDITION == "Full"
    // H22: Install CUDA packages sequentially (ewWaitUntilTerminated) so each
    // completes before the next starts, avoiding pip lock conflicts.
    PipPath := ExpandConstant('{app}\venv\Scripts\pip.exe');
    Exec(PipPath, 'install --no-deps "https://github.com/TheColliny/LinguaTaxi-CUDA/releases/download/v12.9/nvidia_cuda_runtime_cu12-12.9.79-py3-none-win_amd64.whl"',
         ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(PipPath, 'install --no-deps "https://github.com/TheColliny/LinguaTaxi-CUDA/releases/download/v12.9/nvidia_cublas_cu12-12.9.1.4-py3-none-win_amd64.whl"',
         ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(PipPath, 'install --no-deps "https://github.com/TheColliny/LinguaTaxi-CUDA/releases/download/v12.9/nvidia_cudnn_cu12-9.19.0.56-py3-none-win_amd64.whl"',
         ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
  #endif
  end;
end;

// ── Uninstall: per-model deletion choices ──
// Detects installed models and asks user about each category.
// Users can choose to keep or delete each model group individually.

const
  MAX_MODELS = 20;

var
  ModelPaths: array[0..MAX_MODELS-1] of String;
  ModelKeep:  array[0..MAX_MODELS-1] of Boolean;
  ModelCount: Integer;
  KeepTranscripts: Boolean;

function FriendlyName(Path: String): String;
var
  DirName: String;
begin
  DirName := ExtractFileName(Path);
  if DirName = 'faster-whisper-large-v3-turbo' then
    Result := 'Whisper large-v3-turbo (GPU)'
  else if Pos('vosk-model', DirName) = 1 then
    Result := DirName + ' (CPU)'
  else if DirName = '_hf_cache' then
    Result := 'Download cache'
  else if DirName = 'm2m100-1.2b' then
    Result := 'M2M-100 Multilingual translation'
  else if Pos('opus-mt-', DirName) = 1 then
    Result := 'OPUS-MT ' + Copy(DirName, 9, Length(DirName))
  else
    Result := 'Tuned: ' + UpperCase(DirName);
end;

procedure DetectModels;
var
  ModelsDir, TunedDir, TransDir: String;
  FindRec: TFindRec;
begin
  ModelCount := 0;
  ModelsDir := ExpandConstant('{app}\models');
  TunedDir := ModelsDir + '\tuned';
  TransDir := ModelsDir + '\translate';

  // Speech: Whisper
  if FileExists(ModelsDir + '\faster-whisper-large-v3-turbo\model.bin') then
  begin
    ModelPaths[ModelCount] := ModelsDir + '\faster-whisper-large-v3-turbo';
    ModelKeep[ModelCount] := True;
    ModelCount := ModelCount + 1;
  end;

  // Speech: Vosk models
  if FindFirst(ModelsDir + '\vosk-model-*', FindRec) then
  begin
    try
      repeat
        if FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then
          if ModelCount < MAX_MODELS then
          begin
            ModelPaths[ModelCount] := ModelsDir + '\' + FindRec.Name;
            ModelKeep[ModelCount] := True;
            ModelCount := ModelCount + 1;
          end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;

  // Tuned models
  if DirExists(TunedDir) and FindFirst(TunedDir + '\*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0) and
           (FindRec.Name <> '.') and (FindRec.Name <> '..') and
           FileExists(TunedDir + '\' + FindRec.Name + '\model.bin') then
          if ModelCount < MAX_MODELS then
          begin
            ModelPaths[ModelCount] := TunedDir + '\' + FindRec.Name;
            ModelKeep[ModelCount] := True;
            ModelCount := ModelCount + 1;
          end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;

  // Translation: OPUS-MT
  if DirExists(TransDir) and FindFirst(TransDir + '\opus-mt-*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0) and
           FileExists(TransDir + '\' + FindRec.Name + '\model.bin') then
          if ModelCount < MAX_MODELS then
          begin
            ModelPaths[ModelCount] := TransDir + '\' + FindRec.Name;
            ModelKeep[ModelCount] := True;
            ModelCount := ModelCount + 1;
          end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;

  // Translation: M2M-100
  if FileExists(TransDir + '\m2m100-1.2b\model.bin') then
    if ModelCount < MAX_MODELS then
    begin
      ModelPaths[ModelCount] := TransDir + '\m2m100-1.2b';
      ModelKeep[ModelCount] := True;
      ModelCount := ModelCount + 1;
    end;

  // Leftover HF cache
  if DirExists(TransDir + '\_hf_cache') then
    if ModelCount < MAX_MODELS then
    begin
      ModelPaths[ModelCount] := TransDir + '\_hf_cache';
      ModelKeep[ModelCount] := False;  // Default: delete cache
      ModelCount := ModelCount + 1;
    end;
end;

function InitializeUninstall(): Boolean;
var
  Msg, ModelList, Summary: String;
  Res, I: Integer;
begin
  Result := False;
  KeepTranscripts := True;
  DetectModels;

  // Transcripts
  Msg := 'Do you want to keep your transcript files?' + #13#10 +
         '(in Documents\LinguaTaxi Transcripts)' + #13#10 + #13#10 +
         'Click Yes to keep, No to delete.';
  Res := MsgBox(Msg, mbConfirmation, MB_YESNOCANCEL);
  if Res = IDCANCEL then Exit;
  KeepTranscripts := (Res = IDYES);

  // Models — ask per model if any exist
  if ModelCount > 0 then
  begin
    // Build the list of models
    ModelList := '';
    for I := 0 to ModelCount - 1 do
      ModelList := ModelList + '  ' + IntToStr(I + 1) + '. ' +
                   FriendlyName(ModelPaths[I]) + #13#10;

    Msg := 'The following ' + IntToStr(ModelCount) +
           ' model(s) are installed:' + #13#10 + #13#10 +
           ModelList + #13#10 +
           'Do you want to KEEP ALL models?' + #13#10 +
           '(saves re-downloading on reinstall)' + #13#10 + #13#10 +
           'Yes = keep all, No = choose individually, Cancel = abort';
    Res := MsgBox(Msg, mbConfirmation, MB_YESNOCANCEL);
    if Res = IDCANCEL then Exit;

    if Res = IDNO then
    begin
      // Ask about each model individually
      for I := 0 to ModelCount - 1 do
      begin
        Msg := 'Keep "' + FriendlyName(ModelPaths[I]) + '"?' + #13#10 + #13#10 +
               'Yes = keep, No = delete';
        Res := MsgBox(Msg, mbConfirmation, MB_YESNOCANCEL);
        if Res = IDCANCEL then Exit;
        ModelKeep[I] := (Res = IDYES);
      end;
    end;
    // else Res = IDYES: all ModelKeep already True
  end;

  // Final summary
  Summary := 'Ready to uninstall LinguaTaxi.' + #13#10 + #13#10;
  if KeepTranscripts then
    Summary := Summary + '  Transcripts: KEEP' + #13#10
  else
    Summary := Summary + '  Transcripts: DELETE' + #13#10;

  for I := 0 to ModelCount - 1 do
  begin
    if ModelKeep[I] then
      Summary := Summary + '  ' + FriendlyName(ModelPaths[I]) + ': KEEP' + #13#10
    else
      Summary := Summary + '  ' + FriendlyName(ModelPaths[I]) + ': DELETE' + #13#10;
  end;

  Summary := Summary + #13#10 + 'Proceed with uninstall?';
  Result := (MsgBox(Summary, mbConfirmation, MB_YESNO) = IDYES);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  TranscriptsDir, AppDataDir: String;
  I: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // Delete unchecked models
    for I := 0 to ModelCount - 1 do
      if not ModelKeep[I] then
        if DirExists(ModelPaths[I]) then
          DelTree(ModelPaths[I], True, True, True);

    // Clean up empty parent directories
    RemoveDir(ExpandConstant('{app}\models\tuned'));
    RemoveDir(ExpandConstant('{app}\models\translate'));
    RemoveDir(ExpandConstant('{app}\models'));

    if not KeepTranscripts then
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
