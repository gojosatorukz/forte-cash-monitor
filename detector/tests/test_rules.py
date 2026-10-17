"""Rule engine on synthetic timelines + a fake probe runner (no GPU, no video)."""
from __future__ import annotations

import json
import time

import pytest

from forte_detector import config
from forte_detector.probes.base import CUES, ProbeResult
from forte_detector.rules import engine as E
from forte_detector.rules import taxonomy as tx
from forte_detector.schema import Event

SPEC = config.REPO_DIR.parent.parent.parent.parent / "docs" / "deliverables" / "rules_spec.json"


# ----------------------------------------------------------------------------- fixtures

def make_tl(duration=20.0, cps=(), tracks=(), n_persons=None, cashier=(1,)):
    step = 0.5
    ts = [round(i * step, 2) for i in range(int(duration / step) + 1)]
    if n_persons is None:
        n_persons = lambda t: 1  # noqa: E731
    return {
        "duration": duration,
        "roles": {"cashier_tracks": list(cashier)},
        "zones": {"seat": [0.3, 0.3, 0.5, 0.5], "door": [0.1, 0.1, 0.2, 0.3]},
        "checkpoints": sorted([{"type": k, "t": t, "track_ids": list(ids)} for k, t, ids in cps],
                              key=lambda c: c["t"]),
        "tracks": [{"id": i, "t": [a, b], "boxes": [[0, 0, 1, 1]] * 2, "scores": [0.9, 0.9]} for i, a, b in tracks],
        "series": {"t": ts, "n_persons": [n_persons(t) for t in ts]},
    }


class Fake:
    """answers: cue -> value | (value, conf) | callable(request) -> (value, conf[, count])."""

    def __init__(self, **answers):
        self.answers = {"id_document_shown": True, **answers}  # default: the client shows an ID
        self.calls: list = []

    def __call__(self, video_path, requests, *, timeline, transform=None, calibration=None):
        out = []
        for r in requests:
            assert r.cue in CUES, r.cue
            self.calls.append(r)
            if r.cue == "person_count" and r.cue not in self.answers:
                # default: a perfect counter = the synthetic timeline's n_persons at r.t
                s = timeline.get("series", {})
                i = min(range(len(s["t"])), key=lambda j: abs(s["t"][j] - r.t)) if s.get("t") else None
                n = s["n_persons"][i] if i is not None else 1
                a = (n >= 2, 0.9, n)
            else:
                a = self.answers.get(r.cue, False)
            if callable(a):
                a = a(r)
            if not isinstance(a, tuple):
                a = (a, 0.9)
            val, conf, *rest = a
            raw = {"count": rest[0]} if rest else {}
            if r.cue == "person_count" and rest:
                val = rest[0] >= 2
            out.append(ProbeResult(r.cue, r.t, val, conf, [r.t], "fake", raw))
        return out


def run(tl, fake, cfg=None):
    ctx = E.Ctx(tl, {}, cfg)
    events = []
    for item in E.plan(ctx):
        res = fake("v.mov", item.requests, timeline=tl)
        events += item.rule.decide(ctx, list(zip(item.requests, res)))
    return E.finalize(events, ctx.cfg)


def types(events):
    return sorted({e.type for e in events})


def count(n):
    return lambda r: (n >= 2, 0.9, n)


# departure scene: cashier leaves seat at 8.5, exits at 13, room empty from 13.3 (like X-24)
def departure_tl():
    return make_tl(14.5, cps=[("cashier_leaves_seat", 8.5, [1]), ("cashier_exits_room", 13.0, [1]),
                              ("room_empty_start", 13.3, [])],
                   tracks=[(1, 0.0, 13.0)], n_persons=lambda t: 0 if t >= 13.3 else 1)


# ----------------------------------------------------------------------------- taxonomy / schema

def test_rules_json_is_verbatim_spec():
    ours = tx.load_rules()
    assert "T1b" in ours and "T15" in ours and "T99" in ours
    if SPEC.exists():
        assert json.loads(SPEC.read_text(encoding="utf-8")) == ours


def test_describe_obs_punctuation_and_legal_conf():
    d = tx.describe("T1", 65.0, "на столе видны наличные")
    assert "на 01:05 кассир" in d and "или сумка, на столе видны наличные." in d
    assert "{obs}" not in tx.describe("T1", 1, "") and ", ." not in tx.describe("T1", 1, "")
    assert tx.describe("T10", 22, "кассир держит наличные").startswith("Наблюдение: на 00:22 кассир держит наличные.")
    with pytest.raises(ValueError):
        tx.describe("T99", 3, "")
    assert tx.legal_conf("T1") == "high" and tx.legal_conf("T6") == "medium" and tx.legal_conf("T99") == "low"


def test_event_schema_fields_and_roundtrip():
    e = E.make_event("T4", 7.0, 0.8, "экран включён", start=6.0, end=9.0, evidence=[6, 7, 9])
    d = e.to_dict()
    for k in ("type_id", "title_ru", "start_sec", "end_sec", "timecode_sec", "fact_conf", "legal_conf",
              "description_ru", "evidence_ts", "obs", "confidence", "type"):
        assert k in d, k
    assert d["type"] == d["type_id"] == "T4" and d["title_ru"] == tx.title("T4")
    assert d["confidence"] <= d["fact_conf"]
    assert Event.from_dict(d).to_dict() == d
    legacy = Event(type="T99", timecode_sec=1.0, confidence=0.5, description="x")
    assert legacy.description_ru == "x" and legacy.start_sec == 1.0 and legacy.fact_conf == 0.5


# ----------------------------------------------------------------------------- departure rules

def test_t1_cash_left_when_room_empty():
    ev = run(departure_tl(), Fake(cash_on_desk=True))
    assert types(ev) == ["T1"]
    e = ev[0]
    # timecode = middle of [leaves seat 8.5, crosses the door ~13]; window = start..end
    assert 10.0 <= e.timecode_sec <= 11.5 and e.start_sec == 8.5 and e.end_sec >= 13.0
    assert e.evidence_ts[0] == e.timecode_sec
    assert e.obs and not e.obs[0].isupper() and not e.obs.endswith(".")


def test_departure_hard_negative_clean_desk():  # X-23 / X-25 / X-27 / X-29: leaving is not a violation
    assert run(departure_tl(), Fake()) == []


def test_t1b_t4_t3_at_departure():
    assert types(run(departure_tl(), Fake(stamps_or_journal_on_desk=True))) == ["T1b"]
    assert types(run(departure_tl(), Fake(screen_on_unlocked=True))) == ["T4"]
    assert types(run(departure_tl(), Fake(key_in_safe=True))) == ["T3"]


def test_low_confidence_probe_is_ignored():
    assert run(departure_tl(), Fake(cash_on_desk=(True, 0.3))) == []


def test_fallback_departure_from_vlm_room_count():
    # perception never saw the cashier leave (X-15): end-of-clip person count = 0 since ~15.8 s
    tl = make_tl(20.8, cps=[], tracks=[(1, 0.0, 20.8), (3, 0.0, 17.0)], n_persons=lambda t: 2)
    fake = Fake(person_count=lambda r: (False, 0.9, 0 if r.t >= 15.0 else 1), bag_on_desk=True)
    ev = run(tl, fake)
    assert types(ev) == ["T1"] and 14.0 <= ev[0].timecode_sec <= 18.0


def test_seat_leave_candidate_confirmed_by_count():
    # no exit / empty checkpoint (people behind the glass keep the room "occupied" for perception)
    tl = make_tl(19.8, cps=[("cashier_leaves_seat", 10.0, [1])], tracks=[(1, 0, 19.8), (8, 0, 19.8)],
                 n_persons=lambda t: 2)
    fake = Fake(person_count=lambda r: (False, 0.9, 0 if r.t >= 15 else 1), cash_on_desk=True)
    ev = run(tl, fake)
    assert types(ev) == ["T1"] and ev[0].start_sec == 10.0 and 10.0 < ev[0].timecode_sec <= 13.0


def test_seat_leave_hard_negative_turn_to_safe():  # X-5 / X-35: stands up, sits down again
    tl = make_tl(30.0, cps=[("cashier_leaves_seat", 10.0, [1]), ("cashier_returns_seat", 11.0, [1])],
                 tracks=[(1, 0, 30)])
    assert len(E.Ctx(tl).departures) == 1 and E.Ctx(tl).departures[0].source == "end"
    assert run(tl, Fake(cash_on_desk=True)) == []  # count at the end = 1 (cashier seated)


def test_fallback_departure_negative_room_occupied():
    tl = make_tl(20.8, cps=[], tracks=[(1, 0.0, 20.8)])
    assert run(tl, Fake(person_count=count(1), bag_on_desk=True)) == []


# ----------------------------------------------------------------------------- client change (T2)

def client_change_tl():  # X-4: client A 5-53.5, client B enters 55
    return make_tl(72.0, cps=[("person_enters", 5.0, [5]), ("person_exits", 53.5, [5]),
                              ("person_enters", 55.0, [33])],
                   tracks=[(1, 0, 72), (5, 5.0, 53.5), (33, 55.0, 72.0)], n_persons=lambda t: 2)


def test_t2_cash_on_desk_when_next_client_enters():
    ev = run(client_change_tl(), Fake(cash_on_desk=lambda r: (r.t > 54, 0.9)))
    assert "T2" in types(ev)
    assert [e for e in ev if e.type == "T2"][0].timecode_sec == 56.0  # next client inside


def test_t2_hard_negative_desk_cleared_between_clients():  # X-5 / X-19 / X-39
    assert run(client_change_tl(), Fake()) == []


def test_t2_parallel_service_needs_three_people():  # X-18
    tl = make_tl(22.5, cps=[("person_enters", 1.0, [3]), ("person_enters", 16.0, [14])],
                 tracks=[(1, 0, 22.5), (3, 1.0, 22.5), (14, 16.0, 22.5)], n_persons=lambda t: 3)
    # first client's cash still in the machine while the second is served -> T2; 2 visitors -> T6
    assert types(run(tl, Fake(person_count=count(3), counting_machine_used=True))) == ["T2", "T6"]
    assert types(run(tl, Fake(person_count=count(3)))) == ["T6"]
    # phantom track, only cashier + 1 client (second look later also 2)
    assert run(tl, Fake(person_count=count(2), counting_machine_used=True)) == []


def test_t2_cash_second_look_or_machine():  # X-38: first look misses the stack
    tl = client_change_tl()
    assert "T2" in types(run(tl, Fake(cash_on_desk=lambda r: (r.t > 57, 0.9))))
    assert "T2" in types(run(tl, Fake(counting_machine_used=lambda r: (54 < r.t < 57, 0.9))))


def test_t7_client_present_from_t0():  # X-34: client track starts at t0 (no person_enters)
    tl = make_tl(17.5, cps=[("person_exits", 13.3, [2])], tracks=[(1, 0, 17.5), (2, 0.0, 13.3)])
    fake = Fake(person_count=lambda r: (None, 0.9, 2 if r.t < 13.3 else 1), counting_machine_used=True)
    ev = run(tl, fake)
    assert types(ev) == ["T7"] and ev[0].timecode_sec == 13.3


def test_t99_unexplained_access_is_low_confidence():
    tl = make_tl(20.0, cps=[("visitor_arrives_at_desk", 10.0, [5])], tracks=[(1, 0, 6), (5, 8, 20)])
    tl["series"]["cashier_present"] = [t < 6 for t in tl["series"]["t"]]
    ctx = E.Ctx(tl)
    ev = E.UnexplainedAccessRule().decide(ctx, [])
    assert [e.type for e in ev] == ["T99"] and ev[0].obs and not E.verdict(ev)
    tl["series"]["cashier_present"] = [True] * len(tl["series"]["t"])  # cashier there: normal service
    assert E.UnexplainedAccessRule().decide(E.Ctx(tl), []) == []


def _only_weak(evs, type_id):
    assert [e.type for e in evs] == [type_id], evs
    assert evs[0].fact_conf <= E.WEAK_FACT and evs[0].extra.get("strength") == "weak"
    assert E.verdict(evs)  # recall-first: weak evidence still counts as a violation
    return evs[0]


def test_t13_phone_two_of_three_is_weak():  # X-5 / X-48: two hits (desk phone?) -> weak, low fact_conf
    tl = make_tl(25.0, tracks=[(1, 0, 25)])
    n = len(tl["series"]["t"])
    person = [1, "cashier", [0.3, 0.3, 0.6, 0.9]]
    tl["series"]["persons"] = [[person]] * n
    # phone lying on the desk (outside every person box): the weak answer is not corroborated
    tl["series"]["objects"] = [[["cell phone", 0.7, 0.1, 0.75, 0.15, 0.8]]] * n
    assert run(tl, Fake(phone_visible=lambda r: (r.t < 20, 1.0))) == []
    # phone held by the cashier (inside the person box): weak T13
    tl["series"]["objects"] = [[["cell phone", 0.4, 0.5, 0.45, 0.55, 0.8]]] * n
    _only_weak(run(tl, Fake(phone_visible=lambda r: (r.t < 20, 1.0))), "T13")
    assert run(tl, Fake(phone_visible=lambda r: (r.t < 12, 1.0))) == []  # a single hit is nothing


def test_t10_alone_is_weak():  # X-35 / X-2 (0.737): one hand cue, ID shown -> weak T10
    _only_weak(run(service_tl(), Fake(cash_in_hand_to_body=lambda r: (r.t > 10, 0.74))), "T10")
    assert run(service_tl(), Fake(cash_in_hand_to_body=(True, 0.4))) == []  # below any answer bar


# ----------------------------------------------------------------------------- short visit (T7, T9)

def short_visit_tl():  # X-34 / X-17: visitor 3-13
    return make_tl(17.5, cps=[("person_enters", 3.0, [8]), ("person_exits", 13.0, [8])],
                   tracks=[(1, 0, 17.5), (8, 3.0, 13.0)])


def test_t7_count_after_client_left():
    fake = Fake(person_count=lambda r: (None, 0.9, 2 if r.t < 13 else 1), counting_machine_used=True)
    ev = run(short_visit_tl(), fake)
    assert types(ev) == ["T7"] and ev[0].timecode_sec == 13.0


def test_t7_hard_negative_client_still_present():  # X-37: client stays; phantom exit
    fake = Fake(person_count=count(2), counting_machine_used=True)
    assert run(short_visit_tl(), fake) == []


def test_t9_bag_left_after_short_collector_visit():
    fake = Fake(person_count=lambda r: (None, 0.9, 2 if r.t < 13 else 1), bag_on_desk=True)
    ev = run(short_visit_tl(), fake)
    assert types(ev) == ["T9"] and 6.0 <= ev[0].timecode_sec <= 9.0


def test_t9_hard_negative_phantom_visitor():  # X-8: nobody was ever there
    fake = Fake(person_count=count(1), bag_on_desk=True)
    assert run(short_visit_tl(), fake) == []


# ----------------------------------------------------------------------------- door (T5, T6)

def door_tl(with_entry=True):  # X-30 / X-32: cashier sits at 4.5, someone enters at 11.5
    cps = [("cashier_arrives_seat", 4.5, [1])]
    tracks = [(1, 0, 18.7)]
    if with_entry:
        cps.append(("person_enters", 11.5, [7]))
        tracks.append((7, 11.5, 18.7))
    return make_tl(18.7, cps=cps, tracks=tracks, n_persons=lambda t: 2 if with_entry and t >= 11.5 else 1)


def test_t5_door_left_open_and_t6_entry():
    ev = run(door_tl(), Fake(door_open=True))
    assert types(ev) == ["T5", "T6"]
    t6 = [e for e in ev if e.type == "T6"][0]
    assert t6.timecode_sec == 11.5


def test_t6_hard_negative_reflection_track():  # glass reflection: VLM sees only the cashier
    ev = run(door_tl(), Fake(door_open=True, person_count=count(1)))
    assert types(ev) == ["T5"]


def test_t5_hard_negative_client_near_door():  # X-1 / X-5: served client masks the door diff
    tl = make_tl(26.0, cps=[("person_enters", 1.0, [3])], tracks=[(1, 0, 26), (3, 0.5, 26)])
    assert run(tl, Fake(door_open=True)) == []


def test_t5_hard_negative_door_open_only_while_passing():  # X-31: closed after entering
    t_first = E.DoorRule().times(E.Ctx(door_tl(False)))[0]
    assert run(door_tl(False), Fake(door_open=lambda r: (r.t <= t_first, 0.9))) == []


# ----------------------------------------------------------------------------- persistent states

def test_t3_safe_open_until_end():  # X-20
    tl = make_tl(11.5, tracks=[(1, 0, 11.5)])
    assert types(run(tl, Fake(safe_open=True))) == ["T3"]


def test_t3_safe_open_partly_is_weak_once_is_nothing():  # X-5 / X-21 / X-35: open during access only
    tl = make_tl(11.5, tracks=[(1, 0, 11.5)])
    _only_weak(run(tl, Fake(safe_open=lambda r: (r.t < 9, 0.9))), "T3")  # 2 of 3 tail probes
    assert run(tl, Fake(safe_open=lambda r: (r.t < 6, 0.9))) == []  # 1 of 3: brief access


def test_t11_t12_t13_need_two_hits():
    tl = make_tl(25.0, tracks=[(1, 0, 25)])
    assert types(run(tl, Fake(handbag_or_coat_on_desk=True))) == ["T11"]
    assert types(run(tl, Fake(cup_or_food=lambda r: (r.t > 12, 0.9)))) == ["T12"]
    assert types(run(tl, Fake(phone_visible=True))) == ["T13"]


def test_t11_t12_single_hit_is_weak():
    tl = make_tl(25.0, tracks=[(1, 0, 25)])
    _only_weak(run(tl, Fake(handbag_or_coat_on_desk=lambda r: (r.t > 24, 0.9))), "T11")
    _only_weak(run(tl, Fake(cup_or_food=lambda r: (r.t < 12, 0.9))), "T12")
    assert run(tl, Fake(cup_or_food=(True, 0.3))) == []  # below the answer bar


def test_coco_only_fallback_stays_below_threshold():  # VLM down: COCO "handbag" on a jacket
    tl = make_tl(25.0, tracks=[(1, 0, 25)])

    class CocoFake(Fake):
        def __call__(self, *a, **k):
            out = super().__call__(*a, **k)
            for r in out:
                if r.cue == "handbag_or_coat_on_desk":
                    r.source = "coco"
            return out

    ev = run(tl, CocoFake(handbag_or_coat_on_desk=(True, 0.63)))
    assert not E.verdict(ev)


def test_t14_camera_blocked_consecutive():
    tl = make_tl(20.0, tracks=[(1, 0, 20)])
    assert types(run(tl, Fake(camera_blocked=lambda r: (r.t >= 9, 0.9)))) == ["T14"]


def test_t14_hard_negative_lights_off_once():  # X-11 / X-12: one dark moment is not obstruction
    tl = make_tl(20.0, tracks=[(1, 0, 20)])
    assert run(tl, Fake(camera_blocked=lambda r: (abs(r.t - 10) < 1, 0.9))) == []


# ----------------------------------------------------------------------------- service (T10, T15)

def service_tl():  # X-2: client 2-17 at desk
    return make_tl(17.0, cps=[("person_enters", 2.0, [3]), ("visitor_arrives_at_desk", 9.3, [3])],
                   tracks=[(1, 0, 17), (3, 2.0, 17.0)], n_persons=lambda t: 2)


def test_t10_and_t15_on_service_visit():
    ev = run(service_tl(), Fake(cash_in_hand_to_body=True, id_document_shown=False))
    assert types(ev) == ["T10", "T15"]
    t10 = [e for e in ev if e.type == "T10"][0]
    assert t10.obs and t10.description_ru.startswith(f"Наблюдение: на {tx.mmss(t10.timecode_sec)} {t10.obs}")


def test_t15_standalone_needs_both_id_probes_negative():  # X-2 primary: no ID before cash
    assert types(run(service_tl(), Fake(id_document_shown=False))) == ["T15"]
    assert run(service_tl(), Fake(id_document_shown=(False, 0.6))) == []  # unsure "no" is not enough


def test_t10_t15_hard_negative():  # X-3 / X-1: ID shown, free hand open
    assert run(service_tl(), Fake(id_document_shown=True)) == []
    assert types(run(service_tl(), Fake(cash_in_hand_to_body=True, id_document_shown=True))) == ["T10"]


def test_t15_procedure_aware():  # human review round 2: X-2, X-19, X-35, X-37, X-39 vs collectors
    tl = service_tl()  # client 2-17, reaches the desk at 9.3
    a, b = E.ServiceRule.id_window(E.Ctx(tl).visits[0])
    assert a < 9.3 < b <= 11.0  # the ID must be shown before / at the cash acceptance
    ev = run(tl, Fake(id_document_shown=False))
    t15 = [e for e in ev if e.type == "T15"]
    assert t15 and t15[0].timecode_sec == 9.3 and t15[0].fact_conf <= 0.55 and E.verdict(t15)
    assert t15[0].extra["procedure"] == "client_deposit"
    # an "ID" seen only after the acceptance window (the cash hand-over) does not count as shown
    assert "T15" in types(run(tl, Fake(id_document_shown=lambda r: (r.t > b + 0.5, 0.9))))
    # ID shown in the window -> no T15
    assert "T15" not in types(run(tl, Fake(id_document_shown=lambda r: (r.t < 6, 0.9))))
    # collector handover (bag on the desk) -> never T15
    assert "T15" not in types(run(tl, Fake(id_document_shown=False, bag_on_desk=True)))
    # client already there at the clip start: the ID may have been shown before the recording ->
    # still T15 (recall-first), at a lower fact_conf, still a violation
    tl0 = make_tl(17.0, cps=[("visitor_arrives_at_desk", 9.3, [3])], tracks=[(1, 0, 17), (3, 0.0, 17.0)],
                  n_persons=lambda t: 2)
    e0 = [e for e in run(tl0, Fake(id_document_shown=False)) if e.type == "T15"]
    assert e0 and e0[0].extra["client_at_clip_start"] and e0[0].fact_conf < t15[0].fact_conf and E.verdict(e0)


def test_weak_paths_clear_threshold_at_no_logprob_conf():  # review I1: VLM conf is 0.7 w/o logprobs
    c = 0.7
    tl0 = make_tl(17.0, cps=[("visitor_arrives_at_desk", 9.3, [3])], tracks=[(1, 0, 17), (3, 0.0, 17.0)],
                  n_persons=lambda t: 2)
    ev = [e for e in run(tl0, Fake(id_document_shown=(False, c), person_count=lambda r: (True, c, 2)))
          if e.type == "T15"]
    assert ev and E.verdict(ev)  # clip-start client, weak T15
    tl = make_tl(17.5, cps=[("person_exits", 16.8, [2])], tracks=[(1, 0, 17.5), (2, 0.0, 16.8)])
    fake = Fake(person_count=lambda r: (None, c, 2 if r.t < 16.8 else 1), counting_machine_used=lambda r: (r.t < 16.0, c))
    t7 = [e for e in run(tl, fake) if e.type == "T7"]
    assert t7 and E.verdict(t7)  # weak T7 (count while the client leaves)
    for fake in (Fake(cash_in_hand_to_body=lambda r: (r.t > 10, c)), Fake(cup_or_food=lambda r: (r.t < 12, c))):
        tlx = service_tl() if "cash_in_hand_to_body" in fake.answers else make_tl(25.0, tracks=[(1, 0, 25)])
        assert E.verdict(run(tlx, fake))


def test_t15_id_answer_on_banknotes_does_not_count():  # PC run: id:1 "yes" = the cash hand-over
    tl = service_tl()
    a, b = E.ServiceRule.id_window(E.Ctx(tl).visits[0])
    late = lambda r: (r.t > a + (b - a) / 2, 0.99)  # noqa: E731 - "ID" only in the 2nd half of the window
    assert "T15" not in types(run(tl, Fake(id_document_shown=late)))  # real ID (no cash yet)
    ev = [e for e in run(tl, Fake(id_document_shown=late, cash_on_desk=late)) if e.type == "T15"]
    assert ev and ev[0].extra["id_answer_was_banknotes"] and ev[0].fact_conf <= E.WEAK_FACT


def test_t15_needs_decided_id_answers():  # review M5: an undecided ID probe never makes a T15
    assert "T15" not in types(run(service_tl(), Fake(id_document_shown=(None, 0.0))))


def test_collector_bag_in_any_frame():  # review M1: X-16 reencode
    tl = service_tl()
    a, b = 2.0, 17.0
    only_late_bag = lambda r: (r.t > 2 + 0.6 * 15, 0.9)  # noqa: E731
    assert "T15" not in types(run(tl, Fake(id_document_shown=False, bag_on_desk=only_late_bag)))


def test_phone_in_hand_box_edge():  # review M5
    tl = make_tl(5.0, tracks=[(1, 0, 5)])
    n = len(tl["series"]["t"])
    tl["series"]["persons"] = [[[1, "cashier", [0.3, 0.3, 0.6, 0.9]]]] * n
    tl["series"]["objects"] = [[["cell phone", 0.55, 0.85, 0.65, 0.95, 0.8]]] * n  # centre (0.6, 0.9) on the corner
    from forte_detector.probes.base import ProbeRequest

    assert E.phone_in_hand(E.Ctx(tl), ProbeRequest("phone_visible", 2.0))
    tl["series"]["objects"] = [[["cell phone", 0.6, 0.85, 0.7, 0.95, 0.8]]] * n  # centre just outside
    assert not E.phone_in_hand(E.Ctx(tl), ProbeRequest("phone_visible", 2.0))
    tl["series"]["objects"] = [[["cell phone", 0.4, 0.5, 0.45, 0.55, 0.2]]] * n  # inside but low score
    assert not E.phone_in_hand(E.Ctx(tl), ProbeRequest("phone_visible", 2.0))


def test_partial_vlm_failure_is_flagged(patched_full):  # review M3
    def half(video_path, reqs, *, timeline, transform=None, calibration=None):
        out = Fake()(video_path, reqs, timeline=timeline)
        for j, r in enumerate(out):
            if j % 2 and r.cue not in E.CV_CUES:
                r.value, r.raw = None, {"error": "vlm_unavailable"}
        return out

    r = patched_full.analyze("X-24.mov", runner=half, calibrate_fn=lambda *a: {})
    st = r.timeline["probes_status"]
    assert st["partial"] and st["note"] and not r.degraded and r.stage_times["partial_vlm"] == 1.0


def test_probes_used_includes_negative_answers(patched_full):  # review I2: T15 relies on ID "no"
    from forte_detector.analyzers import full

    tl = service_tl()
    full_perceive = full.perceive
    try:
        full.perceive = lambda *a, **k: dict(tl, meta={"cached": True})
        r = full.analyze("X-2.mov", runner=Fake(id_document_shown=False), calibrate_fn=lambda *a: {})
    finally:
        full.perceive = full_perceive
    t15 = [e for e in r.events if e.type == "T15"]
    assert t15 and any(u["cue"] == "id_document_shown" and u["value"] is False for u in t15[0].extra["probes_used"])


def test_t15_checks_each_client():  # X-39: two sequential clients, the second shows no ID
    tl = make_tl(78.0, cps=[("person_enters", 2.0, [2]), ("visitor_arrives_at_desk", 8.5, [2]),
                            ("person_exits", 39.0, [2]), ("person_enters", 45.0, [17]),
                            ("visitor_arrives_at_desk", 51.0, [17])],
                 tracks=[(1, 0, 78), (2, 2.0, 39.0), (17, 45.0, 77.0)], n_persons=lambda t: 2)
    ev = run(tl, Fake(id_document_shown=lambda r: (r.t < 30, 0.9)))
    assert [e.timecode_sec for e in ev if e.type == "T15"] == [51.0]


def test_t5_single_late_open_probe_is_weak():  # X-33-like: one CV 'open' after the cashier settled
    t_first = E.DoorRule().times(E.Ctx(door_tl(False)))[0]
    _only_weak(run(door_tl(False), Fake(door_open=lambda r: (r.t > t_first + 5, 0.9))), "T5")


def test_t2_single_probe_weak_two_probes_strong():  # multi-frame 'any' voting across the change
    tl = client_change_tl()
    one = [e for e in run(tl, Fake(cash_on_desk=lambda r: (57 < r.t < 59, 0.9))) if e.type == "T2"]
    assert one and one[0].extra["strength"] == "weak" and E.verdict(one)
    two = [e for e in run(tl, Fake(cash_on_desk=lambda r: (r.t > 55, 0.9))) if e.type == "T2"]
    assert two and two[0].extra["strength"] == "strong" and two[0].fact_conf > one[0].fact_conf
    span = [e for e in run(tl, Fake(cash_on_desk=lambda r: (r.t < 53 or 57 < r.t < 59, 0.9))) if e.type == "T2"]
    assert span and span[0].extra["spans_change"] and span[0].extra["strength"] == "strong"


def test_t7_machine_while_client_leaving_is_weak():  # X-34: perception exit lags the door
    tl = make_tl(17.5, cps=[("person_exits", 16.8, [2])], tracks=[(1, 0, 17.5), (2, 0.0, 16.8)])
    fake = Fake(person_count=lambda r: (None, 0.9, 2 if r.t < 16.8 else 1),
                counting_machine_used=lambda r: (r.t < 16.0, 0.9))
    _only_weak(run(tl, fake), "T7")


# ----------------------------------------------------------------------------- engine plumbing

def test_module_propose_decide_roundtrip():
    tl = departure_tl()
    reqs = E.propose(tl, {})
    assert all(r.cue in CUES for r in reqs)
    res = Fake(cash_on_desk=True)("v.mov", reqs, timeline=tl)
    assert types(E.decide(tl, {}, res)) == ["T1"]
    with pytest.raises(ValueError):
        E.decide(tl, {}, res[:-1])


def test_vlm_budget_respected():
    tl = client_change_tl()
    cfg = E.RulesConfig(max_vlm_requests=8)
    reqs = E.propose(tl, {}, cfg)
    assert sum(r.cue not in E.CV_CUES for r in reqs) <= 8


def test_merge_and_cap():
    evs = [E.make_event("T1", 8.0, 0.9, "a"), E.make_event("T1", 11.0, 0.7, "b"),
           E.make_event("T4", 9.0, 0.8, "c"), E.make_event("T3", 9.0, 0.6, "d"), E.make_event("T12", 2, 0.5, "e")]
    out = E.finalize(evs)
    assert len(out) == 3 and sorted(e.type for e in out) == ["T1", "T3", "T4"] and out[0].type == "T1"
    assert out[0].end_sec >= 11.0


def test_never_emits_unknown_type():
    with pytest.raises(KeyError):
        E.make_event("T42", 1.0, 0.9, "x")


# ----------------------------------------------------------------------------- full analyzer

@pytest.fixture
def patched_full(monkeypatch):
    from forte_detector.analyzers import full

    tl = departure_tl()
    monkeypatch.setattr(full, "perceive", lambda *a, **k: dict(tl, meta={"cached": True}))
    return full


def test_full_analyze_streams_events(patched_full):
    seen, prog = [], []
    r = patched_full.analyze("X-24.mov", lambda p, m: prog.append((p, m)), runner=Fake(cash_on_desk=True),
                             calibrate_fn=lambda v, tl, tr: {"desk": None}, on_event=seen.append)
    assert r.verdict == "violation" and types(r.events) == ["T1"]
    assert [e.type for e in seen] == ["T1"] and prog[-1] == (1.0, "done")
    assert all(0 <= p <= 1 for p, _ in prog) and r.probes
    json.dumps(r.to_dict(), ensure_ascii=False)


def test_full_analyze_deadline_skips_probes(patched_full):
    fake = Fake(cash_on_desk=True)
    r = patched_full.analyze("X-24.mov", runner=fake, calibrate_fn=lambda *a: {},
                             deadline=time.monotonic() - 1)
    assert r.verdict == "no_violation" and r.events == [] and fake.calls == []
    assert r.timeline["deadline_hit"]


def _truncated(tl, why="deadline", video_sec=3600.0):
    return dict(tl, meta={"cached": False, "truncated": why, "video_duration": video_sec})


def test_truncated_timeline_is_late_no_calibration_vlm_deadline_now(monkeypatch):
    """(1) + (2): perception cut at deadline - 3 s -> no calibration, the runner's VLM deadline is
    now (CV cues still asked), and the result says deadline_hit / truncated."""
    from forte_detector.analyzers import full

    tl = _truncated(departure_tl())
    monkeypatch.setattr(full, "perceive", lambda *a, **k: tl)
    seen, cal = [], []

    def runner(video_path, requests, *, timeline, transform=None, calibration=None, deadline=None):
        seen.append((deadline, time.monotonic(), [r.cue for r in requests]))
        return [ProbeResult(r.cue, r.t, None, 0.0, [r.t], "", {"why": "deadline", "skipped": "deadline"})
                if r.cue not in E.CV_CUES else ProbeResult(r.cue, r.t, False, 0.9, [r.t], "cv", {})
                for r in requests]

    r = full.analyze("X-24.mov", runner=runner, calibrate_fn=lambda *a: cal.append(1) or {},
                     deadline=time.monotonic() + 100)
    assert cal == []  # calibration skipped
    assert seen and all(d is not None and d <= now + 1e-6 for d, now, _ in seen)
    assert any("camera_blocked" in cues for _, _, cues in seen)  # CV-only rules still run
    assert r.timeline["deadline_hit"] is True and r.timeline["truncated"] is True
    assert r.timeline["truncated_reason"] == "deadline"
    assert r.timeline["analyzed_sec"] == 14.5 and r.timeline["video_sec"] == 3600.0
    assert r.events == []


def test_untruncated_timeline_reports_not_truncated(patched_full):
    r = patched_full.analyze("X-24.mov", runner=Fake(cash_on_desk=True), calibrate_fn=lambda *a: {})
    assert r.timeline["truncated"] is False and types(r.events) == ["T1"]


def test_truncation_cut_is_not_end_of_clip():
    """(3): exits / room-empty at the cut, a seat leave right before it and the end-of-clip
    candidate are not departures on a truncated timeline (nothing after the cut was seen)."""
    full_ctx = E.Ctx(departure_tl())
    assert {d.source for d in full_ctx.departures} & {"exit", "empty"}
    cut = E.Ctx(_truncated(departure_tl()))
    assert cut.truncated and cut.departures == []
    # leave at 10.5 with the cut at 14.5: no return seen, but there was no time to see one
    tl = make_tl(14.5, cps=[("cashier_leaves_seat", 10.5, [1])], tracks=[(1, 0.0, 14.5)])
    assert E.Ctx(tl).departures  # untruncated: a departure candidate (leave / end of clip)
    assert E.Ctx(_truncated(tl)).departures == []
    # an earlier departure, well before the cut, is still a candidate
    tl2 = make_tl(40.0, cps=[("cashier_leaves_seat", 8.5, [1]), ("cashier_exits_room", 13.0, [1]),
                             ("room_empty_start", 13.3, [])], tracks=[(1, 0.0, 13.0)],
                  n_persons=lambda t: 0 if t >= 13.3 else 1)
    deps = E.Ctx(_truncated(tl2)).departures
    assert deps and all(d.source != "end" for d in deps)
    fake = Fake(cash_on_desk=True)
    assert types(run(_truncated(tl2), fake)) == ["T1"]
    assert types(run(_truncated(departure_tl()), Fake(cash_on_desk=True))) == []


def test_full_analyze_passes_deadline_and_stop_to_perception(monkeypatch):
    from forte_detector.analyzers import full

    tl, seen = departure_tl(), {}

    def fake_perceive(*a, **k):
        seen.update(k)
        return dict(tl, meta={"cached": True})

    monkeypatch.setattr(full, "perceive", fake_perceive)
    dl = time.monotonic() + 100
    stop = {"v": False}
    fake = Fake(cash_on_desk=True)
    r = full.analyze("X-24.mov", runner=fake, calibrate_fn=lambda *a: {}, deadline=dl, should_stop=lambda: stop["v"])
    assert seen["deadline"] == pytest.approx(dl - full.PERCEPTION_RESERVE_S)
    assert seen["should_stop"]() is False and types(r.events) == ["T1"]
    # cancelled: no calibration / probes are started (treated like a passed deadline)
    stop["v"] = True
    fake2, cal = Fake(cash_on_desk=True), []
    r2 = full.analyze("X-24.mov", runner=fake2, calibrate_fn=lambda *a: cal.append(1) or {}, deadline=dl,
                      should_stop=lambda: stop["v"])
    assert fake2.calls == [] and cal == [] and r2.events == []


def test_full_hard_vlm_cap_includes_followups(patched_full):
    fake = Fake(cash_on_desk=True)
    cfg = E.RulesConfig(max_vlm_total=4)
    r = patched_full.analyze("X-24.mov", runner=fake, calibrate_fn=lambda *a: {}, cfg=cfg)
    vlm = {(c.cue, c.t) for c in fake.calls if c.cue not in E.CV_CUES}
    assert len(vlm) <= 4 and r.timeline["n_vlm_requests"] <= 4
    assert any(c.cue in E.CV_CUES for c in fake.calls)  # CV cues are not capped
    # default cap: <= 60 VLM requests per video
    fake2 = Fake(cash_on_desk=True)
    patched_full.analyze("X-24.mov", runner=fake2, calibrate_fn=lambda *a: {})
    assert len({(c.cue, c.t) for c in fake2.calls if c.cue not in E.CV_CUES}) <= 60


def test_room_empty_at_t0_is_not_a_departure():
    tl = make_tl(20.0, cps=[("room_empty_start", 0.0, []), ("room_empty_end", 2.0, [])], tracks=[(1, 2, 20)])
    assert [d.source for d in E.Ctx(tl).departures] == ["end"]


def test_full_uses_effective_transform_for_probes(patched_full, monkeypatch):
    import forte_detector.perception as P

    rot = object()
    monkeypatch.setattr(P, "effective_transform", lambda tl, tf: rot, raising=False)
    seen = {}

    def runner(video_path, reqs, *, timeline, transform=None, calibration=None):
        seen["tf"] = transform
        return Fake()(video_path, reqs, timeline=timeline)

    patched_full.analyze("X-24.mov", runner=runner, calibrate_fn=lambda v, tl, tf: seen.setdefault("cal", tf) and {})
    assert seen["tf"] is rot and seen["cal"] is rot


def test_full_on_timeline_fires_once_even_past_deadline(patched_full):
    seen = []
    r = patched_full.analyze("X-24.mov", runner=Fake(cash_on_desk=True), calibrate_fn=lambda *a: {},
                             deadline=time.monotonic() - 1, on_timeline=seen.append)
    assert len(seen) == 1 and "checkpoints" in seen[0] and r.events == []

    def boom(tl):
        raise RuntimeError("consumer bug")

    r2 = patched_full.analyze("X-24.mov", runner=Fake(cash_on_desk=True), calibrate_fn=lambda *a: {}, on_timeline=boom)
    assert types(r2.events) == ["T1"]  # swallowed


def _words(text):
    import re

    return re.findall(r"[а-яёa-z0-9]+", text.lower())


def _ngrams(ws, n):
    return {tuple(ws[i:i + n]) for i in range(len(ws) - n + 1)}


def test_obs_adds_specifics_only_never_restates_template():
    """obs must not repeat the template wording (service renders template + obs), must be a bare
    lowercase clause, and is never empty for T10/T99."""
    tl99 = make_tl(20.0, cps=[("visitor_arrives_at_desk", 10.0, [5])], tracks=[(1, 0, 6), (5, 8, 20)])
    tl99["series"]["cashier_present"] = [t < 6 for t in tl99["series"]["t"]]
    par = make_tl(22.5, cps=[("person_enters", 1.0, [3]), ("person_enters", 16.0, [14])],
                  tracks=[(1, 0, 22.5), (3, 1.0, 22.5), (14, 16.0, 22.5)], n_persons=lambda t: 3)
    visit_count = lambda r: (None, 0.9, 2 if r.t < 13 else 1)  # noqa: E731
    scen = [
        (departure_tl(), Fake(cash_on_desk=True, bag_on_desk=True, stamps_or_journal_on_desk=True,
                              screen_on_unlocked=True, key_in_safe=True, safe_open=True)),
        (client_change_tl(), Fake(cash_on_desk=True)),
        (client_change_tl(), Fake(counting_machine_used=lambda r: (54 < r.t < 57, 0.9))),
        (par, Fake(person_count=count(3), counting_machine_used=True)),
        (short_visit_tl(), Fake(person_count=visit_count, counting_machine_used=True)),
        (short_visit_tl(), Fake(person_count=visit_count, bag_on_desk=True)),
        (door_tl(), Fake(door_open=True)),
        (make_tl(11.5, tracks=[(1, 0, 11.5)]), Fake(safe_open=True)),
        (make_tl(25.0, tracks=[(1, 0, 25)]), Fake(handbag_or_coat_on_desk=True, cup_or_food=True, phone_visible=True)),
        (make_tl(20.0, tracks=[(1, 0, 20)]), Fake(camera_blocked=True)),
        (service_tl(), Fake(cash_in_hand_to_body=True, id_document_shown=False)),
    ]
    events = []
    for tl, fake in scen:
        ctx = E.Ctx(tl)
        for item in E.plan(ctx):
            res = fake("v.mov", item.requests, timeline=tl)
            events += item.rule.decide(ctx, list(zip(item.requests, res)))
    events += E.UnexplainedAccessRule().decide(E.Ctx(tl99), [])
    seen_types = {e.type for e in events}
    assert {"T1", "T1b", "T2", "T3", "T4", "T5", "T6", "T7", "T9", "T10", "T11", "T12", "T13", "T14", "T15",
            "T99"} <= seen_types
    for e in events:
        tpl = tx.load_rules()[e.type]["description_template"].replace("{t}", " ").replace("{obs}", " ")
        title = tx.title(e.type)
        assert e.obs and e.obs == e.obs.strip() and not e.obs.startswith(",") and not e.obs.endswith(".")
        assert e.obs[0] == e.obs[0].lower(), e.obs
        shared = _ngrams(_words(e.obs), 3) & (_ngrams(_words(tpl), 3) | _ngrams(_words(title), 3))
        assert not shared, (e.type, e.obs, shared)
        # rendered text: no glued tokens (digits+letters, e.g. "00:48одежда"), no double punctuation
        import re

        for text in (e.obs, e.description_ru):
            assert not re.search(r"\d[а-яёА-ЯЁa-zA-Z]|[а-яёА-ЯЁa-zA-Z]\d", text), (e.type, text)
            assert not re.search(r"[,.;:]\s*[,.;]|\s[,.]", text), (e.type, text)
            assert "{" not in text and "}" not in text, (e.type, text)


def test_persistent_state_missing_probes_never_lower_the_bar():  # review #2
    tl = make_tl(25.0, tracks=[(1, 0, 25)])
    ctx = E.Ctx(tl)
    rule = [r for r in E.default_rules() if r.name == "phone"][0]
    reqs = rule.propose(ctx)[:1]  # only 1 of the 3 planned probes was run
    res = Fake(phone_visible=(True, 1.0))("v.mov", reqs, timeline=tl)
    assert rule.decide(ctx, list(zip(reqs, res))) == []


def test_full_cap_drops_whole_rule_groups(patched_full):  # review #2
    fake = Fake(phone_visible=(True, 1.0))
    r = patched_full.analyze("X-24.mov", runner=fake, calibrate_fn=lambda *a: {},
                             cfg=E.RulesConfig(max_vlm_total=1))
    assert all(e.type != "T13" for e in r.events)
    vlm_calls = [c for c in fake.calls if c.cue not in E.CV_CUES]
    assert len(vlm_calls) <= 1
    assert not any(c.context["rule"] == "phone" for c in fake.calls)  # 3-probe group cannot fit -> skipped
    assert any(p.get("skipped") == "vlm_cap" for p in r.probes)


def test_accumulator_policy_matches_finalize():  # review #3
    from forte_detector.analyzers.full import _Accumulator

    streamed = []
    acc = _Accumulator(E.RulesConfig(), streamed.append)
    evs = [E.make_event("T1", 8.0, 0.6, "a"), E.make_event("T4", 30.0, 0.7, "b"), E.make_event("T3", 50.0, 0.65, "c"),
           E.make_event("T1", 10.0, 0.95, "d"),  # stronger, same type within 5 s -> wins the merge
           E.make_event("T15", 70.0, 0.9, "e")]  # late but stronger -> displaces the weakest
    for e in evs:
        acc.add(e)
    final = acc.final()
    assert [(e.type, e.timecode_sec) for e in final] == [(e.type, e.timecode_sec) for e in E.finalize(evs)]
    assert ("T1", 10.0) in [(e.type, e.timecode_sec) for e in final] and final[0].start_sec == 8.0
    assert "T15" in [e.type for e in final] and "T3" not in [e.type for e in final]
    assert {e.type for e in final} <= {e.type for e in streamed}  # streamed set is a superset
    cut, merged = acc.displaced()
    assert [e.type for e in cut] == ["T3"]  # cut by the cap
    assert [(e.type, e.timecode_sec) for e in merged] == [("T1", 8.0)]  # folded into the stronger T1


def test_merged_events_are_not_reported_as_low_conf(patched_full):  # fix round 2 #2
    from forte_detector.analyzers import full

    seen = []

    class Two(E.Rule):
        name = "two"

        def decide(self, ctx, pairs):
            return [E.make_event("T1", 8.0, 0.9, "a"), E.make_event("T1", 10.0, 0.95, "b")]

    orig = E.default_rules
    try:
        full.engine.default_rules = lambda: [Two()]
        r = full.analyze("X-24.mov", runner=Fake(), calibrate_fn=lambda *a: {}, on_event=seen.append)
    finally:
        full.engine.default_rules = orig
    assert [e.timecode_sec for e in r.events] == [10.0]
    assert r.timeline["low_conf_events"] == [] and [e["timecode_sec"] for e in r.timeline["merged_events"]] == [8.0]


def test_event_key_is_public():
    from forte_detector.analyzers.full import event_key

    e = E.make_event("T4", 7.0, 0.8, "x", source="rule:y")
    assert event_key(e) == ("T4", 7.0, "rule:y")


def test_not_degraded_on_budget_skips_or_none_answers(patched_full):  # fix round 2 #1
    def budget(video_path, reqs, *, timeline, transform=None, calibration=None):
        return [ProbeResult(q.cue, q.t, None, 0.0, [q.t], "vlm", {"skipped": "budget"}) for q in reqs]

    r = patched_full.analyze("X-24.mov", runner=budget, calibrate_fn=lambda *a: {})
    st = r.timeline["probes_status"]
    assert not r.degraded and st["budget_skipped"] == st["requested"] > 0

    def undecided(video_path, reqs, *, timeline, transform=None, calibration=None):
        return [ProbeResult(q.cue, q.t, None, 0.4, [q.t], "vlm", {"answer": "unclear"}) for q in reqs]

    assert not patched_full.analyze("X-24.mov", runner=undecided, calibrate_fn=lambda *a: {}).degraded


def test_degraded_when_vlm_unavailable(patched_full, monkeypatch):  # review #4
    monkeypatch.setattr(patched_full, "_default_runner", lambda: (None, None))
    r = patched_full.analyze("X-24.mov")
    assert r.degraded and r.timeline["probes_status"]["unavailable"] and r.stage_times["degraded"] == 1.0

    def down(video_path, reqs, *, timeline, transform=None, calibration=None):
        return [ProbeResult(q.cue, q.t, None, 0.0, [q.t], "vlm", {"error": "vlm_unavailable"}) for q in reqs]

    assert patched_full.analyze("X-24.mov", runner=down, calibrate_fn=lambda *a: {}).degraded
    ok = patched_full.analyze("X-24.mov", runner=Fake(), calibrate_fn=lambda *a: {})
    assert not ok.degraded and ok.timeline["probes_status"]["answered"] > 0


def test_deadline_passed_to_runner_and_not_degraded(patched_full):  # review #5 + Task 3 91a439b
    seen = {}

    def runner(video_path, reqs, *, timeline, transform=None, calibration=None, deadline=None):
        seen["deadline"] = deadline
        return [ProbeResult(q.cue, q.t, None if q.cue not in E.CV_CUES else False, 0.0, [q.t], "vlm",
                            {"why": "deadline"}) for q in reqs]

    dl = time.monotonic() - 1
    r = patched_full.analyze("X-24.mov", runner=runner, calibrate_fn=lambda *a: {}, deadline=dl)
    assert seen["deadline"] == dl  # called even past the deadline (CV cues still answer)
    assert not r.degraded and r.timeline["deadline_hit"] and r.timeline["probes_status"]["deadline_skipped"] > 0


def test_end_of_clip_needs_a_cashier():  # review minor: empty room all clip
    tl = make_tl(20.0, tracks=[], n_persons=lambda t: 0, cashier=())
    assert E.Ctx(tl).departures == []


def test_door_t6_served_client_is_not_an_outsider():  # review minor
    tl = door_tl()
    tl["checkpoints"].append({"type": "visitor_arrives_at_desk", "t": 13.0, "track_ids": [7]})
    tl["checkpoints"].sort(key=lambda c: c["t"])
    assert types(run(tl, Fake(door_open=True))) == ["T5"]  # 2 people, entrant sits at the desk
    assert types(run(tl, Fake(door_open=True, person_count=count(3)))) == ["T5", "T6"]


def test_t99_unknown_cashier_presence_is_skipped():  # review minor
    tl = make_tl(20.0, cps=[("visitor_arrives_at_desk", 10.0, [5])], tracks=[(1, 0, 6), (5, 8, 20)])
    tl["series"]["cashier_present"] = [None] * len(tl["series"]["t"])
    assert E.UnexplainedAccessRule().decide(E.Ctx(tl), []) == []


def test_people_plural():
    assert [E.people(n) for n in (1, 2, 3, 5, 11, 12, 21, 22)] == [
        "1 человек", "2 человека", "3 человека", "5 человек", "11 человек", "12 человек", "21 человек", "22 человека"]


def test_events_carry_supporting_probe_rois(patched_full):  # Task 6 prep: ROI/boxes per probe
    def runner(video_path, reqs, *, timeline, transform=None, calibration=None):
        out = Fake(cash_on_desk=True)(video_path, reqs, timeline=timeline)
        for r in out:
            r.raw["roi"] = [0.1, 0.2, 0.5, 0.6]
        return out

    r = patched_full.analyze("X-24.mov", runner=runner, calibrate_fn=lambda *a: {})
    used = r.events[0].extra["probes_used"]
    assert any(u["cue"] == "cash_on_desk" and u["roi"] == [0.1, 0.2, 0.5, 0.6] for u in used)
    assert any(u["cue"] == "person_count" and u["count"] == 0 for u in used)


def test_full_analyze_without_runner(patched_full, monkeypatch):
    monkeypatch.setattr(patched_full, "_default_runner", lambda: (None, None))
    r = patched_full.analyze("X-24.mov")
    assert r.verdict == "no_violation" and all(p["result"]["value"] is None for p in r.probes)


def _video(tmp_path, sub, content: bytes):
    d = tmp_path / sub
    d.mkdir()
    p = d / "X-24.mov"
    p.write_bytes(content)
    return str(p)


def test_full_probe_disk_cache(patched_full, monkeypatch, tmp_path):
    monkeypatch.setenv("FORTE_EVAL_CACHE", "1")
    monkeypatch.setenv("FORTE_CACHE_DIR", str(tmp_path / "cache"))
    v = _video(tmp_path, "a", b"video-a")
    f1 = Fake(cash_on_desk=True)
    r1 = patched_full.analyze(v, runner=f1, calibrate_fn=lambda *a: {})
    f2 = Fake()  # would answer False everywhere: cached answers must win
    r2 = patched_full.analyze(v, runner=f2, calibrate_fn=lambda *a: {})
    assert f1.calls and not f2.calls
    assert types(r1.events) == types(r2.events) == ["T1"]
    assert (tmp_path / "cache" / "probes").is_dir()


def test_cache_keyed_by_content_not_name(patched_full, monkeypatch, tmp_path):
    monkeypatch.setenv("FORTE_EVAL_CACHE", "1")
    monkeypatch.setenv("FORTE_CACHE_DIR", str(tmp_path / "cache"))
    a = _video(tmp_path, "a", b"video-a")
    b = _video(tmp_path, "b", b"video-b")  # same file name, different bytes
    ra = patched_full.analyze(a, runner=Fake(cash_on_desk=True), calibrate_fn=lambda *x: {})
    fb = Fake()
    rb = patched_full.analyze(b, runner=fb, calibrate_fn=lambda *x: {})
    assert fb.calls  # not served from a's cache
    assert types(ra.events) == ["T1"] and rb.events == []


def test_cache_off_by_default(patched_full, monkeypatch, tmp_path):
    monkeypatch.delenv("FORTE_EVAL_CACHE", raising=False)
    monkeypatch.setenv("FORTE_CACHE_DIR", str(tmp_path / "cache"))
    v = _video(tmp_path, "a", b"video-a")
    patched_full.analyze(v, runner=Fake(cash_on_desk=True), calibrate_fn=lambda *x: {})
    f2 = Fake()
    r2 = patched_full.analyze(v, runner=f2, calibrate_fn=lambda *x: {})
    assert f2.calls and r2.events == [] and not (tmp_path / "cache").exists()


@pytest.mark.parametrize("n_end,asked", [(0, True), (1, False)])
def test_full_staged_fallback_asks_desk_cues_only_if_empty(monkeypatch, n_end, asked):
    from forte_detector.analyzers import full

    tl = make_tl(20.8, cps=[("cashier_leaves_seat", 10.5, [1])], tracks=[(1, 0.0, 20.8)])
    monkeypatch.setattr(full, "perceive", lambda *a, **k: dict(tl, meta={"cached": True}))
    fake = Fake(person_count=count(n_end), bag_on_desk=True)
    r = full.analyze("X-15.mov", runner=fake, calibrate_fn=lambda *a: {})
    assert any(c.cue == "bag_on_desk" and c.t > 15 for c in fake.calls) == asked
    assert (types(r.events) == ["T1"]) == asked
