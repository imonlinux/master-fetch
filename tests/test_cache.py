"""Tests for cache.py — SQLite caching with TTL."""

import pytest
import tempfile
from pathlib import Path
from master_fetch.cache import (
    _cache_key,
    get_cached,
    set_cached,
    clear_cache,
    clear_all_cache,
)


class TestCacheKey:
    """Deterministic cache key generation."""

    def test_same_params_same_key(self):
        k1 = _cache_key("https://example.com", "markdown", None)
        k2 = _cache_key("https://example.com", "markdown", None)
        assert k1 == k2

    def test_different_urls_different_key(self):
        k1 = _cache_key("https://example.com/a", "markdown", None)
        k2 = _cache_key("https://example.com/b", "markdown", None)
        assert k1 != k2

    def test_different_extraction_different_key(self):
        k1 = _cache_key("https://example.com", "markdown", None)
        k2 = _cache_key("https://example.com", "text", None)
        assert k1 != k2

    def test_key_is_string(self):
        key = _cache_key("https://example.com", "article", "div.content")
        assert isinstance(key, str)
        assert len(key) == 24  # SHA256 hex digest truncated


class TestCacheOperations:
    """Integration tests for cache operations using temp dirs."""

    @pytest.fixture
    def cache_dir(self):
        with tempfile.TemporaryDirectory() as d:
            yield Path(d)

    @pytest.mark.asyncio
    async def test_set_and_get(self, cache_dir):
        await set_cached(
            "https://example.com", "markdown", ["Hello World"], 200,
            cache_dir=cache_dir,
        )
        cached = await get_cached(
            "https://example.com", "markdown", cache_dir=cache_dir,
        )
        assert cached is not None
        assert cached["status"] == 200
        assert cached["content"] == ["Hello World"]
        assert cached["url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_miss_returns_none(self, cache_dir):
        cached = await get_cached(
            "https://never-cached.com", "markdown", cache_dir=cache_dir,
        )
        assert cached is None

    @pytest.mark.asyncio
    async def test_expired_cache_returns_none(self, cache_dir):
        # Set with negative TTL (already expired)
        await set_cached(
            "https://expired.com", "markdown", ["Expired"], 200,
            ttl=-1, cache_dir=cache_dir,
        )
        # Query with ttl=0 (no additional buffer)
        cached = await get_cached(
            "https://expired.com", "markdown", ttl=0, cache_dir=cache_dir,
        )
        # With TTL -1, fetched_at + (-1) will be < time.time(), so None
        assert cached is None

    @pytest.mark.asyncio
    async def test_update_existing(self, cache_dir):
        await set_cached(
            "https://example.com", "markdown", ["Old"], 200,
            cache_dir=cache_dir,
        )
        await set_cached(
            "https://example.com", "markdown", ["New"], 201,
            cache_dir=cache_dir,
        )
        cached = await get_cached(
            "https://example.com", "markdown", cache_dir=cache_dir,
        )
        assert cached["status"] == 201
        assert cached["content"] == ["New"]

    @pytest.mark.asyncio
    async def test_different_extraction_types_separate(self, cache_dir):
        await set_cached(
            "https://example.com", "markdown", ["MD"], 200, cache_dir=cache_dir,
        )
        await set_cached(
            "https://example.com", "text", ["TEXT"], 200, cache_dir=cache_dir,
        )
        md = await get_cached("https://example.com", "markdown", cache_dir=cache_dir)
        txt = await get_cached("https://example.com", "text", cache_dir=cache_dir)
        assert md["content"] == ["MD"]
        assert txt["content"] == ["TEXT"]

    @pytest.mark.asyncio
    async def test_clear_expired(self, cache_dir):
        await set_cached(
            "https://expired.com", "markdown", ["Expired"], 200,
            ttl=-1, cache_dir=cache_dir,
        )
        await set_cached(
            "https://fresh.com", "markdown", ["Fresh"], 200,
            cache_dir=cache_dir,
        )
        count = await clear_cache(cache_dir)
        assert count >= 1  # Expired should be cleared

    @pytest.mark.asyncio
    async def test_clear_all(self, cache_dir):
        await set_cached("https://a.com", "markdown", ["A"], 200, cache_dir=cache_dir)
        await set_cached("https://b.com", "markdown", ["B"], 200, cache_dir=cache_dir)
        count = await clear_all_cache(cache_dir)
        assert count >= 2

        a = await get_cached("https://a.com", "markdown", cache_dir=cache_dir)
        b = await get_cached("https://b.com", "markdown", cache_dir=cache_dir)
        assert a is None
        assert b is None
