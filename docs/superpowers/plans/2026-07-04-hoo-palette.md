# HOO/SHO Palette Masters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `tools/palette.py` (dual-band Ha/OIII extraction → linear HOO/SHO palette FITS masters, gated by an emission-separation metric) and wire it into `/seestar-pipeline` as auto Step 9b.

**Architecture:** Standalone CLI tool following the repo's tools/ pattern (pure numpy functions + argparse `main()`, tests alongside). The pipeline command doc gains a short Step 9b that calls the tool on the adopted SPCC master. The EMIT/SKIP threshold is calibrated on real data (C103 emission vs C76 cluster) in a dedicated task.

**Tech Stack:** Python 3.13 (`.venv/bin/python`), numpy, astropy, pytest. No new dependencies.

Spec: `docs/superpowers/specs/2026-07-04-hoo-palette-design.md` — read it before starting.

## Global Constraints

- Interpreter: `.venv/bin/python` (repo venv); run pytest as `.venv/bin/python -m pytest -q tools/test_palette.py` from the repo root.
- English only in code comments.
- Outputs are **linear** float32 FITS, header + WCS copied from the input, non-negative values.
- Only numpy + astropy in `tools/palette.py` (no sep/scipy needed).
- Never commit `.fit`/`.png` image data (gitignored); commit code, docs, FINDINGS.md only.
- Verdict line format is fixed and parseable: `PALETTES: EMIT (separation=0.421, threshold=0.15)`.
- Exit code 0 on both EMIT and SKIP; non-zero only on real errors (bad input).

---

### Task 1: Ha/OIII extraction + background neutralization

**Files:**
- Create: `tools/palette.py`
- Test: `tools/test_palette.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `load_rgb_master(path: str) -> tuple[np.ndarray, fits.Header]` — float64 `(3,H,W)` cube + header copy; `sys.exit(str)` on non-RGB input.
  - `extract_ha_oiii(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]` — 2D float64 `(ha, oiii)`.
  - `neutralize_background(ha, oiii) -> tuple[np.ndarray, np.ndarray, float]` — `(ha_n, oiii_n, pedestal)`.
  - Test helpers `_continuum(...)` and `_emission(...)` reused by later tasks.

- [ ] **Step 1: Write the failing tests**

Create `tools/test_palette.py`:

```python
"""Tests for palette.py: dual-band Ha/OIII extraction, gate metric, HOO/SHO composition."""
import os
import tempfile

import numpy as np
import pytest
from astropy.io import fits

import palette


def _continuum(h=200, w=150, seed=0):
    """Continuum-only cube: identical structure in all channels, per-channel scale only."""
    rng = np.random.default_rng(seed)
    base = rng.normal(0.1, 0.005, (h, w))
    yy, xx = np.mgrid[0:h, 0:w]
    for _ in range(15):
        cy = rng.integers(10, h - 10)
        cx = rng.integers(10, w - 10)
        base = base + rng.uniform(0.5, 1.0) * np.exp(
            -((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * 2.0**2)
        )
    return np.stack([base * s for s in (1.0, 0.9, 0.8)])


def _emission(h=200, w=150, seed=0):
    """Emission cube: disjoint Ha-only blob at (60,40) and OIII-only blob at (140,100)."""
    rng = np.random.default_rng(seed)
    r = rng.normal(0.1, 0.005, (h, w))
    g = rng.normal(0.1, 0.005, (h, w))
    b = rng.normal(0.1, 0.005, (h, w))
    yy, xx = np.mgrid[0:h, 0:w]
    ha_blob = 0.8 * np.exp(-((yy - 60) ** 2 + (xx - 40) ** 2) / (2 * 12**2))
    o_blob = 0.8 * np.exp(-((yy - 140) ** 2 + (xx - 100) ** 2) / (2 * 12**2))
    return np.stack([r + ha_blob, g + o_blob, b + o_blob])


def _write(path, cube, header=None):
    fits.writeto(path, cube.astype(np.float32), header, overwrite=True)


def test_extract_channel_mapping():
    ha, oiii = palette.extract_ha_oiii(_emission())
    assert ha[60, 40] > 0.5 and ha[140, 100] < 0.3      # Ha blob only in R
    assert oiii[140, 100] > 0.5 and oiii[60, 40] < 0.3  # OIII blob only in G+B


def test_neutralize_background_equalizes_medians():
    ha, oiii = palette.extract_ha_oiii(_emission())
    ha_n, oiii_n, pedestal = palette.neutralize_background(ha, oiii)
    assert abs(np.median(ha_n) - np.median(oiii_n)) < 1e-6
    assert abs(np.median(ha_n) - pedestal) < 1e-6
    # structure untouched: blob amplitude over its own background is preserved
    assert abs((ha_n[60, 40] - np.median(ha_n)) - (ha[60, 40] - np.median(ha))) < 1e-9


def test_load_rgb_master_rejects_mono():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "mono.fit")
        fits.writeto(p, np.zeros((50, 50), dtype=np.float32), overwrite=True)
        with pytest.raises(SystemExit):
            palette.load_rgb_master(p)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tools/test_palette.py`
Expected: FAIL / error with `ModuleNotFoundError: No module named 'palette'`.

- [ ] **Step 3: Write the implementation**

Create `tools/palette.py`:

```python
#!/usr/bin/env python
"""Dual-band palette masters (HOO / synthetic SHO) from a Seestar LP-filter RGB master.

The Seestar LP filter is dual-band: it passes only Ha (656 nm) and OIII (~500 nm).
On the IMX585 OSC sensor the red channel carries Ha; green and blue carry OIII
(green leaks ~10-15% of Ha). This tool splits a linear RGB master into Ha/OIII,
measures whether the target actually shows emission-line separation, and (on EMIT)
writes linear, stretch-ready palette masters with the input header + WCS intact:

  <base>_HOO.fit   R=Ha, G=OIII, B=OIII               (red hydrogen / teal oxygen)
  <base>_SHO.fit   R=Ha, G=a*Ha+(1-a)*OIII, B=OIII    (synthetic Hubble-style; a=0.3)

Continuum targets (galaxies, clusters) have Ha proportional to OIII everywhere, so
the log-ratio spread over signal pixels is small -> SKIP (a palette of a continuum
target is just the same grey image twice). Verdict line (parseable, exit code 0):

  PALETTES: EMIT (separation=0.421, threshold=0.15)
  PALETTES: SKIP (separation=0.062, threshold=0.15)

Usage:
  palette.py MASTER.fit [--outdir DIR] [--basename NAME] [--sho-alpha 0.3]
             [--force] [--metric-only]
"""
import argparse
import os
import sys

import numpy as np
from astropy.io import fits


def load_rgb_master(path):
    """Load a linear RGB FITS as float64 (3,H,W) + a copy of its header."""
    with fits.open(path, memmap=False, ignore_missing_simple=True) as hdul:
        data = hdul[0].data
        header = hdul[0].header.copy()
    if data is None or data.ndim != 3 or data.shape[0] != 3:
        shape = None if data is None else data.shape
        sys.exit(f"{path}: palette extraction needs an RGB master (3,H,W), got shape {shape}")
    return data.astype(np.float64), header


def extract_ha_oiii(rgb):
    """Dual-band split: red pixels see Ha, green+blue see OIII."""
    ha = rgb[0]
    oiii = 0.5 * rgb[1] + 0.5 * rgb[2]
    return ha, oiii


def neutralize_background(ha, oiii):
    """Subtract each channel's own median background, re-add a common pedestal.

    Keeps the data linear and the structure untouched, but makes the background
    neutral grey so the composites carry no colour cast.
    """
    bg_ha = float(np.median(ha))
    bg_oiii = float(np.median(oiii))
    pedestal = 0.5 * (bg_ha + bg_oiii)
    return ha - bg_ha + pedestal, oiii - bg_oiii + pedestal, pedestal
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tools/test_palette.py`
Expected: 3 passed (`test_extract_channel_mapping`, `test_neutralize_background_equalizes_medians`, `test_load_rgb_master_rejects_mono`).

- [ ] **Step 5: Commit**

```bash
git add tools/palette.py tools/test_palette.py
git commit -m "palette: Ha/OIII extraction + background neutralization"
```

---

### Task 2: Emission-separation gate metric

**Files:**
- Modify: `tools/palette.py` (append after `neutralize_background`)
- Test: `tools/test_palette.py` (append)

**Interfaces:**
- Consumes: `extract_ha_oiii`, test helpers `_continuum` / `_emission` from Task 1.
- Produces:
  - `SEPARATION_THRESHOLD: float` — module constant (initial 0.15; calibrated on real data in Task 4).
  - `emission_separation(ha: np.ndarray, oiii: np.ndarray) -> float | None` — normalized MAD of `log2(Ha/OIII)` over the signal mask; `None` when the mask is degenerate (no usable signal).

- [ ] **Step 1: Write the failing tests**

Append to `tools/test_palette.py`:

```python
def test_separation_low_for_continuum():
    ha, oiii = palette.extract_ha_oiii(_continuum())
    sep = palette.emission_separation(ha, oiii)
    assert sep is not None
    assert sep < palette.SEPARATION_THRESHOLD


def test_separation_high_for_emission():
    ha, oiii = palette.extract_ha_oiii(_emission())
    sep = palette.emission_separation(ha, oiii)
    assert sep is not None
    assert sep > palette.SEPARATION_THRESHOLD


def test_separation_none_for_empty_field():
    rng = np.random.default_rng(3)
    flat = rng.normal(0.1, 0.005, (3, 120, 90))  # noise only, no signal above bg+3sigma
    ha, oiii = palette.extract_ha_oiii(flat)
    assert palette.emission_separation(ha, oiii) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tools/test_palette.py`
Expected: 3 new tests FAIL with `AttributeError: module 'palette' has no attribute 'emission_separation'`; the 3 Task-1 tests still pass.

- [ ] **Step 3: Write the implementation**

Append to `tools/palette.py` (after `neutralize_background`):

```python
# Emission-separation threshold: normalized MAD of log2(Ha/OIII) over signal pixels.
# Initial value; calibrated on real Seestar data (C103 Tarantula SPCC master must
# EMIT, C76 open-cluster stack must SKIP) — measured values in FINDINGS.md.
SEPARATION_THRESHOLD = 0.15

# Signal mask needs at least this many usable pixels for a meaningful spread.
MIN_MASK_PIXELS = 100


def _median_madn(x):
    med = float(np.median(x))
    madn = float(np.median(np.abs(x - med)) * 1.4826)
    return med, madn


def emission_separation(ha, oiii):
    """Normalized MAD of log2(Ha/OIII) over signal pixels, or None if degenerate.

    Continuum sources (stars, galaxies) have Ha proportional to OIII everywhere,
    so the ratio spread is small; emission targets diverge region by region.
    """
    combined = ha + oiii
    med_c, madn_c = _median_madn(combined)
    if madn_c <= 0:
        return None
    mask = combined > med_c + 3.0 * madn_c
    if int(mask.sum()) < MIN_MASK_PIXELS:
        return None
    med_ha, _ = _median_madn(ha)
    med_oiii, _ = _median_madn(oiii)
    ha_sig = ha[mask] - med_ha
    oiii_sig = oiii[mask] - med_oiii
    valid = (ha_sig > 0) & (oiii_sig > 0)
    if int(valid.sum()) < MIN_MASK_PIXELS:
        return None
    ratio = np.log2(ha_sig[valid] / oiii_sig[valid])
    _, spread = _median_madn(ratio)
    return spread
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tools/test_palette.py`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/palette.py tools/test_palette.py
git commit -m "palette: emission-separation gate metric"
```

---

### Task 3: HOO/SHO composition, FITS output, CLI

**Files:**
- Modify: `tools/palette.py` (append composition + `main`)
- Test: `tools/test_palette.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 1–2.
- Produces:
  - `compose_hoo(ha, oiii) -> np.ndarray` — float32 `(3,H,W)`: `[Ha, OIII, OIII]`.
  - `compose_sho(ha, oiii, alpha: float) -> np.ndarray` — float32 `(3,H,W)`: `[Ha, alpha*Ha+(1-alpha)*OIII, OIII]`.
  - `write_master(path, cube, header, formula: str)` — non-negative float32 FITS, header + HISTORY line.
  - `main(argv=None)` — CLI; prints the verdict line and (on EMIT or `--force`, unless `--metric-only`) writes `<base>_HOO.fit` / `<base>_SHO.fit` and prints `wrote: <path>` per file.

- [ ] **Step 1: Write the failing tests**

Append to `tools/test_palette.py`:

```python
def test_cli_emission_emits_both_masters(capsys):
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "master.fit")
        hdr = fits.Header({"OBJECT": "TEST", "CRVAL1": 84.67, "CRVAL2": -69.1, "LIVETIME": 3600.0})
        _write(src, _emission(), hdr)
        palette.main([src, "--outdir", d, "--basename", "TEST_final"])
        out = capsys.readouterr().out
        assert "PALETTES: EMIT" in out
        hoo_path = os.path.join(d, "TEST_final_HOO.fit")
        sho_path = os.path.join(d, "TEST_final_SHO.fit")
        assert os.path.exists(hoo_path) and os.path.exists(sho_path)
        with fits.open(hoo_path) as h:
            hoo, hoo_hdr = h[0].data, h[0].header
        # channel mapping: R carries the Ha blob, G and B carry the OIII blob
        assert hoo[0, 60, 40] > hoo[0, 140, 100]
        assert hoo[1, 140, 100] > hoo[1, 60, 40]
        assert np.array_equal(hoo[1], hoo[2])
        # sanity: float32, finite, non-negative
        assert hoo.dtype == np.float32
        assert np.isfinite(hoo).all() and (hoo >= 0).all()
        # header + WCS preserved, HISTORY added
        assert hoo_hdr["OBJECT"] == "TEST" and abs(hoo_hdr["CRVAL1"] - 84.67) < 1e-9
        assert any("palette.py" in str(c) for c in hoo_hdr.get("HISTORY", []))


def test_cli_sho_green_is_blend():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "master.fit")
        _write(src, _emission())
        palette.main([src, "--outdir", d, "--basename", "T", "--sho-alpha", "0.3"])
        with fits.open(os.path.join(d, "T_SHO.fit")) as h:
            sho = h[0].data.astype(np.float64)
        # at the Ha blob, G carries alpha*Ha: above background but below R
        bg = np.median(sho[1])
        assert sho[1, 60, 40] > bg + 0.1
        assert sho[1, 60, 40] < sho[0, 60, 40]
        # B has no Ha contribution at the Ha blob
        assert sho[2, 60, 40] < bg + 0.1


def test_cli_continuum_skips_without_files(capsys):
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "master.fit")
        _write(src, _continuum())
        palette.main([src, "--outdir", d, "--basename", "T"])
        assert "PALETTES: SKIP" in capsys.readouterr().out
        assert not os.path.exists(os.path.join(d, "T_HOO.fit"))
        assert not os.path.exists(os.path.join(d, "T_SHO.fit"))


def test_cli_force_writes_on_skip():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "master.fit")
        _write(src, _continuum())
        palette.main([src, "--outdir", d, "--basename", "T", "--force"])
        assert os.path.exists(os.path.join(d, "T_HOO.fit"))
        assert os.path.exists(os.path.join(d, "T_SHO.fit"))


def test_cli_metric_only_writes_nothing(capsys):
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "master.fit")
        _write(src, _emission())
        palette.main([src, "--outdir", d, "--basename", "T", "--metric-only"])
        assert "PALETTES: EMIT" in capsys.readouterr().out
        assert not os.path.exists(os.path.join(d, "T_HOO.fit"))


def test_cli_empty_field_skips_gracefully(capsys):
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "master.fit")
        rng = np.random.default_rng(3)
        _write(src, rng.normal(0.1, 0.005, (3, 120, 90)))
        palette.main([src, "--outdir", d, "--basename", "T"])
        out = capsys.readouterr().out
        assert "PALETTES: SKIP" in out and "separation=n/a" in out
        assert not os.path.exists(os.path.join(d, "T_HOO.fit"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tools/test_palette.py`
Expected: 6 new tests FAIL with `AttributeError: module 'palette' has no attribute 'main'`; the 6 earlier tests still pass.

- [ ] **Step 3: Write the implementation**

Append to `tools/palette.py` (after `emission_separation`):

```python
def compose_hoo(ha, oiii):
    """HOO: R=Ha, G=OIII, B=OIII."""
    return np.stack([ha, oiii, oiii]).astype(np.float32)


def compose_sho(ha, oiii, alpha):
    """Synthetic SHO: R=Ha, G=alpha*Ha+(1-alpha)*OIII, B=OIII (no real SII in dual-band)."""
    return np.stack([ha, alpha * ha + (1.0 - alpha) * oiii, oiii]).astype(np.float32)


def write_master(path, cube, header, formula):
    """Write a linear float32 palette master, input header + WCS intact."""
    hdr = header.copy()
    hdr.add_history(f"palette.py: {formula}")
    fits.writeto(path, np.clip(cube, 0.0, None).astype(np.float32), hdr, overwrite=True)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("master", help="linear RGB FITS master (e.g. the SPCC output)")
    ap.add_argument("--outdir", default=None, help="output dir (default: next to the input)")
    ap.add_argument("--basename", default=None,
                    help="output name stem (default: input filename stem)")
    ap.add_argument("--sho-alpha", type=float, default=0.3,
                    help="Ha fraction in the SHO green channel (default 0.3)")
    ap.add_argument("--force", action="store_true",
                    help="write palette masters even on a SKIP verdict")
    ap.add_argument("--metric-only", action="store_true",
                    help="print the verdict only, write nothing")
    args = ap.parse_args(argv)

    rgb, header = load_rgb_master(args.master)
    ha, oiii = extract_ha_oiii(rgb)
    separation = emission_separation(ha, oiii)
    if separation is None:
        emit = False
        print(f"PALETTES: SKIP (separation=n/a, threshold={SEPARATION_THRESHOLD:g})"
              " — no usable signal mask")
    else:
        emit = separation >= SEPARATION_THRESHOLD
        verdict = "EMIT" if emit else "SKIP"
        print(f"PALETTES: {verdict} (separation={separation:.3f},"
              f" threshold={SEPARATION_THRESHOLD:g})")

    if args.metric_only or (not emit and not args.force):
        return

    outdir = args.outdir or os.path.dirname(os.path.abspath(args.master))
    os.makedirs(outdir, exist_ok=True)
    base = args.basename or os.path.splitext(os.path.basename(args.master))[0]
    ha_n, oiii_n, _ = neutralize_background(ha, oiii)
    alpha = args.sho_alpha

    hoo_path = os.path.join(outdir, f"{base}_HOO.fit")
    write_master(hoo_path, compose_hoo(ha_n, oiii_n), header,
                 "HOO: R=Ha(R), G=B=OIII((G+B)/2), bg-neutralized, linear")
    print(f"wrote: {hoo_path}")

    sho_path = os.path.join(outdir, f"{base}_SHO.fit")
    write_master(sho_path, compose_sho(ha_n, oiii_n, alpha), header,
                 f"SHO: R=Ha, G={alpha:g}*Ha+{1 - alpha:g}*OIII, B=OIII,"
                 " bg-neutralized, linear")
    print(f"wrote: {sho_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tools/test_palette.py`
Expected: 12 passed.

- [ ] **Step 5: Run the full tools suite to check for fallout**

Run: `.venv/bin/python -m pytest -q tools`
Expected: all pass (palette tests + existing preview/astrobin/score tests).

- [ ] **Step 6: Commit**

```bash
git add tools/palette.py tools/test_palette.py
git commit -m "palette: HOO/SHO composition + CLI with EMIT/SKIP gate"
```

---

### Task 4: Calibrate the threshold on real data (C103 vs C76), record in FINDINGS.md

**Files:**
- Modify: `tools/palette.py` (the `SEPARATION_THRESHOLD` constant + its comment)
- Modify: `FINDINGS.md` (append a short section)

**Interfaces:**
- Consumes: the complete CLI from Task 3.
- Produces: the calibrated `SEPARATION_THRESHOLD` value that Step 9b relies on.

**Real data paths (exist on this machine; image data is gitignored — never commit it):**
- Emission target (must EMIT): `/Users/ib/prj-other/astro/_stacking-C103-Tarantul-nebula/C103_final_spcc.fit`
- Continuum target (must SKIP): the Seestar on-device stack inside
  `/Users/ib/prj-other/astro/_stacking-caldwell-76/C 76/` — pick the real
  `Stacked_*.fit` (ignore `._*` AppleDouble files). If it is a 2D Bayer frame
  rather than an RGB stack, note that and instead use any other continuum RGB
  master available (a galaxy/cluster SPCC master); if none exists, keep the
  C76 raw-stack attempt out of FINDINGS and calibrate the SKIP side on the
  synthetic continuum value from the test suite, saying so explicitly.

- [ ] **Step 1: Measure both sides**

```bash
.venv/bin/python tools/palette.py \
  "/Users/ib/prj-other/astro/_stacking-C103-Tarantul-nebula/C103_final_spcc.fit" --metric-only
.venv/bin/python tools/palette.py \
  "/Users/ib/prj-other/astro/_stacking-caldwell-76/C 76/Stacked_13_C 76_60.0s_LP_20260614-193528.fit" --metric-only
```

Expected: two verdict lines with measured separations — C103 well above C76.
Record both numbers.

- [ ] **Step 2: Set the threshold**

Set `SEPARATION_THRESHOLD` in `tools/palette.py` to the geometric mean of the two
measured separations, rounded to two decimals (e.g. measured 0.45 and 0.05 →
`sqrt(0.45*0.05) ≈ 0.15`). Update the constant's comment with the actual measured
values, e.g.:

```python
# Emission-separation threshold: normalized MAD of log2(Ha/OIII) over signal pixels.
# Calibrated on real Seestar data (see FINDINGS.md): C103 Tarantula SPCC master
# -> 0.45 (EMIT), C76 open-cluster stack -> 0.05 (SKIP); threshold = geometric mean.
SEPARATION_THRESHOLD = 0.15
```

If the synthetic-test values in `test_separation_low_for_continuum` /
`test_separation_high_for_emission` end up on the wrong side of the new threshold,
adjust the synthetic amplitudes is NOT the fix — the threshold is data-calibrated;
instead relax those two asserts to compare against `palette.SEPARATION_THRESHOLD`
with the actual synthetic values in mind (they compare against the constant
already, so they only break if the calibrated threshold crosses the synthetic
values; report it if so rather than papering over).

- [ ] **Step 3: Run the palette end-to-end on C103 and view the previews**

```bash
.venv/bin/python tools/palette.py \
  "/Users/ib/prj-other/astro/_stacking-C103-Tarantul-nebula/C103_final_spcc.fit" \
  --outdir /private/tmp/claude-501/-Users-ib-prj-other-astro-explore-fits/d8ea54be-2590-44c7-b484-0903300006b7/scratchpad \
  --basename C103_cal
.venv/bin/python tools/preview.py \
  /private/tmp/claude-501/-Users-ib-prj-other-astro-explore-fits/d8ea54be-2590-44c7-b484-0903300006b7/scratchpad/C103_cal_HOO.fit \
  --out /private/tmp/claude-501/-Users-ib-prj-other-astro-explore-fits/d8ea54be-2590-44c7-b484-0903300006b7/scratchpad/C103_cal_HOO.png \
  --title "HOO calibration check"
.venv/bin/python tools/preview.py \
  /private/tmp/claude-501/-Users-ib-prj-other-astro-explore-fits/d8ea54be-2590-44c7-b484-0903300006b7/scratchpad/C103_cal_SHO.fit \
  --out /private/tmp/claude-501/-Users-ib-prj-other-astro-explore-fits/d8ea54be-2590-44c7-b484-0903300006b7/scratchpad/C103_cal_SHO.png \
  --title "SHO calibration check"
```

Expected: `PALETTES: EMIT`, two `.fit` written. **View both PNGs** (Read tool):
HOO must show red Ha filaments vs teal OIII core regions; SHO must show the
same structure with a golden/orange Ha cast. WCS check:
`.venv/bin/python -c "from astropy.io import fits; h=fits.getheader('/private/tmp/claude-501/-Users-ib-prj-other-astro-explore-fits/d8ea54be-2590-44c7-b484-0903300006b7/scratchpad/C103_cal_HOO.fit'); print(h['OBJECT'], h['CRVAL1'], h['CRVAL2'])"`
prints OBJECT and the WCS reference coordinates.

- [ ] **Step 4: Append the calibration to FINDINGS.md**

Read FINDINGS.md first and match its existing tone/format (short, numbers-first).
Append a section along these lines, with the real measured numbers substituted:

```markdown
## Palette gate (HOO/SHO) — emission-separation threshold

Metric: normalized MAD of log2(Ha/OIII) over signal pixels (bg+3sigma mask),
Ha=R, OIII=(G+B)/2 from the linear RGB master.

- C103 Tarantula SPCC master: separation 0.45 -> EMIT (strong OIII core vs Ha shells)
- C76 open cluster (on-device stack): separation 0.05 -> SKIP (pure continuum)
- Threshold: 0.15 (geometric mean, rounded)
```

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/python -m pytest -q tools`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add tools/palette.py FINDINGS.md
git commit -m "palette: calibrate separation threshold on C103/C76 (FINDINGS)"
```

---

### Task 5: Pipeline Step 9b + README sync

**Files:**
- Modify: `.claude/commands/seestar-pipeline.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: the `tools/palette.py` CLI exactly as built in Task 3
  (`--outdir`, `--basename`, verdict line `PALETTES: EMIT|SKIP (separation=..., threshold=...)`).
- Produces: pipeline behaviour — Step 9b between SPCC (Step 9) and stretch (Step 10).

- [ ] **Step 1: Add the Step 9b row to the steps table**

In `.claude/commands/seestar-pipeline.md`, the steps table currently ends with:

```
| 9 | *(SPCC colour calibration — Siril, no skill)* | `05_stretch/` | SPCC reports `succeeded` and the star-core G/R moves toward 1 — auto-adopt the calibrated master | **warn + continue** if SPCC fails (no internet / no `siril-spcc-database`) — keep the un-calibrated solved master |
| 10 | *(stretch — manual, no skill)* | `05_stretch/` | — | **always present** the final result (stretch is the user's call) |
```

Insert between those two rows:

```
| 9b | *(palette masters HOO/SHO — `tools/palette.py`, no skill)* | `05_stretch/` | always — EMIT or SKIP decided by the emission-separation metric; log either way | never |
```

- [ ] **Step 2: Add the Step 9b note to "Notes per step"**

In the same file, after the **Step 9 (SPCC colour calibration)** note bullet and
before **Step 10 (stretch)**, insert:

````markdown
- **Step 9b (palette masters HOO/SHO):** the LP filter is dual-band (Ha 656 nm +
  OIII ~500 nm), so an emission target carries a second free palette in the same
  data (Ha lives in R, OIII in G+B). Run on the adopted master — `<OBJECT>_final_spcc.fit`,
  or `<OBJECT>_final_solved.fit` if SPCC failed:
  ```
  .venv/bin/python tools/palette.py 05_stretch/<OBJECT>_final_spcc.fit \
    --outdir 05_stretch --basename <OBJECT>_final
  ```
  It prints one parseable line: `PALETTES: EMIT (separation=..., threshold=...)` or
  `PALETTES: SKIP (...)`. On **EMIT** it writes `<OBJECT>_final_HOO.fit` (R=Ha,
  G=B=OIII) and `<OBJECT>_final_SHO.fit` (synthetic SHO, golden Ha after stretch) —
  linear, header + WCS intact, stretch-ready like the SPCC master. Render a preview
  PNG for each with `tools/preview.py` (no `--ref`) into **`05_stretch/`** (not
  `previews/` — they must survive Step 11 cleanup) as
  `05_stretch/<OBJECT>_final_HOO.png` / `<OBJECT>_final_SHO.png`, and drop the
  `validate here:` lines for both. On **SKIP** (continuum target — galaxy/cluster:
  Ha≈OIII everywhere, a palette would be grey) log the verdict line with the
  measured separation to REPORT.md and move on. This step is always AUTO — never
  stop to ask; log the verdict to REPORT.md in both cases.
````

- [ ] **Step 3: Extend the Finish step and cleanup keep-list**

In the **Finish** section, item 3 currently reads:

```
3. **Copy the deliverables next to the input** so the user finds them with their data — into
   `DATADIR` (the parent of `LIGHTS/`, or beside the input FITS): the calibrated master
   `<OBJECT>_final_spcc.fit` (and `<OBJECT>_final_solved.fit` if SPCC ran — the pre-SPCC version),
   `<OBJECT>_astrobin.txt`, `<OBJECT>_astrobin_acquisition.csv`, `<OBJECT>_final_stretch.png`.
```

Replace with:

```
3. **Copy the deliverables next to the input** so the user finds them with their data — into
   `DATADIR` (the parent of `LIGHTS/`, or beside the input FITS): the calibrated master
   `<OBJECT>_final_spcc.fit` (and `<OBJECT>_final_solved.fit` if SPCC ran — the pre-SPCC version),
   the palette masters `<OBJECT>_final_HOO.fit` + `<OBJECT>_final_SHO.fit` and their PNGs
   (if Step 9b emitted), `<OBJECT>_astrobin.txt`, `<OBJECT>_astrobin_acquisition.csv`,
   `<OBJECT>_final_stretch.png`.
```

In the same section, item 2 (astrobin.txt) — after the processing-chain sentence, add
one sentence: `On EMIT, mention the available HOO/SHO palette masters in the description.`

In **Step 11 — Offer cleanup**, the Keep bullet currently reads:

```
   - **Keep:** `05_stretch/` (SPCC-calibrated master `<OBJECT>_final_spcc.fit`, the pre-SPCC
     `<OBJECT>_final_solved.fit`, the final autostretch PNG `<OBJECT>_final_stretch.png`,
     `astrobin.txt`, `astrobin_acquisition.csv`), `REPORT.md`, and the deliverable **copies in
     `DATADIR`**. The final stretch preview lives here (in `05_stretch/`, not `previews/`), so
     removing `previews/` never loses it.
```

Replace with:

```
   - **Keep:** `05_stretch/` (SPCC-calibrated master `<OBJECT>_final_spcc.fit`, the pre-SPCC
     `<OBJECT>_final_solved.fit`, the palette masters `<OBJECT>_final_HOO.fit` /
     `<OBJECT>_final_SHO.fit` + their PNGs when Step 9b emitted, the final autostretch PNG
     `<OBJECT>_final_stretch.png`, `astrobin.txt`, `astrobin_acquisition.csv`), `REPORT.md`,
     and the deliverable **copies in `DATADIR`**. The final stretch preview and the palette
     previews live here (in `05_stretch/`, not `previews/`), so removing `previews/` never
     loses them.
```

- [ ] **Step 4: Sync README.md**

Two edits in `README.md`:

1. In the `### Run the whole pipeline` paragraph, after "…SPCC-colour-calibrate the linear
   master (Siril, Seestar S30 sensor + LP-filter profiles)", extend the sentence so it reads
   "…SPCC-colour-calibrate the linear master (Siril, Seestar S30 sensor + LP-filter profiles),
   derive HOO/SHO palette masters when the target shows real Ha/OIII emission separation
   (measured, auto-skipped for galaxies/clusters), and finish with an autostretch preview."

2. In the `### tools/` list, after the `tools/score_subs.py` bullet, add:

```markdown
- `tools/palette.py MASTER.fit [--outdir DIR --basename NAME]` — dual-band palette
  masters from an LP-filter RGB master: splits Ha (R) / OIII (G+B), gates on a
  measured emission-separation metric (EMIT/SKIP), writes linear `*_HOO.fit` +
  `*_SHO.fit` with header/WCS intact. Backs the pipeline's Step 9b.
```

- [ ] **Step 5: Verify docs consistency**

Run: `grep -n "9b\|palette" .claude/commands/seestar-pipeline.md README.md | head -30`
Expected: the table row, the Step 9b note, Finish/cleanup mentions, and both README
edits all present; no stray "Step 10.5"/"Step 12" numbering anywhere.

- [ ] **Step 6: Run the full test suite one last time**

Run: `.venv/bin/python -m pytest -q tools`
Expected: all pass (docs changes can't break tests — this is the final gate).

- [ ] **Step 7: Commit**

```bash
git add .claude/commands/seestar-pipeline.md README.md
git commit -m "Pipeline: add Step 9b — HOO/SHO palette masters; README sync"
```
