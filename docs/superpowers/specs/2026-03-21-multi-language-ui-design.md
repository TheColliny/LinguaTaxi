# Design: Multi-Language UI & Installer

**Date:** 2026-03-21
**Status:** Approved

## Overview

Localize the entire LinguaTaxi UI (launcher, operator panel, display, dictation, installer) into all 30 DeepL-supported languages. Translations generated once via DeepL API and shipped as static JSON files. OS language auto-detected on first launch. Language selectable from launcher dropdown and installer language page.

## Supported Languages (30)

AR (Arabic), BG (Bulgarian), CS (Czech), DA (Danish), DE (German), EL (Greek), EN (English), ES (Spanish), ET (Estonian), FI (Finnish), FR (French), HU (Hungarian), ID (Indonesian), IT (Italian), JA (Japanese), KO (Korean), LT (Lithuanian), LV (Latvian), NB (Norwegian), NL (Dutch), PL (Polish), PT (Portuguese), RO (Romanian), RU (Russian), SK (Slovak), SL (Slovenian), SV (Swedish), TR (Turkish), UK (Ukrainian), ZH (Chinese)

## 1. Translation File Architecture

### Source Files

`locales/en.json` — source of truth with all English strings, keyed by component. Strings with dynamic values use `{variable}` placeholders:
```json
{
  "launcher.start_server": "Start Server",
  "launcher.stop": "Stop",
  "launcher.update_available": "LinguaTaxi v{version} is available!",
  "launcher.download_result": "{succeeded} downloaded, {failed} failed",
  "launcher.source_n": "Source {num}:",
  "operator.go_live": "GO LIVE — Start Captioning",
  "operator.translation_slot": "Translation {num}",
  "display.no_languages": "No languages configured",
  "installer.desktop_shortcut": "Create a desktop shortcut"
}
```

One JSON file per language: `locales/es.json`, `locales/fr.json`, etc. — all 30 languages. Generated once using DeepL API during development, committed to repo as static files.

### Placeholder & Interpolation Syntax

All dynamic strings use `{variable_name}` placeholders:
- Python `_t()`: `_t("launcher.update_available", version="1.1.0")` — replaces `{version}` with value
- JavaScript `t()`: `t("operator.translation_slot", {num: 3})` — same pattern

No pluralization system — use separate keys for singular/plural where needed:
```json
{
  "dictation.word_count_one": "1 word",
  "dictation.word_count_other": "{count} words"
}
```

### Language Metadata

`locales/languages.json` maps codes to native names, flag emoji, and RTL flag:
```json
{
  "EN": {"name": "English", "native": "English", "flag": "🇬🇧", "rtl": false},
  "ES": {"name": "Spanish", "native": "Español", "flag": "🇪🇸", "rtl": false},
  "AR": {"name": "Arabic", "native": "العربية", "flag": "🇸🇦", "rtl": true},
  "JA": {"name": "Japanese", "native": "日本語", "flag": "🇯🇵", "rtl": false},
  "ZH": {"name": "Chinese", "native": "中文", "flag": "🇨🇳", "rtl": false}
}
```

### Installer Translation Files

Custom `.isl` files for all 30 languages in `build/windows/languages/`. Use Inno Setup's built-in `.isl` files where available, generate custom `.isl` files via DeepL for the remainder (notably Arabic, Korean, Indonesian). Arabic `.isl` file includes `RightToLeft=yes`.

## 2. Language Selection UI

### Launcher — Top-of-Window Dropdown
- Globe icon + dropdown at the very top of the window, above the header/title
- Dropdown shows: `[flag emoji] Native Name` for each language (e.g., `🇪🇸 Español`, `🇯🇵 日本語`)
- Sorted alphabetically by native name
- Changing the language immediately reloads all UI strings without restarting
- Selection persisted in `launcher_settings.json` as `"language": "ES"`

### Default Language Detection
- First launch: detect OS language using platform-specific methods:
  - **Windows:** `ctypes.windll.kernel32.GetUserDefaultUILanguage()` returns Windows LCID, mapped to DeepL language code
  - **macOS:** `subprocess.check_output(["defaults", "read", ".GlobalPreferences", "AppleLanguages"])`, parse first entry
  - **Linux:** `os.environ.get("LANG", "en")`, extract language code
- If detected language matches a supported language, use it; otherwise default to English
- User can override at any time

### Installer Language Selection
- Inno Setup shows language selection dialog on first page (built-in feature when multiple `[Languages]` entries configured)
- Auto-selects based on OS language
- User can switch from dropdown before proceeding
- All installer text (tasks, status messages, custom dialogs, upgrade messaging) uses selected language

### Web Pages
- Server stores selected language in `config.json` as `ui_language`
- Launcher communicates language changes to running server via `POST /api/config` with `{ui_language: "ES"}`
- Server broadcasts `{type: "config_update", ui_language: "ES"}` to all connected WebSocket clients
- On WebSocket connect, server sends current `ui_language` in the initial config message
- Web pages fetch translations from `GET /api/locales/{lang}` and call `applyTranslations()`
- On `config_update` message with new `ui_language`, pages re-fetch translations and re-apply
- Update `document.documentElement.lang` to active language code for accessibility

## 3. String Extraction & Runtime Loading

### Python (launcher.pyw)

**Translation function:**
```python
_strings = {}  # loaded from locales/{lang}.json
_strings_en = {}  # English fallback

def _t(key, **kwargs):
    text = _strings.get(key) or _strings_en.get(key, key)
    if kwargs:
        for k, v in kwargs.items():
            text = text.replace(f"{{{k}}}", str(v))
    return text
```

**Widget reference refactoring:**
- All text-bearing widgets in `_build_ui()` must store references for later update (e.g., `self._start_btn`, `self._srv_frame`, etc.)
- `LabelFrame` widgets store references for `configure(text=...)` updates
- Inline labels that currently lack references get assigned to instance variables

**`_refresh_ui()` method:**
- Re-applies `_t()` to all stored widget references
- Re-evaluates state-dependent text (e.g., "Stopped"/"Running" based on `_server_running`)
- Any open dialogs are closed before refresh (simpler than refreshing dialog contents)
- Also updates RTL alignment if switching to/from Arabic

### RTL (Right-to-Left) Support

**Arabic requires special handling across all components:**

- **launcher.pyw (tkinter):** When Arabic is selected, set `justify="right"` and `anchor="e"` on text widgets. Reverse pack order for left/right layouts using a helper function.
- **HTML pages:** Set `dir="rtl"` on `<html>` element when Arabic is active. Use CSS logical properties where possible (`margin-inline-start` vs `margin-left`). Add a small `rtl.css` override stylesheet loaded conditionally.
- **Inno Setup:** Arabic `.isl` file includes `RightToLeft=yes` directive.

### Layout & Overflow

- Buttons use `expand=True` and `fill="x"` in tkinter — text wraps naturally
- Operator panel CSS: add `overflow-wrap: break-word` to button and label styles
- Translation generation script flags any translation >50% longer than the English source for manual review
- Installer task descriptions: Inno Setup wraps long text automatically in checkboxes

### HTML/JavaScript (operator.html, display.html, dictation.html)

**Translation function:**
```javascript
let _strings = {};
let _strings_en = {};

function t(key, vars) {
    let text = _strings[key] || _strings_en[key] || key;
    if (vars) Object.entries(vars).forEach(([k, v]) => {
        text = text.replace(`{${k}}`, v);
    });
    return text;
}
```

- Static text elements use `data-i18n="operator.go_live"` attributes
- `applyTranslations()` function scans all `[data-i18n]` elements and sets `textContent`
- Dynamic strings (status messages, errors, alerts) use `t(key)` inline in JavaScript
- Strings in dynamically generated HTML (e.g., `buildSpeakerBtns()`, status updates, alert calls) all use `t(key)` calls

**String extraction guidance:** All occurrences of `.textContent=`, `.innerHTML=`, `alert(`, `.innerText=`, `.placeholder=`, `.title=`, and hardcoded strings in template literals must be identified and converted.

### Inno Setup (installer.iss)
- `[Languages]` section with one entry per language pointing to `.isl` file
- Custom messages in `[CustomMessages]` section with per-language overrides
- Task descriptions, status messages, Pascal code strings use `{cm:CustomMessageName}` syntax

## 4. Translation Generation (One-Time Build Step)

- Script: `scripts/generate_translations.py`
- Input: `locales/en.json` (source strings), DeepL API key (passed as argument or env var)
- Process:
  1. Load all English source strings
  2. Batch-translate via DeepL API for each of the 29 non-English languages
  3. Use DeepL's `context` parameter with "UI button label" / "dialog message" hints for better quality
  4. Flag any translation >50% longer than English source for manual review
- Output: `locales/{lang}.json` for each language
- Also generates custom `.isl` files for Inno Setup languages without built-in support
- Supports `locales/overrides/{lang}.json` — manual corrections that take precedence over auto-generated translations
- Run once during development, output committed to repo
- Re-run when source strings change (add/modify keys in `en.json`, run script, commit updates)

## 5. Server Changes

- New config field: `ui_language` (default: `"EN"`) in `config.json`
- New endpoint: `GET /api/locales/{lang}` — serves the translation JSON file for a given language
- New endpoint: `POST /api/config` — accepts `{ui_language: "ES"}` to update language
- On language change: broadcasts `{type: "config_update", ui_language: "ES"}` to all WebSocket clients
- Initial WebSocket config message includes `ui_language` field

## Files Created/Modified

| File | Action | Purpose |
|------|--------|---------|
| `locales/en.json` | Create | Source English strings (~250 keys) |
| `locales/{lang}.json` (x29) | Create | Translated strings per language |
| `locales/languages.json` | Create | Language metadata (native names, flags, RTL) |
| `locales/overrides/` | Create | Directory for manual translation corrections |
| `scripts/generate_translations.py` | Create | DeepL batch translation script |
| `build/windows/languages/*.isl` | Create | Inno Setup translation files |
| `launcher.pyw` | Modify | Add `_t()`, language dropdown, `_refresh_ui()`, OS detection, widget refs |
| `server.py` | Modify | `ui_language` config, `/api/locales/` endpoint, `/api/config` endpoint, WebSocket broadcast |
| `operator.html` | Modify | Add `t()`, `data-i18n` attributes, `applyTranslations()`, RTL CSS |
| `display.html` | Modify | Add `t()`, `data-i18n`, RTL CSS |
| `dictation.html` | Modify | Add `t()`, `data-i18n` attributes, RTL CSS |
| `installer.iss` | Modify | Multi-language `[Languages]`, `[CustomMessages]`, `{cm:}` refs, include `locales/` in `[Files]` |
