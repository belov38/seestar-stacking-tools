#!/usr/bin/env python
"""Dual-band Ha/OIII channel split from a Seestar LP-filter RGB master.

The Seestar LP filter is dual-band: it passes only Ha (656 nm) and OIII (~500 nm).
On the IMX585 OSC sensor the red channel carries Ha; green and blue carry OIII
(green leaks ~10-15% of Ha). This tool splits a linear RGB master into Ha/OIII,
measures whether the target actually shows emission-line separation, and (on EMIT)
writes the two linear, stretch-ready mono masters with the input header + WCS intact:

  <base>_Ha.fit     mono Ha channel   (from R)
  <base>_OIII.fit   mono OIII channel (from (G+B)/2)

Only the mono channels are emitted — no combined HOO cube. A linked stretch of an
R=Ha,G=B=OIII cube renders Ha-dominant targets (e.g. Carina, Ha/OIII ~3x) uniformly
red and is useless. The teal OIII colour is a non-linear, star-sensitive step: the
user stretches each mono channel, removes stars (StarNet++/StarXTerminator), and
feeds the two starless channels to hoo_recombine.py, which does LinearFit + a
dynamic green blend + SCNR. The channels here are bg-neutralized to a common
pedestal so that recombination is consistent.

HOO is the only honest palette for dual-band data: there is no SII line in the
filter, so an "SHO" would have to synthesize its S channel out of Ha — zero new
information, just a colour remap. We used to emit one; dropped by decision.

LP masters only: on a broadband (IRCUT) master the R vs G+B split is not
Ha vs OIII — yet Ha still lands in R, so an emission target can fake a
plausible separation score. A master whose FILTER header is not LP is
therefore hard-skipped, and --force does not override (there is nothing
physically meaningful to write).

The EMIT/SKIP gate measures the log2(Ha/OIII) spread over the extended (star-
suppressed) signal: emission targets diverge region by region, while continuum
targets (clusters, galaxies) stay proportional -> SKIP (a palette of a continuum
target is just the same grey image twice). Stars are suppressed first because
star-colour diversity fakes separation. Verdict line (parseable, exit code 0):

  PALETTES: EMIT (separation=0.316, threshold=0.23)
  PALETTES: SKIP (separation=0.167, threshold=0.23)

Usage:
  palette.py MASTER.fit [--outdir DIR] [--basename NAME] [--force] [--metric-only]
"""
import argparse
import os
import sys

import numpy as np
from astropy.io import fits
from scipy.ndimage import median_filter


def load_rgb_master(path):
    """Load a linear RGB FITS as float64 (3,H,W) + a copy of its header."""
    with fits.open(path, memmap=False, ignore_missing_simple=True) as hdul:
        data = hdul[0].data
        header = hdul[0].header.copy()
    if data is None or data.ndim != 3 or data.shape[0] != 3:
        shape = None if data is None else data.shape
        sys.exit(f"{path}: palette extraction needs an RGB master (3,H,W), got shape {shape}")
    return data.astype(np.float64), header


def extract_ha_oiii(rgb):
    """Dual-band split: red pixels see Ha, green+blue see OIII."""
    ha = rgb[0]
    oiii = 0.5 * rgb[1] + 0.5 * rgb[2]
    return ha, oiii


def neutralize_background(ha, oiii):
    """Subtract each channel's own median background, re-add a common pedestal.

    Keeps the data linear and the structure untouched, but makes the background
    neutral grey so the composites carry no colour cast.
    """
    bg_ha = float(np.median(ha))
    bg_oiii = float(np.median(oiii))
    pedestal = 0.5 * (bg_ha + bg_oiii)
    return ha - bg_ha + pedestal, oiii - bg_oiii + pedestal, pedestal


# Emission-separation threshold: normalized MAD of log2(Ha/OIII) over the
# star-suppressed signal mask. Calibrated on real Seestar masters (FINDINGS.md):
# emission C103 0.316 / M17 0.335 / M8 0.419 (EMIT) vs continuum M6 open cluster
# 0.167 / C80 globular 0.131 (SKIP); threshold = geometric mean of the two
# nearest classes, sqrt(0.167 * 0.316) ~= 0.23.
SEPARATION_THRESHOLD = 0.23

# Signal mask needs at least this many (binned) pixels for a meaningful spread.
MIN_MASK_PIXELS = 100

# Star suppression before the metric: 2x2 mean bin + median filter (~18 px
# full-res window, above Seestar star FWHM, far below nebula scales).
SUPPRESS_MEDIAN_SIZE = 9


def _median_madn(x):
    med = float(np.median(x))
    madn = float(np.median(np.abs(x - med)) * 1.4826)
    return med, madn


def _bin2(x):
    """2x2 mean bin."""
    h, w = x.shape
    return x[: h // 2 * 2, : w // 2 * 2].reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))


def emission_separation(ha, oiii):
    """Normalized MAD of log2(Ha/OIII) over star-suppressed signal pixels.

    Returns None when no extended signal survives suppression (degenerate mask).
    Stars are suppressed first because a star field's colour diversity (different
    stellar temperatures) fakes a large ratio spread without any emission — on a
    raw star-pixel mask the M6 open cluster scores HIGHER than the Tarantula.
    Only the extended structure left after suppression is evidence of Ha/OIII
    emission separation. The signal mask thresholds the suppressed map against
    the PIXEL noise of the unsuppressed (binned) map — the suppressed map is so
    smooth that its own MAD collapses and star-suppression residuals would leak
    back into the mask.
    """
    ha_b = _bin2(ha)
    oiii_b = _bin2(oiii)
    ha_s = median_filter(ha_b, size=SUPPRESS_MEDIAN_SIZE)
    oiii_s = median_filter(oiii_b, size=SUPPRESS_MEDIAN_SIZE)
    # noise floor from the unsuppressed binned map; mask on the suppressed one
    _, madn_pixel = _median_madn(ha_b + oiii_b)
    if madn_pixel <= 0:
        return None
    combined_s = ha_s + oiii_s
    med_s, _ = _median_madn(combined_s)
    mask = combined_s > med_s + 3.0 * madn_pixel
    if int(mask.sum()) < MIN_MASK_PIXELS:
        return None
    med_ha, _ = _median_madn(ha_s)
    med_oiii, _ = _median_madn(oiii_s)
    ha_sig = ha_s[mask] - med_ha
    oiii_sig = oiii_s[mask] - med_oiii
    valid = (ha_sig > 0) & (oiii_sig > 0)
    if int(valid.sum()) < MIN_MASK_PIXELS:
        return None
    ratio = np.log2(ha_sig[valid] / oiii_sig[valid])
    _, spread = _median_madn(ratio)
    return spread


def write_master(path, cube, header, formula):
    """Write a linear float32 channel master, input header + WCS intact."""
    hdr = header.copy()
    hdr.add_history(f"palette.py: {formula}")
    fits.writeto(path, np.clip(cube, 0.0, None).astype(np.float32), hdr, overwrite=True)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("master", help="linear RGB FITS master (e.g. the SPCC output)")
    ap.add_argument("--outdir", default=None, help="output dir (default: next to the input)")
    ap.add_argument("--basename", default=None,
                    help="output name stem (default: input filename stem)")
    ap.add_argument("--force", action="store_true",
                    help="write palette masters even on a SKIP verdict")
    ap.add_argument("--metric-only", action="store_true",
                    help="print the verdict only, write nothing")
    args = ap.parse_args(argv)

    rgb, header = load_rgb_master(args.master)
    filt = str(header.get("FILTER", "")).strip().upper()
    if filt and filt != "LP":
        print(f"PALETTES: SKIP (filter={filt} — dual-band split needs the LP filter;"
              " a broadband master has no Ha/OIII channels)")
        return
    ha, oiii = extract_ha_oiii(rgb)
    separation = emission_separation(ha, oiii)
    if separation is None:
        emit = False
        print(f"PALETTES: SKIP (separation=n/a, threshold={SEPARATION_THRESHOLD:g})"
              " — no usable signal mask")
    else:
        emit = separation >= SEPARATION_THRESHOLD
        verdict = "EMIT" if emit else "SKIP"
        print(f"PALETTES: {verdict} (separation={separation:.3f},"
              f" threshold={SEPARATION_THRESHOLD:g})")

    if args.metric_only or (not emit and not args.force):
        return

    outdir = args.outdir or os.path.dirname(os.path.abspath(args.master))
    os.makedirs(outdir, exist_ok=True)
    base = args.basename or os.path.splitext(os.path.basename(args.master))[0]
    ha_n, oiii_n, _ = neutralize_background(ha, oiii)

    ha_path = os.path.join(outdir, f"{base}_Ha.fit")
    write_master(ha_path, ha_n.astype(np.float32), header,
                 "Ha: mono, from R, bg-neutralized, linear")
    print(f"wrote: {ha_path}")

    oiii_path = os.path.join(outdir, f"{base}_OIII.fit")
    write_master(oiii_path, oiii_n.astype(np.float32), header,
                 "OIII: mono, from (G+B)/2, bg-neutralized, linear")
    print(f"wrote: {oiii_path}")


if __name__ == "__main__":
    main()
