# Offline Translation Performance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make offline M2M-100 translation keep up with real-time speech by adding configurable CPU core allocation, greedy decoding, and stale interim dropping.

**Architecture:** CTranslate2 `intra_threads` is exposed as a user-configurable `translate_cores` config key. Model cache is cleared on change so new thread count takes effect. A generation counter per translation slot skips stale interim translations that pile up when the model is slower than the interim interval.

**Tech Stack:** Python (CTranslate2, sentencepiece), FastAPI, vanilla JS operator panel

---

### Task 1: Add `get_default_cores()` and threading to model loading in `offline_translate.py`

**Files:**
- Modify: `offline_translate.py:1-27` (imports, module-level)
- Modify: `offline_translate.py:514-560` (`_load_opus_model`, `_load_m2m_model`)

- [ ] **Step 1: Add `get_default_cores()` helper**

At the top of `offline_translate.py`, after the existing imports (line 24), add:

```python
def get_default_cores():
    """Default CPU cores for translation: system cores / 4, min 1."""
    return max(1, (os.cpu_count() or 4) // 4)

def get_max_cores():
    """Maximum allowed cores for translation: system cores - 1, min 1."""
    return max(1, (os.cpu_count() or 4) - 1)
```

- [ ] **Step 2: Add module-level `_intra_threads` variable**

After the new helper functions, add a module-level variable that model loaders will read:

```python
_intra_threads = get_default_cores()
```

- [ ] **Step 3: Add `set_threads(n)` function**

Below `_intra_threads`, add:

```python
def set_threads(n):
    """Set the number of intra-op threads for translation models.
    Clears the model cache so the next call re-creates translators."""
    global _intra_threads
    _intra_threads = max(1, min(n, get_max_cores()))
    reload_models()
```

- [ ] **Step 4: Add `reload_models()` function**

Below `set_threads`, add:

```python
def reload_models():
    """Clear cached models so they reload with current thread settings."""
    with _models_lock:
        _loaded_models.clear()
    log.info(f"Offline translation models unloaded (will reload with intra_threads={_intra_threads})")
```

- [ ] **Step 5: Update `_load_opus_model` to use `_intra_threads`**

In `_load_opus_model` (line 524-525), change the Translator constructor from:

```python
        translator = ctranslate2.Translator(model_path, device="cpu",
                                             compute_type="int8")
```

to:

```python
        translator = ctranslate2.Translator(model_path, device="cpu",
                                             compute_type="int8",
                                             inter_threads=1,
                                             intra_threads=_intra_threads)
```

- [ ] **Step 6: Update `_load_m2m_model` to use `_intra_threads`**

In `_load_m2m_model` (line 552-553), change the Translator constructor from:

```python
        translator = ctranslate2.Translator(model_path, device="cpu",
                                             compute_type="int8")
```

to:

```python
        translator = ctranslate2.Translator(model_path, device="cpu",
                                             compute_type="int8",
                                             inter_threads=1,
                                             intra_threads=_intra_threads)
```

- [ ] **Step 7: Verify — start server, confirm no import errors**

Run: `python server.py` — confirm it starts without errors. If an offline model is downloaded, check the log for any ctranslate2 initialization issues.

- [ ] **Step 8: Commit**

```bash
git add offline_translate.py
git commit -m "[feat] add configurable CPU core threading to offline translation models"
```

---

### Task 2: Add `beam_size=1` to translation functions in `offline_translate.py`

**Files:**
- Modify: `offline_translate.py:571` (`_translate_opus`)
- Modify: `offline_translate.py:590-591` (`_translate_m2m`)

- [ ] **Step 1: Update `_translate_opus` beam_size**

In `_translate_opus` (line 571), change:

```python
    results = translator.translate_batch([tokens])
```

to:

```python
    results = translator.translate_batch([tokens], beam_size=1)
```

- [ ] **Step 2: Update `_translate_m2m` beam_size**

In `_translate_m2m` (lines 590-591), change:

```python
    results = translator.translate_batch([source_tokens],
                                          target_prefix=target_prefix)
```

to:

```python
    results = translator.translate_batch([source_tokens],
                                          target_prefix=target_prefix,
                                          beam_size=1)
```

- [ ] **Step 3: Verify — test translation if model available**

If M2M or OPUS is downloaded, run:
```bash
python offline_translate.py --test "The unemployment rate is 3.5 percent" --target ES --models-dir models
```
Confirm it still produces a valid Spanish translation.

- [ ] **Step 4: Commit**

```bash
git add offline_translate.py
git commit -m "[perf] use greedy decoding (beam_size=1) for offline translation"
```

---

### Task 3: Add `translate_cores` to server config and wire to `offline_translate`

**Files:**
- Modify: `server.py:157-184` (DEFAULT_CONFIG)
- Modify: `server.py:1644-1731` (`o_update` config endpoint)
- Modify: `server.py:1566-1589` (config response — add `system_cpu_count`)
- Modify: `server.py:1593-1615` (fallback config response — add `system_cpu_count`)

- [ ] **Step 1: Add `translate_cores` to DEFAULT_CONFIG**

In `server.py` at `DEFAULT_CONFIG` (after line 177, `"voice_id_threshold": 0.65,`), add:

```python
    "translate_cores": 0,
```

A value of `0` means "use default" (`os.cpu_count() // 4`). This avoids baking a machine-specific core count into the portable config file.

- [ ] **Step 2: Initialize `offline_translate` thread count at startup**

After `config = load_config()` (line 202), add:

```python
_configured_cores = config.get("translate_cores", 0)
if _configured_cores > 0:
    offline_translate.set_threads(_configured_cores)
```

- [ ] **Step 3: Add `translate_cores` Form parameter to `o_update`**

In the `o_update` function signature (line 1644-1658), add after the `footer_text` parameter:

```python
    translate_cores: int = Form(None),
```

- [ ] **Step 4: Handle `translate_cores` in `o_update` body**

After the `footer_text` handling (line 1729), before `save_config(config)` (line 1731), add:

```python
    if translate_cores is not None:
        clamped = max(1, min(translate_cores, offline_translate.get_max_cores()))
        config["translate_cores"] = clamped
        offline_translate.set_threads(clamped)
```

- [ ] **Step 5: Add `system_cpu_count` and `translate_cores` to config response**

In the main config endpoint response (around line 1588, after `"footer_position"`), add:

```python
            "system_cpu_count": os.cpu_count() or 4,
            "translate_cores": config.get("translate_cores", 0),
            "translate_cores_default": offline_translate.get_default_cores(),
```

- [ ] **Step 6: Add same fields to fallback config response**

In the fallback/error config response (around line 1614, after `"footer_position"`), add:

```python
            "system_cpu_count": os.cpu_count() or 4,
            "translate_cores": config.get("translate_cores", 0),
            "translate_cores_default": offline_translate.get_default_cores(),
```

- [ ] **Step 7: Verify — start server, hit config endpoint**

Start the server, open `http://localhost:3001/api/config` in browser. Confirm `system_cpu_count`, `translate_cores`, and `translate_cores_default` appear in the JSON response.

- [ ] **Step 8: Commit**

```bash
git add server.py
git commit -m "[feat] add translate_cores config with dynamic default based on system CPU count"
```

---

### Task 4: Add `/api/offline-translate/reload` endpoint

**Files:**
- Modify: `server.py:1960-1963` (after existing offline-translate endpoints)

- [ ] **Step 1: Add the reload endpoint**

After the `o_offline_progress` endpoint (line 1963), add:

```python
@operator_app.post("/api/offline-translate/reload")
async def o_offline_reload():
    """Clear cached translation models so they reload with current settings."""
    offline_translate.reload_models()
    cores = config.get("translate_cores", 0)
    effective = cores if cores > 0 else offline_translate.get_default_cores()
    return JSONResponse({"status": "reloaded", "intra_threads": effective})
```

- [ ] **Step 2: Commit**

```bash
git add server.py
git commit -m "[feat] add /api/offline-translate/reload endpoint for live core count changes"
```

---

### Task 5: Add stale interim translation dropping in `server.py`

**Files:**
- Modify: `server.py:313-315` (globals area, before `_translate_pool`)
- Modify: `server.py:1090-1115` (`_translate_all`)
- Modify: `server.py:1117-1147` (`_do_translate`)

- [ ] **Step 1: Add generation counter globals**

Before `_translate_pool` (line 315), add:

```python
_translate_gen = {}
_translate_gen_lock = threading.Lock()
```

- [ ] **Step 2: Update `_translate_all` to stamp interims with generation**

In `_translate_all`, replace the `_translate_pool.submit` call (lines 1114-1115):

```python
        _translate_pool.submit(_do_translate,
            text, t["lang"], i, msg_type, loop, line_id, speaker_override, source_lang)
```

with:

```python
        gen = None
        if msg_type == "interim_translation":
            with _translate_gen_lock:
                gen = _translate_gen.get(i, 0) + 1
                _translate_gen[i] = gen
        _translate_pool.submit(_do_translate,
            text, t["lang"], i, msg_type, loop, line_id, speaker_override, source_lang, gen)
```

- [ ] **Step 3: Update `_do_translate` signature and add staleness check**

Change the `_do_translate` function signature (line 1117) from:

```python
def _do_translate(text, lang, slot, msg_type, loop, line_id=None, speaker_override=None, source_lang=None):
```

to:

```python
def _do_translate(text, lang, slot, msg_type, loop, line_id=None, speaker_override=None, source_lang=None, generation=None):
```

Then, at the top of the function body (before line 1118), add the staleness check:

```python
    if generation is not None:
        with _translate_gen_lock:
            if _translate_gen.get(slot, 0) != generation:
                return
```

- [ ] **Step 4: Verify — start server with offline translation, watch logs**

Start the server with an offline translation slot. Speak continuously. Check the console logs — you should see final translations still appearing (`[0] ES (offline): ...`). Stale interims will silently return without logging. To confirm dropping works, temporarily add a log line inside the early return:

```python
                log.debug(f"   [{slot}] Skipping stale interim (gen {generation} < {_translate_gen.get(slot, 0)})")
```

Remove after confirming.

- [ ] **Step 5: Commit**

```bash
git add server.py
git commit -m "[perf] drop stale interim translations to prevent unbounded queue growth"
```

---

### Task 6: Add CPU cores UI to operator panel

**Files:**
- Modify: `operator.html` — JS globals area (~line 646), `applyConfig` (~line 762), `buildTransSlots` (~line 1121), `saveAll` (~line 1805)

- [ ] **Step 1: Add global variables for core count**

In the JS globals area (near line 646, after `let offlinePollTimers={};`), add:

```javascript
let systemCpuCount=4;
let translateCores=0;
let translateCoresDefault=1;
```

- [ ] **Step 2: Read core count from config in `applyConfig`**

In `applyConfig` (near line 775, after `offlineStatus=c.offline_translate||{opus:{},m2m100:{}};`), add:

```javascript
    systemCpuCount=c.system_cpu_count||4;
    translateCores=c.translate_cores||0;
    translateCoresDefault=c.translate_cores_default||Math.max(1,Math.floor(systemCpuCount/4));
```

- [ ] **Step 3: Add core count UI below the translation slots**

In the HTML, after the `extNote` div (line 466), before the closing `</div>` of the languages section (line 467), add:

```html
      <div id="translateCoresRow" style="margin-top:10px;display:none">
        <div style="display:flex;align-items:center;gap:6px">
          <span style="font-size:11px">CPU Cores for Translation</span>
          <span id="translateCoresInfo" title="" style="cursor:help;font-size:11px;width:16px;height:16px;border-radius:50%;border:1px solid rgba(255,255,255,.3);display:inline-flex;align-items:center;justify-content:center;color:rgba(255,255,255,.5)">i</span>
        </div>
        <input type="number" id="iTranslateCores" min="1" max="1" value="1"
               onchange="onTranslateCoresChange(this.value)"
               style="width:60px;margin-top:4px">
      </div>
```

- [ ] **Step 4: Add visibility logic for core count row**

At the end of `buildTransSlots` (inside the function, after the existing loop ends at line 1171), add:

```javascript
  const coresRow=document.getElementById('translateCoresRow');
  const hasOffline=translations.some(tr=>tr.mode&&tr.mode.startsWith('offline'));
  if(coresRow){
    coresRow.style.display=hasOffline?'block':'none';
    const inp=document.getElementById('iTranslateCores');
    const maxCores=Math.max(1,systemCpuCount-1);
    const effective=translateCores>0?translateCores:translateCoresDefault;
    if(inp){
      inp.min=1;
      inp.max=maxCores;
      inp.value=effective;
    }
    const infoEl=document.getElementById('translateCoresInfo');
    if(infoEl){
      infoEl.title='How many CPU cores to use for translation. Default is '+translateCoresDefault+'. Increase if translation is lagging behind real-time. Decrease if translation is causing system lag.';
    }
  }
```

- [ ] **Step 5: Add `onTranslateCoresChange` handler**

After `onSlotModeChange` (around line 1178), add:

```javascript
function onTranslateCoresChange(val){
  const n=parseInt(val,10);
  if(isNaN(n)||n<1) return;
  const maxCores=Math.max(1,systemCpuCount-1);
  const clamped=Math.min(n,maxCores);
  translateCores=clamped;
  const inp=document.getElementById('iTranslateCores');
  if(inp) inp.value=clamped;
  const fd=new FormData();
  fd.append('translate_cores',clamped);
  fetch('/api/config',{method:'POST',body:fd});
  fetch('/api/offline-translate/reload',{method:'POST'});
}
```

- [ ] **Step 6: Verify in browser**

1. Start the server, open `http://localhost:3001`
2. Set a translation slot to any offline mode (offline-auto, offline-opus, or offline-m2m)
3. Confirm the "CPU Cores for Translation" row appears below the translation slots
4. Hover over the (i) icon — confirm tooltip shows the correct default for your system
5. Change the core count — confirm it persists after page reload
6. Set all slots back to DeepL — confirm the core count row hides

- [ ] **Step 7: Commit**

```bash
git add operator.html
git commit -m "[feat] add CPU cores setting for offline translation with dynamic default and info tooltip"
```

---

### Task 7: End-to-end verification and patch build

**Files:**
- Modify: `build/windows/patch_files.iss`

- [ ] **Step 1: Manual end-to-end test**

1. Start server with M2M or OPUS offline translation active
2. Speak for ~30 seconds continuously
3. Watch logs — finals should all translate, stale interims should be dropped silently
4. Change CPU cores in operator panel mid-session
5. Confirm next translation uses new core count (log message: "Offline translation models unloaded (will reload with intra_threads=N)")
6. Confirm core count persists after server restart

- [ ] **Step 2: Update `patch_files.iss`**

```
; Auto-generated — Patch 2 for v1.0.3
; Offline translation performance: dynamic CPU cores, beam_size=1, stale interim dropping
Source: "..\..\offline_translate.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\operator.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\server.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\version.json"; DestDir: "{app}"; Flags: ignoreversion
```

- [ ] **Step 3: Build patches**

```bash
cd build/windows
"C:/Users/Laptop/AppData/Local/Programs/Inno Setup 6/ISCC.exe" //DEDITION=Full //DPATCH_VER=1.0.3 //DPATCH_NUM=2 patch_installer.iss
"C:/Users/Laptop/AppData/Local/Programs/Inno Setup 6/ISCC.exe" //DEDITION=Lite //DPATCH_VER=1.0.3 //DPATCH_NUM=2 patch_installer.iss
```

Expected output: `LinguaTaxi-GPU-Patch-1.0.3-p2.exe` and `LinguaTaxi-CPU-Patch-1.0.3-p2.exe`

- [ ] **Step 4: Commit patch file**

```bash
git add build/windows/patch_files.iss
git commit -m "[build] patch 2 — offline translation performance improvements"
```
