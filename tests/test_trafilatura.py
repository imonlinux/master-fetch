"""Tests for trafilatura_extractor.py — content extraction."""

import pytest
from unittest.mock import MagicMock, patch
from master_fetch.trafilatura_extractor import (
    _is_probably_binary,
    _get_html_from_page,
    _extract_html_title,
    extract_with_trafilatura,
)


class TestIsProbablyBinary:
    """Binary content detection."""

    def test_plain_text_not_binary(self):
        assert _is_probably_binary(b"Hello World") is False

    def test_html_not_binary(self):
        assert _is_probably_binary(b"<html><body>Test</body></html>") is False

    def test_null_bytes_binary(self):
        data = b"\x00" * 500 + b"hello"
        assert _is_probably_binary(data) is True

    def test_random_bytes_binary(self):
        # Half null bytes, half random — should be well under 10% printable
        data = b"\x00" * 512 + b"\xff" * 512
        assert _is_probably_binary(data) is True

    def test_empty_not_binary(self):
        assert _is_probably_binary(b"") is False


class TestExtractHtmlTitle:
    """HTML <title> tag extraction."""

    def test_extracts_title(self):
        html = "<html><head><title>My Page Title</title></head><body></body></html>"
        assert _extract_html_title(html) == "My Page Title"

    def test_no_title_returns_empty(self):
        html = "<html><head></head><body>No title here</body></html>"
        assert _extract_html_title(html) == ""

    def test_title_with_attributes(self):
        html = '<html><head><title id="main">Test</title></head></html>'
        assert _extract_html_title(html) == "Test"

    def test_regex_fallback(self):
        # Malformed HTML where lxml can't parse
        html = "<title>Fallback Title</title><body>Broken"
        result = _extract_html_title(html)
        assert "Fallback Title" in result

    def test_strips_whitespace(self):
        html = "<html><title>  Padded  </title></html>"
        assert _extract_html_title(html) == "Padded"


class TestGetHtmlFromPage:
    """HTML extraction from Scrapling response objects."""

    def test_extracts_from_body(self):
        page = MagicMock()
        page.body = b"<html><body>Test</body></html>"
        page.encoding = "utf-8"
        result = _get_html_from_page(page)
        assert "Test" in result

    def test_extracts_from_html_content(self):
        page = MagicMock()
        page.body = None
        page.html_content = "<html><body>Direct</body></html>"
        result = _get_html_from_page(page)
        assert "Direct" in result

    def test_no_html_returns_none(self):
        # Use spec=[] to prevent MagicMock from auto-creating attributes
        page = MagicMock(spec=[])
        page.body = None
        result = _get_html_from_page(page)
        assert result is None

    def test_handles_encoding(self):
        page = MagicMock()
        page.body = "café".encode("utf-8")
        page.encoding = "utf-8"
        result = _get_html_from_page(page)
        assert "café" in result


class TestExtractWithTrafilatura:
    """Full extraction pipeline tests (mocked)."""

    def test_binary_content_detected(self):
        """Binary content should return error message, not crash."""
        page = MagicMock()
        page.body = b"\x00" * 5000
        page.url = "https://example.com/file.pdf"

        with patch("master_fetch.trafilatura_extractor._fallback_extract") as mock_fallback:
            mock_fallback.return_value = ["fallback"]
            result = extract_with_trafilatura(page)
            assert len(result) == 1
            assert "Binary" in result[0] or "fallback" in result[0]

    def test_empty_content_fallback(self):
        """When Trafilatura returns empty, fall back to Scrapling."""
        page = MagicMock()
        page.body = b"<html><body>Minimal content</body></html>"
        page.url = "https://example.com"
        page.encoding = "utf-8"

        with patch("master_fetch.trafilatura_extractor._extract_type", return_value=None):
            with patch("master_fetch.trafilatura_extractor._fallback_extract") as mock_fallback:
                mock_fallback.return_value = ["scrapling output"]
                result = extract_with_trafilatura(page)
                assert result == ["scrapling output"]

    def test_exception_fallback(self):
        """When Trafilatura crashes, fall back to Scrapling."""
        page = MagicMock()
        page.body = b"<html><body>Content</body></html>"
        page.url = "https://example.com"
        page.encoding = "utf-8"

        with patch(
            "master_fetch.trafilatura_extractor._get_html_from_page",
            side_effect=RuntimeError("Boom"),
        ):
            with patch("master_fetch.trafilatura_extractor._fallback_extract") as mock_fallback:
                mock_fallback.return_value = ["recovered"]
                result = extract_with_trafilatura(page)
                assert result == ["recovered"]

    def test_css_selector_narrowing(self):
        """CSS selector should narrow HTML before extraction."""
        from lxml.etree import fromstring as lxml_fromstring, Element

        page = MagicMock()
        page.body = (
            b"<html><body>"
            b"<div class='content'>Hello</div>"
            b"<footer>bye</footer>"
            b"</body></html>"
        )
        page.url = "https://example.com"
        page.encoding = "utf-8"

        # Create a mock lxml element for css() to return
        mock_element = MagicMock()
        mock_element._root = lxml_fromstring("<div class='content'>Hello</div>")

        def mock_css(selector):
            return [mock_element]

        page.css = mock_css

        with patch("master_fetch.trafilatura_extractor._extract_type", return_value="Extracted"):
            result = extract_with_trafilatura(page, css_selector="div.content")
            assert result == ["Extracted"]
