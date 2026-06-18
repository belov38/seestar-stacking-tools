# Vendored third-party code

`vendor/setiastro/saspro/` contains a **minimal subset** of
[setiastro/setiastrosuitepro](https://github.com/setiastro/setiastrosuitepro),
pinned to commit `297f1f87d56443e51f2bcfcf05dc70f469c6b740` (2026-06-14).

License: **GPL-3.0** (upstream). The vendored files are unmodified; all adaptations
needed to run headless live as runtime monkeypatches in `mf_deconv.py`, not in the
vendored source.

Vendored modules (only what `mfdeconv` needs, no GUI/Qt deps):

| file | role |
|---|---|
| `mfdeconv.py` | multi-frame robust Richardson-Lucy deconvolution |
| `mfdeconv_earlystop.py` | `EarlyStopper` (convergence-based stop) |
| `runtime_torch.py` | torch loader (bypassed by our shim) |
| `psf_utils.py` | empirical per-frame PSF kernel estimation |
| `free_torch_memory.py` | MPS/CUDA cache release |
| `memory_utils.py` | `LRUDict` frame cache |

The full upstream repo (≈118 MB) also includes XISF loaders (`legacy/`) that pull in
PyQt6; those are imported in try/except blocks and are unused here (we load FITS via
astropy), so they are intentionally not vendored.

To refresh: re-clone upstream at a new commit and re-copy these six files; update the
commit hash above and re-verify against `requirements.txt`.
