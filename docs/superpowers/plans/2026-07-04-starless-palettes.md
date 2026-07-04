# Starless Palettes (SyQon via headless Siril) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Optional starless sub-step for pipeline Step 10 — `tools/starless.py` drives SyQon Starless.py through headless Siril; `palette.py --starless` composes palettes from the starless image and re-adds natural-colour stars linearly.

**Architecture:** A thin wrapper generates a temp `.ssf` (`load master` → `pyscript Starless`), runs `siril-cli -d outdir -s ssf`, renames SyQon's `starless_*` output to `<base>_starless.fit` and computes `<base>_stars.fit = clip(master − starless, 0)`. palette.py gains one flag. Everything degrades to today's behaviour when siril-cli or the SyQon script is missing.

**Tech Stack:** Python 3.13 (`.venv/bin/python`), numpy, astropy, pytest; external: siril-cli + SyQon Starless.py (Siril scripts repo) + zenith.pt (already installed on this machine).

Spec: `docs/superpowers/specs/2026-07-04-starless-palettes-design.md` — read it first.

## Global Constraints

- Interpreter `.venv/bin/python`; tests: `.venv/bin/python -m pytest -q tools/test_starless.py tools/test_palette.py` from repo root.
- English only in code comments.
- Outputs: linear float32 FITS, input header + WCS preserved, HISTORY line added, non-negative.
- No new pip dependencies (numpy + astropy only; siril-cli is an existing external).
- Never commit `.fit`/`.png`/`.ssf` run artifacts; commit code + docs + FINDINGS.md only.
- Parseable verdict lines: `SYQON: OK (...)` / `SYQON: NOT INSTALLED (...)`; exit 0 on NOT INSTALLED (dormant), non-zero only on real failures.
- Unit tests never run real inference (minutes on MPS) — `run_siril` is stubbed.

---

### Task 1: Live spike — pin the headless `pyscript` invocation

No repo files change (scratchpad only). The deliverable is knowledge: the exact
`.ssf` line that runs SyQon Starless headless, and the output filename pattern.
SyQon Starless.py and zenith.pt are installed on this machine, so this runs now.

**Interfaces:**
- Produces: the working `pyscript` reference (e.g. `pyscript Starless`) → becomes
  the `PYSCRIPT_LINE` constant in Task 2, and the observed output name pattern
  (expected `starless_<stem>.fit` in the siril working dir).

- [ ] **Step 1: Create a small synthetic RGB FITS in the scratchpad**

```bash
SCRATCH=/private/tmp/claude-501/-Users-ib-prj-other-astro-explore-fits/d8ea54be-2590-44c7-b484-0903300006b7/scratchpad
.venv/bin/python - <<'EOF'
import numpy as np
from astropy.io import fits
rng = np.random.default_rng(0)
h, w = 200, 150
cube = rng.normal(0.1, 0.005, (3, h, w))
yy, xx = np.mgrid[0:h, 0:w]
for cy, cx in ((60, 40), (140, 100)):
    cube += 0.7 * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * 2.0**2))
fits.writeto("/private/tmp/claude-501/-Users-ib-prj-other-astro-explore-fits/d8ea54be-2590-44c7-b484-0903300006b7/scratchpad/spike_in.fit",
             cube.astype(np.float32), fits.Header({"OBJECT": "SPIKE"}), overwrite=True)
print("ok")
EOF
```

- [ ] **Step 2: Write the candidate ssf and run headless Siril**

```bash
SCRATCH=/private/tmp/claude-501/-Users-ib-prj-other-astro-explore-fits/d8ea54be-2590-44c7-b484-0903300006b7/scratchpad
printf 'requires 1.4.0\nsetext fit\nload "%s/spike_in.fit"\npyscript Starless\n' "$SCRATCH" > "$SCRATCH/spike.ssf"
SIRIL=$(command -v siril-cli || echo /Applications/Siril.app/Contents/MacOS/siril-cli)
"$SIRIL" -d "$SCRATCH" -s "$SCRATCH/spike.ssf" 2>&1 | tail -20
ls "$SCRATCH" | grep -i starless
```

Expected: siril runs the script (torch loads zenith, one-tile inference, seconds
on a 200×150 image) and `starless_spike_in.fit` appears.
If `pyscript Starless` is rejected, try in order, re-running the same command:
`pyscript Starless.py`, `pyscript "SyQon/Starless.py"`, `pyscript "SyQon/Starless"`.
Record the working line and the actual output filename. If the script errors on
sirilpy connection or venv, capture the full output — that's a blocker to report,
not to work around silently.

- [ ] **Step 3: Record the result**

No commit. Note the working `pyscript` line + output name pattern for Task 2's
`PYSCRIPT_LINE` constant and output lookup.

---

### Task 2: `tools/starless.py` — probe, ssf, run flow, outputs

**Files:**
- Create: `tools/starless.py`
- Test: `tools/test_starless.py`

**Interfaces:**
- Consumes: the `pyscript` line pinned in Task 1.
- Produces:
  - `find_siril_cli() -> str | None`, `find_starless_script() -> str | None`
  - `probe() -> tuple[bool, str]` — message is the `SYQON: ...` verdict line
  - `write_ssf(outdir: str, master_abs: str) -> str` — path to the written ssf
  - `run_siril(siril_cli: str, outdir: str, ssf_path: str) -> int` — isolated for stubbing
  - `main(argv=None)` — CLI: `MASTER.fit --outdir DIR [--basename NAME] [--probe-only]`;
    writes `<base>_starless.fit` + `<base>_stars.fit`, prints `wrote:` lines.

- [ ] **Step 1: Write the failing tests**

Create `tools/test_starless.py`:

```python
"""Tests for starless.py: probe, ssf generation, run flow with a stubbed siril call."""
import os

import numpy as np
import pytest
from astropy.io import fits

import starless


def _rgb_with_star(path, h=80, w=60):
    """Flat background + one star present in all three channels at (40, 30)."""
    rng = np.random.default_rng(0)
    cube = rng.normal(0.1, 0.005, (3, h, w))
    yy, xx = np.mgrid[0:h, 0:w]
    star = 0.7 * np.exp(-((yy - 40) ** 2 + (xx - 30) ** 2) / (2 * 2.0**2))
    cube = cube + star
    fits.writeto(path, cube.astype(np.float32),
                 fits.Header({"OBJECT": "T", "CRVAL1": 10.0}), overwrite=True)
    return star


def test_probe_not_installed(monkeypatch, capsys):
    monkeypatch.setattr(starless, "find_siril_cli", lambda: None)
    starless.main(["/nonexistent.fit", "--probe-only"])
    assert "SYQON: NOT INSTALLED" in capsys.readouterr().out


def test_probe_ok(monkeypatch, capsys):
    monkeypatch.setattr(starless, "find_siril_cli", lambda: "/bin/echo")
    monkeypatch.setattr(starless, "find_starless_script", lambda: "/x/Starless.py")
    starless.main(["/nonexistent.fit", "--probe-only"])
    assert "SYQON: OK" in capsys.readouterr().out


def test_not_installed_is_dormant_not_error(monkeypatch, capsys, tmp_path):
    # without --probe-only and without the tools installed: verdict line, exit 0, no files
    monkeypatch.setattr(starless, "find_siril_cli", lambda: None)
    starless.main([str(tmp_path / "m.fit"), "--outdir", str(tmp_path)])
    assert "SYQON: NOT INSTALLED" in capsys.readouterr().out
    assert not list(tmp_path.glob("*_starless.fit"))


def test_ssf_contents(tmp_path):
    ssf = starless.write_ssf(str(tmp_path), "/abs/master.fit")
    text = open(ssf).read()
    assert "requires 1.4.0" in text
    assert "setext fit" in text
    assert 'load "/abs/master.fit"' in text
    assert "pyscript" in text


def test_run_flow_with_stub(monkeypatch, tmp_path):
    master = str(tmp_path / "M_final_spcc.fit")
    star = _rgb_with_star(master)

    def fake_run(siril_cli, outdir, ssf_path):
        data = fits.getdata(master).astype(np.float64)
        fits.writeto(os.path.join(outdir, "starless_M_final_spcc.fit"),
                     (data - star).astype(np.float32),
                     fits.getheader(master), overwrite=True)
        return 0

    monkeypatch.setattr(starless, "find_siril_cli", lambda: "/bin/true")
    monkeypatch.setattr(starless, "find_starless_script", lambda: "/x/Starless.py")
    monkeypatch.setattr(starless, "run_siril", fake_run)
    starless.main([master, "--outdir", str(tmp_path), "--basename", "M_final"])

    sl_path = tmp_path / "M_final_starless.fit"
    st_path = tmp_path / "M_final_stars.fit"
    assert sl_path.exists() and st_path.exists()
    with fits.open(sl_path) as h:
        sl, sl_hdr = h[0].data.astype(np.float64), h[0].header
    with fits.open(st_path) as h:
        st, st_hdr = h[0].data.astype(np.float64), h[0].header
    # stars = master - starless: the star survives in the stars layer, all channels
    assert st[0, 40, 30] > 0.5 and st[1, 40, 30] > 0.5 and st[2, 40, 30] > 0.5
    assert (st >= 0).all()
    # starless is flat at the star position
    assert sl[0, 40, 30] < 0.2
    # headers preserved + our HISTORY
    assert sl_hdr["OBJECT"] == "T" and st_hdr["CRVAL1"] == 10.0
    assert any("starless.py" in str(c) for c in sl_hdr.get("HISTORY", []))


def test_run_failure_exits_nonzero(monkeypatch, tmp_path):
    master = str(tmp_path / "m.fit")
    _rgb_with_star(master)
    monkeypatch.setattr(starless, "find_siril_cli", lambda: "/bin/true")
    monkeypatch.setattr(starless, "find_starless_script", lambda: "/x/Starless.py")
    monkeypatch.setattr(starless, "run_siril", lambda a, b, c: 1)
    with pytest.raises(SystemExit):
        starless.main([master, "--outdir", str(tmp_path)])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tools/test_starless.py`
Expected: collection error `ModuleNotFoundError: No module named 'starless'`.

- [ ] **Step 3: Write the implementation**

Create `tools/starless.py` (set `PYSCRIPT_LINE` to the exact line pinned in Task 1):

```python
#!/usr/bin/env python
"""Optional starless wrapper: run SyQon Starless.py through headless Siril.

Probes for siril-cli and the SyQon Starless script (Siril scripts repo). If
either is missing it prints a one-line verdict and exits 0 — the pipeline step
stays dormant. When available it loads the master in headless Siril, runs the
script's CLI branch (no GUI, zenith model on the GPU), renames the output to
<base>_starless.fit and computes <base>_stars.fit = clip(master - starless, 0),
so palettes can re-add natural-colour stars linearly.

Verdict lines (parseable):
  SYQON: OK (siril-cli=..., script=...)
  SYQON: NOT INSTALLED (missing: ...)

Usage:
  starless.py MASTER.fit [--outdir DIR] [--basename NAME] [--probe-only]
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

import numpy as np
from astropy.io import fits

SIRIL_CLI_FALLBACK = "/Applications/Siril.app/Contents/MacOS/siril-cli"
STARLESS_SCRIPT = os.path.join(
    os.path.expanduser("~"),
    "Library/Application Support/org.siril.Siril/siril-scripts/SyQon/Starless.py",
)
# How the ssf references the SyQon script (pinned by a live headless run).
PYSCRIPT_LINE = "pyscript Starless"
# zenith on MPS takes minutes on a full-res master, not seconds.
SIRIL_TIMEOUT_S = 1800


def find_siril_cli():
    found = shutil.which("siril-cli")
    if found:
        return found
    return SIRIL_CLI_FALLBACK if os.path.exists(SIRIL_CLI_FALLBACK) else None


def find_starless_script():
    return STARLESS_SCRIPT if os.path.exists(STARLESS_SCRIPT) else None


def probe():
    siril = find_siril_cli()
    script = find_starless_script()
    if siril and script:
        return True, f"SYQON: OK (siril-cli={siril}, script={script})"
    missing = []
    if not siril:
        missing.append("siril-cli")
    if not script:
        missing.append("SyQon Starless.py (Siril scripts repo)")
    return False, "SYQON: NOT INSTALLED (missing: " + ", ".join(missing) + ")"


def write_ssf(outdir, master_abs):
    path = os.path.join(outdir, "_syqon_starless.ssf")
    with open(path, "w") as f:
        f.write("requires 1.4.0\n")
        f.write("setext fit\n")
        f.write(f'load "{master_abs}"\n')
        f.write(PYSCRIPT_LINE + "\n")
    return path


def run_siril(siril_cli, outdir, ssf_path):
    """Run headless Siril on the ssf. Isolated so tests can stub it."""
    result = subprocess.run(
        [siril_cli, "-d", outdir, "-s", ssf_path],
        capture_output=True, text=True, timeout=SIRIL_TIMEOUT_S,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout[-2000:] + result.stderr[-2000:])
    return result.returncode


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("master", help="linear RGB FITS master (e.g. the SPCC output)")
    ap.add_argument("--outdir", default=None, help="output dir (default: next to the input)")
    ap.add_argument("--basename", default=None,
                    help="output name stem (default: input filename stem)")
    ap.add_argument("--probe-only", action="store_true",
                    help="print the availability verdict and exit")
    args = ap.parse_args(argv)

    ok, msg = probe()
    print(msg)
    if args.probe_only or not ok:
        return

    master_abs = os.path.abspath(args.master)
    stem = os.path.splitext(os.path.basename(master_abs))[0]
    outdir = os.path.abspath(args.outdir or os.path.dirname(master_abs))
    os.makedirs(outdir, exist_ok=True)
    base = args.basename or stem

    t0 = time.time()
    ssf_path = write_ssf(outdir, master_abs)
    rc = run_siril(find_siril_cli(), outdir, ssf_path)
    try:
        os.remove(ssf_path)
    except OSError:
        pass

    raw = None
    for ext in (".fit", ".fits"):
        cand = os.path.join(outdir, f"starless_{stem}{ext}")
        if os.path.exists(cand):
            raw = cand
            break
    if rc != 0 or raw is None:
        sys.exit(f"SyQon starless failed (siril exit {rc},"
                 f" starless output {'missing' if raw is None else raw})")

    starless_path = os.path.join(outdir, f"{base}_starless.fit")
    if os.path.abspath(raw) != os.path.abspath(starless_path):
        os.replace(raw, starless_path)

    with fits.open(master_abs, memmap=False, ignore_missing_simple=True) as h:
        master = h[0].data.astype(np.float64)
        master_header = h[0].header.copy()
    with fits.open(starless_path, memmap=False, ignore_missing_simple=True) as h:
        starless_img = h[0].data.astype(np.float64)
        starless_header = h[0].header.copy()
    if master.shape != starless_img.shape:
        sys.exit(f"shape mismatch: master {master.shape} vs starless {starless_img.shape}")

    starless_header.add_history("starless.py: SyQon Starless (zenith) via headless Siril")
    fits.writeto(starless_path, starless_img.astype(np.float32), starless_header,
                 overwrite=True)
    print(f"wrote: {starless_path}")

    stars = np.clip(master - starless_img, 0.0, None).astype(np.float32)
    stars_path = os.path.join(outdir, f"{base}_stars.fit")
    stars_header = master_header.copy()
    stars_header.add_history("starless.py: stars = clip(master - starless, 0)")
    fits.writeto(stars_path, stars, stars_header, overwrite=True)
    print(f"wrote: {stars_path}")
    print(f"elapsed: {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tools/test_starless.py`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/starless.py tools/test_starless.py
git commit -m "starless: headless SyQon wrapper (probe, ssf, stars layer)"
```

---

### Task 3: `palette.py --starless`

**Files:**
- Modify: `tools/palette.py` (argparse + main flow)
- Test: `tools/test_palette.py` (append)

**Interfaces:**
- Consumes: existing palette.py functions (`load_rgb_master`, `extract_ha_oiii`,
  `neutralize_background`, `compose_hoo`, `compose_sho`, `write_master`,
  `emission_separation`, `SEPARATION_THRESHOLD`).
- Produces: `main(argv)` accepts `--starless STARLESS.fit`; composition from
  starless channels + per-channel linear re-add of `clip(master − starless, 0)`.

- [ ] **Step 1: Write the failing tests**

Append to `tools/test_palette.py`:

```python
def _emission_with_star(star_y=100, star_x=75):
    """Master = emission cube + one bright star in ALL channels (continuum star)."""
    cube = _emission()
    yy, xx = np.mgrid[0 : cube.shape[1], 0 : cube.shape[2]]
    star = 0.7 * np.exp(-((yy - star_y) ** 2 + (xx - star_x) ** 2) / (2 * 2.0**2))
    return cube + star


def test_cli_starless_natural_stars(capsys):
    with tempfile.TemporaryDirectory() as d:
        master = os.path.join(d, "master.fit")
        sl = os.path.join(d, "sl.fit")
        _write(master, _emission_with_star())
        _write(sl, _emission())
        palette.main([master, "--starless", sl, "--outdir", d, "--basename", "T"])
        assert "PALETTES: EMIT" in capsys.readouterr().out
        with fits.open(os.path.join(d, "T_HOO.fit")) as h:
            hoo = h[0].data.astype(np.float64)
        bgs = [np.median(hoo[c]) for c in range(3)]
        # the star came back with natural (white) colour: bright in R, G AND B
        assert all(hoo[c, 100, 75] > bgs[c] + 0.4 for c in range(3))
        # nebula mapping still palette-coloured: Ha blob in R only
        assert hoo[0, 60, 40] > bgs[0] + 0.4
        assert hoo[2, 60, 40] < bgs[2] + 0.2


def test_cli_starless_shape_mismatch_errors():
    with tempfile.TemporaryDirectory() as d:
        master = os.path.join(d, "master.fit")
        sl = os.path.join(d, "sl.fit")
        _write(master, _emission())
        _write(sl, _emission()[:, :100, :])
        with pytest.raises(SystemExit):
            palette.main([master, "--starless", sl, "--outdir", d])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tools/test_palette.py`
Expected: 2 new FAIL (`unrecognized arguments: --starless`), 13 old pass.

- [ ] **Step 3: Implement**

In `tools/palette.py`, add the option after `--sho-alpha`:

```python
    ap.add_argument("--starless", default=None,
                    help="starless version of the master: compose palettes from it"
                         " and re-add natural stars (master - starless) linearly")
```

Replace the two lines after `args = ap.parse_args(argv)`:

```python
    rgb, header = load_rgb_master(args.master)
    ha, oiii = extract_ha_oiii(rgb)
```

with:

```python
    rgb, header = load_rgb_master(args.master)
    stars = None
    if args.starless:
        starless_rgb, _ = load_rgb_master(args.starless)
        if starless_rgb.shape != rgb.shape:
            sys.exit(f"shape mismatch: master {rgb.shape} vs starless {starless_rgb.shape}")
        stars = np.clip(rgb - starless_rgb, 0.0, None)
        ha, oiii = extract_ha_oiii(starless_rgb)
    else:
        ha, oiii = extract_ha_oiii(rgb)
```

And replace the two `write_master(...)` call blocks:

```python
    star_note = ", starless composition + natural star re-add" if stars is not None else ""

    hoo_path = os.path.join(outdir, f"{base}_HOO.fit")
    hoo_cube = compose_hoo(ha_n, oiii_n)
    if stars is not None:
        hoo_cube = hoo_cube + stars.astype(np.float32)
    write_master(hoo_path, hoo_cube, header,
                 "HOO: R=Ha(R), G=B=OIII((G+B)/2), bg-neutralized, linear" + star_note)
    print(f"wrote: {hoo_path}")

    sho_path = os.path.join(outdir, f"{base}_SHO.fit")
    sho_cube = compose_sho(ha_n, oiii_n, alpha)
    if stars is not None:
        sho_cube = sho_cube + stars.astype(np.float32)
    write_master(sho_path, sho_cube, header,
                 f"SHO: R=Ha, G={alpha:g}*Ha+{1 - alpha:g}*OIII, B=OIII,"
                 " bg-neutralized, linear" + star_note)
    print(f"wrote: {sho_path}")
```

Also add `--starless` to the module docstring Usage line.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tools/test_palette.py tools/test_starless.py`
Expected: 21 passed (15 palette + 6 starless).

- [ ] **Step 5: Commit**

```bash
git add tools/palette.py tools/test_palette.py
git commit -m "palette: --starless — compose from starless, re-add natural stars linearly"
```

---

### Task 4: Live C103 validation + FINDINGS.md

**Files:**
- Modify: `FINDINGS.md` (extend section 5 with a starless note)

Real data: `/Users/ib/prj-other/astro/_stacking-C103-Tarantul-nebula/C103_final_spcc.fit`
(and the user's own `starless_C103_final_spcc.fit` next to it for comparison).
Never commit image outputs.

- [ ] **Step 1: Run the wrapper live on the C103 master**

```bash
SCRATCH=/private/tmp/claude-501/-Users-ib-prj-other-astro-explore-fits/d8ea54be-2590-44c7-b484-0903300006b7/scratchpad
.venv/bin/python tools/starless.py \
  "/Users/ib/prj-other/astro/_stacking-C103-Tarantul-nebula/C103_final_spcc.fit" \
  --outdir "$SCRATCH" --basename C103_live
```

(Bash timeout 600000 ms; if it exceeds that, rerun with `run_in_background`.)
Expected: `SYQON: OK`, then `wrote: .../C103_live_starless.fit`,
`wrote: .../C103_live_stars.fit`, `elapsed: <seconds>`.

- [ ] **Step 2: Sanity-compare with the user's own SyQon output**

```bash
.venv/bin/python - <<'EOF'
import numpy as np
from astropy.io import fits
ours = fits.getdata("/private/tmp/claude-501/-Users-ib-prj-other-astro-explore-fits/d8ea54be-2590-44c7-b484-0903300006b7/scratchpad/C103_live_starless.fit").astype(np.float64)
theirs = fits.getdata("/Users/ib/prj-other/astro/_stacking-C103-Tarantul-nebula/starless_C103_final_spcc.fit").astype(np.float64)
print("shapes:", ours.shape, theirs.shape)
if ours.shape == theirs.shape:
    d = np.abs(ours - theirs)
    print(f"median|diff|={np.median(d):.5f}  p99={np.percentile(d, 99):.5f}")
EOF
```

Expected: same shape, small differences (model/config versions may differ
slightly — large systematic diff means our invocation is wrong; stop and check).

- [ ] **Step 3: Palette from starless + previews, view them**

```bash
SCRATCH=/private/tmp/claude-501/-Users-ib-prj-other-astro-explore-fits/d8ea54be-2590-44c7-b484-0903300006b7/scratchpad
.venv/bin/python tools/palette.py \
  "/Users/ib/prj-other/astro/_stacking-C103-Tarantul-nebula/C103_final_spcc.fit" \
  --starless "$SCRATCH/C103_live_starless.fit" \
  --outdir "$SCRATCH" --basename C103_live
.venv/bin/python tools/preview.py "$SCRATCH/C103_live_starless.fit" \
  --out "$SCRATCH/C103_live_starless.png" --title "SyQon starless (live)"
.venv/bin/python tools/preview.py "$SCRATCH/C103_live_HOO.fit" \
  --out "$SCRATCH/C103_live_HOO.png" --title "HOO from starless + natural stars"
```

View both PNGs (Read tool). Gate: starless preview shows no star residues and
intact nebula; HOO preview shows white/neutral star field over red/teal nebula
(star zoom crops must NOT be pink-white palette stars).

- [ ] **Step 4: Extend FINDINGS.md section 5**

Append to the `## 5. Palette gate (HOO/SHO)` section (real numbers substituted):

```markdown
**Starless palettes (optional):** with the SyQon Starless script installed
(Siril scripts repo), Step 10 composes palettes from a starless master and
re-adds natural-colour stars linearly (`stars = master − starless`). Headless
run via `siril-cli -s` + `pyscript` (the script's CLI branch, no GUI).
C103 full-res master: ~NNN s on MPS; starless output matches the GUI-run
reference (median |diff| ~0.000NN). Palette gate on starless C103: separation
0.NNN → EMIT (threshold unchanged).
```

- [ ] **Step 5: Run the full suite, commit**

```bash
.venv/bin/python -m pytest -q tools/test_palette.py tools/test_starless.py
git add FINDINGS.md tools/starless.py
git commit -m "starless: live C103 validation numbers (FINDINGS)"
```

(`tools/starless.py` included only if the live run required changing
`PYSCRIPT_LINE` or the output lookup.)

---

### Task 5: Pipeline Step 10 sub-step + README

**Files:**
- Modify: `.claude/commands/seestar-pipeline.md` (Step 10 note, Finish item 3, Step 12 keep-list)
- Modify: `README.md` (pipeline paragraph + tools/ bullet)

**Interfaces:**
- Consumes: `tools/starless.py` CLI and `tools/palette.py --starless` exactly as
  built in Tasks 2–3.

- [ ] **Step 1: Rewrite the Step 10 note in `.claude/commands/seestar-pipeline.md`**

Replace the sentence that starts the command block in the Step 10 note — from
"Run on the adopted master — `<OBJECT>_final_spcc.fit`, or
`<OBJECT>_final_solved.fit` if SPCC failed:" through the closing code fence —
with:

````markdown
  First probe the optional SyQon starless engine and log the verdict:
  ```
  .venv/bin/python tools/starless.py 05_stretch/<OBJECT>_final_spcc.fit --probe-only
  ```
  **`SYQON: OK`** → run star removal (slow — minutes on MPS), then palettes from
  the starless master with natural star re-add:
  ```
  .venv/bin/python tools/starless.py 05_stretch/<OBJECT>_final_spcc.fit \
    --outdir 05_stretch --basename <OBJECT>_final
  .venv/bin/python tools/palette.py 05_stretch/<OBJECT>_final_spcc.fit \
    --starless 05_stretch/<OBJECT>_final_starless.fit \
    --outdir 05_stretch --basename <OBJECT>_final
  ```
  `<OBJECT>_final_starless.fit` + `<OBJECT>_final_stars.fit` are deliverables
  too (inputs for a manual VeraLux StarComposer pass); render a starless preview
  PNG into `05_stretch/` alongside the palette previews.
  **`SYQON: NOT INSTALLED`** → log the line to REPORT.md and run palettes
  directly on the adopted master (stars stay in, palette-coloured):
  ```
  .venv/bin/python tools/palette.py 05_stretch/<OBJECT>_final_spcc.fit \
    --outdir 05_stretch --basename <OBJECT>_final
  ```
  (Use `<OBJECT>_final_solved.fit` instead of `_spcc` when SPCC failed.)
````

Keep the rest of the note (EMIT/SKIP explanation, previews into `05_stretch/`,
always AUTO) unchanged.

- [ ] **Step 2: Finish item 3 + Step 12 keep-list**

In the Finish section item 3, extend the palette clause to:
`the palette masters <OBJECT>_final_HOO.fit + <OBJECT>_final_SHO.fit and their
PNGs (if Step 10 emitted), <OBJECT>_final_starless.fit + <OBJECT>_final_stars.fit
(if the SyQon starless sub-step ran),` — rest unchanged.

In Step 12's Keep bullet, extend the parenthetical list with:
`the starless layer pair <OBJECT>_final_starless.fit / <OBJECT>_final_stars.fit
when the SyQon sub-step ran,` after the palette masters clause.

- [ ] **Step 3: README**

In the pipeline paragraph, change "derive HOO/SHO palette masters when the target
shows real Ha/OIII emission separation (measured, auto-skipped for
clusters/galaxies)" to "derive HOO/SHO palette masters when the target shows real
Ha/OIII emission separation (measured, auto-skipped for clusters/galaxies;
composes from a SyQon starless layer with natural star re-add when the SyQon
Siril script is installed)".

After the `tools/palette.py` bullet add:

```markdown
- `tools/starless.py MASTER.fit [--outdir DIR --basename NAME] [--probe-only]` —
  optional star removal via the SyQon Starless Siril script (headless
  `siril-cli` + `pyscript`, zenith model on the GPU): writes `*_starless.fit` +
  `*_stars.fit` (linear star layer for natural-colour re-add / manual VeraLux
  StarComposer). Dormant with a one-line verdict when not installed.
```

- [ ] **Step 4: Consistency check + full suite**

```bash
grep -n "starless" .claude/commands/seestar-pipeline.md README.md | head -20
.venv/bin/python -m pytest -q tools
```

Expected: Step 10 probe/branch text, Finish/keep-list mentions, README bullet;
tests: 21 pass in the two new files (the pre-existing
`test_astrobin_session_csv.py::test_csv_columns_and_values` failure is known and
out of scope).

- [ ] **Step 5: Commit**

```bash
git add .claude/commands/seestar-pipeline.md README.md
git commit -m "Pipeline: optional SyQon starless sub-step in Step 10; README sync"
```
