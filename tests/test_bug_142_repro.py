"""Repro for BUG_142 — see the work-item entry (`invariant item show BUG_142`).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

Symptom: in a store project, the guideline ``docs.items-carry-no-design`` is
never selected, so no change is ever judged against it.

Asserted: a toy project set up by ``init`` with the store home (the store
starter writes the ruleset, the docs project and the backlog in the store,
4c-2). A session changes a code file in the repository and files a work
item in the store's backlog; the guidelines the review phase selects for
the session's change (``guidelines show --phase review --touched``) include
``docs.items-carry-no-design``. ``docs.decisions-are-declared``, which
applies to every file, is selected over the same scope, so the selection is
read.
NOT asserted: how the rule's ``applies_to`` or the touched set should be
read, the verdicts gate's arithmetic, or any design for the fix.

Contract: the entry's Expected line, "in a store project,
``docs.items-carry-no-design`` is selected for a change that touches the
work items, the same as it is in an in-repo project".
"""

from __future__ import annotations

import pytest
from test_adr_014_docs_lock import run_json
from test_docs_onboarding import (  # noqa: F401 - fixtures are used by name
    SRC,
    git,
    init_store,
    interactive,
    run,
    toy,
    unrelated,
)

from invariant.core.manifest import Manifest

SID = "aaaa1111"
RULE = "docs.items-carry-no-design"

ITEM = """
.. bug:: A widget breaks on save
   :id: BUG_001
   :status: filed

   **Symptom:** saving a widget breaks it.
"""


def commit_all(root, like) -> None:
    """Commit everything in ``root`` under the toy repository's identity."""
    for key in ("user.email", "user.name"):
        git(root, "config", key, git(like, "config", key).strip())
    git(root, "add", "-A")
    git(root, "commit", "-qm", "set up")


@pytest.mark.xfail(
    strict=True,
    reason="BUG_142 is live — delete this marker with the fix",
)
def test_BUG_142_a_work_item_change_selects_items_carry_no_design(
    unrelated, capsys, interactive  # noqa: F811
):
    interactive("not_now")
    code, text = init_store(capsys)
    assert code == 0, text
    manifest = Manifest.locate(unrelated)
    assert manifest.get("records.backlog") == "store"
    assert manifest.get("records.guidelines") == "store"
    backlog = manifest.store.root / SRC / "backlog.rst"
    assert backlog.is_file()
    commit_all(manifest.store.root, unrelated)
    commit_all(unrelated, unrelated)
    code, text = run(capsys, "session", "start", "repro", "--id", SID)
    assert code == 0, text

    # The change: a code edit in the repository, and a work item filed in
    # the store's backlog.
    (unrelated / "alpha" / "mod.py").write_text("x = 2\n", encoding="utf-8")
    with backlog.open("a", encoding="utf-8") as handle:
        handle.write(ITEM)

    code, out = run_json(
        capsys, "guidelines", "show", "--phase", "review", "--touched", "--id", SID
    )
    assert code == 0, out
    rules = out["rules"]
    assert "docs.decisions-are-declared" in rules, out
    assert RULE in rules, (
        f"{RULE} was not selected for a change that files a work item: "
        f"selected {rules} over {out.get('files')}"
    )
