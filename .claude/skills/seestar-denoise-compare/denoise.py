#!/usr/bin/env python3
"""Denoise a Seestar stack on the Apple-Silicon GPU (CoreML), header preserved.

Thin wrapper over tools/gpu/gx_gpu.py. No GraXpert install needed — the ONNX models are
fetched from the GitHub release by tools/gpu/fetch_models.py. The output keeps the input
FITS header (OBJECT/RA/DEC/FOCALLEN/XPIXSZ/FILTER…) for plate solving / SPCC.

Usage:
  denoise.py INPUT.fits OUTPUT.fits [STRENGTH] [--cpu]
    STRENGTH  denoise strength 0.0-1.0 (default 0.3).
    --cpu     run on CPU (our onnxruntime) instead of the GPU.

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
    args = [a for a in sys.argv[1:] if a != "--cpu"]
    cpu = "--cpu" in sys.argv
    if len(args) < 2:
        print(__doc__); sys.exit(1)
    inp, out = args[0], args[1]
    strength = args[2] if len(args) > 2 else "0.3"
    if not os.path.exists(inp):
        sys.exit(f"input not found: {inp}")
    if not os.path.exists(GPU_PY):
        sys.exit("GPU venv missing — run: bash tools/gpu/setup.sh && "
                 "tools/gpu/.venv/bin/python tools/gpu/fetch_models.py")
    cmd = [GPU_PY, GPU_CLI, "denoise", inp, out, "--strength", str(strength)]
    if cpu:
        cmd.append("--cpu")
    sys.exit(subprocess.run(cmd).returncode)


if __name__ == "__main__":
    main()
