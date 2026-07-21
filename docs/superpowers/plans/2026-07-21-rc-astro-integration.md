# RC Astro CLI Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the licensed `rc-astro` CLI (bxt/sxt/nxt) into the Seestar pipeline as optional, license-probed steps with quiet fallbacks to the existing built-in tools.

**Architecture:** One new adapter `tools/rcastro.py` (license probe + thin passthrough wrappers) with unit tests; the orchestrator prompt `.claude/commands/seestar-pipeline.md` is rewritten (renumbered steps, bxt/nxt preferred paths, new starless-decomposition and two-filter-layers steps, Ha/OIII split and teal-HOO removed); two SKILL.md files gain an "RC Astro path" section; the root README gains an optional install step and updated descriptions.

**Tech Stack:** Python 3.13 (`.venv/bin/python`), pytest, `rc-astro` CLI 1.1.0 (external, optional), Siril CLI.

**Spec:** `docs/superpowers/specs/2026-07-21-rc-astro-integration-design.md` — read it before starting any task.

## Global Constraints

- rc-astro may be absent or partially licensed; absence is NEVER an error — probe prints `RCASTRO: absent` and exits 0.
- License keys never pass through the agent; no activation logic anywhere in this repo.
- The probe line format is exact: `RCASTRO: cli=<ver> bxt=ok|no sxt=ok|no nxt=ok|no` or `RCASTRO: absent`.
- Existing `measure_deconv.py` / `measure_denoise.py` adopt rules are unchanged (deconv: FWHM gain ≥3% AND ring_worst ≥ −1×RMS; denoise: FWHM Δ < 3% AND faint_keep > 0.85).
- `tools/palette.py` and `tools/hoo_recombine.py` stay in the repo untouched (manual tools) — only their pipeline/README references change.
- English only in code comments and docs.
- Tests run as: `cd tools && ../.venv/bin/python -m pytest -q test_rcastro.py`.

---

### Task 1: `tools/rcastro.py` — license probe

**Files:**
- Create: `tools/rcastro.py`
- Test: `tools/test_rcastro.py`

**Interfaces:**
- Produces: `find_cli() -> str | None`; `probe_line() -> str` (the exact `RCASTRO: …` line); CLI `rcastro.py probe` printing that line, always exit 0. Task 2 adds run wrappers to the same file; Tasks 3/6 reference the CLI form `.venv/bin/python tools/rcastro.py probe`.

- [ ] **Step 1: Write the failing tests**

```python
# tools/test_rcastro.py
"""Tests for rcastro.py: license probe parsing and run wrappers (CLI fully mocked)."""
import json
import subprocess

import pytest

import rcastro

LICENSED = json.dumps({
    "schemaVersion": 4, "cliVersion": "1.1.0",
    "licenseStatus": {"valid": True, "message": "Permanently licensed through ML4.",
                      "email": "user@example.com"},
})
UNLICENSED = json.dumps({
    "schemaVersion": 4, "cliVersion": "1.1.0",
    "licenseStatus": {"valid": False, "message": "Not activated."},
})


def _mock_run(responses):
    """responses: product -> (returncode, stdout). Returns a subprocess.run stand-in."""
    def run(cmd, **kwargs):
        product = next(p for p in rcastro.PRODUCTS if p in cmd)
        rc, out = responses[product]
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
    return run


def test_probe_all_licensed(monkeypatch):
    monkeypatch.setattr(rcastro, "find_cli", lambda: "/usr/local/bin/rc-astro")
    monkeypatch.setattr(rcastro.subprocess, "run",
                        _mock_run({p: (0, LICENSED) for p in rcastro.PRODUCTS}))
    assert rcastro.probe_line() == "RCASTRO: cli=1.1.0 bxt=ok sxt=ok nxt=ok"


def test_probe_one_unlicensed(monkeypatch):
    monkeypatch.setattr(rcastro, "find_cli", lambda: "/usr/local/bin/rc-astro")
    responses = {p: (0, LICENSED) for p in rcastro.PRODUCTS}
    responses["nxt"] = (0, UNLICENSED)
    monkeypatch.setattr(rcastro.subprocess, "run", _mock_run(responses))
    assert rcastro.probe_line() == "RCASTRO: cli=1.1.0 bxt=ok sxt=ok nxt=no"


def test_probe_cli_absent(monkeypatch):
    monkeypatch.setattr(rcastro, "find_cli", lambda: None)
    assert rcastro.probe_line() == "RCASTRO: absent"


def test_probe_malformed_json_is_no(monkeypatch):
    monkeypatch.setattr(rcastro, "find_cli", lambda: "/usr/local/bin/rc-astro")
    responses = {p: (0, LICENSED) for p in rcastro.PRODUCTS}
    responses["sxt"] = (0, "not json at all")
    monkeypatch.setattr(rcastro.subprocess, "run", _mock_run(responses))
    assert rcastro.probe_line() == "RCASTRO: cli=1.1.0 bxt=ok sxt=no nxt=ok"


def test_probe_product_error_is_no(monkeypatch):
    monkeypatch.setattr(rcastro, "find_cli", lambda: "/usr/local/bin/rc-astro")
    responses = {p: (0, LICENSED) for p in rcastro.PRODUCTS}
    responses["bxt"] = (1, "")
    monkeypatch.setattr(rcastro.subprocess, "run", _mock_run(responses))
    assert rcastro.probe_line() == "RCASTRO: cli=1.1.0 bxt=no sxt=ok nxt=ok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd tools && ../.venv/bin/python -m pytest -q test_rcastro.py`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'rcastro'`

- [ ] **Step 3: Write the probe implementation**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd tools && ../.venv/bin/python -m pytest -q test_rcastro.py`
Expected: `5 passed`

- [ ] **Step 5: Live smoke (this machine has all three licensed)**

Run: `.venv/bin/python tools/rcastro.py probe`
Expected: `RCASTRO: cli=1.1.0 bxt=ok sxt=ok nxt=ok`

- [ ] **Step 6: Commit**

```bash
git add tools/rcastro.py tools/test_rcastro.py
git commit -m "rcastro.py: license probe for the rc-astro CLI"
```

---

### Task 2: `tools/rcastro.py` — run wrappers

**Files:**
- Modify: `tools/rcastro.py` (extend `main`, add `run_product`)
- Test: `tools/test_rcastro.py` (append)

**Interfaces:**
- Consumes: `find_cli()`, `PRODUCTS` from Task 1.
- Produces: `run_product(product, inp, out, extra) -> int` and CLI form
  `rcastro.py bxt|sxt|nxt IN OUT [extra args passed through verbatim]` — exit 0 on success
  (OUT exists), non-zero with a stderr message on failure. Tasks 3–5 invoke it as e.g.
  `.venv/bin/python tools/rcastro.py sxt IN.fit OUT.fit --stars --unscreen`.

- [ ] **Step 1: Append failing tests**

```python
# append to tools/test_rcastro.py

def test_run_product_success(monkeypatch, tmp_path):
    out = tmp_path / "out.fit"

    def fake_run(cmd, **kwargs):
        assert cmd[:2] == ["/usr/local/bin/rc-astro", "--no-banner"]
        assert "--json" in cmd and "sxt" in cmd
        assert ["-o", str(out)] == cmd[cmd.index("-o"):cmd.index("-o") + 2]
        assert "--overwrite" in cmd and "--stars" in cmd
        out.write_bytes(b"fits")
        events = '{"event":"status","phase":"complete","message":"Done"}\n'
        return subprocess.CompletedProcess(cmd, 0, stdout=events, stderr="")

    monkeypatch.setattr(rcastro, "find_cli", lambda: "/usr/local/bin/rc-astro")
    monkeypatch.setattr(rcastro.subprocess, "run", fake_run)
    rc = rcastro.run_product("sxt", "in.fit", str(out), ["--stars"])
    assert rc == 0


def test_run_product_missing_output_fails(monkeypatch, tmp_path):
    out = tmp_path / "never_written.fit"

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(rcastro, "find_cli", lambda: "/usr/local/bin/rc-astro")
    monkeypatch.setattr(rcastro.subprocess, "run", fake_run)
    assert rcastro.run_product("bxt", "in.fit", str(out), []) != 0


def test_run_product_cli_absent_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(rcastro, "find_cli", lambda: None)
    assert rcastro.run_product("nxt", "in.fit", str(tmp_path / "o.fit"), []) != 0


def test_run_product_nonzero_exit_fails(monkeypatch, tmp_path):
    out = tmp_path / "out.fit"

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 1, stdout='{"event":"error","message":"boom"}\n', stderr="")

    monkeypatch.setattr(rcastro, "find_cli", lambda: "/usr/local/bin/rc-astro")
    monkeypatch.setattr(rcastro.subprocess, "run", fake_run)
    assert rcastro.run_product("bxt", "in.fit", str(out), []) != 0
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `cd tools && ../.venv/bin/python -m pytest -q test_rcastro.py`
Expected: 5 pass (Task 1), 4 FAIL with `AttributeError: ... no attribute 'run_product'`

- [ ] **Step 3: Implement `run_product` and extend `main`**

```python
# add to tools/rcastro.py (below probe_line)

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
    return 0
```

Replace `main` with:

```python
def main(argv):
    if len(argv) >= 1 and argv[0] == "probe":
        print(probe_line())
        return 0
    if len(argv) >= 3 and argv[0] in PRODUCTS:
        return run_product(argv[0], argv[1], argv[2], argv[3:])
    print(f"usage: rcastro.py probe | {{{'|'.join(PRODUCTS)}}} IN OUT [args...]",
          file=sys.stderr)
    return 2
```

- [ ] **Step 4: Run all tests**

Run: `cd tools && ../.venv/bin/python -m pytest -q test_rcastro.py`
Expected: `9 passed`

- [ ] **Step 5: Live smoke on a small crop (licensed machine only; skip gracefully if `probe` says absent)**

```bash
S=$(mktemp -d)
.venv/bin/python - "$S" << 'EOF'
import sys
import numpy as np
from astropy.io import fits
cube = np.random.default_rng(0).normal(0.1, 0.01, (3, 256, 256)).astype("float32")
fits.PrimaryHDU(cube).writeto(f"{sys.argv[1]}/crop.fit", overwrite=True)
EOF
.venv/bin/python tools/rcastro.py sxt "$S/crop.fit" "$S/starless.fit" --stars --unscreen
ls "$S"/starless.fit "$S"/*sxt*stars* 2>/dev/null || ls "$S"
```

Expected: exit 0, `starless.fit` exists (a stars sidecar file appears with rc-astro's own naming — note its actual name for Task 3's Step 11 text if it differs from `<out>` + `-stars`).

- [ ] **Step 6: Commit**

```bash
git add tools/rcastro.py tools/test_rcastro.py
git commit -m "rcastro.py: bxt/sxt/nxt passthrough run wrappers"
```

---

### Task 3: Rewrite `.claude/commands/seestar-pipeline.md`

**Files:**
- Modify: `.claude/commands/seestar-pipeline.md`

**Interfaces:**
- Consumes: `tools/rcastro.py probe` line format and `rcastro.py <product> IN OUT [args]` CLI (Tasks 1–2).
- Produces: the renumbered step structure (1–9 unchanged names, 10 Stretch, Finish, 11 Starless decomposition, 12 Two-filter star layers, 13 Cleanup) that Tasks 4–6 reference.

No tests (prompt document). Apply these edits top-to-bottom; the spec's "Renumbered pipeline" table is the authority.

- [ ] **Step 1: Fixed facts — add rc-astro**

Add to the "Fixed facts" list:

```markdown
- RC Astro CLI (optional, licensed per product): probe once with
  `.venv/bin/python tools/rcastro.py probe` → one line, `RCASTRO: cli=1.1.0 bxt=ok sxt=ok
  nxt=no` or `RCASTRO: absent`. `bxt` replaces Siril RL at Step 6, `nxt` replaces the
  GraXpert sweep at Step 7, `sxt` powers Steps 11–12 — each only when its flag is `ok`;
  otherwise the built-in path runs and nothing is said beyond the Step-1 summary line.
  Run products via `.venv/bin/python tools/rcastro.py bxt|nxt|sxt IN.fit OUT.fit [args]`
  (passthrough; exits non-zero on failure → warn, fall back, log to REPORT.md).
```

- [ ] **Step 2: Step 1 — probe + third mixed-filter option**

In "Step 1 — Explore & quarantine" add a numbered item after the deps check:

```markdown
2. **RC Astro probe:** run `.venv/bin/python tools/rcastro.py probe`, include the `RCASTRO:`
   line in the "here's how your data is organized" summary and later in the REPORT.md header.
   This is the only time tool availability is mentioned — downstream steps fall back quietly.
```

(renumber the following items). In the **mixed** filter-census branch, extend the STOP options:

```markdown
   - **mixed** → never stack the two together. **STOP and ask**, reporting per-filter sub
     counts and integration minutes. Options: quarantine the minority filter to
     `<DATADIR>/_<filter>_aside/` and process the majority now; run the pipeline twice; or —
     **only when sxt=ok** — **"LP → nebula, IRCUT → stars"**: the LP set runs the full
     pipeline and the IRCUT subs move to `<DATADIR>/_ircut_stars/` for the Step-12 stars
     mini-run (Path B). Recommend this third option when the IRCUT share is too small for a
     standalone broadband run (roughly < 30 min).
```

- [ ] **Step 3: Step table — replace rows 6, 7, drop old 10, renumber**

The table header becomes "The processing steps (Steps 4–10)". Rows:

```markdown
| 6 | `seestar-deconvolution-compare` | `03_deconv/` | bxt path (licensed): clean verdict from `measure_deconv.py` on a bxt variant | **default to STOP** on any doubt — borderline ring depth, marginal FWHM, or visible rings; bxt REJECT → keep baseline (no RL fallback sweep) |
| 7 | `seestar-denoise-compare` | `04_denoise/` | strongest setting with FWHM Δ < ~3% **and** faint_keep > ~0.85 (nxt sweep when licensed, else GraXpert) | even the lowest strength over-blurs → propose **skip denoise** |
| 10 | *(stretch — manual, no skill)* | `05_stretch/` | — | **always present** the final result (stretch is the user's call) |
```

Delete the old Step 10 (Ha/OIII split) row entirely; old Step 11 (stretch) becomes 10. Update the post-steps sentence:

```markdown
After Step 10 + Finish there are three **optional** post-steps: **Step 11** (starless
decomposition, sxt), **Step 12** (two-filter star layers, sxt), **Step 13** (cleanup).
All three only run on the user's say-so.
```

- [ ] **Step 4: Step 6 notes — bxt preferred path**

Replace the Step 6 note with:

```markdown
- **Step 6 (deconv):** if `bxt=ok`, the Siril RL sweep is **not run**. Run two bxt variants
  on the linear master:
  ```
  .venv/bin/python tools/rcastro.py bxt IN.fit 03_deconv/bxt_default.fit --ss 0.5 --sn 1.0
  .venv/bin/python tools/rcastro.py bxt IN.fit 03_deconv/bxt_correct.fit --correct-only
  ```
  then validate with the existing skill measurer:
  `measure_deconv.py IN.fit 03_deconv/bxt_*.fit` — same adopt rule (FWHM gain ≥3% AND
  ring_worst ≥ −1×RMS), same trap (never adopt on FWHM alone), same stop rule (doubt →
  STOP with preview). REJECT → keep baseline; do **not** fall back to the RL sweep.
  A failed bxt run (non-zero exit) → warn, run the normal Siril RL path instead.
  If `bxt=no`: the skill's Siril RL path exactly as before (makepsf stars, ~10 it, -tv;
  reject mfdeconv / Cosmic Clarity).
```

- [ ] **Step 5: Step 7 notes — nxt preferred path**

Replace the Step 7 note with:

```markdown
- **Step 7 (denoise):** if `nxt=ok`, sweep NoiseXTerminator instead of GraXpert:
  ```
  for dn in 0.1 0.25 0.5 0.75 0.9:
    .venv/bin/python tools/rcastro.py nxt IN.fit 04_denoise/nxt$dn.fit --dn $dn
  ```
  measure with the skill's `measure_denoise.py` (same adopt rule: strongest with
  FWHM Δ < ~3% and faint_keep > ~0.85; all over-blur → propose skip). A failed nxt run →
  warn, fall back to the GraXpert sweep. If `nxt=no`: the skill's GraXpert GPU sweep
  (~0.1–0.9, absolute output paths) exactly as before.
```

- [ ] **Step 6: Delete old Step 10 (Ha/OIII split) note entirely**

Remove the whole "Step 10 (Ha/OIII channel split …)" block. `palette.py` is no longer part
of the pipeline (manual tool; the user splits channels in Alchemy).

- [ ] **Step 7: Renumber Step 11 → Step 10 (stretch)**

Same content, new number; it still writes `05_stretch/<OBJECT>_final_stretch.png`.

- [ ] **Step 8: Finish — drop Ha/OIII, reflect adopted tools**

In the Finish section: remove the Ha/OIII master mentions from `astrobin.txt` guidance and
from the DATADIR copy list. Add to the `astrobin.txt` chain description:

```markdown
   the **actual processing chain you logged** (stack params → GraXpert AI bg → BXT variant
   *or* Siril RL params → NXT strength *or* GraXpert strength → plate-solved → SPCC:
   `Sony IMX585` + the Step-9 filter profile) — name the tools that were actually adopted.
```

- [ ] **Step 9: Replace old Step 12 (teal recombine) with new Step 11 (starless decomposition)**

Delete the old Step 12 section (auto path AND manual handoff). New section:

```markdown
## Step 11 — Offer starless decomposition (optional; sxt=ok; any run)

*Offer* after Finish; never run unasked; skip the offer silently when `sxt` is not `ok`.
The deliverable is composition-ready layers — the user composes in their own tool
(e.g. Alchemy); the pipeline does **no** blending, no HOO, no palettes.

1. **Stretch the adopted master** — if the user has placed their own
   `<OBJECT>_final_stretched.fit` in `05_stretch/`, use it (say so). Otherwise:
   ```
   load <OBJECT>_final_spcc        # or _final_solved if SPCC failed
   autostretch
   save <OBJECT>_final_stretched
   ```
2. **Decompose:**
   ```
   .venv/bin/python tools/rcastro.py sxt 05_stretch/<OBJECT>_final_stretched.fit \
     05_stretch/<OBJECT>_final_starless.fit --stars --unscreen
   ```
   → `<OBJECT>_final_starless.fit` (nebula/galaxy layer) + the stars sidecar; rename the
   sidecar to `<OBJECT>_final_stars.fit`. Both share the input's pixel grid — sxt never
   moves pixels, so the layers are orientation-matched by construction.
3. **Deliver:** previews of both layers into `05_stretch/` (they must survive cleanup),
   log to REPORT.md, copy both layers to DATADIR.
A failed sxt run → warn, keep whatever exists (at minimum the stretched master), never
fail the pipeline.
```

- [ ] **Step 10: Replace old Step 13 (composite) with new Step 12 (two-filter star layers)**

Rewrite the section keeping the existing `composite.py` mechanics and combined-CSV part:

```markdown
## Step 12 — Offer two-filter star layers (optional; sxt=ok; LP + IRCUT data)

The LP master carries the emission/nebula signal; IRCUT subs carry honest broadband star
colour (stars need little SNR). *Offer* when sxt=ok and either applies; never run unasked.

**Path A — two plate-solved masters exist** (this run + an earlier run in DATADIR):
1. `tools/composite.py <LP> <IRCUT> --mode align` → `<OBJECT>_final_IRCUT_aligned.fit`
   (WCS reprojection onto the LP grid — from here every file shares one orientation).
   Log the `COMPOSITE: ALIGN (…, coverage=…)` line; low coverage → say the masters barely
   overlap.
2. Stretch both (same rule as Step 11: user-provided `*_stretched.fit` wins, else Siril
   `autostretch`): LP master → `<OBJECT>_final_stretched.fit`; aligned IRCUT →
   `<OBJECT>_final_IRCUT_stretched.fit`.
3. Two sxt calls:
   - LP: `rcastro.py sxt <OBJECT>_final_stretched.fit <OBJECT>_final_starless.fit`
     (starless nebula base; LP star colour is discarded — the LP filter guts continuum);
   - IRCUT: `rcastro.py sxt <OBJECT>_final_IRCUT_stretched.fit
     <OBJECT>_final_IRCUT_starless_tmp.fit --stars --unscreen` — keep only the stars
     sidecar, renamed `<OBJECT>_final_IRCUT_stars.fit`; delete the tmp starless.
4. Deliver layers as-is: `starless` + `IRCUT_stars` (+ linear `IRCUT_aligned` for users who
   want their own stretch). No blending.
5. Previews into `05_stretch/`, REPORT lines, DATADIR copies, and the combined acquisition
   CSV over both `lights/` dirs (existing mechanics).

**Path B — "LP → nebula, IRCUT → stars" was chosen at Step 1** (`_ircut_stars/` exists):
run a **stars mini-run** first, all intermediates under `<RUN>/stars_run/`:
1. Step-3 quality gate on `_ircut_stars/` (cloudy IRCUT subs are as useless as cloudy LP);
2. stack: baseline winsor 3/3 only, no variant sweep (stars don't need the contest);
3. background extraction (GraXpert AI, default smoothing);
4. plate-solve + SPCC with `"-oscfilter=UV/IR Block"`;
5. deconv/denoise: **skipped** (stars don't need them);
then continue exactly as Path A from item 1, using the mini-run master as the IRCUT master.

Without sxt this step is not offered; the old `--mode align` / `--mode hargb` composite
remains available to the user manually via `tools/composite.py`.
```

- [ ] **Step 11: Renumber Step 14 → Step 13 (cleanup) and extend the prunable set**

Same content, plus `stars_run/` in the Remove list:

```markdown
   - **Remove:** `01_stack/`, `02_background/`, `03_deconv/`, `04_denoise/`, `previews/`,
     and `stars_run/` when a Path-B mini-run created it (its final layers already live in
     `05_stretch/`).
```

Also update every cross-reference in the file: the universal rule ("Steps 4–11" → "Steps
4–10"), the run-dir comment, "When Step 11 is done" → "When Step 10 is done" in Finish, and
the skill frontmatter description (it mentions the Ha/OIII split and teal recombine — reword
to starless decomposition / two-filter layers).

- [ ] **Step 12: Read the whole edited file once; check numbering is contiguous and no stale references to palette/HOO/old numbers remain**

Run: `grep -nE 'palette|HOO|hoo_|Step 1[0-4]|teal' .claude/commands/seestar-pipeline.md`
Expected: no palette/hoo/teal hits; step-number hits only for the new 10/11/12/13 meanings.

- [ ] **Step 13: Commit**

```bash
git add .claude/commands/seestar-pipeline.md
git commit -m "pipeline: rc-astro integration, renumbered steps, layer deliverables"
```

---

### Task 4: `seestar-deconvolution-compare` — RC Astro path section

**Files:**
- Modify: `.claude/skills/seestar-deconvolution-compare/SKILL.md`

**Interfaces:**
- Consumes: `rcastro.py bxt IN OUT [args]` (Task 2); `measure_deconv.py` (existing).

- [ ] **Step 1: Add the section after "Use Siril RL — not mfdeconv / Seti tools"**

```markdown
## RC Astro path (BlurXTerminator) — preferred when licensed

If `tools/rcastro.py probe` reports `bxt=ok`, skip the Siril RL sweep entirely and run two
bxt variants on the **linear** stack:

```
../../../.venv/bin/python ../../../tools/rcastro.py bxt stack.fit bxt_default.fit --ss 0.5 --sn 1.0
../../../.venv/bin/python ../../../tools/rcastro.py bxt stack.fit bxt_correct.fit --correct-only
```

Measure them with the SAME measurer and the SAME adopt rule as the RL variants:

```
python measure_deconv.py stack.fit bxt_default.fit bxt_correct.fit
```

- Adopt only on FWHM gain ≥3% AND ring_worst ≥ −1×RMS. bxt is trained not to ring, but the
  measurement — not the reputation — decides; a REJECT keeps the un-deconvolved baseline
  (do not fall back to the RL sweep: on S30 data RL was never cleaner than bxt's rejects).
- `--correct-only` fixes PSF aberrations without sharpening — often the honest winner on
  undersampled S30 stacks where sharpening has no room.
- A failed bxt run (non-zero exit) → fall back to the Siril RL workflow below.
```

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/seestar-deconvolution-compare/SKILL.md
git commit -m "deconv skill: BlurXTerminator preferred path"
```

---

### Task 5: `seestar-denoise-compare` — RC Astro path section

**Files:**
- Modify: `.claude/skills/seestar-denoise-compare/SKILL.md`

**Interfaces:**
- Consumes: `rcastro.py nxt IN OUT --dn <v>` (Task 2); `measure_denoise.py` (existing).

- [ ] **Step 1: Add the section after "Tools (in this skill dir)"**

```markdown
## RC Astro path (NoiseXTerminator) — preferred when licensed

If `tools/rcastro.py probe` reports `nxt=ok`, sweep NoiseXTerminator instead of GraXpert
(same linear input, same measurement):

```
for dn in 0.1 0.25 0.5 0.75 0.9; do
  ../../../.venv/bin/python ../../../tools/rcastro.py nxt stack.fit nxt$dn.fit --dn $dn
done
python measure_denoise.py stack.fit nxt0.1.fit nxt0.25.fit nxt0.5.fit nxt0.75.fit nxt0.9.fit
```

The adopt rule is unchanged: strongest noise drop with FWHM Δ < ~3% AND faint_keep > ~0.85;
if even 0.1 over-blurs, skip denoise. A failed nxt run → fall back to the GraXpert sweep
below. `--dn` scales overall strength; leave the frequency/channel splits (`--dihf` etc.)
at their defaults unless chasing a measured artifact.
```

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/seestar-denoise-compare/SKILL.md
git commit -m "denoise skill: NoiseXTerminator preferred path"
```

---

### Task 6: Root README updates + final verification

**Files:**
- Modify: `README.md` (five places: runbook, skills table, pipeline description, tools list, manual setup)

**Interfaces:**
- Consumes: probe line format (Task 1), renumbered steps (Task 3).

- [ ] **Step 1: Automated install runbook — new optional step after the GPU-models step**

```markdown
**Step 6 — RC Astro tools (optional, licensed).** Ask the user: "Do you own RC Astro
licenses (BlurXTerminator / StarXTerminator / NoiseXTerminator)?"
- **No** → skip; the pipeline runs fully on the built-in tools (Siril RL, GraXpert GPU).
- **Yes** → the user downloads and installs the CLI themselves from
  https://www.rc-astro.com (requires their RC Astro account; no Homebrew formula). Then:
  1. verify: `rc-astro` on PATH (or `/usr/local/bin/rc-astro`);
  2. the user activates each owned product **themselves** — license keys must not pass
     through the agent — by typing `! rc-astro bxt --activate` (and `sxt` / `nxt`) in the
     session, or running it in their own terminal;
  3. `rc-astro download-models`;
  4. verify: `.venv/bin/python tools/rcastro.py probe` → expect `bxt=ok` etc. for the
     owned products.
```

(renumber the runbook's subsequent steps if any follow the inserted one).

- [ ] **Step 2: Skills table — reflect the preferred paths**

```markdown
| 3. Deconvolution | `seestar-deconvolution-compare` | BXT (licensed) / Siril RL ~10it | mfdeconv/Seti ring; measure ring-vs-background, not just FWHM |
| 4. Denoise | `seestar-denoise-compare` | NXT (licensed) / GraXpert ~0.3 | monotonic noise↔blur tradeoff; deep stacks need little |
```

- [ ] **Step 3: `/seestar-pipeline` description — rewrite the deliverables sentences**

Replace the sentences about the Ha/OIII split and teal recombine offer (lines ~183–188)
with:

```markdown
master (Siril, Seestar S30 sensor + the run's filter profile), and finish with an
autostretch preview. With the optional RC Astro CLI (licensed, probed once per run):
BlurXTerminator replaces Siril RL at the deconvolution step and NoiseXTerminator replaces
the GraXpert sweep — same measured adopt rules — and StarXTerminator unlocks two optional
post-steps that deliver **composition-ready layers** (the user composes in their own tool):
a starless decomposition of the final master (`*_final_starless.fit` + `*_final_stars.fit`,
one pixel grid), and — when IRCUT data exists (a second master, or the minority of a mixed
session routed "LP → nebula, IRCUT → stars") — a two-filter layer set
(`*_final_starless.fit` + `*_final_IRCUT_stars.fit`, WCS-aligned via `tools/composite.py`).
```

- [ ] **Step 4: tools/ list — add rcastro.py, reword palette/hoo entries**

Add:

```markdown
- `tools/rcastro.py probe | bxt|sxt|nxt IN OUT [args]` — adapter for the optional RC Astro
  CLI: `probe` prints one per-product license line (`RCASTRO: cli=… bxt=ok sxt=ok nxt=no`,
  or `RCASTRO: absent`; never an error), the product forms are thin passthrough wrappers
  (`--overwrite`, JSON events parsed, non-zero exit on failure). Backs the pipeline's
  Steps 6/7/11/12.
```

In the `palette.py` entry: replace "Backs the pipeline's Step 10." with "Manual tool — not
part of the pipeline (split channels in your compositing tool instead)." In the
`hoo_recombine.py` entry: replace "Backs the pipeline's optional Step 12." with "Manual
tool — not part of the pipeline." In the `composite.py` entry: replace "Backs the
pipeline's optional Step 13." with "Backs the pipeline's optional Step 12."

- [ ] **Step 5: Setup (manual) — one pointer line**

Append after the venv block:

```markdown
Optional: RC Astro CLI (licensed) — see the runbook's RC Astro step; verify with
`.venv/bin/python tools/rcastro.py probe`.
```

- [ ] **Step 6: Verify the README is fully updated (explicit user requirement)**

Run: `grep -nE 'rc-astro|rcastro|RCASTRO' README.md`
Expected: hits in all five places (runbook step, skills table has BXT/NXT, pipeline
description, tools list, manual setup).
Run: `grep -nE "Backs the pipeline's Step 1[03]|teal-OIII recombine" README.md`
Expected: no hits.

- [ ] **Step 7: Full test suite + commit**

```bash
cd tools && ../.venv/bin/python -m pytest -q && cd ..
git add README.md
git commit -m "README: rc-astro optional install step, updated pipeline description"
```

Expected: all `tools/` tests pass (existing + 9 new).
