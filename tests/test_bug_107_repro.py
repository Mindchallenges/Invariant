"""Repro for BUG_107 — see the work-item entry (`invariant item show BUG_107`).

Fails while the defect is live. When the fix lands, delete the expected-failure
wrappers; the assertions must then pass unchanged.

Symptom: two evidence writers running at once in one session lose each other's
writes. Evidence that was recorded disappears from the pipeline record, and a
stale save carries the pipeline state backwards, undoing an advance.

Asserted: what the record holds once both writers have returned. NOT asserted:
the suspected cause. Nothing here names a lock, so the fix may take any route
that leaves both writes on the record.

Contract: gates "read what was recorded at the moment those things actually
happened" (the docstring of domains/pipeline.py), so a write that vanishes
changes a verdict. The Expected line of the entry is the assertion: concurrent
evidence writers never lose each other's writes, and no save carries the state
backwards. docs/architecture/state_model.md, "Storage layout", "Concurrency",
holds the assumption this disproves: per-session files "are written only by
their owning session".

The interleave is forced, not raced. Writer A is parked at the entry to its
store write, so it has loaded and has not saved. Writer B is a real second
process over the same temporary state root, as a parallel hook is.

* While the defect is live, B finishes while A is parked, then A saves.
* Once writes are serialised, B cannot finish while A is parked. So B gets
  GRACE_SECONDS, which is far longer than B's write takes and well inside the
  store's lock timeout. Then A is released and both finish in turn. The bound
  decides nothing about the verdict on the live defect and turns a deadlock
  into a failure.

A harness failure goes through ``pytest.fail``. The markers below expect only
``AssertionError``, so a hang or a writer that crashed is a real failure and
never reads as this defect.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from test_domains import git_project

import invariant
from invariant.core.store import JsonStore
from invariant.domains.pipeline import PipelineDomain

SESSION = "s1"
#: Longer than writer B's whole write, and well under the store's lock timeout.
GRACE_SECONDS = 3.0
#: What any one step may take before the test fails instead of waiting on.
BOUND_SECONDS = 30.0

#: The second writer. Touching ``ready`` immediately before the writes keeps the
#: interpreter's start-up out of the grace period.
SECOND_WRITER = """
import pathlib, sys
from invariant.core.manifest import Manifest
from invariant.domains.pipeline import PipelineDomain

root, session, ready, operation = sys.argv[1:]
pipelines = PipelineDomain(Manifest.load(pathlib.Path(root)))
pathlib.Path(ready).touch()
if operation == "evidence":
    pipelines.record_docs_read(session, "docs/architecture/from-b.md")
    pipelines.record_agent_run(session, "agent-from-b")
elif operation == "advance":
    pipelines.advance(session, "red", cwd=pathlib.Path(root))
else:
    raise SystemExit(f"unknown operation {operation}")
"""


class ParkingStore(JsonStore):
    """Stops the first write of the pipeline record until it is released.

    Whichever route a writer takes, it has loaded by the time it writes.
    """

    def __init__(self, root: Path, rel: str) -> None:
        super().__init__(root)
        self.rel = rel
        self.parked = threading.Event()
        self.release = threading.Event()

    def write(self, rel: str, data) -> None:
        if rel == self.rel and not self.parked.is_set():
            self.parked.set()
            if not self.release.wait(BOUND_SECONDS):
                raise TimeoutError("writer A was never released")
        super().write(rel, data)


def _second_writer(m, operation: str, tmp_path: Path) -> tuple[subprocess.Popen, Path]:
    """Start writer B, against the same build of invariant as this test."""
    ready = tmp_path / "second-writer-ready"
    log = tmp_path / "second-writer-stderr"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(invariant.__file__).parents[1])
    with log.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-c", SECOND_WRITER, str(m.root), SESSION, str(ready), operation],
            cwd=m.root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
        )
    deadline = time.monotonic() + BOUND_SECONDS
    while not ready.exists():
        if process.poll() is not None or time.monotonic() >= deadline:
            pytest.fail(f"writer B never reached its writes: {log.read_text(encoding='utf-8')}")
        time.sleep(0.01)
    return process, log


def _interleave(m, tmp_path: Path, operation: str) -> dict:
    """A loads and parks, B writes, A saves. Returns the record both left behind."""
    plain = PipelineDomain(m)
    plain.start(SESSION, methodology="tdd", task="t")
    # The advance's gate needs this. It is recorded before either writer runs.
    plain.record_docs_read(SESSION, str(m.root / "docs" / "architecture" / "overview.md"))

    store = ParkingStore(m.state_root, plain.relpath(SESSION))
    first = PipelineDomain(m, store=store)
    failures: list[BaseException] = []

    def writer_a() -> None:
        try:
            first.record_docs_read(SESSION, "docs/architecture/from-a.md")
        except BaseException as exc:  # noqa: BLE001 - reported on the main thread
            failures.append(exc)

    thread = threading.Thread(target=writer_a, daemon=True)
    process = None
    try:
        thread.start()
        if not store.parked.wait(BOUND_SECONDS):
            pytest.fail(f"writer A never reached a store write of its record: {failures}")
        process, log = _second_writer(m, operation, tmp_path)
        try:
            process.wait(timeout=GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass  # B is waiting for A, which only A's release can end
        store.release.set()
        thread.join(BOUND_SECONDS)
        if thread.is_alive():
            pytest.fail("writer A did not finish once released")
        try:
            process.wait(timeout=BOUND_SECONDS)
        except subprocess.TimeoutExpired:
            pytest.fail("writer B did not finish once writer A had")
    finally:
        store.release.set()
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()

    if failures:
        pytest.fail(f"writer A failed: {failures}")
    if process.returncode != 0:
        pytest.fail(f"writer B failed: {log.read_text(encoding='utf-8')}")
    return PipelineDomain(m).require(SESSION)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BUG_107 is live — delete this marker with the fix",
)
def test_BUG_107_a_second_writers_evidence_survives_the_first_writers_save(tmp_path):
    m = git_project(tmp_path / "proj")

    pipeline = _interleave(m, tmp_path, "evidence")

    docs_read = pipeline.evidence.get("docs_read", [])
    agents_run = pipeline.evidence.get("agents_run", [])
    lost = []
    if "docs/architecture/from-a.md" not in docs_read:
        lost.append("writer A's docs_read")
    if "docs/architecture/from-b.md" not in docs_read:
        lost.append("writer B's docs_read")
    if "agent-from-b" not in agents_run:
        lost.append("writer B's agents_run")
    assert not lost, f"recorded and then lost: {lost}"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BUG_107 is live — delete this marker with the fix",
)
def test_BUG_107_a_stale_save_does_not_undo_an_advance(tmp_path):
    m = git_project(tmp_path / "proj")

    pipeline = _interleave(m, tmp_path, "advance")

    assert pipeline.state == "red", (
        f"writer B advanced designing -> red and a stale save left {pipeline.state!r}"
    )
    assert [(row["from"], row["to"]) for row in pipeline.history] == [("designing", "red")], (
        f"the advance's history row was lost: {pipeline.history}"
    )
    assert "docs/architecture/from-a.md" in pipeline.evidence.get("docs_read", []), (
        f"writer A's read was lost: {pipeline.evidence.get('docs_read')}"
    )
