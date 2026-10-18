"""I4 (offline by default) and M1 (open-vocab device override + OOM fallback). No models, no GPU."""
from __future__ import annotations

import os
import subprocess
import sys
import types

import numpy as np
import pytest

from forte_detector import config
from forte_detector.probes import openvocab


def _py(code: str, **env) -> str:
    e = {k: v for k, v in os.environ.items()
         if k not in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "FORTE_ALLOW_DOWNLOAD")}
    e.update(env)
    return subprocess.run([sys.executable, "-c", code], env=e, capture_output=True, text=True, check=True,
                          timeout=120).stdout.strip()


def test_package_import_sets_offline_env_before_hub():
    out = _py("import forte_detector.service_adapter, os; import huggingface_hub.constants as c; "
              "print(os.environ.get('HF_HUB_OFFLINE'), os.environ.get('TRANSFORMERS_OFFLINE'), c.HF_HUB_OFFLINE)")
    assert out == "1 1 True"


def test_allow_download_leaves_env_alone():
    out = _py("import forte_detector, os; print(os.environ.get('HF_HUB_OFFLINE'))", FORTE_ALLOW_DOWNLOAD="1")
    assert out == "None"


def test_explicit_env_wins():
    out = _py("import forte_detector, os; print(os.environ.get('HF_HUB_OFFLINE'))", HF_HUB_OFFLINE="0")
    assert out == "0"


def test_hf_kwargs(monkeypatch):
    monkeypatch.delenv("FORTE_ALLOW_DOWNLOAD", raising=False)
    assert config.hf_kwargs() == {"local_files_only": True}
    monkeypatch.setenv("FORTE_ALLOW_DOWNLOAD", "1")
    assert config.hf_kwargs() == {}


# ----------------------------------------------------------------------------- M1


@pytest.mark.parametrize("env,free_gb,want", [("cpu", 5, "cpu"), ("cuda", 0, "cuda"), ("auto", 5, "cuda"),
                                              ("auto", 1, "cpu"), (None, 5, "cuda"), ("", 1, "cpu")])
def test_ov_device_override(monkeypatch, env, free_gb, want):
    import torch

    if env is None:
        monkeypatch.delenv("FORTE_OV_DEVICE", raising=False)
    else:
        monkeypatch.setenv("FORTE_OV_DEVICE", env)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda *a: (int(free_gb * 2**30), 6 * 2**30))
    assert openvocab.ov_device() == want


class OOM(RuntimeError):
    pass


def test_detect_cuda_oom_falls_back_to_cpu(monkeypatch):
    det = openvocab.OpenVocabDetector.__new__(openvocab.OpenVocabDetector)
    torch = types.SimpleNamespace(OutOfMemoryError=OOM, cuda=types.SimpleNamespace(empty_cache=lambda: None))
    det._torch, det.device, det.kind = torch, "cuda", "owlv2"
    moves = []
    det.model = types.SimpleNamespace(to=lambda d: moves.append(d))
    calls = []

    def run(image, queries):
        calls.append(det.device)
        if det.device == "cuda":
            raise OOM("CUDA out of memory")
        return {"safe": (0.5, [0, 0, 1, 1])}

    det._detect = run
    out = det.detect(np.zeros((8, 8, 3), np.uint8), {"safe": ["a safe"]})
    assert out == {"safe": (0.5, [0, 0, 1, 1])} and calls == ["cuda", "cpu"] and moves == ["cpu"]
    assert det.device == "cpu"
