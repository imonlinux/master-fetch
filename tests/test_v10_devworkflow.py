"""v10 dev-workflow pin: pytest must import master_fetch from src/, not a built
wheel in .venv.

The dev venv ships a BUILT wheel (not an editable install). Before
``pythonpath = ["src"]`` in pyproject, pytest silently ran the stale installed
copy and src/ edits went untested (bit the project 4+ times). This test fails
loud if that regression returns.
"""
from pathlib import Path

import master_fetch


def test_imports_from_src():
    """master_fetch.__file__ must resolve under the project's src/ tree."""
    f = Path(master_fetch.__file__).resolve()
    # Walk up to find the repo root (the dir containing pyproject.toml + src/).
    for parent in f.parents:
        if (parent / "pyproject.toml").exists() and (parent / "src").is_dir():
            assert f.is_relative_to(parent / "src"), (
                f"master_fetch imported from {f}, not under {parent / 'src'}. "
                "The dev venv is serving a stale built wheel. Check "
                "[tool.pytest.ini_options] pythonpath in pyproject.toml."
            )
            return
    # Not under any src/ we can find — fail explicitly.
    raise AssertionError(f"could not locate src/ above {f}")
