#!/usr/bin/env python3
"""Measure deconvolution quality vs a baseline stack and print an adopt/skip verdict.

For each deconvolved image: detect stars on luminance, position-match to the baseline
(KD-tree <=3 px), and report TWO things that matter:
  - bright-star FWHM change (is it actually sharper?)
  - ring depth = darkest annulus (2-8 px) around bright stars, in units of background
    RMS (does it carve a dark ring? negative => visible black donut).

FWHM alone is misleading: aggressive RL shrinks the core (good FWHM) while gouging a
black ring around it (ruined image). Always judge on BOTH.

Usage:
  measure_deconv.py BASELINE.fit DECONV1.fit [DECONV2.fit ...]

Needs: numpy, astropy, sep, scipy.  Adopt rule: FWHM improves >=3% AND ring stays
above background (worst >= -1x RMS). Otherwise keep baseline.
"""
import sys, numpy as np
from astropy.io import fits
import sep
from scipy.spatial import cKDTree

FWHM_MIN_GAIN = 3.0   # percent; below this is measurement noise
RING_FLOOR = -1.0     # x RMS; trough below this around bright stars = visible dark ring


def luma(path):
    d = fits.getdata(path).astype(np.float32)
    if d.ndim == 3:
        d = d.mean(axis=0 if d.shape[0] in (1, 3) else -1)
    return np.ascontiguousarray(d)


def detect(img):
    img = img - np.median(img)
    bkg = sep.Background(img)
    sub = img - bkg
    rms = bkg.globalrms
    o = sep.extract(sub, thresh=10.0, err=rms, minarea=5, deblend_cont=0.005)
    a, b = o['a'], o['b']
    ecc = np.sqrt(np.clip(1 - (b / a) ** 2, 0, 1))
    fw = np.sqrt(a * b) * 2.3548
    keep = (ecc < 0.4) & (fw > 0.8) & (fw < 20) & np.isfinite(fw) & (o['flux'] > 0)
    return (np.c_[o['x'][keep], o['y'][keep]], fw[keep], o['flux'][keep],
            o['peak'][keep], sub, rms)


def ring_depth(sub, rms, xs, ys, peaks, pkmax, n=30):
    """Median/worst darkest-annulus value (2-8 px) around brightest unsaturated stars,
    in units of background RMS. Positive => stays above background (clean)."""
    order = np.argsort(peaks)[::-1]
    out = []
    R = 10
    for i in order:
        if peaks[i] > 0.9 * pkmax:
            continue
        xi, yi = int(round(xs[i])), int(round(ys[i]))
        if xi - R < 0 or yi - R < 0 or xi + R + 1 > sub.shape[1] or yi + R + 1 > sub.shape[0]:
            continue
        yy, xx = np.mgrid[-R:R + 1, -R:R + 1]
        r = np.sqrt(xx ** 2 + yy ** 2).ravel()
        patch = sub[yi - R:yi + R + 1, xi - R:xi + R + 1].ravel()
        out.append(np.nanmin([patch[(r >= rb - 0.5) & (r < rb + 0.5)].mean()
                              for rb in range(2, 9)]) / rms)
        if len(out) >= n:
            break
    if not out:
        return float('nan'), float('nan')
    return float(np.median(out)), float(np.min(out))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    base_path, deconv_paths = sys.argv[1], sys.argv[2:]

    bx, bfw, bflux, bpk, bsub, brms = detect(luma(base_path))
    pkmax = bpk.max()
    brmed, brworst = ring_depth(bsub, brms, bx[:, 0], bx[:, 1], bpk, pkmax)
    print(f"baseline: {base_path}")
    print(f"  stars={len(bx)}  median_FWHM={np.median(bfw):.2f}px  "
          f"ring(med/worst)={brmed:+.1f}/{brworst:+.1f}xRMS  (positive=clean)\n")

    print(f"{'variant':30s} {'FWHMΔ':>8} {'ring_med':>9} {'ring_worst':>11} {'verdict'}")
    results = []
    for p in deconv_paths:
        dx, dfw, dflux, dpk, dsub, drms = detect(luma(p))
        tree = cKDTree(bx)
        d, idx = tree.query(dx, distance_upper_bound=3.0)
        m = np.isfinite(d) & (d <= 3.0)
        bi = idx[m]
        bp = bpk[bi]
        sel = bp / pkmax >= 0.3                      # bright unsaturated subset
        if sel.sum() < 5:                             # fallback: brightest 100 by flux
            sel = np.zeros(m.sum(), bool)
            sel[np.argsort(bflux[bi])[::-1][:100]] = True
        fwhm_d = float(np.median((dfw[m][sel] - bfw[bi][sel]) / bfw[bi][sel]) * 100)
        rmed, rworst = ring_depth(dsub, drms, dx[m][:, 0], dx[m][:, 1], bpk[bi], pkmax)

        clean = rworst >= RING_FLOOR
        gained = fwhm_d <= -FWHM_MIN_GAIN
        if gained and clean:
            verdict = "ADOPT (sharper, clean)"
        elif gained and not clean:
            verdict = "REJECT (ringing! dark donuts)"
        else:
            verdict = "skip (no real gain)"
        name = p.rsplit('/', 1)[-1]
        print(f"{name:30s} {fwhm_d:>+7.1f}% {rmed:>+8.1f}x {rworst:>+10.1f}x  {verdict}")
        results.append((name, fwhm_d, rworst, clean and gained))

    winners = [r for r in results if r[3]]
    print()
    if winners:
        best = min(winners, key=lambda r: r[1])      # most negative FWHM among clean
        print(f"RECOMMEND: {best[0]}  ({best[1]:+.1f}% FWHM, no ringing)")
    else:
        print("RECOMMEND: KEEP BASELINE — no variant gives a clean FWHM gain "
              "(either no real gain, or sharpening only via ringing).")


if __name__ == "__main__":
    main()
