; Auto-generated — Patch 1 for v1.0.3b
; Fix server proxy for __main__, filter audio devices to active-only, add refresh button
Source: "..\..\server.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\linguataxi\launcher\settings_panel.py"; DestDir: "{app}\linguataxi\launcher"; Flags: ignoreversion
Source: "..\..\linguataxi\launcher\app.py"; DestDir: "{app}\linguataxi\launcher"; Flags: ignoreversion
Source: "..\..\linguataxi\launcher\model_download.py"; DestDir: "{app}\linguataxi\launcher"; Flags: ignoreversion
Source: "..\..\locales\en.json"; DestDir: "{app}\locales"; Flags: ignoreversion
Source: "..\..\version.json"; DestDir: "{app}"; Flags: ignoreversion
