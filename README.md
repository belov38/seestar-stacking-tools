# Seestar stacking tools

Tools for stacking ZWO Seestar deep-sky FITS in Siril and finding the best
stacking parameters per session by measuring background noise and SNR.

## Contents

- `.claude/skills/seestar-stacking-compare/` — the skill (run a parameter sweep,
  measure each result, compare to the default Seestar script, save the winner):
  - `SKILL.md` — when/how to use + variant-selection guidance
  - `experiment_full.ssf` / `experiment_reuse.ssf` — Siril stacking scripts
  - `measure_stacks.py` — ranks results, BEST-vs-BASELINE verdict, writes `metrics.csv`
  - `test_measure_stacks.py` — pytest unit + smoke tests
- `Seestar_Preprocessing.ssf` — Cyril Richard's stock Seestar Siril script (reference baseline)
- `measure_stacks.py` — earlier dev copy of the measurer

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install astropy numpy pytest
```

## Run tests

```bash
cd .claude/skills/seestar-stacking-compare
../../../.venv/bin/python -m pytest -q
```

## Key finding

The optimal stacking parameters depend on **frame count AND target type** — there is
no single best setting, so you measure per session. Star-based weighting
(`-weight=nbstars`/`wfwhm`) is the most volatile knob: it won big on an open cluster
(M6, +7% faint SNR) but collapsed on a dense globular and on nebula-filled frames.
Always measure; adopt a tuned variant only on a measured ≥3% faint-SNR win.

Image data (`*.fit`) and the venv are gitignored.
