#!/usr/bin/env python3
"""Composite validation preview for a (linear) Seestar FITS.

Renders one PNG that lets the user eyeball a pipeline step:
  - full frame, auto-stretched (gradient / colour cast / overall look)
  - zoom crops on the brightest stars (reveals deconv rings / "donuts" and star colour)
  - optional before/after: same panels for a reference FITS (the step input), stretched
    IDENTICALLY so the comparison is fair

The stretch is a LINKED PixInsight-style auto-STF (one midtones balance applied to all three
channels), so the colour cast stays visible instead of being neutralised away.

Usage:
  preview.py RESULT.fits [--ref BEFORE.fits] [--out preview.png]
             [--title TEXT] [--stars N] [--crop PX]

Needs: numpy, astropy, Pillow. sep is optional (star crops are skipped if missing/empty).
"""
import sys, os, argparse
import numpy as np
from astropy.io import fits
from PIL import Image, ImageDraw, ImageFont


def load_rgb(path):
    """Return float image as (H, W, 3) for RGB FITS or (H, W) for mono."""
    data = fits.getdata(path, ignore_missing_simple=True).astype(np.float64)
    if data.ndim == 3:                      # FITS (chan, H, W) -> (H, W, chan)
        data = np.moveaxis(data, 0, -1)
        if data.shape[-1] > 3:
            data = data[..., :3]
    return data


def luminance(img):
    if img.ndim == 2:
        return img
    return 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]


def _mtf(m, x):
    """PixInsight midtones transfer function; x in [0,1], 0<m<1."""
    if abs(m - 0.5) < 1e-6:
        return x
    return (m - 1.0) * x / ((2.0 * m - 1.0) * x - m)


def auto_stf_params(img):
    """Linked auto-STF (shadow clip c0, midtones m) from pooled finite pixels."""
    v = luminance(img)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return 0.0, 0.5
    med = np.median(v)
    mad = np.median(np.abs(v - med)) * 1.4826
    B, C = 0.25, -2.8                       # target bg, shadow clip (sigma)
    if mad < 1e-12:
        return float(np.clip(med, 0, 1)), 0.5
    c0 = float(np.clip(med + C * mad, 0.0, 1.0))
    x = med - c0
    m = _mtf(B, x) if x > 0 else 0.5
    return c0, float(np.clip(m, 1e-3, 1 - 1e-3))


def stretch(img, params):
    """Apply linked auto-STF, return uint8 (H, W, 3)."""
    c0, m = params
    v = np.clip((img - c0) / max(1.0 - c0, 1e-6), 0.0, 1.0)
    out = _mtf(m, v)
    out = np.clip(out, 0.0, 1.0)
    if out.ndim == 2:
        out = np.stack([out] * 3, axis=-1)
    return (out * 255 + 0.5).astype(np.uint8)


def norm_range(img):
    """(lo, hi) linear-display range: keep the low end, tame hot pixels at the top."""
    finite = img[np.isfinite(img)]
    if finite.size == 0:
        return 0.0, 1.0
    lo = float(finite.min())
    hi = float(np.percentile(finite, 99.99))
    return lo, (hi if hi > lo else lo + 1.0)


def normalize(img, rng=None):
    """Map to [0,1] using rng (lo, hi); compute from img if not given."""
    lo, hi = rng if rng is not None else norm_range(img)
    out = (img - lo) / (hi - lo)
    return np.nan_to_num(out, nan=0.0)


def find_stars(img, n, margin_frac=0.06):
    """Brightest, well-separated stars away from registration borders. (cy, cx) list."""
    try:
        import sep
    except Exception:
        return []
    lum = np.ascontiguousarray(luminance(img), dtype=np.float32)
    try:
        sep.set_extract_pixstack(2_000_000)
        bkg = sep.Background(lum)
        sub = lum - bkg.back()
        objs = sep.extract(sub, 8.0, err=bkg.globalrms, minarea=5)
    except Exception:
        return []
    if len(objs) == 0:
        return []
    h, w = lum.shape
    mx, my = int(w * margin_frac), int(h * margin_frac)
    cand = [(objs["peak"][i], objs["y"][i], objs["x"][i]) for i in range(len(objs))
            if mx < objs["x"][i] < w - mx and my < objs["y"][i] < h - my]
    cand.sort(reverse=True)
    picked, min_sep = [], max(h, w) * 0.04
    for _, cy, cx in cand:
        if all((cy - py) ** 2 + (cx - px) ** 2 > min_sep ** 2 for py, px in picked):
            picked.append((cy, cx))
        if len(picked) >= n:
            break
    return picked


def crop_panel(rgb8, cy, cx, half, zoom):
    h, w = rgb8.shape[:2]
    y0, x0 = int(cy - half), int(cx - half)
    y0 = max(0, min(y0, h - 2 * half)); x0 = max(0, min(x0, w - 2 * half))
    sub = rgb8[y0:y0 + 2 * half, x0:x0 + 2 * half]
    im = Image.fromarray(sub)
    return im.resize((2 * half * zoom, 2 * half * zoom), Image.NEAREST)


def fit_width(rgb8, target_w):
    h, w = rgb8.shape[:2]
    im = Image.fromarray(rgb8)
    if w != target_w:
        im = im.resize((target_w, max(1, round(h * target_w / w))), Image.LANCZOS)
    return im


def _font():
    for p in ("/System/Library/Fonts/SFNSMono.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, 16)
            except Exception:
                pass
    return ImageFont.load_default()


def label(draw, xy, text, font):
    x, y = xy
    draw.rectangle([x, y, x + 9 * len(text) + 8, y + 22], fill=(0, 0, 0))
    draw.text((x + 4, y + 3), text, fill=(255, 255, 0), font=font)


def build(result_path, ref_path, out_path, title, n_stars, crop_px):
    res = load_rgb(result_path)
    rng = norm_range(res)                       # one display range, shared by all panels
    params = auto_stf_params(normalize(res, rng))   # one stretch, shared by all panels
    res8 = stretch(normalize(res, rng), params)
    stars = find_stars(res, n_stars)

    cols = []
    if ref_path:
        ref8 = stretch(normalize(load_rgb(ref_path), rng), params)
        cols = [("BEFORE", ref8), ("AFTER", res8)]
    else:
        cols = [("RESULT", res8)]

    pad, fw_w = 12, 760 if ref_path else 900
    font = _font()
    full_imgs = [(name, fit_width(arr, fw_w)) for name, arr in cols]
    full_h = max(im.height for _, im in full_imgs)
    half, zoom = crop_px // 2, max(2, 192 // crop_px)
    crop_side = crop_px * zoom

    canvas_w = pad + sum(im.width + pad for _, im in full_imgs)
    crop_rows_h = 0
    if stars:
        crop_rows_h = pad + 24 + len(cols) * (crop_side + pad)
    canvas_h = pad + 26 + full_h + pad + crop_rows_h + pad
    canvas = Image.new("RGB", (canvas_w, canvas_h), (24, 24, 28))
    draw = ImageDraw.Draw(canvas)

    if title:
        draw.text((pad, 6), title, fill=(220, 220, 220), font=font)

    x = pad
    top = pad + 26
    for name, im in full_imgs:
        canvas.paste(im, (x, top))
        label(draw, (x, top), name, font)
        x += im.width + pad

    if stars:
        cy0 = top + full_h + pad
        draw.text((pad, cy0), f"star zoom ×{zoom}  ({len(stars)} brightest)",
                  fill=(200, 200, 200), font=font)
        cy0 += 24
        for r, (name, arr) in enumerate(cols):
            ry = cy0 + r * (crop_side + pad)
            cx = pad
            label(draw, (cx, ry), name, font)
            cx += 60
            for (sy, sx) in stars:
                canvas.paste(crop_panel(arr, sy, sx, half, zoom), (cx, ry))
                cx += crop_side + pad
                if cx + crop_side > canvas_w:
                    break

    canvas.save(out_path)
    return out_path, len(stars)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("result")
    ap.add_argument("--ref")
    ap.add_argument("--out")
    ap.add_argument("--title", default="")
    ap.add_argument("--stars", type=int, default=6)
    ap.add_argument("--crop", type=int, default=32)
    a = ap.parse_args()
    out = a.out or os.path.splitext(a.result)[0] + "_preview.png"
    title = a.title or os.path.basename(a.result)
    path, ns = build(a.result, a.ref, out, title, a.stars, a.crop)
    print(f"[preview] {path}  (full frame{' + before/after' if a.ref else ''}, {ns} star crops)")


if __name__ == "__main__":
    main()
