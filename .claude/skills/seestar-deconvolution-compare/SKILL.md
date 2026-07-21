---
name: seestar-deconvolution-compare
description: Use when deconvolving a stacked ZWO Seestar deep-sky image to sharpen stars, and you want to compare Siril Richardson-Lucy settings against the un-deconvolved stack, measure star FWHM AND ringing objectively, and adopt only a clean gain. Triggers on Seestar stack deconvolution, Siril rl/makepsf, "sharpen stars", "deconvolve the stack", choosing iters/TV/alpha, ringing/dark-halo artifacts around stars.
---

# Seestar Deconvolution Compare

## Overview
Deconvolve a Seestar stack several ways with **Siril Richardson-Lucy**, measure each result
objectively (bright-star FWHM **and** ringing relative to background), and compare to the
**baseline** (the un-deconvolved stack). Adopt a variant only when it sharpens stars **without
carving dark rings**; otherwise keep the baseline. Deconvolution here is a minor polish, not a
transformative step.

**Core insight:** Seestar S30 data is **undersampled** (~4 "/px, stars ~2.8 px FWHM in the
stack) — there is little real blur to remove, so the honest ceiling is ~−5% FWHM. Push harder
and Richardson-Lucy stops sharpening and starts **ringing** (a black halo around bright stars).
**FWHM alone is a trap:** aggressive RL shrinks the core (great FWHM number) while gouging a
dark ring that ruins the image. Always judge on FWHM **and** ring-vs-background.

## When to use
- You have a stacked Seestar deep-sky image (linear FITS, RGB or mono) and want sharper stars.
- You want to know if deconvolution beats the un-deconvolved stack — and by how much, cleanly.

Not for: undersampled targets where you already know the answer is "skip" (use judgement —
the gain is always small on S30); per-frame deconvolution (never deconvolve single subs).

## Use Siril RL — not mfdeconv / Seti tools
SASpro **mfdeconv** and Cosmic Clarity were evaluated and **rejected** for S30: mfdeconv
estimates the PSF per-frame (wider than the stacked stars) →
over-deconvolves → black rings 80–150× RMS below background on every bright star, even at low
iterations. Cosmic Clarity is deprecated. **Siril `makepsf stars` measures the PSF from the
stack itself**, so it matches the real star size and stays clean. Denoise is a separate step
(GraXpert, after deconvolution).

## RC Astro path (BlurXTerminator) — preferred when licensed

If `tools/rcastro.py probe` reports `bxt=ok`, skip the Siril RL sweep entirely and run two
bxt variants on the **linear** stack:

```
../../../.venv/bin/python ../../../tools/rcastro.py bxt stack.fit bxt_default.fit --ss 0.5 --sn 1.0
../../../.venv/bin/python ../../../tools/rcastro.py bxt stack.fit bxt_correct.fit --correct-only
```

Measure them with the SAME measurer and the SAME adopt rule as the RL variants:

```
python measure_deconv.py stack.fit bxt_default.fit bxt_correct.fit
```

- Adopt only on FWHM gain ≥3% AND ring_worst ≥ −1×RMS. bxt is trained not to ring, but the
  measurement — not the reputation — decides; a REJECT keeps the un-deconvolved baseline
  (do not fall back to the RL sweep: on S30 data RL was never cleaner than bxt's rejects).
- `--correct-only` fixes PSF aberrations without sharpening — often the honest winner on
  undersampled S30 stacks where sharpening has no room.
- A failed bxt run (non-zero exit) → fall back to the Siril RL workflow below.

## Tools (in this skill dir)
- `experiment.ssf` — Siril RL variants on a stack (`stack.fit` in the workdir → `rl_*.fit`).
- `measure_deconv.py BASELINE.fit DECONV...fit` — per-variant bright-star FWHM Δ, ring depth
  (median/worst, in RMS units), and an ADOPT/REJECT/skip verdict. Needs Python with
  `astropy`, `numpy`, `sep`, `scipy`.

## Workflow
1. **Stage the stack:** copy your stack into a workdir as `stack.fit` (linear, the output of
   the stacking skill).
2. **Run Siril:** `siril-cli -d <workdir> -s experiment.ssf`. The Siril binary on macOS:
   `siril-cli` if on PATH (Homebrew install), else
   `/Applications/Siril.app/Contents/MacOS/siril-cli` (manual app install). Seconds per variant.
3. **Measure:** `python measure_deconv.py <workdir>/stack.fit <workdir>/rl_*.fit`. Read the
   verdict.
4. **Decide:** adopt the recommended variant, or KEEP BASELINE if nothing wins cleanly.
5. **Save** the chosen file with a param-encoded name, e.g. `M6_RL_TV_10it.fit`. If a colour
   result is needed, RL output is already RGB (it deconvolves all channels with the stack PSF).

## Choosing variants (the key judgement)

| Goal | Setting | Notes |
|---|---|---|
| **Default / safe** | `rl -iters=10 -tv` | ~−5% FWHM, no ringing, the recommended operating point |
| Test if TV matters | `rl -iters=10` (no TV) | at ~10it TV barely changes anything (nothing to suppress yet) |
| More regularization | `rl -iters=10 -tv -alpha=500` | lower alpha = more smoothing; use only if a variant rings |
| See where it breaks | `rl -iters=30 -tv` | demonstrates ringing onset — usually a REJECT |

Keep iterations **low (~10)**. The FWHM gain saturates by ~10it on S30; beyond that you buy
ringing, not sharpness.

## Interpreting metrics
- **ring_worst (×RMS)** = darkest annulus (2–8 px) around bright stars, vs background.
  **The decisive metric.** Positive = stars sit above background (clean). Negative = a dark
  ring; below ~−1×RMS it is a visible black donut → REJECT, no matter how good the FWHM.
- **FWHM Δ** = bright-star sharpening, position-matched to baseline. Trust it **only** when
  ring stays clean — otherwise the "gain" is the carved core of a donut.
- **Adopt rule:** take a variant only if FWHM improves by **≥3%** AND **ring_worst ≥ −1×RMS**.
  On S30 expect ~−5% clean; if nothing clears the bar, **keep baseline** (deconv is optional).

## Common mistakes
- **Judging by FWHM alone** — the central lesson. A −30% FWHM with a black ring is worse than
  no deconvolution. Always read ring_worst.
- Using **mfdeconv / SASpro / Cosmic Clarity** on S30 — per-frame-PSF deconv rings badly;
  Cosmic Clarity is deprecated. Use Siril RL.
- **Too many iterations** — past ~10it Siril rings too. More iters ≠ better.
- **Deconvolving non-linear (stretched) data** — RL needs linear input; deconvolve before
  stretching.
- **Denoising before deconvolution** — order is stack → deconvolve → denoise → stretch.
  Denoise (GraXpert) cleans the noise RL raises; doing it first removes the detail RL needs.
- Deconvolving individual subs — never; SNR is too low, stacking handles that.
