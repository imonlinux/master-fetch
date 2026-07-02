"""Tests for v7 Phase 2 neural rerank wiring.

The real ONNX model is never downloaded in CI: `neural_rerank` and
`unavailable_reason` (imported into master_fetch.search) are monkeypatched.
Verifies mode validation, neural-when-available, graceful consensus fallback
with a note when neural is requested but unavailable, keyword-rejected, auto-
detection, and mode-aware cache keys.
"""

import asyncio
import json

import pytest

import master_fetch.search as search_mod
from master_fetch.search import (
    SearchResponseModel,
    _validate_mode, smart_search as _ss,
)
from master_fetch.search_engines import RawResult, EngineReport
from master_fetch.security import SecurityError


def _raw(title, url, src="duckduckgo", pos=1, snip="python asyncio"):
    return RawResult(title=title, url=url, snippet=snip, source=src, position=pos)


def _stub_multi(monkeypatch, results):
    async def fake_multi(query, max_results, *, engines, site, exclude_sites,
                         region, freshness, page=0, server=None):
        return results, [EngineReport("duckduckgo", ok=True)]
    monkeypatch.setattr(search_mod, "multi_search", fake_multi)
    # Default reranker path to unavailable; neural tests override below.
    monkeypatch.setattr(search_mod, "neural_rerank", lambda q, r: None)


# ─── _validate_mode ──────────────────────────────────────────────────────────

def test_validate_mode_accepts_implemented():
    assert _validate_mode(None) == "auto"
    assert _validate_mode("auto") == "auto"
    assert _validate_mode("NEURAL") == "neural"
    assert _validate_mode("neural") == "neural"
    assert _validate_mode("find_similar") == "find_similar"


def test_validate_mode_rejects_unimplemented():
    # keyword/bm25 removed (redundant); auto/neural/find_similar are valid.
    for bad in ("deep", "semantic", "magic", "", "autox", "keyword", "bm25"):
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

def test_mode_neural_falls_back_to_merge_with_note(monkeypatch):
    results = [_raw("python asyncio guide", "https://b.com", snip="asyncio event loop python"),
               _raw("cooking", "https://a.com", snip="food recipes")]
    _stub_multi(monkeypatch, results)
    monkeypatch.setattr(search_mod, "neural_rerank", lambda q, r: None)
    monkeypatch.setattr(search_mod, "unavailable_reason",
                        lambda: "neural rerank needs hound-mcp[all]")
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    resp = asyncio.run(_ss(srv, "python asyncio", mode="neural", cache_ttl=0))
    assert resp.rerank_mode == "merge"  # neural unavailable -> consensus+position order
    # merge order preserved (b.com is first in the stubbed results).
    assert resp.results[0].url == "https://b.com"
    # The note explaining the fallback is surfaced in fetch_hint.
    assert "neural rerank unavailable" in resp.fetch_hint
    assert "hound-mcp[all]" in resp.fetch_hint


def test_mode_auto_falls_back_to_merge_silently_when_unavailable(monkeypatch):
    results = [_raw("A", "https://a.com")]
    _stub_multi(monkeypatch, results)
    monkeypatch.setattr(search_mod, "neural_rerank", lambda q, r: None)
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    resp = asyncio.run(_ss(srv, "python asyncio", mode="auto", cache_ttl=0))
    assert resp.rerank_mode == "merge"  # neural unavailable -> consensus+position order
    # auto fallback is silent (no note) since the user did not explicitly ask for neural.
    assert "neural rerank unavailable" not in resp.fetch_hint


def test_keyword_mode_rejected(monkeypatch):
    # keyword mode was REMOVED (redundant; neural matches its speed and ranks
    # better). Passing it must be rejected, not silently fall back.
    results = [_raw("A", "https://a.com")]
    _stub_multi(monkeypatch, results)
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    resp = asyncio.run(_ss(srv, "python asyncio", mode="keyword", cache_ttl=0))
    assert resp.error and "Invalid mode" in resp.error and "keyword" in resp.error
    assert resp.results == []


# ─── mode-aware cache key ────────────────────────────────────────────────────

def test_neural_and_auto_cache_keys_differ(monkeypatch):
    # The mode string is part of the cache key, so different valid modes get
    # different cache entries (even though auto + neural now behave the same).
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
    asyncio.run(_ss(srv, "python asyncio", mode="auto", cache_ttl=60))
    asyncio.run(_ss(srv, "python asyncio", mode="neural", cache_ttl=60))
    types = [t for (_u, t) in store.keys()]
    assert len(set(types)) == 2
    assert any(":auto:" in t for t in types)
    assert any(":neural:" in t for t in types)


# ─── reranker module contract (no network) ───────────────────────────────────

def test_rerank_returns_none_when_get_reranker_none(monkeypatch):
    import master_fetch.reranker as rer
    monkeypatch.setattr(rer, "get_reranker", lambda: None)
    assert rer.rerank("q", [_raw("a", "https://a.com")]) is None


# ─── Phase 4: find_similar + autoretrieval (expand) ───────────────────────────

def test_find_similar_fetches_source_and_reranks_vs_source(monkeypatch):
    candidates = [
        _raw("A", "https://a.com", snip="asyncio event loop"),
        _raw("B", "https://b.com", snip="transformer attention"),
        _raw("C", "https://c.com", snip="cooking recipes"),
    ]

    async def fake_source(url, timeout=10):
        return ("Asyncio Guide", "this page is about asyncio event loops in python")
    monkeypatch.setattr(search_mod, "fetch_source_for_similar", fake_source)

    async def fake_multi(query, max_results, *, engines, site, exclude_sites,
                          region, freshness, page=0, server=None):
        return candidates, [EngineReport("duckduckgo", ok=True)]
    monkeypatch.setattr(search_mod, "multi_search", fake_multi)

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
    async def fake_source(url, timeout=10):
        return "", ""
    monkeypatch.setattr(search_mod, "fetch_source_for_similar", fake_source)
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    resp = asyncio.run(_ss(srv, "https://src.com/x", mode="find_similar", cache_ttl=0))
    assert resp.results == []
    assert "could not fetch" in resp.error


def test_find_similar_falls_back_to_merge_when_no_reranker(monkeypatch):
    candidates = [
        _raw("python asyncio", "https://a.com", snip="python asyncio event loop"),
        _raw("cooking", "https://b.com", snip="food recipes"),
    ]

    async def fake_source(url, timeout=10):
        return ("Python Asyncio", "python asyncio event loop content")
    monkeypatch.setattr(search_mod, "fetch_source_for_similar", fake_source)

    async def fake_multi(query, max_results, *, engines, site, exclude_sites,
                          region, freshness, page=0, server=None):
        return candidates, [EngineReport("duckduckgo", ok=True)]
    monkeypatch.setattr(search_mod, "multi_search", fake_multi)
    monkeypatch.setattr(search_mod, "get_reranker", lambda: None)
    monkeypatch.setattr(search_mod, "unavailable_reason", lambda: "needs hound-mcp[all]")
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()
    resp = asyncio.run(_ss(srv, "x", mode="find_similar",
                           url="https://src.com/x", cache_ttl=0))
    assert resp.rerank_mode == "find_similar"
    # merge order (consensus + position): a.com is first in the candidates.
    assert resp.results[0].url == "https://a.com"
    assert "consensus + position" in resp.fetch_hint  # the fallback note


# ─── reranker prewarm (no-download-when-absent) ──────────────────────────────

def test_model_present_false_when_model_absent(tmp_path, monkeypatch):
    import master_fetch.reranker as rer
    monkeypatch.setattr(rer, "MODEL_DIR", tmp_path)
    assert rer.model_present() is False


def test_prewarm_reranker_skips_when_model_absent(monkeypatch):
    import master_fetch.reranker as rer
    # Force "model not cached" so prewarm (download=False) must NOT load. If it
    # did call the loader, the bomb raises.
    monkeypatch.setattr(rer, "_reranker", None)
    monkeypatch.setattr(rer, "_reranker_tried", False)
    monkeypatch.setattr(rer, "_reranker_lock", None)
    monkeypatch.setattr(rer, "model_present", lambda: False)
    called = {"yes": False}
    def bomb():
        called["yes"] = True
        raise AssertionError("prewarm must not load the reranker when the model is absent")
    monkeypatch.setattr(rer, "_load_reranker", bomb)
    asyncio.run(rer.prewarm_reranker())   # must not raise
    assert called["yes"] is False


def test_prewarm_reranker_warms_when_model_present(monkeypatch):
    import master_fetch.reranker as rer
    # prewarm -> ensure_reranker(download=False) -> _load_reranker when present.
    monkeypatch.setattr(rer, "_reranker", None)
    monkeypatch.setattr(rer, "_reranker_tried", False)
    monkeypatch.setattr(rer, "_reranker_lock", None)
    monkeypatch.setattr(rer, "model_present", lambda: True)
    called = {"yes": False}
    def fake_load():
        called["yes"] = True
        return None
    monkeypatch.setattr(rer, "_load_reranker", fake_load)
    asyncio.run(rer.prewarm_reranker())
    assert called["yes"] is True


def test_get_reranker_is_peek_only_and_never_loads(monkeypatch):
    """get_reranker() must NOT trigger a load (that's ensure_reranker's job).
    It just returns the cached singleton or None."""
    import master_fetch.reranker as rer
    monkeypatch.setattr(rer, "_reranker", None)
    monkeypatch.setattr(rer, "_reranker_tried", False)
    bomb = lambda *a, **k: (_ for _ in ()).throw(AssertionError("get_reranker must not load"))
    monkeypatch.setattr(rer, "_load_reranker", bomb)
    assert rer.get_reranker() is None  # peek returns None, no load triggered


def test_ensure_reranker_concurrent_callers_share_one_load(monkeypatch):
    """The race fix: concurrent ensure_reranker calls (prewarm + first search)
    must share ONE load via the lock, not double-load or fall back to None."""
    import master_fetch.reranker as rer
    monkeypatch.setattr(rer, "_reranker", None)
    monkeypatch.setattr(rer, "_reranker_tried", False)
    monkeypatch.setattr(rer, "_reranker_lock", None)
    monkeypatch.setattr(rer, "model_present", lambda: True)
    loads = {"n": 0}
    sentinel = object()
    def fake_load():
        loads["n"] += 1
        rer._reranker = sentinel   # mirror real _load_reranker: set the singleton
        rer._reranker_tried = True
        return sentinel
    monkeypatch.setattr(rer, "_load_reranker", fake_load)
    import asyncio
    async def main():
        return await asyncio.gather(rer.ensure_reranker(), rer.ensure_reranker(), rer.ensure_reranker())
    results = asyncio.run(main())
    assert loads["n"] == 1, f"concurrent callers must share ONE load, got {loads['n']}"
    assert all(r is sentinel for r in results), "all callers must get the same loaded instance"


def test_ensure_reranker_does_not_retry_after_failure(monkeypatch):
    """Once a load finished and failed (_reranker_tried=True, _reranker=None),
    subsequent ensure_reranker calls return None without re-loading."""
    import master_fetch.reranker as rer
    monkeypatch.setattr(rer, "_reranker", None)
    monkeypatch.setattr(rer, "_reranker_tried", True)
    monkeypatch.setattr(rer, "_reranker_lock", None)
    bomb = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not retry after a finished failure"))
    monkeypatch.setattr(rer, "_load_reranker", bomb)
    import asyncio
    assert asyncio.run(rer.ensure_reranker()) is None
