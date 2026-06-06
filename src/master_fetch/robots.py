"""Robots.txt compliance for Hound.

Respects robots.txt Disallow rules with caching per domain.
Uses Scrapling's HTTP fetcher (curl_cffi) instead of stdlib urllib
for browser-impersonated requests that bypass basic bot blocking.
Non-blocking — all I/O is async.
"""

import asyncio
import logging
from urllib.robotparser import RobotFileParser
from time import time

logger = logging.getLogger("master-fetch.robots")

# Cache robots.txt parsers per domain: {domain: (RobotFileParser, fetch_time)}
_robots_cache: dict[str, tuple[RobotFileParser, float]] = {}
_ROBOTS_CACHE_TTL = 3600  # 1 hour
_FETCH_TIMEOUT = 10  # seconds

DEFAULT_USER_AGENT = (
    "Hound/2.7 (web research for AI agents; https://github.com/dondai1234/master-fetch)"
)


def _extract_netloc(url: str) -> str:
    """Extract netloc from URL. Returns '' for invalid URLs."""
    from urllib.parse import urlparse
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


async def _fetch_robots_txt(domain: str) -> str | None:
    """Fetch robots.txt for a domain using curl_cffi (async, impersonated).

    Returns the raw text content or None if unreachable.
    """
    try:
        from scrapling.engines.static import FetcherSession
        async with FetcherSession() as sess:
            response, _ = await asyncio.to_thread(
                lambda: sess.get(f"https://{domain}/robots.txt", timeout=_FETCH_TIMEOUT)
            )
            # Extract body from response
            if hasattr(response, 'body'):
                body = response.body
                if body:
                    return body.decode(response.encoding or 'utf-8', errors='replace')
    except ImportError:
        # Fallback: use aiohttp if available, or urllib in thread
        pass
    except Exception:
        pass

    # Fallback: urllib in thread (no impersonation, but works for basic sites)
    try:
        from urllib.request import Request, urlopen
        from urllib.error import URLError

        def _sync_fetch():
            req = Request(
                f"https://{domain}/robots.txt",
                headers={"User-Agent": DEFAULT_USER_AGENT},
            )
            with urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
                return resp.read().decode("utf-8", errors="replace")

        return await asyncio.to_thread(_sync_fetch)
    except (URLError, OSError, Exception):
        return None


async def _get_robots_parser(domain: str, user_agent: str = "*") -> RobotFileParser | None:
    """Fetch and parse robots.txt for a domain. Caches result.

    Returns None if robots.txt is unreachable (allow by default).
    Returns RobotFileParser if successfully fetched.
    """
    now_ts = time()

    # Check cache
    if domain in _robots_cache:
        parser, fetched_at = _robots_cache[domain]
        if now_ts - fetched_at < _ROBOTS_CACHE_TTL:
            return parser
        del _robots_cache[domain]

    raw = await _fetch_robots_txt(domain)
    if raw is None:
        logger.debug(f"robots.txt unreachable for {domain}")
        return None

    try:
        parser = RobotFileParser()
        parser.parse(raw.splitlines())
        _robots_cache[domain] = (parser, now_ts)
        logger.debug(f"Fetched and parsed robots.txt for {domain}")
        return parser
    except Exception as e:
        logger.debug(f"Failed to parse robots.txt for {domain}: {e}")
        return None


async def is_allowed(url: str, user_agent: str = "*") -> bool:
    """Check if a URL is allowed per robots.txt.

    Returns True if:
    - robots.txt is unreachable (allow by default)
    - robots.txt allows this URL
    - URL is invalid (malformed)

    Returns False only if robots.txt explicitly disallows this URL.
    """
    domain = _extract_netloc(url)
    if not domain:
        return True  # Malformed URL: allow

    parser = await _get_robots_parser(domain, user_agent)
    if parser is None:
        return True  # Can't reach robots.txt: allow

    try:
        return parser.can_fetch(user_agent, url)
    except Exception:
        return True  # Parse error: allow


def clear_robots_cache() -> None:
    """Clear the robots.txt cache."""
    _robots_cache.clear()
    logger.info("Robots.txt cache cleared")
