#!/usr/bin/env python3
"""Background-extract a Seestar stack on the Apple-Silicon GPU (CoreML), header preserved.

Thin wrapper over tools/gpu/gx_gpu.py (GraXpert AI background model). No GraXpert install
needed — the ONNX models are fetched from the GitHub release by tools/gpu/fetch_models.py.
The output keeps the input FITS header for plate solving / SPCC.

Usage:
  background.py INPUT.fits OUTPUT.fits [--smoothing 0.5] [--correction Subtraction|Division] [--cpu]
    --cpu   run on CPU (our onnxruntime) instead of the GPU.

Setup (once):
  bash tools/gpu/setup.sh
  tools/gpu/.venv/bin/python tools/gpu/fetch_models.py
"""
import sys, os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
GPU_PY = os.path.join(REPO, "tools", "gpu", ".venv", "bin", "python")
GPU_CLI = os.path.join(REPO, "tools", "gpu", "gx_gpu.py")


def main():
    argv = sys.argv[1:]
    cpu = "--cpu" in argv
    smoothing, correction, pos = "0.5", "Subtraction", []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--smoothing":
            smoothing = argv[i + 1]; i += 2
        elif a == "--correction":
            correction = argv[i + 1]; i += 2
        elif a == "--cpu":
            i += 1
        else:
            pos.append(a); i += 1
    if len(pos) < 2:
        print(__doc__); sys.exit(1)
    inp, out = pos[0], pos[1]
    if not os.path.exists(inp):
        sys.exit(f"input not found: {inp}")
    if not os.path.exists(GPU_PY):
        sys.exit("GPU venv missing — run: bash tools/gpu/setup.sh && "
                 "tools/gpu/.venv/bin/python tools/gpu/fetch_models.py")
    cmd = [GPU_PY, GPU_CLI, "background", inp, out,
           "--smoothing", str(smoothing), "--correction", correction]
    if cpu:
        cmd.append("--cpu")
    sys.exit(subprocess.run(cmd).returncode)


if __name__ == "__main__":
    main()
