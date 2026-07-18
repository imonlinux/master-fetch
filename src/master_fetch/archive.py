"""v10 Internet Archive (Wayback Machine) fallback for hard-blocked fetches.

When smart_fetch's live tiers hard-fail (404 / 451 / 500 / network error /
bot-block-after-escalation / auth-required), this module recovers the page
from the Internet Archive's closest snapshot — honestly marked
(``source='archive.org'``, ``archived_at``) so the agent always knows it got a
dated snapshot, not the live page.

Reliability-first design (the agent must never get bloat or dumb info from the
archive):

- Trigger ONLY on a genuine hard-fail (see ``_is_archive_worthy`` in server.py).
  Never on success, soft errors, or cases archive can't fix.
- Require the snapshot's own ``status == 200`` — reject snapshots that
  archived a 404/error page (archive would just re-serve the failure).
- Fetch the snapshot via the Wayback ``id_`` identity marker, which serves the
  RAW archived HTML with NO toolbar/wrapper and ORIGINAL links intact. The
  agent gets clean content, not Wayback chrome or rewritten ``/web/<ts>/``
  link prefixes. No manual HTML stripping needed.
- Validate the snapshot actually yields real, usable content (status<400, no
  error, non-empty, not a JS shell). If it doesn't, return None and fall
  through to the original error — never worse than today.
- Retry the flaky availability API (transient ~1-in-10 errors, confirmed 2026)
  with backoff. Cap the whole fallback at ~12s so a dead-end doesn't linger.
- Cache archive results under a separate key (``source='archive'``) so a
  repeat fetch of a blocked URL is instant and a later live-unblock isn't
  served a stale archive snapshot.

Never raises. A failure here always falls through to the original error in the
caller.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional, Callable, Awaitable, TYPE_CHECKING
from urllib.parse import quote as _urlquote

if TYPE_CHECKING:
    from master_fetch.server import MasterFetchServer, ResponseModel

logger = logging.getLogger("master-fetch.archive")

_AVAILABILITY_URL = "https://archive.org/wayback/available?url="
# Snapshot fetch + overall cap. The live fetch already failed; archive must
# not add unbounded latency to a dead-end.
_SNAPSHOT_TIMEOUT_S = 10
_OVERALL_CAP_S = 12
# Availability API retry (transient ~1-in-10 errors). Backoff in seconds.
_AVAIL_RETRIES = 3
_AVAIL_BACKOFF_S = (1.5, 3.0)

# /web/<14-digit-timestamp>/  ->  we insert 'id_' right after the timestamp.
_TS_RE = re.compile(r"/web/(\d{14})/")


def _timestamp_to_iso(ts: str) -> str:
    """YYYYMMDDhhmmss -> ISO-8601 date (UTC). Best-effort; '' on failure."""
    try:
        return datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).date().isoformat()
    except (ValueError, TypeError):
        return ""


def _insert_id_marker(snapshot_url: str) -> str:
    """Transform a Wayback snapshot URL to the ``id_`` (identity) form.

    ``id_`` tells the Wayback Machine to serve the raw archived response with no
    toolbar/wrapper injected and the original links intact (not rewritten to
    ``/web/<ts>/...``). This is what keeps archived content clean for the agent.

    ``http://web.archive.org/web/20240101064348/http://example.com/`` ->
    ``http://web.archive.org/web/20240101064348id_/http://example.com/``
    """
    m = _TS_RE.search(snapshot_url)
    if not m:
        return snapshot_url  # no timestamp found; try the URL as-is
    # Insert 'id_' right BEFORE the trailing slash of /web/<ts>/ so the form is
    # /web/<ts>id_/<original-url> (the documented Wayback identity marker).
    slash_pos = m.end() - 1
    return snapshot_url[:slash_pos] + "id_" + snapshot_url[slash_pos:]


async def _http_get_json(url: str) -> dict:
    """GET a JSON endpoint. Lazy-imports httpx (already a runtime dep) so the
    archive module adds zero startup cost. Raises on non-2xx / network error."""
    import httpx
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": "hound-mcp/10.0 (archive fallback)"})
        r.raise_for_status()
        return r.json()


async def _query_availability(
    url: str, client_get: Callable[[str], Awaitable[dict]],
) -> Optional[dict]:
    """Call the Wayback availability API with retry. Returns the parsed JSON
    dict or None on persistent failure.

    ``client_get`` is injected so tests can mock the network without httpx.
    """
    api = _AVAILABILITY_URL + _urlquote(url, safe="")
    last_err: Optional[BaseException] = None
    for attempt in range(_AVAIL_RETRIES):
        try:
            data = await client_get(api)
            if isinstance(data, dict):
                return data
        except Exception as e:
            last_err = e
        if attempt < _AVAIL_RETRIES - 1:
            await asyncio.sleep(_AVAIL_BACKOFF_S[attempt])
    if last_err is not None:
        logger.debug("wayback availability failed for %s: %s", url, last_err)
    return None


async def try_archive_fallback(
    server: "MasterFetchServer",
    url: str,
    extraction_type: str,
    css_selector: Optional[str],
    cache_ttl: int,
    pages: Optional[str],
    max_chars: int,
    client_get: Optional[Callable[[str], Awaitable[dict]]] = None,
) -> "Optional[ResponseModel]":
    """Recover a hard-blocked URL from the Internet Archive.

    Returns a ResponseModel with ``source='archive.org'`` and ``archived_at``
    set, or None if no usable snapshot exists. Never raises.

    ``client_get`` (optional) overrides the availability-API HTTP client for
    tests; the snapshot fetch always goes through the real ``server.get``.
    """
    from master_fetch.cache import get_cached
    from master_fetch.server import ResponseModel, _is_js_shell

    # 1. Archive cache: a repeat fetch of a blocked URL should be instant.
    if cache_ttl > 0:
        cached = await get_cached(
            url, extraction_type, css_selector, ttl=cache_ttl, pages=pages, source="archive",
        )
        if cached is not None:
            env = cached.get("envelope") or {}
            return ResponseModel(
                url=cached["url"], status=cached["status"], content=cached["content"],
                cached=True, fetcher_used="archive", duration_ms=0,
                extracted_type=extraction_type,
                content_type=cached.get("content_type", ""),
                total_size_bytes=cached.get("total_size_bytes", 0),
                metadata=env.get("metadata", {}) or {},
                media=env.get("media", []) or [],
                links=env.get("links", {}) or {},
                quality_score=env.get("quality_score", 0.0) or 0.0,
                table_of_contents=env.get("table_of_contents", []) or [],
                page_type=env.get("page_type", "unknown") or "unknown",
                source="archive.org",
                archived_at=env.get("archived_at", "") or "",
            )

    # 2. Availability API (with retry) to find the closest snapshot.
    getter = client_get or _http_get_json
    availability = await _query_availability(url, getter)
    if not availability:
        return None
    closest = (availability.get("archived_snapshots") or {}).get("closest") or {}
    if not closest.get("available"):
        return None
    # Reject snapshots that archived an error page — only trust status 200.
    if str(closest.get("status", "")) != "200":
        return None
    snapshot_url = closest.get("url") or ""
    ts = closest.get("timestamp") or ""
    if not snapshot_url:
        return None
    archived_at = _timestamp_to_iso(ts)

    # 3. Fetch the snapshot via the id_ marker (raw archived HTML, no toolbar).
    raw_snapshot = _insert_id_marker(snapshot_url)
    try:
        result = await asyncio.wait_for(
            server.get(
                raw_snapshot, extraction_type=extraction_type,
                css_selector=css_selector, main_content_only=True,
                use_trafilatura=True, timeout=_SNAPSHOT_TIMEOUT_S,
                stealthy_headers=True,
            ),
            timeout=_OVERALL_CAP_S,
        )
    except Exception as e:  # timeout, network, validation — never propagate
        logger.debug("archive snapshot fetch failed for %s: %s", url, e)
        return None

    # 4. Validate: the snapshot must yield real, usable content. A wayback
    # "not archived" stub, an archived error page, or a JS shell is NOT served.
    if result.status >= 400 or result.error:
        return None
    if not result.content or not any(c.strip() for c in result.content):
        return None
    if _is_js_shell(result):
        return None

    # 5. Stamp honestly: the agent asked for `url`, not the archive URL.
    result.url = url
    result.source = "archive.org"
    result.archived_at = archived_at
    result.fetcher_used = "archive"
    result.escalation_path = (result.escalation_path or "") + "→archive"
    result.cached = False
    return result
