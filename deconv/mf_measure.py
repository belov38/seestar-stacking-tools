#!/usr/bin/env python3
"""Measure median star FWHM via SEP on luminance, for before/after deconv comparison."""
import sys, numpy as np
from astropy.io import fits
import sep

def luma(path):
    d = fits.getdata(path).astype(np.float32)
    if d.ndim == 3:
        # channel-first (3,H,W) or channel-last (H,W,3)
        if d.shape[0] in (1, 3):
            d = d.mean(axis=0)
        elif d.shape[-1] in (1, 3):
            d = d.mean(axis=-1)
    return np.ascontiguousarray(d)

def fwhm(path):
    img = luma(path)
    img = img - np.median(img)
    bkg = sep.Background(img)
    sub = img - bkg
    obj = sep.extract(sub, thresh=5.0, err=bkg.globalrms)
    if len(obj) == 0:
        return None, 0
    # keep roundish, non-saturated
    a, b = obj['a'], obj['b']
    flux = obj['flux']
    ecc = np.sqrt(np.clip(1 - (b/a)**2, 0, 1))
    keep = (ecc < 0.5) & (flux > 0) & np.isfinite(a) & np.isfinite(b)
    if keep.sum() == 0:
        keep = np.ones(len(obj), bool)
    sigma = np.sqrt(a[keep]*b[keep])
    fw = sigma * 2.3548
    fw = fw[(fw > 0.5) & (fw < 30)]
    return float(np.median(fw)), int(keep.sum())

for p in sys.argv[1:]:
    f, n = fwhm(p)
    print(f"{p}\n  median_FWHM_px = {f:.3f}   (stars={n})" if f else f"{p}: no stars")
