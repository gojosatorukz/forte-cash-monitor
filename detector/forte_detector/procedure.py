"""Cash-operation procedure summary for the service UI (`AnalysisResult.meta["procedure"]`).

Read-only over a finished `forte_detector.schema.AnalysisResult`: it never changes verdicts, events or
timecodes. The operation type is inferred from what the pipeline already knows (visitor tracks and
checkpoints, the service / visit-exit rules' probes, events), then each required visible step of
that operation (docs/07_procedures_coverage.md) gets a status:

* "violation" - an emitted event of the step's types;
* "risk"      - the step's dedicated risk check fired (T10), or only a weak / capped / merged event exists;
* "ok"        - the rule behind the step actually ran on a relevant window (its probes were answered)
                and found nothing;
* "not_checked" - no rule for the step, or the rule did not run / got no answers;
* "manual"    - a documentation step a specialist checks (not visible on video).

`rule` strings come only from taxonomy/rules.json citations, plus the points that docs/regulations.md
quotes verbatim (adilet-verified addendum: НБРК №231 п.19 пп.5)-6), п.31 пп.1), 4), 5)-6)).

A confirmed client visit is "client_operation": the cash direction (приём vs выдача) is not observable
on video, so the steps cite both procedures where they differ; "client_deposit" needs a direction cue
(none exists yet, so it is never emitted).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

STATUSES = ("ok", "violation", "risk", "not_checked", "manual")
MAX_STEPS = 30
MAX_BYTES = 32 * 1024

# Quoted verbatim in docs/regulations.md (Addendum, adilet V1900019680) but not detector rule citations.
DOC_STEPS_RULE = "НБРК №231 п. 19 пп. 5)–6)"  # приём: подпись/штамп, документ клиенту
PAYOUT_DOCS_RULE = "НБРК №231 п. 31 пп. 5)–6)"  # выдача: то же
PAYOUT_ID_RULE = "НБРК №231 п. 31 пп. 1)"  # выдача: идентификация
PAYOUT_COUNT_RULE = "НБРК №231 п. 31 пп. 4)"  # выдача: пересчёт в присутствии клиента
VERIFIED_EXTRA_RULES = (DOC_STEPS_RULE, PAYOUT_DOCS_RULE, PAYOUT_ID_RULE, PAYOUT_COUNT_RULE)
DIRECTION_NOTE = "направление движения наличных по видео не определяется"

TITLES = {
    "client_operation": "Обслуживание клиента (приём или выдача наличных)",
    "client_deposit": "Приём наличных от клиента",
    "client_withdrawal": "Выдача наличных клиенту",
    "collection_handover": "Приём сумки от инкассатора",
    "no_client": "Работа кассира без клиента",
    "unknown": "Операция не определена",
}


@dataclass(frozen=True)
class StepSpec:
    id: str
    title_ru: str
    type_ids: tuple[str, ...] = ()
    rules: tuple[tuple[str, frozenset | None], ...] = ()  # (rule name, cues or None = any cue)
    mode: str = "rule"  # "rule" | "risk" | "cv" (CV-only rule, always runs) | "not_checked" | "manual"
    note_ru: str = ""
    rule_override: str | None = None  # citation for steps without a type (manual / not_checked)
    cite_types: tuple[str, ...] = field(default=())  # types whose citations describe the step
    extra_rule: str = ""  # verified non-rules.json point appended to the citation (see VERIFIED_EXTRA_RULES)


def _cues(*c: str) -> frozenset:
    return frozenset(c)


S_ID = StepSpec("identification", "Идентификация клиента по документу", ("T15",),
                (("service", _cues("id_document_shown")),))
S_COUNT = StepSpec("count_in_presence", "Пересчёт в присутствии клиента", ("T7",),
                   (("visit_exit", _cues("counting_machine_used")),))
S_AUTH = StepSpec("authenticity", "Проверка подлинности и полистный пересчёт", (), (), "not_checked",
                  "на видео не проверяется автоматически", cite_types=("T8",))
S_STORED = StepSpec("cash_stored", "Наличные убраны после клиента, не оставлены на столе", ("T1", "T2"),
                    (("departure", None), ("client_change", None)))
S_SECURED = StepSpec("workplace_secured", "Рабочее место защищено при уходе: штампы, сейф, экран, дверь",
                     ("T1b", "T3", "T4", "T5"), (("departure", None), ("safe_tail", None), ("door", None)))
S_DOCS = StepSpec("documents", "Подпись и штамп на приходном документе, выдача документа клиенту", (), (),
                  "manual", "проверяет специалист: на видео документ не читается", rule_override=DOC_STEPS_RULE)
S_FULL = StepSpec("full_amount", "Полная сумма передана в хранилище, не удержана кассиром", ("T10",),
                  (("service", _cues("cash_in_hand_to_body")),), "risk")
S_BAG = StepSpec("bag_check", "Проверка документа, целостности сумки и пломбы", ("T9",),
                 (("visit_exit", _cues("bag_on_desk")), ("service", _cues("bag_on_desk"))))
S_BAG_SUM = StepSpec("bag_amount", "Регистрация суммы по документу", (), (), "not_checked",
                     "на видео не проверяется")
# Client service with unknown cash direction: where приём (п.19/п.20) and выдача (п.31) differ, cite both.
S_ID_OP = StepSpec("identification", "Идентификация клиента по документу", ("T15",),
                   (("service", _cues("id_document_shown")),), extra_rule=PAYOUT_ID_RULE)
S_COUNT_OP = StepSpec("count_in_presence", "Пересчёт наличных в присутствии клиента", ("T7",),
                      (("visit_exit", _cues("counting_machine_used")),), extra_rule=PAYOUT_COUNT_RULE)
S_AUTH_OP = StepSpec("authenticity", "Проверка подлинности и полистный пересчёт принимаемых купюр (при приёме)",
                     (), (), "not_checked", "на видео не проверяется автоматически", cite_types=("T8",))
S_DOCS_OP = StepSpec("documents", "Подпись и штамп на кассовом документе, выдача документа клиенту", (), (),
                     "manual", "проверяет специалист: на видео документ не читается",
                     rule_override=f"{DOC_STEPS_RULE}; {PAYOUT_DOCS_RULE}")
S_FULL_OP = StepSpec("full_amount", "Полная сумма передана (в хранилище или клиенту), не удержана кассиром",
                     ("T10",), (("service", _cues("cash_in_hand_to_body")),), "risk")
COMMON = (
    StepSpec("isolation", "Касса изолирована: посторонних на стороне кассира нет", ("T6",),
             (("door", None), ("client_change", None), ("crowd", None))),
    StepSpec("belongings", "Личных вещей на рабочем месте нет", ("T11",), (("belongings", None),)),
    StepSpec("food", "Еды и напитков на рабочем месте нет", ("T12",), (("food", None),)),
    StepSpec("phone", "Мобильный телефон не используется", ("T13",), (("phone", None),)),
    StepSpec("camera", "Камера не перекрыта", ("T14",), (("camera", None),)),
    # UnexplainedAccessRule is CV-only (timeline, no probes): it ran whenever perception covered the clip
    StepSpec("other", "Посторонний не подходил к столу в отсутствие кассира", ("T99",), (), "cv"),
)
PROCEDURE_STEPS = {
    "client_operation": (S_ID_OP, S_COUNT_OP, S_AUTH_OP, S_STORED, S_SECURED, S_DOCS_OP, S_FULL_OP) + COMMON,
    # only behind a real cash-direction cue (none exists yet: infer_type never emits it)
    "client_deposit": (S_ID, S_COUNT, S_AUTH, S_STORED, S_SECURED, S_DOCS, S_FULL) + COMMON,
    "collection_handover": (S_BAG, S_BAG_SUM, S_STORED, S_SECURED) + COMMON,
    "no_client": (S_STORED, S_SECURED) + COMMON,
    "unknown": (S_STORED, S_SECURED, S_FULL) + COMMON,
}
PROCEDURE_STEPS["client_withdrawal"] = PROCEDURE_STEPS["unknown"]  # not detectable yet (see infer)


# ----------------------------------------------------------------------------- helpers

def mmss(t: float | None) -> str:
    s = max(0, int(round(float(t or 0.0))))
    return f"{s // 60:02d}:{s % 60:02d}"


_DOC_RE = re.compile(r"(НБРК|АРРФР)\s*№\s*(\d+)")


def citation(type_ids: tuple[str, ...] | list[str]) -> str:
    """«НБРК №231 п. 20 ч. 2; …» from taxonomy/rules.json (verified citations only)."""
    try:
        from forte_detector.rules import taxonomy

        rules = taxonomy.load_rules()
    except Exception:  # noqa: BLE001
        return ""
    out: list[str] = []
    for tid in type_ids:
        for c in (rules.get(tid) or {}).get("citations", []):
            m = _DOC_RE.search(c.get("document", ""))
            point = (c.get("point") or "").strip()
            if not m or not point:
                continue
            s = f"{m.group(1)} №{m.group(2)} {point}"
            if s not in out:
                out.append(s)
    return "; ".join(out)


def _answered(p: dict) -> bool:
    r = p.get("result") or {}
    if p.get("skipped") or r.get("skipped") or r.get("error"):
        return False
    return r.get("value") is not None or r.get("count") is not None


def _probes(result) -> list[dict]:
    return [p for p in (getattr(result, "probes", None) or []) if isinstance(p, dict)]


def rule_ran(result, rules: tuple[tuple[str, frozenset | None], ...]) -> bool:
    """True when at least one probe of these rules (matching cues) was actually answered."""
    for p in _probes(result):
        req = p.get("request") or {}
        for name, cues in rules:
            if p.get("rule") == name and (cues is None or req.get("cue") in cues) and _answered(p):
                return True
    return False


def _probe_hits(result, rule: str, cue: str, key_re: str = "") -> list[dict]:
    out = []
    for p in _probes(result):
        req = p.get("request") or {}
        if p.get("rule") == rule and req.get("cue") == cue and re.search(key_re, str(req.get("key") or "")):
            out.append(p)
    return out


def _true(p: dict, min_conf: float = 0.5) -> bool:
    r = p.get("result") or {}
    return r.get("value") is True and float(r.get("conf") or 0.0) >= min_conf


def _count(p: dict) -> int | None:
    r = p.get("result") or {}
    c = r.get("count")
    if c is None and isinstance(r.get("value"), (int, float)) and not isinstance(r.get("value"), bool):
        c = r.get("value")
    try:
        return None if c is None else int(c)
    except (TypeError, ValueError):
        return None


def visitor_windows(timeline: dict) -> list[tuple[float, float]]:
    """(enter, leave) per visitor track from the timeline checkpoints (slim timeline is enough)."""
    tl = timeline or {}
    vis = set((tl.get("roles") or {}).get("visitor_tracks") or [])
    if not vis:
        return []
    dur = float(tl.get("duration") or tl.get("analyzed_sec") or 0.0)
    start: dict[int, float] = {}
    end: dict[int, float] = {}
    for c in tl.get("checkpoints") or []:
        typ, t = c.get("type"), c.get("t")
        for tid in c.get("track_ids") or []:
            if tid not in vis or t is None:
                continue
            if typ in ("person_enters", "visitor_arrives_at_desk"):
                start[tid] = min(start.get(tid, t), t)
            elif typ == "person_exits":
                end[tid] = max(end.get(tid, t), t)
    out = sorted((start.get(tid, 0.0), end.get(tid, dur)) for tid in vis if tid in start or tid in end)
    return [(a, b) for a, b in out if b > a]


def _event_list(result) -> list:
    return list(getattr(result, "events", None) or [])


def _weak_events(timeline: dict) -> list[dict]:
    tl = timeline or {}
    return [e for k in ("low_conf_events", "capped_events", "merged_events") for e in (tl.get(k) or [])
            if isinstance(e, dict)]


# ----------------------------------------------------------------------------- inference

def infer_type(result) -> tuple[str, float, str]:
    """(procedure type, confidence, basis_ru)."""
    tl = getattr(result, "timeline", None) or {}
    types = {e.type for e in _event_list(result)}
    windows = visitor_windows(tl)
    counts = [c for rule in ("service", "visit_exit") for p in _probe_hits(result, rule, "person_count")
              if (c := _count(p)) is not None]
    real_client = any(c >= 2 for c in counts)
    bag_hits = [p for rule in ("service", "visit_exit") for p in _probe_hits(result, rule, "bag_on_desk")]
    bag = any(_true(p) for p in bag_hits)
    who = ", ".join(f"{mmss(a)}–{mmss(b)}" for a, b in windows[:3])
    seen = f"посетитель в кадре {who}" if who else "посетителей в кадре нет"

    if "T9" in types or (bag and real_client):
        conf = 0.85 if "T9" in types else 0.7
        return "collection_handover", conf, f"{seen}; на столе оставлена сумка — сдача сумки инкассатором"
    if real_client:
        # The cash direction (приём vs выдача) is not observable: never claim a deposit without a cue.
        conf = 0.65 if types & {"T15", "T7", "T10"} else 0.6
        why = "сумка инкассатора не обнаружена" if bag_hits else "сумка не проверялась"
        return ("client_operation", conf, f"{seen}, клиент подтверждён подсчётом людей; {why}; {DIRECTION_NOTE}")
    if not windows and not any(c >= 2 for c in counts):
        return "no_client", 0.8 if not counts else 0.7, "посетителей в кадре нет"
    return "unknown", 0.35, f"{seen}, но участие клиента в операции не подтверждено"


def _step(spec: StepSpec, result, weak: list[dict]) -> dict:
    events = [e for e in _event_list(result) if e.type in spec.type_ids]
    rule = spec.rule_override if spec.rule_override is not None else citation(spec.type_ids or spec.cite_types)
    if spec.extra_rule:
        rule = f"{rule}; {spec.extra_rule}" if rule else spec.extra_rule
    out = {"id": spec.id, "title_ru": spec.title_ru, "status": "not_checked", "t_sec": None,
           "type_ids": list(spec.type_ids), "rule": rule, "note_ru": spec.note_ru}
    if spec.mode in ("manual", "not_checked"):
        out["status"] = spec.mode
        return out
    if events:
        e = min(events, key=lambda e: e.timecode_sec)
        out.update(status="risk" if spec.mode == "risk" else "violation", t_sec=round(float(e.timecode_sec), 2),
                   type_ids=sorted({x.type for x in events}, key=list(spec.type_ids).index),
                   note_ru=(getattr(e, "obs", "") or "").strip().rstrip(".")[:200])
        return out
    w = [e for e in weak if e.get("type") in spec.type_ids]
    if w:
        e = min(w, key=lambda e: float(e.get("timecode_sec") or 0.0))
        out.update(status="risk", t_sec=round(float(e.get("timecode_sec") or 0.0), 2),
                   note_ru="слабый признак, требует проверки")
        return out
    tl = getattr(result, "timeline", None) or {}
    if spec.mode == "cv" and not tl.get("truncated") and tl.get("roles") is not None:
        out.update(status="ok", note_ru="")
    elif spec.rules and rule_ran(result, spec.rules):
        out.update(status="ok", note_ru="")
    else:
        out["note_ru"] = "правило не применялось: нет подходящего окна или ответа модели"
    return out


def build_procedure(result) -> dict:
    """meta["procedure"] for a finished detector AnalysisResult (never raises: falls back to unknown)."""
    try:
        typ, conf, basis = infer_type(result)
    except Exception as e:  # noqa: BLE001
        log.warning("procedure type inference failed: %s", e)
        typ, conf, basis = "unknown", 0.0, "тип операции не определён"
    weak = _weak_events(getattr(result, "timeline", None) or {})
    steps = []
    for spec in PROCEDURE_STEPS[typ]:
        try:
            steps.append(_step(spec, result, weak))
        except Exception as e:  # noqa: BLE001
            log.warning("procedure step %s failed: %s", spec.id, e)
    covered = {t for s in steps for t in s["type_ids"]}
    for e in _event_list(result):  # every reported violation appears in some step
        if e.type not in covered:
            covered.add(e.type)
            steps.append({"id": f"other_{e.type}", "title_ru": getattr(e, "title_ru", "") or e.type,
                          "status": "violation", "t_sec": round(float(e.timecode_sec), 2), "type_ids": [e.type],
                          "rule": citation((e.type,)), "note_ru": (getattr(e, "obs", "") or "")[:200]})
    proc = {"type": typ, "title_ru": TITLES[typ], "confidence": round(float(conf), 2), "basis_ru": basis,
            "steps": steps[:MAX_STEPS]}
    while len(json.dumps(proc, ensure_ascii=False).encode("utf-8")) > MAX_BYTES and proc["steps"]:
        proc["steps"].pop()
    return proc
