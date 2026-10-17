"""State probes: VLM client (mocked HTTP), CV probes on synthetic images, runner routing.

No GPU, no real VLM, no real video: frames come from a monkeypatched reader.
"""
import time

import numpy as np
import pytest

from forte_detector.probes import cheap, questions, runner, vlm
from forte_detector.probes.base import CUES, ProbeRequest, ProbeResult


# ----------------------------------------------------------------------------- helpers

def _lp(tok, logprob, alts=()):
    """One llama-server logprobs entry: sampled token + its top alternatives."""
    top = [{"token": tok, "logprob": logprob}] + [{"token": t, "logprob": l} for t, l in alts]
    return {"token": tok, "logprob": logprob, "top_logprobs": top}


def _yes_no_response(answer: str, p: float):
    other = "no" if answer == "yes" else "yes"
    toks = [_lp("{\n", 0.0), _lp(' "', 0.0), _lp("answer", 0.0), _lp('":', 0.0), _lp(' "', 0.0),
            _lp(answer, float(np.log(p)), [(other, float(np.log(1 - p)))]), _lp('"\n', 0.0), _lp("}", 0.0)]
    return {"choices": [{"message": {"content": '{"answer": "%s"}' % answer}, "logprobs": {"content": toks}}],
            "timings": {"prompt_n": 500, "predicted_n": 8}}


class FakeHTTP:
    """Records POST bodies; returns queued responses (or raises queued exceptions)."""

    def __init__(self, responses=(), up=True):
        self.responses = list(responses)
        self.bodies = []
        self.gets = 0
        self.up = up

    def post(self, url, body, timeout):
        self.bodies.append(body)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def get(self, url, timeout):
        self.gets += 1
        if not self.up:
            raise OSError("connection refused")
        return {"data": [{"id": "fake-vlm"}]}


def _client(http, **kw):
    return vlm.VLMClient(url="http://fake/v1/chat/completions", post=http.post, get=http.get, **kw)


IMG = np.full((80, 60, 3), 128, np.uint8)


# ----------------------------------------------------------------------------- VLM client

def test_yesno_parses_answer_and_logprob_confidence():
    http = FakeHTTP([_yes_no_response("yes", 0.9)])
    c = _client(http)
    val, conf, raw = c.yesno([IMG], "Is there cash?")
    assert val is True
    assert conf == pytest.approx(0.9, abs=1e-3)
    assert raw["p_yes"] == pytest.approx(0.9, abs=1e-3)
    body = http.bodies[0]
    assert body["temperature"] == 0
    # strict GBNF grammar (no free whitespace), not a JSON-schema response_format
    assert "response_format" not in body
    assert body["grammar"] == vlm.YESNO_GRAMMAR and '"yes"' in body["grammar"] and '"no"' in body["grammar"]
    parts = body["messages"][-1]["content"]
    assert sum(p["type"] == "image_url" for p in parts) == 1
    assert parts[-1]["type"] == "text" and "Is there cash?" in parts[-1]["text"]


def test_yesno_no_answer_without_logprobs_uses_default_conf():
    resp = {"choices": [{"message": {"content": '{"answer": "no"}'}}]}
    c = _client(FakeHTTP([resp]))
    val, conf, raw = c.yesno([IMG], "q")
    assert val is False
    assert conf == pytest.approx(vlm.DEFAULT_CONF)


def test_yesno_multi_image_labels_interleaved():
    http = FakeHTTP([_yes_no_response("no", 0.8)])
    c = _client(http)
    val, conf, _ = c.yesno([IMG, IMG], "q", labels=["Frame 1 (t=1.0s):", "Frame 2 (t=2.0s):"])
    assert val is False and conf == pytest.approx(0.8, abs=1e-3)
    parts = http.bodies[0]["messages"][-1]["content"]
    kinds = [p["type"] for p in parts]
    assert kinds == ["text", "image_url", "text", "image_url", "text"]


def test_count_parses_number():
    resp = {"choices": [{"message": {"content": '{"num_people": 2}'}}]}
    c = _client(FakeHTTP([resp]))
    n, conf, raw = c.count([IMG], "count")
    assert n == 2 and 0 < conf <= 1


def test_server_down_returns_none_fast_and_caches_availability():
    http = FakeHTTP(up=False)
    c = _client(http, avail_ttl=60)
    t0 = time.perf_counter()
    for _ in range(5):
        val, conf, raw = c.yesno([IMG], "q")
        assert val is None and conf == 0.0
        assert raw.get("error")
    assert time.perf_counter() - t0 < 0.5
    assert http.gets == 1  # availability checked once, then cached
    assert http.bodies == []  # never posted


def test_request_errors_plus_failed_health_check_mark_down():
    http = FakeHTTP([TimeoutError("timed out"), TimeoutError("timed out"), _yes_no_response("yes", 0.9)])
    c = _client(http, avail_ttl=60, max_failures=2)
    assert c.available()  # initial check passes
    http.up = False  # server died: the post-failure health check fails
    assert c.yesno([IMG], "q")[0] is None
    assert c.yesno([IMG], "q")[0] is None
    # two consecutive failures AND failed health check -> down: no further POSTs
    assert c.yesno([IMG], "q")[0] is None
    assert len(http.bodies) == 2


def test_request_errors_with_healthy_server_keep_trying():
    # one slow call under load must not cost the whole video: health OK -> keep using the server
    http = FakeHTTP([TimeoutError("timed out"), TimeoutError("timed out"), _yes_no_response("yes", 0.9)])
    c = _client(http, avail_ttl=60, max_failures=2)
    assert c.yesno([IMG], "q")[0] is None
    assert c.yesno([IMG], "q")[0] is None
    assert c.yesno([IMG], "q")[0] is True
    assert len(http.bodies) == 3


def test_failure_time_budget_bounds_the_batch():
    http = FakeHTTP([TimeoutError("t"), _yes_no_response("yes", 0.9)])
    c = _client(http, fail_budget_s=0.0)  # strict: no budget left -> no call at all
    c.begin_batch()
    assert c.yesno([IMG], "q")[0] is None
    assert c.yesno([IMG], "q")[0] is None  # down for the batch even though /v1/models is fine
    assert len(http.bodies) == 0


def test_malformed_json_returns_none():
    resp = {"choices": [{"message": {"content": '{"answer": '}}]}
    c = _client(FakeHTTP([resp]))
    assert c.yesno([IMG], "q")[0] is None


# ----------------------------------------------------------------------------- questions

def test_every_vlm_cue_has_one_question():
    for cue in runner.VLM_CUES:
        spec = questions.SPECS[cue]
        assert spec.prompt.strip()
        assert spec.kind in ("yesno", "count")
    assert set(runner.VLM_CUES) | set(runner.CV_CUES) == set(CUES)
    assert "door_open" in runner.CV_CUES and "door_open" not in runner.VLM_CUES
    assert questions.SPECS["person_count"].kind == "count"


# ----------------------------------------------------------------------------- CV probes

def _scene(seed=0, h=240, w=160):
    rng = np.random.default_rng(seed)
    img = rng.integers(60, 200, (h, w, 3), dtype=np.uint8)
    # some structure: stripes
    img[:, ::10] = 30
    img[::12, :] = 220
    return img


def test_overlay_mask_finds_yellow_timestamp_box():
    img = _scene()
    img[0:12, 0:60] = (0, 255, 255)  # BGR yellow box
    img[3:9, 5:55:6] = 0  # black "digits"
    m = cheap.overlay_mask(img)
    assert m[0:12, 0:60].mean() > 0.95
    assert m[100:, 100:].mean() == 0


def test_camera_blocked_detects_black_uniform_and_covered_frames():
    ref = _scene()
    assert cheap.camera_blocked(ref, ref)[0] is False
    black = np.zeros_like(ref)
    assert cheap.camera_blocked(black, ref)[0] is True
    covered = ref.copy()
    covered[:, :] = (90, 80, 70)
    covered[:30] = ref[:30]  # only a sliver of the scene left
    assert cheap.camera_blocked(covered, ref)[0] is True


def test_camera_blocked_not_fooled_by_lights_off():
    ref = _scene()
    dark = (ref.astype(np.float32) * 0.3).astype(np.uint8)  # lights off: darker, structure kept
    assert cheap.camera_blocked(dark, ref)[0] is False


def test_door_state_detects_change_in_roi_and_ignores_persons():
    ref = _scene(1)
    roi = (0.0, 0.0, 0.5, 0.5)
    same = ref.copy()
    v, conf, raw = cheap.door_state(same, ref, roi, person_boxes=[])
    assert v is False
    opened = ref.copy()
    opened[20:110, 30:70] = 15  # dark band in the door panel
    v, conf, raw = cheap.door_state(opened, ref, roi, person_boxes=[])
    assert v is True and conf > 0.5
    assert raw["change_frac"] > raw["thr"]
    # the same change covered by a person box is masked out -> closed
    v, _, _ = cheap.door_state(opened, ref, roi, person_boxes=[[0.15, 0.05, 0.5, 0.5]])
    assert v is False


def test_door_state_undecidable_when_roi_mostly_occluded():
    ref = _scene(2)
    v, conf, raw = cheap.door_state(ref, ref, (0.0, 0.0, 0.5, 0.5), person_boxes=[[0, 0, 0.5, 0.5]])
    assert v is None


def test_door_state_brightness_change_alone_is_not_open():
    ref = _scene(3)
    brighter = np.clip(ref.astype(np.int16) + 35, 0, 255).astype(np.uint8)
    assert cheap.door_state(brighter, ref, (0.0, 0.0, 0.5, 0.5), [])[0] is False


def test_coco_evidence_window_and_classes():
    tl = {"series": {"t": [0.0, 0.25, 0.5, 0.75, 5.0],
                     "objects": [[["cup", 0.1, 0.1, 0.2, 0.2, 0.8]], [["cup", 0.1, 0.1, 0.2, 0.2, 0.7]],
                                 [["cell phone", 0.3, 0.3, 0.4, 0.4, 0.9]], [], [["cup", 0.1, 0.1, 0.2, 0.2, 0.95]]]}}
    ev = cheap.coco_evidence(tl, 0.4, ("cup", "bottle"), window=0.5)
    assert ev["max_score"] == pytest.approx(0.8)
    assert ev["n_strong"] == 2  # 0.8 and 0.7 >= 0.6
    assert ev["boxes"]
    ev2 = cheap.coco_evidence(tl, 0.4, ("handbag",), window=0.5)
    assert ev2["max_score"] == 0 and ev2["n_strong"] == 0


# ----------------------------------------------------------------------------- runner

TL = {
    "frame_wh": [160, 240], "duration": 10.0,
    "zones": {"seat": [0.3, 0.3, 0.6, 0.6], "door": [0.0, 0.0, 0.3, 0.4]},
    "series": {"t": [0.0, 1.0, 2.0, 3.0, 4.0],
               "persons": [[], [[1, "cashier", [0.6, 0.6, 0.9, 0.95]]], [], [], []],
               "objects": [[], [["cup", 0.4, 0.4, 0.45, 0.45, 0.9]], [["cup", 0.4, 0.4, 0.45, 0.45, 0.85]],
                           [], []],
               "n_persons": [0, 1, 0, 0, 0]},
    "checkpoints": [],
}


class FakeVLM:
    def __init__(self, up=True, answer=True, count=2):
        self.up, self.answer, self.n = up, answer, count
        self.calls = []

    def available(self):
        return self.up

    def begin_batch(self):
        self.batches = getattr(self, "batches", 0) + 1

    def yesno(self, images, prompt, labels=None, max_tokens=None, deadline=None):
        self.deadlines = getattr(self, "deadlines", []) + [deadline]
        self.calls.append(("yesno", len(images), prompt, [im.shape for im in images]))
        if not self.up:
            return None, 0.0, {"error": "down"}
        return self.answer, 0.9, {"p_yes": 0.9}

    def count(self, images, prompt, labels=None, max_tokens=None, deadline=None):
        self.calls.append(("count", len(images), prompt, [im.shape for im in images]))
        if not self.up:
            return None, 0.0, {"error": "down"}
        return self.n, 0.8, {}


@pytest.fixture
def frames(monkeypatch):
    """Reader returning a synthetic frame per t; door 'opens' (dark band) for t >= 3."""
    reads = []

    def fake_read(path, t, transform=None):
        reads.append(t)
        img = _scene(7)
        if t >= 3:
            img[20:90, 15:40] = 10
        if transform is not None:
            img = transform(img)
        return img

    monkeypatch.setattr(runner, "read_frame_at", fake_read)
    return reads


def test_run_probes_routes_and_preserves_order(frames, monkeypatch):
    fake = FakeVLM(answer=True, count=2)
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    reqs = [ProbeRequest("cash_on_desk", 1.0), ProbeRequest("door_open", 4.0),
            ProbeRequest("person_count", 2.0), ProbeRequest("door_open", 1.0),
            ProbeRequest("camera_blocked", 2.0), ProbeRequest("not_a_cue", 1.0)]
    res = runner.run_probes("v.mov", reqs, timeline=TL, calibration={})
    assert [r.cue for r in res] == [r.cue for r in reqs]
    assert all(isinstance(r, ProbeResult) for r in res)
    assert res[0].value is True and res[0].source == "vlm"
    assert res[1].value is True and res[1].source.startswith("cv:")
    assert res[2].value is True and res[2].raw["count"] == 2
    assert res[3].value is False
    assert res[4].value is False and res[4].source.startswith("cv:")
    assert res[5].value is None
    assert [c[0] for c in fake.calls] == ["yesno", "count"]  # door never goes to the VLM
    for r in res:
        assert r.to_dict()["cue"] == r.cue


def test_run_probes_vlm_down_gives_none_but_cv_works_and_coco_fallback(frames, monkeypatch):
    fake = FakeVLM(up=False)
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    reqs = [ProbeRequest("cash_on_desk", 1.0), ProbeRequest("cup_or_food", 1.5),
            ProbeRequest("phone_visible", 1.5), ProbeRequest("door_open", 4.0)]
    res = runner.run_probes("v.mov", reqs, timeline=TL, calibration={})
    assert res[0].value is None
    assert res[1].value is True and res[1].source == "coco"  # strong cup detections near t
    assert res[2].value is None  # no phone detections -> undecidable without VLM
    assert res[3].value is True


def test_run_probes_never_raises(frames, monkeypatch):
    class Boom(FakeVLM):
        def yesno(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr(runner, "get_client", lambda: Boom())

    def bad_read(path, t, transform=None):
        raise ValueError("no frames")

    res = runner.run_probes("v.mov", [ProbeRequest("cash_on_desk", 1.0)], timeline=TL, calibration={})
    assert res[0].value is None and "error" in res[0].raw
    monkeypatch.setattr(runner, "read_frame_at", bad_read)
    res = runner.run_probes("v.mov", [ProbeRequest("door_open", 1.0), ProbeRequest("safe_open", 1.0)],
                            timeline=TL, calibration={})
    assert [r.value for r in res] == [None, None]


def test_roi_crop_and_upscale_for_laptop(frames, monkeypatch):
    fake = FakeVLM()
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    cal = {"laptop": [0.5, 0.5, 0.75, 0.75]}
    runner.run_probes("v.mov", [ProbeRequest("screen_on_unlocked", 1.0)], timeline=TL, calibration=cal)
    (_, n, _, shapes), = fake.calls
    h, w = shapes[0][:2]
    # crop of 40x60 px (plus margin) upscaled: larger than the raw crop, smaller than huge
    assert max(h, w) >= 300 and max(h, w) <= 1000
    # explicit request ROI wins over calibration
    fake.calls.clear()
    runner.run_probes("v.mov", [ProbeRequest("cash_on_desk", 1.0, roi=(0.0, 0.0, 1.0, 1.0))],
                      timeline=TL, calibration=cal)
    assert fake.calls[0][3][0][:2] == (240, 160)


def test_event_cue_sends_extra_frames_state_cue_does_not(frames, monkeypatch):
    fake = FakeVLM()
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    reqs = [ProbeRequest("id_document_shown", 2.0, extra_ts=[1.0, 1.5, 0.5]),
            ProbeRequest("cash_on_desk", 2.0, extra_ts=[1.0])]
    res = runner.run_probes("v.mov", reqs, timeline=TL, calibration={})
    assert fake.calls[0][1] == 3  # keyframe + max 2 extra
    assert fake.calls[1][1] == 1
    assert res[0].evidence_ts == [2.0, 1.0, 1.5]
    assert res[1].evidence_ts == [2.0]


def test_transform_is_passed_to_frame_reader(frames, monkeypatch):
    fake = FakeVLM()
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    flip = lambda im: im[:, ::-1]
    runner.run_probes("v.mov", [ProbeRequest("cash_on_desk", 1.0)], timeline=TL, transform=flip, calibration={})
    assert fake.calls


def test_budget_skips_remaining_vlm_calls(frames, monkeypatch):
    fake = FakeVLM()
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    monkeypatch.setattr(runner, "budget_s", lambda: 0.0)
    res = runner.run_probes("v.mov", [ProbeRequest("cash_on_desk", 1.0), ProbeRequest("door_open", 4.0)],
                            timeline=TL, calibration={})
    assert res[0].value is None and res[0].raw.get("skipped") == "budget"
    assert res[1].value is True  # CV probes are cheap and not budgeted


# ----------------------------------------------------------------------------- calibration

class FakeOV:
    model_id = "fake-ov"
    device = "cpu"

    def __init__(self):
        self.calls = 0

    def detect(self, image, queries):
        self.calls += 1
        h, w = image.shape[:2]
        # pixel boxes for two of the queries only
        return {"safe": (0.6, [0.7 * w, 0.2 * h, 0.9 * w, 0.5 * h]), "laptop": (0.3, [0.5 * w, 0.4 * h, 0.6 * w, 0.45 * h])}


def test_calibrate_normalized_boxes_and_nulls(frames, monkeypatch):
    ov = FakeOV()
    cal = runner.calibrate("v.mov", TL, detector=ov)
    assert cal["safe"] == pytest.approx([0.7, 0.2, 0.9, 0.5], abs=1e-3)
    assert cal["laptop"] == pytest.approx([0.5, 0.4, 0.6, 0.45], abs=1e-3)
    assert cal["counting_machine"] is None and cal["desk"] is None
    assert "door" in cal  # from OV or timeline fallback (here: timeline door zone)
    assert cal["door"] is not None and cal["meta"]["door_source"] == "timeline"
    assert 1 <= ov.calls <= 3
    assert cal["scores"]["safe"] == pytest.approx(0.6)
    assert cal["door_ref"] is not None and cal["door_ref"]["roi"] == cal["door"]
    # no global memo: a second call (e.g. another robustness variant) recomputes
    n = ov.calls
    runner.calibrate("v.mov", TL, transform=lambda im: im[:, ::-1], detector=ov)
    assert ov.calls > n


def test_calibrate_never_raises(frames, monkeypatch):
    class Bad(FakeOV):
        def detect(self, image, queries):
            raise RuntimeError("no model")

    cal = runner.calibrate("v2.mov", TL, detector=Bad())
    assert cal["safe"] is None and "error" in cal["meta"]


def test_door_roi_from_zone_pads_sideways_and_up_and_is_flip_symmetric():
    zone = [0.27, 0.19, 0.32, 0.30]
    r = cheap.door_roi_from_zone(zone)
    assert r[0] < zone[0] - 0.1 and r[2] > zone[2] + 0.1  # both sides (hinge side unknown)
    assert r[1] < zone[1] and r[3] > zone[3]
    assert r[2] - r[0] <= 0.45 + 1e-9
    flipped = cheap.door_roi_from_zone([1 - zone[2], zone[1], 1 - zone[0], zone[3]])
    assert flipped[0] == pytest.approx(1 - r[2]) and flipped[2] == pytest.approx(1 - r[0])


# ----------------------------------------------------------------------------- fix round 1


def test_default_timeout_and_worst_case_budget():
    c = vlm.VLMClient(url="http://x/v1/chat/completions")
    assert c.timeout == 20
    assert 2 + 2 * c.timeout + 3 <= 45 and c.fail_budget_s <= 2 * c.timeout


def test_down_flag_sticks_for_the_batch_and_resets_on_begin_batch():
    http = FakeHTTP([TimeoutError("t"), TimeoutError("t"), _yes_no_response("yes", 0.9)])
    c = _client(http, avail_ttl=60.0, max_failures=2)
    c.begin_batch()
    assert c.available()
    http.up = False  # health check after the 2 failures fails
    assert c.yesno([IMG], "q")[0] is None
    assert c.yesno([IMG], "q")[0] is None
    for _ in range(3):
        assert c.yesno([IMG], "q")[0] is None
    assert len(http.bodies) == 2  # sticky for the rest of the batch
    http.up = True
    c.begin_batch()
    assert c.yesno([IMG], "q")[0] is True


def test_server_down_at_check_sticks_for_the_batch():
    http = FakeHTTP(up=False)
    c = _client(http, avail_ttl=0.0)
    c.begin_batch()
    for _ in range(4):
        assert c.yesno([IMG], "q")[0] is None
    assert http.gets == 1


def test_run_probes_starts_a_vlm_batch(frames, monkeypatch):
    fake = FakeVLM()
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    runner.run_probes("v.mov", [ProbeRequest("cash_on_desk", 1.0)], timeline=TL, calibration={})
    runner.run_probes("v.mov", [ProbeRequest("door_open", 1.0)], timeline=TL, calibration={})
    assert fake.batches == 1  # CV-only calls do not touch the VLM


def test_no_stale_state_between_calls_or_transforms(frames, monkeypatch):
    """Each run_probes call without calibration calibrates for itself (no id()-keyed memo)."""
    calls = []

    def fake_cal(path, tl, transform=None, detector=None):
        calls.append(transform)
        return {"laptop": [0.5, 0.5, 0.75, 0.75], "door_ref": None}

    monkeypatch.setattr(runner, "calibrate", fake_cal)
    monkeypatch.setattr(runner, "get_client", lambda: FakeVLM())
    for _ in range(2):
        runner.run_probes("v.mov", [ProbeRequest("screen_on_unlocked", 1.0), ProbeRequest("safe_open", 1.0)],
                          timeline=TL, transform=lambda im: im)
    assert len(calls) == 2  # once per call, shared by the requests inside the call


def _door_tl(n=10, checkpoints=()):
    return {"frame_wh": [160, 240], "duration": float(n - 1),
            "zones": {"door": [0.0, 0.0, 0.3, 0.4]},
            "series": {"t": [float(i) for i in range(n)], "persons": [[] for _ in range(n)],
                       "objects": [[] for _ in range(n)], "n_persons": [0] * n},
            "checkpoints": list(checkpoints)}


def _door_reader(open_ts):
    def read(path, t, transform=None):
        img = _scene(7)
        if any(abs(t - o) < 1e-6 for o in open_ts):
            img[20:90, 15:40] = 10
        return img
    return read


def test_door_reference_not_fooled_by_open_door_at_start(monkeypatch):
    # a visitor enters at t=0.2 and the door is still open in the first clear frames (0-2 s)
    monkeypatch.setattr(runner, "read_frame_at", _door_reader({0.0, 1.0, 2.0}))
    tl = _door_tl(10, [{"type": "person_enters", "t": 0.2, "track_ids": [2]}])
    res = runner.run_probes("v.mov", [ProbeRequest("door_open", 8.0), ProbeRequest("door_open", 1.0)],
                            timeline=tl, calibration={})
    assert res[0].value is False and res[1].value is True
    assert res[0].raw["ref_t"] >= 3.0


def test_door_reference_uses_track_births_when_no_checkpoint(monkeypatch):
    # a visitor's track is born at the door at t=0.6 (not promoted to a person_enters checkpoint);
    # the door is open in the clear frames right after (1-2 s) -> its birth window (-0.5..+4 s)
    # keeps them out of the reference, which comes from the closed frames
    monkeypatch.setattr(runner, "read_frame_at", _door_reader({1.0, 2.0}))
    tl = _door_tl(10)
    tl["tracks"] = [{"id": 2, "t": [0.6, 1.0, 9.0], "boxes": [[0.05, 0.05, 0.15, 0.3]] * 3}]
    res = runner.run_probes("v.mov", [ProbeRequest("door_open", 6.0), ProbeRequest("door_open", 1.0)],
                            timeline=tl, calibration={})
    assert [r.value for r in res] == [False, True]
    cands = res[0].raw["ref_candidates"]
    assert cands and not any(0.1 <= t <= 4.6 for t in cands)  # birth window excluded
    assert res[0].raw["ref_t"] not in (1.0, 2.0)


def test_door_abstains_when_open_at_start_is_unexplained(monkeypatch):
    # N1: door open in the first frames (0-2 s), closed afterwards, and NOTHING explains it (the
    # visitor's track is born at t_first, so it is no event): the earliest cluster (open) is neither
    # the largest nor followed by a door event -> abstain instead of inverting every decision
    monkeypatch.setattr(runner, "read_frame_at", _door_reader({0.0, 1.0, 2.0}))
    tl = _door_tl(10)
    tl["tracks"] = [{"id": 2, "t": [0.0, 1.0, 9.0], "boxes": [[0.05, 0.05, 0.15, 0.3]] * 3}]
    res = runner.run_probes("v.mov", [ProbeRequest("door_open", 6.0), ProbeRequest("door_open", 1.0)],
                            timeline=tl, calibration={})
    assert [r.value for r in res] == [None, None]
    assert res[0].raw["why"] == "ambiguous_reference" and res[0].raw["ref_why"].startswith("ambiguous")


def test_door_abstains_when_only_an_unrelated_later_event_exists(monkeypatch):
    # open at 0-2 s, closed afterwards, the only door event is an unrelated exit at t=50
    monkeypatch.setattr(runner, "read_frame_at", _door_reader({0.0, 1.0, 2.0}))
    tl = _door_tl(60, [{"type": "person_exits", "t": 50.0, "track_ids": [3]}])
    res = runner.run_probes("v.mov", [ProbeRequest("door_open", 30.0)], timeline=tl, calibration={})
    assert res[0].value is None and res[0].raw["ref_why"].startswith("ambiguous")


def test_door_explained_by_event_in_the_transition_gap(monkeypatch):
    # closed 0-1 s, a person enters at 2.5 s (door used), open from 3 s on: the event in the gap
    # explains the change -> the earliest (closed) cluster is trusted even though it is the minority
    monkeypatch.setattr(runner, "read_frame_at", _door_reader({float(i) for i in range(3, 20)}))
    tl = _door_tl(20, [{"type": "person_enters", "t": 2.5, "track_ids": [2]}])
    res = runner.run_probes("v.mov", [ProbeRequest("door_open", 15.0), ProbeRequest("door_open", 0.0)],
                            timeline=tl, calibration={})
    assert [r.value for r in res] == [True, False]


def test_door_explained_by_person_at_door_in_the_gap(monkeypatch):
    # X-30/X-33 pattern: closed 0-1 s; the cashier (track born at t=0 behind the glass, so no event)
    # walks through the door at 2 s; open from 3 s on -> the person in the gap explains the change
    monkeypatch.setattr(runner, "read_frame_at", _door_reader({float(i) for i in range(3, 20)}))
    tl = _door_tl(20)
    tl["series"]["persons"][2] = [[1, "cashier", [0.02, 0.02, 0.28, 0.4]]]  # in the doorway (zone)
    res = runner.run_probes("v.mov", [ProbeRequest("door_open", 15.0), ProbeRequest("door_open", 0.0)],
                            timeline=tl, calibration={})
    assert [r.value for r in res] == [True, False]


def _door_tl_step(n, step):
    tl = _door_tl(n)
    tl["series"]["t"] = [round(i * step, 3) for i in range(n)]
    tl["duration"] = (n - 1) * step
    return tl


def test_door_weak_person_overlap_in_gap_does_not_explain(monkeypatch):
    # inversion guard: door OPEN 0-2 s, someone grazes the padded door ROI (occ ~0.09, beside the
    # doorway - e.g. behind the glass) at 2.5 s, CLOSED from 3 s on. Trusting the earliest (open)
    # minority cluster would invert every decision -> abstain
    monkeypatch.setattr(runner, "read_frame_at", _door_reader({0.0, 0.5, 1.0, 1.5, 2.0}))
    tl = _door_tl_step(40, 0.5)
    roi = cheap.door_roi_from_zone(tl["zones"]["door"])
    box = [0.33, 0.1, 0.375, 0.5]
    assert 0.05 <= runner._overlap_frac(roi, [box]) < 0.15
    tl["series"]["persons"][5] = [[3, "visitor", box]]  # t = 2.5
    res = runner.run_probes("v.mov", [ProbeRequest("door_open", 10.0), ProbeRequest("door_open", 1.0)],
                            timeline=tl, calibration={})
    assert [r.value for r in res] == [None, None]
    assert res[0].raw["why"] == "ambiguous_reference"


def test_door_transition_gap_wider_than_cap_abstains(monkeypatch):
    # closed 0-1 s, the doorway is fully covered 2-7 s (no usable frames), open from 8 s: the change
    # happened somewhere in a 7 s gap -> too wide to tie it to the door use there -> abstain
    monkeypatch.setattr(runner, "read_frame_at", _door_reader({float(i) for i in range(8, 20)}))
    tl = _door_tl(20)
    for i in range(2, 8):
        tl["series"]["persons"][i] = [[1, "cashier", [0.0, 0.0, 0.375, 0.6]]]
    res = runner.run_probes("v.mov", [ProbeRequest("door_open", 15.0)], timeline=tl, calibration={})
    assert res[0].value is None and res[0].raw["why"] == "ambiguous_reference"
    assert res[0].raw["ref_gap"][1] - res[0].raw["ref_gap"][0] > runner.DOOR_GAP_MAX


def test_door_gap_is_narrowed_by_dense_frames(monkeypatch):
    # X-32 pattern with a sparse pool: the birth window excludes 0.5-5 s from the candidates (gap
    # 0 -> 6 s), but the frames inside it locate the change at 1 -> 2 s: explained, decides
    monkeypatch.setattr(runner, "read_frame_at", _door_reader({float(i) for i in range(2, 10)}))
    tl = _door_tl(10)
    tl["tracks"] = [{"id": 1, "t": [1.0, 2.0, 9.0], "boxes": [[0.05, 0.05, 0.15, 0.3]] * 3}]
    (r,) = runner.run_probes("v.mov", [ProbeRequest("door_open", 8.0)], timeline=tl, calibration={})
    assert r.value is True and r.raw["ref_gap"] == [1.0, 2.0]


def test_deadline_skips_vlm_but_runs_cheap_cv(frames, monkeypatch):
    import time as _time

    fake = FakeVLM()
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    reqs = [ProbeRequest("cash_on_desk", 1.0), ProbeRequest("camera_blocked", 2.0),
            ProbeRequest("door_open", 4.0), ProbeRequest("person_count", 2.0)]
    past = _time.monotonic() - 1.0
    res = runner.run_probes("v.mov", reqs, timeline=TL, calibration={}, deadline=past)
    assert fake.calls == []  # no VLM call after the deadline
    assert res[0].value is None and res[0].raw["why"] == "deadline"
    assert res[3].value is None and res[3].raw["why"] == "deadline"
    assert res[1].value is False  # camera_blocked: cheap, still runs
    # door: reference unknown (not in calibration) -> would read ~10 frames -> skipped
    assert res[2].value is None and res[2].raw["why"] == "deadline"
    # with the reference precomputed in the calibration the door probe is cheap and runs
    cal = {"door_ref": runner.door_reference(runner._Frames("v.mov", None), TL,
                                             cheap.door_roi_from_zone(TL["zones"]["door"]))}
    res = runner.run_probes("v.mov", [ProbeRequest("door_open", 4.0)], timeline=TL, calibration=cal, deadline=past)
    assert res[0].value is True
    # a future deadline changes nothing
    res = runner.run_probes("v.mov", reqs[:1], timeline=TL, calibration={}, deadline=_time.monotonic() + 60)
    assert res[0].value is True and len(fake.calls) == 1


def test_door_tie_between_clusters_abstains(monkeypatch):
    # open 0-4 s, closed 5-9 s, no events: 5 vs 5 frames -> no majority, nothing explains it
    monkeypatch.setattr(runner, "read_frame_at", _door_reader({0.0, 1.0, 2.0, 3.0, 4.0}))
    res = runner.run_probes("v.mov", [ProbeRequest("door_open", 8.0)], timeline=_door_tl(10), calibration={})
    assert res[0].value is None


def test_door_earliest_cluster_trusted_when_largest(monkeypatch):
    # closed for most of the video, a short open spell later, no events -> earliest = largest: decide
    monkeypatch.setattr(runner, "read_frame_at", _door_reader({7.0, 8.0}))
    res = runner.run_probes("v.mov", [ProbeRequest("door_open", 7.0), ProbeRequest("door_open", 3.0)],
                            timeline=_door_tl(10), calibration={})
    assert [r.value for r in res] == [True, False]


def test_large_desk_box_uses_full_frame_prompt(frames, monkeypatch):
    fake = FakeVLM()
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    runner.run_probes("v.mov", [ProbeRequest("cash_on_desk", 1.0)], timeline=TL,
                      calibration={"desk": [0.0, 0.1, 1.0, 0.95]})  # huge box -> full frame
    (_, _, prompt, _), = fake.calls
    assert prompt == questions.SPECS["cash_on_desk"].full_prompt


def test_count_grammar_is_clamped_to_0_20():
    g = vlm.COUNT_GRAMMAR
    assert '"20"' in g and '"1" [0-9]' in g and "[0-9] [0-9]?" not in g


def test_door_reference_medoid_outvotes_a_noisy_frame(monkeypatch):
    # closed throughout, but one candidate frame (t=6) has a passer-by-like blob: not chosen as ref
    def read(path, t, transform=None):
        img = _scene(7)
        if abs(t - 6.0) < 1e-6:
            img[30:60, 20:35] = 240
        return img

    monkeypatch.setattr(runner, "read_frame_at", read)
    (r,) = runner.run_probes("v.mov", [ProbeRequest("door_open", 8.0)], timeline=_door_tl(10), calibration={})
    assert r.value is False and r.raw["ref_t"] != 6.0


def test_door_reference_prefers_pre_entry_frames(monkeypatch):
    # X-32 pattern: closed before the cashier enters (t=0), open for most of the video afterwards
    monkeypatch.setattr(runner, "read_frame_at", _door_reader({float(i) for i in range(4, 10)}))
    tl = _door_tl(10, [{"type": "cashier_enters_room", "t": 1.5, "track_ids": [1]}])
    res = runner.run_probes("v.mov", [ProbeRequest("door_open", 8.0)], timeline=tl, calibration={})
    assert res[0].value is True and res[0].raw["ref_t"] == 0.0


def test_door_tie_with_undecided_keyframe_is_none(monkeypatch):
    monkeypatch.setattr(runner, "read_frame_at", _door_reader({5.0}))
    tl = _door_tl(10)
    tl["series"]["persons"][2] = [[9, "visitor", [0.0, 0.0, 0.5, 0.5]]]  # keyframe t=2 fully occluded
    res = runner.run_probes("v.mov", [ProbeRequest("door_open", 2.0, extra_ts=[5.0, 6.0])],
                            timeline=tl, calibration={})
    # votes: None (occluded), open, closed -> tie among decided with an undecided keyframe
    assert res[0].value is None


def test_camera_blocked_very_dark_room_is_not_blocked():
    ref = _scene()
    very_dark = (ref.astype(np.float32) * 0.03).astype(np.uint8)  # std < 4 but structure kept
    assert very_dark.std() < 4
    assert cheap.camera_blocked(very_dark, ref)[0] is False
    assert cheap.camera_blocked(np.zeros_like(ref), ref)[0] is True


@pytest.mark.parametrize("text,kind,exp", [
    ('{"answer": "yes"}', "yesno", "yes"),
    ('{\n\n\n\n\n\n  "answer": "no"\n}', "yesno", "no"),
    ('Sure. {"answer": "YES"} done', "yesno", "yes"),
    ('{\n\n\n\n\n\n  "answer": ', "yesno", None),  # truncated, nothing to recover
    ('"answer":\n  "no', "yesno", "no"),  # truncated JSON, regex fallback
    ('{"num_people": 2}', "count", 2),
    ('{ "num_people" : 0', "count", 0),
    ('{"num_people": "x"}', "count", None),
])
def test_parse_answer_is_tolerant(text, kind, exp):
    assert vlm.parse_answer(text, kind) == exp


def test_unparseable_text_is_kept_in_raw():
    resp = {"choices": [{"message": {"content": "\n\n\n\n"}}]}
    val, conf, raw = _client(FakeHTTP([resp])).yesno([IMG], "q")
    assert val is None and raw["error"] == "bad_answer" and raw["text"] == "\n\n\n\n"


def test_door_reference_x32_pattern_track_born_after_closed_first_frame(monkeypatch):
    # X-32: closed at t=0, the cashier's track is born at the door at 1.0 s (seen through the glass
    # before stepping in), the door is left open from ~2 s to the end -> open, not inverted
    monkeypatch.setattr(runner, "read_frame_at", _door_reader({float(i) for i in range(2, 10)}))
    tl = _door_tl(10)
    tl["tracks"] = [{"id": 1, "t": [1.0, 2.0, 9.0], "boxes": [[0.05, 0.05, 0.15, 0.3]] * 3}]
    res = runner.run_probes("v.mov", [ProbeRequest("door_open", 8.0), ProbeRequest("door_open", 0.0)],
                            timeline=tl, calibration={})
    assert [r.value for r in res] == [True, False]
    assert res[0].raw["ref_t"] == 0.0


def test_busy_server_check_timeout_is_not_down():
    class Busy(FakeHTTP):
        def get(self, url, timeout):
            self.gets += 1
            raise TimeoutError("timed out")

    http = Busy([_yes_no_response("yes", 0.9)])
    c = _client(http)
    c.begin_batch()
    assert c.yesno([IMG], "q")[0] is True


def test_desk_cues_use_desk_crop_or_full_frame_prompt(frames, monkeypatch):
    fake = FakeVLM()
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    runner.run_probes("v.mov", [ProbeRequest("cash_on_desk", 1.0)], timeline=TL,
                      calibration={"desk": [0.1, 0.4, 0.7, 0.65]})
    (_, _, prompt, shapes), = fake.calls
    assert prompt == questions.SPECS["cash_on_desk"].prompt and max(shapes[0][:2]) > 240  # upscaled crop (4x cap on this tiny frame)
    fake.calls.clear()
    runner.run_probes("v.mov", [ProbeRequest("cash_on_desk", 1.0)], timeline=TL, calibration={})
    (_, _, prompt, shapes), = fake.calls
    assert prompt == questions.SPECS["cash_on_desk"].full_prompt and shapes[0][:2] == (240, 160)


def test_count_uses_strict_grammar():
    http = FakeHTTP([{"choices": [{"message": {"content": '{"num_people": 1}'}}]}])
    _client(http).count([IMG], "q")
    assert http.bodies[0]["grammar"] == vlm.COUNT_GRAMMAR


# ----------------------------------------------------------------------------- final review I2 / M5


class TimedHTTP(FakeHTTP):
    """POST sleeps min(its timeout, `sleep`) then raises a timeout (a hung server), recording timeouts."""

    def __init__(self, sleep, **kw):
        super().__init__(**kw)
        self.sleep, self.timeouts = sleep, []

    def post(self, url, body, timeout):
        self.bodies.append(body)
        self.timeouts.append(timeout)
        time.sleep(min(timeout, self.sleep))
        raise TimeoutError("timed out")


def test_request_timeout_is_capped_by_the_deadline():
    http = TimedHTTP(sleep=0.0)
    c = _client(http, timeout=20.0)
    c.begin_batch()
    c.yesno([IMG], "q", deadline=time.monotonic() + 5.0)
    assert http.timeouts and http.timeouts[0] <= 4.0 + 1e-3  # deadline - now - 1 s, not 20 s


def test_call_skipped_when_too_close_to_the_deadline():
    http = TimedHTTP(sleep=0.0)
    c = _client(http, timeout=20.0)
    c.begin_batch()
    val, conf, raw = c.yesno([IMG], "q", deadline=time.monotonic() + 2.0)  # 1 s left < 1.5 s
    assert val is None and raw.get("why") == "deadline" and raw.get("skipped") == "deadline"
    assert http.bodies == [] and not raw.get("error")
    n, _, raw = c.count([IMG], "q", deadline=time.monotonic() - 1)
    assert n is None and raw.get("why") == "deadline" and http.bodies == []


def test_failure_budget_is_strict():
    """Prior edge: two failures just under the budget + a passing health check allowed a third full
    timeout. Now each call's timeout is capped by the budget left, and no call starts below the
    minimum, so failed calls never take more than the budget in total."""
    http = TimedHTTP(sleep=0.6)
    c = _client(http, timeout=20.0, fail_budget_s=1.0, max_failures=2)
    c.min_call_s = 0.1
    c.begin_batch()
    t0 = time.perf_counter()
    for _ in range(4):
        assert c.yesno([IMG], "q")[0] is None
    spent = time.perf_counter() - t0
    assert http.timeouts[0] <= 1.0 + 1e-6 and http.timeouts[1] <= 0.45  # 1.0 - ~0.6 already failed
    assert len(http.bodies) == 2  # budget used up -> down, no third call
    assert spent < 1.0 + 0.3  # failed time <= budget (+ the fake's health checks / overhead)


def test_default_worst_case_per_batch_is_bounded():
    c = vlm.VLMClient(url="http://x/v1/chat/completions")
    # 2 s availability check + failed calls capped at the budget + one <= 3 s health check
    assert 2 + c.fail_budget_s + 3 <= 45


def _slow_server(delay):
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # /v1/models
            self._send({"data": [{"id": "slow"}]})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            time.sleep(delay)
            try:
                self._send(_yes_no_response("yes", 0.9))
            except OSError:
                pass

        def _send(self, d):
            b = __import__("json").dumps(d).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_real_http_slow_server_respects_deadline_and_ignores_proxy(monkeypatch):
    """Real urllib against a local server that answers in 3 s: with 2.6 s to the deadline the call
    gives up after ~1.6 s (not the 20 s default). http_proxy points at a dead port: 127.0.0.1 must
    not go through it (M5)."""
    for k in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    srv = _slow_server(3.0)
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions"
        c = vlm.VLMClient(url=url, timeout=20.0)
        c.begin_batch()
        assert c.available() and c.model == "slow"  # GET via the direct opener, not the proxy
        t0 = time.monotonic()
        val, _, raw = c.yesno([IMG], "q", deadline=t0 + 2.6)
        assert val is None and "error" in raw
        assert time.monotonic() - t0 < 2.5
    finally:
        srv.shutdown()
        srv.server_close()


def test_runner_passes_deadline_to_the_vlm_client(frames, monkeypatch):
    fake = FakeVLM()
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    dl = time.monotonic() + 100
    runner.run_probes("v.mov", [ProbeRequest("id_document_shown", 5.0)], timeline=TL, deadline=dl)
    assert fake.deadlines == [dl]
