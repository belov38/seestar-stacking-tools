---
description: Run a Seestar S30 frame through the full processing pipeline (explore → frame quality gate → stack → background → deconv → denoise → plate-solve → SPCC colour calibration → HOO palette → stretch), auto-picking parameters by measurement, stopping only when a choice is doubtful, emitting AstroBin title/description + acquisition CSV, and offering an optional cleanup of intermediate files at the end.
argument-hint: <lights-dir | stack.fits>
---

# Seestar pipeline orchestrator

You are running the **full Seestar S30 post-processing pipeline** end to end as an agent
orchestrator. Drive the existing per-step skills, measure each result, generate previews, and
**advance automatically on a clear verdict — stop and ask the user only when the result is
doubtful** (the table below says exactly when).

Input path: `$1` (a directory of raw lights, or a single stacked FITS).

## Fixed facts (don't re-derive)

- Python: `.venv/bin/python` (python3.13; astropy, numpy, sep, scipy, Pillow, onnxruntime).
- Siril CLI: `siril-cli` if on PATH (Homebrew install), else
  `/Applications/Siril.app/Contents/MacOS/siril-cli` (manual app install).
- Background + denoise run on the **Apple-Silicon GPU** via `tools/gpu/` (CoreML, no GraXpert
  install). One-time: `.venv/bin/python tools/gpu/fetch_models.py`.
  The skill runners `background.py` / `denoise.py` wrap it.
- AstroBin session CSV: `tools/astrobin_session_csv.py` (scans lights, emits the import CSV).
- Sub quality scorer: `tools/score_subs.py` (per-frame bg / star count / FWHM / roundness →
  CLOUD/HAZY/SOFT/TRAILED classes; `--move CLASSES --aside-dir DIR` quarantines).
- Processing order is **physically fixed**: stack → background extraction → deconvolution →
  denoise → plate-solve → **SPCC colour calibration** → stretch. Deconv and denoise run on
  **linear** data; denoise comes after deconv; plate-solve runs on the final linear master; SPCC
  runs right after plate-solve (it needs the WCS to match the star catalog **and** a flat
  background — both already true by then) and **before** stretch (SPCC is a linear operation).
  (Steps 1–3 — explore, run dir, frame quality gate — come first; they don't touch pixels,
  only inspect and quarantine whole frames.)
- Pass an **absolute** output path to the runners to avoid any cwd ambiguity.
- Each processing step is a skill with a `measure_*.py` that prints a verdict — trust the numbers,
  but the **stop rules below override blind adoption** (FWHM once fooled us into adopting deconv
  donuts).

## Universal rule — every step leaves a FITS *and* a PNG

After **every** processing step (Steps 4–11), whether it auto-adopts or stops, write the adopted
result as **both** a header-preserved `.fit` in the step's output dir **and** a preview `.png` in
`previews/`. Do this even when no user input is needed. Then print a one-line
`validate here: <abs .fit>  |  <abs .png>` so the user can open any intermediate in Siril and **pick
up the pipeline manually from any stage**. Carry the **adopted FITS forward** as the next step's
input. After every step append to `REPORT.md`: variants tried, measured numbers, what was adopted,
and **why** (or what the user chose at a stop).

## Step 1 — Explore & quarantine (always, before anything is created)

This step only inspects and tidies the input — it creates no run dir and touches no pixels.

1. **Deps:** `.venv/bin/python -c "import astropy,numpy,sep,scipy,PIL"`. If it fails, tell the
   user to run `.venv/bin/python -m pip install sep scipy pillow` and stop.
2. **Explore the path `$1` the user pointed at** and report how their data is organized — run a
   shallow tree and inspect:
   - the layout: are the `.fit` directly in `$1`, in a `lights/` subdir, or is `$1` a single FITS?
   - the `.fit` count, and any `.jpg` count (Seestar thumbnails);
   - whether a Siril `process/r_pp_light_.seq` exists (→ reuse stacking) or not (→ full stack);
   - a representative FITS header (does it carry OBJECT/RA/DEC, or is it header-stripped?).
   Post a short **"here's how your data is organized"** summary before doing anything else.
3. **Quarantine `.jpg`:** Seestar drops `.jpg` thumbnails that break Siril `link` — **move** (don't
   delete — reversible) any `.jpg` next to the lights into `<lights>/_jpg_aside/` and report the
   count.
4. **Resolve `$1` to the Siril `lights/` convention.** The stacking `.ssf` scripts hardcode
   `cd lights`, so any other layout silently breaks at `link` — enforce it:
   - `$1` is (or ends in) **`lights/`** containing `.fit` → `LIGHTS=$1`, `DATADIR=dirname($1)`.
   - `$1` is a **dir containing a `lights/` subdir** → `DATADIR=$1`, `LIGHTS=$1/lights`.
   - `$1` is a **dir of `.fit` not named `lights`** (no `lights/` subdir) → **error clearly**:
     ask the user to move the subs into `<dir>/lights/`. Do **not** guess or auto-rename.
   - `$1` is a **single FITS** → ready-stack mode (skip Steps 3–4; this file is the Step-5 base),
     `DATADIR=dirname($1)`. Warn if the header is stripped (no OBJECT/RA/DEC). (No `lights/` needed.)
   Then **validate**: `LIGHTS/` exists and holds ≥1 `.fit` (else error out).
5. **Read `OBJECT`** from a representative FITS (astropy) for naming; fall back to the basename.

## Step 2 — Make the run dir (next to the user's data)

Create the branded run folder **inside `DATADIR`** (next to the user's data, not in the repo), so
all intermediates and deliverables sit with the source:

```
STAMP=$(date +%Y-%m-%d-%H%M)        # local time — human-facing brand label
RUN="$DATADIR/belov38-<OBJECT>-$STAMP"
mkdir -p "$RUN"/{01_stack,02_background,03_deconv,04_denoise,05_stretch,previews}
```

(The `NN_*` subdir numbers track the five **processing** stages, independent of the Step numbers in
this doc.) Create `REPORT.md` with a header (input path, mode, object, local + UTC time). Announce
the plan and the absolute run dir to the user, then proceed.

## Step 3 — Frame quality gate (clouds, haze, focus, trails) — stacking mode only

Registration does **not** catch this: on the NGC 292 run Siril registered 920/932 frames while 87
of them were shot through clouds (background +7σ, star count −6σ) — they went into the stack and
diluted SNR the whole way down the pipeline. Score every sub **before** stacking (skip this step
in ready-stack mode):

```
.venv/bin/python tools/score_subs.py <LIGHTS> --out "$RUN/sub_scores.csv"
```

The scorer measures per frame — background level, star count, FWHM, roundness — on a 2×2-binned
luminance (~0.5 s/frame), groups frames **by exposure** (a 60 s sub has ~2× the sky of a 30 s sub;
mixing groups falsely flags every long sub), and classifies with robust median/MAD thresholds
per group:

- **CLOUD** — bg > +3σ **and** nstars < −3σ: shot through clouds, ~zero signal → drop.
- **HAZY** — bg > +3σ, star count normal: thin haze; normalization mostly compensates → usually keep.
- **SOFT** — FWHM > +3σ, or nstars < −3σ with normal bg (gross defocus): usually keep the
  mild-seeing kind; drop true defocus (star count collapsed / FWHM +50%+).
- **TRAILED** — roundness < −3σ (and < 0.8): wind / tracking error → drop if visibly elongated.

**Always STOP and ask** — this step is never auto (dropping frames costs integration the user paid
for). Present the per-class counts **with integration minutes lost** per option, note the sanity
cross-check (Seestar's own on-device stack count, e.g. `Stacked_552_...` vs subs captured, roughly
predicts how many frames are bad), give your recommendation (typically: drop CLOUD + TRAILED,
keep HAZY + SOFT), and offer: **drop CLOUD only / drop recommended set / drop all flagged /
keep everything**. On the answer, quarantine — move, never delete:

```
.venv/bin/python tools/score_subs.py <LIGHTS> --move CLOUD,TRAILED --aside-dir <DATADIR>/_clouds_aside
```

Log the decision, per-class counts, and the new frame count / integration total to `REPORT.md`.
The AstroBin CSV (Finish step) scans `lights/` and so picks up the reduced set automatically.

## The processing steps (Steps 4–11)

For each step: invoke the named skill (to load its current how-to), run its sweep with the
binaries above, run its `measure_*.py`, generate the preview(s), apply the stop rule, then honor
the **universal rule** (FITS + PNG + `validate here:`).

| Step | Skill to invoke | Output dir | AUTO-adopt when | STOP & ask when |
|---|---|---|---|---|
| 4 | `seestar-stacking-compare` *(dir input only)* | `01_stack/` | verdict `KEEP BASELINE`, **or** a tuned win ≥3% faint-SNR that is **not** from star weighting | the winner is a `nbstars`/`wfwhm` (star-weighting) variant — volatile, confirm before adopting |
| 5 | `seestar-background-extraction-compare` | `02_background/` | GraXpert AI: colour cast < ~1% and **not** `BACKFIRED` | cast not pulled under ~1%, or every method backfired |
| 6 | `seestar-deconvolution-compare` | `03_deconv/` | ring depth comfortably above the floor **AND** a clear FWHM gain | **default to STOP** on any doubt — borderline ring depth, marginal FWHM, or visible rings in the preview |
| 7 | `seestar-denoise-compare` | `04_denoise/` | strongest setting with FWHM Δ < ~3% **and** faint_keep > ~0.85 | even the lowest strength over-blurs → propose **skip denoise** |
| 8 | *(plate-solve — Siril, no skill)* | `05_stretch/` | always solve the final master (skip if already `PLTSOLVD`) | **warn + continue** if the solve fails (e.g. no internet) — keep the unsolved master |
| 9 | *(SPCC colour calibration — Siril, no skill)* | `05_stretch/` | SPCC reports `succeeded` and the star-core G/R moves toward 1 — auto-adopt the calibrated master | **warn + continue** if SPCC fails (no internet / no `siril-spcc-database`) — keep the un-calibrated solved master |
| 10 | *(palette master HOO — `tools/palette.py`, no skill)* | `05_stretch/` | always — EMIT or SKIP decided by the emission-separation metric; log either way | never |
| 11 | *(stretch — manual, no skill)* | `05_stretch/` | — | **always present** the final result (stretch is the user's call) |

Notes per step:
- **Step 4 (stack):** pick `experiment_reuse.ssf` if `process/r_pp_light_.seq` exists (you found this
  in Step 1), else `experiment_full.ssf`; choose variants by frame count + target type (the skill's
  table). The adopted stack is the colour-correct base (equalized RGB) — do **not** substitute a raw
  mean. **Note:** an existing `process/` sequence predates the Step-3 quarantine — if frames were
  dropped in Step 3, re-register (full script), don't reuse.
- **Step 5 (background):** the skill's `background.py` runs the GraXpert AI model on the GPU.
  AI is the default; subsky usually backfires on star fields.
- **Step 6 (deconv):** Siril RL (~10 it, optional `-tv`); `makepsf stars` first. This is the
  trap step — measure **ring depth vs background**, not FWHM alone, and lean toward stopping.
  Reject mfdeconv / Cosmic Clarity.
- **Step 7 (denoise):** use the skill's `denoise.py` runner (GPU denoise, ~25s).
  **Pass an absolute output path.** The denoiser is fast, so sweep **broad in one pass** —
  ~0.1 / 0.15 / 0.2 / 0.3 / 0.5 / 0.8 / 0.9 — render a preview per variant, and pick by measurement. Deep
  stacks usually want ~0.15–0.3 or skip; if even the lowest over-blurs, propose skip denoise.
- **Step 8 (plate-solve):** copy the final adopted **linear, header-complete** FITS to
  `05_stretch/<OBJECT>_final_solved.fit`, then plate-solve it in Siril **seeded by the header**
  and **online** (queries the catalog; Seestar's RA/DEC + `FOCALLEN` + `XPIXSZ` make it fast):
  ```
  load <OBJECT>_final_solved
  platesolve -focal=<FOCALLEN> -pixelsize=<XPIXSZ> -noflip
  save <OBJECT>_final_solved
  ```
  Use **`-noflip`** — write WCS into the header, **do not rotate/flip pixels** (non-destructive;
  WCS-aware viewers/SPCC orient North-up from the WCS). Siril prints "Image is already plate
  solved. Nothing will be done." when `PLTSOLVD` is set (Siril's `-2pass` registration already
  solves the stack and the WCS survives header-restore) — that's a fine no-op. If the solve
  **fails** (e.g. no internet), warn the user and continue with the unsolved master.
- **Step 9 (SPCC colour calibration):** Seestar stacks autostretch with green-dominant star
  cores (cyan/turquoise stars) — un-calibrated OSC channel balance (GRBG 2× green + the LP filter
  cuts red). SPCC fixes it on the **linear, plate-solved** master, **before** stretch. Run it in
  Siril with the Seestar S30 profiles from the SPCC database (`siril-spcc-database`):
  ```
  load <OBJECT>_final_solved
  spcc "-oscsensor=Sony IMX585" "-oscfilter=ZWO Seestar LP"
  save <OBJECT>_final_spcc
  ```
  **Siril 1.4.x CLI quoting (the trap):** the whole `-flag=value` token must be wrapped in quotes —
  `spcc "-oscsensor=Sony IMX585" "-oscfilter=ZWO Seestar LP"`. The forms `-oscsensor="Sony IMX585"`
  (quotes after `=`) and `-oscsensor "Sony IMX585"` (space, no `=`) **both fail**. Sensor/filter
  for the S30 are fixed (`Sony IMX585` + `ZWO Seestar LP`; the `_LP_` in the sub filenames confirms
  the LP filter). SPCC needs **online** access (downloads the Gaia catalog) — if it **fails** (no
  internet / DB missing), warn and continue with the un-calibrated `<OBJECT>_final_solved.fit`.
  It may print *"imprecise solution, consider correcting the image gradient first"* on a
  frame-filling nebula — acceptable (background was already extracted in Step 5; the fit is still
  usable). **Measure the effect:** read bright-star-core R/G/B before vs after (top ~0.05% by
  luminance) and log the G/R move toward ~1 (e.g. 1.66 → 1.09) as the verdict. The adopted SPCC
  master `<OBJECT>_final_spcc.fit` becomes the deliverable carried into Steps 10–11; keep the
  pre-SPCC `<OBJECT>_final_solved.fit` too.
- **Step 10 (palette master HOO):** the LP filter is dual-band (Ha 656 nm + OIII ~500 nm),
  so an emission target carries a second free palette in the same data (Ha lives in R, OIII in
  G+B). HOO is the **only** palette emitted — there is no SII line in the filter, so a
  synthetic "SHO" would carry zero new information (we used to emit one; dropped by decision).
  Run on the adopted master — `<OBJECT>_final_spcc.fit`, or `<OBJECT>_final_solved.fit`
  if SPCC failed:
  ```
  .venv/bin/python tools/palette.py 05_stretch/<OBJECT>_final_spcc.fit \
    --outdir 05_stretch --basename <OBJECT>_final
  ```
  It prints one parseable line: `PALETTES: EMIT (separation=..., threshold=...)` or
  `PALETTES: SKIP (...)`. On **EMIT** it writes `<OBJECT>_final_HOO.fit` (R=Ha, G=B=OIII) —
  linear, header + WCS intact, stretch-ready like the SPCC master. Render a preview PNG with
  `tools/preview.py` (no `--ref`) into **`05_stretch/`** (not `previews/` — it must survive
  Step 12 cleanup) as `05_stretch/<OBJECT>_final_HOO.png`, and drop
  the `validate here:` line. On **SKIP** (continuum target — cluster/galaxy: star
  colours, not emission; the gate suppresses stars before measuring, see FINDINGS.md) log the
  verdict line with the measured separation to REPORT.md and move on. This step is always
  AUTO — never stop to ask; log the verdict to REPORT.md in both cases.
- **Step 11 (stretch):** the colour-calibrated `05_stretch/<OBJECT>_final_spcc.fit` (or
  `<OBJECT>_final_solved.fit` if SPCC failed) is the deliverable for the user's own stretch / curves
  (header + WCS + colour intact). Render a stretched full-frame PNG with `tools/preview.py` (no
  `--ref`) **from that calibrated master** as a visual deliverable, writing it **into the kept
  `05_stretch/` dir** as `05_stretch/<OBJECT>_final_stretch.png` (not `previews/`) so it survives
  Step 12 cleanup. Do **not** auto-tune a stretch.

## Previews

After each step's result exists, generate a composite PNG into `previews/` with:
```
.venv/bin/python tools/preview.py <STEP_RESULT>.fit --ref <STEP_INPUT>.fit \
  --out previews/<NN>_<step>.png --title "<step>: <adopted params>"
```
(full frame + before/after + bright-star zoom — the zoom is what reveals deconv rings and star
colour). For multi-variant steps (esp. the broad denoise sweep) preview the top candidates against
the input. **Always view the preview yourself** (read the PNG) before deciding.

## How to stop (when a stop rule fires)

1. Generate the relevant preview(s) and **view them**.
2. **Open the composite PNG for the user** so they can eyeball it without hunting for the file:
   ```
   open <abs preview .png>
   ```
   (macOS `open` → Preview.app; one `open` per decision so windows don't pile up.)
3. Post to the user: the measured table, what the metric recommends, **what you see in the
   preview** (e.g. "zoom shows clean stars" / "rings forming"), and your recommendation.
4. Use a multiple-choice question: adopt the recommended candidate / adopt a different one /
   skip this step / stop the pipeline. **Wait** for the answer, log it, then proceed.

On an auto-adopt, don't `open` a window — just drop the one-line `validate here:` paths (per the
universal rule) so the user can glance back if they want.

## Finish

When Step 11 is done, produce the publication deliverables, then summarize.

1. **AstroBin acquisition CSV** (stacking mode only — needs the raw lights):
   ```
   .venv/bin/python tools/astrobin_session_csv.py <LIGHTS> \
     --out 05_stretch/astrobin_acquisition.csv
   ```
   It groups subs into **observing nights** off the Seestar **local** filename timestamp
   (`_YYYYMMDD-HHMMSS`, shifted −12 h so a night crossing local midnight stays one row — the
   header `DATE-OBS` is UTC, so don't group on it). Auto-fills date/number/duration/binning/gain;
   `filter` defaults to the Seestar integrated LP filter (AstroBin ID 40954 — override with
   `--filter-id N`, or `--filter-id 0` to leave blank) and darks/flats/bias stay blank (Seestar
   calibrates on-device). Pass `--bortle/--sqm/--fwhm` if the user gives them.
2. **AstroBin title + description** → write `05_stretch/astrobin.txt`: a title (designation +
   common name, e.g. `NGC 2070 — Tarantula Nebula (Caldwell 103)`) and a description with scope
   (ZWO Seestar S30 / sensor / focal), total integration (subs × dur = hours, and stacked count
   if it differs), date range, and the **actual processing chain you logged** (stack params →
   GraXpert AI bg → Siril RL params → GraXpert denoise strength → plate-solved → SPCC colour
   calibration: `Sony IMX585` + `ZWO Seestar LP`). If Step 10 emitted, mention the available
   HOO palette master in the description.
3. **Copy the deliverables next to the input** so the user finds them with their data — into
   `DATADIR` (the parent of `LIGHTS/`, or beside the input FITS): the calibrated master
   `<OBJECT>_final_spcc.fit` (and `<OBJECT>_final_solved.fit` if SPCC ran — the pre-SPCC version),
   the palette master `<OBJECT>_final_HOO.fit` and its PNG
   (if Step 10 emitted), `<OBJECT>_astrobin.txt`, `<OBJECT>_astrobin_acquisition.csv`,
   `<OBJECT>_final_stretch.png`.

Then post a short summary: the per-step decisions, the deliverable paths (run dir `05_stretch/`
**and** the copies in `DATADIR`), and the next manual steps (stretch curves — colour is already
SPCC-calibrated, header + WCS intact). Do **not** commit anything (image data is gitignored; the
user commits skills/tools, not run outputs).

## Step 12 — Offer cleanup (optional; last action; never automatic)

The pipeline deliberately leaves a `.fit` + `.png` at **every** stage so the user can resume
manually from any step — deleting those throws that away, so **never prune on your own**. As the
final action, *offer* to reclaim disk by removing the heavy intermediates, and act only on an
explicit confirmation.

1. **Size the prunable set** so the offer carries a real number:
   ```
   du -sh "$RUN"/{01_stack,02_background,03_deconv,04_denoise,previews} "$RUN"
   ```
2. **State exactly what stays vs goes:**
   - **Keep:** `05_stretch/` (SPCC-calibrated master `<OBJECT>_final_spcc.fit`, the pre-SPCC
     `<OBJECT>_final_solved.fit`, the palette master `<OBJECT>_final_HOO.fit` +
     its PNG when Step 10 emitted, the final autostretch PNG
     `<OBJECT>_final_stretch.png`, `astrobin.txt`, `astrobin_acquisition.csv`), `REPORT.md`,
     and the deliverable **copies in `DATADIR`**. The final stretch preview and the palette
     previews live here (in `05_stretch/`, not `previews/`), so removing `previews/` never
     loses them.
   - **Remove:** `01_stack/`, `02_background/`, `03_deconv/`, `04_denoise/`, `previews/`.
   - **Never touch:** the user's source lights, `<lights>/_jpg_aside/`, and
     `<DATADIR>/_clouds_aside/` — those are the user's own originals, not pipeline output.
3. **Ask a yes/no question** with the measured size (e.g. "Permanently delete the intermediates
   and reclaim ~1.4 GB? `05_stretch/` + `REPORT.md` + the `DATADIR` copies are kept; this can't be
   undone"). **Wait** for the answer.
4. On **yes**, permanently remove only those five paths:
   ```
   rm -rf "$RUN"/{01_stack,02_background,03_deconv,04_denoise,previews}
   ```
   then confirm what was freed and what remains. On **no**, leave everything untouched and say so.
