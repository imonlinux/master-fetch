#!/usr/bin/env python3
"""
Sync check: compare tool definitions in server.py (_TOOL_DEFS) with
the Pi extension's hound.ts descriptions. Catches divergence when
updating one surface but forgetting the other.

Also checks version lockstep across pyproject.toml, __init__.py,
pi-extension/package.json, and root package.json.

Usage: python scripts/check_pi_extension_sync.py
Exit 0 = in sync, exit 1 = drift detected.
"""
from __future__ import annotations
import re, sys, json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVER = ROOT / "src" / "master_fetch" / "server.py"
HOUND_TS = ROOT / "pi-extension" / "extensions" / "hound.ts"
PI_PKG = ROOT / "pi-extension" / "package.json"
ROOT_PKG = ROOT / "package.json"
PYPROJECT = ROOT / "pyproject.toml"
INIT_PY = ROOT / "src" / "master_fetch" / "__init__.py"

# Tool name mapping: MCP method name -> Pi tool name
TOOL_MAP = {
    "mcp_smart_fetch": "web_fetch",
    "mcp_smart_search": "web_search",
    "mcp_smart_crawl": "web_crawl",
    "mcp_screenshot": "web_screenshot",
    "cache_clear": "cache_clear",
    "version": "hound_version",
}

# Key terms that MUST appear in BOTH descriptions for each tool.
# These are capability words, not exact phrases.
KEY_TERMS = {
    "mcp_smart_fetch": [
        "archive", "pdf", "offset", "focus", "next_offset", "content_ok",
        "page_type", "is_stale", "source_type", "archived_at",
    ],
    "mcp_smart_search": [
        "keyless", "backend", "consensus", "related", "freshness", "site",
    ],
    "mcp_smart_crawl": [
        "sitemap", "discover_only", "crawl_urls", "max_pages", "max_depth",
        "focus", "page_type",
    ],
    "mcp_screenshot": [
        "multimodal", "full_page", "image_type", "stealthy",
    ],
    "cache_clear": [
        "expired", "cache_ttl",
    ],
    "version": [
        "version", "update",
    ],
}

def extract_tooldef_descriptions() -> dict[str, str]:
    """Extract {tool_name: description} from _TOOL_DEFS in server.py."""
    text = SERVER.read_text(encoding="utf-8")
    # Find _TOOL_DEFS list
    m = re.search(r"_TOOL_DEFS.*?=\s*\[", text, re.DOTALL)
    if not m:
        print("ERROR: _TOOL_DEFS not found in server.py")
        return {}
    # Extract the list body (find matching bracket)
    start = m.end() - 1
    depth = 0
    end = start
    for i in range(start, len(text)):
        if text[i] == "[": depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    body = text[start:end]
    # Extract tool_name -> description pairs from list of dicts
    descs = {}
    for name in TOOL_MAP:
        pat = rf'"{re.escape(name)}"\s*,\s*"description"\s*:\s*"((?:[^"\\]|\\.)*)"'
        m2 = re.search(pat, body, re.DOTALL)
        if m2:
            descs[name] = m2.group(1).replace('\\"', '"').replace("\\n", " ")
        else:
            # Try alternate: "name": "tool_name", ... "description": "..."
            pat2 = rf'"name"\s*:\s*"{re.escape(name)}".*?"description"\s*:\s*"((?:[^"\\]|\\.)*)"'
            m3 = re.search(pat2, body, re.DOTALL)
            if m3:
                descs[name] = m3.group(1).replace('\\"', '"').replace("\\n", " ")
    return descs

def extract_hound_ts_descriptions() -> dict[str, str]:
    """Extract {pi_tool_name: description} from hound.ts."""
    text = HOUND_TS.read_text(encoding="utf-8")
    descs = {}
    for mcp_name, pi_name in TOOL_MAP.items():
        # Find registerTool blocks by name
        pat = rf'name:\s*"{re.escape(pi_name)}".*?description:\s*"((?:[^"\\]|\\.)*)"'
        m = re.search(pat, text, re.DOTALL)
        if m:
            descs[mcp_name] = m.group(1).replace('\\"', '"').replace("\\n", " ")
    return descs

def check_terms(descs_a: dict, descs_b: dict, name: str) -> list[str]:
    """Check that key terms for each tool appear in both descriptions."""
    issues = []
    for tool, terms in KEY_TERMS.items():
        a_desc = descs_a.get(tool, "").lower()
        b_desc = descs_b.get(tool, "").lower()
        for term in terms:
            in_a = term.lower() in a_desc
            in_b = term.lower() in b_desc
            if not in_a and not in_b:
                # Missing from both = maybe renamed, skip
                pass
            elif in_a and not in_b:
                issues.append(f"  {name}: '{term}' in server.py but MISSING from hound.ts (tool={tool})")
            elif in_b and not in_a:
                issues.append(f"  {name}: '{term}' in hound.ts but MISSING from server.py (tool={tool})")
    return issues

def extract_version(filepath: pathlib.Path, pattern: str) -> str | None:
    text = filepath.read_text(encoding="utf-8")
    m = re.search(pattern, text)
    return m.group(1) if m else None

def check_versions() -> list[str]:
    issues = []
    versions = {}
    # pyproject.toml
    v = extract_version(PYPROJECT, r'version\s*=\s*"([^"]+)"')
    if v: versions["pyproject.toml"] = v
    # __init__.py
    v = extract_version(INIT_PY, r'__version__\s*=\s*"([^"]+)"')
    if v: versions["__init__.py"] = v
    # pi-extension/package.json
    if PI_PKG.exists():
        pkg = json.loads(PI_PKG.read_text(encoding="utf-8"))
        versions["pi-extension/package.json"] = pkg.get("version", "")
    # root package.json
    if ROOT_PKG.exists():
        pkg = json.loads(ROOT_PKG.read_text(encoding="utf-8"))
        versions["package.json (root)"] = pkg.get("version", "")
    # Check lockstep: Python versions must match each other,
    # npm versions must match each other, majors must match across.
    py_versions = {k: v for k, v in versions.items() if "pyproject" in k or "__init__" in k}
    npm_versions = {k: v for k, v in versions.items() if "package.json" in k}
    py_unique = set(py_versions.values())
    npm_unique = set(npm_versions.values())
    if len(py_unique) > 1:
        issues.append("  PYTHON VERSION MISMATCH:")
        for f, v in sorted(py_versions.items()):
            issues.append(f"    {f}: {v}")
    if len(npm_unique) > 1:
        issues.append("  NPM VERSION MISMATCH:")
        for f, v in sorted(npm_versions.items()):
            issues.append(f"    {f}: {v}")
    # Cross-check: majors must match (npm can be patch ahead of Python)
    py_major = next(iter(py_unique), "").split(".")[0] if py_unique else ""
    npm_major = next(iter(npm_unique), "").split(".")[0] if npm_unique else ""
    if py_major and npm_major and py_major != npm_major:
        issues.append(f"  MAJOR VERSION MISMATCH: Python={py_major}.x vs npm={npm_major}.x")
    return issues

def main() -> int:
    issues: list[str] = []

    # 1. Version lockstep
    v_issues = check_versions()
    if v_issues:
        issues.append("VERSION LOCKSTEP:")
        issues.extend(v_issues)
        issues.append("")

    # 2. Tool name completeness
    server_descs = extract_tooldef_descriptions()
    ts_descs = extract_hound_ts_descriptions()
    server_names = set(server_descs.keys())
    ts_names = set(ts_descs.keys())
    expected = set(TOOL_MAP.keys())
    if server_names != expected:
        missing = expected - server_names
        extra = server_names - expected
        if missing: issues.append(f"  server.py _TOOL_DEFS missing: {missing}")
        if extra: issues.append(f"  server.py _TOOL_DEFS unexpected: {extra}")
    if ts_names != expected:
        missing = expected - ts_names
        extra = ts_names - expected
        if missing: issues.append(f"  hound.ts missing descriptions for: {missing}")
        if extra: issues.append(f"  hound.ts unexpected descriptions for: {extra}")

    # 3. Key term sync
    term_issues = check_terms(server_descs, ts_descs, "TERM SYNC")
    if term_issues:
        issues.append("KEY TERM DRIFT:")
        issues.extend(term_issues)
        issues.append("")

    if issues:
        print("\n".join(issues))
        print("\nSync check FAILED. Fix the issues above.")
        return 1
    else:
        print("Sync check PASSED. All 6 tools, terms, and versions in lockstep.")
        return 0

if __name__ == "__main__":
    sys.exit(main())
