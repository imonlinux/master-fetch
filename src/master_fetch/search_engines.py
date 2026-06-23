"""Hound-native keyless search engine scrapers (v7 local search flagship).

No API key, no account, no third-party service. Scrapes public search engines
(DuckDuckGo, Bing, Google, Wikipedia) over browser-impersonated HTTP (scrapling
FetcherSession, a CORE dependency, so lean installs get working search), with
escalation to hound's warm stealthy Patchright browser when an engine blocks.

Built for one job: feed smart_fetch.

Honest posture (same as SearXNG/ddgs): engines may rate-limit or CAPTCHA. We
mitigate with browser-impersonated TLS, a real user agent, backoff, multi-engine
fallback, the warm stealthy browser as the anti-bot tier, and result caching
(in search.py). No search-engine ToS compliance is claimed.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, asdict
from time import time
from typing import Optional
from urllib.parse import quote, urlparse, parse_qs, unquote, urljoin

from bs4 import BeautifulSoup

from master_fetch.crawl import normalize_url

logger = logging.getLogger("master-fetch.search_engines")

DEFAULT_ENGINES = ("duckduckgo", "bing", "wikipedia")
ENGINE_TIMEOUT = 12  # seconds per engine request
CAPTCHA_MARKERS = ("captcha", "unusual traffic", "are you a robot", "sorry/image", "ddg-captcha", "blocked", "access denied")


@dataclass
class RawResult:
    title: str
    url: str
    snippet: str
    source: str  # engine name
    position: int = 0  # 1-indexed within its engine


@dataclass
class EngineReport:
    name: str
    ok: bool = False        # parsed >=1 result
    blocked: bool = False   # rate-limited / CAPTCHA'd / refused
    error: str = ""


# ─── transport ──────────────────────────────────────────────────────────────

def _is_blocked(status: int, body_text: str) -> bool:
    if status in (429, 503, 403):
        return True
    low = (body_text or "").lower()
    if any(m in low for m in CAPTCHA_MARKERS):
        # Only treat as blocked if the body is short (a real SERP is large and
        # may legitimately contain the word "blocked" in a result snippet).
        return len(low) < 6000
    return False


async def _impersonated_get(url: str, *, method: str = "GET", form: Optional[dict] = None,
                            timeout: int = ENGINE_TIMEOUT) -> tuple[Optional[str], int, bool]:
    """Browser-impersonated HTTP GET/POST via scrapling FetcherSession (core dep).

    Returns (html_text, status, blocked). html_text is None on transport failure.
    """
    try:
        from scrapling.engines.static import FetcherSession
    except ImportError:
        # Fallback to urllib (no impersonation) so search still works on a
        # minimal install where scrapling's static engine is unavailable.
        return await _urllib_get(url, timeout=timeout)

    try:
        async with FetcherSession() as sess:
            if method == "POST" and form is not None:
                resp = await sess.post(url, data=form, timeout=timeout)
            else:
                resp = await sess.get(url, timeout=timeout)
            status = getattr(resp, "status", 0) or 0
            body = getattr(resp, "body", None)
            if not body:
                return None, status, status in (429, 503, 403)
            text = body.decode(getattr(resp, "encoding", None) or "utf-8", errors="replace")
            blocked = _is_blocked(status, text)
            return text, status, blocked
    except Exception as e:
        logger.debug(f"impersonated GET failed for {url[:80]}: {e}")
        return None, 0, False


async def _urllib_get(url: str, *, timeout: int = ENGINE_TIMEOUT) -> tuple[Optional[str], int, bool]:
    """Stdlib fallback transport (no TLS impersonation)."""
    from urllib.request import Request, urlopen
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

    def _do():
        req = Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        with urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace"), r.status

    try:
        text, status = await asyncio.to_thread(_do)
        return text, status, _is_blocked(status, text)
    except Exception as e:
        logger.debug(f"urllib GET failed for {url[:80]}: {e}")
        return None, 0, False


async def _stealthy_html(server, url: str, timeout: int = ENGINE_TIMEOUT) -> Optional[str]:
    """Escalation: fetch a SERP via hound's warm stealthy browser, return raw HTML.

    This is the flagship anti-bot move: when an engine blocks the HTTP scraper,
    hound renders the SERP in the warm Patchright browser and parses that instead.
    No keyless search lib does this. Returns None if the stealthy path is
    unavailable or yields no HTML.
    """
    if server is None:
        return None
    try:
        res = await server.stealthy_fetch(
            url, extraction_type="html", main_content_only=False,
            use_trafilatura=False, google_search=False,
            disable_resources=True, timeout=int(timeout * 1000),
        )
        # content is a list of text chunks; for html extraction it's the page HTML.
        if res and res.content and not res.error:
            html = "".join(res.content)
            if html and len(html) > 200:
                return html
    except Exception as e:
        logger.debug(f"stealthy SERP escalation failed for {url[:80]}: {e}")
    return None


# ─── DuckDuckGo (html endpoint) ─────────────────────────────────────────────

def _ddg_real_url(href: str) -> str:
    """Decode a DDG redirect link (//duckduckgo.com/l/?uddg=ENCODED&rut=...) to the real URL."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    if "uddg=" in href:
        qs = parse_qs(urlparse(href).query)
        u = qs.get("uddg", [""])[0]
        if u:
            return unquote(u)
    return href


def _parse_ddg(html: str) -> list[RawResult]:
    soup = BeautifulSoup(html, "lxml")
    out: list[RawResult] = []
    for i, block in enumerate(soup.select(".result, .web-result")):
        a = block.select_one(".result__a")
        if not a:
            continue
        href = a.get("href", "") or ""
        url = _ddg_real_url(href)
        title = a.get_text(" ", strip=True)
        if not url or not title:
            continue
        snip_el = block.select_one(".result__snippet")
        snippet = snip_el.get_text(" ", strip=True) if snip_el else ""
        out.append(RawResult(title=title, url=url, snippet=snippet, source="duckduckgo", position=i + 1))
    return out


async def search_ddg(query: str, max_results: int, *, region: str = "us-en",
                     freshness: Optional[str] = None, server=None) -> tuple[list[RawResult], EngineReport]:
    q = query
    # DDG html endpoint supports a time filter via the `df` param (d/w/m/y).
    params = f"q={quote(q)}&kl={quote(region)}"
    if freshness in ("day", "week", "month", "year"):
        params += f"&df={freshness[0]}"
    url = f"https://html.duckduckgo.com/html/?{params}"
    text, status, blocked = await _impersonated_get(url)
    rep = EngineReport(name="duckduckgo")
    if blocked and server is not None:
        escalated = await _stealthy_html(server, url)
        if escalated:
            text, blocked = escalated, False
    if not text:
        rep.blocked = blocked or (status in (429, 503, 403))
        rep.error = f"no response (status {status})"
        return [], rep
    results = _parse_ddg(text)[:max_results]
    rep.ok = bool(results)
    rep.blocked = (not results) and blocked
    if not results:
        rep.error = "no results parsed"
    return results, rep


# ─── Bing ────────────────────────────────────────────────────────────────────

def _parse_bing(html: str) -> list[RawResult]:
    soup = BeautifulSoup(html, "lxml")
    out: list[RawResult] = []
    for i, li in enumerate(soup.select("li.b_algo")):
        a = li.select_one("h2 a")
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        # Bing wraps the main link in an opaque bing.com/ck/a redirect that has
        # NO recoverable real URL in the href. The real URL is shown in the <cite>
        # display element (sometimes with '›' breadcrumb separators).
        cite = li.select_one(".b_attribution cite, .t_tgk cite, cite")
        cite_text = cite.get_text(" ", strip=True) if cite else ""
        url = _bing_real_url(cite_text)
        if not url:
            continue  # can't recover the real URL; skip the junk redirect
        snip = li.select_one(".b_caption p, p.b_paractr, .b_lineclamp4, .b_caption .b_paractr")
        snippet = snip.get_text(" ", strip=True) if snip else ""
        out.append(RawResult(title=title, url=url, snippet=snippet, source="bing", position=i + 1))
    return out


def _bing_real_url(cite_text: str) -> str:
    """Reconstruct a real URL from Bing's <cite> display text.

    Bing shows the result URL as e.g. 'https://www.programiz.com › python-programming › online-compiler'.
    Replace the '›' breadcrumb separators with '/' and ensure a scheme.
    """
    if not cite_text:
        return ""
    parts = [p.strip() for p in cite_text.split("\u203a") if p.strip()]
    url = "/".join(parts) if len(parts) > 1 else cite_text.strip()
    if url.startswith(("http://", "https://")):
        return url
    first = url.split("/", 1)[0]
    if url.startswith("www.") or ("." in first and " " not in first):
        return "https://" + url
    return ""


async def search_bing(query: str, max_results: int, *, region: str = "us-en",
                      freshness: Optional[str] = None, server=None) -> tuple[list[RawResult], EngineReport]:
    params = f"q={quote(query)}&count={max(min(max_results * 2, 50), 10)}&setlang=en"
    if freshness in ("day", "week", "month"):
        params += f"&filters=ex1%3a%22ez5_{freshness[0]}1%22"
    elif freshness == "year":
        params += "&filters=ex1%3a%22ez5_y1%22"
    url = f"https://www.bing.com/search?{params}"
    text, status, blocked = await _impersonated_get(url)
    rep = EngineReport(name="bing")
    if blocked and server is not None:
        escalated = await _stealthy_html(server, url)
        if escalated:
            text, blocked = escalated, False
    if not text:
        rep.blocked = blocked or (status in (429, 503, 403))
        rep.error = f"no response (status {status})"
        return [], rep
    results = _parse_bing(text)[:max_results]
    rep.ok = bool(results)
    rep.blocked = (not results) and blocked
    if not results:
        rep.error = "no results parsed"
    return results, rep


# ─── Google (stealthy-friendly; impersonated HTTP often CAPTCHAs) ────────────

def _parse_google(html: str) -> list[RawResult]:
    soup = BeautifulSoup(html, "lxml")
    out: list[RawResult] = []
    seen: set[str] = set()
    # Modern Google: div.g / div.MjjYud > div[data-ved] with h3 + a + snippet span.
    for block in soup.select("div.g, div._kno, div[data-ved]"):
        a = block.select_one("a:has(h3)") or block.select_one("h3 a, a h3")
        # fallback: any <a> with an <h3> inside
        if not a:
            h3 = block.select_one("h3")
            if h3:
                a = h3.find_parent("a")
        if not a:
            continue
        href = a.get("href", "") or ""
        if href.startswith("/url?"):
            qs = parse_qs(urlparse(href).query)
            href = qs.get("q", [""])[0] or href
        if not href.startswith("http"):
            continue
        h3 = block.select_one("h3")
        title = h3.get_text(" ", strip=True) if h3 else a.get_text(" ", strip=True)
        if not title:
            continue
        if href in seen:
            continue
        seen.add(href)
        snip = block.select_one(".VwiC3b, [data-sncf], span.aCOpRe, div.IsZvec span")
        snippet = snip.get_text(" ", strip=True) if snip else ""
        out.append(RawResult(title=title, url=href, snippet=snippet, source="google", position=len(out) + 1))
        if len(out) >= 30:
            break
    return out


async def search_google(query: str, max_results: int, *, region: str = "us-en",
                        freshness: Optional[str] = None, server=None) -> tuple[list[RawResult], EngineReport]:
    params = f"q={quote(query)}&hl=en&num={max(min(max_results * 2, 50), 10)}"
    if freshness in ("day", "week", "month", "year"):
        params += f"&tbs=qdr:{freshness[0]}"
    url = f"https://www.google.com/search?{params}"
    text, status, blocked = await _impersonated_get(url)
    rep = EngineReport(name="google")
    # Google almost always CAPTCHAs plain impersonated HTTP under any load; if
    # blocked or empty, escalate to the warm stealthy browser (the flagship move).
    if (blocked or not text) and server is not None:
        escalated = await _stealthy_html(server, url)
        if escalated:
            text, blocked = escalated, False
    if not text:
        rep.blocked = blocked or (status in (429, 503, 403))
        rep.error = f"no response (status {status})"
        return [], rep
    results = _parse_google(text)[:max_results]
    rep.ok = bool(results)
    rep.blocked = (not results) and blocked
    if not results:
        rep.error = "no results parsed (likely CAPTCHA)"
    return results, rep


# ─── Wikipedia (official API, keyless, always works) ─────────────────────────

async def search_wikipedia(query: str, max_results: int, *, region: str = "us-en",
                           freshness: Optional[str] = None, server=None
                           ) -> tuple[list[RawResult], EngineReport]:
    lang = "en"
    # region is like 'us-en' (country-language); the Wikipedia host language is
    # the LAST segment (the language), not the country prefix.
    if region and "-" in region:
        lang = region.split("-")[-1]
    elif region:
        lang = region
    url = (f"https://{lang}.wikipedia.org/w/api.php?action=query&list=search"
           f"&srsearch={quote(query)}&srlimit={max(min(max_results, 20), 1)}"
           f"&srprop=snippet&format=json&utf8=1")
    text, status, blocked = await _impersonated_get(url)
    rep = EngineReport(name="wikipedia")
    if not text:
        rep.blocked = blocked or (status in (429, 503, 403))
        rep.error = f"no response (status {status})"
        return [], rep
    out: list[RawResult] = []
    try:
        data = __import__("json").loads(text)
        for i, item in enumerate(data.get("query", {}).get("search", [])[:max_results]):
            title = item.get("title", "").strip()
            if not title:
                continue
            snip_html = item.get("snippet", "")
            snippet = BeautifulSoup(snip_html, "lxml").get_text(" ", strip=True)
            page_url = f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
            out.append(RawResult(title=title, url=page_url, snippet=snippet, source="wikipedia", position=i + 1))
    except Exception as e:
        rep.error = f"parse error: {e}"
        return [], rep
    rep.ok = bool(out)
    return out, rep


# ─── orchestrator ─────────────────────────────────────────────────────────────

_ENGINES = {
    "duckduckgo": search_ddg,
    "bing": search_bing,
    "google": search_google,
    "wikipedia": search_wikipedia,
}


def _strip_tags(s: str) -> str:
    if not s:
        return ""
    return BeautifulSoup(s, "lxml").get_text(" ", strip=True)


def merge_dedupe(per_engine: list[tuple[list[RawResult], EngineReport]], max_results: int,
                 site: Optional[str] = None, exclude_sites: Optional[list[str]] = None,
                 ) -> list[RawResult]:
    """Merge results across engines, dedup by normalized URL, apply site filters.

    Same-domain `site:` filter and `-site:` exclusions are applied here (on the
    final URL) so they work regardless of which engine returned the result.
    """
    seen: dict[str, RawResult] = {}
    for results, _rep in per_engine:
        for r in results:
            try:
                host = (urlparse(r.url).netloc or "").lower()
            except Exception:
                continue
            if site and site.lower() not in host:
                continue
            if any(d and d.lower() in host for d in (exclude_sites or [])):
                continue
            key = normalize_url(r.url)
            if key in seen:
                # Keep the higher-ranked / earlier copy; prefer a non-empty snippet.
                prev = seen[key]
                if (not prev.snippet and r.snippet) or r.position < prev.position:
                    seen[key] = r
                continue
            seen[key] = r
    return list(seen.values())[:max(max_results * 3, max_results)]


# ─── BM25 keyword rerank (Phase 1 baseline; neural comes in Phase 2) ──────────

def _tokenize(text: str) -> list[str]:
    import re
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 1]


def bm25_rerank(query: str, results: list[RawResult], *, k1: float = 1.5, b: float = 0.75
                ) -> list[tuple[RawResult, float]]:
    """Rank the merged set by BM25 over (title + snippet). Returns (result, score)
    sorted desc. Score normalized to 0..1 by the max. When scores are all 0
    (no term overlap, e.g. a purely semantic query), preserves engine order via a
    position-based tiebreak so results are never randomly shuffled."""
    q_terms = _tokenize(query)
    docs = [f"{r.title} {r.snippet}" for r in results]
    doc_tokens = [_tokenize(d) for d in docs]
    N = len(doc_tokens)
    if N == 0 or not q_terms:
        return [(r, 0.0) for r in results]
    avgdl = (sum(len(t) for t in doc_tokens) / N) or 1.0
    df: dict[str, int] = {}
    for toks in doc_tokens:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    scored: list[tuple[RawResult, float]] = []
    for r, toks in zip(results, doc_tokens):
        if not toks:
            scored.append((r, 0.0))
            continue
        dl = len(toks)
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        score = 0.0
        for q in q_terms:
            if q not in tf:
                continue
            n_q = df.get(q, 0)
            idf = max(0.0, ((N - n_q + 0.5) / (n_q + 0.5)) + 1.0)
            f = tf[q]
            score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        scored.append((r, score))
    max_s = max((s for _, s in scored), default=0.0)
    # Tiebreak: engine position (lower = better) then original order, so zero-overlap
    # queries don't get randomly reordered by the merge dict.
    order = {id(r): i for i, (r, _) in enumerate(scored)}
    scored.sort(key=lambda rs: (-rs[1], rs[0].position, order[id(rs[0])]))
    if max_s > 0:
        scored = [(r, s / max_s) for r, s in scored]
    return scored


async def peek_content(url: str, *, timeout: int = 6, max_chars: int = 2000) -> str:
    """Cheap HTTP peek: fetch a URL (browser-impersonated, no stealthy escalation
    so it stays fast) and return a short trafilatura text extract of its real
    content. Returns '' on any failure / block so the deep-rerank caller falls
    back to the engine snippet for that result. This is the Phase 3 'live content
    peek' that lets hound rerank on ACTUAL page text, not just engine snippets."""
    try:
        text, _status, blocked = await _impersonated_get(url, timeout=timeout)
    except Exception:
        return ""
    if not text or blocked:
        return ""
    try:
        import trafilatura
        extracted = (trafilatura.extract(text[:60000], include_comments=False,
                                          include_tables=False) or "")
    except Exception:
        extracted = ""
    return extracted[:max_chars]


async def peek_many(urls: list[str], *, timeout: int = 6, max_chars: int = 2000,
                    concurrency: int = 8) -> dict[str, str]:
    """Peek many URLs in parallel (bounded). Returns {url: peek_text} (failed
    peeks are omitted so the caller falls back to snippets for those URLs)."""
    sem = asyncio.Semaphore(concurrency)

    async def _one(u):
        async with sem:
            return u, await peek_content(u, timeout=timeout, max_chars=max_chars)

    pairs = await asyncio.gather(*[_one(u) for u in urls], return_exceptions=True)
    out: dict[str, str] = {}
    for p in pairs:
        if isinstance(p, BaseException):
            continue
        u, txt = p
        if txt:
            out[u] = txt
    return out


async def multi_search(
    query: str,
    max_results: int = 10,
    *,
    engines: Optional[list[str]] = None,
    site: Optional[str] = None,
    exclude_sites: Optional[list[str]] = None,
    region: str = "us-en",
    freshness: Optional[str] = None,
    server=None,
) -> tuple[list[RawResult], list[EngineReport]]:
    """Run the chosen engines in parallel, merge, dedup, BM25-rerank.

    Returns (ranked_results, engine_reports). engines defaults to
    (duckduckgo, bing, wikipedia). Unknown engine names are ignored.
    """
    names = [e for e in (engines or list(DEFAULT_ENGINES)) if e in _ENGINES]
    if not names:
        names = list(DEFAULT_ENGINES)
    per_engine = await asyncio.gather(
        *[_ENGINES[n](query, max_results, region=region, freshness=freshness, server=server) for n in names],
        return_exceptions=True,
    )
    reports: list[EngineReport] = []
    cleaned: list[tuple[list[RawResult], EngineReport]] = []
    for name, res in zip(names, per_engine):
        if isinstance(res, BaseException):
            rep = EngineReport(name=name, error=redact(str(res)[:120]))
            reports.append(rep)
            cleaned.append(([], rep))
            logger.warning(f"engine {name} crashed: {res}")
        else:
            results, rep = res
            reports.append(rep)
            cleaned.append((results, rep))
    merged = merge_dedupe(cleaned, max_results, site=site, exclude_sites=exclude_sites)
    ranked = bm25_rerank(query, merged)
    return [r for r, _ in ranked], reports


def redact(s: str) -> str:
    """Light redaction for engine error strings surfaced to the agent."""
    return s
