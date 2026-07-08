"""Live integration check for the v9.2 idle-browser-close feature.

Real browser launch + real fetch + real idle reap + real relaunch, verified by
counting Chrome processes. NOT a pytest test (it takes ~40s and launches a real
browser); run it directly. Exits non-zero on any assertion failure.

Flow:
  1. smart_fetch example.com -> Chrome launches, content_ok=True
  2. wait -> idle monitor reaps Chrome (HOUND_BROWSER_IDLE_TIMEOUT shortened)
  3. assert Chrome process count dropped to 0 (RAM actually freed)
  4. smart_fetch again -> Chrome relaunches (cold-start path works)
  5. assert Chrome process count > 0 again
"""

import asyncio
import os
import sys
import time

# Must set the env BEFORE importing server (it reads the env at import time).
os.environ["HOUND_BROWSER_IDLE_TIMEOUT"] = "15"

import master_fetch.server as srvmod
# Shorten the check interval so the test runs in ~30s instead of ~80s.
srvmod.IDLE_CHECK_INTERVAL = 8

import subprocess


def chrome_count() -> int:
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process chrome -ErrorAction SilentlyContinue | Measure-Object).Count"],
            stderr=subprocess.DEVNULL,
        )
        return int(out.strip() or 0)
    except Exception:
        return -1


async def main():
    srv = srvmod.MasterFetchServer()
    print(f"[1] config: idle_timeout={srvmod.AUTO_SESSION_IDLE_TIMEOUT}s "
          f"check_interval={srvmod.IDLE_CHECK_INTERVAL}s")

    # 1. First fetch -> browser launches.
    print("[2] fetching example.com (forces browser launch)...")
    res = await srv.smart_fetch(
        "https://example.com", cache_ttl=0, force_fetcher="stealthy", timeout=30000,
    )
    ok = getattr(res, "content_ok", None)
    print(f"    fetcher={res.fetcher_used} content_ok={ok} "
          f"chars={getattr(res, 'total_size_bytes', 0)}")
    if ok is False:
        print("    FAIL: first fetch did not return content_ok=True")
        return 1
    # give the browser a moment to settle
    await asyncio.sleep(1)
    n_after = chrome_count()
    print(f"[3] chrome procs after fetch: {n_after} (expect > 0)")
    if n_after <= 0:
        print("    FAIL: browser did not launch on first fetch")
        return 1

    # 2. Wait for the idle monitor to reap it (15s timeout, 8s interval -> by ~23s).
    print(f"[4] waiting for idle monitor to reap (sleeping 30s)...")
    await asyncio.sleep(30)
    n_idle = chrome_count()
    print(f"[5] chrome procs after idle: {n_idle} (expect 0, RAM freed)")
    if n_idle != 0:
        print("    FAIL: idle monitor did NOT close the browser (RAM not freed)")
        return 1

    # 3. Second fetch -> cold relaunch.
    print("[6] fetching again (cold relaunch)...")
    res2 = await srv.smart_fetch(
        "https://example.com", cache_ttl=0, force_fetcher="stealthy", timeout=30000,
    )
    ok2 = getattr(res2, "content_ok", None)
    print(f"    fetcher={res2.fetcher_used} content_ok={ok2}")
    if ok2 is False:
        print("    FAIL: relaunch fetch did not return content_ok=True")
        return 1
    await asyncio.sleep(1)
    n_relaunch = chrome_count()
    print(f"[7] chrome procs after relaunch: {n_relaunch} (expect > 0)")
    if n_relaunch <= 0:
        print("    FAIL: browser did not relaunch after idle close")
        return 1

    # 4. Clean shutdown.
    try:
        await srv._shutdown_close_sessions()
    except BaseException as e:
        print(f"    (shutdown warning: {e})")
    print("\nPASS: idle close reaps Chrome (RAM freed) and next fetch relaunches cleanly.")
    return 0


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
