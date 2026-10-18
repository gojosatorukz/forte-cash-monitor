"""One-shot model download for deploy (the analyzer itself never downloads: it runs offline).

    uv run python scripts/fetch_models.py            # default detector + open-vocab model
    uv run python scripts/fetch_models.py --also PekingU/rtdetr_v2_r50vd

Fetches into the normal Hugging Face cache (HF_HOME / ~/.cache/huggingface) the checkpoints named by
FORTE_DET_MODEL (default RT-DETRv2-S) and FORTE_OV_MODEL (default OWLv2-base), plus any `--also`
ids. Loads each on CPU once to make sure every file the analyzer needs is cached, then checks the
offline load. The VLM GGUF for llama-server is not handled here (see docs/spike_vlm.md).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

os.environ["FORTE_ALLOW_DOWNLOAD"] = "1"  # before importing forte_detector (which defaults to offline)
for _k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
    os.environ.pop(_k, None)

from forte_detector import config  # noqa: E402
from forte_detector.probes import openvocab  # noqa: E402

OFFLINE_CHECK = """
import sys
from transformers import AutoConfig, AutoModelForObjectDetection, AutoModelForZeroShotObjectDetection, AutoProcessor
det, ov = sys.argv[1:3]
AutoConfig.from_pretrained(det, local_files_only=True)
AutoModelForObjectDetection.from_pretrained(det, local_files_only=True)
AutoProcessor.from_pretrained(ov, local_files_only=True)
AutoModelForZeroShotObjectDetection.from_pretrained(ov, local_files_only=True)
print("offline load ok:", det, ov)
"""


def fetch(model_id: str, kind: str) -> None:
    from transformers import (AutoConfig, AutoModelForObjectDetection, AutoModelForZeroShotObjectDetection,
                              AutoProcessor)

    print(f"fetching {kind}: {model_id}", flush=True)
    if kind == "detector":
        AutoConfig.from_pretrained(model_id)
        m = AutoModelForObjectDetection.from_pretrained(model_id)
    else:
        AutoProcessor.from_pretrained(model_id)
        m = AutoModelForZeroShotObjectDetection.from_pretrained(model_id)
    del m


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--also", nargs="*", default=[], help="extra detector checkpoints (e.g. the prod RT-DETR M)")
    ap.add_argument("--no-check", action="store_true", help="skip the offline load check")
    a = ap.parse_args()
    det, ov = config.det_model(), openvocab.ov_model()
    fetch(det, "detector")
    fetch(ov, "openvocab")
    for m in a.also:
        fetch(m, "openvocab" if any(k in m.lower() for k in ("owl", "grounding")) else "detector")
    if a.no_check:
        return 0
    env = {k: v for k, v in os.environ.items() if k != "FORTE_ALLOW_DOWNLOAD"}
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    return subprocess.run([sys.executable, "-c", OFFLINE_CHECK, det, ov], env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
