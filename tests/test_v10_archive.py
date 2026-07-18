"""v10 archive fallback tests: worthiness, the id_ marker transform, the
availability retry, snapshot validation, honest marking, archive cache, and the
_finalize_result integration (fires on hard-fail, not on success, opt-out works).
"""
import asyncio
import contextvars

import pytest

from master_fetch.archive import (
    _insert_id_marker, _timestamp_to_iso, try_archive_fallback,
)
from master_fetch.server import (
    ResponseModel, _is_archive_worthy, _ARCHIVE_FALLBACK,
)


# ─── pure helpers ─────────────────────────────────────────────────

def test_insert_id_marker():
    url = "http://web.archive.org/web/20240101064348/http://example.com/page"
    assert _insert_id_marker(url) == "http://web.archive.org/web/20240101064348id_/http://example.com/page"


def test_insert_id_marker_https():
    url = "https://web.archive.org/web/20231231235959/https://site.org/"
    assert _insert_id_marker(url) == "https://web.archive.org/web/20231231235959id_/https://site.org/"


def test_insert_id_marker_no_timestamp_passthrough():
    # If the timestamp can't be found, return unchanged (best-effort).
    url = "https://web.archive.org/web/nope/http://example.com/"
    assert _insert_id_marker(url) == url


def test_timestamp_to_iso():
    assert _timestamp_to_iso("20240101064348") == "2024-01-01"
    assert _timestamp_to_iso("20231231235959") == "2023-12-31"
    assert _timestamp_to_iso("bad") == ""
    assert _timestamp_to_iso("") == ""


# ─── _is_archive_worthy ───────────────────────────────────────────

def test_archive_worthy_hard_fails():
    u = "https://example.com/x"
    assert _is_archive_worthy(ResponseModel(status=404, content=["x"], url=u)) is True
    assert _is_archive_worthy(ResponseModel(status=451, content=["x"], url=u)) is True
    assert _is_archive_worthy(ResponseModel(status=500, content=["x"], url=u)) is True
    assert _is_archive_worthy(ResponseModel(status=503, content=["x"], url=u)) is True
    assert _is_archive_worthy(ResponseModel(status=0, content=["x"], url=u, error="network")) is True
    assert _is_archive_worthy(ResponseModel(status=403, content=["x"], url=u, error="bot_challenge_detected: x")) is True
    assert _is_archive_worthy(ResponseModel(status=503, content=["x"], url=u, error="bot_challenge_detected: x")) is True
    assert _is_archive_worthy(ResponseModel(status=403, content=["x"], url=u, error="all_tiers_failed: HTTP status 403")) is True
    assert _is_archive_worthy(ResponseModel(status=200, content=["x"], url=u, error="auth_required: login page")) is True


def test_archive_not_worthy_success_and_soft():
    u = "https://example.com/x"
    assert _is_archive_worthy(ResponseModel(status=200, content=["real content"], url=u)) is False
    assert _is_archive_worthy(ResponseModel(status=301, content=["x"], url=u)) is False
    assert _is_archive_worthy(ResponseModel(status=400, content=["x"], url=u)) is False  # bad request — archive can't fix
    assert _is_archive_worthy(ResponseModel(status=403, content=["x"], url=u, error="")) is False  # bare 403, no bot_challenge
    assert _is_archive_worthy(ResponseModel(status=200, content=["x"], url=u, error="encrypted_pdf: x")) is False


def test_archive_not_worthy_already_archive():
    # An archive result must never recurse into archive fallback.
    assert _is_archive_worthy(ResponseModel(status=404, content=["x"], url="https://example.com/x", source="archive.org")) is False


# ─── try_archive_fallback ─────────────────────────────────────────

class FakeServer:
    """Stand-in for MasterFetchServer: records get() calls, returns a preset result."""
    def __init__(self, get_result):
        self._get_result = get_result
        self.get_calls = []

    async def get(self, url, **kwargs):
        self.get_calls.append(url)
        if isinstance(self._get_result, Exception):
            raise self._get_result
        return self._get_result


async def _avail_ok(url):
    return {
        "archived_snapshots": {"closest": {
            "available": True,
            "url": "http://web.archive.org/web/20240101064348/http://example.com/page",
            "timestamp": "20240101064348", "status": "200",
        }}
    }


async def _avail_no_snapshot(url):
    return {"archived_snapshots": {}}


async def _avail_archived_404(url):
    return {"archived_snapshots": {"closest": {
        "available": True, "url": "http://web.archive.org/web/20240101064348/http://example.com/page",
        "timestamp": "20240101064348", "status": "404",
    }}}


def _valid_snapshot_result():
    return ResponseModel(
        status=200, content=["archived page content here"], url="http://web.archive.org/web/20240101064348id_/http://example.com/page",
        fetcher_used="http", content_type="text/html", error="",
    )


@pytest.mark.asyncio
async def test_archive_fallback_success():
    srv = FakeServer(_valid_snapshot_result())
    r = await try_archive_fallback(
        srv, "https://example.com/page", "markdown", None,
        cache_ttl=0, pages=None, max_chars=40000, client_get=_avail_ok,
    )
    assert r is not None
    assert r.source == "archive.org"
    assert r.archived_at == "2024-01-01"
    assert r.url == "https://example.com/page"  # original, not the archive URL
    assert r.fetcher_used == "archive"
    assert "→archive" in r.escalation_path
    # The snapshot was fetched via the id_ marker:
    assert srv.get_calls and "id_" in srv.get_calls[0]


@pytest.mark.asyncio
async def test_archive_fallback_no_snapshot():
    srv = FakeServer(_valid_snapshot_result())
    r = await try_archive_fallback(
        srv, "https://example.com/page", "markdown", None,
        cache_ttl=0, pages=None, max_chars=40000, client_get=_avail_no_snapshot,
    )
    assert r is None
    assert srv.get_calls == []  # never fetched the snapshot


@pytest.mark.asyncio
async def test_archive_fallback_rejects_archived_error_page():
    # A snapshot that archived a 404 must NOT be served (archive would re-serve the failure).
    srv = FakeServer(_valid_snapshot_result())
    r = await try_archive_fallback(
        srv, "https://example.com/page", "markdown", None,
        cache_ttl=0, pages=None, max_chars=40000, client_get=_avail_archived_404,
    )
    assert r is None
    assert srv.get_calls == []


@pytest.mark.asyncio
async def test_archive_fallback_rejects_empty_snapshot():
    srv = FakeServer(ResponseModel(status=200, content=[""], url="x", content_type="text/html"))
    r = await try_archive_fallback(
        srv, "https://example.com/page", "markdown", None,
        cache_ttl=0, pages=None, max_chars=40000, client_get=_avail_ok,
    )
    assert r is None


@pytest.mark.asyncio
async def test_archive_fallback_rejects_error_snapshot():
    srv = FakeServer(ResponseModel(status=500, content=["x"], url="x", error="server error"))
    r = await try_archive_fallback(
        srv, "https://example.com/page", "markdown", None,
        cache_ttl=0, pages=None, max_chars=40000, client_get=_avail_ok,
    )
    assert r is None


@pytest.mark.asyncio
async def test_archive_fallback_snapshot_fetch_raises():
    # server.get raising (timeout/network) must not propagate — fall through to None.
    srv = FakeServer(asyncio.TimeoutError())
    r = await try_archive_fallback(
        srv, "https://example.com/page", "markdown", None,
        cache_ttl=0, pages=None, max_chars=40000, client_get=_avail_ok,
    )
    assert r is None


@pytest.mark.asyncio
async def test_archive_fallback_availability_retry_then_success(tmp_path, monkeypatch):
    """The availability API throws transient errors ~1-in-10; retry must recover."""
    # Speed up the backoff so the test is fast.
    import master_fetch.archive as arch
    monkeypatch.setattr(arch, "_AVAIL_BACKOFF_S", (0.0, 0.0))

    calls = {"n": 0}

    async def flaky(url):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient availability error")
        return await _avail_ok(url)

    srv = FakeServer(_valid_snapshot_result())
    r = await try_archive_fallback(
        srv, "https://example.com/page", "markdown", None,
        cache_ttl=0, pages=None, max_chars=40000, client_get=flaky,
    )
    assert r is not None
    assert r.source == "archive.org"
    assert calls["n"] == 2  # failed once, succeeded on retry


@pytest.mark.asyncio
async def test_archive_fallback_availability_persistent_failure(monkeypatch):
    import master_fetch.archive as arch
    monkeypatch.setattr(arch, "_AVAIL_BACKOFF_S", (0.0, 0.0))

    async def always_fails(url):
        raise RuntimeError("archive.org down")

    srv = FakeServer(_valid_snapshot_result())
    r = await try_archive_fallback(
        srv, "https://example.com/page", "markdown", None,
        cache_ttl=0, pages=None, max_chars=40000, client_get=always_fails,
    )
    assert r is None
    assert srv.get_calls == []  # never fetched the snapshot


@pytest.mark.asyncio
async def test_archive_cache_hit_returns_instant(tmp_path):
    """A repeat fetch of a blocked URL hits the archive cache (no network)."""
    from master_fetch.cache import set_cached, _CACHE_DIR
    # Use a temp cache dir for isolation.
    monkeypatch_dir = tmp_path
    # Pre-seed the archive cache.
    await set_cached(
        "https://example.com/page", "markdown", ["cached archive content"], 200,
        None, 3600, cache_dir=tmp_path, content_type="text/html",
        total_size_bytes=42, source="archive",
        envelope={"page_type": "article", "source": "archive.org", "archived_at": "2023-05-05",
                  "metadata": {"title": "Cached"}, "media": [], "links": {}, "quality_score": 0.0, "table_of_contents": []},
    )
    # Point the cache module at our temp dir for the get_cached call inside.
    import master_fetch.cache as cache_mod
    orig = cache_mod._CACHE_DIR
    cache_mod._CACHE_DIR = tmp_path
    try:
        srv = FakeServer(_valid_snapshot_result())  # should NOT be called (cache hit)
        r = await try_archive_fallback(
            srv, "https://example.com/page", "markdown", None,
            cache_ttl=3600, pages=None, max_chars=40000, client_get=_avail_ok,
        )
        assert r is not None
        assert r.source == "archive.org"
        assert r.archived_at == "2023-05-05"
        assert r.cached is True
        assert r.fetcher_used == "archive"
        assert srv.get_calls == []  # cache hit — no snapshot fetch, no availability call
    finally:
        cache_mod._CACHE_DIR = orig


# ─── _finalize_result integration ──────────────────────────────────

@pytest.mark.asyncio
async def test_finalize_result_archive_fires_on_hard_fail(monkeypatch):
    """A 404 result triggers archive fallback; the archive result is returned."""
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()

    archive_result = ResponseModel(
        status=200, content=["recovered from archive"], url="https://example.com/gone",
        fetcher_used="archive", content_type="text/html", source="archive.org",
        archived_at="2024-01-01", escalation_path="http→archive",
    )

    async def fake_fallback(server, url, *a, **kw):
        return archive_result

    monkeypatch.setattr("master_fetch.archive.try_archive_fallback", fake_fallback)
    monkeypatch.setattr("master_fetch.server._ARCHIVE_ENABLED", True)

    hard_fail = ResponseModel(
        status=404, content=["[404 Not Found]"], url="https://example.com/gone",
        fetcher_used="http", error="",
    )
    out = await srv._finalize_result(
        hard_fail, "https://example.com/gone", "markdown", None, cache_ttl=0, max_chars=40000,
    )
    assert out.source == "archive.org"
    assert out.archived_at == "2024-01-01"
    assert "recovered from archive" in out.content[0]


@pytest.mark.asyncio
async def test_finalize_result_no_archive_on_success(monkeypatch):
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()

    called = {"n": 0}

    async def fake_fallback(server, url, *a, **kw):
        called["n"] += 1
        return None

    monkeypatch.setattr("master_fetch.archive.try_archive_fallback", fake_fallback)
    monkeypatch.setattr("master_fetch.server._ARCHIVE_ENABLED", True)

    ok = ResponseModel(
        status=200, content=["real live content"], url="https://example.com/ok",
        fetcher_used="http", content_type="text/html",
    )
    out = await srv._finalize_result(
        ok, "https://example.com/ok", "markdown", None, cache_ttl=0, max_chars=40000,
    )
    assert called["n"] == 0  # archive never tried on success
    assert out.source == "live"


@pytest.mark.asyncio
async def test_finalize_result_archive_opt_out_per_call(monkeypatch):
    """archive_fallback=False (via contextvar) suppresses the fallback."""
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()

    called = {"n": 0}

    async def fake_fallback(server, url, *a, **kw):
        called["n"] += 1
        return None

    monkeypatch.setattr("master_fetch.archive.try_archive_fallback", fake_fallback)
    monkeypatch.setattr("master_fetch.server._ARCHIVE_ENABLED", True)

    token = _ARCHIVE_FALLBACK.set(False)
    try:
        hard_fail = ResponseModel(
            status=404, content=["[404]"], url="https://example.com/gone",
            fetcher_used="http",
        )
        out = await srv._finalize_result(
            hard_fail, "https://example.com/gone", "markdown", None, cache_ttl=0, max_chars=40000,
        )
        assert called["n"] == 0  # opt-out suppressed archive
        assert out.source == "live"
    finally:
        _ARCHIVE_FALLBACK.reset(token)


@pytest.mark.asyncio
async def test_finalize_result_archive_opt_out_env(monkeypatch):
    """HOUND_ARCHIVE_FALLBACK=0 (module flag False) suppresses the fallback."""
    from master_fetch.server import MasterFetchServer
    srv = MasterFetchServer()

    called = {"n": 0}

    async def fake_fallback(server, url, *a, **kw):
        called["n"] += 1
        return None

    monkeypatch.setattr("master_fetch.archive.try_archive_fallback", fake_fallback)
    monkeypatch.setattr("master_fetch.server._ARCHIVE_ENABLED", False)

    hard_fail = ResponseModel(
        status=404, content=["[404]"], url="https://example.com/gone", fetcher_used="http",
    )
    out = await srv._finalize_result(
        hard_fail, "https://example.com/gone", "markdown", None, cache_ttl=0, max_chars=40000,
    )
    assert called["n"] == 0
    assert out.source == "live"
