"""Tests for cache.py — SQLite caching with TTL."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from master_fetch.cache import (
    _cache_key,
    _ensure_db,
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

    @pytest.mark.asyncio
    async def test_set_without_new_kwargs_defaults_to_empty(self, cache_dir):
        """Backwards compat: callers using old set_cached signature still work."""
        await set_cached("https://legacy.example", "markdown", ["Legacy"], 200, cache_dir=cache_dir)
        cached = await get_cached("https://legacy.example", "markdown", cache_dir=cache_dir)
        assert cached is not None
        # New columns should be empty defaults, not crash.
        assert cached["content_type"] == ""
        assert cached["total_size_bytes"] == 0

    @pytest.mark.asyncio
    async def test_content_type_and_size_roundtrip(self, cache_dir):
        """Round-trip the v3.5.3 cache fields end-to-end."""
        await set_cached(
            "https://example.com/feed", "article", ["Body"], 200,
            cache_dir=cache_dir,
            content_type="application/json",
            total_size_bytes=4096,
        )
        cached = await get_cached("https://example.com/feed", "article", cache_dir=cache_dir)
        assert cached is not None
        assert cached["content_type"] == "application/json"
        assert cached["total_size_bytes"] == 4096

    @pytest.mark.asyncio
    async def test_update_replaces_new_fields(self, cache_dir):
        """INSERT OR REPLACE must overwrite content_type/total_size_bytes, not leak old values."""
        await set_cached(
            "https://example.com", "markdown", ["v1"], 200,
            cache_dir=cache_dir, content_type="text/html", total_size_bytes=1000,
        )
        await set_cached(
            "https://example.com", "markdown", ["v2"], 200,
            cache_dir=cache_dir, content_type="text/plain", total_size_bytes=42,
        )
        cached = await get_cached("https://example.com", "markdown", cache_dir=cache_dir)
        assert cached["content"] == ["v2"]
        assert cached["content_type"] == "text/plain"  # not text/html from first write
        assert cached["total_size_bytes"] == 42  # not 1000 from first write


class TestCacheSchemaMigration:
    """v3.5.3 schema upgrade — older DBs require ALTER TABLE on first access."""

    @pytest.fixture
    def cache_dir(self):
        with tempfile.TemporaryDirectory() as d:
            yield Path(d)

    @pytest.fixture
    def old_db_dir(self):
        """Temp dir pre-populated with a pre-3.5.3 (7-column) cache DB.

        Uses TemporaryDirectory(ignore_cleanup_errors=True) because aiosqlite's
        WAL-mode -wal/-shm sidecar files sometimes linger on Windows beyond
        the asyncio loop teardown. ignore_cleanup_errors shields the test
        from that — the test logic itself runs against the file, then we let
        the OS clean up.
        """
        import time as _time
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db_path = Path(d) / "cache.db"
            # Fetched_at = now() so the row stays non-expired post-migration.
            # Key = real cache_key hash, not a literal string.
            real_key = _cache_key("https://old.example", "markdown", None)
            with sqlite3.connect(db_path) as conn:
                conn.execute("""
                    CREATE TABLE cache (
                        key TEXT PRIMARY KEY,
                        url TEXT NOT NULL,
                        extraction_type TEXT NOT NULL,
                        content TEXT NOT NULL,
                        status INTEGER NOT NULL,
                        fetched_at REAL NOT NULL,
                        ttl INTEGER NOT NULL DEFAULT 3600
                    )
                """)
                conn.execute(
                    "INSERT INTO cache VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (real_key, "https://old.example", "markdown", '["Old"]', 200, _time.time(), 3600),
                )
                conn.commit()
            yield Path(d)

    @pytest.mark.asyncio
    async def test_old_schema_gets_new_columns_on_first_ensure(self, old_db_dir):
        """A real-world upgrade: existing pre-3.5.3 DB, _ensure_db ALTERs in the new cols."""
        import aiosqlite as _aio
        db_path = old_db_dir / "cache.db"

        # Pre-check via aiosqlite — the OLD DB has 7 columns, no content_type
        async with _aio.connect(db_path) as b:
            async with b.execute("PRAGMA table_info(cache)") as cur:
                cols_before = [r[1] for r in await cur.fetchall()]
        assert "content_type" not in cols_before
        assert "total_size_bytes" not in cols_before

        # Now _ensure_db runs the migration on this DB.
        result_path = await _ensure_db(old_db_dir)
        assert result_path == db_path

        # Re-check — new columns must now exist
        async with _aio.connect(db_path) as b:
            async with b.execute("PRAGMA table_info(cache)") as cur:
                cols_after = [r[1] for r in await cur.fetchall()]
        assert "content_type" in cols_after
        assert "total_size_bytes" in cols_after

        # Old row is still queryable via get_cached; new columns default to ''/0
        cached = await get_cached(
            "https://old.example", "markdown", cache_dir=old_db_dir,
        )
        assert cached is not None
        assert cached["status"] == 200
        assert cached["content"] == ["Old"]
        assert cached["content_type"] == ""  # ALTER DEFAULT applied to old row
        assert cached["total_size_bytes"] == 0  # ALTER DEFAULT applied to old row

    @pytest.mark.asyncio
    async def test_idempotent_migration_on_already_upgraded_db(self, cache_dir):
        """Re-running _ensure_db on a DB that already has v3.5.3 cols must not crash.
        Without try/except on ALTER, this raises 'duplicate column name'.
        """
        # First call creates fresh v3.5.3 schema
        db_path_1 = await _ensure_db(cache_dir)
        # Second call on same dir — must not raise "duplicate column"
        db_path_2 = await _ensure_db(cache_dir)
        assert db_path_1 == db_path_2
