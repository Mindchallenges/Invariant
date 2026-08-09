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
