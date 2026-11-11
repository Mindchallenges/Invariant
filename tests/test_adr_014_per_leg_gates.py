# ruff: noqa: F811 - the fixtures imported below are named as parameters
"""ADR-014 increment 4c-1: the gates read each leg (D9), and the lock is
released before implementation (D5).

On 4b's two repositories linked to one store, each session bound to its own
host: ``docs.updated`` takes "source changed" from the repository leg and
"docs changed" from the store leg, counting a store doc only when it was
committed under a span of its unit that also holds a recorded read there (or
the file is new in the span); ``docs.diagrams_current`` reads the doc leg;
the privacy review boundary scans the store leg too; and
``store.docs_lock_released`` holds a design state until every unit is
released. An in-repo project is unchanged. The guidelines half of D9 (the
manifest's location, the store's base, the refusals lifted) is 4c-2's.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_adr_014_docs_lock import (  # noqa: F401 - fixtures are used by name
    git,
    run_json,
    two,
)
from test_adr_014_docs_lock_host import (  # noqa: F401 - fixtures are used by name
    HOST_A,
    denied,
    hosts,
    in_repo,
)

from invariant.core import machines
from invariant.core.manifest import Manifest
from invariant.core.rules import RuleContext, _changed_set, get_rule
from invariant.domains.pipeline import PipelineDomain
from invariant.domains.session import SessionDomain

SID = "aaaa1111"

#: A home path, assembled so this file carries none.
HOME_PATH = "/Us" + "ers/someone/app"


def gate(repo: Path, rule: str, sid: str = SID, payload: dict | None = None):
    manifest = Manifest.locate(repo)
    session = SessionDomain(manifest).require(sid)
    pipeline = PipelineDomain(manifest).current(sid)
    ctx = RuleContext(
        manifest=manifest,
        cwd=repo,
        session=session.to_dict(),
        pipeline=pipeline.to_dict(),
        payload=payload or {},
    )
    return get_rule(rule).fn(ctx)


def ctx_for(repo: Path, sid: str = SID) -> RuleContext:
    manifest = Manifest.locate(repo)
    return RuleContext(
        manifest=manifest,
        cwd=repo,
        session=SessionDomain(manifest).require(sid).to_dict(),
        pipeline=PipelineDomain(manifest).current(sid).to_dict(),
    )


def passed(verdict) -> bool:
    return verdict.ok and not verdict.skipped


def change_source(repo: Path) -> None:
    (repo / "conf.py").write_text("x = 2\n", encoding="utf-8")


def write_doc(store: Path, rel: str, text: str) -> Path:
    path = store / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def commit(store: Path, message: str = "docs") -> None:
    git(store, "add", "-A")
    git(store, "commit", "-qm", message)


def read_doc(hosts, path: Path) -> None:
    """A Read of a store doc through the host: granted, then recorded."""
    assert denied(hosts.read(hosts.widgets, HOST_A, path)) is None
    hosts.after(hosts.widgets, HOST_A, path)


def lock(hosts, capsys, *units: str) -> dict:
    hosts.at(hosts.widgets)
    code, out = run_json(capsys, "docs", "lock", *units, "--id", SID)
    assert code == 0, out
    return out


def unlock(hosts, capsys) -> dict:
    hosts.at(hosts.widgets)
    code, out = run_json(capsys, "docs", "unlock", "--id", SID)
    assert code == 0, out
    return out


DIAGRAM = "overview\n\n.. mermaid::\n\n   graph TD\n       A --> B\n"


# -- the proof ---------------------------------------------------------------


class TestTheProof:
    def test_a_conf_py_change_is_held_until_a_doc_is_committed_under_a_span_with_its_read(
        self, hosts, capsys
    ):
        change_source(hosts.widgets)
        held = gate(hosts.widgets, "docs.updated")
        assert not held.ok
        assert "source changed" in held.message

        # Read under the lock, then write the doc: uncommitted, it still holds.
        read_doc(hosts, hosts.overview)
        write_doc(hosts.store, "docs/architecture/overview.rst", DIAGRAM)
        held = gate(hosts.widgets, "docs.updated")
        assert not held.ok
        assert "uncommitted" in (held.detail or "")

        # Committed in the store, under the span that holds the read: it clears.
        commit(hosts.store)
        cleared = gate(hosts.widgets, "docs.updated")
        assert passed(cleared), cleared.message
        assert "under the docs lock" in cleared.message

        # And it stays clear once the unit is released: the closed span holds it.
        unlock(hosts, capsys)
        assert passed(gate(hosts.widgets, "docs.updated"))

    def test_a_doc_committed_under_a_span_with_no_read_does_not_count(self, hosts, capsys):
        change_source(hosts.widgets)
        lock(hosts, capsys, "architecture")
        write_doc(hosts.store, "docs/architecture/overview.rst", DIAGRAM)
        commit(hosts.store)
        held = gate(hosts.widgets, "docs.updated")
        assert not held.ok
        assert "no read" in (held.detail or "")
        assert "docs/architecture/overview.rst" in (held.detail or "")

        # The read made under the same span pairs it.
        read_doc(hosts, hosts.overview)
        assert passed(gate(hosts.widgets, "docs.updated"))

    def test_a_doc_committed_outside_every_span_does_not_count(self, hosts, capsys):
        change_source(hosts.widgets)
        read_doc(hosts, hosts.overview)
        unlock(hosts, capsys)
        # Written after the release: no span of this session covers it.
        write_doc(hosts.store, "docs/architecture/overview.rst", DIAGRAM)
        commit(hosts.store)
        held = gate(hosts.widgets, "docs.updated")
        assert not held.ok
        assert "source changed" in held.message

    def test_a_file_new_in_the_span_needs_no_read(self, hosts, capsys):
        change_source(hosts.widgets)
        lock(hosts, capsys, "architecture")
        write_doc(hosts.store, "docs/architecture/flows.rst", DIAGRAM)
        commit(hosts.store)
        cleared = gate(hosts.widgets, "docs.updated")
        assert passed(cleared), cleared.message


# -- the store leg -------------------------------------------------------------


class TestTheStoreLeg:
    def test_the_store_leg_is_the_commits_inside_the_spans_and_what_a_held_unit_carries(
        self, hosts, capsys
    ):
        read_doc(hosts, hosts.overview)
        write_doc(hosts.store, "docs/architecture/overview.rst", DIAGRAM)
        commit(hosts.store)
        write_doc(hosts.store, "docs/architecture/cli.rst", "cli changed\n")
        leg = _changed_set(ctx_for(hosts.widgets), "store")
        assert leg.answerable
        assert {p.name for p in leg.paths} == {"overview.rst", "cli.rst"}
        assert all(p.is_absolute() for p in leg.paths)
        # The repository leg is untouched by the store's commits.
        repo = _changed_set(ctx_for(hosts.widgets))
        assert not any(str(p).endswith(".rst") for p in repo.paths)

    def test_a_session_with_no_span_has_an_empty_store_leg(self, hosts):
        leg = _changed_set(ctx_for(hosts.widgets), "store")
        assert leg.paths == ()
        assert leg.answerable

    def test_an_unknown_leg_is_refused(self, hosts):
        with pytest.raises(ValueError):
            _changed_set(ctx_for(hosts.widgets), "sideways")

    def test_an_unreadable_span_makes_the_store_leg_unanswerable(self, hosts):
        read_doc(hosts, hosts.overview)
        ctx = ctx_for(hosts.widgets)
        for leg in ctx.session["legs"]:
            for span in leg.get("lock_spans") or []:
                span["acquired_at_sha"] = "0" * 40
        leg = _changed_set(ctx, "store")
        assert not leg.answerable
        change_source(hosts.widgets)
        verdict = get_rule("docs.updated").fn(ctx)
        assert not verdict.ok
        assert "store" in verdict.message


# -- diagrams and privacy --------------------------------------------------------


class TestDiagramsAndPrivacyReadTheStoreLeg:
    def test_diagrams_current_reads_the_doc_leg(self, hosts):
        change_source(hosts.widgets)
        assert gate(hosts.widgets, "docs.diagrams_current").skipped
        read_doc(hosts, hosts.overview)
        write_doc(hosts.store, "docs/architecture/overview.rst", "prose only\n")
        commit(hosts.store)
        failed = gate(hosts.widgets, "docs.diagrams_current")
        assert not failed.ok
        write_doc(hosts.store, "docs/architecture/overview.rst", DIAGRAM)
        commit(hosts.store)
        assert passed(gate(hosts.widgets, "docs.diagrams_current"))

    def test_the_review_boundary_scans_the_store_leg(self, hosts):
        read_doc(hosts, hosts.overview)
        write_doc(hosts.store, "docs/architecture/overview.rst", f"see {HOME_PATH}\n")
        commit(hosts.store)
        verdict = gate(hosts.widgets, "privacy.clean")
        assert not verdict.ok
        assert "personal-data" in verdict.message

    def test_a_clean_store_leg_is_named_as_scanned(self, hosts):
        read_doc(hosts, hosts.overview)
        write_doc(hosts.store, "docs/architecture/overview.rst", DIAGRAM)
        commit(hosts.store)
        verdict = gate(hosts.widgets, "privacy.clean")
        assert passed(verdict), verdict.message
        assert "overview.rst" in verdict.message

    def test_a_store_path_outside_the_scope_is_not_scanned(self, hosts):
        notes = hosts.store / "docs" / "notes.rst"
        read_doc(hosts, notes)
        write_doc(hosts.store, "docs/notes.rst", f"see {HOME_PATH}\n")
        commit(hosts.store)
        verdict = gate(hosts.widgets, "privacy.clean")
        assert verdict.ok


# -- the lock released ----------------------------------------------------------


class TestTheLockReleased:
    def test_a_held_unit_holds_the_design_state(self, hosts, capsys):
        read_doc(hosts, hosts.overview)
        held = gate(hosts.widgets, "store.docs_lock_released")
        assert not held.ok
        assert "area:architecture" in held.message
        assert f"invariant docs unlock --id {SID}" in held.fixes
        unlock(hosts, capsys)
        assert passed(gate(hosts.widgets, "store.docs_lock_released"))

    def test_a_queued_session_is_held_too(self, hosts, capsys):
        read_doc(hosts, hosts.overview)
        hosts.at(hosts.gadgets)
        code, out = run_json(capsys, "docs", "lock", "architecture", "--id", "bbbb2222")
        assert code == 0, out
        held = gate(hosts.gadgets, "store.docs_lock_released", sid="bbbb2222")
        assert not held.ok
        assert "queued" in held.message

    def test_no_lease_passes(self, hosts):
        verdict = gate(hosts.widgets, "store.docs_lock_released")
        assert passed(verdict)

    def test_it_rides_the_design_state_of_every_shipped_machine_in_a_store_project(
        self, hosts
    ):
        manifest = Manifest.locate(hosts.widgets)
        for lane in ("tdd", "bugfix", "spec-first", "docs"):
            machine = machines.load_pipeline_machine(manifest, lane)
            (design,) = machine.states_with_role("design")
            assert "store.docs_lock_released" in machine.states[design].exit_gates, lane
        chore = machines.load_pipeline_machine(manifest, "chore")
        assert not any(
            "store.docs_lock_released" in state.exit_gates for state in chore.states.values()
        )


# -- in-repo projects are unchanged ------------------------------------------------


class TestInRepoUnchanged:
    def test_the_gate_is_not_injected_and_answers_na(self, tmp_path, monkeypatch, capsys):
        root = in_repo(tmp_path / "plain", "plain")
        manifest = Manifest.locate(root)
        machine = machines.load_pipeline_machine(manifest, "tdd")
        assert not any(
            "store.docs_lock_released" in state.exit_gates for state in machine.states.values()
        )
        verdict = get_rule("store.docs_lock_released").fn(
            RuleContext(manifest=manifest, cwd=root, session={"id": "x"})
        )
        assert verdict.skipped
        leg = _changed_set(RuleContext(manifest=manifest, cwd=root, session={"id": "x"}), "store")
        assert leg.paths == () and leg.answerable


# -- the edges -------------------------------------------------------------------


class TestTheEdges:
    def test_a_span_naming_no_sha_is_unanswerable(self, hosts):
        read_doc(hosts, hosts.overview)
        ctx = ctx_for(hosts.widgets)
        for leg in ctx.session["legs"]:
            for span in leg.get("lock_spans") or []:
                span["acquired_at_sha"] = None
        leg = _changed_set(ctx, "store")
        assert not leg.answerable

    def test_an_unreadable_store_config_is_unanswerable(self, hosts):
        read_doc(hosts, hosts.overview)
        ctx = ctx_for(hosts.widgets)
        (hosts.store / "invariant.yaml").write_text("store: [\n", encoding="utf-8")
        assert not _changed_set(ctx, "store").answerable

    def test_an_unanswerable_store_leg_fails_the_review_boundary(self, hosts):
        read_doc(hosts, hosts.overview)
        ctx = ctx_for(hosts.widgets)
        for leg in ctx.session["legs"]:
            for span in leg.get("lock_spans") or []:
                span["acquired_at_sha"] = "0" * 40
        verdict = get_rule("privacy.clean").fn(ctx)
        assert not verdict.ok
        assert "store leg" in verdict.message

    def test_a_binary_store_doc_is_counted_as_looked_at(self, hosts):
        read_doc(hosts, hosts.overview)
        hosts.overview.write_bytes(b"\xff\xfe\x00binary")
        commit(hosts.store)
        verdict = gate(hosts.widgets, "privacy.clean")
        assert passed(verdict), verdict.message
        assert "overview.rst" in verdict.message

    def test_a_store_doc_removed_in_the_span_is_not_scanned(self, hosts):
        read_doc(hosts, hosts.overview)
        hosts.overview.unlink()
        commit(hosts.store)
        verdict = gate(hosts.widgets, "privacy.clean")
        assert verdict.ok
        assert "overview.rst" not in verdict.message

    def test_scope_all_reaches_every_store_path(self, hosts):
        notes = hosts.store / "docs" / "notes.rst"
        read_doc(hosts, notes)
        write_doc(hosts.store, "docs/notes.rst", f"see {HOME_PATH}\n")
        commit(hosts.store)
        ctx = ctx_for(hosts.widgets)
        ctx.manifest.data.setdefault("privacy", {})["scope"] = ["all"]
        verdict = get_rule("privacy.clean").fn(ctx)
        assert not verdict.ok

    def test_the_released_gate_without_a_session_is_na(self, hosts):
        manifest = Manifest.locate(hosts.widgets)
        verdict = get_rule("store.docs_lock_released").fn(
            RuleContext(manifest=manifest, cwd=hosts.widgets, session=None)
        )
        assert verdict.skipped

    def test_the_released_gate_fails_on_an_unreadable_lock(self, hosts, monkeypatch):
        from invariant.core import doclock
        from invariant.core.errors import StateError

        def broken(*_a, **_k):
            raise StateError("no store")

        monkeypatch.setattr(doclock, "DocsLock", broken)
        verdict = gate(hosts.widgets, "store.docs_lock_released")
        assert not verdict.ok
        assert "cannot be read" in verdict.message

    def test_the_released_gate_names_a_unit_still_wanted(self, hosts, monkeypatch):
        from invariant.core import doclock

        class Wanting:
            def __init__(self, *_a, **_k):
                pass

            def outcome(self, _sid):
                return doclock.Outcome(lease="l1", pending=["area:guidelines"])

        monkeypatch.setattr(doclock, "DocsLock", Wanting)
        verdict = gate(hosts.widgets, "store.docs_lock_released")
        assert not verdict.ok
        assert "waits for area:guidelines" in verdict.message

    def test_unreadable_uncommitted_changes_are_unanswerable(self, hosts, monkeypatch):
        from invariant.core import git as core_git

        read_doc(hosts, hosts.overview)
        ctx = ctx_for(hosts.widgets)
        monkeypatch.setattr(core_git, "dirty_paths", lambda _root: None)
        assert not _changed_set(ctx, "store").answerable

    def test_a_scope_path_outside_the_store_reaches_no_store_path(self, hosts, tmp_path):
        read_doc(hosts, hosts.overview)
        write_doc(hosts.store, "docs/architecture/overview.rst", f"see {HOME_PATH}\n")
        commit(hosts.store)
        ctx = ctx_for(hosts.widgets)
        ctx.manifest.data.setdefault("privacy", {})["scope"] = ["reports"]
        ctx.manifest.data.setdefault("paths", {})["reports"] = str(tmp_path / "elsewhere")
        assert get_rule("privacy.clean").fn(ctx).ok

    def test_an_unreadable_store_doc_in_scope_fails_the_boundary(self, hosts):
        read_doc(hosts, hosts.overview)
        write_doc(hosts.store, "docs/architecture/overview.rst", DIAGRAM)
        commit(hosts.store)
        hosts.overview.chmod(0)
        try:
            verdict = gate(hosts.widgets, "privacy.clean")
        finally:
            hosts.overview.chmod(0o644)
        assert not verdict.ok
        assert "could not be scanned" in verdict.message
