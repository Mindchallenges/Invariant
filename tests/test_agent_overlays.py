"""Overlays and the agents keys (ADR-012 D8, A6 increment 3).

A project amends a library agent with an overlay rather than an edited copy,
names it under ``agents.overlays``, and installs only what ``agents.library``
lists. The installed marker records the source's digest, the overlay's and
the body's own witness (the A6 ruling 1), so an install can say which input
moved. A bad overlay is refused before anything is written, with its fix.
"""

from __future__ import annotations

import json

import pytest
import yaml

from invariant.core.manifest import Manifest
from invariant.hosts import assets
from invariant.hosts.claude_code import DATA_DIR, ClaudeCodeHost

NAME = "feature-architect"
OVERLAY_REL = f"docs/agents/{NAME}.yaml"
LINE = "Our house rule: every change names its work item."


@pytest.fixture
def project(tmp_path, monkeypatch):
    from invariant.cli.main import main

    root = tmp_path / "proj"
    (root / "docs" / "architecture").mkdir(parents=True)
    monkeypatch.chdir(root)
    assert main(["init", "--name", "demo"]) == 0
    return root


def run(*argv):
    from invariant.cli.main import main

    return main(list(argv))


def configure(root, **sections):
    path = root / ".claude" / "invariant.yaml"
    body = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key, value in sections.items():
        if key == "agents":
            body.setdefault("agents", {}).update(value)
        else:
            body[key] = value
    path.write_text(yaml.safe_dump(body), encoding="utf-8")


def write_overlay(root, *operations, rel=OVERLAY_REL, name=NAME):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump({"version": 1, "operations": list(operations)})
    path.write_text(text, encoding="utf-8")
    configure(root, agents={"overlays": {name: rel}})
    return text


def append_line(text=LINE):
    return {"append": "phase-1", "text": text}


def target(root, name=NAME):
    return root / ".claude" / "agents" / f"{name}.md"


def host_for(root):
    return ClaudeCodeHost(root, manifest=Manifest.locate(root))


def agent_files(root):
    return {p.name: p.read_bytes() for p in sorted((root / ".claude" / "agents").glob("*"))}


def source_text(name=NAME):
    return (DATA_DIR / "agents" / f"{name}.md").read_text(encoding="utf-8")


class TestAnOverlayIsInstalled:
    def test_the_install_carries_the_overlay(self, project, capsys):
        write_overlay(project, append_line())
        assert run("agents", "install") == 0
        assert LINE in target(project).read_text(encoding="utf-8")

    def test_the_marker_records_the_three_digests(self, project, capsys):
        text = write_overlay(project, append_line())
        run("agents", "install")
        installed = target(project).read_text(encoding="utf-8")
        marker = assets.read_marker(installed)
        assert marker.source == assets.digest_of(source_text())
        assert marker.overlay == assets.digest_of(text)
        assert marker.body == assets.digest_of(assets.reconstruct_source(installed))

    def test_an_agent_with_no_overlay_records_none(self, project):
        installed = target(project, "bug-reproducer").read_text(encoding="utf-8")
        assert "overlay=none" in installed
        assert assets.read_marker(installed).overlay is None

    def test_an_installed_overlay_reads_as_current(self, project):
        write_overlay(project, append_line())
        run("agents", "install")
        states = {s.identity: s.state for s in host_for(project).plan_assets(only="agent")}
        assert set(states.values()) == {assets.CURRENT}

    def test_agents_show_prints_the_merged_render(self, project, capsys):
        write_overlay(project, {"insert_after": "phase-1", "id": "house", "text": LINE})
        capsys.readouterr()
        assert run("agents", "show", NAME) == 0
        assert LINE in capsys.readouterr().out
        assert run("agents", "show", NAME, "--sections") == 0
        ids = capsys.readouterr().out.split()
        assert ids[ids.index("phase-1") + 1] == "house"

    def test_the_description_append_reaches_the_frontmatter(self, project):
        write_overlay(project, {"description_append": "Here it also files work items."})
        run("agents", "install")
        front = assets.frontmatter(target(project).read_text(encoding="utf-8"))
        assert front["description"].endswith("Here it also files work items.")


class TestInstallSaysWhichInputChanged:
    def test_a_changed_overlay(self, project, capsys):
        write_overlay(project, append_line())
        run("agents", "install")
        write_overlay(project, append_line("A different rule."))
        capsys.readouterr()

        assert run("agents", "install", "--dry-run") == 0
        assert f"{NAME} (installed from an older version) — the overlay changed" in (
            capsys.readouterr().out
        )
        assert run("agents", "install") == 0
        assert f"updated {NAME} — the overlay changed" in capsys.readouterr().out
        assert "A different rule." in target(project).read_text(encoding="utf-8")

    def test_a_removed_overlay(self, project, capsys):
        write_overlay(project, append_line())
        run("agents", "install")
        configure(project, agents={"overlays": {}})
        capsys.readouterr()
        assert run("assets", "install") == 0
        assert f"agent:{NAME} — the overlay changed" in capsys.readouterr().out
        assert LINE not in target(project).read_text(encoding="utf-8")

    def stale_copy(self, project, source_digest):
        path = target(project)
        shipped = assets.reconstruct_source(path.read_text(encoding="utf-8"))
        older = shipped + "\nWording an earlier release shipped.\n"
        path.write_text(
            assets.render_managed(older, source_digest=source_digest, overlay_digest=None),
            encoding="utf-8",
        )

    def test_a_changed_library(self, project, capsys):
        self.stale_copy(project, "0" * 16)
        capsys.readouterr()
        assert run("agents", "install") == 0
        assert f"updated {NAME} — the library changed" in capsys.readouterr().out

    def test_a_changed_renderer(self, project, capsys):
        self.stale_copy(project, assets.digest_of(source_text()))
        capsys.readouterr()
        assert run("agents", "install") == 0
        assert f"updated {NAME} — the renderer changed" in capsys.readouterr().out

    def test_a_current_copy_is_not_said_to_have_changed(self, project, capsys):
        capsys.readouterr()
        assert run("agents", "install") == 0
        assert "updated" not in capsys.readouterr().out


class TestACopyFromBeforeA6_3:
    """An A6.1 or A6.2 copy has a one-digest marker, and still classifies."""

    def one_digest(self, project, text):
        front, body = assets.split_frontmatter(text)
        marker = f"{assets.MARKER_PREFIX} {assets.digest_of(text)}"
        target(project).write_text(
            "\n".join([assets.FENCE, marker, *front, assets.FENCE, body]), encoding="utf-8"
        )

    def shipped(self, project):
        return assets.reconstruct_source(target(project).read_text(encoding="utf-8"))

    def test_an_untouched_current_copy_is_current(self, project):
        self.one_digest(project, self.shipped(project))
        states = {s.identity: s.state for s in host_for(project).plan_assets(only="agent")}
        assert states[NAME] == assets.CURRENT

    def test_an_untouched_stale_copy_is_rewritten_saying_it_cannot_tell_which(
        self, project, capsys
    ):
        self.one_digest(project, self.shipped(project) + "\nOlder.\n")
        capsys.readouterr()
        assert run("agents", "install") == 0
        assert f"updated {NAME} — {assets.UNRECORDED}" in capsys.readouterr().out
        assert assets.read_marker(target(project).read_text(encoding="utf-8")).source

    def test_one_under_a_new_overlay_is_the_overlay(self, project, capsys):
        self.one_digest(project, self.shipped(project))
        write_overlay(project, append_line())
        capsys.readouterr()
        assert run("agents", "install") == 0
        assert f"updated {NAME} — the overlay changed" in capsys.readouterr().out

    def test_an_edited_one_is_kept(self, project, capsys):
        self.one_digest(project, self.shipped(project) + "\nOlder.\n")
        path = target(project)
        path.write_text(path.read_text(encoding="utf-8") + "mine\n", encoding="utf-8")
        capsys.readouterr()
        assert run("agents", "install") == 0
        assert path.read_text(encoding="utf-8").endswith("mine\n")


class TestAnEditedCopyIsTheOperators:
    def edit(self, project):
        path = target(project)
        path.write_text(path.read_text(encoding="utf-8") + "mine\n", encoding="utf-8")
        return path

    def test_install_skips_it_and_names_the_overlay_fix(self, project, capsys):
        path = self.edit(project)
        capsys.readouterr()
        assert run("agents", "install") == 0
        out = capsys.readouterr().out
        assert f"skipped {NAME} (edited locally)" in out
        assert "move the edit into an overlay" in out
        assert path.read_text(encoding="utf-8").endswith("mine\n")

    def test_assets_install_names_it_too(self, project, capsys):
        self.edit(project)
        capsys.readouterr()
        assert run("assets", "install") == 0
        assert "move the edit into an overlay" in capsys.readouterr().out

    @pytest.mark.parametrize("command", ["agents", "assets"])
    def test_a_dry_run_names_it_with_the_overlay_fix(self, project, capsys, command):
        before = self.edit(project).read_bytes()
        capsys.readouterr()
        assert run(command, "install", "--dry-run") == 0
        out = capsys.readouterr().out
        assert "SKIP" in out and "move the edit into an overlay" in out
        assert target(project).read_bytes() == before

    def test_doctors_row_names_the_overlay_fix(self, project):
        self.edit(project)
        rows = {label: detail for label, _, detail in host_for(project).asset_checks()}
        assert "move the edit into an overlay" in rows["library agents current"]

    def test_an_edit_under_an_overlay_is_still_kept(self, project, capsys):
        write_overlay(project, append_line())
        run("agents", "install")
        path = self.edit(project)
        capsys.readouterr()
        assert run("agents", "install") == 0
        assert path.read_text(encoding="utf-8").endswith("mine\n")


class TestTheLibraryKey:
    def test_only_the_listed_agents_are_installed(self, project, capsys):
        for path in (project / ".claude" / "agents").glob("*.md"):
            path.unlink()
        configure(project, agents={"library": [NAME]})
        assert run("agents", "install") == 0
        assert sorted(agent_files(project)) == [f"{NAME}.md"]

    def test_an_agent_left_out_is_not_reported_absent(self, project):
        target(project, "bug-reproducer").unlink()
        configure(project, agents={"library": [NAME, "compliance-reviewer", "backlog-curator"]})
        rows = {label: (ok, d) for label, ok, d in host_for(project).asset_checks()}
        assert rows["agents loadable by host"][0], rows["agents loadable by host"]
        assert rows["library agents current"][0], rows["library agents current"]

    def test_a_listed_agent_the_library_does_not_ship_is_refused(self, project, capsys):
        configure(project, agents={"library": [NAME, "nobody"]})
        before = agent_files(project)
        capsys.readouterr()
        assert run("agents", "install") == 1
        err = capsys.readouterr().err
        assert "'nobody'" in err and "bug-reproducer" in err and "agents.local" in err
        assert agent_files(project) == before

    def test_a_project_local_agent_is_never_touched(self, project, capsys):
        mine = "---\nname: bug-reproducer\ndescription: ours\n---\nOurs.\n"
        target(project, "bug-reproducer").write_text(mine, encoding="utf-8")
        configure(
            project,
            agents={
                "library": [NAME, "compliance-reviewer", "backlog-curator"],
                "local": ["bug-reproducer"],
            },
        )
        for argv in (["agents", "install"], ["assets", "install"]):
            assert run(*argv) == 0
            assert target(project, "bug-reproducer").read_text(encoding="utf-8") == mine
        plan = host_for(project).plan_uninstall(remove_assets=True)
        assert target(project, "bug-reproducer") not in plan["assets"]

    def test_no_manifest_means_every_shipped_agent(self, tmp_path):
        host = ClaudeCodeHost(tmp_path)
        shipped = sorted(p.stem for p in (DATA_DIR / "agents").glob("*.md"))
        assert sorted(s.identity for s in host.plan_assets(only="agent")) == shipped


class TestABadOverlayIsRefusedWritingNothing:
    def refused(self, project, capsys):
        before = agent_files(project)
        capsys.readouterr()
        assert run("agents", "install") == 1
        err = capsys.readouterr().err
        assert agent_files(project) == before
        assert run("assets", "install") == 1
        assert agent_files(project) == before
        return err

    def test_an_unknown_operation(self, project, capsys):
        write_overlay(project, {"prepend": "phase-1", "text": "x"})
        err = self.refused(project, capsys)
        assert "'prepend'" in err and "fix:" in err

    def test_a_missing_anchor_names_the_nearest(self, project, capsys):
        write_overlay(project, {"append": "phase_1", "text": "x"})
        err = self.refused(project, capsys)
        assert "'phase-1'" in err

    def test_a_colliding_inserted_id(self, project, capsys):
        write_overlay(project, {"insert_after": "phase-1", "id": "phase-2", "text": "x"})
        assert "'phase-2'" in self.refused(project, capsys)

    def test_a_path_outside_the_project(self, project, capsys):
        configure(project, agents={"overlays": {NAME: "../elsewhere.yaml"}})
        err = self.refused(project, capsys)
        assert "outside the project" in err and "docs/agents/" in err

    def test_a_path_exempt_from_isolation(self, project, capsys):
        write_overlay(project, append_line(), rel=".claude/overlays/fa.yaml")
        err = self.refused(project, capsys)
        assert "exempt from session isolation" in err and "D10" in err

    def test_a_missing_file(self, project, capsys):
        configure(project, agents={"overlays": {NAME: OVERLAY_REL}})
        err = self.refused(project, capsys)
        assert OVERLAY_REL in err and "no such file" in err

    def test_a_privacy_finding_in_the_merged_text(self, project, capsys):
        configure(project, privacy={"extra": ["SECRET-[0-9]+"]})
        write_overlay(project, append_line("Ask for SECRET-42 first."))
        err = self.refused(project, capsys)
        assert "privacy" in err and "SECRET-42" not in err
        assert OVERLAY_REL in err

    def test_only_what_the_overlay_brings_counts(self, project, capsys):
        """A finding already in the shipped render is the library's, not the
        overlay's, so it does not refuse the overlay."""
        configure(project, privacy={"extra": ["invariant pipeline"]})
        write_overlay(project, append_line())
        capsys.readouterr()
        assert run("agents", "install") == 0
        assert LINE in target(project).read_text(encoding="utf-8")

    def test_doctor_reports_it_rather_than_crashing(self, project, capsys):
        write_overlay(project, {"append": "phase_1", "text": "x"})
        capsys.readouterr()
        run("doctor", "--json")
        rows = {c["check"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
        row = rows["library agents current"]
        assert row["ok"] is False and "'phase-1'" in row["detail"]
