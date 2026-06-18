#!/usr/bin/env python3
"""Position-matched bright-star FWHM comparison between a baseline stack and a
deconvolved image. Both must share the same registration grid (same pixel coords).

Usage: mf_compare.py BASELINE.fit DECONV.fit [topN]
"""
import sys, numpy as np
from astropy.io import fits
import sep
from scipy.spatial import cKDTree

def luma(path):
    d = fits.getdata(path).astype(np.float32)
    if d.ndim == 3:
        if d.shape[0] in (1, 3):   d = d.mean(axis=0)
        elif d.shape[-1] in (1, 3): d = d.mean(axis=-1)
    return np.ascontiguousarray(d)

def detect(img):
    img = img - np.median(img)
    bkg = sep.Background(img)
    sub = img - bkg
    obj = sep.extract(sub, thresh=8.0, err=bkg.globalrms, minarea=5,
                      deblend_cont=0.005)
    a, b = obj['a'], obj['b']
    ecc = np.sqrt(np.clip(1 - (b/a)**2, 0, 1))
    fw = np.sqrt(a*b) * 2.3548
    keep = (ecc < 0.4) & (fw > 0.8) & (fw < 20) & np.isfinite(fw) & (obj['flux'] > 0) \
           & (obj['peak'] < 0.95*np.nanmax(sub))   # drop saturated
    return (np.c_[obj['x'][keep], obj['y'][keep]],
            fw[keep], obj['flux'][keep], ecc[keep])

base_path, dec_path = sys.argv[1], sys.argv[2]
topN = int(sys.argv[3]) if len(sys.argv) > 3 else 200

bx, bfw, bflux, becc = detect(luma(base_path))
dx, dfw, dflux, decc = detect(luma(dec_path))
print(f"baseline stars={len(bx)}  median_FWHM={np.median(bfw):.3f}")
print(f"deconv   stars={len(dx)}  median_FWHM={np.median(dfw):.3f}")

# match deconv stars to baseline stars by position (<=3 px)
tree = cKDTree(bx)
d, idx = tree.query(dx, distance_upper_bound=3.0)
m = np.isfinite(d) & (d <= 3.0)
bi = idx[m]
b_fw_m, d_fw_m = bfw[bi], dfw[m]
b_flux_m = bflux[bi]
print(f"matched pairs={m.sum()}")

# Bright-star subset (top by baseline flux) — most reliable FWHM
order = np.argsort(b_flux_m)[::-1][:topN]
bb, dd = b_fw_m[order], d_fw_m[order]
ecc_b = becc[bi][order]; ecc_d = decc[m][order]
print(f"\n--- top {len(order)} brightest matched ---")
print(f"baseline  median FWHM = {np.median(bb):.3f} px   mean ecc={np.mean(ecc_b):.3f}")
print(f"deconv    median FWHM = {np.median(dd):.3f} px   mean ecc={np.mean(ecc_d):.3f}")
rel = (np.median(dd)-np.median(bb))/np.median(bb)*100
print(f"FWHM change = {rel:+.1f}%   (negative = tighter = better)")
# per-star paired delta (robust to outliers)
delta = (dd-bb)/bb*100
print(f"per-star median Δ = {np.median(delta):+.1f}%   (paired)")
