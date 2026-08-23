"""The operator's authority over their own project.

The position this library is built on has two halves: it exists to make an
agent deterministic, and it must never take authority away from the operator,
who stays free to override any rule — temporarily or permanently. The
permanent half has always worked (profiles pin nothing, `enforcement.rules`
overrides every level). This suite covers the temporary half, and it exists
because the 2026-08 authority audit found that half absent at exactly the
surface where the operator meets the tool.

Every existing test of the override machinery drives a DOMAIN method —
`session transition --force`, `pipeline advance --force`, `item transition
--force`. None drove the hook. So a `pre-tool-use` deny with no appeal, and a
`stop` block with no appeal, were invisible to a green suite (BUG_049).
"""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from invariant.cli.main import main
from invariant.core.errors import UsageError
from invariant.core.manifest import MANIFEST_RELPATH, Level, Manifest
from invariant.core.rules import Evaluated, Verdict
from invariant.domains.pipeline import PipelineDomain
from invariant.domains.session import SessionDomain

HOST = "host-authority-0001"


def run(*argv) -> int:
    return main(list(argv))


def out(capsys) -> str:
    return capsys.readouterr().out


def payload(capsys) -> dict:
    text = out(capsys)
    lines = text.splitlines()
    for start in range(len(lines) - 1, -1, -1):
        if not lines[start].lstrip().startswith("{"):
            continue
        try:
            return json.loads("\n".join(lines[start:]))
        except json.JSONDecodeError:
            continue
    raise AssertionError(f"no JSON document in output:\n{text}")


def commit_all(root, message="wip"):
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=root, check=True)


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "docs" / "architecture").mkdir(parents=True)
    (root / "docs" / "architecture" / "overview.md").write_text(
        "# Overview\n\n```mermaid\ngraph TD\n  A-->B\n```\n", encoding="utf-8"
    )
    (root / "app.py").write_text("print(1)\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    monkeypatch.chdir(root)
    monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
    return root


class HookHarness:
    """One session, bound to one host id, so attribution is never the variable."""

    def setup(self, project, capsys, *, isolate=False, rules=None):
        assert run("init", "--name", "demo") == 0
        if rules:
            path = project / MANIFEST_RELPATH
            body = yaml.safe_load(path.read_text(encoding="utf-8"))
            body.setdefault("enforcement", {}).setdefault("rules", {}).update(rules)
            path.write_text(yaml.safe_dump(body), encoding="utf-8")
        commit_all(project, "invariant")
        run("session", "start", "task", "--json")
        sid = payload(capsys)["session"]["id"]
        if isolate:
            run("session", "isolate", "--execute", "--id", sid)
        run("session", "adopt", HOST, "--id", sid)
        capsys.readouterr()
        return sid

    def hook(self, event, body):
        stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(body))
        try:
            return main(["hook", event])
        finally:
            sys.stdin = stdin

    def edit(self, path):
        return {
            "session_id": HOST,
            "tool_name": "Edit",
            "tool_input": {"file_path": str(path), "old_string": "a", "new_string": "b"},
        }

    def decision(self, capsys):
        return payload(capsys).get("hookSpecificOutput", {})


# -- AUTH-01, part 1: the one-shot override token -----------------------


#: One blocking rule, reached deliberately: an isolated session editing a file
#: in the SHARED checkout. Picked because exactly one gate fires on it, so a
#: test that grants an override for that gate is testing the override rather
#: than the arithmetic of how many gates happened to fire.
RULE = "session.no_edit_in_main_checkout"


class TestABlockingHookVerdictCanBePassedDeliberately(HookHarness):
    """BUG_049's repro, at the boundary the bug is about.

    A blocking hook verdict was final: `_pre_tool_use` returned
    `permissionDecision: "deny"` and there was no argument, marker, flag or
    prompt that let the operator say "yes, do it anyway" for this one call.
    The only relief was a permanent edit to project policy, made mid-task, to
    get past a single call.
    """

    def setup(self, project, capsys, **kw):
        kw.setdefault("isolate", True)
        return super().setup(project, capsys, **kw)

    def test_the_edit_is_denied_with_no_override(self, project, capsys):
        self.setup(project, capsys)
        self.hook("pre-tool-use", self.edit(project / "app.py"))
        decision = self.decision(capsys)
        assert decision["permissionDecision"] == "deny"
        assert RULE in decision["permissionDecisionReason"]

    def test_a_granted_override_lets_the_next_call_through(self, project, capsys):
        sid = self.setup(project, capsys)
        assert run(
            "override", RULE,
            "--reason", "restoring a file the isolation itself needs",
            "--id", sid,
        ) == 0
        capsys.readouterr()

        self.hook("pre-tool-use", self.edit(project / "app.py"))
        decision = self.decision(capsys)
        assert decision.get("permissionDecision") != "deny", decision

    def test_the_grant_is_spent_by_the_call_it_let_through(self, project, capsys):
        sid = self.setup(project, capsys)
        run("override", RULE, "--reason", "once", "--id", sid)
        capsys.readouterr()

        self.hook("pre-tool-use", self.edit(project / "app.py"))
        assert self.decision(capsys).get("permissionDecision") != "deny"

        self.hook("pre-tool-use", self.edit(project / "app.py"))
        assert self.decision(capsys)["permissionDecision"] == "deny"

    def test_calls_makes_the_grant_last_that_many_calls(self, project, capsys):
        sid = self.setup(project, capsys)
        run("override", RULE, "--reason", "two",
            "--calls", "2", "--id", sid)
        capsys.readouterr()

        for _ in range(2):
            self.hook("pre-tool-use", self.edit(project / "app.py"))
            assert self.decision(capsys).get("permissionDecision") != "deny"
        self.hook("pre-tool-use", self.edit(project / "app.py"))
        assert self.decision(capsys)["permissionDecision"] == "deny"

    def test_spending_it_is_recorded_on_the_session(self, project, capsys):
        """The whole point. An override that leaves no trace is a hole."""
        sid = self.setup(project, capsys)
        run("override", RULE,
            "--reason", "a one-line fixup on the shared branch", "--id", sid)
        capsys.readouterr()
        self.hook("pre-tool-use", self.edit(project / "app.py"))
        capsys.readouterr()

        run("session", "status", "--id", sid, "--json")
        session = payload(capsys)["session"]
        spent = [o for o in session["overrides"] if o.get("rule") == RULE]
        assert spent, session["overrides"]
        assert spent[0]["reason"] == "a one-line fixup on the shared branch"
        assert spent[0]["surface"] == "pre-tool-use"

    def test_an_unspent_grant_is_visible_before_it_is_used(self, project, capsys):
        sid = self.setup(project, capsys)
        run("override", RULE, "--reason", "why", "--id", sid)
        capsys.readouterr()
        run("session", "status", "--id", sid, "--json")
        pending = payload(capsys)["session"]["pending_overrides"]
        assert [p["rule"] for p in pending] == [RULE]

    def test_it_only_covers_the_rule_it_names(self, project, capsys):
        sid = self.setup(project, capsys)
        run("override", "privacy.clean", "--reason", "unrelated", "--id", sid)
        capsys.readouterr()
        self.hook("pre-tool-use", self.edit(project / "app.py"))
        assert self.decision(capsys)["permissionDecision"] == "deny"

    def test_an_override_with_no_reason_is_refused(self, project, capsys):
        """An override with no stated reason is a hole, not an override."""
        sid = self.setup(project, capsys)
        assert run("override", RULE, "--id", sid) == 2
        self.hook("pre-tool-use", self.edit(project / "app.py"))
        assert self.decision(capsys)["permissionDecision"] == "deny"

    def test_an_override_naming_no_rule_is_refused(self, project, capsys):
        sid = self.setup(project, capsys)
        assert run("override", "not.a.rule", "--reason", "x", "--id", sid) == 2

    def test_a_grant_can_be_withdrawn(self, project, capsys):
        sid = self.setup(project, capsys)
        run("override", RULE, "--reason", "why", "--id", sid)
        run("override", RULE, "--clear", "--id", sid)
        capsys.readouterr()
        self.hook("pre-tool-use", self.edit(project / "app.py"))
        assert self.decision(capsys)["permissionDecision"] == "deny"


class TestTheOverrideReachesTheStopSurfaceToo(HookHarness):
    """`ask` cannot reach `stop` — there is no human in that loop. The token can."""

    RULE = "session.work_would_be_stranded"

    def _stranded(self, project, capsys):
        """A finished pipeline with its commits still only on the branch."""
        sid = self.setup(project, capsys, isolate=True)
        run("session", "status", "--id", sid, "--json")
        worktree = Path(payload(capsys)["session"]["worktree"])
        (worktree / "app.py").write_text("print(2)\n", encoding="utf-8")
        commit_all(worktree, "work that is not merged")
        run("pipeline", "start")
        run("pipeline", "advance", "committed", "--force", "--reason", "fixture")
        capsys.readouterr()
        return sid

    def test_the_turn_is_blocked_with_no_override(self, project, capsys):
        self._stranded(project, capsys)
        assert self.hook("stop", {"session_id": HOST}) == 0
        assert payload(capsys).get("decision") == "block"

    def test_a_stop_block_is_passable_with_a_grant(self, project, capsys):
        sid = self._stranded(project, capsys)
        run("override", self.RULE,
            "--reason", "handing this branch to a colleague", "--id", sid)
        capsys.readouterr()

        self.hook("stop", {"session_id": HOST})
        assert payload(capsys).get("decision") != "block"

    def test_spending_it_at_stop_is_recorded_with_that_surface(self, project, capsys):
        sid = self._stranded(project, capsys)
        run("override", self.RULE, "--reason", "handing it over", "--id", sid)
        self.hook("stop", {"session_id": HOST})
        capsys.readouterr()

        run("session", "status", "--id", sid, "--json")
        spent = [
            o for o in payload(capsys)["session"]["overrides"]
            if o.get("rule") == self.RULE
        ]
        assert spent and spent[0]["surface"] == "stop"


# -- AUTH-01, part 2: `ask` as a delivery level -------------------------


class TestAskIsAFourthDeliveryLevel:
    """`Level` is already "how a verdict is delivered, not a property of the
    rule's logic". The host's PreToolUse contract carries `ask` alongside
    `allow` and `deny`, and this library only ever emitted `deny` — so the
    human was never offered the choice.
    """

    def test_ask_parses_as_a_level(self):
        assert Level.parse("ask") is Level.ASK

    def test_a_failing_ask_verdict_asks(self):
        e = Evaluated("r", Level.ASK, Verdict.fail("no"))
        assert e.asks and not e.warns

    def test_a_passing_ask_verdict_does_not(self):
        assert not Evaluated("r", Level.ASK, Verdict.clean()).asks

    def test_ask_stops_work_where_nobody_can_be_asked(self):
        """Fail closed. An `ask` delivered where there is no human in the loop
        is delivered as a block, in the same do-not-relax convention the rest
        of the engine uses — a spurious block is visible, a silent pass is not.
        """
        assert Evaluated("r", Level.ASK, Verdict.fail("no")).blocks


class TestAskDeliveryAtTheHook(HookHarness):
    def test_a_failing_ask_rule_routes_the_call_to_the_human(self, project, capsys):
        self.setup(project, capsys, isolate=True, rules={RULE: "ask"})
        self.hook("pre-tool-use", self.edit(project / "app.py"))
        decision = self.decision(capsys)
        assert decision["permissionDecision"] == "ask"
        assert RULE in decision["permissionDecisionReason"]

    def test_a_real_block_still_denies_alongside_an_ask(self, project, capsys):
        """A deny is not softened by an `ask` sitting next to it."""
        self.setup(project, capsys, isolate=True,
                   rules={RULE: "ask", "privacy.clean": "block"})
        body = self.edit(project / "docs" / "architecture" / "leak.md")
        body["tool_input"]["new_string"] = "reach me at a.person@example.com"
        self.hook("pre-tool-use", body)
        assert self.decision(capsys)["permissionDecision"] == "deny"


# -- AUTH-01, part 3: the advice that pointed nowhere -------------------


def git_project(root: Path, manifest: dict | None = None) -> Manifest:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "docs" / "architecture").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "architecture" / "overview.md").write_text(
        "# Overview\n", encoding="utf-8"
    )
    path = root / MANIFEST_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(manifest or {"version": 1, "project": {"name": "demo"}}),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    return Manifest.load(root)


@pytest.fixture
def m(tmp_path):
    return git_project(tmp_path / "proj")


class TestAcknowledgeAdviceRoutesSomewhereThatCanHelp:
    """`acknowledge` is the one message in the library that discusses
    overriding a blocking rule, and for a rule that fires on a TOOL CALL it
    sent the caller to `pipeline advance --force`, where there is no
    transition to force. Worse than saying "there is no override for this".
    """

    def _refusal(self, m, rule_id) -> str:
        sessions = SessionDomain(m)
        session = sessions.start("task")
        pipelines = PipelineDomain(m, sessions.store)
        pipelines.start(session.id)
        with pytest.raises(UsageError) as exc:
            pipelines.acknowledge(session.id, rule_id, "because")
        return str(exc.value)

    def test_a_hook_rule_is_pointed_at_the_override_command(self, m):
        """It fires on a tool call. There is no transition to force."""
        text = self._refusal(m, "session.isolated_before_edit")
        assert "invariant override session.isolated_before_edit" in text
        assert "pipeline advance" not in text

    def test_a_transition_gate_still_points_at_advance_force(self, m):
        """`test.all_pass` gates three states of `tdd` and fires at no hook."""
        text = self._refusal(m, "test.all_pass")
        assert "pipeline advance" in text
        assert "invariant override" not in text

    def test_a_rule_that_is_both_names_both_routes(self, m):
        """`privacy.clean` screens an edit AND gates `reviewing`'s exit."""
        text = self._refusal(m, "privacy.clean")
        assert "invariant override privacy.clean" in text
        assert "pipeline advance" in text

    def test_a_rule_neither_route_reaches_says_so(self, m):
        """`item.bug_reproduced_before_fix` gates the BUG machine, fires at no
        hook, and gates no state of any pipeline. The old advice named a
        `pipeline advance` that cannot reach it. Saying there is no route from
        here beats naming a wrong one.
        """
        text = self._refusal(m, "item.bug_reproduced_before_fix")
        assert "Nothing can override it here" in text
        assert "pipeline advance" not in text


class TestAcknowledgementReachesTheHookSurface(HookHarness):
    """`acknowledged` evidence was read only in `PipelineDomain.evaluate_gates`.
    Hooks call `evaluate_event`, which never consulted it — so even a WARN-level
    hook rule could not be acknowledged into silence for the run.
    """

    def test_an_acknowledged_advisory_hook_rule_stops_repeating(self, project, capsys):
        sid = self.setup(project, capsys, isolate=True,
                         rules={"pipeline.started_before_edit": "warn"})
        run("session", "status", "--id", sid, "--json")
        worktree = payload(capsys)["session"]["worktree"]
        run("pipeline", "start")
        run("pipeline", "ack", "pipeline.started_before_edit",
            "--reason", "a chore with no pipeline", "--id", sid)
        capsys.readouterr()

        self.hook("pre-tool-use", self.edit(Path(worktree) / "app.py"))
        assert "pipeline.started_before_edit" not in out(capsys)
