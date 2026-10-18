"""Adapter for the FastAPI service (`ROOT/service`): `ANALYZER=forte_detector.service_adapter:ForteAnalyzer`.

Contract (ROOT/docs/status_service.md, "Analyzer interface"):

    analyzer = ForteAnalyzer()          # worker thread at startup: models are loaded once here
    analyzer.analyze(video_path, progress_cb, deadline, partial) -> app.models.AnalysisResult

* every accepted event is pushed to `partial.add(Violation(...))` as soon as it is decided;
* `deadline` (time.monotonic) is passed to the full analyzer (remaining VLM probes are skipped);
* `partial.cancelled` aborts the run at the next progress callback (the job is already finalized),
  and is passed to full.analyze as `should_stop` (checked by perception between detector batches);
* `torch.inference_mode()` is entered inside analyze (the worker calls it from a fresh thread);
* evidence frames: 1-3 JPEGs read from the .mov with the same orientation transform perception used
  (`forte_detector.perception.effective_transform`), sent RAW (nothing drawn); per-frame `boxes`
  [{x, y, w, h, label, kind}] normalized to the served upright JPEG (persons from the timeline series
  with role labels, plus any ROI/object boxes the event carries); the first frame is exactly at
  `timecode_sec`;
* meta `applied_rotation_deg` = our auto-orientation (clockwise, beyond the container tag).

The service types are imported lazily, so this module imports without the service on sys.path.
"""
from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

log = logging.getLogger(__name__)

# rules.json citation strength -> the service's numeric legal_conf. This is an ORDINAL encoding of the
# rule-link strength (high > medium > low) for sorting / thresholds, NOT a calibrated probability.
# The original label is also passed as the extra field `legal_strength`.
LEGAL_CONF_NUM = {"high": 0.9, "medium": 0.7, "low": 0.4}
EVIDENCE_MAX_SIDE = 960
EVIDENCE_JPEG_QUALITY = 85
EVIDENCE_MIN_GAP_S = 0.25  # extra evidence timestamps closer than this to an earlier one are dropped
# Evidence frames are sent RAW; boxes go per EvidenceFrame as {x, y, w, h, label, kind} normalized to
# the served upright JPEG, and the service draws them (person -> red, object/region -> green).
PERSON_MAX_DT_S = 0.3  # timeline sample must be this close to the evidence timestamp
ROLE_LABELS = {"cashier": "кассир", "visitor": "посетитель"}
ROLE_LABEL_OUTSIDE = "за стеклом"

# full-analyzer stage prefix -> Russian stage text for the service UI
STAGES_RU = (
    ("perception (cached)", "Распознавание людей в кадре"),
    ("perception", "Распознавание людей в кадре"),
    ("calibration", "Калибровка зон: касса, дверь, рабочее место"),
    ("probes", "Проверка правил"),
    ("done", "Формирование результата"),
)
STAGE_OPEN = "Чтение видео"
STAGE_EVIDENCE = "Подготовка кадров-доказательств"
STAGE_FINAL = "Формирование результата"


class _Cancelled(BaseException):
    """Raised from the progress callback when the service cancelled the job.

    A BaseException on purpose: the full analyzer guards each stage with `except Exception`, so this
    one passes through all of them and unwinds the run at the next progress call."""


class VideoReadError(RuntimeError):
    """The video is missing or cannot be decoded (fallback when app.video.VideoError is unavailable)."""


def _service_types():
    from app.models import AnalysisResult, EvidenceFrame, Violation

    return Violation, EvidenceFrame, AnalysisResult


def _video_error(msg: str) -> Exception:
    try:
        from app.video import VideoError

        return VideoError(msg)
    except Exception:  # noqa: BLE001 - service not importable (tests / CLI)
        return VideoReadError(msg)


def event_key(e) -> tuple:
    """Stable identity of an event across the accumulator's copies (same key as full.analyze's
    streaming dedupe: a merged event keeps the stronger event's type / timecode / source)."""
    try:
        from forte_detector.analyzers.full import event_key as _full_event_key

        return _full_event_key(e)
    except ImportError:  # pragma: no cover - mirror of full.event_key
        return (e.type, e.timecode_sec, e.source)


DEGRADED_NOTE_RU = "анализ выполнен без VLM-проверок, результат может быть неполным"
DEADLINE_NOTE_RU = "часть VLM-проверок пропущена из-за лимита времени, результат может быть неполным"
TRUNCATED_NOTE_RU = "анализ прерван по времени: обработаны первые {n} с из {m} с"


def truncated_note(analyzed: Any, total: Any) -> str:
    def sec(x):
        try:
            return str(int(round(float(x))))
        except (TypeError, ValueError):
            return "?"

    return TRUNCATED_NOTE_RU.format(n=sec(analyzed), m=sec(total))


def stage_ru(msg: str) -> str:
    m = (msg or "").strip()
    for prefix, ru in STAGES_RU:
        if m.startswith(prefix):
            return ru
    return m or STAGE_FINAL


def legal_conf_num(level: Any) -> float | None:
    if isinstance(level, (int, float)) and not isinstance(level, bool):
        return float(level)
    return LEGAL_CONF_NUM.get(str(level or "").strip().lower())


def clean_obs(obs: str | None) -> str:
    """Bare clause: no leading capital, no trailing period (the service's template adds both)."""
    s = (obs or "").strip().rstrip(".").rstrip()
    if s and s[0].isupper() and not (len(s) > 1 and s[1].isupper()):  # keep acronyms ("ID", "VLM")
        s = s[0].lower() + s[1:]
    return s


def evidence_times(timecode: float, evidence_ts: list[float] | None, n_max: int = 3) -> list[float]:
    """`timecode` first (exactly), then up to n_max-1 distinct extra timestamps in their order."""
    out = [float(timecode)]
    for t in evidence_ts or []:
        try:
            t = float(t)
        except (TypeError, ValueError):
            continue
        if not np.isfinite(t) or t < 0:
            continue
        if all(abs(t - u) >= EVIDENCE_MIN_GAP_S for u in out):
            out.append(t)
        if len(out) >= n_max:
            break
    return out


def downscale(img: np.ndarray, max_side: int = EVIDENCE_MAX_SIDE) -> np.ndarray:
    import cv2

    h, w = img.shape[:2]
    s = max_side / max(h, w)
    if s < 1:
        img = cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
    return img


def _xyxy_box(b, label: str | None, kind: str) -> dict | None:
    """Normalized [x0, y0, x1, y1, ...] -> service box {x, y, w, h, label, kind} (clamped), or None."""
    try:
        x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in list(b)[:4])
    except (TypeError, ValueError):
        return None
    if not all(np.isfinite([x0, y0, x1, y1])) or x1 - x0 <= 1e-6 or y1 - y0 <= 1e-6:
        return None
    return {"x": round(x0, 4), "y": round(y0, 4), "w": round(x1 - x0, 4), "h": round(y1 - y0, 4),
            "label": label, "kind": kind}


def person_boxes_at(timeline: dict | None, t: float, max_dt: float = PERSON_MAX_DT_S) -> list[dict]:
    """Person boxes from the timeline series at the sample nearest `t` (<= max_dt away).

    Timeline boxes are normalized to perception's frames, which were read with the same
    effective_transform as the served evidence JPEG (downscaling keeps normalized coords), so they
    are used as is. Labels are roles only (no identities)."""
    series = (timeline or {}).get("series") or {}
    ts, persons = series.get("t") or [], series.get("persons") or []
    if not ts or not persons:
        return []
    import bisect

    i = bisect.bisect_left(ts, t)
    cand = [j for j in (i - 1, i) if 0 <= j < len(ts) and j < len(persons)]
    if not cand:
        return []
    j = min(cand, key=lambda k: abs(ts[k] - t))
    if abs(ts[j] - t) > max_dt:
        return []
    out = []
    for p in persons[j] or []:
        try:
            _tid, role, box, outside = p[0], p[1], p[2], (p[3] if len(p) > 3 else False)
        except (TypeError, IndexError):
            continue
        label = ROLE_LABEL_OUTSIDE if outside else ROLE_LABELS.get(role, ROLE_LABELS["visitor"])
        b = _xyxy_box(box, label, "person")
        if b is not None:
            out.append(b)
    return out


def event_boxes(event, t: float) -> list[dict]:
    """ROI / object boxes the event itself carries (normalized to the upright frame), if any:
    `extra["boxes"]` = [{x, y, w, h, label?, kind?}] or {timestamp: [...]}, `extra["roi"]` = [x0,y0,x1,y1]."""
    extra = getattr(event, "extra", None) or {}
    label = getattr(event, "title_ru", "") or None
    raw = extra.get("boxes")
    if isinstance(raw, dict):  # per evidence timestamp
        key = min(raw, key=lambda k: abs(float(k) - t), default=None) if raw else None
        raw = raw.get(key) if key is not None and abs(float(key) - t) <= PERSON_MAX_DT_S else []
    out = []
    for b in raw or []:
        if not isinstance(b, dict):
            continue
        try:
            x, y, w, h = (float(b[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            continue
        kind = b.get("kind") if b.get("kind") in ("person", "object", "region") else "region"
        bb = _xyxy_box([x, y, x + w, y + h], b.get("label") or label, kind)
        if bb is not None:
            out.append(bb)
    roi = extra.get("roi")
    if isinstance(roi, (list, tuple)) and len(roi) >= 4:
        bb = _xyxy_box(roi, label, "region")
        if bb is not None:
            out.append(bb)
    return out


def jpeg_b64(img: np.ndarray, quality: int = EVIDENCE_JPEG_QUALITY) -> str:
    import cv2

    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def check_video(path: Path) -> None:
    """Raise a clear error for a missing / undecodable file (the service reports the job as failed)."""
    if not path.is_file():
        raise _video_error(f"Файл видео не найден: {path.name}")
    import av

    has_stream = got_frame = False
    try:
        with av.open(str(path)) as c:
            has_stream = bool(c.streams.video)
            if has_stream:
                for _ in c.decode(video=0):
                    got_frame = True
                    break
    except Exception as e:  # noqa: BLE001
        raise _video_error(f"Не удалось декодировать видео {path.name}: {type(e).__name__}: {e}") from e
    if not has_stream:
        raise _video_error(f"В файле нет видеопотока: {path.name}")
    if not got_frame:
        raise _video_error(f"В видео нет ни одного кадра: {path.name}")


class ForteAnalyzer:
    """Service analyzer backed by `forte_detector.analyzers.full.analyze`."""

    name = "forte-detector"

    def __init__(self, *, warm: bool = True, analyze_fn: Callable | None = None,
                 read_frame: Callable | None = None) -> None:
        """`warm=False` and the injectable callables are for tests; the service calls `ForteAnalyzer()`."""
        self._analyze_fn = analyze_fn
        self._read_frame = read_frame
        self._detector = None
        self.warm_info: dict[str, Any] = {}
        if warm:
            self._warm()

    # ------------------------------------------------------------------ warm-up
    def _warm(self) -> None:
        """Load every model once; the module-level singletons are then reused by each analyze()."""
        t0 = time.perf_counter()
        try:
            from forte_detector import config
            from forte_detector.perception.pipeline import PerceptionConfig, get_detector

            # same cache key as perception's own call (cfg defaults + FORTE_DEVICE) -> same instance
            self._detector = get_detector(PerceptionConfig(), config.det_device())
            dummy = np.zeros((64, 64, 3), np.uint8)
            import torch

            with torch.inference_mode():
                self._detector.detect([dummy])
            self.warm_info["detector"] = {"model": self._detector.model_id, "device": str(self._detector.device)}
        except Exception as e:  # noqa: BLE001 - analyze() will retry lazily through perception
            log.exception("detector warm-up failed")
            self.warm_info["detector"] = {"error": f"{type(e).__name__}: {e}"}
        try:
            from forte_detector.probes import openvocab

            ov = openvocab.get_detector()
            self.warm_info["openvocab"] = {"model": openvocab.ov_model(), "device": str(getattr(ov, "device", "?"))}
        except Exception as e:  # noqa: BLE001 - calibration falls back inside the runner
            log.exception("open-vocab warm-up failed")
            self.warm_info["openvocab"] = {"error": f"{type(e).__name__}: {e}"}
        try:
            from forte_detector.probes import runner

            client = runner.get_client()
            up = client.available()
            client.begin_batch()  # do not let a down-at-startup flag stick into the first job
            self.warm_info["vlm"] = {"url": client.url, "up": bool(up), "model": client.model}
            if not up:
                log.warning("VLM server is not reachable at %s; VLM probes will be skipped until it is up",
                            client.url)
        except Exception as e:  # noqa: BLE001
            self.warm_info["vlm"] = {"error": f"{type(e).__name__}: {e}"}
        self.warm_info["warm_s"] = round(time.perf_counter() - t0, 2)
        log.info("ForteAnalyzer warm: %s", self.warm_info)

    # ------------------------------------------------------------------ evidence
    def _frame(self, video_path: Path, t: float, transform) -> np.ndarray:
        if self._read_frame is not None:
            return self._read_frame(str(video_path), t, transform)
        from forte_detector.video_io import read_frame_at

        return read_frame_at(str(video_path), t, transform)

    def evidence_frames(self, video_path: Path, event, transform, *, timeline: dict | None = None,
                        n_max: int = 3) -> list:
        """RAW upright JPEGs (nothing drawn) + per-frame boxes normalized to that JPEG."""
        _, EvidenceFrame, _ = _service_types()
        frames = []
        for t in evidence_times(event.timecode_sec, event.evidence_ts, n_max):
            try:
                img = self._frame(video_path, t, transform)
                b64 = jpeg_b64(downscale(img))
            except Exception as e:  # noqa: BLE001
                log.warning("evidence frame at %.2f s failed: %s", t, e)
                continue
            try:
                boxes = event_boxes(event, t) + person_boxes_at(timeline, t)
            except Exception as e:  # noqa: BLE001 - a frame without boxes is still evidence
                log.warning("evidence boxes at %.2f s failed: %s", t, e)
                boxes = []
            frames.append(EvidenceFrame(timestamp_sec=round(float(t), 3), image_base64=b64, boxes=boxes))
        return frames

    @staticmethod
    def violation(event, frames: list):
        Violation, _, _ = _service_types()
        obs = clean_obs(getattr(event, "obs", ""))
        return Violation(
            type=event.type,
            timecode_sec=round(float(event.timecode_sec), 3),
            start_sec=None if event.start_sec is None else round(float(event.start_sec), 3),
            end_sec=None if event.end_sec is None else round(float(event.end_sec), 3),
            confidence=float(event.confidence),
            fact_conf=None if event.fact_conf is None else float(event.fact_conf),
            legal_conf=legal_conf_num(event.legal_conf),  # ordinal, see LEGAL_CONF_NUM
            legal_strength=str(event.legal_conf or "").strip().lower() or None,  # extra field
            title_ru=getattr(event, "title_ru", "") or None,
            obs=obs or None,
            description=getattr(event, "description_ru", "") or "",
            evidence_frames=frames,
        )

    # ------------------------------------------------------------------ analyze
    def analyze(self, video_path, progress_cb, deadline: float, partial):
        import torch

        _, _, ServiceResult = _service_types()
        t_start = time.perf_counter()
        video_path = Path(video_path)
        progress = progress_cb or (lambda p, m: None)

        def cancelled() -> bool:
            return bool(getattr(partial, "cancelled", False))

        def late() -> bool:
            return deadline is not None and time.monotonic() >= deadline

        def report(p: float, msg: str) -> None:
            if cancelled():
                raise _Cancelled()
            try:
                progress(round(min(1.0, max(0.0, p)), 3), msg)
            except Exception:  # noqa: BLE001 - a progress hiccup must not break the analysis
                log.debug("progress_cb failed", exc_info=True)

        report(0.0, STAGE_OPEN)
        check_video(video_path)  # missing / corrupt -> raise (the job fails with a clear message)

        # Perception's orientation transform (container rotation + auto-rotation), learned from the
        # timeline via full.analyze's on_timeline hook, which fires right after perception (before any
        # event, even past the deadline). Evidence frames must be read with it to be upright.
        seen: dict[str, Any] = {"tl": None, "tf": None}

        def on_timeline(tl: dict) -> None:
            seen["tl"] = tl
            seen["tf"] = _effective_transform(tl)

        # event_key(event) -> (frames, read with the timeline transform). Keyed by a stable event key, not
        # id(): on_event gets copies and the final events are fresh copies (engine.finalize).
        frames_by_event: dict[tuple, tuple[list, bool]] = {}
        errors: list[str] = []

        def on_event(ev) -> None:
            if cancelled():
                raise _Cancelled()  # on_event errors are caught by `except Exception`; this is not
            frames = []
            try:
                frames = self.evidence_frames(video_path, ev, seen["tf"], timeline=seen["tl"],
                                              n_max=1 if late() else 3)
            except Exception as e:  # noqa: BLE001
                log.warning("evidence for %s failed: %s", ev.type, e)
            frames_by_event[event_key(ev)] = (frames, seen["tl"] is not None)
            try:
                partial.add(self.violation(ev, frames))
            except Exception as e:  # noqa: BLE001
                log.warning("partial.add(%s) failed: %s", ev.type, e)
                errors.append(f"partial: {type(e).__name__}: {e}")

        def prog(p: float, msg: str) -> None:
            report(0.02 + 0.9 * p, stage_ru(msg))

        analyze_fn = self._analyze_fn
        if analyze_fn is None:
            from forte_detector.analyzers.full import analyze as analyze_fn

        extra = {"should_stop": cancelled} if _accepts(analyze_fn, "should_stop") else {}
        stopped_early = False
        result = None
        try:
            with torch.inference_mode():
                result = analyze_fn(str(video_path), prog, deadline=deadline, on_event=on_event,
                                    on_timeline=on_timeline, **extra)
        except _Cancelled:
            log.info("analysis of %s cancelled by the service", video_path.name)
            stopped_early = True
        except Exception as e:  # noqa: BLE001
            log.exception("analysis of %s failed", video_path.name)
            if not len(partial.snapshot()):
                raise RuntimeError(f"Ошибка анализа видео {video_path.name}: {type(e).__name__}: {e}") from e
            errors.append(f"{type(e).__name__}: {e}")
            stopped_early = True

        if result is None:  # cancelled / failed after some events: return what we have
            items = list(partial.snapshot())
            return _make_result(ServiceResult, "violation" if items else "no_violation", items,
                                self._meta(None, t_start, stopped_early=True, errors=errors, timeline=seen["tl"]))

        # Final list: the accumulator may have widened start/end of an event after it was emitted.
        # Frames already read with the timeline's transform are reused; others are (re)read.
        violations = []
        tf_final = seen["tf"] if seen["tl"] is not None else _effective_transform(result.timeline)
        tl_final = seen["tl"] if seen["tl"] is not None else result.timeline  # full series only via hook

        def cancelled_result():
            return _make_result(ServiceResult, None, list(partial.snapshot()),
                                self._meta(result, t_start, stopped_early=True, errors=errors, timeline=seen["tl"]))

        try:
            report(0.93, STAGE_EVIDENCE)
        except _Cancelled:
            return cancelled_result()
        with torch.inference_mode():
            for ev in result.events:
                if cancelled():
                    return cancelled_result()
                frames, oriented = frames_by_event.get(event_key(ev), (None, False))
                # Past the deadline: keep what exists; read at most one plain frame when there is none.
                if frames is None or (not oriented and not late()) or (late() and not frames):
                    try:
                        frames = self.evidence_frames(video_path, ev, tf_final, timeline=tl_final,
                                                      n_max=1 if late() else 3)
                    except Exception as e:  # noqa: BLE001
                        log.warning("evidence for %s failed: %s", ev.type, e)
                        frames = frames or []
                try:
                    violations.append(self.violation(ev, frames))
                except Exception as e:  # noqa: BLE001
                    log.warning("violation %s dropped: %s", ev.type, e)
                    errors.append(f"violation: {type(e).__name__}: {e}")
        try:
            report(1.0, STAGE_FINAL)
        except _Cancelled:
            stopped_early = True
        return _make_result(
            ServiceResult,
            "violation" if violations else "no_violation",  # what the service recomputes
            violations,
            self._meta(result, t_start, stopped_early=stopped_early, errors=errors, timeline=seen["tl"]),
        )

    def _meta(self, result, t_start: float, *, stopped_early: bool, errors: list[str],
              timeline: dict | None = None) -> dict:
        m: dict[str, Any] = {
            "analyzer": self.name,
            "runtime_sec": round(time.perf_counter() - t_start, 2),
            "stopped_early": bool(stopped_early),
        }
        # Clockwise rotation applied to the decoded frames BEYOND the container tag (our auto-orientation
        # only; ffmpeg autorotate already applies the tag), so the service can rotate clips to match.
        tl_any = timeline if timeline is not None else (result.timeline if result is not None else {})
        m.update(rotation_meta((tl_any or {}).get("orientation")))
        if result is not None:
            tl = result.timeline or {}
            truncated = bool(tl.get("truncated"))
            m["truncated"] = truncated
            if truncated:  # only part of the video was analysed: the worker shows the job as partial
                m["stopped_early"] = True
                m["partial"] = True
                m["analyzed_sec"] = tl.get("analyzed_sec")
                m["video_sec"] = tl.get("video_sec")
            m.update({
                "detector_verdict": result.verdict,
                "stage_times": result.stage_times,
                "deadline_hit": bool(tl.get("deadline_hit")),
                "n_events": len(result.events),
                "n_low_conf": len(tl.get("low_conf_events") or []),
                "n_probe_requests": tl.get("n_probe_requests"),
                "n_vlm_requests": tl.get("n_vlm_requests"),
                "low_conf": [{"type": e.get("type"), "t": e.get("timecode_sec"), "conf": e.get("confidence")}
                             for e in (tl.get("low_conf_events") or [])[:10]],
            })
            n_vlm_skipped = sum(1 for p in result.probes if (p.get("result") or {}).get("skipped")
                                or (p.get("result") or {}).get("error"))
            m["n_probes_skipped_or_failed"] = n_vlm_skipped
            # Flags + Russian notes go into meta (job meta["analyzer"]) and the notes also into the
            # service's AnalysisResult.warnings (see _make_result).
            status = tl.get("probes_status")
            degraded = bool(getattr(result, "degraded", False))
            m["degraded"] = degraded
            if isinstance(status, dict):
                m["probes_status"] = status
            notes = []
            if truncated:
                notes.append(truncated_note(tl.get("analyzed_sec"), tl.get("video_sec")))
            if degraded:
                notes.append(DEGRADED_NOTE_RU)
            elif isinstance(status, dict) and status.get("deadline_skipped"):
                notes.append(DEADLINE_NOTE_RU)
            if notes:
                m["notes_ru"] = notes
                log.warning("%s: %s (probes_status=%s)", result.video, "; ".join(notes), status)
        if errors:
            m["errors"] = [e[:300] for e in errors[:5]]
        m["warm"] = self.warm_info
        if result is not None:
            try:  # read-only summary of the cash operation and its steps (does not touch events)
                from forte_detector.procedure import build_procedure

                m["procedure"] = build_procedure(result)
            except Exception as e:  # noqa: BLE001
                log.warning("procedure summary failed: %s", e)
        return fit_meta(m)


SERVICE_META_MAX_BYTES = 8 * 1024  # service worker drops the WHOLE analyzer meta above this
META_DROP_ORDER = ("warm", "low_conf", "stage_times", "probes_status", "errors")


def _json_size(obj) -> int:
    import json

    return len(json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))


def fit_meta(m: dict, limit: int = SERVICE_META_MAX_BYTES) -> dict:
    """Keep the analyzer meta under the service's size cap: drop debug keys first, then procedure
    step notes, then trailing procedure steps (never the flags the worker reads)."""
    for k in META_DROP_ORDER:
        if _json_size(m) <= limit:
            return m
        m.pop(k, None)
    proc = m.get("procedure")
    if isinstance(proc, dict) and _json_size(m) > limit:
        for s in proc.get("steps") or []:
            if s.get("status") == "ok":
                s["note_ru"] = ""
        while _json_size(m) > limit and proc.get("steps"):
            proc["steps"].pop()
    return m


def rotation_meta(orientation: dict | None) -> dict:
    o = orientation or {}

    def deg(v) -> int:
        try:
            return int(round(float(v or 0))) % 360
        except (TypeError, ValueError):
            return 0

    return {"applied_rotation_deg": deg(o.get("rot_cw_deg")),
            "container_rotation_deg": deg(o.get("container_rot_cw_deg"))}


def _make_result(cls, verdict, violations: list, meta: dict):
    """Service AnalysisResult; the Russian notes also go to its `warnings` field (shown to the operator)."""
    notes = list(meta.get("notes_ru") or [])
    try:
        return cls(verdict=verdict, violations=violations, meta=meta, warnings=notes)
    except TypeError:  # an older service model without `warnings`
        return cls(verdict=verdict, violations=violations, meta=meta)


def _accepts(fn, name: str) -> bool:
    """Whether `fn` takes keyword `name` (or **kwargs); injected test fakes may not."""
    import inspect

    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _effective_transform(timeline: dict):
    try:
        from forte_detector.perception import effective_transform
    except ImportError:
        return None
    try:
        return effective_transform(timeline or {}, None)
    except Exception as e:  # noqa: BLE001
        log.warning("effective_transform failed, using raw frames: %s", e)
        return None
