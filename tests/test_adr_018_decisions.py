"""ADR-018 increment 5: decisions as needs, the refused homes, and the floor.

D7's ``invariant docs decision new``; D8's ``docs.design_home`` and
``items.decisions_linked``; D9's ``session decisions`` and
``docs.decisions_recorded``; D10's switch, ``docs.mandate_kept``; ruling 19's
block floor; and the two judgement rules the starter's ruleset carries.

The toy project and its fake ``sphinx-build`` are increment 3's. One test
builds a minted record page strictly in the starter's real pinned environment,
and runs only where ``INVARIANT_TEST_SPHINX_BUILD`` is set and uv is present.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml
from test_adr_018_docs_build import (
    OVERVIEW,
    REQUIREMENTS,
    build,
    commit,
    ctx,
    git,
    landing_gates,
    manifest_body,
    worktree,
    write,
    write_manifest,
)
from test_adr_018_needs import need, needs_json

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core.errors import GateFailure, ManifestError, UsageError
from invariant.core.manifest import MANIFEST_RELPATH, Level, Manifest
from invariant.core.rules import RuleContext, evaluate, get_rule
from invariant.domains import docs as docs_domain
from invariant.domains.session import SessionDomain
from invariant.domains.workitem import WorkItemDomain

HOME = "docs.design_home"
LINKED = "items.decisions_linked"
RECORDED = "docs.decisions_recorded"
KEPT = "docs.mandate_kept"
FLOORED = (
    "docs.builds_strict",
    "docs.needs_traceable",
    HOME,
    RECORDED,
    LINKED,
)

@pytest.fixture
def toy(tmp_path):
    """Increment 3's enforced toy project: a docs project and a backlog page."""
    main_root = tmp_path / "main"
    main_root.mkdir()
    git(main_root, "init", "-q", "-b", "master")
    git(main_root, "config", "user.email", "test@example.invalid")
    git(main_root, "config", "user.name", "Test")
    write(main_root, "docs/conf.py", "extensions = ['sphinx_needs']\n")
    write(main_root, "docs/requirements.txt", REQUIREMENTS)
    write(main_root, "docs/.gitignore", ".venv/\n")
    write(main_root, "docs/index.rst", "Toy\n===\n")
    write(main_root, "docs/architecture/overview.rst", OVERVIEW)
    write(main_root, "docs/backlog.rst", "Backlog\n=======\n")
    write(main_root, "src/toy/mod.py", "x = 1\n")
    write_manifest(main_root, manifest_body())
    commit(main_root, "init")
    return main_root


INDEX = "Decisions\n=========\n\nOne page per decision record.\n"


def with_index(root: Path, text: str = INDEX) -> None:
    write(root, "docs/decisions/index.rst", text)


def green_with(fake, *ids: str) -> None:
    """The fake build's needs.json: the element, and each id as a decision."""
    fake.green(
        needs_json(
            [need("COMP_CORE", "comp", "architecture/overview", 4)]
            + [
                need(i, "dec", "decisions/x", 3, "accepted", implements=["COMP_CORE"])
                for i in ids
            ]
        )
    )


def write_call(path: Path, text: str = "x\n") -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": text}}


def edit_call(path: Path, new: str) -> dict:
    return {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(path), "old_string": "a", "new_string": new},
    }


def one(context: RuleContext, rule_id: str):
    (result,) = evaluate([rule_id], context)
    return result


def at_hook(main_root: Path, wt: Path, call: dict, **session) -> RuleContext:
    context = ctx(main_root, wt)
    context.payload = call
    context.session.update(session)
    return context


def migrate(root: Path) -> None:
    write_manifest(root, manifest_body(state=None))
    commit(root, "migrating")


# -- D7: docs decision new --------------------------------------------------------


class TestDecisionNew:
    def test_it_mints_the_first_record_page_and_lists_it(self, toy):
        with_index(toy)
        page = docs_domain.new_decision(
            Manifest.locate(toy), "Records are pages", today=date(2026, 11, 9)
        )
        assert page.need_id == "DEC_ADR_001" and page.number == 1
        assert page.rel == "docs/decisions/ADR-001-records-are-pages.rst"
        text = page.path.read_text(encoding="utf-8")
        assert ".. dec:: Records are pages" in text
        assert "   :id: DEC_ADR_001" in text
        assert "   :status: proposed" in text
        assert ".. _adr-001:" in text
        assert "2026-11-09" in text
        title, underline = text.splitlines()[2:4]
        assert title == "ADR-001 — Records are pages" and len(underline) >= len(title)
        index = (toy / "docs/decisions/index.rst").read_text(encoding="utf-8")
        assert ".. toctree::" in index and "   ADR-001-records-are-pages" in index

    def test_the_next_number_counts_every_record_page_there(self, toy):
        with_index(toy)
        write(
            toy,
            "docs/decisions/by-hand.rst",
            "By hand\n=======\n\n.. dec:: Old\n   :id: DEC_ADR_007\n   :status: accepted\n",
        )
        manifest = Manifest.locate(toy)
        first = docs_domain.new_decision(manifest, "One")
        second = docs_domain.new_decision(manifest, "Two")
        assert (first.need_id, second.need_id) == ("DEC_ADR_008", "DEC_ADR_009")
        index = (toy / "docs/decisions/index.rst").read_text(encoding="utf-8")
        assert index.count(".. toctree::") == 1
        assert index.index("ADR-008-one") < index.index("ADR-009-two")

    def test_a_file_name_alone_spends_its_number(self, toy):
        with_index(toy)
        write(toy, "docs/decisions/ADR-004-gone.rst", "Gone\n====\n")
        page = docs_domain.new_decision(Manifest.locate(toy), "Next")
        assert page.need_id == "DEC_ADR_005"

    def test_a_glob_toctree_is_left_alone(self, toy):
        with_index(toy, INDEX + "\n.. toctree::\n   :glob:\n\n   ADR-*\n")
        before = (toy / "docs/decisions/index.rst").read_text(encoding="utf-8")
        page = docs_domain.new_decision(Manifest.locate(toy), "Globbed")
        assert page.index is None
        assert (toy / "docs/decisions/index.rst").read_text(encoding="utf-8") == before

    def test_the_projects_own_decision_type_and_first_status(self, toy):
        types = [
            {"directive": "comp", "prefix": "COMP_", "role": "element"},
            {"directive": "decision", "prefix": "D_", "role": "decision"},
            {"directive": "req", "prefix": "REQ_", "role": "requirement"},
            {"directive": "ui", "prefix": "UI_", "role": "ui"},
            {"directive": "rule", "prefix": "RULE_", "role": "rule"},
            {"directive": "idea", "prefix": "IDEA_", "role": "idea"},
            {"directive": "bug", "prefix": "BUG_", "role": "bug"},
        ]
        write_manifest(toy, manifest_body(types=types, decision_statuses=["draft", "done"]))
        with_index(toy)
        page = docs_domain.new_decision(Manifest.locate(toy), "Own type")
        text = page.path.read_text(encoding="utf-8")
        assert page.need_id == "D_ADR_001"
        assert ".. decision:: Own type" in text and ":status: draft" in text

    def test_dry_run_writes_nothing(self, toy):
        with_index(toy)
        page = docs_domain.new_decision(Manifest.locate(toy), "Not yet", dry_run=True)
        assert not page.path.exists()
        assert "ADR-001" not in (toy / "docs/decisions/index.rst").read_text(encoding="utf-8")

    @pytest.mark.parametrize("title", ["", "   ", "two\nlines"])
    def test_a_title_that_is_not_one_line_is_refused(self, toy, title):
        with_index(toy)
        with pytest.raises(UsageError, match="one line"):
            docs_domain.new_decision(Manifest.locate(toy), title)

    def test_no_decisions_index_is_refused_naming_it(self, toy):
        with pytest.raises(UsageError, match="decisions/index.rst"):
            docs_domain.new_decision(Manifest.locate(toy), "Nowhere")

    def test_no_docs_project_is_refused(self, toy):
        write_manifest(toy, manifest_body(state=None))
        (toy / "docs/conf.py").unlink()
        with pytest.raises(UsageError, match="no docs project"):
            docs_domain.new_decision(Manifest.locate(toy), "Nowhere")

    def test_the_command(self, toy, monkeypatch, capsys):
        with_index(toy)
        monkeypatch.chdir(toy)
        capsys.readouterr()
        assert main(["--json", "docs", "decision", "new", "A record"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["id"] == "DEC_ADR_001"
        assert payload["path"] == "docs/decisions/ADR-001-a-record.rst"
        assert payload["index"] == "docs/decisions/index.rst"
        assert main(["docs", "decision", "new", "Second", "--dry-run"]) == 0
        text = capsys.readouterr().out
        assert "would write docs/decisions/ADR-002-second.rst" in text
        assert "DEC_ADR_002" in text and "require_links" in text
        assert not (toy / "docs/decisions/ADR-002-second.rst").exists()


@pytest.mark.skipif(
    not os.environ.get("INVARIANT_TEST_SPHINX_BUILD") or shutil.which("uv") is None,
    reason="set INVARIANT_TEST_SPHINX_BUILD (and have uv) to build a minted record "
    "strictly in the starter's pinned environment",
)
def test_a_minted_record_page_builds_strictly(tmp_path, monkeypatch, capsys):
    from test_adr_018_starter import repo
    from test_docs_onboarding import Scripted

    root = repo(
        tmp_path / "toy",
        {
            "pyproject.toml": '[project]\nname = "toy"\nversion = "0"\n',
            "pkg/__init__.py": "",
            "pkg/alpha/__init__.py": "x = 1\n",
        },
    )
    monkeypatch.chdir(root)
    monkeypatch.setattr(cli_main, "_init_prompter", lambda args: Scripted("not_now"))
    monkeypatch.setattr(docs_domain, "_uv", lambda: shutil.which("uv"))
    assert main(["init", "--ecosystem", "python"]) == 0
    assert main(["docs", "decision", "new", "The first record"]) == 0
    assert main(["docs", "decision", "new", "The second record"]) == 0
    capsys.readouterr()
    code = main(["--json", "docs", "build"])
    record = json.loads(capsys.readouterr().out)
    assert code == 0 and record["passed"], record
    assert record["diagnostics"] == []
    held = json.loads(Path(record["needs_json"]).read_text(encoding="utf-8"))
    (version,) = held["versions"].values()
    assert {"DEC_ADR_001", "DEC_ADR_002"} <= set(version["needs"])


# -- D8: docs.design_home -----------------------------------------------------------


class TestDesignHome:
    @pytest.mark.parametrize(
        "rel",
        [
            "docs/adr/ADR-002.md",
            "docs/adr/notes.txt",
            "design/ADR-003-x.md",
            "notes/adr-004.md",
            "docs/architecture/notes.md",
            "docs/decisions/draft.md",
        ],
    )
    def test_a_refused_home_is_refused_naming_decision_new(self, toy, rel):
        wt, _ = worktree(toy)
        result = one(at_hook(toy, wt, write_call(wt / rel)), HOME)
        assert result.level is Level.BLOCK and result.blocks, result.render()
        assert rel in result.verdict.message
        assert any("invariant docs decision new" in fix for fix in result.verdict.fixes)

    @pytest.mark.parametrize(
        "rel",
        [
            "docs/guide/usage.md",
            "docs/reports/session-1.md",
            "README.md",
            "docs/architecture/overview.rst",
            "docs/decisions/ADR-001-x.rst",
            "src/toy/mod.py",
        ],
    )
    def test_a_guide_a_report_and_the_needs_pages_are_written_freely(self, toy, rel):
        wt, _ = worktree(toy)
        result = one(at_hook(toy, wt, write_call(wt / rel)), HOME)
        assert result.verdict.ok, result.render()

    def test_a_generated_file_invariant_declares_is_not_refused(self, toy):
        body = manifest_body()
        body["guidelines"] = {"checklist": "docs/architecture/checklist.md"}
        write_manifest(toy, body)
        commit(toy, "checklist")
        wt, _ = worktree(toy)
        result = one(at_hook(toy, wt, write_call(wt / "docs/architecture/checklist.md")), HOME)
        assert result.verdict.ok, result.render()

    def test_refused_paths_replaces_the_globs(self, toy):
        write_manifest(toy, manifest_body(refused_paths=["plans/**"]))
        commit(toy, "refused")
        wt, _ = worktree(toy)
        assert one(at_hook(toy, wt, write_call(wt / "plans/x.txt")), HOME).blocks
        assert one(at_hook(toy, wt, write_call(wt / "docs/adr/ADR-002.md")), HOME).verdict.ok
        # Markdown under paths.docs is refused whatever the globs say.
        assert one(at_hook(toy, wt, write_call(wt / "docs/architecture/a.md")), HOME).blocks

    def test_a_read_is_not_screened(self, toy):
        wt, _ = worktree(toy)
        call = {"tool_name": "Read", "tool_input": {"file_path": str(wt / "docs/adr/A.md")}}
        assert one(at_hook(toy, wt, call), HOME).verdict.skipped

    def test_migrating_it_warns(self, toy):
        migrate(toy)
        wt, _ = worktree(toy)
        result = one(at_hook(toy, wt, write_call(wt / "docs/adr/ADR-002.md")), HOME)
        assert result.level is Level.WARN and result.warns and not result.blocks

    @pytest.mark.parametrize("value", ["docs/adr/**", [""], [3]])
    def test_a_malformed_refused_paths_is_refused_at_load(self, toy, value):
        write_manifest(toy, manifest_body(refused_paths=value))
        with pytest.raises(ManifestError, match="refused_paths"):
            Manifest.locate(toy)


# -- D8: items.decisions_linked -----------------------------------------------------


class TestDecisionsLinked:
    def test_an_item_edit_naming_an_unknown_decision_is_refused(self, toy):
        wt, fake = worktree(toy)
        green_with(fake, "DEC_ADR_001")
        build(wt)
        call = edit_call(wt / "docs/backlog.rst", "   Ruled in DEC_ADR_009 and REQ_LOGIN.\n")
        result = one(at_hook(toy, wt, call), LINKED)
        assert result.blocks, result.render()
        assert "DEC_ADR_009" in result.verdict.message and "REQ_LOGIN" in result.verdict.message

    def test_a_held_decision_passes(self, toy):
        wt, fake = worktree(toy)
        green_with(fake, "DEC_ADR_001")
        build(wt)
        call = edit_call(wt / "docs/backlog.rst", "   Ruled in DEC_ADR_001.\n")
        assert one(at_hook(toy, wt, call), LINKED).verdict.ok

    def test_no_recorded_build_is_refused_naming_docs_build(self, toy):
        wt, _ = worktree(toy)
        call = edit_call(wt / "docs/backlog.rst", "   See DEC_ADR_001.\n")
        result = one(at_hook(toy, wt, call), LINKED)
        assert result.blocks and "invariant docs build" in result.verdict.fixes

    @pytest.mark.parametrize(
        "text", ["No ids here.", "DEC_ADR_<nnn> is a pattern", "BUG_001 and COMP_CORE"]
    )
    def test_an_edit_naming_no_design_id_is_clean(self, toy, text):
        wt, _ = worktree(toy)
        call = edit_call(wt / "docs/backlog.rst", text)
        assert one(at_hook(toy, wt, call), LINKED).verdict.ok

    def test_an_edit_elsewhere_is_na(self, toy):
        wt, _ = worktree(toy)
        call = edit_call(wt / "docs/architecture/overview.rst", "DEC_ADR_009")
        assert one(at_hook(toy, wt, call), LINKED).verdict.skipped

    def test_item_new_refuses_and_files_nothing(self, toy, monkeypatch):
        wt, fake = worktree(toy)
        green_with(fake, "DEC_ADR_001")
        build(wt)
        monkeypatch.chdir(wt)
        domain = WorkItemDomain(Manifest.locate(wt))
        with pytest.raises(GateFailure) as raised:
            domain.new("idea", "A thing", body="As DEC_ADR_404 rules.")
        assert "DEC_ADR_404" in raised.value.verdicts[0].verdict.message
        assert domain.list() == []
        item = domain.new("idea", "A thing", body="As DEC_ADR_001 rules.")
        assert domain.get(item.id)

    def test_item_transition_and_residue_are_checked(self, toy, monkeypatch):
        wt, fake = worktree(toy)
        green_with(fake, "DEC_ADR_001")
        build(wt)
        monkeypatch.chdir(wt)
        domain = WorkItemDomain(Manifest.locate(wt))
        item = domain.new("bug", "Broken")
        with pytest.raises(GateFailure):
            domain.add_residue(item.id, "owed by DEC_ADR_404")
        with pytest.raises(GateFailure):
            domain.transition(item.id, "not_reproducible", reason="DEC_ADR_404 says so")
        assert domain.get(item.id).status == "filed"

    def test_migrating_item_new_warns_and_files(self, toy, monkeypatch):
        migrate(toy)
        wt, _ = worktree(toy)
        monkeypatch.chdir(wt)
        domain = WorkItemDomain(Manifest.locate(wt))
        item = domain.new("idea", "A thing", body="As DEC_ADR_404 rules.")
        assert domain.get(item.id)
        (warned,) = domain.warnings
        assert warned.warns and "DEC_ADR_404" in warned.verdict.message

    def test_the_command_refuses_with_exit_1(self, toy, monkeypatch, capsys):
        wt, _ = worktree(toy)
        monkeypatch.chdir(wt)
        code = main(["item", "new", "idea", "--title", "T", "--body", "per DEC_ADR_404"])
        out = capsys.readouterr()
        assert code != 0
        assert "DEC_ADR_404" in out.out + out.err

    def test_at_the_landing_the_items_the_session_changed_are_checked(self, toy):
        wt, fake = worktree(toy)
        write(wt, "docs/backlog.rst", "Backlog\n=======\n\nSee DEC_ADR_404.\n")
        commit(wt)
        green_with(fake, "DEC_ADR_001")
        build(wt)
        result = one(ctx(toy, wt), LINKED)
        assert result.blocks and "DEC_ADR_404" in result.verdict.message
        write(wt, "docs/backlog.rst", "Backlog\n=======\n\nSee DEC_ADR_001.\n")
        commit(wt)
        build(wt)
        assert one(ctx(toy, wt), LINKED).verdict.ok

    def test_at_the_landing_no_item_change_is_na(self, toy):
        wt, _ = worktree(toy)
        write(wt, "src/toy/mod.py", "x = 2\n")
        commit(wt)
        assert one(ctx(toy, wt), LINKED).verdict.skipped


# -- D9: session decisions and docs.decisions_recorded ------------------------------


@pytest.fixture
def sessions(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    git(root, "init", "-q", "-b", "master")
    write(root, MANIFEST_RELPATH, "version: 1\nproject: {name: p}\n")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    commit(root, "init")
    domain = SessionDomain(Manifest.locate(root))
    return domain, domain.start("decide")


class TestSessionDecisions:
    def test_add_an_id_and_a_draft_then_resolve_the_draft(self, sessions):
        domain, session = sessions
        domain.decisions_add(session.id, need_id="DEC_ADR_001")
        domain.decisions_add(session.id, draft="Records are pages")
        entries = domain.get(session.id).decisions
        assert entries == [
            {"n": 1, "id": "DEC_ADR_001", "draft": None},
            {"n": 2, "id": None, "draft": "Records are pages"},
        ]
        domain.decisions_resolve(session.id, 2, "DEC_ADR_002")
        assert domain.get(session.id).decisions[1] == {
            "n": 2, "id": "DEC_ADR_002", "draft": "Records are pages"
        }
        domain.decisions_remove(session.id, 1)
        domain.decisions_add(session.id, need_id="DEC_ADR_003")
        assert [e["n"] for e in domain.get(session.id).decisions] == [2, 3]

    @pytest.mark.parametrize(
        "kwargs",
        [{}, {"need_id": "DEC_ADR_001", "draft": "x"}, {"need_id": "not an id"},
         {"need_id": "COMP_CORE"}, {"draft": "  "}, {"draft": "two\nlines"}],
    )
    def test_a_malformed_entry_is_refused(self, sessions, kwargs):
        domain, session = sessions
        with pytest.raises(UsageError):
            domain.decisions_add(session.id, **kwargs)
        assert domain.get(session.id).decisions == []

    def test_an_unknown_number_is_refused(self, sessions):
        domain, session = sessions
        with pytest.raises(UsageError, match="no decision 4"):
            domain.decisions_resolve(session.id, 4, "DEC_ADR_001")

    def test_the_command(self, sessions, monkeypatch, capsys):
        domain, session = sessions
        monkeypatch.chdir(domain.manifest.root)
        sid = session.id
        assert main(["session", "decisions", "add", "--draft", "One", "--id", sid]) == 0
        assert main(["session", "decisions", "add", "DEC_ADR_002", "--id", sid]) == 0
        capsys.readouterr()
        assert main(["--json", "session", "decisions", "show", "--id", sid]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["drafts"] == 1 and len(payload["decisions"]) == 2
        assert main(["session", "decisions", "resolve", "1", "DEC_ADR_001", "--id", sid]) == 0
        text = capsys.readouterr().out
        assert "1  DEC_ADR_001  (was: One)" in text
        assert main(["session", "decisions", "remove", "2", "--id", sid]) == 0
        assert main(["session", "decisions", "add", "--id", sid]) == 2


class TestDecisionsRecorded:
    def test_it_rides_the_landing_entry(self, toy):
        gates = landing_gates(Manifest.locate(toy))
        for rule in (RECORDED, LINKED, KEPT):
            assert rule in gates

    def test_a_draft_blocks_the_landing_until_it_is_a_need(self, toy):
        wt, fake = worktree(toy)
        with_index(wt)
        commit(wt)
        drafts = [{"n": 1, "id": None, "draft": "Records are pages"}]
        context = ctx(toy, wt)
        context.session["decisions"] = drafts
        result = one(context, RECORDED)
        assert result.blocks, result.render()
        assert "draft 1: Records are pages" in result.verdict.detail
        assert any("invariant docs decision new" in f for f in result.verdict.fixes)
        assert any("session decisions resolve <n>" in f for f in result.verdict.fixes)

        page = docs_domain.new_decision(Manifest.locate(wt), "Records are pages")
        commit(wt)
        green_with(fake, page.need_id)
        build(wt)
        context.session["decisions"] = [{"n": 1, "id": page.need_id, "draft": "Records are pages"}]
        assert one(context, RECORDED).verdict.ok

    def test_an_id_the_green_build_does_not_hold_blocks(self, toy):
        wt, fake = worktree(toy)
        write(wt, "docs/index.rst", "Toy\n===\n\nMore.\n")
        commit(wt)
        green_with(fake, "DEC_ADR_001")
        build(wt)
        context = ctx(toy, wt)
        context.session["decisions"] = [{"n": 1, "id": "DEC_ADR_002", "draft": None}]
        result = one(context, RECORDED)
        assert result.blocks and "DEC_ADR_002" in result.verdict.detail

    def test_an_id_with_no_green_build_names_docs_build(self, toy):
        wt, _ = worktree(toy)
        context = ctx(toy, wt)
        context.session["decisions"] = [{"n": 1, "id": "DEC_ADR_001", "draft": None}]
        result = one(context, RECORDED)
        assert result.blocks and "invariant docs build" in result.verdict.fixes

    def test_ids_a_plan_or_a_commit_cites_are_held_too(self, toy):
        wt, fake = worktree(toy)
        write(wt, "docs/index.rst", "Toy\n===\n\nMore.\n")
        commit(wt, "Implements DEC_ADR_404")
        green_with(fake, "DEC_ADR_001")
        build(wt)
        context = ctx(toy, wt)
        context.session["plan"] = [{"n": 1, "title": "Build DEC_ADR_001 and DEC_ADR_333"}]
        result = one(context, RECORDED)
        assert result.blocks
        assert "DEC_ADR_333" in result.verdict.detail and "DEC_ADR_404" in result.verdict.detail
        assert "DEC_ADR_001" not in result.verdict.detail

    def test_nothing_declared_is_na(self, toy):
        wt, _ = worktree(toy)
        assert one(ctx(toy, wt), RECORDED).verdict.skipped

    def test_no_session_is_na(self, toy):
        context = RuleContext(manifest=Manifest.locate(toy), cwd=toy)
        assert one(context, RECORDED).verdict.skipped

    def test_migrating_a_draft_warns(self, toy):
        migrate(toy)
        wt, _ = worktree(toy)
        context = ctx(toy, wt)
        context.session["decisions"] = [{"n": 1, "id": None, "draft": "x"}]
        result = one(context, RECORDED)
        assert result.warns and not result.blocks


# -- ruling 19: the block floor, and D10: the switch is not walked back --------------


class TestTheFloor:
    @pytest.mark.parametrize("rule", FLOORED)
    @pytest.mark.parametrize("level", ["warn", "ask", "off"])
    def test_an_enforced_manifest_lowering_a_mandate_rule_is_refused(self, toy, rule, level):
        body = manifest_body()
        body["enforcement"] = {"rules": {rule: level}}
        write_manifest(toy, body)
        with pytest.raises(ManifestError, match="ruling 1") as raised:
            Manifest.locate(toy)
        assert rule in str(raised.value)

    def test_block_itself_loads(self, toy):
        body = manifest_body()
        body["enforcement"] = {"rules": {"docs.builds_strict": "block"}}
        write_manifest(toy, body)
        assert Manifest.locate(toy).rule_level("docs.builds_strict") is Level.BLOCK

    @pytest.mark.parametrize("rule", FLOORED)
    def test_enforced_each_blocks(self, toy, rule):
        assert Manifest.locate(toy).rule_level(rule) is Level.BLOCK

    @pytest.mark.parametrize("rule", FLOORED)
    def test_migrating_each_warns_and_a_lower_level_loads(self, toy, rule):
        body = manifest_body(state=None)
        write_manifest(toy, body)
        assert Manifest.locate(toy).rule_level(rule) is Level.WARN
        body["enforcement"] = {"rules": {rule: "off"}}
        write_manifest(toy, body)
        assert Manifest.locate(toy).rule_level(rule) is Level.OFF

    def test_a_profile_from_the_environment_does_not_lower_them(self, toy):
        manifest = Manifest.locate(toy, env={"INVARIANT_PROFILE": "loose"})
        for rule in FLOORED:
            assert manifest.rule_level(rule) is Level.BLOCK

    def test_invariant_disable_is_ignored_for_them_and_says_so(self, toy, capsys):
        manifest = Manifest.locate(
            toy, env={"INVARIANT_DISABLE": "docs.builds_strict,docs.updated"}
        )
        assert manifest.rule_level("docs.builds_strict") is Level.BLOCK
        assert manifest.rule_level("docs.updated") is Level.OFF
        err = capsys.readouterr().err
        assert "docs.builds_strict" in err and "ruling 1" in err

    def test_the_kept_rule_has_its_floor_in_every_state(self, toy):
        for state in ("enforced", None):
            body = manifest_body(state=state)
            body["enforcement"] = {"rules": {KEPT: "warn"}}
            write_manifest(toy, body)
            with pytest.raises(ManifestError, match=KEPT):
                Manifest.locate(toy)


class TestMandateKept:
    def test_moving_enforced_back_is_refused_at_the_landing(self, toy):
        wt, _ = worktree(toy)
        write_manifest(wt, manifest_body(state="migrating"))
        commit(wt)
        result = one(ctx(toy, wt), KEPT)
        assert result.level is Level.BLOCK and result.blocks, result.render()
        assert "enforced" in result.verdict.message
        assert any("--force --reason" in fix for fix in result.verdict.fixes)

    def test_dropping_the_key_is_moving_it_back(self, toy):
        wt, _ = worktree(toy)
        write_manifest(wt, manifest_body(state=None))
        commit(wt)
        assert one(ctx(toy, wt), KEPT).blocks

    def test_keeping_it_passes(self, toy):
        wt, _ = worktree(toy)
        write(wt, "src/toy/mod.py", "x = 2\n")
        commit(wt)
        result = one(ctx(toy, wt), KEPT)
        assert result.verdict.ok and not result.verdict.skipped

    def test_a_migrating_base_passes(self, toy):
        migrate(toy)
        wt, _ = worktree(toy)
        write_manifest(wt, manifest_body(state="enforced"))
        commit(wt)
        result = one(ctx(toy, wt), KEPT)
        assert result.verdict.ok and not result.verdict.skipped

    def test_no_base_is_na(self, toy):
        context = RuleContext(manifest=Manifest.locate(toy), cwd=toy)
        assert one(context, KEPT).verdict.skipped


# -- the hook, end to end: an enforced init, a refused write, an override ------------


HOST = "host-adr-018-inc5"


class TestAtTheHook:
    @pytest.fixture
    def project(self, tmp_path, monkeypatch):
        root = tmp_path / "proj"
        root.mkdir()
        for argv in (
            ["init", "-q", "-b", "master"],
            ["config", "user.email", "t@example.invalid"],
            ["config", "user.name", "T"],
        ):
            git(root, *argv)
        write(root, "pyproject.toml", '[project]\nname = "toy"\nversion = "0"\n')
        write(root, "pkg/__init__.py", "")
        commit(root, "init")
        monkeypatch.chdir(root)
        monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
        return root

    def hook(self, body: dict) -> dict:
        stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps({"session_id": HOST, **body}))
        try:
            main(["hook", "pre-tool-use"])
        finally:
            sys.stdin = stdin

    def setup(self, project, capsys) -> tuple[str, Path]:
        assert main(["init", "--ecosystem", "python"]) == 0
        assert Manifest.locate(project).get("docs.needs.state") == "enforced"
        commit(project, "invariant")
        capsys.readouterr()
        main(["--json", "session", "start", "adr", "--json"])
        sid = json.loads(capsys.readouterr().out)["session"]["id"]
        main(["session", "isolate", "--execute", "--id", sid])
        main(["session", "adopt", HOST, "--id", sid])
        capsys.readouterr()
        wt = Path(SessionDomain(Manifest.locate(project)).get(sid).worktree)
        return sid, wt

    def decision(self, capsys) -> dict:
        out = capsys.readouterr().out.strip()
        return json.loads(out).get("hookSpecificOutput", {}) if out else {}

    def test_a_refused_write_is_denied_and_an_override_passes_one_call(self, project, capsys):
        sid, wt = self.setup(project, capsys)
        call = write_call(wt / "docs" / "adr" / "ADR-002.md")
        self.hook(call)
        denied = self.decision(capsys)
        assert denied["permissionDecision"] == "deny"
        assert HOME in denied["permissionDecisionReason"]
        assert "invariant docs decision new" in denied["permissionDecisionReason"]

        assert main(["override", HOME, "--reason", "importing a record", "--id", sid]) == 0
        capsys.readouterr()
        self.hook(call)
        assert self.decision(capsys).get("permissionDecision") != "deny"
        self.hook(call)
        assert self.decision(capsys)["permissionDecision"] == "deny"

    def test_a_user_guide_page_is_written_freely(self, project, capsys):
        _sid, wt = self.setup(project, capsys)
        self.hook(write_call(wt / "docs" / "guide.md"))
        assert self.decision(capsys).get("permissionDecision") != "deny"


# -- the starter's ruleset: the two judgement rules ---------------------------------


class TestStarterRuleset:
    @pytest.fixture
    def toy_python(self, tmp_path, monkeypatch):
        root = tmp_path / "toy"
        root.mkdir()
        git(root, "init", "-q", "-b", "main")
        write(root, "pyproject.toml", '[project]\nname = "toy"\nversion = "0"\n')
        write(root, "pkg/__init__.py", "")
        monkeypatch.chdir(root)
        return root

    def test_init_writes_the_two_judgement_rules(self, toy_python, capsys):
        assert main(["init", "--ecosystem", "python"]) == 0
        manifest = Manifest.locate(toy_python)
        assert manifest.get("guidelines.ruleset") == "invariant-docs/guidelines/ruleset.yaml"
        data = yaml.safe_load(
            (toy_python / "invariant-docs/guidelines/ruleset.yaml").read_text(encoding="utf-8")
        )
        rules = {rule["id"]: rule for rule in data["rules"]}
        assert set(rules) == {"docs.items-carry-no-design", "docs.decisions-are-declared"}
        for rule in rules.values():
            assert rule["check"]["kind"] == "judgement"
        assert "invariant-docs/backlog.rst" in rules["docs.items-carry-no-design"]["applies_to"]
        context = RuleContext(manifest=manifest, cwd=toy_python)
        assert get_rule("guidelines.ruleset_valid").fn(context).ok
        assert get_rule("guidelines.generated_current").fn(context).ok
        page = (toy_python / "invariant-docs/rules.rst").read_text(encoding="utf-8")
        assert ":id: RULE_DOCS_ITEMS_CARRY_NO_DESIGN" in page
        assert ":id: RULE_DOCS_DECISIONS_ARE_DECLARED" in page

    def plan(self, root: Path, **guidelines):
        body = {"version": 1, "project": {"name": "t", "ecosystems": ["generic"]}}
        if guidelines:
            body["guidelines"] = guidelines
        manifest = Manifest.from_raw(body, root=root, path=root / "m.yaml", env={})
        return docs_domain.plan_starter(
            manifest, project="t", base=root, docs="docs/architecture"
        )

    def test_a_configured_ruleset_is_not_rewritten(self, tmp_path):
        write(tmp_path, "rules/own.yaml", "version: 1\nsections: []\nrules: []\n")
        starter = self.plan(tmp_path, ruleset="rules/own.yaml")
        assert starter.ruleset is None and "guidelines" not in starter.settings
        assert "rules/own.yaml" in starter.note
        for rule_id in ("docs.items-carry-no-design", "docs.decisions-are-declared"):
            assert rule_id in starter.note
        assert "      - docs/backlog.rst" in starter.note

    def test_a_configured_ruleset_holding_both_rules_needs_no_note(self, tmp_path):
        write(
            tmp_path,
            "rules/own.yaml",
            "# docs.items-carry-no-design docs.decisions-are-declared\n",
        )
        assert self.plan(tmp_path, ruleset="rules/own.yaml").note is None

    def test_a_ruleset_file_already_there_is_never_written_over(self, tmp_path):
        write(tmp_path, "docs/guidelines/ruleset.yaml", "ours\n")
        starter = self.plan(tmp_path)
        assert starter.ruleset is None and "set guidelines.ruleset" in starter.note

    def test_a_store_project_gets_the_rules_to_add_by_hand(self, tmp_path):
        body = {"version": 1, "project": {"name": "t", "ecosystems": ["generic"]}}
        manifest = Manifest.from_raw(body, root=tmp_path, path=tmp_path / "m.yaml", env={})
        store = tmp_path / "store"
        store.mkdir()
        starter = docs_domain.plan_starter(
            manifest, project="t", base=store, docs="docs/architecture"
        )
        assert starter.ruleset is None and "store" in starter.note


# -- the agents and this repository --------------------------------------------------


def test_the_design_agent_declares_its_decisions():
    source = (
        Path(__file__).resolve().parents[1]
        / "src/invariant/data/agents/feature-architect.md"
    ).read_text(encoding="utf-8")
    assert "invariant session decisions add" in source
    assert "DECISIONS:" in source


def test_this_repository_is_enforced_and_blocks(monkeypatch):
    """invariant itself migrated (increment 7): the mandate's rules block here."""
    root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(root)
    manifest = Manifest.locate(root)
    assert manifest.get("docs.needs.state") == "enforced"
    assert manifest.get("docs.format") == "sphinx"
    assert manifest.get("items.backend") == "sphinx_needs"
    for rule in FLOORED:
        assert manifest.rule_level(rule) is Level.BLOCK
    assert manifest.rule_level(KEPT) is Level.BLOCK
    context = RuleContext(
        manifest=manifest,
        cwd=root,
        payload=write_call(root / "docs/adr/ADR-020-x.md"),
    )
    assert one(context, HOME).blocks
    context.payload = write_call(root / "docs/architecture/new.md")
    assert one(context, HOME).blocks
    context.payload = write_call(root / "docs/reports/x.md")
    assert one(context, HOME).verdict.ok
    context.payload = write_call(root / "docs/guide/x.md")
    assert one(context, HOME).verdict.ok
    assert one(RuleContext(manifest=manifest, cwd=root), KEPT).verdict.ok


# -- the edges of each new path ------------------------------------------------------


class TestEdges:
    def test_a_toctree_with_options_only_gains_a_blank_line_and_the_entry(self, toy):
        with_index(toy, INDEX + "\n.. toctree::\n   :maxdepth: 1\n\nAfter.\n")
        docs_domain.new_decision(Manifest.locate(toy), "First")
        index = (toy / "docs/decisions/index.rst").read_text(encoding="utf-8")
        assert "   :maxdepth: 1\n\n   ADR-001-first\n" in index
        assert index.rstrip().endswith("After.")

    def test_a_title_of_no_latin_letters_gets_no_slug(self, toy):
        with_index(toy)
        page = docs_domain.new_decision(Manifest.locate(toy), "日本")
        assert page.rel == "docs/decisions/ADR-001.rst"

    def test_an_instruction_file_under_paths_docs_is_not_refused(self, toy):
        body = manifest_body()
        body["guidelines"] = {"instruction_files": ["docs/architecture/AGENTS.md"]}
        write_manifest(toy, body)
        commit(toy, "instructions")
        wt, _ = worktree(toy)
        call = write_call(wt / "docs/architecture/AGENTS.md")
        assert one(at_hook(toy, wt, call), HOME).verdict.ok

    def test_a_write_outside_the_project_is_na(self, toy, tmp_path):
        wt, _ = worktree(toy)
        elsewhere = tmp_path / "elsewhere" / "docs" / "adr" / "ADR-1.md"
        assert one(at_hook(toy, wt, write_call(elsewhere)), HOME).verdict.skipped

    def test_a_multiedit_is_read_whole(self, toy):
        wt, fake = worktree(toy)
        green_with(fake, "DEC_ADR_001")
        build(wt)
        call = {
            "tool_name": "MultiEdit",
            "tool_input": {
                "file_path": str(wt / "docs/backlog.rst"),
                "edits": [{"old_string": "a", "new_string": "UI_LOGIN"}, "junk"],
            },
        }
        result = one(at_hook(toy, wt, call), LINKED)
        assert result.blocks and "UI_LOGIN" in result.verdict.message

    def test_the_last_recorded_build_answers_for_an_unbuilt_tree(self, toy):
        wt, fake = worktree(toy)
        green_with(fake, "DEC_ADR_001")
        build(wt)
        write(wt, "docs/index.rst", "Toy\n===\n\nMoved on.\n")
        call = edit_call(wt / "docs/backlog.rst", "DEC_ADR_001")
        assert one(at_hook(toy, wt, call), LINKED).verdict.ok

    def test_a_record_whose_needs_json_is_gone_is_not_the_last(self, toy):
        wt, fake = worktree(toy)
        green_with(fake, "DEC_ADR_001")
        record, _ = build(wt)
        Path(record.needs_json).unlink()
        write(wt, "docs/index.rst", "Toy\n===\n\nMoved on.\n")
        call = edit_call(wt / "docs/backlog.rst", "DEC_ADR_001")
        result = one(at_hook(toy, wt, call), LINKED)
        assert result.blocks and "no docs build is recorded" in result.verdict.message

    def test_a_forced_transition_passes_the_link_check_with_its_reason(self, toy, monkeypatch):
        wt, fake = worktree(toy)
        green_with(fake, "DEC_ADR_001")
        build(wt)
        monkeypatch.chdir(wt)
        domain = WorkItemDomain(Manifest.locate(wt))
        item = domain.new("bug", "Broken")
        moved, _ = domain.transition(
            item.id, "not_reproducible", reason="DEC_ADR_404 says so", force=True
        )
        assert moved.status == "not_reproducible"
        assert domain.warnings and domain.warnings[0].blocks

    def test_the_item_command_prints_a_migrating_warning(self, toy, monkeypatch, capsys):
        migrate(toy)
        wt, _ = worktree(toy)
        monkeypatch.chdir(wt)
        code = main(["item", "new", "idea", "--title", "T", "--body", "per DEC_ADR_404"])
        err = capsys.readouterr().err
        assert code == 0 and "[warn] items.decisions_linked" in err

    def test_residue_through_the_command(self, toy, monkeypatch, capsys):
        migrate(toy)
        wt, _ = worktree(toy)
        monkeypatch.chdir(wt)
        item = WorkItemDomain(Manifest.locate(wt)).new("bug", "Broken")
        assert main(["item", "residue", item.id, "--add", "see REQ_X"]) == 0
        assert "REQ_X" in capsys.readouterr().err

    def test_session_decisions_show_with_nothing_declared(self, sessions, monkeypatch, capsys):
        domain, session = sessions
        monkeypatch.chdir(domain.manifest.root)
        capsys.readouterr()
        assert main(["session", "decisions", "show", "--id", session.id]) == 0
        assert "declare one" in capsys.readouterr().out

    def test_a_draft_shows_how_to_resolve_it(self, sessions, monkeypatch, capsys):
        domain, session = sessions
        monkeypatch.chdir(domain.manifest.root)
        main(["session", "decisions", "add", "--draft", "Later", "--id", session.id])
        out = capsys.readouterr().out
        assert "1  draft: Later" in out and "resolve <n> <DEC_id>" in out

    def test_decision_new_says_when_its_index_globs(self, toy, monkeypatch, capsys):
        with_index(toy, INDEX + "\n.. toctree::\n   :glob:\n\n   *\n")
        monkeypatch.chdir(toy)
        capsys.readouterr()
        assert main(["docs", "decision", "new", "Globbed"]) == 0
        assert "globs, and lists it already" in capsys.readouterr().out

    def test_an_unreadable_base_manifest_counts_as_not_enforced(self, toy):
        write(toy, MANIFEST_RELPATH, "docs: [unclosed\n")
        commit(toy, "broken")
        wt, _ = worktree(toy)
        write_manifest(wt, manifest_body())
        commit(wt)
        session = {"worktree": str(wt), "base": "master", "branch": f"session/{wt.name}"}
        context = RuleContext(manifest=Manifest.locate(wt), cwd=wt, session=session)
        assert one(context, KEPT).verdict.ok
