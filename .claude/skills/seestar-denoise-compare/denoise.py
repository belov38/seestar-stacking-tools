#!/usr/bin/env python3
"""Denoise a Seestar stack with GraXpert and emit a FITS WITH its header intact.

GraXpert strips the FITS header to NAXIS only; this wrapper runs the denoise and then
restores OBJECT/RA/DEC/FOCALLEN/XPIXSZ/FILTER… from the input, so the output is ready for
plate solving / SPCC in one step.

Usage:
  denoise.py INPUT.fits OUTPUT.fits [STRENGTH] [--gpu]
    STRENGTH  GraXpert denoise_strength 0.0-1.0 (default 0.3).
    --gpu     use CoreML GPU (default CPU, which is the reliable path on macOS).

Env:
  GRAXPERT_BIN  override the GraXpert binary
                (default /Applications/GraXpert.app/Contents/MacOS/GraXpert).

Needs: astropy (for the header restore). GraXpert installed.
"""
import sys, os, re, json, subprocess, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "tools"))
from restore_fits_header import restore   # noqa: E402

GRAXPERT = os.environ.get(
    "GRAXPERT_BIN", "/Applications/GraXpert.app/Contents/MacOS/GraXpert")


def main():
    args = [a for a in sys.argv[1:] if a != "--gpu"]
    use_gpu = "--gpu" in sys.argv
    if len(args) < 2:
        print(__doc__); sys.exit(1)
    inp, out = args[0], args[1]
    strength = float(args[2]) if len(args) > 2 else 0.3

    if not os.path.exists(inp):
        sys.exit(f"input not found: {inp}")
    if not os.path.exists(GRAXPERT):
        sys.exit(f"GraXpert not found: {GRAXPERT} (set GRAXPERT_BIN)")

    # GraXpert appends .fits to the -output prefix; work in a temp prefix then move.
    prefix = re.sub(r"\.(fits?|fts)$", "", out, flags=re.I)
    produced = prefix + ".fits"

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as pf:
        json.dump({"denoise_strength": strength}, pf)
        prefs = pf.name

    try:
        cmd = [GRAXPERT, "-cli", "-cmd", "denoising",
               "-gpu", "true" if use_gpu else "false",
               "-preferences_file", prefs, "-output", prefix, inp]
        print(f"[denoise] strength={strength} gpu={use_gpu} -> {produced}", flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(produced):
            sys.stderr.write(r.stdout[-2000:] + r.stderr[-2000:])
            sys.exit(f"GraXpert failed (rc={r.returncode}); no {produced}")
    finally:
        os.unlink(prefs)

    n = restore(inp, produced)
    if produced != out:
        os.replace(produced, out)
    print(f"[denoise] done: {out}  (+{n} header cards restored)")


if __name__ == "__main__":
    main()
