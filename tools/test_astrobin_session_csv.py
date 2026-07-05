"""Tests for astrobin_session_csv: session grouping and AstroBin column output."""
import csv
import io
import os
import tempfile

import numpy as np
from astropy.io import fits

import astrobin_session_csv as mod


def _light(path, date_obs, exptime=30.0, gain=200, binning=1, ccdtemp=25.0):
    hdr = fits.Header()
    hdr["DATE-OBS"] = date_obs
    hdr["EXPTIME"] = exptime
    hdr["GAIN"] = gain
    hdr["XBINNING"] = binning
    hdr["CCD-TEMP"] = ccdtemp
    fits.writeto(path, np.zeros((2, 2), np.float32), hdr, overwrite=True)


def _rows(out_csv):
    return list(csv.DictReader(io.StringIO(out_csv)))


def test_night_crossing_midnight_is_one_session():
    """Local times spanning local midnight collapse to a single night via -12h shift."""
    with tempfile.TemporaryDirectory() as d:
        lights = os.path.join(d, "lights"); os.makedirs(lights)
        # Seestar local filenames: 20:54 .. 03:19 next day = one night (2026-06-17)
        for stamp in ["20260617-205453", "20260617-235959", "20260618-031927"]:
            _light(os.path.join(lights, f"Light_C 103_30.0s_LP_{stamp}.fit"),
                   "2026-06-17T08:54:13")  # UTC, deliberately a different date
        sessions, total, skipped = mod.collect_sessions(lights, 12.0, 0.0)
        assert total == 3 and skipped == 0
        assert set(sessions) == {("2026-06-17", "LP")}
        assert sessions[("2026-06-17", "LP")]["n"] == 3


def test_two_distinct_nights():
    with tempfile.TemporaryDirectory() as d:
        lights = os.path.join(d, "lights"); os.makedirs(lights)
        _light(os.path.join(lights, "Light_X_30.0s_LP_20260617-210000.fit"), "x")
        _light(os.path.join(lights, "Light_X_30.0s_LP_20260618-210000.fit"), "x")
        sessions, _, _ = mod.collect_sessions(lights, 12.0, 0.0)
        assert set(sessions) == {("2026-06-17", "LP"), ("2026-06-18", "LP")}


def test_dateobs_fallback_with_offset():
    """No Seestar timestamp in the name -> use DATE-OBS (UTC) + offset."""
    with tempfile.TemporaryDirectory() as d:
        lights = os.path.join(d, "lights"); os.makedirs(lights)
        _light(os.path.join(lights, "stack_001.fit"), "2026-06-17T08:54:00")
        # +12h local -> 20:54 local -> night-shift -12h -> 2026-06-17
        sessions, _, skipped = mod.collect_sessions(lights, 12.0, 12.0)
        assert skipped == 0 and set(sessions) == {("2026-06-17", "")}


def test_csv_columns_and_values(capsys):
    with tempfile.TemporaryDirectory() as d:
        lights = os.path.join(d, "lights"); os.makedirs(lights)
        for stamp in ["20260617-210000", "20260617-210031"]:
            _light(os.path.join(lights, f"Light_X_30.0s_LP_{stamp}.fit"), "x")
        mod.main([lights])
        out = capsys.readouterr().out
        rows = _rows(out)
        assert list(rows[0].keys()) == mod.COLUMNS
        assert len(rows) == 1
        r = rows[0]
        assert r["date"] == "2026-06-17"
        assert r["number"] == "2"
        assert r["duration"] == "30"   # no trailing .0
        assert r["binning"] == "1"
        assert r["gain"] == "200"
        assert r["filter"] == "40954"  # auto-detected from the _LP_ token
        assert r["darks"] == "" and r["flats"] == "" and r["bias"] == ""


def test_mixed_filters_one_row_per_filter():
    """A night shot in both filters must split into one honest row each."""
    with tempfile.TemporaryDirectory() as d:
        lights = os.path.join(d, "lights"); os.makedirs(lights)
        for stamp in ["20260627-210000", "20260627-210031"]:
            _light(os.path.join(lights, f"Light_X_30.0s_LP_{stamp}.fit"), "x")
        _light(os.path.join(lights, "Light_X_30.0s_IRCUT_20260627-220000.fit"), "x")
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod.main([lights])
        rows = _rows(buf.getvalue())
        assert len(rows) == 2
        by_filter = {r["filter"]: r for r in rows}
        assert by_filter["42307"]["number"] == "1"   # IRCUT
        assert by_filter["40954"]["number"] == "2"   # LP
        assert rows[0]["date"] == rows[1]["date"] == "2026-06-27"


def test_filter_id_zero_blanks_all():
    with tempfile.TemporaryDirectory() as d:
        lights = os.path.join(d, "lights"); os.makedirs(lights)
        _light(os.path.join(lights, "Light_X_30.0s_LP_20260617-210000.fit"), "x")
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod.main([lights, "--filter-id", "0"])
        assert _rows(buf.getvalue())[0]["filter"] == ""


def test_optional_flags():
    with tempfile.TemporaryDirectory() as d:
        lights = os.path.join(d, "lights"); os.makedirs(lights)
        _light(os.path.join(lights, "Light_X_30.0s_LP_20260617-210000.fit"), "x", ccdtemp=24.4)
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod.main([lights, "--filter-id", "4663", "--bortle", "5",
                      "--sqm", "20.5", "--fwhm", "2.3", "--sensor-temp"])
        r = _rows(buf.getvalue())[0]
        assert r["filter"] == "4663"
        assert r["bortle"] == "5"
        assert r["meanSqm"] == "20.5"
        assert r["meanFwhm"] == "2.3"
        assert r["sensorCooling"] == "24"  # rounded mean CCD-TEMP
