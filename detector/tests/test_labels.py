import pytest

from forte_detector.labels import (
    HYP_TYPES,
    load_labels,
    merge_duplicate_seconds,
    normalize_id,
    parse_label_seconds,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, []),
        ("", []),
        ("22 секунда", [22.0]),
        ("7 секунда", [7.0]),
        ("1минута 8 секунда", [68.0]),
        ("1 мин 8 сек", [68.0]),
        ("2 минуты", [120.0]),
        ("0:57", [57.0]),
        ("1:08", [68.0]),
        ("57", [57.0]),
        ("12.5 сек", [12.5]),
        (15, [15.0]),
        (15.5, [15.5]),
    ],
)
def test_parse_label_seconds(raw, expected):
    assert parse_label_seconds(raw) == expected


def test_parse_label_seconds_rejects_garbage():
    with pytest.raises(ValueError):
        parse_label_seconds("около середины")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("X-1", "X-1"),
        ("Х-15", "X-15"),  # Cyrillic Х
        ("х-15", "X-15"),  # lowercase Cyrillic х
        (" Х-4.mov ", "X-4"),
        ("x-2", "X-2"),
    ],
)
def test_normalize_id(raw, expected):
    assert normalize_id(raw) == expected


def test_merge_duplicate_seconds():
    labels = {
        "X-4": {"label_sec": [57.0]},
        "X-47": {"label_sec": [68.0]},
        "X-5": {"label_sec": []},
        "X-48": {"label_sec": []},
    }
    merge_duplicate_seconds(labels)
    assert labels["X-4"]["label_sec"] == [57.0, 68.0]
    assert labels["X-47"]["label_sec"] == [57.0, 68.0]
    assert labels["X-5"]["label_sec"] == []


def test_committed_labels_json():
    labels = load_labels()
    assert len(labels) == 42
    assert sum(v["violation"] for v in labels.values()) == 22
    for vid, v in labels.items():
        assert set(v) == {"file", "violation", "label_sec", "hyp_types", "duration_sec"}
        assert v["file"].endswith(".mov")
        assert normalize_id(v["file"]) == vid
        assert v["duration_sec"] > 0
        if v["violation"]:
            assert v["label_sec"], vid
            assert v["hyp_types"] == HYP_TYPES[vid]
        else:
            assert v["label_sec"] == [] and v["hyp_types"] == []
    assert labels["X-4"]["label_sec"] == labels["X-47"]["label_sec"] == [57.0, 68.0]
    assert labels["X-15"]["file"] == "Х-15.mov"  # original Cyrillic filename kept
    assert labels["X-1"]["file"] == "X-1.mov"


def test_hyp_types_cover_exactly_the_positives():
    labels = load_labels()
    positives = {k for k, v in labels.items() if v["violation"]}
    assert set(HYP_TYPES) == positives
