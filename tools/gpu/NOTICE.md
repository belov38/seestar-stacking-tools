# Attribution & licensing — tools/gpu

This directory provides an Apple-Silicon (CoreML GPU) runner for two GraXpert AI models.
It builds on third-party work with separate licenses.

## Models (CC BY-NC-SA 4.0)

The denoise and background-extraction ONNX models are **© GraXpert Development Team**,
licensed under **Creative Commons Attribution-NonCommercial-ShareAlike 4.0**
(https://creativecommons.org/licenses/by-nc-sa/4.0/).

- Source: GraXpert (https://github.com/Steffenhir/GraXpert),
  `licenses/Denoise-Model-LICENSE.html`, `licenses/BGE-Model-LICENSE.html`.
- **Modification:** the only change is freezing the ONNX input batch dimension to a static
  shape `[1,256,256,3]` so Apple's CoreML compiler can run them on the GPU. Weights are
  unchanged.
- **NonCommercial:** these models, and the frozen copies mirrored for download, are for
  non-commercial use only.
- **ShareAlike:** the frozen models are redistributed under the same CC BY-NC-SA 4.0 license.

## Code (GPL-3.0)

`gx_gpu.py` reproduces the tiling/normalization logic from GraXpert's GPL-3.0 source
(`graxpert/denoising.py`, `graxpert/background_extraction.py`). It is therefore a derivative
work licensed under **GPL-3.0**, © GraXpert Development Team and contributors.
