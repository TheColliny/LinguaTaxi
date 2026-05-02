# Operator Panel Redesign — Design Spec

**Goal:** Reorganize the operator panel layout for better workflow, combine related styling panels, fix button sync issues, and move the footer image to a dedicated display grid row with positioning controls.

**Status:** Approved design

---

## Overview

The operator panel (`operator.html`) gets a layout reorganization: sections are reordered for workflow priority, related panels are combined, all major sections become collapsible, and the GO LIVE / Resume Translation buttons are fixed to stay in sync with actual server state.

The footer/sponsor image is moved out of the operator side panel and into the display grid as its own bottom row with draggable positioning.

---

## Operator Panel Layout

New section order (top to bottom):

| # | Section | Collapsible? | Default State | Contents |
|---|---|---|---|---|
| 1 | Session | Yes | **Expanded** | Session title, DeepL API key, GO LIVE button, Resume Translation button |
| 2 | Language Slots | Yes | Collapsed | Up to 5 DeepL translation slots, language selection |
| 3 | Audio Sources | Yes | Expanded | Active sources list, add device/app source controls, refresh button |
| 4 | Speakers | Yes | Expanded | Speaker buttons, keyboard shortcuts (1-9, 0 to clear), color assignment |
| 5 | Clear Captions | No | — | Standalone button, not inside a collapsible panel |
| 6 | Font Size / Line Visibility / Mic Sensitivity | Yes | Expanded | Font size slider, max lines, mic sensitivity threshold |
| 7 | Background & Text Styling | Yes | Expanded | **Combined** — background selection (4 options), font selection (5 options), caption text color (12 options), translation text color (12 options) |

### Collapsible Section Behavior

- Each collapsible section has a header bar with the section title and a collapse/expand toggle (chevron or +/- icon).
- Clicking the header bar toggles the section.
- Collapsed state is saved to `config.json` so it persists across page reloads.
- The "Session" section defaults to expanded on first load but respects saved state after that.

### What Changed From Current Layout

- **Session:** Now collapsible. GO LIVE and Resume Translation buttons stay inside this section.
- **Language Slots:** Moved from below styling to position 2 (right after Session). Now collapsible, defaults collapsed.
- **Audio Sources:** NEW section (created by the app audio capture spec, positioned here).
- **Speakers:** Split out from audio — was grouped with mic selection, now its own section.
- **Clear Captions:** Moved to between Speakers and Font Size (was elsewhere).
- **Font Size / Line Visibility / Mic Sensitivity:** Moved up from below styling panels.
- **Background & Text Styling:** Two panels (background/font + caption/translation colors) combined into one collapsible section.

---

## GO LIVE / Resume Translation Button Sync Fix

**Problem:** The buttons display incorrect state — showing "off" with option to turn on even when captioning/translation is already running. This happens because the buttons toggle local state without confirming server state.

**Fix:** The operator panel polls `GET /api/status` (already exists or add if needed) on connection and periodically (every 5 seconds, same interval as source polling). The response includes:

```json
{
  "captioning_paused": false,
  "translation_paused": true
}
```

On each poll, the button labels and styles are updated to match the server's actual state. Button clicks still send the toggle command, but the button doesn't visually toggle until the next status poll confirms the change took effect.

This also fixes the case where one operator panel starts captioning and another operator panel's button still shows "Start."

---

## Footer Image — Display Grid Bottom Row

**Current:** Footer/sponsor image is inside the operator panel's side panel.

**New:** The image moves to the **display output** (display.html / extended display) as its own row at the bottom of the caption grid.

### Display Grid Structure

```
┌─────────────────────────────────┐
│        Caption Lines            │
│     (scrolling, as today)       │
├─────────────────────────────────┤
│  [footer image]                 │  ← new dedicated row
└─────────────────────────────────┘
```

### Positioning

The footer image position is controlled from the operator panel:

**Preset positions** (buttons):
- Left
- Center-Left
- Center
- Center-Right
- Right

**Draggable:** The image can also be dragged/slid to any position along the row for fine-tuned placement. The drag position is stored as a percentage (0% = left edge, 100% = right edge).

Preset buttons set the percentage: Left=0%, Center-Left=25%, Center=50%, Center-Right=75%, Right=100%.

**Implementation:**
- The footer row uses CSS flexbox with `justify-content` controlled by the position value.
- For drag positioning, a CSS `margin-left: X%` approach or `transform: translateX()` allows smooth arbitrary placement.
- Position is saved to `config.json` and broadcast to display clients via WebSocket (same pattern as other style changes).
- The operator panel shows a small position control: 5 preset buttons + a slider for fine adjustment.

### Footer Image Selection

Stays in the operator panel as a control within the "Background & Text Styling" section (or a small sub-section). The operator selects/uploads the image; it renders on the display grid.

---

## Launcher Sync

The launcher's source management panel (added by the app audio capture spec) stays in sync with the operator panel via the same server API polling. No special coordination needed — both are API clients of the same server.

The launcher does NOT need the operator panel's styling controls, footer image, or collapsible sections. Those are operator-panel-only features.
