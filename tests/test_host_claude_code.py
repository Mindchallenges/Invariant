"""The Claude Code host adapter.

This suite exists because of a shipped regression: the provenance marker was
written as an HTML comment on line 1, which pushed the YAML frontmatter to line
2, and a host that keys agents off frontmatter starting at line 1 skipped all
four library agents silently. Nothing failed. The agents were simply absent.

``verify`` did not catch it, because ``verify`` looked for the marker on line 1
— it asserted the presence of the very thing that broke loading. So the tests
here check two independent properties and never let one stand in for the other:

* **loadable** — the host can parse this file into an agent at all
* **current**  — the file matches the version the library ships

A test that only proved "current" is what let this reach a user.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from invariant.core.errors import InvariantError
from invariant.hosts import claude_code
from invariant.hosts.claude_code import (
    CHECK_AGENTS_CURRENT,
    CHECK_HOOKS,
    CHECK_HOOKS_RUN,
    CHECK_AGENTS_LOADABLE,
    ClaudeCodeHost,
    agent_frontmatter,
    loadability_problem,
)

SHIPPED = sorted((claude_code.DATA_DIR / "agents").glob("*.md"))


def check(checks, label):
    """One verify() row by label, so tests read as claims not tuple indexing."""
    for name, ok, detail in checks:
        if name == label:
            return ok, detail
    raise AssertionError(f"verify() produced no check named {label!r}")


@pytest.fixture
def host(tmp_path):
    return ClaudeCodeHost(tmp_path)


@pytest.fixture
def installed(host):
    # A real installed project always has a manifest — `init` writes it before
    # it installs the shims. It matters to the liveness probes: a shim that
    # finds the CLI but no manifest now answers with the DEGRADED marker
    # (AUD-04) rather than a silent `{}`, so a manifest-less fixture would
    # make a healthy install read as broken.
    manifest = host.root / ".claude" / "invariant.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        yaml.safe_dump({"version": 1, "project": {"name": "demo"}}),
        encoding="utf-8",
    )
    host.install()
    return host


def agents_dir(host):
    return host.claude_dir / "agents"


# -- the shipped data itself -------------------------------------------


class TestShippedAgentFiles:
    """Guards on the source files, so a newly added agent cannot regress this.

    These run against ``data/agents`` rather than an install, because a source
    file with no frontmatter would produce a broken install no matter how
    careful the installer is.
    """

    def test_there_are_agents_to_install(self):
        assert SHIPPED, "no agents shipped; the rest of this suite would be vacuous"

    @pytest.mark.parametrize("source", SHIPPED, ids=lambda p: p.stem)
    def test_frontmatter_opens_on_line_one(self, source):
        assert source.read_text(encoding="utf-8").startswith("---\n")

    @pytest.mark.parametrize("source", SHIPPED, ids=lambda p: p.stem)
    def test_frontmatter_declares_name_and_description(self, source):
        front = agent_frontmatter(source.read_text(encoding="utf-8"))
        assert front is not None
        assert front.get("name")
        assert front.get("description")

    @pytest.mark.parametrize("source", SHIPPED, ids=lambda p: p.stem)
    def test_name_matches_filename(self, source):
        # The host addresses an agent by its `name`, but a human looks for the
        # file. A mismatch is invisible until someone invokes the wrong thing.
        front = agent_frontmatter(source.read_text(encoding="utf-8"))
        assert front["name"] == source.stem


# -- installation ------------------------------------------------------


class TestInstall:
    def test_every_agent_lands(self, installed):
        landed = {p.name for p in agents_dir(installed).glob("*.md")}
        assert landed == {p.name for p in SHIPPED}

    @pytest.mark.parametrize("source", SHIPPED, ids=lambda p: p.stem)
    def test_installed_file_still_opens_with_frontmatter(self, installed, source):
        """The regression, stated directly."""
        text = (agents_dir(installed) / source.name).read_text(encoding="utf-8")
        assert text.startswith("---\n"), (
            "frontmatter must be the first line or the host skips the file "
            "without reporting anything"
        )

    @pytest.mark.parametrize("source", SHIPPED, ids=lambda p: p.stem)
    def test_installed_frontmatter_is_valid_yaml_with_a_name(self, installed, source):
        front = agent_frontmatter(
            (agents_dir(installed) / source.name).read_text(encoding="utf-8")
        )
        assert front is not None, "installed frontmatter did not parse"
        assert front["name"] == source.stem

    @pytest.mark.parametrize("source", SHIPPED, ids=lambda p: p.stem)
    def test_marker_is_carried_without_becoming_a_yaml_key(self, installed, source):
        """Provenance must not change what the host parses.

        A comment inside the frontmatter is visible to grep and to ``verify``
        while being nothing at all to a YAML parser — which is the whole trick.
        """
        text = (agents_dir(installed) / source.name).read_text(encoding="utf-8")
        assert claude_code.MARKER_PREFIX in text
        front = agent_frontmatter(text)
        assert not any("invariant" in str(key).lower() for key in front)

    @pytest.mark.parametrize("source", SHIPPED, ids=lambda p: p.stem)
    def test_body_is_untouched(self, installed, source):
        original = source.read_text(encoding="utf-8")
        installed_text = (agents_dir(installed) / source.name).read_text(
            encoding="utf-8"
        )
        body = original.split("---\n", 2)[2]
        assert installed_text.endswith(body)

    def test_install_is_idempotent(self, host):
        host.install()
        first = {p.name: p.read_text(encoding="utf-8") for p in agents_dir(host).glob("*.md")}
        host.install()
        second = {p.name: p.read_text(encoding="utf-8") for p in agents_dir(host).glob("*.md")}
        assert first == second

    def test_reinstall_repairs_a_broken_agent(self, installed):
        target = agents_dir(installed) / SHIPPED[0].name
        target.write_text("<!-- junk -->\n---\nname: x\n---\n", encoding="utf-8")
        installed.install()
        assert check(installed.verify(), CHECK_AGENTS_LOADABLE)[0]

    def test_a_source_without_frontmatter_is_refused(self, host, tmp_path):
        bad = tmp_path / "bad-agent.md"
        bad.write_text("just prose, no frontmatter\n", encoding="utf-8")
        with pytest.raises(InvariantError) as exc:
            host.install_agent(bad, agents_dir(host) / "bad-agent.md")
        assert "frontmatter" in str(exc.value).lower()


# -- verification ------------------------------------------------------


class TestVerifyLoadable:
    """Self-tests for the instrument.

    Each of these hands ``verify`` a *known* bad file and demands it go red.
    A checker that has never gone red on a positive is not evidence of anything
    — that is precisely how the original bug was certified as fine.
    """

    def test_clean_install_is_loadable_and_current(self, installed):
        checks = installed.verify()
        assert check(checks, CHECK_AGENTS_LOADABLE)[0]
        assert check(checks, CHECK_AGENTS_CURRENT)[0]

    def test_the_shipped_regression_is_detected(self, installed):
        """The exact layout that shipped: marker above the frontmatter."""
        target = agents_dir(installed) / SHIPPED[0].name
        body = SHIPPED[0].read_text(encoding="utf-8")
        digest = claude_code.digest_of(body)
        target.write_text(f"<!-- invariant-managed: {digest} -->\n{body}", encoding="utf-8")

        ok, detail = check(installed.verify(), CHECK_AGENTS_LOADABLE)
        assert not ok
        assert SHIPPED[0].stem in detail

    def test_the_old_check_could_not_have_caught_it(self, installed):
        """Why the currency check could never have covered loadability.

        The broken file carries the correct digest, so the substring test the
        original ``verify`` performed answers yes on a file that loads nothing.
        """
        target = agents_dir(installed) / SHIPPED[0].name
        body = SHIPPED[0].read_text(encoding="utf-8")
        digest = claude_code.digest_of(body)
        target.write_text(f"<!-- invariant-managed: {digest} -->\n{body}", encoding="utf-8")

        assert digest in target.read_text(encoding="utf-8")  # the old check's basis
        assert not check(installed.verify(), CHECK_AGENTS_LOADABLE)[0]

    def test_unterminated_frontmatter_is_detected(self, installed):
        target = agents_dir(installed) / SHIPPED[0].name
        target.write_text("---\nname: feature-architect\nno closing fence\n", encoding="utf-8")
        assert not check(installed.verify(), CHECK_AGENTS_LOADABLE)[0]

    def test_a_colon_in_the_description_stays_loadable(self, installed):
        """The false positive this check must never produce."""
        target = agents_dir(installed) / SHIPPED[0].name
        target.write_text(
            f"---\nname: {SHIPPED[0].stem}\ndescription: Use when: X\n---\nbody\n",
            encoding="utf-8",
        )
        assert check(installed.verify(), CHECK_AGENTS_LOADABLE)[0]

    def test_missing_description_is_detected(self, installed):
        """Without a description the host has no basis to route work to it."""
        target = agents_dir(installed) / SHIPPED[0].name
        target.write_text(f"---\nname: {SHIPPED[0].stem}\n---\nbody\n", encoding="utf-8")
        assert not check(installed.verify(), CHECK_AGENTS_LOADABLE)[0]

    def test_name_stem_mismatch_is_detected(self, installed):
        target = agents_dir(installed) / SHIPPED[0].name
        target.write_text(
            "---\nname: something-else\ndescription: d\n---\nbody\n", encoding="utf-8"
        )
        assert not check(installed.verify(), CHECK_AGENTS_LOADABLE)[0]

    def test_absent_agent_is_not_loadable(self, installed):
        (agents_dir(installed) / SHIPPED[0].name).unlink()
        assert not check(installed.verify(), CHECK_AGENTS_LOADABLE)[0]

    def test_loadable_check_names_the_repair(self, installed):
        (agents_dir(installed) / SHIPPED[0].name).unlink()
        _, detail = check(installed.verify(), CHECK_AGENTS_LOADABLE)
        assert "invariant assets install" in detail


class TestVerifyCurrency:
    def test_local_edit_is_reported_as_drift(self, installed):
        target = agents_dir(installed) / SHIPPED[0].name
        text = target.read_text(encoding="utf-8")
        target.write_text(text + "\n\nlocal addition\n", encoding="utf-8")
        ok, detail = check(installed.verify(), CHECK_AGENTS_CURRENT)
        assert not ok
        assert SHIPPED[0].stem in detail

    def test_drift_does_not_make_a_file_unloadable(self, installed):
        """Drift and breakage are different verdicts and must stay separable.

        A local edit is a choice the library respects; it must not be reported
        as the agent being broken, or the two get fixed with the same reflex.
        """
        target = agents_dir(installed) / SHIPPED[0].name
        target.write_text(
            target.read_text(encoding="utf-8") + "\nlocal\n", encoding="utf-8"
        )
        checks = installed.verify()
        assert not check(checks, CHECK_AGENTS_CURRENT)[0]
        assert check(checks, CHECK_AGENTS_LOADABLE)[0]


# -- frontmatter helper ------------------------------------------------


class TestAgentFrontmatter:
    def test_returns_none_when_the_first_line_is_not_a_fence(self):
        assert agent_frontmatter("<!-- x -->\n---\nname: a\n---\nbody\n") is None

    def test_returns_none_when_unterminated(self):
        assert agent_frontmatter("---\nname: a\n") is None

    def test_a_colon_inside_a_value_is_not_an_error(self):
        """The host reads these line by line; strict YAML would reject them.

        Every agent in this library has a description containing ``": "``, as
        does the project-local agent that has always loaded correctly. Judging
        these files with a YAML parser would condemn four working agents — so
        this helper models the host, not the spec the format resembles.
        """
        front = agent_frontmatter("---\nname: a\ndescription: Use when: X\n---\nb\n")
        assert front == {"name": "a", "description": "Use when: X"}

    def test_returns_none_when_no_keys_are_declared(self):
        assert agent_frontmatter("---\n- a\n- b\n---\nbody\n") is None

    def test_a_continuation_line_does_not_become_a_key(self):
        front = agent_frontmatter("---\nname: a\n  wrapped text\n---\nb\n")
        assert front == {"name": "a"}

    def test_ignores_comment_lines(self):
        front = agent_frontmatter("---\n# invariant-managed: abc\nname: a\n---\nb\n")
        assert front == {"name": "a"}

    def test_tolerates_crlf(self):
        assert agent_frontmatter("---\r\nname: a\r\n---\r\nbody\r\n") == {"name": "a"}


class TestLoadabilityReasons:
    """The reason is the deliverable — "broken" alone tells nobody what to do."""

    def test_a_file_with_no_frontmatter_says_so(self):
        assert loadability_problem("prose only\n", "x") == "no frontmatter on line 1"

    def test_a_block_with_no_keys_says_so(self):
        problem = loadability_problem("---\n- a\n---\nbody\n", "x")
        assert problem == "frontmatter declares no keys"

    def test_a_missing_name_says_so(self):
        problem = loadability_problem("---\ndescription: d\n---\nbody\n", "x")
        assert problem == "no name"

    def test_a_mismatched_name_names_both_sides(self):
        problem = loadability_problem("---\nname: a\ndescription: d\n---\nb\n", "x")
        assert "'a'" in problem and "x.md" in problem

    def test_a_good_file_has_no_problem(self):
        assert loadability_problem("---\nname: x\ndescription: d\n---\nb\n", "x") is None


class TestMarkerDigest:
    def test_absent_frontmatter_has_no_marker(self):
        assert claude_code._marker_digest("no frontmatter\n") is None

    def test_frontmatter_without_a_marker_returns_none(self):
        assert claude_code._marker_digest("---\nname: a\n---\nb\n") is None

    def test_the_marker_is_read_back(self, installed):
        text = (agents_dir(installed) / SHIPPED[0].name).read_text(encoding="utf-8")
        assert claude_code._marker_digest(text) == claude_code.digest_of(
            SHIPPED[0].read_text(encoding="utf-8")
        )


# -- settings wiring ---------------------------------------------------


class TestSettingsWiring:
    def test_existing_keys_survive(self, host):
        settings = host.claude_dir / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text('{"model": "opus", "hooks": {}}', encoding="utf-8")
        host.install()
        data = yaml.safe_load(settings.read_text(encoding="utf-8"))
        assert data["model"] == "opus"

    def test_hooks_are_not_duplicated_on_reinstall(self, host):
        host.install()
        host.install()
        import json

        data = json.loads((host.claude_dir / "settings.json").read_text(encoding="utf-8"))
        for entries in data["hooks"].values():
            commands = [
                h["command"] for entry in entries for h in entry.get("hooks", [])
            ]
            assert len(commands) == len(set(commands))

    def test_invalid_settings_json_is_refused_rather_than_overwritten(self, host):
        settings = host.claude_dir / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text("{ not json", encoding="utf-8")
        with pytest.raises(InvariantError):
            host.install()
        assert settings.read_text(encoding="utf-8") == "{ not json"


# -- did the agent actually fire ---------------------------------------


class FakePipelines:
    def __init__(self):
        self.docs: list[tuple[str, str]] = []
        self.agents: list[tuple[str, str]] = []
        self.agent_how: list[tuple[str, str, str]] = []

    def record_docs_read(self, session_id, path):
        self.docs.append((session_id, path))

    def record_agent_run(self, session_id, agent_type, *, how="dispatch"):
        self.agents.append((session_id, agent_type))
        self.agent_how.append((session_id, agent_type, how))
        return "recorded"


class FakeSession:
    id = "s1"


class FakeSessions:
    def __init__(self):
        self.adopted: list[tuple[str, str]] = []
        self.ended: list[str] = []
        self.raises = None

    def adopt_host_session(self, session_id, host_session_id, force=False):
        if self.raises:
            raise self.raises
        self.adopted.append((session_id, host_session_id))

    def end_host_session(self, host_session_id):
        self.ended.append(host_session_id)


class FakeCtx:
    """Deliberately answers only the question the host is supposed to ask.

    There is no ``current_session`` here. Recording used to resolve by working
    directory (BUG_010), and a fake that answers both routes with the same
    session would let a regression to the weaker one pass every test in this
    file. Asking the wrong question now raises.
    """

    def __init__(self, host_session_id="host-1111", how="host"):
        self.pipelines = FakePipelines()
        self.sessions = FakeSessions()
        self.host_session_id = host_session_id
        #: How the fake claims it identified the session. ``host`` is a proven
        #: route; tests that need a guess pass ``origin``.
        self.how = how
        #: Every payload attribution was asked about, so a test can assert the
        #: call's own path was in hand at the moment the owner was chosen.
        self.asked: list[dict] = []

    def session_for_payload(self, payload):
        return self.resolution_for_payload(payload).session

    def resolution_for_payload(self, payload):
        self.asked.append(payload)
        from invariant.domains.session import Resolution

        return Resolution(FakeSession(), self.how)

    def rule_context(self, payload=None):  # pragma: no cover - unused here
        raise NotImplementedError


class TestAgentInvocationIsRecorded:
    """An agent that ran is a fact the host can observe; record it as one.

    The gates below read this evidence rather than asking the transcript, for
    the same reason the docs-first gate reads recorded reads: a gate that
    re-derives can be satisfied by accident.
    """

    def _post(self, host, payload):
        ctx = FakeCtx()
        host._post_tool_use(payload, ctx)
        return ctx.pipelines

    def test_task_tool_records_the_subagent_type(self, host):
        pipelines = self._post(
            host, {"tool_name": "Task", "tool_input": {"subagent_type": "feature-architect"}}
        )
        assert pipelines.agents == [("s1", "feature-architect")]

    def test_agent_tool_name_is_recorded_too(self, host):
        pipelines = self._post(
            host, {"tool_name": "Agent", "tool_input": {"subagent_type": "compliance-reviewer"}}
        )
        assert pipelines.agents == [("s1", "compliance-reviewer")]

    def test_an_agent_with_no_type_is_not_recorded(self, host):
        assert self._post(host, {"tool_name": "Task", "tool_input": {}}).agents == []

    def test_an_unrelated_tool_records_nothing(self, host):
        pipelines = self._post(
            host, {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        )
        assert pipelines.agents == []

    def test_a_read_still_records_the_doc(self, host):
        pipelines = self._post(
            host, {"tool_name": "Read", "tool_input": {"file_path": "/x/overview.md"}}
        )
        assert pipelines.docs == [("s1", "/x/overview.md")]


    def test_a_recording_failure_never_breaks_the_tool_call(self, host):
        """Evidence is a side effect; it must not be able to fail a tool.

        A hook that raises turns every Task invocation in the project into an
        error, which is a far worse outcome than a gate missing one data point.
        """
        class Exploding(FakePipelines):
            def record_agent_run(self, session_id, agent_type, *, how="dispatch"):
                raise InvariantError("state store is unwritable")

        ctx = FakeCtx()
        ctx.pipelines = Exploding()
        assert host._post_tool_use(
            {"tool_name": "Task", "tool_input": {"subagent_type": "feature-architect"}},
            ctx,
        ) == {}

    def test_the_hook_entry_point_routes_post_tool_use(self, host):
        ctx = FakeCtx()
        payload = '{"tool_name": "Task", "tool_input": {"subagent_type": "bug-reproducer"}}'
        assert host.handle("post-tool-use", payload, ctx) == {}
        assert ctx.pipelines.agents == [("s1", "bug-reproducer")]

    def test_malformed_hook_input_is_not_an_error(self, host):
        assert host.handle("post-tool-use", "not json at all", FakeCtx()) == {}

    def test_an_unknown_event_is_ignored(self, host):
        assert host.handle("session-end", "{}", FakeCtx()) == {}

    def test_the_owner_is_chosen_with_the_calls_own_path_in_hand(self, host):
        """The payload reaches attribution, not just the recording below it."""
        ctx = FakeCtx()
        payload = {"tool_name": "Read", "tool_input": {"file_path": "/x/overview.md"}}
        host._post_tool_use(payload, ctx)
        assert ctx.asked == [payload]

    def test_recording_is_skipped_without_a_session(self, host):
        class NoSession(FakeCtx):
            def resolution_for_payload(self, payload):
                from invariant.domains.session import Resolution

                return Resolution(None, "none")

        ctx = NoSession()
        host._post_tool_use(
            {"tool_name": "Task", "tool_input": {"subagent_type": "feature-architect"}}, ctx
        )
        assert ctx.pipelines.agents == []


class TestReadIsAttributedByItsPath:
    """Repro for BUG_010, narrow half — see the work-item entry.

    The suite above proves the recording *happens*; it cannot prove the
    recording lands on the right session, because its ``FakeCtx`` answers
    ``current_session`` with the same session no matter what was asked. This
    class therefore builds real repositories, real worktrees and a real
    ``Context``: the defect lives in attribution, so a harness that stubs
    attribution out cannot see it.

    An edit is attributed by its destination path — step 3 of the order in
    ``docs/architecture/state_model.md``, "Attribution" — which survives the
    host resetting its working directory. A Read carries the same absolute
    ``file_path`` in the same payload key, and the host pulls that key out a
    few lines further down purely to record it, but resolves the owner without
    it. With more than one session live and the hook firing from the shared
    checkout, the path is the only signal that names an owner, and it is the
    one signal not consulted.
    """

    HOST_ID = "host-standing-elsewhere"

    @pytest.fixture
    def two_live_sessions(self, tmp_path, monkeypatch):
        """A project with two isolated sessions, one of them mid-design.

        Two, because one is not the case attribution exists for: with a single
        session the working directory still resolves to it by origin, and the
        read is recorded by luck rather than by evidence. The documented
        rationale for path attribution is the several-sessions case, where
        "every one of them looks identical from the shared checkout".
        """
        import subprocess

        from invariant.cli.main import Context
        from invariant.core.manifest import MANIFEST_RELPATH
        from invariant.domains.pipeline import PipelineDomain
        from invariant.domains.session import SessionDomain

        # Resolution consults the environment first. A developer running this
        # suite with a session exported would otherwise get a pass that proves
        # only that the override works.
        monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
        monkeypatch.delenv("INVARIANT_HOST_SESSION_ID", raising=False)

        def git(*args):
            subprocess.run(
                ["git", *args], cwd=str(tmp_path), check=True, capture_output=True, text=True
            )

        (tmp_path / MANIFEST_RELPATH).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / MANIFEST_RELPATH).write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "project": {"name": "demo"},
                    "session": {"worktree_dir": ".claude/worktrees"},
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "docs" / "architecture").mkdir(parents=True)
        (tmp_path / "docs" / "architecture" / "overview.md").write_text(
            "# Overview\n\n```mermaid\ngraph TD\n  A-->B\n```\n", encoding="utf-8"
        )
        git("init", "-q", "-b", "master")
        # Deliberately not address-shaped: this repository's own privacy gate
        # blocks on anything that parses as an email, synthetic or not.
        git("config", "user.email", "tester")
        git("config", "user.name", "Test")
        git("add", "-A")
        git("commit", "-qm", "init")

        ctx = Context(cwd=tmp_path, host_session_id=self.HOST_ID)
        sessions = SessionDomain(ctx.manifest, ctx.store)
        pipelines = PipelineDomain(ctx.manifest, ctx.store)

        owner = sessions.start("alpha")
        sessions.isolate(owner.id, execute=True)
        peer = sessions.start("beta")
        sessions.isolate(peer.id, execute=True)
        pipelines.start(owner.id, task="the change alpha is designing")

        worktree = Path(sessions.get(owner.id).worktree)
        return {
            "project": tmp_path,
            "owner": owner.id,
            "peer": peer.id,
            "worktree": worktree,
            "peer_worktree": Path(sessions.get(peer.id).worktree),
            "doc": worktree / "docs" / "architecture" / "overview.md",
            "shared_doc": tmp_path / "docs" / "architecture" / "overview.md",
            "sessions": sessions,
            "pipelines": pipelines,
        }

    def post(self, world, payload, cwd=None, host_session_id=None):
        """Fire PostToolUse the way a host does — from wherever it is standing."""
        from invariant.cli.main import Context

        ClaudeCodeHost(world["project"])._post_tool_use(
            payload,
            Context(
                cwd=cwd or world["project"],
                host_session_id=(
                    self.HOST_ID if host_session_id is None else host_session_id
                ),
            ),
        )

    def evidence(self, world, session_id, key):
        pipeline = world["pipelines"].current(session_id)
        return list(pipeline.evidence.get(key, [])) if pipeline else []

    def read_gate(self, world):
        """What `invariant pipeline gates` would report, run in the worktree."""
        from invariant.core.manifest import Manifest
        from invariant.core.rules import RuleContext, evaluate

        worktree = world["worktree"]
        rule_ctx = RuleContext(
            manifest=Manifest.load(worktree),
            cwd=worktree,
            session={"id": world["owner"]},
            pipeline=world["pipelines"].current(world["owner"]).to_dict(),
            payload={},
            extra={},
        )
        results = evaluate(["docs.read_architecture"], rule_ctx)
        assert len(results) == 1, "the gate under test did not evaluate"
        return results[0].verdict

    def test_BUG_010_a_read_inside_a_worktree_satisfies_that_sessions_docs_gate(
        self, two_live_sessions
    ):
        from invariant.cli.main import Context

        world = two_live_sessions
        assert world["doc"].is_file(), "the doc to be read is not in the worktree"
        assert not self.read_gate(world).ok, "the gate was already satisfied; nothing to prove"

        # The hook fires from the shared checkout, which is where a host runs
        # it, and names an absolute path inside one session's worktree.
        ClaudeCodeHost(world["project"])._post_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": str(world["doc"])}},
            Context(cwd=world["project"], host_session_id=self.HOST_ID),
        )

        verdict = self.read_gate(world)
        assert verdict.ok, verdict.message

    def test_the_host_identifier_still_outranks_the_path(self, two_live_sessions):
        """Step 2 beats step 3, for reads as it already does for writes.

        Who is acting beats where the bytes are, which is what keeps a session
        reaching into a peer's worktree a *detectable* crossing rather than a
        silent reattribution. Reading a peer's file is not evidence that the
        peer did the reading.
        """
        world = two_live_sessions
        world["sessions"].adopt_host_session(world["peer"], self.HOST_ID)
        world["pipelines"].start(world["peer"], task="what beta is doing")

        self.post(
            world,
            {"tool_name": "Read", "tool_input": {"file_path": str(world["doc"])}},
        )

        assert self.evidence(world, world["peer"], "docs_read") == [str(world["doc"])]
        assert self.evidence(world, world["owner"], "docs_read") == []

    def test_a_path_in_no_worktree_falls_through_rather_than_dropping(
        self, two_live_sessions
    ):
        """An unmatched target is not an answer, and must not become a refusal.

        The shared checkout's own docs belong to no session's worktree. Before
        this fix that read resolved by working directory; it still must, or
        closing the path route would have cost the routes that already worked.
        """
        world = two_live_sessions
        self.post(
            world,
            {"tool_name": "Read", "tool_input": {"file_path": str(world["shared_doc"])}},
            cwd=world["worktree"],
            host_session_id=None,
        )
        assert self.evidence(world, world["owner"], "docs_read") == [
            str(world["shared_doc"])
        ]

    def test_an_agent_dispatch_carries_no_path_and_resolves_as_before(
        self, two_live_sessions
    ):
        """The narrow fix cannot reach the agent-role gates, and does not.

        A `Task` payload holds `subagent_type`, `description` and `prompt` and
        no path at all, so there is nothing for step 3 to match on: dispatch
        still resolves by host identifier or working directory exactly as it
        did. Recorded as a test so the wide half of BUG_010 cannot be mistaken
        for closed.
        """
        world = two_live_sessions
        dispatch = {
            "tool_name": "Task",
            "tool_input": {
                "subagent_type": "feature-architect",
                "description": "design the change",
                "prompt": "...",
            },
        }

        # From the shared checkout, with two sessions live and this host owning
        # neither: nobody is identifiable, so nothing is recorded.
        self.post(world, dispatch, host_session_id=None)
        assert self.evidence(world, world["owner"], "agents_run") == []

        # From inside the session's own worktree, the working directory answers.
        self.post(world, dispatch, cwd=world["worktree"], host_session_id=None)
        assert self.evidence(world, world["owner"], "agents_run") == [
            "feature-architect"
        ]


    def test_a_dispatch_naming_a_worktree_is_recorded_against_that_session(
        self, two_live_sessions
    ):
        """One host driving two tracks must not bank every agent on one of them.

        A parent that owns session alpha and dispatches an agent AT session
        beta -- naming beta's worktree in the brief, which is the only way the
        agent can work there -- used to record `feature-architect` against
        alpha, because a dispatch carries no `file_path` and so fell through to
        the host identifier. Beta's pipeline then showed no agent had run, and
        `agents.design_agent_ran` is block-level: no action available to beta
        could satisfy it, because the evidence had been filed under alpha.
        """
        world = two_live_sessions
        world["pipelines"].start(world["peer"], task="the change beta is designing")
        peer_worktree = world["peer_worktree"]

        self.post(
            world,
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "feature-architect",
                    "description": "design the change",
                    "prompt": f"Work inside {peer_worktree} and fix the thing.",
                },
            },
        )

        assert self.evidence(world, world["peer"], "agents_run") == [
            "feature-architect"
        ]
        assert self.evidence(world, world["owner"], "agents_run") == [], (
            "the dispatch was banked on the host's own session instead of the "
            "one the brief names"
        )

    def test_a_dispatch_at_a_peer_outranks_the_hosts_own_binding(
        self, two_live_sessions
    ):
        """The parent that OWNS a session must still be able to dispatch at another.

        The sibling test above proves the prompt route works when the host owns
        nothing. That is not the production case: a parent driving two tracks
        ran ``invariant session start`` for each, and the second bound the host,
        so ``by_host_session`` answers first and step 3 is never reached. Every
        dispatch then banks on whichever session the host happens to hold,
        including the ones whose briefs name a different worktree entirely --
        and the OTHER track's ``agents.*_ran`` gates become unsatisfiable by
        anything that track can do.

        Attribution by host is right for a read or an edit: reading a peer's
        file is not evidence that the peer did the reading, which is what
        ``test_the_host_identifier_still_outranks_the_path`` pins. A dispatch is
        the opposite shape. The host is not acting on the named worktree, it is
        DELEGATING into it, and the delegate is the one whose run gets recorded.
        One host binding cannot represent two concurrent delegations, so for a
        dispatch the brief is the better evidence -- and it is the only evidence
        that scales past one track.
        """
        world = two_live_sessions
        world["sessions"].adopt_host_session(world["owner"], self.HOST_ID)
        world["pipelines"].start(world["peer"], task="the change beta is designing")

        self.post(
            world,
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "compliance-reviewer",
                    "description": "review the change",
                    "prompt": f"Work inside {world['peer_worktree']} and review it.",
                },
            },
        )

        assert self.evidence(world, world["peer"], "agents_run") == [
            "compliance-reviewer"
        ], "the brief named beta's worktree and beta recorded nothing"
        assert self.evidence(world, world["owner"], "agents_run") == [], (
            "the dispatch was banked on the host's own session, which is the "
            "wedge: beta can never satisfy agents.review_agent_ran"
        )

    def test_a_dispatch_naming_two_worktrees_falls_through_rather_than_guessing(
        self, two_live_sessions
    ):
        """Ambiguity resolves as before; a wrong attribution is worse than none.

        Naming a peer's tree alongside your own is ordinary -- "do not touch
        X" -- so a brief mentioning two live worktrees must not silently pick
        one. Here the host owns nothing, so falling through records nothing.
        """
        world = two_live_sessions
        world["pipelines"].start(world["peer"], task="the change beta is designing")

        self.post(
            world,
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "feature-architect",
                    "description": "design the change",
                    "prompt": (
                        f"Work in {world['worktree']}. "
                        f"Do NOT touch {world['peer_worktree']}."
                    ),
                },
            },
            host_session_id=None,
        )

        assert self.evidence(world, world["owner"], "agents_run") == []
        assert self.evidence(world, world["peer"], "agents_run") == []


class TestBUG028TheAgentThatCreatedThePipelineIsInIt:
    """A subagent that starts its own session and pipeline must be recordable.

    The reported wedge, end to end. A ``feature-architect`` was dispatched, and
    IT ran `session start`, `session isolate` and `pipeline start` before
    authoring anything. The dispatch was therefore observed at a moment when
    the session it would belong to did not exist and the pipeline it would be
    evidence for did not exist either — so `agents.design_agent_ran`, which is
    block-level, refused the pipeline the design agent had itself created, and
    no action available to that pipeline could satisfy it.

    No lookup fixes this. A better resolution still resolves to nothing when
    the destination has not been created yet. What is available instead is the
    ACTOR: the host stamps the acting agent's type on every hook payload,
    including the ones the subagent's own tool calls raise, so the run is
    observable from the agent's side even though the dispatch was not
    attributable from the parent's.
    """

    HOST_ID = "host-parent"

    @pytest.fixture
    def world(self, tmp_path, monkeypatch):
        """A governed repository whose design role names one agent."""
        import subprocess

        from invariant.cli.main import Context
        from invariant.core.manifest import MANIFEST_RELPATH

        monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
        monkeypatch.delenv("INVARIANT_HOST_SESSION_ID", raising=False)

        (tmp_path / MANIFEST_RELPATH).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / MANIFEST_RELPATH).write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "project": {"name": "demo"},
                    "session": {"worktree_dir": ".claude/worktrees"},
                    "agents": {"roles": {"design": ["feature-architect"]}},
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "docs" / "architecture").mkdir(parents=True)

        def git(*args):
            subprocess.run(
                ["git", *args], cwd=str(tmp_path), check=True, capture_output=True
            )

        git("init", "-q", "-b", "master")
        git("config", "user.email", "dev@example.invalid")
        git("config", "user.name", "Dev")
        git("add", "-A")
        git("commit", "-qm", "init")
        return tmp_path, Context(cwd=tmp_path, host_session_id=self.HOST_ID)

    def post(self, world, payload, cwd=None, host_session_id=None):
        from invariant.cli.main import Context

        root, _ = world
        return ClaudeCodeHost(root)._post_tool_use(
            payload,
            Context(
                cwd=cwd or root,
                host_session_id=(
                    self.HOST_ID if host_session_id is None else host_session_id
                ),
            ),
        )

    def design_gate(self, world, session_id):
        from invariant.core.manifest import Manifest
        from invariant.core.rules import RuleContext, evaluate

        root, ctx = world
        results = evaluate(
            ["agents.design_agent_ran"],
            RuleContext(
                manifest=Manifest.load(root),
                cwd=root,
                session={"id": session_id},
                pipeline=ctx.pipelines.current(session_id).to_dict(),
                payload={},
                extra={},
            ),
        )
        assert len(results) == 1, "the gate under test did not evaluate"
        return results[0].verdict

    def test_the_architect_that_created_the_pipeline_clears_the_design_gate(
        self, world
    ):
        """The whole defect, in the order it actually happened."""
        _root, ctx = world

        # 1. The parent dispatches. There is no session and no pipeline yet, so
        #    there is nothing to attribute this to and nothing to be evidence
        #    for. This is the event the engine used to rely on.
        self.post(
            world,
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "feature-architect",
                    "description": "design the change",
                    "prompt": "Design it.",
                },
            },
        )

        # 2. The SUBAGENT registers the session. Its Bash call carries the
        #    parent's host session id -- a subagent shares it -- and the agent
        #    type the host stamped on the payload.
        session = ctx.sessions.start("designs-itself")
        self.post(
            world,
            {
                "tool_name": "Bash",
                "agent_type": "feature-architect",
                "tool_input": {"command": "invariant session start designs-itself"},
                "tool_response": f"session {session.id} (designs-itself) registered",
            },
        )
        ctx.sessions.isolate(session.id, execute=True)

        # 3. The subagent starts the pipeline, still as itself.
        ctx.pipelines.start(session.id, task="the ADR the architect is writing")
        assert not self.design_gate(world, session.id).ok, (
            "the gate was already satisfied; there is nothing to prove"
        )

        # 4. The subagent does its work. Every call it makes is stamped.
        self.post(
            world,
            {
                "tool_name": "Write",
                "agent_type": "feature-architect",
                "tool_input": {
                    "file_path": str(
                        Path(ctx.sessions.get(session.id).worktree)
                        / "docs"
                        / "adr"
                        / "ADR-062.md"
                    )
                },
            },
        )

        verdict = self.design_gate(world, session.id)
        assert verdict.ok, verdict.message

    def test_the_actor_is_recorded_as_an_actor_not_as_a_dispatch(self, world):
        """The record says WHY the agent is in it, because the two differ.

        A dispatch proves somebody asked for this agent and that the request
        resolved here. An actor reading proves the agent operated in this
        pipeline. Only the second survives the question "did the design
        actually happen in this pipeline", so the record keeps them apart
        instead of flattening both into a name in a list.
        """
        root, ctx = world
        session = ctx.sessions.start("designs-itself")
        ctx.sessions.adopt_host_session(session.id, self.HOST_ID)
        ctx.pipelines.start(session.id, task="t")

        self.post(
            world,
            {
                "tool_name": "Read",
                "agent_type": "feature-architect",
                "tool_input": {"file_path": str(root / "docs" / "architecture")},
            },
        )
        self.post(
            world,
            {
                "tool_name": "Task",
                "tool_input": {"subagent_type": "compliance-reviewer"},
            },
        )

        evidence = ctx.pipelines.current(session.id).evidence
        assert evidence["agents_run_how"] == {
            "feature-architect": "actor",
            "compliance-reviewer": "dispatch",
        }

    def test_an_actor_reading_is_not_downgraded_by_a_later_dispatch(self, world):
        _root, ctx = world
        session = ctx.sessions.start("s")
        ctx.sessions.adopt_host_session(session.id, self.HOST_ID)
        ctx.pipelines.start(session.id, task="t")

        actor = {
            "tool_name": "Bash",
            "agent_type": "feature-architect",
            "tool_input": {"command": "ls"},
        }
        self.post(world, actor)
        self.post(
            world,
            {
                "tool_name": "Task",
                "tool_input": {"subagent_type": "feature-architect"},
            },
        )

        evidence = ctx.pipelines.current(session.id).evidence
        assert evidence["agents_run"] == ["feature-architect"], "recorded twice"
        assert evidence["agents_run_how"]["feature-architect"] == "actor", (
            "the weaker reading overwrote the stronger one"
        )

    def test_an_ordinary_turn_carries_no_actor_and_records_none(self, world):
        """`agent_type` is absent from a main-loop payload, and absence is not
        an empty agent. Asserted on key PRESENCE, not on contents: `.get(k) or
        []` cannot tell a dropped write from a key that was never written, and
        that conflation already cost one analysis of this bug."""
        _root, ctx = world
        session = ctx.sessions.start("s")
        ctx.sessions.adopt_host_session(session.id, self.HOST_ID)
        ctx.pipelines.start(session.id, task="t")

        self.post(world, {"tool_name": "Bash", "tool_input": {"command": "ls"}})

        assert "agents_run" not in ctx.pipelines.current(session.id).evidence

    def test_an_actor_working_outside_an_isolated_session_is_not_banked_on_it(
        self, world
    ):
        """The contamination that actually happened, from the actor side.

        A subagent shares its PARENT's `session_id`, so the host route answers
        for every agent that parent dispatched — including ones working in the
        shared checkout while the bound session sits isolated elsewhere. Two
        real pipelines carry exactly this: a `backlog-curator` and a
        `general-purpose` agent, dispatched by the same host that owned the
        session, filed against a pipeline that had recorded none of its own
        work.

        `Resolution.proven` does NOT catch this — the host route identified the
        session, it did not guess it. An actor row has to clear a higher bar
        than a dispatch row, because it asserts the agent worked HERE.
        """
        _root, ctx = world
        session = ctx.sessions.start("isolated-elsewhere")
        ctx.sessions.adopt_host_session(session.id, self.HOST_ID)
        ctx.sessions.isolate(session.id, execute=True)
        ctx.pipelines.start(session.id, task="what this session is really doing")

        # A curator filing a bug in the SHARED checkout, dispatched by the same
        # host. No path inside the worktree, no worktree named in a brief.
        self.post(
            world,
            {
                "tool_name": "Bash",
                "agent_type": "backlog-curator",
                "tool_input": {"command": "invariant item new"},
            },
        )

        assert "agents_run" not in ctx.pipelines.current(session.id).evidence, (
            "an agent working in the shared checkout was recorded as having "
            "worked in an isolated session's pipeline"
        )

    def test_an_actor_inside_the_worktree_is_still_recorded(self, world):
        """The restriction above must not cost the case the fix exists for.

        The same host, the same binding, the same isolated session — but a call
        whose path lands inside the worktree. That is the architect authoring
        its ADR, and it must still record.
        """
        _root, ctx = world
        session = ctx.sessions.start("designs-itself")
        ctx.sessions.adopt_host_session(session.id, self.HOST_ID)
        ctx.sessions.isolate(session.id, execute=True)
        ctx.pipelines.start(session.id, task="the ADR the architect is writing")

        self.post(
            world,
            {
                "tool_name": "Write",
                "agent_type": "feature-architect",
                "tool_input": {
                    "file_path": str(
                        Path(ctx.sessions.get(session.id).worktree)
                        / "docs"
                        / "adr"
                        / "ADR-062.md"
                    )
                },
            },
        )

        assert ctx.pipelines.current(session.id).evidence["agents_run"] == [
            "feature-architect"
        ]

    def test_an_actor_in_a_second_tracks_worktree_is_recorded_there(self, world):
        """One host binding cannot represent two concurrent tracks.

        A parent that owns session A and drives session B has every pathless
        call resolve to A, because step 2 answers first. For a DISPATCH that is
        already inverted when the brief names B's worktree. For an actor there
        is no brief to read — but there is something better: the agent is
        standing in B, or writing into it, right now.

        Refusing here would keep A clean and leave B unable to record any agent
        that never names a path, which is the original wedge seen from the
        other end. So the location wins, and it wins for this route only:
        reading a peer's file stays a detectable crossing, because a main-loop
        turn never names an actor.
        """
        _root, ctx = world
        owned = ctx.sessions.start("track-a")
        ctx.sessions.adopt_host_session(owned.id, self.HOST_ID)
        ctx.sessions.isolate(owned.id, execute=True)
        ctx.pipelines.start(owned.id, task="what the host is bound to")

        other = ctx.sessions.start("track-b")
        ctx.sessions.isolate(other.id, execute=True)
        ctx.pipelines.start(other.id, task="the second track")
        assert ctx.sessions.get(other.id).host_session_id is None, (
            "the second track is bound; the 1:1 rule did not hold and this "
            "test is not exercising what it claims"
        )

        self.post(
            world,
            {
                "tool_name": "Write",
                "agent_type": "feature-architect",
                "tool_input": {
                    "file_path": str(
                        Path(ctx.sessions.get(other.id).worktree) / "src" / "x.py"
                    )
                },
            },
        )

        assert ctx.pipelines.current(other.id).evidence["agents_run"] == [
            "feature-architect"
        ], "the second track recorded nothing an agent demonstrably did in it"
        assert "agents_run" not in ctx.pipelines.current(owned.id).evidence, (
            "the host's own session absorbed a run that happened elsewhere"
        )

    def test_a_session_with_no_worktree_still_accepts_the_host_route(self, world):
        """A session that has not isolated has nowhere else its agents could be.

        The shared checkout IS that session's location, so the host binding
        cannot be contradicted by where the call stands, and refusing here
        would lose every agent that runs before `session isolate`.
        """
        _root, ctx = world
        session = ctx.sessions.start("never-isolated")
        ctx.sessions.adopt_host_session(session.id, self.HOST_ID)
        ctx.pipelines.start(session.id, task="t")
        assert ctx.sessions.get(session.id).worktree is None

        self.post(
            world,
            {
                "tool_name": "Bash",
                "agent_type": "feature-architect",
                "tool_input": {"command": "ls"},
            },
        )

        assert ctx.pipelines.current(session.id).evidence["agents_run"] == [
            "feature-architect"
        ]

    def test_a_guessed_session_never_receives_an_actor(self, world):
        """The foreign-write cell, from the actor side.

        A curator running in the main checkout under a different host resolves
        by `origin` -- "the only session registered at or above here that is
        not somebody else's" -- which is a guess. A guess must never write
        evidence, because the pipeline that receives it clears its gate on work
        that never happened in it.
        """
        root, ctx = world
        victim = ctx.sessions.start("someone-elses-work")
        ctx.pipelines.start(victim.id, task="t")
        other = ctx.sessions.start("bound-elsewhere")
        ctx.sessions.adopt_host_session(other.id, self.HOST_ID)

        # A different host entirely: `other` is excluded as somebody else's, so
        # `victim` is the single candidate left standing and `origin` answers.
        from invariant.cli.main import Context

        resolution = Context(
            cwd=root, host_session_id="host-stranger"
        ).resolution_for_payload({"tool_name": "Bash", "tool_input": {}})
        assert resolution.how == "origin", (
            f"the guess route was not exercised (got {resolution.how!r})"
        )
        assert resolution.session.id == victim.id

        self.post(
            world,
            {
                "tool_name": "Bash",
                "agent_type": "backlog-curator",
                "tool_input": {"command": "ls"},
            },
            host_session_id="host-stranger",
        )

        assert "agents_run" not in ctx.pipelines.current(victim.id).evidence, (
            "a foreign agent was written to a pipeline that never ran it"
        )

    def test_a_guessed_session_never_receives_a_dispatch_and_is_told_so(
        self, world
    ):
        """The same cell from the dispatch side -- and it stops being silent.

        BUG_028's cost was not only the wrong write. It was that the dispatcher
        could not tell a write that landed from one that vanished until a
        blocking gate refused, after the phase that needed the evidence.
        """
        _root, ctx = world
        victim = ctx.sessions.start("someone-elses-work")
        ctx.pipelines.start(victim.id, task="t")
        other = ctx.sessions.start("bound-elsewhere")
        ctx.sessions.adopt_host_session(other.id, self.HOST_ID)

        response = self.post(
            world,
            {
                "tool_name": "Task",
                "tool_input": {"subagent_type": "backlog-curator"},
            },
            host_session_id="host-stranger",
        )

        assert "agents_run" not in ctx.pipelines.current(victim.id).evidence
        said = response["hookSpecificOutput"]["additionalContext"]
        assert "NOT recorded" in said and "backlog-curator" in said

    def test_a_dispatch_before_any_pipeline_is_allowed_and_announced(self, world):
        """The legitimate case the old silence was defending, kept and voiced.

        Dispatching an agent before starting a pipeline is allowed and still
        is: nothing raises and nothing is refused. What changed is that the
        caller is told the dispatch cannot be evidence, at the moment it
        happens, while starting a pipeline first is still an option.
        """
        _root, ctx = world
        session = ctx.sessions.start("no-pipeline-yet")
        ctx.sessions.adopt_host_session(session.id, self.HOST_ID)
        assert ctx.pipelines.current(session.id) is None

        response = self.post(
            world,
            {
                "tool_name": "Task",
                "tool_input": {"subagent_type": "feature-architect"},
            },
        )

        said = response["hookSpecificOutput"]["additionalContext"]
        assert "no pipeline running" in said
        assert "feature-architect" in said

    def test_a_dispatch_that_lands_says_nothing(self, world):
        """Silence is still correct when there is nothing to report."""
        _root, ctx = world
        session = ctx.sessions.start("s")
        ctx.sessions.adopt_host_session(session.id, self.HOST_ID)
        ctx.pipelines.start(session.id, task="t")

        assert (
            self.post(
                world,
                {
                    "tool_name": "Task",
                    "tool_input": {"subagent_type": "feature-architect"},
                },
            )
            == {}
        )
        assert ctx.pipelines.current(session.id).evidence["agents_run"] == [
            "feature-architect"
        ]


class TestAutomaticBinding:
    """`session start` runs through Bash, which carries no hook payload.

    So the CLI cannot see the host identifier and cannot record it. PostToolUse
    can: it has the identifier and it brackets the exact call that registered
    the session, so the binding is made there rather than left to a flag
    someone has to remember to pass.
    """

    def post(self, host, command, response, host_session_id="host-1111"):
        ctx = FakeCtx(host_session_id=host_session_id)
        host._post_tool_use(
            {
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "tool_response": response,
            },
            ctx,
        )
        return ctx.sessions

    def test_a_started_session_is_bound(self, host):
        sessions = self.post(
            host,
            "invariant session start fix-the-thing",
            "session ab12cd34 (fix-the-thing) registered\n  next: ...",
        )
        assert sessions.adopted == [("ab12cd34", "host-1111")]

    def test_the_json_form_is_bound_too(self, host):
        sessions = self.post(
            host,
            "invariant session start x --json",
            '{"session": {"id": "ff00aa11", "name": "x"}}',
        )
        assert sessions.adopted == [("ff00aa11", "host-1111")]

    def test_a_structured_tool_response_is_read(self, host):
        sessions = self.post(
            host,
            "invariant session start x",
            {"stdout": "session 99887766 (x) registered"},
        )
        assert sessions.adopted == [("99887766", "host-1111")]

    def test_a_command_reached_through_cd_still_binds(self, host):
        sessions = self.post(
            host,
            "cd /somewhere && invariant session start x",
            "session 12345678 (x) registered",
        )
        assert sessions.adopted == [("12345678", "host-1111")]

    def test_an_unrelated_command_binds_nothing(self, host):
        assert self.post(host, "git status", "on branch master").adopted == []

    def test_a_different_invariant_command_binds_nothing(self, host):
        sessions = self.post(host, "invariant session list", "session ab12cd34 (x) ...")
        assert sessions.adopted == []

    def test_unparseable_output_binds_nothing(self, host):
        """Guessing which session was created would bind the wrong one."""
        sessions = self.post(host, "invariant session start x", "command failed")
        assert sessions.adopted == []

    def test_no_host_identifier_binds_nothing(self, host):
        ctx = FakeCtx(host_session_id=None)
        host._post_tool_use(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "invariant session start x"},
                "tool_response": "session ab12cd34 (x) registered",
            },
            ctx,
        )
        assert ctx.sessions.adopted == []

    def test_a_refused_binding_does_not_fail_the_command(self, host):
        """The 1:1 rule working is not a reason to error a finished command."""
        ctx = FakeCtx()
        ctx.sessions.raises = InvariantError("this host already owns one")
        host._post_tool_use(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "invariant session start x"},
                "tool_response": "session ab12cd34 (x) registered",
            },
            ctx,
        )
        assert ctx.sessions.adopted == []


class TestSessionEndReleasesTheBinding:
    def test_the_host_session_is_marked_ended(self, host):
        ctx = FakeCtx(host_session_id="host-9999")
        host.handle("session-end", "{}", ctx)
        assert ctx.sessions.ended == ["host-9999"]

    def test_nothing_happens_without_a_host_identifier(self, host):
        ctx = FakeCtx(host_session_id=None)
        host.handle("session-end", "{}", ctx)
        assert ctx.sessions.ended == []



class TestHooksActuallyRun:
    """Installed, wired, and working are three different facts.

    An entire session ran with every hook crashing on import and enforcement
    off for every call, while `doctor` reported "hook shims installed 5/5" and
    "hooks wired in settings" the whole time. Both were true. Neither was the
    question.

    This is the same mistake as the agent-loading one, in the same module: a
    check on the artifact of an installation is not a check on the
    installation. So these tests hand the checker known-broken hooks and
    require it to go red — a checker that has only ever been run against a
    healthy install is not evidence of anything.
    """

    def stub(self, host, event, body):
        shim = host.claude_dir / "hooks" / f"invariant-{event}.sh"
        shim.parent.mkdir(parents=True, exist_ok=True)
        shim.write_text(body, encoding="utf-8")
        shim.chmod(0o755)

    def run_check(self, host):
        _, ok, detail = host.hooks_run_check()
        return ok, detail

    def test_healthy_shims_pass(self, installed):
        """A healthy install — shims, CLI, manifest — must not read as broken.

        (When the CLI binary is absent the shims fall back to echoing {},
        which is also healthy; the fixture's manifest matters because a shim
        that finds the CLI but no manifest is a real misconfiguration and now
        reports itself degraded, AUD-04.)"""
        ok, detail = self.run_check(installed)
        assert ok, detail

    def test_a_crashing_hook_is_caught(self, installed):
        self.stub(installed, "pre-tool-use", "#!/usr/bin/env bash\nexit 1\n")
        ok, detail = self.run_check(installed)
        assert not ok
        assert "pre-tool-use" in detail

    def test_non_json_output_is_caught(self, installed):
        self.stub(
            installed, "post-tool-use", "#!/usr/bin/env bash\necho 'not json'\n"
        )
        ok, detail = self.run_check(installed)
        assert not ok
        assert "not JSON" in detail

    def test_the_degraded_marker_is_caught(self, installed):
        """The case the old checks could never have seen.

        A crashed engine answers with exit 0 and valid JSON on purpose — it has
        to, or a broken library would deny every tool call in the project. So
        exit status and parseability both look healthy, and only the marker
        separates "the hook ran" from "the hook worked".
        """
        payload = json.dumps(
            {
                "hookSpecificOutput": {"hookEventName": "PreToolUse"},
                claude_code.DEGRADED_KEY: "NameError: name 'Resolution' is not defined",
            }
        )
        self.stub(
            installed,
            "pre-tool-use",
            f"#!/usr/bin/env bash\ncat <<'EOF'\n{payload}\nEOF\n",
        )
        ok, detail = self.run_check(installed)
        assert not ok
        assert "NameError" in detail

    def test_exit_zero_and_valid_json_alone_would_have_passed_it(self, installed):
        """Why the marker had to exist, stated as a test."""
        payload = json.dumps(
            {
                "hookSpecificOutput": {"hookEventName": "PreToolUse"},
                claude_code.DEGRADED_KEY: "ImportError: boom",
            }
        )
        self.stub(
            installed,
            "pre-tool-use",
            f"#!/usr/bin/env bash\ncat <<'EOF'\n{payload}\nEOF\n",
        )
        shim = installed.claude_dir / "hooks" / "invariant-pre-tool-use.sh"
        import subprocess

        proc = subprocess.run(
            ["bash", str(shim)], input="{}", capture_output=True, text=True
        )
        assert proc.returncode == 0            # looks healthy
        assert json.loads(proc.stdout)         # looks healthy
        assert not self.run_check(installed)[0]  # is not healthy

    def test_a_missing_shim_is_caught(self, installed):
        (installed.claude_dir / "hooks" / "invariant-session-end.sh").unlink()
        ok, detail = self.run_check(installed)
        assert not ok
        assert "session-end" in detail

    def test_the_failure_says_enforcement_is_off(self, installed):
        self.stub(installed, "pre-tool-use", "#!/usr/bin/env bash\nexit 3\n")
        assert "enforcement is OFF" in self.run_check(installed)[1]

    def test_it_is_reported_as_its_own_check(self, installed):
        labels = [name for name, _, _ in installed.verify()]
        assert CHECK_HOOKS_RUN in labels
        assert CHECK_HOOKS in labels  # the old one stays; they answer differently

    def test_installed_and_running_are_reported_apart(self, installed):
        """A shim can be present and still not work; the report must say which."""
        self.stub(installed, "pre-tool-use", "#!/usr/bin/env bash\nexit 1\n")
        checks = {name: ok for name, ok, _ in installed.verify()}
        assert checks[CHECK_HOOKS] is True
        assert checks[CHECK_HOOKS_RUN] is False

    def test_the_probe_payloads_cover_every_event(self, installed):
        assert set(claude_code._PROBE_PAYLOADS) == set(claude_code.EVENTS)


class TestStopDeliversTheCloseoutDemand:
    """`SessionEnd` carries the right name and is the wrong hook.

    A closeout demand belongs where work stops. `SessionEnd` is where the name
    says it goes — and it is the one hook whose output the host discards, so a
    demand made there is made to nobody. That is why every session that ended
    without merging did so in silence.

    `Stop` is the hook that can still be answered: blocking returns the turn to
    the agent with the reason attached instead of ending it.
    """

    class Ctx:
        def __init__(self, verdicts):
            self._verdicts = verdicts
            self.seen_payload = None
            self.host_session_id = "host-1111"

        def rule_context(self, payload=None):
            self.seen_payload = payload
            # A real RuleContext, because `_stop` now reads `.session` off it
            # to look for a granted one-shot override (AUTH-01). `None` here
            # is the honest value for a doubled context that owns no session,
            # and it is what makes these cases about DELIVERY alone — the
            # override path is exercised end to end in `test_authority.py`.
            return SimpleNamespace(session=None, payload=payload or {})

    def evaluate_to(self, monkeypatch, verdicts):
        monkeypatch.setattr(claude_code, "evaluate_event", lambda event, ctx: verdicts)

    class Fake:
        def __init__(self, blocks=False, warns=False, text="reason"):
            self.blocks = blocks
            self.warns = warns
            self._text = text

        def render(self):
            return self._text

    def test_stop_is_a_wired_event(self, installed):
        """It has to be installed and wired, or it is prose again."""
        assert "stop" in claude_code.EVENTS
        assert (installed.claude_dir / "hooks" / "invariant-stop.sh").is_file()
        settings = json.loads(
            (installed.claude_dir / "settings.json").read_text(encoding="utf-8")
        )
        wired = json.dumps(settings["hooks"]["Stop"])
        assert "invariant-stop.sh" in wired

    def test_a_blocking_verdict_returns_the_turn_to_the_agent(
        self, host, monkeypatch
    ):
        self.evaluate_to(monkeypatch, [self.Fake(blocks=True, text="4 commits stranded")])
        result = host.handle("stop", "{}", self.Ctx([]))
        assert result["decision"] == "block"
        assert "4 commits stranded" in result["reason"]

    def test_a_warning_does_not_hold_the_turn(self, host, monkeypatch):
        """Advisory means advisory: surfaced, and the turn still ends."""
        self.evaluate_to(monkeypatch, [self.Fake(warns=True, text="worktree left behind")])
        result = host.handle("stop", "{}", self.Ctx([]))
        assert "decision" not in result
        assert "worktree left behind" in result["systemMessage"]

    def test_a_clean_stop_says_nothing(self, host, monkeypatch):
        self.evaluate_to(monkeypatch, [self.Fake()])
        assert host.handle("stop", "{}", self.Ctx([])) == {}

    def test_the_payload_reaches_the_rules(self, host, monkeypatch):
        """`stop_hook_active` lives in the payload and is the loop guard.

        A handler that dropped it would make the demand unsatisfiable, and an
        unsatisfiable gate gets disabled rather than obeyed.
        """
        self.evaluate_to(monkeypatch, [])
        ctx = self.Ctx([])
        host.handle("stop", json.dumps({"stop_hook_active": True}), ctx)
        assert ctx.seen_payload == {"stop_hook_active": True}

    def test_the_liveness_probe_cannot_block_the_caller(self):
        """A check that could halt a real turn is worse than the outage it hunts."""
        assert claude_code._PROBE_PAYLOADS["stop"]["stop_hook_active"] is True
