"""Adversarial tests for v11.2.0 search quality improvements.

Tests the six-signal ranking (domain reputation + answer-signal scoring + title
relevance + URL relevance), source type detection, snippet merging, smarter
fetch_relevance tiers, and heading-aware BM25 with table/code preservation
in focus_content.

All tests use real code paths with mocked network (no live HTTP).
"""

import pytest
from master_fetch.search import (
    _domain_boost, _answer_signal_score, _is_technical_query,
    _apply_quality_boost, SearchResult, compute_fetch_hint,
    _source_type, _title_relevance, _url_relevance, _tier,
)
from master_fetch.search_engines import RawResult
from master_fetch.focus import focus_content, _is_table, _is_code, _is_heading, _heading_level


# ─── Domain reputation boosting ──────────────────────────────────

class TestDomainBoost:

    def test_github_technical_query_gets_boost(self):
        boost = _domain_boost("https://github.com/user/repo", "transformer architecture model", True)
        assert boost == 0.15

    def test_arxiv_technical_query_gets_boost(self):
        boost = _domain_boost("https://arxiv.org/abs/2024.12345", "neural network paper", True)
        assert boost == 0.15

    def test_medium_technical_query_no_boost(self):
        boost = _domain_boost("https://medium.com/@user/post", "transformer architecture", True)
        assert boost == 0.0

    def test_github_non_technical_query_no_boost(self):
        boost = _domain_boost("https://github.com/user/repo", "best restaurants in paris", False)
        assert boost == 0.0

    def test_wikipedia_always_gets_small_boost(self):
        boost = _domain_boost("https://en.wikipedia.org/wiki/GPT-3", "best restaurants", False)
        assert boost == 0.05

    def test_wikipedia_technical_always_small_boost_not_full(self):
        boost = _domain_boost("https://en.wikipedia.org/wiki/Transformer", "neural architecture", True)
        assert boost == 0.05

    def test_www_prefix_stripped(self):
        boost = _domain_boost("https://www.github.com/repo", "code api", True)
        assert boost == 0.15

    def test_stackoverflow_boosted(self):
        boost = _domain_boost("https://stackoverflow.com/q/12345", "python code error", True)
        assert boost == 0.15

    def test_invalid_url_no_crash(self):
        boost = _domain_boost("not-a-url", "anything", True)
        assert boost == 0.0

    def test_empty_url_no_crash(self):
        boost = _domain_boost("", "anything", True)
        assert boost == 0.0


# ─── Answer-signal scoring ──────────────────────────────────────

class TestAnswerSignalScore:

    def test_digits_in_snippet_technical_query(self):
        score = _answer_signal_score(
            "GPT-3 model architecture dimension parameters",
            "The model has d_model=12288 and 96 layers",
            True,
        )
        assert score >= 0.15

    def test_no_digits_no_boost(self):
        score = _answer_signal_score(
            "GPT-3 model architecture dimension parameters",
            "This article discusses the evolution of language models",
            True,
        )
        assert score == 0.0

    def test_table_markers_in_snippet(self):
        score = _answer_signal_score(
            "model architecture comparison table",
            "Model | d_model | layers\nGPT-3 | 12288 | 96",
            True,
        )
        assert score >= 0.15

    def test_code_markers_in_snippet(self):
        score = _answer_signal_score(
            "how to implement python api function",
            "def fetch(url): import requests; return requests.get(url)",
            False,
        )
        assert score >= 0.10

    def test_comparison_markers_in_snippet(self):
        score = _answer_signal_score(
            "GPT-3 vs GPT-4 comparison difference",
            "GPT-4 is better compared to GPT-3, while being faster",
            False,
        )
        assert score >= 0.10

    def test_combined_signals_capped(self):
        score = _answer_signal_score(
            "model dimension table comparison vs code api",
            "d_model=12288 | table | def func(): compared vs better",
            True,
        )
        assert score <= 0.30

    def test_empty_snippet_no_crash(self):
        score = _answer_signal_score("anything", "", True)
        assert score == 0.0

    def test_two_digit_numbers_not_boosted(self):
        score = _answer_signal_score(
            "model dimension size",
            "The model has 24 layers and 12 heads",
            True,
        )
        assert score == 0.0


# ─── Technical query detection ───────────────────────────────────

class TestIsTechnicalQuery:

    def test_model_architecture_is_technical(self):
        assert _is_technical_query("GPT-3 model architecture d_model embedding dimension") is True

    def test_restaurant_query_not_technical(self):
        assert _is_technical_query("best restaurants in paris france") is False

    def test_code_api_is_technical(self):
        assert _is_technical_query("how to implement python api code") is True

    def test_arxiv_paper_is_technical(self):
        assert _is_technical_query("arxiv paper on transformer neural networks") is True

    def test_empty_query_not_technical(self):
        assert _is_technical_query("") is False


# ─── Source type detection ──────────────────────────────────────

class TestSourceType:

    def test_github_is_repo(self):
        assert _source_type("https://github.com/user/repo") == "repo"

    def test_arxiv_is_paper(self):
        assert _source_type("https://arxiv.org/abs/2024.12345") == "paper"

    def test_stackoverflow_is_forum(self):
        assert _source_type("https://stackoverflow.com/q/12345") == "forum"

    def test_docs_python_is_docs(self):
        assert _source_type("https://docs.python.org/3/library/") == "docs"

    def test_wikipedia_is_reference(self):
        assert _source_type("https://en.wikipedia.org/wiki/GPT-3") == "reference"

    def test_medium_is_blog(self):
        assert _source_type("https://medium.com/@user/post") == "blog"

    def test_reddit_is_forum(self):
        assert _source_type("https://reddit.com/r/MachineLearning") == "forum"

    def test_hackernews_is_forum(self):
        assert _source_type("https://news.ycombinator.com/item?id=12345") == "forum"

    def test_techcrunch_is_news(self):
        assert _source_type("https://techcrunch.com/2024/01/01/some-story") == "news"

    def test_huggingface_is_other(self):
        # huggingface.co is not in any domain set -> "other" (but gets domain boost from _TECH_DOMAINS)
        assert _source_type("https://huggingface.co/models") == "other"

    def test_path_based_docs_detection(self):
        assert _source_type("https://some-site.com/docs/api/reference") == "docs"

    def test_path_based_blog_detection(self):
        assert _source_type("https://some-site.com/blog/my-post") == "blog"

    def test_path_based_forum_detection(self):
        assert _source_type("https://some-site.com/forum/thread/123") == "forum"

    def test_empty_url_is_other(self):
        assert _source_type("") == "other"

    def test_invalid_url_is_other(self):
        assert _source_type("not-a-url") == "other"

    def test_subdomain_matching(self):
        assert _source_type("https://en.wikipedia.org/wiki/Test") == "reference"
        assert _source_type("https://api.github.com/repos/user/repo") == "repo"


# ─── Title relevance scoring ────────────────────────────────────

class TestTitleRelevance:

    def test_all_query_terms_in_title(self):
        boost = _title_relevance("GPT-3 architecture model", "GPT-3 Architecture Model Details")
        assert boost == pytest.approx(0.10)

    def test_some_query_terms_in_title(self):
        boost = _title_relevance("GPT-3 architecture model d_model", "GPT-3 Architecture Overview")
        # 2 of 3 effective terms (gpt-3, architecture, model) = 0.667 ratio * 0.10
        assert 0.06 < boost < 0.08

    def test_no_query_terms_in_title(self):
        boost = _title_relevance("python api code", "The Evolution of Artificial Intelligence")
        assert boost == 0.0

    def test_empty_title(self):
        assert _title_relevance("anything", "") == 0.0

    def test_empty_query(self):
        assert _title_relevance("", "Some Title") == 0.0

    def test_case_insensitive(self):
        boost = _title_relevance("GPT ARCHITECTURE", "gpt architecture model")
        assert boost == pytest.approx(0.10)


# ─── URL relevance scoring ──────────────────────────────────────

class TestURLRelevance:

    def test_query_terms_in_url_path(self):
        boost = _url_relevance("gpt-3 api models", "https://docs.openai.com/api/models/gpt-3")
        assert boost > 0.0

    def test_no_query_terms_in_url(self):
        boost = _url_relevance("python api code", "https://example.com/blog/random-thoughts")
        assert boost == 0.0

    def test_root_url_no_boost(self):
        boost = _url_relevance("anything", "https://example.com/")
        assert boost == 0.0

    def test_empty_url(self):
        assert _url_relevance("anything", "") == 0.0

    def test_empty_query(self):
        assert _url_relevance("", "https://example.com/page") == 0.0

    def test_max_cap_at_008(self):
        boost = _url_relevance("models api gpt reference", "https://docs.openai.com/api/models/gpt/reference")
        assert boost <= 0.08


# ─── Combined quality boost ──────────────────────────────────────

class TestApplyQualityBoost:

    def test_github_with_digits_outranks_medium_without(self):
        """The core test: GitHub with data should outrank Medium without data."""
        github = RawResult(
            title="LLM Architecture Table",
            url="https://github.com/user/llm-table",
            snippet="d_model=12288, layers=96, heads=96 for GPT-3 175B",
            source="duckduckgo", position=1, consensus=1,
        )
        medium = RawResult(
            title="The Evolution of GPT Models",
            url="https://medium.com/@user/gpt-evolution",
            snippet="GPT-3 was a revolutionary model that changed everything",
            source="brave", position=2, consensus=1,
        )
        ranked = [medium, github]
        scores = [0.85, 0.80]
        query = "GPT model embedding dimension d_model architecture table"

        boosted_ranked, boosted_scores, qsig = _apply_quality_boost(ranked, scores, query)

        assert boosted_ranked[0].url == "https://github.com/user/llm-table"
        assert boosted_scores[0] >= boosted_scores[1]

    def test_consensus_still_matters(self):
        """A result with high consensus but no domain/signal boost still ranks well."""
        result = RawResult(
            title="Some Result",
            url="https://random-site.com/page",
            snippet="Some content about the topic",
            source="duckduckgo", position=1, consensus=3,
        )
        ranked = [result]
        scores = [0.5]
        boosted_ranked, boosted_scores, _ = _apply_quality_boost(ranked, scores, "some topic")
        assert boosted_scores[0] >= 0.5

    def test_empty_results_no_crash(self):
        boosted, scores, qsig = _apply_quality_boost([], [], "anything")
        assert boosted == []
        assert scores == []
        assert qsig == {}

    def test_renormalization_preserves_order(self):
        """When boosts push scores above 1.0, renormalization should preserve order."""
        github = RawResult(
            title="Tech Model Table Comparison",
            url="https://github.com/repo",
            snippet="d_model=12288 table | comparison vs",
            source="ddg", position=1, consensus=2,
        )
        blog = RawResult(
            title="Blog Post About Stuff",
            url="https://blog.example.com/post",
            snippet="discusses the topic",
            source="ddg", position=2, consensus=1,
        )
        ranked = [github, blog]
        scores = [0.9, 0.9]
        boosted_ranked, boosted_scores, _ = _apply_quality_boost(
            ranked, scores, "model d_model table comparison vs")
        assert boosted_ranked[0].url == "https://github.com/repo"
        assert all(0.0 <= s <= 1.0 for s in boosted_scores)

    def test_title_relevance_affects_ranking(self):
        """A result with query terms in the title should get a boost."""
        with_title = RawResult(
            title="GPT-3 Architecture: d_model and layers",
            url="https://some-site.com/post",
            snippet="discusses architecture",
            source="ddg", position=2, consensus=1,
        )
        without_title = RawResult(
            title="The History of AI",
            url="https://other-site.com/article",
            snippet="discusses architecture",
            source="brave", position=1, consensus=1,
        )
        ranked = [without_title, with_title]
        scores = [0.85, 0.80]
        boosted_ranked, boosted_scores, _ = _apply_quality_boost(
            ranked, scores, "GPT-3 architecture d_model layers")
        # with_title should get +0.10 title boost, without_title gets 0
        assert boosted_ranked[0].title == "GPT-3 Architecture: d_model and layers"

    def test_quality_signals_returned(self):
        """_apply_quality_boost should return quality_signals dict with per-result data."""
        github = RawResult(
            title="Code",
            url="https://github.com/repo",
            snippet="d_model=12288",
            source="ddg", position=1, consensus=1,
        )
        ranked = [github]
        scores = [0.5]
        _, _, qsig = _apply_quality_boost(ranked, scores, "model d_model architecture")
        assert id(github) in qsig
        assert qsig[id(github)]["domain_boosted"] is True
        assert qsig[id(github)]["answer_signals"] >= 0.15


# ─── Smarter tier calculation ───────────────────────────────────

class TestSmarterTier:

    def test_high_consensus_promotes_to_high(self):
        """A result with 3+ engine consensus and medium score should get 'high'."""
        tier = _tier(0.30, 3, 6, consensus=3, domain_boosted=False, answer_signals=0.0)
        assert tier == "high"

    def test_domain_boosted_promotes_to_high(self):
        """A domain-boosted result with medium score should get 'high'."""
        tier = _tier(0.30, 3, 6, consensus=1, domain_boosted=True, answer_signals=0.0)
        assert tier == "high"

    def test_answer_signals_promote_to_high(self):
        """A result with strong answer signals (>=0.15) and medium score should get 'high'."""
        tier = _tier(0.30, 3, 6, consensus=1, domain_boosted=False, answer_signals=0.20)
        assert tier == "high"

    def test_no_quality_signals_stays_med(self):
        """A result with no quality signals and medium score stays 'med'."""
        tier = _tier(0.30, 3, 6, consensus=1, domain_boosted=False, answer_signals=0.0)
        assert tier == "med"

    def test_top_result_always_high(self):
        """Rank 1 is always 'high' regardless of signals."""
        tier = _tier(0.05, 1, 6, consensus=1, domain_boosted=False, answer_signals=0.0)
        assert tier == "high"

    def test_low_score_with_signals_not_promoted_if_too_low(self):
        """A result with quality signals but very low score (<0.25) should not be promoted."""
        tier = _tier(0.10, 3, 6, consensus=3, domain_boosted=True, answer_signals=0.20)
        assert tier != "high"


# ─── Heading-aware BM25 in focus_content ─────────────────────────

class TestHeadingAwareBM25:

    def test_blocks_under_matching_heading_boosted(self):
        """A block with no query terms but under a matching heading should be kept."""
        text = """# Introduction

This is an intro paragraph with no query terms.

## Model Architecture

The hidden dimension is critical for performance.
Each layer processes the full representation.

## Training Details

We used Adam optimizer with learning rate scheduling."""

        result = focus_content(text, "model architecture dimension")
        assert "Model Architecture" in result

    def test_heading_without_query_terms_not_boosted(self):
        """Blocks under a non-matching heading should not get heading boost."""
        text = """## Training Details

We used Adam optimizer with cosine annealing.

## Model Architecture

The architecture uses 96 transformer layers."""

        result = focus_content(text, "training details optimizer")
        assert "Adam optimizer" in result

    def test_heading_level_hierarchy(self):
        """### heading under ## heading: both match, both boosted."""
        text = """## Architecture

### Transformer Architecture

The attention mechanism is key.

### RNN Architecture

Recurrent connections process sequences."""

        result = focus_content(text, "architecture transformer attention")
        assert "Transformer Architecture" in result
        assert "attention mechanism" in result

    def test_table_preservation(self):
        """Tables with query terms are always kept, even with low BM25 score."""
        text = """# Introduction

Some intro text here.

| Model | d_model | layers |
|---|---|---|
| GPT-3 | 12288 | 96 |

## Other Section

Unrelated content about training."""

        result = focus_content(text, "GPT-3 d_model 12288")
        assert "12288" in result
        assert "|" in result  # table format preserved


# ─── Table/code detection helpers ────────────────────────────────

class TestTableCodeDetection:

    def test_is_table_markdown(self):
        assert _is_table("| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |") is True

    def test_is_table_not_prose(self):
        assert _is_table("This is a paragraph of text with no pipes.") is False

    def test_is_code_fenced(self):
        assert _is_code("```python\ndef foo():\n    pass\n```") is True

    def test_is_code_indented(self):
        assert _is_code("    def foo():\n        return 42\n    bar = foo()") is True

    def test_is_code_not_prose(self):
        assert _is_code("This is a paragraph, not code.") is False

    def test_is_heading_markdown(self):
        assert _is_heading("## Section Title") is True

    def test_is_heading_not_prose(self):
        assert _is_heading("This is a paragraph.") is False

    def test_heading_level(self):
        assert _heading_level("# Title") == 1
        assert _heading_level("## Section") == 2
        assert _heading_level("### Subsection") == 3
        assert _heading_level("Not a heading") == 0


# ─── compute_fetch_hint with source types ──────────────────────

class TestComputeFetchHint:

    def test_hint_includes_source_types(self):
        results = [
            SearchResult(title="A", url="https://github.com/a", snippet="",
                        fetch_relevance="high", source_type="repo"),
            SearchResult(title="B", url="https://arxiv.org/b", snippet="",
                        fetch_relevance="high", source_type="paper"),
            SearchResult(title="C", url="https://medium.com/c", snippet="",
                        fetch_relevance="med", source_type="blog"),
        ]
        hint = compute_fetch_hint(results)
        assert "repo:1" in hint
        assert "paper:1" in hint
        assert "blog:1" in hint

    def test_hint_without_source_types(self):
        results = [
            SearchResult(title="A", url="https://a.com", snippet="", fetch_relevance="high"),
        ]
        hint = compute_fetch_hint(results)
        assert "Ranked by relevance_score" in hint

    def test_empty_results(self):
        assert compute_fetch_hint([]) == ""
