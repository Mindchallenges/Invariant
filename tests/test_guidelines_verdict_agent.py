"""The host stamps the acting agent onto each verdict (IDEA_031 A4 increment 8).

ADR-012 DN-12 and the user's A4.8 rulings (a) to (c). A verdict is recorded
in the CLI child of a shell call, which never sees the hook payload, so the
CLI cannot know which agent submitted it. The host can:

- each accepted submission puts one random token on every record it writes,
  and prints it (text and ``--json``); a submitter may not supply the token or
  the agent, which stay unknown keys (ruling b);
- the Claude Code ``PostToolUse`` hook, for a verdict command whose payload
  names the acting agent, reads the token out of the command's output (ruling
  c), places the session through the actor rows' own check, and asks
  ``PipelineDomain.stamp_verdict_agent`` to set the agent;
- the stamp is set once, only on records that carry the token and no agent
  yet, and never overwrites (ruling a).

It is audit only (D1 item 2, DN-12): no gate, standing or landing record
reads the agent or the token, which the last class pins.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_cli import payload, run
from test_domains import git_project
from test_guidelines_domain import RULESET_REL, _review, _stored, write
from test_guidelines_verdicts_complete import (
    PYTHON,
    RULESET,
    SELECTOR,
    context,
    every_pass,
    gate,
    green,
    pipeline,
    sh,
)
from test_host_claude_code import FakeCtx, FakePipelines, FakeSession

from invariant.core import verdicts
from invariant.core.errors import InvariantError
from invariant.core.manifest import Level
from invariant.core.rules import Evaluated, read_standings
from invariant.domains.pipeline import PipelineDomain, guidelines_landing
from invariant.domains.session import Resolution, SessionDomain
from invariant.hosts.claude_code import ClaudeCodeHost, _submission_tokens

TOKEN_A = "0123456789abcdef" * 2
TOKEN_B = "fedcba9876543210" * 2
HEX32 = re.compile(r"[0-9a-f]{32}")


# -- the record (core/verdicts) ----------------------------------------------------------


def _record(**extra) -> verdicts.VerdictRecord:
    return verdicts.VerdictRecord(
        rule="data.single-open", status="pass",
        citations=(verdicts.Citation("app/m.py", 2, 2, "abcdefabcdef"),),
        tests=(), na_condition=None, note=None, ruleset="5be0c2d91f7a",
        state="reviewing", surface="cli", **extra,
    )


class TestTheRecord:
    def test_the_token_is_written_only_when_set(self):
        assert "token" not in _record().record()
        assert _record(token=TOKEN_A).record()["token"] == TOKEN_A

    def test_the_token_and_the_agent_are_dropped_when_read_back(self):
        stamped = _record(token=TOKEN_A, agent="compliance-reviewer").record()
        plain_sub, plain_problems = verdicts.resubmitted(_record().record())
        sub, problems = verdicts.resubmitted(stamped)
        assert (sub, problems) == (plain_sub, plain_problems)
        assert problems == ()

    @pytest.mark.parametrize("key", ["token", "agent"])
    def test_a_submitter_supplying_either_is_refused_as_an_unknown_key(self, key):
        """Ruling (b): the token is the engine's, and so is the agent."""
        entry = {"rule": "data.single-open", "status": "pass", key: TOKEN_A}
        sub, problems = verdicts.parse_entry(entry)
        assert sub is None
        assert [p.predicate for p in problems] == ["shape"]
        assert f"unknown key {key}" in problems[0].message


# -- the submission's token, through main ------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch, capsys):
    from test_guidelines_verdicts import PYTHON as V_PYTHON
    from test_guidelines_verdicts import RULESET as VERDICTS

    root = tmp_path / "proj"
    root.mkdir()
    write(root, RULESET_REL, VERDICTS)
    write(root, "app/m.py", "one\ntwo\nthree\n")
    git_project(root, {"version": 1, "project": V_PYTHON,
                       "guidelines": {"ruleset": RULESET_REL}})
    sh(root, "checkout", "-q", "-b", "work")
    monkeypatch.chdir(root)
    assert run("session", "start", "verdicts") == 0
    assert run("pipeline", "start", "--task", "t") == 0
    write(root, "app/m.py", "one\nTWO\nthree\n")
    _review(root)
    capsys.readouterr()
    return root


def _bulk(root: Path, entries: list) -> str:
    path = root.parent / "submission.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return str(path)


CITED = ["--cite", "app/m.py", "2", "2"]
TWO = [
    {"rule": "data.single-open", "status": "pass",
     "citations": [{"path": "app/m.py", "lines": [2, 2]}]},
    {"rule": "data.either", "status": "pass",
     "citations": [{"path": "app/m.py", "lines": [2, 2]}]},
]


class TestTheSubmissionToken:
    def test_every_record_of_one_submission_carries_one_token(self, project, capsys):
        assert run("guidelines", "verdicts", "submit", _bulk(project, TWO)) == 0
        tokens = {r["token"] for r in _stored(project)}
        (token,) = tokens
        assert HEX32.fullmatch(token)

    def test_two_submissions_carry_different_tokens(self, project, capsys):
        for _ in range(2):
            assert run("guidelines", "verdict", "data.single-open", "--status", "pass",
                       *CITED) == 0
        first, second = _stored(project)
        assert first["token"] != second["token"]

    def test_no_agent_is_recorded_by_the_command(self, project, capsys):
        assert run("guidelines", "verdict", "data.single-open", "--status", "pass",
                   *CITED) == 0
        (stored,) = _stored(project)
        assert "agent" not in stored

    def test_the_text_names_the_token(self, project, capsys):
        assert run("guidelines", "verdict", "data.single-open", "--status", "pass",
                   *CITED) == 0
        text = capsys.readouterr().out
        (stored,) = _stored(project)
        assert f"token {stored['token']}" in text

    def test_json_carries_the_token(self, project, capsys):
        assert run("guidelines", "verdicts", "submit", _bulk(project, TWO), "--json") == 0
        body = payload(capsys)
        assert HEX32.fullmatch(body["token"])
        assert {r["token"] for r in body["recorded"]} == {body["token"]}

    def test_a_refused_submission_prints_no_token(self, project, capsys):
        assert run("guidelines", "verdict", "data.single-open", "--status", "pass",
                   "--cite", "app/m.py", "1", "1", "--json") == 1
        out = capsys.readouterr().out
        assert "token" not in out and _submission_tokens(out) == ()

    @pytest.mark.parametrize("as_json", [False, True])
    def test_the_host_reads_the_token_out_of_the_real_output(
        self, project, capsys, as_json
    ):
        """The coupling the stamp rests on, pinned end to end: what the CLI
        prints is what the hook parses."""
        extra = ["--json"] if as_json else []
        assert run("guidelines", "verdict", "data.single-open", "--status", "pass",
                   *CITED, *extra) == 0
        out = capsys.readouterr().out
        (stored,) = _stored(project)
        assert _submission_tokens(out) == (stored["token"],)


# -- the stamp (domains/pipeline) ----------------------------------------------------------


@pytest.fixture
def reviewing(tmp_path):
    m = git_project(tmp_path / "proj")
    session = SessionDomain(m).start("stamp")
    pipelines = PipelineDomain(m)
    pipelines.start(session.id, task="t")
    return pipelines, session.id


def _seed(pipelines, sid, *records):
    p = pipelines.require(sid)
    p.evidence["verdicts"] = [dict(r) for r in records]
    pipelines._save(sid, p)


def _verdicts(pipelines, sid):
    return PipelineDomain(pipelines.manifest).current(sid).evidence["verdicts"]


A1 = {"rule": "r.one", "status": "pass", "token": TOKEN_A}
A2 = {"rule": "r.two", "status": "fail", "token": TOKEN_A}
B1 = {"rule": "r.one", "status": "pass", "token": TOKEN_B}
OLD = {"rule": "r.one", "status": "pass"}


class TestStampVerdictAgent:
    def test_it_stamps_every_record_carrying_the_token(self, reviewing):
        pipelines, sid = reviewing
        _seed(pipelines, sid, A1, A2, B1, OLD)
        assert pipelines.stamp_verdict_agent(sid, TOKEN_A, "compliance-reviewer") == 2
        stored = _verdicts(pipelines, sid)
        assert [r.get("agent") for r in stored] == [
            "compliance-reviewer", "compliance-reviewer", None, None,
        ]

    def test_it_never_overwrites(self, reviewing):
        """Ruling (a): set once."""
        pipelines, sid = reviewing
        _seed(pipelines, sid, A1, dict(A2, agent="first"))
        assert pipelines.stamp_verdict_agent(sid, TOKEN_A, "second") == 1
        assert pipelines.stamp_verdict_agent(sid, TOKEN_A, "third") == 0
        assert [r["agent"] for r in _verdicts(pipelines, sid)] == ["second", "first"]

    def test_a_null_agent_counts_as_none_yet(self, reviewing):
        pipelines, sid = reviewing
        _seed(pipelines, sid, dict(A1, agent=None))
        assert pipelines.stamp_verdict_agent(sid, TOKEN_A, "reviewer") == 1
        assert _verdicts(pipelines, sid)[0]["agent"] == "reviewer"

    def test_it_changes_nothing_else_on_the_record(self, reviewing):
        pipelines, sid = reviewing
        _seed(pipelines, sid, A1)
        pipelines.stamp_verdict_agent(sid, TOKEN_A, "reviewer")
        assert _verdicts(pipelines, sid) == [dict(A1, agent="reviewer")]

    def test_an_unknown_token_stamps_nothing(self, reviewing):
        pipelines, sid = reviewing
        _seed(pipelines, sid, A1)
        assert pipelines.stamp_verdict_agent(sid, TOKEN_B, "reviewer") == 0
        assert "agent" not in _verdicts(pipelines, sid)[0]

    @pytest.mark.parametrize("token, agent", [("", "reviewer"), (TOKEN_A, ""),
                                              (None, "reviewer"), (TOKEN_A, None)])
    def test_an_empty_token_or_agent_stamps_nothing(self, reviewing, token, agent):
        pipelines, sid = reviewing
        _seed(pipelines, sid, OLD, A1)
        assert pipelines.stamp_verdict_agent(sid, token, agent) == 0
        assert all("agent" not in r for r in _verdicts(pipelines, sid))

    def test_no_pipeline_stamps_nothing(self, tmp_path):
        m = git_project(tmp_path / "proj")
        session = SessionDomain(m).start("idle")
        assert PipelineDomain(m).stamp_verdict_agent(session.id, TOKEN_A, "r") == 0

    def test_no_verdicts_stamps_nothing(self, reviewing):
        pipelines, sid = reviewing
        assert pipelines.stamp_verdict_agent(sid, TOKEN_A, "reviewer") == 0
        assert "verdicts" not in pipelines.current(sid).evidence

    def test_a_record_that_is_not_an_object_is_passed_over(self, reviewing):
        pipelines, sid = reviewing
        p = pipelines.require(sid)
        p.evidence["verdicts"] = ["junk", dict(A1)]
        pipelines._save(sid, p)
        assert pipelines.stamp_verdict_agent(sid, TOKEN_A, "reviewer") == 1
        assert _verdicts(pipelines, sid)[0] == "junk"


# -- reading the token out of the command's output (hosts/claude_code) -----------------------


class TestSubmissionTokens:
    def test_the_text_form(self):
        text = f"recorded 1 verdict (ruleset abcdefabcdef, state reviewing, token {TOKEN_A})"
        assert _submission_tokens(text) == (TOKEN_A,)

    def test_the_json_form(self):
        text = json.dumps({"recorded": [{"token": TOKEN_A}], "token": TOKEN_A}, indent=2)
        assert _submission_tokens(text) == (TOKEN_A,)

    def test_two_submissions_in_one_call_give_both_in_order(self):
        text = f"... token {TOKEN_B})\n... token {TOKEN_A})\n... token {TOKEN_B})"
        assert _submission_tokens(text) == (TOKEN_B, TOKEN_A)

    @pytest.mark.parametrize("text", [
        "", "recorded 1 verdict", f"token {TOKEN_A[:-1]}", f"token {TOKEN_A}0",
        f"token {TOKEN_A.upper()}", f"xtoken {TOKEN_A}",
    ])
    def test_anything_else_is_no_token(self, text):
        assert _submission_tokens(text) == ()


# -- the hook, through its functions with fixture payloads ------------------------------------


class StampingPipelines(FakePipelines):
    def __init__(self, raises=None):
        super().__init__()
        self.stamps: list[tuple[str, str, str]] = []
        self.raises = raises

    def stamp_verdict_agent(self, session_id, token, agent):
        if self.raises:
            raise self.raises
        self.stamps.append((session_id, token, agent))
        return 1


def _ctx(how="host", raises=None) -> FakeCtx:
    ctx = FakeCtx(how=how)
    ctx.pipelines = StampingPipelines(raises)
    return ctx


VERDICT_CMD = "invariant guidelines verdict data.single-open --status pass --cite app/m.py 2 2"
TEXT_OUT = f"recorded 1 verdict (ruleset abcdefabcdef, state reviewing, token {TOKEN_A})\n"


def _bash(command=VERDICT_CMD, response=TEXT_OUT, agent="compliance-reviewer") -> dict:
    p = {"tool_name": "Bash", "tool_input": {"command": command},
         "tool_response": response}
    if agent is not None:
        p["agent_type"] = agent
    return p


def _post(p, ctx):
    return ClaudeCodeHost(Path("."))._post_tool_use(p, ctx)


class TestTheHookStamps:
    def test_a_subagents_verdict_command_is_stamped(self):
        ctx = _ctx()
        assert _post(_bash(), ctx) == {}
        assert ctx.pipelines.stamps == [("s1", TOKEN_A, "compliance-reviewer")]

    def test_the_bulk_form_through_a_cd_and_json_output(self):
        ctx = _ctx()
        out = json.dumps({"recorded": [{"token": TOKEN_A}], "token": TOKEN_A}, indent=2)
        cmd = "cd /proj && invariant guidelines verdicts submit v.json --json"
        _post(_bash(cmd, {"stdout": out, "stderr": ""}), ctx)
        assert ctx.pipelines.stamps == [("s1", TOKEN_A, "compliance-reviewer")]

    def test_the_response_may_be_spelled_output(self):
        ctx = _ctx()
        _post(_bash(response={"output": TEXT_OUT}), ctx)
        assert [s[1] for s in ctx.pipelines.stamps] == [TOKEN_A]

    def test_two_submissions_in_one_call_are_both_stamped(self):
        ctx = _ctx()
        out = TEXT_OUT + TEXT_OUT.replace(TOKEN_A, TOKEN_B)
        _post(_bash(f"{VERDICT_CMD}; {VERDICT_CMD}", out), ctx)
        assert [s[1] for s in ctx.pipelines.stamps] == [TOKEN_A, TOKEN_B]

    def test_a_main_loop_turn_names_no_agent_and_stamps_nothing(self):
        """A host without agent_type stamps nothing; the verdict stands."""
        ctx = _ctx()
        _post(_bash(agent=None), ctx)
        assert ctx.pipelines.stamps == []

    @pytest.mark.parametrize("agent", ["", 7])
    def test_an_agent_that_is_not_a_name_stamps_nothing(self, agent):
        ctx = _ctx()
        _post(_bash(agent=agent), ctx)
        assert ctx.pipelines.stamps == []

    @pytest.mark.parametrize("command", [
        "invariant guidelines verdicts show",
        "invariant guidelines verdicts show --json",
        "invariant pipeline status --json",
        "echo invariant; guidelines verdict x",
        "cat notes.txt",
    ])
    def test_only_the_verdict_commands_are_read(self, command):
        ctx = _ctx()
        _post(_bash(command), ctx)
        assert ctx.pipelines.stamps == []

    def test_an_output_without_a_token_stamps_nothing(self):
        ctx = _ctx()
        _post(_bash(response="entry 1 (data.single-open): P3: ...\n"), ctx)
        assert ctx.pipelines.stamps == []

    def test_a_response_that_is_neither_text_nor_an_object_stamps_nothing(self):
        ctx = _ctx()
        _post(_bash(response=None), ctx)
        assert ctx.pipelines.stamps == []

    def test_a_guessed_session_is_never_stamped(self):
        ctx = _ctx(how="origin")
        _post(_bash(), ctx)
        assert ctx.pipelines.stamps == []

    def test_a_refusal_from_the_domain_does_not_fail_the_call(self):
        ctx = _ctx(raises=InvariantError("store is busy"))
        assert _post(_bash(), ctx) == {}
        assert ctx.pipelines.stamps == []

    def test_the_actor_row_is_recorded_too(self):
        ctx = _ctx()
        _post(_bash(), ctx)
        assert ctx.pipelines.agent_how == [("s1", "compliance-reviewer", "actor")]

    def test_an_unplaced_call_stamps_nothing(self):
        ctx = _ctx()
        ctx.unplaced = object()
        host = ClaudeCodeHost(Path("."))
        host._report_unplaced = lambda tool, p, unplaced: {}
        host._post_tool_use(_bash(), ctx)
        assert ctx.pipelines.stamps == []


class WorktreeSession:
    id = "s-iso"
    worktree = "/proj/.claude/worktrees/iso"


class PlacingSessions:
    def __init__(self, found):
        self.found = found
        self.asked: list = []

    def by_worktree_containing(self, path):
        self.asked.append(path)
        return self.found


class TestTheStampIsPlacedAsAnActorRowIs:
    """The same place check `_actor_session` makes for actor rows: for the
    host route on an isolated session, the call must land in a worktree."""

    def _ctx(self, found, cwd):
        ctx = _ctx()
        ctx.cwd = Path(cwd)
        ctx.sessions = PlacingSessions(found)
        ctx.resolution_for_payload = lambda p: Resolution(WorktreeSession(), "host")
        return ctx

    def test_inside_the_worktree_it_is_stamped_there(self):
        ctx = self._ctx(WorktreeSession(), WorktreeSession.worktree)
        ClaudeCodeHost(Path("."))._stamp_verdict_agent(_bash(), ctx)
        assert ctx.pipelines.stamps == [("s-iso", TOKEN_A, "compliance-reviewer")]
        assert ctx.sessions.asked == [Path(WorktreeSession.worktree)]

    def test_outside_every_worktree_it_is_not_stamped(self):
        ctx = self._ctx(None, "/proj")
        ClaudeCodeHost(Path("."))._stamp_verdict_agent(_bash(), ctx)
        assert ctx.pipelines.stamps == []

    def test_a_proven_target_route_needs_no_place_check(self):
        ctx = _ctx()
        ctx.resolution_for_payload = lambda p: Resolution(FakeSession(), "target")
        ClaudeCodeHost(Path("."))._stamp_verdict_agent(_bash(), ctx)
        assert ctx.pipelines.stamps == [("s1", TOKEN_A, "compliance-reviewer")]


# -- DN-12: nothing reads the agent or the token -------------------------------------------


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    write(root, RULESET_REL, RULESET)
    write(root, "app/m.py", "x = 1\n")
    git_project(root, {"version": 1, "project": PYTHON, "guidelines": {"ruleset": RULESET_REL}})
    sh(root, "checkout", "-q", "-b", "work")
    write(root, "app/m.py", "x = 1\ny = 2\n")
    return root


VARIANTS = {
    "plain": {},
    "agent": {"agent": "compliance-reviewer"},
    "token": {"token": TOKEN_A},
    "both": {"agent": "feature-architect", "token": TOKEN_B},
}


class TestDN12NothingReadsTheStamp:
    def _read(self, repo, records, runs, last_fail=False):
        recorded = [dict(r) for r in records]
        if last_fail:
            recorded[-1] = dict(recorded[-1], status="fail")
        ctx = context(repo, pipe=pipeline(verdicts_=recorded, runs=runs))
        result = gate(ctx)
        landing = guidelines_landing(
            "reviewing", "committed", forced=False,
            verdicts=[Evaluated(result.rule_id, Level.BLOCK, result.verdict)],
        )
        return result.verdict, read_standings(ctx), landing

    @pytest.mark.parametrize("last_fail", [False, True])
    def test_the_gate_the_standings_and_the_landing_record_are_unchanged(
        self, repo, last_fail
    ):
        runs = [green(SELECTOR)]
        base = every_pass(repo, runs)
        seen = {
            name: self._read(repo, [dict(r, **extra) for r in base], runs, last_fail)
            for name, extra in VARIANTS.items()
        }
        plain = seen.pop("plain")
        assert plain[2] is not None
        for name, other in seen.items():
            assert other == plain, name

    def test_a_missing_verdict_is_still_missing_whoever_is_stamped(self, repo):
        runs = [green(SELECTOR)]
        base = every_pass(repo, runs)[:-1]
        plain = self._read(repo, base, runs)
        assert not plain[0].ok
        for extra in VARIANTS.values():
            assert self._read(repo, [dict(r, **extra) for r in base], runs) == plain
