# Seestar S30 processing — empirical findings

Cross-session findings behind the skills. Setup: ZWO Seestar S30 (IMX585 2.9 µm, GRBG, ~4 "/px
→ undersampled), Siril 1.4.3 CLI + GraXpert 3.1, Apple Silicon. Seestar does dark/bias
subtraction and frame rejection on-device, so the Siril preprocessing only debayers.

## 1. Stacking — `seestar-stacking-compare`

Best rejection/weighting depends on **frame count AND target type**; there is no single best
setting → sweep and measure per session (metrics: bg_sigma, bright_SNR, **faint_SNR** = primary,
in three concentric crops; registration borders are garbage).

- **Few frames (~13, C76 star field):** keep every frame; best `sigma`/`winsor 3 3` (+ optional
  `-weight=noise`). Frame weighting/filtering HURT (too little data).
- **Many frames + star field (~258, M6 open cluster):** `-weight=nbstars`/`-weight=wfwhm` win,
  mild `-filter-wfwhm` helps; ≈ +7% faint SNR vs default winsor 3 3.
- **Many frames + nebula fills frame (~406, Carina C92):** star-based weighting FAILS (bright
  extended nebula corrupts star detection) → gentle `sigma 3.5 3.5`; default winsor 3 3 also fine.
- **Globular + short subs (~136f, 10s, Omega Cen C80):** star weighting also collapsed (faint
  SNR halved, bg ×3) — dense crowding + short subs destabilize star detection → kept baseline.
- Always worst: `sigma 2 2`, `linear`, `k-MAD`. Consistently good on star fields: `percentile 0.2`.
- **Lesson:** `-weight=nbstars/wfwhm` is the most volatile knob and cannot be predicted from
  target type alone — adopt only on a measured ≥3% faint-SNR win.

## 2. Background extraction — `seestar-background-extraction-compare`

Use **GraXpert AI**. On M6: flattens background (gradient ~0.5%) and neutralizes the **colour
cast** (per-channel 4.7% → 0.1%). On S30 the dominant problem is the cast, not the gradient
(small FOV + good tracking keep gradients low ~2%; raw red cast from LP filter + IMX585).

**Siril `subsky` BACKFIRES on dense star fields** — it auto-places background samples, a crowded
cluster leaves no clean sky, so it fits a garbage plane and *adds* a 20–35% gradient. subsky is
only safe where clean samples can be placed (sparse field / open-sky nebula).

## 3. Deconvolution — `seestar-deconvolution-compare` (detail in `deconv/FINDINGS.md`)

Use **Siril RL (~10 iters, optional `-tv`)**: real, clean −5% bright-star FWHM, no ringing,
seconds. `makepsf stars` measures the PSF from the stack so it matches the ~2.8 px stars.

**Reject SASpro mfdeconv / Cosmic Clarity (deprecated).** mfdeconv estimates the PSF per-frame
(~3.4 px, wider than the stacked stars) → over-deconvolves → black rings 80–150× RMS below
background on bright stars, even at low iters. **Lesson:** judge deconvolution by **ring depth
vs background**, not FWHM alone — aggressive RL shrinks the core (good FWHM) while gouging a
dark ring (ruined image).

## 4. Denoise — `seestar-denoise-compare`

Use **GraXpert denoise**, strength **~0.3** on deep stacks. Monotonic noise↔blur tradeoff
(M6: 0.3 → −18% noise / +2.9% star FWHM; 0.5 → −30% / +5.5%; 0.8 → −46% / +9.6%). On 200+ subs
the noise floor is already low, so blur overtakes benefit fast; raise strength only on genuinely
noisy data. Cost is star blur (FWHM), not lost faint stars → FWHM is the guard metric.

## FITS headers

Siril preserves the full FITS header (OBJECT, DATE-OBS, EXPTIME, INSTRUME, TELESCOP, FOCALLEN,
XPIXSZ/YPIXSZ, RA/DEC, FILTER, GAIN…). The GPU denoise/background runner (`tools/gpu/`) also
copies the input header onto its output, so every pipeline step keeps OBJECT/RA/DEC/FOCALLEN/
XPIXSZ/FILTER. (GraXpert's own CLI strips the header to NAXIS — another reason the pipeline uses
the in-house runner instead.) The Seestar header has RA/DEC + FOCALLEN + XPIXSZ but no WCS —
enough to seed a plate solve.

## Pipeline order

`stack → background extraction → deconvolution → denoise → stretch`. Deconvolution and denoise
run on **linear** data; denoise comes **after** deconvolution (deconv raises noise, denoise
cleans it; denoising first removes detail deconv needs).

Run all steps at once with the `/seestar-pipeline` command (agent orchestrator): it auto-picks
each step's parameters by measurement and stops for the user only on doubtful cases (deconv
rings, backfired background, volatile star-weighted stack).
