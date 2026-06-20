# Seestar processing tools

Tools for post-processing ZWO Seestar (S30, IMX585, GRBG) deep-sky FITS, built as a set
of **measure-and-compare** skills: for each step, run a few variants, measure the result
objectively, and adopt the best — or keep the baseline if nothing wins cleanly.

Findings live in [`FINDINGS.md`](FINDINGS.md), versioned in this repo.

## Pipeline & skills

Processing order, one skill per step (all in `.claude/skills/`, project-scoped):

| step | skill | tool | one-line finding |
|---|---|---|---|
| 1. Stack | `seestar-stacking-compare` | Siril | best params depend on frame count + target type |
| 2. Background extraction | `seestar-background-extraction-compare` | GraXpert AI | Siril subsky backfires on star fields; the cast is the real problem |
| 3. Deconvolution | `seestar-deconvolution-compare` | Siril RL ~10it | mfdeconv/Seti ring; measure ring-vs-background, not just FWHM |
| 4. Denoise | `seestar-denoise-compare` | GraXpert ~0.3 | monotonic noise↔blur tradeoff; deep stacks need little |

Then stretch (manual). Each skill has a `SKILL.md` (when/how + variant guidance), a runner
(`.ssf` / GraXpert prefs JSON), and a `measure_*.py` that prints an adopt/skip verdict.

### Run the whole pipeline: `/seestar-pipeline`

`/seestar-pipeline <lights-dir | stack.fits>` chains all four steps: auto-detects the input
(lights → stack first; single FITS → ready stack), picks each step's parameters by measurement,
and **stops to ask only when a choice is doubtful** (deconv rings, backfired background, volatile
star-weighted stack). Outputs land under `out/pipeline/<object>_<stamp>/` with a `REPORT.md` log;
the deliverable is a header-complete linear FITS plus a stretched PNG.

### tools/

- `tools/gpu/` — Apple-Silicon GPU runner (CoreML) for the GraXpert denoise & background
  models, no GraXpert install needed. See `tools/gpu/README.md`. All pipeline steps preserve
  the FITS header themselves.
- `tools/preview.py RESULT.fits [--ref BEFORE.fits] [--out p.png]` — composite validation PNG:
  full-frame auto-stretch + bright-star zoom crops (reveal deconv rings / star colour) +
  optional before/after, all under one linked stretch. Used by `/seestar-pipeline` at each
  validation gate.

## Setup

```bash
/opt/homebrew/bin/python3.13 -m venv .venv        # py3.13 — onnxruntime≥1.20 needs ≥3.10
.venv/bin/python -m pip install astropy numpy sep scipy pillow pytest \
  "onnxruntime>=1.20" onnx scikit-image opencv-python-headless packaging
.venv/bin/python tools/gpu/fetch_models.py        # GPU denoise/background models
```

One venv for everything: the skill measurers need astropy/numpy (+`sep`/`scipy`); the GPU
runner (`tools/gpu/`) adds onnxruntime/scikit-image/opencv. Only external tool is Siril
(`/Applications/Siril.app/Contents/MacOS/siril-cli`), headless. GraXpert install is **not**
needed — the GPU runner runs its models directly.

## Run tests

```bash
cd .claude/skills/seestar-stacking-compare
../../../.venv/bin/python -m pytest -q
```

## Key cross-cutting lesson

The "obvious" tool backfires somewhere on almost every step (star weighting on some stacks,
Siril subsky on star fields, multi-frame deconv rings, denoise blurs) — so every step measures
and adopts only on a clean win. Image data (`*.fit`) and the venv are gitignored.
