---
description: Run a Seestar S30 frame through the full processing pipeline (stack → background → deconv → denoise → plate-solve → stretch), auto-picking parameters by measurement, stopping only when a choice is doubtful, and emitting AstroBin title/description + acquisition CSV.
argument-hint: <lights-dir | stack.fits>
---

# Seestar pipeline orchestrator

You are running the **full Seestar S30 post-processing pipeline** end to end as an agent
orchestrator. Drive the existing per-step skills, measure each result, generate previews, and
**advance automatically on a clear verdict — stop and ask the user only when the result is
doubtful** (the table below says exactly when).

Input path: `$1` (a directory of raw lights, or a single stacked FITS).

## Fixed facts (don't re-derive)

- Python: `.venv/bin/python` (has astropy, numpy, sep, scipy, Pillow).
- Siril CLI: `/Applications/Siril.app/Contents/MacOS/siril-cli`
- Background + denoise run on the **Apple-Silicon GPU** via `tools/gpu/` (CoreML, no GraXpert
  install). One-time: `bash tools/gpu/setup.sh && tools/gpu/.venv/bin/python tools/gpu/fetch_models.py`.
  The skill runners `background.py` / `denoise.py` wrap it and preserve the FITS header.
- AstroBin session CSV: `tools/astrobin_session_csv.py` (scans lights, emits the import CSV).
- Pipeline order is fixed: **stack → background extraction → deconvolution → denoise →
  plate-solve → stretch**. Deconv and denoise run on **linear** data; denoise comes after deconv;
  plate-solve runs on the final linear master.
- Pass an **absolute** output path to the runners to avoid any cwd ambiguity.
- Each step is a skill with a `measure_*.py` that prints a verdict — trust the numbers, but the
  **stop rules below override blind adoption** (FWHM once fooled us into adopting deconv donuts).

## Step 0 — preflight (always)

1. **Deps:** `.venv/bin/python -c "import astropy,numpy,sep,scipy,PIL"`. If it fails, tell the
   user to run `.venv/bin/python -m pip install sep scipy pillow` and stop.
2. **Resolve input `$1` to the Siril `lights/` convention.** The stacking `.ssf` scripts
   hardcode `cd lights`, so any other layout silently breaks at `link` — enforce it:
   - `$1` is (or ends in) **`lights/`** containing `.fit` → `LIGHTS=$1`, `WORKDIR=dirname($1)`.
   - `$1` is a **dir containing a `lights/` subdir** → `WORKDIR=$1`, `LIGHTS=$1/lights`.
   - `$1` is a **dir of `.fit` not named `lights`** (no `lights/` subdir) → **error clearly**:
     ask the user to move the subs into `<dir>/lights/`. Do **not** guess or auto-rename.
   - `$1` is a **single FITS** → ready-stack mode (skip Step 1; this file is the Step-2 base).
     Verify it has a real header (OBJECT/RA/DEC) — warn if header-stripped. (No `lights/` needed.)
   Then **validate**: `LIGHTS/` exists and holds ≥1 `.fit` (else error out). **Quarantine `.jpg`:**
   Seestar drops `.jpg` thumbnails that break Siril `link` — move any to `LIGHTS/_jpg_aside/`
   (move, don't delete — reversible) and report the count.
3. **Read `OBJECT`** from a representative FITS (astropy) for naming; fall back to the basename.
4. **Make the run dir:** `STAMP=$(date -u +%Y%m%dT%H%M%SZ)`, then
   `out/pipeline/<OBJECT>_<STAMP>/` with subdirs `01_stack 02_background 03_deconv 04_denoise
   05_stretch previews`. Create `REPORT.md` with a header (input, mode, object, UTC time).
5. Announce the plan and the run dir to the user, then proceed.

Throughout: after every step append to `REPORT.md` — the variants tried, the measured numbers,
what was adopted, and **why** (or what the user chose at a stop). Carry the **adopted FITS
forward** as the input to the next step. **After every step (auto-adopt or stop), print to the
user a one-line `validate here:` with the absolute path of the adopted FITS and the preview PNG**,
so they can open any intermediate in Siril and check it before the pipeline moves on.

## The steps

For each step: invoke the named skill (to load its current how-to), run its sweep with the
binaries above, run its `measure_*.py`, generate the preview(s), then apply the stop rule.

| # | Skill to invoke | Output dir | AUTO-adopt when | STOP & ask when |
|---|---|---|---|---|
| 1 | `seestar-stacking-compare` *(dir input only)* | `01_stack/` | verdict `KEEP BASELINE`, **or** a tuned win ≥3% faint-SNR that is **not** from star weighting | the winner is a `nbstars`/`wfwhm` (star-weighting) variant — volatile, confirm before adopting |
| 2 | `seestar-background-extraction-compare` | `02_background/` | GraXpert AI: colour cast < ~1% and **not** `BACKFIRED` | cast not pulled under ~1%, or every method backfired |
| 3 | `seestar-deconvolution-compare` | `03_deconv/` | ring depth comfortably above the floor **AND** a clear FWHM gain | **default to STOP** on any doubt — borderline ring depth, marginal FWHM, or visible rings in the preview |
| 4 | `seestar-denoise-compare` | `04_denoise/` | strongest setting with FWHM Δ < ~3% **and** faint_keep > ~0.85 | even the lowest strength over-blurs → propose **skip denoise** |
| 5 | *(plate-solve — Siril, no skill)* | `05_stretch/` | always solve the final master (skip if already `PLTSOLVD`) | **warn + continue** if the solve fails (e.g. no internet) — keep the unsolved master |
| 6 | *(stretch — manual, no skill)* | `05_stretch/` | — | **always present** the final result (stretch is the user's call) |

Notes per step:
- **Step 1 (stack):** pick `experiment_reuse.ssf` if `process/r_pp_light_.seq` exists, else
  `experiment_full.ssf`; choose variants by frame count + target type (the skill's table). The
  adopted stack is the colour-correct base (equalized RGB) — do **not** substitute a raw mean.
- **Step 2 (background):** the skill's `background.py` runs the GraXpert AI model on the GPU and
  preserves the header. AI is the default; subsky usually backfires on star fields.
- **Step 3 (deconv):** Siril RL (~10 it, optional `-tv`); `makepsf stars` first. This is the
  trap step — measure **ring depth vs background**, not FWHM alone, and lean toward stopping.
  Reject mfdeconv / Cosmic Clarity.
- **Step 4 (denoise):** use the skill's `denoise.py` runner (GPU denoise, header preserved, ~25s).
  **Pass an absolute output path.** Sweep ~0.3/0.5/0.8; if even the lowest over-blurs, re-sweep
  gentler (~0.15/0.2) before proposing skip. Deep stacks usually want ~0.2–0.3 or skip.
- **Step 5 (plate-solve):** copy the final adopted **linear, header-complete** FITS to
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
- **Step 6 (stretch):** the `05_stretch/<OBJECT>_final_solved.fit` is the deliverable for the
  user's own stretch / SPCC (header + WCS intact). Render a stretched full-frame PNG with
  `tools/preview.py` (no `--ref`) as a visual deliverable. Do **not** auto-tune a stretch.

## Previews

After each step's result exists, generate a composite PNG into `previews/` with:
```
.venv/bin/python tools/preview.py <STEP_RESULT>.fit --ref <STEP_INPUT>.fit \
  --out previews/<NN>_<step>.png --title "<step>: <adopted params>"
```
(full frame + before/after + bright-star zoom — the zoom is what reveals deconv rings and star
colour). For multi-variant steps you may also preview the top 1–2 candidates against the input.
**Always view the preview yourself** (read the PNG) before deciding, and **show it to the user**
at every stop. For the final stretch deliverable run `preview.py` with no `--ref`.

## How to stop (when a stop rule fires)

1. Generate the relevant preview(s) and **view them**.
2. Post to the user: the measured table, what the metric recommends, **what you see in the
   preview** (e.g. "zoom shows clean stars" / "rings forming"), and your recommendation.
3. Use a multiple-choice question: adopt the recommended candidate / adopt a different one /
   skip this step / stop the pipeline. **Wait** for the answer, log it, then proceed.

Even on an auto-adopt, drop a one-line note + the preview path so the user can glance back.

## Finish

When Step 6 is done, produce the publication deliverables, then summarize.

1. **AstroBin acquisition CSV** (stacking mode only — needs the raw lights):
   ```
   .venv/bin/python tools/astrobin_session_csv.py <LIGHTS> \
     --out 05_stretch/astrobin_acquisition.csv
   ```
   It groups subs into **observing nights** off the Seestar **local** filename timestamp
   (`_YYYYMMDD-HHMMSS`, shifted −12 h so a night crossing local midnight stays one row — the
   header `DATE-OBS` is UTC, so don't group on it). Auto-fills date/number/duration/binning/gain;
   leaves `filter` blank (it needs the user's AstroBin numeric filter ID — tell them to set
   `--filter-id`) and darks/flats/bias blank (Seestar calibrates on-device). Pass `--bortle/--sqm/
   --fwhm` if the user gives them.
2. **AstroBin title + description** → write `05_stretch/astrobin.txt`: a title (designation +
   common name, e.g. `NGC 2070 — Tarantula Nebula (Caldwell 103)`) and a description with scope
   (ZWO Seestar S30 / sensor / focal), total integration (subs × dur = hours, and stacked count
   if it differs), date range, and the **actual processing chain you logged** (stack params →
   GraXpert AI bg → Siril RL params → GraXpert denoise strength → plate-solved).
3. **Copy the deliverables next to the input** so the user finds them with their data: into the
   **parent of `LIGHTS/`** (stacking mode) or next to the input FITS (ready-stack mode):
   `<OBJECT>_final_solved.fit`, `<OBJECT>_astrobin.txt`, `<OBJECT>_astrobin_acquisition.csv`,
   `<OBJECT>_final_stretch.png`.

Then post a short summary: the per-step decisions, the deliverable paths (run dir `05_stretch/`
**and** the copies next to `lights/`), and the next manual steps (stretch curves, SPCC — header +
WCS are intact). Do **not** commit anything (image data is gitignored; the user commits
skills/tools, not run outputs).
