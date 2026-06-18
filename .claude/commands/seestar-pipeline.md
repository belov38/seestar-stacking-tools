---
description: Run a Seestar S30 frame through the full processing pipeline (stack → background → deconv → denoise → stretch), auto-picking parameters by measurement and stopping only when a choice is doubtful.
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
- GraXpert CLI: `/Applications/GraXpert.app/Contents/MacOS/GraXpert`
- Pipeline order is fixed: **stack → background extraction → deconvolution → denoise → stretch**.
  Deconv and denoise run on **linear** data; denoise comes after deconv.
- Each step is a skill with a `measure_*.py` that prints a verdict — trust the numbers, but the
  **stop rules below override blind adoption** (FWHM once fooled us into adopting deconv donuts).

## Step 0 — preflight (always)

1. **Deps:** `.venv/bin/python -c "import astropy,numpy,sep,scipy,PIL"`. If it fails, tell the
   user to run `.venv/bin/python -m pip install sep scipy pillow` and stop.
2. **Resolve input `$1`:**
   - A **directory** → stacking mode (start at Step 1). Sanity-check it holds `Light_*.fit`
     (or `*.fit` lights); if none, error out clearly.
   - A **single FITS** → ready-stack mode (skip Step 1; this file is the base for Step 2). Verify
     it has a real header (OBJECT/RA/DEC) — if it's header-stripped, warn the user.
3. **Read `OBJECT`** from a representative FITS (astropy) for naming; fall back to the basename.
4. **Make the run dir:** `STAMP=$(date -u +%Y%m%dT%H%M%SZ)`, then
   `out/pipeline/<OBJECT>_<STAMP>/` with subdirs `01_stack 02_background 03_deconv 04_denoise
   05_stretch previews`. Create `REPORT.md` with a header (input, mode, object, UTC time).
5. Announce the plan and the run dir to the user, then proceed.

Throughout: after every step append to `REPORT.md` — the variants tried, the measured numbers,
what was adopted, and **why** (or what the user chose at a stop). Carry the **adopted FITS
forward** as the input to the next step.

## The steps

For each step: invoke the named skill (to load its current how-to), run its sweep with the
binaries above, run its `measure_*.py`, generate the preview(s), then apply the stop rule.

| # | Skill to invoke | Output dir | AUTO-adopt when | STOP & ask when |
|---|---|---|---|---|
| 1 | `seestar-stacking-compare` *(dir input only)* | `01_stack/` | verdict `KEEP BASELINE`, **or** a tuned win ≥3% faint-SNR that is **not** from star weighting | the winner is a `nbstars`/`wfwhm` (star-weighting) variant — volatile, confirm before adopting |
| 2 | `seestar-background-extraction-compare` | `02_background/` | GraXpert AI: colour cast < ~1% and **not** `BACKFIRED` | cast not pulled under ~1%, or every method backfired |
| 3 | `seestar-deconvolution-compare` | `03_deconv/` | ring depth comfortably above the floor **AND** a clear FWHM gain | **default to STOP** on any doubt — borderline ring depth, marginal FWHM, or visible rings in the preview |
| 4 | `seestar-denoise-compare` | `04_denoise/` | strongest setting with FWHM Δ < ~3% **and** faint_keep > ~0.85 | even the lowest strength over-blurs → propose **skip denoise** |
| 5 | *(stretch — manual, no skill)* | `05_stretch/` | — | **always present** the final result (stretch is the user's call) |

Notes per step:
- **Step 1 (stack):** pick `experiment_reuse.ssf` if `process/r_pp_light_.seq` exists, else
  `experiment_full.ssf`; choose variants by frame count + target type (the skill's table). The
  adopted stack is the colour-correct base (equalized RGB) — do **not** substitute a raw mean.
- **Step 2 (background):** GraXpert strips the header — the skill restores it
  (`tools/restore_fits_header.py`). GraXpert AI is the default; subsky usually backfires on star
  fields.
- **Step 3 (deconv):** Siril RL (~10 it, optional `-tv`); `makepsf stars` first. This is the
  trap step — measure **ring depth vs background**, not FWHM alone, and lean toward stopping.
  Reject mfdeconv / Cosmic Clarity.
- **Step 4 (denoise):** use the skill's `denoise.py` runner (GraXpert denoise + header restore in
  one step). Sweep ~0.3/0.5/0.8; deep stacks usually want ~0.3 or skip.
- **Step 5 (stretch):** copy the final adopted **linear, header-complete** FITS to `05_stretch/`
  (this is the deliverable for the user's own stretch / plate-solve / SPCC), and render a
  stretched full-frame PNG with `tools/preview.py` as a visual deliverable. Do not auto-tune a
  stretch.

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

When Step 5 is done, post a short summary: the per-step decisions, the final deliverable paths
(`05_stretch/` FITS + stretched PNG), and the next manual steps (stretch curves, plate solve,
SPCC — the FITS header is intact for these). Do **not** commit anything (image data is
gitignored; the user commits skills/tools, not run outputs).
