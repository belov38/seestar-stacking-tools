"""Tests for rcastro.py: license probe parsing and run wrappers (CLI fully mocked)."""
import json
import subprocess

import pytest

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
