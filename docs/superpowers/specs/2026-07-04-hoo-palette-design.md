# HOO/SHO palette masters — design

Date: 2026-07-04
Status: approved (design), pending spec review

## Context

The Seestar S30 LP filter is a dual-band filter: it passes only Ha (656 nm) and
OIII (~500 nm). On the IMX585 OSC sensor, red pixels see essentially only Ha, while
green and blue pixels see essentially only OIII (green leaks ~10–15% of Ha). This
means every LP-filter dataset carries two independent emission channels that can be
recombined into narrowband-style palettes at near-zero cost.

A manual proof on the C103 (Tarantula) SPCC master produced a clean HOO render:
the 30 Dor core cavity shows cyan-blue OIII against red Ha filaments.

Decisions made during brainstorming (with Ilia):

- Deliverables: **SPCC (already exists) + HOO + SHO** — three linear, stretch-ready
  FITS masters with header + WCS intact, plus a preview PNG for each palette.
- Extraction method: **from the SPCC RGB master** (`Ha = R`, `OIII = (G+B)/2`) —
  not the canonical CFA `seqextract_HaOIII` path. The quick method keeps the
  pipeline chain untouched, runs in seconds, and works in ready-stack mode. The
  CFA path (cleaner separation, double stacking) is explicitly out of scope; it
  could become a measure-and-compare skill later.
- Trigger: **by measurement** — always compute an emission-separation metric;
  emit palette masters only when the target actually shows Ha/OIII separation
  (emission nebulae, SNRs, planetaries). Continuum targets (galaxies, clusters)
  get a one-line SKIP verdict in REPORT.md. Never stops to ask.
- Architecture: **standalone `tools/palette.py` + a short new pipeline step**
  (repo pattern, like `tools/score_subs.py` behind the quality gate). The tool is
  unit-testable and usable outside the pipeline on any existing master.

## 1. Tool: `tools/palette.py`

```
.venv/bin/python tools/palette.py MASTER.fit --outdir DIR \
    [--basename NAME] [--sho-alpha 0.3] [--force] [--metric-only]
```

- Input: a linear RGB FITS (3×H×W). A mono/2D input is a clear error ("palette
  extraction needs an RGB master").
- Extraction: `Ha = R`, `OIII = 0.5*G + 0.5*B`.
- Background neutralisation before composition: subtract each channel's own
  robust background (median), re-add a common pedestal (mean of the two channel
  medians). Data stays linear; the background becomes neutral grey so composites
  carry no colour cast. Structure is untouched.
- Outputs (float32, header copied from input incl. WCS, plus a HISTORY line with
  the exact formula used):
  - `<base>_HOO.fit` — channels `[Ha, OIII, OIII]`
  - `<base>_SHO.fit` — channels `[Ha, alpha*Ha + (1-alpha)*OIII, OIII]`,
    `alpha = 0.3` default (classic duo-band synthetic SHO; golden Ha after
    stretch). True SHO is impossible — the LP filter does not pass SII.
- `--basename` overrides `<base>` (default: input filename stem).
- `--metric-only` computes and prints the verdict without writing files.
- Exit code 0 on both EMIT and SKIP verdicts; non-zero only on real errors.

## 2. Gate metric (EMIT/SKIP verdict)

- Signal mask: pixels above background + 3σ on the combined `Ha + OIII` image
  (robust median/MAD estimates).
- Metric: MAD of `log2(Ha/OIII)` over the signal mask, both channels
  background-subtracted and clipped to positive values.
- Rationale: for continuum sources (stars, galaxies, clusters) Ha and OIII are
  proportional everywhere → ratio spread is small. For emission targets,
  Ha-dominant and OIII-dominant regions diverge → spread is large.
- Threshold: a named constant in the script, calibrated on real data —
  C103 Tarantula SPCC master must yield EMIT, the C76 open-cluster stack must
  yield SKIP. Measured values for both go into FINDINGS.md.
- Output format (one line, parseable):
  `PALETTES: EMIT (separation=0.42, threshold=0.15)` or
  `PALETTES: SKIP (separation=0.06, threshold=0.15)`.
- `--force` writes the palette files regardless of the verdict (still prints it).

## 3. Pipeline integration (`.claude/commands/seestar-pipeline.md`)

- New **Step 9b — palette masters (HOO/SHO)**, after Step 9 (SPCC), before
  Step 10 (stretch). Existing step numbers stay untouched.
- Runs `tools/palette.py` on the adopted master: `<OBJECT>_final_spcc.fit`, or
  `<OBJECT>_final_solved.fit` if SPCC failed (the metric normalises per channel,
  so uncalibrated balance is acceptable). Passes `--basename <OBJECT>_final` so
  outputs land as `<OBJECT>_final_HOO.fit` / `<OBJECT>_final_SHO.fit` regardless
  of which input master was used.
- Always AUTO — this step never stops to ask:
  - **EMIT** → write `<OBJECT>_final_HOO.fit` and `<OBJECT>_final_SHO.fit` into
    `05_stretch/`, render a preview PNG for each with `tools/preview.py` into
    `05_stretch/` (not `previews/`, so they survive Step 11 cleanup), print the
    universal `validate here:` line, log metric + adopted outputs to REPORT.md.
  - **SKIP** → one REPORT.md line with the measured separation; no files.
- Steps table gets a row: Step 9b, AUTO-adopt "always (EMIT or SKIP by metric)",
  STOP "never".
- Finish step: on EMIT, copy both palette `.fit` + PNGs next to the input
  (`DATADIR`) with the other deliverables; `astrobin.txt` description mentions
  the available palettes.
- Step 11 cleanup: no change needed — keep-list already preserves `05_stretch/`.
- README pipeline section: sync with the new step (repo habit).

## 4. Tests (`tools/test_palette.py`, pytest)

- Synthetic continuum frame (identical structure in all channels + noise) →
  SKIP verdict.
- Synthetic emission frame (disjoint Ha-only and OIII-only blobs) → EMIT, and
  the HOO output maps channels correctly (R carries the Ha blob, G/B carry the
  OIII blob).
- Header/WCS preservation: input header keys survive into both outputs.
- Output sanity: float32, all finite, non-negative.
- Mono input raises the clear error.

## Error handling

- Missing/unreadable input, non-RGB shape → clear error message, non-zero exit.
- Degenerate mask (almost no signal pixels, e.g. empty field) → SKIP verdict
  with a note, not a crash.

## Out of scope

- Canonical CFA `seqextract_HaOIII` extraction with separate Ha/OIII stacks
  (possible future `seestar-palette-compare` skill to quantify the gain).
- Dynamic Foraxx-style SHO blending (fixed-alpha blend is the v1; alpha is a
  CLI knob).
- Star-colour preservation via star masks in palette composites.
- Mono `_Ha.fit` / `_OIII.fit` deliverables (not requested; trivial to add to
  the tool later if wanted).

## First validation after implementation

Run the tool standalone on the existing C103 SPCC master and view both palette
previews; run `--metric-only` on the C76 stack to confirm the SKIP side of the
threshold. Record both measured separations in FINDINGS.md.
