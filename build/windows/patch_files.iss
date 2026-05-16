; Auto-generated — Patch 1 for v1.0.3
; Fix websocket thread-safety crash in dictation, tray quit hang, clipboard text injection
Source: "..\..\server.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\tray_dictation.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\launcher.pyw"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\version.json"; DestDir: "{app}"; Flags: ignoreversion
