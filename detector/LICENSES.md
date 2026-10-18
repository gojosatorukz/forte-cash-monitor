# Licenses

Only Apache-2.0 / MIT / BSD code and weights are allowed (see docs/plan_phase1.md, Global Constraints).

## Models

| model | used for | license | URL |
|---|---|---|---|
| RT-DETRv2-S `PekingU/rtdetr_v2_r18vd` (COCO) | person + COCO object detection (Task 2, default) | Apache-2.0 | https://huggingface.co/PekingU/rtdetr_v2_r18vd |
| D-FINE-S `ustc-community/dfine-small-coco` (COCO) | compared only (Task 2), not used by default | Apache-2.0 | https://huggingface.co/ustc-community/dfine-small-coco |

Detector choice (Task 2): RT-DETRv2-S over D-FINE-S — similar person recall on our frames, 2x faster
(16 vs 34 ms/frame fp16 batch 4 on RTX 3060), peak VRAM ~240 MB. Override with env `FORTE_DET_MODEL`
(e.g. `PekingU/rtdetr_v2_r50vd`, Apache-2.0, on the 16 GB prod GPU).

## Python dependencies

| package | license | URL |
|---|---|---|
| numpy | BSD-3-Clause | https://github.com/numpy/numpy |
| opencv-python-headless | Apache-2.0 (OpenCV); MIT (wheel packaging) | https://github.com/opencv/opencv-python |
| av (PyAV) | BSD-3-Clause; the PyAV 19 wheel bundles FFmpeg **and libx264/libx265 (GPL-2.0+)** in `av.libs` — see note below | https://github.com/PyAV-Org/PyAV |
| pillow | MIT-CMU (HPND) | https://github.com/python-pillow/Pillow |
| openpyxl | MIT | https://foss.heptapod.net/openpyxl/openpyxl |
| pytest (dev) | MIT | https://github.com/pytest-dev/pytest |
| torch / torchvision (cu128 wheels) | BSD-3-Clause | https://github.com/pytorch/pytorch |
| transformers | Apache-2.0 | https://github.com/huggingface/transformers |
| supervision 0.26.1 (ByteTrack; pinned: last release with `sv.ByteTrack`; does not depend on ultralytics) | MIT | https://github.com/roboflow/supervision |
| scipy (supervision dep) | BSD-3-Clause | https://github.com/scipy/scipy |

### Notable transitive dependencies

Read from the installed package metadata in `.venv` (versions as locked in `uv.lock`). Everything
not listed here is MIT / BSD / Apache-2.0 / ISC / PSF. None of these is a model and none is
modified or redistributed by us; they are installed by `uv sync`.

| package | version | license | note |
|---|---|---|---|
| certifi (via requests / httpx) | 2026.7.22 | MPL-2.0 | CA bundle; file-level copyleft, unmodified use is fine |
| tqdm (via transformers / huggingface-hub) | 4.70.1 | MPL-2.0 AND MIT | progress bars only |
| NVIDIA CUDA wheels (via torch cu128): `nvidia-cublas/cudnn/cufft/curand/cusolver/cusparse/cusparselt/nccl/nvjitlink/nvshmem/cuda-runtime/cuda-nvrtc/cuda-cupti/cufile-cu12`, `cuda-toolkit` | 12.8.x / cuDNN 9.19 | NVIDIA proprietary (CUDA Toolkit / cuDNN EULA; redistributable runtime libraries) | runtime libraries for the GPU; not open source, permitted as unmodified runtime components. `nvidia-nvtx-cu12` is Apache-2.0, `cuda-bindings` / `cuda-pathfinder` Apache-2.0 |
| triton (via torch) | 3.6.0 | MIT | |
| huggingface-hub | 2.2.0 | Apache-2.0 | weights download (only `scripts/fetch_models.py`; analysis runs offline) |
| hf-xet (via huggingface-hub) | 1.7.0 | Apache-2.0 | |
| tokenizers | 0.23.3 | Apache-2.0 | OWLv2 text queries |
| safetensors | 0.8.0 | Apache-2.0 | weight files |
| regex (via transformers) | 2026.9.29 | Apache-2.0 AND CNRI-Python | |
| matplotlib (via supervision) | 3.11.2 | matplotlib license (PSF-style, BSD-compatible) | not used at runtime by our code |
| defusedxml (via supervision) | 0.7.1 | PSF-2.0 | |
| typing_extensions | 4.16.0 | PSF-2.0 | |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | bundled components |

## Task 3 (state probes) models

| model | use | license | URL |
|---|---|---|---|
| OWLv2 base patch16 ensemble (`google/owlv2-base-patch16-ensemble`) | open-vocab calibration (safe, laptop, counting machine, desk, door) | Apache-2.0 | https://huggingface.co/google/owlv2-base-patch16-ensemble |
| Grounding-DINO tiny (`IDEA-Research/grounding-dino-tiny`) | optional alternative calibration model (`FORTE_OV_MODEL`), not default | Apache-2.0 | https://huggingface.co/IDEA-Research/grounding-dino-tiny |
| Qwen3-VL-4B-Instruct GGUF Q4_K_M + mmproj F16 (`Qwen/Qwen3-VL-4B-Instruct-GGUF`) | VLM yes/no / count probes via local llama-server | Apache-2.0 | https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF |
| llama.cpp `llama-server` (b11707, prebuilt CUDA 12.8) | local OpenAI-compatible VLM server | MIT | https://github.com/ggml-org/llama.cpp |

### PyAV / FFmpeg licensing note

- The PyAV 19 binary wheel bundles FFmpeg together with libx264 and libx265 (GPL-2.0+) in `av.libs`; it is
  not an LGPL-only build.
- The case requires licences that permit commercial use. GPL-2.0+ permits commercial use; its copyleft
  obligations attach to distributing the binaries.
- We run a hosted service and only DECODE video, with libavcodec's native H.264/HEVC decoders (LGPL);
  the GPL encoders (x264/x265) are never invoked.
- Post-hackathon: build PyAV against an LGPL-only FFmpeg (no `--enable-gpl`, no x264/x265) before any
  binary distribution.
