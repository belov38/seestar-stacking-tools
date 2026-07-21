#!/usr/bin/env python3
"""rc-astro CLI adapter: license probe + thin product wrappers (bxt/sxt/nxt).

The rc-astro CLI is optional and licensed per product. probe never fails:
absence or an unlicensed product just shows up in the printed line.
"""
import json
import os
import shutil
import subprocess
import sys

PRODUCTS = ("bxt", "sxt", "nxt")
FALLBACK_PATH = "/usr/local/bin/rc-astro"


def find_cli():
    """Locate the rc-astro binary: PATH first, then the observed install path."""
    return shutil.which("rc-astro") or (
        FALLBACK_PATH if os.path.exists(FALLBACK_PATH) else None
    )


def _find_key(obj, key):
    """Depth-first search for key in nested dicts/lists (JSON shape may evolve)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key(v, key)
            if found is not None:
                return found
    return None


def probe_line():
    """One parseable status line for the pipeline's Step 1."""
    cli = find_cli()
    if cli is None:
        return "RCASTRO: absent"
    version = "?"
    states = {}
    for product in PRODUCTS:
        try:
            proc = subprocess.run(
                [cli, "--no-banner", "--json", product, "--license"],
                capture_output=True, text=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError):
            states[product] = "no"
            continue
        ok = False
        if proc.returncode == 0:
            try:
                doc = json.loads(proc.stdout)
            except (json.JSONDecodeError, ValueError):
                doc = None
            if doc is not None:
                ok = _find_key(doc, "valid") is True
                v = _find_key(doc, "cliVersion")
                if v:
                    version = v
        states[product] = "ok" if ok else "no"
    parts = " ".join(f"{p}={states[p]}" for p in PRODUCTS)
    return f"RCASTRO: cli={version} {parts}"


def _normalize_roworder(inp, out):
    """The rc-astro CLI writes TOP-DOWN FITS (flipped pixels) regardless of the
    input's row order; astropy/Siril read raw pixel order, so flip the output
    back to the input's convention when the ROWORDER keywords disagree."""
    from astropy.io import fits

    import numpy as np

    with fits.open(inp) as hdul:
        in_order = hdul[0].header.get("ROWORDER", "BOTTOM-UP")
    # memmap=False: flipping a memmapped HDU in update mode corrupts the file
    # (rows are overwritten while still being read as the flip source)
    with fits.open(out, mode="update", memmap=False) as hdul:
        hdu = hdul[0]
        out_order = hdu.header.get("ROWORDER", "BOTTOM-UP")
        if out_order != in_order:
            hdu.data = np.ascontiguousarray(hdu.data[..., ::-1, :])
            hdu.header["ROWORDER"] = in_order


def run_product(product, inp, out, extra):
    """Run one product on one file. Returns 0 on success (out exists), else non-zero."""
    cli = find_cli()
    if cli is None:
        print("rcastro: rc-astro CLI not found", file=sys.stderr)
        return 1
    cmd = [cli, "--no-banner", "--json", product, inp, "-o", out, "--overwrite"] + list(extra)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if event.get("event") == "error":
            print(f"rcastro {product}: {event.get('message', 'error')}", file=sys.stderr)
    if proc.returncode != 0:
        print(f"rcastro {product}: exit {proc.returncode}", file=sys.stderr)
        return proc.returncode or 1
    if not os.path.exists(out):
        print(f"rcastro {product}: output not written: {out}", file=sys.stderr)
        return 1
    base, ext = os.path.splitext(out)
    for path in (out, base + "-stars" + ext):
        if os.path.exists(path):
            _normalize_roworder(inp, path)
    return 0


MTF_TARGET = 0.25


def _mtf(x, m):
    """PixInsight midtones transfer function: maps m -> 0.5, fixes 0 and 1.
    Exactly invertible: _mtf(_mtf(x, m), 1 - m) == x."""
    return ((m - 1.0) * x) / ((2.0 * m - 1.0) * x - m)


def sxt_linear(inp, starless_out, stars_out, neutral_out=None):
    """Star removal on LINEAR data via a reversible MTF round-trip.

    SXT is trained on stretched images, so: MTF stretch (median -> MTF_TARGET)
    -> sxt -> exact inverse MTF on the starless result. The stars layer is the
    exact complement `input - starless`, so starless + stars reproduces the
    input identically and linear recombination is plain addition.

    neutral_out (optional, RGB input only): additionally write a neutral
    (white-star) mask — the per-pixel channel mean replicated to all channels,
    still linear and dim. For filters that gut stellar continuum (Seestar LP)
    the colour in the stars layer is not trustworthy; a neutral mask sidesteps
    it for star-composition tools."""
    from astropy.io import fits

    import numpy as np

    with fits.open(inp) as hdul:
        data = hdul[0].data.astype(np.float64)
        header = hdul[0].header.copy()
    b = float(np.median(data))
    # b outside (0, MTF_TARGET) -> m=0.5 is the identity MTF (already bright/degenerate)
    if 0.0 < b < MTF_TARGET:
        m = b * (MTF_TARGET - 1.0) / (2.0 * MTF_TARGET * b - MTF_TARGET - b)
    else:
        m = 0.5
    base, ext = os.path.splitext(starless_out)
    tmp_in = base + "-mtf-tmp" + ext
    tmp_starless = base + "-mtf-starless-tmp" + ext
    stretched = _mtf(np.clip(data, 0.0, 1.0), m)
    fits.PrimaryHDU(stretched.astype(np.float32), header).writeto(tmp_in, overwrite=True)
    try:
        rc = run_product("sxt", tmp_in, tmp_starless, [])
        if rc != 0:
            return rc
        with fits.open(tmp_starless) as hdul:
            starless = _mtf(np.clip(hdul[0].data.astype(np.float64), 0.0, 1.0), 1.0 - m)
    finally:
        for path in (tmp_in, tmp_starless):
            if os.path.exists(path):
                os.remove(path)
    fits.PrimaryHDU(starless.astype(np.float32), header).writeto(
        starless_out, overwrite=True)
    stars = data - starless
    fits.PrimaryHDU(stars.astype(np.float32), header).writeto(
        stars_out, overwrite=True)
    if neutral_out is not None:
        if stars.ndim != 3:
            print("rcastro sxt-linear: neutral mask needs an RGB input, skipping",
                  file=sys.stderr)
        else:
            neutral = np.broadcast_to(stars.mean(axis=0), stars.shape)
            fits.PrimaryHDU(neutral.astype(np.float32), header).writeto(
                neutral_out, overwrite=True)
    return 0


def main(argv):
    if len(argv) >= 1 and argv[0] == "probe":
        print(probe_line())
        return 0
    if len(argv) in (4, 5) and argv[0] == "sxt-linear":
        return sxt_linear(argv[1], argv[2], argv[3],
                          argv[4] if len(argv) == 5 else None)
    if len(argv) >= 3 and argv[0] in PRODUCTS:
        return run_product(argv[0], argv[1], argv[2], argv[3:])
    print(f"usage: rcastro.py probe | {{{'|'.join(PRODUCTS)}}} IN OUT [args...]"
          " | sxt-linear IN STARLESS_OUT STARS_OUT [NEUTRAL_STARS_OUT]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
