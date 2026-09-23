"""Hot-path package: stdlib-only, must run on Python 3.9 (global-constraints.md).

This same source tree is copied verbatim by scripts/sync_hot.py to
src/agent_verdict/verdict_hot/ so it can also be imported as
`agent_verdict.verdict_hot` from the CLI. Modules in this package import each
other with relative imports (`from . import ledger`) so both copies work.
"""

from __future__ import annotations

SCHEMA_V = 1
PLUGIN_VERSION = "0.2.0"
