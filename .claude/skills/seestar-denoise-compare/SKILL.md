---
name: seestar-denoise-compare
description: Use when denoising a stacked ZWO Seestar deep-sky image with GraXpert, comparing denoise strengths, measuring noise reduction against star blur and faint-detail loss objectively, and adopting the strongest clean setting. Triggers on Seestar denoise, GraXpert denoising CLI, choosing denoise strength, noise vs detail tradeoff, "plastic" over-denoised stars, denoise step before stretch.
---

# Seestar Denoise Compare

## Overview
Denoise a Seestar stack with **GraXpert**, sweep the strength, measure each result
objectively (noise reduction **vs** star blur **vs** faint-detail loss), and adopt the
strongest setting that stays clean. This is a **late step: after deconvolution, before
stretch** — denoise cleans the noise that stacking left and deconvolution raised.

**Core insight:** denoise is a **monotonic tradeoff** — more strength = more noise gone
**and** more star blur. On a deeply-stacked Seestar image the noise floor is already low, so
the blur cost overtakes the benefit quickly; a **modest strength (~0.3)** is usually the
sweet spot. The right value is data-dependent (fewer subs / noisier data justify more).

## When to use
- You have a stacked (and usually deconvolved) Seestar image and want a cleaner background.
- You want to pick a denoise strength by measurement, not by eye-balling one setting.

## Measured tradeoff (M6, 258×30s, GraXpert denoise on the linear stack)

| strength | noise drop | FWHM Δ (blur) | faint kept | verdict |
|---|---|---|---|---|
| **0.3** | **18.5%** | **+2.9%** | 0.95 | good — adopt |
| 0.5 | 30.4% | +5.5% | 0.94 | over (stars blur) |
| 0.8 | 46.2% | +9.6% | 0.92 | over (stars blur) |

Noise drop and star blur both rise monotonically; faint stars are largely kept (the cost is
blur, not lost stars). ~0.3 banks a real ~18% noise cut for <3% blur.

## Tools (in this skill dir)
- `denoise.py INPUT.fits OUTPUT.fits [STRENGTH] [--gpu]` — **the runner**: runs GraXpert
  denoise and restores the FITS header in one step, so the output is ready for plate
  solving / SPCC. Default strength 0.3, CPU. Needs `astropy`; GraXpert installed.
- `measure_denoise.py BASELINE.fit DENOISED...fit` — noise drop %, bright-star FWHM Δ (blur),
  faint-star retention; flags `OVER-DENOISED`, recommends the strongest clean setting. Needs
  `numpy`, `astropy`, `sep`, `scipy`.
- `prefs_denoise.json` — GraXpert preferences template (only needed if calling GraXpert raw).

## Workflow
1. **Sweep strengths** with the runner (each writes a header-complete FITS):
   ```
   python denoise.py <stack.fits> dn03.fit 0.3
   python denoise.py <stack.fits> dn05.fit 0.5
   python denoise.py <stack.fits> dn08.fit 0.8
   ```
   ~3-4 min/run (CPU; add `--gpu` for CoreML). The runner restores OBJECT/RA/DEC/FOCALLEN/
   XPIXSZ/FILTER… that GraXpert strips, copying them from the input stack.
2. **Measure:** `python measure_denoise.py <baseline> dn03.fit dn05.fit dn08.fit`.
3. **Adopt** the recommended (strongest noise drop with FWHM Δ < ~3% and faint kept > ~0.85).
   If even the lowest strength over-blurs, the stack is already clean enough — skip denoise.

Raw GraXpert (without the wrapper) strips the header — if you call it directly, restore it
with `python ../../../tools/restore_fits_header.py <input.fits> <out>.fits` afterwards.

## Choosing strength
- `denoise_strength`: 0.0 → 1.0. **Start ~0.3** on deeply-stacked Seestar data.
- Raise it only if the data is genuinely noisy (few subs, short integration) and the FWHM/blur
  cost stays acceptable — measure, do not assume more is better.
- The blur shows up on **stars first** (FWHM), which is why FWHM is the guard metric.

## Interpreting metrics
- **noise_drop %** = background RMS reduction. Higher = quieter, but never read it alone.
- **FWHM Δ** = bright-star blur. The guard metric: **> ~3% means over-denoised** (stars going
  soft / "plastic"), no matter how good the noise drop.
- **faint_keep** = fraction of real-ish faint stars (minarea-filtered, so noise spikes excluded)
  still detected. Below ~0.85 = faint detail being eaten.
- **Adopt rule:** strongest noise drop with FWHM Δ < ~3% AND faint_keep > ~0.85.

## Common mistakes
- **Cranking strength for the biggest noise drop** — it blurs stars (FWHM up). Read FWHM, not
  just noise_drop.
- **Denoising before deconvolution** — order is stack → deconvolve → denoise → stretch.
  Denoising first removes detail deconvolution needs, and deconvolution re-amplifies residual
  noise.
- **Denoising an already-clean deep stack hard** — on 200+ subs the noise floor is low; heavy
  denoise costs more (blur) than it gains. Skip if the lowest strength already over-blurs.
- Judging the "plastic" look by eye on one setting — sweep and measure the blur.
