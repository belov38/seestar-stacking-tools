# Optional starless palettes (SyQon) — design

Date: 2026-07-04
Status: approved direction (Ilia: "делай опциональный шаг"), pending spec review

## Context

Palette masters (Step 10, `tools/palette.py`) currently composite HOO/SHO with the
stars in place, so stars come out pink-white. The pro technique separates the star
field first: build the palette from a starless image, then re-add the stars with
their natural SPCC colours. In linear space the star layer is exactly
`stars = master − starless`, and re-adding it is exact addition — no VeraLux
needed for the linear deliverables.

What exists on this machine (verified 2026-07-04):

- `~/Library/Application Support/org.siril.Siril/siril-scripts/SyQon/SyQon_Studio.py`
  — Siril wrapper script (v3). It only drives an external C++ binary; without the
  binary it exits with an error. Internally it calls:
  `SyQonStarless -i in.tif -o out.tif -d Auto -t 512 -v 256 -c seti --gui`
  → dropping `--gui` gives a **headless CLI**. Input/output format is 32-bit TIFF
  (the wrapper exports via Siril `savetif32`).
- `~/Library/Application Support/org.siril.Siril/siril/syqon_starless/zenith.pt`
  — the neural model (394 MB, downloaded+verified).
- The `SyQonStarless` **binary is NOT installed** (not in `/Applications`, not in
  PATH, no cached path in `~/.siril/syqon_starless/saved_binary_path.txt`).
- `VeraLux_StarComposer.py` — PyQt6 **GUI-only**, designed for stretched-domain
  star recomposition. Not automatable and not needed for linear composition; it
  stays the user's manual finishing tool. The pipeline's starless/stars
  deliverables are exactly the inputs it wants.

Decision: the starless sub-step is **optional and dormant** — it activates only
when the binary is found. Until then Step 10 behaves exactly as today.

## 1. Tool: `tools/starless.py`

```
.venv/bin/python tools/starless.py MASTER.fit --outdir DIR [--basename NAME]
    [--binary PATH] [--probe-only]
```

- **Probe order** (mirrors the SyQon wrapper): `--binary` argument →
  `~/.siril/syqon_starless/saved_binary_path.txt` (if the file exists and points
  to an executable) → `/Applications/SyQonStarless.app/Contents/MacOS/SyQonStarless`
  → `shutil.which("SyQonStarless")`. First executable hit wins.
- Not found → print `SYQON: NOT INSTALLED (install: https://syqon.it/starless)`,
  exit 0 (dormant, not an error). `--probe-only` prints
  `SYQON: OK (<path>)` / the NOT INSTALLED line and exits.
- Found → run headless star removal on a linear RGB FITS:
  1. FITS → 32-bit float TIFF via OpenCV (`cv2.imwrite`, channels reordered
     RGB→BGR; opencv-python-headless is already in the venv).
  2. `SyQonStarless -i in.tif -o out.tif -d Auto -t 512 -v 256 -c seti`
     (no `--gui`). Non-zero exit / missing output → clear error, non-zero exit.
  3. TIFF → FITS: `<base>_starless.fit` (input header + HISTORY line) and
     `<base>_stars.fit` = `clip(master − starless, 0, None)` (same header).
- Outputs float32, linear, header + WCS intact — same conventions as palette.py.
- Risk (documented, not testable until the binary is installed): the exact TIFF
  flavour SyQonStarless expects. The wrapper uses Siril `savetif32`
  (32-bit float, no compression); cv2 writes the same. First real run must be
  verified with `tools/preview.py` before trusting it in the pipeline.

## 2. `tools/palette.py` extension: `--starless FILE`

- New option `--starless STARLESS.fit`. When given:
  - Ha/OIII for **composition** come from the starless image;
  - the gate metric runs on the starless channels too (star suppression stays on
    — harmless, and the threshold 0.23 is unchanged; log the measured value as
    usual);
  - after composing HOO/SHO from starless channels, re-add the natural-colour
    star layer per channel: `out += clip(master − starless, 0, None)`
    (master = the positional input, e.g. the SPCC master; all linear, exact);
  - HISTORY line notes `starless composition + natural star re-add`.
- Shape mismatch between master and starless → clear error.
- Without `--starless`, behaviour is exactly as today (regression-guarded by the
  existing 13 tests).

## 3. Pipeline integration (Step 10, `.claude/commands/seestar-pipeline.md`)

Step 10 gains an optional starless sub-step, still always AUTO:

1. `tools/starless.py <master> --probe-only` → probe verdict, logged to REPORT.md.
2. **Found:** run `tools/starless.py <master> --outdir 05_stretch --basename
   <OBJECT>_final` → `<OBJECT>_final_starless.fit` + `<OBJECT>_final_stars.fit`;
   then `tools/palette.py <master> --starless 05_stretch/<OBJECT>_final_starless.fit
   --outdir 05_stretch --basename <OBJECT>_final`. On EMIT the HOO/SHO masters
   carry natural stars. Render previews for starless + both palettes; view them.
3. **Not found:** exactly today's behaviour (`tools/palette.py <master> ...`),
   plus the one-line `SYQON: NOT INSTALLED` note in REPORT.md.
4. Deliverables/cleanup: `<OBJECT>_final_starless.fit`, `<OBJECT>_final_stars.fit`
   (+ starless preview PNG) join the `05_stretch/` keep-list and the `DATADIR`
   copies — they are the inputs for the user's manual VeraLux StarComposer pass.
   README tools/ section gets a `tools/starless.py` bullet; the pipeline
   description mentions "optional SyQon starless".

## 4. Tests (`tools/test_starless.py` + additions to `tools/test_palette.py`)

No binary on CI/dev machines, so the binary call is isolated in one function and
mocked:

- probe: absent everywhere → NOT INSTALLED, exit 0; fake executable via `--binary`
  → OK.
- FITS↔TIFF round-trip: synthetic RGB FITS → TIFF → back, values equal within
  float32, channel order preserved (R stays R).
- run flow with a **stub binary** (tiny shell script that copies input TIFF to the
  output path): produces `_starless.fit` + `_stars.fit`, header preserved,
  stars = master − starless ≥ 0.
- palette `--starless`: synthetic master = emission cube + one star present in all
  channels; starless = same cube without the star. Output HOO: nebula channels
  from starless, star pixel present in R, G and B (natural, not palette-coloured);
  without `--starless` the star is palette-coloured. Shape-mismatch error case.

## Out of scope

- Driving `SyQon_Studio.py` / Siril GUI, VeraLux StarComposer automation.
- Running `zenith.pt` directly (torch dependency, licensing, reimplementing
  SyQon's tiling — not ours to rebuild).
- StarNet fallback.
- Stretched-domain star recomposition (that is VeraLux's job, manual).

## First validation once the binary is installed

`tools/starless.py <C103 SPCC master> --outdir scratch --basename C103` → view the
starless preview (no star residues, nebula intact), then palette `--starless` run
→ HOO preview must show natural white/blue star field over the red/teal nebula.
Record the verdict + timing in FINDINGS.md.
