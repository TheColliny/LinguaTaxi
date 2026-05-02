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
#define MyAppVersion "1.0.2"
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
; ── Reinstall / repair behaviour ──
; Auto-close any running LinguaTaxi launcher before file copy so we don't hit
; "file in use" errors. Restart it after install completes.
CloseApplications=yes
RestartApplications=yes
; Use the previous install location automatically (no DirPage on reinstall)
UsePreviousAppDir=yes
UsePreviousTasks=yes
; Don't bail out if the same version is already installed — the InitializeSetup
; hook below shows a friendly "Reinstall?" prompt and proceeds.

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
VoskDe=German — Vosk (~45 MB download, ~77 MB on disk)
VoskFr=French — Vosk (~41 MB download, ~70 MB on disk)
VoskEs=Spanish — Vosk (~39 MB download, ~67 MB on disk)
VoskRu=Russian — Vosk (~45 MB download, ~77 MB on disk)
VoskAr=Arabic — Vosk (~318 MB download, ~543 MB on disk)
VoskJa=Japanese — Vosk (~48 MB download, ~82 MB on disk)
VoskZh=Chinese — Vosk (~42 MB download, ~72 MB on disk)
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
Source: "..\..\tray_dictation.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\display.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\operator.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dictation.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\bidirectional.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\lang_detect.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\voice_id.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\model_manager.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\models-manifest.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\version.json"; DestDir: "{app}"; Flags: ignoreversion

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

; ── Bundled speech models ──
; Use `onlyifdoesntexist` so reinstall over the same version skips the slow
; re-extraction (the bundled models for the same build are byte-identical).
; To force a re-extract, the operator can delete the model directory first.
; Note: `onlyifdoesntexist` overrides `ignoreversion` — drop the latter as it's a no-op here.
#ifexist ".\models_prebuilt\vosk-model-small-en-us-0.15\README"
Source: ".\models_prebuilt\vosk-model-small-en-us-0.15\*"; DestDir: "{app}\models\vosk-model-small-en-us-0.15"; Flags: onlyifdoesntexist recursesubdirs createallsubdirs
#endif
#if EDITION == "Full"
  #ifexist ".\models_prebuilt\faster-whisper-large-v3-turbo\model.bin"
Source: ".\models_prebuilt\faster-whisper-large-v3-turbo\*"; DestDir: "{app}\models\faster-whisper-large-v3-turbo"; Flags: onlyifdoesntexist recursesubdirs createallsubdirs
  #endif
#endif
; ── Silero language detection (bundled for both editions — 16 MB, MIT licensed) ──
#ifexist ".\models_prebuilt\silero-lang-detect\lang_classifier_95.onnx"
Source: ".\models_prebuilt\silero-lang-detect\*"; DestDir: "{app}\models\silero-lang-detect"; Flags: onlyifdoesntexist
#endif

[Dirs]
Name: "{app}\uploads"; Permissions: users-modify
Name: "{app}\models"; Permissions: users-modify
Name: "{app}\models\translate"; Permissions: users-modify
Name: "{app}\models\tuned"; Permissions: users-modify

[Icons]
Name: "{group}\{#MyAppShortName}"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\linguataxi.ico"; Comment: "Launch LinguaTaxi"; AppUserModelID: "LinguaTaxi.Launcher"
Name: "{group}\{#MyAppShortName} Dictation"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\tray_dictation.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\linguataxi.ico"; Comment: "LinguaTaxi Global Dictation (tray)"; AppUserModelID: "LinguaTaxi.Dictation"
Name: "{group}\Uninstall {#MyAppShortName}"; Filename: "{uninstallexe}"; IconFilename: "{app}\assets\linguataxi.ico"
Name: "{autodesktop}\{#MyAppShortName}"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\linguataxi.ico"; Tasks: desktopicon; AppUserModelID: "LinguaTaxi.Launcher"

[Run]
; Model downloads are handled by model_manager.py with a progress UI (see [Code] section).
; Only the post-install launch entry remains here.
Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; Description: "Launch {#MyAppShortName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; M55: Use plain /IM filter — WINDOWTITLE filter does not match pythonw.exe windows.
; Note: This may terminate other pythonw.exe processes running on this machine.
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM pythonw.exe"; Flags: runhidden skipifdoesntexist; RunOnceId: "KillLinguaTaxi"

[Code]
// ═══════════════════════════════════════════════════════════════════════════
// LinguaTaxi Installer — Pascal Script
//
// 1. Fix venv paths after file copy
// 2. Install CUDA wheels (GPU edition)
// 3. Run model downloads with a progress UI via model_manager.py
// 4. Stamp installed model versions for future update checks
// ═══════════════════════════════════════════════════════════════════════════

var
  DownloadPage: TOutputProgressWizardPage;

// ── Helpers ──

function GetInstalledVersion(): String;
begin
  if not RegQueryStringValue(HKLM,
       'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1',
       'DisplayVersion', Result) then
    Result := '';
end;

procedure StopRunningLinguaTaxi();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM pythonw.exe',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM python.exe',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  PrevVersion: String;
begin
  Result := '';
  PrevVersion := GetInstalledVersion();
  if PrevVersion <> '' then
    StopRunningLinguaTaxi();
end;

function InitializeSetup(): Boolean;
var
  PrevVersion: String;
  Res: Integer;
begin
  Result := True;
  PrevVersion := GetInstalledVersion();
  if (PrevVersion <> '') and (PrevVersion = '{#MyAppVersion}') then
  begin
    Res := MsgBox(
      'LinguaTaxi v' + PrevVersion + ' is already installed.' + #13#10 + #13#10 +
      'Reinstall and replace all program files?' + #13#10 + #13#10 +
      'PRESERVED:' + #13#10 +
      '  - Your transcripts (in Documents\LinguaTaxi Transcripts)' + #13#10 +
      '  - Downloaded models (tuned, OPUS-MT, M2M-100, voice ID)' + #13#10 +
      '  - Bundled models will not be re-extracted (saves time)' + #13#10 +
      '  - Voice ID enrollments and dossiers' + #13#10 +
      '  - All settings, profiles, and saved layouts' + #13#10 + #13#10 +
      'REPLACED:' + #13#10 +
      '  - All program files (server.py, plugins, HTML, JS, CSS)' + #13#10 +
      '  - The Python runtime and venv' + #13#10 + #13#10 +
      'Click Yes to reinstall, No to cancel.',
      mbConfirmation, MB_YESNO);
    Result := (Res = IDYES);
  end;
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateOutputProgressPage(
    'Downloading Models',
    'Downloading optional models. This may take several minutes depending on your internet speed.');
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  PrevVersion: String;
begin
  PrevVersion := GetInstalledVersion();
  if PrevVersion <> '' then
  begin
    if PrevVersion = '{#MyAppVersion}' then
      Result := 'REINSTALLING LinguaTaxi v{#MyAppVersion}.' + NewLine + NewLine +
                'Program files will be replaced. Models, transcripts, dossiers,' + NewLine +
                'and saved layouts will be preserved.' + NewLine + NewLine
    else
      Result := 'UPGRADING LinguaTaxi from v' + PrevVersion + ' to v{#MyAppVersion}.' + NewLine + NewLine +
                'Your models, transcripts, and settings will be preserved.' + NewLine +
                'Only program files will be updated.' + NewLine + NewLine;
  end;
  if MemoDirInfo <> '' then
    Result := Result + MemoDirInfo + NewLine + NewLine;
  if MemoGroupInfo <> '' then
    Result := Result + MemoGroupInfo + NewLine + NewLine;
  if MemoTasksInfo <> '' then
    Result := Result + MemoTasksInfo + NewLine;
end;

// ── Download plan builder ──
// Builds a JSON download plan based on selected tasks.

function HasModelDownloads(): Boolean;
begin
  Result := IsTaskSelected('updatemodels')
         or IsTaskSelected('vosk_lang\de') or IsTaskSelected('vosk_lang\fr')
         or IsTaskSelected('vosk_lang\es') or IsTaskSelected('vosk_lang\ru')
         or IsTaskSelected('vosk_lang\ar') or IsTaskSelected('vosk_lang\ja')
         or IsTaskSelected('vosk_lang\zh');
#if EDITION == "Full"
  Result := Result
         or IsTaskSelected('offline\opus_es') or IsTaskSelected('offline\opus_fr')
         or IsTaskSelected('offline\opus_de') or IsTaskSelected('offline\opus_it')
         or IsTaskSelected('offline\opus_ru') or IsTaskSelected('offline\m2m100')
         or IsTaskSelected('tuned\es') or IsTaskSelected('tuned\fr')
         or IsTaskSelected('tuned\de') or IsTaskSelected('tuned\ar')
         or IsTaskSelected('tuned\ja') or IsTaskSelected('tuned\zh');
#endif
end;

function EscapeJsonPath(const S: String): String;
var
  I: Integer;
begin
  Result := '';
  for I := 1 to Length(S) do begin
    if S[I] = '\' then
      Result := Result + '\\'
    else
      Result := Result + S[I];
  end;
end;

function BuildDownloadPlan(): String;
var
  AppDir, AppJ: String;
  Plan, DL: String;
  First: Boolean;
begin
  AppDir := ExpandConstant('{app}');
  AppJ := EscapeJsonPath(AppDir);
  First := True;

  DL := '';

  // Helper macro — appends a download entry
  // (Inno Pascal doesn't have macros so we repeat the pattern)

  if IsTaskSelected('updatemodels') then begin
    if not First then DL := DL + ',';
    DL := DL + '{"type":"update_models"}';
    First := False;
  end;

  // Vosk language models
  if IsTaskSelected('vosk_lang\de') then begin if not First then DL := DL + ','; DL := DL + '{"type":"vosk_lang","lang":"de"}'; First := False; end;
  if IsTaskSelected('vosk_lang\fr') then begin if not First then DL := DL + ','; DL := DL + '{"type":"vosk_lang","lang":"fr"}'; First := False; end;
  if IsTaskSelected('vosk_lang\es') then begin if not First then DL := DL + ','; DL := DL + '{"type":"vosk_lang","lang":"es"}'; First := False; end;
  if IsTaskSelected('vosk_lang\ru') then begin if not First then DL := DL + ','; DL := DL + '{"type":"vosk_lang","lang":"ru"}'; First := False; end;
  if IsTaskSelected('vosk_lang\ar') then begin if not First then DL := DL + ','; DL := DL + '{"type":"vosk_lang","lang":"ar"}'; First := False; end;
  if IsTaskSelected('vosk_lang\ja') then begin if not First then DL := DL + ','; DL := DL + '{"type":"vosk_lang","lang":"ja"}'; First := False; end;
  if IsTaskSelected('vosk_lang\zh') then begin if not First then DL := DL + ','; DL := DL + '{"type":"vosk_lang","lang":"zh"}'; First := False; end;

#if EDITION == "Full"
  // OPUS-MT models
  if IsTaskSelected('offline\opus_es') then begin if not First then DL := DL + ','; DL := DL + '{"type":"opus","lang":"ES"}'; First := False; end;
  if IsTaskSelected('offline\opus_fr') then begin if not First then DL := DL + ','; DL := DL + '{"type":"opus","lang":"FR"}'; First := False; end;
  if IsTaskSelected('offline\opus_de') then begin if not First then DL := DL + ','; DL := DL + '{"type":"opus","lang":"DE"}'; First := False; end;
  if IsTaskSelected('offline\opus_it') then begin if not First then DL := DL + ','; DL := DL + '{"type":"opus","lang":"IT"}'; First := False; end;
  if IsTaskSelected('offline\opus_ru') then begin if not First then DL := DL + ','; DL := DL + '{"type":"opus","lang":"RU"}'; First := False; end;

  // M2M-100
  if IsTaskSelected('offline\m2m100') then begin if not First then DL := DL + ','; DL := DL + '{"type":"m2m100"}'; First := False; end;

  // Tuned models
  if IsTaskSelected('tuned\es') then begin if not First then DL := DL + ','; DL := DL + '{"type":"tuned","lang":"ES"}'; First := False; end;
  if IsTaskSelected('tuned\fr') then begin if not First then DL := DL + ','; DL := DL + '{"type":"tuned","lang":"FR"}'; First := False; end;
  if IsTaskSelected('tuned\de') then begin if not First then DL := DL + ','; DL := DL + '{"type":"tuned","lang":"DE"}'; First := False; end;
  if IsTaskSelected('tuned\ar') then begin if not First then DL := DL + ','; DL := DL + '{"type":"tuned","lang":"AR"}'; First := False; end;
  if IsTaskSelected('tuned\ja') then begin if not First then DL := DL + ','; DL := DL + '{"type":"tuned","lang":"JA"}'; First := False; end;
  if IsTaskSelected('tuned\zh') then begin if not First then DL := DL + ','; DL := DL + '{"type":"tuned","lang":"ZH"}'; First := False; end;
#endif

  Plan := '{"models_dir":"' + AppJ + '\\models","app_dir":"' + AppJ
        + '","venv_dir":"' + AppJ + '\\venv","downloads":[' + DL + ']}';

  Result := Plan;
end;

// ── Progress file reader ──
// Reads the JSON status file written by model_manager.py and returns
// parsed values via var parameters.

// Extract a JSON string value by key from a flat JSON string.
// Simple parser — works for our single-level status JSON.
function JsonStr(const Json, Key: String): String;
var
  Search: String;
  P, Q: Integer;
  Rest: String;
begin
  Result := '';
  Search := '"' + Key + '":';
  P := Pos(Search, Json);
  if P = 0 then Exit;
  Rest := Copy(Json, P + Length(Search), Length(Json));
  Rest := Trim(Rest);
  if (Length(Rest) > 0) and (Rest[1] = '"') then begin
    Rest := Copy(Rest, 2, Length(Rest));
    Q := Pos('"', Rest);
    if Q > 0 then
      Result := Copy(Rest, 1, Q - 1);
  end;
end;

// Extract a JSON integer value by key.
function JsonInt(const Json, Key: String): Integer;
var
  Search: String;
  P, I: Integer;
  Rest, Num: String;
begin
  Result := 0;
  Search := '"' + Key + '":';
  P := Pos(Search, Json);
  if P = 0 then Exit;
  Rest := Trim(Copy(Json, P + Length(Search), Length(Json)));
  Num := '';
  for I := 1 to Length(Rest) do begin
    if (Rest[I] >= '0') and (Rest[I] <= '9') then
      Num := Num + Rest[I]
    else
      Break;
  end;
  Result := StrToIntDef(Num, 0);
end;

function ReadStatusFile(StatusPath: String;
  var TaskLabel: String; var OverallPct, CurrentPct: Integer;
  var State: String): Boolean;
var
  Content: AnsiString;
  S: String;
begin
  Result := False;
  if not FileExists(StatusPath) then Exit;
  if not LoadStringFromFile(StatusPath, Content) then Exit;
  S := String(Content);

  State := JsonStr(S, 'state');
  TaskLabel := JsonStr(S, 'task_label');
  OverallPct := JsonInt(S, 'overall_pct');
  CurrentPct := JsonInt(S, 'current_pct');
  Result := True;
end;

// ── Run downloads with progress UI ──

procedure RunModelDownloads();
var
  PlanPath, StatusPath, DonePath: String;
  PlanContent: String;
  Python, Params: String;
  ResultCode: Integer;
  TaskLabel, State: String;
  OverallPct, CurrentPct: Integer;
  WaitCount: Integer;
begin
  PlanPath := ExpandConstant('{tmp}\download_plan.json');
  StatusPath := ExpandConstant('{tmp}\download_status.json');
  DonePath := StatusPath + '.done';

  // Write the download plan
  PlanContent := BuildDownloadPlan();
  SaveStringToFile(PlanPath, PlanContent, False);

  // Delete stale status/done files from any previous run
  DeleteFile(StatusPath);
  DeleteFile(DonePath);

  // Launch model_manager.py in background
  Python := ExpandConstant('{app}\venv\Scripts\python.exe');
  Params := '"' + ExpandConstant('{app}\model_manager.py') + '" download'
          + ' --plan "' + PlanPath + '"'
          + ' --progress "' + StatusPath + '"';

  if not Exec(Python, Params, ExpandConstant('{app}'), SW_HIDE, ewNoWait, ResultCode) then
  begin
    MsgBox('Failed to start model download helper. Models can be downloaded later from the app.',
           mbError, MB_OK);
    Exit;
  end;

  // Show progress page and poll the status file
  DownloadPage.Show;
  try
    DownloadPage.SetText('Starting downloads...', '');
    DownloadPage.SetProgress(0, 100);

    WaitCount := 0;
    while not FileExists(DonePath) do
    begin
      Sleep(500);

      TaskLabel := '';
      OverallPct := 0;
      CurrentPct := 0;
      State := '';

      if ReadStatusFile(StatusPath, TaskLabel, OverallPct, CurrentPct, State) then
      begin
        DownloadPage.SetText(TaskLabel, 'Overall progress: ' + IntToStr(OverallPct) + '%');
        DownloadPage.SetProgress(OverallPct, 100);
        WaitCount := 0;
      end else begin
        // Status file not yet written — increment wait counter
        WaitCount := WaitCount + 1;
      end;

      // Safety timeout: if no .done file after 2 hours, bail
      if WaitCount > 14400 then begin
        MsgBox('Model download appears to have stalled. ' +
               'You can download remaining models from within the app.',
               mbError, MB_OK);
        Break;
      end;

      // Keep Inno Setup UI responsive
      WizardForm.Refresh;
    end;

    // Final status read
    if FileExists(StatusPath) then begin
      ReadStatusFile(StatusPath, TaskLabel, OverallPct, CurrentPct, State);
      if (State = 'complete_with_errors') then
        MsgBox('Some model downloads failed. You can retry them from within the app.',
               mbInformation, MB_OK);
    end;

  finally
    DownloadPage.Hide;
  end;
end;

// ── Post-install step ──

procedure CurStepChanged(CurStep: TSetupStep);
var
  CfgPath, PythonHome, EditionPath, PipPath: String;
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    // 1. Fix venv paths
    CfgPath := ExpandConstant('{app}\venv\pyvenv.cfg');
    PythonHome := ExpandConstant('{app}\python');
    SaveStringToFile(CfgPath,
      'home = ' + PythonHome + #13#10 +
      'include-system-site-packages = false' + #13#10 +
      'version = 3.11.9' + #13#10,
      False);

    // 2. Write edition.txt
    EditionPath := ExpandConstant('{app}\edition.txt');
  #if EDITION == "Full"
    SaveStringToFile(EditionPath, 'GPU', False);
  #else
    SaveStringToFile(EditionPath, 'CPU', False);
  #endif

    // 3. Write version.json with edition
    SaveStringToFile(ExpandConstant('{app}\version.json'),
      '{"version": "{#MyAppVersion}", "patch": 0, "edition": "{#EDITION}"}',
      False);

  #if EDITION == "Full"
    // 4. Install CUDA packages (GPU edition only) — with progress UI
    PipPath := ExpandConstant('{app}\venv\Scripts\pip.exe');

    DownloadPage.Show;
    try
      DownloadPage.SetText('Installing NVIDIA CUDA Runtime (1 of 3)...', 'Downloading from GitHub - this may take a few minutes.');
      DownloadPage.SetProgress(5, 100);
      WizardForm.Refresh;

      Exec(PipPath, 'install --no-deps "https://github.com/TheColliny/LinguaTaxi-CUDA/releases/download/v12.9/nvidia_cuda_runtime_cu12-12.9.79-py3-none-win_amd64.whl"',
           ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);

      DownloadPage.SetText('Installing NVIDIA cuBLAS (2 of 3)...', 'Downloading from GitHub - this may take a few minutes.');
      DownloadPage.SetProgress(35, 100);
      WizardForm.Refresh;

      Exec(PipPath, 'install --no-deps "https://github.com/TheColliny/LinguaTaxi-CUDA/releases/download/v12.9/nvidia_cublas_cu12-12.9.1.4-py3-none-win_amd64.whl"',
           ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);

      DownloadPage.SetText('Installing NVIDIA cuDNN (3 of 3)...', 'Downloading from GitHub - this may take a few minutes.');
      DownloadPage.SetProgress(65, 100);
      WizardForm.Refresh;

      Exec(PipPath, 'install --no-deps "https://github.com/TheColliny/LinguaTaxi-CUDA/releases/download/v12.9/nvidia_cudnn_cu12-9.19.0.56-py3-none-win_amd64.whl"',
           ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);

      DownloadPage.SetProgress(100, 100);
    finally
      DownloadPage.Hide;
    end;
  #endif

    // 4. Run model downloads if any tasks were selected
    if HasModelDownloads() then
      RunModelDownloads();

    // 5. Stamp installed model versions for future update checks
    Exec(ExpandConstant('{app}\venv\Scripts\python.exe'),
         '"' + ExpandConstant('{app}\model_manager.py') + '" stamp --models-dir "' +
         ExpandConstant('{app}\models') + '"',
         ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
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
