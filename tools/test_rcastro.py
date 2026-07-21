"""Tests for rcastro.py: license probe parsing and run wrappers (CLI fully mocked)."""
import json
import subprocess

import numpy as np
from astropy.io import fits

import rcastro

LICENSED = json.dumps({
    "schemaVersion": 4, "cliVersion": "1.1.0",
    "licenseStatus": {"valid": True, "message": "Permanently licensed through ML4.",
                      "email": "user@example.com"},
})
UNLICENSED = json.dumps({
    "schemaVersion": 4, "cliVersion": "1.1.0",
    "licenseStatus": {"valid": False, "message": "Not activated."},
})


def _mock_run(responses):
    """responses: product -> (returncode, stdout). Returns a subprocess.run stand-in."""
    def run(cmd, **kwargs):
        product = next(p for p in rcastro.PRODUCTS if p in cmd)
        rc, out = responses[product]
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
    return run


def test_probe_all_licensed(monkeypatch):
    monkeypatch.setattr(rcastro, "find_cli", lambda: "/usr/local/bin/rc-astro")
    monkeypatch.setattr(rcastro.subprocess, "run",
                        _mock_run({p: (0, LICENSED) for p in rcastro.PRODUCTS}))
    assert rcastro.probe_line() == "RCASTRO: cli=1.1.0 bxt=ok sxt=ok nxt=ok"


def test_probe_one_unlicensed(monkeypatch):
    monkeypatch.setattr(rcastro, "find_cli", lambda: "/usr/local/bin/rc-astro")
    responses = {p: (0, LICENSED) for p in rcastro.PRODUCTS}
    responses["nxt"] = (0, UNLICENSED)
    monkeypatch.setattr(rcastro.subprocess, "run", _mock_run(responses))
    assert rcastro.probe_line() == "RCASTRO: cli=1.1.0 bxt=ok sxt=ok nxt=no"


def test_probe_cli_absent(monkeypatch):
    monkeypatch.setattr(rcastro, "find_cli", lambda: None)
    assert rcastro.probe_line() == "RCASTRO: absent"


def test_probe_malformed_json_is_no(monkeypatch):
    monkeypatch.setattr(rcastro, "find_cli", lambda: "/usr/local/bin/rc-astro")
    responses = {p: (0, LICENSED) for p in rcastro.PRODUCTS}
    responses["sxt"] = (0, "not json at all")
    monkeypatch.setattr(rcastro.subprocess, "run", _mock_run(responses))
    assert rcastro.probe_line() == "RCASTRO: cli=1.1.0 bxt=ok sxt=no nxt=ok"


def test_probe_product_error_is_no(monkeypatch):
    monkeypatch.setattr(rcastro, "find_cli", lambda: "/usr/local/bin/rc-astro")
    responses = {p: (0, LICENSED) for p in rcastro.PRODUCTS}
    responses["bxt"] = (1, "")
    monkeypatch.setattr(rcastro.subprocess, "run", _mock_run(responses))
    assert rcastro.probe_line() == "RCASTRO: cli=1.1.0 bxt=no sxt=ok nxt=ok"


def test_probe_timeout_is_no(monkeypatch):
    monkeypatch.setattr(rcastro, "find_cli", lambda: "/usr/local/bin/rc-astro")
    responses = {p: (0, LICENSED) for p in rcastro.PRODUCTS}

    def run(cmd, **kwargs):
        product = next(p for p in rcastro.PRODUCTS if p in cmd)
        if product == "sxt":
            raise subprocess.TimeoutExpired(cmd="rc-astro", timeout=30)
        rc, out = responses[product]
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")

    monkeypatch.setattr(rcastro.subprocess, "run", run)
    assert rcastro.probe_line() == "RCASTRO: cli=1.1.0 bxt=ok sxt=no nxt=ok"


def test_probe_oserror_is_no(monkeypatch):
    monkeypatch.setattr(rcastro, "find_cli", lambda: "/usr/local/bin/rc-astro")
    responses = {p: (0, LICENSED) for p in rcastro.PRODUCTS}

    def run(cmd, **kwargs):
        product = next(p for p in rcastro.PRODUCTS if p in cmd)
        if product == "nxt":
            raise OSError("binary vanished")
        rc, out = responses[product]
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")

    monkeypatch.setattr(rcastro.subprocess, "run", run)
    assert rcastro.probe_line() == "RCASTRO: cli=1.1.0 bxt=ok sxt=ok nxt=no"


def _write_fits(path, data, roworder):
    hdu = fits.PrimaryHDU(data.astype(np.float32))
    hdu.header["ROWORDER"] = roworder
    hdu.writeto(path, overwrite=True)


def test_run_product_success(monkeypatch, tmp_path):
    inp = tmp_path / "in.fit"
    out = tmp_path / "out.fit"
    data = np.arange(60, dtype=np.float32).reshape(3, 4, 5)
    _write_fits(inp, data, "BOTTOM-UP")

    def fake_run(cmd, **kwargs):
        assert cmd[:2] == ["/usr/local/bin/rc-astro", "--no-banner"]
        assert "--json" in cmd and "sxt" in cmd
        assert ["-o", str(out)] == cmd[cmd.index("-o"):cmd.index("-o") + 2]
        assert "--overwrite" in cmd and "--stars" in cmd
        _write_fits(out, data, "BOTTOM-UP")
        events = '{"event":"status","phase":"complete","message":"Done"}\n'
        return subprocess.CompletedProcess(cmd, 0, stdout=events, stderr="")

    monkeypatch.setattr(rcastro, "find_cli", lambda: "/usr/local/bin/rc-astro")
    monkeypatch.setattr(rcastro.subprocess, "run", fake_run)
    rc = rcastro.run_product("sxt", str(inp), str(out), ["--stars"])
    assert rc == 0
    assert np.array_equal(fits.getdata(out), data)


def test_run_product_normalizes_roworder(monkeypatch, tmp_path):
    inp = tmp_path / "in.fit"
    out = tmp_path / "out.fit"
    sidecar = tmp_path / "out-stars.fit"
    data = np.arange(60, dtype=np.float32).reshape(3, 4, 5)
    _write_fits(inp, data, "BOTTOM-UP")

    def fake_run(cmd, **kwargs):
        # rc-astro writes TOP-DOWN: rows flipped relative to the input
        flipped = data[..., ::-1, :]
        _write_fits(out, flipped, "TOP-DOWN")
        _write_fits(sidecar, flipped, "TOP-DOWN")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(rcastro, "find_cli", lambda: "/usr/local/bin/rc-astro")
    monkeypatch.setattr(rcastro.subprocess, "run", fake_run)
    assert rcastro.run_product("sxt", str(inp), str(out), ["--stars"]) == 0
    for path in (out, sidecar):
        assert fits.getheader(path)["ROWORDER"] == "BOTTOM-UP"
        assert np.array_equal(fits.getdata(path), data)


def test_run_product_missing_output_fails(monkeypatch, tmp_path):
    out = tmp_path / "never_written.fit"

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(rcastro, "find_cli", lambda: "/usr/local/bin/rc-astro")
    monkeypatch.setattr(rcastro.subprocess, "run", fake_run)
    assert rcastro.run_product("bxt", "in.fit", str(out), []) != 0


def test_run_product_cli_absent_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(rcastro, "find_cli", lambda: None)
    assert rcastro.run_product("nxt", "in.fit", str(tmp_path / "o.fit"), []) != 0


def test_run_product_nonzero_exit_fails(monkeypatch, tmp_path):
    out = tmp_path / "out.fit"

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 1, stdout='{"event":"error","message":"boom"}\n', stderr="")

    monkeypatch.setattr(rcastro, "find_cli", lambda: "/usr/local/bin/rc-astro")
    monkeypatch.setattr(rcastro.subprocess, "run", fake_run)
    assert rcastro.run_product("bxt", "in.fit", str(out), []) != 0
