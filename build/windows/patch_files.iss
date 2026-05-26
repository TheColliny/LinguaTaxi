; Auto-generated — Patch 1 for v1.0.3b
; Plugin loader: always load panel assets, grid-editor resize fix, page detection fix, uninstaller model preservation
Source: "..\..\linguataxi\plugins\loader.py"; DestDir: "{app}\linguataxi\plugins"; Flags: ignoreversion
Source: "..\..\static\js\grid-editor.js"; DestDir: "{app}\static\js"; Flags: ignoreversion
Source: "..\..\static\plugin_grid.js"; DestDir: "{app}\static"; Flags: ignoreversion
Source: "..\..\plugins\window_capture\panel.js"; DestDir: "{app}\plugins\window_capture"; Flags: ignoreversion
Source: "..\..\version.json"; DestDir: "{app}"; Flags: ignoreversion
