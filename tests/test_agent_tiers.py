"""A tier reaches the host as a hint (ADR-012 D9, IDEA_031 A7 increment 2).

The host resolves an agent to its effective tier (`agents.tiers`, then the
source's) and that tier to `host.tier_map`'s value, and `render_agent` writes
the value as one line after `tools:`. Everything a concrete value appears as
here is a placeholder (the A7 ruling 4): the engine never reads what it says.

A copy whose text differs from today's render only in that line is a state of
its own, "tier hint moved" (the A7 ruling 6). No gate reads it as drift, an
install still applies it, and a `doctor` row outside any gate names it.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

from invariant.core.agents import parse
from invariant.core.manifest import Manifest
from invariant.core.rules import RuleContext, evaluate
from invariant.hosts import assets, claude_code
from invariant.hosts import claude_code as cc
from invariant.hosts.claude_code import ClaudeCodeHost, render_agent

ONE = "placeholder-one"
TWO = "placeholder-two"
DEEP = "feature-architect"
STANDARD = "compliance-reviewer"
RULE = "agents.rendered_current"
LIBRARY = claude_code.DATA_DIR / "agents"
DATA = claude_code.DATA_DIR
HOSTILE = [
    "x: y",
    "a #b",
    '"quoted"',
    "it's",
    "[list]",
    "{map: 1}",
    "&anchor",
    "*alias",
    "!tag",
    "%directive",
    "@at",
    "`tick",
    "true",
    "null",
    "123",
    "1.5",
    "~",
    "---",
    "...",
    "- dash",
    "? q",
    ": c",
    "back\\slash",
    " padded ",
    "tab\there",
    "\x7fdel",
    "日本",
    "\U0001f600",
    "|",
    ">",
    "#",
]


def agent(front=""):
    return parse(f"---\nname: a\ndescription: d\ncapabilities: [shell]\n{front}---\nBody.\n")


class TestTheModelLine:
    def test_no_model_writes_no_line(self):
        assert render_agent(agent()) == "---\nname: a\ndescription: d\ntools: Bash\n---\nBody.\n"

    def test_it_follows_tools_and_is_the_only_line_changed(self):
        text = render_agent(agent(), model=ONE)
        assert text == f"---\nname: a\ndescription: d\ntools: Bash\nmodel: {ONE}\n---\nBody.\n"

    def test_a_plain_value_is_written_as_itself(self):
        """The host reads this frontmatter line by line, so a value that needs
        no quoting does not get any."""
        assert assets.frontmatter(render_agent(agent(), model=ONE))["model"] == ONE

    @pytest.mark.parametrize("value", HOSTILE)
    def test_the_frontmatter_stays_valid_for_any_one_line_value(self, value):
        text = render_agent(agent(), model=value)
        front, body = assets.split_frontmatter(text)
        assert len(front) == 4 and front[3].startswith("model: ")
        assert body == "Body.\n"
        assert yaml.safe_load("\n".join(front)) == {
            "name": "a",
            "description": "d",
            "tools": "Bash",
            "model": value,
        }
        assert assets.frontmatter(text)["name"] == "a"

    def test_a_tier_alone_writes_nothing(self):
        assert "model" not in render_agent(agent("tier: deep\n"))


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
        body.setdefault(key, {}).update(value)
    path.write_text(yaml.safe_dump(body), encoding="utf-8")


def tier_map(root, **mapping):
    configure(root, host={"tier_map": mapping})


def plan(root):
    host = ClaudeCodeHost(root, manifest=Manifest.load(root))
    return {step.identity: step for step in host.plan_assets(only="agent")}


def target(root, name=DEEP):
    return root / ".claude" / "agents" / f"{name}.md"


def model_of(text):
    return assets.frontmatter(text).get("model")


class TestTheHostResolves:
    def test_the_source_tier_goes_through_the_map(self, project):
        tier_map(project, deep=ONE, standard=TWO)
        steps = plan(project)
        assert model_of(steps[DEEP].text) == ONE
        assert model_of(steps["bug-reproducer"].text) == ONE
        assert model_of(steps[STANDARD].text) == TWO
        assert model_of(steps["backlog-curator"].text) == TWO

    def test_the_operators_tier_overrides_the_sources(self, project):
        tier_map(project, deep=ONE, standard=TWO)
        configure(project, agents={"tiers": {DEEP: "standard"}})
        assert model_of(plan(project)[DEEP].text) == TWO

    def test_a_tier_with_no_map_entry_gives_no_line(self, project):
        tier_map(project, deep=ONE)
        configure(project, agents={"tiers": {DEEP: "light"}})
        steps = plan(project)
        assert model_of(steps[DEEP].text) is None
        assert model_of(steps["bug-reproducer"].text) == ONE
        assert model_of(steps[STANDARD].text) is None

    def test_no_tier_at_all_gives_no_line(self, project, tmp_path, monkeypatch):
        data = tmp_path / "library"
        shutil.copytree(LIBRARY, data / "agents")
        path = data / "agents" / f"{DEEP}.md"
        lines = path.read_text(encoding="utf-8").split("\n")
        path.write_text(
            "\n".join(line for line in lines if not line.startswith("tier:")), encoding="utf-8"
        )
        monkeypatch.setattr(cc, "DATA_DIR", data)
        tier_map(project, light=ONE, standard=ONE, deep=ONE)
        assert model_of(plan(project)[DEEP].text) is None

    def test_an_empty_map_leaves_the_render_alone(self, project):
        for name, step in plan(project).items():
            source = parse((LIBRARY / f"{name}.md").read_text(encoding="utf-8"), name)
            assert step.text == render_agent(source)

    def test_no_manifest_means_no_map(self, tmp_path):
        host = ClaudeCodeHost(tmp_path)
        assert all(model_of(s.text) is None for s in host.plan_assets(only="agent"))

    def test_an_install_writes_the_line_under_its_witness(self, project):
        tier_map(project, deep="x: y")
        assert run("agents", "install") == 0
        installed = target(project).read_text(encoding="utf-8")
        assert assets.marker_digest(installed) == assets.digest_of(
            assets.reconstruct_source(installed)
        )
        front = "\n".join(
            line
            for line in assets.split_frontmatter(installed)[0]
            if line.startswith("model:")
        )
        assert yaml.safe_load(front) == {"model": "x: y"}

    def test_an_overlay_keeps_the_hint(self, project):
        rel = f"docs/agents/{DEEP}.yaml"
        (project / rel).parent.mkdir(parents=True)
        (project / rel).write_text(
            "version: 1\noperations:\n  - description_append: Also this.\n", encoding="utf-8"
        )
        configure(project, agents={"overlays": {DEEP: rel}})
        tier_map(project, deep=ONE)
        assert model_of(plan(project)[DEEP].text) == ONE


class TestTheSources:
    @pytest.mark.parametrize(
        ("name", "tier"),
        [
            ("feature-architect", "deep"),
            ("bug-reproducer", "deep"),
            ("compliance-reviewer", "standard"),
            ("backlog-curator", "standard"),
        ],
    )
    def test_each_shipped_agent_states_its_default(self, name, tier):
        assert parse((LIBRARY / f"{name}.md").read_text(encoding="utf-8"), name).tier == tier

    @pytest.mark.parametrize("name", sorted(p.stem for p in LIBRARY.glob("*.md")))
    def test_the_model_prose_is_gone(self, name):
        source = parse((LIBRARY / f"{name}.md").read_text(encoding="utf-8"), name)
        assert "MODEL:" not in source.description


#: What "names a model" means to the lint. The families a shipped file could
#: plausibly carry; a test's word list, never the engine's.
MODEL_NAMES = re.compile(
    r"\b(?:sonnet|opus|haiku|gemini|llama|mistral|gpt-?\d[\w.-]*|claude-\d[\w.-]*)\b",
    re.IGNORECASE,
)


def names_a_model(text):
    return MODEL_NAMES.findall(text)


class TestNoShippedFileNamesAModel:
    @pytest.mark.parametrize(
        "line",
        [
            "model: sonnet",
            "run this on Opus",
            "use gpt-5 here",
            "or claude-3.5",
            "a Haiku pass",
        ],
    )
    def test_the_lint_goes_red_on_a_name(self, line):
        assert names_a_model(line)

    @pytest.mark.parametrize(
        "line",
        ["run on the session model", "tier: deep", "host.tier_map", ".claude/agents", "claude_code"],
    )
    def test_the_lint_passes_the_word_model(self, line):
        assert not names_a_model(line)

    @pytest.mark.parametrize(
        "path",
        sorted(
            path
            for folder in ("agents", "scaffold", "profiles")
            for path in (DATA / folder).rglob("*")
            if path.is_file()
        ),
        ids=lambda path: f"{path.parent.name}/{path.name}",
    )
    def test_no_shipped_agent_scaffold_or_profile_names_one(self, path: Path):
        assert names_a_model(path.read_text(encoding="utf-8")) == []

    def test_every_folder_the_lint_covers_ships_something(self):
        for folder in ("agents", "scaffold", "profiles"):
            assert any(p.is_file() for p in (DATA / folder).rglob("*")), folder


def managed(text):
    return assets.render_managed(text, source_digest="s" * 16, overlay_digest=None)


def one_digest(text):
    front, body = assets.split_frontmatter(text)
    marker = f"{assets.MARKER_PREFIX} {assets.digest_of(text)}"
    return "\n".join([assets.FENCE, marker, *front, assets.FENCE, body])


BASE = "---\nname: a\ndescription: d\ntools: Bash\n---\nBody.\n"
HINTED = f"---\nname: a\ndescription: d\ntools: Bash\nmodel: {ONE}\n---\nBody.\n"
MOVED = f"---\nname: a\ndescription: d\ntools: Bash\nmodel: {TWO}\n---\nBody.\n"


class TestTheClassifierState:
    @pytest.mark.parametrize(
        ("installed", "shipped"),
        [(BASE, HINTED), (HINTED, BASE), (HINTED, MOVED)],
        ids=["gained", "lost", "changed"],
    )
    @pytest.mark.parametrize("wrap", [managed, one_digest], ids=["three-digest", "one-digest"])
    def test_an_intact_copy_differing_only_in_the_line(self, installed, shipped, wrap):
        assert assets.classify(wrap(installed), shipped, tier_hints=True) == assets.TIER_MOVED

    def test_a_copy_that_matches_is_still_current(self):
        assert assets.classify(managed(HINTED), HINTED, tier_hints=True) == assets.CURRENT

    def test_anything_else_differing_is_older(self):
        shipped = MOVED.replace("Body.", "New body.")
        assert assets.classify(managed(HINTED), shipped, tier_hints=True) == assets.OLDER

    def test_a_hand_edited_model_line_breaks_the_witness(self):
        edited = managed(HINTED).replace(f"model: {ONE}", f"model: {TWO}")
        assert assets.classify(edited, BASE, tier_hints=True) == assets.LOCAL

    def test_a_hand_added_model_line_breaks_the_witness(self):
        edited = managed(BASE).replace("tools: Bash\n", f"tools: Bash\nmodel: {ONE}\n")
        assert assets.classify(edited, BASE, tier_hints=True) == assets.LOCAL

    def test_a_model_line_in_the_body_is_body(self):
        installed = managed(BASE.replace("Body.", f"model: {ONE}"))
        assert assets.classify(installed, BASE, tier_hints=True) == assets.OLDER

    def test_a_shipped_text_with_no_frontmatter_is_not_the_state(self):
        assert assets.classify(managed(HINTED), "no frontmatter\n", tier_hints=True) == assets.OLDER

    def test_only_a_kind_with_tier_hints_has_the_state(self):
        """A command or a skill carries no tier, so a change to a `model:` line
        in one is a change like any other."""
        assert assets.classify(managed(HINTED), MOVED) == assets.OLDER

    def test_the_input_named_is_the_hint(self, project):
        tier_map(project, deep=ONE)
        assert run("agents", "install") == 0
        tier_map(project, deep=TWO)
        step = plan(project)[DEEP]
        assert step.state == assets.TIER_MOVED
        assert step.why == assets.TIER_CHANGED == "the tier hint changed"
        assert step.write is True


def moved(root, first=ONE, second=TWO):
    tier_map(root, deep=first)
    assert run("agents", "install") == 0
    tier_map(root, deep=second)


def verdict_of(root):
    from invariant.cli.main import host_agent_plan

    manifest = Manifest.load(root)
    context = RuleContext(manifest=manifest, cwd=root, host_agent_plan=host_agent_plan(manifest))
    (result,) = evaluate([RULE], context)
    return result.verdict


def doctor_rows(capsys):
    capsys.readouterr()
    run("--json", "doctor")
    return {row["check"]: row for row in json.loads(capsys.readouterr().out)["checks"]}


class TestItIsNotDrift:
    def test_the_rule_reads_it_as_current(self, project):
        moved(project)
        verdict = verdict_of(project)
        assert verdict.ok and not verdict.skipped
        assert "4 library agent(s) current" in verdict.message

    def test_a_map_emptied_is_current_too(self, project):
        tier_map(project, deep=ONE)
        assert run("agents", "install") == 0
        path = project / ".claude" / "invariant.yaml"
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
        body["host"]["tier_map"] = {}
        path.write_text(yaml.safe_dump(body), encoding="utf-8")
        assert plan(project)[DEEP].state == assets.TIER_MOVED
        assert verdict_of(project).ok

    def test_an_override_moved_is_current_too(self, project):
        tier_map(project, deep=ONE, standard=TWO)
        assert run("agents", "install") == 0
        configure(project, agents={"tiers": {DEEP: "standard"}})
        assert plan(project)[DEEP].state == assets.TIER_MOVED
        assert verdict_of(project).ok

    def test_the_rule_never_learns_the_state(self):
        """The host says `current`; the state word stays on the host's side of
        the arrow, so no gate can read a tier hint."""
        rules = Path(claude_code.__file__).parents[1] / "core" / "rules.py"
        text = rules.read_text(encoding="utf-8")
        assert assets.TIER_MOVED not in text
        assert "TIER_MOVED" not in text

    def test_doctors_currency_row_reads_it_as_current(self, project, capsys):
        moved(project)
        row = doctor_rows(capsys)[cc.CHECK_AGENTS_CURRENT]
        assert row["ok"] is True and row["warn"] is False
        assert row["detail"] == verdict_of(project).message

    def test_a_separate_row_names_it(self, project, capsys):
        moved(project)
        row = doctor_rows(capsys)[cc.CHECK_AGENT_TIER_HINTS]
        assert row["ok"] is True and row["warn"] is True
        assert f"{DEEP} ({assets.TIER_MOVED})" in row["detail"]
        assert "bug-reproducer" in row["detail"]
        assert STANDARD not in row["detail"]
        assert "invariant agents install" in row["detail"]

    def test_with_nothing_moved_there_is_no_such_row(self, project, capsys):
        assert cc.CHECK_AGENT_TIER_HINTS not in doctor_rows(capsys)

    def test_the_row_alone_leaves_doctor_clean(self, project):
        moved(project)
        host = ClaudeCodeHost(project, manifest=Manifest.load(project))
        rows = host.asset_checks(only="agent")
        assert all(ok for _, ok, _ in rows)
        assert cc.CHECK_AGENT_TIER_HINTS in host.warned_rows

    def test_the_row_moves_no_exit_status(self, project, capsys):
        """`doctor` reads a false row as failure. A moved hint adds a row and no
        failure, so the exit it gives is the one it gave before the hint moved."""
        tier_map(project, deep=ONE)
        assert run("agents", "install") == 0
        capsys.readouterr()
        before = run("--json", "doctor")
        failing = {r["check"] for r in json.loads(capsys.readouterr().out)["checks"] if not r["ok"]}
        tier_map(project, deep=TWO)
        after = run("--json", "doctor")
        rows = json.loads(capsys.readouterr().out)["checks"]
        assert cc.CHECK_AGENT_TIER_HINTS in {r["check"] for r in rows}
        assert {r["check"] for r in rows if not r["ok"]} == failing
        assert after == before

    def test_a_refused_plan_has_no_such_row(self, project):
        moved(project)
        configure(project, agents={"overlays": {DEEP: "docs/agents/none.yaml"}})
        host = ClaudeCodeHost(project, manifest=Manifest.load(project))
        rows = {label: ok for label, ok, _ in host.asset_checks(only="agent")}
        assert rows[cc.CHECK_AGENTS_CURRENT] is False
        assert cc.CHECK_AGENT_TIER_HINTS not in rows

    def test_a_row_for_one_doctor_is_not_carried_into_the_next(self, project):
        moved(project)
        host = ClaudeCodeHost(project, manifest=Manifest.load(project))
        host.asset_checks(only="agent")
        host.install_assets(only="agent")
        rows = {label: ok for label, ok, _ in host.asset_checks(only="agent")}
        assert cc.CHECK_AGENT_TIER_HINTS not in rows
        assert cc.CHECK_AGENT_TIER_HINTS not in host.warned_rows


class TestAnInstallStillAppliesIt:
    def test_it_rewrites_and_says_why(self, project, capsys):
        moved(project)
        capsys.readouterr()
        assert run("agents", "install") == 0
        out = capsys.readouterr().out
        assert f"updated {DEEP} — the tier hint changed" in out
        assert model_of(target(project).read_text(encoding="utf-8")) == TWO
        assert plan(project)[DEEP].state == assets.CURRENT

    def test_the_dry_run_shows_the_write(self, project, capsys):
        moved(project)
        capsys.readouterr()
        assert run("agents", "install", "--dry-run") == 0
        out = capsys.readouterr().out
        assert f"{DEEP} ({assets.TIER_MOVED}) — the tier hint changed" in out
        assert model_of(target(project).read_text(encoding="utf-8")) == ONE

    def test_assets_install_says_the_same(self, project, capsys):
        moved(project)
        capsys.readouterr()
        assert run("assets", "install") == 0
        assert f"agent:{DEEP} — the tier hint changed" in capsys.readouterr().out

    def test_uninstall_removes_it_as_the_librarys_own(self, project):
        moved(project)
        host = ClaudeCodeHost(project, manifest=Manifest.load(project))
        planned = host.plan_uninstall(remove_assets=True)
        assert target(project) in planned["assets"]
        assert f"agent:{DEEP}" not in planned["protected"]


class TestAHandEditedModelLine:
    def edited(self, root):
        tier_map(root, deep=ONE)
        assert run("agents", "install") == 0
        path = target(root)
        path.write_text(
            path.read_text(encoding="utf-8").replace(f"model: {ONE}", f"model: {TWO}"),
            encoding="utf-8",
        )

    def test_it_reads_as_edited_and_is_kept(self, project):
        self.edited(project)
        assert plan(project)[DEEP].state == assets.LOCAL
        assert run("agents", "install") == 0
        assert model_of(target(project).read_text(encoding="utf-8")) == TWO

    def test_the_fix_names_the_route(self, project):
        self.edited(project)
        verdict = verdict_of(project)
        assert not verdict.ok
        (fix,) = verdict.fixes
        assert "agents.tiers" in fix and "host.tier_map" in fix
        assert "--overwrite-local" in fix

    def test_the_install_hint_names_the_route(self, project, capsys):
        self.edited(project)
        capsys.readouterr()
        assert run("agents", "install") == 0
        out = capsys.readouterr().out
        assert "agents.tiers" in out and "host.tier_map" in out


class TestTheCommands:
    def test_agents_show_prints_the_line(self, project, capsys):
        tier_map(project, deep=ONE)
        capsys.readouterr()
        assert run("agents", "show", DEEP) == 0
        out = capsys.readouterr().out
        assert out == plan(project)[DEEP].text
        assert f"\nmodel: {ONE}\n---\n" in out

    def test_agents_show_with_no_entry_prints_none(self, project, capsys):
        tier_map(project, deep=ONE)
        capsys.readouterr()
        assert run("agents", "show", STANDARD) == 0
        assert "\nmodel:" not in capsys.readouterr().out.split("\n---\n")[0]

    def list_rows(self, capsys):
        capsys.readouterr()
        assert run("agents", "list", "--json") == 0
        return {row["agent"]: row for row in json.loads(capsys.readouterr().out)["agents"]}

    def test_agents_list_carries_the_source_tier_and_no_value(self, project, capsys):
        row = self.list_rows(capsys)[DEEP]
        assert (row["tier"], row["tier_from"], row["mapped"]) == ("deep", "source", None)

    def test_agents_list_carries_the_override_and_the_value(self, project, capsys):
        tier_map(project, standard=TWO)
        configure(project, agents={"tiers": {DEEP: "standard"}})
        row = self.list_rows(capsys)[DEEP]
        assert (row["tier"], row["tier_from"], row["mapped"]) == (
            "standard",
            "agents.tiers",
            TWO,
        )

    def test_agents_list_with_no_tier(self, project, capsys, tmp_path, monkeypatch):
        data = tmp_path / "library"
        shutil.copytree(LIBRARY, data / "agents")
        path = data / "agents" / f"{DEEP}.md"
        lines = path.read_text(encoding="utf-8").split("\n")
        path.write_text(
            "\n".join(line for line in lines if not line.startswith("tier:")), encoding="utf-8"
        )
        monkeypatch.setattr(cc, "DATA_DIR", data)
        row = self.list_rows(capsys)[DEEP]
        assert (row["tier"], row["tier_from"], row["mapped"]) == (None, None, None)
        capsys.readouterr()
        assert run("agents", "list") == 0
        (line,) = [ln for ln in capsys.readouterr().out.splitlines() if DEEP in ln]
        assert "no tier" in line

    def test_agents_list_says_all_three_in_text(self, project, capsys):
        tier_map(project, deep=ONE)
        configure(project, agents={"tiers": {STANDARD: "light"}})
        capsys.readouterr()
        assert run("agents", "list") == 0
        lines = capsys.readouterr().out.splitlines()
        (deep,) = [ln for ln in lines if DEEP in ln]
        (standard,) = [ln for ln in lines if STANDARD in ln]
        (curator,) = [ln for ln in lines if "backlog-curator" in ln]
        assert f"tier deep (source) -> {ONE}" in deep
        assert "tier light (agents.tiers), not mapped" in standard
        assert "tier standard (source), not mapped" in curator
