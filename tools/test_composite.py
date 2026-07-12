"""Tests for composite.py: WCS alignment, continuum fit, HaRGB composition."""
import os
import tempfile

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import pixel_to_pixel

import composite

H, W = 60, 80


def _wcs_header(crpix=(40.0, 30.0), filt=None):
    """Minimal plate-solved header: TAN projection, ~3.6"/px."""
    h = fits.Header()
    h["CTYPE1"], h["CTYPE2"] = "RA---TAN", "DEC--TAN"
    h["CRPIX1"], h["CRPIX2"] = crpix
    h["CRVAL1"], h["CRVAL2"] = 84.0, -69.0
    h["CDELT1"], h["CDELT2"] = -0.001, 0.001
    h["CUNIT1"] = h["CUNIT2"] = "deg"
    if filt:
        h["FILTER"] = filt
    return h


def _star(y, x, amp=5.0, sigma=2.0):
    yy, xx = np.mgrid[0:H, 0:W]
    return amp * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2 * sigma**2))


def _flat(seed=0):
    return np.random.default_rng(seed).normal(0.1, 0.005, (3, H, W))


def _write(path, cube, header):
    fits.writeto(path, cube.astype(np.float32), header, overwrite=True)


def test_align_moves_star_to_lp_grid():
    with tempfile.TemporaryDirectory() as d:
        lp_hdr = _wcs_header(crpix=(40.0, 30.0), filt="LP")
        ir_hdr = _wcs_header(crpix=(45.0, 33.0), filt="IRCUT")
        ir = _flat(1)
        ir[0] += _star(20, 50)
        lp_path, ir_path = os.path.join(d, "lp.fit"), os.path.join(d, "ir.fit")
        _write(lp_path, _flat(0), lp_hdr)
        _write(ir_path, ir, ir_hdr)
        composite.main([lp_path, ir_path, "--outdir", d, "--basename", "T"])
        with fits.open(os.path.join(d, "T_IRCUT_aligned.fit")) as h:
            aligned, hdr = h[0].data, h[0].header
        # where should the star land on the LP grid? ask the WCS itself
        ex, ey = pixel_to_pixel(WCS(ir_hdr), WCS(lp_hdr), 50.0, 20.0)
        y, x = np.unravel_index(np.argmax(aligned[0]), aligned[0].shape)
        assert abs(x - ex) <= 1 and abs(y - ey) <= 1
        # output carries the LP grid's WCS but the IRCUT filter tag
        assert hdr["CRPIX1"] == 40.0 and hdr["FILTER"] == "IRCUT"
        assert any("composite.py" in str(c) for c in hdr.get("HISTORY", []))
        # off-coverage corner (LP grid extends past the shifted IRCUT frame) is 0
        assert aligned[0, -1, -1] == 0.0


def test_align_prints_coverage(capsys):
    with tempfile.TemporaryDirectory() as d:
        lp_path, ir_path = os.path.join(d, "lp.fit"), os.path.join(d, "ir.fit")
        _write(lp_path, _flat(0), _wcs_header(filt="LP"))
        _write(ir_path, _flat(1), _wcs_header(crpix=(48.0, 30.0), filt="IRCUT"))
        composite.main([lp_path, ir_path, "--outdir", d, "--basename", "T"])
        out = capsys.readouterr().out
        assert "COMPOSITE: ALIGN (coverage=" in out
        # 8 px horizontal shift of a 80 px frame -> ~90% coverage
        cov = float(out.split("coverage=")[1].split("%")[0])
        assert 85.0 < cov < 95.0


def test_missing_wcs_errors():
    with tempfile.TemporaryDirectory() as d:
        lp_path, ir_path = os.path.join(d, "lp.fit"), os.path.join(d, "ir.fit")
        hdr = fits.Header({"FILTER": "LP"})  # no WCS
        _write(lp_path, _flat(0), hdr)
        _write(ir_path, _flat(1), _wcs_header(filt="IRCUT"))
        with pytest.raises(SystemExit):
            composite.main([lp_path, ir_path, "--outdir", d])


def test_sip_3axis_header_accepted():
    """Siril writes SIP distortion keywords on RGB cubes (NAXIS=3); WCSLIB
    rejects SIP+3D unless reduced to the two celestial axes."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "lp.fit")
        hdr = _wcs_header(filt="LP")
        hdr["CTYPE1"], hdr["CTYPE2"] = "RA---TAN-SIP", "DEC--TAN-SIP"
        hdr["A_ORDER"] = hdr["B_ORDER"] = 2
        hdr["A_2_0"] = hdr["B_0_2"] = 1e-7
        _write(path, _flat(0), hdr)  # 3-plane cube -> NAXIS=3 in the header
        read_back = fits.getheader(path)
        w = composite.celestial_wcs(read_back, path)
        assert w.has_celestial and w.naxis == 2


def test_swapped_arguments_error():
    with tempfile.TemporaryDirectory() as d:
        lp_path, ir_path = os.path.join(d, "lp.fit"), os.path.join(d, "ir.fit")
        _write(lp_path, _flat(0), _wcs_header(filt="LP"))
        _write(ir_path, _flat(1), _wcs_header(filt="IRCUT"))
        with pytest.raises(SystemExit):
            composite.main([ir_path, lp_path, "--outdir", d])  # IRCUT passed first


def test_mono_master_rejected():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "mono.fit")
        fits.writeto(p, np.zeros((H, W), np.float32), _wcs_header(), overwrite=True)
        with pytest.raises(SystemExit):
            composite.load_rgb_master(p)


def _emission_pair():
    """Same WCS; LP_R = 0.7*IRCUT_R + Ha blob disjoint from the stars."""
    ir = _flat(2)
    for y, x in ((15, 20), (40, 60), (50, 15)):
        ir[0] += _star(y, x)
    lp = _flat(3)
    lp[0] = 0.1 + 0.7 * (ir[0] - 0.1) + _star(30, 40, amp=1.0, sigma=6.0)
    return lp, ir


def test_estimate_k_recovers_scale():
    lp, ir = _emission_pair()
    assert abs(composite.estimate_k(lp[0], ir[0]) - 0.7) < 0.05


def test_hargb_subtracts_stars_keeps_ha(capsys):
    with tempfile.TemporaryDirectory() as d:
        lp, ir = _emission_pair()
        lp_path, ir_path = os.path.join(d, "lp.fit"), os.path.join(d, "ir.fit")
        _write(lp_path, lp, _wcs_header(filt="LP"))
        _write(ir_path, ir, _wcs_header(filt="IRCUT"))
        composite.main([lp_path, ir_path, "--mode", "hargb",
                        "--outdir", d, "--basename", "T"])
        out = capsys.readouterr().out
        assert "COMPOSITE: HARGB (k=" in out
        ha = fits.getdata(os.path.join(d, "T_Ha.fit"))
        # the Ha blob survives, the continuum stars are cancelled
        assert ha[30, 40] > 0.5
        assert ha[15, 20] < 0.2 * ha[30, 40]
        assert ha[40, 60] < 0.2 * ha[30, 40]
        hargb = fits.getdata(os.path.join(d, "T_HaRGB.fit"))
        assert hargb.shape == (3, H, W)
        # R gains the Ha blob on top of broadband; G/B stay broadband
        assert hargb[0, 30, 40] > hargb[1, 30, 40] + 0.5
        assert os.path.exists(os.path.join(d, "T_IRCUT_aligned.fit"))
        # sanity: float32, finite, non-negative
        assert hargb.dtype.kind == "f" and hargb.dtype.itemsize == 4
        assert np.isfinite(hargb).all() and (hargb >= 0).all()
