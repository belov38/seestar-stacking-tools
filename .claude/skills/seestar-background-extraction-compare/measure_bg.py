#!/usr/bin/env python3
"""Measure background-extraction quality vs the original stack and print a verdict.

For each result: estimate the BACKGROUND across the frame on a tile grid (star-masked
via sigma-clipping) and report:
  - gradient = spread of tile background levels (p95-p5) / overall level, in %.
    Lower = flatter = better gradient removal.
  - cast = max per-channel background deviation, in %. Lower = more neutral colour.
  - level = mean background (to catch OVER-subtraction: near-zero/negative = clipped,
    real nebulosity likely eaten).

Usage:
  measure_bg.py ORIGINAL.fit RESULT1.fit [RESULT2.fit ...]

Needs: numpy, astropy.  Adopt rule: gradient drops a lot AND cast < ~1% AND level stays
clearly positive (no over-subtraction). Otherwise keep the gentler option / baseline.
"""
import sys, numpy as np
from astropy.io import fits

NTILE = 8


def load(path):
    d = fits.getdata(path).astype(np.float32)
    if d.ndim == 2:
        d = d[None, ...]                       # (1,H,W)
    elif d.shape[-1] in (1, 3) and d.shape[0] not in (1, 3):
        d = np.moveaxis(d, -1, 0)              # (H,W,C) -> (C,H,W)
    return d                                    # (C,H,W)


def clipped_bg(v):
    """sigma-clipped background level of a pixel vector (excludes stars)."""
    v = v[np.isfinite(v)]
    if v.size == 0:
        return np.nan
    m, s = np.median(v), v.std()
    for _ in range(4):
        v = v[(v > m - 3 * s) & (v < m + 2 * s)]   # asymmetric: cut bright harder
        if v.size < 16:
            break
        m, s = np.median(v), v.std()
    return float(m)


def tile_bg(ch):
    H, W = ch.shape
    meds = []
    for i in range(NTILE):
        for j in range(NTILE):
            t = ch[i * H // NTILE:(i + 1) * H // NTILE,
                   j * W // NTILE:(j + 1) * W // NTILE]
            meds.append(clipped_bg(t.ravel()))
    return np.array(meds)


def metrics(path):
    d = load(path)
    # background luminance map (mean over channels), tile-based
    lum = d.mean(axis=0)
    meds = tile_bg(lum)
    level = float(np.nanmedian(meds))
    grad = float((np.nanpercentile(meds, 95) - np.nanpercentile(meds, 5)) / max(abs(level), 1e-8) * 100)
    # colour cast: per-channel global clipped background
    if d.shape[0] == 3:
        chbg = [clipped_bg(d[c].ravel()) for c in range(3)]
        mean = np.mean(chbg)
        cast = float(max(abs(x - mean) for x in chbg) / max(abs(mean), 1e-8) * 100)
    else:
        cast = float('nan')
    negfrac = float((lum < 0).mean() * 100)              # over-subtraction hint
    return level, grad, cast, negfrac


def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    orig, results = sys.argv[1], sys.argv[2:]
    olevel, ograd, ocast, oneg = metrics(orig)
    print(f"original: {orig}")
    print(f"  gradient={ograd:.1f}%  colour_cast={ocast:.1f}%  bg_level={olevel:.5f}  neg={oneg:.0f}%\n")
    print(f"{'result':30s} {'gradient':>8} {'cast':>6} {'bg_level':>9} {'neg%':>5}  {'note'}")
    rows = []
    for p in results:
        lvl, grad, cast, neg = metrics(p)
        # over-subtraction: background driven strongly negative (real signal eaten).
        # NOTE: ~50% slightly-negative is NORMAL when a tool centres background on 0;
        # only a strongly negative MEDIAN level flags trouble. Nebulosity loss must
        # still be confirmed visually (this metric only sees the background plane).
        over = lvl < -0.2 * olevel
        backfired = grad > 1.5 * ograd and grad > 5.0    # tool ADDED a gradient
        flat = grad < max(1.0, 0.6 * ograd)              # flat in absolute or relative terms
        neutral = (cast < 1.0) or np.isnan(cast)
        if backfired:
            note = "BACKFIRED (added gradient!)"
        elif over:
            note = "over-subtracted?"
        elif flat and neutral:
            note = "flat & neutral"
        elif neutral:
            note = "neutral"
        else:
            note = "cast left"
        name = p.rsplit('/', 1)[-1]
        print(f"{name:30s} {grad:>7.1f}% {cast:>5.1f}% {lvl:>9.5f} {neg:>4.0f}%  {note}")
        # score: lower gradient + cast is better; penalise backfire/over-subtraction
        score = grad + (0 if np.isnan(cast) else cast) + (1e6 if (over or backfired) else 0)
        rows.append((name, grad, cast, score))

    best = min(rows, key=lambda r: r[3])
    print()
    print(f"RECOMMEND: {best[0]}  (gradient {best[1]:.1f}%, cast {best[2]:.1f}%)")
    print("Confirm visually for nebula targets — no automatic metric fully detects "
          "real nebulosity eaten by over-aggressive extraction.")


if __name__ == "__main__":
    main()
