"""Managed host assets — agents, commands, skills.

Agents were installed, hashed and drift-checked. Commands and skills were not
managed at all, and nothing checked that any of the three could be LOADED. That
asymmetry was not a decision: agents got the attention because an agent failing
to load is what someone noticed, and four of them sat unloadable for a day while
`doctor` reported them current.

Commands and skills have the same shape and the same failure mode. They simply
had not failed yet.

Each broken case here is handed to the checker as a KNOWN positive, because a
checker that has only run against healthy files is not evidence of anything.
"""

from __future__ import annotations

import pytest

from invariant.hosts import assets
from invariant.hosts.claude_code import ClaudeCodeHost


@pytest.fixture
def host(tmp_path):
    return ClaudeCodeHost(tmp_path)


def write(host, kind_name, identity, text):
    kind = assets.BY_NAME[kind_name]
    base = host.claude_dir / kind.directory
    path = (
        base / identity / kind.nested_file if kind.nested_file else base / f"{identity}.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def shipped(name):
    """The agent definition as it is SHIPPED, not as it is installed.

    The installed copy carries a digest marker and is regenerated from this
    one, so a contract test that read it would be testing the install rather
    than the instruction. Module-level because two classes ask the same
    question of the same files.
    """
    from invariant.hosts.claude_code import DATA_DIR

    return (DATA_DIR / "agents" / f"{name}.md").read_text(encoding="utf-8")


def check(rows, label_fragment):
    for name, ok, detail in rows:
        if label_fragment in name:
            return ok, detail
    raise AssertionError(f"no check matching {label_fragment!r} in {[r[0] for r in rows]}")


GOOD = {
    "agent": "---\nname: {id}\ndescription: Does a thing. Use when: X\n---\nBody.\n",
    "command": "---\ndescription: Does a thing. Use when: X\n---\nBody.\n",
    "skill": "---\nname: {id}\ndescription: Does a thing. Use when: X\n---\nBody.\n",
}


class TestEveryKindIsChecked:
    """The point of the change: three kinds, one code path."""

    @pytest.mark.parametrize("kind", [k.name for k in assets.KINDS])
    def test_a_healthy_asset_is_reported_loadable(self, host, kind):
        # The shipped assets are installed first: without them the library's
        # own agents are ABSENT, which is correctly unloadable and would make
        # this assert for the wrong reason.
        host.install_assets()
        write(host, kind, "thing", GOOD[kind].format(id="thing"))
        assert check(host.asset_checks(), f"{kind}s loadable")[0]

    @pytest.mark.parametrize("kind", [k.name for k in assets.KINDS])
    def test_frontmatter_off_line_one_is_caught(self, host, kind):
        """The failure that hid for a day, now checked for all three."""
        host.install_assets()
        write(host, kind, "thing", "<!-- marker -->\n" + GOOD[kind].format(id="thing"))
        ok, detail = check(host.asset_checks(), f"{kind}s loadable")
        assert not ok
        assert "no frontmatter on line 1" in detail

    @pytest.mark.parametrize("kind", [k.name for k in assets.KINDS])
    def test_a_missing_description_is_caught(self, host, kind):
        host.install_assets()
        write(host, kind, "thing", "---\nname: thing\n---\nBody.\n")
        ok, detail = check(host.asset_checks(), f"{kind}s loadable")
        assert not ok
        assert "no description" in detail

    def test_the_repair_command_is_named(self, host):
        host.install_assets()
        write(host, "command", "thing", "no frontmatter at all\n")
        assert "invariant assets install" in check(host.asset_checks(), "commands loadable")[1]


class TestKindsDifferWhereTheyActuallyDiffer:
    def test_a_command_needs_no_name_key(self, host):
        """A command is addressed by FILENAME and carries no `name`.

        Requiring one would condemn every correct command in existence — the
        mirror of the YAML-parser mistake, where a checker stricter than the
        system it models produces false alarms just as reliably.
        """
        write(host, "command", "backlog", "---\ndescription: d\n---\nBody.\n")
        assert check(host.asset_checks(), "commands loadable")[0]

    def test_an_agent_name_must_match_its_filename(self, host):
        write(host, "agent", "alpha", "---\nname: beta\ndescription: d\n---\nB.\n")
        ok, detail = check(host.asset_checks(), "agents loadable")
        assert not ok and "installed as alpha" in detail

    def test_a_skill_is_found_inside_its_directory(self, host):
        write(host, "skill", "feature", GOOD["skill"].format(id="feature"))
        ok, detail = check(host.asset_checks(), "skills loadable")
        assert ok and "1 skill(s)" in detail

    def test_a_skill_directory_without_its_file_is_not_an_asset(self, host):
        host.install_assets()
        (host.claude_dir / "skills" / "empty").mkdir(parents=True)
        assert check(host.asset_checks(), "skills loadable")[0]

    def test_an_absent_shipped_asset_is_unloadable(self, host):
        """Absence is the strongest form of "will not load".

        Currency reports it too, but a reader seeing "loadable: ok" beside a
        missing agent has been told the wrong thing by the check whose whole
        job is that question.
        """
        host.install_assets()
        next((host.claude_dir / "agents").glob("*.md")).unlink()
        ok, detail = check(host.asset_checks(), "agents loadable")
        assert not ok and "(absent)" in detail


class TestOwnershipIsDistinguished:
    """A project's own file is not drift and must never be reported as such —
    but it is still checked for loadability, because a local file that silently
    fails to load is the same outage as a shipped one."""

    def test_a_project_file_is_not_managed(self, host):
        write(host, "command", "soakwatch", GOOD["command"])
        text = (host.claude_dir / "commands" / "soakwatch.md").read_text()
        assert not assets.is_managed(text)

    def test_an_installed_file_is_managed(self, host):
        host.install_assets()
        agent = next((host.claude_dir / "agents").glob("*.md"))
        assert assets.is_managed(agent.read_text(encoding="utf-8"))

    def test_a_project_file_is_still_checked_for_loading(self, host):
        host.install_assets()
        write(host, "command", "local-thing", "<!-- oops -->\n---\ndescription: d\n---\n")
        assert not check(host.asset_checks(), "commands loadable")[0]

    def test_currency_is_not_reported_for_a_kind_the_library_ships_none_of(self, host):
        """Reporting "0 stale" would imply an opinion about files never written."""
        write(host, "command", "local-thing", GOOD["command"])
        labels = [name for name, _, _ in host.asset_checks()]
        assert "library commands current" not in labels


class TestInstallRoundTrip:
    def test_installed_agents_open_with_frontmatter(self, host):
        host.install_assets()
        for path in (host.claude_dir / "agents").glob("*.md"):
            assert path.read_text(encoding="utf-8").startswith("---\n")

    def test_installing_one_kind_leaves_the_others_alone(self, host):
        host.install_assets(only="command")
        assert not (host.claude_dir / "agents").exists()

    def test_reinstall_repairs_a_broken_library_asset(self, host):
        host.install_assets()
        target = next((host.claude_dir / "agents").glob("*.md"))
        target.write_text("junk\n", encoding="utf-8")
        host.install_assets()
        assert check(host.asset_checks(), "agents loadable")[0]

    def test_a_local_edit_is_reported_as_drift_not_breakage(self, host):
        host.install_assets()
        target = next((host.claude_dir / "agents").glob("*.md"))
        target.write_text(target.read_text(encoding="utf-8") + "\nlocal\n", encoding="utf-8")
        rows = host.asset_checks()
        assert not check(rows, "library agents current")[0]
        assert check(rows, "agents loadable")[0]


class TestFrontmatterHelper:
    def test_a_colon_in_a_value_is_not_an_error(self):
        """Strict YAML rejects these; every shipped description contains one."""
        assert assets.frontmatter("---\nname: a\ndescription: Use when: X\n---\nb\n") == {
            "name": "a",
            "description": "Use when: X",
        }

    def test_the_marker_is_not_a_key(self):
        front = assets.frontmatter("---\n# invariant-managed: abc\nname: a\n---\nb\n")
        assert front == {"name": "a"}

    def test_a_marker_round_trips_out_again(self):
        source = "---\nname: a\ndescription: d\n---\nBody.\n"
        rendered = assets.render_managed(source, "deadbeef")
        assert assets.marker_digest(rendered) == "deadbeef"
        assert assets.reconstruct_source(rendered) == source

    def test_rendering_refuses_a_source_without_frontmatter(self):
        with pytest.raises(ValueError):
            assets.render_managed("just prose\n", "abc")


class TestTheGateReceiptCannotHashAnEmptyDiff:
    """BUG_033 — the shipped receipt block ran

        git diff --no-color HEAD -- $SESSION_FILES | shasum -a 256

    and zsh does not word-split an unquoted parameter. A multi-file set
    arrived at git as ONE bogus pathspec, the diff came back empty, and
    `diff_sha` became the SHA-256 of the empty string whatever had changed —
    silently, because `git diff` exits 0 on a pathspec matching nothing.

    Both sides of the comparison computed it the same broken way, so
    `WANT_DIFF == GOT_DIFF` held always and compliance-reviewer's fast path
    took a tree that had moved, every time. The receipt's stated purpose is
    to detect exactly that case.

    This is markdown nothing here executes, which is why it needed a test at
    all: a shipped instruction no test runs is an instrument nobody
    self-tested.
    """

    #: `printf '' | shasum -a 256`. Recognising this constant is the only way
    #: four separate agents caught the defect.
    EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    RECEIPT_AGENTS = ("feature-architect", "compliance-reviewer")

    def shell(self, name):
        """Only the fenced shell blocks — the prose NAMES the broken form as
        the thing not to write, and a check that cannot tell an example from
        an instruction would forbid documenting the defect."""
        text = shipped(name)
        blocks, inside = [], False
        for line in text.splitlines():
            if line.startswith("```"):
                inside = line.startswith(("```bash", "```sh"))
                continue
            if inside:
                blocks.append(line)
        assert blocks, f"{name} ships no shell block"
        return "\n".join(blocks)

    @pytest.mark.parametrize("name", RECEIPT_AGENTS)
    def test_the_file_set_is_never_expanded_unquoted(self, name):
        residue = self.shell(name).replace('"${SESSION_FILES[@]}"', "").replace(
            "${#SESSION_FILES[@]}", ""
        )
        assert "SESSION_FILES" not in residue.replace("SESSION_FILES=(", "")

    @pytest.mark.parametrize("name", RECEIPT_AGENTS)
    def test_both_sides_build_the_pathspec_the_same_way(self, name):
        """The reviewer carried a `<SESSION FILE SET>` placeholder instead of
        a construction, which is how it reached the same empty hash. A fix
        that changes only the architect leaves the other free to reintroduce
        it."""
        text = shipped(name)
        assert "SESSION_FILES=(" in text
        assert '"${SESSION_FILES[@]}"' in text

    @pytest.mark.parametrize("name", RECEIPT_AGENTS)
    def test_the_empty_string_hash_is_named_as_a_refusal(self, name):
        """Naming the constant costs one line and converts a silent match
        into a caught one — which matters because the next way to produce an
        empty diff will not be a quoting bug."""
        assert self.EMPTY_SHA in shipped(name)


class TestTheReviewerAsksHowTheSessionLands:
    """Increment 6 — the reviewer reads its mode off the engine, not off prose.

    Five increments of a seven-increment plan were committed by a
    `compliance-reviewer` run that had to be told, in the invocation prompt,
    not to merge and not to tear down: its Definition of Done was
    commit -> merge -> teardown, so the first "ship this" would have ended a
    session that was deliberately mid-plan. Correct behaviour rested entirely
    on a human remembering to paste the same paragraph every time, which is
    prose-as-control-flow in the repository that exists to replace it.

    `session plan show --json` answers the question instead. These are checks
    over shipped markdown that nothing executes — the same reason
    :class:`TestTheGateReceiptCannotHashAnEmptyDiff` exists: a shipped
    instruction no test runs is an instrument nobody self-tested.
    """

    REVIEWER = "compliance-reviewer"

    def rows(self, marker_cells):
        """Markdown table rows, as `(cells...)` tuples with markup stripped.

        The tables carry the decision — which mode a plan state means, and
        which phases that mode skips — so the test reads the table rather than
        a sentence near it. Only rows whose FIRST cell matches one of
        ``marker_cells`` are returned, which is what keeps a header row and the
        alignment row out without a positional assumption about either.
        """
        out = []
        for line in shipped(self.REVIEWER).splitlines():
            if not line.startswith("|"):
                continue
            cells = [c.strip().strip("`*_ ") for c in line.strip().strip("|").split("|")]
            if cells and any(m in cells[0] for m in marker_cells):
                out.append(tuple(cells))
        return out

    def modes(self):
        return {(r[0], r[1]): r[2] for r in self.rows(("true", "false"))}

    def test_the_mode_is_read_from_the_engine(self):
        """The command, spelled exactly, because that is the whole mechanism."""
        assert "invariant session plan show --json" in shipped(self.REVIEWER)

    def test_an_unplanned_session_still_lands_final(self):
        """The load-bearing row.

        `planned` and `complete` are separate keys precisely so this case is
        expressible: no checklist was declared, so there is no outstanding
        promise to wait for. Without the row, adopting a plan becomes
        mandatory and every project that never keeps one defers its merge
        forever.
        """
        assert self.modes()[("false", "false")] == "FINAL"

    def test_every_plan_state_names_its_mode(self):
        modes = self.modes()
        assert modes[("true", "false")] == "INCREMENT"
        assert modes[("true", "true")] == "FINAL"

    def test_an_engine_that_cannot_answer_does_not_defer_the_merge(self):
        text = shipped(self.REVIEWER)
        assert "treat it as **FINAL**" in text or "treat it as FINAL" in text

    def test_the_phases_an_increment_skips_are_named(self):
        """The behaviour, not the vocabulary: which steps stop running."""
        phases = {r[0]: r[1] for r in self.rows(("8b step 4", "8c step 1", "8c step 4", "8d"))}
        assert all(cell == "SKIP" for cell in phases.values()), phases
        assert len(phases) == 4

    def test_the_memory_sync_runs_in_both_modes(self):
        """Memory and host config commit straight to the base branch and are
        exempt from the worktree flow, so deferring them to the final run
        leaves the shared main checkout dirty across every increment."""
        sync = {r[0]: (r[1], r[2]) for r in self.rows(("8c steps 2-3", "8c steps 2–3"))}
        assert sync, "the memory/config sync row is not in the conditional table"
        assert all("SKIP" not in cell for cells in sync.values() for cell in cells), sync

    def test_the_deliverables_are_marked_through_the_engine(self):
        assert "invariant session plan done" in shipped(self.REVIEWER)

    def test_an_unnamed_deliverable_is_never_guessed(self):
        """Guessing which line of somebody's checklist a diff discharged is
        how a session merges before its work is finished."""
        assert "[WARN] no deliverable id named — plan not advanced" in shipped(self.REVIEWER)

    def test_both_increment_verdicts_are_present(self):
        text = shipped(self.REVIEWER)
        assert (
            "VERDICT: INCREMENT COMMITTED — 3/5 deliverables done, session stays open" in text
        )
        assert "VERDICT: INCREMENT COMMITTED — plan not advanced (no deliverable id named)" in text

    def test_a_success_verdict_never_reads_as_the_existing_failure_one(self):
        """`COMMITTED — NOT MERGED` already exists and already means work
        stranded by a refused merge. A success that reads like it gets
        escalated to the user every time."""
        for line in shipped(self.REVIEWER).splitlines():
            if "NOT MERGED" in line:
                assert "INCREMENT COMMITTED" not in line, line

    def test_the_stranded_work_verdict_still_exists(self):
        """The half that must NOT change: a refused merge is still a failure."""
        assert "VERDICT: COMMITTED — NOT MERGED" in shipped(self.REVIEWER)

    def test_teardown_is_still_the_definition_of_done_for_a_final_run(self):
        text = shipped(self.REVIEWER)
        assert "Definition of Done" in text
        assert "invariant session teardown" in text
        assert "VERDICT: COMMITTED & MERGED — session torn down" in text

    def test_an_unmerged_increment_is_named_as_the_complete_outcome(self):
        """The mirror of "an unpushed run is never a failure verdict"."""
        assert "An unmerged INCREMENT run is never a failure verdict" in shipped(self.REVIEWER)

    def test_the_report_has_somewhere_to_put_the_plan(self):
        """A claim with no slot in the template is a claim that gets
        improvised."""
        assert "\nPLAN\n" in shipped(self.REVIEWER)


class TestTheParentIsToldWhatTheModeCostsIt:
    """The frontmatter `description` is the only part of an agent definition
    the PARENT reads before invoking. A mode the subagent honours and the
    parent does not know about produces a correct run the parent then undoes.
    """

    def description(self, name):
        """Through the host's own line-based reader, which is also the check
        that the value stayed on ONE line: `agent_frontmatter` treats a
        following non-`key:` line as a continuation of the value."""
        front = assets.frontmatter(shipped(name))
        assert "description" in front, front
        return front["description"]

    def test_the_parent_is_told_which_ids_to_name(self):
        assert "DELIVERABLES DONE:" in self.description("compliance-reviewer")

    def test_the_parent_is_told_that_an_increment_is_a_success(self):
        desc = self.description("compliance-reviewer")
        assert "INCREMENT COMMITTED" in desc
        assert "SUCCESS" in desc

    def test_the_merge_is_no_longer_promised_unconditionally(self):
        assert "deliverables plan is complete" in self.description("compliance-reviewer")

    def test_the_architect_can_record_what_is_left_instead_of_widening_scope(self):
        assert "invariant session plan add" in shipped("feature-architect")


class TestTheAgentsRecordScopedEvidenceUnderACadence:
    """ADR-010 increment 3 remainder: under a sweep cadence the design and
    review agents record evidence with `--selector`, never a bare full run,
    because `sweep.no_full_run` reports one and the sweep holds the whole
    suite. Both must say how to tell a cadence is active, and both must name
    the one exception, a touched `held_paths` glob, which owes a full run."""

    @pytest.mark.parametrize("name", ["feature-architect", "compliance-reviewer"])
    def test_each_names_the_scoped_recorders(self, name):
        text = shipped(name)
        assert "invariant pipeline test --selector" in text
        assert "invariant pipeline coverage --selector" in text

    @pytest.mark.parametrize("name", ["feature-architect", "compliance-reviewer"])
    def test_each_says_how_a_cadence_is_recognised(self, name):
        assert "methodology.gates.schedule" in shipped(name)

    @pytest.mark.parametrize("name", ["feature-architect", "compliance-reviewer"])
    def test_each_names_the_held_path_exception_and_the_rule(self, name):
        text = shipped(name)
        assert "held_paths" in text
        assert "sweep.no_full_run" in text
