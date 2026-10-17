"""Client for a local llama-server (OpenAI-compatible) running Qwen3-VL.

One narrow question per call (see docs/spike_vlm.md): yes/no or a person count, answer forced by a
JSON schema (llama-server turns it into a grammar). Confidence comes from the token logprobs of the
answer token when the server returns them (``logprobs: true``), else a fixed default.

Never raises from ``yesno`` / ``count``: any failure returns ``(None, 0.0, {"error": ...})``.
Availability: ``begin_batch()`` (called by run_probes, i.e. once per video batch) clears the state;
the server is then checked once (GET /v1/models, <= 2 s; a check that only times out = busy, not
down). It is marked down until the next ``begin_batch()`` when the check is refused, when
``max_failures`` consecutive requests fail AND a strict health check then fails, or when failed
requests have used ``FORTE_VLM_FAIL_BUDGET_S`` (default 40 s) of the batch. Request timeout
``FORTE_VLM_TIMEOUT`` default 20 s (observed max latency under load ~11 s).

Time bounds (strict): each request's timeout is min(FORTE_VLM_TIMEOUT, deadline - now - 1 s, failure
budget left); a request that would get less than ``min_call_s`` (1.5 s) is not sent - past/near the
deadline it returns why "deadline", with the failure budget used up the server is marked down. So
failed requests never exceed the budget: worst case per batch ~ 2 s check + 40 s + <= 3 s health
check, and nothing runs past the deadline. The local server is reached without any proxy
(http_proxy / HTTPS_PROXY are ignored).
"""
from __future__ import annotations

import base64
import json
import re
import socket
import logging
import math
import os
import threading
import time
import urllib.request
from typing import Callable

import cv2
import numpy as np

log = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8090/v1/chat/completions"
DEFAULT_CONF = 0.7  # confidence when the server returns no logprobs

YESNO_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "enum": ["yes", "no"]}},
    "required": ["answer"],
}
COUNT_SCHEMA = {
    "type": "object",
    "properties": {"num_people": {"type": "integer", "minimum": 0, "maximum": 20}},
    "required": ["num_people"],
}
# Strict GBNF grammars, sent instead of response_format. A JSON-schema grammar allows arbitrary
# whitespace, and the 4B model sometimes emits runs of newlines until max_tokens truncates the JSON
# ("bad_json: Expecting value: line 7 column 19"). These admit exactly one compact answer.
YESNO_GRAMMAR = r'root ::= "{\"answer\": \"" ("yes" | "no") "\"}"'
COUNT_GRAMMAR = r'root ::= "{\"num_people\": " ("20" | "1" [0-9] | [0-9]) "}"'  # 0..20

_YESNO_RE = re.compile(r'"?answer"?\s*:\s*"?\s*(yes|no)\b', re.I)
_COUNT_RE = re.compile(r'"?num_people"?\s*:\s*(\d+)')


def parse_answer(text: str, kind: str):
    """Tolerant parse: the first {...} block as JSON, else a regex on the raw text; None if neither."""
    key = "answer" if kind == "yesno" else "num_people"
    a, b = text.find("{"), text.find("}")
    if a >= 0 and b > a:
        try:
            v = json.loads(text[a:b + 1]).get(key)
            if kind == "yesno" and isinstance(v, str) and v.strip().lower() in ("yes", "no"):
                return v.strip().lower()
            if kind == "count" and isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                return v
        except (json.JSONDecodeError, AttributeError):
            pass
    m = (_YESNO_RE if kind == "yesno" else _COUNT_RE).search(text)
    if not m:
        return None
    return m.group(1).lower() if kind == "yesno" else int(m.group(1))


def vlm_url() -> str:
    return os.environ.get("FORTE_VLM_URL", DEFAULT_URL)


# The VLM is a local server: never route it through http_proxy/HTTPS_PROXY from the environment
# (urllib's default opener honours them even for 127.0.0.1 unless no_proxy says otherwise).
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_post(url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read())


def _http_get(url: str, timeout: float) -> dict:
    with _OPENER.open(url, timeout=timeout) as r:
        return json.loads(r.read())


def _models_url(chat_url: str) -> str:
    base = chat_url.split("/v1/")[0] if "/v1/" in chat_url else chat_url.rstrip("/")
    return base + "/v1/models"


def encode_jpeg(img_bgr: np.ndarray, quality: int = 90) -> str:
    ok, buf = cv2.imencode(".jpg", img_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def _norm_tok(tok: str) -> str:
    return tok.strip().strip('"').strip().lower()


def _answer_probs(logprobs: dict | None, choices=("yes", "no")) -> dict | None:
    """p(choice) at the first sampled token that is one of `choices` (renormalized over choices)."""
    if not logprobs:
        return None
    for ent in logprobs.get("content") or []:
        if _norm_tok(ent.get("token", "")) in choices:
            ps = {c: 0.0 for c in choices}
            for alt in ent.get("top_logprobs") or [ent]:
                k = _norm_tok(alt.get("token", ""))
                if k in ps:
                    ps[k] += math.exp(alt.get("logprob", -1e9))
            total = sum(ps.values())
            if total <= 0:
                return None
            return {c: p / total for c, p in ps.items()}
    return None


def _number_prob(logprobs: dict | None) -> float | None:
    if not logprobs:
        return None
    for ent in logprobs.get("content") or []:
        if _norm_tok(ent.get("token", "")).isdigit():
            return math.exp(ent.get("logprob", -1e9))
    return None


class VLMClient:
    min_call_s = 1.5  # never send a request with less time than this (deadline / failure budget)
    deadline_margin_s = 1.0  # a request must end this long before the deadline

    def __init__(
        self,
        url: str | None = None,
        timeout: float | None = None,
        avail_ttl: float = 30.0,
        max_failures: int = 2,
        post: Callable[[str, dict, float], dict] | None = None,
        get: Callable[[str, float], dict] | None = None,
        fail_budget_s: float | None = None,
    ):
        self.url = url or vlm_url()
        self.timeout = timeout if timeout is not None else float(os.environ.get("FORTE_VLM_TIMEOUT", "20"))
        self.avail_ttl = avail_ttl
        self.max_failures = max_failures
        self.fail_budget_s = (fail_budget_s if fail_budget_s is not None
                              else float(os.environ.get("FORTE_VLM_FAIL_BUDGET_S", "40")))
        self._fail_time = 0.0
        self._post = post or _http_post
        self._get = get or _http_get
        self._avail: tuple[float, bool] | None = None  # (checked_at, up)
        self._failures = 0
        self._batch_down = False  # sticky until begin_batch()
        self._lock = threading.Lock()
        self.model: str | None = None

    # ------------------------------------------------------------------ availability
    def begin_batch(self) -> None:
        """Start of a probe batch (one video): forget the down-flag and re-check once."""
        self._batch_down = False
        self._failures = 0
        self._fail_time = 0.0
        self._avail = None

    def available(self) -> bool:
        if self._batch_down:
            return False
        now = time.monotonic()
        if self._avail is not None and now - self._avail[0] < self.avail_ttl:
            return self._avail[1]
        try:
            d = self._get(_models_url(self.url), min(2.0, self.timeout))
            data = d.get("data") or d.get("models") or []
            if data:
                self.model = data[0].get("id") or data[0].get("name")
            up = True
        except (TimeoutError, socket.timeout) as e:
            # a busy server (another client's request in flight) can be slow to answer /v1/models:
            # assume up and let the POST (with its own timeout / failure counting) decide
            log.info("VLM availability check timed out (%s); assuming busy, not down", e)
            up = True
        except Exception as e:  # noqa: BLE001 - refused / unreachable means "down"
            if isinstance(getattr(e, "reason", None), (TimeoutError, socket.timeout)):
                up = True
            else:
                log.warning("VLM server unavailable at %s: %s", self.url, e)
                up = False
        self._avail = (now, up)
        # do NOT reset _failures here: a hung server can still answer /v1/models quickly
        self._batch_down = not up
        return up

    def _health_ok(self) -> bool:
        """Strict liveness check after repeated failures: any error or timeout counts as failed."""
        try:
            self._get(_models_url(self.url), 3.0)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("VLM health check failed: %s", e)
            return False

    def _mark_down(self, why: str) -> None:
        log.warning("VLM marked down for the rest of this batch: %s", why)
        self._avail = (time.monotonic(), False)
        self._batch_down = True

    def _mark_failure(self, elapsed: float = 0.0) -> None:
        """Down for the batch when (a) max_failures consecutive failures AND the health check fails, or
        (b) failed calls have used up the batch failure budget (bounds the worst case: 2 x 20 s)."""
        self._failures += 1
        self._fail_time += elapsed
        if self._fail_time >= self.fail_budget_s:
            self._mark_down(f"failed calls took {self._fail_time:.1f}s >= budget {self.fail_budget_s:.0f}s")
        elif self._failures >= self.max_failures:
            if self._health_ok():
                self._failures = 0  # server alive (e.g. one slow call under load): keep using it
            else:
                self._mark_down(f"{self._failures} consecutive failures and failed health check")

    # ------------------------------------------------------------------ raw call
    def ask(self, images: list[np.ndarray], prompt: str, grammar: str, labels: list[str] | None = None,
            max_tokens: int = 24, timeout: float | None = None) -> dict:
        """POST one question; returns {"text", "logprobs", "wall_s", "timings"}. Raises on HTTP error."""
        content = []
        for i, im in enumerate(images):
            if labels:
                content.append({"type": "text", "text": labels[i]})
            content.append({"type": "image_url", "image_url": {"url": encode_jpeg(im)}})
        content.append({"type": "text", "text": prompt})
        body = {
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "logprobs": True,
            "top_logprobs": 5,
            "grammar": grammar,
        }
        t0 = time.perf_counter()
        with self._lock:  # llama-server runs -np 1: keep calls sequential
            d = self._post(self.url, body, self.timeout if timeout is None else timeout)
        wall = time.perf_counter() - t0
        ch = d["choices"][0]
        text = ch["message"]["content"]
        return {"text": text or "", "logprobs": ch.get("logprobs"),
                "wall_s": round(wall, 3), "timings": d.get("timings") or {}}

    def _call(self, images, prompt, grammar, labels, max_tokens, deadline=None):
        timeout = self.timeout
        if deadline is not None:
            left = deadline - time.monotonic() - self.deadline_margin_s
            if left < self.min_call_s:
                return None, {"why": "deadline", "skipped": "deadline"}
            timeout = min(timeout, left)
        if not self.available():
            return None, {"error": "vlm_unavailable", "url": self.url}
        if deadline is not None:  # the availability check may have taken up to 2 s
            timeout = min(timeout, deadline - time.monotonic() - self.deadline_margin_s)
            if timeout < self.min_call_s:
                return None, {"why": "deadline", "skipped": "deadline"}
        budget_left = self.fail_budget_s - self._fail_time
        if budget_left < self.min_call_s:  # strict: failed calls never exceed the batch budget
            self._mark_down(f"failure budget left {budget_left:.1f}s < {self.min_call_s}s")
            return None, {"error": "vlm_unavailable", "url": self.url, "why": "fail_budget"}
        timeout = min(timeout, budget_left)
        t0 = time.perf_counter()
        try:
            out = self.ask(images, prompt, grammar, labels=labels, max_tokens=max_tokens, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            self._mark_failure(time.perf_counter() - t0)
            return None, {"error": f"{type(e).__name__}: {e}"}
        self._failures = 0
        tm = out["timings"]
        raw = {"wall_s": out["wall_s"], "text": out["text"], "prompt_n": tm.get("prompt_n"),
               "predicted_n": tm.get("predicted_n"), "model": self.model}
        return out, raw

    # ------------------------------------------------------------------ public questions
    def yesno(self, images: list[np.ndarray], prompt: str, labels: list[str] | None = None,
              max_tokens: int | None = None, deadline: float | None = None) -> tuple[bool | None, float, dict]:
        """`deadline` (time.monotonic): request timeout capped to end 1 s before it; skipped when
        less than min_call_s would be left (raw why/skipped "deadline")."""
        out, raw = self._call(images, prompt, YESNO_GRAMMAR, labels, max_tokens or 24, deadline)
        if out is None:
            return None, 0.0, raw
        ans = parse_answer(out["text"], "yesno")
        if ans is None:
            log.warning("VLM yes/no unparseable: %r", out["text"][:200])
            return None, 0.0, {**raw, "error": "bad_answer"}
        val = ans == "yes"
        ps = _answer_probs(out["logprobs"])
        if ps is None:
            return val, DEFAULT_CONF, raw
        raw["p_yes"] = round(ps["yes"], 4)
        return val, round(ps[ans], 4), raw

    def count(self, images: list[np.ndarray], prompt: str, labels: list[str] | None = None,
              max_tokens: int | None = None, deadline: float | None = None) -> tuple[int | None, float, dict]:
        out, raw = self._call(images, prompt, COUNT_GRAMMAR, labels, max_tokens or 24, deadline)
        if out is None:
            return None, 0.0, raw
        n = parse_answer(out["text"], "count")
        if n is None:
            log.warning("VLM count unparseable: %r", out["text"][:200])
            return None, 0.0, {**raw, "error": "bad_answer"}
        p = _number_prob(out["logprobs"])
        return n, round(p, 4) if p is not None else DEFAULT_CONF, raw
