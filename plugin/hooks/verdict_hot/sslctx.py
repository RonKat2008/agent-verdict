"""D-012 TLS context: verify by default, patch a missing CA store only.

Used by `verdict doctor` (Task 6) and, later, the M2 provider client --
**never** by anything in the recorder path. `global-constraints.md`:
"Nothing in M1 opens a socket except `verdict doctor`'s TLS probe."
`tests/unit/test_sslctx.py` proves this module is never imported by a real
hook invocation by parsing a `-X importtime` trace of `verdict_hook.py`.

Behavior matches `scripts/smoke_jev.py`'s `build_ssl_context` /
`pick_ca_bundle` by design (task-5-brief.md: "after this task
`scripts/smoke_jev.py` stays independent; do not import across" -- so this
is a second, independent copy, not a shared import). The first `python3`
on PATH on this development machine has an empty default certificate
store (VERIFIED_FACTS E3); loading one of the common Linux/macOS CA bundle
paths when that happens fixes verification without ever disabling it.
"""

from __future__ import annotations

import os
import ssl
from collections.abc import Callable, Sequence

CA_FALLBACKS: tuple[str, ...] = (
    "/etc/ssl/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
)


def pick_ca_bundle(candidates: Sequence[str], exists: Callable[[str], bool]) -> str | None:
    """Return the first candidate `exists` reports as present, else `None`."""
    for candidate in candidates:
        if exists(candidate):
            return candidate
    return None


def build_context() -> ssl.SSLContext:
    """Default verifying context, patched with a CA bundle if the store is empty.

    Verification is never disabled (D-012): a context with an empty CA
    store still fails closed on every connection attempt until a bundle is
    found and loaded onto it.
    """
    context = ssl.create_default_context()
    if not context.cert_store_stats().get("x509_ca"):
        bundle = pick_ca_bundle(CA_FALLBACKS, os.path.exists)
        if bundle is not None:
            context.load_verify_locations(cafile=bundle)
    return context


__all__ = ["CA_FALLBACKS", "build_context", "pick_ca_bundle"]
