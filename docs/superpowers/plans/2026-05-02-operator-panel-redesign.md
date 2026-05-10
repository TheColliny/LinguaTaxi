# Operator Panel Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize operator panel layout, add collapsible sections, combine styling panels, fix GO LIVE/Resume Translation button sync, and move footer image to display grid with positioning controls.

**Architecture:** All changes are in three files: `server.py` (new config keys + status endpoint), `operator.html` (HTML restructure + CSS + JS), and `display.html` (footer grid row + positioning). The operator panel's left-side `.ctrl` div gets its sections reordered and wrapped in collapsible containers. Button sync uses a new `/api/status` polling endpoint. Footer positioning is broadcast to display clients via the existing WebSocket + config_update mechanism.

**Tech Stack:** HTML/CSS/JS (vanilla, no frameworks), Python/FastAPI (server.py), WebSocket broadcast

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `server.py` | Modify | New config keys (`collapsed_sections`, `footer_position`), new `GET /api/status` endpoint, accept new config fields in POST |
| `operator.html` | Modify | HTML restructure (section reorder), collapsible CSS/JS, combined styling panel, button sync polling, footer position controls |
| `display.html` | Modify | Footer image dedicated bottom row with CSS positioning from config |

---

### Task 1: Server — New Config Keys and Status Endpoint

**Files:**
- Modify: `server.py:156-180` (DEFAULT_CONFIG)
- Modify: `server.py:1244-1256` (_style_config)
- Modify: `server.py` (add new endpoint, modify config POST handler)

This task adds the server-side support: new config keys, a status endpoint for button sync, and footer position in the style broadcast.

- [ ] **Step 1: Add new config keys to DEFAULT_CONFIG**

In `server.py`, add `collapsed_sections` and `footer_position` to `DEFAULT_CONFIG` (around line 176, before `display_grids`):

```python
    "collapsed_sections": ["languages"],
    "footer_position": 50,
```

`collapsed_sections` is a list of section IDs that should be collapsed. Default: `["languages"]` (Language Slots defaults collapsed per spec).

`footer_position` is a percentage 0-100 (0=left, 50=center, 100=right). Default: 50 (center).

- [ ] **Step 2: Add GET /api/status endpoint**

Add this endpoint to the operator app, near the other operator endpoints (after the `/api/config` GET handler, around line 1556):

```python
@operator_app.get("/api/status")
async def o_status():
    return JSONResponse({
        "captioning_paused": captioning_paused,
        "translation_paused": translation_paused,
    })
```

This returns the actual server-side state of the two toggle flags, which the operator panel will poll every 5 seconds to keep buttons in sync.

- [ ] **Step 3: Include footer_position in _style_config**

In the `_style_config()` function (line 1244), add `footer_position` so display clients receive it:

```python
def _style_config():
    """Common style config for all display clients."""
    return {
        "session_title": config.get("session_title", "Live Captioning"),
        "input_lang": config.get("input_lang", "EN"),
        "input_lang_name": DEEPL_SOURCE_LANGS.get(config.get("input_lang","EN"), "English"),
        "footer_image": config.get("footer_image"),
        "footer_position": config.get("footer_position", 50),
        "font_size": config.get("font_size", 42),
        "max_lines": config.get("max_lines", 3),
        "bg_color": config.get("bg_color", "#00004D"),
        "font_family": config.get("font_family", "atkinson"),
        "caption_color": config.get("caption_color", "#FFFFFF"),
    }
```

- [ ] **Step 4: Accept new config fields in POST /api/config**

In the `o_save_config()` handler (the POST `/api/config` endpoint), add parameters for the new fields. Find the function signature and add:

```python
    collapsed_sections: Optional[str] = Form(None),
    footer_position: Optional[int] = Form(None),
```

And in the body, before `save_config(config)`:

```python
    if collapsed_sections is not None:
        try:
            config["collapsed_sections"] = json.loads(collapsed_sections)
        except (json.JSONDecodeError, TypeError):
            pass
    if footer_position is not None:
        config["footer_position"] = max(0, min(100, footer_position))
```

- [ ] **Step 5: Include collapsed_sections in operator config response**

In the operator's `GET /api/config` handler (around line 1535), add to the returned JSON:

```python
            "collapsed_sections": config.get("collapsed_sections", ["languages"]),
            "footer_position": config.get("footer_position", 50),
```

- [ ] **Step 6: Commit**

```bash
git add server.py
git commit -m "[feat] add collapsed_sections, footer_position config keys and GET /api/status endpoint"
```

---

### Task 2: Operator Panel — Collapsible Section CSS and JS

**Files:**
- Modify: `operator.html:10-170` (CSS section)
- Modify: `operator.html:504-540` (JS section, early in `<script>`)

This task adds the CSS styles and JS functions for collapsible sections, without restructuring the HTML yet.

- [ ] **Step 1: Add collapsible section CSS**

In `operator.html`, add these styles after the `.card` rule (around line 84, after the `.card{...}` rule):

```css
/* ── Collapsible sections ── */
.coll-section{background:var(--card);border:1px solid var(--bdr);border-radius:8px;overflow:hidden}
.coll-hdr{display:flex;align-items:center;justify-content:space-between;padding:10px 12px;cursor:pointer;user-select:none;transition:background .15s}
.coll-hdr:hover{background:rgba(255,255,255,.03)}
.coll-hdr .coll-title{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:rgba(255,255,255,.6)}
.coll-hdr .coll-chevron{font-size:10px;color:rgba(255,255,255,.4);transition:transform .2s}
.coll-section.collapsed .coll-chevron{transform:rotate(-90deg)}
.coll-body{padding:12px;display:flex;flex-direction:column;gap:8px;transition:max-height .25s ease,padding .25s ease,opacity .2s ease;overflow:hidden}
.coll-section.collapsed .coll-body{max-height:0;padding:0 12px;opacity:0;pointer-events:none}
.coll-section:not(.collapsed) .coll-body{max-height:2000px;opacity:1}
```

- [ ] **Step 2: Add collapsible JS functions**

In the `<script>` section of `operator.html`, add these functions after the `debounce` utility (around line 539):

```javascript
// ── Collapsible sections ──
let collapsedSections = [];

function toggleSection(id) {
  const el = document.getElementById('section-' + id);
  if (!el) return;
  const idx = collapsedSections.indexOf(id);
  if (idx >= 0) {
    collapsedSections.splice(idx, 1);
    el.classList.remove('collapsed');
  } else {
    collapsedSections.push(id);
    el.classList.add('collapsed');
  }
  saveCollapsedState();
}

function saveCollapsedState() {
  const fd = new FormData();
  fd.append('collapsed_sections', JSON.stringify(collapsedSections));
  fetch('/api/config', {method: 'POST', body: fd});
}

function restoreCollapsedState() {
  document.querySelectorAll('.coll-section').forEach(el => {
    const id = el.id.replace('section-', '');
    if (collapsedSections.includes(id)) {
      el.classList.add('collapsed');
    } else {
      el.classList.remove('collapsed');
    }
  });
}
```

- [ ] **Step 3: Load collapsed state from config**

In the `loadCfg()` function, after `cfg=c;` (around line 587), add:

```javascript
    collapsedSections = c.collapsed_sections || ['languages'];
```

And at the end of `loadCfg()`, after `applyPreviewStyle();` (around line 638), add:

```javascript
    restoreCollapsedState();
```

- [ ] **Step 4: Verify collapsible CSS renders correctly**

Open operator panel at `http://localhost:3001`. The CSS is loaded but not yet applied to any sections (that's Task 3). Verify no visual regressions from the added CSS.

- [ ] **Step 5: Commit**

```bash
git add operator.html
git commit -m "[feat] add collapsible section CSS and JS infrastructure to operator panel"
```

---

### Task 3: Operator Panel — HTML Restructure and Section Reordering

**Files:**
- Modify: `operator.html:297-451` (the entire `.ctrl` panel HTML)

This is the main restructure. The current HTML sections inside `<div class="ctrl" id="ctrlPanel">` get reordered and wrapped in collapsible containers. The new order:

1. Status bar (unchanged, not collapsible)
2. Warning (unchanged)
3. **Session** (collapsible, default expanded) — title, DeepL key, GO LIVE, Resume Translation, Save Transcripts, hint
4. **Language Slots** (collapsible, default collapsed) — input language, bi-directional, count, slots
5. **Audio Sources** (collapsible, expanded) — source list (existing #sourceList)
6. **Speakers** (collapsible, expanded) — speaker buttons, add, reset
7. **Clear Captions** — standalone button (not collapsible)
8. **Font Size / Lines / Mic** (collapsible, expanded) — three sliders
9. **Background & Text Styling** (collapsible, expanded) — background options, font options, caption color, translation colors, footer image controls

- [ ] **Step 1: Replace the ctrl panel HTML**

Replace everything inside `<div class="ctrl" id="ctrlPanel">` (from line 298 to line 451) with the restructured sections. The content inside each section stays the same — we're moving and wrapping, not rewriting.

The new HTML structure:

```html
<div class="ctrl" id="ctrlPanel">
  <div class="ptitle"><span class="dot" id="sDot"></span><span data-i18n="operator.panel_title">Operator —</span> <span id="sTxt" data-i18n="operator.status_connecting">Connecting...</span></div>
  <div class="warn" id="warn" style="display:none" data-i18n="operator.warn_no_api_key">No DeepL API key set. Translation disabled.</div>

  <!-- 1. Session (collapsible, default expanded) -->
  <div class="coll-section" id="section-session">
    <div class="coll-hdr" onclick="toggleSection('session')">
      <span class="coll-title" data-i18n="operator.session_label">Session</span>
      <span class="coll-chevron">&#9660;</span>
    </div>
    <div class="coll-body">
      <div class="row">
        <input type="text" id="iTitle" placeholder="Session title..." data-i18n="operator.session_title_placeholder" data-i18n-ph>
        <button class="btn btn-p" onclick="saveAll()" data-i18n="operator.save_btn">Save</button>
      </div>
      <div class="row">
        <input type="password" id="iKey" placeholder="DeepL API key" data-i18n="operator.deepl_key_placeholder" data-i18n-ph>
        <button class="btn btn-p" onclick="saveAll()" data-i18n="operator.save_btn">Save</button>
      </div>
      <span class="hint"><span data-i18n="operator.deepl_hint">Free key:</span> <a href="https://www.deepl.com/pro-api" target="_blank" style="color:var(--ac)" data-i18n="operator.deepl_link_text">deepl.com/pro-api</a></span>
      <button class="btn" id="liveBtn" onclick="toggleLive()" style="width:100%;padding:12px;font-size:14px;font-weight:800;background:#4CAF50;color:#fff;letter-spacing:.06em" data-i18n="operator.go_live">&#9654; GO LIVE — Start Captioning</button>
      <button class="btn" id="pauseBtn" onclick="togglePause()" style="width:100%;background:rgba(255,255,255,.08);color:rgba(255,255,255,.55);font-weight:800;border:1px solid rgba(255,255,255,.1)" data-i18n="operator.resume_translation_credits">&#9654; Resume Translation (uses API credits)</button>
      <div class="hint" id="sessionHint" data-i18n="operator.session_hint_both_paused">Both captioning and translation are paused. Configure your settings, then go live.</div>
      <div style="display:flex;align-items:center;justify-content:space-between;padding:2px 0">
        <div>
          <span style="font-size:11px;font-weight:700" data-i18n="operator.save_transcripts">Save Transcripts</span>
          <div class="hint" data-i18n="operator.save_transcripts_hint">One .txt file per language in /transcripts</div>
        </div>
        <label style="position:relative;display:inline-block;width:44px;height:24px;cursor:pointer">
          <input type="checkbox" id="iSaveTranscripts" checked onchange="toggleSaveTranscripts()" style="opacity:0;width:0;height:0">
          <span style="position:absolute;top:0;left:0;right:0;bottom:0;background:rgba(255,255,255,.15);border-radius:12px;transition:.3s"></span>
          <span id="saveToggleKnob" style="position:absolute;top:2px;left:22px;width:20px;height:20px;background:#4CAF50;border-radius:50%;transition:.3s;box-shadow:0 1px 3px rgba(0,0,0,.3)"></span>
        </label>
      </div>
    </div>
  </div>

  <!-- 2. Language Slots (collapsible, default collapsed) -->
  <div class="coll-section" id="section-languages">
    <div class="coll-hdr" onclick="toggleSection('languages')">
      <span class="coll-title" data-i18n="operator.languages_label">Languages</span>
      <span class="coll-chevron">&#9660;</span>
    </div>
    <div class="coll-body">
      <div>
        <span style="font-size:11px" data-i18n="operator.input_language">Input Language (what speakers speak)</span>
        <select id="iInputLang" onchange="onInputLangChange()"></select>
      </div>
      <div id="tunedRow" style="display:none;margin-top:4px">
        <div style="display:flex;align-items:center;gap:6px;padding:8px;border-radius:6px;background:rgba(255,255,255,.03);border:1px solid var(--bdr)">
          <div style="flex:1;min-width:0">
            <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:rgba(255,255,255,.55)" id="tunedLabel">Tuned Model</div>
            <div style="font-size:11px;margin-top:2px" id="tunedStatus">—</div>
            <div id="tunedProgressWrap" style="display:none;margin-top:4px">
              <div style="height:4px;border-radius:2px;background:rgba(255,255,255,.08);overflow:hidden">
                <div id="tunedProgressBar" style="height:100%;background:var(--ac);width:0%;transition:width .3s"></div>
              </div>
              <div style="font-size:9px;color:rgba(255,255,255,.45);margin-top:2px" id="tunedProgressMsg"></div>
            </div>
          </div>
          <button class="btn btn-g" id="tunedBtn" onclick="onTunedAction()" style="white-space:nowrap;font-size:10px" data-i18n="operator.tuned_btn_download">Download</button>
        </div>
      </div>
      <!-- Bi-directional Mode -->
      <div id="bidirSection" style="margin-bottom:4px;">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:12px;font-weight:600">
          <input type="checkbox" id="bidirToggle" onchange="toggleBidir()" style="accent-color:var(--ac);width:15px;height:15px;cursor:pointer">
          <span data-i18n="operator.bidirectional_mode">Bi-directional Mode</span>
        </label>
        <div id="bidirControls" style="display:none;margin-top:8px;">
          <div style="display:flex;gap:8px;margin-bottom:6px;align-items:center;">
            <select id="bidirLangA" onchange="onBidirLangChange()" style="flex:1;"></select>
            <button class="btn" onclick="swapBidirLangs()" title="Swap languages (Keyboard: X)" style="flex-shrink:0;padding:4px 10px;font-size:16px;min-width:0;line-height:1">&#8644;</button>
            <select id="bidirLangB" onchange="onBidirLangChange()" style="flex:1;"></select>
          </div>
          <label id="tunedSwapLabel" style="display:none;font-size:11px;color:rgba(255,255,255,.55);cursor:pointer;align-items:center;gap:6px">
            <input type="checkbox" id="tunedSwapToggle" onchange="onTunedSwapChange()" style="accent-color:var(--ac);cursor:pointer">
            <span data-i18n="operator.tuned_swap">Use tuned model when available (~1s swap delay)</span>
          </label>
          <div id="detectionIndicator" style="margin-top:6px;font-size:11px;color:rgba(255,255,255,.5)"></div>
          <button class="btn btn-g" id="bidirDisplayBtn" onclick="window.open(location.protocol+'//'+location.hostname+':3000/bidirectional?mode=split','_blank')" style="margin-top:8px;width:100%;font-size:11px">
            <span data-i18n="operator.bidirectional_display">Bi-directional Display</span>
          </button>
        </div>
      </div>
      <div>
        <span style="font-size:11px" data-i18n="operator.num_translations">Number of Translations</span>
        <select id="iTransCount" onchange="onTransCountChange()">
          <option value="0" data-i18n="operator.trans_count_0">0 — Captioning only</option>
          <option value="1" data-i18n="operator.trans_count_1">1 — Single translation</option>
          <option value="2" selected data-i18n="operator.trans_count_2">2 — Dual translation</option>
          <option value="3" data-i18n="operator.trans_count_3">3 — Dual + extended window (1)</option>
          <option value="4" data-i18n="operator.trans_count_4">4 — Dual + extended window (2)</option>
          <option value="5" data-i18n="operator.trans_count_5">5 — Dual + extended window (3)</option>
        </select>
      </div>
      <div id="transSlots"></div>
      <div id="extNote" class="hint" style="display:none" data-i18n="operator.ext_note">Translations 3+ appear on the Extended display (port 3002)</div>
    </div>
  </div>

  <!-- 3. Audio Sources (collapsible, expanded) -->
  <div class="coll-section" id="section-sources">
    <div class="coll-hdr" onclick="toggleSection('sources')">
      <span class="coll-title" data-i18n="operator.audio_sources_label">Audio Sources</span>
      <span class="coll-chevron">&#9660;</span>
    </div>
    <div class="coll-body">
      <div id="sourceList" class="source-list"></div>
    </div>
  </div>

  <!-- 4. Speakers (collapsible, expanded) -->
  <div class="coll-section" id="section-speakers">
    <div class="coll-hdr" onclick="toggleSection('speakers')">
      <span class="coll-title" data-i18n="operator.speakers_label">Speakers</span>
      <span class="coll-chevron">&#9660;</span>
    </div>
    <div class="coll-body">
      <span class="hint" data-i18n="operator.speakers_hint">Speakers (keys 1-9, 0=none, double-click to edit)</span>
      <div class="spgrid" id="spGrid"></div>
      <div class="row">
        <input type="text" id="iNewSp" placeholder="Add speaker..." data-i18n="operator.speaker_add_placeholder" data-i18n-ph onkeydown="if(event.key==='Enter')addSp()">
        <button class="btn btn-g" onclick="addSp()" data-i18n="operator.speaker_add_btn">Add</button>
      </div>
      <div id="resetContainer" style="margin-top:8px">
        <button class="btn" onclick="resetSpeakers()" data-i18n="operator.reset_speakers">Reset Speakers</button>
      </div>
    </div>
  </div>

  <!-- 5. Clear Captions (standalone, not collapsible) -->
  <button class="btn btn-d" onclick="clearCaptions()" style="width:100%" data-i18n="operator.clear_captions">Clear Captions (C)</button>

  <!-- 6. Font Size / Line Visibility / Mic Sensitivity (collapsible, expanded) -->
  <div class="coll-section" id="section-display">
    <div class="coll-hdr" onclick="toggleSection('display')">
      <span class="coll-title">Display &amp; Sensitivity</span>
      <span class="coll-chevron">&#9660;</span>
    </div>
    <div class="coll-body">
      <span class="clbl"><span data-i18n="operator.font_size_label">Font Size:</span> <span class="vd" id="vFs">42px</span></span>
      <input type="range" id="iFs" min="24" max="960" value="42" oninput="onFsChange(this.value)">
      <div style="display:flex;justify-content:space-between" class="hint"><span>24px</span><span>960px</span></div>

      <span class="clbl"><span data-i18n="operator.visible_lines_label">Visible Lines:</span> <span class="vd" id="vLn">3</span></span>
      <input type="range" id="iLn" min="1" max="8" value="3" oninput="onLnChange(this.value)">
      <div style="display:flex;justify-content:space-between" class="hint"><span>1</span><span>8</span></div>

      <span class="clbl" data-i18n="operator.mic_sensitivity_label">Mic Sensitivity</span>
      <input type="range" id="iMic" min="1" max="50" value="8" oninput="onMicChange(this.value)">
      <div style="display:flex;justify-content:space-between" class="hint"><span data-i18n="operator.mic_more_sensitive">More sensitive</span><span data-i18n="operator.mic_less_sensitive">Less sensitive</span></div>
    </div>
  </div>

  <!-- 7. Background & Text Styling (collapsible, expanded) — COMBINED -->
  <div class="coll-section" id="section-styling">
    <div class="coll-hdr" onclick="toggleSection('styling')">
      <span class="coll-title">Background &amp; Text Styling</span>
      <span class="coll-chevron">&#9660;</span>
    </div>
    <div class="coll-body">
      <span class="clbl" data-i18n="operator.background_label">Background</span>
      <div class="bg-opts" id="bgOpts"></div>
      <div style="height:8px"></div>

      <span class="clbl" data-i18n="operator.font_label">Font</span>
      <div class="font-opts" id="fontOpts"></div>
      <div style="height:8px"></div>

      <span class="clbl" data-i18n="operator.caption_text_color_label">Caption Text Color</span>
      <div class="swatch-row" id="captionColors"></div>
      <div id="transColors"></div>
      <div style="height:8px"></div>

      <!-- Footer Image -->
      <span class="clbl" data-i18n="operator.footer_image_label">Footer Image (optional)</span>
      <input type="file" id="iFooter" accept="image/*" onchange="uploadFooter(this)" style="font-size:11px">
      <div id="footerPrev" style="display:none">
        <img id="footerPrevImg" style="max-height:40px;border-radius:3px">
        <button class="btn btn-g" onclick="rmFooter()" style="margin-top:4px;font-size:10px" data-i18n="operator.footer_remove">Remove</button>
      </div>

      <!-- Footer Position -->
      <div id="footerPosControls" style="display:none;margin-top:4px">
        <span class="clbl">Footer Position</span>
        <div style="display:flex;gap:4px;margin:4px 0">
          <button class="btn btn-g" onclick="setFooterPos(0)" style="flex:1;font-size:9px;padding:5px 2px">Left</button>
          <button class="btn btn-g" onclick="setFooterPos(25)" style="flex:1;font-size:9px;padding:5px 2px">C-Left</button>
          <button class="btn btn-g" onclick="setFooterPos(50)" style="flex:1;font-size:9px;padding:5px 2px">Center</button>
          <button class="btn btn-g" onclick="setFooterPos(75)" style="flex:1;font-size:9px;padding:5px 2px">C-Right</button>
          <button class="btn btn-g" onclick="setFooterPos(100)" style="flex:1;font-size:9px;padding:5px 2px">Right</button>
        </div>
        <input type="range" id="iFooterPos" min="0" max="100" value="50" oninput="setFooterPos(parseInt(this.value))">
      </div>
    </div>
  </div>

  <div class="badge" id="badge">—</div>
</div>
```

Key changes from current HTML:
- **Session**: Title/DeepL key inputs + GO LIVE + Resume Translation + Save Transcripts all in one collapsible section. The old separate "Session Controls" card and "Save Transcripts" card are removed.
- **Language Slots**: Moved to position 2 (was position 4). Now collapsible, defaults collapsed.
- **Audio Sources**: Split out from the old Speakers+Sources card into its own section.
- **Speakers**: Split out into its own section, moved below Audio Sources.
- **Clear Captions**: Standalone button between Speakers and Display (was elsewhere).
- **Font Size/Lines/Mic**: Combined into one section called "Display & Sensitivity", moved above styling (was below).
- **Background & Text Styling**: Two separate cards (bg/font and colors) merged into one. Footer image controls and new position controls included.
- Old standalone footer card removed.

- [ ] **Step 2: Show/hide footer position controls based on footer state**

In the `loadCfg()` function, after the footer image check (around the block that sets `footerPrev` display), add logic to show footer position controls:

```javascript
    if(c.footer_image){
      document.getElementById('footerPrevImg').src='/uploads/'+c.footer_image;
      document.getElementById('footerPrev').style.display='block';
      document.getElementById('pFooterImg').src='/uploads/'+c.footer_image;
      document.getElementById('pFooter').style.display='block';
      document.getElementById('footerPosControls').style.display='block';
    } else {
      document.getElementById('footerPosControls').style.display='none';
    }
    if(c.footer_position !== undefined){
      document.getElementById('iFooterPos').value = c.footer_position;
    }
```

- [ ] **Step 3: Add footer position JS functions**

Add these functions near the existing `uploadFooter`/`rmFooter` functions:

```javascript
function setFooterPos(pct) {
  document.getElementById('iFooterPos').value = pct;
  const fd = new FormData();
  fd.append('footer_position', pct);
  fetch('/api/config', {method: 'POST', body: fd}).then(r => r.json());
}
```

Also update `uploadFooter()` to show position controls after upload:

```javascript
function uploadFooter(inp){
  if(!inp.files[0])return;
  const fd=new FormData();fd.append('file',inp.files[0]);
  fetch('/api/upload-footer',{method:'POST',body:fd}).then(r=>r.json()).then(r=>{
    if(r.filename){
      const u='/uploads/'+r.filename+'?t='+Date.now();
      document.getElementById('footerPrevImg').src=u;document.getElementById('footerPrev').style.display='block';
      document.getElementById('pFooterImg').src=u;document.getElementById('pFooter').style.display='block';
      document.getElementById('footerPosControls').style.display='block';
    }
  });
}
function rmFooter(){
  fetch('/api/remove-footer',{method:'POST'}).then(()=>{
    document.getElementById('footerPrev').style.display='none';document.getElementById('pFooter').style.display='none';
    document.getElementById('footerPosControls').style.display='none';
  });
}
```

- [ ] **Step 4: Test the restructured layout**

Start the server and open `http://localhost:3001`. Verify:
- All 7 sections appear in correct order
- Collapsing/expanding works for each section
- Language Slots starts collapsed, all others start expanded
- GO LIVE and Resume Translation buttons are in the Session section
- Background & Text Styling shows bg options, font options, caption colors, and translation colors in one panel
- Footer image upload still works
- Footer position controls appear after uploading an image
- Clear Captions button is between Speakers and Display & Sensitivity
- All keyboard shortcuts still work (L, P, C, 0-9, X)
- Saving config still works

- [ ] **Step 5: Commit**

```bash
git add operator.html
git commit -m "[feat] restructure operator panel — reorder sections, add collapsible containers, combine styling panels"
```

---

### Task 4: GO LIVE / Resume Translation Button Sync

**Files:**
- Modify: `operator.html` (JS section)

This task adds 5-second polling of `GET /api/status` so the GO LIVE and Resume Translation buttons always reflect the server's actual state, fixing the sync issue where multiple operator panels or page refreshes show incorrect button states.

- [ ] **Step 1: Add status polling**

In the `<script>` section of `operator.html`, after the `connect()` call (around line 1993), add:

```javascript
// ── Button sync: poll server status every 5s ──
function pollStatus() {
  fetch('/api/status').then(r => r.json()).then(s => {
    if (s.captioning_paused !== undefined && s.captioning_paused !== captioningPaused) {
      captioningPaused = s.captioning_paused;
      const btn = document.getElementById('liveBtn');
      if (captioningPaused) {
        btn.textContent = t('operator.go_live');
        btn.style.background = '#4CAF50';
        btn.style.color = '#fff';
      } else {
        btn.textContent = t('operator.pause_captioning');
        btn.style.background = '#f44336';
        btn.style.color = '#fff';
      }
    }
    if (s.translation_paused !== undefined && s.translation_paused !== translationPaused) {
      translationPaused = s.translation_paused;
      const btn = document.getElementById('pauseBtn');
      if (translationPaused) {
        const allOffline = translations.slice(0, transCount).every(tr => (tr.mode || 'deepl').startsWith('offline'));
        btn.textContent = allOffline ? t('operator.resume_translation_offline') : t('operator.resume_translation_credits');
        btn.style.background = 'rgba(255,255,255,.08)';
        btn.style.color = 'rgba(255,255,255,.55)';
        btn.style.borderColor = 'rgba(255,255,255,.1)';
      } else {
        btn.textContent = t('operator.pause_translation');
        btn.style.background = '#FF9800';
        btn.style.color = '#000';
        btn.style.borderColor = '#FF9800';
      }
    }
    updateSessionHint();
  }).catch(() => {});
}

setInterval(pollStatus, 5000);
pollStatus();
```

- [ ] **Step 2: Update toggleLive/togglePause to not visually toggle immediately**

Modify `toggleLive()` so it sends the toggle command but doesn't update the button until the next status poll (or WebSocket message) confirms:

```javascript
function toggleLive(){
  if(ws&&ws.readyState===1) ws.send(JSON.stringify({type:'set_captioning_paused', paused:!captioningPaused}));
}

function togglePause(){
  if(ws&&ws.readyState===1) ws.send(JSON.stringify({type:'set_translation_paused', paused:!translationPaused}));
}
```

The visual update now comes from either:
1. The WebSocket `captioning_paused` / `translation_paused` messages (immediate, same as current)
2. The 5-second status poll (catches cases where WebSocket missed the update)

This means the existing WebSocket handlers for `captioning_paused` and `translation_paused` messages (already in the `ws.onmessage` handler around lines 1906-1924) continue to work as the primary update path. The poll is a fallback for multi-panel sync.

- [ ] **Step 3: Test button sync**

1. Open two operator panel tabs
2. Click GO LIVE in tab 1
3. Verify tab 2's button updates within 5 seconds to show "Pause Captioning"
4. Click Resume Translation in tab 2
5. Verify tab 1's button updates within 5 seconds to show "Pause Translation"
6. Refresh a tab — verify buttons reflect actual server state

- [ ] **Step 4: Commit**

```bash
git add operator.html
git commit -m "[fix] sync GO LIVE / Resume Translation buttons across panels via status polling"
```

---

### Task 5: Display Grid — Footer Image Dedicated Row with Positioning

**Files:**
- Modify: `display.html:51-53` (CSS for footer)
- Modify: `display.html:59-66` (HTML structure)
- Modify: `display.html:449-465` (applyStyle function)

This task moves the footer image from a simple centered banner to a dedicated row with CSS positioning controlled by the `footer_position` config value.

- [ ] **Step 1: Update footer CSS in display.html**

Replace the existing `.footer-banner` CSS (lines 51-53):

```css
.footer-banner{flex-shrink:0;padding:.6vh 0 0;display:none;position:relative;height:6vh;min-height:30px}
.footer-banner.vis{display:flex;align-items:center}
.footer-banner img{height:auto;max-height:100%;max-width:min(95%,1000px);object-fit:contain;border-radius:5px;position:absolute;transform:translateX(-50%);transition:left .3s ease}
```

The image uses `position:absolute` with `left` set as a percentage, and `transform:translateX(-50%)` to center the image on that point. This gives smooth positioning from 0% to 100%.

- [ ] **Step 2: Update applyStyle to handle footer_position**

In the `applyStyle(c)` function, replace the footer handling block (around line 460):

```javascript
  const fw = document.getElementById('footerWrap'), fi = document.getElementById('footerImg');
  if (c.footer_image) {
    fi.src = '/uploads/' + c.footer_image + '?t=' + Date.now();
    fw.classList.add('vis');
  } else {
    fw.classList.remove('vis');
  }
  if (c.footer_position !== undefined) {
    const pos = Math.max(0, Math.min(100, c.footer_position));
    fi.style.left = pos + '%';
  }
```

- [ ] **Step 3: Handle footer_position in WebSocket config_update messages**

The existing `config_update` handler in `handleMsg()` already calls `applyStyle(msg)`, so any `footer_position` in the broadcast will be applied automatically. No additional code needed — but verify that the server includes `footer_position` in the broadcast.

Check: In `server.py`, the `o_save_config()` handler broadcasts `**_style_config()` which now includes `footer_position` (from Task 1 Step 3). The `o_upload_footer()` and `o_rm_footer()` handlers also broadcast `**_style_config()`. So footer position is included in all relevant broadcasts.

- [ ] **Step 4: Test footer positioning**

1. Upload a footer image via operator panel
2. Verify image appears centered (50%) at bottom of display
3. Click "Left" preset — image moves to left edge
4. Click "Right" preset — image moves to right edge
5. Use slider for intermediate positions
6. Refresh display page — position persists from config

- [ ] **Step 5: Commit**

```bash
git add display.html
git commit -m "[feat] move footer image to dedicated display row with configurable positioning"
```

---

### Task 6: End-to-End Verification

**Files:** None (verification only)

- [ ] **Step 1: Start the server**

```bash
python server.py
```

Open operator panel at `http://localhost:3001` and display at `http://localhost:3000`.

- [ ] **Step 2: Verify section order**

In the operator panel's left sidebar, confirm sections appear in this order:
1. Status bar (Operator — Connected)
2. Session (collapsible, expanded) — title, DeepL key, GO LIVE, Resume Translation, Save Transcripts
3. Language Slots (collapsible, **collapsed** by default)
4. Audio Sources (collapsible, expanded) — source list
5. Speakers (collapsible, expanded) — speaker buttons
6. Clear Captions (standalone button)
7. Display & Sensitivity (collapsible, expanded) — font size, lines, mic
8. Background & Text Styling (collapsible, expanded) — bg, font, caption color, translation colors, footer image + position

- [ ] **Step 3: Verify collapsible behavior**

- Click each section header — it should collapse/expand
- Collapse "Session" and "Speakers", refresh the page — they should stay collapsed
- Expand them, refresh — they should stay expanded
- Language Slots should start collapsed on first load

- [ ] **Step 4: Verify combined styling panel**

In "Background & Text Styling":
- Background options (4 color boxes) visible
- Font options (5 font choices) visible
- Caption Text Color (12 swatches) visible
- Translation Color swatches visible (one per active slot)
- Footer image upload control at bottom
- After uploading: footer preview + position controls appear

- [ ] **Step 5: Verify button sync**

- Open two operator panel tabs
- GO LIVE in tab 1 → tab 2 shows "Pause Captioning" within 5s
- Resume Translation in tab 2 → tab 1 shows "Pause Translation" within 5s
- Refresh either tab → buttons reflect server state

- [ ] **Step 6: Verify footer positioning on display**

- Upload footer image in operator panel
- On display page: image appears at bottom in a dedicated row
- Use preset buttons: Left/Center-Left/Center/Center-Right/Right
- Display updates position in real-time
- Use slider for fine position
- Refresh display — position persists

- [ ] **Step 7: Verify keyboard shortcuts**

- L = toggle captioning
- P = toggle translation
- C = clear captions
- 1-9 = select speaker
- 0 = no speaker
- X = swap bidir languages (when enabled)

- [ ] **Step 8: Commit any fixes**

If any issues found, fix and commit:
```bash
git add -A
git commit -m "[fix] operator panel redesign polish — address verification findings"
```
