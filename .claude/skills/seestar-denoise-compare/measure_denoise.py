#!/usr/bin/env python3
"""Measure denoise quality vs the pre-denoise image and print a verdict.

Denoising trades noise for detail. For each result report:
  - noise_drop = background RMS reduction vs baseline, in % (higher = quieter).
  - FWHM_delta = bright-star FWHM change (positive = stars blurred = over-denoised).
  - faint_keep = fraction of faint baseline stars still detected (low = faint detail eaten).

Good denoise: large noise drop, FWHM ~unchanged, faint stars kept. Over-denoise shows up
as rising FWHM and/or dropping faint_keep ("plastic" look).

Usage:
  measure_denoise.py BASELINE.fit DENOISED1.fit [DENOISED2.fit ...]

Needs: numpy, astropy, sep, scipy.  Adopt rule: take the strongest noise drop with
FWHM_delta < ~3% AND faint_keep > ~0.85. Otherwise back off the strength.
"""
import sys, numpy as np
from astropy.io import fits
import sep
from scipy.spatial import cKDTree

sep.set_extract_pixstack(2_000_000)   # faint detection on noisy frames needs headroom

FWHM_TOL = 3.0     # percent FWHM increase tolerated
KEEP_TOL = 0.85    # min fraction of faint stars retained


def luma(path):
    d = fits.getdata(path).astype(np.float32)
    if d.ndim == 3:
        d = d.mean(axis=0 if d.shape[0] in (1, 3) else -1)
    return np.ascontiguousarray(d)


def analyse(img):
    img = img - np.median(img)
    bkg = sep.Background(img)
    sub = img - bkg
    rms = float(bkg.globalrms)
    bright = sep.extract(sub, thresh=10.0, err=rms, minarea=5, deblend_cont=0.005)
    # faint = real-ish faint stars: minarea filters 1-3 px noise spikes that denoise
    # legitimately removes, so a drop here means REAL faint detail eaten.
    faint = sep.extract(sub, thresh=5.0, err=rms, minarea=4)
    a, b = bright['a'], bright['b']
    ecc = np.sqrt(np.clip(1 - (b / a) ** 2, 0, 1))
    fw = np.sqrt(a * b) * 2.3548
    keep = (ecc < 0.4) & (fw > 0.8) & (fw < 20) & np.isfinite(fw)
    return (rms, np.c_[bright['x'][keep], bright['y'][keep]], fw[keep],
            np.c_[faint['x'], faint['y']])


def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    base, results = sys.argv[1], sys.argv[2:]
    brms, bxy, bfw, bfaint = analyse(luma(base))
    print(f"baseline: {base}")
    print(f"  bg_rms={brms:.6g}  bright_stars={len(bxy)}  faint_stars={len(bfaint)}  "
          f"median_FWHM={np.median(bfw):.2f}px\n")
    ftree = cKDTree(bfaint)
    btree = cKDTree(bxy)

    print(f"{'result':28s} {'noise_drop':>10} {'FWHMΔ':>7} {'faint_keep':>10} {'verdict'}")
    rows = []
    for p in results:
        rms, xy, fw, faint = analyse(luma(p))
        noise_drop = (1 - rms / brms) * 100
        # match bright stars to baseline for FWHM delta
        d, idx = btree.query(xy, distance_upper_bound=3.0)
        m = np.isfinite(d) & (d <= 3.0)
        fwd = float(np.median((fw[m] - bfw[idx[m]]) / bfw[idx[m]]) * 100) if m.sum() >= 5 else float('nan')
        # faint retention: baseline faint stars that still have a detection nearby
        if len(faint):
            d2, _ = cKDTree(faint).query(bfaint, distance_upper_bound=2.0)
            keep = float(np.mean(np.isfinite(d2) & (d2 <= 2.0)))
        else:
            keep = 0.0
        ok = (noise_drop > 5) and (np.isnan(fwd) or fwd < FWHM_TOL) and (keep > KEEP_TOL)
        over = (not np.isnan(fwd) and fwd >= FWHM_TOL) or (keep <= KEEP_TOL)
        verdict = "good" if ok else ("OVER-DENOISED (detail lost)" if over else "weak")
        name = p.rsplit('/', 1)[-1]
        print(f"{name:28s} {noise_drop:>9.1f}% {fwd:>+6.1f}% {keep:>9.2f}  {verdict}")
        rows.append((name, noise_drop, fwd, keep, ok))

    good = [r for r in rows if r[4]]
    print()
    if good:
        best = max(good, key=lambda r: r[1])        # strongest clean noise drop
        print(f"RECOMMEND: {best[0]}  ({best[1]:.1f}% noise drop, FWHM {best[2]:+.1f}%, "
              f"faint kept {best[3]:.2f})")
    else:
        print("RECOMMEND: lower the strength — every variant either barely denoises or "
              "over-denoises (blurs stars / eats faint detail).")


if __name__ == "__main__":
    main()
