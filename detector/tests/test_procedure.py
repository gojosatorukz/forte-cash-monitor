"""meta["procedure"]: operation type + step statuses from synthetic detector results (no models)."""
from __future__ import annotations

import json
import re

from forte_detector import procedure as P
from forte_detector.schema import AnalysisResult, Event

TYPE_ID = re.compile(r"^T(\d{1,2}|1b|99)$")


def probe(rule, cue, t, value=None, conf=0.9, key="", count=None, skipped=None):
    return {"rule": rule, "request": {"cue": cue, "t": t, "extra_ts": [], "key": key},
            "result": {"value": value, "conf": conf, "source": "vlm", "count": count, "skipped": skipped,
                       "error": None}}


def ev(type_, t, conf=0.6, obs="наблюдение"):
    return Event(type=type_, timecode_sec=t, confidence=conf, obs=obs, title_ru=type_)


def tl_with_visitor(a=1.0, b=13.0, **kw):
    return {"duration": 20.0, "roles": {"cashier_tracks": [1], "visitor_tracks": [2]},
            "checkpoints": [{"type": "person_enters", "t": a, "track_ids": [2]},
                            {"type": "person_exits", "t": b, "track_ids": [2]}], **kw}


def deposit_probes(id_answer=False, bag=False):
    return [probe("service", "person_count", 7.0, value=2, count=2, key="0:count"),
            probe("service", "bag_on_desk", 5.0, value=bag, key="0:bag"),
            probe("service", "id_document_shown", 3.0, value=id_answer, key="0:id@0"),
            probe("service", "cash_in_hand_to_body", 8.0, value=False, key="0:hand"),
            probe("visit_exit", "counting_machine_used", 14.0, value=False, key="0:machine"),
            probe("departure", "cash_on_desk", 19.5, value=False, key="end:cash"),
            probe("door", "door_open", 19.0, value=False, key="door:0"),
            probe("camera", "camera_blocked", 10.0, value=False, key="cam:0")]


def steps_by_id(proc):
    return {s["id"]: s for s in proc["steps"]}


def test_client_operation_with_t15_violation_and_ok_steps():
    r = AnalysisResult(video="x", verdict="violation", events=[ev("T15", 4.0, obs="карточка не видна")],
                       timeline=tl_with_visitor(), probes=deposit_probes())
    proc = P.build_procedure(r)
    # cash direction is not observable: never «Приём наличных» for a plain client visit
    assert proc["type"] == "client_operation"
    assert proc["title_ru"] == "Обслуживание клиента (приём или выдача наличных)"
    assert "00:01–00:13" in proc["basis_ru"] and "сумка инкассатора не обнаружена" in proc["basis_ru"]
    assert "направление движения наличных по видео не определяется" in proc["basis_ru"]
    s = steps_by_id(proc)
    assert s["identification"]["status"] == "violation" and s["identification"]["t_sec"] == 4.0
    assert s["identification"]["rule"] == "НБРК №231 п. 19 пп. 1); НБРК №231 п. 31 пп. 1)"
    assert s["count_in_presence"]["status"] == "ok"  # machine probe answered, no T7
    assert s["count_in_presence"]["rule"] == "НБРК №231 п. 20 ч. 1; НБРК №231 п. 31 пп. 4)"
    assert s["authenticity"]["status"] == "not_checked"
    assert s["authenticity"]["note_ru"] == "на видео не проверяется автоматически"
    assert s["documents"]["status"] == "manual"
    assert s["documents"]["rule"] == "НБРК №231 п. 19 пп. 5)–6); НБРК №231 п. 31 пп. 5)–6)"
    assert s["full_amount"]["status"] == "ok"
    assert s["cash_stored"]["status"] == "ok" and s["workplace_secured"]["status"] == "ok"
    assert s["camera"]["status"] == "ok"
    assert s["phone"]["status"] == "not_checked"  # phone rule never ran
    assert s["cash_stored"]["rule"].startswith("НБРК №231 п. 20 ч. 2")


def test_t10_event_is_risk_and_weak_event_is_risk():
    r = AnalysisResult(video="x", verdict="violation", events=[ev("T10", 9.0)],
                       timeline=tl_with_visitor(low_conf_events=[{"type": "T13", "timecode_sec": 6.0}]),
                       probes=deposit_probes(id_answer=True))
    s = steps_by_id(P.build_procedure(r))
    assert s["full_amount"]["status"] == "risk" and s["full_amount"]["type_ids"] == ["T10"]
    assert s["identification"]["status"] == "ok"
    assert s["phone"]["status"] == "risk" and s["phone"]["t_sec"] == 6.0


def test_ok_requires_answers_not_skipped_probes():
    probes = [probe("service", "person_count", 7.0, value=2, count=2, key="0:count"),
              probe("service", "id_document_shown", 3.0, value=None, skipped="deadline", key="0:id@0")]
    r = AnalysisResult(video="x", verdict="no_violation", events=[], timeline=tl_with_visitor(), probes=probes)
    s = steps_by_id(P.build_procedure(r))
    assert s["identification"]["status"] == "not_checked"


def test_collection_handover_from_bag_and_t9():
    probes = deposit_probes(bag=True)
    r = AnalysisResult(video="x", verdict="violation", events=[ev("T9", 7.0)], timeline=tl_with_visitor(),
                       probes=probes)
    proc = P.build_procedure(r)
    assert proc["type"] == "collection_handover" and proc["confidence"] == 0.85
    s = steps_by_id(proc)
    assert s["bag_check"]["status"] == "violation" and s["bag_check"]["rule"] == "НБРК №231 п. 24 пп. 1), 3), 4)"
    assert s["bag_amount"]["status"] == "not_checked" and s["bag_amount"]["rule"] == ""
    assert "identification" not in s


def test_no_client_and_unknown():
    r = AnalysisResult(video="x", verdict="no_violation", events=[],
                       timeline={"roles": {"visitor_tracks": []}, "checkpoints": []},
                       probes=[probe("departure", "cash_on_desk", 5.0, value=False)])
    proc = P.build_procedure(r)
    assert proc["type"] == "no_client" and proc["basis_ru"] == "посетителей в кадре нет"
    assert steps_by_id(proc)["cash_stored"]["status"] == "ok"
    r2 = AnalysisResult(video="x", verdict="no_violation", events=[], timeline=tl_with_visitor(), probes=[])
    assert P.build_procedure(r2)["type"] == "unknown"


def test_every_event_type_appears_in_a_step():
    r = AnalysisResult(video="x", verdict="violation", events=[ev("T7", 14.0), ev("T5", 2.0)],
                       timeline={"roles": {"visitor_tracks": []}}, probes=[])
    proc = P.build_procedure(r)  # no_client has no T7 step -> an extra step is added
    ids = {t for s in proc["steps"] for t in s["type_ids"]}
    assert {"T7", "T5"} <= ids
    assert steps_by_id(proc)["other_T7"]["status"] == "violation"


def test_limits_statuses_and_type_id_format():
    for typ, steps in P.PROCEDURE_STEPS.items():
        assert len(steps) <= P.MAX_STEPS
    events = [ev(t, 1.0 + i) for i, t in enumerate(["T1", "T1b", "T2", "T3", "T4", "T5", "T6", "T7", "T9", "T10",
                                                     "T11", "T12", "T13", "T14", "T15", "T99"])]
    r = AnalysisResult(video="x", verdict="violation", events=events, timeline=tl_with_visitor(),
                       probes=deposit_probes())
    proc = P.build_procedure(r)
    assert len(proc["steps"]) <= 30
    assert len(json.dumps(proc, ensure_ascii=False).encode("utf-8")) <= 32 * 1024
    assert proc["type"] in P.TITLES and 0.0 <= proc["confidence"] <= 1.0
    for s in proc["steps"]:
        assert s["status"] in ("ok", "violation", "risk", "not_checked", "manual")
        assert all(TYPE_ID.match(t) for t in s["type_ids"]), s["type_ids"]
        assert set(s) == {"id", "title_ru", "status", "t_sec", "type_ids", "rule", "note_ru"}
    assert {t for s in proc["steps"] for t in s["type_ids"]} >= {e.type for e in events}


def test_citations_only_from_rules_json():
    from forte_detector.rules import taxonomy

    pts = {c["point"] for v in taxonomy.load_rules().values() for c in v.get("citations", [])}
    r = AnalysisResult(video="x", verdict="no_violation", events=[], timeline=tl_with_visitor(),
                       probes=deposit_probes())
    for s in P.build_procedure(r)["steps"]:
        for part in filter(None, s["rule"].split("; ")):
            if part in P.VERIFIED_EXTRA_RULES:
                continue
            assert any(part.endswith(p) for p in pts), part


def test_extra_points_are_quoted_in_regulations():
    """Every non-rules.json point we cite is quoted verbatim in docs/regulations.md (Addendum)."""
    from pathlib import Path

    reg = Path(__file__).resolve().parents[1].parents[0] / "docs" / "regulations.md"
    candidates = [Path("/home/datamata/Projects/datathon2026/docs/regulations.md"), reg]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        import pytest

        pytest.skip("regulations.md not available")
    text = path.read_text(encoding="utf-8")
    quotes = {P.DOC_STEPS_RULE: ["подписывает все экземпляры приходного кассового документа",
                                 "выдает клиенту документ, подтверждающий проведение кассовой операции по приему"],
              P.PAYOUT_DOCS_RULE: ["подписывает все экземпляры расходного кассового документа",
                                   "подтверждающий проведение кассовой операции по выдаче наличных денег"],
              P.PAYOUT_ID_RULE: ["1) проводит визуальную идентификацию клиента по данным документа"],
              P.PAYOUT_COUNT_RULE: ["пересчитывает подготовленные к выдаче наличные деньги в присутствии клиента"]}
    assert set(quotes) == set(P.VERIFIED_EXTRA_RULES)
    for rule, qs in quotes.items():
        for q in qs:
            assert q in text, (rule, q)


def test_client_deposit_never_inferred_without_direction_cue():
    for bag in (False,):
        r = AnalysisResult(video="x", verdict="violation", events=[ev("T15", 4.0), ev("T7", 14.0)],
                           timeline=tl_with_visitor(), probes=deposit_probes(bag=bag))
        assert P.infer_type(r)[0] == "client_operation"


def test_cv_only_t99_step_ok_unless_truncated():
    r = AnalysisResult(video="x", verdict="no_violation", events=[], timeline=tl_with_visitor(), probes=[])
    assert steps_by_id(P.build_procedure(r))["other"]["status"] == "ok"
    r.timeline["truncated"] = True
    assert steps_by_id(P.build_procedure(r))["other"]["status"] == "not_checked"


def test_build_never_raises_on_garbage():
    class Weird:
        events = None
        probes = [None, 3, {"rule": "service"}]
        timeline = {"roles": None, "checkpoints": [{"type": "person_enters"}]}

    proc = P.build_procedure(Weird())
    assert proc["type"] in P.TITLES
