"""Tests for hoo_recombine.py: dynamic HOO teal blend from starless Ha/OIII."""
import os
import tempfile

import numpy as np
import pytest
from astropy.io import fits

import hoo_recombine as hr


def _blobs(h=200, w=150, seed=0):
    """Two disjoint stretched starless channels: Ha blob at (60,40), OIII at (140,100)."""
    rng = np.random.default_rng(seed)
    ha = np.clip(rng.normal(0.05, 0.005, (h, w)), 0, None)
    oiii = np.clip(rng.normal(0.05, 0.005, (h, w)), 0, None)
    yy, xx = np.mgrid[0:h, 0:w]
    ha = ha + 0.9 * np.exp(-((yy - 60) ** 2 + (xx - 40) ** 2) / (2 * 12**2))
    oiii = oiii + 0.9 * np.exp(-((yy - 140) ** 2 + (xx - 100) ** 2) / (2 * 12**2))
    return ha, oiii


def test_ha_region_is_red():
    ha, oiii = _blobs()
    rgb, _ = hr.recombine(ha, oiii)
    r, g, b = rgb
    assert r[60, 40] > g[60, 40] and r[60, 40] > b[60, 40]


def test_oiii_region_is_teal():
    ha, oiii = _blobs()
    rgb, _ = hr.recombine(ha, oiii, oiii_blur=0.0)
    r, g, b = rgb
    # OIII blob: blue and green both above red -> teal, not red
    assert b[140, 100] > r[140, 100]
    assert g[140, 100] > r[140, 100]


def test_scnr_clamps_green():
    ha, oiii = _blobs()
    rgb, _ = hr.recombine(ha, oiii, do_scnr=True)
    r, g, b = rgb
    assert np.all(g <= 0.5 * (r + b) + 1e-6)


def test_output_is_valid_rgb():
    ha, oiii = _blobs()
    rgb, info = hr.recombine(ha, oiii)
    assert rgb.shape == (3, 200, 150)
    assert rgb.dtype == np.float32
    assert np.isfinite(rgb).all() and (rgb >= 0).all() and (rgb <= 1).all()
    assert set(info) >= {"a", "b", "blur", "boost", "linfit", "scnr"}


def test_boost_raises_oiii():
    ha, oiii = _blobs()
    base, _ = hr.recombine(ha, oiii, oiii_boost=1.0, oiii_blur=0.0, do_scnr=False)
    boosted, _ = hr.recombine(ha, oiii, oiii_boost=2.0, oiii_blur=0.0, do_scnr=False)
    # blue channel (=OIII) stronger away from saturation after boost
    assert boosted[2][140, 100] >= base[2][140, 100]


def test_shape_mismatch_exits():
    ha, _ = _blobs()
    with pytest.raises(SystemExit):
        hr.recombine(ha, np.zeros((10, 10)))


def test_load_mono_rejects_rgb_cube():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "cube.fit")
        fits.writeto(p, np.zeros((3, 40, 40), dtype=np.float32), overwrite=True)
        with pytest.raises(SystemExit):
            hr.load_mono(p)


def test_load_mono_accepts_single_layer_cube():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "m.fit")
        fits.writeto(p, np.ones((1, 40, 40), dtype=np.float32), overwrite=True)
        data, _ = hr.load_mono(p)
        assert data.shape == (40, 40)


def test_cli_writes_fits_and_png(capsys):
    with tempfile.TemporaryDirectory() as d:
        ha, oiii = _blobs()
        hp = os.path.join(d, "T_final_Ha.fit")
        op = os.path.join(d, "T_final_OIII.fit")
        fits.writeto(hp, ha.astype(np.float32), fits.Header({"OBJECT": "T", "CRVAL1": 1.5}))
        fits.writeto(op, oiii.astype(np.float32), overwrite=True)
        out = os.path.join(d, "T_final_HOO_teal.fit")
        hr.main([hp, op, "--out", out])
        text = capsys.readouterr().out
        assert "RECOMBINE: TEAL" in text
        assert os.path.exists(out) and os.path.exists(out.replace(".fit", ".png"))
        with fits.open(out) as h:
            assert h[0].data.shape == (3, 200, 150)
            assert h[0].header["OBJECT"] == "T"
            assert any("hoo_recombine.py" in str(c) for c in h[0].header.get("HISTORY", []))
