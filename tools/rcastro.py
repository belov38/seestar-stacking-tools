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
        proc = subprocess.run(
            [cli, "--no-banner", "--json", product, "--license"],
            capture_output=True, text=True,
        )
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


def main(argv):
    if len(argv) >= 1 and argv[0] == "probe":
        print(probe_line())
        return 0
    print(f"usage: rcastro.py probe | {{{'|'.join(PRODUCTS)}}} IN OUT [args...]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
