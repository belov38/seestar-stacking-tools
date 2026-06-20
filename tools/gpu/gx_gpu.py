#!/usr/bin/env python3
"""Apple-Silicon (CoreML GPU) runner for GraXpert's denoise and background models.

GraXpert ships its inference models as ONNX with a dynamic batch dimension and asks
onnxruntime for CoreML MLProgram + ALL compute units. On Apple Silicon that combo
crashes the CoreML compiler (Neural-Engine respecialization abort) or silently falls
back to CPU. This tool reuses the exact same model.onnx files but:

  * freezes the batch dimension to a static [1,256,256,3] shape, and
  * runs CoreML with ModelFormat=MLProgram, MLComputeUnits=CPUAndGPU (GPU, skip ANE),

which loads cleanly and runs ~7-8x faster than CPU with fp16-level numerical diff.

The image I/O, normalization and tiling reproduce GraXpert's own code so the output
matches the CPU result. The original FITS header is preserved.

Usage:
  gx_gpu.py denoise    INPUT.fits OUTPUT.fits [--strength 0.3] [--cpu]
  gx_gpu.py background INPUT.fits OUTPUT.fits [--smoothing 0.5]
                       [--correction Subtraction|Division] [--cpu]

Options:
  --cpu   run on CPU with the original dynamic model (for A/B comparison).

Needs (in the root venv, see README): onnxruntime>=1.20, numpy, astropy,
scikit-image, opencv-python-headless.
"""
import argparse
import os
import sys

import numpy as np

# GraXpert stores downloaded models here (latest version wins).
GX_DATA = os.path.expanduser("~/Library/Application Support/GraXpert")
MODEL_DIRS = {
    "denoise": "denoise-ai-models",
    "background": "bge-ai-models",
}
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")


# --------------------------------------------------------------------------- models
def latest_model(op):
    """Return (path_to_model.onnx, version_string) for the newest local version."""
    base = os.path.join(GX_DATA, MODEL_DIRS[op])
    if not os.path.isdir(base):
        sys.exit(f"no GraXpert {op} models at {base} — run GraXpert once to download them")
    from packaging import version as V
    versions = [d for d in os.listdir(base)
                if os.path.isfile(os.path.join(base, d, "model.onnx"))]
    if not versions:
        sys.exit(f"no model.onnx under {base}")
    v = sorted(versions, key=V.parse)[-1]
    return os.path.join(base, v, "model.onnx"), v


def local_frozen(op):
    """Newest already-frozen static model in CACHE_DIR, or None.

    These are self-contained (no GraXpert needed) — either frozen locally from a
    GraXpert install, or fetched by fetch_models.py.
    """
    import glob
    from packaging import version as V
    found = glob.glob(os.path.join(CACHE_DIR, f"{op}_*_bs1.onnx"))
    if not found:
        return None
    def ver(p):
        try:
            return V.parse(os.path.basename(p).split("_")[1])
        except Exception:
            return V.parse("0")
    return sorted(found, key=ver)[-1]


def frozen_model(op):
    """Static-shape model with batch dim fixed to 1. Prefer a local/fetched copy;
    otherwise freeze from a local GraXpert install."""
    local = local_frozen(op)
    if local:
        return local
    # No frozen copy yet — need GraXpert's source model to make one.
    base = os.path.join(GX_DATA, MODEL_DIRS[op])
    if not os.path.isdir(base):
        sys.exit(
            f"no frozen {op} model in {CACHE_DIR} and no GraXpert install at {base}.\n"
            f"Run:  python {os.path.join(os.path.dirname(__file__), 'fetch_models.py')}")
    src, ver = latest_model(op)
    os.makedirs(CACHE_DIR, exist_ok=True)
    dst = os.path.join(CACHE_DIR, f"{op}_{ver}_bs1.onnx")
    import onnx
    from onnxruntime.tools.onnx_model_utils import make_input_shape_fixed, fix_output_shapes
    m = onnx.load(src)
    make_input_shape_fixed(m.graph, "gen_input_image", [1, 256, 256, 3])
    fix_output_shapes(m)
    onnx.save(m, dst)
    print(f"[gx_gpu] froze {op} model {ver} -> {os.path.basename(dst)}", flush=True)
    return dst


def make_session(op, use_gpu):
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.log_severity_level = 3
    if use_gpu:
        path = frozen_model(op)
        provs = [("CoreMLExecutionProvider",
                  {"ModelFormat": "MLProgram",
                   "MLComputeUnits": "CPUAndGPU",
                   "RequireStaticInputShapes": "1"}),
                 "CPUExecutionProvider"]
    else:
        # CPU works with either the dynamic original or the frozen static model.
        path = (latest_model(op)[0]
                if os.path.isdir(os.path.join(GX_DATA, MODEL_DIRS[op]))
                else frozen_model(op))
        provs = ["CPUExecutionProvider"]
    s = ort.InferenceSession(path, sess_options=so, providers=provs)
    print(f"[gx_gpu] providers: {s.get_providers()}", flush=True)
    return s


# --------------------------------------------------------------------------- FITS I/O
def load_fits(path):
    """Mirror GraXpert AstroImage.set_from_file: HxWxC float32 in [0,1] + header."""
    from astropy.io import fits
    from skimage import exposure
    from skimage.util import img_as_float32
    with fits.open(path) as hdul:
        data = np.copy(hdul[0].data)
        header = hdul[0].header.copy()
    if data.ndim == 3:                      # FITS is CxHxW -> HxWxC
        data = np.moveaxis(data, 0, -1)
    if data.ndim == 2:                      # greyscale -> HxWx1
        data = data[:, :, np.newaxis]
    img = img_as_float32(data)
    if np.min(img) < 0 or np.max(img) > 1:
        img = exposure.rescale_intensity(img, out_range=(0, 1))
    return img, header


def save_fits(path, img, header, provenance):
    """Mirror GraXpert AstroImage.save (32 bit Fits), keeping the original header."""
    from astropy.io import fits
    out = img.astype(np.float32)
    if out.shape[-1] == 3:                  # HxWxC -> CxHxW
        out = np.moveaxis(out, -1, 0)
    else:
        out = out[:, :, 0]
    header["GX-GPU"] = provenance
    hdu = fits.PrimaryHDU(data=out, header=header)
    fits.HDUList([hdu]).writeto(path, output_verify="warn", overwrite=True)


# --------------------------------------------------------------------------- denoise
def denoise(image, session, strength, model_threshold=10.0,
            window_size=256, stride=128):
    """Port of graxpert.denoising.denoise with batch_size=1 (static-shape friendly)."""
    inp = np.copy(image)
    median = np.median(image[::4, ::4, :], axis=(0, 1))
    mad = np.median(np.abs(image[::4, ::4, :] - median), axis=(0, 1))

    num_colors = image.shape[-1]
    if num_colors == 1:
        image = np.repeat(image, 3, axis=-1)

    H, W, _ = image.shape
    offset = (window_size - stride) // 2
    h, w, _ = image.shape
    ith = h // stride + 1
    itw = w // stride + 1
    dh = ith * stride - h
    dw = itw * stride - w

    image = np.concatenate((image, image[(h - dh):, :, :]), axis=0)
    image = np.concatenate((image, image[:, (w - dw):, :]), axis=1)
    h, w, _ = image.shape
    image = np.concatenate((image, image[(h - offset):, :, :]), axis=0)
    image = np.concatenate((image[:offset, :, :], image), axis=0)
    image = np.concatenate((image, image[:, (w - offset):, :]), axis=1)
    image = np.concatenate((image[:, :offset, :], image), axis=1)

    output = np.copy(image)
    in_name = session.get_inputs()[0].name

    for i in range(ith):
        for j in range(itw):
            x = stride * i
            y = stride * j
            tile = image[x:x + window_size, y:y + window_size, :]
            tile = (tile - median) / mad * 0.04
            tile_copy = np.copy(tile)
            tile = np.clip(tile, -model_threshold, model_threshold)
            res = session.run(None, {in_name: tile[np.newaxis].astype(np.float32)})[0][0]
            res = np.where(tile_copy < model_threshold, res, tile_copy)
            res = res / 0.04 * mad + median
            res = res[offset:offset + stride, offset:offset + stride, :]
            output[x + offset:stride * (i + 1) + offset,
                   y + offset:stride * (j + 1) + offset, :] = res

    output = output[offset:H + offset, offset:W + offset, :]
    if num_colors == 1:
        output = output[:, :, :1]

    threshold = model_threshold / 0.04 * mad + median
    blend = np.where(inp < threshold, output, inp)
    blend = blend * strength + inp * (1 - strength)
    return np.clip(blend, 0, 1)


# ------------------------------------------------------------------------- background
def extract_background(image, session, smoothing, correction):
    """Port of graxpert.background_extraction.extract_background (AI path)."""
    import cv2

    def gaussian_kernel(sigma, truncate=4.0):
        k = round(sigma * truncate)
        k = k - 1 if k % 2 == 0 else k
        return (k, k)

    num_colors = image.shape[-1]
    imarray = image.astype(np.float32).copy()

    padding = 8
    shrink = cv2.resize(imarray, dsize=(256 - 2 * padding, 256 - 2 * padding),
                        interpolation=cv2.INTER_LINEAR)
    if shrink.ndim == 2:
        shrink = shrink[:, :, np.newaxis]
    shrink = np.pad(shrink, ((padding, padding), (padding, padding), (0, 0)), mode="edge")

    median = [np.median(shrink[:, :, c]) for c in range(num_colors)]
    mad = [np.median(np.abs(shrink[:, :, c] - median[c])) for c in range(num_colors)]

    shrink = (shrink - median) / mad * 0.04
    shrink = np.clip(shrink, -1.0, 1.0)
    if num_colors == 1:
        shrink = np.repeat(shrink, 3, axis=-1)

    in_name = session.get_inputs()[0].name
    background = session.run(None, {in_name: shrink[np.newaxis].astype(np.float32)})[0][0]
    background = background / 0.04 * np.array(mad) + np.array(median) \
        if num_colors == 3 else background / 0.04 * mad[0] + median[0]

    if smoothing != 0:
        sigma = smoothing * 20
        background = cv2.GaussianBlur(background, ksize=gaussian_kernel(sigma),
                                      sigmaX=sigma, sigmaY=sigma)
    if num_colors == 1:
        background = background[:, :, :1]
    if padding != 0:
        background = background[padding:-padding, padding:-padding, :]

    sigma = 3.0
    background = cv2.GaussianBlur(background, ksize=gaussian_kernel(sigma),
                                  sigmaX=sigma, sigmaY=sigma)
    background = cv2.resize(background, dsize=(image.shape[1], image.shape[0]),
                            interpolation=cv2.INTER_LINEAR)
    if background.ndim == 2:
        background = background[:, :, np.newaxis]

    if correction == "Subtraction":
        mean = np.mean(background)
        imarray = imarray - background + mean
    elif correction == "Division":
        for c in range(num_colors):
            mean = np.mean(imarray[:, :, c])
            imarray[:, :, c] = imarray[:, :, c] / background[:, :, c] * mean
    return np.clip(imarray, 0.0, 1.0)


# --------------------------------------------------------------------------- CLI
def main():
    p = argparse.ArgumentParser(description="CoreML GPU runner for GraXpert models")
    sub = p.add_subparsers(dest="op", required=True)

    pd = sub.add_parser("denoise")
    pd.add_argument("input"); pd.add_argument("output")
    pd.add_argument("--strength", type=float, default=0.3)
    pd.add_argument("--cpu", action="store_true")

    pb = sub.add_parser("background")
    pb.add_argument("input"); pb.add_argument("output")
    pb.add_argument("--smoothing", type=float, default=0.5)
    pb.add_argument("--correction", choices=["Subtraction", "Division"], default="Subtraction")
    pb.add_argument("--cpu", action="store_true")

    a = p.parse_args()
    if not os.path.exists(a.input):
        sys.exit(f"input not found: {a.input}")

    img, header = load_fits(a.input)
    use_gpu = not a.cpu
    session = make_session(a.op, use_gpu)

    import time
    t0 = time.perf_counter()
    if a.op == "denoise":
        out = denoise(img, session, a.strength)
        prov = f"denoise strength={a.strength} {'GPU' if use_gpu else 'CPU'}"
    else:
        out = extract_background(img, session, a.smoothing, a.correction)
        prov = f"background smoothing={a.smoothing} {a.correction} {'GPU' if use_gpu else 'CPU'}"
    dt = time.perf_counter() - t0

    save_fits(a.output, out, header, prov)
    print(f"[gx_gpu] {a.op} done in {dt:.1f}s -> {a.output}", flush=True)


if __name__ == "__main__":
    main()
