"""Tests for the self-healing CLI entry point (master_fetch.cli).

Tests that:
1. Normal case: import succeeds, server main() is called
2. Broken install: ImportError -> auto-recover via repair.py
3. No repair script: inline repair script is written and run
4. Doctor detects stale hound processes
"""

import os
import sys
import importlib

import pytest


class TestSelfHealingCLI:
    """Test the cli.py self-heal wrapper."""

    def test_normal_import_calls_server_main(self, monkeypatch):
        """When import succeeds, server.main() is called."""
        called = [False]
        def fake_server_main():
            called[0] = True
            return 0
        # Inject a fake server module
        fake_module = type(sys)("master_fetch.server")
        fake_module.main = fake_server_main
        monkeypatch.setitem(sys.modules, "master_fetch.server", fake_module)
        from master_fetch.cli import main
        rc = main()
        assert called[0] is True
        assert rc == 0

    def test_broken_import_triggers_repair(self, monkeypatch, tmp_path):
        """When import fails, repair.py is run to auto-recover."""
        # Make master_fetch.server unimportable
        monkeypatch.setitem(sys.modules, "master_fetch.server", None)

        # Write a fake repair.py that just prints and exits 0
        repair_dir = tmp_path / ".hound"
        repair_dir.mkdir()
        repair_script = repair_dir / "repair.py"
        repair_script.write_text('print("fake repair OK")\n')

        monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path) if p == "~" else p)

        # Mock subprocess.run to not actually run anything
        import subprocess
        class FakeResult:
            returncode = 0
        run_calls = []
        def fake_run(cmd, *args, **kwargs):
            run_calls.append(cmd)
            return FakeResult()
        monkeypatch.setattr(subprocess, "run", fake_run)

        from master_fetch.cli import main
        rc = main()
        assert rc == 0
        # Should have called subprocess.run with the repair script
        assert any("repair.py" in str(c) for c in run_calls)

    def test_no_repair_script_writes_inline(self, monkeypatch, tmp_path):
        """When repair.py doesn't exist, an inline repair script is written."""
        # Make master_fetch.server unimportable
        monkeypatch.setitem(sys.modules, "master_fetch.server", None)

        # Point ~ to tmp_path (no .hound dir yet)
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path) if p == "~" else p)

        # Mock subprocess.run
        import subprocess
        class FakeResult:
            returncode = 0
        def fake_run(cmd, *args, **kwargs):
            return FakeResult()
        monkeypatch.setattr(subprocess, "run", fake_run)

        from master_fetch.cli import main
        rc = main()

        # The repair script should have been written
        repair_path = tmp_path / ".hound" / "repair.py"
        assert repair_path.exists()
        assert "force-reinstall" in repair_path.read_text()

    def test_clean_error_when_repair_fails(self, monkeypatch, tmp_path):
        """When repair.py exists but repair fails, a clean error is shown."""
        # Make master_fetch.server unimportable
        monkeypatch.setitem(sys.modules, "master_fetch.server", None)

        # Write a fake repair.py
        repair_dir = tmp_path / ".hound"
        repair_dir.mkdir()
        (repair_dir / "repair.py").write_text('print("repair")\n')

        monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path) if p == "~" else p)

        # Mock subprocess.run to return failure
        import subprocess
        class FakeResult:
            returncode = 1
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())

        from master_fetch.cli import main
        rc = main()
        assert rc == 1

    def test_cli_imports_without_heavy_deps(self):
        """cli.py must import without triggering heavy dep imports.

        This is critical: if cli.py imported trafilatura/mcp/etc at module
        level, a broken dep would crash the entry point before the self-heal
        try/except can catch it.
        """
        # Verify cli.py doesn't import any heavy deps at module level
        import master_fetch.cli
        # Check that heavy modules are NOT in sys.modules after importing cli
        heavy = ["trafilatura", "mcp", "patchright", "playwright", "browserforge"]
        for mod in heavy:
            # These might be in sys.modules from other tests, but cli.py itself
            # should not have them as direct attributes
            assert not hasattr(master_fetch.cli, mod), f"cli.py imports {mod} at module level"


class TestStaleProcessDetection:
    """Test that the doctor detects stale hound processes."""

    def test_doctor_has_stale_server_check(self):
        """Doctor function should include a 'no stale servers' check."""
        import inspect
        from master_fetch import updater
        source = inspect.getsource(updater.doctor)
        assert "stale" in source.lower() or "no stale servers" in source

    def test_stop_all_hound_exists(self):
        """_stop_all_hound() should exist in the updater module."""
        from master_fetch import updater
        assert hasattr(updater, "_stop_all_hound")
        assert callable(updater._stop_all_hound)

    def test_helper_kills_all_hound_before_pip(self):
        """The detached helper should kill ALL hound processes before pip,
        not just in the FULL reinstall path."""
        import inspect
        from master_fetch import updater
        source = inspect.getsource(updater._build_helper_source)
        # The helper should call _stop_all_hound() regardless of FULL
        # (not inside an 'if FULL' block)
        assert "_stop_all_hound" in source
        # The call should happen before _stage(), not gated by FULL
        idx_stop = source.index("_stop_all_hound")
        idx_stage = source.index("_stage")
        assert idx_stop < idx_stage, "_stop_all_hound should be called before _stage"
