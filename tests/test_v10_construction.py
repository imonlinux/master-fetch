"""v10 construction regression: _apply_chunking must preserve EVERY preserved
ResponseModel field on truncation (and on the no-more-content branch).

Pre-v10, _apply_chunking rebuilt ResponseModel by hand at two sites, listing
fields explicitly. Any field not listed (metadata, links, quality_score, and
every new envelope field) silently vanished on truncated responses — the
contextvar-vanish bug class flagged in STATUS dev notes. v10 collapses both
sites to ``result.model_copy(update={...})`` so fields survive by construction.

NOTE on derived vs preserved: _apply_envelope (run inside _with_agent_hints)
RECOMPUTES source_type/is_official/content_age_days/is_stale from url+metadata
on every result — those are outputs, not preserved. This test asserts the
PRESERVED fields (metadata/links/media/quality_score/toc/page_type/source/
archived_at) survive chunking. The derived fields are tested in
test_v10_envelope.py.
"""
from master_fetch.server import ResponseModel, _apply_chunking


def _fully_populated() -> ResponseModel:
    """A ResponseModel with every PRESERVED field set to a recognizable value.

    Uses source='archive.org' so we also prove the archive-fallback fields
    survive the chunking refactor (archive results get chunked too).
    """
    return ResponseModel(
        status=200,
        content=["x" * 50_000],  # large enough to force truncation under a small max_chars
        url="https://example.com/article",
        cached=False,
        fetcher_used="http",
        extracted_type="markdown",
        session_id="sess123",
        duration_ms=123.4,
        error="",
        content_type="text/html",
        total_size_bytes=999,
        total_extracted_chars=50_000,
        is_truncated=False,
        next_offset=0,
        escalation_path="direct:http",
        retry_count=1,
        summary="orig",
        content_ok=True,
        next_action="orig",
        fetched_at="2026-07-18T12:00:00+00:00",
        metadata={"title": "T", "published_time": "2024-01-01", "author": "A"},
        media=["https://example.com/img.png"],
        links={"citations": [{"url": "https://example.com/c", "text": "c"}]},
        quality_score=0.0,
        table_of_contents=[{"level": 1, "title": "Sec", "page": 1, "end_page": 2}],
        # v10 preserved envelope fields:
        page_type="article",
        source="archive.org",
        archived_at="2024-06-01T00:00:00+00:00",
    )


def test_truncation_preserves_all_fields():
    result = _fully_populated()
    out = _apply_chunking(result, max_chars=1000, offset=0)

    # Fields _apply_chunking is allowed to change:
    assert out.is_truncated is True
    assert out.next_offset > 0
    assert out.total_extracted_chars == 50_000
    assert len(out.content) == 1
    # Everything else MUST survive (this is the regression):
    assert out.status == 200
    assert out.url == "https://example.com/article"
    assert out.fetcher_used == "http"
    assert out.extracted_type == "markdown"
    assert out.session_id == "sess123"
    assert out.duration_ms == 123.4
    assert out.content_type == "text/html"
    assert out.total_size_bytes == 999
    assert out.escalation_path == "direct:http"
    assert out.retry_count == 1
    assert out.metadata == {"title": "T", "published_time": "2024-01-01", "author": "A"}
    assert out.media == ["https://example.com/img.png"]
    assert out.links == {"citations": [{"url": "https://example.com/c", "text": "c"}]}
    assert out.table_of_contents == [{"level": 1, "title": "Sec", "page": 1, "end_page": 2}]
    # v10 preserved envelope fields survive chunking:
    assert out.page_type == "article"
    assert out.source == "archive.org"
    assert out.archived_at == "2024-06-01T00:00:00+00:00"


def test_no_more_content_branch_preserves_all_fields():
    """The offset >= total_len branch (returns '[No more content.]') must also
    preserve every preserved field — the old code dropped them here too."""
    result = _fully_populated()
    out = _apply_chunking(result, max_chars=1000, offset=50_001)

    assert out.content == ["[No more content.]"]
    assert out.is_truncated is False
    assert out.next_offset == 0
    # Everything else survives:
    assert out.metadata == {"title": "T", "published_time": "2024-01-01", "author": "A"}
    assert out.links == {"citations": [{"url": "https://example.com/c", "text": "c"}]}
    assert out.media == ["https://example.com/img.png"]
    assert out.table_of_contents == [{"level": 1, "title": "Sec", "page": 1, "end_page": 2}]
    assert out.page_type == "article"
    assert out.source == "archive.org"
    assert out.archived_at == "2024-06-01T00:00:00+00:00"
    assert out.escalation_path == "direct:http"


def test_short_content_not_truncated_preserves_fields():
    """No truncation path — preserved fields still survive (sanity)."""
    result = _fully_populated()
    result.content = ["short"]
    out = _apply_chunking(result, max_chars=40000, offset=0)
    assert out.is_truncated is False
    assert out.page_type == "article"
    assert out.source == "archive.org"
    assert out.archived_at == "2024-06-01T00:00:00+00:00"
    assert out.metadata["title"] == "T"
    assert out.table_of_contents == [{"level": 1, "title": "Sec", "page": 1, "end_page": 2}]
