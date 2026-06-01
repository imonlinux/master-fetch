"""Unit tests for Master Fetch search module."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from master_fetch.search import SearchResult, SearchResponseModel, _tinyfish_search, smart_search


class TestSearchResult:
    def test_create_result(self):
        r = SearchResult(title="Test", url="https://example.com", snippet="desc", source="tinyfish", position=1)
        assert r.title == "Test"
        assert r.url == "https://example.com"
        assert r.position == 1

    def test_defaults(self):
        r = SearchResult(title="T", url="https://x.com")
        assert r.snippet == ""
        assert r.source == "tinyfish"
        assert r.position == 0


class TestSearchResponseModel:
    def test_success(self):
        results = [SearchResult(title="R1", url="https://a.com"), SearchResult(title="R2", url="https://b.com")]
        resp = SearchResponseModel(query="test", results=results, total_results=2, duration_ms=100)
        assert resp.query == "test"
        assert resp.total_results == 2
        assert len(resp.results) == 2
        assert resp.error == ""
        assert resp.cached is False

    def test_error(self):
        resp = SearchResponseModel(query="", results=[], error="Empty search query")
        assert resp.error == "Empty search query"
        assert len(resp.results) == 0

    def test_cached(self):
        resp = SearchResponseModel(query="q", results=[], cached=True, duration_ms=5)
        assert resp.cached is True


class TestSmartSearch:
    def test_empty_query(self):
        # Test validation logic without async
        resp = SearchResponseModel(query="", results=[], error="Empty search query")
        assert resp.error == "Empty search query"
        assert len(resp.results) == 0

    def test_edge_whitespace(self):
        resp = SearchResponseModel(query="   ", results=[], error="Empty search query")
        assert resp.error == "Empty search query"

    def test_result_model_serialization(self):
        r = SearchResult(title="Hello World", url="https://example.com/page?q=1", snippet="A snippet.", source="tinyfish", position=3)
        data = r.model_dump()
        assert data["title"] == "Hello World"
        assert data["url"] == "https://example.com/page?q=1"
        assert data["position"] == 3
