"""
Unit + smoke tests for measure_stacks.py.

Run from this directory with the venv that has astropy/numpy/pytest:
    pytest -q

Focus: the verdict-critical logic (rank, crop, mask/SNR, adopt threshold)
plus one end-to-end smoke test on synthetic FITS. The .ssf scripts are not
unit-tested (they are command lists for Siril, validated by real runs).
"""
import importlib.util
import os
import sys

import numpy as np
import pytest

# Load measure_stacks.py by path (dir name has hyphens/dots -> not importable).
_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "measure_stacks", os.path.join(_HERE, "measure_stacks.py"))
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)


# ---------------- rank() ----------------

def test_rank_higher_better():
    # values 10,30,20 -> best=30(idx1), then 20(idx2), then 10(idx0)
    assert ms.rank([10, 30, 20], higher_better=True) == [3.0, 1.0, 2.0]


def test_rank_lower_better():
    assert ms.rank([10, 30, 20], higher_better=False) == [1.0, 3.0, 2.0]


def test_rank_ties_get_averaged():
    # two 10s tie for last two slots -> averaged rank 2.5 each
    assert ms.rank([10, 10, 20], higher_better=True) == [2.5, 2.5, 1.0]


# ---------------- crop() ----------------

def test_crop_full_returns_same_object():
    a = np.zeros((100, 100))
    assert ms.crop(a, 1.0) is a


def test_crop_central_half():
    a = np.arange(100 * 100).reshape(100, 100).astype(float)
    c = ms.crop(a, 0.5)
    assert c.shape == (50, 50)
    # central slice: rows/cols 25..75
    assert np.array_equal(c, a[25:75, 25:75])


def test_crop_two_thirds():
    a = np.zeros((90, 120))
    c = ms.crop(a, 0.66)
    # int(90*0.66)=59, int(120*0.66)=79
    assert c.shape == (59, 79)


# ---------------- make_masks + measure ----------------

def _synthetic_image(noise_sigma, seed):
    """Background ~1000 with given noise, plus fixed bright & faint blocks.
    Blocks are CENTERED so they survive all crops (90% / 2/3 / 1/4)."""
    rng = np.random.default_rng(seed)
    img = rng.normal(1000.0, noise_sigma, (200, 200))
    # Small CENTERED blocks: present in every crop (90/2/3/1/4) yet a small
    # fraction so the background estimate stays clean.
    img[90:110, 80:95] = 1500.0    # bright: >> med+10sigma -> bright mask
    img[90:110, 105:120] = 1060.0  # faint: within med+[2,10]*sigma (sigma~10)
    return img


def test_measure_recovers_expected_snr():
    img = _synthetic_image(noise_sigma=10.0, seed=1)
    masks = ms.make_masks(img)
    out = ms.measure(img, masks)
    # background noise ~10 (robust)
    assert out["bg_sigma"] == pytest.approx(10.0, abs=2.0)
    # bright mask is clean (only the 1500 block) -> (1500-1000)/10 ~ 50
    assert out["bright_SNR"] == pytest.approx(50.0, abs=4.0)
    # faint mask is diluted by background noise crossing 2-sigma, so don't
    # assert an exact value -- just that it is a sane positive signal below bright
    assert 0 < out["faint_SNR"] < out["bright_SNR"]


def test_lower_noise_gives_higher_faint_snr():
    masks = ms.make_masks(_synthetic_image(10.0, seed=2))
    clean = ms.measure(_synthetic_image(6.0, seed=3), masks)
    noisy = ms.measure(_synthetic_image(15.0, seed=4), masks)
    assert clean["faint_SNR"] > noisy["faint_SNR"]


# ---------------- adopt_decision() ----------------

def test_adopt_above_threshold():
    decision, d = ms.adopt_decision(best_faint=4.40, base_faint=4.00)
    assert decision == "ADOPT"
    assert d == pytest.approx(10.0, abs=0.01)


def test_keep_below_threshold():
    decision, d = ms.adopt_decision(best_faint=4.04, base_faint=4.00)
    assert decision == "KEEP"
    assert d == pytest.approx(1.0, abs=0.01)


def test_adopt_exactly_at_threshold():
    # exactly +3.0% -> ADOPT (>=)
    decision, _ = ms.adopt_decision(base_faint=100.0, best_faint=103.0)
    assert decision == "ADOPT"


def test_adopt_just_under_threshold():
    decision, _ = ms.adopt_decision(base_faint=100.0, best_faint=102.99)
    assert decision == "KEEP"


def test_adopt_handles_zero_baseline():
    assert ms.adopt_decision(best_faint=5.0, base_faint=0.0) == ("KEEP", 0.0)


# ---------------- smoke: main() end-to-end ----------------

def _write_fits(path, noise_sigma, seed):
    from astropy.io import fits
    fits.writeto(path, _synthetic_image(noise_sigma, seed).astype(np.float32),
                 overwrite=True)


def test_main_smoke_picks_best_and_prints_verdict(tmp_path, capsys, monkeypatch):
    proc = tmp_path
    # baseline (noise 10), a clean winner (noise 6), a worse one (noise 15)
    _write_fits(proc / "result_v07_winsor_3_3_BASELINE.fit", 10.0, 11)
    _write_fits(proc / "result_v05_sigma_35_35.fit", 6.0, 12)
    _write_fits(proc / "result_v01_sigma_2_2.fit", 15.0, 13)

    monkeypatch.setattr(sys, "argv", ["measure_stacks.py", str(proc)])
    ms.main()
    out = capsys.readouterr().out

    # Smoke test = the pipeline runs end-to-end and emits a verdict + CSV.
    # (Exact best/ADOPT logic is covered by the rank() and adopt_decision tests.)
    assert "Found 3 results" in out
    assert "VERDICT" in out and "BASELINE" in out and "BEST =" in out
    assert "result file 'result_v" not in out  # no crash placeholder
    assert (proc / "metrics.csv").exists()
