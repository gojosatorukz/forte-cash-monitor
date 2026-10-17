import json
from pathlib import Path

import pytest

from app import rules as rules_mod
from app.models import EvidenceFrame, Violation
from app.rules import (
    BUNDLED_RULES_PATH,
    RulesError,
    format_timecode,
    load_rules,
    load_rules_auto,
    render_template,
)

SPEC_PATH = Path("/home/datamata/Projects/datathon2026/docs/deliverables/rules_spec.json")
SPEC_IDS = {"T1", "T1b", *(f"T{i}" for i in range(2, 16)), "T99"}
APP_DIR = Path(rules_mod.__file__).resolve().parent


@pytest.fixture(scope="module")
def kb():
    return load_rules(BUNDLED_RULES_PATH)


def write_json(path: Path, data) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def no_external_rules(monkeypatch, tmp_path):
    """Hide the detector/spec files so the lookup order is deterministic."""
    monkeypatch.delenv("RULES_PATH", raising=False)
    monkeypatch.setattr(rules_mod, "DETECTOR_RULES_PATH", tmp_path / "nope" / "rules.json")
    monkeypatch.setattr(rules_mod, "SPEC_RULES_PATH", tmp_path / "nope" / "rules_spec.json")


# ---------- bundled file ----------

def test_bundled_rules_are_the_spec(kb):
    assert set(kb.rules) == SPEC_IDS
    if SPEC_PATH.is_file():
        assert BUNDLED_RULES_PATH.read_bytes() == SPEC_PATH.read_bytes()
    for rid, rule in kb.rules.items():
        assert rule.title_ru and rule.title_en and rule.description_template
        assert ("{t}" in rule.description_template), rid
    assert kb.rules["T99"].citations == []
    assert all(kb.rules[r].citations for r in SPEC_IDS - {"T99"})


def test_no_placeholder_strings_anywhere_in_app():
    offenders = []
    for p in APP_DIR.rglob("*"):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "ЗАГЛУШКА" in text or "заглушк" in text.lower() or "[placeholder]" in text.lower():
            offenders.append(str(p))
    assert offenders == []


def test_citation_extra_keys_preserved(kb):
    d = kb.enrich({"type": "T1", "timecode_sec": 1})
    assert d["rule"]["legal_conf"] in {"high", "medium", "low"}  # string, citation level
    assert all("legal_conf" in c for c in d["rules"])
    assert d["legal_conf"] is None  # violation-level float stays separate


# ---------- enrich ----------

def test_enrich_known_type_maps_type_to_title(kb):
    v = Violation(
        type="T3", timecode_sec=12.5, start_sec=11.0, end_sec=20.0, fact_conf=0.87, legal_conf=0.7,
        description="Сейф открыт",
        evidence_frames=[EvidenceFrame(timestamp_sec=12.5, image_base64="/9j/xx")],
    )
    d = kb.enrich(v)
    rule = kb.rules["T3"]
    assert d["type"] == rule.title_ru == d["title_ru"]
    assert d["type_id"] == "T3"
    assert d["timecode_sec"] == 12.5 and d["start_sec"] == 11.0 and d["end_sec"] == 20.0
    assert d["fact_conf"] == 0.87 and d["legal_conf"] == 0.7
    assert d["confidence"] == 0.87  # defaults to fact_conf
    assert d["description"].startswith("Наблюдение: на 00:12")  # rule template wins
    assert d["analyzer_description"] == "Сейф открыт"
    assert d["rule"] == rule.citations[0].model_dump()
    assert d["rules"] == [c.model_dump() for c in rule.citations]
    assert d["evidence_frames"] == [{"timestamp_sec": 12.5, "image_base64": "/9j/xx", "boxes": []}]
    json.dumps(d)


def test_start_sec_defaults_to_timecode(kb):
    d = kb.enrich({"type": "T2", "timecode_sec": 4.25, "confidence": 0.4, "fact_conf": 0.9})
    assert d["start_sec"] == 4.25
    assert d["confidence"] == 0.4  # explicit confidence wins over fact_conf


def test_unknown_type_passes_through_without_citation(kb):
    d = kb.enrich({"type": "teleportation", "timecode_sec": 3, "description": "Странное"})
    assert d["type"] == "teleportation" and d["type_id"] == "teleportation"
    assert d["description"] == "Странное"
    assert d["rule"] is None and d["rules"] == []
    assert "original_type" not in d


def test_unknown_type_uses_analyzer_title(kb):
    d = kb.enrich({"type": "X42", "title_ru": "Новый тип", "timecode_sec": 3,
                   "rule": {"document": "FAKE"}})
    assert d["type"] == "Новый тип" == d["title_ru"]
    assert d["type_id"] == "X42"
    assert d["rule"] is None and d["rules"] == []
    assert d["description"] == "Новый тип"


def test_enrich_ignores_analyzer_citations_and_title_for_known_type(kb):
    d = kb.enrich({
        "type": "T1", "timecode_sec": 1.0, "description": "x",
        "rule": {"document": "FAKE", "point": "0", "quote": "fake", "url": "http://evil"},
        "rules": [{"document": "FAKE"}], "citations": [{"document": "FAKE"}],
        "title_ru": "Подделка",
    })
    assert d["rule"]["document"] != "FAKE"
    assert all(c["document"] != "FAKE" for c in d["rules"])
    assert "citations" not in d
    assert d["type"] == d["title_ru"] == kb.rules["T1"].title_ru


def test_lenient_confidences():
    v = Violation(type="T1", timecode_sec=0, fact_conf=7, legal_conf="high", confidence=float("nan"))
    assert v.fact_conf == 1.0 and v.legal_conf is None and v.confidence == 1.0
    v = Violation(type="T1", timecode_sec=0, fact_conf=-1, legal_conf=float("inf"))
    assert v.fact_conf == 0.0 and v.legal_conf is None


def test_type_id_alias_accepted(kb):
    d = kb.enrich({"type_id": "T2", "timecode_sec": 1})
    assert d["type_id"] == "T2"


# ---------- descriptions ----------

@pytest.mark.parametrize("t,expected", [(0, "00:00"), (57.3, "00:57"), (59.9, "00:59"), (61, "01:01"),
                                        (3599, "59:59"), (3600, "1:00:00"), (3725.5, "1:02:05"),
                                        (-3, "00:00"), (float("nan"), "00:00")])
def test_format_timecode(t, expected):
    assert format_timecode(t) == expected


def test_description_template_without_obs(kb):
    d = kb.enrich({"type": "T1", "timecode_sec": 57.3, "description": ""})
    assert "на 00:57 кассир" in d["description"]
    assert "или сумка. Норма: " in d["description"]
    assert "{" not in d["description"] and "  " not in d["description"]


def test_description_template_with_obs(kb):
    d = kb.enrich({"type": "T1", "timecode_sec": 57.3, "obs": "рядом лежит телефон"})
    assert "или сумка, рядом лежит телефон. Норма: " in d["description"]


def test_obs_separator_rules():
    # after a non-space character
    assert render_template("A{obs}. B", 0, "ещё") == "A, ещё. B"
    assert render_template("A{obs}. B", 0, "; ещё") == "A; ещё. B"
    assert render_template("A{obs}. B", 0, ": ещё") == "A: ещё. B"
    assert render_template("A{obs}. B", 0, "(ещё)") == "A (ещё). B"
    assert render_template("A{obs}. B", 0, "«ещё»") == "A «ещё». B"
    assert render_template("A{obs}. B", 0, " ещё.") == "A, ещё. B"  # obs trimmed, trailing dot dropped
    # after a space
    assert render_template("A {obs}. B", 0, "ещё") == "A ещё. B"
    # at template start: as-is, capitalised, no leading ", "
    assert render_template("{obs} на {t}.", 5, "кассир ушёл") == "Кассир ушёл на 00:05."
    assert render_template("{obs} на {t}.", 5) == "на 00:05."


# Real templates from the bundled rules file (= docs/deliverables/rules_spec.json).
OBS = "кассир передал пачку купюр"
T1_HEAD = "Наблюдение: на 00:57 кассир покинул рабочее место, на столе остались наличные или сумка"
T1_TAIL = (". Норма: ранее принятые наличные хранятся в устройствах, закрывающихся на ключ; покидать рабочее "
           "место до окончания обслуживания нельзя. Требует проверки: содержимое оставленных предметов.")
T10_TAIL = (". Это подозрительное действие с наличными, требует проверки по записи. Хищение не утверждается. "
            "Норма (контекст): приём с полистным пересчётом и в присутствии клиента; сомнительные банкноты "
            "оформляются актом на экспертизу, а банкноты с признаками подделки клиенту не возвращаются.")
T99_TAIL = (". Тип нарушения не определён автоматически, требуется проверка сотрудником. Ссылка на норму не "
            "приводится, если событие не совпадает ни с одним правилом справочника.")


@pytest.mark.parametrize("type_id,obs,expected", [
    ("T1", None, T1_HEAD + T1_TAIL),
    ("T1", OBS, T1_HEAD + ", " + OBS + T1_TAIL),
    ("T1", "(предварительно)", T1_HEAD + " (предварительно)" + T1_TAIL),
    ("T10", None, "Наблюдение: на 00:57" + T10_TAIL),
    ("T10", OBS, "Наблюдение: на 00:57 " + OBS + T10_TAIL),
    ("T99", None, "Наблюдение: на 00:57" + T99_TAIL),
    ("T99", OBS, "Наблюдение: на 00:57 " + OBS + T99_TAIL),
])
def test_real_templates_render_exactly(kb, type_id, obs, expected):
    d = kb.enrich({"type": type_id, "timecode_sec": 57.3, "obs": obs})
    assert d["description"] == expected
    if type_id == "T99" and obs:
        assert d["description"].startswith("Наблюдение: на 00:57 кассир передал пачку купюр. ")


def test_type_id_is_stripped(kb):
    d = kb.enrich({"type": " T1 ", "timecode_sec": 1})
    assert d["type_id"] == "T1" and d["rule"] is not None


def test_description_cleanup_unknown_placeholders():
    out = render_template("На {t} {who} ушёл {obs} , и {x}.  Конец {obs}.", 5)
    assert out == "На 00:05 ушёл, и. Конец."
    assert render_template("X {a},{b}, Y", 0) == "X, Y"
    assert render_template("X {a}, , Y", 0) == "X, Y"


def test_description_missing_template_falls_back(tmp_path):
    kb = load_rules(write_json(tmp_path / "r.json", {"Z1": {"title_ru": "Без шаблона"}}))
    assert kb.enrich({"type": "Z1", "timecode_sec": 1})["description"] == "Без шаблона"
    assert kb.enrich({"type": "Z1", "timecode_sec": 1, "description": "своё"})["description"] == "своё"
    assert kb.enrich({"type": "Z1", "timecode_sec": 1, "obs": "детали"})["description"] == "Без шаблона: детали"


def test_rule_template_wins_over_analyzer_description(kb):
    d = kb.enrich({"type": "T1", "timecode_sec": 57.3, "description": "  Своё описание ", "obs": OBS})
    assert d["description"] == T1_HEAD + ", " + OBS + T1_TAIL
    assert d["analyzer_description"] == "Своё описание"  # preserved, not lost


def test_analyzer_description_used_without_template(kb):
    # unknown id -> analyzer text, no analyzer_description duplicate
    d = kb.enrich({"type": "X1", "timecode_sec": 1, "description": "Своё описание"})
    assert d["description"] == "Своё описание" and "analyzer_description" not in d
    # known id without a template -> analyzer text
    assert "analyzer_description" not in kb.enrich({"type": "T1", "timecode_sec": 1})


def test_analyzer_description_absent_when_identical(tmp_path):
    kb2 = load_rules(write_json(tmp_path / "r.json", {"Z1": {"title_ru": "Без шаблона"}}))
    d = kb2.enrich({"type": "Z1", "timecode_sec": 1, "description": "своё"})
    assert d["description"] == "своё" and "analyzer_description" not in d


# ---------- lenient loader ----------

ENTRY = {"title_ru": "Тест", "citations": [{"document": "Док", "legal_conf": "low"}], "severity": 3}


@pytest.mark.parametrize("shape", ["map", "wrapped_map", "wrapped_list", "list"])
def test_lenient_schema_variants(tmp_path, shape):
    data = {
        "map": {"A1": ENTRY},
        "wrapped_map": {"rules": {"A1": ENTRY}},
        "wrapped_list": {"rules": [{"id": "A1", **ENTRY}]},
        "list": [{"id": "A1", **ENTRY}],
    }[shape]
    kb = load_rules(write_json(tmp_path / "r.json", data))
    assert set(kb.rules) == {"A1"}
    pub = kb.public_dict()["A1"]
    assert pub["title_ru"] == "Тест" and pub["title_en"] == "" and pub["description_template"] == ""
    assert pub["severity"] == 3  # unknown keys preserved
    assert pub["citations"] == [{"document": "Док", "point": "", "quote": "", "url": "", "legal_conf": "low"}]
    d = kb.enrich({"type": "A1", "timecode_sec": 0})
    assert d["rule"] == pub["citations"][0]


def test_entry_named_rules_is_not_unwrapped(tmp_path):
    kb = load_rules(write_json(tmp_path / "r.json", {"rules": {"title_ru": "Правило rules"}}))
    assert set(kb.rules) == {"rules"}


def test_invalid_entries_skipped(tmp_path, caplog):
    data = {
        "ok": {"title_ru": "Хорошее", "citations": [{"document": "Д"}, {"point": "no document"}, "junk"]},
        "no_title": {"title_en": "x"},
        "empty_title": {"title_ru": ""},
        "bad_citations": {"title_ru": "x", "citations": "nope"},
        "not_obj": 5,
    }
    kb = load_rules(write_json(tmp_path / "r.json", data))
    assert set(kb.rules) == {"ok"}
    assert [c.document for c in kb.rules["ok"].citations] == ["Д"]
    assert "skipping" in caplog.text


def test_zero_valid_entries_is_an_error(tmp_path):
    with pytest.raises(RulesError):
        load_rules(write_json(tmp_path / "r.json", {"a": {"title_en": "x"}}))
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(RulesError):
        load_rules(bad)


# ---------- lookup order ----------

def test_rules_path_env_loading(tmp_path, monkeypatch, no_external_rules):
    p = write_json(tmp_path / "custom.json", {"C1": {"title_ru": "Своё"}})
    monkeypatch.setenv("RULES_PATH", str(p))
    kb = load_rules_auto()
    assert kb.path == p and set(kb.rules) == {"C1"}
    # explicit argument (Settings.rules_path) beats the env
    p2 = write_json(tmp_path / "other.json", {"C2": {"title_ru": "Другое"}})
    assert set(load_rules_auto(str(p2)).rules) == {"C2"}


@pytest.mark.parametrize("content", [None, "{broken", {"x": {"title_en": "no title_ru"}}])
def test_rules_path_fallback_to_bundled(tmp_path, monkeypatch, no_external_rules, content):
    p = tmp_path / "custom.json"
    if isinstance(content, str):
        p.write_text(content, encoding="utf-8")
    elif content is not None:
        write_json(p, content)
    monkeypatch.setenv("RULES_PATH", str(p))
    kb = load_rules_auto()
    assert kb.path == BUNDLED_RULES_PATH and set(kb.rules) == SPEC_IDS


def test_default_lookup_order(tmp_path, monkeypatch):
    monkeypatch.delenv("RULES_PATH", raising=False)
    det = write_json(tmp_path / "det.json", {"D1": {"title_ru": "Детектор"}})
    spec = write_json(tmp_path / "spec.json", {"S1": {"title_ru": "Спека"}})
    monkeypatch.setattr(rules_mod, "DETECTOR_RULES_PATH", det)
    monkeypatch.setattr(rules_mod, "SPEC_RULES_PATH", spec)
    assert set(load_rules_auto().rules) == {"D1"}
    monkeypatch.setattr(rules_mod, "DETECTOR_RULES_PATH", tmp_path / "missing.json")
    assert set(load_rules_auto().rules) == {"S1"}
    monkeypatch.setattr(rules_mod, "SPEC_RULES_PATH", tmp_path / "missing2.json")
    assert load_rules_auto().path == BUNDLED_RULES_PATH


def test_public_dict(kb):
    pub = kb.public_dict()
    assert set(pub) == set(kb.rules)
    assert pub["T1"]["citations"] and pub["T99"]["citations"] == []


def test_enrich_passes_legal_strength(kb):
    d = kb.enrich({"type": "T1", "timecode_sec": 1, "legal_conf": 0.7, "legal_strength": "Medium"})
    assert d["legal_strength"] == "medium" and d["legal_conf"] == 0.7
    assert kb.enrich({"type": "T1", "timecode_sec": 1})["legal_strength"] is None
    assert kb.enrich({"type": "T1", "timecode_sec": 1, "legal_strength": "huge"})["legal_strength"] is None
