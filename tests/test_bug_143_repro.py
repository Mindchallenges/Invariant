# ruff: noqa: F811 - the fixtures imported below are named as parameters
"""Repro for BUG_143 — see the work-item entry (`invariant item show BUG_143`).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

Symptom: in a store project, the store's ``invariant.yaml`` is not covered
by the docs lock, so two sessions can edit the project's configuration in
the store at once with nothing holding either back.

Asserted: on 4c-2's toy store (two repositories linked to one store, widgets
keeping its guidelines there), session A edits the store's ``invariant.yaml``
through the host, then session B's edit of the same file, through its own
host, is denied by the pre-tool-use hook.
NOT asserted: which unit the manifest belongs to, or any design for the fix.

Contract: the entry's Expected line, "the store's ``invariant.yaml`` is
under the docs lock like the other store-held records, so a second session's
edit to it waits on or is refused by the lock"; ``manifest.rst``: "The
policy records kept there, ``docs`` and ``guidelines``, are what the lock
governs", and since increment 4c-2 the store's manifest holds the
guidelines' ``baseline`` and ``ruleset`` keys.
"""

from __future__ import annotations

from test_adr_014_docs_lock import two  # noqa: F401 - the fixture, by name
from test_adr_014_docs_lock_host import HOST_A, HOST_B, denied, hosts  # noqa: F401
from test_adr_014_guidelines_store import gstore  # noqa: F401


def test_BUG_143_a_second_session_editing_the_store_manifest_is_held_back(gstore):
    manifest = gstore.store / "invariant.yaml"
    # Session A (widgets) edits the project's configuration in the store.
    first = gstore.read(gstore.widgets, HOST_A, manifest, tool="Edit")
    assert denied(first) is None, first
    # Session B (gadgets) edits the same file while A is at it.
    second = gstore.read(gstore.gadgets, HOST_B, manifest, tool="Edit")
    assert denied(second) is not None, (
        "session bbbb2222's edit of the store's invariant.yaml was admitted while "
        f"session aaaa1111 was editing it: {second}"
    )
