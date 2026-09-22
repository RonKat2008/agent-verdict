"""plugin/hooks must run on stdlib-only Python 3.9 (global-constraints.md).

AST-walks every .py file under plugin/hooks/ and asserts every imported
top-level module is either a relative import or a name present in
sys.stdlib_module_names, and that no module file shadows a stdlib name.

D-028 (docs/DECISIONS.md): `dataclasses` is banned outright under
`plugin/hooks/` even though it's stdlib -- importing it costs ~8ms per
process on Python 3.9 (it pulls in `inspect`/`ast`/`dis`/`tokenize`).
Immutable value types there are `typing.NamedTuple` instead.

The stdlib set that matters is **Python 3.9's**, not the running
interpreter's (final review, minor): `sys.stdlib_module_names` on 3.12
contains `tomllib`, which does not exist on the 3.9 interpreter the hot
path actually runs on, so the check alone would pass a module that crashes
at import on macOS's bundled python3. `_NOT_ON_PY39` hardcodes that
difference.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOT_DIR = ROOT / "plugin" / "hooks"
_BANNED_MODULES = frozenset({"dataclasses"})
# Names the RUNNING interpreter reports as stdlib that the hot path must not
# import anyway: `tomllib` is 3.11+ and absent on 3.9 (global-constraints.md
# names it explicitly), and `graphlib`, though it landed in 3.9, is kept out
# of the hot tree so nothing there grows a dependency on a module the 3.9
# floor barely has. `zoneinfo` IS in 3.9 and stays allowed.
_NOT_ON_PY39 = frozenset({"tomllib", "graphlib"})


def _iter_py_files() -> list[Path]:
    return sorted(HOT_DIR.rglob("*.py"))


def _top_level_import_names(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import: `from . import ledger`
            if node.module:
                names.append(node.module.split(".")[0])
    return names


def test_hot_tree_has_python_files() -> None:
    assert _iter_py_files(), f"expected .py files under {HOT_DIR}"


def _is_allowed_import(name: str, path: Path) -> bool:
    if name in _NOT_ON_PY39 or name in _BANNED_MODULES:
        return False
    if name == "verdict_hot" and path.parent == HOT_DIR:
        return True
    return name in sys.stdlib_module_names


def test_every_import_under_plugin_hooks_is_stdlib_or_relative() -> None:
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name in _top_level_import_names(tree):
            assert _is_allowed_import(name, path), (
                f"{path}: import {name!r} is not stdlib-on-3.9 or relative"
            )


def test_modules_missing_from_python_39_are_rejected_even_when_running_newer() -> None:
    """`tomllib` is stdlib on the interpreter running these tests but not on
    3.9; the allow-list must not inherit the running interpreter's set."""
    probe = HOT_DIR / "verdict_hook.py"
    assert not _is_allowed_import("tomllib", probe)
    assert not _is_allowed_import("graphlib", probe)
    assert not _is_allowed_import("dataclasses", probe)
    assert _is_allowed_import("zoneinfo", probe), "zoneinfo is stdlib on 3.9"
    assert _is_allowed_import("json", probe)


def test_no_module_stem_under_plugin_hooks_shadows_a_stdlib_module() -> None:
    stdlib = set(sys.stdlib_module_names)
    for path in _iter_py_files():
        assert path.stem not in stdlib, f"{path}: file name shadows stdlib module {path.stem!r}"


def test_no_module_under_plugin_hooks_imports_dataclasses() -> None:
    """D-028: `dataclasses` costs ~8ms per process on Python 3.9 (it pulls in
    `inspect`/`ast`/`dis`/`tokenize`). Immutable value types under
    `plugin/hooks/` must be `typing.NamedTuple` instead."""
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name in _top_level_import_names(tree):
            assert name not in _BANNED_MODULES, f"{path}: banned import {name!r} (D-028)"
