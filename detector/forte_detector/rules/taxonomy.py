"""Type ids, titles, citations and description templates from the canonical `taxonomy/rules.json`.

Path: env `RULES_PATH`, default `<repo>/taxonomy/rules.json`. Titles and citations are never
generated; descriptions are the rules.json template with `{t}` (mm:ss) and `{obs}` filled in.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from forte_detector import config

DEFAULT_RULES_PATH = config.REPO_DIR / "taxonomy" / "rules.json"
LEGAL_LEVELS = ("low", "medium", "high")
LEGAL_WEIGHT = {"high": 1.0, "medium": 0.95, "low": 0.9, "": 0.9}
# Templates where {obs} is the main sentence ("на {t} {obs}.") instead of a trailing clause.
_MAIN_SENTENCE_RE = "{t} {obs}"


def rules_path() -> Path:
    return Path(os.environ.get("RULES_PATH", DEFAULT_RULES_PATH))


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_rules(path: str | Path | None = None) -> dict:
    return _load(str(path or rules_path()))


def type_ids() -> list[str]:
    return list(load_rules())


def title(type_id: str) -> str:
    return load_rules()[type_id]["title_ru"]


def legal_conf(type_id: str) -> str:
    """Strongest rule link among the type's citations ("low" if it has none, e.g. T99)."""
    levels = [c.get("legal_conf", "low") for c in load_rules()[type_id].get("citations", [])]
    return max(levels, key=LEGAL_LEVELS.index) if levels else "low"


def mmss(t: float) -> str:
    s = max(0, int(round(t)))
    return f"{s // 60:02d}:{s % 60:02d}"


def obs_is_main(type_id: str) -> bool:
    return _MAIN_SENTENCE_RE in load_rules()[type_id]["description_template"]


def render_obs(type_id: str, obs: str) -> str:
    """Punctuation for the {obs} slot (single place): trailing clause -> ", <obs>"; main sentence
    (T10/T99) -> bare <obs>. `obs` itself is always a bare lowercase clause without prefix."""
    obs = (obs or "").strip().rstrip(".")
    if obs_is_main(type_id):
        return obs
    return f", {obs}" if obs else ""


def describe(type_id: str, t: float, obs: str) -> str:
    if obs_is_main(type_id) and not (obs or "").strip():
        raise ValueError(f"{type_id}: obs is the main sentence and must not be empty")
    tpl = load_rules()[type_id]["description_template"]
    return tpl.replace("{t}", mmss(t)).replace("{obs}", render_obs(type_id, obs))
