"""plugin/hooks must run on stdlib-only Python 3.9 (global-constraints.md).

AST-walks every .py file under plugin/hooks/ and asserts every imported
top-level module is either a relative import or a name present in
sys.stdlib_module_names, and that no module file shadows a stdlib name.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOT_DIR = ROOT / "plugin" / "hooks"


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


def test_every_import_under_plugin_hooks_is_stdlib_or_relative() -> None:
    stdlib = set(sys.stdlib_module_names)
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name in _top_level_import_names(tree):
            assert name in stdlib, f"{path}: non-stdlib top-level import {name!r}"


def test_no_module_stem_under_plugin_hooks_shadows_a_stdlib_module() -> None:
    stdlib = set(sys.stdlib_module_names)
    for path in _iter_py_files():
        assert path.stem not in stdlib, f"{path}: file name shadows stdlib module {path.stem!r}"
