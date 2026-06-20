# tools/gpu — Apple-Silicon GPU runner for GraXpert's denoise & background models

Runs GraXpert's denoise and background-extraction AI models on the **Apple-Silicon GPU**
(CoreML) **without GraXpert installed**. ~10× faster than CPU for denoise; output is
numerically identical to GraXpert (fp16-level diff, max ~1e-4).

## Why this exists

GraXpert ships its models with a dynamic ONNX batch dimension and asks onnxruntime for
CoreML `MLProgram + ALL` compute units. On Apple Silicon that combo **crashes** the CoreML
compiler (Neural-Engine respecialization abort) or silently falls back to CPU. The fix is
three things together:

1. **onnxruntime ≥ 1.20** (needs Python ≥ 3.10) — exposes the CoreML provider options.
2. CoreML `ModelFormat=MLProgram` + `MLComputeUnits=CPUAndGPU` (GPU, **skip the Neural Engine**).
3. The model's batch dimension **frozen** to a static `[1,256,256,3]` shape.

With all three, CoreML loads cleanly and runs on the GPU.

## Setup (once)

```
bash tools/gpu/setup.sh                                  # build py3.13 venv (ORT 1.27)
tools/gpu/.venv/bin/python tools/gpu/fetch_models.py     # download frozen models
```

`fetch_models.py` pulls the frozen `.onnx` from this repo's `models-v1` GitHub release
(no GraXpert needed). If you *do* have GraXpert installed, the models are auto-located from
its data dir and frozen on first use instead.

## Usage

```
tools/gpu/.venv/bin/python tools/gpu/gx_gpu.py denoise    IN.fits OUT.fits [--strength 0.3] [--cpu]
tools/gpu/.venv/bin/python tools/gpu/gx_gpu.py background  IN.fits OUT.fits [--smoothing 0.5] \
                                                          [--correction Subtraction|Division] [--cpu]
```

`--cpu` runs on our own onnxruntime (still no GraXpert). The original FITS header is preserved.

The skill wrappers `denoise.py` / `background.py` (in the seestar-* skill dirs) call this and
are the convenient entry points.

## Licensing

See [NOTICE.md](NOTICE.md). Models © GraXpert Development Team, **CC BY-NC-SA 4.0**
(NonCommercial); `gx_gpu.py` ports GraXpert GPL-3.0 code and is **GPL-3.0**.
