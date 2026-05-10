# Offline Translation Performance — Design Spec

## Problem

M2M-100 1.2B running on CPU via CTranslate2 cannot keep up with real-time translation. A single EN→ES slot on an AMD 5900HX (8-core) falls ~15 minutes behind during a 25-minute session. The model takes 2-3+ seconds per translation, while interim text arrives every 1.5s — creating unbounded queue growth.

## Root Causes

1. **M2M-100 1.2B is too large for real-time CPU inference** — 1.2 billion parameters at int8 (~1.2GB weights) overwhelms CPU cache on every call.
2. **CTranslate2 threading not configured** — default `inter_threads=1`, conservative `intra_threads`. Model likely underutilizes available cores.
3. **No beam_size tuning** — defaults to `beam_size=2`, doing double the decoding work for marginal quality gain in live captions.
4. **Stale interim translations pile up** — ThreadPoolExecutor queues every interim translation with no staleness check. Old translations execute long after their text has been superseded.
5. **OPUS-MT not promoted for supported pairs** — OPUS-MT handles EN→ES at 35-50ms/sentence but users may not realize it's available or download it.

## Design: Tiered Engine Strategy with Dynamic Threading

### 1. Dynamic CPU Core Allocation

**Config key:** `translate_cores` in `config.json`

**Default calculation** (in `offline_translate.py`):
```
default_cores = max(1, os.cpu_count() // 4)
```

**Constraints:**
- Minimum: 1
- Maximum: `os.cpu_count() - 1`
- Stored in config, persisted across restarts

**CTranslate2 Translator init changes:**
```python
cores = config.get("translate_cores", default_cores)
translator = ctranslate2.Translator(
    model_path,
    device="cpu",
    compute_type="int8",
    inter_threads=1,
    intra_threads=cores,
)
```

We use `intra_threads` (cores within a single translation) rather than `inter_threads` (concurrent translations) because:
- With a single translation slot, `inter_threads=2` provides no benefit — there's only one request at a time
- With multiple slots, each slot is already dispatched to a separate thread pool worker, so they naturally parallelize
- `intra_threads` directly controls how many cores each translation uses, which is what the user wants to tune

**When config changes at runtime:** The loaded model cache (`_loaded_models`) must be cleared so the next translation call re-creates the Translator with the new thread count. Add a function `reload_models()` that clears the cache.

### 2. Operator Panel UI — Core Count Setting

**Location:** Below the existing offline model status area in each translation slot section, or as a global setting in a new "Offline Translation" settings area.

Recommend: **Global setting** (not per-slot) since CTranslate2 thread counts apply to the model instance, which is shared across all slots.

**UI elements:**
- Label: "CPU Cores for Translation"
- Input: Number spinner, min=1, max=system_cores-1, default=system_cores/4
- Info button (i icon): tooltip/popover with text:
  > "How many CPU cores to use for translation. Default is {N}. Increase if translation is lagging behind real-time. Decrease if translation is causing system lag."
  
  Where `{N}` is the calculated default for this machine.

**API:**
- `GET /api/status` already returns config — add `translate_cores` to config and `system_cpu_count` to the status response so the UI knows the max and default
- `POST /api/config` already saves config — `translate_cores` saves like any other config key
- `POST /api/offline-translate/reload` — new endpoint that clears model cache so new thread count takes effect without restarting

### 3. CTranslate2 Beam Size Optimization

Change both `_translate_opus` and `_translate_m2m` to use `beam_size=1`:

```python
results = translator.translate_batch([tokens], beam_size=1)
```

For live captions, greedy decoding is sufficient. The quality difference between beam=1 and beam=2 is negligible for the sentence lengths seen in live speech, and it roughly halves decoding time.

### 4. Stale Interim Translation Dropping

Add a generation counter per translation slot. Each time `_translate_all` is called for an interim, it increments the counter. The translation worker checks if its generation is still current before executing.

```python
_translate_gen = {}  # {slot_index: generation_counter}
_translate_gen_lock = threading.Lock()
```

In `_translate_all`:
```python
if msg_type == "interim_translation":
    with _translate_gen_lock:
        gen = _translate_gen.get(i, 0) + 1
        _translate_gen[i] = gen
    _translate_pool.submit(_do_translate, text, ..., generation=gen)
```

In `_do_translate`:
```python
if generation is not None:
    with _translate_gen_lock:
        if _translate_gen.get(slot, 0) != generation:
            return  # Stale — newer text already queued
```

**Final translations are never skipped** — only interims get the staleness check. This eliminates the unbounded queue growth that caused the 15-minute backlog.

### 5. Smaller M2M Fallback Model (Future Enhancement)

Not in initial implementation, but noted for future:
- `facebook/m2m100_418M` — 3x smaller, ~3x faster, covers same 100 languages
- `facebook/nllb-200-distilled-600M` — successor to M2M, better quality at similar speed
- Would require a new download option in the UI and a model selection preference

This is deferred because the combination of OPUS-MT for supported pairs + beam_size=1 + proper threading + stale dropping should solve real-time for most use cases. The 1.2B model with these optimizations may be sufficient for the languages that need it.

## Files Changed

| File | Change |
|------|--------|
| `offline_translate.py` | Add `beam_size=1` to both translate functions. Add `intra_threads` parameter to model loading. Add `reload_models()` function. Add `get_default_cores()` helper. |
| `server.py` | Add `translate_cores` to DEFAULT_CONFIG. Pass core count to offline_translate model loading. Add generation counter for stale interim dropping. Add `/api/offline-translate/reload` endpoint. Add `system_cpu_count` to status response. |
| `operator.html` | Add CPU cores spinner with info button to translation settings area. Wire to config save + model reload. |

## Not Changing

- **OPUS-MT auto-preference logic** — already exists in `offline_translate.py` (`M2M_PREFERRED` set). No changes needed; OPUS is already preferred for European pairs when downloaded.
- **Thread pool size** — keeping `max_workers=10` is fine. The bottleneck is model inference time, not pool size.
- **Translation budget / skip system** — the stale interim dropping achieves the same goal more simply. If a translation would arrive after the next interim, the generation check catches it.

## Testing

- Verify core count persists across restart
- Verify changing core count mid-session takes effect after reload
- Verify stale interims are dropped (check logs for skipped translations)
- Verify final translations are never skipped
- Benchmark: M2M-100 translation time with beam_size=1 vs default on target hardware
- Benchmark: M2M-100 with 1 vs 2 vs 4 intra_threads on target hardware
