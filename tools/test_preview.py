"""Smoke tests for preview.py: a composite PNG is produced from a synthetic RGB FITS."""
import os, tempfile
import numpy as np
from astropy.io import fits
from PIL import Image

import preview


def _synthetic_rgb(path, h=240, w=160, n_stars=12, seed=0):
    """Linear RGB FITS (chan, H, W) with a faint background and a few bright stars."""
    rng = np.random.default_rng(seed)
    img = rng.normal(100.0, 5.0, size=(3, h, w)).astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w]
    for _ in range(n_stars):
        cy = rng.integers(20, h - 20); cx = rng.integers(20, w - 20)
        amp = rng.uniform(2000, 8000)
        g = np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * 1.6 ** 2))
        for c in range(3):
            img[c] += amp * g * rng.uniform(0.7, 1.0)
    hdr = fits.Header({"OBJECT": "TEST", "NAXIS": 3})
    fits.writeto(path, img, hdr, overwrite=True)


def test_single_frame_preview():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "res.fits"); out = os.path.join(d, "p.png")
        _synthetic_rgb(src)
        path, nstars = preview.build(src, None, out, "test", 6, 32)
        assert os.path.exists(path)
        im = Image.open(out)
        assert im.width > 100 and im.height > 100
        assert nstars >= 1                      # sep should find the planted stars


def test_before_after_preview_shared_stretch():
    with tempfile.TemporaryDirectory() as d:
        before = os.path.join(d, "b.fits"); after = os.path.join(d, "a.fits")
        out = os.path.join(d, "ba.png")
        _synthetic_rgb(before, seed=1)
        _synthetic_rgb(after, seed=1)           # same lineage
        path, _ = preview.build(after, before, out, "ba", 6, 32)
        assert os.path.exists(path)
        # before/after canvas is wider than a single-frame one
        single = os.path.join(d, "s.png")
        preview.build(after, None, single, "s", 6, 32)
        assert Image.open(out).width > Image.open(single).width


def test_mono_frame_does_not_crash():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "m.fits"); out = os.path.join(d, "m.png")
        data = np.random.default_rng(2).normal(100, 5, size=(200, 200)).astype(np.float32)
        fits.writeto(src, data, overwrite=True)
        path, _ = preview.build(src, None, out, "mono", 6, 32)
        assert os.path.exists(path)
