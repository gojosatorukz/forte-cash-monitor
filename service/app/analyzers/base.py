"""Analyzer plug-in interface."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.models import AnalysisResult, Violation

ProgressCb = Callable[[float, str], None]  # fraction 0..1, stage text (RU)


class PartialSink:
    """Thread-safe accumulator of violations found so far.

    The worker reads `snapshot()` if the analyzer overruns its deadline, so analyzers
    should `add()` each violation as soon as it is confirmed.

    `cancelled` turns true once the worker has finalized the job (deadline overrun, or any
    other finalization): an analyzer still running at that point should stop at the next
    loop iteration, because its result is ignored and it competes with the next job.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[Violation] = []
        self._cancel = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()

    def add(self, violation: Violation) -> None:
        with self._lock:
            self._items.append(violation)

    def snapshot(self) -> list[Violation]:
        with self._lock:
            return list(self._items)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


def deadline_passed(deadline: float) -> bool:
    """`deadline` is a time.monotonic() value."""
    return time.monotonic() > deadline


@runtime_checkable
class Analyzer(Protocol):
    name: str

    def analyze(
        self, video_path: Path, progress_cb: ProgressCb, deadline: float, partial: PartialSink
    ) -> AnalysisResult: ...
