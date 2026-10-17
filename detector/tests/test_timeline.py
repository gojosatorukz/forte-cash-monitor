"""Checkpoint / role / zone logic on synthetic tracks (no GPU, no video)."""
import json

from forte_detector.perception.timeline import build_timeline
from forte_detector.perception.tracker import Track

FPS = 4.0
SEAT = [0.40, 0.45, 0.60, 0.70]
DOOR = [0.05, 0.15, 0.20, 0.45]
DESK_FRONT = [0.30, 0.20, 0.45, 0.45]


def _seg(t0, t1, box0, box1=None):
    """Samples on the 4 fps grid from t0 to t1 inclusive, linearly moving box0 -> box1."""
    box1 = box1 or box0
    n = int(round((t1 - t0) * FPS)) + 1
    out = []
    for i in range(n):
        a = i / max(n - 1, 1)
        out.append((round(t0 + i / FPS, 3), [b0 + a * (b1 - b0) for b0, b1 in zip(box0, box1)]))
    return out


def _track(tid, *segs):
    samples = []
    for s in segs:
        for t, b in s:
            if samples and abs(samples[-1][0] - t) < 1e-6:
                continue
            samples.append((t, b))
    return Track(tid, [t for t, _ in samples], [b for _, b in samples], [0.8] * len(samples))


def _times(duration):
    return [round(i / FPS, 3) for i in range(int(duration * FPS) + 1)]


def _cps(tl, kind):
    return [c for c in tl["checkpoints"] if c["type"] == kind]


def _build(tracks, duration):
    return build_timeline(tracks, _times(duration), duration=duration, frame_wh=(464, 848))


def test_cashier_leaves_and_exits_room():
    cashier = _track(1, _seg(0, 8, SEAT), _seg(8, 12, SEAT, DOOR))
    tl = _build([cashier], 20.0)
    assert tl["roles"]["cashier_tracks"] == [1]
    seat = tl["zones"]["seat"]
    assert all(abs(a - b) < 0.03 for a, b in zip(seat, SEAT))
    leave = _cps(tl, "cashier_leaves_seat")
    assert len(leave) == 1 and 8.0 <= leave[0]["t"] <= 9.5
    exits = _cps(tl, "cashier_exits_room")
    assert len(exits) == 1 and abs(exits[0]["t"] - 12.0) <= 0.5 and exits[0]["track_ids"] == [1]
    empty = _cps(tl, "room_empty_start")
    assert len(empty) == 1 and 12.0 <= empty[0]["t"] <= 12.5
    assert _cps(tl, "room_empty_end") == []
    assert not _cps(tl, "person_enters")
    assert tl["zones"]["door"] is not None


def test_visitor_enters_arrives_and_exits():
    cashier = _track(1, _seg(0, 20, SEAT))
    visitor = _track(2, _seg(3, 6, DOOR, DESK_FRONT), _seg(6, 12, DESK_FRONT), _seg(12, 14, DESK_FRONT, DOOR))
    tl = _build([cashier, visitor], 20.0)
    assert tl["roles"]["cashier_tracks"] == [1]
    assert tl["roles"]["visitor_tracks"] == [2]
    enters = _cps(tl, "person_enters")
    assert len(enters) == 1 and enters[0]["t"] == 3.0 and enters[0]["track_ids"] == [2]
    arrive = _cps(tl, "visitor_arrives_at_desk")
    assert len(arrive) == 1 and 5.5 <= arrive[0]["t"] <= 7.0
    exits = _cps(tl, "person_exits")
    assert len(exits) == 1 and abs(exits[0]["t"] - 14.0) <= 0.5
    changes = [(c["from"], c["to"]) for c in _cps(tl, "n_persons_change")]
    assert changes == [(1, 2), (2, 1)]
    assert not _cps(tl, "cashier_leaves_seat")
    assert not _cps(tl, "cashier_exits_room")
    assert not _cps(tl, "room_empty_start")
    # series
    s = tl["series"]
    i = s["t"].index(8.0)
    assert s["n_persons"][i] == 2 and s["cashier_at_seat"][i] is True and s["cashier_present"][i] is True


def test_cashier_returns_with_new_track_id():
    out = _track(1, _seg(0, 5, SEAT), _seg(5, 8, SEAT, DOOR))
    back = _track(5, _seg(12, 14, DOOR, SEAT), _seg(14, 20, SEAT))
    tl = _build([out, back], 20.0)
    assert tl["roles"]["cashier_tracks"] == [1, 5]
    assert tl["roles"]["visitor_tracks"] == []
    assert len(_cps(tl, "cashier_exits_room")) == 1
    start, end = _cps(tl, "room_empty_start"), _cps(tl, "room_empty_end")
    assert len(start) == 1 and 8.0 <= start[0]["t"] <= 8.5
    assert len(end) == 1 and abs(end[0]["t"] - 12.0) <= 0.3
    ret = _cps(tl, "cashier_returns_seat")
    assert len(ret) == 1 and 13.5 <= ret[0]["t"] <= 15.0
    assert not _cps(tl, "person_enters")  # the cashier coming back is not a visitor


def test_cashier_present_from_start_is_not_an_entry_and_seat_dominates():
    # a client sits for 8 s; the cashier sits the whole 20 s -> cashier is the longer stationary track
    cashier = _track(1, _seg(0, 20, SEAT))
    client = _track(2, _seg(0, 8, DESK_FRONT), _seg(8, 10, DESK_FRONT, DOOR))
    tl = _build([client, cashier], 20.0)
    assert tl["roles"]["cashier_tracks"] == [1]
    assert not _cps(tl, "person_enters")
    assert len(_cps(tl, "person_exits")) == 1


def test_three_persons_and_noise_track():
    cashier = _track(1, _seg(0, 20, SEAT))
    v1 = _track(2, _seg(2, 5, DOOR, DESK_FRONT), _seg(5, 20, DESK_FRONT))
    v2 = _track(3, _seg(10, 13, DOOR, [0.20, 0.25, 0.35, 0.50]), _seg(13, 20, [0.20, 0.25, 0.35, 0.50]))
    noise = _track(4, _seg(15, 15.25, [0.8, 0.1, 0.9, 0.2]))
    tl = _build([cashier, v1, v2, noise], 20.0)
    assert 4 not in tl["roles"]["visitor_tracks"]
    assert max(tl["series"]["n_persons"]) == 3
    changes = [(c["from"], c["to"], c["t"]) for c in _cps(tl, "n_persons_change")]
    assert [(a, b) for a, b, _ in changes] == [(1, 2), (2, 3)]
    assert changes[1][2] == 10.0
    assert len(_cps(tl, "person_enters")) == 2


def test_no_persons_at_all():
    tl = _build([], 10.0)
    assert tl["roles"]["cashier_tracks"] == []
    assert tl["zones"]["seat"] is None and tl["zones"]["door"] is None
    assert _cps(tl, "room_empty_start")[0]["t"] == 0.0
    assert all(n == 0 for n in tl["series"]["n_persons"])


def test_brief_dropout_does_not_create_events():
    # cashier track has a 1 s hole (occlusion) at the seat: no leave/exit/empty events
    cashier = _track(1, _seg(0, 9, SEAT), _seg(10, 20, SEAT))
    tl = _build([cashier], 20.0)
    for kind in ("cashier_leaves_seat", "cashier_exits_room", "room_empty_start", "n_persons_change"):
        assert not _cps(tl, kind), kind


def test_timeline_is_json_serializable():
    cashier = _track(1, _seg(0, 8, SEAT), _seg(8, 12, SEAT, DOOR))
    tl = _build([cashier], 20.0)
    json.dumps(tl)
    assert tl["checkpoints"] == sorted(tl["checkpoints"], key=lambda c: c["t"])


def test_far_static_track_is_background_not_a_person_in_room():
    # e.g. somebody seen through the glass wall / a reflection: never approaches the seat
    cashier = _track(1, _seg(0, 20, SEAT))
    glass = _track(2, _seg(0, 20, [0.85, 0.02, 0.95, 0.12]))
    tl = _build([cashier, glass], 20.0)
    assert tl["roles"]["background_tracks"] == [2]
    assert tl["roles"]["visitor_tracks"] == []
    assert max(tl["series"]["n_persons"]) == 1
    assert not _cps(tl, "n_persons_change")


def test_small_static_track_near_seat_is_background():
    # person behind the glass right "above" the seat in 2D: static, present from start, much smaller
    cashier = _track(1, _seg(0, 20, SEAT))
    behind = _track(2, _seg(0, 20, [0.45, 0.30, 0.52, 0.40]))
    tl = _build([cashier, behind], 20.0)
    assert tl["roles"]["background_tracks"] == [2]
    assert max(tl["series"]["n_persons"]) == 1


def test_standing_up_counts_as_leaving_seat_and_farthest_point_is_reported():
    stand = [0.40, 0.25, 0.60, 0.70]  # same place, box much taller: she stood up
    cashier = _track(1, _seg(0, 8, SEAT), _seg(8.25, 10, stand), _seg(10, 13, stand, DOOR), _seg(13, 16, DOOR, SEAT), _seg(16, 20, SEAT))
    tl = _build([cashier], 20.0)
    leave = _cps(tl, "cashier_leaves_seat")
    assert len(leave) == 1 and 8.0 <= leave[0]["t"] <= 8.5
    far = _cps(tl, "cashier_farthest_from_seat")
    assert len(far) == 1 and abs(far[0]["t"] - 13.0) <= 0.3


def test_no_farthest_point_before_cashier_first_sits():
    # cashier walks in from the door at the start and sits down: no away-episode, no farthest point
    cashier = _track(1, _seg(0, 3, DOOR, SEAT), _seg(3, 20, SEAT))
    tl = _build([cashier], 20.0)
    assert not _cps(tl, "cashier_farthest_from_seat")
    assert not _cps(tl, "cashier_leaves_seat")
    assert len(_cps(tl, "cashier_arrives_seat")) == 1


STAND_GLASS = [0.30, 0.15, 0.45, 0.45]  # standing behind the glass wall, "above" the desk in 2D
SAFE = [0.70, 0.30, 0.85, 0.70]


def _glass_person():
    """Somebody in the office seen through the glass wall next to the door (a background track)."""
    return _track(9, _seg(1, 6, [0.06, 0.12, 0.18, 0.30]))


def test_cashier_seen_behind_glass_after_door_is_outside():
    # X-15/X-24 pattern: walks to the door, crosses it, keeps being detected behind the glass
    cashier = _track(1, _seg(0, 8, SEAT), _seg(8, 12, SEAT, DOOR), _seg(12, 16, DOOR, STAND_GLASS),
                     _seg(16, 20, STAND_GLASS))
    tl = _build([cashier, _glass_person()], 20.0)
    assert tl["roles"]["background_tracks"] == [9]
    exits = _cps(tl, "cashier_exits_room")
    assert len(exits) == 1 and abs(exits[0]["t"] - 12.0) <= 0.5
    start = _cps(tl, "room_empty_start")
    assert len(start) == 1 and 12.0 <= start[0]["t"] <= 12.75
    s = tl["series"]
    i = s["t"].index(18.0)
    assert s["n_persons"][i] == 0 and s["cashier_present"][i] is False
    assert s["persons"][i][0][3] is True  # kept in the timeline, flagged outside
    assert tl["outside"] == [{"track_id": 1, "from_t": 12.0, "to_t": 20.0, "came_back": False}]


def test_cashier_walking_to_safe_and_back_is_never_outside():
    safe = [0.70, 0.30, 0.85, 0.70]
    cashier = _track(1, _seg(0, 5, SEAT), _seg(5, 7, SEAT, safe), _seg(7, 12, safe), _seg(12, 14, safe, SEAT),
                     _seg(14, 20, SEAT))
    tl = _build([cashier], 20.0)
    assert tl["outside"] == []
    assert not _cps(tl, "cashier_exits_room") and not _cps(tl, "room_empty_start")


def test_visitor_seen_behind_glass_after_leaving_is_outside():
    cashier = _track(1, _seg(0, 20, SEAT))
    visitor = _track(2, _seg(2, 5, DOOR, DESK_FRONT), _seg(5, 9, DESK_FRONT), _seg(9, 11, DESK_FRONT, DOOR),
                     _seg(11, 14, DOOR, STAND_GLASS), _seg(14, 20, STAND_GLASS))
    tl = _build([cashier, visitor], 20.0)
    exits = _cps(tl, "person_exits")
    assert len(exits) == 1 and abs(exits[0]["t"] - 11.0) <= 0.5
    i = tl["series"]["t"].index(17.0)
    assert tl["series"]["n_persons"][i] == 1


def test_low_score_visitor_like_track_is_background():
    # a mirror image of the cashier in the glass: moves, "enters", but is detected weakly
    cashier = _track(1, _seg(0, 20, SEAT))
    ghost = _track(2, _seg(2, 5, DOOR, DESK_FRONT), _seg(5, 12, DESK_FRONT))
    ghost.scores = [0.35] * len(ghost.t)
    tl = _build([cashier, ghost], 20.0)
    assert tl["roles"]["background_tracks"] == [2]
    assert not _cps(tl, "person_enters")


def test_seat_is_the_seated_box_even_if_she_stands_nearby_longer():
    # X-15: seated 0-6 s, then stands right there (much taller box) for longer, then leaves
    tall = [0.38, 0.20, 0.62, 0.70]
    cashier = _track(1, _seg(0, 6, SEAT), _seg(6.25, 14, tall), _seg(14, 16, tall, DOOR), _seg(16, 20, STAND_GLASS))
    tl = _build([cashier, _glass_person()], 20.0)
    seat = tl["zones"]["seat"]
    assert all(abs(a - b) < 0.03 for a, b in zip(seat, SEAT))
    leave = _cps(tl, "cashier_leaves_seat")
    assert len(leave) == 1 and 6.0 <= leave[0]["t"] <= 6.75
    exits = _cps(tl, "cashier_exits_room")
    assert len(exits) == 1 and abs(exits[0]["t"] - 16.0) <= 0.5


def test_cashier_standing_at_safe_until_the_end_is_not_outside():
    # walks to the safe (far from the seat, not at the door/glass) and is still there when the clip ends
    safe_far = [0.72, 0.30, 0.87, 0.70]  # small sway at the safe: the farthest point is mid-way
    cashier = _track(1, _seg(0, 8, SEAT), _seg(8, 10, SEAT, SAFE), _seg(10, 14, SAFE, safe_far),
                     _seg(14, 20, safe_far, SAFE))
    for extra in ([], [_glass_person()]):
        tl = _build([cashier, *extra], 20.0)
        assert tl["outside"] == [], extra
        assert not _cps(tl, "cashier_exits_room") and not _cps(tl, "room_empty_start")


def test_outside_is_reversible_when_the_person_comes_back():
    # crosses the door, is seen behind the glass for 3 s, comes back in and sits down again
    cashier = _track(1, _seg(0, 8, SEAT), _seg(8, 11, SEAT, DOOR), _seg(11, 14, DOOR, STAND_GLASS),
                     _seg(14, 16, STAND_GLASS, DOOR), _seg(16, 18, DOOR, SEAT), _seg(18, 24, SEAT))
    tl = _build([cashier, _glass_person()], 24.0)
    assert len(tl["outside"]) == 1
    o = tl["outside"][0]
    assert abs(o["from_t"] - 11.0) <= 0.5 and o["to_t"] <= 17.5 and o["came_back"] is True
    s = tl["series"]
    assert s["cashier_present"][s["t"].index(20.0)] is True
    assert len(_cps(tl, "cashier_exits_room")) == 1 and len(_cps(tl, "cashier_enters_room")) == 1


def test_walking_along_the_glass_outside_is_not_a_return():
    # after crossing the door she walks along the glass towards the desk side (2D distance drops to
    # ~0.5x of the crossing distance) without coming in: still outside
    along = [0.38, 0.30, 0.53, 0.60]  # 2D distance to the seat ~0.37x the crossing distance
    cashier = _track(1, _seg(0, 8, SEAT), _seg(8, 12, SEAT, DOOR), _seg(12, 16, DOOR, along), _seg(16, 20, along))
    tl = _build([cashier, _glass_person()], 20.0)
    assert [o["came_back"] for o in tl["outside"]] == [False]
    assert not _cps(tl, "cashier_enters_room")


def test_client_seated_at_desk_until_the_end_is_not_outside():
    cashier = _track(1, _seg(0, 20, SEAT))
    client = _track(2, _seg(2, 5, DOOR, DESK_FRONT), _seg(5, 20, DESK_FRONT))
    tl = _build([cashier, client, _glass_person()], 20.0)
    assert tl["outside"] == []


def test_exit_without_any_door_evidence_via_frame_edge():
    # the cashier is the only track; the door is at the frame edge and she stays visible behind it
    edge_door = [0.0, 0.15, 0.12, 0.45]
    cashier = _track(1, _seg(0, 8, SEAT), _seg(8, 12, SEAT, edge_door), _seg(12, 16, edge_door, STAND_GLASS),
                     _seg(16, 20, STAND_GLASS))
    tl = _build([cashier], 20.0)
    exits = _cps(tl, "cashier_exits_room")
    assert len(exits) == 1 and abs(exits[0]["t"] - 12.0) <= 0.5
    assert len(_cps(tl, "room_empty_start")) == 1


def test_exit_without_any_door_evidence_via_scale_drop():
    # not at an edge, but behind the glass she is seen much smaller (farther away) than in the room
    far = [0.30, 0.20, 0.35, 0.30]  # height 0.10 vs ~0.25 in the room
    cashier = _track(1, _seg(0, 8, SEAT), _seg(8, 12, SEAT, DOOR), _seg(12, 13, DOOR, far), _seg(13, 20, far))
    tl = _build([cashier], 20.0)
    exits = _cps(tl, "cashier_exits_room")
    assert len(exits) == 1 and 11.5 <= exits[0]["t"] <= 13.5


def test_small_reflection_inside_the_cashier_box_is_not_door_evidence():
    # a small mirror-image box lying inside the cashier's box at the safe must not count as "door"
    safe_far = [0.72, 0.30, 0.87, 0.70]
    cashier = _track(1, _seg(0, 8, SEAT), _seg(8, 10, SEAT, SAFE), _seg(10, 14, SAFE, safe_far),
                     _seg(14, 20, safe_far, SAFE))
    ghost = _track(9, _seg(1, 6, [0.75, 0.32, 0.80, 0.40]))  # small, far from the seat -> background
    tl = _build([cashier, ghost], 20.0)
    assert 9 in tl["roles"]["background_tracks"]
    assert tl["outside"] == []


def _assert_never_outside(tl):
    assert tl["outside"] == []
    assert not _cps(tl, "cashier_exits_room") and not _cps(tl, "room_empty_start")


def test_score_drop_at_the_safe_and_coming_back_is_not_an_exit():
    # A: walks to the safe, crouches 3 s partly occluded (score 0.8 -> 0.5), comes back and sits
    cashier = _track(1, _seg(0, 8, SEAT), _seg(8, 10, SEAT, SAFE), _seg(10, 13, SAFE), _seg(13, 15, SAFE, SEAT),
                     _seg(15, 20, SEAT))
    cashier.scores = [0.5 if 10.0 < t <= 13.0 else 0.8 for t in cashier.t]
    _assert_never_outside(_build([cashier], 20.0))


def test_safe_box_touching_the_bottom_edge_and_coming_back_is_not_an_exit():
    # B: in a ceiling-corner view the feet are cut at the bottom edge near the safe
    safe_low = [0.70, 0.45, 0.85, 0.99]
    cashier = _track(1, _seg(0, 8, SEAT), _seg(8, 10, SEAT, safe_low), _seg(10, 13, safe_low),
                     _seg(13, 15, safe_low, SEAT), _seg(15, 20, SEAT))
    _assert_never_outside(_build([cashier], 20.0))


def test_safe_box_touching_the_bottom_edge_until_the_end_is_not_an_exit():
    safe_low = [0.70, 0.45, 0.85, 0.99]
    cashier = _track(1, _seg(0, 8, SEAT), _seg(8, 10, SEAT, safe_low), _seg(10, 20, safe_low))
    _assert_never_outside(_build([cashier], 20.0))


def test_crouching_after_standing_long_before_the_episode_is_not_an_exit():
    # C: stands (tall box) for 8 s before first sitting, then crouches at the safe (~0.55x standing)
    tall = [0.43, 0.10, 0.63, 0.60]  # height 0.50
    crouch = [0.70, 0.425, 0.85, 0.70]  # height 0.275 = 0.55x standing, about the seated height
    returning = _track(1, _seg(0, 8, tall), _seg(8.25, 14, SEAT), _seg(14, 16, SEAT, crouch), _seg(16, 19, crouch),
                       _seg(19, 21, crouch, SEAT), _seg(21, 26, SEAT))
    staying = _track(1, _seg(0, 8, tall), _seg(8.25, 14, SEAT), _seg(14, 16, SEAT, crouch), _seg(16, 26, crouch))
    for cashier in (returning, staying):
        _assert_never_outside(_build([cashier], 26.0))


def test_cashier_back_at_the_seat_as_a_new_track_is_not_an_exit():
    # the first track ends at the safe (score drop while occluded), she sits again as a new track id
    first = _track(1, _seg(0, 8, SEAT), _seg(8, 10, SEAT, SAFE), _seg(10, 13, SAFE))
    first.scores = [0.5 if t > 10.0 else 0.8 for t in first.t]
    second = _track(2, _seg(13.5, 15, SAFE, SEAT), _seg(15, 20, SEAT))
    _assert_never_outside(_build([first, second], 20.0))


def test_cashier_reentering_as_a_new_track_is_door_evidence_for_her_exit():
    # track 1 goes out through the door, stays visible behind the glass, dies there; after a real
    # absence she comes back through the door as track 2 and sits. Track 2's birth is the only door
    # evidence (no background / visitor tracks), and it is not a tracker id switch.
    first = _track(1, _seg(0, 8, SEAT), _seg(8, 12, SEAT, DOOR), _seg(12, 14, DOOR, STAND_GLASS),
                   _seg(14, 16, STAND_GLASS))
    second = _track(2, _seg(19, 21, DOOR, SEAT), _seg(21, 28, SEAT))
    tl = _build([first, second], 28.0)
    assert tl["roles"]["cashier_tracks"] == [1, 2]
    exits = _cps(tl, "cashier_exits_room")
    assert len(exits) == 1 and abs(exits[0]["t"] - 12.0) <= 0.5 and exits[0].get("seen_behind_glass")
    assert [o["track_id"] for o in tl["outside"]] == [1]
    enters = _cps(tl, "cashier_enters_room")
    assert len(enters) == 1 and abs(enters[0]["t"] - 19.0) <= 0.5
    s = tl["series"]
    assert s["cashier_present"][s["t"].index(24.0)] is True
