#!/usr/bin/env python3
"""Download the frozen GraXpert models for gx_gpu.py — no GraXpert install required.

Pulls the static-shape ONNX models from the GitHub release into tools/gpu/models/.
Tries a plain HTTPS download first (works once the repo/release is public); if that
fails (private repo, 404), falls back to `gh release download`, which uses your gh auth.

Models are © GraXpert Development Team, CC BY-NC-SA 4.0 (NonCommercial). See NOTICE.md.

Usage:  python fetch_models.py
"""
import os
import subprocess
import sys
import urllib.request

REPO = "belov38/seestar-stacking-tools"
TAG = "models-v1"
ASSETS = ["denoise_3.0.2_bs1.onnx", "background_1.0.1_bs1.onnx"]
DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")


def http_get(asset, dst):
    url = f"https://github.com/{REPO}/releases/download/{TAG}/{asset}"
    tmp = dst + ".part"
    with urllib.request.urlopen(url, timeout=30) as r, open(tmp, "wb") as f:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        while chunk := r.read(1 << 20):
            f.write(chunk)
    os.replace(tmp, dst)


def gh_get(asset):
    subprocess.run(
        ["gh", "release", "download", TAG, "-R", REPO,
         "--pattern", asset, "--dir", DEST, "--clobber"],
        check=True)


def main():
    os.makedirs(DEST, exist_ok=True)
    for asset in ASSETS:
        dst = os.path.join(DEST, asset)
        if os.path.isfile(dst):
            print(f"[fetch] have {asset}")
            continue
        print(f"[fetch] downloading {asset} ...", flush=True)
        try:
            http_get(asset, dst)
        except Exception as e:
            print(f"[fetch] HTTPS failed ({e}); trying gh ...", flush=True)
            try:
                gh_get(asset)
            except Exception as e2:
                sys.exit(f"[fetch] could not download {asset}: {e2}")
        print(f"[fetch] -> {dst}")
    print("[fetch] done")


if __name__ == "__main__":
    main()
