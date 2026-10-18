"""Rules knowledge base: the ONLY source of citations attached to violations.

Lookup order (first file with at least one valid entry wins):
  1. env `RULES_PATH` (or `Settings.rules_path`)
  2. `<service root>/../detector/taxonomy/rules.json`   (canonical, owned by the detector)
  3. `<service root>/../docs/deliverables/rules_spec.json`
  4. bundled `app/rules.json` (verbatim copy of the spec)

Accepted file shapes: `{id: {...}}`, `{"rules": {id: {...}}}`, `{"rules": [...]}` or a list
of objects with `"id"`. Per entry `title_ru` is required; `title_en`, `description_template`
and `citations[{document, point?, quote?, url?}]` are optional; unknown keys are preserved
(also inside citations, e.g. a string `legal_conf: "high"`). Invalid entries are skipped
with a warning.

The service never invents citations: a violation whose type id is not in the loaded file
gets `rule: null, rules: []`, and analyzer-supplied citations are always dropped.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.models import Violation

log = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent
SERVICE_ROOT = APP_DIR.parent
BUNDLED_RULES_PATH = APP_DIR / "rules.json"
DEFAULT_RULES_PATH = BUNDLED_RULES_PATH  # backward-compatible name
DETECTOR_RULES_PATH = SERVICE_ROOT.parent / "detector" / "taxonomy" / "rules.json"
SPEC_RULES_PATH = SERVICE_ROOT.parent / "docs" / "deliverables" / "rules_spec.json"


class RulesError(ValueError):
    pass


def _as_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    raise ValueError(f"expected a string, got {type(v).__name__}")


class RuleCitation(BaseModel):
    """One citation. Extra keys (e.g. `legal_conf: "high"`) are kept and passed through."""

    model_config = ConfigDict(extra="allow")

    document: str = Field(min_length=1)
    point: str = ""
    quote: str = ""
    url: str = ""

    @field_validator("document", "point", "quote", "url", mode="before")
    @classmethod
    def _str(cls, v: Any) -> str:
        return _as_str(v)


class RuleEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    title_ru: str = Field(min_length=1)
    title_en: str = ""
    description_template: str = ""
    citations: list[RuleCitation] = Field(default_factory=list)

    @field_validator("title_ru", "title_en", "description_template", mode="before")
    @classmethod
    def _str(cls, v: Any) -> str:
        return _as_str(v)


# ---------- description rendering ----------

def format_timecode(t: float) -> str:
    """Seconds -> «мм:сс» (57.3 -> «00:57»); from one hour on «ч:мм:сс»."""
    try:
        t = float(t)
    except (TypeError, ValueError):
        t = 0.0
    if not math.isfinite(t) or t < 0:
        t = 0.0
    total = int(t)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")


_OBS_AS_IS = ",;:."
_OBS_OPENING = "(«\"“„'["


def _obs_insertion(template: str, pos: int, obs: str) -> str:
    """Text replacing the `{obs}` that starts at `template[pos]` (ruling after review):
    after a non-space character -> ", " + obs, except obs starting with ,;:. (as-is) or with
    opening punctuation such as ( « " (" " + obs); after a space -> obs as-is; at the very
    start of the template -> obs as-is with its first letter capitalised."""
    if not obs:
        return ""
    if pos == 0:
        return obs[0].upper() + obs[1:]
    before = template[pos - 1]
    if before.isspace():
        return obs
    if obs[0] in _OBS_AS_IS:
        return obs
    if obs[0] in _OBS_OPENING or unicodedata.category(obs[0]) == "Ps":
        return " " + obs
    return ", " + obs


def clean_text(s: str) -> str:
    """Tidy up after placeholder substitution: no double spaces, no space before .,;:!?,
    no ",." / ",," sequences."""
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"[ \t ]{2,}", " ", s)
        s = re.sub(r"[ \t ]+([.,;:!?])", r"\1", s)
        s = re.sub(r",(?=[.,;:!?])", "", s)
    return s.strip()


def render_template(template: str, t: float, obs: str | None = None) -> str:
    """`{t}` -> «мм:сс»; `{obs}` -> see `_obs_insertion` (removed if no obs); any other
    `{...}` is removed; then `clean_text`."""
    obs = (obs or "").strip().rstrip(".").rstrip()  # the template supplies the punctuation

    def sub(m: re.Match[str]) -> str:
        name = m.group(1).strip()
        if name == "t":
            return format_timecode(t)
        if name == "obs":
            return _obs_insertion(template, m.start(), obs)
        return ""

    return clean_text(_PLACEHOLDER_RE.sub(sub, template))


# ---------- citation grounding ----------

_PAREN_RE = re.compile(r"\(([^()]*)\)")


def short_doc_name(document: str) -> str:
    """A parenthesized part starting with «Постановление» (the act's number/date) if present,
    else the full document string."""
    doc = " ".join((document or "").split())
    for m in _PAREN_RE.finditer(doc):
        inner = m.group(1).strip()
        if inner.startswith("Постановление"):
            return inner
    return doc


def grounding_sentence(citations: list[dict[str, Any]]) -> str:
    """«Основание: <doc>, <point>; <point>; <doc2>, <point>.» — consecutive citations of the same
    document share one doc name; empty points are skipped. "" when nothing to cite."""
    parts: list[str] = []
    prev_doc: str | None = None
    for c in citations:
        doc = short_doc_name(str(c.get("document") or "")).rstrip(" .")
        point = " ".join(str(c.get("point") or "").split()).rstrip(" .")
        if not doc and not point:
            continue
        if doc and doc != prev_doc:
            parts.append(f"{doc}, {point}" if point else doc)
            prev_doc = doc
        elif point:
            parts.append(point)
    if not parts:
        return ""
    return "Основание: " + "; ".join(parts) + "."


def append_grounding(description: str, citations: list[dict[str, Any]]) -> str:
    sentence = grounding_sentence(citations)
    if not sentence:
        return description
    base = description.strip()
    if base and base[-1] not in ".!?…":
        base += "."
    return f"{base} {sentence}" if base else sentence


# ---------- knowledge base ----------

class RulesKB:
    def __init__(self, rules: dict[str, RuleEntry], path: Path | None = None):
        if not rules:
            raise RulesError("rules KB is empty")
        self.rules = rules
        self.path = path

    def __len__(self) -> int:
        return len(self.rules)

    def enrich(self, violation: Violation | dict[str, Any]) -> dict[str, Any]:
        """Analyzer violation -> public dict.

        `description` = rendered rule template (if the id is known and has one), else the
        analyzer's `description`, else the title; a differing analyzer text is kept in
        `analyzer_description`.
        `type` = rules `title_ru` (or the analyzer's `title_ru`, or the raw id if the id is
        unknown); `type_id` = raw id; `rule`/`rules` come ONLY from the rules file (null / []
        for unknown ids). Analyzer-supplied citations are discarded.
        """
        v = violation if isinstance(violation, Violation) else Violation.model_validate(violation)
        type_id = v.type.strip()
        rule = self.rules.get(type_id)
        if rule is not None:
            title = rule.title_ru
        else:
            title = (v.title_ru or "").strip() or type_id
        citations = [c.model_dump() for c in rule.citations] if rule is not None else []

        # Precedence (orchestrator ruling): the rule's template wins when it exists, so the
        # wording is the reviewed one; then the analyzer's text; then the title.
        analyzer_text = v.description.strip()
        if rule is not None and rule.description_template.strip():
            description = render_template(rule.description_template, v.timecode_sec, v.obs)
        else:
            description = analyzer_text
        if not description:
            obs = (v.obs or "").strip()
            description = f"{title}: {obs}" if obs else title

        out: dict[str, Any] = {
            "type": title,
            "type_id": type_id,
            "title_ru": title,
            "timecode_sec": float(v.timecode_sec),
            "start_sec": float(v.timecode_sec) if v.start_sec is None else float(v.start_sec),
            "end_sec": None if v.end_sec is None else float(v.end_sec),
            "confidence": v.confidence,
            "fact_conf": v.fact_conf,
            "legal_conf": v.legal_conf,
            "legal_strength": v.legal_strength,
            "description": append_grounding(description, citations),
            "description_text": description,
            "rule": citations[0] if citations else None,
            "rules": citations,
            "evidence_frames": [f.model_dump() for f in v.evidence_frames],
        }
        if analyzer_text and analyzer_text != description:  # vs the pre-grounding text
            out["analyzer_description"] = analyzer_text  # kept so it isn't lost; the UI ignores it
        return out

    def public_dict(self) -> dict[str, Any]:
        """Payload for GET /ui-api/rules (unknown keys preserved)."""
        return {k: r.model_dump() for k, r in self.rules.items()}


# ---------- loading ----------

def _iter_raw_entries(raw: Any) -> list[tuple[Any, Any]]:
    if isinstance(raw, dict) and "rules" in raw:
        inner = raw["rules"]
        is_entry_named_rules = isinstance(inner, dict) and isinstance(inner.get("title_ru"), str)
        if isinstance(inner, list) or (isinstance(inner, dict) and not is_entry_named_rules):
            raw = inner
    if isinstance(raw, dict):
        return list(raw.items())
    if isinstance(raw, list):
        out: list[tuple[Any, Any]] = []
        for i, item in enumerate(raw):
            if isinstance(item, dict):
                item = dict(item)
                out.append((item.pop("id", None), item))
            else:
                out.append((f"#{i}", item))
        return out
    raise RulesError("rules file must be a JSON object or a list of objects")


def parse_rules(raw: Any, source: str = "<rules>") -> dict[str, RuleEntry]:
    """Lenient parse: invalid entries (and invalid citations inside an entry) are skipped."""
    rules: dict[str, RuleEntry] = {}
    for key, value in _iter_raw_entries(raw):
        if not isinstance(key, str) or not key.strip():
            log.warning("%s: skipping rule entry without a valid id: %r", source, key)
            continue
        key = key.strip()
        if not isinstance(value, dict):
            log.warning("%s: skipping rule %r: not an object", source, key)
            continue
        value = dict(value)
        cits = value.get("citations")
        if cits is None:
            value["citations"] = []
        elif not isinstance(cits, list):
            log.warning("%s: skipping rule %r: 'citations' is not a list", source, key)
            continue
        else:
            good = []
            for j, c in enumerate(cits):
                try:
                    good.append(RuleCitation.model_validate(c))
                except ValidationError as e:
                    log.warning("%s: rule %r: dropping invalid citation #%d: %s", source, key, j,
                                e.errors()[0].get("msg", e))
            value["citations"] = good
        try:
            entry = RuleEntry.model_validate(value)
        except ValidationError as e:
            log.warning("%s: skipping invalid rule %r: %s", source, key, e.errors()[0].get("msg", e))
            continue
        if key in rules:
            log.warning("%s: duplicate rule id %r, keeping the last one", source, key)
        rules[key] = entry
    return rules


def load_rules(path: Path | str = BUNDLED_RULES_PATH) -> RulesKB:
    """Load one rules file; raises RulesError if unreadable or without any valid entry."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RulesError(f"cannot read rules file {path}: {e}") from e
    rules = parse_rules(raw, str(path))
    if not rules:
        raise RulesError(f"rules file {path} has no valid entries")
    return RulesKB(rules, path=path)


def display_path(path: Path | None) -> str | None:
    """Path for /healthz without leaking the home directory: relative to the service root
    when the file is under the service root's parent (e.g. "app/rules.json",
    "../detector/taxonomy/rules.json"), otherwise just the file name."""
    if path is None:
        return None
    try:
        resolved = Path(path).resolve()
        resolved.relative_to(SERVICE_ROOT.parent)
    except (OSError, ValueError):
        return Path(path).name
    return Path(os.path.relpath(resolved, SERVICE_ROOT)).as_posix()


def rules_candidates(explicit: str | Path | None = None) -> list[Path]:
    """Lookup order; `explicit` (else env RULES_PATH) first, bundled file last."""
    if explicit is None:
        explicit = os.environ.get("RULES_PATH") or ""
    out: list[Path] = []
    if str(explicit).strip():
        out.append(Path(str(explicit).strip()).expanduser())
    out += [DETECTOR_RULES_PATH, SPEC_RULES_PATH, BUNDLED_RULES_PATH]
    return out


def load_rules_auto(explicit: str | Path | None = None) -> RulesKB:
    """First candidate that loads with at least one valid entry wins (never crashes
    startup unless even the bundled file is broken)."""
    errors: list[str] = []
    candidates = rules_candidates(explicit)
    has_explicit = len(candidates) > 3
    for i, path in enumerate(candidates):
        is_explicit = has_explicit and i == 0
        if not path.is_file():
            if is_explicit:
                log.warning("RULES_PATH=%s does not exist; falling back", path)
            continue
        try:
            kb = load_rules(path)
        except RulesError as e:
            log.warning("Rules file %s rejected (%s); falling back", path, e)
            errors.append(str(e))
            continue
        log.info("Rules loaded from %s (%d entries)", path, len(kb))
        return kb
    raise RulesError("no usable rules file: " + "; ".join(errors or ["bundled app/rules.json missing"]))
