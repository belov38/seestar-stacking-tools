#!/usr/bin/env python
"""Per-sub quality scoring for Seestar lights: catch clouds, haze, defocus, trails
BEFORE stacking. Siril registration alone does not catch these (on NGC 292 it
registered 920/932 frames while 87 were shot through clouds).

Measures per frame (on 2x2-binned luminance, fast): background level, star count,
FWHM, roundness. Groups frames by exposure (sky level scales with exposure), then
classifies with robust median/MAD thresholds per group:

  CLOUD   bg > +3 sigma AND nstars < -3 sigma   (shot through clouds, ~zero signal)
  HAZY    bg > +3 sigma, star count normal      (thin haze; normalization compensates)
  SOFT    fwhm > +3 sigma, or nstars < -3 sigma
          with normal bg                        (defocus or bad seeing)
  TRAILED roundness < -3 sigma and < 0.8        (wind / tracking error)

Usage:
  score_subs.py LIGHTS_DIR [--out scores.csv]            # report only
  score_subs.py LIGHTS_DIR --move CLOUD,HAZY --aside-dir DIR   # quarantine (move, not delete)
"""
import argparse
import csv
import os
import re
import shutil
import sys

import numpy as np
import sep
from astropy.io import fits

CLASS_ORDER = ["CLOUD", "HAZY", "TRAILED", "SOFT", "OK"]


def mad_stats(x):
    x = np.asarray(x, float)
    m = np.nanmedian(x)
    s = np.nanmedian(np.abs(x - m)) * 1.4826
    # relative floor: on unnaturally uniform data a tiny MAD makes +/-3 sigma a hair trigger
    return m, max(s, 0.02 * abs(m), 1e-9)


def exposure_key(path):
    m = re.search(r"_(\d+(?:\.\d+)?)s_", os.path.basename(path))
    if m:
        return m.group(1) + "s"
    try:
        return f"{float(fits.getheader(path).get('EXPTIME', 0)):g}s"
    except Exception:
        return "unknown"


def score_frame(path):
    with fits.open(path, memmap=False) as hdul:
        d = hdul[0].data.astype(np.float32)
    if d.ndim != 2:
        sys.exit(f"{path}: expected a 2D Bayer frame, got shape {d.shape}")
    h, w = d.shape
    # 2x2 superpixel bin: collapses the Bayer mosaic into mono luminance
    d = d[: h // 2 * 2, : w // 2 * 2].reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
    bkg = sep.Background(d)
    sub = d - bkg.back()
    try:
        objs = sep.extract(sub, 6.0, err=bkg.globalrms, minarea=5)
    except Exception:
        objs = np.array([])
    n = len(objs)
    if n:
        idx = np.argsort(objs["flux"])[max(0, n - max(5, n // 4)):]  # brightest quartile
        a = objs["a"][idx]
        b = objs["b"][idx]
        fwhm = float(np.median(a) * 2.3548 * 2)  # *2 undoes the binning -> original px
        roundness = float(np.median(b / np.maximum(a, 1e-9)))
    else:
        fwhm, roundness = float("nan"), float("nan")
    return float(bkg.globalback), float(bkg.globalrms), n, fwhm, roundness


def classify(rows):
    """rows: list of dicts with bg/nstars/fwhm/roundness/exposure. Adds 'class' in place."""
    for exp in sorted({r["exposure"] for r in rows}):
        grp = [r for r in rows if r["exposure"] == exp]
        mbg, sbg = mad_stats([r["bg"] for r in grp])
        mns, sns = mad_stats([r["nstars"] for r in grp])
        mfw, sfw = mad_stats([r["fwhm_px"] for r in grp])
        mrd, srd = mad_stats([r["roundness"] for r in grp])
        if len(grp) < 20:
            print(f"[score] warning: only {len(grp)} frames in {exp} group — thresholds unstable")
        for r in grp:
            if r["bg"] > mbg + 3 * sbg and r["nstars"] < mns - 3 * sns:
                r["class"] = "CLOUD"
            elif r["bg"] > mbg + 3 * sbg:
                r["class"] = "HAZY"
            elif r["nstars"] < mns - 3 * sns:
                # stars gone but bg normal: gross defocus (also catches NaN fwhm at 0 stars)
                r["class"] = "SOFT"
            elif r["roundness"] < min(mrd - 3 * srd, 0.8):
                r["class"] = "TRAILED"
            elif r["fwhm_px"] > mfw + 3 * sfw:
                r["class"] = "SOFT"
            else:
                r["class"] = "OK"
        yield exp, grp, (mbg, sbg, mns, sns, mfw, sfw, mrd, srd)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lights", help="directory of raw Seestar .fit lights")
    ap.add_argument("--out", help="CSV output path (default: <lights>/../sub_scores.csv)")
    ap.add_argument("--move", help="comma-separated classes to quarantine, e.g. CLOUD,HAZY")
    ap.add_argument("--aside-dir", help="where to move quarantined frames (required with --move)")
    ap.add_argument("--limit", type=int, help="score only the first N frames (smoke test)")
    args = ap.parse_args()

    files = sorted(
        os.path.join(args.lights, f) for f in os.listdir(args.lights) if f.lower().endswith(".fit")
    )
    if args.limit:
        files = files[: args.limit]
    if not files:
        sys.exit(f"no .fit files in {args.lights}")

    rows = []
    for i, p in enumerate(files):
        bg, rms, n, fwhm, rnd = score_frame(p)
        rows.append(dict(file=os.path.basename(p), exposure=exposure_key(p), bg=bg,
                         bg_rms=rms, nstars=n, fwhm_px=fwhm, roundness=rnd))
        if (i + 1) % 100 == 0:
            print(f"[score] {i+1}/{len(files)}", flush=True)

    for exp, grp, (mbg, sbg, mns, sns, mfw, sfw, mrd, srd) in classify(rows):
        print(f"\n== {exp}: {len(grp)} frames  bg={mbg:.0f}±{sbg:.0f}  nstars={mns:.0f}±{sns:.0f}"
              f"  fwhm={mfw:.2f}±{sfw:.2f}px  roundness={mrd:.2f}±{srd:.2f}")
        counts = {c: sum(1 for r in grp if r["class"] == c) for c in CLASS_ORDER}
        secs = float(exp.rstrip("s")) if exp.rstrip("s").replace(".", "").isdigit() else 0
        for c in CLASS_ORDER:
            if counts[c] and c != "OK":
                print(f"   {c:8s} {counts[c]:4d} frames  (~{counts[c]*secs/60:.0f} min)")
        print(f"   OK       {counts['OK']:4d} frames")

    flagged = [r for r in rows if r["class"] != "OK"]
    print(f"\n[score] flagged {len(flagged)}/{len(rows)} frames")
    for r in sorted(flagged, key=lambda r: (r["class"], -r["bg"]))[:40]:
        print(f"  {r['class']:8s} {r['file']}  bg={r['bg']:.0f}  nstars={r['nstars']}"
              f"  fwhm={r['fwhm_px']:.2f}  round={r['roundness']:.2f}")
    if len(flagged) > 40:
        print(f"  ... and {len(flagged)-40} more (see CSV)")

    out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.lights)), "sub_scores.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["file", "exposure", "class", "bg", "bg_rms",
                                          "nstars", "fwhm_px", "roundness"])
        w.writeheader()
        w.writerows(rows)
    print(f"[score] CSV written: {out}")

    if args.move:
        if not args.aside_dir:
            sys.exit("--move requires --aside-dir")
        classes = {c.strip().upper() for c in args.move.split(",")}
        unknown = classes - set(CLASS_ORDER)
        if unknown:
            sys.exit(f"unknown classes: {unknown}")
        os.makedirs(args.aside_dir, exist_ok=True)
        moved = 0
        for r in rows:
            if r["class"] in classes:
                shutil.move(os.path.join(args.lights, r["file"]),
                            os.path.join(args.aside_dir, r["file"]))
                moved += 1
        print(f"[score] moved {moved} frames ({','.join(sorted(classes))}) -> {args.aside_dir}")


if __name__ == "__main__":
    main()
