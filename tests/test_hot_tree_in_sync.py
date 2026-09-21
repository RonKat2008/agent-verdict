"""Exercises scripts/sync_hot.sync() in isolation (temp dirs).

The invariant that the *committed* plugin/hooks/verdict_hot tree matches
src/agent_verdict/verdict_hot is enforced by `make check`'s
`diff -r plugin/hooks/verdict_hot src/agent_verdict/verdict_hot` step
(global-constraints.md), not here.
"""

from __future__ import annotations

import filecmp
from pathlib import Path

from sync_hot import sync


def test_sync_copies_py_files_verbatim(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "__init__.py").write_text("SCHEMA_V = 1\n", encoding="utf-8")
    (src / "paths.py").write_text("def f() -> None:\n    pass\n", encoding="utf-8")

    changed = sync(src, dst)

    assert set(changed) == {"__init__.py", "paths.py"}
    comparison = filecmp.dircmp(src, dst)
    assert not comparison.left_only
    assert not comparison.right_only
    assert not comparison.diff_files


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
