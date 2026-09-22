"""D-012 SSL context: verification stays on, CA bundle patched only when the store is empty."""

from __future__ import annotations

import ssl

from verdict_hot import sslctx


def test_pick_ca_bundle_returns_first_existing_candidate() -> None:
    assert sslctx.pick_ca_bundle(["/a", "/b", "/c"], lambda p: p in {"/b", "/c"}) == "/b"
    assert sslctx.pick_ca_bundle(["/a"], lambda p: False) is None


def test_build_context_verifies_and_has_a_ca_store() -> None:
    context = sslctx.build_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.cert_store_stats().get("x509_ca", 0) > 0
