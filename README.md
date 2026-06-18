# Seestar processing tools

Tools for post-processing ZWO Seestar (S30, IMX585, GRBG) deep-sky FITS, built as a set
of **measure-and-compare** skills: for each step, run a few variants, measure the result
objectively, and adopt the best — or keep the baseline if nothing wins cleanly.

Everything is versioned in this repo. Empirical findings live in [`FINDINGS.md`](FINDINGS.md)
(and [`deconv/FINDINGS.md`](deconv/FINDINGS.md) for the deconvolution deep-dive) — not in any
external/global store.

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

`deconv/` is a research record (why mfdeconv was rejected), not a runtime tool.

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install astropy numpy sep scipy pytest
```

`measure_stacks.py` needs astropy+numpy; the deconv/bg/denoise measurers also need `sep`+`scipy`.
External tools: Siril (`/Applications/Siril.app/Contents/MacOS/siril-cli`) and GraXpert
(`/Applications/GraXpert.app/Contents/MacOS/GraXpert`), both run headless.

## Run tests

```bash
cd .claude/skills/seestar-stacking-compare
../../../.venv/bin/python -m pytest -q
```

## Key cross-cutting lesson

The "obvious" tool backfires somewhere on almost every step (star weighting on some stacks,
Siril subsky on star fields, multi-frame deconv rings, denoise blurs). So every step
**measures** and adopts only on a clean, quantified win. Image data (`*.fit`) and the venv are
gitignored.
