"""ADR-032 increment 3: the design documents change before the build begins.

:need:`DEC_ADR_032_D6`, as built in :need:`DEC_ADR_032_I3`. An advance out of
a design-role state records which design documents differ from the merge
base and whether structural source does (``evidence["design_snapshots"]``),
and prints it. ``docs.ahead_of_code`` rides the entry of every review-role
state and judges a structural change, read through ``docs.updated``'s one
function, against those snapshots.

The deliverable tests (the increments table, row 3): a tdd pipeline that
edits ``docs/architecture`` in design and source in green passes; one that
edits only source fails under ``standard`` and warns under ``loose``; a
``chore`` pipeline is N/A; a store-docs project reads the committed store
leg.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from test_adr_014_docs_lock import git, run_json, two  # noqa: F401 - fixtures by name
from test_adr_014_docs_lock_host import hosts  # noqa: F401 - the fixture, by name
from test_domains import git_project

from invariant.core import machines, needs, rules
from invariant.core.manifest import Level, Manifest
from invariant.core.rules import RuleContext, get_rule
from invariant.domains import pipeline as pipeline_domain
from invariant.domains.pipeline import PipelineDomain
from invariant.domains.session import SessionDomain

#: The evidence key, spelled here so a missing one fails each test rather
#: than the module's import.
DESIGN_SNAPSHOTS = "design_snapshots"


def design_snapshot(ctx):
    return rules.design_snapshot(ctx)


def design_snapshot_notice(snapshot):
    return rules.design_snapshot_notice(snapshot)


def design_snapshot_taken(pipeline):
    return pipeline_domain.design_snapshot_taken(pipeline)

RULE = "docs.ahead_of_code"
DOC = "docs/architecture/overview.md"
SOURCE = "src/app/core.py"
NOT_BEFORE = "the design documents were not changed before the build began"
DECISION_PAGE = (
    "Decision\n========\n\n.. dec:: A decision\n   :id: DEC_DEMO_1\n"
    "   :status: accepted\n\n   Body.\n"
)


def write(root: Path, rel: str, text: str = "x = 1\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def sh(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


class Flow:
    """One session and one pipeline on a real repository."""

    def __init__(self, tmp_path: Path, methodology: str = "tdd", profile: str = "standard"):
        self.m = git_project(
            tmp_path / "proj",
            {
                "version": 1,
                "project": {"name": "demo", "ecosystems": ["python"]},
                "enforcement": {"profile": profile},
            },
        )
        self.root = self.m.root
        self.session = SessionDomain(self.m).start("x")
        self.sid = self.session.id
        self.pipelines = PipelineDomain(self.m)
        self.pipelines.start(self.sid, methodology=methodology, cwd=self.root)
        self.pipelines.record_docs_read(self.sid, str(self.root / DOC))

    def advance(self, target: str, force: bool = False):
        pipeline, _ = self.pipelines.advance(
            self.sid,
            target,
            force=force,
            reason="the test walks past this edge" if force else None,
            session=self.session.to_dict(),
            cwd=self.root,
        )
        return pipeline

    def gate(self, target: str):
        verdicts = self.pipelines.evaluate_gates(
            self.sid, target, session=self.session.to_dict(), cwd=self.root
        )
        found = [v for v in verdicts if v.rule_id == RULE]
        assert len(found) == 1, [v.rule_id for v in verdicts]
        return found[0]

    def ctx(self, pipeline: dict | None = None) -> RuleContext:
        return RuleContext(
            manifest=self.m,
            cwd=self.root,
            session=self.session.to_dict(),
            pipeline=pipeline
            if pipeline is not None
            else self.pipelines.require(self.sid).to_dict(),
        )

    def snapshots(self) -> list[dict]:
        return self.pipelines.require(self.sid).evidence.get(DESIGN_SNAPSHOTS) or []


def passed(evaluated) -> bool:
    return evaluated.verdict.ok and not evaluated.verdict.skipped


# -- the deliverable -------------------------------------------------------------


class TestTheDeliverable:
    def test_a_design_doc_edited_in_design_and_source_in_green_passes(self, tmp_path):
        flow = Flow(tmp_path)
        write(flow.root, DOC, "# Overview\n\nThe core component.\n")
        flow.advance("red")
        write(flow.root, SOURCE)
        flow.advance("green", force=True)
        result = flow.gate("reviewing")
        assert passed(result), result.verdict.message
        assert DOC in result.verdict.message

    def test_source_alone_fails_under_standard(self, tmp_path):
        flow = Flow(tmp_path)
        flow.advance("red")
        write(flow.root, SOURCE)
        flow.advance("green", force=True)
        result = flow.gate("reviewing")
        assert not result.verdict.ok
        assert result.verdict.message == NOT_BEFORE
        assert result.level is Level.BLOCK
        assert result.blocks

    def test_source_alone_warns_under_loose(self, tmp_path):
        flow = Flow(tmp_path, profile="loose")
        flow.advance("red")
        write(flow.root, SOURCE)
        flow.advance("green", force=True)
        result = flow.gate("reviewing")
        assert not result.verdict.ok
        assert result.level is Level.WARN
        assert not result.blocks

    def test_a_chore_pipeline_is_not_applicable(self, tmp_path):
        flow = Flow(tmp_path, methodology="chore")
        write(flow.root, SOURCE)
        result = flow.gate("reviewing")
        assert result.verdict.skipped
        assert "chore" in result.verdict.message
        assert "design state" in result.verdict.message

    def test_the_doc_written_after_the_build_began_does_not_count(self, tmp_path):
        flow = Flow(tmp_path)
        flow.advance("red")
        write(flow.root, SOURCE)
        write(flow.root, DOC, "# Overview\n\nWritten with the code.\n")
        flow.advance("green", force=True)
        result = flow.gate("reviewing")
        assert result.verdict.message == NOT_BEFORE
        # docs.updated still owes the doc, so the fix names it.
        assert any("docs/architecture" in fix for fix in result.verdict.fixes)


# -- the snapshot -----------------------------------------------------------------


class TestTheSnapshot:
    def test_the_advance_out_of_design_records_one(self, tmp_path):
        flow = Flow(tmp_path)
        write(flow.root, DOC, "# Overview\n\nchanged\n")
        pipeline = flow.advance("red")
        (snap,) = flow.snapshots()
        assert snap["from"] == "designing" and snap["to"] == "red"
        assert snap["step"] == 0
        assert snap["leg"] == "repo"
        assert snap["answerable"] is True
        assert [d["path"] for d in snap["docs"]] == [DOC]
        assert len(snap["docs"][0]["digest"]) == 12
        assert snap["source_changed"] is False
        assert design_snapshot_taken(pipeline) == snap

    def test_no_later_advance_records_another(self, tmp_path):
        flow = Flow(tmp_path)
        flow.advance("red")
        pipeline = flow.advance("green", force=True)
        assert len(flow.snapshots()) == 1
        assert design_snapshot_taken(pipeline) is None

    def test_an_abandon_edge_records_none(self, tmp_path):
        flow = Flow(tmp_path)
        flow.advance("abandoned")
        assert flow.snapshots() == []

    def test_a_new_untracked_doc_counts(self, tmp_path):
        flow = Flow(tmp_path)
        write(flow.root, "docs/architecture/flows.md", "# Flows\n")
        flow.advance("red")
        assert [d["path"] for d in flow.snapshots()[0]["docs"]] == [
            "docs/architecture/flows.md"
        ]

    def test_a_committed_doc_counts(self, tmp_path):
        flow = Flow(tmp_path)
        sh(flow.root, "checkout", "-q", "-b", "work")
        write(flow.root, DOC, "# Overview\n\ncommitted\n")
        sh(flow.root, "commit", "-qam", "the design")
        flow.session.branch = "work"
        snap = design_snapshot(flow.ctx())
        assert [d["path"] for d in snap["docs"]] == [DOC]

    def test_a_deleted_doc_counts_with_no_digest(self, tmp_path):
        flow = Flow(tmp_path)
        (flow.root / DOC).unlink()
        snap = design_snapshot(flow.ctx())
        assert snap["docs"] == [{"path": DOC, "digest": None}]

    def test_a_page_holding_a_decision_need_counts(self, tmp_path):
        flow = Flow(tmp_path)
        write(flow.root, "docs/decisions/ADR-001.rst", DECISION_PAGE)
        write(flow.root, "README.rst", "Readme\n======\n")
        write(flow.root, "docs/notes.rst", ".. note::\n\n   not a decision\n")
        snap = design_snapshot(flow.ctx())
        assert [d["path"] for d in snap["docs"]] == ["docs/decisions/ADR-001.rst"]

    def test_source_changed_is_the_structural_reading(self, tmp_path):
        flow = Flow(tmp_path)
        write(flow.root, "tests/test_core.py")
        write(flow.root, "docs/backlog.rst", "backlog\n")
        assert design_snapshot(flow.ctx())["source_changed"] is False
        write(flow.root, SOURCE)
        assert design_snapshot(flow.ctx())["source_changed"] is True

    def test_an_unreadable_base_makes_it_unanswerable_never_empty(self, tmp_path):
        flow = Flow(tmp_path)
        flow.session.base = "no-such-branch"
        snap = design_snapshot(flow.ctx())
        assert snap["answerable"] is False
        assert "no-such-branch" in snap["problem"]
        assert snap["docs"] == []


    def test_no_base_makes_it_unanswerable(self, tmp_path):
        flow = Flow(tmp_path)
        flow.session.base = None
        snap_ = design_snapshot(flow.ctx())
        assert snap_["answerable"] is False
        assert "no base" in snap_["problem"]

    def test_a_page_that_is_not_text_holds_no_decision(self, tmp_path):
        flow = Flow(tmp_path)
        (flow.root / "docs" / "binary.rst").write_bytes(b"\xff\xfe\x00 not text")
        assert design_snapshot(flow.ctx())["docs"] == []

    def test_the_last_advance_took_none_on_a_malformed_record(self, tmp_path):
        flow = Flow(tmp_path)
        pipeline = flow.advance("red")
        pipeline.evidence[DESIGN_SNAPSHOTS] = "not a list"
        assert design_snapshot_taken(pipeline) is None
        pipeline.history = []
        pipeline.evidence[DESIGN_SNAPSHOTS] = [{"step": 0}]
        assert design_snapshot_taken(pipeline) is None

    def test_an_edge_naming_no_state_leaves_no_design(self):
        machine = machines.load_pipeline_machine(None, "tdd")
        assert pipeline_domain._leaves_design(machine, "designing", "red")
        assert not pipeline_domain._leaves_design(machine, "designing", "nowhere")
        assert not pipeline_domain._leaves_design(machine, "red", "green")
        assert not pipeline_domain._leaves_design(machine, "designing", "abandoned")


# -- the notice -------------------------------------------------------------------


class TestTheNotice:
    def test_it_names_the_documents_changed(self):
        lines = design_snapshot_notice(
            {"answerable": True, "docs": [{"path": DOC, "digest": "abc"}]}
        )
        assert lines == [f"design snapshot  1 design document(s) changed: {DOC}"]

    def test_with_none_it_says_the_gate_will_fail(self):
        lines = design_snapshot_notice({"answerable": True, "docs": []})
        assert lines[0] == "design snapshot  no design document differs from the merge base"
        assert "will fail docs.ahead_of_code at review" in lines[1]
        assert "write the doc now" in lines[1]

    def test_an_unanswerable_one_says_why(self):
        lines = design_snapshot_notice({"answerable": False, "problem": "git said no", "docs": []})
        assert "could not be read: git said no" in lines[0]
        assert "docs.ahead_of_code" in lines[1]

    def test_many_documents_are_counted_past_the_first_five(self):
        docs = [{"path": f"docs/architecture/{i}.md", "digest": "x"} for i in range(7)]
        (line,) = design_snapshot_notice({"answerable": True, "docs": docs})
        assert "7 design document(s)" in line and "and 2 more" in line


# -- the judgement ----------------------------------------------------------------


def judge(flow: Flow, snapshots: list[dict] | None, history=None, state="green"):
    pipeline = flow.pipelines.require(flow.sid).to_dict()
    pipeline["state"] = state
    if history is not None:
        pipeline["history"] = history
    evidence = dict(pipeline.get("evidence") or {})
    if snapshots is None:
        evidence.pop(DESIGN_SNAPSHOTS, None)
    else:
        evidence[DESIGN_SNAPSHOTS] = snapshots
    pipeline["evidence"] = evidence
    return get_rule(RULE).fn(flow.ctx(pipeline))


def snap(docs=(), source=False, answerable=True, problem=None) -> dict:
    return {
        "from": "designing", "to": "red", "step": 0, "leg": "repo",
        "answerable": answerable, "problem": problem,
        "docs": [{"path": p, "digest": "x"} for p in docs],
        "source_changed": source,
    }


LEFT_DESIGN = [{"from": "designing", "to": "red"}]


class TestTheJudgement:
    def test_a_change_with_no_structural_source_is_clean(self, tmp_path):
        flow = Flow(tmp_path)
        write(flow.root, "tests/test_core.py")
        verdict = judge(flow, [snap()], LEFT_DESIGN)
        assert verdict.ok and not verdict.skipped
        assert "no structural change" in verdict.message
        assert "tests/test_core.py" in verdict.detail

    def test_nothing_changed_is_not_applicable(self, tmp_path):
        flow = Flow(tmp_path)
        verdict = judge(flow, [snap()], LEFT_DESIGN)
        assert verdict.skipped and verdict.message == "nothing changed"

    def test_an_unestablished_change_fails(self, tmp_path):
        flow = Flow(tmp_path)
        flow.session.base = "no-such-branch"
        verdict = judge(flow, [snap()], LEFT_DESIGN)
        assert not verdict.ok
        assert "could not be established" in verdict.message

    def test_a_pipeline_begun_before_the_build_is_not_applicable(self, tmp_path):
        flow = Flow(tmp_path)
        write(flow.root, SOURCE)
        verdict = judge(flow, None, LEFT_DESIGN)
        assert verdict.skipped
        assert "before design snapshots were recorded" in verdict.message

    def test_no_pipeline_is_not_applicable(self, tmp_path):
        flow = Flow(tmp_path)
        verdict = get_rule(RULE).fn(
            RuleContext(manifest=flow.m, cwd=flow.root, session=flow.session.to_dict())
        )
        assert verdict.skipped and "no pipeline" in verdict.message

    def test_an_unloadable_methodology_fails(self, tmp_path):
        flow = Flow(tmp_path)
        pipeline = flow.pipelines.require(flow.sid).to_dict()
        pipeline["methodology"] = "no-such-lane"
        verdict = get_rule(RULE).fn(flow.ctx(pipeline))
        assert not verdict.ok and "no-such-lane" in verdict.message

    def test_still_in_design_with_no_snapshot_is_not_applicable(self, tmp_path):
        flow = Flow(tmp_path)
        write(flow.root, SOURCE)
        verdict = judge(flow, None, [], state="green")
        assert verdict.skipped
        assert "has not left its design state" in verdict.message

    def test_source_before_the_docs_fails_even_when_a_later_pass_wrote_them(self, tmp_path):
        flow = Flow(tmp_path)
        write(flow.root, SOURCE)
        verdict = judge(flow, [snap(source=True), snap(docs=[DOC])], LEFT_DESIGN)
        assert not verdict.ok and verdict.message == NOT_BEFORE
        assert "designing -> red" in verdict.detail

    def test_a_second_design_pass_that_writes_the_docs_passes(self, tmp_path):
        flow = Flow(tmp_path)
        write(flow.root, SOURCE)
        verdict = judge(flow, [snap(), snap(docs=[DOC])], LEFT_DESIGN)
        assert verdict.ok and not verdict.skipped
        assert DOC in verdict.message

    def test_a_malformed_record_reads_as_none(self, tmp_path):
        flow = Flow(tmp_path)
        write(flow.root, SOURCE)
        pipeline = flow.pipelines.require(flow.sid).to_dict()
        pipeline["state"] = "green"
        pipeline["history"] = LEFT_DESIGN
        pipeline["evidence"] = {**pipeline["evidence"], DESIGN_SNAPSHOTS: "not a list"}
        verdict = get_rule(RULE).fn(flow.ctx(pipeline))
        assert verdict.skipped and "before design snapshots" in verdict.message

    def test_an_unanswerable_snapshot_fails_naming_why(self, tmp_path):
        flow = Flow(tmp_path)
        write(flow.root, SOURCE)
        verdict = judge(flow, [snap(answerable=False, problem="git said no")], LEFT_DESIGN)
        assert not verdict.ok
        assert "git said no" in verdict.detail

    def test_design_straight_into_review_is_judged_on_the_live_snapshot(self, tmp_path):
        flow = Flow(tmp_path, methodology="docs")
        write(flow.root, SOURCE)
        assert flow.gate("reviewing").verdict.message == NOT_BEFORE
        write(flow.root, DOC, "# Overview\n\nchanged\n")
        result = flow.gate("reviewing")
        assert passed(result), result.verdict.message
        flow.advance("reviewing")
        assert [d["path"] for d in flow.snapshots()[0]["docs"]] == [DOC]


# -- the wiring -------------------------------------------------------------------


class TestTheWiring:
    @pytest.mark.parametrize("lane", ["tdd", "chore", "bugfix", "docs", "spec-first"])
    def test_every_review_state_enters_through_the_gate(self, lane, tmp_path):
        machine = machines.load_pipeline_machine(None, lane)
        reviews = machine.states_with_role("review")
        assert reviews
        for state in reviews:
            assert RULE in machine.state(state).entry_gates
        others = set(machine.states) - set(reviews)
        assert not any(RULE in machine.state(s).entry_gates for s in others)

    @pytest.mark.parametrize(
        ("profile", "level"),
        [("standard", Level.BLOCK), ("strict", Level.BLOCK), ("loose", Level.WARN)],
    )
    def test_its_level_in_each_profile(self, profile, level, tmp_path):
        m = git_project(
            tmp_path / "proj",
            {"version": 1, "project": {"name": "demo"}, "enforcement": {"profile": profile}},
        )
        assert Manifest.locate(m.root).rule_level(RULE) is level

    def test_the_key_is_the_rules_own(self):
        assert rules.DESIGN_SNAPSHOTS == DESIGN_SNAPSHOTS

    def test_the_decision_directives_come_from_the_types(self):
        types = needs.default_types()
        assert needs.directives_of(types, ("decision",)) == frozenset({"dec"})
        custom = (needs.NeedType("adr", "ADR_", "decision"), *types)
        assert needs.directives_of(custom, ("decision",)) == frozenset({"adr", "dec"})


# -- the store leg ----------------------------------------------------------------


class TestTheStoreLeg:
    """A store-docs project reads the committed store leg, as docs.updated
    counts it: committed under a span of its unit that also holds its read."""

    def test_a_doc_committed_in_the_store_under_the_lock_counts(
        self, hosts, capsys  # noqa: F811 - the fixture
    ):
        from test_adr_014_per_leg_gates import (
            DIAGRAM,
            SID,
            change_source,
            commit,
            ctx_for,
            read_doc,
            unlock,
            write_doc,
        )
        read_doc(hosts, hosts.overview)
        write_doc(hosts.store, "docs/architecture/overview.rst", DIAGRAM)
        uncommitted = design_snapshot(ctx_for(hosts.widgets))
        assert uncommitted["leg"] == "store"
        assert uncommitted["docs"] == []

        commit(hosts.store)
        unlock(hosts, capsys)
        snap_ = design_snapshot(ctx_for(hosts.widgets))
        assert [d["path"] for d in snap_["docs"]] == ["docs/architecture/overview.rst"]

        # The advance records it and prints it; the review gate reads it.
        from test_cli import out, run

        hosts.at(hosts.widgets)
        capsys.readouterr()
        assert run("pipeline", "advance", "red", "--id", SID) == 0
        assert "design snapshot  1 design document(s) changed" in out(capsys)
        change_source(hosts.widgets)
        assert run("pipeline", "advance", "green", "--force", "--reason", "t", "--id", SID) == 0
        manifest = Manifest.locate(hosts.widgets)
        verdicts = PipelineDomain(manifest).evaluate_gates(
            SID,
            "reviewing",
            session=SessionDomain(manifest).require(SID).to_dict(),
            cwd=hosts.widgets,
        )
        (result,) = [v for v in verdicts if v.rule_id == RULE]
        assert passed(result), result.verdict.message


# -- the command ------------------------------------------------------------------


class TestTheCommand:
    def test_advance_prints_the_snapshot_and_json_carries_it(self, tmp_path, monkeypatch, capsys):
        from test_cli import out, payload, run

        flow = Flow(tmp_path)
        monkeypatch.chdir(flow.root)
        monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
        capsys.readouterr()
        assert run("pipeline", "advance", "red", "--id", flow.sid) == 0
        text = out(capsys)
        assert "design snapshot  no design document differs from the merge base" in text
        assert "will fail docs.ahead_of_code at review" in text

        # A later advance takes none and prints none; --json carries none.
        assert run("--json", "pipeline", "advance", "green", "--force", "--reason", "t",
                   "--id", flow.sid) == 0
        body = payload(capsys)
        assert body["design_snapshot"] is None
