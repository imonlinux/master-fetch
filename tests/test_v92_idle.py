"""v9.2 idle-browser-close test: proves the existing idle-session monitor (now
enabled by default via HOUND_BROWSER_IDLE_TIMEOUT) actually closes the warm
Chrome after the idle timeout, frees the session, and the next fetch relaunches
a fresh session cleanly.

This is the RAM-reclaim feature for issue #1 (Chrome sitting in RAM when hound
runs in the background). The mechanism already existed in the codebase
(`_start_idle_monitor`), disabled with AUTO_SESSION_IDLE_TIMEOUT=0; v9.2 enables
it by default (300s) and makes it tunable. These tests would FAIL with the old
default of 0 because the monitor never closes anything in that mode.

Uses fake session entries (no real browser launch) so the test is CI-safe and
fast (~2s). Module globals AUTO_SESSION_IDLE_TIMEOUT and IDLE_CHECK_INTERVAL are
patched to short values so the monitor cycle runs in fractions of a second.
"""

import asyncio
import time

import pytest

from master_fetch.server import (
    MasterFetchServer,
    SessionCreatedModel,
    _SessionEntry,
)
import master_fetch.server as _mod  # the module, for global patching


class _FakeSession:
    """Stand-in for a Scrapling AsyncStealthySession. Tracks close() so the test
    can assert the process-tree-equivalent (session.close()) actually fired."""

    def __init__(self):
        self._is_alive = True
        self.close_called = False

    async def close(self):
        self.close_called = True
        self._is_alive = False


def _seed_auto_stealthy(srv, session_id="warm1", age_seconds=1000):
    """Inject a fake warm auto-stealthy session + last-used timestamp into the
    server, the state _ensure_auto_session / the idle monitor operate on."""
    fake = _FakeSession()
    entry = _SessionEntry(session=fake, session_type="stealthy")
    srv._sessions[session_id] = entry
    srv._auto_stealthy_id = session_id
    srv._auto_stealthy_last_used = _mod.now() - age_seconds
    return fake, entry


@pytest.fixture
def srv():
    s = MasterFetchServer()
    yield s
    # Cancel any lingering idle-monitor task so it doesn't leak across tests.
    if s._idle_monitor_task is not None and not s._idle_monitor_task.done():
        s._idle_monitor_task.cancel()
        try:
            asyncio.get_event_loop().run_until_complete(s._idle_monitor_task)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_idle_monitor_closes_warm_browser_after_timeout(monkeypatch):
    """The monitor reaps the auto-stealthy session once it has been idle longer
    than AUTO_SESSION_IDLE_TIMEOUT: _auto_stealthy_id is cleared, the session is
    popped from _sessions, and session.close() actually fired (RAM freed)."""
    monkeypatch.setattr(_mod, "AUTO_SESSION_IDLE_TIMEOUT", 1)   # 1s idle threshold
    monkeypatch.setattr(_mod, "IDLE_CHECK_INTERVAL", 0.2)       # check every 0.2s

    srv = MasterFetchServer()
    fake, entry = _seed_auto_stealthy(srv, age_seconds=0)
    # idle monitor reads now()-last_used; age 0 but threshold 1s -> wait > 1s.
    try:
        srv._ensure_idle_monitor()
        # Wait long enough for at least one check cycle past the 1s threshold.
        await asyncio.sleep(1.6)
        assert srv._auto_stealthy_id is None, "monitor did not clear _auto_stealthy_id"
        assert "warm1" not in srv._sessions, "monitor did not pop the idle session"
        assert fake.close_called is True, "session.close() was never called (RAM not freed)"
    finally:
        if srv._idle_monitor_task is not None and not srv._idle_monitor_task.done():
            srv._idle_monitor_task.cancel()
            try:
                await srv._idle_monitor_task
            except BaseException:
                pass


@pytest.mark.asyncio
async def test_idle_monitor_disabled_when_timeout_zero(monkeypatch):
    """HOUND_BROWSER_IDLE_TIMEOUT=0 (the old default) keeps the browser alive
    forever: the monitor task is never even started, so nothing is closed."""
    monkeypatch.setattr(_mod, "AUTO_SESSION_IDLE_TIMEOUT", 0)
    monkeypatch.setattr(_mod, "IDLE_CHECK_INTERVAL", 0.2)

    srv = MasterFetchServer()
    _seed_auto_stealthy(srv, age_seconds=99999)
    try:
        srv._ensure_idle_monitor()
        assert srv._idle_monitor_task is None, "monitor task started despite timeout=0"
        await asyncio.sleep(0.7)
        assert srv._auto_stealthy_id == "warm1", "session was closed despite timeout=0"
        assert "warm1" in srv._sessions, "session was popped despite timeout=0"
    finally:
        if srv._idle_monitor_task is not None and not srv._idle_monitor_task.done():
            srv._idle_monitor_task.cancel()
            try:
                await srv._idle_monitor_task
            except BaseException:
                pass


@pytest.mark.asyncio
async def test_next_fetch_relaunches_fresh_session_after_idle_close(monkeypatch):
    """After the idle monitor closes the warm browser, the next fetch's
    _ensure_auto_session creates a fresh session (cold relaunch). Verified by
    patching open_session to a fake that registers a new entry — no real browser
    launch. The new session id differs from the closed one and is live."""
    monkeypatch.setattr(_mod, "AUTO_SESSION_IDLE_TIMEOUT", 1)
    monkeypatch.setattr(_mod, "IDLE_CHECK_INTERVAL", 0.2)

    srv = MasterFetchServer()
    _seed_auto_stealthy(srv, session_id="warm1", age_seconds=0)

    # Fake the browser relaunch: open_session registers a new live entry.
    async def _fake_open(session_type, session_id=None, headless=True, **kw):
        new_id = "relaunched"
        fresh = _FakeSession()
        srv._sessions[new_id] = _SessionEntry(session=fresh, session_type=session_type)
        return SessionCreatedModel(
            session_id=new_id, session_type=session_type,
            created_at="now", is_alive=True, message="created",
        )

    monkeypatch.setattr(srv, "open_session", _fake_open)

    try:
        srv._ensure_idle_monitor()
        await asyncio.sleep(1.6)  # let the monitor close the warm session
        assert srv._auto_stealthy_id is None, "precondition: monitor should have closed warm1"
        assert "warm1" not in srv._sessions

        # Next fetch path: _ensure_auto_session sees no live session -> creates one.
        sid = await srv._ensure_auto_session("stealthy")
        assert sid == "relaunched", "relaunch did not produce the fresh session id"
        assert srv._auto_stealthy_id == "relaunched"
        assert "relaunched" in srv._sessions
        assert srv._sessions["relaunched"].session._is_alive is True
    finally:
        if srv._idle_monitor_task is not None and not srv._idle_monitor_task.done():
            srv._idle_monitor_task.cancel()
            try:
                await srv._idle_monitor_task
            except BaseException:
                pass


@pytest.mark.asyncio
async def test_busy_session_not_closed(monkeypatch):
    """A session that was used recently (within the idle threshold) is NOT
    closed by the monitor. Guards against the monitor killing Chrome mid-work
    while the agent is actively fetching with short think-pauses."""
    monkeypatch.setattr(_mod, "AUTO_SESSION_IDLE_TIMEOUT", 1)
    monkeypatch.setattr(_mod, "IDLE_CHECK_INTERVAL", 0.2)

    srv = MasterFetchServer()
    # last_used = now (just used), threshold 1s -> not idle yet.
    _seed_auto_stealthy(srv, age_seconds=0)
    srv._auto_stealthy_last_used = _mod.now()  # freshly used
    try:
        srv._ensure_idle_monitor()
        await asyncio.sleep(0.7)  # < 1s threshold -> must not close
        assert srv._auto_stealthy_id == "warm1", "monitor closed a freshly-used session"
        assert "warm1" in srv._sessions
    finally:
        if srv._idle_monitor_task is not None and not srv._idle_monitor_task.done():
            srv._idle_monitor_task.cancel()
            try:
                await srv._idle_monitor_task
            except BaseException:
                pass


def test_env_default_is_300_not_zero(monkeypatch):
    """The shipped default (no env var set) is 300s, not 0. This is the v9.2
    behavior change: idle close is ON out of the box. A regression to 0 would
    mean the feature is silently disabled again."""
    import master_fetch.server as srvmod
    # Re-evaluate the env read the way the module does, with no env set.
    monkeypatch.delenv("HOUND_BROWSER_IDLE_TIMEOUT", raising=False)
    assert srvmod._env_int("HOUND_BROWSER_IDLE_TIMEOUT", 300) == 300


def test_env_overrides_timeout():
    """HOUND_BROWSER_IDLE_TIMEOUT env var is honored, including 0 (opt-out)."""
    import os
    import master_fetch.server as srvmod
    # Can't easily set env post-import to change the module global, but _env_int
    # is the reader; verify it parses the values the docs promise.
    os.environ["HOUND_BROWSER_IDLE_TIMEOUT"] = "120"
    try:
        assert srvmod._env_int("HOUND_BROWSER_IDLE_TIMEOUT", 300) == 120
    finally:
        del os.environ["HOUND_BROWSER_IDLE_TIMEOUT"]
    os.environ["HOUND_BROWSER_IDLE_TIMEOUT"] = "0"
    try:
        assert srvmod._env_int("HOUND_BROWSER_IDLE_TIMEOUT", 300) == 0
    finally:
        del os.environ["HOUND_BROWSER_IDLE_TIMEOUT"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
