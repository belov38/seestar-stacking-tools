---
name: seestar-background-extraction-compare
description: Use when removing the background gradient and colour cast from a stacked ZWO Seestar deep-sky image, comparing GraXpert AI against Siril subsky, measuring residual gradient and colour cast objectively, and adopting the cleanest result. Triggers on Seestar stack background extraction, gradient/light-pollution removal, red/colour cast on autostretch, GraXpert background-extraction CLI, Siril subsky, neutralizing background before stretch.
---

# Seestar Background Extraction Compare

## Overview
Flatten the background gradient and neutralize the colour cast of a Seestar stack, compare
methods objectively (residual gradient + colour cast), and adopt the cleanest. This is the
step **right after stacking, before deconvolution and stretch** — raw Seestar stacks
autostretch with a strong **red/colour cast** (LP filter + IMX585 response) that must be
removed first.

**Core insight:** on Seestar S30 the dominant problem is usually the **colour cast, not the
gradient** — the small FOV and good tracking keep gradients low (~2%). And **Siril `subsky`
backfires on star fields**: it places background samples automatically, but a dense cluster
leaves no clean background, so it fits a garbage plane and *introduces* a gradient. **GraXpert
AI** (trained model, no manual samples) flattens cleanly and neutralizes the cast.

## When to use
- You have a stacked Seestar image (linear FITS) with a colour cast / gradient on autostretch.
- You want it flat and neutral before deconvolution / stretching.

## Use GraXpert AI — not Siril subsky on star fields
Measured on M6 (dense cluster, 258×30s):

| method | residual gradient | colour cast | note |
|---|---|---|---|
| original | 2.0% | 4.7% | red cast |
| **GraXpert AI** | **~flat (0.5%)** | **0.1%** | flat & neutral — adopt |
| Siril subsky poly1 | 29% | 0.6% | **backfired** (added gradient) |
| Siril subsky poly4 | 35% | 0.1% | **backfired** |
| Siril subsky rbf | 22% | 0.0% | **backfired** |

Siril subsky is fine where clean background samples *can* be placed (sparse field, nebula with
open sky). On crowded fields it fails — measure, don't assume. GraXpert AI is the safe default.

## Tools (in this skill dir)
- `background.py INPUT.fits OUTPUT.fits [--smoothing 0.5] [--correction Subtraction|Division] [--cpu]`
  — **the runner**: runs the GraXpert AI background model on the **Apple-Silicon GPU** (CoreML,
  output identical to GraXpert) via `tools/gpu/gx_gpu.py`, preserving the FITS header. No GraXpert
  install needed (see one-time setup below); `--cpu` runs on our own onnxruntime.
- `subsky_compare.ssf` — optional Siril subsky variants, to confirm whether subsky backfires
  on your target.
- `measure_bg.py ORIGINAL.fit RESULT...fit` — residual gradient %, colour cast %, neg-pixel %,
  flags `BACKFIRED`/over-subtraction, recommends the flattest+neutral result. Needs
  `numpy`, `astropy`.

**One-time setup** (downloads models into the root venv, no GraXpert required):
```
../../../.venv/bin/python ../../../tools/gpu/fetch_models.py
```

## Workflow
1. **AI background extraction** (the main path) — keeps the header, ready for plate solving / SPCC:
   ```
   python background.py <stack.fits> bg_ai.fits --smoothing 0.5
   ```
   `--smoothing` 0.0 (follow data) → 1.0 (very smooth); ~0.5 is a good default. AI mode needs no
   sample grid and is the safe default on crowded Seestar fields.
2. **(optional) Siril subsky** for comparison: `siril-cli -d <workdir> -s subsky_compare.ssf`
   (needs `stack.fit` in the workdir). Confirms backfire on crowded fields.
3. **Measure:** `python measure_bg.py <stack> bg_ai.fits <subsky_out>.fit ...`.
4. **Adopt** the recommended result (flat + neutral, not backfired/over-subtracted).

## Choosing parameters (GraXpert prefs JSON)
- `interpol_type_option`: **"AI"** (default, no samples needed). "RBF"/"Splines"/"Kriging" use
  a sample grid (`bg_pts_option`, `bg_tol_option`) and can backfire on crowded fields like subsky.
- `smoothing_option`: 0.0 (follow data) → 1.0 (very smooth). **~0.5** is a good default; raise it
  if extraction eats faint structure, lower it if a gradient survives.
- `correction`: "Subtraction" (default, additive gradient) — keep for linear data.

## Interpreting metrics
- **colour_cast %** = max per-channel background deviation. The decisive metric on Seestar —
  get it **< ~1%** (raw is ~5%). This is what kills the red autostretch.
- **gradient %** = spread of tile background levels across the frame. Lower = flatter. On
  Seestar it starts low (~2%); the goal is "don't make it worse." A result with gradient
  **higher than the original** = the tool **BACKFIRED** → reject.
- **neg %** = fraction of negative background pixels (over-subtraction hint).
- **Adopt rule:** take the result with lowest gradient+cast that did **not** backfire. On
  Seestar that is essentially always GraXpert AI.

## Common mistakes
- **Siril subsky on a dense star field** — auto-sample placement fails, it adds a gradient.
  The skill's `BACKFIRED` flag catches this; on crowded fields use GraXpert AI.
- Chasing gradient when the real problem is the **colour cast** — read cast first on Seestar.
- Running background extraction on **stretched** data — do it on the linear stack, before stretch.
- **Over-smoothing** (smoothing→1.0) or sample-based methods on nebula targets can eat real
  nebulosity — the metric only sees the background plane, so confirm nebula targets visually.
- Wrong pipeline order — background extraction comes **after stacking, before deconvolution**.
