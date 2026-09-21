"""Copy plugin/hooks/verdict_hot verbatim to src/agent_verdict/verdict_hot.

plugin/hooks/verdict_hot/ is the only place hot-path code is edited
(global-constraints.md). `make check` fails when `diff -r` on the two trees
finds a difference, so this script is the only way the CLI-importable copy
under src/ should ever change.
"""

from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

# Task 3 adds a policy JSON that must ride along with the hot tree even though
# it does not live under plugin/hooks/verdict_hot/. Each entry is
# (absolute source path, path relative to `dst`).
#
# policy.load_policy() looks for the packaged default at
# `Path(__file__).parent / "default_policy.json"` first (the CLI/src layout);
# this is the file that makes that branch true once synced. The plugin
# layout falls back to plugin/policies/default.json directly, so that file
# is never duplicated under plugin/hooks/verdict_hot/ itself -- `make check`
# compares the two copies with a separate `diff`, not the `diff -r` over the
# hot tree (see Makefile).
_ROOT = Path(__file__).resolve().parents[1]
EXTRA_FILES: tuple[tuple[Path, str], ...] = (
    (_ROOT / "plugin" / "policies" / "default.json", "default_policy.json"),
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "plugin" / "hooks" / "verdict_hot"
DEFAULT_DST = ROOT / "src" / "agent_verdict" / "verdict_hot"


def sync(src: Path, dst: Path) -> list[str]:
    """Mirror `src`'s *.py files into `dst`. Returns the names that changed."""
    dst.mkdir(parents=True, exist_ok=True)
    changed: list[str] = []

    src_names = {p.name for p in src.glob("*.py")}
    dst_names = {p.name for p in dst.glob("*.py")}

    for name in sorted(dst_names - src_names):
        (dst / name).unlink()
        changed.append(name)

    for name in sorted(src_names):
        if _copy_if_different(src / name, dst / name):
            changed.append(name)

    for extra_src, extra_rel in EXTRA_FILES:
        extra_dst = dst / extra_rel
        extra_dst.parent.mkdir(parents=True, exist_ok=True)
        if _copy_if_different(extra_src, extra_dst):
            changed.append(extra_rel)

    return changed


def _copy_if_different(src_file: Path, dst_file: Path) -> bool:
    if dst_file.exists() and filecmp.cmp(src_file, dst_file, shallow=False):
        return False
    shutil.copyfile(src_file, dst_file)
    return True


def main() -> int:
    for name in sync(DEFAULT_SRC, DEFAULT_DST):
        print(f"synced {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
