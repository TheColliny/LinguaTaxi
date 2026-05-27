; ══════════════════════════════════════════════════════
; LinguaTaxi Patch Installer — Inno Setup Script
;
; Copies only changed app files into an existing install.
; Built by build_patch.bat — do NOT compile manually.
;
; Required defines (passed by build_patch.bat):
;   /DEDITION=Full|Lite
;   /DPATCH_VER=1.0.0
;   /DPATCH_NUM=1
; ══════════════════════════════════════════════════════

#ifndef EDITION
  #define EDITION "Full"
#endif
#ifndef PATCH_VER
  #define PATCH_VER "0.0.0"
#endif
#ifndef PATCH_NUM
  #define PATCH_NUM "0"
#endif

#define MyAppName "LinguaTaxi - Live Caption and Translation"
#define MyAppShortName "LinguaTaxi"

#if EDITION == "Lite"
  #define OutputName "LinguaTaxi-CPU-Patch-" + PATCH_VER + "-p" + PATCH_NUM
  #define EditionLabel "CPU Only"
  #define ExpectedEdition "Lite"
#else
  #define OutputName "LinguaTaxi-GPU-Patch-" + PATCH_VER + "-p" + PATCH_NUM
  #define EditionLabel "CPU+GPU"
  #define ExpectedEdition "Full"
#endif

[Setup]
; Same AppId as the full installer so Windows recognizes it
AppId={{B8A5C2E1-4F3D-4A7B-9E2C-1D3F5A6B7C8D}
AppName={#MyAppName} Patch
AppVersion={#PATCH_VER}.{#PATCH_NUM}
DefaultDirName={autopf}\{#MyAppName}
OutputDir=..\..\dist
OutputBaseFilename={#OutputName}
#ifexist "..\..\assets\linguataxi.ico"
SetupIconFile=..\..\assets\linguataxi.ico
#endif
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Patch-specific: install to existing dir, no uninstall entry
UsePreviousAppDir=yes
CreateUninstallRegKey=no
UpdateUninstallLogAppName=no
; Close running LinguaTaxi before patching
CloseApplications=yes
RestartApplications=yes
; Disable pages that don't apply to patches
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Auto-generated file list from build_patch.bat
#include "patch_files.iss"

[Code]

// ── JSON helpers (same as full installer) ──

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

// ── Version check ──

function InitializeSetup(): Boolean;
var
  VersionPath, Content: String;
  ContentA: AnsiString;
  InstalledVer, InstalledEdition: String;
  InstalledPatch, RequiredPatch: Integer;
begin
  Result := False;

  // Find the existing install directory
  VersionPath := ExpandConstant('{autopf}\{#MyAppName}\version.json');
  if not FileExists(VersionPath) then begin
    MsgBox('LinguaTaxi is not installed, or version.json is missing.' + #13#10 +
           'Please install the full version first.' + #13#10 + #13#10 +
           'This patch requires: v{#PATCH_VER}',
           mbError, MB_OK);
    Exit;
  end;

  // Read and parse version.json
  if not LoadStringFromFile(VersionPath, ContentA) then begin
    MsgBox('Could not read version.json.', mbError, MB_OK);
    Exit;
  end;
  Content := String(ContentA);

  InstalledVer := JsonStr(Content, 'version');
  InstalledEdition := JsonStr(Content, 'edition');
  InstalledPatch := JsonInt(Content, 'patch');
  RequiredPatch := {#PATCH_NUM} - 1;

  // Check version
  if InstalledVer <> '{#PATCH_VER}' then begin
    MsgBox('Version mismatch!' + #13#10 + #13#10 +
           'Installed: v' + InstalledVer + #13#10 +
           'This patch requires: v{#PATCH_VER}' + #13#10 + #13#10 +
           'Please install the matching full version first.',
           mbError, MB_OK);
    Exit;
  end;

  // Check edition
  if InstalledEdition <> '{#ExpectedEdition}' then begin
    MsgBox('Edition mismatch!' + #13#10 + #13#10 +
           'Installed edition: ' + InstalledEdition + #13#10 +
           'This patch is for: {#ExpectedEdition} ({#EditionLabel})' + #13#10 + #13#10 +
           'Please use the correct patch for your edition.',
           mbError, MB_OK);
    Exit;
  end;

  // Check patch level (must be exactly N-1)
  if InstalledPatch <> RequiredPatch then begin
    if InstalledPatch < RequiredPatch then
      MsgBox('Patch level too low!' + #13#10 + #13#10 +
             'Installed: v' + InstalledVer + ' patch ' + IntToStr(InstalledPatch) + #13#10 +
             'This patch requires: patch ' + IntToStr(RequiredPatch) + ' applied first.' + #13#10 + #13#10 +
             'Please install the missing patches in order.',
             mbError, MB_OK)
    else
      MsgBox('Patch already applied!' + #13#10 + #13#10 +
             'Installed: v' + InstalledVer + ' patch ' + IntToStr(InstalledPatch) + #13#10 +
             'This patch: p{#PATCH_NUM}' + #13#10 + #13#10 +
             'Your installation is already at or beyond this patch level.',
             mbInformation, MB_OK);
    Exit;
  end;

  Result := True;
end;

// ── Post-install: bump patch number in version.json ──

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    SaveStringToFile(
      ExpandConstant('{app}\version.json'),
      '{"version": "{#PATCH_VER}", "patch": {#PATCH_NUM}, "edition": "{#ExpectedEdition}"}',
      False);
  end;
end;

// ── Ready memo ──

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := 'APPLYING PATCH {#PATCH_NUM} to LinguaTaxi v{#PATCH_VER} ({#EditionLabel}).' + NewLine + NewLine +
            'Only changed program files will be updated.' + NewLine +
            'Models, transcripts, and settings are not affected.' + NewLine + NewLine;
  if MemoDirInfo <> '' then
    Result := Result + MemoDirInfo + NewLine;
end;
