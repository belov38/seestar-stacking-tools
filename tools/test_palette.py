"""Tests for palette.py: dual-band Ha/OIII extraction, gate metric, HOO composition."""
import os
import tempfile

import numpy as np
import pytest
from astropy.io import fits

import palette


def _continuum(h=200, w=150, seed=0):
    """Continuum-only cube: identical structure in all channels, per-channel scale only."""
    rng = np.random.default_rng(seed)
    base = rng.normal(0.1, 0.005, (h, w))
    yy, xx = np.mgrid[0:h, 0:w]
    for _ in range(15):
        cy = rng.integers(10, h - 10)
        cx = rng.integers(10, w - 10)
        base = base + rng.uniform(0.5, 1.0) * np.exp(
            -((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * 2.0**2)
        )
    return np.stack([base * s for s in (1.0, 0.9, 0.8)])


def _emission(h=200, w=150, seed=0):
    """Emission cube: disjoint Ha-only blob at (60,40) and OIII-only blob at (140,100)."""
    rng = np.random.default_rng(seed)
    r = rng.normal(0.1, 0.005, (h, w))
    g = rng.normal(0.1, 0.005, (h, w))
    b = rng.normal(0.1, 0.005, (h, w))
    yy, xx = np.mgrid[0:h, 0:w]
    ha_blob = 0.8 * np.exp(-((yy - 60) ** 2 + (xx - 40) ** 2) / (2 * 12**2))
    o_blob = 0.8 * np.exp(-((yy - 140) ** 2 + (xx - 100) ** 2) / (2 * 12**2))
    return np.stack([r + ha_blob, g + o_blob, b + o_blob])


def _write(path, cube, header=None):
    fits.writeto(path, cube.astype(np.float32), header, overwrite=True)


def test_extract_channel_mapping():
    ha, oiii = palette.extract_ha_oiii(_emission())
    assert ha[60, 40] > 0.5 and ha[140, 100] < 0.3      # Ha blob only in R
    assert oiii[140, 100] > 0.5 and oiii[60, 40] < 0.3  # OIII blob only in G+B


def test_neutralize_background_equalizes_medians():
    ha, oiii = palette.extract_ha_oiii(_emission())
    ha_n, oiii_n, pedestal = palette.neutralize_background(ha, oiii)
    assert abs(np.median(ha_n) - np.median(oiii_n)) < 1e-6
    assert abs(np.median(ha_n) - pedestal) < 1e-6
    # structure untouched: blob amplitude over its own background is preserved
    assert abs((ha_n[60, 40] - np.median(ha_n)) - (ha[60, 40] - np.median(ha))) < 1e-9


def test_load_rgb_master_rejects_mono():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "mono.fit")
        fits.writeto(p, np.zeros((50, 50), dtype=np.float32), overwrite=True)
        with pytest.raises(SystemExit):
            palette.load_rgb_master(p)


def _star_field(h=200, w=150, seed=5):
    """Isolated stars of diverse colours on a grid — point sources, no emission.

    This is the M6 failure mode: star-colour diversity must not fake emission
    separation. Point sources are not extended signal, so after star suppression
    the mask must come up empty. (The absolute threshold for messy real fields —
    close pairs, saturated halos — is anchored on real masters in FINDINGS.md;
    a 200x150 toy cannot reproduce their statistics.)
    """
    rng = np.random.default_rng(seed)
    r = rng.normal(0.1, 0.005, (h, w))
    g = rng.normal(0.1, 0.005, (h, w))
    b = rng.normal(0.1, 0.005, (h, w))
    yy, xx = np.mgrid[0:h, 0:w]
    for cy in (40, 100, 160):
        for cx in (30, 75, 120):
            amp = rng.uniform(0.3, 1.0)
            colour = rng.uniform(0.4, 1.6)  # per-star R/(G+B), like a real HR diagram
            star = np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * 2.0**2))
            r = r + amp * colour * star
            g = g + amp * star
            b = b + amp * star
    return np.stack([r, g, b])


def test_separation_skips_continuum():
    ha, oiii = palette.extract_ha_oiii(_continuum())
    sep = palette.emission_separation(ha, oiii)
    # SKIP semantics: no extended signal left (None) or spread under threshold
    assert sep is None or sep < palette.SEPARATION_THRESHOLD


def test_separation_ignores_star_colour_diversity():
    ha, oiii = palette.extract_ha_oiii(_star_field())
    # point sources are suppressed entirely: no extended signal -> degenerate mask
    assert palette.emission_separation(ha, oiii) is None


def test_separation_high_for_emission():
    ha, oiii = palette.extract_ha_oiii(_emission())
    sep = palette.emission_separation(ha, oiii)
    assert sep is not None
    assert sep > palette.SEPARATION_THRESHOLD


def test_separation_none_for_empty_field():
    rng = np.random.default_rng(3)
    flat = rng.normal(0.1, 0.005, (3, 120, 90))  # noise only, no signal above bg+3sigma
    ha, oiii = palette.extract_ha_oiii(flat)
    assert palette.emission_separation(ha, oiii) is None


def test_cli_emission_emits_hoo_master(capsys):
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "master.fit")
        hdr = fits.Header({"OBJECT": "TEST", "CRVAL1": 84.67, "CRVAL2": -69.1,
                           "LIVETIME": 3600.0, "FILTER": "LP"})
        _write(src, _emission(), hdr)
        palette.main([src, "--outdir", d, "--basename", "TEST_final"])
        out = capsys.readouterr().out
        assert "PALETTES: EMIT" in out
        hoo_path = os.path.join(d, "TEST_final_HOO.fit")
        assert os.path.exists(hoo_path)
        with fits.open(hoo_path) as h:
            hoo, hoo_hdr = h[0].data, h[0].header
        # channel mapping: R carries the Ha blob, G and B carry the OIII blob
        assert hoo[0, 60, 40] > hoo[0, 140, 100]
        assert hoo[1, 140, 100] > hoo[1, 60, 40]
        assert np.array_equal(hoo[1], hoo[2])
        # sanity: float32 (FITS stores big-endian), finite, non-negative
        assert hoo.dtype.kind == "f" and hoo.dtype.itemsize == 4
        assert np.isfinite(hoo).all() and (hoo >= 0).all()
        # header + WCS preserved, HISTORY added
        assert hoo_hdr["OBJECT"] == "TEST" and abs(hoo_hdr["CRVAL1"] - 84.67) < 1e-9
        assert any("palette.py" in str(c) for c in hoo_hdr.get("HISTORY", []))


def test_cli_broadband_master_hard_skips(capsys):
    """An IRCUT master must be refused even with --force: emission still lands
    in R on broadband data, so the metric could fake a plausible EMIT."""
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "master.fit")
        _write(src, _emission(), fits.Header({"FILTER": "IRCUT"}))
        palette.main([src, "--outdir", d, "--basename", "T", "--force"])
        out = capsys.readouterr().out
        assert "PALETTES: SKIP (filter=IRCUT" in out
        assert not os.path.exists(os.path.join(d, "T_HOO.fit"))


def test_cli_continuum_skips_without_files(capsys):
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "master.fit")
        _write(src, _continuum())
        palette.main([src, "--outdir", d, "--basename", "T"])
        assert "PALETTES: SKIP" in capsys.readouterr().out
        assert not os.path.exists(os.path.join(d, "T_HOO.fit"))


def test_cli_force_writes_on_skip():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "master.fit")
        _write(src, _continuum())
        palette.main([src, "--outdir", d, "--basename", "T", "--force"])
        assert os.path.exists(os.path.join(d, "T_HOO.fit"))


def test_cli_metric_only_writes_nothing(capsys):
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "master.fit")
        _write(src, _emission())
        palette.main([src, "--outdir", d, "--basename", "T", "--metric-only"])
        assert "PALETTES: EMIT" in capsys.readouterr().out
        assert not os.path.exists(os.path.join(d, "T_HOO.fit"))


def test_cli_empty_field_skips_gracefully(capsys):
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "master.fit")
        rng = np.random.default_rng(3)
        _write(src, rng.normal(0.1, 0.005, (3, 120, 90)))
        palette.main([src, "--outdir", d, "--basename", "T"])
        out = capsys.readouterr().out
        assert "PALETTES: SKIP" in out and "separation=n/a" in out
        assert not os.path.exists(os.path.join(d, "T_HOO.fit"))
