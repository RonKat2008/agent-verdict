"""Circuit breaker for the provider client (split from provider.py).

Three consecutive 429 responses open it for `open_s` (default
`_BREAKER_OPEN_SECONDS`, overridable per call via `policy.provider.
breaker_open_s` -- fix round 1 item 4); any 200 resets the count. State
lives in `verdict_home() / "breaker.json"` (0600, never followed through a
symlink). Never raises: a corrupt, missing, or unwritable file behaves as
closed.

Writes are atomic (fix round 1 item 9, D-032): `_write` creates
`breaker.json.tmp` with `O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW` (never
overwriting a stale temp file left by a crashed writer -- it simply gives
up instead), writes and closes it, then `os.replace`s it onto
`breaker.json`. Any `OSError` along the way removes the temp file, if it
exists, and returns without writing -- an unwritable or racing breaker
still behaves as closed, never half-written or corrupt.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from . import paths

_BREAKER_FILENAME = "breaker.json"
_BREAKER_OPEN_SECONDS = 600
_BREAKER_THRESHOLD = 3
_BREAKER_FILE_MODE = 0o600


class Breaker:
    """Circuit breaker persisted at `paths.verdict_home() / "breaker.json"`
    (task-3-brief.md). Three consecutive HTTP 429 responses open it for
    `_BREAKER_OPEN_SECONDS`; any 200 resets the consecutive count. Never
    raises: a missing, corrupt, or unwritable state file behaves as closed.

    `path` is an optional override for tests; production code always uses
    the default (`paths.verdict_home()`, itself `VERDICT_HOME`-overridable),
    resolved lazily on every call rather than cached at construction, the
    same way every other module in this package reads it. `open_s`
    overrides how long a trip stays open (fix round 1 item 4); production
    code passes `policy.provider.breaker_open_s` through `provider.evaluate`.
    """

    def __init__(self, path: Path | None = None, open_s: float = _BREAKER_OPEN_SECONDS) -> None:
        self._path = path
        self._open_s = open_s

    def _resolve_path(self) -> Path:
        if self._path is not None:
            return self._path
        return paths.verdict_home() / _BREAKER_FILENAME

    def _read(self) -> dict[str, object]:
        import json

        try:
            text = self._resolve_path().read_text(encoding="utf-8")
            parsed = json.loads(text)
        except (OSError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _write(self, data: Mapping[str, object]) -> None:
        import json

        tmp_path: Path | None = None
        try:
            target = self._resolve_path()
            paths.ensure_private_dir(target.parent, allow_symlink=True)
            if target.is_symlink():
                # `os.replace` would otherwise happily replace a symlink at
                # this exact path (rename(2) never follows the destination),
                # which is a different, weaker guarantee than the O_NOFOLLOW
                # open this replaced: refuse outright instead.
                raise OSError(f"refusing to write through a symlinked breaker file: {target}")
            tmp_path = target.parent / (target.name + ".tmp")
            text = json.dumps(dict(data), separators=(",", ":"))
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(str(tmp_path), flags, _BREAKER_FILE_MODE)
            try:
                os.fchmod(fd, _BREAKER_FILE_MODE)
                os.write(fd, text.encode("utf-8"))
            finally:
                os.close(fd)
            os.replace(str(tmp_path), str(target))
        except OSError:
            # Never raises: an unwritable or racing breaker behaves as
            # closed. A half-written temp file is cleaned up, never left
            # behind to be picked up (or block a future write) later.
            if tmp_path is not None:
                import contextlib

                with contextlib.suppress(OSError):
                    os.remove(str(tmp_path))

    def is_open(self, now: float) -> bool:
        try:
            data = self._read()
            open_until = data.get("open_until")
            return isinstance(open_until, (int, float)) and now < open_until
        except Exception:  # noqa: BLE001 - never raises (task-3-brief.md)
            return False

    def record(self, status: int, now: float) -> None:
        try:
            data = self._read()
            consecutive = data.get("consecutive_429")
            consecutive = consecutive if isinstance(consecutive, int) else 0
            if status == 429:
                consecutive += 1
                open_until: object = data.get("open_until", 0)
                if consecutive >= _BREAKER_THRESHOLD:
                    open_until = now + self._open_s
                self._write({"consecutive_429": consecutive, "open_until": open_until})
            elif status == 200:
                self._write({"consecutive_429": 0, "open_until": data.get("open_until", 0)})
        except Exception:  # noqa: BLE001 - never raises (task-3-brief.md)
            return
