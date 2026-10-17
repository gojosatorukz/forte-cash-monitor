"""I3: the detector returns to CUDA after a CUDA OOM fallback (mocked torch, no GPU, no model)."""
from __future__ import annotations

import types

import numpy as np

from forte_detector.perception import detector as D
from forte_detector.perception import pipeline


class OOM(RuntimeError):
    pass


class FakeModel:
    def __init__(self, torch):
        self.torch, self.moves = torch, []

    def to(self, device=None, dtype=None):
        if device.startswith("cuda") and self.torch.oom_on_move:
            raise OOM("CUDA out of memory (move)")
        self.moves.append(device)
        self.device = device
        return self


def fake_torch(cuda=True):
    t = types.SimpleNamespace(float16="f16", float32="f32", OutOfMemoryError=OOM, oom_on_move=False, freed=0)
    t.cuda = types.SimpleNamespace(is_available=lambda: cuda, empty_cache=lambda: setattr(t, "freed", t.freed + 1))
    return t


def make_det(torch, device="cuda"):
    det = D.Detector.__new__(D.Detector)
    det._torch, det._model, det.batch_size = torch, FakeModel(torch), 4
    det.preferred_device = device
    det._to_device(device)
    det.oom_next = 0

    def forward(chunk):
        if det.oom_next and det.device.startswith("cuda"):
            det.oom_next -= 1
            raise OOM("CUDA out of memory (forward)")
        return np.zeros((len(chunk), 2, 3)), np.zeros((len(chunk), 2, 4))

    det._forward = forward
    det._decode = lambda sc, bx: {"persons": [], "objects": []}
    return det


def test_oom_falls_back_to_cpu_then_returns_to_cuda():
    torch = fake_torch()
    det = make_det(torch)
    det.oom_next = 1
    det.detect([np.zeros((8, 8, 3), np.uint8)])
    assert det.device == "cpu" and det.dtype == "f32" and torch.freed >= 1  # fallback, CUDA cache freed
    assert det.ensure_device() is True
    assert det.device == "cuda" and det.dtype == "f16" and det._model.moves[-1] == "cuda"


def test_retry_cuda_failing_stays_on_cpu():
    torch = fake_torch()
    det = make_det(torch)
    det.oom_next = 1
    det.detect([np.zeros((8, 8, 3), np.uint8)])
    torch.oom_on_move = True  # the VLM still holds the memory
    assert det.ensure_device() is False
    assert det.device == "cpu" and det._model.moves[-1] == "cpu"


def test_cpu_configured_never_moves_to_cuda():
    torch = fake_torch()
    det = make_det(torch, device="cpu")
    assert det.ensure_device() is False and det.device == "cpu"
    torch.cuda.is_available = lambda: False
    det2 = make_det(torch, device="cuda")
    det2._to_device("cpu")
    assert det2.ensure_device() is False and det2.device == "cpu"  # CUDA gone: no attempt


def test_perceive_start_calls_ensure_device(monkeypatch, tmp_path):
    from test_perception_smoke import FakeDetector, _cashier_walks_out, _write_clip

    calls = []

    class Det(FakeDetector):
        def ensure_device(self):
            calls.append(1)
            return False

    monkeypatch.setattr(pipeline, "get_detector", lambda cfg, device: Det())
    clip = tmp_path / "c.mp4"
    _write_clip(clip, 160, 120, 4, 2, _cashier_walks_out)
    pipeline.perceive(str(clip), use_cache=False)
    assert calls == [1]
