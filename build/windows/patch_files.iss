; Auto-generated — Patch 4 for v1.0.3b
; Enable WASAPI auto_convert so 16kHz streams work when system mixer is at 48kHz
Source: "..\..\linguataxi\server\audio.py"; DestDir: "{app}\linguataxi\server"; Flags: ignoreversion
Source: "..\..\version.json"; DestDir: "{app}"; Flags: ignoreversion
