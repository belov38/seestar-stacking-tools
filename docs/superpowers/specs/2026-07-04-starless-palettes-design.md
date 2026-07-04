# Optional starless palettes (SyQon Starless.py via headless Siril) — design

Date: 2026-07-04 (revised same day: the engine is the SyQon *Python* script, not a
C++ binary — first draft probed the wrong tool)
Status: approved direction (Ilia: "делай опциональный шаг"), pending spec review

## Context

Palette masters (Step 10, `tools/palette.py`) composite HOO/SHO with stars in
place, so stars come out pink-white. The pro technique: build the palette from a
starless image, then re-add the stars with their natural SPCC colours. In linear
space `stars = master − starless` and re-adding is exact addition.

What is actually installed (verified 2026-07-04):

- `…/org.siril.Siril/siril-scripts/SyQon/Starless.py` — the script Ilia runs from
  Siril. Pure Python (torch + sirilpy), zenith.pt model in
  `…/org.siril.Siril/siril/syqon_starless/`, GPU via MPS. **It has a CLI branch:**
  under headless Siril (`siril.is_cli()`) it runs with no GUI — processes the
  loaded image, writes `starless_<base>` and `starmask_<base>` FITS into Siril's
  cwd, header preserved (subtraction starmask per the saved config). Ilia's
  existing `starless_C103_final_spcc.fit` came from exactly this.
- `SyQon_Studio.py` (added by the Jul 4 scripts update) drives a separate C++
  binary that is NOT installed — out of scope, not what the user runs.
- `VeraLux_StarComposer.py` — PyQt6 GUI-only, stretched-domain star
  recomposition. Stays the user's manual finishing tool; our starless/stars
  deliverables are its ideal inputs.

## 1. Tool: `tools/starless.py` (headless Siril wrapper)

```
.venv/bin/python tools/starless.py MASTER.fit --outdir DIR [--basename NAME]
    [--probe-only]
```

- **Probe** (availability = both present):
  1. `siril-cli` on PATH or `/Applications/Siril.app/Contents/MacOS/siril-cli`
     (same resolution as the pipeline already uses);
  2. `Starless.py` in the Siril scripts repo:
     `~/Library/Application Support/org.siril.Siril/siril-scripts/SyQon/Starless.py`.
  Missing either → print `SYQON: NOT INSTALLED (Siril scripts repo → SyQon → Starless)`,
  exit 0 (dormant, not an error). `--probe-only` prints `SYQON: OK` / NOT
  INSTALLED and exits.
- **Run**: generate a temp `.ssf` in the output dir:
  ```
  requires 1.4.0
  load <abs MASTER.fit>
  pyscript <Starless script reference>
  ```
  and execute `siril-cli -d <outdir> -s <temp.ssf>`. The exact `pyscript`
  reference syntax (menu name vs path) is pinned during implementation by a live
  probe on this machine — both the script and the model are installed, so this is
  testable now.
- **Outputs** (rename SyQon's products to our deliverable convention):
  - `<base>_starless.fit` ← SyQon's `starless_*` output (header already
    preserved by the script; add our HISTORY line);
  - `<base>_stars.fit` = `clip(master − starless, 0, None)` computed by us
    (deterministic; SyQon's subtraction starmask file, if written, is left in
    place untouched).
  - float32, linear, header + WCS intact — same conventions as palette.py.
- Inference failure (non-zero siril-cli exit, missing starless output) → clear
  error, non-zero exit. Log runtime (zenith on MPS takes minutes, not seconds).

## 2. `tools/palette.py` extension: `--starless FILE`

- New option `--starless STARLESS.fit`. When given:
  - Ha/OIII for **composition** come from the starless image;
  - the gate metric runs on the starless channels (star suppression stays on —
    harmless; threshold 0.23 unchanged; log the measured value as usual);
  - after composing HOO/SHO from starless channels, re-add the natural-colour
    star layer per channel: `out += clip(master − starless, 0, None)`
    (master = the positional input; all linear, exact);
  - HISTORY line notes `starless composition + natural star re-add`.
- Shape mismatch between master and starless → clear error.
- Without `--starless`, behaviour is exactly as today (regression-guarded by the
  existing 13 tests).

## 3. Pipeline integration (Step 10, `.claude/commands/seestar-pipeline.md`)

Step 10 gains an optional starless sub-step, still always AUTO:

1. `tools/starless.py <master> --probe-only` → verdict logged to REPORT.md.
2. **Available:** `tools/starless.py <master> --outdir 05_stretch --basename
   <OBJECT>_final` (this is the slow part — minutes on MPS), then
   `tools/palette.py <master> --starless 05_stretch/<OBJECT>_final_starless.fit
   --outdir 05_stretch --basename <OBJECT>_final`. On EMIT the HOO/SHO masters
   carry natural stars. Render previews for starless + both palettes; view them.
3. **Not available:** exactly today's behaviour, plus the one-line
   `SYQON: NOT INSTALLED` note in REPORT.md.
4. Deliverables/cleanup: `<OBJECT>_final_starless.fit`, `<OBJECT>_final_stars.fit`
   (+ starless preview PNG) join the `05_stretch/` keep-list and the `DATADIR`
   copies — they are the inputs for the user's manual VeraLux StarComposer pass.
   README tools/ section gets a `tools/starless.py` bullet; the pipeline
   description mentions "optional SyQon starless".

## 4. Tests (`tools/test_starless.py` + additions to `tools/test_palette.py`)

Unit tests never run real inference (minutes) — the siril-cli call is isolated in
one function and stubbed:

- probe: both present → OK; either missing → NOT INSTALLED, exit 0.
- run flow with a **stub runner** (monkeypatched: writes a fake `starless_*` FITS
  = input minus a synthetic star) → produces `<base>_starless.fit` +
  `<base>_stars.fit`, header preserved, stars ≥ 0, stars ≈ the synthetic star.
- `.ssf` generation: absolute paths, correct pyscript line.
- palette `--starless`: synthetic master = emission cube + one star in all
  channels; starless = same cube without the star. Output HOO: star pixel present
  in R, G and B (natural, not palette-coloured); nebula channels from starless.
  Shape-mismatch error case.

One **live integration check** during implementation (not in pytest): run
`tools/starless.py` on the real C103 SPCC master, compare against Ilia's existing
`starless_C103_final_spcc.fit`, view previews, record runtime + verdict in
FINDINGS.md.

## Out of scope

- SyQon_Studio.py / C++ binary, VeraLux automation, StarNet fallback,
  stretched-domain recomposition (VeraLux's job, manual).
- Running zenith.pt directly with our own torch (reimplementing SyQon's tiling).

## First validation

Part of implementation (see Tests): live C103 run + preview review + FINDINGS.md
entry with runtime.
