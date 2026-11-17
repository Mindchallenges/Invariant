"""ADR-021 increment 3: rulings (D6, D7, D8).

A configuration that loosens a built-in, a rule below its ``standard`` level,
a profile that sets one there, or a machine shadowing a shipped one, carries a
ruling under ``enforcement.rulings``: who allowed it, when and why (D6). Only
the user records one, with ``rules loosen`` confirmed at a terminal, and an
agent's edit adding or changing one is refused at ``pre-tool-use`` (D7). The
lowerings a configuration already has are carried over once, and until then
they warn for one release rather than refuse (D8).

Every repository here is a scratch one, and the terminal is scripted.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.cli.main import Context, main
from invariant.core import rulings
from invariant.core.errors import ManifestError
from invariant.core.home import user_config_path
from invariant.core.machines import MACHINE_DIR
from invariant.core.manifest import MANIFEST_RELPATH, Level, Manifest
from invariant.core.rules import RuleContext, evaluate, get_rule
from invariant.hosts.claude_code import ClaudeCodeHost

TODAY = datetime.datetime.now().astimezone().date().isoformat()
ORIGIN = "https://host.example/team/widgets.git"
IDENTITY = "host.example/team/widgets"
RULING = {"by": "owner", "date": "2026-11-15", "reason": "no coverage tool runs here yet"}


# -- fixtures ------------------------------------------------------------------


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def run(capsys, *argv) -> tuple[int, str]:
    capsys.readouterr()
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def manifest_path(root: Path) -> Path:
    return root / MANIFEST_RELPATH


def write_manifest(root: Path, extra: dict) -> None:
    body = {"version": 1, "project": {"name": "widgets"}, **extra}
    path = manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "widgets"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    write_manifest(root, {})
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    monkeypatch.chdir(root)
    return root


def load(root: Path, extra: dict, env: dict | None = None) -> Manifest:
    """Resolve ``extra`` as the repo's manifest; ``env`` adds to the real
    environment, whose INVARIANT_HOME is the suite's scratch home."""
    write_manifest(root, extra)
    return Manifest.locate(root, env={**os.environ, **(env or {})})


def enforcement(rules: dict | None = None, rulings_: dict | None = None, **more) -> dict:
    block: dict = dict(more)
    if rules is not None:
        block["rules"] = rules
    if rulings_ is not None:
        block["rulings"] = rulings_
    return {"enforcement": block}


@pytest.fixture
def terminal(monkeypatch):
    """A terminal on stdin, answering each question from ``answers``."""

    def answer(*answers: str) -> list[str]:
        asked: list[str] = []
        queue = list(answers)
        monkeypatch.setattr("invariant.cli.main._stdin_is_terminal", lambda: True)

        def reply(prompt: str = "") -> str:
            asked.append(prompt)
            if not queue:
                raise EOFError
            return queue.pop(0)

        monkeypatch.setattr("builtins.input", reply)
        return asked

    return answer


@pytest.fixture
def no_terminal(monkeypatch):
    monkeypatch.setattr("invariant.cli.main._stdin_is_terminal", lambda: False)

    def refuse(prompt: str = "") -> str:
        raise AssertionError("asked with no terminal")

    monkeypatch.setattr("builtins.input", refuse)


def shadow(root: Path, machine: str = "tdd") -> str:
    """A project machine with a shipped machine's id: it replaces its gates."""
    target = root / "machines" / f"{machine}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text((MACHINE_DIR / f"{machine}.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    return f"machines/{machine}.yaml"


# -- D6: what loosens, and the refusal ------------------------------------------------


LOOSENINGS = [
    pytest.param(lambda root: enforcement({"coverage.meets_floor": "off"}),
                 "coverage.meets_floor", id="a rule below standard"),
    pytest.param(lambda root: enforcement({"test.all_pass": "ask"}),
                 "test.all_pass", id="ask is below block"),
    pytest.param(lambda root: enforcement(profile="loose"), "profile", id="the loose profile"),
    pytest.param(lambda root: {"methodology": {"extra_machines": [shadow(root)]}},
                 "machine.tdd", id="a shadowing machine"),
    pytest.param(lambda root: {"methodology": {"extra_machines": ["machines"]}, "_shadow": True},
                 "machine.tdd", id="a directory holding a shadowing machine"),
]


def _config(root: Path, build) -> dict:
    config = build(root)
    if config.pop("_shadow", False):
        shadow(root)
    return config


def _with_rulings(config: dict, rulings_: dict) -> dict:
    out = json.loads(json.dumps(config))
    out.setdefault("enforcement", {})["rulings"] = rulings_
    return out


class TestWhatLoosens:
    def test_a_clean_configuration_loosens_nothing_and_loads_as_before(self, repo):
        manifest = Manifest.locate(repo)
        assert rulings.loosenings(manifest) == []
        assert manifest.unruled == ()
        assert "rulings" not in manifest.data["enforcement"]
        assert not rulings.in_force(manifest)

    @pytest.mark.parametrize("build, key", LOOSENINGS)
    def test_each_loosening_is_refused_without_a_ruling(self, repo, build, key):
        config = _with_rulings(_config(repo, build), {})
        with pytest.raises(ManifestError) as caught:
            load(repo, config)
        said = str(caught.value)
        assert "needs a ruling" in said
        assert f"invariant rules loosen {key}" in said

    @pytest.mark.parametrize("build, key", LOOSENINGS)
    def test_each_loosening_loads_with_its_ruling(self, repo, build, key):
        config = _with_rulings(_config(repo, build), {key: dict(RULING)})
        manifest = load(repo, config)
        assert manifest.unruled == ()
        assert [item.key for item in rulings.loosenings(manifest)] == [key]

    @pytest.mark.parametrize("build, key", LOOSENINGS)
    def test_each_loosening_only_warns_until_a_project_records_rulings(self, repo, build, key):
        manifest = load(repo, _config(repo, build))
        assert [item.key for item in manifest.unruled] == [key]
        said = rulings.warning(manifest)
        assert key in said and "ADR-021 D8" in said and "rules carry-over" in said

    def test_raising_a_level_and_choosing_strict_need_nothing(self, repo):
        manifest = load(repo, enforcement(
            {"coverage.meets_floor": "block", "agents.design_agent_ran": "block"},
            {}, profile="strict"))
        assert rulings.loosenings(manifest) == []

    def test_a_level_at_standard_under_strict_is_no_loosening(self, repo):
        manifest = load(repo, enforcement({"session.own_file_set_only": "warn"}, {}, profile="strict"))
        assert rulings.loosenings(manifest) == []

    def test_under_loose_its_own_levels_ride_the_profile_ruling(self, repo):
        manifest = load(repo, enforcement(
            {"coverage.meets_floor": "off"}, {"profile": dict(RULING)}, profile="loose"))
        assert [item.key for item in rulings.loosenings(manifest)] == ["profile"]

    def test_under_loose_a_level_below_loose_needs_its_own_ruling(self, repo):
        with pytest.raises(ManifestError, match="test.all_pass"):
            load(repo, enforcement({"test.all_pass": "off"}, {"profile": dict(RULING)}, profile="loose"))

    def test_a_projects_own_step_is_relevelled_without_a_ruling(self, repo):
        manifest = load(repo, {
            "steps": {"lint": {"at": "session.before_merge", "run": "true"}},
            **enforcement({"step.lint": "off"}, {}),
        })
        assert rulings.loosenings(manifest) == []

    def test_a_shipped_id_is_the_only_shadow(self, repo):
        target = repo / "machines" / "mine.yaml"
        target.parent.mkdir()
        target.write_text((MACHINE_DIR / "tdd.yaml").read_text(encoding="utf-8").replace(
            "id: tdd", "id: mine"), encoding="utf-8")
        manifest = load(repo, {"methodology": {"extra_machines": ["machines/mine.yaml", "absent"]}})
        assert rulings.loosenings(manifest) == []

    def test_the_loosening_names_where_it_was_set(self, repo):
        manifest = load(repo, enforcement({"coverage.meets_floor": "off"}))
        (item,) = manifest.unruled
        assert item.source == "repo:.claude/invariant.yaml"
        assert item.baseline == "warn" and item.value == "off"
        assert "below warn in the standard profile" in item.describe()

    def test_only_shipped_profiles_below_standard_loosen(self):
        assert rulings.profile_loosens("loose")
        assert not rulings.profile_loosens("standard")
        assert not rulings.profile_loosens("strict")


class TestTheRulingItself:
    def test_a_stale_ruling_is_refused(self, repo):
        with pytest.raises(ManifestError, match="does not loosen coverage.meets_floor"):
            load(repo, enforcement({}, {"coverage.meets_floor": dict(RULING)}))

    def test_a_ruling_for_no_rule_is_refused_naming_what_a_key_may_be(self, repo):
        with pytest.raises(ManifestError, match="no registered rule, 'profile'"):
            load(repo, enforcement({}, {"coverage.meets_flor": dict(RULING)}))

    def test_a_ruling_left_after_the_loosening_was_removed_is_stale(self, repo):
        with pytest.raises(ManifestError, match="does not loosen profile"):
            load(repo, enforcement(rulings_={"profile": dict(RULING)}, profile="standard"))

    @pytest.mark.parametrize("missing", ["by", "date", "reason"])
    def test_a_ruling_with_no_by_date_or_reason_is_refused(self, repo, missing):
        ruling = {k: v for k, v in RULING.items() if k != missing}
        with pytest.raises(ManifestError, match=f"has no {missing}"):
            load(repo, enforcement({"coverage.meets_floor": "off"}, {"coverage.meets_floor": ruling}))

    @pytest.mark.parametrize("ruling, said", [
        ({**RULING, "approved": True}, "is not a key this build reads"),
        ({**RULING, "date": "yesterday"}, "must be a date"),
        ({**RULING, "date": "2026-13-40"}, "must be a date"),
        ({**RULING, "by": 7}, "must be one line"),
        ({**RULING, "reason": ["a", "b"]}, "must be text"),
        ("owner said so", "must be a mapping"),
    ])
    def test_a_malformed_ruling_is_refused(self, repo, ruling, said):
        with pytest.raises(ManifestError, match=said):
            load(repo, enforcement({"coverage.meets_floor": "off"}, {"coverage.meets_floor": ruling}))

    def test_rulings_must_be_a_mapping(self, repo):
        with pytest.raises(ManifestError, match="enforcement.rulings must be a mapping"):
            load(repo, enforcement({}, ["coverage.meets_floor"]))

    def test_an_empty_rulings_key_puts_the_refusal_in_force(self, repo):
        write_manifest(repo, {})
        manifest_path(repo).write_text(
            manifest_path(repo).read_text(encoding="utf-8")
            + "enforcement:\n  rules:\n    coverage.meets_floor: 'off'\n  rulings:\n",
            encoding="utf-8",
        )
        with pytest.raises(ManifestError, match="needs a ruling"):
            Manifest.locate(repo)

    def test_a_floored_rule_is_refused_even_with_a_ruling(self, repo):
        with pytest.raises(ManifestError) as caught:
            load(repo, enforcement(
                {"docs.mandate_kept": "warn"}, {"docs.mandate_kept": dict(RULING)}))
        said = str(caught.value)
        assert "cannot be" in said and "a ruling does not lower it" in said

    def test_a_ruling_at_the_user_level_is_refused(self, repo):
        path = user_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({"version": 1, "defaults": enforcement(
            {"coverage.meets_floor": "off"}, {"coverage.meets_floor": dict(RULING)})}),
            encoding="utf-8")
        with pytest.raises(ManifestError, match="set at the user level"):
            Manifest.locate(repo)

    def test_a_user_level_loosening_is_ruled_in_the_project(self, repo):
        path = user_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({"version": 1, "defaults": enforcement(profile="loose")}),
                        encoding="utf-8")
        manifest = load(repo, enforcement(rulings_={"profile": dict(RULING)}))
        assert manifest.unruled == ()

    def test_a_yaml_date_reads_as_its_iso_text(self, repo):
        write_manifest(repo, enforcement({"coverage.meets_floor": "off"}))
        manifest_path(repo).write_text(
            manifest_path(repo).read_text(encoding="utf-8")
            + "  rulings:\n    coverage.meets_floor:\n      by: owner\n"
            "      date: 2026-11-15\n      reason: none yet\n",
            encoding="utf-8",
        )
        manifest = Manifest.locate(repo)
        assert manifest.get("enforcement.rulings")["coverage.meets_floor"]["date"] == "2026-11-15"


class TestTheEnvironmentIsNotJudged:
    """D6: INVARIANT_PROFILE and INVARIANT_DISABLE are per-invocation routes."""

    def test_invariant_profile_loose_loads_where_the_refusal_is_in_force(self, repo):
        manifest = load(repo, enforcement(rulings_={}), env={"INVARIANT_PROFILE": "loose"})
        assert manifest.rule_level("coverage.meets_floor") is Level.OFF
        assert manifest.unruled == ()

    def test_invariant_disable_loads_where_the_refusal_is_in_force(self, repo, capsys):
        manifest = load(repo, enforcement(rulings_={}), env={"INVARIANT_DISABLE": "test.all_pass"})
        assert manifest.rule_level("test.all_pass") is Level.OFF
        assert manifest.unruled == ()

    def test_a_ruled_profile_is_not_stale_under_invariant_profile(self, repo):
        manifest = load(
            repo, enforcement(rulings_={"profile": dict(RULING)}, profile="loose"),
            env={"INVARIANT_PROFILE": "strict"},
        )
        assert manifest.get("enforcement.profile") == "strict"
        assert manifest.configured()[0] == "loose"

    def test_a_hand_built_manifest_answers_what_it_holds(self, repo):
        manifest = Manifest.locate(repo)
        manifest._configured = None
        assert manifest.configured()[0] == "standard"


class TestProvenance:
    def test_by_and_date_show_in_config_show_provenance(self, repo, capsys):
        write_manifest(repo, enforcement({"coverage.meets_floor": "off"},
                                         {"coverage.meets_floor": dict(RULING)}))
        code, text = run(capsys, "config", "show", "--provenance")
        assert code == 0
        by = next(line for line in text.splitlines() if "rulings.coverage.meets_floor.by" in line)
        date = next(line for line in text.splitlines() if "rulings.coverage.meets_floor.date" in line)
        assert "'owner'" in by and "[repo:.claude/invariant.yaml]" in by
        assert "'2026-11-15'" in date

    def test_config_show_json_carries_the_ruling(self, repo, capsys):
        write_manifest(repo, enforcement({"coverage.meets_floor": "off"},
                                         {"coverage.meets_floor": dict(RULING)}))
        _code, text = run(capsys, "config", "show", "--json")
        assert json.loads(text)["config"]["enforcement"]["rulings"]["coverage.meets_floor"]["by"] == "owner"


class TestTheSurfaces:
    def test_doctor_warns_on_a_loosening_with_no_ruling(self, repo, capsys):
        write_manifest(repo, enforcement(profile="loose"))
        _code, text = run(capsys, "doctor", "--json")
        row = next(c for c in json.loads(text[text.index("{"):])["checks"] if c["check"] == "rulings")
        assert row["ok"] and row["warn"]
        assert "profile" in row["detail"] and "rules carry-over" in row["detail"]

    def test_doctor_lists_the_rulings_recorded(self, repo, capsys):
        write_manifest(repo, enforcement({"coverage.meets_floor": "off"},
                                         {"coverage.meets_floor": dict(RULING)}))
        _code, text = run(capsys, "doctor", "--json")
        row = next(c for c in json.loads(text[text.index("{"):])["checks"] if c["check"] == "rulings")
        assert row["ok"] and not row["warn"]
        assert "coverage.meets_floor (by owner, 2026-11-15)" in row["detail"]

    def test_doctor_has_no_rulings_row_when_nothing_is_loosened(self, repo, capsys):
        _code, text = run(capsys, "doctor", "--json")
        assert not [c for c in json.loads(text[text.index("{"):])["checks"] if c["check"] == "rulings"]

    def test_rules_list_marks_a_ruled_and_an_unruled_loosening(self, repo, capsys):
        write_manifest(repo, enforcement({"coverage.meets_floor": "off", "test.all_pass": "warn"}))
        code, text = run(capsys, "rules", "list")
        assert code == 0
        row = next(line for line in text.splitlines() if line.strip().startswith("test.all_pass"))
        assert "[loosened with no ruling: invariant rules carry-over]" in row
        assert "2 loosening(s) carry no ruling" in text
        write_manifest(repo, enforcement({"coverage.meets_floor": "off"},
                                         {"coverage.meets_floor": dict(RULING)}))
        code, text = run(capsys, "rules", "list", "--json")
        payload = json.loads(text)
        assert payload["loosenings"] == [{
            "key": "coverage.meets_floor", "value": "off",
            "source": "repo:.claude/invariant.yaml", "ruling": RULING}]
        _code, text = run(capsys, "rules", "list")
        row = next(line for line in text.splitlines() if line.strip().startswith("coverage.meets_floor"))
        assert "[ruling: by owner, 2026-11-15]" in row

    def test_the_new_rule_is_graded_block_in_every_profile(self, repo):
        assert get_rule("config.rulings_by_user") is not None
        for profile in ("loose", "standard", "strict"):
            manifest = load(repo, enforcement(profile=profile, rulings_={"profile": dict(RULING)})
                            if profile == "loose" else enforcement(profile=profile))
            assert manifest.rule_level("config.rulings_by_user") is Level.BLOCK


# -- D7: rules loosen ---------------------------------------------------------------------


COMMENTED = """version: 1
# The project's name.
project:
  name: widgets

enforcement:
  profile: standard   # the default
  rules:
    # Advisory while the suite grows.
    pipeline.complete: warn
    test.all_pass: block  # green before landing
"""


class TestRulesLoosen:
    def test_it_refuses_without_a_terminal_and_writes_nothing(self, repo, capsys, no_terminal):
        before = manifest_path(repo).read_text(encoding="utf-8")
        code, text = run(capsys, "rules", "loosen", "coverage.meets_floor", "--to", "off",
                         "--by", "owner", "--reason", "no tool yet")
        assert code == 1
        assert "refused: a ruling is the user's to record" in text
        assert "in a terminal of your own" in text
        assert manifest_path(repo).read_text(encoding="utf-8") == before

    def test_it_refuses_under_json_even_at_a_terminal(self, repo, capsys, terminal):
        asked = terminal("y")
        before = manifest_path(repo).read_text(encoding="utf-8")
        code, text = run(capsys, "rules", "loosen", "coverage.meets_floor", "--to", "off",
                         "--by", "owner", "--reason", "no tool yet", "--json")
        assert code == 1 and asked == []
        assert json.loads(text)["refused"] == "no terminal"
        assert manifest_path(repo).read_text(encoding="utf-8") == before

    def test_declined_at_the_terminal_writes_nothing(self, repo, capsys, terminal):
        terminal("n")
        before = manifest_path(repo).read_text(encoding="utf-8")
        code, text = run(capsys, "rules", "loosen", "coverage.meets_floor", "--to", "off",
                         "--by", "owner", "--reason", "no tool yet")
        assert code == 1 and "declined" in text
        assert manifest_path(repo).read_text(encoding="utf-8") == before

    def test_end_of_input_at_the_terminal_declines(self, repo, capsys, terminal):
        terminal()
        code, text = run(capsys, "rules", "loosen", "coverage.meets_floor", "--to", "off",
                         "--by", "owner", "--reason", "no tool yet")
        assert code == 1 and "declined" in text

    def test_confirmed_it_writes_the_level_and_the_ruling_keeping_every_comment(
        self, repo, capsys, terminal
    ):
        manifest_path(repo).write_text(COMMENTED, encoding="utf-8")
        asked = terminal("y")
        code, text = run(capsys, "rules", "loosen", "test.all_pass", "--to", "warn",
                         "--by", "owner", "--reason", "the device suite is flaky")
        assert code == 0, text
        assert asked == ["Record this ruling? [y/N] "]
        written = manifest_path(repo).read_text(encoding="utf-8")
        for comment in ("# The project's name.", "# the default", "# Advisory while the suite grows."):
            assert comment in written
        assert "test.all_pass: warn  # green before landing" in written
        body = yaml.safe_load(written)
        assert body["enforcement"]["rulings"] == {"test.all_pass": {
            "by": "owner", "date": TODAY, "reason": "the device suite is flaky"}}
        manifest = Manifest.locate(repo)
        assert manifest.rule_level("test.all_pass") is Level.WARN
        assert rulings.in_force(manifest) and manifest.unruled == ()

    def test_it_writes_an_enforcement_block_where_there_is_none(self, repo, capsys, terminal):
        terminal("y")
        code, text = run(capsys, "rules", "loosen", "coverage.meets_floor", "--to", "off",
                         "--by", "owner", "--reason", "no tool yet")
        assert code == 0, text
        body = yaml.safe_load(manifest_path(repo).read_text(encoding="utf-8"))
        assert body["enforcement"]["rules"] == {"coverage.meets_floor": "off"}
        assert body["enforcement"]["rulings"]["coverage.meets_floor"]["by"] == "owner"

    def test_a_second_loosening_is_added_beside_the_first(self, repo, capsys, terminal):
        terminal("y", "y")
        for rule_id in ("coverage.meets_floor", "docs.diagrams_current"):
            code, text = run(capsys, "rules", "loosen", rule_id, "--to", "off",
                             "--by", "owner", "--reason", "not yet")
            assert code == 0, text
        body = yaml.safe_load(manifest_path(repo).read_text(encoding="utf-8"))
        assert set(body["enforcement"]["rulings"]) == {"coverage.meets_floor", "docs.diagrams_current"}
        Manifest.locate(repo)

    def test_it_records_the_loose_profile(self, repo, capsys, terminal):
        terminal("y")
        code, text = run(capsys, "rules", "loosen", "profile", "--to", "loose",
                         "--by", "owner", "--reason", "observing first")
        assert code == 0, text
        manifest = Manifest.locate(repo)
        assert manifest.get("enforcement.profile") == "loose"
        assert rulings.recorded(manifest)["profile"]["reason"] == "observing first"

    def test_it_records_a_shadowing_machine_without_a_level(self, repo, capsys, terminal):
        entry = shadow(repo)
        write_manifest(repo, {"methodology": {"extra_machines": [entry]}})
        terminal("y")
        code, text = run(capsys, "rules", "loosen", "machine.tdd",
                         "--by", "owner", "--reason", "our own tdd lane")
        assert code == 0, text
        assert rulings.recorded(Manifest.locate(repo))["machine.tdd"]["by"] == "owner"

    def test_it_loads_a_configuration_the_refusal_would_stop(self, repo, capsys, terminal):
        write_manifest(repo, enforcement({"coverage.meets_floor": "off"}, {}))
        with pytest.raises(ManifestError):
            Manifest.locate(repo)
        terminal("y")
        code, text = run(capsys, "rules", "loosen", "coverage.meets_floor", "--to", "off",
                         "--by", "owner", "--reason", "no tool yet")
        assert code == 0, text
        Manifest.locate(repo)

    def test_a_file_it_cannot_edit_in_place_gets_the_block_to_add_by_hand(self, repo, capsys, terminal):
        manifest_path(repo).write_text(
            "version: 1\nproject: {name: widgets}\nenforcement: {profile: standard}\n",
            encoding="utf-8")
        before = manifest_path(repo).read_text(encoding="utf-8")
        terminal("y")
        code, text = run(capsys, "rules", "loosen", "coverage.meets_floor", "--to", "off",
                         "--by", "owner", "--reason", "no tool yet")
        assert code == 1
        assert "written inline" in text and "by hand" in text
        assert "coverage.meets_floor: 'off'" in text
        assert manifest_path(repo).read_text(encoding="utf-8") == before

    def test_a_write_the_configuration_refuses_is_put_back(self, repo, capsys, terminal, monkeypatch):
        before = manifest_path(repo).read_text(encoding="utf-8")
        terminal("y")
        real = Manifest.locate
        calls = []

        def refusing(*args, **kwargs):
            calls.append(args)
            if len(calls) > 1:
                raise ManifestError("refused on purpose")
            return real(*args, **kwargs)

        monkeypatch.setattr(Manifest, "locate", refusing)
        code, text = run(capsys, "rules", "loosen", "coverage.meets_floor", "--to", "off",
                         "--by", "owner", "--reason", "no tool yet")
        assert code == 1 and "would not load with it: refused on purpose" in text
        assert manifest_path(repo).read_text(encoding="utf-8") == before

    @pytest.mark.parametrize("argv, said", [
        (["docs.mandate_kept", "--to", "warn"], "no ruling lowers it"),
        (["coverage.meets_floor", "--to", "block"], "loosens nothing and needs no ruling"),
        (["coverage.meets_flor", "--to", "off"], "no registered rule"),
        (["coverage.meets_floor"], "--to must name the level"),
        (["step.lint", "--to", "off"], "the project's own step"),
        (["profile", "--to", "strict"], "loosens nothing and needs no ruling"),
        (["profile", "--to", "lax"], "--to must name a shipped profile"),
        (["machine.tdd", "--to", "off"], "a shadowing machine has no level"),
        (["machine.tdd"], "no methodology.extra_machines entry shadows"),
    ])
    def test_what_it_refuses(self, repo, capsys, terminal, argv, said):
        terminal("y")
        code, text = run(capsys, "rules", "loosen", *argv, "--by", "owner", "--reason", "x")
        assert code == 2 and said in text

    def test_under_loose_a_level_the_profile_holds_needs_no_ruling(self, repo, capsys, terminal):
        write_manifest(repo, enforcement(rulings_={"profile": dict(RULING)}, profile="loose"))
        terminal("y")
        code, text = run(capsys, "rules", "loosen", "coverage.meets_floor", "--to", "off",
                         "--by", "owner", "--reason", "x")
        assert code == 2 and "which its own ruling covers" in text

    @pytest.mark.parametrize("by, reason, said", [
        (" ", "x", "--by must name"), ("a\nb", "x", "--by must name"), ("owner", " ", "--reason must say"),
    ])
    def test_it_needs_who_and_why(self, repo, capsys, terminal, by, reason, said):
        code, text = run(capsys, "rules", "loosen", "coverage.meets_floor", "--to", "off",
                         "--by", by, "--reason", reason)
        assert code == 2 and said in text

    def test_a_pointer_only_project_has_no_level_to_write(self, tmp_path):
        manifest = Manifest.locate(tmp_path, required=False)
        with pytest.raises(ManifestError, match="no project here"):
            rulings.own_level(manifest)


# -- D8: rules carry-over --------------------------------------------------------------------


HEAP_LIKE = """version: 1
project:
  name: widgets

enforcement:
  profile: strict
  rules:
    # The device suite cannot run on every change yet.
    test.all_pass: warn

    session.own_file_set_only: warn

    memory.no_dead_links: "off"   # links into the archive
"""


class TestCarryOver:
    def test_it_carries_over_a_lowered_rule_with_its_comment_as_the_reason(self, repo, capsys, terminal):
        manifest_path(repo).write_text(HEAP_LIKE, encoding="utf-8")
        manifest = Manifest.locate(repo)
        assert [item.key for item in manifest.unruled] == ["test.all_pass", "memory.no_dead_links"]
        terminal("y")
        code, text = run(capsys, "rules", "carry-over")
        assert code == 0, text
        assert "test.all_pass: The device suite cannot run on every change yet." in text
        written = manifest_path(repo).read_text(encoding="utf-8")
        assert written.startswith(HEAP_LIKE.rstrip("\n"))
        recorded = rulings.recorded(Manifest.locate(repo))
        assert recorded == {
            "test.all_pass": {"by": "carried over", "date": TODAY,
                              "reason": "The device suite cannot run on every change yet."},
            "memory.no_dead_links": {"by": "carried over", "date": TODAY,
                                     "reason": "links into the archive"},
        }
        assert Manifest.locate(repo).unruled == ()

    def test_it_carries_over_the_loose_profile(self, repo, capsys, terminal):
        manifest_path(repo).write_text(
            "version: 1\nproject:\n  name: widgets\nenforcement:\n  profile: loose\n",
            encoding="utf-8")
        terminal("y")
        code, text = run(capsys, "rules", "carry-over")
        assert code == 0, text
        manifest = Manifest.locate(repo)
        assert rulings.recorded(manifest)["profile"] == {
            "by": "carried over", "date": TODAY, "reason": rulings.CARRIED_REASON}
        assert rulings.in_force(manifest)

    def test_it_refuses_without_a_terminal(self, repo, capsys, no_terminal):
        manifest_path(repo).write_text(HEAP_LIKE, encoding="utf-8")
        code, text = run(capsys, "rules", "carry-over")
        assert code == 1 and "refused" in text
        assert manifest_path(repo).read_text(encoding="utf-8") == HEAP_LIKE

    def test_on_a_clean_configuration_it_puts_the_refusal_in_force(self, repo, capsys, terminal):
        terminal("y")
        code, text = run(capsys, "rules", "carry-over")
        assert code == 0, text
        assert "nothing is loosened" in text
        assert yaml.safe_load(manifest_path(repo).read_text(encoding="utf-8"))["enforcement"] == {
            "rulings": {}}
        code, text = run(capsys, "rules", "carry-over")
        assert code == 0 and "nothing to carry over" in text
        write_manifest(repo, enforcement({"coverage.meets_floor": "off"}, {}))
        with pytest.raises(ManifestError, match="needs a ruling"):
            Manifest.locate(repo)

    def test_a_ruling_added_after_an_empty_rulings_replaces_it(self, repo, capsys, terminal):
        manifest_path(repo).write_text(
            "version: 1\nproject:\n  name: widgets\nenforcement:\n  rulings: {}\n", encoding="utf-8")
        terminal("y")
        code, text = run(capsys, "rules", "loosen", "coverage.meets_floor", "--to", "off",
                         "--by", "owner", "--reason", "no tool yet")
        assert code == 0, text
        Manifest.locate(repo)


# -- store projects -----------------------------------------------------------------------------


@pytest.fixture
def store_project(tmp_path, monkeypatch, capsys):
    root = tmp_path / "widgets"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    git(root, "remote", "add", "origin", ORIGIN)
    store = tmp_path / "store"
    store.mkdir()
    git(store, "init", "-q", "-b", "main")
    (store / "invariant.yaml").write_text(
        "version: 1\n# The store's own level.\nenforcement:\n  profile: loose\n"
        f"store:\n  repos:\n    {IDENTITY}: {{}}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(root)
    assert main(["store", "link", str(store)]) == 0
    capsys.readouterr()
    return root, store


class TestStoreProjects:
    def test_the_store_level_loosening_warns_until_carried_over(self, store_project):
        root, _store = store_project
        manifest = Manifest.locate(root)
        assert [item.key for item in manifest.unruled] == ["profile"]
        assert manifest.unruled[0].source.startswith("store:")

    def test_carry_over_writes_in_the_repositorys_block_of_the_store(self, store_project, capsys, terminal):
        root, store = store_project
        terminal("y")
        code, text = run(capsys, "rules", "carry-over")
        assert code == 0, text
        written = (store / "invariant.yaml").read_text(encoding="utf-8")
        assert "# The store's own level." in written
        body = yaml.safe_load(written)
        assert body["store"]["repos"][IDENTITY]["enforcement"]["rulings"]["profile"]["by"] == "carried over"
        assert "rulings" not in body["enforcement"]
        manifest = Manifest.locate(root)
        assert manifest.unruled == () and rulings.in_force(manifest)

    def test_loosen_writes_the_level_and_ruling_in_the_store(self, store_project, capsys, terminal):
        root, store = store_project
        terminal("y", "y")
        assert run(capsys, "rules", "carry-over")[0] == 0
        code, text = run(capsys, "rules", "loosen", "test.all_pass", "--to", "off",
                         "--by", "owner", "--reason", "x")
        assert code == 0, text
        block = yaml.safe_load((store / "invariant.yaml").read_text(encoding="utf-8"))[
            "store"]["repos"][IDENTITY]["enforcement"]
        assert block["rules"] == {"test.all_pass": "off"}
        assert set(block["rulings"]) == {"profile", "test.all_pass"}
        assert Manifest.locate(root).rule_level("test.all_pass") is Level.OFF

    def test_the_hook_covers_the_stores_file(self, store_project):
        root, store = store_project
        manifest = Manifest.locate(root)
        assert rulings.is_config_file(manifest, store / "invariant.yaml")
        assert not rulings.is_config_file(manifest, store / "other.yaml")


# -- D7: the hook -----------------------------------------------------------------------------------


def verdict(root: Path, payload: dict):
    manifest = Manifest.locate(root)
    ctx = RuleContext(manifest=manifest, cwd=root, payload=payload)
    (judged,) = evaluate(["config.rulings_by_user"], ctx)
    return judged


def edit(path: Path, old: str, new: str, **more) -> dict:
    return {"tool_name": "Edit", "tool_input": {
        "file_path": str(path), "old_string": old, "new_string": new, **more}}


ADD_RULING = (
    "  rulings:\n    coverage.meets_floor:\n      by: owner\n"
    "      date: '2026-11-15'\n      reason: no tool yet\n"
)


class TestTheHook:
    @pytest.fixture
    def lowered(self, repo) -> Path:
        manifest_path(repo).write_text(
            "version: 1\nproject:\n  name: widgets\nenforcement:\n  rules:\n"
            "    coverage.meets_floor: 'off'\n", encoding="utf-8")
        return repo

    def test_an_edit_adding_a_ruling_is_refused_naming_the_command(self, lowered):
        path = manifest_path(lowered)
        judged = verdict(lowered, edit(path, "    coverage.meets_floor: 'off'\n",
                                       "    coverage.meets_floor: 'off'\n" + ADD_RULING))
        assert judged.blocks
        assert "coverage.meets_floor" in judged.verdict.message
        assert any("invariant rules loosen" in fix for fix in judged.verdict.fixes)

    def test_a_write_adding_a_ruling_is_refused(self, lowered):
        path = manifest_path(lowered)
        content = path.read_text(encoding="utf-8") + ADD_RULING
        judged = verdict(lowered, {"tool_name": "Write", "tool_input": {
            "file_path": str(path), "content": content}})
        assert judged.blocks

    def test_a_multi_edit_changing_a_ruling_is_refused(self, lowered):
        path = manifest_path(lowered)
        path.write_text(path.read_text(encoding="utf-8") + ADD_RULING, encoding="utf-8")
        judged = verdict(lowered, {"tool_name": "MultiEdit", "tool_input": {
            "file_path": str(path), "edits": [
                {"old_string": "by: owner", "new_string": "by: agent"},
                {"old_string": "no tool yet", "new_string": "it said so", "replace_all": True},
            ]}})
        assert judged.blocks

    def test_an_edit_removing_a_ruling_or_touching_other_keys_passes(self, lowered):
        path = manifest_path(lowered)
        path.write_text(path.read_text(encoding="utf-8") + ADD_RULING, encoding="utf-8")
        assert verdict(lowered, edit(path, ADD_RULING, "")).verdict.ok
        assert verdict(lowered, edit(path, "name: widgets", "name: gadgets")).verdict.ok

    def test_an_edit_in_a_session_worktrees_manifest_is_covered(self, lowered, tmp_path):
        other = tmp_path / "wt" / ".claude" / "invariant.yaml"
        other.parent.mkdir(parents=True)
        other.write_text(manifest_path(lowered).read_text(encoding="utf-8"), encoding="utf-8")
        judged = verdict(lowered, {"tool_name": "Write", "tool_input": {
            "file_path": str(other), "content": other.read_text(encoding="utf-8") + ADD_RULING}})
        assert judged.blocks

    def test_the_user_level_is_covered(self, lowered):
        path = user_config_path()
        judged = verdict(lowered, {"tool_name": "Write", "tool_input": {
            "file_path": str(path),
            "content": "version: 1\ndefaults:\n  enforcement:\n" + ADD_RULING.replace("\n  ", "\n    ")
            .replace("  rulings", "    rulings", 1)}})
        assert judged.blocks

    def test_other_files_and_other_calls_are_not_its_business(self, lowered):
        assert verdict(lowered, edit(lowered / "app.py", "x = 1", "x = 2")).verdict.skipped
        assert verdict(lowered, {"tool_name": "Bash", "tool_input": {"command": "ls"}}).verdict.skipped
        assert verdict(lowered, {"tool_name": "Write", "tool_input": {}}).verdict.skipped

    def test_an_edit_that_does_not_apply_is_not_judged(self, lowered):
        path = manifest_path(lowered)
        assert verdict(lowered, edit(path, "absent text", "rulings: {}")).verdict.skipped
        assert verdict(lowered, edit(path, "", "rulings: {}")).verdict.skipped

    def test_a_new_file_is_judged_from_nothing(self, lowered, tmp_path):
        fresh = tmp_path / "fresh" / ".claude" / "invariant.yaml"
        judged = verdict(lowered, edit(fresh, "", "enforcement:\n" + ADD_RULING))
        assert judged.blocks

    def test_unreadable_yaml_naming_rulings_is_refused_and_otherwise_passes(self, lowered):
        path = manifest_path(lowered)
        assert verdict(lowered, edit(path, "name: widgets", "name: [widgets")).verdict.ok
        judged = verdict(lowered, edit(path, "name: widgets", "name: [rulings"))
        assert judged.blocks and "does not read as YAML" in judged.verdict.message

    def test_a_manifest_that_cannot_be_read_fails(self, lowered, tmp_path):
        folder = tmp_path / "odd" / ".claude" / "invariant.yaml"
        folder.mkdir(parents=True)
        judged = verdict(lowered, edit(folder, "a", "b"))
        assert judged.blocks and "cannot be read" in judged.verdict.message

    def test_the_pre_tool_use_hook_denies_it(self, lowered):
        path = manifest_path(lowered)
        payload = {"session_id": "h1", **edit(path, "    coverage.meets_floor: 'off'\n",
                                              "    coverage.meets_floor: 'off'\n" + ADD_RULING)}
        response = ClaudeCodeHost(lowered).handle(
            "pre-tool-use", json.dumps(payload), Context(cwd=lowered))
        decision = response["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "config.rulings_by_user" in decision["permissionDecisionReason"]


# -- the line editor ---------------------------------------------------------------------------------


class TestTheLineEditor:
    def test_it_creates_nested_blocks_and_keeps_trailing_comments(self):
        text = "version: 1\n# trailing\n"
        after = rulings.edit_text(text, [(("store", "repos", "a/b"), "x", 1)])
        assert yaml.safe_load(after) == {"version": 1, "store": {"repos": {"a/b": {"x": 1}}}}
        assert "# trailing" in after

    def test_it_matches_a_quoted_key_and_keeps_the_indent(self):
        text = 'enforcement:\n    rules:\n        "test.all_pass": block\n'
        after = rulings.edit_text(text, [(("enforcement", "rules"), "test.all_pass", "warn")])
        assert yaml.safe_load(after) == {"enforcement": {"rules": {"test.all_pass": "warn"}}}
        assert "        test.all_pass: warn" in after

    def test_it_replaces_a_mapping_entry_whole(self):
        text = "a:\n  b:\n    c: 1\n    d: 2\n  e: 3\n"
        after = rulings.edit_text(text, [(("a",), "b", {"f": 4})])
        assert yaml.safe_load(after) == {"a": {"b": {"f": 4}, "e": 3}}

    @pytest.mark.parametrize("text, said", [
        ("a: [1\n", "not valid YAML"),
        ("- 1\n- 2\n", "does not hold a mapping"),
        ("enforcement: standard\n", "written inline"),
    ])
    def test_what_it_will_not_edit(self, text, said):
        with pytest.raises(rulings._Unwritable, match=said):
            rulings.edit_text(text, [(("enforcement", "rules"), "x", "off")])

    def test_an_empty_file_and_a_null_block_are_filled(self):
        assert yaml.safe_load(rulings.edit_text("", [(("a",), "b", 1)])) == {"a": {"b": 1}}
        assert yaml.safe_load(rulings.edit_text("a: null\n", [(("a",), "b", 1)])) == {"a": {"b": 1}}
        assert yaml.safe_load(rulings.edit_text("# only\n", [(("a",), "b", 1)])) == {"a": {"b": 1}}

    def test_a_misjudged_edit_is_refused(self):
        text = "a:\n  b: 1\na:\n  c: 2\n"
        with pytest.raises(rulings._Unwritable, match="would not read back"):
            rulings.edit_text(text, [(("a",), "b", 3)])

    def test_changed_rulings_reads_every_level_and_answers_none_on_bad_yaml(self):
        before = "store:\n  repos:\n    k:\n      enforcement:\n        rulings: {}\n"
        after = "store:\n  repos:\n    k:\n      enforcement:\n        rulings:\n          profile: {by: x}\n"
        assert rulings.changed_rulings(before, after) == ["profile"]
        assert rulings.changed_rulings(after, before) == []
        assert rulings.changed_rulings("", "a: [") is None
        assert rulings.changed_rulings("", "enforcement:\n  rulings: [x]\n") == []

    def test_a_comment_is_read_from_the_level_that_set_it(self, repo):
        manifest_path(repo).write_text(HEAP_LIKE, encoding="utf-8")
        manifest = Manifest.locate(repo)
        reasons = {item.key: rulings.comment_for(manifest, item) for item in manifest.unruled}
        assert reasons == {
            "test.all_pass": "The device suite cannot run on every change yet.",
            "memory.no_dead_links": "links into the archive",
        }

    def test_no_comment_is_read_from_a_level_with_no_file(self, repo):
        manifest = Manifest.locate(repo)
        item = rulings.Loosening("profile", "loose", None, "default")
        assert rulings.comment_for(manifest, item) is None
        item = rulings.Loosening("profile", "loose", None, "repo:absent.yaml")
        assert rulings.comment_for(manifest, item) is None
        item = rulings.Loosening("profile", "loose", None, "repo:.claude/invariant.yaml")
        assert rulings.comment_for(manifest, item) is None

    def test_a_write_with_no_after_is_refused(self, repo):
        manifest = Manifest.locate(repo)
        plan = rulings.Plan(rulings.own_level(manifest), (), "", None, "", "inline")
        with pytest.raises(ManifestError, match="cannot be edited in place"):
            rulings.write(plan, lambda: None)

    def _plan_for(self, repo):
        manifest = Manifest.locate(repo)
        target = rulings.own_level(manifest)
        before = target.path.read_text(encoding="utf-8")
        after = before + "# written\n"
        return target, before, rulings.Plan(target, (), before, after, "")

    def test_a_file_changed_since_the_plan_is_not_written_over(self, repo):
        target, before, plan = self._plan_for(repo)
        target.path.write_text(before + "# a peer's edit\n", encoding="utf-8")
        with pytest.raises(ManifestError, match="changed since the entries were shown"):
            rulings.write(plan, lambda: None)
        assert target.path.read_text(encoding="utf-8") == before + "# a peer's edit\n"

    def test_any_failure_of_the_reload_puts_the_file_back(self, repo):
        target, before, plan = self._plan_for(repo)

        def failing():
            raise RuntimeError("not a refusal")

        with pytest.raises(RuntimeError, match="not a refusal"):
            rulings.write(plan, failing)
        assert target.path.read_text(encoding="utf-8") == before

    def test_a_reload_that_holds_keeps_the_write(self, repo):
        target, before, plan = self._plan_for(repo)
        rulings.write(plan, lambda: None)
        assert target.path.read_text(encoding="utf-8") == before + "# written\n"
