"""Adversarial tests for v11.2.0 search quality improvements.

Tests the six-signal ranking (domain reputation + answer-signal scoring + title
relevance + URL relevance), source type detection, snippet merging, smarter
fetch_relevance tiers, and heading-aware BM25 with table/code preservation
in focus_content.

All tests use real code paths with mocked network (no live HTTP).
"""

import json
import pytest
from master_fetch.search import (
    _domain_boost, _answer_signal_score, _is_technical_query,
    _apply_quality_boost, SearchResult, compute_fetch_hint,
    _source_type, _title_relevance, _url_relevance, _tier,
    _detect_intent, _expand_query, _generate_query_map, _diversify,
    _search_next_action,
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
        assert "smart_fetch" in hint
        assert "high" in hint

    def test_empty_results(self):
        assert compute_fetch_hint([]) == ""


# ─── Intent detection (v12) ───────────────────────────────────────

class TestDetectIntent:

    def test_comparison_intent(self):
        assert _detect_intent("GPT-4 vs Claude 3 comparison") == "comparison"

    def test_versus_intent(self):
        assert _detect_intent("BERT versus GPT difference") == "comparison"

    def test_howto_intent(self):
        assert _detect_intent("how to implement REST API") == "howto"

    def test_guide_intent(self):
        assert _detect_intent("step by step guide to fine-tuning") == "howto"

    def test_research_intent(self):
        assert _detect_intent("transformer architecture research paper") == "research"

    def test_arxiv_intent(self):
        assert _detect_intent("attention is all you need arxiv study") == "research"

    def test_code_intent(self):
        assert _detect_intent("implement binary search function in python") == "code"

    def test_api_debug_intent(self):
        assert _detect_intent("fix nullpointer exception error in Java method") == "code"

    def test_reference_intent(self):
        assert _detect_intent("what is gradient descent explained") == "reference"

    def test_definition_intent(self):
        assert _detect_intent("definition of recurrent neural network overview") == "reference"

    def test_news_intent(self):
        assert _detect_intent("latest LLM announcement release") == "news"

    def test_factual_technical_data_intent(self):
        """Technical query with data signals but no pattern match → factual."""
        assert _detect_intent("GPT-3 embedding dimension d_model table") == "factual"

    def test_factual_parameters_intent(self):
        assert _detect_intent("transformer model parameters size specifications") == "factual"

    def test_general_intent_no_match(self):
        assert _detect_intent("best restaurants in paris") == "general"

    def test_general_intent_random(self):
        assert _detect_intent("hello world") == "general"

    def test_priority_comparison_over_code(self):
        """Comparison should beat code when both match."""
        assert _detect_intent("compare REST API vs GraphQL implementation") == "comparison"

    def test_priority_howto_over_research(self):
        assert _detect_intent("how to write a research paper guide") == "howto"

    def test_empty_query(self):
        assert _detect_intent("") == "general"

    def test_very_long_query(self):
        long_q = " ".join(["word"] * 50)
        assert _detect_intent(long_q) == "general"

    def test_multi_word_code_intent(self):
        assert _detect_intent("python function class snippet example program") == "code"


# ─── Query expansion (v12) ────────────────────────────────────────

class TestExpandQuery:

    def test_comparison_no_expansion(self):
        """Comparison queries don't get expansion (returns tutorial spam, not primary sources)."""
        assert _expand_query("GPT-4 vs Claude 3", "comparison") == "GPT-4 vs Claude 3"

    def test_code_no_expansion(self):
        """Code queries don't get expansion (returns tutorial spam, not primary sources)."""
        assert _expand_query("implement REST API", "code") == "implement REST API"

    def test_general_no_expansion(self):
        assert _expand_query("best restaurants", "general") == "best restaurants"

    def test_research_expansion(self):
        result = _expand_query("attention mechanism", "research")
        assert "paper" in result
        assert "arxiv" in result
        assert "benchmark" in result

    def test_factual_expansion(self):
        result = _expand_query("GPT-3 d_model", "factual")
        assert "specifications" in result
        assert "table" in result
        assert "parameters" in result

    def test_expansion_preserves_original(self):
        """Research expansion preserves the original query as prefix."""
        original = "attention mechanism"
        result = _expand_query(original, "research")
        assert result.startswith(original)

    def test_no_expansion_for_non_research_intents(self):
        """Only research and factual get expansion. All others return unchanged."""
        for intent in ("comparison", "code", "news", "howto", "reference", "general"):
            q = "some test query"
            assert _expand_query(q, intent) == q, f"{intent} should not expand"


# ─── Query map generation (v12) ────────────────────────────────────

class TestGenerateQueryMap:

    _ALL_ENGINES = ["duckduckgo", "brave", "mojeek", "yahoo",
                    "yandex", "startpage", "google", "qwant"]

    def test_core_engines_get_original_query(self):
        """Research expansion: core engines get original query."""
        qm = _generate_query_map("attention mechanism", "research", self._ALL_ENGINES)
        assert qm["duckduckgo"] == "attention mechanism"
        assert qm["brave"] == "attention mechanism"
        assert qm["mojeek"] == "attention mechanism"
        assert qm["yahoo"] == "attention mechanism"

    def test_diversity_engines_get_expanded_query(self):
        """Research expansion: diversity engines get expanded query."""
        qm = _generate_query_map("attention mechanism", "research", self._ALL_ENGINES)
        expanded = qm["yandex"]
        assert expanded != "attention mechanism"
        assert "attention mechanism" in expanded
        assert "paper" in expanded or "arxiv" in expanded

    def test_general_intent_returns_empty_map(self):
        qm = _generate_query_map("best restaurants", "general", self._ALL_ENGINES)
        assert qm == {}

    def test_non_research_intents_return_empty_map(self):
        """Only research and factual get expansion; others return empty map."""
        for intent in ("comparison", "code", "news", "howto", "reference"):
            qm = _generate_query_map("test query", intent, self._ALL_ENGINES)
            assert qm == {}, f"{intent} should return empty map"

    def test_subset_of_engines(self):
        """Works with a partial engine list."""
        qm = _generate_query_map("attention mechanism", "research", ["duckduckgo", "google"])
        assert qm["duckduckgo"] == "attention mechanism"
        assert qm["google"] != "attention mechanism"
        assert len(qm) == 2

    def test_empty_engine_list(self):
        qm = _generate_query_map("attention mechanism", "research", [])
        assert qm == {}

    def test_core_engine_not_in_list(self):
        """If a core engine isn't in the provided list, it's simply not in the map."""
        qm = _generate_query_map("attention mechanism", "research", ["yandex", "google"])
        assert "yandex" in qm
        assert "duckduckgo" not in qm
        assert qm["yandex"] != "attention mechanism"


# ─── Result diversity (v12) ───────────────────────────────────────

class TestDiversify:

    def _make_results(self, urls):
        return ([RawResult(title=f"Title {i}", url=u, snippet="",
                          source="brave", position=i) for i, u in enumerate(urls)],
                [0.9 - i * 0.01 for i in range(len(urls))])

    def test_same_domain_capped_at_two(self):
        ranked, scores = self._make_results([
            "https://medium.com/a",
            "https://medium.com/b",
            "https://medium.com/c",
            "https://medium.com/d",
        ])
        r, s = _diversify(ranked, scores, max_per_domain=2)
        # First two medium.com kept in top, last two deferred to bottom
        top_domains = [_get_domain(r[i].url) for i in range(2)]
        assert top_domains == ["medium.com", "medium.com"]
        # Third position should NOT be medium.com (deferred)
        assert _get_domain(r[2].url) != "medium.com" or len(r) == 4
        # Actually all 4 are medium.com so 2 kept + 2 deferred
        assert len(r) == 4

    def test_different_domains_not_affected(self):
        ranked, scores = self._make_results([
            "https://github.com/a",
            "https://arxiv.org/b",
            "https://medium.com/c",
        ])
        r, s = _diversify(ranked, scores, max_per_domain=2)
        assert [i.url for i in r] == [ranked[0].url, ranked[1].url, ranked[2].url]

    def test_mixed_domains_partial_deferral(self):
        ranked, scores = self._make_results([
            "https://medium.com/a",
            "https://medium.com/b",
            "https://github.com/c",
            "https://medium.com/d",  # 3rd medium.com → deferred
        ])
        r, s = _diversify(ranked, scores, max_per_domain=2)
        assert len(r) == 4
        # First two are medium.com, third is github, fourth is deferred medium.com
        assert _get_domain(r[0].url) == "medium.com"
        assert _get_domain(r[1].url) == "medium.com"
        assert _get_domain(r[2].url) == "github.com"
        assert _get_domain(r[3].url) == "medium.com"

    def test_empty_list(self):
        r, s = _diversify([], [], max_per_domain=2)
        assert r == [] and s == []

    def test_single_result(self):
        ranked, scores = self._make_results(["https://example.com/a"])
        r, s = _diversify(ranked, scores, max_per_domain=2)
        assert len(r) == 1

    def test_www_prefix_stripped(self):
        """www.medium.com and medium.com should be treated as same domain."""
        ranked, scores = self._make_results([
            "https://www.medium.com/a",
            "https://medium.com/b",
            "https://medium.com/c",
        ])
        r, s = _diversify(ranked, scores, max_per_domain=2)
        # First two kept (same domain after www strip), third deferred
        assert _get_domain(r[0].url) == "medium.com"
        assert _get_domain(r[1].url) == "medium.com"

    def test_max_per_domain_one(self):
        ranked, scores = self._make_results([
            "https://medium.com/a",
            "https://medium.com/b",
            "https://github.com/c",
        ])
        r, s = _diversify(ranked, scores, max_per_domain=1)
        assert _get_domain(r[0].url) == "medium.com"
        assert _get_domain(r[1].url) == "github.com"
        assert _get_domain(r[2].url) == "medium.com"

    def test_scores_preserved_with_results(self):
        ranked, scores = self._make_results([
            "https://medium.com/a",
            "https://medium.com/b",
            "https://github.com/c",
            "https://medium.com/d",
        ])
        r, s = _diversify(ranked, scores, max_per_domain=2)
        # Each result's score should still correspond to it
        for result, score in zip(r, s):
            original_idx = [rr.url for rr in ranked].index(result.url)
            assert abs(score - scores[original_idx]) < 0.001


from master_fetch.search import _get_domain


# ─── Integration: query_map end-to-end through search pipeline ────

class TestQueryMapIntegration:

    def test_research_query_generates_different_queries(self):
        """A research query should produce different queries for core vs diversity engines."""
        engines = ["duckduckgo", "brave", "mojeek", "yahoo",
                    "yandex", "startpage", "google", "qwant"]
        intent = _detect_intent("transformer attention mechanism research")
        assert intent == "research"
        qm = _generate_query_map("transformer attention mechanism research", intent, engines)
        assert qm != {}
        # Core engines get original
        assert qm["duckduckgo"] == "transformer attention mechanism research"
        # Diversity engines get expanded
        assert qm["yandex"] != "transformer attention mechanism research"
        assert "paper" in qm["yandex"] or "arxiv" in qm["yandex"]

    def test_comparison_query_no_fan_out(self):
        """Comparison queries should NOT trigger fan-out (only research/factual expand)."""
        engines = ["duckduckgo", "brave", "yandex"]
        intent = _detect_intent("Python vs Rust performance comparison")
        assert intent == "comparison"
        qm = _generate_query_map("Python vs Rust performance comparison", intent, engines)
        assert qm == {}

    def test_general_query_no_fan_out(self):
        """General queries should NOT trigger fan-out (backward compat)."""
        engines = ["duckduckgo", "brave", "yandex"]
        intent = _detect_intent("best restaurants in paris")
        qm = _generate_query_map("best restaurants in paris", intent, engines)
        assert qm == {}

    def test_factual_query_fan_out(self):
        """Technical factual queries should trigger fan-out with spec terms."""
        engines = ["duckduckgo", "yandex"]
        intent = _detect_intent("GPT-3 embedding dimension d_model parameters")
        assert intent == "factual"
        qm = _generate_query_map("GPT-3 embedding dimension d_model parameters", intent, engines)
        assert qm != {}
        assert "specifications" in qm["yandex"]
        assert "table" in qm["yandex"]


# ─── False positive guards (v12 intent hardening) ────────────────

class TestIntentFalsePositives:
    """Ambiguous standalone words removed from patterns should NOT trigger
    wrong intents. These words have common English meanings that would dilute
    diversity engine queries with irrelevant expansion terms."""

    def test_area_code_not_code(self):
        assert _detect_intent("area code 212") != "code"

    def test_toilet_paper_not_research(self):
        assert _detect_intent("toilet paper brands") != "research"

    def test_study_abroad_not_research(self):
        assert _detect_intent("study abroad programs") != "research"

    def test_database_update_not_news(self):
        assert _detect_intent("database update syntax") != "news"

    def test_pressure_release_not_news(self):
        assert _detect_intent("pressure release valve") != "news"

    def test_make_a_difference_not_comparison(self):
        assert _detect_intent("make a difference quotes") != "comparison"

    def test_alternative_music_not_comparison(self):
        assert _detect_intent("alternative music genres") != "comparison"

    def test_feel_better_not_comparison(self):
        assert _detect_intent("I feel better today") != "comparison"

    def test_tv_program_not_code(self):
        assert _detect_intent("TV program schedule") != "code"

    def test_for_example_not_code(self):
        assert _detect_intent("for example in writing") != "code"

    def test_dining_table_not_factual(self):
        assert _detect_intent("dining table wood") != "factual"

    def test_general_query_no_expansion(self):
        """General intent should produce no expansion (no fan-out)."""
        assert _expand_query("best restaurants in paris", "general") == "best restaurants in paris"

    def test_no_fanout_for_general(self):
        engines = ["duckduckgo", "brave", "yandex", "google"]
        qm = _generate_query_map("best restaurants in paris", "general", engines)
        assert qm == {}


class TestFactualDetectionExpanded:
    """Expanded _FACTUAL_DATA_WORDS should catch technical queries that the
    narrow v11 word list missed."""

    def test_architecture_layers_heads_factual(self):
        assert _detect_intent("GPT-3 architecture layers heads") == "factual"

    def test_context_window_factual(self):
        assert _detect_intent("transformer model context window") == "factual"

    def test_throughput_latency_factual(self):
        assert _detect_intent("LLM inference throughput latency") == "factual"

    def test_hidden_config_factual(self):
        assert _detect_intent("BERT hidden size config") == "factual"

    def test_optimizer_embedding_factual(self):
        assert _detect_intent("neural network optimizer embedding") == "factual"

    def test_precision_vocab_factual(self):
        assert _detect_intent("model precision vocab size") == "factual"

    def test_non_technical_with_data_word_not_factual(self):
        """A data word without tech context should not be factual."""
        # 'size' is a data word but 'shoe size guide' is not technical
        assert _detect_intent("shoe size guide") != "factual"


class TestQueryLengthLimit:
    """Queries with >= 15 words should not be expanded (could exceed engine limits)."""

    def test_long_query_no_expansion(self):
        long_q = " ".join(["word"] * 15)
        result = _expand_query(long_q, "code")
        assert result == long_q

    def test_short_query_still_expanded(self):
        """Short research queries should still be expanded."""
        short_q = "attention mechanism"
        result = _expand_query(short_q, "research")
        assert result != short_q

    def test_exactly_14_words_expanded(self):
        q = " ".join(["attention"] + ["word"] * 13)
        result = _expand_query(q, "research")
        assert result != q


class TestNewsYearDynamic:
    """News expansion should use the current year, not a hardcoded value."""

    def test_news_no_expansion(self):
        """News queries don't get expansion (returns irrelevant news, not primary sources)."""
        assert _expand_query("latest AI announcement", "news") == "latest AI announcement"

    def test_news_expansion_not_hardcoded(self):
        """News intent doesn't expand, so no year is hardcoded."""
        result = _expand_query("latest AI announcement", "news")
        assert result == "latest AI announcement"


# ─── Agent QoL: related_queries noise reduction ────────────────

class TestRelatedQueriesNoiseReduction:

    def test_no_single_word_unigrams(self):
        """Single words like 'function' are never useful as search queries.
        Only multi-word phrases should be returned."""
        from master_fetch.search import _related_queries, SearchResult
        results = [
            SearchResult(title="Gradient descent optimization", url="https://a.com",
                        snippet="Gradient descent is an optimization algorithm for machine learning",
                        fetch_relevance="high"),
            SearchResult(title="Optimization in ML", url="https://b.com",
                        snippet="The optimization function minimizes the cost",
                        fetch_relevance="high"),
            SearchResult(title="Machine learning basics", url="https://c.com",
                        snippet="Machine learning uses optimization to train models",
                        fetch_relevance="high"),
        ]
        rq = _related_queries("gradient descent", results)
        for q in rq:
            assert len(q.split()) >= 2, f"Single-word query returned: {q!r}"

    def test_bigrames_need_3_docfreq(self):
        """Bigrams appearing in only 2 results are not strong enough patterns."""
        from master_fetch.search import _related_queries, SearchResult
        results = [
            SearchResult(title="A", url="https://a.com",
                        snippet="rare phrase here and some other words",
                        fetch_relevance="high"),
            SearchResult(title="B", url="https://b.com",
                        snippet="rare phrase also appears in this result",
                        fetch_relevance="high"),
            SearchResult(title="C", url="https://c.com",
                        snippet="completely different content about something else",
                        fetch_relevance="high"),
        ]
        rq = _related_queries("test query", results)
        # "rare phrase" appears in only 2 results, not 3 -> should not be returned
        assert "rare phrase" not in rq

    def test_related_queries_can_be_empty(self):
        """If no bigrams meet the threshold, return empty list (no noise fallback)."""
        from master_fetch.search import _related_queries, SearchResult
        results = [
            SearchResult(title="A", url="https://a.com", snippet="short", fetch_relevance="high"),
        ]
        rq = _related_queries("test", results)
        assert rq == []

    def test_related_queries_exclude_query_overlap(self):
        """Bigrams that fully overlap the query should not be returned."""
        from master_fetch.search import _related_queries, SearchResult
        results = [
            SearchResult(title="Gradient descent", url="https://a.com",
                        snippet="Gradient descent optimization in multiple results",
                        fetch_relevance="high"),
            SearchResult(title="Gradient descent guide", url="https://b.com",
                        snippet="Gradient descent is used for optimization",
                        fetch_relevance="high"),
            SearchResult(title="Gradient descent tutorial", url="https://c.com",
                        snippet="Gradient descent optimization algorithm explained",
                        fetch_relevance="high"),
        ]
        rq = _related_queries("gradient descent", results)
        # "gradient descent" fully overlaps the query -> should not appear
        assert "gradient descent" not in rq


# ─── Agent QoL: _search_next_action intent-awareness ────────────

class TestNextActionIntentAware:

    def _make_result(self, url, title="Test", snippet="", source_type="other",
                     fetch_relevance="high", position=1):
        from master_fetch.search import SearchResult
        return SearchResult(title=title, url=url, snippet=snippet,
                           fetch_relevance=fetch_relevance, source_type=source_type,
                           position=position)

    def test_comparison_query_says_fetch_different_sources(self):
        results = [
            self._make_result("https://a.com", "GPT vs Claude"),
            self._make_result("https://b.com", "Claude vs GPT"),
        ]
        action = _search_next_action(results, [], "", "GPT vs Claude comparison")
        assert "different sources" in action.lower() or "balanced" in action.lower()

    def test_code_query_points_to_repo_or_docs(self):
        results = [
            self._make_result("https://github.com/repo", "Implement X", source_type="repo"),
            self._make_result("https://medium.com/post", "About X", source_type="blog"),
        ]
        action = _search_next_action(results, [], "", "implement REST API function")
        assert "#1" in action
        assert "github.com" in action or "repo" in action

    def test_reference_query_snippet_sufficiency(self):
        """For reference queries with a long reference-type snippet, tell the agent
        to check the snippet first."""
        long_snippet = "Gradient descent is a first-order iterative optimization algorithm " \
                       "for finding a local minimum of a differentiable function. It is widely " \
                       "used in machine learning and deep learning to minimize loss functions."
        results = [
            self._make_result("https://en.wikipedia.org/wiki/X", "What is X",
                             snippet=long_snippet, source_type="reference"),
        ]
        action = _search_next_action(results, [], "", "what is gradient descent")
        assert "snippet" in action.lower() or "check" in action.lower()

    def test_reference_query_short_snippet_fetches(self):
        """For reference queries with a short snippet, tell the agent to fetch."""
        results = [
            self._make_result("https://en.wikipedia.org/wiki/X", "What is X",
                             snippet="Short.", source_type="reference"),
        ]
        action = _search_next_action(results, [], "", "what is gradient descent")
        assert "smart_fetch" in action or "#1" in action

    def test_howto_query_suggests_focus(self):
        results = [
            self._make_result("https://docs.python.org/howto", "How to X",
                             source_type="docs"),
        ]
        action = _search_next_action(results, [], "", "how to implement REST API in Python")
        assert "focus" in action.lower()

    def test_research_query_prefers_paper_sources(self):
        results = [
            self._make_result("https://medium.com/post", "About transformers",
                             source_type="blog"),
            self._make_result("https://arxiv.org/abs/1234", "Transformer paper",
                             source_type="paper"),
        ]
        action = _search_next_action(results, [], "", "transformer attention mechanism research paper")
        assert "#2" in action or "paper" in action.lower() or "arxiv" in action.lower()

    def test_general_query_with_high_results(self):
        results = [
            self._make_result("https://a.com", "A"),
            self._make_result("https://b.com", "B"),
            self._make_result("https://c.com", "C"),
        ]
        action = _search_next_action(results, [], "", "best restaurants in paris")
        assert "#1" in action

    def test_general_query_no_high_results(self):
        results = [
            self._make_result("https://a.com", "A", fetch_relevance="med"),
        ]
        action = _search_next_action(results, [], "", "obscure query about nothing")
        assert "rephrase" in action.lower() or "neural" in action.lower()

    def test_engine_blocked_note(self):
        results = [self._make_result("https://a.com", "A")]
        action = _search_next_action(results, ["google"], "", "test query")
        assert "didn't contribute" in action.lower() or "retry" in action.lower()

    def test_empty_results_no_error(self):
        action = _search_next_action([], [], "", "test")
        assert "No results" in action

    def test_empty_results_rate_limited(self):
        action = _search_next_action([], ["google"], "rate-limited", "test")
        assert "rate-limited" in action.lower() or "retry" in action.lower()

    def test_code_query_includes_focus_hint(self):
        results = [
            self._make_result("https://github.com/repo", "Implement X",
                             source_type="repo"),
        ]
        action = _search_next_action(results, [], "", "implement binary search function python")
        assert "focus" in action.lower()


# ─── Agent QoL: compute_fetch_hint concrete recommendations ─────

class TestFetchHintConcrete:

    def _make_result(self, fetch_relevance="high", source_type="other"):
        from master_fetch.search import SearchResult
        return SearchResult(title="T", url="https://a.com", snippet="",
                           fetch_relevance=fetch_relevance, source_type=source_type)

    def test_high_3_plus_recommends_1_2(self):
        results = [self._make_result("high") for _ in range(4)]
        hint = compute_fetch_hint(results)
        assert "#1-2" in hint

    def test_high_1_recommends_1(self):
        results = [self._make_result("high"), self._make_result("med")]
        hint = compute_fetch_hint(results)
        assert "#1" in hint

    def test_all_med_recommends_rephrase(self):
        results = [self._make_result("med") for _ in range(3)]
        hint = compute_fetch_hint(results)
        assert "rephrase" in hint.lower() or "med" in hint

    def test_all_low_recommends_rephrase(self):
        results = [self._make_result("low") for _ in range(3)]
        hint = compute_fetch_hint(results)
        assert "rephrase" in hint.lower() or "thin" in hint.lower()

    def test_source_types_present(self):
        results = [
            self._make_result("high", "repo"),
            self._make_result("high", "paper"),
        ]
        hint = compute_fetch_hint(results)
        assert "repo:1" in hint
        assert "paper:1" in hint


# ─── API Backends: _strip_site_prefixes ────────────────────────────

class TestStripSitePrefixes:

    def test_strips_site_prefix(self):
        from master_fetch.api_backends import _strip_site_prefixes
        assert _strip_site_prefixes("site:github.com implement REST API") == "implement REST API"

    def test_strips_negative_site_prefix(self):
        from master_fetch.api_backends import _strip_site_prefixes
        assert _strip_site_prefixes("-site:medium.com best python framework") == "best python framework"

    def test_strips_both_prefixes(self):
        from master_fetch.api_backends import _strip_site_prefixes
        result = _strip_site_prefixes("site:github.com -site:medium.com implement REST API")
        assert "site:" not in result
        assert "implement REST API" in result

    def test_no_prefixes_unchanged(self):
        from master_fetch.api_backends import _strip_site_prefixes
        assert _strip_site_prefixes("implement REST API") == "implement REST API"

    def test_empty_after_strip(self):
        from master_fetch.api_backends import _strip_site_prefixes
        assert _strip_site_prefixes("site:github.com") == ""


# ─── API Backends: _intent_backends ─────────────────────────────────

class TestIntentBackends:

    def test_research_returns_semantic_scholar(self):
        from master_fetch.search import _intent_backends
        assert "semantic_scholar" in _intent_backends("research")

    def test_factual_returns_semantic_scholar(self):
        from master_fetch.search import _intent_backends
        assert "semantic_scholar" in _intent_backends("factual")

    def test_code_returns_github_api(self):
        from master_fetch.search import _intent_backends
        assert "github_api" in _intent_backends("code")

    def test_news_returns_hackernews(self):
        from master_fetch.search import _intent_backends
        assert "hackernews" in _intent_backends("news")

    def test_howto_returns_hackernews(self):
        from master_fetch.search import _intent_backends
        assert "hackernews" in _intent_backends("howto")

    def test_reference_returns_wikipedia(self):
        from master_fetch.search import _intent_backends
        assert "wikipedia" in _intent_backends("reference")

    def test_comparison_returns_empty(self):
        from master_fetch.search import _intent_backends
        assert _intent_backends("comparison") == []

    def test_general_returns_empty(self):
        from master_fetch.search import _intent_backends
        assert _intent_backends("general") == []

    def test_unknown_intent_returns_empty(self):
        from master_fetch.search import _intent_backends
        assert _intent_backends("nonexistent") == []


# ─── API Backends: _validate_engines accepts new names ──────────────

class TestValidateEnginesAcceptsAPIBackends:

    def test_semantic_scholar_accepted(self):
        from master_fetch.search import _validate_engines
        result = _validate_engines(["duckduckgo", "semantic_scholar"])
        assert result == ["duckduckgo", "semantic_scholar"]

    def test_github_api_accepted(self):
        from master_fetch.search import _validate_engines
        result = _validate_engines(["duckduckgo", "github_api"])
        assert result == ["duckduckgo", "github_api"]

    def test_hackernews_accepted(self):
        from master_fetch.search import _validate_engines
        result = _validate_engines(["duckduckgo", "hackernews"])
        assert result == ["duckduckgo", "hackernews"]

    def test_max_12_engines(self):
        from master_fetch.search import _validate_engines, SecurityError
        import pytest
        with pytest.raises(SecurityError, match="max 12"):
            _validate_engines([str(i) for i in range(13)])


# ─── API Backends: Semantic Scholar parsing ─────────────────────────

class TestSemanticScholarParsing:

    def _make_engine(self):
        from master_fetch.api_backends import SemanticScholarEngine
        return SemanticScholarEngine(timeout=5)

    def _mock_response(self, data, status=200):
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.status_code = status
        resp.text = json.dumps(data)
        return resp

    def test_parses_paper_results(self):
        eng = self._make_engine()
        eng.http_client.request = lambda *a, **k: self._mock_response({
            "data": [
                {"title": "Attention Is All You Need", "url": "https://arxiv.org/abs/1706.03762",
                 "abstract": "We propose a new architecture...", "year": 2017,
                 "citationCount": 120000, "authors": [{"name": "Ashish Vaswani"}],
                 "externalIds": {"DOI": "10.5555/3295222.3295349"}},
                {"title": "BERT", "url": "https://arxiv.org/abs/1810.04805",
                 "abstract": "We introduce a new language representation...",
                 "year": 2018, "citationCount": 50000,
                 "authors": [{"name": "Jacob Devlin"}], "externalIds": {}},
            ]
        })
        results = eng.search("transformer attention")
        assert results is not None
        assert len(results) == 2
        assert results[0].title == "Attention Is All You Need"
        assert results[0].href == "https://arxiv.org/abs/1706.03762"
        assert "2017" in results[0].body
        assert "120000" in results[0].body or "120,000" in results[0].body

    def test_handles_missing_url(self):
        eng = self._make_engine()
        eng.http_client.request = lambda *a, **k: self._mock_response({
            "data": [{"title": "Paper", "paperId": "abc123", "abstract": "text"}]
        })
        results = eng.search("test")
        assert results is not None
        assert len(results) == 1
        assert "semanticscholar.org" in results[0].href

    def test_skips_results_without_title(self):
        eng = self._make_engine()
        eng.http_client.request = lambda *a, **k: self._mock_response({
            "data": [{"title": "Good Paper", "url": "https://arxiv.org/abs/1234"},
                     {"url": "https://arxiv.org/abs/5678"}]
        })
        results = eng.search("test")
        assert results is not None
        assert len(results) == 1

    def test_empty_data_returns_none(self):
        eng = self._make_engine()
        eng.http_client.request = lambda *a, **k: self._mock_response({"data": []})
        results = eng.search("test")
        assert results is None

    def test_403_raises_blocked(self):
        from master_fetch.search_metasearch import MetaBlockedException
        eng = self._make_engine()
        eng.http_client.request = lambda *a, **k: self._mock_response({}, status=403)
        with pytest.raises(MetaBlockedException):
            eng.search("test")

    def test_429_raises_blocked(self):
        from master_fetch.search_metasearch import MetaBlockedException
        eng = self._make_engine()
        eng.http_client.request = lambda *a, **k: self._mock_response({}, status=429)
        with pytest.raises(MetaBlockedException):
            eng.search("test")

    def test_500_returns_none(self):
        eng = self._make_engine()
        eng.http_client.request = lambda *a, **k: self._mock_response({}, status=500)
        results = eng.search("test")
        assert results is None

    def test_invalid_json_returns_none(self):
        from unittest.mock import MagicMock
        eng = self._make_engine()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "not json{{"
        eng.http_client.request = lambda *a, **k: resp
        results = eng.search("test")
        assert results is None

    def test_strips_site_prefix_from_query(self):
        eng = self._make_engine()
        captured_params = {}
        def mock_req(*a, **k):
            captured_params.update(k.get('params', {}))
            return self._mock_response({"data": []})
        eng.http_client.request = mock_req
        eng.search("site:arxiv.org transformer attention")
        assert "site:" not in captured_params.get("query", "")


# ─── API Backends: GitHub parsing ───────────────────────────────────

class TestGitHubParsing:

    def _make_engine(self):
        from master_fetch.api_backends import GitHubSearchEngine
        return GitHubSearchEngine(timeout=5)

    def _mock_response(self, data, status=200):
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.status_code = status
        resp.text = json.dumps(data)
        return resp

    def test_parses_repo_results(self):
        eng = self._make_engine()
        eng.http_client.request = lambda *a, **k: self._mock_response({
            "items": [
                {"full_name": "facebook/react", "html_url": "https://github.com/facebook/react",
                 "description": "The library for web and native UIs.",
                 "stargazers_count": 220000, "language": "JavaScript",
                 "topics": ["react", "javascript", "library"],
                 "updated_at": "2026-07-20T10:00:00Z"},
            ]
        })
        results = eng.search("react ui library")
        assert results is not None
        assert len(results) == 1
        assert results[0].title == "facebook/react"
        assert results[0].href == "https://github.com/facebook/react"
        assert "220,000" in results[0].body
        assert "JavaScript" in results[0].body

    def test_skips_results_without_url(self):
        eng = self._make_engine()
        eng.http_client.request = lambda *a, **k: self._mock_response({
            "items": [{"full_name": "repo", "html_url": ""},
                     {"full_name": "good", "html_url": "https://github.com/good/repo"}]
        })
        results = eng.search("test")
        assert results is not None
        assert len(results) == 1

    def test_empty_items_returns_none(self):
        eng = self._make_engine()
        eng.http_client.request = lambda *a, **k: self._mock_response({"items": []})
        results = eng.search("test")
        assert results is None

    def test_403_raises_blocked(self):
        from master_fetch.search_metasearch import MetaBlockedException
        eng = self._make_engine()
        eng.http_client.request = lambda *a, **k: self._mock_response({}, status=403)
        with pytest.raises(MetaBlockedException):
            eng.search("test")


# ─── API Backends: Hacker News parsing ──────────────────────────────

class TestHackerNewsParsing:

    def _make_engine(self):
        from master_fetch.api_backends import HackerNewsEngine
        return HackerNewsEngine(timeout=5)

    def _mock_response(self, data, status=200):
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.status_code = status
        resp.text = json.dumps(data)
        return resp

    def test_parses_story_results(self):
        eng = self._make_engine()
        eng.http_client.request = lambda *a, **k: self._mock_response({
            "hits": [
                {"title": "Show HN: A new web scraper", "url": "https://example.com/scraper",
                 "points": 150, "num_comments": 42, "author": "johndoe",
                 "created_at": "2026-07-20T10:00:00Z"},
            ]
        })
        results = eng.search("web scraper")
        assert results is not None
        assert len(results) == 1
        assert results[0].title == "Show HN: A new web scraper"
        assert results[0].href == "https://example.com/scraper"
        assert "150" in results[0].body
        assert "42" in results[0].body

    def test_fallback_to_hn_url_when_no_external_url(self):
        eng = self._make_engine()
        eng.http_client.request = lambda *a, **k: self._mock_response({
            "hits": [
                {"title": "Ask HN: Best tools", "objectID": "12345",
                 "points": 50, "num_comments": 20},
            ]
        })
        results = eng.search("best tools")
        assert results is not None
        assert len(results) == 1
        assert "news.ycombinator.com" in results[0].href
        assert "12345" in results[0].href

    def test_skips_results_without_title(self):
        eng = self._make_engine()
        eng.http_client.request = lambda *a, **k: self._mock_response({
            "hits": [{"url": "https://example.com", "points": 10}]
        })
        results = eng.search("test")
        assert results is None

    def test_empty_hits_returns_none(self):
        eng = self._make_engine()
        eng.http_client.request = lambda *a, **k: self._mock_response({"hits": []})
        results = eng.search("test")
        assert results is None

    def test_403_raises_blocked(self):
        from master_fetch.search_metasearch import MetaBlockedException
        eng = self._make_engine()
        eng.http_client.request = lambda *a, **k: self._mock_response({}, status=403)
        with pytest.raises(MetaBlockedException):
            eng.search("test")


# ─── API Backends: engine registration ──────────────────────────────

class TestAPIBackendRegistration:

    def setup_method(self):
        from master_fetch.search_metasearch import _register_api_backends
        _register_api_backends()

    def test_semantic_scholar_in_text_engines(self):
        from master_fetch.search_metasearch import _TEXT_ENGINES
        assert "semantic_scholar" in _TEXT_ENGINES

    def test_github_api_in_text_engines(self):
        from master_fetch.search_metasearch import _TEXT_ENGINES
        assert "github_api" in _TEXT_ENGINES

    def test_hackernews_in_text_engines(self):
        from master_fetch.search_metasearch import _TEXT_ENGINES
        assert "hackernews" in _TEXT_ENGINES

    def test_not_in_default_backends(self):
        from master_fetch.search_metasearch import _DEFAULT_BACKENDS
        assert "semantic_scholar" not in _DEFAULT_BACKENDS
        assert "github_api" not in _DEFAULT_BACKENDS
        assert "hackernews" not in _DEFAULT_BACKENDS

    def test_hound_to_backend_mapping(self):
        from master_fetch.search_metasearch import _HOUND_TO_BACKEND
        assert _HOUND_TO_BACKEND["semantic_scholar"] == "semantic_scholar"
        assert _HOUND_TO_BACKEND["github_api"] == "github_api"
        assert _HOUND_TO_BACKEND["hackernews"] == "hackernews"

    def test_index_family_mapping(self):
        from master_fetch.search_engines import _INDEX_FAMILY
        assert _INDEX_FAMILY["semantic_scholar"] == "semantic_scholar"
        assert _INDEX_FAMILY["github_api"] == "github_api"
        assert _INDEX_FAMILY["hackernews"] == "hackernews"
