"""Track merging on synthetic tracks (no GPU, no video)."""
from forte_detector.perception.tracker import Track, merge_tracks


def _track(tid, t0, t1, box0, box1=None, fps=4.0):
    box1 = box1 or box0
    n = int(round((t1 - t0) * fps)) + 1
    ts, boxes = [], []
    for i in range(n):
        a = i / max(n - 1, 1)
        ts.append(round(t0 + i / fps, 3))
        boxes.append([b0 + a * (b1 - b0) for b0, b1 in zip(box0, box1)])
    return Track(tid, ts, boxes, [0.8] * n)


SEAT = [0.40, 0.45, 0.60, 0.70]
DOOR = [0.05, 0.10, 0.20, 0.40]


def test_fragment_with_short_gap_nearby_is_merged():
    a = _track(1, 0.0, 5.0, SEAT)
    b = _track(7, 6.0, 10.0, [0.42, 0.46, 0.62, 0.71])
    out = merge_tracks([a, b], max_gap=2.0)
    assert len(out) == 1
    m = out[0]
    assert m.id == 1 and m.t[0] == 0.0 and m.t[-1] == 10.0
    assert m.t == sorted(m.t)
    assert m.members == [1, 7]


def test_long_gap_not_merged():
    a = _track(1, 0.0, 5.0, SEAT)
    b = _track(2, 8.0, 10.0, SEAT)
    assert len(merge_tracks([a, b], max_gap=2.0)) == 2


def test_far_apart_not_merged():
    a = _track(1, 0.0, 5.0, SEAT)
    b = _track(2, 5.5, 10.0, DOOR)
    assert len(merge_tracks([a, b], max_gap=2.0)) == 2


def test_temporal_overlap_not_merged():
    a = _track(1, 0.0, 5.0, SEAT)
    b = _track(2, 3.0, 8.0, SEAT)
    assert len(merge_tracks([a, b], max_gap=2.0)) == 2


def test_fragment_joins_nearest_of_two_concurrent_people():
    cashier = _track(1, 0.0, 10.0, SEAT)
    visitor = _track(2, 0.0, 4.0, [0.10, 0.20, 0.25, 0.50])
    frag = _track(3, 4.75, 10.0, [0.11, 0.21, 0.26, 0.51])
    out = {t.id: t for t in merge_tracks([cashier, visitor, frag], max_gap=2.0)}
    assert set(out) == {1, 2}
    assert out[2].members == [2, 3]
    assert out[1].members == [1]


def test_chain_of_three_fragments():
    a = _track(5, 0.0, 2.0, SEAT)
    b = _track(9, 3.0, 5.0, SEAT)
    c = _track(12, 6.0, 9.0, SEAT)
    out = merge_tracks([c, a, b], max_gap=2.0)
    assert len(out) == 1 and out[0].members == [5, 9, 12]


def test_moving_person_gap_allows_motion():
    # person walks left; tracker loses them for 1.5 s while they keep walking
    a = _track(1, 0.0, 3.0, [0.60, 0.40, 0.75, 0.70], [0.45, 0.40, 0.60, 0.70])
    b = _track(2, 4.5, 6.0, [0.30, 0.40, 0.45, 0.70], [0.22, 0.40, 0.37, 0.70])
    assert len(merge_tracks([a, b], max_gap=2.0)) == 1


def test_track_roundtrip_dict():
    a = _track(1, 0.0, 1.0, SEAT)
    assert Track.from_dict(a.to_dict()) == a


def test_suppress_contained_detections():
    from forte_detector.perception.tracker import suppress_contained

    big = [0.40, 0.40, 0.60, 0.80, 0.9]
    arm = [0.50, 0.50, 0.58, 0.60, 0.4]  # fully inside the big box, lower score
    other = [0.10, 0.10, 0.20, 0.40, 0.5]
    assert suppress_contained([arm, big, other]) == [big, other]


def test_bytetrack_keeps_id_through_missed_detections():
    # 4 fps; the person is missed for 2 samples (< 3 misses) -> must keep the same ByteTrack id
    from forte_detector.perception.tracker import run_bytetrack

    times = [i / 4 for i in range(24)]
    box = [0.40, 0.40, 0.60, 0.80, 0.9]
    dets = [[] if 8 <= i < 10 else [box] for i in range(24)]
    tracks = [t for t in run_bytetrack(times, dets, 4.0, (464, 848)) if t.id > 0]
    assert len(tracks) == 1
    assert tracks[0].t[0] == 0.0 and tracks[0].t[-1] == times[-1]


def test_confirmation_needs_3_of_5_samples():
    from forte_detector.perception.tracker import confirmed

    times = [i / 4 for i in range(20)]
    assert confirmed(Track(1, [0.0, 0.5, 1.0], [SEAT] * 3, [0.5] * 3), times)  # samples 0,2,4 -> 3 of 5
    assert not confirmed(Track(2, [0.0, 0.75, 1.5], [SEAT] * 3, [0.5] * 3), times)  # 0,3,6 -> never 3 of 5
    assert not confirmed(Track(3, [2.0, 2.25], [SEAT] * 2, [0.5] * 2), times)


def test_bytetrack_needs_strong_detection_to_start_and_drops_after_3_misses():
    from forte_detector.perception.tracker import run_bytetrack

    times = [i / 4 for i in range(16)]
    weak = [[[0.40, 0.40, 0.60, 0.80, 0.3]] for _ in times]  # never >= 0.35: no track, no orphans
    assert run_bytetrack(times, weak, 4.0, (464, 848)) == []
    box = [0.40, 0.40, 0.60, 0.80, 0.9]
    dets = [[] if 5 <= i < 10 else [box] for i in range(16)]  # 5 misses -> new ByteTrack id afterwards
    ids = {t.id for t in run_bytetrack(times, dets, 4.0, (464, 848)) if t.id > 0}
    assert len(ids) == 2


def test_concurrent_duplicate_tracks_are_merged():
    from forte_detector.perception.tracker import dedupe_concurrent

    a = _track(2, 0.0, 5.5, [0.15, 0.23, 0.25, 0.39])
    b = _track(3, 1.0, 40.0, [0.151, 0.231, 0.251, 0.391])  # same person, ByteTrack id switch with overlap
    c = _track(4, 0.0, 40.0, SEAT)  # a different person at the same time
    out = {t.id: t for t in dedupe_concurrent([a, b, c])}
    assert set(out) == {3, 4}
    assert out[3].t[0] == 0.0 and out[3].t[-1] == 40.0 and out[3].t == sorted(set(out[3].t))
    assert sorted(out[3].members) == [2, 3]


def test_overlapping_but_much_smaller_person_is_not_deduped():
    from forte_detector.perception.tracker import dedupe_concurrent

    cashier = _track(1, 0.0, 20.0, [0.30, 0.40, 0.60, 0.70])
    client = _track(2, 0.0, 20.0, [0.32, 0.40, 0.58, 0.58])  # IoU 0.55 with the cashier, 0.6x height
    assert len(dedupe_concurrent([cashier, client])) == 2
