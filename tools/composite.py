#!/usr/bin/env python
"""LP + IRCUT composite for Seestar masters: WCS-align, then optional HaRGB.

The Seestar S30 Pro carries two switchable filters: the dual-band LP (Ha 656 nm
+ OIII ~500 nm) and a broadband UV/IR cut (IRCUT). The two are spectrally
incompatible inside one stack (normalization, rejection and SPCC all break), so
each filter gets its own pipeline run and its own master. This tool combines
the two masters after the fact:

  align (default)  reproject the IRCUT master onto the LP master's pixel grid
                   (WCS-based; both must be plate-solved). The aligned
                   broadband master is the natural-star-colour layer for star
                   recomposition over a starless LP/HOO stretch — the LP
                   filter guts the stellar continuum, IRCUT keeps it honest.
  hargb            align, then continuum-subtract Ha and blend it into the
                   broadband red channel:
                     Ha = LP_R - k * IRCUT_R    (k = median flux ratio on the
                                                 brightest continuum pixels)
                     R = IRCUT_R + w * Ha,  G/B = IRCUT G/B
                   Also writes the continuum-subtracted Ha as a mono FITS.

All outputs are linear float32 with the LP master's header + WCS intact
(the aligned IRCUT master keeps FILTER=IRCUT). Verdict lines are parseable:

  COMPOSITE: ALIGN (coverage=96.4%)
  COMPOSITE: HARGB (k=0.702, ha_weight=1, coverage=96.4%)

Usage:
  composite.py LP_MASTER.fit IRCUT_MASTER.fit [--mode align|hargb]
      [--outdir DIR] [--basename NAME] [--ha-weight W]
"""
import argparse
import os
import sys
import warnings

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning
from astropy.wcs.utils import pixel_to_pixel
from scipy.ndimage import map_coordinates


def load_rgb_master(path):
    """Load a linear RGB FITS as float64 (3,H,W) + a copy of its header."""
    with fits.open(path, memmap=False, ignore_missing_simple=True) as hdul:
        data = hdul[0].data
        header = hdul[0].header.copy()
    if data is None or data.ndim != 3 or data.shape[0] != 3:
        shape = None if data is None else data.shape
        sys.exit(f"{path}: composite needs an RGB master (3,H,W), got shape {shape}")
    return data.astype(np.float64), header


def celestial_wcs(header, path):
    """Celestial WCS from a master header; error out if not plate-solved."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        # naxis=2: Siril writes SIP keywords on RGB cubes (NAXIS=3), and
        # WCSLIB refuses SIP+3D unless reduced to the celestial axes
        w = WCS(header, naxis=2)
    if not w.has_celestial:
        sys.exit(f"{path}: no celestial WCS — plate-solve both masters first "
                 "(pipeline Step 8)")
    return w.celestial


def reproject_to(ir_rgb, ir_wcs, lp_wcs, out_shape):
    """Sample the IRCUT cube on the LP pixel grid (bilinear); NaN outside.

    Returns (aligned (3,H,W) with NaN where the IRCUT frame has no coverage,
    coverage fraction of the LP grid).
    """
    h, w = out_shape
    xx, yy = np.meshgrid(np.arange(w, dtype=np.float64),
                         np.arange(h, dtype=np.float64))
    ix, iy = pixel_to_pixel(lp_wcs, ir_wcs, xx, yy)
    aligned = np.empty((3, h, w))
    for c in range(3):
        aligned[c] = map_coordinates(ir_rgb[c], [iy, ix], order=1,
                                     mode="constant", cval=np.nan)
    coverage = float(np.isfinite(aligned[0]).mean())
    return aligned, coverage


def estimate_k(lp_r, ir_r):
    """Continuum scale k for Ha = LP_R - k*IRCUT_R.

    Stars are continuum sources: their red flux in both filters is continuum,
    so the median LP_R/IRCUT_R over the brightest IRCUT pixels (continuum-
    dominated by construction — emission regions are dim in broadband) is the
    scale that cancels the continuum. Backgrounds are subtracted first.
    """
    lp = lp_r - float(np.nanmedian(lp_r))
    ir = ir_r - float(np.nanmedian(ir_r))
    madn = float(np.nanmedian(np.abs(ir - np.nanmedian(ir))) * 1.4826)
    mask = np.isfinite(lp) & np.isfinite(ir) & (ir > 10 * madn) & (lp > 0)
    n_mask = int(mask.sum())
    if n_mask < 30:
        sys.exit("hargb: not enough bright continuum pixels to fit k "
                 f"({n_mask} above 10 sigma) — is the IRCUT master empty?")
    # keep only the brightest slice so faint-tail noise cannot drag the median
    n = min(n_mask, max(30, int(0.002 * ir.size)))
    lp_m, ir_m = lp[mask], ir[mask]
    sel = np.argpartition(ir_m, -n)[-n:]
    return float(np.median(lp_m[sel] / ir_m[sel]))


def continuum_subtract(lp_r, ir_r_aligned, k):
    """Ha = LP_R - k*IRCUT_R, backgrounds neutralized, NaN (no coverage) -> 0."""
    ha = (lp_r - float(np.nanmedian(lp_r))) \
        - k * (ir_r_aligned - float(np.nanmedian(ir_r_aligned)))
    ha = ha - float(np.nanmedian(ha))
    return np.nan_to_num(ha, nan=0.0)


def write_master(path, cube, header, formula):
    """Write a linear float32 master, header + WCS intact."""
    hdr = header.copy()
    hdr.add_history(f"composite.py: {formula}")
    fits.writeto(path, np.clip(cube, 0.0, None).astype(np.float32), hdr,
                 overwrite=True)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("lp_master", help="plate-solved LP (dual-band) RGB master")
    ap.add_argument("ircut_master", help="plate-solved IRCUT (broadband) RGB master")
    ap.add_argument("--mode", choices=["align", "hargb"], default="align",
                    help="align: star-colour layer only; hargb: + continuum-"
                         "subtracted Ha and HaRGB blend")
    ap.add_argument("--outdir", default=None, help="output dir (default: next to the LP master)")
    ap.add_argument("--basename", default=None,
                    help="output name stem (default: LP master filename stem)")
    ap.add_argument("--ha-weight", type=float, default=1.0,
                    help="weight of continuum-subtracted Ha added to R in hargb (default 1.0)")
    args = ap.parse_args(argv)

    lp_rgb, lp_hdr = load_rgb_master(args.lp_master)
    ir_rgb, ir_hdr = load_rgb_master(args.ircut_master)

    # argument-order guard: the FILTER keyword survives the whole pipeline
    lp_filt = str(lp_hdr.get("FILTER", "")).strip().upper()
    ir_filt = str(ir_hdr.get("FILTER", "")).strip().upper()
    if lp_filt == "IRCUT" or ir_filt == "LP":
        sys.exit("arguments look swapped: first the LP master, then the IRCUT master "
                 f"(got FILTER={lp_filt or 'n/a'} / {ir_filt or 'n/a'})")

    lp_wcs = celestial_wcs(lp_hdr, args.lp_master)
    ir_wcs = celestial_wcs(ir_hdr, args.ircut_master)

    aligned, coverage = reproject_to(ir_rgb, ir_wcs, lp_wcs, lp_rgb.shape[1:])

    outdir = args.outdir or os.path.dirname(os.path.abspath(args.lp_master))
    os.makedirs(outdir, exist_ok=True)
    base = args.basename or os.path.splitext(os.path.basename(args.lp_master))[0]

    if args.mode == "align":
        print(f"COMPOSITE: ALIGN (coverage={coverage * 100:.1f}%)")
    aligned_hdr = lp_hdr.copy()
    if ir_filt:
        aligned_hdr["FILTER"] = ir_filt
    aligned_path = os.path.join(outdir, f"{base}_IRCUT_aligned.fit")
    write_master(aligned_path, np.nan_to_num(aligned, nan=0.0), aligned_hdr,
                 "IRCUT master reprojected onto the LP master's WCS grid, linear")
    print(f"wrote: {aligned_path}")

    if args.mode != "hargb":
        return

    k = estimate_k(lp_rgb[0], aligned[0])
    print(f"COMPOSITE: HARGB (k={k:.3f}, ha_weight={args.ha_weight:g},"
          f" coverage={coverage * 100:.1f}%)")
    ha = continuum_subtract(lp_rgb[0], aligned[0], k)

    ha_path = os.path.join(outdir, f"{base}_Ha.fit")
    write_master(ha_path, ha, lp_hdr,
                 f"Ha = LP_R - {k:.3f}*IRCUT_R, continuum-subtracted, linear")
    print(f"wrote: {ha_path}")

    ir_filled = np.nan_to_num(aligned, nan=0.0)
    hargb = np.stack([ir_filled[0] + args.ha_weight * ha, ir_filled[1], ir_filled[2]])
    hargb_path = os.path.join(outdir, f"{base}_HaRGB.fit")
    write_master(hargb_path, hargb, lp_hdr,
                 f"HaRGB: R=IRCUT_R+{args.ha_weight:g}*Ha, G/B=IRCUT, linear")
    print(f"wrote: {hargb_path}")


if __name__ == "__main__":
    main()
