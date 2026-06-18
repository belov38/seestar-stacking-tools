# Seestar pipeline command — design

`/seestar-pipeline` — one agent-orchestrated command that runs a full Seestar S30 frame
through every processing step, picking parameters by measurement, generating previews, and
pausing for the user only when a choice is genuinely ambiguous.

## Goal

Today each step (stack, background, deconv, denoise) is a separate measure-and-compare skill the
user invokes by hand. This command chains them: auto-adopt on a clear verdict, **stop and ask
only when the result is doubtful**, generate intermediate FITS/PNG for validation, then advance.

## Approach

**Agent-orchestrator (approach A).** The command is a markdown instruction file that drives the
agent through the pipeline. The agent invokes the existing per-step skills, runs their
`measure_*.py`, generates previews, and decides (per the rules below) whether to adopt silently
or stop. Parameter choice and ambiguous-case judgement stay with the agent — the scripts produce
numbers, the command says how to read them and when to halt. No monolithic pipeline script (it
would duplicate skill logic and cannot do judgement stops); no workflow engine (overkill for a
linear pipeline with manual gates).

## Form

A **project slash command**: `.claude/commands/seestar-pipeline.md` (frontmatter + body). The body
is the orchestration playbook. It references the four existing skills by name and tells the agent
to invoke each, where to read the verdict, and the stop criteria. Existing skills are unchanged.

## Input (auto-detect)

Single argument = a path.
- **Directory** of raw `.fit` lights → run stacking first (`seestar-stacking-compare`), then the
  rest. The Siril stack (equalized RGB) is the base, so colour is correct.
- **Single FITS** → treat as a ready stack; skip stacking, start at background extraction.

The agent inspects the path to decide; if a directory has no `Light_*.fit` it errors out clearly.

## Pipeline steps and stop logic

"Auto" = adopt the measured winner, write it forward, log to REPORT.md, show preview as FYI
(non-blocking). "Stop" = present the preview + numbers and wait for the user's OK / override.

| Step | Skill | Auto-adopt when | STOP when |
|---|---|---|---|
| 1. Stack *(dir input only)* | `seestar-stacking-compare` | verdict `KEEP BASELINE`, or a tuned win ≥3% faint-SNR **not** from star weighting | the winner is a volatile `nbstars`/`wfwhm` variant (documented trap) |
| 2. Background | `seestar-background-extraction-compare` | GraXpert AI: colour cast < ~1% and not `BACKFIRED` | cast not reduced below ~1%, or every method backfired |
| 3. Deconv | `seestar-deconvolution-compare` | ring depth comfortably above the floor **and** FWHM gain clear | **default** — ring depth borderline / any doubt (this is the step where FWHM-alone misled us into adopting donuts) |
| 4. Denoise | `seestar-denoise-compare` | strongest setting with FWHM Δ < ~3% and faint_keep > ~0.85 | even the lowest strength over-blurs → propose **skip** |
| 5. Stretch | (no skill — final) | — | always present the final result (stretch is a manual decision) |

Deconv is conservative by design: it auto-adopts only on a clean ring metric **and** a clear FWHM
gain; any borderline reading stops. All other steps run hands-off on a clear verdict.

## Header / colour correctness

- Base = the Siril stack (stacking mode) or the input FITS (single-FITS mode) — never a raw
  red-heavy mean. This keeps star colour balanced (the white-star bug came from a wrong base).
- Every GraXpert step (background, denoise) restores the FITS header (the skills / `denoise.py`
  wrapper already do this), so the final FITS is plate-solve / SPCC ready.

## Previews — new `tools/preview.py`

GraXpert/Siril emit linear FITS; the user validates by eye. New shared tool generates a composite
PNG per stop point:
- **Full frame**, autostretched (asinh / midtones) — shows gradient, colour cast, overall look.
- **Zoom crops** on the brightest stars — reveals deconv rings / "donuts" and star colour.
- **Before/after** side-by-side (step input vs step result), same stretch for both.

`preview.py INPUT.fits [--ref BEFORE.fits] [--out preview.png]`. Needs `Pillow` (+ existing
`numpy`/`astropy`). RGB FITS rendered in colour; the autostretch matches across panels so the
comparison is fair.

## Output layout

```
out/pipeline/<OBJECT>_<UTCstamp>/
  01_stack/        candidate stacks + metrics (if stacking mode)
  02_background/   bg-extracted FITS + measure output
  03_deconv/       deconv variants + ring/FWHM measure
  04_denoise/      denoise sweep + measure
  05_stretch/      final linear FITS (header-complete) + stretched PNG deliverable
  previews/        composite PNGs shown at each stop point
  REPORT.md        running log: input, variants tried, numbers, what was adopted and why
```

`<UTCstamp>` from the shell at run start. `out/` is gitignored (image data is not committed).

## Setup / dependencies

The measure scripts need `sep` + `scipy`; the new preview tool needs `Pillow`. The venv currently
has only `astropy` + `numpy`. The command checks deps at start and tells the user to run:
```
.venv/bin/python -m pip install sep scipy pillow
```
(README setup line updated to include them.) External tools unchanged: Siril CLI, GraXpert CLI.

## Out of scope (YAGNI)

- Automated stretch tuning — stretch stays a manual step; the command only delivers a preview +
  header-complete linear FITS for the user's own stretch / SPCC in astro software.
- Plate solving / SPCC inside the command — the output is made *ready* for it, not run through it.
- Parallel multi-target batch runs — one frame per invocation.

## Files

- **New:** `.claude/commands/seestar-pipeline.md`, `tools/preview.py`.
- **Edited:** `README.md` (setup deps + command in the pipeline table), `FINDINGS.md` (pointer to
  the orchestrator), `.gitignore` (ensure `out/pipeline/` covered — already under `out/`).
- **Unchanged:** the four `seestar-*-compare` skills.
