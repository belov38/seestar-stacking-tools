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

## 3. Deconvolution — `seestar-deconvolution-compare`

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

## 5. Palette gate (HOO) — `tools/palette.py`

The LP filter is dual-band (Ha 656 nm + OIII ~500 nm): Ha lives in R, OIII in G+B, so every
emission target carries a free HOO palette. **HOO only:** the filter passes no SII (672 nm),
so a "SHO" from this data can only synthesize its S channel out of Ha — a colour remap with
zero new information. We emitted a synthetic SHO until 2026-07-04; dropped per Ilia — the
honest dual-band palette is HOO. The EMIT/SKIP gate = normalized MAD of
log2(Ha/OIII) over the signal mask, **stars suppressed first** (2×2 bin + median 9, mask
thresholded at 3× the *pixel* noise of the unsuppressed map).

- **Star suppression is mandatory:** on raw star pixels the M6 open cluster scores **0.699** —
  HIGHER than the Tarantula (0.462) — because star-colour diversity (stellar temperatures)
  fakes emission separation. Point sources are not extended emission.
- Measured (suppressed metric): emission C103 **0.316** / M17 **0.335** / M8 **0.419** → EMIT;
  continuum M6 open cluster **0.167** / C80 globular **0.131** → SKIP.
- Threshold **0.23** = geometric mean of the nearest classes (M6 0.167 ↔ C103 0.316).
- Mask erosion was tried and rejected: it narrows the real-data gap (M17 0.335 → 0.227,
  nearly touching the continuum class) while only helping synthetic edge cases.

## 6. LP vs IRCUT — two-filter datasets

The S30 Pro switches between two filters, and every sub is tagged (`_LP_`/`_IRCUT_` in the
filename, `FILTER` in the header — the keyword survives all the way into the Siril master).
**Never stack the two together:** they are spectrally incompatible in one stack — per-frame
normalization fits a different sky, rejection sees the other filter's frames as outliers, and
SPCC has no profile for the mixture. The pipeline routes them into separate runs (Step 1) and
keeps two masters.

- **SPCC profiles:** LP → `ZWO Seestar LP`; IRCUT → the generic `UV/IR Block` profile (the
  SPCC DB has no Seestar-specific IRCUT entry and needs none — the IRCUT position is a plain
  UV/IR window). AstroBin filter IDs: LP 40954, IRCUT 42307.
- **Palette is LP-only:** on broadband data R vs G+B is not Ha vs OIII, yet Ha still lands in
  R — an emission target can *fake* a plausible separation score, so the gate must not be
  trusted there; `palette.py` hard-skips any master whose `FILTER` is not LP.
- **Combine after the fact** (`tools/composite.py`, both masters plate-solved): WCS-reproject
  IRCUT onto the LP grid. ~40 min of IRCUT already makes a **natural-star-colour layer**
  (stars need little SNR; LP guts stellar continuum — LP star cores come out Ha-red).
  A continuum-subtracted **HaRGB** (`Ha = LP_R − k·IRCUT_R`, k = median flux ratio on bright
  continuum pixels) needs **hours** of IRCUT — the broadband base must stand on its own.

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

## Plate-solving dense fields (NOMAD vs Gaia)

Siril's default NOMAD catalog failed to solve the 533-frame IRCUT stack of NGC 292 twice
("The image could not be aligned with the reference stars", 381 detected vs 3211 catalog
stars), even seeded with correct header RA/DEC + focal + pixel size. `platesolve
-catalog=gaia -downscale` solved it on the first try (4654 Gaia DR3 stars via Vizier).
On dense fields — Magellanic star clouds, rich clusters — go straight to Gaia when NOMAD
fails. Siril writes SIP distortion keywords with the solution on an RGB cube (NAXIS=3);
astropy's `WCS(header)` refuses SIP+3D — construct with `WCS(header, naxis=2)`
(`tools/composite.py` does).

## HOO renders all-red on Ha-dominant targets — emit mono Ha/OIII channels

Measured on C92 (Carina, 473 subs / 3.4 h LP): after SPCC the Ha/OIII flux ratio is ~1.58 over
the nebula and ~1.12 in the bright core — OIII signal is clearly present, yet the HOO cube
autostretches uniformly red/pink. Cause: a linked stretch applies one curve to all channels,
and with Ha ahead of OIII *everywhere* (typical for HII regions), teal never wins a pixel.
The prominent blue OIII in published Carina HOO images comes from processing — the O channel
is stretched separately (unlinked) to match Ha, or LinearFit/pixel-math boosted while linear,
then selectively saturated. So the HOO cube alone is not a finished deliverable for emission
targets: `tools/palette.py` therefore also writes mono `*_Ha.fit` and `*_OIII.fit` masters
(linear, bg-neutralized to the HOO pedestal, header + WCS intact) for exactly that manual
unlinked composition.
