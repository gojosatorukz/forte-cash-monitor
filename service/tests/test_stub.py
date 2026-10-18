import time

import pytest

from app.analyzers import AnalyzerLoadError, load_analyzer
from app.analyzers.base import PartialSink
from app.analyzers.stub import StubAnalyzer
from app.models import AnalysisResult, Violation


def _run(analyzer, path, budget=30.0):
    calls = []
    sink = PartialSink()
    result = analyzer.analyze(
        path, lambda f, s: calls.append((f, s)), time.monotonic() + budget, sink
    )
    return result, calls, sink


def test_stub_no_violation(test_video):
    result, calls, sink = _run(StubAnalyzer(debug=False), test_video)
    assert isinstance(result, AnalysisResult)
    assert result.verdict == "no_violation"
    assert result.violations == []
    assert sink.snapshot() == []
    assert result.meta["frames_decoded"] >= 3
    assert calls, "progress callback must be called"
    fracs = [f for f, _ in calls]
    assert all(0.0 <= f <= 1.0 for f in fracs)
    assert fracs == sorted(fracs)
    assert all(isinstance(s, str) and s for _, s in calls)


def test_stub_debug_violation(cyrillic_video):
    result, _, sink = _run(StubAnalyzer(debug=True), cyrillic_video)
    assert result.verdict == "violation"
    assert len(result.violations) == 1
    v = result.violations[0]
    assert v.type == "T99"
    assert v.fact_conf == 0.5 and v.confidence == 0.5
    assert v.timecode_sec == pytest.approx(1.5, abs=0.1)
    assert "демо" in v.description.lower() or "тест" in v.description.lower()
    assert len(v.evidence_frames) == 2
    assert all(f.image_base64.startswith("/9j/") for f in v.evidence_frames)
    assert len(sink.snapshot()) == 1


def test_stub_debug_from_env(monkeypatch):
    monkeypatch.setenv("STUB_DEBUG", "1")
    assert StubAnalyzer().debug is True
    monkeypatch.setenv("STUB_DEBUG", "0")
    assert StubAnalyzer().debug is False


def test_stub_honours_deadline(test_video):
    t0 = time.monotonic()
    result, _, _ = _run(StubAnalyzer(debug=False), test_video, budget=-1.0)
    assert time.monotonic() - t0 < 5
    assert result.meta["stopped_early"] is True
    assert result.verdict == "no_violation"


def test_partial_sink_thread_safe():
    import threading

    sink = PartialSink()
    v = Violation(type="x", timecode_sec=0, description="d")

    def worker():
        for _ in range(500):
            sink.add(v)

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    snap = sink.snapshot()
    assert len(snap) == 2000
    snap.clear()
    assert len(sink.snapshot()) == 2000  # snapshot is a copy


def test_load_analyzer():
    a = load_analyzer("app.analyzers.stub:StubAnalyzer")
    assert isinstance(a, StubAnalyzer)
    assert a.name == "stub"


@pytest.mark.parametrize(
    "dotted", ["app.analyzers.stub", "no.such.module:X", "app.analyzers.stub:Nope", "app.models:VideoMeta"]
)
def test_load_analyzer_errors(dotted):
    with pytest.raises(AnalyzerLoadError):
        load_analyzer(dotted)
