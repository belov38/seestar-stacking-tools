#!/usr/bin/env bash
# Build the py3.13 venv used by gx_gpu.py (CoreML GPU runner for GraXpert models).
# Needs a Python >= 3.10 so onnxruntime >= 1.20 (CoreML ModelFormat/MLComputeUnits) is available.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-/opt/homebrew/bin/python3.13}"

"$PY" -m venv "$HERE/.venv"
"$HERE/.venv/bin/pip" install -q --upgrade pip
"$HERE/.venv/bin/pip" install -q \
  "onnxruntime>=1.20" onnx numpy astropy scikit-image opencv-python-headless packaging
echo "venv ready: $HERE/.venv"
"$HERE/.venv/bin/python" -c "import onnxruntime as o; print('onnxruntime', o.__version__, o.get_available_providers())"
