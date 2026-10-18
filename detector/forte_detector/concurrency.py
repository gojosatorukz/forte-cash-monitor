"""Thread-safety helpers for running several analyze() calls concurrently in one process.

The service runs JOB_WORKERS analyzers, each analyze() in its own thread. The heavy models are
process-wide singletons on purpose (one copy in VRAM):

* ``SingletonCache`` - lazy, double-checked, lock-protected init of those singletons (no two
  threads ever load the same model twice).
* The detectors themselves (RT-DETR, OWLv2) serialise their forward passes with a per-instance
  lock: their CUDA-OOM fallback moves the SHARED model to CPU in place (and perceive() moves it back),
  so an unlocked concurrent forward could see half-moved weights (device/dtype mismatch). Inference is
  short and GPU-bound anyway, so serialising costs little; a per-thread CPU copy would cost RAM and
  complexity for no throughput gain.
* VLM per-job state (failure counters, "down for this batch") lives in a ``threading.local`` inside
  VLMClient: run_probes() / begin_batch() run in the analyze() thread, so one slow job never marks
  the VLM down for the others. Concurrent POSTs are bounded by a BoundedSemaphore (FORTE_VLM_SLOTS).
* ``atomic_write_text`` - cache files are written to a temp file and renamed, so a concurrent reader
  of the same cache entry never sees a half-written JSON.
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from typing import Callable, Hashable, TypeVar

T = TypeVar("T")


class SingletonCache(dict):
    """A dict of lazily built singletons; ``get_or_create`` builds each key at most once."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()

    def get_or_create(self, key: Hashable, factory: Callable[[], T]) -> T:
        try:
            return self[key]
        except KeyError:
            pass
        with self._lock:
            if key not in self:
                self[key] = factory()
            return self[key]


def atomic_write_text(path: Path | str, text: str, encoding: str = "utf-8") -> None:
    """Write via a temp file in the same directory + os.replace (atomic on POSIX)."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
