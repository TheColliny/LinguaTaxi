# Multi-Language UI & Installer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Localize the entire LinguaTaxi UI and installer into 30 languages using DeepL-generated translations shipped as static JSON files.

**Architecture:** All user-facing strings are extracted into `locales/en.json` with `{variable}` placeholders. A one-time script translates them via DeepL API into 29 other languages. Python uses `_t(key, **kwargs)`, JavaScript uses `t(key, vars)`, Inno Setup uses `{cm:Key}`. Language selection via launcher dropdown (auto-detected from OS on first run) propagates to server and all web clients.

**Tech Stack:** Python 3.11, tkinter, FastAPI, JavaScript (vanilla), Inno Setup, DeepL API

**Spec:** `docs/superpowers/specs/2026-03-21-multi-language-ui-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `locales/en.json` | Create | All ~250 English source strings |
| `locales/languages.json` | Create | Language metadata (native names, flags, RTL) |
| `scripts/generate_translations.py` | Create | DeepL batch translation script |
| `locales/{lang}.json` (x29) | Generate | Translated strings |
| `build/windows/languages/*.isl` | Generate | Inno Setup translation files |
| `launcher.pyw` | Modify | `_t()`, language dropdown, `_refresh_ui()`, OS detection, widget refs |
| `server.py` | Modify | `ui_language` config, locale API endpoints, WebSocket broadcast |
| `operator.html` | Modify | `t()`, `data-i18n`, `applyTranslations()`, RTL |
| `display.html` | Modify | `t()`, `data-i18n`, RTL |
| `dictation.html` | Modify | `t()`, `data-i18n`, RTL |
| `installer.iss` | Modify | Multi-language `[Languages]`, `[CustomMessages]`, `{cm:}` |

---

### Task 1: Create Language Metadata and English Source Strings

**Files:**
- Create: `locales/languages.json`
- Create: `locales/en.json`

- [ ] **Step 1: Create locales directory**

```bash
mkdir -p locales locales/overrides
```

- [ ] **Step 2: Create languages.json**

Create `locales/languages.json` with all 30 supported languages. Each entry has `name` (English), `native` (native script), `flag` (emoji), and `rtl` (boolean). Use the most commonly associated flag for each language.

The file should contain entries for: AR, BG, CS, DA, DE, EL, EN, ES, ET, FI, FR, HU, ID, IT, JA, KO, LT, LV, NB, NL, PL, PT, RO, RU, SK, SL, SV, TR, UK, ZH.

- [ ] **Step 3: Extract all English strings into en.json**

Read every user-facing string from `launcher.pyw`, `operator.html`, `display.html`, `dictation.html`, and `installer.iss`. Create `locales/en.json` with keys organized by component prefix:

- `launcher.*` — all launcher.pyw strings (~80 strings)
- `operator.*` — all operator.html strings (~100 strings)
- `display.*` — display.html strings (~5 strings)
- `dictation.*` — dictation.html strings (~25 strings)
- `installer.*` — installer.iss strings (~35 strings)

Use `{variable}` placeholders for dynamic values. Examples:
```json
{
  "launcher.title": "LinguaTaxi",
  "launcher.subtitle": "Live Caption & Translation",
  "launcher.start_server": "Start Server",
  "launcher.stop": "Stop",
  "launcher.source_n": "Source {num}:",
  "launcher.update_available": "LinguaTaxi v{version} is available!",
  "launcher.you_have_version": "You have v{version}.",
  "operator.go_live": "GO LIVE — Start Captioning",
  "operator.pause": "Pause Captioning",
  "operator.speaker_label": "Speaker {num}",
  "installer.desktop_shortcut": "Create a desktop shortcut",
  "installer.tuned_model_download": "{language} tuned model (~{size} GB download)"
}
```

**IMPORTANT:** This is the most critical task. Every hardcoded user-facing string in the codebase must be captured. Systematically search each file for:
- Python: string arguments to `ttk.Label(text=...)`, `ttk.Button(text=...)`, `messagebox.show*(title, message)`, `dlg.title(...)`, `.configure(text=...)`, `tk.StringVar(value=...)`, `f"..."` strings shown to users
- JavaScript: `.textContent=`, `.innerHTML=` (static text), `alert(...)`, `.placeholder=`, `.title=`, template literals with user text
- Inno Setup: `Description:`, `StatusMsg:`, `GroupDescription:`, string literals in `[Code]` section

- [ ] **Step 4: Commit**

```bash
git add locales/
git commit -m "[feat] create language metadata and extract English source strings"
```

---

### Task 2: Translation Generation Script

**Files:**
- Create: `scripts/generate_translations.py`

- [ ] **Step 1: Create the script**

Create `scripts/generate_translations.py` that:
1. Loads `locales/en.json`
2. For each of the 29 non-English target languages:
   a. Batch-translates all strings via DeepL API (`POST https://api-free.deepl.com/v2/translate`)
   b. Preserves `{variable}` placeholders by using DeepL's `tag_handling=xml` and wrapping placeholders in `<x>` tags before sending, unwrapping after
   c. Flags translations >50% longer than English source (print warning)
   d. Saves to `locales/{lang_lower}.json`
3. Loads `locales/overrides/{lang}.json` if present — overrides take precedence
4. Accepts `--api-key KEY` argument or `DEEPL_AUTH_KEY` env var

Key implementation details:
- DeepL free API endpoint: `https://api-free.deepl.com/v2/translate`
- Send strings in batches of 50 (DeepL limit per request)
- Map language codes: DeepL uses `EN-US`/`EN-GB` for English target, `PT-BR`/`PT-PT` for Portuguese, `ZH-HANS` for Chinese — use appropriate target codes
- For source language, always use `EN`
- Sleep 0.5s between batches to avoid rate limiting

- [ ] **Step 2: Test the script with a small subset**

Run with 2-3 languages first to verify:
```bash
python scripts/generate_translations.py --api-key YOUR_KEY --languages ES,FR,DE
```

Verify `locales/es.json`, `locales/fr.json`, `locales/de.json` are created with correct translations.

- [ ] **Step 3: Generate all 29 languages**

```bash
python scripts/generate_translations.py --api-key YOUR_KEY
```

- [ ] **Step 4: Commit all translation files**

```bash
git add locales/ scripts/
git commit -m "[feat] translation generation script and 29 language translations via DeepL"
```

---

### Task 3: Launcher i18n Infrastructure

**Files:**
- Modify: `launcher.pyw` — add translation loading, `_t()` function, OS language detection

- [ ] **Step 1: Add translation infrastructure at module level**

Near the top of `launcher.pyw`, after the imports and constants, add:

```python
# ── Internationalization ──

_strings = {}
_strings_en = {}

def _load_translations(lang_code):
    """Load translation strings for a language, with English fallback."""
    global _strings, _strings_en
    en_path = APP_DIR / "locales" / "en.json"
    if en_path.exists():
        _strings_en = json.loads(en_path.read_text(encoding="utf-8"))
    lang_path = APP_DIR / "locales" / f"{lang_code.lower()}.json"
    if lang_path.exists():
        _strings = json.loads(lang_path.read_text(encoding="utf-8"))
    else:
        _strings = _strings_en.copy()

def _t(key, **kwargs):
    """Translate a string key with optional variable substitution."""
    text = _strings.get(key) or _strings_en.get(key, key)
    if kwargs:
        for k, v in kwargs.items():
            text = text.replace(f"{{{k}}}", str(v))
    return text

def _detect_os_language():
    """Detect the OS UI language and return a DeepL language code."""
    try:
        if IS_WIN:
            import ctypes
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            # LCID primary language ID is lower 10 bits
            primary = lcid & 0x3FF
            lcid_map = {
                0x01: "AR", 0x02: "BG", 0x05: "CS", 0x06: "DA", 0x07: "DE",
                0x08: "EL", 0x09: "EN", 0x0A: "ES", 0x25: "ET", 0x0B: "FI",
                0x0C: "FR", 0x0E: "HU", 0x21: "ID", 0x10: "IT", 0x11: "JA",
                0x12: "KO", 0x27: "LT", 0x26: "LV", 0x14: "NB", 0x13: "NL",
                0x15: "PL", 0x16: "PT", 0x18: "RO", 0x19: "RU", 0x1B: "SK",
                0x24: "SL", 0x1D: "SV", 0x1F: "TR", 0x22: "UK", 0x04: "ZH",
            }
            return lcid_map.get(primary, "EN")
        elif IS_MAC:
            import subprocess
            out = subprocess.check_output(
                ["defaults", "read", ".GlobalPreferences", "AppleLanguages"],
                text=True, timeout=5)
            # Parse first entry like "en-US" or "fr-FR"
            for line in out.splitlines():
                line = line.strip().strip('",() ')
                if len(line) >= 2:
                    code = line[:2].upper()
                    return code if code != "NB" else "NB"  # Norwegian
            return "EN"
        else:
            import os
            lang = os.environ.get("LANG", "en_US.UTF-8")
            return lang[:2].upper()
    except Exception:
        return "EN"
```

- [ ] **Step 2: Load languages.json metadata**

```python
def _load_language_list():
    """Load language metadata from languages.json."""
    lpath = APP_DIR / "locales" / "languages.json"
    if lpath.exists():
        return json.loads(lpath.read_text(encoding="utf-8"))
    return {"EN": {"name": "English", "native": "English", "flag": "", "rtl": False}}
```

- [ ] **Step 3: Add language setting to DEFAULT_SETTINGS**

Add `"language": None` to `DEFAULT_SETTINGS` (None = auto-detect on first run).

- [ ] **Step 4: Initialize translations in __init__**

In `LinguaTaxiApp.__init__`, before `_build_ui()`:

```python
        # Load language
        lang = self.settings.get("language")
        if not lang:
            lang = _detect_os_language()
            self.settings["language"] = lang
        self._languages = _load_language_list()
        _load_translations(lang)
        self._current_lang = lang
```

- [ ] **Step 5: Commit**

```bash
git add launcher.pyw
git commit -m "[feat] add translation infrastructure, _t() function, OS language detection"
```

---

### Task 4: Launcher — Language Dropdown and String Replacement

**Files:**
- Modify: `launcher.pyw` — add language dropdown, replace all hardcoded strings with `_t()`, add `_refresh_ui()`

- [ ] **Step 1: Add language dropdown at top of window**

In `_build_ui()`, before the header frame, add a language selector row:

```python
        # ── Language Selector ──
        lang_row = ttk.Frame(main)
        lang_row.pack(fill="x", pady=(0, 4))

        ttk.Label(lang_row, text="\U0001F310", font=("Segoe UI", 12)).pack(side="left", padx=(0, 4))

        lang_values = []
        self._lang_codes = []
        for code, info in sorted(self._languages.items(), key=lambda x: x[1].get("native", "")):
            flag = info.get("flag", "")
            native = info.get("native", info.get("name", code))
            lang_values.append(f"{flag} {native}")
            self._lang_codes.append(code)

        self._lang_var = tk.StringVar()
        self._lang_combo = ttk.Combobox(lang_row, textvariable=self._lang_var,
                                         values=lang_values, state="readonly",
                                         font=("Segoe UI", 10), width=25)
        self._lang_combo.pack(side="left")
        self._lang_combo.bind("<<ComboboxSelected>>", self._on_language_changed)

        # Select current language
        if self._current_lang in self._lang_codes:
            self._lang_combo.current(self._lang_codes.index(self._current_lang))
```

- [ ] **Step 2: Store widget references for all text-bearing elements**

Go through `_build_ui()` and ensure every label, button, and labelframe has a stored reference. For example, change:
```python
ttk.Label(hdr_left, text="Live Caption & Translation", style="Subtitle.TLabel").pack(anchor="w")
```
to:
```python
self._subtitle_lbl = ttk.Label(hdr_left, text=_t("launcher.subtitle"), style="Subtitle.TLabel")
self._subtitle_lbl.pack(anchor="w")
```

Do this for ALL text-bearing widgets: buttons, labels, labelframes, the title label, section headers, etc. Replace every hardcoded string with `_t("launcher.key_name")`.

- [ ] **Step 3: Replace all hardcoded strings in methods**

Replace hardcoded strings in:
- `_start_server` / `_stop_server` status messages
- `_update_ui_state` state labels ("Stopped", "Running", etc.)
- All dialog methods (`_download_models`, `_show_tuned_models_dialog`, `_show_offline_translate_dialog`, `_show_model_manager_dialog`, `_show_update_dialog`, `_show_download_progress`, `_show_about`)
- `messagebox.show*()` calls
- Error messages
- Log messages that are user-facing (system log entries)

- [ ] **Step 4: Add _on_language_changed and _refresh_ui**

```python
    def _on_language_changed(self, event=None):
        idx = self._lang_combo.current()
        if idx < 0:
            return
        lang = self._lang_codes[idx]
        self._current_lang = lang
        self.settings["language"] = lang
        save_settings(self.settings)
        _load_translations(lang)
        self._refresh_ui()
        # Notify running server
        if self._server_running:
            try:
                import urllib.request
                port = self.settings.get("operator_port", 3001)
                data = json.dumps({"ui_language": lang}).encode()
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/config",
                    data=data, headers={"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(req, timeout=2)
            except Exception:
                pass

    def _refresh_ui(self):
        """Re-apply all translated strings to stored widget references."""
        # Close any open dialogs
        for w in self.winfo_children():
            if isinstance(w, tk.Toplevel):
                w.destroy()

        # Update all stored widget text
        self._subtitle_lbl.configure(text=_t("launcher.subtitle"))
        self._start_btn.configure(text=_t("launcher.start_server"))
        self.stop_btn.configure(text=_t("launcher.stop"))
        self._srv_frame.configure(text=_t("launcher.server"))
        # ... (all other stored references)

        # Re-evaluate state-dependent text
        self._update_ui_state(running=self._server_running)

        # Update window title
        self.title(_t("launcher.window_title"))
```

The `_refresh_ui` method must update EVERY widget that has translated text. This is tedious but necessary — go through every `self._*` widget reference.

- [ ] **Step 5: Commit**

```bash
git add launcher.pyw
git commit -m "[feat] language dropdown, replace all hardcoded strings with _t(), add _refresh_ui()"
```

---

### Task 5: Server — Language Config and Locale API

**Files:**
- Modify: `server.py` — add `ui_language` to config, locale endpoints, WebSocket broadcast

- [ ] **Step 1: Add ui_language to config defaults**

Find the config defaults and add `"ui_language": "EN"`.

- [ ] **Step 2: Add locale API endpoints**

```python
@operator_app.get("/api/locales/{lang}")
async def api_get_locale(lang: str):
    """Serve translation JSON for a language."""
    locale_path = BASE_DIR / "locales" / f"{lang.lower()}.json"
    if locale_path.exists():
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        return JSONResponse(data)
    return JSONResponse({}, 404)

@operator_app.post("/api/config")
async def api_update_config(request: Request):
    """Update config values (currently: ui_language)."""
    data = await request.json()
    if "ui_language" in data:
        config["ui_language"] = data["ui_language"]
        _save_config()
        await broadcast_all({"type": "config_update", "ui_language": data["ui_language"]})
    return JSONResponse({"ok": True})
```

Also add these routes to the display app so display clients can fetch locales.

- [ ] **Step 3: Include ui_language in WebSocket initial config**

Find where the initial config is sent on WebSocket connect. Add `"ui_language": config.get("ui_language", "EN")` to the message.

- [ ] **Step 4: Commit**

```bash
git add server.py
git commit -m "[feat] ui_language config, locale API endpoints, WebSocket language broadcast"
```

---

### Task 6: Operator Panel i18n

**Files:**
- Modify: `operator.html` — add `t()` function, `data-i18n` attributes, `applyTranslations()`, handle config_update

- [ ] **Step 1: Add translation infrastructure in JavaScript**

Add at the top of the script section:

```javascript
let _strings = {};
let _strings_en = {};
let _currentLang = 'EN';

function t(key, vars) {
    let text = _strings[key] || _strings_en[key] || key;
    if (vars) Object.entries(vars).forEach(([k, v]) => {
        text = text.replaceAll(`{${k}}`, v);
    });
    return text;
}

async function loadTranslations(lang) {
    try {
        if (!Object.keys(_strings_en).length) {
            const enResp = await fetch('/api/locales/en');
            if (enResp.ok) _strings_en = await enResp.json();
        }
        const resp = await fetch(`/api/locales/${lang.toLowerCase()}`);
        if (resp.ok) _strings = await resp.json();
        else _strings = {..._strings_en};
    } catch(e) {
        _strings = {..._strings_en};
    }
    _currentLang = lang;
    applyTranslations();
}

function applyTranslations() {
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        const ph = el.getAttribute('data-i18n-placeholder');
        if (ph) el.placeholder = t(key);
        else el.textContent = t(key);
    });
    // Update RTL
    const langMeta = {AR: true}; // Arabic is RTL
    document.documentElement.dir = langMeta[_currentLang] ? 'rtl' : 'ltr';
    document.documentElement.lang = _currentLang.toLowerCase();
}
```

- [ ] **Step 2: Add data-i18n attributes to all static HTML text**

For every static text element in the HTML, add `data-i18n="operator.key_name"`:

```html
<div class="section-label" data-i18n="operator.session_title">Session Title</div>
<button class="btn btn-g btn-go" data-i18n="operator.go_live">GO LIVE — Start Captioning</button>
<input placeholder="Session title..." data-i18n="operator.session_title_placeholder" data-i18n-placeholder="1">
```

- [ ] **Step 3: Replace dynamic strings in JavaScript with t() calls**

Replace all hardcoded strings in JavaScript with `t()`:
```javascript
// Before:
alert('Config error — check server logs');
// After:
alert(t('operator.config_error'));

// Before:
status.textContent = 'Connected';
// After:
status.textContent = t('operator.connected');
```

Do this for ALL dynamic strings: status messages, error messages, alerts, generated HTML text.

- [ ] **Step 4: Load translations on page init and handle config_update**

In the WebSocket message handler, add:
```javascript
else if(m.type==='config_update' && m.ui_language){
    loadTranslations(m.ui_language);
}
```

On page load, after WebSocket connects and receives initial config:
```javascript
if(m.type==='config' || m.type==='status'){
    // ... existing config handling ...
    if(m.ui_language) loadTranslations(m.ui_language);
}
```

- [ ] **Step 5: Commit**

```bash
git add operator.html
git commit -m "[feat] operator panel i18n with t(), data-i18n, and dynamic language switching"
```

---

### Task 7: Display and Dictation i18n

**Files:**
- Modify: `display.html` — add `t()`, translate minimal strings
- Modify: `dictation.html` — add `t()`, translate strings

- [ ] **Step 1: Add translation infrastructure to display.html**

Same pattern as operator — add `t()`, `loadTranslations()`, `applyTranslations()` functions. Display has very few strings (~5) so this is quick.

Add `data-i18n` to any static text and use `t()` for dynamic strings.

- [ ] **Step 2: Add translation infrastructure to dictation.html**

Same pattern. Dictation has ~25 strings (button labels, status messages, placeholder text).

- [ ] **Step 3: Handle config_update WebSocket message in both pages**

Add the `config_update` handler to switch languages dynamically.

- [ ] **Step 4: Commit**

```bash
git add display.html dictation.html
git commit -m "[feat] display and dictation i18n with dynamic language switching"
```

---

### Task 8: Inno Setup Multi-Language Installer

**Files:**
- Modify: `build/windows/installer.iss` — add `[Languages]`, `[CustomMessages]`, `{cm:}` references
- Create/copy: `build/windows/languages/*.isl`

- [ ] **Step 1: Set up language ISL files**

Inno Setup 6 ships with ISL files for ~25 languages at `C:\Program Files (x86)\Inno Setup 6\Languages\`. Copy/reference the built-in ones and generate custom ISL files for any missing languages (AR, KO, ID) using the DeepL translations.

- [ ] **Step 2: Add [Languages] section with all 30 languages**

Replace the current single-language entry:
```ini
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
```

With entries for all 30 languages:
```ini
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
; ... (all built-in languages)
Name: "arabic"; MessagesFile: "languages\Arabic.isl"
Name: "korean"; MessagesFile: "languages\Korean.isl"
; ... (custom languages)
```

- [ ] **Step 3: Add [CustomMessages] with per-language translations**

Move all hardcoded task descriptions and status messages to `[CustomMessages]`:

```ini
[CustomMessages]
english.DesktopShortcut=Create a desktop shortcut
spanish.DesktopShortcut=Crear un acceso directo en el escritorio
french.DesktopShortcut=Créer un raccourci sur le bureau
; ... for all languages and all custom strings
```

- [ ] **Step 4: Replace hardcoded strings with {cm:} references**

In `[Tasks]`:
```ini
Name: "desktopicon"; Description: "{cm:DesktopShortcut}"; ...
```

In `[Run]` StatusMsg values:
```ini
StatusMsg: "{cm:CheckingModels}"; ...
```

In `[Code]` Pascal strings:
```pascal
Result := '{cm:UpgradeMessage}' + ...
```

- [ ] **Step 5: Add locales directory to [Files]**

```ini
Source: "..\..\locales\*"; DestDir: "{app}\locales"; Flags: ignoreversion recursesubdirs createallsubdirs
```

- [ ] **Step 6: Commit**

```bash
git add build/windows/installer.iss build/windows/languages/
git commit -m "[feat] multi-language installer with 30 languages and translated task descriptions"
```

---

### Task 9: RTL Support for Arabic

**Files:**
- Modify: `operator.html` — conditional RTL stylesheet
- Modify: `display.html` — RTL support
- Modify: `dictation.html` — RTL support
- Modify: `launcher.pyw` — RTL widget alignment

- [ ] **Step 1: Add RTL CSS rules to HTML pages**

Add a `<style>` block or conditional class that activates when `dir="rtl"`:

```css
[dir="rtl"] .sidebar { right: 0; left: auto; }
[dir="rtl"] .main-content { margin-right: 400px; margin-left: 0; }
[dir="rtl"] input, [dir="rtl"] select { text-align: right; }
[dir="rtl"] .source-row { flex-direction: row-reverse; }
[dir="rtl"] .spbtn .hk { left: 4px; right: auto; }
```

- [ ] **Step 2: Add RTL support to launcher.pyw**

In `_refresh_ui()`, when switching to/from Arabic, update widget alignment:

```python
        is_rtl = self._languages.get(self._current_lang, {}).get("rtl", False)
        anchor = "e" if is_rtl else "w"
        justify = "right" if is_rtl else "left"
        # Update label anchors
        for lbl in self._section_labels:
            lbl.configure(anchor=anchor)
```

- [ ] **Step 3: Commit**

```bash
git add operator.html display.html dictation.html launcher.pyw
git commit -m "[feat] RTL layout support for Arabic"
```

---

### Task 10: Run Translation Generation

**Files:**
- Generate: `locales/*.json` (29 language files)

- [ ] **Step 1: Run the translation script with the user's DeepL API key**

```bash
python scripts/generate_translations.py --api-key YOUR_DEEPL_API_KEY
```

- [ ] **Step 2: Verify a few translations**

Spot-check `locales/es.json`, `locales/ja.json`, `locales/ar.json` for correctness.

- [ ] **Step 3: Commit all translations**

```bash
git add locales/
git commit -m "[feat] generate translations for 29 languages via DeepL API"
```

---

### Task 11: Integration Verification

- [ ] **Step 1: Syntax check all modified files**

```bash
python -c "import ast; [ast.parse(open(f, encoding='utf-8').read()) for f in ['server.py','launcher.pyw']]"
```

- [ ] **Step 2: Verify git log**

```bash
git log --oneline -12
```

- [ ] **Step 3: Manual smoke test plan**

1. Launch app — language dropdown at top with flags and native names
2. Switch to Spanish — all UI text changes to Spanish
3. Switch to Japanese — CJK characters render correctly
4. Switch to Arabic — text right-aligned, layout mirrored
5. Start server — operator panel loads in selected language
6. Change language in launcher — operator panel switches dynamically
7. Close and reopen — language persists from settings
8. First launch with no settings — auto-detects OS language
