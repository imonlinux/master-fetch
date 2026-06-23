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
    # Default reranker path to unavailable; neural/deep tests override below.
    monkeypatch.setattr(search_mod, "neural_rerank", lambda q, r: None)

    async def _no_deep(q, r, peek_n=15):
        return None
    monkeypatch.setattr(search_mod, "deep_rerank", _no_deep)


# ─── _validate_mode ──────────────────────────────────────────────────────────

def test_validate_mode_accepts_implemented():
    assert _validate_mode(None) == "auto"
    assert _validate_mode("auto") == "auto"
    assert _validate_mode("KEYWORD") == "keyword"
    assert _validate_mode("neural") == "neural"
    assert _validate_mode("deep") == "deep"


def test_validate_mode_rejects_unimplemented():
    # All v7 modes (auto/keyword/neural/deep/find_similar) are now implemented.
    for bad in ("semantic", "magic", "", "autox"):
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
    assert any(":keyword:" in t for t in types)
    assert any(":neural:" in t for t in types)


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


# ─── Phase 3: deep content-aware rerank ──────────────────────────────────────

def _stub_deep(monkeypatch, pairs, peeks):
    async def fake_deep(query, results, peek_n=15):
        return pairs, peeks
    monkeypatch.setattr(search_mod, "deep_rerank", fake_deep)


def _neural_should_not_run(q, r):
    raise AssertionError("neural rerank must not run in deep mode")


def test_mode_deep_uses_deep_rerank_and_attaches_peeks(monkeypatch):
    results = [
        _raw("A", "https://a.com", snip="python asyncio"),
        _raw("B", "https://b.com", snip="event loop"),
        _raw("C", "https://c.com", snip="cooking"),
    ]
    _stub_multi(monkeypatch, results)
    pairs = [(results[1], 0.9), (results[0], 0.5), (results[2], 0.3)]
    peeks = {"https://b.com": "real content about asyncio event loops",
             "https://a.com": "some content a"}
    _stub_deep(monkeypatch, pairs, peeks)
    monkeypatch.setattr(search_mod, "neural_rerank", _neural_should_not_run)
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    resp = asyncio.run(_ss(srv, "asyncio event loop", mode="deep", cache_ttl=0))
    assert resp.rerank_mode == "deep"
    assert [r.url for r in resp.results] == ["https://b.com", "https://a.com", "https://c.com"]
    # peek is populated for top results that have one (truncated to 200 chars).
    assert resp.results[0].peek and "asyncio event loops" in resp.results[0].peek
    assert resp.results[1].peek  # a.com had a peek
    # third result had no peek -> empty.
    assert resp.results[2].peek == ""


def test_mode_deep_falls_back_when_reranker_unavailable(monkeypatch):
    results = [
        _raw("python asyncio", "https://b.com", snip="asyncio event loop python"),
        _raw("cooking", "https://a.com", snip="food recipes"),
    ]
    _stub_multi(monkeypatch, results)
    async def fake_deep(query, r, peek_n=15):
        return None
    monkeypatch.setattr(search_mod, "deep_rerank", fake_deep)
    monkeypatch.setattr(search_mod, "neural_rerank", lambda q, r: None)  # neural also unavailable
    monkeypatch.setattr(search_mod, "unavailable_reason", lambda: "needs hound-mcp[all]")
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    resp = asyncio.run(_ss(srv, "python asyncio", mode="deep", cache_ttl=0))
    assert resp.rerank_mode == "keyword"  # fell all the way back to BM25
    assert "deep rerank unavailable" in resp.fetch_hint


def test_research_mode_auto_uses_deep(monkeypatch):
    results = [_raw("A", "https://a.com", snip="python asyncio")]
    _stub_multi(monkeypatch, results)
    deep_called = {"n": 0}

    async def fake_deep(query, r, peek_n=15):
        deep_called["n"] += 1
        return [(r[0], 0.9)], {"https://a.com": "real content about asyncio"}
    monkeypatch.setattr(search_mod, "deep_rerank", fake_deep)
    monkeypatch.setattr(search_mod, "neural_rerank", lambda q, r: None)
    from master_fetch.server import MasterFetchServer, ResponseModel
    srv = MasterFetchServer()

    async def fake_smart_fetch(url, cache_ttl=3600, max_content_chars=8000, **kw):
        return ResponseModel(status=200, content=["c"], url=url, content_ok=True, summary="ok")
    srv.smart_fetch = fake_smart_fetch  # type: ignore
    # mode defaults to 'auto' + fetch_content=True -> auto-upgrades to 'deep'.
    resp = asyncio.run(_ss(srv, "python asyncio", fetch_content=True, cache_ttl=0))
    assert deep_called["n"] == 1
    assert resp.rerank_mode == "deep"
    assert resp.fetched_count == 1


# ─── peek helpers (search_engines) ───────────────────────────────────────────

def test_peek_content_returns_trafilatura_extract(monkeypatch):
    import master_fetch.search_engines as se
    html = ("<html><head><title>x</title></head><body><main>"
            "<p>Real page content about asyncio event loops in python. "
            "It explains the event loop, tasks and coroutines in detail.</p>"
            "</main></body></html>")

    async def fake_get(url, *, method="GET", form=None, timeout=8):
        return (html, 200, False)
    monkeypatch.setattr(se, "_impersonated_get", fake_get)
    out = asyncio.run(se.peek_content("https://x.com"))
    assert out and "asyncio" in out


def test_peek_content_empty_on_block(monkeypatch):
    import master_fetch.search_engines as se

    async def fake_get(url, **kw):
        return (None, 403, True)
    monkeypatch.setattr(se, "_impersonated_get", fake_get)
    assert asyncio.run(se.peek_content("https://x.com")) == ""


def test_peek_many_drops_failed_peeks(monkeypatch):
    import master_fetch.search_engines as se

    async def fake_get(url, **kw):
        if "fail" in url:
            raise RuntimeError("boom")
        return (f"<html><body><main><p>content for {url}</p></main></body></html>", 200, False)
    monkeypatch.setattr(se, "_impersonated_get", fake_get)
    out = asyncio.run(se.peek_many(["https://a.com", "https://b.com", "https://fail.com"]))
    assert "https://a.com" in out and "https://b.com" in out
    assert "https://fail.com" not in out  # the failing peek is dropped


def test_deep_rerank_returns_none_when_no_reranker(monkeypatch):
    import master_fetch.reranker as rer
    monkeypatch.setattr(rer, "get_reranker", lambda: None)
    assert asyncio.run(rer.deep_rerank("q", [_raw("a", "https://a.com")])) is None


# ─── Phase 4: find_similar + autoretrieval (expand) ───────────────────────────

def test_validate_expand_accepts_and_rejects():
    from master_fetch.search import _validate_expand
    assert _validate_expand(None) == 1
    assert _validate_expand(3) == 3
    for bad in (0, 6, -1, True, "2"):
        with pytest.raises(SecurityError):
            _validate_expand(bad)


def test_expand_query_generates_distinct_variants():
    from master_fetch.search import _expand_query
    out = _expand_query("transformer attention", 3)
    assert len(out) == 3
    assert out[0] == "transformer attention"
    assert len(set(out)) == 3
    assert _expand_query("x", 1) == ["x"]


def _stub_for_expand(monkeypatch, fake_multi):
    monkeypatch.setattr(search_mod, "multi_search", fake_multi)
    monkeypatch.setattr(search_mod, "neural_rerank", lambda q, r: None)

    async def _no_deep(q, r, peek_n=15):
        return None
    monkeypatch.setattr(search_mod, "deep_rerank", _no_deep)


def test_expand_runs_subqueries_and_merges(monkeypatch):
    seen = []

    async def fake_multi(query, max_results, *, engines, site, exclude_sites,
                         region, freshness, server):
        seen.append(query)
        return [_raw(query, f"https://{abs(hash(query)) % 9999}.com", snip=query)
                ], [EngineReport("duckduckgo", ok=True)]
    _stub_for_expand(monkeypatch, fake_multi)
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    resp = asyncio.run(_ss(srv, "transformer attention", expand=3, mode="keyword", cache_ttl=0))
    assert len(seen) == 3
    assert seen[0] == "transformer attention"
    assert len(set(seen)) == 3  # 3 distinct sub-query variants
    # Results from all 3 subqueries merged (distinct URLs -> not deduped away).
    assert resp.total_results >= 2


def test_expand_one_is_no_expansion(monkeypatch):
    seen = []

    async def fake_multi(query, max_results, **kw):
        seen.append(query)
        return [_raw("a", "https://a.com")], [EngineReport("duckduckgo", ok=True)]
    _stub_for_expand(monkeypatch, fake_multi)
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    asyncio.run(_ss(srv, "python", expand=1, mode="keyword", cache_ttl=0))
    assert len(seen) == 1  # no expansion


def test_find_similar_fetches_source_and_reranks_vs_source(monkeypatch):
    candidates = [
        _raw("A", "https://a.com", snip="asyncio event loop"),
        _raw("B", "https://b.com", snip="transformer attention"),
        _raw("C", "https://c.com", snip="cooking recipes"),
    ]

    async def fake_source(url):
        return ("Asyncio Guide", "this page is about asyncio event loops in python")
    monkeypatch.setattr(search_mod, "fetch_source_for_similar", fake_source)

    async def fake_gather(query, expand, max_results, engines, site, exclude_sites,
                          region, freshness, server):
        return candidates, [EngineReport("duckduckgo", ok=True)]
    monkeypatch.setattr(search_mod, "_gather", fake_gather)

    class FakeRer:
        def score(self, q, docs):
            # q is the source page text; score favors the asyncio candidate.
            return [0.9, 0.2, 0.1]
    monkeypatch.setattr(search_mod, "get_reranker", lambda: FakeRer())
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    resp = asyncio.run(_ss(srv, "ignored", mode="find_similar",
                           url="https://src.com/x", cache_ttl=0))
    assert resp.rerank_mode == "find_similar"
    assert resp.query == "https://src.com/x"  # response query is the source URL
    assert resp.results[0].url == "https://a.com"  # asyncio candidate ranked first
    assert "find_similar to https://src.com/x" in resp.fetch_hint


def test_find_similar_requires_url(monkeypatch):
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    resp = asyncio.run(_ss(srv, "not a url", mode="find_similar", cache_ttl=0))
    assert resp.results == []
    assert "requires a url" in resp.error


def test_find_similar_source_unfetchable(monkeypatch):
    async def fake_source(url):
        return "", ""
    monkeypatch.setattr(search_mod, "fetch_source_for_similar", fake_source)
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    resp = asyncio.run(_ss(srv, "https://src.com/x", mode="find_similar", cache_ttl=0))
    assert resp.results == []
    assert "could not fetch" in resp.error


def test_find_similar_falls_back_to_keyword_when_no_reranker(monkeypatch):
    candidates = [
        _raw("python asyncio", "https://a.com", snip="python asyncio event loop"),
        _raw("cooking", "https://b.com", snip="food recipes"),
    ]

    async def fake_source(url):
        return ("Python Asyncio", "python asyncio event loop content")
    monkeypatch.setattr(search_mod, "fetch_source_for_similar", fake_source)

    async def fake_gather(query, expand, max_results, engines, site, exclude_sites,
                          region, freshness, server):
        return candidates, [EngineReport("duckduckgo", ok=True)]
    monkeypatch.setattr(search_mod, "_gather", fake_gather)
    monkeypatch.setattr(search_mod, "get_reranker", lambda: None)
    monkeypatch.setattr(search_mod, "unavailable_reason", lambda: "needs hound-mcp[all]")
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    resp = asyncio.run(_ss(srv, "x", mode="find_similar",
                           url="https://src.com/x", cache_ttl=0))
    assert resp.rerank_mode == "find_similar"
    # BM25 on derived query "Python Asyncio" -> a.com (python asyncio) first.
    assert resp.results[0].url == "https://a.com"
    assert "keyword BM25" in resp.fetch_hint  # the fallback note
