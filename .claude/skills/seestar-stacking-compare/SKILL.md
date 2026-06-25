---
name: seestar-stacking-compare
description: Use when stacking ZWO Seestar deep-sky FITS lights in Siril and you want to compare rejection/weighting parameter combinations against the default Seestar script, measure background noise and SNR, and save the best result. Triggers on Seestar .fit lights, Siril siril-cli stacking, choosing sigma/winsor/weight params, "which stacking settings are best", comparing to baseline.
---

# Seestar Stacking Compare

## Overview
Stack a Seestar lights folder many ways in Siril, measure each result objectively (background noise + SNR in three crops), and compare to the **baseline** (what the default Seestar script does = `winsor 3 3`). Adopt a tuned variant only when it beats baseline by a meaningful margin; otherwise keep the default. Save the winner to a `best/` folder.

**Core insight:** the optimal stacking parameters depend on **frame count AND target type** — there is no single best setting, so you measure per session.

## When to use
- You have a Seestar `lights/` folder (raw `Light_*.fit`, Bayer `GRBG`, already dark/bias-subtracted on-device).
- You want to know if tuning rejection/weighting beats the default Seestar Siril script.
- You want a reproducible "best stack" with documented params.

Not for: calibration-frame workflows (Seestar handles darks/bias itself — script only debayers), or non-Seestar data.

## Tools (in this skill dir)
- `experiment_full.ssf` — from raw lights: link → debayer → register once → baseline + variant stacks.
- `experiment_reuse.ssf` — reuse an existing `process/r_pp_light_.seq` (skip registration; big time/disk saving on large sets).
- `measure_stacks.py <process_dir>` — ranks `result_v[0-9]*.fit`, prints per-crop tables and **BEST-vs-BASELINE** verdict, writes `metrics.csv`. Needs Python with `astropy` + `numpy`.

## Workflow
1. **Inspect** the folder: count `lights/*.fit`; remove any `.jpg` (breaks Siril `link`); check for existing `process/r_pp_light_.seq`.
2. **Identify the target type** from `OBJECT`/the image: **star field** vs **nebula fills the frame**. This changes which variants help (see table). Ask the user if unsure.
3. **Pick the script:** `experiment_reuse.ssf` if registration exists, else `experiment_full.ssf`. Edit the variant block per the table below.
4. **Run:** `siril-cli -d <workdir> -s <script>.ssf` (background; ~1-2 s/variant small, longer for hundreds of frames). The Siril binary on macOS: `siril-cli` if on PATH (Homebrew install), else `/Applications/Siril.app/Contents/MacOS/siril-cli` (manual app install).
5. **Measure:** `python measure_stacks.py <workdir>/process`. Read the verdict.
6. **Save:** copy the recommended file to `<workdir>/best/` with a param-encoded name, e.g. `M6_258f_winsor3-3_wnbstars_BEST.fit`. If the verdict says KEEP BASELINE, copy the baseline file instead.

## Choosing variants (the key judgement)

| Dataset | Add these variants | Avoid |
|---|---|---|
| **Few frames (<~30)** | baseline, `sigma 3 3`, `sigma 3.5 3.5`, `+weight=noise` | star-based weighting, frame filtering, aggressive `sigma 2 2` |
| **Many frames (≳100), STAR FIELD** | test `weight=nbstars`, `weight=wfwhm`, `filter-wfwhm=90%` — **but verify, they often backfire** | adopting weighting without measuring |
| **Many frames, NEBULA fills frame** | + gentle `sigma 3.5 3.5` / `sigma 4 4` | `weight=nbstars`/`weight=wfwhm` (bright nebula corrupts star detection → worse) |

Always include the BASELINE (`winsor 3 3`) and `percentile 0.2` (robust). Stable losers everywhere: `sigma 2 2`, `linear`, `k-MAD`.

**Star-based weighting (`nbstars`/`wfwhm`) is the most volatile knob — NEVER adopt it on prediction alone.** It won big on one open cluster (M6, +7%) but collapsed on a dense globular with short subs (Omega Cen / C80: faint SNR halved, bg noise ×3) and on nebula-filled frames (Carina). When star detection is unstable (crowding, faint/short subs, extended nebulosity) the weights zero out good frames. Outcome is target-specific and not reliably predictable — that is why the workflow MEASURES every time and adopts only on a measured ≥3% win.

## Interpreting metrics
- **faint_SNR** = primary metric. Scale-invariant ratio on faint nebulosity; higher = better. Trust it over the others.
- **bright_SNR** = SNR on bright stars/core (scale-invariant).
- **bg_sigma** = background noise. **Scale-dependent** (`-output_norm` rescales each result) — only relative, don't over-weight it.
- Metrics are computed in **three crops (90% / 2/3 / 1/4)** because registration leaves garbage borders; verdict uses 2/3.
- **Adopt rule:** take the tuned variant only if faint_SNR beats baseline by **≥3%** (below that is measurement noise → keep default). A +x% SNR gain ≈ what you'd get from ((1+x)²−1) more integration time.

## Common mistakes
- Stacking already-debayered/stacked files — feed **raw single Bayer lights** only.
- Forgetting `.jpg` removal → `link` fails.
- Applying `weight=nbstars`/`wfwhm` on a nebula-filled frame (it backfires).
- Filtering/weighting on a small set (loses signal — every frame counts).
- Trusting `bg_sigma` across variants as if absolute — use SNR ratios.
- Re-registering hundreds of frames when `process/r_pp_light_.seq` already exists — reuse it.
