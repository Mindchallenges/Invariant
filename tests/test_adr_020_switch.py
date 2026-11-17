"""ADR-020 increment 3, the switch: the store is init's default home.

D1: a fresh `init` in a git repository makes a store project, created (or
linked) at the default path: the configuration and every record in the store,
`records` set explicitly (data and policy `store`, `hooks` and `agents`
`local`), the docs starter at `invariant-docs/` in the store, nothing written
to the repository's working tree and no `.gitignore` line. Outside a git
repository, or below its top level, the home is `repo`, and the report says
why. D4: the `home` section is the first core section, `home:location` its
question, asked first; `--home` and `--store` answer it; the store default
holds in every mode; under the store home the level question offers `user`,
`store` and `store-repo`, the block the default; the report opens with a
`home` row and names the other route; `--json` carries `home`. D5: `doctor`'s
`config home` row for a store project and for two homes, and its `docs
folder` row. D9's question, `host:install`, stays last, default no, and under
the store home a yes wires `local`. D2: an existing project keeps its home.

Every test runs with a scratch `HOME` and the suite's scratch
`INVARIANT_HOME`; the real `~/.claude` and `~/.invariant` are never read or
written.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core import questionnaire
from invariant.core.errors import ManifestError
from invariant.core.home import invariant_home, load_user_config, user_config_path
from invariant.core.identity import repo_identity
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.core.storemigrate import MARKER, STORE_HOME_RECORDS
from invariant.core.stores import plan_home_store
from invariant.domains import docs as docs_domain
from invariant.hosts.claude_code import EVENTS, ClaudeCodeHost

ORIGIN = "https://host.example/team/widgets.git"
IDENTITY = "host.example/team/widgets"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def run(capsys, *argv) -> tuple[int, str]:
    capsys.readouterr()
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def run_json(capsys, *argv) -> tuple[int, dict]:
    code, text = run(capsys, "--json", *argv)
    return code, json.loads(text)


def porcelain(cwd: Path) -> str:
    return git(cwd, "status", "--porcelain", "--untracked-files=all")


def tree(path: Path) -> dict[str, bytes]:
    if not path.exists():
        return {}
    return {
        str(p.relative_to(path)): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()
    }


def make_repo(path: Path, origin: str | None = ORIGIN) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.invalid")
    git(path, "config", "user.name", "T")
    (path / "pyproject.toml").write_text("[project]\nname = 'widgets'\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "init")
    if origin:
        git(path, "remote", "add", "origin", origin)
    return path


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A scratch HOME holding nothing: init must leave it so."""
    scratch = tmp_path / "home"
    scratch.mkdir()
    monkeypatch.setenv("HOME", str(scratch))
    return scratch


@pytest.fixture
def repo(tmp_path, monkeypatch, home):
    root = make_repo(tmp_path / "widgets")
    monkeypatch.chdir(root)
    return root


def store_root() -> Path:
    return invariant_home() / "stores" / "widgets"


def block(store: Path) -> dict:
    body = yaml.safe_load((store / "invariant.yaml").read_text(encoding="utf-8"))
    return body["store"]["repos"][IDENTITY]


def doctor_rows(capsys) -> dict[str, dict]:
    _code, payload = run_json(capsys, "doctor")
    return {row["check"]: row for row in payload["checks"]}


class Keyed:
    """A prompter answering by question: the first scripted answer whose key
    the question contains, else the default. Records every question and the
    options it offered."""

    def __init__(self, **answers: list[str]) -> None:
        self.answers = {key.replace("_", " "): list(values) for key, values in answers.items()}
        self.asked: list[tuple[str, list[str] | None, str]] = []
        self.said: list[str] = []

    def _answer(self, question: str) -> str | None:
        for key, values in self.answers.items():
            if values and key in question:
                return values.pop(0)
        return None

    def say(self, text: str) -> None:
        self.said.append(text)

    def choose(self, question, options, default):
        keys = [key for key, _ in options]
        assert default in keys, (question, default, keys)
        self.asked.append((question, keys, default))
        answer = self._answer(question)
        if answer is None:
            return default
        assert answer in keys, (question, answer, keys)
        return answer

    def ask(self, question, default=""):
        self.asked.append((question, None, default))
        answer = self._answer(question)
        return answer or default

    def question(self, text: str) -> tuple[str, list[str] | None, str]:
        return next(entry for entry in self.asked if text in entry[0])


@pytest.fixture
def interactive(monkeypatch):
    def install(**answers: list[str]) -> Keyed:
        keyed = Keyed(**answers)
        monkeypatch.setattr(cli_main, "_init_prompter", lambda args: keyed)
        monkeypatch.setattr(cli_main, "_questionnaire_prompter", lambda args: keyed)
        return keyed

    return install


# -- D1: init with no terminal makes a store project ------------------------------


class TestTheDefault:
    def test_init_with_no_terminal_writes_nothing_in_the_repository(self, repo, home, capsys):
        code, text = run(capsys, "init")
        assert code == 0, text
        assert porcelain(repo) == ""
        assert not (repo / ".claude").exists()
        assert not (repo / ".gitignore").exists()
        assert not (repo / "invariant-docs").exists()
        assert tree(home) == {}
        # The runtime state is in the git common directory, untracked.
        assert (repo / ".git" / "invariant" / "state").is_dir()

    def test_it_creates_and_links_a_store_with_every_record_in_it(self, repo, capsys):
        code, text = run(capsys, "init")
        assert code == 0, text
        store = store_root()
        assert (store / ".git").is_dir()
        assert load_user_config().repos[IDENTITY]["store"] == str(store)
        assert block(store)["records"] == STORE_HOME_RECORDS
        assert STORE_HOME_RECORDS == {
            "docs": "store", "backlog": "store", "guidelines": "store", "memory": "store",
            "reports": "store", "sweeps": "store", "hooks": "local", "agents": "local",
        }
        m = Manifest.locate(repo)
        assert m.store is not None and m.store.root == store
        assert m.get("records.hooks") == "local"
        assert m.path_for("docs") == store / "invariant-docs" / "architecture"
        assert m.source_of("paths.docs") == f"store:widgets#{IDENTITY}"
        assert m.source_of("enforcement.profile") == "store:widgets"
        assert m.get("guidelines.ruleset") == str(
            store / "invariant-docs" / "guidelines" / "ruleset.yaml"
        )
        # The store's privacy screen is installed, as `store init` gives it.
        assert git(store, "config", "--local", "core.hooksPath")
        assert "privacy hooks installed" in text

    def test_the_starter_is_written_at_invariant_docs_in_the_store(self, repo, capsys):
        run(capsys, "init")
        src = store_root() / "invariant-docs"
        for rel in ("conf.py", "index.rst", "requirements.txt", "architecture/overview.rst",
                    "backlog.rst", "guidelines/ruleset.yaml"):
            assert (src / rel).is_file(), rel
        assert block(store_root())["docs"]["needs"] == {
            "state": "enforced", "srcdir": "invariant-docs"
        }

    def test_its_report_names_the_install_commands_and_the_home(self, repo, capsys):
        code, text = run(capsys, "init")
        assert code == 0, text
        assert "invariant hooks install" in text and "invariant agents install" in text
        assert "Nothing of invariant's is in the repository" in text
        assert f"store:widgets ({store_root()})" in text
        rows = text.split("configuration (core questions):\n", 1)[1].splitlines()
        assert rows[0].split()[0] == "home" and rows[0].split()[-1] == "default"
        assert "to commit the configuration instead: invariant store migrate --to repo" in text
        # The route a written run cannot take any more is not offered.
        assert "invariant init --home repo" not in text

    def test_json_carries_the_home(self, repo, capsys):
        code, payload = run_json(capsys, "init")
        assert code == 0
        assert payload["home"] == {
            "location": "store", "store": str(store_root()), "reason": None
        }
        first = payload["configuration"][0]
        assert first["id"] == "home:location" and first["key"] is None
        assert first["provenance"] == "default"
        assert payload["host_install"] == {
            "installed": False,
            "commands": ["invariant hooks install", "invariant agents install"],
        }

    def test_a_dry_run_names_the_store_and_writes_nothing(self, repo, home, capsys):
        code, text = run(capsys, "init", "--dry-run")
        assert code == 0, text
        assert f"create the store {store_root()} and link it as {IDENTITY}" in text
        assert f"write {store_root() / 'invariant-docs' / 'conf.py'}" in text
        assert "invariant init --home repo," in text
        assert "or later: invariant store migrate --to repo" in text
        assert "add the state root to .gitignore" not in text
        assert not store_root().exists()
        assert not user_config_path().exists()
        assert porcelain(repo) == ""
        found = subprocess.run(
            ["git", "config", "--local", "--get-regexp", "invariant"],
            cwd=repo, capture_output=True, text=True, check=False,
        )
        assert found.stdout == ""

    def test_a_dry_run_of_a_repository_with_no_origin_names_a_provisional_id(
        self, tmp_path, monkeypatch, capsys, home
    ):
        root = make_repo(tmp_path / "plain", origin=None)
        monkeypatch.chdir(root)
        code, payload = run_json(capsys, "init", "--dry-run")
        assert code == 0
        assert payload["home"]["store"] == str(invariant_home() / "stores" / "plain")
        assert repo_identity(root) is None

    def test_a_second_init_is_refused_naming_the_store(self, repo, capsys):
        run(capsys, "init")
        code, text = run(capsys, "init")
        assert code == 2
        assert str(store_root()) in text and "--force" in text

    def test_force_keeps_the_store_home_and_its_records(self, repo, capsys):
        run(capsys, "init")
        git(store_root(), "add", "-A")
        git(store_root(), "commit", "-qm", "init")
        before = block(store_root())
        code, text = run(capsys, "init", "--force")
        assert code == 0, text
        assert f"link the store {store_root()}" in text
        assert block(store_root()) == before
        assert not (repo / ".claude").exists()
        code, text = run(capsys, "init", "--force", "--home", "repo")
        assert code == 2 and "store migrate --to repo" in text

    def test_force_naming_another_store_is_refused(self, repo, tmp_path, capsys):
        run(capsys, "init")
        code, text = run(capsys, "init", "--force", "--store", str(tmp_path / "other"))
        assert code == 2 and "this project's store is" in text


# -- D9's question under the store home ------------------------------------------


class TestHostInstall:
    def test_host_install_wires_locally_and_git_status_stays_empty(self, repo, home, capsys):
        code, text = run(capsys, "init", "--host-install")
        assert code == 0, text
        settings = json.loads((repo / ".claude" / "settings.local.json").read_text())
        for host_event in EVENTS.values():
            ours = [
                hook
                for entry in settings["hooks"][host_event]
                for hook in entry["hooks"]
                if "invariant-" in hook["command"]
            ]
            assert len(ours) == 1, host_event
        assert settings["permissions"]["additionalDirectories"] == [str(store_root())]
        assert not (repo / ".claude" / "settings.json").exists()
        assert (repo / ".claude" / "agents" / "feature-architect.md").is_file()
        assert porcelain(repo) == ""
        assert tree(home) == {}

    def test_the_hooks_fire_once_as_the_host_calls_them(self, repo, capsys):
        run(capsys, "init", "--host-install")
        host = ClaudeCodeHost(repo, manifest=Manifest.locate(repo))
        _label, ok, detail = host.hooks_run_check()
        assert ok, detail


# -- the docs lane, start to merge --------------------------------------------------


class _Stdin:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text

    def isatty(self) -> bool:
        return False


class TestADocsLaneSession:
    def test_runs_from_start_to_merge_and_the_repository_holds_only_its_commit(
        self, repo, capsys, monkeypatch
    ):
        code, text = run(capsys, "init")
        assert code == 0, text
        store = store_root()
        git(store, "config", "user.email", "t@example.invalid")
        git(store, "config", "user.name", "T")
        git(store, "add", "-A")
        git(store, "commit", "-qm", "invariant init")
        before = git(repo, "rev-parse", "HEAD")

        sid = "d0c5a001"
        code, text = run(capsys, "session", "start", "a-doc", "--id", sid)
        assert code == 0, text
        code, text = run(capsys, "session", "adopt", "host-widgets", "--id", sid)
        assert code == 0, text
        code, text = run(capsys, "session", "isolate", "--id", sid, "--execute")
        assert code == 0, text
        worktree = repo.parent / f"widgets-wt-{sid}"
        assert porcelain(worktree) == ""
        monkeypatch.chdir(worktree)
        code, text = run(
            capsys, "pipeline", "start", "--methodology", "docs", "--task", "a doc", "--id", sid
        )
        assert code == 0, text

        overview = store / "invariant-docs" / "architecture" / "overview.rst"

        def hook(event: str, payload: dict) -> None:
            body = {"session_id": "host-widgets", **payload}
            monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
            monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(body)))
            code, text = run(capsys, "hook", event)
            assert code == 0, text

        read = {"tool_name": "Read", "tool_input": {"file_path": str(overview)}}
        hook("pre-tool-use", read)
        hook("post-tool-use", read)
        overview.write_text(
            overview.read_text(encoding="utf-8") + "\nA sentence the session adds.\n",
            encoding="utf-8",
        )
        git(store, "commit", "-qam", "docs: a sentence")
        code, text = run(capsys, "docs", "unlock", "--id", sid)
        assert code == 0, text
        (worktree / "README.md").write_text("widgets\n", encoding="utf-8")
        git(worktree, "add", "README.md")
        git(worktree, "commit", "-qm", "readme")

        for state in ("reviewing", "committed"):
            code, text = run(
                capsys, "pipeline", "advance", state, "--id", sid,
                "--force", "--reason", "test fixture: no sphinx-build here",
            )
            assert code == 0, text
        for state in ("working", "reviewed"):
            code, text = run(
                capsys, "session", "transition", state, "--id", sid,
                "--force", "--reason", "test fixture",
            )
            assert code == 0, text
        monkeypatch.chdir(repo)
        code, text = run(capsys, "session", "merge", "--id", sid)
        assert code == 0, text
        assert git(repo, "rev-list", f"{before}..HEAD") == git(repo, "rev-parse", "HEAD")
        assert git(repo, "log", "-1", "--format=%s") == "readme"
        assert sorted(git(repo, "ls-files").splitlines()) == ["README.md", "pyproject.toml"]
        assert porcelain(repo) == ""
        assert "A sentence the session adds." in git(store, "show", "HEAD:" + str(
            overview.relative_to(store)
        ))


# -- --home and --store -----------------------------------------------------------


class TestTheFlags:
    def test_home_repo_writes_the_in_repo_layout(self, repo, capsys):
        code, payload = run_json(capsys, "init", "--home", "repo")
        assert code == 0
        assert (repo / MANIFEST_RELPATH).is_file()
        assert (repo / "invariant-docs" / "conf.py").is_file()
        assert ".claude/invariant/state/" in (repo / ".gitignore").read_text()
        assert not store_root().exists()
        assert not user_config_path().exists()
        assert payload["home"] == {"location": "repo", "store": None, "reason": None}
        assert payload["configuration"][0]["provenance"] == "--home"
        assert payload["configuration"][0]["value"] == "repo (.claude/invariant.yaml)"
        assert Manifest.locate(repo).store is None

    def test_the_in_repo_body_is_the_one_init_always_wrote(self, repo, capsys):
        run(capsys, "init", "--home", "repo", "--name", "widgets")
        body = (repo / MANIFEST_RELPATH).read_text(encoding="utf-8")
        assert body.startswith("version: 1\n\nproject:\n  name: widgets\n  ecosystems: [python]\n")
        assert "enforcement:\n  profile: standard\n" in body
        assert "records" not in body

    def test_store_names_the_path(self, repo, tmp_path, capsys):
        target = tmp_path / "elsewhere"
        code, payload = run_json(capsys, "init", "--store", str(target))
        assert code == 0
        assert payload["home"]["store"] == str(target)
        assert payload["configuration"][0]["provenance"] == "--store"
        assert Manifest.locate(repo).store.root == target

    def test_store_naming_a_store_links_it(self, repo, tmp_path, capsys):
        target = tmp_path / "shared"
        run(capsys, "init", "--store", str(target))
        git(target, "add", "-A")
        git(target, "commit", "-qm", "store")
        # Forget the link: a second clone of the repository on this machine.
        user_config_path().unlink()
        code, text = run(capsys, "init", "--store", str(target))
        assert code == 0, text
        assert f"link the store {target} as {IDENTITY}" in text

    @pytest.mark.parametrize(
        "argv",
        [
            ("--home", "repo", "--store", "x"),
            ("--store", "occupied"),
        ],
    )
    def test_a_refused_flag_exits_2_writing_nothing(self, repo, tmp_path, home, capsys, argv):
        occupied = repo.parent / "occupied"
        occupied.mkdir()
        (occupied / "file").write_text("x", encoding="utf-8")
        argv = tuple(str(occupied) if a == "occupied" else a for a in argv)
        code, text = run(capsys, "init", *argv)
        assert code == 2, text
        assert porcelain(repo) == ""
        assert not (invariant_home() / "stores").exists()
        assert not user_config_path().exists()

    def test_force_on_an_in_repo_project_keeps_the_repository_home(self, repo, home, capsys):
        """D2, the shape of every project initialised before the switch: a
        committed in-repo manifest in a git repository with an origin. ``init
        --force`` with no terminal keeps it in the repository; no store is
        made or linked, and ``--home store`` is refused naming the move."""
        run(capsys, "init", "--home", "repo")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "invariant init")
        code, payload = run_json(capsys, "init", "--force")
        assert code == 0
        assert payload["home"]["location"] == "repo"
        assert payload["configuration"][0]["provenance"] == "kept"
        assert "store migrate --to store" in payload["home"]["reason"]
        assert (repo / MANIFEST_RELPATH).is_file()
        assert Manifest.locate(repo).store is None
        assert not (invariant_home() / "stores").exists()
        assert not user_config_path().exists()
        assert tree(home) == {}
        code, payload = run_json(capsys, "init", "--force", "--dry-run")
        assert code == 0 and payload["home"]["location"] == "repo"
        code, text = run(capsys, "init", "--force", "--home", "store")
        assert code == 2 and "store migrate --to store" in text
        code, text = run(capsys, "init", "--force", "--store", str(repo.parent / "s"))
        assert code == 2
        assert not (invariant_home() / "stores").exists()
        assert not (repo.parent / "s").exists()
        assert Manifest.locate(repo).store is None

    def test_store_home_below_the_top_level_is_refused(self, repo, monkeypatch, capsys):
        sub = repo / "sub"
        sub.mkdir()
        monkeypatch.chdir(sub)
        code, text = run(capsys, "init", "--home", "store")
        assert code == 2
        assert "not the top level" in text
        assert not (sub / ".claude").exists()


class TestNoStoreHere:
    def test_outside_git_init_takes_repo_and_names_the_reason(
        self, tmp_path, monkeypatch, home, capsys
    ):
        plain = tmp_path / "plain"
        plain.mkdir()
        monkeypatch.chdir(plain)
        code, payload = run_json(capsys, "init")
        assert code == 0
        assert (plain / MANIFEST_RELPATH).is_file()
        assert payload["home"]["location"] == "repo"
        assert "not inside a git repository" in payload["home"]["reason"]
        code, text = run(capsys, "init", "--force")
        assert "a store is not offered as the home" in text

    def test_outside_git_store_is_refused(self, tmp_path, monkeypatch, capsys):
        plain = tmp_path / "plain"
        plain.mkdir()
        monkeypatch.chdir(plain)
        code, text = run(capsys, "init", "--home", "store")
        assert code == 2
        assert "the store home is not offered here" in text
        assert not (plain / ".claude").exists()

    def test_below_the_top_level_init_takes_repo(self, repo, monkeypatch, capsys):
        sub = repo / "sub"
        sub.mkdir()
        monkeypatch.chdir(sub)
        code, payload = run_json(capsys, "init")
        assert code == 0
        assert payload["home"]["location"] == "repo"
        assert "not the top level" in payload["home"]["reason"]


# -- a documentation repository whose root conf.py reads invariant-docs/ ---------


@pytest.fixture
def content_repo(tmp_path, monkeypatch, home):
    root = make_repo(tmp_path / "widgets")
    (root / "conf.py").write_text("project = 'manual'\nextensions = []\n", encoding="utf-8")
    (root / "index.rst").write_text("Manual\n======\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "content")
    monkeypatch.chdir(root)
    return root


class TestARootConfPy:
    def test_repo_is_not_offered_and_the_store_is_taken(self, content_repo, capsys):
        code, payload = run_json(capsys, "init")
        assert code == 0
        assert payload["home"]["location"] == "store"
        assert "exclude_patterns" in payload["home"]["reason"]
        assert porcelain(content_repo) == ""

    def test_home_repo_is_refused_naming_the_line(self, content_repo, capsys):
        code, text = run(capsys, "init", "--home", "repo")
        assert code == 2
        assert '"invariant-docs"' in text and "conf.py" in text
        assert not (content_repo / ".claude").exists()

    def test_doctor_names_the_exclude_line_until_it_is_added(self, content_repo, capsys):
        run(capsys, "init")
        row = doctor_rows(capsys)["docs folder"]
        assert row["ok"] and row["warn"]
        assert 'exclude_patterns = [..., "invariant-docs"]' in row["detail"]
        assert "stay in the store" in row["detail"]
        conf = content_repo / "conf.py"
        conf.write_text(conf.read_text() + "exclude_patterns = ['invariant-docs']\n")
        row = doctor_rows(capsys)["docs folder"]
        assert row["ok"] and not row["warn"]

    def test_an_excluding_conf_py_offers_repo(self, content_repo, capsys):
        conf = content_repo / "conf.py"
        conf.write_text(conf.read_text() + "exclude_patterns = ['invariant-docs/**']\n")
        code, text = run(capsys, "init", "--home", "repo")
        assert code == 0, text
        assert (content_repo / MANIFEST_RELPATH).is_file()

    def test_no_row_without_a_conf_py_above_the_docs_project(self, repo, capsys):
        run(capsys, "init")
        assert "docs folder" not in doctor_rows(capsys)


# -- D5: doctor's config home ------------------------------------------------------


class TestConfigHome:
    def test_a_store_project_names_its_store_and_this_machine_only(self, repo, capsys):
        run(capsys, "init")
        row = doctor_rows(capsys)["config home"]
        assert row["ok"] and not row["warn"]
        assert f"store widgets at {store_root()}" in row["detail"]
        assert "store clone" in row["detail"]
        assert "store migrate --to repo" in row["detail"]
        assert "this machine only" in row["detail"]

    def test_a_store_with_a_remote_is_not_this_machine_only(self, repo, tmp_path, capsys):
        run(capsys, "init")
        bare = tmp_path / "store.git"
        git(tmp_path, "init", "-q", "--bare", str(bare))
        git(store_root(), "remote", "add", "origin", str(bare))
        assert "this machine only" not in doctor_rows(capsys)["config home"]["detail"]

    def test_two_homes_warn_naming_both_and_the_one_used(self, repo, capsys):
        run(capsys, "init")
        (repo / ".claude").mkdir()
        (repo / MANIFEST_RELPATH).write_text("version: 1\n", encoding="utf-8")
        git(repo, "add", str(MANIFEST_RELPATH))
        git(repo, "commit", "-qm", "manifest")
        row = doctor_rows(capsys)["config home"]
        assert row["ok"] and row["warn"]
        assert "two homes" in row["detail"]
        assert str(store_root()) in row["detail"]
        assert f"repos.{IDENTITY}" in row["detail"]
        assert "Manifest.locate uses .claude/invariant.yaml" in row["detail"]
        assert "retire the one not in use" in row["detail"]

    def test_two_homes_name_a_half_landed_migration(self, repo, capsys):
        run(capsys, "init")
        (repo / ".claude").mkdir()
        (repo / MANIFEST_RELPATH).write_text("version: 1\n", encoding="utf-8")
        state = repo / ".git" / "invariant" / "state"
        (state / MARKER).write_text(
            json.dumps({"session": "abc12345", "direction": "repo", "worktree": "/w"}),
            encoding="utf-8",
        )
        detail = doctor_rows(capsys)["config home"]["detail"]
        assert "abc12345" in detail

    def test_a_link_that_does_not_resolve_is_a_second_home_too(self, repo, capsys):
        run(capsys, "init")
        shutil.rmtree(store_root())
        (repo / ".claude").mkdir()
        (repo / MANIFEST_RELPATH).write_text("version: 1\n", encoding="utf-8")
        detail = doctor_rows(capsys)["config home"]["detail"]
        assert "does not resolve" in detail

    def test_one_in_repo_home_is_unchanged(self, repo, capsys):
        run(capsys, "init", "--home", "repo")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "adopt")
        row = doctor_rows(capsys)["config home"]
        assert row["detail"] == (
            "repo: .claude/invariant.yaml, committed (shared through the repository)"
        )


# -- D4: interactive init asks the home first ----------------------------------------


class TestInteractive:
    def test_the_home_is_asked_first_and_the_store_is_the_default(
        self, repo, interactive, capsys
    ):
        keyed = interactive()
        code, text = run(capsys, "init")
        assert code == 0, text
        first, options, default = keyed.asked[0]
        assert first == cli_main.HOME_QUESTION
        assert options == ["store", "repo"] and default == "store"
        assert "store path" in keyed.asked[1][0]
        level = keyed.question("Which level")
        assert level[1] == ["user", "store", "store-repo"] and level[2] == "store-repo"
        assert Manifest.locate(repo).store.root == store_root()
        assert porcelain(repo) == ""

    def test_answers_at_the_block_and_the_store_level_land_in_the_store(
        self, repo, interactive, capsys
    ):
        interactive(profile=["strict"])
        code, text = run(capsys, "init")
        assert code == 0, text
        assert block(store_root())["enforcement"] == {"profile": "strict"}
        interactive(Which_level=["store"], profile=["loose"])
        user_config_path().unlink()
        shutil.rmtree(store_root())
        code, text = run(capsys, "init")
        assert code == 0, text
        body = yaml.safe_load((store_root() / "invariant.yaml").read_text())
        assert body["enforcement"] == {"profile": "loose"}
        assert "to the store's own level" in text

    def test_a_store_path_answer_is_checked_and_asked_again(
        self, repo, interactive, tmp_path, capsys
    ):
        occupied = tmp_path / "occupied"
        occupied.mkdir()
        (occupied / "f").write_text("x")
        target = tmp_path / "chosen"
        keyed = interactive(store_path=[str(occupied), str(target)])
        code, text = run(capsys, "init")
        assert code == 0, text
        assert any("refused" in line for line in keyed.said)
        assert Manifest.locate(repo).store.root == target

    def test_choosing_repo_writes_the_in_repo_layout(self, repo, interactive, capsys):
        keyed = interactive(Where_does=["repo"])
        code, text = run(capsys, "init")
        assert code == 0, text
        assert (repo / MANIFEST_RELPATH).is_file()
        level = keyed.question("Which level")
        assert level[1] == ["user", "repo"] and level[2] == "repo"
        assert not store_root().exists()

    def test_outside_git_the_home_is_said_not_asked(
        self, tmp_path, monkeypatch, interactive, capsys
    ):
        plain = tmp_path / "plain"
        plain.mkdir()
        monkeypatch.chdir(plain)
        keyed = interactive()
        code, text = run(capsys, "init")
        assert code == 0, text
        assert cli_main.HOME_QUESTION not in [q for q, _o, _d in keyed.asked]
        assert any("store is not offered" in line for line in keyed.said)

    def test_the_docs_location_is_offered_in_the_store_where_it_exists(
        self, repo, interactive, tmp_path, capsys
    ):
        target = tmp_path / "shared"
        run(capsys, "init", "--store", str(target))
        git(target, "add", "-A")
        git(target, "commit", "-qm", "store")
        user_config_path().unlink()
        body = yaml.safe_load((target / "invariant.yaml").read_text())
        body["store"]["repos"][IDENTITY] = {}
        (target / "invariant.yaml").write_text(yaml.safe_dump(body))
        keyed = interactive(store_path=[str(target)])
        code, text = run(capsys, "init")
        assert code == 0, text
        _question, options, default = keyed.question("Where should invariant's architecture docs")
        assert options == ["reuse", "path"] and default == "reuse"
        assert any("invariant-docs/architecture, exists" in line for line in keyed.said)

    def test_under_repo_the_docs_location_offers_no_store(
        self, repo, interactive, capsys
    ):
        (repo / "invariant-docs" / "architecture").mkdir(parents=True)
        keyed = interactive(Where_does=["repo"])
        code, text = run(capsys, "init")
        assert code == 0, text
        _q, options, _d = keyed.question("Where should invariant's architecture docs")
        assert options == ["reuse", "path"]


# -- the questionnaire's home step ----------------------------------------------------


class TestTheCatalogue:
    def test_home_is_the_first_core_section(self):
        catalogue = questionnaire.load_catalogue()
        assert catalogue.core[0] == "home"
        assert catalogue.sections["home"].questions[0].kind == "step"
        assert "home" in questionnaire.STEPS

    def test_home_location_resolves_where_the_configuration_lives(self, tmp_path):
        m = Manifest.from_raw({}, root=tmp_path, path=tmp_path / MANIFEST_RELPATH, env={})
        question = questionnaire.home_question(m)
        assert question.id == "home:location" and question.key is None
        assert question.choices == ("store", "repo")
        assert question.default == "repo"
        assert questionnaire.home_of(m) == "repo (.claude/invariant.yaml)"
        catalogue = questionnaire.load_catalogue()
        resolved = questionnaire.resolve(catalogue, m, sections=["home"])
        assert [q.id for q in resolved] == ["home:location"]

    def test_a_store_project_resolves_to_its_store(self, repo, capsys):
        run(capsys, "init")
        m = Manifest.locate(repo)
        question = questionnaire.home_question(m)
        assert question.default == "store" and question.provenance == "store:widgets"
        assert questionnaire.home_of(m) == f"store:widgets ({store_root()})"

    def test_the_report_names_a_keyless_row_by_its_section(self, tmp_path):
        m = Manifest.from_raw({}, root=tmp_path, path=tmp_path / MANIFEST_RELPATH, env={})
        text = questionnaire.render_report(
            [questionnaire.home_question(m)], ["a line between"]
        )
        assert text.splitlines()[1].split()[0] == "home"
        assert text.splitlines()[2] == "a line between"


# -- the store planned before anything is written ------------------------------------


class TestPlanHomeStore:
    def test_the_default_is_created_under_the_invariant_home(self, repo):
        plan = plan_home_store(repo)
        assert plan.root == store_root() and not plan.link
        assert plan.key == IDENTITY

    def test_a_default_store_listing_the_repository_is_linked(self, repo, capsys):
        run(capsys, "store", "init")
        assert plan_home_store(repo).link

    def test_a_taken_name_falls_back_and_says_so(self, repo):
        store_root().mkdir(parents=True)
        (store_root() / "x").write_text("x")
        plan = plan_home_store(repo)
        assert plan.root.name == IDENTITY.replace("/", "_")
        assert "already exists for another repository" in plan.note

    def test_a_store_not_listing_the_repository_is_refused(self, repo, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        (other / "invariant.yaml").write_text(
            yaml.safe_dump({"version": 1, "store": {"repos": {"someone/else": {}}}})
        )
        with pytest.raises(ManifestError, match="does not list this repository"):
            plan_home_store(repo, other)

    def test_no_free_name_is_refused(self, repo):
        for name in ("widgets", IDENTITY.replace("/", "_")):
            (invariant_home() / "stores" / name).mkdir(parents=True)
            (invariant_home() / "stores" / name / "x").write_text("x")
        with pytest.raises(ManifestError, match="no store name is free"):
            plan_home_store(repo)

    def test_url_naming_from_the_user_level(self, repo):
        path = user_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("version: 1\nstore_naming: url\n", encoding="utf-8")
        assert plan_home_store(repo).root.name == IDENTITY.replace("/", "_")


# -- the strict build of the starter in the store -------------------------------------


@pytest.mark.skipif(
    not os.environ.get("INVARIANT_TEST_SPHINX_BUILD") or shutil.which("uv") is None,
    reason="set INVARIANT_TEST_SPHINX_BUILD (and have uv) to build the starter strictly",
)
def test_the_starter_in_the_store_builds_strictly(repo, capsys, monkeypatch):
    monkeypatch.setattr(docs_domain, "_uv", lambda: shutil.which("uv"))
    code, text = run(capsys, "init")
    assert code == 0, text
    code, text = run(capsys, "docs", "venv")
    assert code == 0, text
    code, text = run(capsys, "--json", "docs", "build")
    record = json.loads(text)
    assert code == 0 and record["passed"], record
    assert porcelain(repo) == ""
