"""Repro for BUG_102 — see the work-item entry (`invariant item show BUG_102`).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

Symptom: a bugfix whose committed change touches only test code (plus the
backlog record) cannot reach the landing state, because `docs.updated` —
a `block`-level gate — says "source changed but no architecture doc did"
and offers only `--force`. Observed in a Swift project whose change was
`HeapTests/**` plus `docs/backlog.rst`.

Asserted: the verdict of `docs.updated` over a committed, test-only change
set is not a block. NOT asserted: the suspected cause (that the gate does
not consult the adapters' `is_test_file`) — the fix may take a different
route, e.g. a declared applicability, and this assertion must hold either
way.

Contract (docs/architecture/state_model.md, the BUG_064 bullet under the
docs-first gates): the gate asks "is it structural", and a change no
architecture doc could honestly be edited for must not need `--force`.
"""

import pytest
from test_rules import ctx_factory, only, repo, run, write_manifest  # noqa: F401

from invariant.core.rules import evaluate

TEST_ONLY_CHANGES = {
    "python": [
        "tests/helpers/restore.py",
        "tests/test_environment.py",
        "tests/test_upload_retry_repro.py",
        "docs/backlog.rst",
    ],
    "swift_xcode": [
        "HeapTests/Helpers/ReachabilityMirrorRestore.swift",
        "HeapTests/NetworkReachabilityEnvironmentTests.swift",
        "HeapTests/UploadRetryRemoteActiveLibraryReproTests.swift",
        "docs/backlog.rst",
    ],
}

#: One non-test source file beside the same test files: must still block.
SOURCE_BESIDE_TESTS = {
    "python": "src/demo/uploader.py",
    "swift_xcode": "Heap/Services/Uploader.swift",
}


def _committed_change(ctx_factory, ecosystem, paths):  # noqa: F811
    """Commit `paths` on the session branch, as a `verifying` exit sees it."""
    write_manifest(
        ctx_factory.main,
        {"version": 1, "project": {"name": "demo", "ecosystems": [ecosystem]}},
    )
    wt = ctx_factory.wt
    for rel in paths:
        (wt / rel).parent.mkdir(parents=True, exist_ok=True)
        (wt / rel).write_text("// changed\n", encoding="utf-8")
    run(["git", "add", "-A"], wt)
    run(["git", "commit", "-qm", "the fix"], wt)
    session = {"worktree": str(wt), "base": "master", "files": []}
    return only(evaluate(["docs.updated"], ctx_factory(cwd=wt, session=session)))


@pytest.mark.xfail(
    strict=True,
    reason="BUG_102 is live — delete this marker with the fix",
)
@pytest.mark.parametrize("ecosystem", sorted(TEST_ONLY_CHANGES))
def test_BUG_102_a_test_only_change_lands_without_an_architecture_doc(
    ctx_factory,  # noqa: F811
    ecosystem,
):
    result = _committed_change(ctx_factory, ecosystem, TEST_ONLY_CHANGES[ecosystem])
    assert not result.verdict.skipped, result.verdict.message
    assert result.verdict.ok, (
        f"{result.verdict.message} — the only route offered: "
        f"{list(result.verdict.fixes)}"
    )


@pytest.mark.parametrize("ecosystem", sorted(TEST_ONLY_CHANGES))
def test_BUG_102_control_source_beside_tests_still_needs_a_doc(
    ctx_factory,  # noqa: F811
    ecosystem,
):
    """Not xfail: the fix must narrow "source", never drop it."""
    paths = [*TEST_ONLY_CHANGES[ecosystem], SOURCE_BESIDE_TESTS[ecosystem]]
    result = _committed_change(ctx_factory, ecosystem, paths)
    assert not result.verdict.ok, result.verdict.message
    assert "source changed but no architecture doc did" in result.verdict.message
