"""Fix the two v10 tool-def bugs + trim duplication. Idempotent, asserts each
replacement landed. No em-dash/curly in any new text (verified after)."""
from pathlib import Path
import sys

p = Path("src/master_fetch/server.py")
s = p.read_text(encoding="utf-8")
orig = s
changes = []

def rep(old, new, label):
    global s
    assert old in s, f"ANCHOR NOT FOUND: {label}"
    assert s.count(old) == 1, f"ANCHOR NOT UNIQUE ({s.count(old)}): {label}"
    s = s.replace(old, new)
    changes.append(label)

# 1) Trim duplicated actions syntax (kept in the `actions` param desc).
rep(
    "actions=[{click:..},{scroll:N},{fill:{selector,text}},{press:Enter},{wait:ms},{wait_selector:..}] for load-more / search forms / pagination / infinite scroll (forces stealthy, bypasses cache).",
    "actions (see param) for load-more / search forms / pagination / infinite scroll (forces stealthy, bypasses cache).",
    "actions syntax trim",
)

# 2) Trim include_links verbosity (detail is in the options param desc).
rep(
    "include_links=true populates response.links with the page's outgoing links classified as citations (main-content references, the ones worth following) / navigation / external + a primary_source hint - use to follow a page's source chain in one step.",
    "include_links=true populates response.links with classified outgoing links (citations/navigation/external) + a primary_source hint.",
    "include_links verbosity trim",
)

# 3) ADD the v10 envelope signals + the archive dead-link recovery to the
#    CLIENT-VISIBLE description (this is the fix 10.2.1 should have been).
rep(
    "Response signals: content_ok (trust content only if true), next_action (do this next if non-empty), summary (one-line status), is_truncated+next_offset (more content available).",
    "Response signals to branch on: content_ok (trust content only if true), next_action (do this next if non-empty), summary (one-line status), is_truncated+next_offset (more content available), page_type (article/docs/list/forum/auth_wall/paywall/js_shell), content_age_days + is_stale, source_type + is_official (vendor/gov/edu/github vs SEO farm). Dead-link recovery: if the live site hard-blocks (404/bot-wall/auth), the page is auto-recovered from the Internet Archive's closest snapshot, honestly marked source='archive.org' + archived_at=<date>; set archive_fallback=false (in options) to opt out and get the raw failure.",
    "envelope + archive in client-visible description",
)

# 4) Document archive_fallback in the options inputSchema description (so it is
#    discoverable in the schema, not only in the prose description).
rep(
    "include_media (bool, default false: populate response.media with up to 20 page image URLs for multimodal agents), proxy (str|dict),",
    "include_media (bool, default false: populate response.media with up to 20 page image URLs for multimodal agents), archive_fallback (bool, default true: on a live hard-block, recover the page from the Internet Archive; false = return the raw failure), proxy (str|dict),",
    "archive_fallback in options schema desc",
)

# 5) WIRE archive_fallback through dispatch so the opt-out actually works.
rep(
    '"include_media", "include_links",\n            )',
    '"include_media", "include_links", "archive_fallback",\n            )',
    "archive_fallback wired through dispatch",
)

p.write_text(s, encoding="utf-8")

# Verify no em-dash / curly quotes in the whole file (public/user-facing content).
import unicodedata
bad = [(c, hex(ord(c)), unicodedata.name(c)) for c in set(s) if ord(c) in (0x2018,0x2019,0x201C,0x201D,0x2014,0x2013)]
print("em/en-dash + curly-quote check:", bad if bad else "CLEAN")
print(f"\napplied {len(changes)} edits:")
for c in changes:
    print("  -", c)
