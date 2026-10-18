"""Human review of a finished job (extra `review` field) and the labelled feedback export.

The automatic result is a hint for a person, not a verdict (case brief §7). A reviewer marks
each violation confirmed / rejected and records a job-level decision; the act page and the
feedback.jsonl export are built from that record. Nothing here touches the jury contract:
`review` is an extra field of the public job JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from app.models import JobRecord, now_iso

VIOLATION_STATUSES = ("confirmed", "rejected", "pending")
DECISIONS = ("confirmed", "rejected", "needs_followup")
MAX_NAME_CHARS = 200  # reviewer_name, reviewer_position
MAX_COMMENT_CHARS = 2000  # decision_comment, violation comments
MAX_BODY_BYTES = 64 * 1024

_TOP_KEYS = {"reviewer_name", "reviewer_position", "decision", "decision_comment", "violations", "checklist"}

# Reviewer checklist: documentation steps the camera cannot verify (НБРК № 231 п. 19 / п. 31).
CHECKLIST_PATH = Path(__file__).resolve().parent / "checklist.json"


def load_checklist(path: Path = CHECKLIST_PATH) -> list[dict[str, Any]]:
    items = json.loads(path.read_text("utf-8"))
    if not isinstance(items, list) or not all(isinstance(i, dict) and i.get("id") for i in items):
        raise ValueError(f"{path}: expected a list of objects with an id")
    return items


CHECKLIST = load_checklist()
CHECKLIST_IDS = tuple(i["id"] for i in CHECKLIST)
_VIOLATION_KEYS = {"status", "comment"}


class ReviewError(ValueError):
    """Invalid review patch (-> HTTP 422)."""


def _text(value: Any, field: str, cap: int) -> str:
    """Optional free text: null -> "", trimmed, capped. Anything but a string/null is an error."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ReviewError(f"Поле {field} должно быть строкой ({field} must be a string)")
    return value.strip()[:cap].strip()


def _enum(value: Any, field: str, allowed: tuple[str, ...], nullable: bool) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or value not in allowed:
        opts = ", ".join(allowed) + (", null" if nullable else "")
        raise ReviewError(f"Недопустимое значение {field} (allowed: {opts})")
    return value


def _violation_key(key: Any, n_violations: int) -> str:
    try:
        n = int(str(key).strip())
    except ValueError:
        n = 0
    if not (1 <= n <= n_violations) or str(n) != str(key).strip():
        raise ReviewError(
            f"Нет нарушения с номером {key!r}: нумерация с 1 до {n_violations} "
            f"(violation number must be 1..{n_violations})")
    return str(n)


def merge_review(current: dict[str, Any] | None, patch: Any, n_violations: int) -> dict[str, Any]:
    """Validate `patch` and merge it into a copy of `current`. Raises ReviewError.

    Only the keys present in the patch change; the server sets every timestamp."""
    if not isinstance(patch, dict):
        raise ReviewError("Тело запроса должно быть JSON-объектом (body must be a JSON object)")
    unknown = sorted(set(patch) - _TOP_KEYS)
    if unknown:
        raise ReviewError(f"Неизвестные поля: {', '.join(map(str, unknown))} (unknown fields)")

    # Validate everything before touching the record: a bad patch changes nothing.
    updates: dict[str, Any] = {}
    if "reviewer_name" in patch:
        updates["reviewer_name"] = _text(patch["reviewer_name"], "reviewer_name", MAX_NAME_CHARS)
    if "reviewer_position" in patch:
        updates["reviewer_position"] = _text(patch["reviewer_position"], "reviewer_position", MAX_NAME_CHARS)
    if "decision" in patch:
        updates["decision"] = _enum(patch["decision"], "decision", DECISIONS, nullable=True)
    if "decision_comment" in patch:
        updates["decision_comment"] = _text(patch["decision_comment"], "decision_comment", MAX_COMMENT_CHARS)
    vpatch: dict[str, dict[str, Any]] = {}
    if "violations" in patch:
        raw = patch["violations"]
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ReviewError("Поле violations должно быть объектом {номер: {...}} (violations must be an object)")
        for key, item in raw.items():
            n = _violation_key(key, n_violations)
            if not isinstance(item, dict):
                raise ReviewError(f"violations[{n}] должно быть объектом (must be an object)")
            bad = sorted(set(item) - _VIOLATION_KEYS)
            if bad:
                raise ReviewError(f"Неизвестные поля violations[{n}]: {', '.join(map(str, bad))} (unknown fields)")
            entry: dict[str, Any] = {}
            if "status" in item:
                entry["status"] = _enum(item["status"], f"violations[{n}].status", VIOLATION_STATUSES, nullable=False)
            if "comment" in item:
                entry["comment"] = _text(item["comment"], f"violations[{n}].comment", MAX_COMMENT_CHARS)
            vpatch[n] = entry
    cpatch: dict[str, bool | None] = {}
    if "checklist" in patch:
        raw = patch["checklist"]
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ReviewError("Поле checklist должно быть объектом {id: true|false|null} (checklist must be an object)")
        for key, value in raw.items():
            if key not in CHECKLIST_IDS:
                raise ReviewError(f"Нет пункта чек-листа {key!r} (unknown checklist item; allowed: {', '.join(CHECKLIST_IDS)})")
            if value is not None and not isinstance(value, bool):
                raise ReviewError(f"checklist[{key}] должно быть true, false или null (must be true/false/null)")
            cpatch[key] = value

    now = now_iso()
    review: dict[str, Any] = {
        "reviewer_name": "", "reviewer_position": "", "decision": None, "decision_comment": "",
        "violations": {}, "checklist": {}, "created_at": now, "updated_at": now, "decided_at": None,
    }
    if current:
        review.update({k: v for k, v in current.items() if k not in ("violations", "checklist")})
        review["violations"] = {k: dict(v) for k, v in (current.get("violations") or {}).items()}
        review["checklist"] = dict(current.get("checklist") or {})
    review.update(updates)
    if "decision" in updates:
        review["decided_at"] = now if updates["decision"] is not None else None
    for n, entry in vpatch.items():
        old = review["violations"].get(n) or {"status": "pending", "comment": ""}
        review["violations"][n] = {**old, **entry, "updated_at": now}
    review["violations"] = dict(sorted(review["violations"].items(), key=lambda kv: int(kv[0])))
    review["checklist"].update(cpatch)
    order = {k: i for i, k in enumerate(CHECKLIST_IDS)}
    review["checklist"] = dict(sorted(review["checklist"].items(), key=lambda kv: order.get(kv[0], len(order))))
    review["updated_at"] = now
    return review


# ---------- feedback export ----------

def _violation_line(job: JobRecord, review: dict[str, Any], n: int, entry: dict[str, Any]) -> dict[str, Any]:
    v = job.violations[n - 1]
    line: dict[str, Any] = {
        "kind": "violation", "job_id": job.job_id, "filename": job.filename, "video_meta": job.video_meta,
        "n": n, "type_id": v.get("type_id"), "type": v.get("type"),
        "timecode_sec": v.get("timecode_sec"), "start_sec": v.get("start_sec"), "end_sec": v.get("end_sec"),
        "fact_conf": v.get("fact_conf"), "legal_conf": v.get("legal_conf"),
    }
    if "legal_strength" in v:
        line["legal_strength"] = v.get("legal_strength")
    if "analyzer_description" in v:
        line["analyzer_description"] = v.get("analyzer_description")
    line.update({
        "description": v.get("description", ""), "status": entry.get("status"), "comment": entry.get("comment", ""),
        "reviewer_name": review.get("reviewer_name", ""), "reviewer_position": review.get("reviewer_position", ""),
        "updated_at": entry.get("updated_at"),
    })
    return line


def feedback_lines(jobs: Iterable[JobRecord]) -> Iterable[dict[str, Any]]:
    """Labelled feedback for retraining / threshold tuning, oldest job first (no images).

    One line per reviewed violation (status != pending) and one `kind: "job"` line per job
    with a decision."""
    for job in jobs:
        review = job.review
        if job.status != "done" or not review:
            continue
        entries = review.get("violations") or {}
        for key in sorted(entries, key=int):
            n, entry = int(key), entries[key]
            if entry.get("status") in ("confirmed", "rejected") and 1 <= n <= len(job.violations):
                yield _violation_line(job, review, n, entry)
        if review.get("decision"):
            statuses = [e.get("status") for k, e in entries.items() if 1 <= int(k) <= len(job.violations)]
            yield {
                "kind": "job", "job_id": job.job_id, "filename": job.filename,
                "verdict": "violation" if job.violations else "no_violation",
                "decision": review.get("decision"), "decision_comment": review.get("decision_comment", ""),
                "reviewer_name": review.get("reviewer_name", ""),
                "reviewer_position": review.get("reviewer_position", ""),
                "decided_at": review.get("decided_at"), "n_violations": len(job.violations),
                "n_confirmed": statuses.count("confirmed"), "n_rejected": statuses.count("rejected"),
                "checklist": dict(review.get("checklist") or {}),
            }


def feedback_ndjson(jobs: Iterable[JobRecord]) -> str:
    return "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in feedback_lines(jobs))
