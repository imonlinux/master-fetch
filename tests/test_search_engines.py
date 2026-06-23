"""Tests for the v7 hound-native keyless search engine layer.

No network: SERP parsers are fed fixture HTML, the Wikipedia JSON path is fed
canned JSON, and multi_search is run against stubbed engine funcs. Verifies
DDG uddg-redirect decoding, Bing <cite> breadcrumb reconstruction, Google
/url?q= decoding, cross-engine merge+dedup, site filters, BM25 ranking + the
zero-overlap order-preserving tiebreak, and the multi-engine orchestrator.
"""

import asyncio
import json

import pytest

from master_fetch import search_engines as se
from master_fetch.search_engines import (
    RawResult, EngineReport, _ddg_real_url, _bing_real_url, _parse_ddg,
    _parse_bing, _parse_google, merge_dedupe, bm25_rerank, multi_search,
)


# ─── DDG redirect decoding ──────────────────────────────────────────────────

def test_ddg_real_url_decodes_uddg():
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&rut=abc"
    assert _ddg_real_url(href) == "https://example.com/page"


def test_ddg_real_url_passthrough_non_redirect():
    assert _ddg_real_url("https://plain.com/x") == "https://plain.com/x"
    assert _ddg_real_url("") == ""


def test_parse_ddg_extracts_title_url_snippet():
    html = """
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Frealpython.com%2Fasync&rut=x">Async IO Walkthrough</a>
      <a class="result__snippet">Learn async await in Python.</a>
    </div>
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Fasyncio.html">asyncio docs</a>
      <a class="result__snippet">The asyncio library reference.</a>
    </div>
    """
    out = _parse_ddg(html)
    assert len(out) == 2
    assert out[0].title == "Async IO Walkthrough"
    assert out[0].url == "https://realpython.com/async"
    assert "async await" in out[0].snippet
    assert out[0].source == "duckduckgo"
    assert out[0].position == 1
    assert out[1].url == "https://docs.python.org/3/library/asyncio.html"


def test_parse_ddg_skips_blocks_without_link():
    html = '<div class="result"><a class="result__snippet">no link here</a></div>'
    assert _parse_ddg(html) == []


# ─── Bing <cite> reconstruction ─────────────────────────────────────────────

def test_bing_real_url_breadcrumbs():
    assert _bing_real_url("https://www.programiz.com \u203a python-programming \u203a online-compiler") == \
        "https://www.programiz.com/python-programming/online-compiler"


def test_bing_real_url_plain():
    assert _bing_real_url("https://www.python.org") == "https://www.python.org"


def test_bing_real_url_no_scheme_prepends_https():
    assert _bing_real_url("www.learnpython.org \u203a plots") == "https://www.learnpython.org/plots"


def test_bing_real_url_empty():
    assert _bing_real_url("") == ""
    assert _bing_real_url("some prose with spaces") == ""


def test_parse_bing_uses_cite_not_redirect_href():
    html = """
    <li class="b_algo">
      <h2><a href="https://www.bing.com/ck/a?!&&p=opaque">W3Schools Python</a></h2>
      <div class="b_caption"><p>Python tutorial for beginners.</p></div>
      <div class="b_attribution"><cite>https://www.w3schools.com \u203a python</cite></div>
    </li>
    """
    out = _parse_bing(html)
    assert len(out) == 1
    assert out[0].title == "W3Schools Python"
    # The opaque ck/a redirect must NOT leak into the URL; cite is used instead.
    assert out[0].url == "https://www.w3schools.com/python"
    assert "ck/a" not in out[0].url
    assert out[0].source == "bing"


def test_parse_bing_skips_when_no_cite():
    # A result whose real URL can't be recovered is dropped, not a junk redirect.
    html = '<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?x">No cite</a></h2></li>'
    assert _parse_bing(html) == []


# ─── Google /url?q= decoding ─────────────────────────────────────────────────

def test_parse_google_decodes_urlq_and_direct():
    html = """
    <div class="g">
      <div><a href="/url?q=https://numpy.org/doc/&sa=U&ved=123"><h3>NumPy docs</h3></a></div>
      <div class="VwiC3b">Numerical Python documentation.</div>
    </div>
    <div class="g">
      <a href="https://pandas.pydata.org/"><h3>pandas</h3></a>
    </div>
    """
    out = _parse_google(html)
    titles = {r.title for r in out}
    assert "NumPy docs" in titles and "pandas" in titles
    assert any(r.url == "https://numpy.org/doc/" for r in out)
    assert any(r.url == "https://pandas.pydata.org/" for r in out)
    assert all(r.source == "google" for r in out)


def test_parse_google_dedups_within_engine():
    html = """
    <div class="g"><a href="https://dup.com"><h3>Dup</h3></a></div>
    <div class="g"><a href="https://dup.com"><h3>Dup again</h3></a></div>
    """
    out = _parse_google(html)
    assert len(out) == 1


# ─── Wikipedia JSON ──────────────────────────────────────────────────────────

def test_search_wikipedia_parses_api_json(monkeypatch):
    payload = {"query": {"search": [
        {"title": "Coroutine", "snippet": "A <span class=\"searchmatch\">coroutine</span> is a program component."},
        {"title": "Async/await", "snippet": "Async <i>await</i> syntax."},
    ]}}

    async def fake_get(url, *, method="GET", form=None, timeout=12):
        return (json.dumps(payload), 200, False)

    monkeypatch.setattr(se, "_impersonated_get", fake_get)
    out, rep = asyncio.run(se.search_wikipedia("coroutine", 5, region="us-en"))
    assert rep.ok and not rep.blocked
    assert out[0].title == "Coroutine"
    assert out[0].url == "https://en.wikipedia.org/wiki/Coroutine"
    # HTML in the snippet is stripped to plain text.
    assert "<span" not in out[0].snippet and "coroutine" in out[0].snippet
    assert out[1].url == "https://en.wikipedia.org/wiki/Async/await"


def test_search_wikipedia_uses_language_suffix_of_region(monkeypatch):
    captured = {}
    payload = {"query": {"search": [{"title": "Python", "snippet": "lang"}]}}
    async def fake_get(url, **kw):
        captured["url"] = url
        return (json.dumps(payload), 200, False)
    monkeypatch.setattr(se, "_impersonated_get", fake_get)
    # region "fr-fr" -> Wikipedia host language = "fr" (last segment), NOT "fr-fr" or the country.
    asyncio.run(se.search_wikipedia("python", 3, region="fr-fr"))
    assert "fr.wikipedia.org" in captured["url"]


# ─── merge + dedup + site filters ────────────────────────────────────────────

def _rr(title, url, src="x", pos=1, snip=""):
    return RawResult(title=title, url=url, snippet=snip, source=src, position=pos)


def test_merge_dedups_normalized_trailing_slash():
    per = [
        ([_rr("A", "https://docs.python.org/3/", "duckduckgo", 1, "s1")], EngineReport("duckduckgo", ok=True)),
        ([_rr("A", "https://docs.python.org/3", "bing", 1, "s2")], EngineReport("bing", ok=True)),
    ]
    merged = merge_dedupe(per, 10)
    assert len(merged) == 1  # /3 and /3/ are the same normalized URL


def test_merge_keeps_snippet_when_deduping():
    per = [
        ([_rr("A", "https://x.com", "duckduckgo", 1, "")], EngineReport("duckduckgo", ok=True)),
        ([_rr("A", "https://x.com", "bing", 1, "good snippet")], EngineReport("bing", ok=True)),
    ]
    merged = merge_dedupe(per, 10)
    assert merged[0].snippet == "good snippet"


def test_merge_site_filter_keeps_only_matching():
    per = [([_rr("A", "https://docs.python.org/x", pos=1),
             _rr("B", "https://numpy.org/y", pos=2)], EngineReport("duckduckgo", ok=True))]
    merged = merge_dedupe(per, 10, site="python.org")
    assert len(merged) == 1 and "python.org" in merged[0].url


def test_merge_exclude_sites_drops_matching():
    per = [([_rr("A", "https://pinterest.com/x", pos=1),
             _rr("B", "https://python.org/y", pos=2)], EngineReport("duckduckgo", ok=True))]
    merged = merge_dedupe(per, 10, exclude_sites=["pinterest.com"])
    assert len(merged) == 1 and "python.org" in merged[0].url


# ─── BM25 rerank ─────────────────────────────────────────────────────────────

def test_bm25_ranks_relevant_doc_first():
    results = [
        _rr("unrelated thing", "https://a.com", snip="cooking recipes food"),
        _rr("python asyncio guide", "https://b.com", snip="asyncio event loop python"),
    ]
    ranked = bm25_rerank("python asyncio", results)
    assert ranked[0][0].url == "https://b.com"
    assert ranked[0][1] == 1.0  # top score normalized to 1.0
    assert ranked[-1][1] < ranked[0][1]


def test_bm25_zero_overlap_preserves_order():
    # No term overlap -> scores all 0 -> must NOT randomly shuffle; preserve
    # the original merge order via the position tiebreak.
    results = [
        _rr("one", "https://1.com", "duckduckgo", 1, "aaa"),
        _rr("two", "https://2.com", "bing", 1, "bbb"),
        _rr("three", "https://3.com", "wikipedia", 1, "ccc"),
    ]
    ranked = bm25_rerank("zzzqqq", results)
    assert [r.url for r, _ in ranked] == ["https://1.com", "https://2.com", "https://3.com"]
    assert all(s == 0.0 for _, s in ranked)


def test_bm25_empty_query_preserves_order():
    results = [_rr("a", "https://a.com", pos=1), _rr("b", "https://b.com", pos=2)]
    ranked = bm25_rerank("", results)
    assert [r.url for r, _ in ranked] == ["https://a.com", "https://b.com"]


# ─── multi_search orchestrator ───────────────────────────────────────────────

def test_multi_search_runs_engines_in_parallel_and_reports(monkeypatch):
    async def fake_ddg(q, n, *, region, freshness, server):
        return ([_rr("DDG", "https://ddg.com", "duckduckgo", 1, "s")], EngineReport("duckduckgo", ok=True))
    async def fake_bing(q, n, *, region, freshness, server):
        return ([], EngineReport("bing", blocked=True, error="captcha"))
    async def fake_wiki(q, n, *, region, freshness, server):
        return ([_rr("Wiki", "https://en.wikipedia.org/wiki/X", "wikipedia", 1, "s")], EngineReport("wikipedia", ok=True))

    monkeypatch.setitem(se._ENGINES, "duckduckgo", fake_ddg)
    monkeypatch.setitem(se._ENGINES, "bing", fake_bing)
    monkeypatch.setitem(se._ENGINES, "wikipedia", fake_wiki)

    ranked, reports = asyncio.run(multi_search("x", 5, server=None))
    names_ok = {r.name: r.ok for r in reports}
    assert names_ok == {"duckduckgo": True, "bing": False, "wikipedia": True}
    # Bing is blocked; the other two still contribute.
    assert {r.name for r in reports if r.blocked} == {"bing"}
    urls = {r.url for r in ranked}
    assert "https://ddg.com" in urls and "https://en.wikipedia.org/wiki/X" in urls


def test_multi_search_unknown_engine_ignored(monkeypatch):
    # No engines patched at all is fine because we pass explicit names that are
    # all unknown -> falls back to the default engine set. Instead test that an
    # explicit unknown name is dropped silently.
    async def fake_ddg(q, n, *, region, freshness, server):
        return ([_rr("D", "https://d.com", "duckduckgo", 1)], EngineReport("duckduckgo", ok=True))
    # Replace _ENGINES with only duckduckgo so 'bing'/'wikipedia' are unknown.
    monkeypatch.setattr(se, "_ENGINES", {"duckduckgo": fake_ddg})
    ranked, reports = asyncio.run(multi_search("x", 5, engines=["duckduckgo", "nonexistent"], server=None))
    assert [r.name for r in reports] == ["duckduckgo"]


def test_multi_search_engine_exception_is_caught(monkeypatch):
    async def boom(q, n, *, region, freshness, server):
        raise RuntimeError("engine exploded")
    monkeypatch.setattr(se, "_ENGINES", {"duckduckgo": boom, "bing": boom, "wikipedia": boom})
    ranked, reports = asyncio.run(multi_search("x", 5, server=None))
    assert ranked == []
    # Every engine reported an error (not ok, not blocked) instead of crashing the call.
    assert all(not r.ok and not r.blocked and r.error for r in reports)
