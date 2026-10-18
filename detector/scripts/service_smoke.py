"""End-to-end smoke check of ForteAnalyzer against the real service package (read-only).

Loads the analyzer the way the service does (`app.analyzers.load_analyzer`), runs it on real videos
with a real PartialSink and deadline, validates every Violation with the service's pydantic models and
renders it with the service's RulesKB (the public JSON the jury sees). Base64 is truncated.

    flock ROOT/work/gpu.lock uv run --with pydantic python scripts/service_smoke.py [X-26 X-43 ...]

Env: SERVICE_DIR (default ROOT/service), BUDGET_S (default 290), DUMP_DIR (write evidence JPEGs there).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = Path(os.environ.get("SERVICE_DIR", "/home/datamata/Projects/datathon2026/service"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SERVICE_DIR))

from app.analyzers import load_analyzer  # noqa: E402
from app.analyzers.base import PartialSink  # noqa: E402
from app.models import AnalysisResult, Violation  # noqa: E402
from app.rules import load_rules_auto  # noqa: E402

from forte_detector import config  # noqa: E402

ANALYZER = "forte_detector.service_adapter:ForteAnalyzer"


def find_video(vid: str) -> Path:
    for p in sorted(config.videos_dir().glob("*.mov")):
        if unicodedata.normalize("NFC", p.stem).replace("Х", "X") == vid:
            return p
    raise SystemExit(f"video {vid} not found in {config.videos_dir()}")


def trunc(obj):
    if isinstance(obj, dict):
        return {k: (v[:40] + f"...<{len(v)} chars>" if k == "image_base64" and isinstance(v, str) else trunc(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [trunc(x) for x in obj]
    return obj


def check_fields() -> None:
    """Which adapter fields the service's Violation model keeps (unknown fields are ignored)."""
    from forte_detector.schema import Event
    from forte_detector.service_adapter import ForteAnalyzer

    e = Event(type="T1", timecode_sec=1.0, confidence=0.5, description_ru="x", start_sec=0.5, end_sec=2.0,
              fact_conf=0.6, legal_conf="medium", obs="кассир вышел", title_ru="t")
    v = ForteAnalyzer.violation(e, [])
    kept = v.model_dump()
    sent = ["type", "timecode_sec", "start_sec", "end_sec", "confidence", "fact_conf", "legal_conf",
            "legal_strength", "title_ru", "obs", "description", "evidence_frames"]
    print("fields kept:", [k for k in sent if k in kept])
    print("fields dropped by the service model:", [k for k in sent if k not in kept] or "none")


def main(ids: list[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    check_fields()
    if ids == ["--fields"]:
        return
    t0 = time.perf_counter()
    analyzer = load_analyzer(ANALYZER)
    print(f"ANALYZER={ANALYZER} name={analyzer.name!r} init={time.perf_counter() - t0:.1f}s")
    print("warm:", json.dumps(getattr(analyzer, "warm_info", {}), ensure_ascii=False))
    rules = load_rules_auto()
    print(f"rules KB: {rules.path} ({len(rules)} entries)")

    for vid in ids:
        path = find_video(vid)
        sink = PartialSink()
        stages: list[tuple[float, str]] = []
        added_at: list[float] = []
        orig_add = sink.add

        def add(v, _orig=orig_add, _t=time.perf_counter):
            added_at.append(round(_t() - ts, 1))
            _orig(v)

        sink.add = add  # type: ignore[method-assign]

        def progress(p, m):
            if not stages or stages[-1][1] != m:
                stages.append((p, m))

        budget = float(os.environ.get("BUDGET_S", "290"))
        ts = time.perf_counter()
        res = analyzer.analyze(path, progress, time.monotonic() + budget, sink)
        dt = time.perf_counter() - ts
        assert isinstance(res, AnalysisResult), type(res)
        print(f"\n=== {vid} ({path.name}) runtime {dt:.1f}s  verdict={res.verdict}  "
              f"violations={len(res.violations)}  partial adds={len(sink)} at {added_at}")
        print("stages:", " | ".join(f"{p:.2f} {m}" for p, m in stages))
        print("meta:", json.dumps(res.meta, ensure_ascii=False)[:1500])
        for v in res.violations:
            assert isinstance(v, Violation)
            raw = v.model_dump()
            again = Violation.model_validate(raw)  # round trip keeps every field
            assert again == v
            dropped = [k for k in ("start_sec", "end_sec", "fact_conf", "legal_conf", "obs", "description",
                                   "title_ru", "confidence") if raw.get(k) is None]
            print("violation:", json.dumps(trunc(raw), ensure_ascii=False))
            print("  None/unset fields:", dropped or "none")
            print("  public:", json.dumps(trunc(rules.enrich(v)), ensure_ascii=False))
            if os.environ.get("DUMP_DIR"):
                out = Path(os.environ["DUMP_DIR"])
                out.mkdir(parents=True, exist_ok=True)
                for f in v.evidence_frames:
                    (out / f"{vid}_{v.type}_{f.timestamp_sec:.2f}.jpg").write_bytes(base64.b64decode(f.image_base64))
    print(f"\ntotal {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    main(sys.argv[1:] or ["X-26", "X-43"])
