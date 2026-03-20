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

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce
Name: "updatemodels"; Description: "Check for updated voice recognition models (requires internet)"; GroupDescription: "Model updates:"; Flags: unchecked
#if EDITION == "Full"
Name: "offline"; Description: "Download offline translation models (translate without internet)"; GroupDescription: "Offline Translation Models:"; Flags: unchecked
Name: "offline\opus_es"; Description: "Spanish — OPUS-MT (~310 MB download, ~75 MB on disk)"; Flags: unchecked
Name: "offline\opus_fr"; Description: "French — OPUS-MT (~310 MB download, ~75 MB on disk)"; Flags: unchecked
Name: "offline\opus_de"; Description: "German — OPUS-MT (~310 MB download, ~75 MB on disk)"; Flags: unchecked
Name: "offline\opus_it"; Description: "Italian — OPUS-MT (~310 MB download, ~75 MB on disk)"; Flags: unchecked
Name: "offline\opus_ru"; Description: "Russian — OPUS-MT (~310 MB download, ~75 MB on disk)"; Flags: unchecked
Name: "offline\m2m100"; Description: "M2M-100 Multilingual (~4.8 GB download, ~1.2 GB on disk, 100 languages)"; Flags: unchecked
Name: "tuned"; Description: "Download language-tuned voice models (optional)"; GroupDescription: "Language-tuned models (better accuracy for specific languages):"; Flags: unchecked
Name: "tuned\es"; Description: "Spanish tuned model (~1.6 GB download, ~1.6 GB on disk)"; Flags: unchecked
Name: "tuned\fr"; Description: "French tuned model (~3.1 GB download, ~2.9 GB on disk)"; Flags: unchecked
Name: "tuned\de"; Description: "German tuned model (~3.1 GB download, ~2.9 GB on disk)"; Flags: unchecked
Name: "tuned\ar"; Description: "Arabic tuned model (~3.1 GB download, ~2.9 GB on disk)"; Flags: unchecked
Name: "tuned\ja"; Description: "Japanese tuned model (~1.5 GB download, ~1.5 GB on disk)"; Flags: unchecked
Name: "tuned\zh"; Description: "Chinese tuned model (~3.1 GB download, ~2.9 GB on disk)"; Flags: unchecked
#endif

[Files]
; ── Core application ──
Source: "..\..\server.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\launcher.pyw"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\download_models.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\tuned_models.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\offline_translate.py"; DestDir: "{app}"; Flags: ignoreversion
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
#if EDITION == "Full"
; ── Download NVIDIA CUDA libraries from GitHub (~1.2 GB total) ──
Filename: "{app}\venv\Scripts\pip.exe"; Parameters: "install --no-deps ""https://github.com/TheColliny/LinguaTaxi-CUDA/releases/download/v12.9/nvidia_cuda_runtime_cu12-12.9.79-py3-none-win_amd64.whl"""; WorkingDir: "{app}"; StatusMsg: "Downloading NVIDIA CUDA Runtime (3.6 MB)..."; Flags: runhidden
Filename: "{app}\venv\Scripts\pip.exe"; Parameters: "install --no-deps ""https://github.com/TheColliny/LinguaTaxi-CUDA/releases/download/v12.9/nvidia_cublas_cu12-12.9.1.4-py3-none-win_amd64.whl"""; WorkingDir: "{app}"; StatusMsg: "Downloading NVIDIA cuBLAS (553 MB)..."; Flags: runhidden
Filename: "{app}\venv\Scripts\pip.exe"; Parameters: "install --no-deps ""https://github.com/TheColliny/LinguaTaxi-CUDA/releases/download/v12.9/nvidia_cudnn_cu12-9.19.0.56-py3-none-win_amd64.whl"""; WorkingDir: "{app}"; StatusMsg: "Downloading NVIDIA cuDNN (644 MB)..."; Flags: runhidden
#endif
; Optional: check for updated speech models (unchecked by default — bundled models work out of the box)
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\download_models.py"""; WorkingDir: "{app}"; Tasks: updatemodels; StatusMsg: "Checking for updated voice recognition models..."
#if EDITION == "Full"
; Download language-tuned models (each runs only if its task is selected)
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\tuned_models.py"" --download ES --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: tuned\es; StatusMsg: "Downloading & converting Spanish tuned model (~1.6 GB)..."
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\tuned_models.py"" --download FR --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: tuned\fr; StatusMsg: "Downloading & converting French tuned model (~3.1 GB)..."
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\tuned_models.py"" --download DE --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: tuned\de; StatusMsg: "Downloading & converting German tuned model (~3.1 GB)..."
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\tuned_models.py"" --download AR --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: tuned\ar; StatusMsg: "Downloading & converting Arabic tuned model (~3.1 GB)..."
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\tuned_models.py"" --download JA --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: tuned\ja; StatusMsg: "Downloading & converting Japanese tuned model (~1.5 GB)..."
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\tuned_models.py"" --download ZH --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: tuned\zh; StatusMsg: "Downloading & converting Chinese tuned model (~3.1 GB)..."
; Offline translation models
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\offline_translate.py"" --download-opus ES --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: offline\opus_es; StatusMsg: "Downloading Spanish OPUS-MT translation model (~310 MB)..."
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\offline_translate.py"" --download-opus FR --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: offline\opus_fr; StatusMsg: "Downloading French OPUS-MT translation model (~310 MB)..."
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\offline_translate.py"" --download-opus DE --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: offline\opus_de; StatusMsg: "Downloading German OPUS-MT translation model (~310 MB)..."
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\offline_translate.py"" --download-opus IT --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: offline\opus_it; StatusMsg: "Downloading Italian OPUS-MT translation model (~310 MB)..."
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\offline_translate.py"" --download-opus RU --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: offline\opus_ru; StatusMsg: "Downloading Russian OPUS-MT translation model (~310 MB)..."
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\offline_translate.py"" --download-m2m --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: offline\m2m100; StatusMsg: "Downloading M2M-100 multilingual model (~4.8 GB, this may take 30-60 minutes)..."
#endif
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
