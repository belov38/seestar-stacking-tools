# RC Astro CLI integration — design

Date: 2026-07-21
Status: approved-pending-review

## Context

The `rc-astro` CLI (v1.1.0) packages BlurXTerminator (`bxt`), StarXTerminator (`sxt`) and
NoiseXTerminator (`nxt`) as licensed command-line tools. Verified on this machine:

- FITS in/out works natively; the full input header (incl. WCS/SIP) is preserved.
- GPU inference via CoreML; `--json` gives a machine-readable event stream.
- License state is queryable per product: `rc-astro --no-banner --json <p> --license`
  → `licenseStatus.valid: true|false`. Licenses are per-product — a user may own any subset.
- Install location observed: `/Applications/RC-Astro/CLI/` + `rc-astro` on PATH
  (`/usr/local/bin`). No Homebrew formula; users download the installer from rc-astro.com
  (requires their account).

Goal: integrate all three tools into the Seestar pipeline as **optional** steps — present and
licensed → preferred; absent → the existing built-in path runs unchanged, quietly.

## Decisions (agreed)

1. **Scope:** integrate all three products (bxt, sxt, nxt).
2. **Policy: prefer rc-astro when present.** A licensed tool replaces the default method for
   its step; the existing `measure_*.py` verdicts still validate the result (adopt rules
   unchanged). No cross-method contest is run.
3. **License UX:** the README install runbook asks "do you own RC Astro licenses?" and guides
   CLI install + activation only on yes. At runtime the pipeline probes once in Step 1,
   reports one summary line, then falls back quietly per step (no per-step nagging).
   Activation is always performed by the user themselves (license keys never pass through
   the agent).
4. **No auto-composition.** The pipeline's job ends at decomposed, orientation-matched
   layers (starless nebula + stars). The user composes in their own tool (Alchemy).
   Consequences:
   - The Ha/OIII channel-split step (`palette.py`) is **removed from the pipeline** (the
     user splits channels in Alchemy); `tools/palette.py` stays in the repo as a manual tool.
   - The teal-HOO recombine step is **removed from the pipeline** (both the auto path and
     the manual starless handoff); `tools/hoo_recombine.py` stays as a manual tool.
   - No screen-blend/recompose tool is added.
5. **Mixed-filter sessions gain a third routing option:** "LP → nebula, IRCUT → stars"
   (available only when sxt is licensed) — the LP majority runs the full pipeline, the IRCUT
   subs are set aside for a stars-only mini-run consumed by the two-filter layers step.
6. **User stretch wins:** wherever the pipeline needs a stretched frame for sxt, it uses a
   deterministic Siril `autostretch`; if the user has placed their own `*_stretched.fit`
   next to the run (e.g. a GHS stretch), the pipeline picks that up instead.

## Renumbered pipeline

| # | Step | Change |
|---|------|--------|
| 1 | Explore & quarantine | + `rcastro.py probe` → one line in the user summary and REPORT.md (`RCASTRO: cli=1.1.0 bxt=ok sxt=ok nxt=no` or `RCASTRO: absent`). Mixed-filter STOP gains option 3: "LP → nebula, IRCUT → stars" (sxt only); chosen → IRCUT subs move to `<DATADIR>/_ircut_stars/`. |
| 2 | Run dir | unchanged |
| 3 | Frame quality gate | unchanged (also applied to the stars mini-run in Step 12) |
| 4 | Stack | unchanged |
| 5 | Background extraction | unchanged |
| 6 | Deconvolution | **bxt when licensed:** run 2 variants on the linear master — default (`--ss 0.5 --sn 1.0`, auto-PSF) and `--correct-only`; validate with the existing `measure_deconv.py` (adopt: FWHM gain ≥3% AND ring_worst ≥ −1×RMS; REJECT → keep baseline). The Siril RL sweep is not run at all. Unlicensed → existing Siril RL path. Stop rule unchanged (doubt → STOP with preview). |
| 7 | Denoise | **nxt when licensed:** sweep `--dn 0.1/0.25/0.5/0.75/0.9`; validate with the existing `measure_denoise.py` (strongest clean: FWHM Δ < 3%, faint_keep > 0.85; all over-blur → propose skip). Unlicensed → existing GraXpert GPU sweep. |
| 8 | Plate-solve | unchanged |
| 9 | SPCC | unchanged |
| 10 | Stretch | unchanged (was Step 11); the old Step 10 (Ha/OIII split) is deleted per decision 4 |
| — | Finish | astrobin.txt processing chain reflects the adopted tools (BXT/NXT when used); Ha/OIII master mentions and copies removed |
| 11 | Starless decomposition *(optional; sxt licensed; any run)* | new — replaces the old Step 12 (teal recombine). (a) stretch the adopted master: Siril `autostretch` → `<OBJECT>_final_stretched.fit`, unless a user-provided `*_stretched.fit` exists; (b) `rcastro.py sxt <stretched> <OBJECT>_final_starless.fit --stars --unscreen` → also writes `<OBJECT>_final_stars.fit`; both on the same pixel grid (sxt never moves pixels). (c) previews into `05_stretch/`, REPORT lines, copies to DATADIR. Offered, never auto-run; not offered without sxt. |
| 12 | Two-filter star layers *(optional; sxt licensed; LP + IRCUT data)* | replaces the old Step 13 composite offer. Path A (two full masters): `composite.py --mode align` → stretch both (autostretch / user override) → LP `sxt` → `<OBJECT>_final_starless.fit`; IRCUT `sxt --stars --unscreen` → `<OBJECT>_final_IRCUT_stars.fit`. Path B (IRCUT minority from Step 1): stars mini-run on `_ircut_stars/`, all intermediates under `<RUN>/stars_run/` — Step 3 gate → stack (baseline winsor 3/3, no sweep) → background extraction → plate-solve → SPCC (`UV/IR Block`); deconv/denoise skipped (stars don't need them) — then align/stretch/sxt as Path A. Deliver layers as-is (starless + stars + linear `IRCUT_aligned`), no blending. Combined acquisition CSV covers both sub sets. Without sxt: the existing `--mode align`/`--mode hargb` offer stays as today. |
| 13 | Cleanup | unchanged (was Step 14); `<RUN>/stars_run/` intermediates included in the prunable set (its final layers are already in `05_stretch/`) |

## Components

### tools/rcastro.py (new)

- `probe` subcommand: locates `rc-astro` (PATH, then `/usr/local/bin/rc-astro`), queries each
  of bxt/sxt/nxt with `--no-banner --json <p> --license`, prints exactly one line:
  `RCASTRO: cli=<ver> bxt=ok|no sxt=ok|no nxt=ok|no` or `RCASTRO: absent`. Always exit 0
  (absence is not an error). Malformed JSON / non-zero product exit → that product reports `no`.
- `bxt|nxt|sxt IN OUT [args...]` subcommands: thin passthrough — build
  `rc-astro --no-banner --json <p> IN -o OUT --overwrite [args...]`, stream/parse JSON events,
  fail loudly (non-zero exit + stderr message) if the run errors or OUT is missing afterwards.
  Product parameters are not re-modelled; extra args pass through verbatim.
- No activation handling, no key storage.

### test_rcastro.py (new)

Unit tests for probe parsing with the CLI mocked (monkeypatched subprocess): licensed,
unlicensed, CLI absent, malformed JSON. No inference in tests.

### .claude/commands/seestar-pipeline.md (rewrite of affected sections)

- Fixed facts: add rc-astro CLI + probe line semantics.
- Step 1: probe + summary line + third mixed-filter option.
- Steps 6/7: bxt/nxt preferred-path blocks with fallback notes.
- Delete old Step 10 (palette) and old Step 12 (teal recombine, incl. manual handoff).
- New Steps 11/12/13 as specified above; renumber throughout; update the step table and the
  Finish/cleanup references.

### Skill edits

- `seestar-deconvolution-compare/SKILL.md`: add "RC Astro path (bxt)" section — when to
  prefer, the two variants, validation via existing `measure_deconv.py`, fallback.
- `seestar-denoise-compare/SKILL.md`: add "RC Astro path (nxt)" section — sweep values,
  validation via existing `measure_denoise.py`, fallback.
- Stacking/background skills: untouched.

### Root README.md (must be updated — verify before closing the task)

- **Automated install runbook:** new optional step (after GPU models): ask "do you own
  RC Astro licenses (BlurX/StarX/NoiseXTerminator)?" — no → skip (say the pipeline runs fully
  on built-in tools); yes → user downloads/installs the CLI from rc-astro.com themselves,
  agent verifies `rc-astro` on PATH, user runs `rc-astro <p> --activate` themselves (suggest
  the `!` prefix; keys never through the agent), then `rc-astro download-models`, verify with
  the probe line.
- **Pipeline & skills table:** deconv row → "Siril RL ~10it / BXT (licensed)"; denoise row →
  "GraXpert ~0.3 / NXT (licensed)".
- **`/seestar-pipeline` description:** drop the Ha/OIII-split and teal-recombine sentences;
  describe the starless decomposition and two-filter layers offers (incl. the
  "LP → nebula, IRCUT → stars" mixed-session mode) and the layer deliverables.
- **tools/ list:** add `rcastro.py`; reword `palette.py` and `hoo_recombine.py` entries as
  manual (out-of-pipeline) tools; drop their "Backs the pipeline's Step N" sentences;
  update `composite.py`'s step reference to Step 12.
- **Setup (manual):** one line pointing at the optional RC Astro step.

## Deliverable set (all layers land in `05_stretch/` + copies in DATADIR)

| File | What |
|---|---|
| `<OBJECT>_final_spcc.fit` | linear calibrated master (unchanged) |
| `<OBJECT>_final_stretched.fit` | stretched master (autostretch or user-provided) |
| `<OBJECT>_final_starless.fit` | nebula/galaxy layer, no stars |
| `<OBJECT>_final_stars.fit` | stars layer (single-filter run) |
| `<OBJECT>_final_IRCUT_stars.fit` | broadband-colour stars layer (two-filter path) |
| `<OBJECT>_final_IRCUT_aligned.fit` | linear aligned IRCUT master (two-filter path) |

All sxt outputs share the input's pixel grid; the two-filter path aligns first
(`composite.py`, WCS reprojection), so every delivered layer is orientation-matched and
composition-ready.

## Error handling

- Probe never blocks the pipeline; `absent`/`no` simply select the fallback path.
- A failed rc-astro run at Steps 6/7 (crash, missing output) → warn, fall back to the
  built-in method for that step, log to REPORT.md.
- A failed sxt at Steps 11/12 → warn, deliver what exists (e.g. stretched master only),
  never fail the run.
- License expiry mid-usage surfaces as a product error → same fallback behavior.

## Testing

- `test_rcastro.py` as above (runs in CI-less `pytest` alongside existing `test_*.py`).
- Manual smoke (already done during design): sxt on a 512×512 FITS crop — FITS I/O + header
  + WCS preservation confirmed.
- First real-data behavior of bxt/nxt (does `measure_deconv.py` pass bxt cleanly?) is
  documented in FINDINGS.md after the first live run — out of scope here.

## Out of scope

- Any auto-composition (screen blend, HOO recombine) — user composes in Alchemy.
- Homebrew packaging of rc-astro; Windows/Linux support (CLI is macOS here).
- Storing or transmitting license keys.
