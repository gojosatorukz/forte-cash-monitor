"""Full analyzer: perceive -> calibrate -> propose -> probes -> decide -> merge/cap -> verdict.

Stage-wise and interruptible: rules run in priority order, each rule's probe group is one
`run_probes` call; after the `deadline` (time.monotonic) remaining VLM probes are skipped (value
None) and rules decide with what they have. `on_event(event)` is called as each event is accepted.
If the probe runner is unavailable, all probe values are None (the analyzer still runs).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from forte_detector.analyzers.perception_only import _variant_name
from forte_detector.concurrency import atomic_write_text
from forte_detector.perception.pipeline import perceive
from forte_detector.probes.base import ProbeRequest, ProbeResult
from forte_detector.rules import engine
from forte_detector.schema import VERDICT_CLEAN, VERDICT_VIOLATION, AnalysisResult, Event, ProgressCb, Transform

log = logging.getLogger(__name__)
Runner = Callable[..., list[ProbeResult]]
# perception stops this long before the analysis deadline: tracking + timeline (bounded by
# PerceptionConfig.max_samples), CV-only rules and one evidence frame per event must still fit
PERCEPTION_RESERVE_S = 3.0


def _default_runner() -> tuple[Runner | None, Callable | None]:
    try:
        from forte_detector.probes import runner as R
    except Exception as e:  # noqa: BLE001 - probes optional
        log.warning("probe runner unavailable: %s", e)
        return None, None
    return R.run_probes, getattr(R, "calibrate", None)


_SHA_MEMO: dict = {}


def file_sha1(path: str) -> str:
    """sha1 of the FULL file bytes (1 MB chunks), memoized per (abs path, size, mtime)."""
    import hashlib
    import os

    st = os.stat(path)
    k = (os.path.abspath(path), st.st_size, st.st_mtime_ns)
    if k not in _SHA_MEMO:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _SHA_MEMO[k] = h.hexdigest()
    return _SHA_MEMO[k]


def _cache_root() -> Path:
    import os

    from forte_detector import config

    return Path(os.environ.get("FORTE_CACHE_DIR") or config.CACHE_DIR)


class _ProbeDiskCache:
    """Eval-only on-disk probe + calibration cache. OFF unless env FORTE_EVAL_CACHE=1 (the eval
    harness sets it; library code never does). File key = sha1 of the full video bytes + variant
    (never the file name); entry key = cue + t + extra_ts + roi + hash of the cue's prompt spec.
    VLM-unavailable / skipped results are never stored. Root dir: env FORTE_CACHE_DIR or cache/."""

    def __init__(self, video_path: str, variant: str | None):
        import json as _json
        import os

        self._json = _json
        self.data: dict = {}
        self.enabled = os.environ.get("FORTE_EVAL_CACHE") == "1" and variant is not None
        self.path = self.cal_path = None
        if not self.enabled:
            return
        try:
            key = file_sha1(video_path)[:20]
        except OSError:
            self.enabled = False
            return
        root = _cache_root()
        self.path = root / "probes" / f"{key}__{variant}.json"
        self.cal_path = root / "calibration" / f"{key}__{variant}.json"
        if self.path.exists():
            try:
                self.data = _json.loads(self.path.read_text())
            except Exception:  # noqa: BLE001
                self.data = {}

    def load_calibration(self) -> dict | None:
        if self.enabled and self.cal_path.exists():
            try:
                return self._json.loads(self.cal_path.read_text())
            except Exception:  # noqa: BLE001
                return None
        return None

    def save_calibration(self, cal: dict) -> None:
        if self.enabled and not (cal.get("meta") or {}).get("error"):
            self.cal_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.cal_path, self._json.dumps(cal))

    @staticmethod
    def _spec_hash(cue: str) -> str:
        import hashlib

        try:
            from forte_detector.probes import questions

            spec = questions.SPECS.get(cue)
            blob = repr(spec)
        except Exception:  # noqa: BLE001
            blob = cue
        return hashlib.sha1(blob.encode()).hexdigest()[:8]

    def key(self, r: ProbeRequest) -> str:
        return f"{r.cue}|{r.t:.2f}|{','.join(f'{x:.2f}' for x in r.extra_ts)}|{r.roi}|{self._spec_hash(r.cue)}"

    def get(self, r: ProbeRequest) -> ProbeResult | None:
        d = self.data.get(self.key(r)) if self.enabled else None
        return ProbeResult(**d) if d else None

    def put(self, r: ProbeRequest, x: ProbeResult) -> None:
        if not self.enabled:
            return
        err = (x.raw or {}).get("error") or (x.raw or {}).get("skipped")
        if x.value is None and err:
            return
        d = x.to_dict()
        d["raw"] = {k: v for k, v in (d.get("raw") or {}).items() if k in ("count", "answer", "coco", "roi")}
        self.data[self.key(r)] = d

    def save(self) -> None:
        if self.enabled and self.data:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.path, self._json.dumps(self.data, ensure_ascii=False))


def effective_frame_transform(timeline: dict, transform: Transform | None):
    """Transform to apply to frames read for probes / calibration / evidence: the caller's transform
    plus whatever rotation perception applied (container tag, auto-orientation), via
    forte_detector.perception.effective_transform when available (Task 2); else the raw transform."""
    try:
        from forte_detector.perception import effective_transform
    except ImportError:
        return transform
    try:
        return effective_transform(timeline, transform)
    except Exception as e:  # noqa: BLE001
        log.warning("effective_transform failed, using the raw transform: %s", e)
        return transform


def _none_results(reqs: list[ProbeRequest], why: str) -> list[ProbeResult]:
    return [ProbeResult(r.cue, r.t, None, 0.0, [r.t], "", {"skipped": why}) for r in reqs]


def _req_key(r: ProbeRequest) -> tuple:
    return (r.cue, round(r.t, 2), r.roi, tuple(r.extra_ts))


def event_key(e: Event) -> tuple:
    """Stable identity of an event across re-finalization (type, timecode, source); public so the
    service adapter can match streamed and final events."""
    return (e.type, e.timecode_sec, e.source)


_ev_key = event_key  # backward-compatible alias (service_adapter imported the private name)


class _Accumulator:
    """Same policy as engine.finalize (single source of truth): events >= the verdict threshold,
    same-type events < merge_gap_s merged into the STRONGER one (window widened), top max_events
    by confidence. Streaming: on_event fires once for each event when it enters the current top
    set; a later, stronger event can still displace it, so the streamed set may be a superset of
    the final events. The returned AnalysisResult.events is authoritative."""

    def __init__(self, cfg: engine.RulesConfig, on_event: Callable[[Event], None] | None):
        self.cfg, self.on_event = cfg, on_event
        self.all: list[Event] = []
        self.low: list[Event] = []
        self.emitted: set = set()

    def add(self, e: Event) -> None:
        if e.confidence < self.cfg.verdict_threshold:
            self.low.append(e)
            return
        self.all.append(e)
        for f in self.final():
            k = event_key(f)
            if k not in self.emitted:
                self.emitted.add(k)
                if self.on_event is not None:
                    try:
                        self.on_event(f)
                    except Exception as ex:  # noqa: BLE001 - a consumer error must not kill the analysis
                        log.warning("on_event failed: %s", ex)

    def final(self) -> list[Event]:
        return engine.finalize(self.all, self.cfg)

    def displaced(self) -> tuple[list[Event], list[Event]]:
        """(cut by the max_events cap, merged into a stronger same-type event)."""
        final = self.final()
        keep = {event_key(f) for f in final}
        merged_src = {src for f in final for src in f.extra.get("merged", [])}
        out = [e for e in self.all if event_key(e) not in keep]
        merged = [e for e in out if e.source in merged_src and any(
            f.type == e.type and abs(f.timecode_sec - e.timecode_sec) < self.cfg.merge_gap_s for f in final)]
        cut = [e for e in out if e not in merged]
        return cut, merged


def _runner_kwargs(runner) -> set:
    import inspect

    try:
        params = inspect.signature(runner).parameters
    except (TypeError, ValueError):
        return set()
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return {"calibration", "deadline"}
    return {k for k in ("calibration", "deadline") if k in params}


def _supporting_probes(e: Event, pairs) -> list[dict]:
    """Probe answers behind an event, inside its window or at its evidence times: positive, count AND
    negative answers (e.g. T15 relies on the ID probes saying "no"), each with the normalized ROI the
    probe used, for evidence-box drawing (Task 6). Undecided (None) answers are left out."""
    lo, hi = min(e.start_sec, e.timecode_sec) - 0.5, max(e.end_sec, e.timecode_sec) + 0.5
    ev = set(e.evidence_ts)
    out = []
    for q, r in pairs:
        raw = r.raw or {}
        decided = r.value is not None or raw.get("count") is not None
        if not decided or not (lo <= q.t <= hi or round(q.t, 2) in ev):
            continue
        roi = raw.get("roi") or (list(q.roi) if q.roi else None)
        out.append({"cue": q.cue, "t": q.t, "extra_ts": list(q.extra_ts), "value": r.value,
                    "count": raw.get("count"), "conf": r.conf, "source": r.source,
                    "roi": [round(float(v), 4) for v in roi] if roi else None})
    return out[:8]


UNAVAILABLE_ERRORS = ("vlm_unavailable", "no_runner")


def _probe_status(memo: dict, runner) -> dict:
    """VLM health for this analysis, from EXPLICIT signals only: results whose raw error is
    "vlm_unavailable" (client found the server down) or our own "no_runner". unavailable = no runner,
    or such results exist and not one VLM-routed request got a real answer. Legitimate None answers,
    budget skips and deadline skips never make an analysis degraded; they are counted separately."""
    vlm = [x for k, x in memo.items() if k[0] not in engine.CV_CUES]

    def raw(x):
        return x.raw or {}

    def why(x):
        return raw(x).get("why") or raw(x).get("skipped") or ""

    down = sum(raw(x).get("error") in UNAVAILABLE_ERRORS or why(x) in UNAVAILABLE_ERRORS for x in vlm)
    answered = sum((x.value is not None or raw(x).get("count") is not None)
                   and x.source != "coco" and not raw(x).get("error") for x in vlm)
    requested = len(vlm)
    unavailable = requested > 0 and (runner is None or (down > 0 and answered == 0))
    # partial: the VLM dropped out for some requests only; rules relying on them may stay silent
    partial = bool(down > 0 and not unavailable)
    return {"requested": requested, "answered": answered, "vlm_unavailable": down,
            "deadline_skipped": sum(why(x) == "deadline" for x in vlm),
            "budget_skipped": sum(why(x) == "budget" for x in vlm),
            "unavailable": bool(unavailable), "partial": partial,
            "note": ("часть VLM-проверок не выполнена (сервер недоступен), часть нарушений могла быть пропущена"
                     if partial else "")}


def analyze(
    video_path: str,
    progress_cb: ProgressCb | None = None,
    *,
    transform: Transform | None = None,
    deadline: float | None = None,
    on_event: Callable[[Event], None] | None = None,
    on_timeline: Callable[[dict], None] | None = None,
    runner: Runner | None = None,
    calibrate_fn: Callable | None = None,
    cfg: engine.RulesConfig | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> AnalysisResult:
    """`should_stop()` (e.g. the service's cancel flag) is checked by perception between detector
    batches and acts like a passed deadline afterwards (no calibration, no VLM probes)."""
    cfg = cfg or engine.RulesConfig()
    t0 = time.perf_counter()
    stage: dict[str, float] = {}

    def prog(p: float, msg: str) -> None:
        if progress_cb is not None:
            try:
                progress_cb(round(min(1.0, max(0.0, p)), 3), msg)
            except Exception:  # noqa: BLE001
                pass

    def stopped() -> bool:
        if should_stop is None:
            return False
        try:
            return bool(should_stop())
        except Exception:  # noqa: BLE001
            return False

    truncated: dict = {"why": None}  # perception stopped early (deadline - reserve / cancel)

    def late() -> bool:
        return (truncated["why"] is not None or (deadline is not None and time.monotonic() >= deadline)
                or stopped())

    def probe_deadline() -> float | None:
        return time.monotonic() if (stopped() or truncated["why"] is not None) else deadline

    # 1. perception
    prog(0.0, "perception")
    ts = time.perf_counter()
    tl = perceive(video_path, transform=transform, variant=_variant_name(transform),
                  progress_cb=(lambda p, m: prog(0.4 * p, m)) if progress_cb else None,
                  deadline=None if deadline is None else deadline - PERCEPTION_RESERVE_S,
                  should_stop=should_stop)
    stage["perception"] = round(time.perf_counter() - ts, 3)
    stage["perception_cached"] = float(bool(tl.get("meta", {}).get("cached")))
    # Frames for probes/calibration must match the (possibly auto-rotated) perception frames.
    frame_tf = effective_frame_transform(tl, transform)
    # A truncated timeline means the time is already up: no calibration (OWLv2 + door reference take
    # seconds), VLM deadline = now, CV-only rules on the samples there are (engine.Ctx also stops
    # treating the cut as the end of the video).
    truncated["why"] = (tl.get("meta") or {}).get("truncated") or None
    if on_timeline is not None:  # e.g. the service adapter needs orientation for evidence frames
        try:
            on_timeline(tl)
        except Exception as e:  # noqa: BLE001 - a consumer error must not kill the analysis
            log.warning("on_timeline failed: %s", e)

    # 2. calibration
    if runner is None and calibrate_fn is None:
        runner, calibrate_fn = _default_runner()
    prog(0.42, "calibration")
    ts = time.perf_counter()
    calibration: dict = {}
    disk = _ProbeDiskCache(video_path, _variant_name(transform))
    cached_cal = disk.load_calibration()
    if cached_cal is not None:
        calibration = cached_cal
    elif calibrate_fn is not None and not late():
        try:
            calibration = calibrate_fn(video_path, tl, frame_tf) or {}
            disk.save_calibration(calibration)
        except Exception as e:  # noqa: BLE001
            log.warning("calibration failed: %s", e)
            calibration = {"meta": {"error": f"{type(e).__name__}: {e}"}}
    stage["calibration"] = round(time.perf_counter() - ts, 3)

    # 3-5. propose -> probes -> decide, rule by rule
    ctx = engine.Ctx(tl, calibration, cfg, staged=True)
    items = engine.plan(ctx)
    acc = _Accumulator(cfg, on_event)
    memo: dict[tuple, ProbeResult] = {}
    probe_log: list[dict] = []
    ts = time.perf_counter()
    n_items = max(1, len(items))
    vlm_keys: set = set()

    def fits(reqs: list[ProbeRequest]) -> bool:
        """Hard cap on VLM-routed requests per video (CV cues are free). A group either fits
        completely or is not run at all (rules never decide on a partial request set)."""
        new = {_req_key(r) for r in reqs if r.cue not in engine.CV_CUES} - vlm_keys
        if len(vlm_keys) + len(new) > cfg.max_vlm_total:
            return False
        vlm_keys.update(new)
        return True

    accepts = _runner_kwargs(runner) if runner is not None else set()

    def run_reqs(reqs: list[ProbeRequest]) -> None:
        todo = [r for r in reqs if _req_key(r) not in memo]
        for r in list(todo):
            hit = disk.get(r)
            if hit is not None:
                memo[_req_key(r)] = hit
                todo.remove(r)
        if not todo:
            return
        if runner is None:
            res = _none_results(todo, "no_runner")
        elif late() and "deadline" not in accepts:
            res = _none_results(todo, "deadline")
        else:
            kw = {}
            if "calibration" in accepts:
                kw["calibration"] = calibration
            if "deadline" in accepts:
                kw["deadline"] = probe_deadline()  # the runner skips VLM past it; CV cues still answer
            try:
                res = runner(video_path, todo, timeline=tl, transform=frame_tf, **kw)
            except Exception as e:  # noqa: BLE001
                log.warning("run_probes failed: %s", e)
                res = _none_results(todo, f"error: {type(e).__name__}")
            if len(res) != len(todo):
                res = _none_results(todo, "runner_length_mismatch")
        for r, x in zip(todo, res):
            memo[_req_key(r)] = x
            disk.put(r, x)

    for i, item in enumerate(items):
        prog(0.45 + 0.5 * i / n_items, f"probes: {item.rule.name}")
        if not fits(item.requests):
            probe_log.append({"rule": item.rule.name, "skipped": "vlm_cap"})
            if getattr(item.rule, "can_decide_unprobed", False):
                try:
                    for e in item.rule.decide(ctx, []):
                        acc.add(e)
                except Exception as e:  # noqa: BLE001
                    log.warning("rule %s failed: %s", item.rule.name, e)
            continue
        reqs = list(item.requests)
        run_reqs(reqs)
        try:
            extra = item.rule.followup(ctx, [(r, memo[_req_key(r)]) for r in reqs])
        except Exception as e:  # noqa: BLE001
            log.warning("followup %s failed: %s", item.rule.name, e)
            extra = []
        if extra and not fits(extra):
            probe_log.append({"rule": item.rule.name, "skipped": "vlm_cap (follow-up)"})
            extra = []
        if extra:
            run_reqs(extra)
            reqs += extra
        pairs = [(r, memo[_req_key(r)]) for r in reqs]
        probe_log += [{"rule": item.rule.name, "request": {"cue": r.cue, "t": r.t, "extra_ts": r.extra_ts,
                                                           "key": r.context.get("key")},
                       "result": {"value": x.value, "conf": x.conf, "source": x.source,
                                  "count": (x.raw or {}).get("count"),
                                  "skipped": (x.raw or {}).get("skipped"), "error": (x.raw or {}).get("error"),
                                  "roi": (x.raw or {}).get("roi") or (list(r.roi) if r.roi else None)}}
                      for r, x in pairs]
        try:
            evs = item.rule.decide(ctx, pairs)
        except Exception as e:  # noqa: BLE001 - one broken rule must not kill the analysis
            log.warning("rule %s failed: %s", item.rule.name, e)
            evs = []
        for e in sorted(evs, key=lambda e: -e.confidence):
            e.extra.setdefault("probes_used", _supporting_probes(e, pairs))
            acc.add(e)
    disk.save()
    stage["probes_rules"] = round(time.perf_counter() - ts, 3)

    events = acc.final()
    status = _probe_status(memo, runner)
    degraded = status["unavailable"]
    stage["degraded"] = float(degraded)
    stage["partial_vlm"] = float(status["partial"])
    prog(1.0, "done")
    slim = {k: v for k, v in tl.items() if k not in ("series", "tracks")}
    slim["series_len"] = len(tl.get("series", {}).get("t", []))
    cut, merged_away = acc.displaced()
    slim["low_conf_events"] = [e.to_dict() for e in acc.low]  # below the verdict threshold only
    slim["capped_events"] = [e.to_dict() for e in cut]  # >= threshold, cut by max_events
    slim["merged_events"] = [e.to_dict() for e in merged_away]  # folded into a stronger same-type event
    slim["probes_status"] = status
    slim["n_probe_requests"] = len(memo)
    slim["n_vlm_requests"] = len(vlm_keys)
    slim["deadline_hit"] = late()
    meta = tl.get("meta") or {}
    slim["truncated"] = bool(truncated["why"])
    if truncated["why"]:
        slim["truncated_reason"] = truncated["why"]
        slim["analyzed_sec"] = round(float(tl.get("duration") or 0.0), 2)
        slim["video_sec"] = meta.get("video_duration")
    return AnalysisResult(
        video=Path(video_path).name,
        verdict=VERDICT_VIOLATION if engine.verdict(events, cfg) else VERDICT_CLEAN,
        events=events,
        timeline=slim,
        calibration=calibration,
        runtime_sec=round(time.perf_counter() - t0, 3),
        stage_times=stage,
        probes=probe_log,
        degraded=degraded,
    )
