"""ForteAnalyzer (service adapter) against a minimal fake of the service's `app` package.

No models, no GPU, no real videos: the full analyzer, frame reader and person detector are injected.
The real pydantic models are exercised by scripts/service_smoke.py.
"""
from __future__ import annotations

import base64
import sys
import threading
import time
import types
from dataclasses import dataclass, field

import av
import cv2
import numpy as np
import pytest

from forte_detector import service_adapter as sa
from forte_detector.schema import AnalysisResult, Event


# ----------------------------------------------------------------------------- fake service

@dataclass
class FakeEvidenceFrame:
    timestamp_sec: float
    image_base64: str
    boxes: list = field(default_factory=list)


@dataclass
class FakeViolation:
    type: str
    timecode_sec: float
    start_sec: float | None = None
    end_sec: float | None = None
    confidence: float | None = None
    fact_conf: float | None = None
    legal_conf: float | None = None
    legal_strength: str | None = None  # extra field (the real service model ignores unknown fields)
    title_ru: str | None = None
    obs: str | None = None
    description: str = ""
    evidence_frames: list = field(default_factory=list)


@dataclass
class FakeResult:
    verdict: str | None = None
    violations: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


class FakeSink:
    def __init__(self):
        self.items, self._cancel = [], threading.Event()

    @property
    def cancelled(self):
        return self._cancel.is_set()

    def cancel(self):
        self._cancel.set()

    def add(self, v):
        self.items.append(v)

    def snapshot(self):
        return list(self.items)


@pytest.fixture(autouse=True)
def fake_app(monkeypatch):
    app = types.ModuleType("app")
    models = types.ModuleType("app.models")
    models.Violation, models.EvidenceFrame, models.AnalysisResult = FakeViolation, FakeEvidenceFrame, FakeResult
    app.models = models
    monkeypatch.setitem(sys.modules, "app", app)
    monkeypatch.setitem(sys.modules, "app.models", models)
    monkeypatch.delitem(sys.modules, "app.video", raising=False)


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("vid") / "clip.mp4"
    with av.open(str(path), "w") as c:
        s = c.add_stream("mpeg4", rate=10)
        s.width, s.height, s.pix_fmt = 64, 48, "yuv420p"
        for i in range(20):
            for pkt in s.encode(av.VideoFrame.from_ndarray(np.full((48, 64, 3), i * 10, np.uint8), format="bgr24")):
                c.mux(pkt)
        for pkt in s.encode():
            c.mux(pkt)
    return str(path)


def ev(t=12.0, **kw) -> Event:
    d = dict(type="T1", timecode_sec=t, confidence=0.81, description_ru="На 00:12 кассир вышел.",
             evidence_ts=[t, t + 2.0, t + 4.0, t + 6.0], source="r", title_ru="Уход кассира",
             start_sec=t - 2, end_sec=t + 5, fact_conf=0.9, legal_conf="high", obs="Кассир вышел из кадра.")
    d.update(kw)
    return Event(**d)


ROT90 = {"orientation": {"rot_cw_deg": 90, "container_rot_cw_deg": 0}}


def make(events, *, timeline=ROT90, after=None, raise_exc=None, record=None):
    """Fake full.analyze: perception (on_timeline), then emits events. No calibration / probe calls."""

    def analyze_fn(path, progress_cb, *, deadline, on_event, on_timeline):
        record is not None and record.update(deadline=deadline, inference=__import__("torch").is_inference_mode_enabled())
        progress_cb(0.0, "perception")
        progress_cb(0.2, "perception: detect")
        if timeline is not None:
            on_timeline(timeline)
        progress_cb(0.42, "calibration")
        for i, e in enumerate(events):
            progress_cb(0.45 + 0.1 * i, "probes: some_rule")
            on_event(e)
            if after:
                after(i)
        if raise_exc:
            raise raise_exc
        progress_cb(1.0, "done")
        return AnalysisResult(video="clip.mp4", verdict="violation" if events else "no_violation",
                              events=list(events), timeline={**(timeline or {}), "low_conf_events": []},
                              stage_times={"perception": 1.0})

    return analyze_fn


class Reader:
    def __init__(self):
        self.calls = []

    def __call__(self, path, t, transform):
        """Like read_frame_at: a landscape 480x640 stored frame, transform applied."""
        self.calls.append((t, transform))
        img = np.full((480, 640, 3), 90, np.uint8)
        return transform(img) if transform is not None else img


def decoded_shape(frame) -> tuple:
    return cv2.imdecode(np.frombuffer(base64.b64decode(frame.image_base64), np.uint8), cv2.IMREAD_COLOR).shape


def analyzer(fn, reader=None):
    return sa.ForteAnalyzer(warm=False, analyze_fn=fn, read_frame=reader or Reader())


# ----------------------------------------------------------------------------- helpers

def test_stage_ru_and_obs_and_legal():
    assert sa.stage_ru("perception: detect") == "Распознавание людей в кадре"
    assert sa.stage_ru("probes: t1_departure").startswith("Проверка правил")
    assert sa.stage_ru("calibration").startswith("Калибровка")
    assert sa.stage_ru("done") == "Формирование результата"
    assert sa.clean_obs("Кассир вышел из кадра.") == "кассир вышел из кадра"
    assert sa.clean_obs("VLM не ответил") == "VLM не ответил"
    assert sa.clean_obs("") == ""
    assert sa.legal_conf_num("high") > sa.legal_conf_num("medium") > sa.legal_conf_num("low")
    assert sa.legal_conf_num("") is None and sa.legal_conf_num(0.5) == 0.5


def test_evidence_times_first_is_timecode_max_three_distinct():
    assert sa.evidence_times(10.0, [9.0, 10.1, 11.0, 12.0, 13.0]) == [10.0, 9.0, 11.0]
    assert sa.evidence_times(5.0, []) == [5.0]
    assert sa.evidence_times(5.0, [float("nan"), -1, "x", 7]) == [5.0, 7.0]


def test_jpeg_roundtrip_and_rotation_meta():
    img = np.zeros((100, 200, 3), np.uint8)
    dec = cv2.imdecode(np.frombuffer(base64.b64decode(sa.jpeg_b64(img)), np.uint8), cv2.IMREAD_COLOR)
    assert dec.shape == (100, 200, 3)
    assert sa.rotation_meta({"rot_cw_deg": 90, "container_rot_cw_deg": 270}) == {
        "applied_rotation_deg": 90, "container_rotation_deg": 270}
    assert sa.rotation_meta(None) == {"applied_rotation_deg": 0, "container_rotation_deg": 0}
    assert sa.rotation_meta({"rot_cw_deg": -90})["applied_rotation_deg"] == 270


def test_person_boxes_at_nearest_sample_with_role_labels():
    tl = {"series": {"t": [0.0, 0.5, 1.0], "persons": [
        [], [[1, "cashier", [0.1, 0.2, 0.3, 0.6], False], [2, "visitor", [0.5, 0.1, 0.9, 0.9], False],
             [3, "visitor", [0.7, 0.0, 0.8, 0.2], True]], []]}}
    boxes = sa.person_boxes_at(tl, 0.6)
    assert [b["label"] for b in boxes] == ["кассир", "посетитель", "за стеклом"]
    assert all(b["kind"] == "person" for b in boxes)
    assert boxes[0] == {"x": 0.1, "y": 0.2, "w": 0.2, "h": 0.4, "label": "кассир", "kind": "person"}
    assert sa.person_boxes_at({"series": {"t": [0.0, 5.0], "persons": [[], [[1, "cashier", [0, 0, 1, 1], 0]]]}},
                              4.6) == []  # nearest sample 0.4 s away -> none
    assert sa.person_boxes_at(None, 1.0) == [] and sa.person_boxes_at({"series": {}}, 1.0) == []


def test_event_boxes_from_extra():
    e = ev(10.0, extra={"roi": [0.2, 0.3, 0.6, 0.7],
                        "boxes": [{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2, "label": "наличные", "kind": "object"},
                                  {"x": 0.5, "y": 0.5, "w": 0.0, "h": 0.1}]})
    boxes = sa.event_boxes(e, 10.0)
    assert boxes == [{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2, "label": "наличные", "kind": "object"},
                     {"x": 0.2, "y": 0.3, "w": 0.4, "h": 0.4, "label": "Уход кассира", "kind": "region"}]
    per_ts = ev(10.0, extra={"boxes": {"10.0": [{"x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1}], "14.0": []}})
    assert len(sa.event_boxes(per_ts, 10.0)) == 1 and sa.event_boxes(per_ts, 14.0) == []
    assert sa.event_boxes(ev(10.0), 10.0) == []


# ----------------------------------------------------------------------------- analyze

def test_analyze_maps_events_and_pushes_partial_immediately(video):
    sink, progress, rec, reader = FakeSink(), [], {}, Reader()
    seen_at_emit = []
    events = [ev(12.0), ev(40.0, type="T5", legal_conf="medium", obs="дверь открыта")]
    fn = make(events, after=lambda i: seen_at_emit.append(len(sink.items)), record=rec)
    dl = time.monotonic() + 100
    res = analyzer(fn, reader).analyze(video, lambda p, m: progress.append((p, m)), dl, sink)

    assert seen_at_emit == [1, 2]  # added to the sink as each event was decided
    assert rec["deadline"] == dl and rec["inference"] is True
    assert res.verdict == "violation" and [v.type for v in res.violations] == ["T1", "T5"]
    v = res.violations[0]
    assert (v.timecode_sec, v.start_sec, v.end_sec) == (12.0, 10.0, 17.0)
    assert v.confidence == 0.81 and v.fact_conf == 0.9 and v.legal_conf == sa.LEGAL_CONF_NUM["high"]
    assert v.obs == "кассир вышел из кадра" and v.description == "На 00:12 кассир вышел."
    assert v.title_ru == "Уход кассира"
    assert [f.timestamp_sec for f in v.evidence_frames] == [12.0, 14.0, 16.0]
    assert v.legal_strength == "high" and res.violations[1].legal_strength == "medium"
    assert res.violations[1].legal_conf == sa.LEGAL_CONF_NUM["medium"]
    assert len(reader.calls) == 6  # frames reused for the final result (not re-read)
    # progress: Russian, within [0, 1], non-decreasing, finishes at 1.0
    ps = [p for p, _ in progress]
    assert all(0 <= p <= 1 for p in ps) and ps == sorted(ps) and ps[-1] == 1.0
    assert all(not m.isascii() for _, m in progress)
    assert res.meta["analyzer"] == "forte-detector" and res.meta["stopped_early"] is False


def test_final_result_reflects_widened_interval(video):
    e = ev(12.0)
    sink = FakeSink()

    def widen(i):
        e.end_sec = 30.0  # accumulator merged a later same-type event

    res = analyzer(make([e], after=widen)).analyze(video, None, time.monotonic() + 100, sink)
    assert sink.items[0].end_sec == 17.0 and res.violations[0].end_sec == 30.0


def test_clean_video_no_violation(video):
    res = analyzer(make([])).analyze(video, None, time.monotonic() + 100, FakeSink())
    assert res.verdict == "no_violation" and res.violations == []


def test_cancel_stops_at_next_progress(video):
    sink = FakeSink()
    events = [ev(12.0), ev(40.0, type="T5"), ev(80.0, type="T7")]
    res = analyzer(make(events, after=lambda i: sink.cancel())).analyze(video, None, time.monotonic() + 100, sink)
    assert [v.type for v in sink.items] == ["T1"]
    assert [v.type for v in res.violations] == ["T1"] and res.meta["stopped_early"] is True


def test_deadline_passed_gives_single_frame(video):
    reader = Reader()
    res = analyzer(make([ev(12.0)]), reader).analyze(video, None, time.monotonic() - 1, FakeSink())
    assert [f.timestamp_sec for f in res.violations[0].evidence_frames] == [12.0] and len(reader.calls) == 1


def test_bad_evidence_frame_is_skipped_not_fatal(video):
    def reader(path, t, tf):
        if t > 12.0:
            raise ValueError("seek failed")
        return np.zeros((48, 64, 3), np.uint8)

    res = analyzer(make([ev(12.0)]), reader=reader).analyze(video, None, time.monotonic() + 100, FakeSink())
    assert [f.timestamp_sec for f in res.violations[0].evidence_frames] == [12.0]


def test_failure_after_events_returns_partial(video):
    sink = FakeSink()
    res = analyzer(make([ev(12.0)], raise_exc=ValueError("boom"))).analyze(video, None, time.monotonic() + 100, sink)
    assert [v.type for v in res.violations] == ["T1"] and res.meta["stopped_early"] is True
    assert "boom" in res.meta["errors"][0]


def test_failure_without_events_raises(video):
    with pytest.raises(RuntimeError, match="boom"):
        analyzer(make([], raise_exc=ValueError("boom"))).analyze(video, None, time.monotonic() + 100, FakeSink())


def test_missing_and_corrupt_video_raise_clear_error(tmp_path):
    a = analyzer(make([]))
    with pytest.raises(sa.VideoReadError, match="не найден"):
        a.analyze(tmp_path / "nope.mov", None, time.monotonic() + 100, FakeSink())
    bad = tmp_path / "bad.mov"
    bad.write_bytes(b"\x00garbage" * 1000)
    with pytest.raises(sa.VideoReadError, match="декодировать"):
        a.analyze(bad, None, time.monotonic() + 100, FakeSink())


def test_uses_service_video_error_when_available(tmp_path, monkeypatch):
    video_mod = types.ModuleType("app.video")

    class VideoError(RuntimeError):
        pass

    video_mod.VideoError = VideoError
    monkeypatch.setitem(sys.modules, "app.video", video_mod)
    with pytest.raises(VideoError):
        analyzer(make([])).analyze(tmp_path / "nope.mov", None, time.monotonic() + 100, FakeSink())


@pytest.mark.parametrize("orientation", [{"rot_cw_deg": 90, "container_rot_cw_deg": 0},
                                         {"rot_cw_deg": 0, "container_rot_cw_deg": 270}])
def test_evidence_rotated_with_timeline_transform_without_calib_or_probes(video, orientation):
    """on_event fires with no calibration / probe call (cache hit, deadline, no runner): the partial
    evidence is still read with perception's transform (from on_timeline) -> portrait frames."""
    sink, reader = FakeSink(), Reader()
    res = analyzer(make([ev(12.0)], timeline={"orientation": orientation}), reader).analyze(
        video, None, time.monotonic() + 100, sink)
    assert [decoded_shape(f)[:2] for f in sink.items[0].evidence_frames] == [(640, 480)] * 3
    assert [decoded_shape(f)[:2] for f in res.violations[0].evidence_frames] == [(640, 480)] * 3
    assert len(reader.calls) == 3  # oriented partial frames reused


def test_unrotated_timeline_keeps_landscape(video):
    res = analyzer(make([ev(12.0)], timeline={"orientation": {}})).analyze(video, None, time.monotonic() + 100,
                                                                            FakeSink())
    assert decoded_shape(res.violations[0].evidence_frames[0])[:2] == (480, 640)


def test_effective_transform_fallback_from_result_timeline(video):
    """No on_timeline call at all -> final frames use the result timeline's orientation."""
    reader = Reader()

    def fn(path, progress_cb, *, deadline, on_event, on_timeline):
        e = ev(12.0)
        on_event(e)  # emitted before any timeline is known: raw frames, re-read at the end
        return AnalysisResult(video="x", verdict="violation", events=[e], timeline=ROT90)

    sink = FakeSink()
    res = analyzer(fn, reader).analyze(video, None, time.monotonic() + 100, sink)
    assert decoded_shape(sink.items[0].evidence_frames[0])[:2] == (480, 640)
    assert [decoded_shape(f)[:2] for f in res.violations[0].evidence_frames] == [(640, 480)] * 3


def test_final_loop_honours_cancel(video):
    sink = FakeSink()
    events = [ev(12.0), ev(40.0, type="T5"), ev(80.0, type="T7")]

    class CancellingReader(Reader):
        def __call__(self, path, t, transform):
            if len(self.calls) >= 3:  # past the 3 frames of the emitted event: cancel in the final pass
                sink.cancel()
            return super().__call__(path, t, transform)

    def fn(path, progress_cb, *, deadline, on_event, on_timeline):
        on_timeline({"orientation": {}})
        on_event(events[0])
        return AnalysisResult(video="x", verdict="violation", events=events, timeline={})

    reader = CancellingReader()
    res = analyzer(fn, reader).analyze(video, None, time.monotonic() + 100, sink)
    assert res.meta["stopped_early"] is True and res.verdict is None
    assert [v.type for v in res.violations] == ["T1"]  # the partial snapshot; T7 never read
    assert len(reader.calls) == 6  # T1 (3) + T5 (3), nothing for T7


def test_final_loop_past_deadline_reuses_frames_no_new_reads(video):
    reader = Reader()
    events = [ev(12.0)]
    dl = time.monotonic() + 0.3

    def fn(path, progress_cb, *, deadline, on_event, on_timeline):
        on_event(events[0])  # emitted before the timeline is known (raw frames)
        time.sleep(max(0.0, deadline - time.monotonic()) + 0.02)  # then the deadline passes
        return AnalysisResult(video="x", verdict="violation", events=events, timeline=ROT90)

    res = analyzer(fn, reader).analyze(video, None, dl, FakeSink())
    assert len(reader.calls) == 3 and len(res.violations[0].evidence_frames) == 3


# ----------------------------------------------------------------------------- real accumulator

class TimeReader(Reader):
    """Frame brightness encodes the read timestamp (4 * t), so each evidence image can be traced."""

    def __call__(self, path, t, transform):
        self.calls.append((t, transform))
        return np.full((48, 64, 3), int(round(4 * t)), np.uint8)


def frame_time(frame) -> float:
    img = cv2.imdecode(np.frombuffer(base64.b64decode(frame.image_base64), np.uint8), cv2.IMREAD_GRAYSCALE)
    return float(img.mean()) / 4


def test_real_accumulator_copies_keep_their_own_frames(video):
    """full._Accumulator / engine.finalize stream and return COPIES: frames must follow the event key
    (a merged pair -> the stronger event's frames; an unmerged event -> its own), with no re-read."""
    from forte_detector.analyzers import full
    from forte_detector.rules import engine

    reader, sink = TimeReader(), FakeSink()
    weak, strong = ev(12.0, confidence=0.6, source="a"), ev(14.0, confidence=0.9, source="b")
    door = ev(40.0, type="T5", confidence=0.7, source="c")

    def fn(path, progress_cb, *, deadline, on_event, on_timeline):
        on_timeline({"orientation": {}})
        acc = full._Accumulator(engine.RulesConfig(), on_event)
        for e in (weak, strong, door):
            acc.add(e)
        return AnalysisResult(video="x", verdict="violation", events=acc.final(), timeline={})

    res = analyzer(fn, reader).analyze(video, None, time.monotonic() + 100, sink)
    by_type = {v.type: v for v in res.violations}
    assert sorted(by_type) == ["T1", "T5"]
    t1, t5 = by_type["T1"], by_type["T5"]
    assert (t1.timecode_sec, t1.start_sec, t1.end_sec) == (14.0, 10.0, 19.0)  # merged into the stronger
    assert [f.timestamp_sec for f in t1.evidence_frames] == [14.0, 16.0, 18.0]
    assert [round(frame_time(f)) for f in t1.evidence_frames] == [14, 16, 18]
    assert [f.timestamp_sec for f in t5.evidence_frames] == [40.0, 42.0, 44.0]
    assert [round(frame_time(f)) for f in t5.evidence_frames] == [40, 42, 44]
    assert len(reader.calls) == 9  # 3 streamed events x 3 frames; the final pass reuses them
    assert len(sink.items) == 3  # the streamed set may be a superset of the final events


def test_degraded_note_in_meta(video):
    def fn(path, progress_cb, *, deadline, on_event, on_timeline):
        r = AnalysisResult(video="x", verdict="no_violation", events=[],
                           timeline={"probes_status": {"requested": 5, "answered": 0, "vlm_unavailable": 5,
                                                       "deadline_skipped": 0}})
        r.degraded = True
        return r

    res = analyzer(fn).analyze(video, None, time.monotonic() + 100, FakeSink())
    assert res.meta["degraded"] is True and res.meta["notes_ru"] == [sa.DEGRADED_NOTE_RU]
    assert res.meta["probes_status"]["requested"] == 5

    def fn2(path, progress_cb, *, deadline, on_event, on_timeline):
        return AnalysisResult(video="x", verdict="no_violation", events=[],
                              timeline={"probes_status": {"requested": 5, "answered": 3, "deadline_skipped": 2}})

    res = analyzer(fn2).analyze(video, None, time.monotonic() + 100, FakeSink())
    assert res.meta["degraded"] is False and res.meta["notes_ru"] == [sa.DEADLINE_NOTE_RU]


def test_truncated_result_is_partial_with_note(video):
    """(4): a truncated analysis is surfaced: meta truncated / stopped_early / partial (the worker
    then shows the job as partial) and a Russian note with the analysed share."""
    def fn(path, progress_cb, *, deadline, on_event, on_timeline):
        return AnalysisResult(video="x", verdict="no_violation", events=[],
                              timeline={"truncated": True, "truncated_reason": "deadline", "analyzed_sec": 140.4,
                                        "video_sec": 3600.0, "deadline_hit": True,
                                        "probes_status": {"requested": 2, "answered": 0, "deadline_skipped": 2}})

    res = analyzer(fn).analyze(video, None, time.monotonic() + 100, FakeSink())
    m = res.meta
    assert m["truncated"] is True and m["stopped_early"] is True and m["partial"] is True
    assert m["analyzed_sec"] == 140.4 and m["video_sec"] == 3600.0 and m["deadline_hit"] is True
    assert m["notes_ru"][0] == "анализ прерван по времени: обработаны первые 140 с из 3600 с"
    assert sa.truncated_note(None, None) == "анализ прерван по времени: обработаны первые ? с из ? с"
    full = analyzer(make([])).analyze(video, None, time.monotonic() + 100, FakeSink())
    assert full.meta["truncated"] is False and full.meta["stopped_early"] is False


def test_should_stop_passed_only_when_accepted(video):
    rec = {}

    def fn(path, progress_cb, *, deadline, on_event, on_timeline, should_stop):
        rec["before"] = should_stop()
        sink.cancel()
        rec["after"] = should_stop()
        return AnalysisResult(video="x", verdict="no_violation", events=[], timeline={})

    sink = FakeSink()
    analyzer(fn).analyze(video, None, time.monotonic() + 100, sink)
    assert rec == {"before": False, "after": True}
    # fakes with the old signature still work (no should_stop kwarg passed)
    res = analyzer(make([])).analyze(video, None, time.monotonic() + 100, FakeSink())
    assert res.verdict == "no_violation"


# ----------------------------------------------------------------------------- integration

def test_integration_real_full_analyze_cancel_mid_run(video, monkeypatch):
    """Real ForteAnalyzer -> real full.analyze (fake perception stage + fake probe runner, real frame
    reads from the synthetic clip): the cancel flag reaches perception as should_stop, a cancel after
    the first streamed event returns the partial snapshot, and the on_timeline orientation (90 deg)
    reaches the evidence frames (64x48 stored -> 48 wide x 64 high)."""
    import functools

    from test_rules import Fake, departure_tl

    from forte_detector.analyzers import full

    tl = dict(departure_tl(), orientation={"rot_cw_deg": 90, "container_rot_cw_deg": 0})
    seen = {}

    def fake_perceive(path, **k):
        seen["should_stop"] = k.get("should_stop")
        seen["deadline"] = k.get("deadline")
        return dict(tl, meta={"cached": False})

    monkeypatch.setattr(full, "perceive", fake_perceive)
    fake = Fake(cash_on_desk=True)
    fn = functools.partial(full.analyze, runner=fake, calibrate_fn=lambda *a: {"desk": None})

    class CancelAfterFirst(FakeSink):
        def add(self, v):
            super().add(v)
            self.cancel()  # the service finalized the job right after the first violation

    sink = CancelAfterFirst()
    dl = time.monotonic() + 100
    a = sa.ForteAnalyzer(warm=False, analyze_fn=fn)
    res = a.analyze(video, None, dl, sink)
    assert seen["deadline"] == pytest.approx(dl - full.PERCEPTION_RESERVE_S)
    assert seen["should_stop"]() is True  # cancelled now
    assert res.meta["stopped_early"] is True
    assert [v.type for v in res.violations] == ["T1"] == [v.type for v in sink.items]
    frames = res.violations[0].evidence_frames
    assert frames and all(decoded_shape(f)[:2] == (64, 48) for f in frames)

    # same run without a cancel: completes, frames oriented the same way
    res2 = a.analyze(video, None, time.monotonic() + 100, FakeSink())
    assert res2.meta["stopped_early"] is False and [v.type for v in res2.violations] == ["T1"]
    assert all(decoded_shape(f)[:2] == (64, 48) for f in res2.violations[0].evidence_frames)

    # perception cut at the deadline: no calibration, no VLM, the job is reported partial with a note
    cal = []
    tl_cut = dict(tl, meta={"cached": False, "truncated": "deadline", "video_duration": 600.0})
    monkeypatch.setattr(full, "perceive", lambda path, **k: tl_cut)
    fake3 = Fake(cash_on_desk=True)
    fn3 = functools.partial(full.analyze, runner=fake3, calibrate_fn=lambda *a: cal.append(1) or {})
    a3 = sa.ForteAnalyzer(warm=False, analyze_fn=fn3)
    res3 = a3.analyze(video, None, time.monotonic() + 100, FakeSink())
    assert cal == [] and res3.violations == []
    assert res3.meta["stopped_early"] is True and res3.meta["truncated"] is True
    assert res3.meta["notes_ru"][0] == "анализ прерван по времени: обработаны первые 14 с из 600 с"
    assert res3.warnings == res3.meta["notes_ru"]  # notes also go to the service's warnings field


# ----------------------------------------------------------------------------- Task 6a: raw frames + boxes

class SceneReader(Reader):
    """Stored 480x640 frame: black with a white 'person' at raw rows 50:150, cols 400:600.
    `mirrored` emulates a horizontally flipped source (as the hflip robustness variant)."""

    def __init__(self, mirrored: bool = False):
        super().__init__()
        self.mirrored = mirrored

    def scene(self) -> np.ndarray:
        img = np.zeros((480, 640, 3), np.uint8)
        img[50:150, 400:600] = 255
        return img[:, ::-1].copy() if self.mirrored else img

    def __call__(self, path, t, transform):
        self.calls.append((t, transform))
        img = self.scene()
        return transform(img) if transform is not None else img


def perceived_box(reader: SceneReader, orientation: dict) -> list[float]:
    """What perception records: the white region's normalized xyxy on the frame read through the
    timeline's effective transform (the same frames perception decoded)."""
    tf = sa._effective_transform({"orientation": orientation})
    img = tf(reader.scene()) if tf is not None else reader.scene()
    ys, xs = np.nonzero(img[:, :, 0] > 128)
    h, w = img.shape[:2]
    return [xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h]


def served(frame) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(base64.b64decode(frame.image_base64), np.uint8), cv2.IMREAD_GRAYSCALE)


@pytest.mark.parametrize("orientation,mirrored", [
    ({}, False),
    ({"rot_cw_deg": 90, "container_rot_cw_deg": 0}, False),
    ({"rot_cw_deg": 0, "container_rot_cw_deg": 270}, False),
    ({"rot_cw_deg": 270, "container_rot_cw_deg": 90}, False),
    ({}, True),
    ({"rot_cw_deg": 90, "container_rot_cw_deg": 0}, True),
])
def test_boxes_cover_the_region_on_the_served_frame(video, orientation, mirrored):
    reader = SceneReader(mirrored)
    box = perceived_box(reader, orientation)
    tl = {"orientation": orientation,
          "series": {"t": [12.0, 14.0, 16.0], "persons": [[[1, "cashier", box, False]]] * 3}}
    sink = FakeSink()
    res = analyzer(make([ev(12.0)], timeline=tl), reader).analyze(video, None, time.monotonic() + 100, sink)
    for frames in (sink.items[0].evidence_frames, res.violations[0].evidence_frames):
        assert len(frames) == 3
        for f in frames:
            (b,) = f.boxes
            assert b["kind"] == "person" and b["label"] == "кассир"
            img = served(f)
            h, w = img.shape
            x0, y0 = round(b["x"] * w), round(b["y"] * h)
            x1, y1 = round((b["x"] + b["w"]) * w), round((b["y"] + b["h"]) * h)
            inside = img[y0 + 2:y1 - 2, x0 + 2:x1 - 2]
            mask = np.ones_like(img, bool)
            mask[max(0, y0 - 2):y1 + 2, max(0, x0 - 2):x1 + 2] = False
            assert inside.size and inside.mean() > 220  # the box covers the white region...
            assert img[mask].mean() < 10  # ...and nothing white lies outside it


def test_rot90_box_differs_from_raw_position(video):
    """Sanity: under rot90 the served box is NOT the raw-frame position (the test above is meaningful)."""
    reader = SceneReader()
    raw = perceived_box(reader, {})
    rot = perceived_box(reader, {"rot_cw_deg": 90})
    assert np.abs(np.array(raw) - np.array(rot)).max() > 0.2


def test_evidence_frames_are_raw_undrawn_reads(video):
    reader = SceneReader()
    tl = {"orientation": {"rot_cw_deg": 90}, "series": {"t": [12.0], "persons": [[[1, "visitor", [0.1, 0.1, 0.5, 0.5],
                                                                                    False]]]}}
    res = analyzer(make([ev(12.0)], timeline=tl), reader).analyze(video, None, time.monotonic() + 100, FakeSink())
    tf = sa._effective_transform(tl)
    for f in res.violations[0].evidence_frames:
        undrawn = sa.jpeg_b64(sa.downscale(tf(reader.scene())))
        assert f.image_base64 == undrawn  # byte-identical to an undrawn read: nothing burned in
    assert res.violations[0].evidence_frames[0].boxes[0]["label"] == "посетитель"
    assert res.violations[0].evidence_frames[1].boxes == []  # 14 s: no series sample within 0.3 s


def test_meta_applied_rotation_is_auto_orientation_only(video):
    tl = {"orientation": {"rot_cw_deg": 270, "container_rot_cw_deg": 90}}
    res = analyzer(make([ev(12.0)], timeline=tl)).analyze(video, None, time.monotonic() + 100, FakeSink())
    assert res.meta["applied_rotation_deg"] == 270 and res.meta["container_rotation_deg"] == 90
    res = analyzer(make([], timeline={"orientation": {}})).analyze(video, None, time.monotonic() + 100, FakeSink())
    assert res.meta["applied_rotation_deg"] == 0
