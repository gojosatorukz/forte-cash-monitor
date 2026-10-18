"""Analyzer loading by dotted path `module:Class`."""

from __future__ import annotations

import importlib

from app.analyzers.base import Analyzer, PartialSink, ProgressCb

__all__ = ["Analyzer", "AnalyzerLoadError", "PartialSink", "ProgressCb", "load_analyzer"]


class AnalyzerLoadError(RuntimeError):
    pass


def load_analyzer(dotted: str) -> Analyzer:
    """Import `module:Class` and instantiate it with no arguments."""
    module_name, sep, attr = dotted.partition(":")
    if not sep or not module_name or not attr:
        raise AnalyzerLoadError(f"ANALYZER must look like 'module:Class', got {dotted!r}")
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise AnalyzerLoadError(f"cannot import analyzer module {module_name!r}: {e}") from e
    cls = getattr(module, attr, None)
    if cls is None:
        raise AnalyzerLoadError(f"{module_name!r} has no attribute {attr!r}")
    try:
        instance = cls()
    except Exception as e:  # noqa: BLE001 - surface any constructor failure clearly
        raise AnalyzerLoadError(f"cannot instantiate {dotted!r}: {e}") from e
    if not callable(getattr(instance, "analyze", None)):
        raise AnalyzerLoadError(f"{dotted!r} has no analyze() method")
    if not hasattr(instance, "name"):
        instance.name = attr  # type: ignore[attr-defined]
    return instance
