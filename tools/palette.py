#!/usr/bin/env python
"""Dual-band palette masters (HOO / synthetic SHO) from a Seestar LP-filter RGB master.

The Seestar LP filter is dual-band: it passes only Ha (656 nm) and OIII (~500 nm).
On the IMX585 OSC sensor the red channel carries Ha; green and blue carry OIII
(green leaks ~10-15% of Ha). This tool splits a linear RGB master into Ha/OIII,
measures whether the target actually shows emission-line separation, and (on EMIT)
writes linear, stretch-ready palette masters with the input header + WCS intact:

  <base>_HOO.fit   R=Ha, G=OIII, B=OIII               (red hydrogen / teal oxygen)
  <base>_SHO.fit   R=Ha, G=a*Ha+(1-a)*OIII, B=OIII    (synthetic Hubble-style; a=0.3)

Continuum targets (galaxies, clusters) have Ha proportional to OIII everywhere, so
the log-ratio spread over signal pixels is small -> SKIP (a palette of a continuum
target is just the same grey image twice). Verdict line (parseable, exit code 0):

  PALETTES: EMIT (separation=0.421, threshold=0.15)
  PALETTES: SKIP (separation=0.062, threshold=0.15)

Usage:
  palette.py MASTER.fit [--outdir DIR] [--basename NAME] [--sho-alpha 0.3]
             [--force] [--metric-only]
"""
import argparse
import os
import sys

import numpy as np
from astropy.io import fits


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


# Emission-separation threshold: normalized MAD of log2(Ha/OIII) over signal pixels.
# Initial value; calibrated on real Seestar data (C103 Tarantula SPCC master must
# EMIT, C76 open-cluster stack must SKIP) — measured values in FINDINGS.md.
SEPARATION_THRESHOLD = 0.15

# Signal mask needs at least this many usable pixels for a meaningful spread.
MIN_MASK_PIXELS = 100


def _median_madn(x):
    med = float(np.median(x))
    madn = float(np.median(np.abs(x - med)) * 1.4826)
    return med, madn


def emission_separation(ha, oiii):
    """Normalized MAD of log2(Ha/OIII) over signal pixels, or None if degenerate.

    Continuum sources (stars, galaxies) have Ha proportional to OIII everywhere,
    so the ratio spread is small; emission targets diverge region by region.
    """
    combined = ha + oiii
    med_c, madn_c = _median_madn(combined)
    if madn_c <= 0:
        return None
    mask = combined > med_c + 3.0 * madn_c
    if int(mask.sum()) < MIN_MASK_PIXELS:
        return None
    med_ha, _ = _median_madn(ha)
    med_oiii, _ = _median_madn(oiii)
    ha_sig = ha[mask] - med_ha
    oiii_sig = oiii[mask] - med_oiii
    valid = (ha_sig > 0) & (oiii_sig > 0)
    if int(valid.sum()) < MIN_MASK_PIXELS:
        return None
    ratio = np.log2(ha_sig[valid] / oiii_sig[valid])
    _, spread = _median_madn(ratio)
    return spread
