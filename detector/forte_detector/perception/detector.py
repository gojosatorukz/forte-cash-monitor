"""Person + COCO-object detector (DETR family via HF transformers, Apache-2.0 weights).

Default: RT-DETRv2-S (`PekingU/rtdetr_v2_r18vd`). fp16 on CUDA, batched; falls back to CPU when
CUDA is unavailable or runs out of memory, and returns to CUDA at the next video (ensure_device).
Outputs normalized xyxy boxes.
"""
from __future__ import annotations

import logging
import threading

import cv2
import numpy as np

log = logging.getLogger(__name__)

DEFAULT_MODEL = "PekingU/rtdetr_v2_r18vd"
OBJECT_CLASSES = (
    "handbag", "backpack", "suitcase", "cell phone", "cup", "bottle", "laptop", "book", "keyboard",
)


def _pick_device(device: str | None) -> str:
    import torch

    if device in (None, "auto"):
        return "cuda" if torch.cuda.is_available() else "cpu"
    if device.startswith("cuda") and not torch.cuda.is_available():
        log.warning("CUDA requested but unavailable; using CPU")
        return "cpu"
    return device


class Detector:
    # Serialises forward passes and device moves on this (process-wide, shared) model: the CUDA-OOM
    # fallback and ensure_device() move the weights in place, which must never overlap another
    # thread's forward (device/dtype mismatch). See forte_detector/concurrency.py.
    _lock = threading.RLock()  # class default (objects built via __new__ in tests); __init__ sets one per instance

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL,
        device: str | None = None,
        input_size: int = 640,
        person_thr: float = 0.3,
        object_thr: float = 0.3,
        batch_size: int = 8,
    ):
        import torch
        from transformers import AutoConfig, AutoModelForObjectDetection

        self._lock = threading.RLock()
        self.model_id = model_id
        self.input_size = input_size
        self.person_thr = person_thr
        self.object_thr = object_thr
        self.batch_size = batch_size
        self.device = _pick_device(device)
        self.preferred_device = self.device  # configured / auto choice; ensure_device() returns to it
        from forte_detector import config as fconfig

        cfg = AutoConfig.from_pretrained(model_id, **fconfig.hf_kwargs())
        names = {int(k): v for k, v in cfg.id2label.items()}
        self.person_id = next(k for k, v in names.items() if v == "person")
        self.object_ids = {k: v for k, v in names.items() if v in OBJECT_CLASSES}
        self._model = AutoModelForObjectDetection.from_pretrained(model_id, **fconfig.hf_kwargs()).eval()
        self._torch = torch
        try:
            self._to_device(self.device)
        except (torch.OutOfMemoryError, RuntimeError) as exc:  # CUDA OOM / busy device
            if self.device == "cpu":
                raise
            log.warning("cannot place detector on %s (%s); using CPU", self.device, exc)
            self._to_device("cpu")
            torch.cuda.empty_cache()

    def _to_device(self, device: str) -> None:
        torch = self._torch
        dtype = torch.float16 if device.startswith("cuda") else torch.float32
        self._model.to(device=device, dtype=dtype)  # in place: the old copy's storage is released
        self.device, self.dtype = device, dtype

    def ensure_device(self) -> bool:
        """Back to the configured CUDA device after an OOM fallback to CPU, when CUDA is available
        (called at the start of each perceive()). True if the detector moved back. On another OOM it
        stays on CPU (the CUDA cache is emptied) and the next call tries again."""
        with self._lock:
            return self._ensure_device()

    def _ensure_device(self) -> bool:
        torch = self._torch
        want = getattr(self, "preferred_device", self.device)
        if not str(want).startswith("cuda") or str(self.device) == str(want):
            return False
        try:
            if not torch.cuda.is_available():
                return False
            self._to_device(want)
        except (torch.OutOfMemoryError, RuntimeError) as exc:
            log.warning("detector still cannot return to %s (%s); staying on CPU", want, exc)
            self._to_device("cpu")
            torch.cuda.empty_cache()
            return False
        log.info("detector back on %s after an earlier CUDA fallback", want)
        return True

    def _prep(self, frames: list[np.ndarray]):
        s = self.input_size
        arr = np.stack([cv2.resize(f, (s, s), interpolation=cv2.INTER_LINEAR)[:, :, ::-1] for f in frames])
        x = self._torch.from_numpy(np.ascontiguousarray(arr)).permute(0, 3, 1, 2)
        return x.to(self.device).to(self.dtype).div_(255.0)

    def _forward(self, frames: list[np.ndarray]):
        torch = self._torch
        with torch.inference_mode():
            out = self._model(pixel_values=self._prep(frames))
        scores = out.logits.float().sigmoid()  # [B, Q, C]
        boxes = out.pred_boxes.float()  # cxcywh normalized
        xyxy = torch.cat([boxes[..., :2] - boxes[..., 2:] / 2, boxes[..., :2] + boxes[..., 2:] / 2], -1).clamp(0, 1)
        return scores.cpu().numpy(), xyxy.cpu().numpy()

    def detect(self, frames: list[np.ndarray]) -> list[dict]:
        """Per frame: {"persons": [[x1,y1,x2,y2,score]], "objects": [[label, x1,y1,x2,y2,score]]}.
        Thread-safe: each batch (forward + a possible OOM move to CPU) runs under the model lock."""
        results: list[dict] = []
        for i in range(0, len(frames), self.batch_size):
            chunk = frames[i : i + self.batch_size]
            with self._lock:
                try:
                    scores, xyxy = self._forward(chunk)
                except self._torch.OutOfMemoryError:
                    log.warning("CUDA OOM in detector; falling back to CPU (CUDA is retried at the next video)")
                    self._to_device("cpu")  # move first, then free the CUDA cache the weights used
                    self._torch.cuda.empty_cache()
                    scores, xyxy = self._forward(chunk)
            for sc, bx in zip(scores, xyxy):
                results.append(self._decode(sc, bx))
        return results

    def _decode(self, sc: np.ndarray, bx: np.ndarray) -> dict:
        persons = []
        ps = sc[:, self.person_id]
        for q in np.nonzero(ps >= self.person_thr)[0]:
            persons.append([*map(float, bx[q]), float(ps[q])])
        persons = _nms(persons, 0.7)
        objects = []
        for cid, name in self.object_ids.items():
            cs = sc[:, cid]
            for q in np.nonzero(cs >= self.object_thr)[0]:
                objects.append([name, *map(float, bx[q]), float(cs[q])])
        return {"persons": persons, "objects": objects}


def _nms(dets: list[list[float]], thr: float) -> list[list[float]]:
    """Plain NMS on [x1,y1,x2,y2,score]; DETR rarely needs it but duplicate queries happen."""
    from forte_detector.perception.tracker import iou

    dets = sorted(dets, key=lambda d: -d[4])
    keep: list[list[float]] = []
    for d in dets:
        if all(iou(d, k) < thr for k in keep):
            keep.append(d)
    return keep
