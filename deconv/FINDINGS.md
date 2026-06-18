# Deconvolution findings — Seestar S30 (undersampled data)

Research record behind the `seestar-deconvolution-compare` skill. Conclusion:
**deconvolve with Siril RL (~10 iterations); reject SASpro mfdeconv / Seti tools.**

## Setup

- Target: M6 open cluster, 258 × 30 s registered subs (`r_pp_light_*.fit`, RGB).
- S30 sampling: pixel 2.9 µm @ FL 150 mm → ~4 "/px. Stars land on ~2.8 px FWHM in
  the stack → **undersampled** (below Nyquist). Little real blur to remove.
- Measurement on luminance: median bright-star FWHM (position-matched to baseline,
  KD-tree ≤3 px) **and** ring depth = darkest annulus (2–8 px) around bright stars,
  in units of background RMS. The ring-vs-background metric is the one that matters.

## What we tested

### SASpro mfdeconv (multi-frame robust Richardson-Lucy) — REJECTED

Ran headless from vendored source (MPS) at 4/6/7/8/9/10/30 iterations.

| iters | bright FWHM Δ | noise ×base | ring trough vs background |
|------:|---:|---:|---:|
| 4 (early-stop) | ~0 | ×1.16 | above bg (clean) |
| 6 | −29% | ×1.22 | **−80×rms** |
| 7 | −28% | ×1.26 | **−84×rms** |
| 10 | −32% | ×1.38 | **−102×rms** |
| 30 | −42% | ×1.95 | **−117×rms** |

The FWHM "improvement" is an artifact: from iter 6 onward a hard black ring is carved
around ~all bright stars (trough 80–150× RMS **below** background → clips to black). The
FWHM metric rewarded the narrow carved core without penalizing the ring. **Visual
inspection caught what FWHM-only missed.**

Root cause: mfdeconv estimates the PSF **per frame** (~3.4 px) which is *wider* than the
stars already present in the stack (~2.8 px) → it removes more blur than exists →
overshoot → Gibbs ringing. It also has no spatial regularization (Huber is only on
cross-frame residuals). The default early-stop halts at iter 4 — before any sharpening —
so out-of-box it is a no-op; pushed past that, it rings.

### Siril RL (`makepsf stars` + `rl`) — ADOPTED

PSF measured from the **stack itself** → matches the real ~2.8 px stars.

| variant | bright FWHM Δ | ring trough vs background |
|---|---:|---:|
| baseline | 0 | +6.0×rms (above bg) |
| RL noTV 10it | −5.3% | +3.8×rms (clean) |
| RL TV α3000 10it | −5.3% | +3.9×rms (clean) |
| RL TV α500 10it | −5.2% | +4.1×rms (clean) |
| RL TV α3000 30it | −11.3% | −6.9×rms (rings appear) |

At ~10 iterations: a real, modest −5% bright-star FWHM with **no ringing** (trough stays
above background), in seconds. TV barely matters at 10it (nothing to suppress yet); it is
cheap insurance if iterations are pushed. Beyond ~10it Siril rings too, but ~15× milder
than mfdeconv.

## Pipeline verdict for S30

| task | tool |
|---|---|
| Deconvolution | **Siril RL ~10it (+TV insurance)** |
| Denoise | GraXpert (headless) — after deconvolution |
| Starless | SyQon |
| Stacking | Siril (`seestar-stacking-compare`) |

- **mfdeconv / SASpro / Cosmic Clarity: not used.** Deconv harmful (ringing); Cosmic
  Clarity deprecated.
- The honest ceiling on undersampled S30 data is ~−5% FWHM, clean. Deconvolution is a
  minor polish here, not a transformative step — measure per session and skip if it does
  not beat baseline cleanly.
- Lesson encoded in the skill: **always measure ring-vs-background, not FWHM alone.**
