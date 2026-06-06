"""Tests for domain_intel.py — domain protection level tracking."""

import pytest
from master_fetch.domain_intel import (
    _extract_domain,
    guess_protection_level,
    get_domain_level,
    record_result,
    _KNOWN_SAFE_DOMAINS,
    _KNOWN_STEALTHY_DOMAINS,
    _KNOWN_DYNAMIC_DOMAINS,
)


class TestExtractDomain:
    """Domain extraction from URLs."""

    def test_simple_domain(self):
        assert _extract_domain("https://example.com/page") == "example.com"

    def test_subdomain(self):
        assert _extract_domain("https://sub.example.com/page") == "example.com"

    def test_multi_part_tld_uk(self):
        assert _extract_domain("https://bbc.co.uk/news") == "bbc.co.uk"

    def test_multi_part_tld_au(self):
        assert _extract_domain("https://example.com.au/page") == "example.com.au"

    def test_multi_part_tld_jp(self):
        assert _extract_domain("https://example.co.jp/page") == "example.co.jp"

    def test_no_protocol(self):
        # Bare hostname without protocol
        result = _extract_domain("example.com")
        assert result == "example.com"

    def test_with_port(self):
        assert _extract_domain("https://example.com:8080/page") == "example.com"


class TestGuessProtection:
    """Static protection level guessing from known domain lists."""

    def test_known_safe_return_none(self):
        assert guess_protection_level("https://github.com/user/repo") == "none"
        assert guess_protection_level("https://wikipedia.org/wiki/Python") == "none"

    def test_known_stealthy_return_high(self):
        assert guess_protection_level("https://cloudflare.com/") == "high"
        assert guess_protection_level("https://nowsecure.nl/") == "high"

    def test_known_dynamic_return_low(self):
        assert guess_protection_level("https://youtube.com/watch") == "low"
        assert guess_protection_level("https://reddit.com/r/python") == "low"

    def test_unknown_return_none(self):
        assert guess_protection_level("https://some-random-site.xyz/") == "none"


class TestDomainLevelDB:
    """Domain intelligence database operations."""

    @pytest.mark.asyncio
    async def test_unknown_domain_returns_none(self):
        level = await get_domain_level("https://completely-unknown-domain-12345.com/")
        assert level == "none"

    @pytest.mark.asyncio
    async def test_known_safe_overrides_db(self):
        # Even if we recorded it as high, github.com should return "none"
        await record_result("https://github.com/test", "high", True)
        level = await get_domain_level("https://github.com/user/repo")
        assert level == "none"

    @pytest.mark.asyncio
    async def test_record_and_retrieve(self):
        test_url = "https://learned-domain-test-999.com/page"
        await record_result(test_url, "low", True, 500)
        level = await get_domain_level(test_url)
        assert level == "low"

    @pytest.mark.asyncio
    async def test_failure_upgrades_level(self):
        test_url = "https://failure-upgrade-test-888.com/page"
        await record_result(test_url, "none", False, 1000)
        level = await get_domain_level(test_url)
        assert level == "low"  # Upgraded from none to low on failure

    @pytest.mark.asyncio
    async def test_second_failure_upgrades_to_high(self):
        test_url = "https://double-failure-test-777.com/page"
        await record_result(test_url, "low", False, 1000)
        level = await get_domain_level(test_url)
        assert level == "high"


class TestKnownDomainsIntegrity:
    """Ensure known domain lists are valid."""

    def test_safe_domains_are_lowercase(self):
        for d in _KNOWN_SAFE_DOMAINS:
            assert d == d.lower(), f"{d} is not lowercase"

    def test_stealthy_domains_are_lowercase(self):
        for d in _KNOWN_STEALTHY_DOMAINS:
            assert d == d.lower(), f"{d} is not lowercase"

    def test_dynamic_domains_are_lowercase(self):
        for d in _KNOWN_DYNAMIC_DOMAINS:
            assert d == d.lower(), f"{d} is not lowercase"

    def test_no_overlap_between_lists(self):
        safe = set(_KNOWN_SAFE_DOMAINS)
        stealthy = set(_KNOWN_STEALTHY_DOMAINS)
        dynamic = set(_KNOWN_DYNAMIC_DOMAINS)

        assert len(safe & stealthy) == 0, f"Overlap: {safe & stealthy}"
        assert len(safe & dynamic) == 0, f"Overlap: {safe & dynamic}"
        assert len(stealthy & dynamic) == 0, f"Overlap: {stealthy & dynamic}"
