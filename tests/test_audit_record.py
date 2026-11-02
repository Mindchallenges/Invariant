"""The audit record (IDEA_031 A7 increment 3, ADR-012 rulings 9, 10 and 11).

``evidence.audit`` says which adapter saw which subagent, its effective tier,
the model it was asked to run on and the models its transcript shows. A host
writes it through ``PipelineDomain.record_audit``; ``RuleContext`` removes it
as it is built, so no rule can see it. ``tests/test_guidelines_verdict_agent.py``
holds the DN-12 pin over it and over the verdict's model stamp.

Every model value here is a placeholder (ruling 4). The hooks are driven only
through their functions, with fixture payloads and transcripts under a
temporary directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_cli import payload, run
from test_domains import git_project
from test_host_claude_code import FakeCtx, FakePipelines, FakeSession

from invariant.core.errors import InvariantError
from invariant.core.manifest import Manifest
from invariant.core.rules import AUDIT_KEY, RuleContext
from invariant.domains.pipeline import PipelineDomain, audit_line
from invariant.domains.session import Resolution, SessionDomain
from invariant.hosts.claude_code import ClaudeCodeHost
from invariant.hosts.claude_code_telemetry import last_model, last_turn, subagent_model

MODEL_A = "model-placeholder-a"
MODEL_B = "model-placeholder-b"
RUN = "a0run"
TOKEN = "0123456789abcdef" * 2


def assistant(model=MODEL_A, **extra) -> dict:
    return {"type": "assistant", "message": {"model": model, "content": []}, **extra}


def lines(*records) -> str:
    return "".join(json.dumps(r) + "\n" for r in records)


def run_transcript(tmp_path: Path, *records, run_id: str = RUN) -> Path:
    """A main transcript's path, with the subagent's own file laid out beside it."""
    main = tmp_path / "host" / "h1.jsonl"
    own = main.parent / main.stem / "subagents" / f"agent-{run_id}.jsonl"
    own.parent.mkdir(parents=True)
    own.write_text(lines(*records), encoding="utf-8")
    return main


# -- the bounded read (hosts/claude_code_telemetry) ---------------------------------------


class TestLastModel:
    def _file(self, tmp_path, text: str) -> Path:
        path = tmp_path / "agent.jsonl"
        path.write_text(text, encoding="utf-8")
        return path

    def test_the_newest_assistant_line_answers(self, tmp_path):
        path = self._file(tmp_path, lines(assistant(MODEL_A), {"type": "user"},
                                          assistant(MODEL_B), {"type": "last-prompt"}))
        assert last_model(path) == MODEL_B

    @pytest.mark.parametrize("record", [
        {"type": "user", "message": {"model": MODEL_B}},
        {"type": "assistant", "message": {}},
        {"type": "assistant", "message": "text"},
        {"type": "assistant", "message": {"model": ""}},
        {"type": "assistant", "message": {"model": 7}},
        {"type": "assistant", "message": {"model": "<synthetic>"}},
        ["not", "an", "object"],
    ])
    def test_a_line_without_a_real_model_is_passed_over(self, tmp_path, record):
        path = self._file(tmp_path, lines(assistant(MODEL_A), record))
        assert last_model(path) == MODEL_A

    def test_garbage_is_passed_over(self, tmp_path):
        path = self._file(tmp_path, lines(assistant(MODEL_A)) + "{not json\n")
        assert last_model(path) == MODEL_A

    def test_a_line_still_being_written_is_skipped(self, tmp_path):
        path = self._file(tmp_path, lines(assistant(MODEL_A)) + json.dumps(assistant(MODEL_B)))
        assert last_model(path) == MODEL_A

    def test_a_line_nested_past_the_parsers_depth_is_passed_over(self, tmp_path):
        """That raises RecursionError, which is not a ValueError."""
        path = self._file(tmp_path, lines(assistant(MODEL_A)) + "[" * 200_000 + "\n")
        assert last_model(path) == MODEL_A

    def test_the_turn_reader_passes_over_the_same_line(self, tmp_path):
        """The shared tail, read for a turn: the oracle's probe asks this one."""
        turn = {"type": "assistant", "timestamp": "2026-01-01T00:00:00Z", "message": {}}
        path = self._file(tmp_path, lines(turn) + "[" * 200_000 + "\n")
        assert last_turn(path) is not None

    def test_the_read_is_bounded(self, tmp_path):
        """A model further back than the budget is null, not a longer read."""
        path = self._file(tmp_path, lines(assistant(MODEL_A)) + "x" * 500 + "\n")
        assert last_model(path, block_size=64, budget=128) is None
        assert last_model(path, block_size=64) == MODEL_A

    def test_a_missing_file_is_null(self, tmp_path):
        assert last_model(tmp_path / "absent.jsonl") is None

    def test_a_directory_is_null(self, tmp_path):
        assert last_model(tmp_path) is None

    def test_an_empty_file_is_null(self, tmp_path):
        assert last_model(self._file(tmp_path, "")) is None


class TestSubagentModel:
    def test_it_reads_that_runs_own_transcript(self, tmp_path):
        main = run_transcript(tmp_path, assistant(MODEL_B))
        # The main transcript and a sibling run say otherwise, and are not read.
        main.write_text(lines(assistant(MODEL_A)), encoding="utf-8")
        sibling = main.parent / main.stem / "subagents" / "agent-other.jsonl"
        sibling.write_text(lines(assistant(MODEL_A)), encoding="utf-8")
        assert subagent_model(str(main), RUN) == MODEL_B

    @pytest.mark.parametrize("main, run_id", [
        (None, RUN), (7, RUN), ("", RUN), ("/x/h1.jsonl", None), ("/x/h1.jsonl", ""),
        ("/x/h1.jsonl", 7), ("/x/h1.jsonl", "../escape"), ("/x/h1.jsonl", "a/b"),
        ("/x/h1.jsonl", "a\\b"), ("/x/h1.jsonl", "a\0b"), ("/x/\0h1.jsonl", RUN),
    ])
    def test_anything_but_two_clean_strings_is_null(self, main, run_id):
        assert subagent_model(main, run_id) is None

    def test_no_such_run_is_null(self, tmp_path):
        main = run_transcript(tmp_path, assistant())
        assert subagent_model(str(main), "other") is None

    def test_dots_in_an_id_stay_inside_the_file_name(self, tmp_path):
        """With no separator there is no path to walk: `..` is only part of a name."""
        main = run_transcript(tmp_path, assistant(), run_id="..")
        assert subagent_model(str(main), "..") == MODEL_A
        (main.parent / "agent-...jsonl").write_text(lines(assistant(MODEL_B)), encoding="utf-8")
        assert subagent_model(str(main), "..") == MODEL_A

    def test_a_transcript_too_deep_to_parse_is_null_and_raises_nothing(self, tmp_path):
        main = tmp_path / "h1.jsonl"
        own = tmp_path / "h1" / "subagents" / f"agent-{RUN}.jsonl"
        own.parent.mkdir(parents=True)
        own.write_text("[" * 200_000 + "\n", encoding="utf-8")
        assert subagent_model(str(main), RUN) is None


# -- no rule can see it (core/rules) ----------------------------------------------------


class TestRuleContext:
    def _pipeline(self, **extra) -> dict:
        return {"state": "reviewing",
                "evidence": {"docs_read": ["a"], AUDIT_KEY: {"host": "h"}, **extra}}

    def _ctx(self, tmp_path, pipeline):
        root = tmp_path / "proj"
        root.mkdir(exist_ok=True)
        return RuleContext(manifest=None, cwd=root, pipeline=pipeline)  # type: ignore[arg-type]

    def test_it_is_gone_however_it_is_asked_for(self, tmp_path):
        ctx = self._ctx(tmp_path, self._pipeline())
        assert ctx.evidence(AUDIT_KEY) is None
        assert AUDIT_KEY not in ctx.pipeline["evidence"]

    def test_the_rest_of_the_evidence_stays(self, tmp_path):
        ctx = self._ctx(tmp_path, self._pipeline())
        assert ctx.evidence("docs_read") == ["a"]
        assert ctx.pipeline["state"] == "reviewing"

    def test_the_callers_record_is_not_touched(self, tmp_path):
        """The caller may go on to save the record it passed."""
        pipeline = self._pipeline()
        self._ctx(tmp_path, pipeline)
        assert pipeline["evidence"][AUDIT_KEY] == {"host": "h"}

    @pytest.mark.parametrize("pipeline", [
        None, {}, {"state": "x"}, {"evidence": None}, {"evidence": {"docs_read": []}},
        {"evidence": "junk"},
    ])
    def test_a_record_without_it_is_the_same_object(self, tmp_path, pipeline):
        assert self._ctx(tmp_path, pipeline).pipeline is pipeline

    def test_the_gate_evaluation_context_has_none(self, tmp_path, monkeypatch):
        """Whoever builds it: the oracle's gate cache evaluates through this."""
        import invariant.domains.pipeline as module

        m = git_project(tmp_path / "proj")
        session = SessionDomain(m).start("audit")
        pipelines = PipelineDomain(m)
        pipelines.start(session.id, task="t")
        pipelines.record_audit(session.id, host="h", agent="reviewer", model=MODEL_A)
        seen: list = []
        original = module.evaluate

        def spy(gate_ids, ctx):
            seen.append(ctx)
            return original(gate_ids, ctx)

        monkeypatch.setattr(module, "evaluate", spy)
        pipelines.evaluate_gates_by_destination(
            session.id, session=session.to_dict(), cwd=m.root
        )
        assert seen
        assert all(AUDIT_KEY not in c.pipeline["evidence"] for c in seen)


# -- the record (domains/pipeline) ---------------------------------------------------------


@pytest.fixture
def domain(tmp_path):
    m = git_project(tmp_path / "proj")
    session = SessionDomain(m).start("audit")
    pipelines = PipelineDomain(m)
    pipelines.start(session.id, task="t")
    return pipelines, session.id


def stored(pipelines, sid):
    return PipelineDomain(pipelines.manifest).current(sid).evidence.get("audit")


class TestRecordAudit:
    def test_the_shape(self, domain):
        pipelines, sid = domain
        assert pipelines.record_audit(
            sid, host="claude_code", agent="reviewer", tier="standard",
            rendered=MODEL_A, model=MODEL_B, run=RUN,
        ) is True
        assert stored(pipelines, sid) == {
            "host": "claude_code",
            "agents": {"reviewer": {
                "tier": "standard", "asked": MODEL_A, "models": [MODEL_B], "runs": [RUN],
            }},
        }

    def test_a_missing_fact_stays_null(self, domain):
        pipelines, sid = domain
        pipelines.record_audit(sid, host="h", agent="reviewer")
        assert stored(pipelines, sid)["agents"]["reviewer"] == {
            "tier": None, "asked": None, "models": [], "runs": [],
        }

    @pytest.mark.parametrize("bad", [7, "", ["x"], True])
    def test_a_value_that_is_not_text_stays_null(self, domain, bad):
        pipelines, sid = domain
        pipelines.record_audit(sid, host="h", agent="reviewer", tier=bad, requested=bad,
                               rendered=bad, model=bad, run=bad)
        assert stored(pipelines, sid)["agents"]["reviewer"] == {
            "tier": None, "asked": None, "models": [], "runs": [],
        }

    def test_each_agent_has_its_own_entry(self, domain):
        pipelines, sid = domain
        pipelines.record_audit(sid, host="h", agent="one", model=MODEL_A)
        pipelines.record_audit(sid, host="h", agent="two", model=MODEL_B)
        agents = stored(pipelines, sid)["agents"]
        assert agents["one"]["models"] == [MODEL_A]
        assert agents["two"]["models"] == [MODEL_B]

    def test_models_and_runs_gather_once_each(self, domain):
        pipelines, sid = domain
        for model, run_id in ((MODEL_A, "r1"), (MODEL_B, "r2"), (MODEL_A, "r1")):
            pipelines.record_audit(sid, host="h", agent="a", model=model, run=run_id)
        entry = stored(pipelines, sid)["agents"]["a"]
        assert entry["models"] == [MODEL_A, MODEL_B]
        assert entry["runs"] == ["r1", "r2"]

    def test_the_request_at_dispatch_outranks_the_rendered_line(self, domain):
        """Whichever arrives first. A foreground subagent acts before the hook
        sees the dispatch that started it."""
        pipelines, sid = domain
        pipelines.record_audit(sid, host="h", agent="a", rendered=MODEL_A)
        pipelines.record_audit(sid, host="h", agent="a", requested=MODEL_B)
        pipelines.record_audit(sid, host="h", agent="a", rendered=MODEL_A)
        assert stored(pipelines, sid)["agents"]["a"]["asked"] == MODEL_B

    def test_the_rendered_line_fills_a_null(self, domain):
        pipelines, sid = domain
        pipelines.record_audit(sid, host="h", agent="a")
        pipelines.record_audit(sid, host="h", agent="a", rendered=MODEL_A)
        assert stored(pipelines, sid)["agents"]["a"]["asked"] == MODEL_A

    def test_a_null_never_clears_a_fact(self, domain):
        pipelines, sid = domain
        pipelines.record_audit(sid, host="h", agent="a", tier="deep", rendered=MODEL_A)
        pipelines.record_audit(sid, host="h", agent="a")
        entry = stored(pipelines, sid)["agents"]["a"]
        assert (entry["tier"], entry["asked"]) == ("deep", MODEL_A)

    def test_the_first_adapter_stays(self, domain):
        pipelines, sid = domain
        pipelines.record_audit(sid, host="first", agent="a")
        pipelines.record_audit(sid, host="second", agent="a")
        assert stored(pipelines, sid)["host"] == "first"

    def test_nothing_new_writes_nothing(self, domain):
        pipelines, sid = domain
        assert pipelines.record_audit(sid, host="h", agent="a", model=MODEL_A) is True
        assert pipelines.record_audit(sid, host="h", agent="a", model=MODEL_A) is False

    @pytest.mark.parametrize("host, agent", [("", "a"), (None, "a"), ("h", ""), ("h", None)])
    def test_no_host_or_agent_records_nothing(self, domain, host, agent):
        pipelines, sid = domain
        assert pipelines.record_audit(sid, host=host, agent=agent) is False
        assert stored(pipelines, sid) is None

    def test_no_pipeline_records_nothing(self, tmp_path):
        m = git_project(tmp_path / "proj")
        session = SessionDomain(m).start("idle")
        assert PipelineDomain(m).record_audit(session.id, host="h", agent="a") is False

    @pytest.mark.parametrize("junk", [
        "junk",
        {"host": "h", "agents": "junk"},
        {"host": "h", "agents": {"a": "junk"}},
        {"host": "h", "agents": {"a": {"models": "junk", "runs": []}}},
        {"host": "h", "agents": {"a": {"models": [], "runs": "junk"}}},
    ])
    def test_a_malformed_record_is_left_alone(self, domain, junk):
        pipelines, sid = domain
        p = pipelines.require(sid)
        p.evidence["audit"] = junk
        pipelines._save(sid, p)
        assert pipelines.record_audit(sid, host="h", agent="a", model=MODEL_A) is False
        assert stored(pipelines, sid) == junk


class TestAuditUnread:
    def test_a_new_run_is_unread_and_a_read_one_is_not(self, domain):
        pipelines, sid = domain
        assert pipelines.audit_unread(sid, "a", RUN) is True
        pipelines.record_audit(sid, host="h", agent="a", run=RUN)
        assert pipelines.audit_unread(sid, "a", RUN) is False
        assert pipelines.audit_unread(sid, "a", "another") is True

    def test_with_no_run_id_an_agent_is_read_once(self, domain):
        pipelines, sid = domain
        assert pipelines.audit_unread(sid, "a", None) is True
        pipelines.record_audit(sid, host="h", agent="a")
        assert pipelines.audit_unread(sid, "a", None) is False
        assert pipelines.audit_unread(sid, "b", None) is True

    def test_no_pipeline_has_nothing_to_read_for(self, tmp_path):
        m = git_project(tmp_path / "proj")
        session = SessionDomain(m).start("idle")
        assert PipelineDomain(m).audit_unread(session.id, "a", RUN) is False

    @pytest.mark.parametrize("junk", ["junk", {"agents": "junk"}, {"agents": {"a": "junk"}},
                                      {"agents": {"a": {"runs": "junk"}}}])
    def test_a_malformed_record_is_not_read_for(self, domain, junk):
        pipelines, sid = domain
        p = pipelines.require(sid)
        p.evidence["audit"] = junk
        pipelines._save(sid, p)
        assert pipelines.audit_unread(sid, "a", RUN) is False

    @pytest.mark.parametrize("agent", ["", None, 7])
    def test_no_agent_is_not_read_for(self, domain, agent):
        pipelines, sid = domain
        assert pipelines.audit_unread(sid, agent, RUN) is False


class TestAuditLine:
    def test_no_record_no_line(self):
        assert audit_line({}) is None

    @pytest.mark.parametrize("junk", ["junk", {"host": "h"}, {"agents": {}},
                                      {"agents": "junk"}, {"agents": {"a": "junk"}}])
    def test_nothing_readable_no_line(self, junk):
        assert audit_line({"audit": junk}) is None

    def test_it_names_each_agent_on_one_line(self):
        line = audit_line({"audit": {"host": "h", "agents": {
            "one": {"tier": "deep", "asked": MODEL_A, "models": [MODEL_A, MODEL_B]},
            "two": {"tier": None, "asked": None, "models": []},
        }}})
        assert "\n" not in line
        assert line == (
            f"h: one tier deep, asked {MODEL_A}, observed {MODEL_A} + {MODEL_B}; "
            "two tier none, asked none, observed none"
        )

    def test_a_bad_models_value_reads_as_none(self):
        line = audit_line({"audit": {"agents": {"one": {"models": "junk"}}}})
        assert line == "unknown: one tier none, asked none, observed none"


class TestPipelineStatus:
    @pytest.fixture
    def project(self, tmp_path, monkeypatch, capsys):
        root = tmp_path / "proj"
        m = git_project(root)
        monkeypatch.chdir(root)
        assert run("session", "start", "status") == 0
        assert run("pipeline", "start", "--task", "t") == 0
        capsys.readouterr()
        return m

    def _record(self, m):
        (session,) = SessionDomain(m).list()
        PipelineDomain(m).record_audit(session.id, host="h", agent="a", model=MODEL_A)

    def test_the_text_has_one_line(self, project, capsys):
        self._record(project)
        assert run("pipeline", "status") == 0
        out = capsys.readouterr().out
        assert [ln for ln in out.splitlines() if ln.startswith("  audit")] == [
            f"  audit   h: a tier none, asked none, observed {MODEL_A}"
        ]

    def test_json_carries_the_record(self, project, capsys):
        self._record(project)
        assert run("pipeline", "status", "--json") == 0
        assert payload(capsys)["evidence"]["audit"]["agents"]["a"]["models"] == [MODEL_A]

    def test_an_older_record_has_no_line(self, project, capsys):
        assert run("pipeline", "status") == 0
        assert "audit" not in capsys.readouterr().out


# -- the hook, through its functions with fixture payloads ---------------------------------


class AuditPipelines(FakePipelines):
    """The two audit questions, answered from a plain record of the calls."""

    def __init__(self, raises=None):
        super().__init__()
        self.audits: list[dict] = []
        self.stamps: list[tuple] = []
        self.unread_asked: list[tuple] = []
        self.raises = raises
        self.read: set = set()

    def audit_unread(self, session_id, agent, run_id):
        self.unread_asked.append((session_id, agent, run_id))
        if self.raises:
            raise self.raises
        return (agent, run_id) not in self.read

    def record_audit(self, session_id, **fields):
        if self.raises:
            raise self.raises
        self.audits.append({"session": session_id, **fields})
        self.read.add((fields["agent"], fields.get("run")))
        return True

    def stamp_verdict_agent(self, session_id, token, agent, model=None):
        self.stamps.append((session_id, token, agent, model))
        return 1


def fake_ctx(raises=None, how="host") -> FakeCtx:
    ctx = FakeCtx(how=how)
    ctx.pipelines = AuditPipelines(raises)
    return ctx


def acting(tmp_path, **extra) -> dict:
    """A subagent's own PostToolUse, the shape `agent_id` and `agent_type` have in
    tests/test_oracle_attention.py and tests/test_host_claude_code.py, with
    ADR-011's `transcript_path`."""
    main = run_transcript(tmp_path, assistant(MODEL_A))
    return {
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "tool_response": "",
        "agent_type": "reviewer",
        "agent_id": RUN,
        "transcript_path": str(main),
        **extra,
    }


def post(p, ctx):
    return ClaudeCodeHost(Path("."))._post_tool_use(p, ctx)


class TestTheFirstAct:
    def test_it_records_the_observed_model_and_the_run(self, tmp_path):
        ctx = fake_ctx()
        assert post(acting(tmp_path), ctx) == {}
        (record,) = ctx.pipelines.audits
        assert record["session"] == "s1"
        assert record["host"] == "claude_code"
        assert record["agent"] == "reviewer"
        assert record["model"] == MODEL_A
        assert record["run"] == RUN

    def test_a_later_call_of_that_run_reads_nothing(self, tmp_path):
        ctx = fake_ctx()
        p = acting(tmp_path)
        post(p, ctx)
        Path(p["transcript_path"]).parent.joinpath(
            "h1", "subagents", f"agent-{RUN}.jsonl"
        ).unlink()
        post(p, ctx)
        assert len(ctx.pipelines.audits) == 1

    def test_a_second_run_of_that_agent_is_read(self, tmp_path):
        ctx = fake_ctx()
        post(acting(tmp_path), ctx)
        post(acting(tmp_path / "other", agent_id="otherrun"), ctx)
        assert [r["run"] for r in ctx.pipelines.audits] == [RUN, "otherrun"]

    def test_a_main_loop_call_is_not_recorded(self, tmp_path):
        ctx = fake_ctx()
        p = acting(tmp_path)
        del p["agent_type"]
        post(p, ctx)
        assert ctx.pipelines.audits == []
        assert ctx.pipelines.unread_asked == []

    def test_a_guessed_session_is_not_recorded(self, tmp_path):
        ctx = fake_ctx(how="origin")
        post(acting(tmp_path), ctx)
        assert ctx.pipelines.audits == []

    def test_an_unplaced_call_is_not_recorded(self, tmp_path):
        ctx = fake_ctx()
        ctx.unplaced = object()
        host = ClaudeCodeHost(Path("."))
        host._report_unplaced = lambda tool, p, unplaced: {}
        host._post_tool_use(acting(tmp_path), ctx)
        assert ctx.pipelines.audits == []

    @pytest.mark.parametrize("key", ["transcript_path", "agent_id"])
    def test_no_way_to_the_transcript_is_a_null_model(self, tmp_path, key):
        ctx = fake_ctx()
        p = acting(tmp_path)
        del p[key]
        post(p, ctx)
        (record,) = ctx.pipelines.audits
        assert record["model"] is None

    def test_no_transcript_on_disk_is_a_null_model(self, tmp_path):
        ctx = fake_ctx()
        post(acting(tmp_path, transcript_path=str(tmp_path / "absent.jsonl")), ctx)
        (record,) = ctx.pipelines.audits
        assert record["model"] is None
        assert record["run"] == RUN

    def test_a_model_field_on_the_payload_is_not_read(self, tmp_path):
        """No fixture shows a tool payload carrying one, so nothing relies on it."""
        ctx = fake_ctx()
        post(acting(tmp_path, model=MODEL_B), ctx)
        (record,) = ctx.pipelines.audits
        assert record["model"] == MODEL_A

    @pytest.mark.parametrize("failure", [InvariantError("store is busy"), RuntimeError("boom")])
    def test_a_failure_never_reaches_the_hooks_answer(self, tmp_path, failure):
        ctx = fake_ctx(raises=failure)
        assert post(acting(tmp_path), ctx) == {}
        assert ctx.pipelines.audits == []
        assert ctx.pipelines.agent_how == [("s1", "reviewer", "actor")]

    def test_a_failing_read_is_a_null_model(self, tmp_path, monkeypatch):
        import invariant.hosts.claude_code as module

        def boom(*_args):
            raise RuntimeError("boom")

        monkeypatch.setattr(module, "subagent_model", boom)
        ctx = fake_ctx()
        assert post(acting(tmp_path), ctx) == {}
        (record,) = ctx.pipelines.audits
        assert record["model"] is None

    def test_an_actor_row_the_domain_refused_is_not_audited(self, tmp_path):
        ctx = fake_ctx()

        def refuse(*_a, **_k):
            raise InvariantError("store is busy")

        ctx.pipelines.record_agent_run = refuse
        assert post(acting(tmp_path), ctx) == {}
        assert ctx.pipelines.audits == []

    def test_the_actor_row_comes_first(self, tmp_path):
        ctx = fake_ctx()
        post(acting(tmp_path), ctx)
        assert ctx.pipelines.agent_how == [("s1", "reviewer", "actor")]


class TestTheDispatch:
    def dispatch(self, **tool_input) -> dict:
        return {"tool_name": "Agent",
                "tool_input": {"subagent_type": "reviewer", "prompt": "go", **tool_input},
                "tool_response": {}}

    def test_the_model_it_asked_for_is_recorded(self):
        ctx = fake_ctx()
        assert post(self.dispatch(model=MODEL_B), ctx) == {}
        (record,) = ctx.pipelines.audits
        assert record["requested"] == MODEL_B
        assert record["agent"] == "reviewer"
        assert record.get("model") is None
        assert record.get("run") is None

    def test_asking_for_none_is_a_null_request(self):
        ctx = fake_ctx()
        post(self.dispatch(), ctx)
        (record,) = ctx.pipelines.audits
        assert record["requested"] is None

    def test_a_dispatch_with_no_pipeline_is_not_recorded(self):
        ctx = fake_ctx()
        ctx.pipelines.record_agent_run = lambda *a, **k: "no-pipeline"
        result = post(self.dispatch(model=MODEL_B), ctx)
        assert ctx.pipelines.audits == []
        assert "reviewer" in json.dumps(result)

    def test_a_failure_does_not_change_the_answer(self):
        ctx = fake_ctx(raises=RuntimeError("boom"))
        assert post(self.dispatch(model=MODEL_B), ctx) == {}


class TestTheHintsAreThisProjects:
    """Tier and the rendered line, read from the project the context is rooted in."""

    def _ctx(self, root: Path) -> FakeCtx:
        ctx = fake_ctx()
        ctx.manifest = Manifest.load(root)
        return ctx

    @pytest.fixture
    def root(self, tmp_path):
        root = tmp_path / "proj"
        git_project(root, {
            "version": 1, "project": {"name": "p", "ecosystems": ["python"]},
            "agents": {"library": ["compliance-reviewer"],
                       "tiers": {"compliance-reviewer": "light"},
                       "local": ["my-agent"]},
        })
        return root

    def _copy(self, root, name, text):
        path = root / ".claude" / "agents" / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _hints(self, root, agent):
        ctx = self._ctx(root)
        post({"tool_name": "Bash", "tool_input": {"command": "ls"}, "agent_type": agent}, ctx)
        (record,) = ctx.pipelines.audits
        return record["tier"], record["rendered"]

    def test_a_library_agent_has_its_effective_tier(self, root):
        assert self._hints(root, "compliance-reviewer") == ("light", None)

    def test_the_installed_copys_model_line_is_the_rendered_one(self, root):
        self._copy(root, "compliance-reviewer",
                   f"---\nname: compliance-reviewer\nmodel: {MODEL_A}\n---\nbody\n")
        assert self._hints(root, "compliance-reviewer") == ("light", MODEL_A)

    def test_a_quoted_model_line_reads_as_its_text(self, root):
        self._copy(root, "my-agent", '---\nname: my-agent\nmodel: "x: y"\n---\nbody\n')
        assert self._hints(root, "my-agent") == (None, "x: y")

    def test_a_quoted_line_that_will_not_parse_is_null(self, root):
        self._copy(root, "my-agent", '---\nname: my-agent\nmodel: "open\n---\nbody\n')
        assert self._hints(root, "my-agent") == (None, None)

    def test_a_model_in_the_body_is_not_the_line(self, root):
        self._copy(root, "my-agent", "---\nname: my-agent\n---\nmodel: inbody\n")
        assert self._hints(root, "my-agent") == (None, None)

    def test_a_local_agent_has_no_tier(self, root):
        self._copy(root, "my-agent", f"---\nname: my-agent\nmodel: {MODEL_B}\n---\nbody\n")
        assert self._hints(root, "my-agent") == (None, MODEL_B)

    @pytest.mark.parametrize("agent", ["general-purpose", "../escape", "a/b", "plugin:agent"])
    def test_an_unknown_or_unsafe_name_is_null(self, root, agent):
        (root.parent / "escape.md").write_text(
            f"---\nmodel: {MODEL_A}\n---\n", encoding="utf-8"
        )
        assert self._hints(root, agent) == (None, None)

    def test_a_copy_that_will_not_read_is_null(self, root):
        path = root / ".claude" / "agents" / "my-agent.md"
        path.mkdir(parents=True)
        assert self._hints(root, "my-agent") == (None, None)

    def test_a_copy_with_no_frontmatter_is_null(self, root):
        self._copy(root, "my-agent", "just text\n")
        assert self._hints(root, "my-agent") == (None, None)

    def test_a_tier_that_cannot_be_resolved_is_null(self, root, monkeypatch):
        def refuse(self, name):
            raise InvariantError("the overlay is bad")

        monkeypatch.setattr(ClaudeCodeHost, "agent_source", refuse)
        assert self._hints(root, "compliance-reviewer") == (None, None)

    def test_no_manifest_on_the_context_is_null(self, tmp_path):
        ctx = fake_ctx()
        post(acting(tmp_path), ctx)
        (record,) = ctx.pipelines.audits
        assert (record["tier"], record["rendered"]) == (None, None)


VERDICT_CMD = "invariant guidelines verdict data.single-open --status pass --cite app/m.py 2 2"
VERDICT_OUT = f"recorded 1 verdict (ruleset abcdefabcdef, state reviewing, token {TOKEN})\n"


class TestTheVerdictStamp:
    def _bash(self, tmp_path, **extra) -> dict:
        return acting(tmp_path, tool_input={"command": VERDICT_CMD},
                      tool_response=VERDICT_OUT, **extra)

    def test_the_run_model_goes_on_the_stamp(self, tmp_path):
        ctx = fake_ctx()
        post(self._bash(tmp_path), ctx)
        assert ctx.pipelines.stamps == [("s1", TOKEN, "reviewer", MODEL_A)]

    def test_a_run_already_read_is_read_again_for_the_stamp(self, tmp_path):
        """The stamp is per verdict, so it asks that run's transcript, and what
        it sees joins the audit too: a run can change model."""
        ctx = fake_ctx()
        p = self._bash(tmp_path)
        ctx.pipelines.read.add(("reviewer", RUN))
        Path(p["transcript_path"]).parent.joinpath(
            "h1", "subagents", f"agent-{RUN}.jsonl"
        ).write_text(lines(assistant(MODEL_B)), encoding="utf-8")
        post(p, ctx)
        assert ctx.pipelines.stamps == [("s1", TOKEN, "reviewer", MODEL_B)]
        assert [r["model"] for r in ctx.pipelines.audits] == [MODEL_B]

    def test_no_model_stamps_the_agent_alone(self, tmp_path):
        ctx = fake_ctx()
        post(self._bash(tmp_path, transcript_path=None), ctx)
        assert ctx.pipelines.stamps == [("s1", TOKEN, "reviewer", None)]

    def test_a_failure_in_the_audit_leaves_the_stamp(self, tmp_path):
        ctx = fake_ctx()

        def boom(*_a, **_k):
            raise RuntimeError("boom")

        ctx.pipelines.record_audit = boom
        assert post(self._bash(tmp_path), ctx) == {}
        assert ctx.pipelines.stamps == [("s1", TOKEN, "reviewer", MODEL_A)]

    def test_a_main_loop_verdict_is_not_stamped_or_recorded(self, tmp_path):
        ctx = fake_ctx()
        p = self._bash(tmp_path)
        del p["agent_type"]
        post(p, ctx)
        assert ctx.pipelines.stamps == []
        assert ctx.pipelines.audits == []

    def test_a_worktree_session_is_placed_as_an_actor_row_is(self, tmp_path):
        class Iso:
            id = "s-iso"
            worktree = "/proj/.claude/worktrees/iso"

        class Sessions:
            def by_worktree_containing(self, _path):
                return None

        ctx = fake_ctx()
        ctx.sessions = Sessions()
        ctx.resolution_for_payload = lambda _p: Resolution(Iso(), "host")
        post(self._bash(tmp_path), ctx)
        assert ctx.pipelines.stamps == []
        assert ctx.pipelines.audits == []

    def test_a_proven_target_route_is_recorded_there(self, tmp_path):
        ctx = fake_ctx()
        ctx.resolution_for_payload = lambda _p: Resolution(FakeSession(), "target")
        post(self._bash(tmp_path), ctx)
        assert ctx.pipelines.stamps == [("s1", TOKEN, "reviewer", MODEL_A)]


class TestTheHookOverTheRealDomain:
    """The fakes above take any keywords, so this pins the two sides together."""

    @pytest.fixture
    def wired(self, tmp_path):
        m = git_project(tmp_path / "proj")
        session = SessionDomain(m).start("wired")
        pipelines = PipelineDomain(m)
        pipelines.start(session.id, task="t")
        ctx = FakeCtx()
        ctx.manifest = m
        ctx.pipelines = pipelines
        ctx.resolution_for_payload = lambda _p: Resolution(session, "target")
        return ctx, session.id

    def _audit(self, ctx, sid):
        return PipelineDomain(ctx.pipelines.manifest).current(sid).evidence["audit"]

    def test_a_first_act_then_a_dispatch(self, wired, tmp_path):
        ctx, sid = wired
        post(acting(tmp_path), ctx)
        post({"tool_name": "Agent", "tool_input": {"subagent_type": "reviewer", "prompt": "p",
                                                   "model": MODEL_B}}, ctx)
        assert self._audit(ctx, sid) == {
            "host": "claude_code",
            "agents": {"reviewer": {
                "tier": None, "asked": MODEL_B, "models": [MODEL_A], "runs": [RUN],
            }},
        }

    def test_a_verdict_is_stamped_and_the_run_is_read_once(self, wired, tmp_path):
        ctx, sid = wired
        p = ctx.pipelines.require(sid)
        p.evidence["verdicts"] = [{"rule": "r", "status": "pass", "token": TOKEN}]
        ctx.pipelines._save(sid, p)
        call = acting(tmp_path, tool_input={"command": VERDICT_CMD}, tool_response=VERDICT_OUT)
        post(call, ctx)
        record = ctx.pipelines.require(sid).evidence["verdicts"][0]
        assert (record["agent"], record["model"]) == ("reviewer", MODEL_A)
        assert ctx.pipelines.audit_unread(sid, "reviewer", RUN) is False
        assert self._audit(ctx, sid)["agents"]["reviewer"]["runs"] == [RUN]

    def test_an_agent_with_no_run_id_is_written_once(self, wired):
        ctx, _ = wired
        writes = []
        original = ctx.pipelines.record_audit

        def counting(*args, **kwargs):
            writes.append(kwargs)
            return original(*args, **kwargs)

        ctx.pipelines.record_audit = counting
        call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "agent_type": "reviewer"}
        post(call, ctx)
        post(call, ctx)
        assert len(writes) == 1
