"""An evidence writer survives a malformed field it reads (ADR-012 A7 increment 4).

The D1 permutation test found this in `record_agent_run`: an `agents_run_how`
that is on the record and is not a mapping raised, the failed save took the
`agents_run` append with it, the hook still exited 0, and both role gates moved
from pass to warn at the landing. The user's ruling is to fix the writer, so a
field of the wrong shape reads as empty. The same read was then checked in every
other writer, and each one found is pinned here. The one reader with no net
under it, `apply_acknowledgements`, is pinned at the end.
"""

from __future__ import annotations

import json

import pytest
from test_domains import git_project

from invariant.core.manifest import Level
from invariant.core.rules import (
    Evaluated,
    RuleContext,
    Verdict,
    apply_acknowledgements,
    evaluate_event,
)
from invariant.domains.pipeline import (
    ALREADY,
    RECORDED,
    VIA_ACTOR,
    VIA_DISPATCH,
    PipelineDomain,
)

SID = "s1"
AGENT = "reviewer"

#: What a field meant to hold a list or a mapping could be instead, one per JSON
#: type. Each writer's own right shape is left out by the two tables below.
SCALARS = {"null": None, "number": 7, "boolean": True, "text": "text", "empty-text": ""}
NOT_A_LIST = {**SCALARS, "mapping": {"a": 1}, "empty-mapping": {}}
NOT_A_MAPPING = {**SCALARS, "list": ["a", 1, None], "empty-list": []}
TRUTHY = {"number": 7, "boolean": True, "text": "text", "list": ["a"]}


def shapes(table: dict) -> dict:
    return {"argvalues": list(table.values()), "ids": list(table)}


@pytest.fixture
def m(tmp_path):
    return git_project(
        tmp_path / "proj",
        {
            "version": 1,
            "project": {"name": "p", "ecosystems": ["python"]},
            "commands": {
                "test": "echo '1 passed in 0.1s'",
                "coverage": "echo '1 passed in 0.1s'; echo 'a.py 10 1 90%'",
            },
        },
    )


@pytest.fixture
def pipelines(m):
    domain = PipelineDomain(m)
    domain.start(SID, task="t")
    return domain


def given(pipelines: PipelineDomain, **fields) -> None:
    """Put these on the record, on disk, the way a damaged record would hold them."""
    path = pipelines.store.root / pipelines.relpath(SID)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["current"]["evidence"].update(fields)
    path.write_text(json.dumps(document), encoding="utf-8")


def evidence(pipelines: PipelineDomain) -> dict:
    """What a fresh reader finds, so a write that was lost shows as lost."""
    return PipelineDomain(pipelines.manifest).require(SID).evidence


class TestRecordAgentRun:
    @pytest.mark.parametrize("value", **shapes(NOT_A_MAPPING))
    def test_a_malformed_how_reads_as_empty_and_the_run_is_kept(self, pipelines, value):
        given(pipelines, agents_run_how=value)
        assert pipelines.record_agent_run(SID, AGENT) == RECORDED
        found = evidence(pipelines)
        assert found["agents_run"] == [AGENT]
        assert found["agents_run_how"] == {AGENT: VIA_DISPATCH}

    @pytest.mark.parametrize("value", **shapes(NOT_A_MAPPING))
    def test_an_agent_already_listed_gets_its_how_again(self, pipelines, value):
        given(pipelines, agents_run=[AGENT], agents_run_how=value)
        assert pipelines.record_agent_run(SID, AGENT, how=VIA_ACTOR) == RECORDED
        found = evidence(pipelines)
        assert found["agents_run"] == [AGENT]
        assert found["agents_run_how"] == {AGENT: VIA_ACTOR}

    @pytest.mark.parametrize("value", **shapes(NOT_A_LIST))
    def test_a_malformed_run_list_reads_as_empty(self, pipelines, value):
        given(pipelines, agents_run=value)
        assert pipelines.record_agent_run(SID, AGENT) == RECORDED
        found = evidence(pipelines)
        assert found["agents_run"] == [AGENT]
        assert found["agents_run_how"] == {AGENT: VIA_DISPATCH}

    def test_the_second_call_finds_nothing_to_do(self, pipelines):
        given(pipelines, agents_run_how=None)
        pipelines.record_agent_run(SID, AGENT)
        assert pipelines.record_agent_run(SID, AGENT) == ALREADY

    def test_a_well_formed_record_keeps_what_it_holds(self, pipelines):
        given(pipelines, agents_run=["other"], agents_run_how={"other": VIA_ACTOR})
        pipelines.record_agent_run(SID, AGENT)
        found = evidence(pipelines)
        assert found["agents_run"] == ["other", AGENT]
        assert found["agents_run_how"] == {"other": VIA_ACTOR, AGENT: VIA_DISPATCH}

    def test_an_actor_reading_is_still_not_downgraded(self, pipelines):
        given(pipelines, agents_run=[AGENT], agents_run_how={AGENT: VIA_ACTOR})
        assert pipelines.record_agent_run(SID, AGENT, how=VIA_DISPATCH) == ALREADY
        assert evidence(pipelines)["agents_run_how"] == {AGENT: VIA_ACTOR}


class TestRecordDocsRead:
    @pytest.mark.parametrize("value", **shapes(NOT_A_LIST))
    def test_a_malformed_list_reads_as_empty(self, pipelines, value):
        given(pipelines, docs_read=value)
        pipelines.record_docs_read(SID, "a.md")
        assert evidence(pipelines)["docs_read"] == ["a.md"]

    def test_a_path_inside_a_text_value_is_not_read_as_recorded(self, pipelines):
        """A string answers `in` by substring, so this one would have written nothing."""
        given(pipelines, docs_read="docs/a.md and more")
        pipelines.record_docs_read(SID, "a.md")
        assert evidence(pipelines)["docs_read"] == ["a.md"]

    def test_a_well_formed_list_keeps_what_it_holds(self, pipelines):
        given(pipelines, docs_read=["b.md"])
        pipelines.record_docs_read(SID, "a.md")
        assert evidence(pipelines)["docs_read"] == ["b.md", "a.md"]


class TestRecordVerdicts:
    @staticmethod
    def record(pipelines) -> dict:
        state = pipelines.require(SID).state
        return {"rule": "a.rule", "status": "pass", "state": state, "token": "tok"}

    @pytest.mark.parametrize("value", **shapes(NOT_A_LIST))
    def test_a_malformed_list_reads_as_empty(self, pipelines, value):
        given(pipelines, verdicts=value)
        record = self.record(pipelines)
        assert pipelines.record_verdicts(SID, [record]) == 1
        assert evidence(pipelines)["verdicts"] == [record]

    def test_a_well_formed_list_keeps_what_it_holds(self, pipelines):
        record = self.record(pipelines)
        given(pipelines, verdicts=[record])
        pipelines.record_verdicts(SID, [record])
        assert evidence(pipelines)["verdicts"] == [record, record]


class TestStampVerdictAgent:
    @pytest.mark.parametrize("value", **shapes(NOT_A_LIST))
    def test_a_malformed_list_holds_nothing_to_stamp_and_stays(self, pipelines, value):
        given(pipelines, verdicts=value)
        assert pipelines.stamp_verdict_agent(SID, "tok", AGENT) == 0
        assert evidence(pipelines)["verdicts"] == value


class TestTheTestRuns:
    @pytest.mark.parametrize("value", **shapes(NOT_A_LIST))
    def test_a_malformed_list_reads_as_empty_and_the_run_is_kept(self, pipelines, m, value):
        given(pipelines, test_runs=value)
        pipelines.run_tests(SID, phase="green", cwd=m.root)
        (run,) = evidence(pipelines)["test_runs"]
        assert run["phase"] == "green"
        assert run["passed"] is True

    def test_a_well_formed_list_keeps_what_it_holds(self, pipelines, m):
        given(pipelines, test_runs=[{"phase": "red"}])
        pipelines.run_tests(SID, phase="green", cwd=m.root)
        assert [r["phase"] for r in evidence(pipelines)["test_runs"]] == ["red", "green"]


class TestAcknowledge:
    RULE = "test.new_test_fails"

    @pytest.mark.parametrize("value", **shapes(NOT_A_MAPPING))
    def test_a_malformed_mapping_reads_as_empty(self, pipelines, value):
        given(pipelines, acknowledged=value)
        pipelines.acknowledge(SID, self.RULE, "because")
        assert evidence(pipelines)["acknowledged"] == {self.RULE: "because"}

    def test_a_well_formed_mapping_keeps_what_it_holds(self, pipelines):
        given(pipelines, acknowledged={"other": "before"})
        pipelines.acknowledge(SID, self.RULE, "because")
        assert evidence(pipelines)["acknowledged"] == {"other": "before", self.RULE: "because"}


class TestTheLandingRecord:
    @staticmethod
    def land(pipelines, monkeypatch) -> None:
        record = pipelines.require(SID)
        record.state = "reviewing"
        pipelines._save(SID, record)
        gate = Evaluated("guidelines.ruleset_valid", Level.BLOCK, Verdict.clean("loads"))
        monkeypatch.setattr(pipelines, "evaluate_gates", lambda *a, **k: [gate])
        pipelines.advance(SID, "committed")

    @pytest.mark.parametrize("value", **shapes(NOT_A_LIST))
    def test_a_malformed_list_reads_as_empty_and_the_landing_is_kept(
            self, pipelines, monkeypatch, value):
        given(pipelines, guidelines_landings=value)
        self.land(pipelines, monkeypatch)
        assert pipelines.require(SID).state == "committed"
        (entry,) = evidence(pipelines)["guidelines_landings"]
        assert (entry["from"], entry["to"]) == ("reviewing", "committed")

    def test_a_well_formed_list_keeps_what_it_holds(self, pipelines, monkeypatch):
        given(pipelines, guidelines_landings=[{"from": "earlier"}])
        self.land(pipelines, monkeypatch)
        assert [e["from"] for e in evidence(pipelines)["guidelines_landings"]] == [
            "earlier", "reviewing"]


class TestTheWritersThatReadNothing:
    """Each replaces its field whole, so a malformed one cannot reach them. Pinned
    because a later change that reads the old value first would bring the failure."""

    @pytest.mark.parametrize("value", **shapes({**NOT_A_MAPPING, "mapping": {"a": 1}}))
    def test_record_coverage(self, pipelines, value):
        given(pipelines, coverage=value)
        pipelines.record_coverage(SID, {"a.py": 90.0})
        assert evidence(pipelines)["coverage"] == {"a.py": 90.0}

    @pytest.mark.parametrize("value", **shapes(NOT_A_MAPPING))
    def test_run_coverage(self, pipelines, m, value):
        given(pipelines, coverage=value, coverage_run=value)
        pipelines.run_coverage(SID, cwd=m.root)
        found = evidence(pipelines)
        assert found["coverage"] == {"a.py": 90.0}
        assert found["coverage_run"]["suite_passed"] is True


class TestTheAcknowledgementReader:
    """A reader, and not a rule, so `evaluate` does not catch what it raises. A
    truthy `acknowledged` of the wrong shape stopped a landing with a traceback and
    turned every pre-tool-use and stop hook off, through the degraded path."""

    RULE = "test.new_test_fails"

    @classmethod
    def warned(cls) -> list[Evaluated]:
        return [Evaluated(cls.RULE, Level.WARN, Verdict.fail("owed"))]

    @pytest.mark.parametrize("value", **shapes(TRUTHY))
    def test_it_holds_no_acknowledgement_and_the_verdict_stands(self, value):
        verdicts = self.warned()
        assert apply_acknowledgements({"evidence": {"acknowledged": value}}, verdicts) is verdicts

    def test_a_well_formed_one_still_acknowledges(self):
        pipeline = {"evidence": {"acknowledged": {self.RULE: "because"}}}
        (found,) = apply_acknowledgements(pipeline, self.warned())
        assert found.verdict.skipped is True
        assert "because" in found.verdict.message

    @pytest.mark.parametrize("value", **shapes(TRUTHY))
    def test_the_gates_still_answer(self, pipelines, m, value):
        given(pipelines, acknowledged=value)
        answered = pipelines.evaluate_gates_by_destination(SID, cwd=m.root)
        assert answered
        assert evidence(pipelines)["acknowledged"] == value

    @pytest.mark.parametrize("event", ["pre-tool-use", "stop"])
    @pytest.mark.parametrize("value", **shapes(TRUTHY))
    def test_the_hook_route_still_answers(self, m, event, value):
        pipeline = {"state": "reviewing", "evidence": {"acknowledged": value}}
        ctx = RuleContext(manifest=m, cwd=m.root, session=None, pipeline=pipeline)
        assert isinstance(evaluate_event(event, ctx), list)
