import pytest

from eval.run_eval import compute_metrics

LABELS = {
    "A": {"file": "A.mov", "violation": True, "label_sec": [10.0], "hyp_types": ["T1"], "duration_sec": 20.0},
    "B": {"file": "B.mov", "violation": True, "label_sec": [57.0, 68.0], "hyp_types": ["T2"], "duration_sec": 70.0},
    "C": {"file": "C.mov", "violation": True, "label_sec": [5.0], "hyp_types": ["T5", "T6"], "duration_sec": 10.0},
    "D": {"file": "D.mov", "violation": False, "label_sec": [], "hyp_types": [], "duration_sec": 30.0},
    "E": {"file": "E.mov", "violation": False, "label_sec": [], "hyp_types": [], "duration_sec": 20.0},
    "F": {"file": "F.mov", "violation": True, "label_sec": [3.0], "hyp_types": ["T9"], "duration_sec": 10.0},
}


def ev(t, typ="T99"):
    return {"type": typ, "timecode_sec": t, "confidence": 0.9, "description": "", "evidence_ts": [t],
            "source": "test", "extra": {}}


def res(vid, verdict, events=(), runtime=1.0, stage_times=None):
    return {"video": vid, "verdict": verdict, "events": list(events), "timeline": {}, "calibration": {},
            "runtime_sec": runtime, "stage_times": stage_times or {}}


RESULTS = {
    # TP, best |dt| = 2 (event at 12 vs 10; the far event at 1 is ignored), right type
    "A": res("A", "violation", [ev(1.0, "T3"), ev(12.0, "T1")], runtime=2.0, stage_times={"decode": 1.5}),
    # TP, two label seconds: best over events x labels -> |64-68| = 4; wrong type
    "B": res("B", "violation", [ev(64.0, "T6")], runtime=7.0),
    # FN: no events counted
    "C": res("C", "no_violation", [], runtime=1.0),
    # TN
    "D": res("D", "no_violation", [], runtime=3.0),
    # FP
    "E": res("E", "violation", [ev(5.0, "T6")], runtime=2.0),
    # TP with |dt| = 7 (> 5s), type T9 matches
    "F": res("F", "violation", [ev(10.0, "T9")], runtime=1.0),
}


def test_confusion_and_rates():
    m = compute_metrics(LABELS, RESULTS)
    assert m["n_videos"] == 6
    assert m["confusion"] == {"tp": 3, "fp": 1, "tn": 1, "fn": 1}
    assert m["accuracy"] == pytest.approx(4 / 6)
    assert m["recall"] == pytest.approx(3 / 4)
    assert m["precision"] == pytest.approx(3 / 4)
    assert m["fp_rate"] == pytest.approx(1 / 2)


def test_per_video_timing():
    pv = compute_metrics(LABELS, RESULTS)["per_video"]
    assert pv["A"]["outcome"] == "TP" and pv["A"]["best_dt"] == pytest.approx(2.0)
    assert pv["A"]["hit3"] and pv["A"]["hit5"]
    assert pv["B"]["best_dt"] == pytest.approx(4.0)
    assert not pv["B"]["hit3"] and pv["B"]["hit5"]
    assert pv["C"]["outcome"] == "FN" and pv["C"]["best_dt"] is None and not pv["C"]["hit5"]
    assert pv["F"]["best_dt"] == pytest.approx(7.0) and not pv["F"]["hit5"]
    assert pv["D"]["best_dt"] is None and pv["D"]["hit3"] is None  # clean videos: n/a
    assert pv["E"]["outcome"] == "FP"


def test_aggregate_timing():
    m = compute_metrics(LABELS, RESULTS)
    # hit rates over all 4 positives (FN counts as miss)
    assert m["hit3_rate"] == pytest.approx(1 / 4)
    assert m["hit5_rate"] == pytest.approx(2 / 4)
    # |dt| stats over positives that have a best event: 2, 4, 7
    assert m["mean_abs_dt"] == pytest.approx(13 / 3)
    assert m["median_abs_dt"] == pytest.approx(4.0)


def test_types():
    m = compute_metrics(LABELS, RESULTS)
    pv = m["per_video"]
    assert pv["A"]["pred_types"] == ["T1", "T3"] and pv["A"]["type_hit"] is True
    assert pv["B"]["pred_types"] == ["T6"] and pv["B"]["type_hit"] is False
    assert pv["C"]["type_hit"] is False
    assert pv["D"]["type_hit"] is None
    assert pv["E"]["type_hit"] is None
    # type-hit rate over TP videos only: A yes, B no, F yes
    assert m["type_hit_rate"] == pytest.approx(2 / 3)


def test_runtime():
    m = compute_metrics(LABELS, RESULTS)
    r = m["runtime"]
    assert r["total_sec"] == pytest.approx(16.0)
    assert r["mean_sec"] == pytest.approx(16.0 / 6)
    assert r["max_sec"] == pytest.approx(7.0)
    assert r["max_video"] == "B"
    assert r["sec_per_video_sec"] == pytest.approx(16.0 / 160.0)
    pv = m["per_video"]["A"]
    assert pv["runtime_sec"] == 2.0 and pv["stage_times"] == {"decode": 1.5}
    assert pv["duration_sec"] == 20.0


def test_only_evaluated_videos_count():
    m = compute_metrics(LABELS, {"A": RESULTS["A"], "D": RESULTS["D"]})
    assert m["n_videos"] == 2
    assert m["confusion"] == {"tp": 1, "fp": 0, "tn": 1, "fn": 0}
    assert m["fp_rate"] == 0.0


def test_empty_denominators_are_none():
    m = compute_metrics(LABELS, {"D": RESULTS["D"]})
    assert m["recall"] is None and m["precision"] is None
    assert m["hit3_rate"] is None and m["mean_abs_dt"] is None and m["type_hit_rate"] is None
    assert m["fp_rate"] == 0.0 and m["accuracy"] == 1.0


def test_degraded_counts_as_error():
    results = {"A": res("A", "violation", [ev(10.0, "T1")]), "D": res("D", "no_violation")}
    results["A"]["degraded"] = results["D"]["degraded"] = True
    m = compute_metrics(LABELS, results)
    assert m["confusion"] == {"tp": 0, "fp": 1, "tn": 0, "fn": 1}
    assert m["n_degraded"] == 2 and m["per_video"]["D"]["outcome"] == "ERR"
    assert "degraded" in m["per_video"]["D"]["error"]


def test_errors_count_as_wrong():
    results = {"A": {"video": "A", "error": "boom", "runtime_sec": 0.5},
               "D": {"video": "D", "error": "boom", "runtime_sec": 0.5}}
    m = compute_metrics(LABELS, results)
    assert m["n_errors"] == 2
    assert m["confusion"] == {"tp": 0, "fp": 1, "tn": 0, "fn": 1}  # an error is never a correct negative
    assert m["per_video"]["A"]["error"] == "boom" and m["per_video"]["D"]["outcome"] == "ERR"
    assert m["accuracy"] == 0.0


def test_headline_and_per_type():
    m = compute_metrics(LABELS, RESULTS)
    assert m["headline"]["recall"] == "3/4" and m["headline"]["fp"] == "1/2"
    assert m["per_type"]["T1"] == {"n": 1, "detected": 1, "type_hit": 1, "videos": ["A"], "hit3": 1, "hit5": 1}
    assert m["per_type"]["T2"]["type_hit"] == 0
    assert m["types_fired"]["T6"] == {"tp_videos": 1, "fp_videos": 1}
    # primary = first hyp type: A (T1) yes, B (T2) no, F (T9) yes
    assert m["per_video"]["A"]["primary_hit"] is True and m["per_video"]["D"]["primary_hit"] is None
    assert m["primary_hit_rate"] == pytest.approx(2 / 3)


def test_select_videos_normalizes_ids():
    from eval.run_eval import select_videos

    labels = {"X-4": {}, "X-47": {}, "X-23": {}, "X-29": {}}
    assert select_videos(labels, "Х-4, x-47", dedupe=False) == ["X-4", "X-47"]
    assert select_videos(labels, "X-4,X-47", dedupe=True) == ["X-4"]
    assert set(select_videos(labels, "hard", dedupe=False)) == {"X-23", "X-29"}
    with pytest.raises(SystemExit):
        select_videos(labels, "X-999", dedupe=False)


def test_hard_negative_report():
    from eval.hard_negatives import hard_negative_report

    pv = {"X-23": {"outcome": "FP", "pred_types": ["T4"]}, "X-29": {"outcome": "TN", "pred_types": []}}
    r = hard_negative_report(pv)
    assert r["n_videos"] == 2 and r["n_fp"] == 1
    cat = r["categories"]["bright screen (lock screen / wallpaper)"]
    assert cat["fp"] == 1 and cat["n"] == 2


def test_report_renders_headline_first():
    from eval.run_eval import render_report

    m = compute_metrics(LABELS, RESULTS)
    cfg = {"analyzer": "x", "videos": "all", "n_videos": 6, "dedupe": False, "started": "now", "total_wall_sec": 1.0}
    text = render_report({"config": cfg, "variants": {"orig": m}})
    assert text.index("recall 3/4, FP 1/2") < text.index("## Variants")
    assert "Hard negatives" in text and "same fold" in text
