#!/usr/bin/env python3
"""
Measure quality metrics for a set of stacked FITS results and rank them.

Metrics per result, computed in THREE concentric crops (full / 2-3 / 1-4),
because registration leaves bad borders that would poison whole-frame stats:

  bg_sigma   - background noise (sigma-clipped std). Lower = cleaner.
               NOTE: scale-dependent (-output_norm rescales each result),
               so treat as relative, within one crop only.
  bright_SNR - signal/noise on bright stars/core. Scale-invariant ratio. Higher = better.
  faint_SNR  - signal/noise on FAINT nebulosity. Higher = better.
               This opposes over-aggressive clipping (which eats faint signal).
  outliers   - count of extreme pixels (> med + 30 sigma). Proxy for residual
               artifacts (satellites/cosmics not rejected). Lower = better.

Masks (which pixels are "bright" / "faint") are derived ONCE from a reference
result and reused for every variant. This is valid because all variants share
the same registration -> identical pixel coordinates.

Combined score = weighted sum of ranks (lower = better):
  faint_SNR (w=1.0), bright_SNR (w=0.7), outliers (w=0.5), bg_sigma (w=0.3)
Final verdict uses the central 2/3 crop.

Usage:
  measure_stacks.py <process_dir>   # scans result_v*.fit
"""
import sys, glob, os, csv
import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats

CROPS = [("90%", 0.90), ("2/3", 0.66), ("1/4", 0.25)]
# reference variant for mask derivation (Siril/Seestar default)
REF_HINT = "winsor_3_3"
W = {"faint_SNR": 1.0, "bright_SNR": 0.7, "outliers": 0.5, "bg_sigma": 0.3}


def load_lum(path):
    """Return luminance 2D array (mean over color channels) as float64."""
    data = fits.getdata(path).astype(np.float64)
    if data.ndim == 3:          # (channels, H, W)
        data = data.mean(axis=0)
    return data


def crop(arr, frac):
    if frac >= 1.0:
        return arr
    h, w = arr.shape
    ch, cw = int(h * frac), int(w * frac)
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    return arr[y0:y0 + ch, x0:x0 + cw]


def measure(lum, masks):
    """masks: (bright_bool, faint_bool) same shape as lum, or None."""
    mean, med, sig = sigma_clipped_stats(lum, sigma=3.0, maxiters=5)
    out = {"bg_median": med, "bg_sigma": sig}
    out["outliers"] = int(np.count_nonzero(lum > med + 30 * sig))
    if masks is not None:
        bright, faint = masks
        b_sig = lum[bright].mean() - med if bright.any() else 0.0
        f_sig = lum[faint].mean() - med if faint.any() else 0.0
        out["bright_SNR"] = b_sig / sig if sig > 0 else 0.0
        out["faint_SNR"] = f_sig / sig if sig > 0 else 0.0
    return out


def make_masks(lum):
    _, med, sig = sigma_clipped_stats(lum, sigma=3.0, maxiters=5)
    bright = lum > med + 10 * sig
    faint = (lum > med + 2 * sig) & (lum <= med + 10 * sig)
    return bright, faint


def rank(values, higher_better):
    """Return dict idx->rank (1=best). Ties share averaged rank."""
    order = sorted(range(len(values)), key=lambda i: values[i],
                   reverse=higher_better)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


ADOPT_THRESHOLD = 3.0  # min % faint-SNR gain over baseline to recommend ADOPT


def adopt_decision(best_faint, base_faint, threshold=ADOPT_THRESHOLD):
    """Return ('ADOPT'|'KEEP', faint_SNR gain %). Gain below threshold = noise."""
    if base_faint <= 0:
        return "KEEP", 0.0
    d = (best_faint / base_faint - 1) * 100
    return ("ADOPT" if d >= threshold else "KEEP"), d


def main():
    proc = sys.argv[1] if len(sys.argv) > 1 else "."
    files = sorted(glob.glob(os.path.join(proc, "result_v[0-9]*.fit*")))
    if not files:
        print(f"No result_v*.fit in {proc}", file=sys.stderr)
        sys.exit(1)

    names = [os.path.basename(f).replace("result_", "").rsplit(".", 1)[0]
             for f in files]
    ref_idx = next((i for i, n in enumerate(names) if REF_HINT in n), 0)
    print(f"Found {len(files)} results. Reference for masks: {names[ref_idx]}\n")

    lums = [load_lum(f) for f in files]

    all_rows = {}          # crop_label -> list of metric dicts
    for label, frac in CROPS:
        ref_crop = crop(lums[ref_idx], frac)
        masks = make_masks(ref_crop)
        rows = []
        for lum in lums:
            rows.append(measure(crop(lum, frac), masks))
        # combined rank score
        bg = [r["bg_sigma"] for r in rows]
        bsnr = [r["bright_SNR"] for r in rows]
        fsnr = [r["faint_SNR"] for r in rows]
        outl = [r["outliers"] for r in rows]
        rk = {
            "bg_sigma": rank(bg, higher_better=False),
            "bright_SNR": rank(bsnr, higher_better=True),
            "faint_SNR": rank(fsnr, higher_better=True),
            "outliers": rank(outl, higher_better=False),
        }
        for i, r in enumerate(rows):
            r["score"] = sum(W[k] * rk[k][i] for k in W)
        all_rows[label] = rows

    # ---- print one table per crop ----
    for label, _ in CROPS:
        rows = all_rows[label]
        idxs = sorted(range(len(rows)), key=lambda i: rows[i]["score"])
        print(f"=== CROP {label} (sorted by combined score, lower=better) ===")
        print(f"{'rank':>4} {'variant':28} {'bg_sigma':>11} "
              f"{'brightSNR':>10} {'faintSNR':>9} {'outliers':>9} {'score':>7}")
        for pos, i in enumerate(idxs, 1):
            r = rows[i]
            print(f"{pos:>4} {names[i]:28} {r['bg_sigma']:>11.3e} "
                  f"{r['bright_SNR']:>10.2f} {r['faint_SNR']:>9.3f} "
                  f"{r['outliers']:>9d} {r['score']:>7.1f}")
        print()

    # ---- verdict on 2/3 crop, compared to BASELINE ----
    rows23 = all_rows["2/3"]
    best = min(range(len(rows23)), key=lambda i: rows23[i]["score"])
    base = next((i for i, n in enumerate(names) if "BASELINE" in n.upper()),
                ref_idx)
    print("=" * 60)
    print(f"VERDICT (central 2/3 crop): BEST = {names[best]}")
    r = rows23[best]
    print(f"  bg_sigma={r['bg_sigma']:.3e}  bright_SNR={r['bright_SNR']:.2f}  "
          f"faint_SNR={r['faint_SNR']:.3f}  outliers={r['outliers']}")
    top3 = sorted(range(len(rows23)), key=lambda i: rows23[i]["score"])[:3]
    print("  Top-3:", ", ".join(names[i] for i in top3))

    # baseline comparison (faint_SNR is the reliable, scale-invariant metric)
    b = rows23[base]
    print(f"\nBASELINE = {names[base]}")
    print(f"  faint_SNR={b['faint_SNR']:.3f}  bright_SNR={b['bright_SNR']:.2f}")
    if base != best and b["faint_SNR"] > 0:
        decision, d_faint = adopt_decision(r["faint_SNR"], b["faint_SNR"])
        d_bright = (r["bright_SNR"] / b["bright_SNR"] - 1) * 100
        # +x% SNR ~= ((1+x)^2 - 1) more frames worth of integration
        eq_frames = ((r["faint_SNR"] / b["faint_SNR"]) ** 2 - 1) * 100
        print(f"\nBEST vs BASELINE: faint_SNR {d_faint:+.1f}%  "
              f"bright_SNR {d_bright:+.1f}%  (~{eq_frames:+.0f}% integration)")
        if decision == "ADOPT":
            print(f"  -> ADOPT '{names[best]}': beats default by {d_faint:.1f}% "
                  "faint SNR. Copy it to best/.")
        else:
            print(f"  -> KEEP BASELINE: gain < {ADOPT_THRESHOLD:.0f}% faint SNR is "
                  "within measurement noise; default is optimal. Copy baseline to best/.")
    else:
        print("\nBEST == BASELINE: the default script is already optimal.")

    # ---- CSV dump ----
    csv_path = os.path.join(proc, "metrics.csv")
    with open(csv_path, "w", newline="") as fh:
        wtr = csv.writer(fh)
        wtr.writerow(["crop", "variant", "bg_median", "bg_sigma",
                      "bright_SNR", "faint_SNR", "outliers", "score"])
        for label, _ in CROPS:
            for i, r in enumerate(all_rows[label]):
                wtr.writerow([label, names[i], f"{r['bg_median']:.6e}",
                              f"{r['bg_sigma']:.6e}", f"{r['bright_SNR']:.4f}",
                              f"{r['faint_SNR']:.4f}", r["outliers"],
                              f"{r['score']:.2f}"])
    print(f"\nCSV written: {csv_path}")


if __name__ == "__main__":
    main()
