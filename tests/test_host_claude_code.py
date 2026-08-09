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

from pathlib import Path

import pytest
import yaml

from invariant.core.errors import InvariantError
from invariant.hosts import claude_code
from invariant.hosts.claude_code import (
    CHECK_AGENTS_CURRENT,
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
        assert "invariant agents install" in detail


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

    def record_docs_read(self, session_id, path):
        self.docs.append((session_id, path))

    def record_agent_run(self, session_id, agent_type):
        self.agents.append((session_id, agent_type))


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
    def __init__(self, host_session_id="host-1111"):
        self.pipelines = FakePipelines()
        self.sessions = FakeSessions()
        self.host_session_id = host_session_id

    def current_session(self, strict=True):
        return FakeSession()

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
            def record_agent_run(self, session_id, agent_type):
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

    def test_recording_is_skipped_without_a_session(self, host):
        class NoSession(FakeCtx):
            def current_session(self, strict=True):
                return None

        ctx = NoSession()
        host._post_tool_use(
            {"tool_name": "Task", "tool_input": {"subagent_type": "feature-architect"}}, ctx
        )
        assert ctx.pipelines.agents == []



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
