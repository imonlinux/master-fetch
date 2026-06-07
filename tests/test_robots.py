"""Tests for robots.py — robots.txt compliance."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from master_fetch.robots import (
    _extract_netloc,
    is_allowed,
    clear_robots_cache,
)


class TestExtractNetloc:
    """Domain extraction from URL for robots.txt lookup."""

    def test_standard_url(self):
        assert _extract_netloc("https://example.com/page") == "example.com"

    def test_url_with_port(self):
        assert _extract_netloc("https://example.com:8080/page") == "example.com:8080"

    def test_url_with_subdomain(self):
        assert _extract_netloc("https://sub.example.com/page") == "sub.example.com"

    def test_malformed_url(self):
        assert _extract_netloc("not a url") == ""

    def test_empty_string(self):
        assert _extract_netloc("") == ""


class TestIsAllowed:
    """Robots.txt compliance checks."""

    @pytest_asyncio.fixture(autouse=True)
    async def _clear_cache(self):
        """Clear robots cache before each test to prevent cross-test pollution."""
        await clear_robots_cache()
        yield
        await clear_robots_cache()

    @pytest.mark.asyncio
    async def test_malformed_url_allowed(self):
        """Malformed URLs should be allowed (fail open)."""
        result = await is_allowed("not a url at all")
        assert result is True

    @pytest.mark.asyncio
    async def test_empty_url_allowed(self):
        result = await is_allowed("")
        assert result is True

    @pytest.mark.asyncio
    async def test_unreachable_robots_allowed(self):
        """When robots.txt is unreachable, allow by default."""
        with patch("master_fetch.robots._fetch_robots_txt", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = None
            result = await is_allowed("https://unreachable-test-12345.com/page")
            assert result is True

    @pytest.mark.asyncio
    async def test_allowing_robots_txt(self):
        """Robots.txt that allows everything."""
        robots_txt = "User-agent: *\nDisallow:\n"
        with patch("master_fetch.robots._fetch_robots_txt", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = robots_txt
            result = await is_allowed("https://allow-all-test.com/anything")
            assert result is True

    @pytest.mark.asyncio
    async def test_disallowing_robots_txt(self):
        """Robots.txt that disallows specific path."""
        robots_txt = "User-agent: *\nDisallow: /admin/\n"
        with patch("master_fetch.robots._fetch_robots_txt", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = robots_txt
            result = await is_allowed("https://disallow-test.com/admin/secret")
            assert result is False

    @pytest.mark.asyncio
    async def test_allowed_path_in_disallowing_robots(self):
        """Robots.txt disallows /admin/ but allows other paths."""
        robots_txt = "User-agent: *\nDisallow: /admin/\n"
        with patch("master_fetch.robots._fetch_robots_txt", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = robots_txt
            result = await is_allowed("https://disallow-test-2.com/public/page")
            assert result is True

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """Second call should use cache, not re-fetch."""
        robots_txt = "User-agent: *\nDisallow:\n"
        with patch("master_fetch.robots._fetch_robots_txt", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = robots_txt
            # First call: fetches
            await is_allowed("https://cache-hit-test.com/page1")
            # Second call: cached, no new fetch
            await is_allowed("https://cache-hit-test.com/page2")
            assert mock_fetch.call_count == 1

    @pytest.mark.asyncio
    async def test_parse_error_allowed(self):
        """If robots.txt parsing fails, allow by default (fail open)."""
        with patch("master_fetch.robots._fetch_robots_txt", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = "\x00\x01\x02"
            result = await is_allowed("https://parse-error-test.com/page")
            assert isinstance(result, bool)


class TestClearRobotsCache:
    """Cache clearing."""

    @pytest_asyncio.fixture(autouse=True)
    async def _clear_cache(self):
        await clear_robots_cache()
        yield
        await clear_robots_cache()

    @pytest.mark.asyncio
    async def test_clear_removes_cached_entries(self):
        robots_txt = "User-agent: *\nDisallow:\n"
        with patch("master_fetch.robots._fetch_robots_txt", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = robots_txt
            # Populate cache
            await is_allowed("https://clear-cache-test.com/page1")
            # Clear
            await clear_robots_cache()
            # Should re-fetch
            await is_allowed("https://clear-cache-test.com/page2")
            assert mock_fetch.call_count == 2
