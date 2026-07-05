## Summary

<!-- What does this PR change and why? One or two sentences. -->

## Type of change

- [ ] Bug fix (non-breaking)
- [ ] New capability
- [ ] Reliability / hardening
- [ ] Docs / README / tool defs
- [ ] Breaking change (note the migration below)

## Checklist

- [ ] The change is functional, not a cosmetic re-label of an existing capability.
- [ ] `pytest tests/` passes locally (`pip install -e . --no-deps` first if you edited `src/`).
- [ ] New behavior has a test that would actually fail without the change.
- [ ] No new heavy module-level import in `server.py` (it stalls the MCP handshake; lazy-import at the call site instead).
- [ ] `CHANGELOG.md` updated under `## [Unreleased]` (or the relevant version).
- [ ] README / tool descriptions updated if the user-facing surface changed.
- [ ] Version stays in sync across `pyproject.toml`, `src/master_fetch/__init__.py`, `CHANGELOG.md`, and the git tag (if releasing).

## Notes for review

<!-- Anything reviewers should pay attention to: failure paths tested, perf
impact, token-cost change on the tool defs, etc. -->
