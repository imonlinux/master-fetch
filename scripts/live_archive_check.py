"""Live check: prove the v10 archive fallback actually recovers a real page
from the Internet Archive (not just mocks).

Calls try_archive_fallback with the REAL availability API + REAL server.get on
a URL that is extensively archived (example.com). Asserts the recovery returns
clean content, honestly marked source='archive.org' with a real archived_at.

Run:  python scripts/live_archive_check.py
"""
import asyncio
import sys
from pathlib import Path

# Ensure src/ is importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from master_fetch.server import MasterFetchServer
from master_fetch.archive import try_archive_fallback


async def main():
    server = MasterFetchServer()
    # example.com is extensively archived; we call the fallback directly
    # (bypassing _is_archive_worthy, which only fires on a live hard-fail) to
    # exercise the full recovery path against the real Internet Archive.
    url = "https://example.com/"
    print(f"Recovering {url} from the Internet Archive (real network)...")
    r = await try_archive_fallback(
        server, url, "markdown", None, cache_ttl=0, pages=None, max_chars=40000,
    )
    if r is None:
        print("FAIL: archive fallback returned None (no snapshot or validation failed).")
        sys.exit(1)
    assert r.source == "archive.org", f"source={r.source!r}"
    assert r.archived_at, "archived_at is empty"
    assert r.url == url, f"url={r.url!r} (should be the original)"
    assert r.fetcher_used == "archive", f"fetcher_used={r.fetcher_used!r}"
    assert r.content and any(c.strip() for c in r.content), "no content"
    text = " ".join(r.content)
    # example.com's archived content contains its well-known heading/text.
    assert "example" in text.lower(), f"unexpected content: {text[:200]!r}"
    print(f"PASS: recovered from archive.org snapshot dated {r.archived_at}")
    print(f"  url={r.url}  fetcher={r.fetcher_used}  page_type={r.page_type}")
    print(f"  escalation_path={r.escalation_path}")
    print(f"  content[:{min(120, len(text))}]={text[:120]!r}")

    # ── Part 2: the full _finalize_result integration (hard-fail -> archive) ──
    # Simulate a live 404 (the page is gone) and let _finalize_result recover it
    # via the real archive fallback. This proves the wiring, not just the module.
    from master_fetch.server import ResponseModel
    print(f"\nSimulating a 404 on {url} and recovering via _finalize_result...")
    hard_fail = ResponseModel(
        status=404, content=["404 Not Found"], url=url,
        fetcher_used="http", extracted_type="markdown",
    )
    out = await server._finalize_result(
        hard_fail, url, "markdown", None, cache_ttl=0, max_chars=40000,
    )
    assert out.source == "archive.org", f"integration failed: source={out.source!r}"
    assert out.archived_at, "integration failed: archived_at empty"
    assert out.content and any(c.strip() for c in out.content), "no content"
    assert "example" in " ".join(out.content).lower()
    print(f"PASS: _finalize_result recovered the 404 from archive.org ({out.archived_at})")
    print(f"  fetcher={out.fetcher_used}  escalation_path={out.escalation_path}")
    print(f"  next_action={out.next_action[:90]!r}")
    print("\nAll live archive checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
