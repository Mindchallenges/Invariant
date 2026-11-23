# ruff: noqa: F811 - the fixtures imported below are named as parameters
"""Repro for BUG_141 — see the work-item entry (`invariant item show BUG_141`).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

Symptom: in a store project, a ``guidelines.baseline`` set at the
user-defaults level is invisible to the readers that judge against the base,
so the baseline the user configured is not the baseline they apply.

Asserted: on 4c-2's toy store, widgets keeps its ruleset in the store and
sets ``guidelines.baseline`` only in the user defaults. The base holds one
baselined finding; the change raises it to two. The base reader,
``guidelines.baseline_only_shrinks``, refuses the growth, as it does when
the same baseline is set in the store (``test_adr_014_guidelines_store``,
``test_a_session_that_held_no_span_compares_against_the_stores_head``).
NOT asserted: where the base reader should look, or any design for the fix.

Contract: the entry's Expected line, "a base reader in a store project sees
the same ``guidelines.baseline`` as the working configuration resolves,
whichever level set it"; ``manifest.rst``: "A store project may set
``guidelines.baseline``".
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from test_adr_014_docs_lock import WIDGETS, two  # noqa: F401 - the fixture, by name
from test_adr_014_docs_lock_host import hosts  # noqa: F401
from test_adr_014_guidelines_store import (
    BASELINE_REL,
    LINE,
    PRINT,
    RULESET,
    RULESET_REL,
    SHRINKS,
    baseline_text,
    commit,
    keep_guidelines_in_store,
    write,
)
from test_adr_014_per_leg_gates import gate
from test_adr_014_user_level import write_user_config

from invariant.core.home import user_config_path
from invariant.core.manifest import Manifest


@pytest.fixture
def user_baseline(hosts):
    """``hosts``, widgets keeping its ruleset in the store and its
    ``guidelines.baseline`` in the user defaults alone: the base branch's
    ``app/m.py`` prints once, and the store's baseline records it."""
    keep_guidelines_in_store(hosts.store, WIDGETS, ruleset=RULESET_REL)
    write(hosts.store, RULESET_REL, RULESET)
    write(hosts.store, BASELINE_REL, baseline_text(1))
    commit(hosts.store, "guidelines")
    # The user level also holds the store links, so the defaults are added.
    path = user_config_path()
    body = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    body = body or {}
    body["defaults"] = {"guidelines": {"baseline": BASELINE_REL}}
    write_user_config(body)
    write(hosts.widgets, "app/m.py", f"{LINE}\n")
    commit(hosts.widgets, "app")
    return hosts


def test_BUG_141_a_user_default_baseline_is_the_one_the_base_reader_applies(user_baseline):
    hosts = user_baseline
    # The working configuration resolves the user's baseline, in the store.
    resolved = Manifest.locate(hosts.widgets).get("guidelines.baseline")
    assert resolved and Path(resolved).resolve() == (hosts.store / BASELINE_REL).resolve()
    store_level = yaml.safe_load((hosts.store / "invariant.yaml").read_text(encoding="utf-8"))
    assert "baseline" not in store_level["store"]["repos"][WIDGETS]["guidelines"]
    # The change raises the baselined finding's count from 1 to 2.
    write(hosts.widgets, "app/m.py", f"{LINE}\n{LINE}\n")
    write(hosts.store, BASELINE_REL, baseline_text(2))
    verdict = gate(hosts.widgets, SHRINKS)
    assert not verdict.ok, (
        f"the baseline grew against the base and was admitted: {verdict.message} "
        f"| {verdict.detail}"
    )
    assert "grew" in verdict.message and PRINT in verdict.message
