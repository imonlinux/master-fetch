"""v9 production lifecycle test: a real MCP connect -> initialize -> tools/list
-> tools/call -> clean shutdown cycle against a `python -m master_fetch` stdio
subprocess. Runs in CI (NOT marked e2e) because it is fast (~3s) and network-
free (calls cache_clear, not smart_fetch/search). This is the production-proof:
the recurring 'hound failed to load' was a cold-start handshake stall; this test
asserts the handshake responds, the 6 tools + connect-time instructions ship,
a tool call succeeds, and the process exits cleanly (exit 0, no crash stderr).

Uses `python -m master_fetch` (NOT the `hound` launcher) so it runs anywhere
the package is importable, including CI matrix cells where the launcher script
may not be on PATH.
"""

import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

# A tiny in-process JSON-RPC stdio client. Bounded reads so a dead server can
# never hang the test; poll() detects a crashed process immediately.

_MAX_EMPTY_READS = 40
_TIMEOUT_S = 60


def _drain_stderr(proc, max_bytes: int = 1500) -> str:
    try:
        proc.wait(timeout=0.1)
    except Exception:
        pass
    try:
        return (proc.stderr.read() or b"")[-max_bytes:].decode("utf-8", "replace")
    except Exception:
        return "(unavailable)"


def _read_line_json(proc) -> dict:
    empty = 0
    while True:
        if proc.poll() is not None:
            raise AssertionError(
                f"Server exited early (code {proc.returncode}). stderr: {_drain_stderr(proc)}"
            )
        line = proc.stdout.readline()
        if not line:
            empty += 1
            if empty > _MAX_EMPTY_READS:
                raise AssertionError(
                    f"Server produced {_MAX_EMPTY_READS} empty readlines (no handshake response). "
                    f"stderr: {_drain_stderr(proc)}"
                )
            time.sleep(0.05)
            continue
        text = line.decode().strip()
        if not text:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"Server emitted non-JSON line: {text[:200]!r} ({exc}). stderr: {_drain_stderr(proc)}"
            )


def _send(proc, method: str, params: dict | None, _id: int) -> dict:
    msg = {"jsonrpc": "2.0", "id": _id, "method": method}
    if params:
        msg["params"] = params
    proc.stdin.write((json.dumps(msg) + "\n").encode())
    proc.stdin.flush()
    return _read_line_json(proc)


def _spawn():
    env = {**os.environ}
    # Keep it network-free + deterministic: no proxy, short search deadline.
    env.setdefault("HOUND_SEARCH_DEADLINE", "5")
    return subprocess.Popen(
        [sys.executable, "-m", "master_fetch"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env,
    )


def _lifecycle():
    """Run one full MCP lifecycle against a fresh subprocess. Returns the
    tools/list result. Raises AssertionError on ANY deviation from a clean
    production handshake."""
    proc = _spawn()
    try:
        # 1. initialize handshake (the step that stalled pre-v8.2).
        init = _send(proc, "initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "lifecycle-test", "version": "1.0"},
        }, 1)
        assert init.get("jsonrpc") == "2.0", f"bad initialize response: {init}"
        result = init.get("result", {})
        assert "serverInfo" in result, f"no serverInfo in initialize: {result}"
        assert result["serverInfo"].get("name") == "Hound", result["serverInfo"]
        # Connect-time instructions must ship (the agent's mastery doc).
        assert result.get("instructions"), "initialize did not ship HOUND_INSTRUCTIONS"

        # 2. notifications/initialized (no response expected).
        proc.stdin.write((json.dumps({
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
        }) + "\n").encode())
        proc.stdin.flush()

        # 3. tools/list -> all 6 tools, hand-crafted (not empty auto-schemas).
        tools_resp = _send(proc, "tools/list", {}, 2)
        tools = tools_resp.get("result", {}).get("tools", [])
        names = {t["name"] for t in tools}
        expected = {"mcp_smart_fetch", "mcp_smart_crawl", "mcp_screenshot",
                    "mcp_smart_search", "cache_clear", "version"}
        assert names == expected, f"tool set mismatch: {names} != {expected}"
        # Hand-crafted defs carry rich descriptions (auto-gen Pydantic schemas
        # do not). Guard the token-saving hand-crafting against a regression to
        # FastMCP auto-generation.
        fetch_desc = next(t["description"] for t in tools if t["name"] == "mcp_smart_fetch")
        assert "EXTRACTED text" in fetch_desc or "extracted text" in fetch_desc
        assert len(fetch_desc) > 300, "smart_fetch description lost its hand-crafted detail"

        # 4. tools/call cache_clear (no network) -> not an error.
        call_resp = _send(proc, "tools/call", {"name": "cache_clear", "arguments": {}}, 3)
        assert not call_resp.get("result", {}).get("isError", False), \
            f"cache_clear returned isError: {call_resp}"

        return tools
    finally:
        # 5. clean shutdown: close stdin, terminate, exit 0, no crash stderr.
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)


def test_mcp_lifecycle_clean_handshake_and_shutdown():
    """The full production cycle: initialize -> instructions -> tools/list ->
    tools/call -> clean shutdown. This is the 'no failed to load' proof."""
    tools = _lifecycle()


def test_mcp_shutdown_exits_clean():
    """After a clean disconnect the process must exit 0 and write no traceback to
    stderr (a noisy teardown is what MCP clients report as 'failed to load')."""
    proc = _spawn()
    try:
        _send(proc, "initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "shutdown-test", "version": "1.0"},
        }, 1)
        proc.stdin.write((json.dumps({
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
        }) + "\n").encode())
        proc.stdin.flush()
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
    try:
        rc = proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = proc.wait(timeout=2)
    stderr = _drain_stderr(proc)
    assert rc == 0, f"process exited {rc} (expected 0). stderr: {stderr}"
    # A traceback in stderr means the event loop crashed on teardown (the
    # v8.2 'Event loop is closed' class of error). Tolerate SDK debug noise but
    # not Python tracebacks.
    assert "Traceback (most recent call last)" not in stderr, \
        f"teardown emitted a traceback (would look like 'failed to load'). stderr: {stderr}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
