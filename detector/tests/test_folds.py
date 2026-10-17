from eval.folds import FOLDS, cross_validate, tune_threshold, video_score
from forte_detector.labels import load_labels


def test_folds_cover_all_videos_once_and_keep_duplicates_together():
    labels = load_labels()
    a, b = set(FOLDS["A"]), set(FOLDS["B"])
    assert not a & b and a | b == set(labels)
    for x, y in (("X-4", "X-47"), ("X-5", "X-48")):
        assert (x in a) == (y in a)
    for f in (a, b):
        assert sum(labels[v]["violation"] for v in f) == 11
        assert sum(not labels[v]["violation"] for v in f) == 10


def test_video_score_includes_low_conf_and_errors_zero():
    r = {"events": [{"confidence": 0.5}], "timeline": {"low_conf_events": [{"confidence": 0.7}]}}
    assert video_score(r) == 0.7
    assert video_score({"error": "x"}) == 0.0


def test_threshold_tuning_and_cv():
    labels = {"p1": {"violation": True}, "p2": {"violation": True}, "n1": {"violation": False}}
    scores = {"p1": 0.8, "p2": 0.5, "n1": 0.6}
    assert tune_threshold(labels, scores, ["p1", "p2", "n1"]) == 0.5  # recall first
    assert tune_threshold(labels, scores, ["p1", "n1"]) == 0.8  # then fewest FP
    lab = load_labels()
    res = {v: {"events": [{"confidence": 0.9 if lab[v]["violation"] else 0.1}]} for v in lab}
    cv = cross_validate(lab, res)
    assert cv["A"]["recall"] == "11/11" and cv["B"]["fp"] == "0/10" and cv["pooled"]["recall"] == "22/22"
