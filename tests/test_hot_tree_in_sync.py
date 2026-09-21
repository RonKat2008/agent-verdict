"""Exercises scripts/sync_hot.sync() in isolation (temp dirs).

The invariant that the *committed* plugin/hooks/verdict_hot tree matches
src/agent_verdict/verdict_hot is enforced by `make check`'s
`diff -r --exclude=default_policy.json plugin/hooks/verdict_hot
src/agent_verdict/verdict_hot` step (global-constraints.md, task-3-brief),
not here. `default_policy.json` (task 3's `EXTRA_FILES` entry) is excluded
from that `diff -r` and compared separately, by a plain `diff` against
plugin/policies/default.json, since it deliberately has no counterpart
under plugin/hooks/verdict_hot/ -- see Makefile's `check` target.

`sync()` always applies the real (module-level) `EXTRA_FILES`, even when
called with temp `src`/`dst` dirs in these tests, since that tuple isn't
parameterized. `_EXTRA_NAMES` accounts for it so the temp-dir assertions
don't false-positive on a file that isn't part of the temp `src` tree.
"""

from __future__ import annotations

import filecmp
from pathlib import Path

from sync_hot import EXTRA_FILES, sync

_EXTRA_NAMES = {rel for _src, rel in EXTRA_FILES}


def test_sync_copies_py_files_verbatim(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "__init__.py").write_text("SCHEMA_V = 1\n", encoding="utf-8")
    (src / "paths.py").write_text("def f() -> None:\n    pass\n", encoding="utf-8")

    changed = sync(src, dst)

    assert set(changed) == {"__init__.py", "paths.py"} | _EXTRA_NAMES
    comparison = filecmp.dircmp(src, dst)
    assert not comparison.left_only
    # EXTRA_FILES entries land in dst without a source-tree counterpart by
    # design (module docstring); every other unexpected dst-only file is
    # still a real bug.
    assert set(comparison.right_only) == _EXTRA_NAMES
    assert not comparison.diff_files


def test_sync_copies_extra_files_to_their_mapped_relative_path(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "a.py").write_text("x = 1\n", encoding="utf-8")

    sync(src, dst)

    for extra_src, extra_rel in EXTRA_FILES:
        copied = dst / extra_rel
        assert copied.exists(), f"expected {extra_rel!r} to be copied into dst"
        assert copied.read_text(encoding="utf-8") == extra_src.read_text(encoding="utf-8")


def test_sync_is_a_noop_when_already_in_sync(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "a.py").write_text("x = 1\n", encoding="utf-8")
    sync(src, dst)

    changed = sync(src, dst)

    assert changed == []


def test_sync_removes_a_stale_file_in_dst(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "a.py").write_text("x = 1\n", encoding="utf-8")
    dst.mkdir()
    (dst / "stale.py").write_text("y = 2\n", encoding="utf-8")

    changed = sync(src, dst)

    assert "stale.py" in changed
    assert not (dst / "stale.py").exists()
    assert (dst / "a.py").exists()


def test_sync_overwrites_a_changed_file(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "a.py").write_text("x = 1\n", encoding="utf-8")
    sync(src, dst)
    (src / "a.py").write_text("x = 2\n", encoding="utf-8")

    changed = sync(src, dst)

    assert changed == ["a.py"]
    assert (dst / "a.py").read_text(encoding="utf-8") == "x = 2\n"
