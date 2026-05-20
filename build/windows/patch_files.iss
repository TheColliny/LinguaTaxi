; Auto-generated — Patch 1 for v1.0.3b
; Fix UnboundLocalError in broadcast_dictation causing HTTP 500 on dictation activation
Source: "..\..\linguataxi\server\websocket.py"; DestDir: "{app}\linguataxi\server"; Flags: ignoreversion
Source: "..\..\version.json"; DestDir: "{app}"; Flags: ignoreversion
