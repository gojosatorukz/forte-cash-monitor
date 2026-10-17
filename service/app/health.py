"""VLM reachability for /healthz: a cached, short-timeout GET of the VLM server's /health.

Informational only: `/healthz` reports `vlm_ok` but its `ok` never depends on it (the
analyzer degrades to a no-VLM run, see job `warnings`)."""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

DEFAULT_FORTE_VLM_URL = "http://127.0.0.1:8090/v1/chat/completions"
VLM_TIMEOUT_SEC = 1.5
VLM_CACHE_SEC = 5.0
VLM_BODY_MAX_BYTES = 4096
_OFF = {"", "off"}


def vlm_health_url_from_env() -> str | None:
    """VLM_HEALTH_URL if set ("" / "off" -> None: not configured), else scheme://host[:port]/health
    of FORTE_VLM_URL (default: the local llama.cpp server). None if the URL is unusable."""
    override = os.environ.get("VLM_HEALTH_URL")
    if override is not None:
        override = override.strip()
        return None if override.lower() in _OFF else override
    base = (os.environ.get("FORTE_VLM_URL") or "").strip() or DEFAULT_FORTE_VLM_URL
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        log.warning("FORTE_VLM_URL=%r is not an http(s) URL; VLM health is not probed", base)
        return None
    return f"{parts.scheme}://{parts.netloc}/health"


# No proxies: the VLM is local (or reached via a local tunnel); HTTP(S)_PROXY must not intercept.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _default_open(url: str, timeout: float) -> Any:
    return _OPENER.open(url, timeout=timeout)


class VlmProbe:
    """`check()` -> True (HTTP 200 and the body contains "ok"), False (anything else, including
    any exception), None (not configured). Results are cached for VLM_CACHE_SEC; concurrent
    callers share one in-flight probe, so a call never waits much longer than VLM_TIMEOUT_SEC."""

    def __init__(self, url: str | None, opener: Callable[[str, float], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.url = url
        self._opener = opener
        self._clock = clock
        self._lock = threading.Lock()
        self._value: bool | None = None
        self._checked_at: float | None = None

    def _probe(self) -> bool:
        opener = self._opener or _default_open  # looked up per call so tests can patch it
        try:
            with opener(self.url, VLM_TIMEOUT_SEC) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                body = resp.read(VLM_BODY_MAX_BYTES).decode("utf-8", errors="replace")
            return status == 200 and "ok" in body.lower()
        except Exception as e:  # noqa: BLE001 - /healthz must never fail because of the VLM
            log.debug("VLM health probe %s failed: %s", self.url, e)
            return False

    def check(self) -> bool | None:
        if not self.url:
            return None
        if not self._lock.acquire(timeout=VLM_TIMEOUT_SEC + 0.5):
            return self._value  # another request is probing right now; report the last result
        try:
            now = self._clock()
            if self._checked_at is None or now - self._checked_at >= VLM_CACHE_SEC:
                self._value = self._probe()
                self._checked_at = self._clock()
            return self._value
        finally:
            self._lock.release()
