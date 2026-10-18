"""Paths and environment configuration."""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DATA_DIR = "/home/datamata/Downloads/Datathon26-20261017T071335Z-1-001/Datathon26/open"
PACKAGE_DIR = Path(__file__).resolve().parent
REPO_DIR = PACKAGE_DIR.parent
LABELS_JSON = PACKAGE_DIR / "data" / "labels.json"


CACHE_DIR = REPO_DIR / "cache"  # default; use cache_dir() (honours FORTE_CACHE_DIR)


def cache_dir() -> Path:
    """Cache root; env FORTE_CACHE_DIR overrides the repo-local cache/."""
    return Path(os.environ.get("FORTE_CACHE_DIR") or CACHE_DIR)


def eval_cache_enabled() -> bool:
    """Result caches are OFF unless FORTE_EVAL_CACHE=1 (set by the eval harness). The service never
    caches by default: uploads with the same name must never be served a stale analysis."""
    return os.environ.get("FORTE_EVAL_CACHE") == "1"


DEFAULT_DET_MODEL = "PekingU/rtdetr_v2_r18vd"  # RT-DETRv2-S; prod may use e.g. PekingU/rtdetr_v2_r50vd (M)


def det_model() -> str:
    """Person/object detector checkpoint (HF id or local path); env FORTE_DET_MODEL overrides."""
    return os.environ.get("FORTE_DET_MODEL", DEFAULT_DET_MODEL)


def det_device() -> str | None:
    """Torch device for CV models ("cuda", "cuda:1", "cpu"); env FORTE_DEVICE; None = auto."""
    return os.environ.get("FORTE_DEVICE") or None


def data_dir() -> Path:
    return Path(os.environ.get("FORTE_DATA_DIR", DEFAULT_DATA_DIR))


def videos_dir() -> Path:
    return data_dir() / "videos"


def labels_xlsx() -> Path:
    return data_dir() / "open_labels.xlsx"


# ----------------------------------------------------------------------------- offline weights

OFFLINE_ENV = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")


def allow_download() -> bool:
    """Model downloads are OFF unless FORTE_ALLOW_DOWNLOAD=1 (scripts/fetch_models.py sets it)."""
    return os.environ.get("FORTE_ALLOW_DOWNLOAD") == "1"


def apply_offline_env() -> None:
    """No network at analysis time: HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE default to 1 (an explicit
    value in the environment wins) unless FORTE_ALLOW_DOWNLOAD=1. Runs on `import forte_detector`,
    i.e. before any submodule imports transformers / huggingface_hub (which read it at import)."""
    if allow_download():
        return
    for k in OFFLINE_ENV:
        os.environ.setdefault(k, "1")


def hf_kwargs() -> dict:
    """Extra from_pretrained kwargs: local cache only unless downloads are allowed (also covers a
    huggingface_hub imported before forte_detector, which would not see the env above)."""
    return {} if allow_download() else {"local_files_only": True}
