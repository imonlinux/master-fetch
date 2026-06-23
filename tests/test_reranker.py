"""Tests for v7 Phase 2 neural rerank wiring.

The real ONNX model is never downloaded in CI: `neural_rerank` and
`unavailable_reason` (imported into master_fetch.search) are monkeypatched.
Verifies mode validation, neural-when-available, graceful keyword fallback with
a note when neural is requested but unavailable, keyword-never-calls-neural,
auto-detection, and mode-aware cache keys.
"""

import asyncio
import json

import pytest

import master_fetch.search as search_mod
from master_fetch.search import (
    SearchResponseModel, ResearchResponseModel,
    _validate_mode, smart_search as _ss,
)
from master_fetch.search_engines import RawResult, EngineReport
from master_fetch.security import SecurityError


def _raw(title, url, src="duckduckgo", pos=1, snip="python asyncio"):
    return RawResult(title=title, url=url, snippet=snip, source=src, position=pos)


def _stub_multi(monkeypatch, results):
    async def fake_multi(query, max_results, *, engines, site, exclude_sites,
                         region, freshness, server):
        return results, [EngineReport("duckduckgo", ok=True)]
    monkeypatch.setattr(search_mod, "multi_search", fake_multi)


# ─── _validate_mode ──────────────────────────────────────────────────────────

def test_validate_mode_accepts_implemented():
    assert _validate_mode(None) == "auto"
    assert _validate_mode("auto") == "auto"
    assert _validate_mode("KEYWORD") == "keyword"
    assert _validate_mode("neural") == "neural"


def test_validate_mode_rejects_unimplemented():
    # deep (Phase 3) and find_similar (Phase 4) are not wired yet.
    for bad in ("deep", "find_similar", "semantic", "magic", ""):
        with pytest.raises(SecurityError):
            _validate_mode(bad)


# ─── neural-when-available ───────────────────────────────────────────────────

def test_mode_neural_uses_reranker_when_available(monkeypatch):
    results = [_raw("A", "https://a.com", snip="python asyncio"),
               _raw("B", "https://b.com", snip="cooking recipes")]
    _stub_multi(monkeypatch, results)
    # Neural reranker returns a DIFFERENT order than BM25 (b before a) to prove it
    # was actually used.
    def fake_neural(query, ranked):
        return [(ranked[1], 0.9), (ranked[0], 0.2)]
    monkeypatch.setattr(search_mod, "neural_rerank", fake_neural)
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    resp = asyncio.run(_ss(srv, "python asyncio", mode="neural", cache_ttl=0))
    assert resp.rerank_mode == "neural"
    assert resp.results[0].url == "https://b.com"  # neural order, not BM25
    assert resp.results[0].relevance_score == 0.9
    assert resp.results[1].relevance_score == 0.2


def test_mode_auto_uses_neural_when_available(monkeypatch):
    results = [_raw("A", "https://a.com")]
    _stub_multi(monkeypatch, results)
    called = {"n": 0}
    def fake_neural(query, ranked):
        called["n"] += 1
        return [(ranked[0], 0.77)]
    monkeypatch.setattr(search_mod, "neural_rerank", fake_neural)
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    resp = asyncio.run(_ss(srv, "python asyncio", mode="auto", cache_ttl=0))
    assert resp.rerank_mode == "neural"
    assert called["n"] == 1
    assert resp.results[0].relevance_score == 0.77


# ─── graceful keyword fallback ───────────────────────────────────────────────

def test_mode_neural_falls_back_to_keyword_with_note(monkeypatch):
    results = [_raw("python asyncio guide", "https://b.com", snip="asyncio event loop python"),
               _raw("cooking", "https://a.com", snip="food recipes")]
    _stub_multi(monkeypatch, results)
    monkeypatch.setattr(search_mod, "neural_rerank", lambda q, r: None)
    monkeypatch.setattr(search_mod, "unavailable_reason",
                        lambda: "neural rerank needs hound-mcp[all]")
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    resp = asyncio.run(_ss(srv, "python asyncio", mode="neural", cache_ttl=0))
    assert resp.rerank_mode == "keyword"
    # BM25 order preserved (b.com is the python/asyncio doc).
    assert resp.results[0].url == "https://b.com"
    # The note explaining the fallback is surfaced in fetch_hint.
    assert "neural rerank unavailable" in resp.fetch_hint
    assert "hound-mcp[all]" in resp.fetch_hint


def test_mode_auto_falls_back_to_keyword_silently_when_unavailable(monkeypatch):
    results = [_raw("A", "https://a.com")]
    _stub_multi(monkeypatch, results)
    monkeypatch.setattr(search_mod, "neural_rerank", lambda q, r: None)
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    resp = asyncio.run(_ss(srv, "python asyncio", mode="auto", cache_ttl=0))
    assert resp.rerank_mode == "keyword"
    # auto fallback is silent (no note) since the user did not explicitly ask for neural.
    assert "neural rerank unavailable" not in resp.fetch_hint


def test_mode_keyword_never_calls_neural(monkeypatch):
    results = [_raw("A", "https://a.com")]
    _stub_multi(monkeypatch, results)
    called = {"n": 0}
    monkeypatch.setattr(search_mod, "neural_rerank", lambda q, r: called.__setitem__("n", called["n"] + 1) or None)
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    resp = asyncio.run(_ss(srv, "python asyncio", mode="keyword", cache_ttl=0))
    assert resp.rerank_mode == "keyword"
    assert called["n"] == 0  # neural reranker was not invoked


# ─── mode-aware cache key ────────────────────────────────────────────────────

def test_neural_and_keyword_cache_keys_differ(monkeypatch):
    results = [_raw("A", "https://a.com")]
    _stub_multi(monkeypatch, results)
    monkeypatch.setattr(search_mod, "neural_rerank", lambda q, r: None)
    store = {}
    async def mock_get(url, etype, css=None, ttl=300, cache_dir=None, **kw):
        return None
    async def mock_set(url, etype, content, status, css=None, ttl=300, cache_dir=None, **kw):
        store[(url, etype)] = True
    monkeypatch.setattr(search_mod, "get_cached", mock_get)
    monkeypatch.setattr(search_mod, "set_cached", mock_set)
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    asyncio.run(_ss(srv, "python asyncio", mode="keyword", cache_ttl=60))
    asyncio.run(_ss(srv, "python asyncio", mode="neural", cache_ttl=60))
    types = [t for (_u, t) in store.keys()]
    assert len(set(types)) == 2
    assert any(t.endswith(":keyword") for t in types)
    assert any(t.endswith(":neural") for t in types)


# ─── reranker module contract (no network) ───────────────────────────────────

def test_rerank_returns_none_when_get_reranker_none(monkeypatch):
    import master_fetch.reranker as rer
    monkeypatch.setattr(rer, "get_reranker", lambda: None)
    assert rer.rerank("q", [_raw("a", "https://a.com")]) is None


def test_rerank_returns_none_for_empty_results(monkeypatch):
    import master_fetch.reranker as rer
    class FakeRer:
        def score(self, q, docs): raise AssertionError("should not be called")
    monkeypatch.setattr(rer, "get_reranker", lambda: FakeRer())
    assert rer.rerank("q", []) is None


def test_get_reranker_returns_none_when_deps_missing(monkeypatch):
    # Simulate a lean install: onnxruntime import fails.
    import builtins
    real_import = builtins.__import__
    def fake_import(name, *a, **k):
        if name in ("onnxruntime", "tokenizers", "numpy"):
            raise ImportError(f"no module named {name}")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    import master_fetch.reranker as rer
    rer._reranker = None
    rer._reranker_tried = False
    rer._reranker_unavailable_reason = ""
    assert rer.get_reranker() is None
    assert rer.unavailable_reason()  # a human-readable reason
    # And rerank() then returns None (caller falls back to BM25).
    assert rer.rerank("q", [_raw("a", "https://a.com")]) is None
