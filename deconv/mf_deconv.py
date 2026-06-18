#!/usr/bin/env python3
"""Headless runner for SASpro multi-frame Richardson-Lucy deconvolution (mfdeconv).

Vendored from setiastro/setiastrosuitepro (see NOTICE). The upstream code expects
its own GUI runtime; runtime shims make it run standalone (no source edits):
  0. PyQt6 stub      -> the GUI worker class never runs headless; a permissive stub
                        satisfies imports/class-bodies, unknown Qt attrs no-op.
  1. import_torch    -> return the torch installed in this venv (skip SASpro's
                        private-runtime auto-installer).
  2. _USE_PROCESS_POOL_FOR_ASSETS=False -> threads, not spawn-multiprocessing
                        (spawn re-imports this unguarded driver and breaks).
  3. _ensure_mask_list(None,...) bug -> upstream builds 0-dim masks from shape
                        probes; return [None]*N so _mask_for_run() makes full masks.

Early-stop is tunable from the CLI (no code edits needed to sweep).

Usage:
  mf_deconv.py OUT.fit "GLOB_OR_FILE" [iters] [options]

  "GLOB_OR_FILE"  one FITS, or a quoted glob of registered (r_pp_*) frames.
  iters           max iterations (default 20).
  --color luma|rgb     luma (default, channel-safe) or per-channel rgb.
  --noearly            disable early-stop entirely -> run the full `iters`.
  --early-frac F       stop when update drops below F*initial (upstream 0.40;
                       LOWER => runs longer before stopping). Tunes early-stop.
  --patience N         consecutive small-update iters before stopping (upstream 2).
  --min-iters N        never stop before this iter (upstream 3).
"""
import sys, os, glob, time, argparse, types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "vendor"))


# --- shim 0: permissive PyQt6 stub (self-healing; no source edits for Qt gaps) ---
def _install_qt_stub():
    if "PyQt6" in sys.modules:
        return

    class _QtDummy:
        """Usable as base class, callable, and attribute source; everything no-ops."""
        def __init__(self, *a, **k): pass
        def __call__(self, *a, **k): return None
        def __getattr__(self, n): return _QtDummy()

    class QObject:           # real base: subclassed by worker classes
        def __init__(self, *a, **k): pass
    class QThread(QObject):
        @staticmethod
        def currentThread(): return None
    def pyqtSignal(*a, **k): return None          # class-body descriptor; never emitted
    class QApplication:
        def __init__(self, *a, **k): pass
        @staticmethod
        def instance(): return None               # -> gui-event pump is a no-op

    def _module(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        def _getattr(n):                          # any other Qt symbol -> dummy,
            if n.startswith("__") and n.endswith("__"):  # but let dunders raise so
                raise AttributeError(n)           # import/inspect machinery works
            return _QtDummy()
        m.__getattr__ = _getattr
        return m

    qtcore = _module("PyQt6.QtCore", QObject=QObject, QThread=QThread, pyqtSignal=pyqtSignal)
    qtwidgets = _module("PyQt6.QtWidgets", QApplication=QApplication)
    qtgui = _module("PyQt6.QtGui")
    pkg = _module("PyQt6", QtCore=qtcore, QtWidgets=qtwidgets, QtGui=qtgui)
    sys.modules.update({
        "PyQt6": pkg, "PyQt6.QtCore": qtcore,
        "PyQt6.QtWidgets": qtwidgets, "PyQt6.QtGui": qtgui,
    })

_install_qt_stub()

# --- shim 1: redirect SASpro's torch loader to the venv torch ----------------
import setiastro.saspro.runtime_torch as rt
import torch as _torch
rt.import_torch = lambda *a, **k: _torch

from setiastro.saspro import mfdeconv

# --- shim 2: threads instead of spawn-multiprocessing ------------------------
mfdeconv._USE_PROCESS_POOL_FOR_ASSETS = False

# --- shim 3: fix 0-dim masks when masks is None ------------------------------
_orig_eml = mfdeconv._ensure_mask_list
mfdeconv._ensure_mask_list = (
    lambda masks, data: ([None] * len(data) if masks is None else _orig_eml(masks, data))
)


def _apply_earlystop_overrides(noearly, early_frac, patience, min_iters):
    """Tune EarlyStopper from the CLI without editing the vendored source."""
    if noearly:
        mfdeconv.EarlyStopper.step = lambda self, *x, **k: False
        return
    overrides = {}
    if early_frac is not None: overrides["early_frac"] = early_frac
    if patience is not None:   overrides["patience"] = patience
    if min_iters is not None:  overrides["min_iters"] = min_iters
    if not overrides:
        return
    _orig_init = mfdeconv.EarlyStopper.__init__
    def _init(self, *a, **k):
        k.update(overrides)          # construction site passes all-keyword args
        _orig_init(self, *a, **k)
    mfdeconv.EarlyStopper.__init__ = _init


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("frames", help="single FITS or quoted glob")
    ap.add_argument("iters", nargs="?", type=int, default=20)
    ap.add_argument("--color", default="luma", choices=["luma", "rgb"])
    ap.add_argument("--noearly", action="store_true",
                    help="disable early-stop -> run the full iters")
    ap.add_argument("--early-frac", type=float, default=None,
                    help="stop threshold as fraction of initial update (upstream 0.40; lower=longer)")
    ap.add_argument("--patience", type=int, default=None,
                    help="consecutive small-update iters before stop (upstream 2)")
    ap.add_argument("--min-iters", type=int, default=None,
                    help="never stop before this iter (upstream 3)")
    a = ap.parse_args()

    paths = sorted(glob.glob(a.frames)) if glob.has_magic(a.frames) else [a.frames]
    assert paths, f"no frames matched {a.frames!r}"

    _apply_earlystop_overrides(a.noearly, a.early_frac, a.patience, a.min_iters)

    def cb(s):
        s = str(s)
        if not s.startswith("__PROGRESS__"):
            print(s, flush=True)

    print(f"torch={_torch.__version__} mps={_torch.backends.mps.is_available()}", flush=True)
    print(f"FRAMES={len(paths)} iters={a.iters} color={a.color} "
          f"noearly={a.noearly} early_frac={a.early_frac} "
          f"patience={a.patience} min_iters={a.min_iters}", flush=True)
    t0 = time.time()
    saved = mfdeconv.multiframe_deconv_normal_rebuild(
        paths=paths,
        out_path=a.out,
        iters=a.iters,
        color_mode=a.color,
        seed_mode="robust",
        use_star_masks=False,
        use_variance_maps=False,
        rejection_strength=0.0,   # cross-frame rejection handled by robust seed; off for stability
        status_cb=cb,
    )
    print(f"\nELAPSED={time.time()-t0:.1f}s", flush=True)
    print(f"SAVED={saved} exists={os.path.exists(str(saved))}", flush=True)


if __name__ == "__main__":
    main()
