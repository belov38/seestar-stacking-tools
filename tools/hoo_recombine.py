#!/usr/bin/env python
"""Teal HOO recombine from user-made STARLESS Ha + OIII (nonlinear).

The pipeline emits linear mono Ha/OIII masters (palette.py). The user stretches
each and removes stars (StarNet++/StarXTerminator) in their own tool, then feeds
the two starless mono images back here. This tool does the emission-line colour
step that is inherently non-linear and star-sensitive, so it lives after the
manual starless handoff:

  1. LinearFit Ha to the OIII reference (align background/scale so the dynamic
     weight O*Ha is meaningful; ref=OIII keeps weak OIII from being swallowed).
  2. Mild blur on OIII (chroma tolerates blur; kills the boosted-OIII colour
     noise that otherwise appears when a 2-3x weaker channel is lifted).
  3. Optional OIII boost coefficient.
  4. Dynamic green blend (Bill Blanshan / telescope.live "dynamic HOO"):
       w = (O*Ha)^(1-O*Ha)
       R = Ha
       G = w*Ha + (1-w)*O        # OIII where the signal is weak, Ha where strong
       B = O
     This shows teal only where OIII is really present, instead of a flat
     G=OIII that greys the whole frame.
  5. SCNR green (average-neutral) so residual green does not tint the result.

Inputs are the STRETCHED starless mono channels (values ~[0,1]); this tool does
not stretch — the user owns the stretch. Output is an 8-bit-ready RGB FITS
(header/WCS from the Ha input) plus a PNG.

Usage:
  hoo_recombine.py HA_STARLESS.fit OIII_STARLESS.fit [--out OUT.fit]
      [--oiii-boost 1.0] [--oiii-blur 1.0] [--no-linfit] [--no-scnr]
"""
import argparse
import os
import sys

import numpy as np
from astropy.io import fits
from scipy.ndimage import gaussian_filter


def load_mono(path):
    """Load a 2D mono FITS as float64 + its header. Reject RGB cubes."""
    with fits.open(path, memmap=False, ignore_missing_simple=True) as hdul:
        data = hdul[0].data
        header = hdul[0].header.copy()
    if data is None:
        sys.exit(f"{path}: no image data")
    data = np.asarray(data, dtype=np.float64)
    if data.ndim == 3 and data.shape[0] == 1:
        data = data[0]
    if data.ndim != 2:
        sys.exit(f"{path}: recombine needs a 2D mono channel, got shape {data.shape} "
                 "(feed the starless Ha and OIII separately, not an RGB cube)")
    return data, header


def normalize01(x):
    """Robust scale to [0,1] on the 99.9th percentile (data is already stretched)."""
    lo = float(np.nanmin(x))
    hi = float(np.nanpercentile(x, 99.9))
    if hi <= lo:
        hi = float(np.nanmax(x))
    if hi <= lo:
        return np.zeros_like(x)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def linear_fit(target, ref):
    """Least-squares match target onto ref's scale: target' = a*target + b.

    Fit over the shared signal (either channel above a low pedestal) so the
    background does not dominate the line. ref=OIII keeps the weak channel from
    collapsing under Ha in the subsequent blend.
    """
    mask = (target > 0.05) | (ref > 0.05)
    if int(mask.sum()) < 100:
        mask = np.ones_like(target, dtype=bool)
    t = target[mask].ravel()
    r = ref[mask].ravel()
    a, b = np.polyfit(t, r, 1)
    if not np.isfinite(a) or a <= 0:
        a, b = 1.0, 0.0
    return np.clip(a * target + b, 0.0, 1.0), float(a), float(b)


def dynamic_hoo(ha, oiii):
    """Dynamic HOO blend on [0,1] channels -> (R,G,B).

    w = (O*Ha)^(1-O*Ha) weights toward Ha where the product is large (strong
    overlapping signal) and toward OIII where it is small (weak OIII survives).
    """
    prod = np.clip(ha * oiii, 0.0, 1.0)
    w = np.power(prod, 1.0 - prod)
    g = w * ha + (1.0 - w) * oiii
    return ha, g, oiii


def scnr_green_average(r, g, b):
    """Average-neutral SCNR: clamp green to the R/B average so it cannot tint."""
    return np.minimum(g, 0.5 * (r + b))


def recombine(ha, oiii, oiii_boost=1.0, oiii_blur=1.0, do_linfit=True, do_scnr=True):
    """Full pipeline; returns (rgb (3,H,W) float32, info dict)."""
    if ha.shape != oiii.shape:
        sys.exit(f"shape mismatch: Ha {ha.shape} vs OIII {oiii.shape}")
    H = normalize01(ha)
    O = normalize01(oiii)
    a, b = 1.0, 0.0
    if do_linfit:
        H, a, b = linear_fit(H, O)  # match Ha onto the OIII reference
    if oiii_blur > 0:
        O = gaussian_filter(O, sigma=oiii_blur)
    if oiii_boost != 1.0:
        O = np.clip(O * oiii_boost, 0.0, 1.0)
    r, g, bl = dynamic_hoo(H, O)
    if do_scnr:
        g = scnr_green_average(r, g, bl)
    rgb = np.stack([r, g, bl]).astype(np.float32)
    return np.clip(rgb, 0.0, 1.0), {"a": a, "b": b, "blur": oiii_blur, "boost": oiii_boost,
                                    "linfit": do_linfit, "scnr": do_scnr}


def save_png(rgb, path):
    from PIL import Image
    arr = (np.clip(rgb, 0, 1).transpose(1, 2, 0) * 255).astype(np.uint8)
    Image.fromarray(arr).save(path)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ha", help="STARLESS stretched mono Ha FITS")
    ap.add_argument("oiii", help="STARLESS stretched mono OIII FITS")
    ap.add_argument("--out", default=None, help="output RGB FITS (default: <Ha stem>_HOO_teal.fit)")
    ap.add_argument("--oiii-boost", type=float, default=1.0, help="multiply OIII before blend")
    ap.add_argument("--oiii-blur", type=float, default=1.0, help="gaussian sigma px on OIII (chroma denoise)")
    ap.add_argument("--no-linfit", action="store_true", help="skip LinearFit Ha->OIII")
    ap.add_argument("--no-scnr", action="store_true", help="skip green SCNR")
    ap.add_argument("--no-png", action="store_true", help="do not write the PNG preview")
    args = ap.parse_args(argv)

    ha, header = load_mono(args.ha)
    oiii, _ = load_mono(args.oiii)
    rgb, info = recombine(ha, oiii, oiii_boost=args.oiii_boost, oiii_blur=args.oiii_blur,
                          do_linfit=not args.no_linfit, do_scnr=not args.no_scnr)

    out = args.out or (os.path.splitext(args.ha)[0].replace("_Ha", "").replace("_starless", "")
                       + "_HOO_teal.fit")
    hdr = header.copy()
    hdr.add_history(f"hoo_recombine.py: dynamic HOO, linfit a={info['a']:.3f} b={info['b']:.3f} "
                    f"blur={info['blur']} boost={info['boost']}")
    fits.writeto(out, rgb, hdr, overwrite=True)
    print(f"RECOMBINE: TEAL (linfit a={info['a']:.3f} b={info['b']:.3f}, "
          f"blur={info['blur']}, boost={info['boost']})")
    print(f"wrote: {out}")
    if not args.no_png:
        png = os.path.splitext(out)[0] + ".png"
        save_png(rgb, png)
        print(f"wrote: {png}")


if __name__ == "__main__":
    main()
