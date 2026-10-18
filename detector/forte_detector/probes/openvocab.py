"""Open-vocabulary zone discovery (calibration): safe, door, laptop, counting machine, desk.

Default model: OWLv2-base (`google/owlv2-base-patch16-ensemble`, Apache-2.0); Grounding-DINO-tiny
(`IDEA-Research/grounding-dino-tiny`, Apache-2.0) is supported via the same transformers API. Env
`FORTE_OV_MODEL` picks the checkpoint, `FORTE_OV_DEVICE` the device (cpu | cuda | auto, default auto:
CUDA when >= 1.5 GB is free at warm-up; a CUDA OOM later falls back to CPU; calibration runs on
<= 3 frames once per video).
"""
from __future__ import annotations

import logging
import os
import threading
import time

import numpy as np

from forte_detector.concurrency import SingletonCache

log = logging.getLogger(__name__)

DEFAULT_OV_MODEL = "google/owlv2-base-patch16-ensemble"

# key -> (text queries, min score). Scores differ a lot between OWLv2 (~0.1-0.5) and GDINO (~0.3-0.7).
QUERIES: dict[str, list[str]] = {
    # tuned on X-8/17/20/26/32/34 first frames (OWLv2): "a metal safe" picks the white pedestal,
    # "a banknote counting machine" picks the small note detector; these phrasings did best.
    "safe": ["a small grey metal cabinet"],
    "door": ["a glass door with a handle"],
    "laptop": ["a laptop"],
    "counting_machine": ["a black machine"],
    "desk": ["an office desk"],
}
MIN_SCORE = {"owlv2": 0.15, "owlvit": 0.1, "grounding": 0.3}


def ov_model() -> str:
    return os.environ.get("FORTE_OV_MODEL", DEFAULT_OV_MODEL)


def ov_device() -> str:
    """FORTE_OV_DEVICE = cpu | cuda[:n] | auto (default). auto: CUDA when >= 1.5 GB VRAM is free at
    warm-up (the VLM shares the GPU: start llama-server first, or pin cpu), else CPU."""
    dev = (os.environ.get("FORTE_OV_DEVICE") or "auto").strip().lower()
    if dev != "auto":
        return dev
    try:
        import torch

        if torch.cuda.is_available() and torch.cuda.mem_get_info()[0] >= 1.5 * 2**30:
            return "cuda"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


class OpenVocabDetector:
    """detect(image_bgr, queries) -> {key: (score, [x0, y0, x1, y1] pixels)} (best box per key)."""

    _lock = threading.RLock()  # class default (objects built via __new__ in tests); __init__ sets one per instance

    def __init__(self, model_id: str | None = None, device: str | None = None, max_side: int = 960):
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        self._lock = threading.RLock()  # forward + OOM move to CPU on this shared model (+ HF processor)
        self.model_id = model_id or ov_model()
        dev = device or ov_device()
        if dev.startswith("cuda") and not torch.cuda.is_available():
            dev = "cpu"
        self.device = dev
        self.max_side = max_side
        self.kind = "grounding" if "grounding" in self.model_id.lower() else (
            "owlv2" if "owlv2" in self.model_id.lower() else "owlvit")
        self.min_score = MIN_SCORE[self.kind]
        self._torch = torch
        from forte_detector import config as fconfig

        self.processor = AutoProcessor.from_pretrained(self.model_id, **fconfig.hf_kwargs())
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(self.model_id, **fconfig.hf_kwargs()).eval()
        try:
            self.model.to(dev)
        except (torch.OutOfMemoryError, RuntimeError) as exc:
            if dev == "cpu":
                raise
            log.warning("cannot place open-vocab model on %s (%s); using CPU", dev, exc)
            self._to_cpu()

    def _to_cpu(self) -> None:
        self.model.to("cpu")
        self.device = "cpu"
        try:
            self._torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass

    def detect(self, image: np.ndarray, queries: dict[str, list[str]]) -> dict[str, tuple[float, list[float]]]:
        """Best box per key; a CUDA OOM (the VLM grew) moves the model to CPU for good and retries.
        Thread-safe: serialised on the model lock (shared singleton; see forte_detector/concurrency.py)."""
        with self._lock:
            try:
                return self._detect(image, queries)
            except self._torch.OutOfMemoryError as exc:
                if str(self.device) == "cpu":
                    raise
                log.warning("open-vocab CUDA OOM (%s); falling back to CPU", exc)
                self._to_cpu()
                return self._detect(image, queries)

    def _detect(self, image: np.ndarray, queries: dict[str, list[str]]) -> dict[str, tuple[float, list[float]]]:
        from PIL import Image

        torch = self._torch
        h, w = image.shape[:2]
        pil = Image.fromarray(image[:, :, ::-1].copy())
        keys, texts = [], []
        for k, qs in queries.items():
            for q in qs:
                keys.append(k)
                texts.append(q)
        if self.kind == "grounding":
            prompt = ". ".join(t.lower() for t in texts) + "."
            inputs = self.processor(images=pil, text=prompt, return_tensors="pt").to(self.device)
        else:
            inputs = self.processor(images=pil, text=[texts], return_tensors="pt").to(self.device)
        with torch.inference_mode():
            out = self.model(**inputs)
        best: dict[str, tuple[float, list[float]]] = {}
        if self.kind == "grounding":
            res = self.processor.post_process_grounded_object_detection(
                out, inputs.input_ids, threshold=self.min_score, text_threshold=0.25, target_sizes=[(h, w)])[0]
            labels = res.get("text_labels") or res.get("labels")
            for sc, lab, box in zip(res["scores"].tolist(), labels, res["boxes"].tolist()):
                k = next((keys[i] for i, t in enumerate(texts) if t.lower().lstrip("a ").strip() in str(lab)
                          or str(lab) in t.lower()), None)
                if k and sc > best.get(k, (0.0,))[0]:
                    best[k] = (float(sc), [float(v) for v in box])
        else:
            # OWL pads the image to a square: boxes are relative to the padded square
            side = max(h, w)
            res = self.processor.post_process_grounded_object_detection(
                out, threshold=self.min_score, target_sizes=[(side, side)])[0]
            for sc, li, box in zip(res["scores"].tolist(), res["labels"].tolist(), res["boxes"].tolist()):
                k = keys[int(li)]
                x0, y0, x1, y1 = box
                box = [max(0.0, x0), max(0.0, y0), min(float(w), x1), min(float(h), y1)]
                if box[2] <= box[0] or box[3] <= box[1]:
                    continue
                if sc > best.get(k, (0.0,))[0]:
                    best[k] = (float(sc), box)
        return best


_DETECTORS = SingletonCache()


def get_detector() -> OpenVocabDetector:
    """Warm singleton per model id (the device is chosen once, at first load). Thread-safe init."""
    m = ov_model()
    return _DETECTORS.get_or_create(m, lambda: OpenVocabDetector(m, ov_device()))


def pick_frames(timeline: dict, n: int = 3) -> list[float]:
    """Up to n sample times with the fewest persons, spread over the video (t=0 always considered)."""
    ser = timeline.get("series", {})
    ts = ser.get("t") or [0.0]
    npers = ser.get("n_persons") or [0] * len(ts)
    dur = max(timeline.get("duration") or ts[-1], 1e-3)
    order = sorted(range(len(ts)), key=lambda i: (npers[i], ts[i]))
    picked: list[float] = []
    for i in order:
        if all(abs(ts[i] - p) >= dur / (2 * n) for p in picked):
            picked.append(float(ts[i]))
        if len(picked) >= n:
            break
    return sorted(picked)


def calibrate_frames(frames: list[tuple[float, np.ndarray]], detector) -> dict:
    """Best box per key over frames, normalized. Missing keys -> None."""
    t0 = time.perf_counter()
    boxes: dict[str, list[float] | None] = {k: None for k in QUERIES}
    scores: dict[str, float | None] = {k: None for k in QUERIES}
    best_t: dict[str, float | None] = {k: None for k in QUERIES}
    for t, img in frames:
        h, w = img.shape[:2]
        for k, (sc, b) in detector.detect(img, QUERIES).items():
            if k in boxes and (scores[k] is None or sc > scores[k]):
                scores[k] = round(float(sc), 4)
                boxes[k] = [round(b[0] / w, 4), round(b[1] / h, 4), round(b[2] / w, 4), round(b[3] / h, 4)]
                best_t[k] = t
    return {**boxes, "scores": scores,
            "meta": {"model": getattr(detector, "model_id", "?"), "device": getattr(detector, "device", "?"),
                     "frames_t": [t for t, _ in frames], "best_t": best_t,
                     "runtime_s": round(time.perf_counter() - t0, 3)}}
