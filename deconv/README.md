# deconv — headless multi-frame deconvolution (mfdeconv)

Self-contained runner for SASpro's multi-frame robust Richardson-Lucy deconvolution,
adapted to run from the CLI on Apple Silicon (MPS) without the SASpro GUI.
See `NOTICE.md` for what is vendored and the upstream license (GPL-3.0).

## Setup (one time)

```bash
cd deconv
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt   # torch is the long pole (~minutes)
```

Needs Python 3.13 and an Apple-Silicon Mac (uses the `mps` torch backend; falls back
to CPU otherwise).

## Run a deconvolution

```bash
# multi-frame: jointly deconvolve registered (r_pp_*) sub-frames
.venv/bin/python mf_deconv.py OUT.fit "/path/to/process/r_pp_light_*.fit" 20
# force the full iteration count (default early-stop halts ~4 iters on undersampled data)
.venv/bin/python mf_deconv.py OUT.fit "/path/to/process/r_pp_light_*.fit" 30 --noearly
# single frame
.venv/bin/python mf_deconv.py OUT.fit /path/to/stack.fit 15
```

Output is a luminance image prefixed `MFDeconv_` in the OUT directory. `--color rgb`
deconvolves per channel (risks small color shifts; luma is the safe default).

### Tuning early-stop from the CLI (no source edits)

The upstream `EarlyStopper` halts when the per-iter update drops below
`early_frac` (0.40) of the first iteration, for `patience` (2) iters in a row, never
before `min_iters` (3). On undersampled data the default fires at ~iter 4 — before any
sharpening accumulates. Tune it without touching code:

```bash
# let it run longer before stopping (sweep the sweet spot between null and over-sharpened)
.venv/bin/python mf_deconv.py OUT.fit "...r_pp_*.fit" 30 --early-frac 0.15 --patience 3
# hard disable early-stop -> exactly `iters`
.venv/bin/python mf_deconv.py OUT.fit "...r_pp_*.fit" 10 --noearly
```

Lower `--early-frac` => runs longer. All adaptations are runtime shims in `mf_deconv.py`;
the vendored source in `vendor/` is never modified.

## Measure (did it actually help?)

```bash
# absolute: deconv vs a grid-matched plain mean of the SAME frames
.venv/bin/python mf_measure.py BASELINE.fit DECONV.fit
# position-matched bright-star FWHM (the trustworthy metric)
.venv/bin/python mf_compare.py BASELINE.fit DECONV.fit 300
```

`mf_compare.py` matches stars by pixel position (KD-tree, ≤3 px) and reports the median
FWHM change on the brightest matched stars. **The baseline MUST be on the same
registration grid** as the deconv output — use a mean stack of the same `r_pp_*` frames,
not an independently-registered Siril stack (different grid → almost no matches).

## Key finding

On undersampled Seestar data (star FWHM ~2–3 px) mfdeconv's default early-stop halts at
~4 iterations and produces **no measurable FWHM gain** (M6, 258 frames: +0.4%). Forcing
more iterations is what to test next — and to watch for ringing. Optimal iteration count
is target- and SNR-dependent (more frames → higher SNR → more iterations tolerated).
