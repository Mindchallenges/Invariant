"""ADR-016 increment 2: init's docs offers, `docs assess --plan`, `docs.onboarding`.

D4: interactive `init` offers, where `paths.docs` exists, to reuse it, to point
`paths.docs` at another repo path, or to use a store (ADR-014 `store init` or
`store link`), never touching the existing directory; then it assesses the
chosen location and, where there are gaps, offers all at once, incremental or
not now. Non-interactive `init` (`--dry-run`, `--json`, no terminal) writes
today's files, prompts for nothing and prints the assessment (ruling H).

D5 and ruling 9: all at once is a deliverable plan, one line per undocumented
area plus a missing-sections line, recorded by `docs assess --plan` on a
parked `docs`-lane session, and only once the base carries the manifest.

D9 and ruling 11: `docs.onboarding` is `off`, `incremental` or `all_at_once`,
refused by name otherwise, at every level but the pointer; an unknown `docs`
key is refused too.

The interactive path is driven through `_init_prompter`, the seam a terminal
answers in real use, so no test fakes a terminal globally.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from invariant.cli import main as cli_main
from invariant.cli import prompts
from invariant.cli.main import main
from invariant.core.errors import ManifestError
from invariant.core.home import user_config_path
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.core.store import JsonStore
from invariant.core.stores import POINTER, read_pointer, write_store_settings
from invariant.domains.docs import Area, Assessment, Gap, plan_deliverables
from invariant.domains.pipeline import PipelineDomain
from invariant.domains.session import SessionDomain

#: What init writes today for `--ecosystem generic --name toy`, byte for byte.
TODAYS_MANIFEST = (
    "version: 1\n"
    "\n"
    "project:\n"
    "  name: toy\n"
    "  ecosystems: [generic]\n"
    "\n"
    "enforcement:\n"
    "  profile: standard\n"
    "\n"
    "# Everything else has a default. 'invariant config show --provenance'\n"
    "# prints the resolved configuration and where each value came from.\n"
)

#: Names alpha and nothing else, with all three starter sections.
HALF = (
    "# Toy\n\n## Layers\n\nThe alpha package does things.\n\n"
    "## Component responsibilities\n\nalpha owns the work.\n\n"
    "## Compliance checklist\n\n- none\n"
)


def git(root: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", *argv], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def tree(root: Path) -> dict[str, str]:
    """Every file under ``root`` and the SHA-256 of its bytes."""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def run(capsys, *argv) -> tuple[int, str]:
    capsys.readouterr()
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


class Scripted:
    """A prompter answering from a script, recording every question."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.asked: list[tuple[str, list[str]]] = []
        self.said: list[str] = []

    def say(self, text: str) -> None:
        self.said.append(text)

    def choose(self, question, options, default):
        keys = [key for key, _ in options]
        assert default in keys
        self.asked.append((question, keys))
        answer = self.answers.pop(0)
        assert answer in keys, (answer, keys)
        return answer

    def ask(self, question, default=""):
        self.asked.append((question, []))
        answer = self.answers.pop(0)
        return answer or default


class Refusing:
    """A prompter that fails the test if init asks it anything."""

    def say(self, text):  # pragma: no cover - reaching it is the failure
        raise AssertionError(f"non-interactive init said {text!r}")

    choose = ask = say


@pytest.fixture
def toy(tmp_path, monkeypatch):
    """A git repo with two source areas, alpha and beta, and no docs yet."""
    root = tmp_path / "toy"
    for area in ("alpha", "beta"):
        (root / area).mkdir(parents=True)
        (root / area / "mod.py").write_text("x = 1\n", encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def unrelated(toy):
    """`paths.docs` exists and holds something that is not architecture."""
    docs = toy / "docs" / "architecture"
    docs.mkdir(parents=True)
    (docs / "notes.txt").write_text("shopping list\n", encoding="utf-8")
    (docs / "photo.md").write_text("# Holiday\n", encoding="utf-8")
    return toy


@pytest.fixture
def half(toy):
    """Docs that name alpha and not beta."""
    docs = toy / "docs" / "architecture"
    docs.mkdir(parents=True)
    (docs / "overview.md").write_text(HALF, encoding="utf-8")
    git(toy, "add", "-A")
    git(toy, "commit", "-qm", "docs")
    return toy


@pytest.fixture
def interactive(monkeypatch):
    """Answer init's questions from a script, through the prompter seam."""

    def install(*answers: str) -> Scripted:
        scripted = Scripted(*answers)
        monkeypatch.setattr(cli_main, "_init_prompter", lambda args: scripted)
        return scripted

    return install


def init(capsys, *extra: str) -> tuple[int, str]:
    return run(capsys, "init", "--ecosystem", "generic", "--name", "toy", *extra)


def area_gaps(assessment: dict) -> list[str]:
    return [gap["subject"] for gap in assessment["checks"]["areas"]]


def parked(root: Path) -> list:
    manifest = Manifest.locate(root)
    return SessionDomain(manifest, JsonStore(manifest.state_root)).resumable(cwd=root)


# -- D9: docs.onboarding and the docs keys ---------------------------------


class TestOnboardingKey:
    def load(self, tmp_path, docs):
        return Manifest.from_raw(
            {"docs": docs}, root=tmp_path, path=tmp_path / "m.yaml", env={}
        )

    def test_the_default_is_off(self, tmp_path):
        assert self.load(tmp_path, {}).get("docs.onboarding") == "off"

    @pytest.mark.parametrize("mode", ["off", "incremental", "all_at_once"])
    def test_each_mode_loads(self, tmp_path, mode):
        assert self.load(tmp_path, {"onboarding": mode}).get("docs.onboarding") == mode

    def test_yaml_spelling_off_unquoted_reads_as_off(self, tmp_path):
        raw = yaml.safe_load("docs:\n  onboarding: off\n")
        assert raw["docs"]["onboarding"] is False
        m = Manifest.from_raw(raw, root=tmp_path, path=tmp_path / "m.yaml", env={})
        assert m.get("docs.onboarding") == "off"

    @pytest.mark.parametrize("value", ["sometimes", True, 3, "", None, ["incremental"]])
    def test_an_unknown_mode_is_refused_naming_it(self, tmp_path, value):
        with pytest.raises(ManifestError) as exc:
            self.load(tmp_path, {"onboarding": value})
        assert "docs.onboarding" in str(exc.value)
        assert "all_at_once" in str(exc.value)

    def test_an_unknown_docs_key_is_refused_naming_it(self, tmp_path):
        with pytest.raises(ManifestError) as exc:
            self.load(tmp_path, {"colour": "blue"})
        assert "docs.colour" in str(exc.value)

    def test_adopt_is_read_since_increment_4(self, tmp_path):
        docs = {"adopt": {"mode": "copy", "source": "handbook"}}
        assert self.load(tmp_path, docs).get("docs.adopt.mode") == "copy"

    def test_every_key_a_project_sets_today_still_loads(self, tmp_path):
        docs = {
            "format": "sphinx",
            "diagrams": "plantuml",
            "index": "docs/index.rst",
            "require_diagram_update": True,
            "assess": {"checks": []},
            "onboarding": "incremental",
        }
        assert self.load(tmp_path, docs).get("docs.format") == "sphinx"

    def test_docs_must_be_a_mapping(self, tmp_path):
        with pytest.raises(ManifestError) as exc:
            self.load(tmp_path, "everything")
        assert "docs" in str(exc.value)


class TestOnboardingLevels:
    """Ruling 11: user defaults, the store and an in-repo manifest; never the pointer."""

    def test_from_the_user_level_with_its_label(self, toy):
        user_config_path().parent.mkdir(parents=True, exist_ok=True)
        user_config_path().write_text(
            "version: 1\ndefaults:\n  docs:\n    onboarding: incremental\n",
            encoding="utf-8",
        )
        (toy / MANIFEST_RELPATH).parent.mkdir(parents=True)
        (toy / MANIFEST_RELPATH).write_text("version: 1\n", encoding="utf-8")
        m = Manifest.locate(toy)
        assert m.get("docs.onboarding") == "incremental"
        assert m.source_of("docs.onboarding").startswith("user:")

    def test_an_unknown_mode_at_the_user_level_is_refused(self, toy):
        user_config_path().parent.mkdir(parents=True, exist_ok=True)
        user_config_path().write_text(
            "version: 1\ndefaults:\n  docs:\n    onboarding: later\n", encoding="utf-8"
        )
        (toy / MANIFEST_RELPATH).parent.mkdir(parents=True)
        (toy / MANIFEST_RELPATH).write_text("version: 1\n", encoding="utf-8")
        with pytest.raises(ManifestError) as exc:
            Manifest.locate(toy)
        assert "later" in str(exc.value)

    def test_the_in_repo_manifest_wins(self, toy):
        (toy / MANIFEST_RELPATH).parent.mkdir(parents=True)
        (toy / MANIFEST_RELPATH).write_text(
            "version: 1\ndocs:\n  onboarding: all_at_once\n", encoding="utf-8"
        )
        m = Manifest.locate(toy)
        assert m.get("docs.onboarding") == "all_at_once"
        assert m.source_of("docs.onboarding") == f"repo:{MANIFEST_RELPATH}"

    def test_from_the_store_level(self, toy, capsys):
        assert run(capsys, "store", "init")[0] == 0
        m = Manifest.locate(toy)
        write_store_settings(m.store.root, m.store.key, repo={"docs": {"onboarding": "incremental"}})
        m = Manifest.locate(toy)
        assert m.get("docs.onboarding") == "incremental"
        assert m.source_of("docs.onboarding").startswith("store:")

    def test_the_pointer_refuses_it(self, toy):
        (toy / POINTER).write_text(
            "version: 1\ndocs:\n  onboarding: incremental\n", encoding="utf-8"
        )
        with pytest.raises(ManifestError) as exc:
            read_pointer(toy)
        assert "docs" in str(exc.value)


# -- the store write init uses ---------------------------------------------


class TestWriteStoreSettings:
    def test_merges_the_top_level_and_the_repo_block(self, toy, capsys):
        assert run(capsys, "store", "init")[0] == 0
        m = Manifest.locate(toy)
        write_store_settings(
            m.store.root,
            m.store.key,
            top={"project": {"ecosystems": ["generic"]}},
            repo={"records": {"docs": "store"}, "paths": {"docs": "arch"}},
        )
        write_store_settings(m.store.root, m.store.key, repo={"paths": {"memory": "mem"}})
        body = yaml.safe_load((m.store.root / "invariant.yaml").read_text(encoding="utf-8"))
        assert body["project"] == {"name": "toy", "ecosystems": ["generic"]}
        assert body["store"]["repos"][m.store.key] == {
            "records": {"docs": "store"},
            "paths": {"docs": "arch", "memory": "mem"},
        }

    def test_an_uncommented_store_is_rewritten_and_says_so(self, toy, capsys):
        assert run(capsys, "store", "init")[0] == 0
        m = Manifest.locate(toy)
        result = write_store_settings(m.store.root, m.store.key, repo={"paths": {"docs": "a"}})
        assert result.written is True
        assert result.by_hand is None
        assert result.path == m.store.root / "invariant.yaml"

    @pytest.mark.parametrize(
        "comment",
        ["# reviewed by the team\n", "version: 1  # pinned\n"],
    )
    def test_a_store_holding_a_comment_is_not_written(self, toy, capsys, comment):
        assert run(capsys, "store", "init")[0] == 0
        m = Manifest.locate(toy)
        path = m.store.root / "invariant.yaml"
        path.write_text(comment + path.read_text(encoding="utf-8"), encoding="utf-8")
        before = path.read_bytes()
        result = write_store_settings(
            m.store.root,
            m.store.key,
            top={"project": {"ecosystems": ["generic"]}},
            repo={"records": {"docs": "store"}},
        )
        assert path.read_bytes() == before
        assert result.written is False
        assert "comment" in result.reason
        assert yaml.safe_load(result.by_hand) == {
            "project": {"ecosystems": ["generic"]},
            "store": {"repos": {m.store.key: {"records": {"docs": "store"}}}},
        }

    def test_a_repo_the_store_does_not_list_is_refused(self, toy, capsys, tmp_path):
        assert run(capsys, "store", "init")[0] == 0
        m = Manifest.locate(toy)
        before = (m.store.root / "invariant.yaml").read_text(encoding="utf-8")
        with pytest.raises(ManifestError) as exc:
            write_store_settings(m.store.root, "host.example/someone/else", repo={"x": 1})
        assert "host.example/someone/else" in str(exc.value)
        assert (m.store.root / "invariant.yaml").read_text(encoding="utf-8") == before


# -- D5: the plan ------------------------------------------------------------


def _assessment(gaps, areas=()) -> Assessment:
    return Assessment(
        location="docs/architecture", format="markdown", present=True, documents=1,
        checks=("sections", "areas", "links"), off=(), areas=tuple(areas),
        gaps=tuple(gaps), digest="0" * 12,
    )


class TestPlanDeliverables:
    def test_missing_sections_first_then_one_line_per_area(self):
        areas = [Area("alpha", "pkg/alpha"), Area("beta", "pkg/beta")]
        gaps = [
            Gap("sections", "Layers", "found in no doc"),
            Gap("sections", "Compliance checklist", "found in no doc"),
            Gap("areas", "pkg/alpha", "alpha is named in no doc"),
            Gap("areas", "pkg/beta", "beta is named in no doc"),
            Gap("links", "overview.md:3", "gone.md does not exist"),
        ]
        assert plan_deliverables(_assessment(gaps, areas)) == [
            "Write the missing sections: Layers, Compliance checklist",
            "Document pkg/alpha",
            "Document pkg/beta",
        ]

    def test_no_gaps_is_no_plan(self):
        assert plan_deliverables(_assessment([])) == []

    def test_gaps_no_document_can_close_are_not_planned(self):
        gaps = [
            Gap("sections", "not assessable", "no reader for sphinx"),
            Gap("areas", "generic", "areas cannot be derived; set ..."),
        ]
        assert plan_deliverables(_assessment(gaps)) == []


# -- a parked session ------------------------------------------------------


class TestParkedStart:
    def test_parked_binds_no_host_and_waits_to_be_resumed(self, toy, monkeypatch):
        (toy / MANIFEST_RELPATH).parent.mkdir(parents=True)
        (toy / MANIFEST_RELPATH).write_text("version: 1\n", encoding="utf-8")
        monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", "host-abc")
        monkeypatch.setenv("INVARIANT_SESSION_ID", "deadbeef")
        m = Manifest.locate(toy)
        sessions = SessionDomain(m, JsonStore(m.state_root))
        session = sessions.start("document-architecture", parked=True)
        assert session.id != "deadbeef"
        assert session.host_session_id is None
        assert session.host_ended is True
        assert [s.id for s in sessions.resumable(cwd=toy)] == [session.id]
        assert sessions.by_host_session("host-abc") is None


# -- D5 and ruling 9: docs assess --plan -------------------------------------


@pytest.fixture
def adopted(half, capsys):
    """An in-repo project whose manifest is committed, documenting half its areas."""
    assert init(capsys)[0] == 0
    git(half, "add", "-A")
    git(half, "commit", "-qm", "adopt invariant")
    return half


class TestAssessPlan:
    def test_records_a_parked_docs_lane_session_with_one_line_per_area(
        self, adopted, capsys
    ):
        code, text = run(capsys, "docs", "assess", "--plan")
        assert code == 0, text
        (session,) = parked(adopted)
        titles = [entry["title"] for entry in session.plan]
        assert "Document beta" in titles
        assert "Document alpha" not in titles
        assert not any(t.startswith("Write the missing sections") for t in titles)
        manifest = Manifest.locate(adopted)
        pipeline = PipelineDomain(manifest, JsonStore(manifest.state_root)).current(session.id)
        assert pipeline.methodology == "docs"
        assert pipeline.state == "drafting"
        assert f"invariant session resume {session.name}" in text
        assert "Document beta" in text
        # A host binds the caller by parsing `session <id> (` from stdout; a
        # parked plan must never be claimed by the command that records it.
        assert cli_main.re.search(r"session\s+[0-9a-z]+\s+\(", text) is None

    def test_json_carries_the_plan(self, adopted, capsys):
        code, text = run(capsys, "--json", "docs", "assess", "--plan")
        assert code == 0, text
        payload = json.loads(text)
        assert payload["plan"]["deliverables"] == ["Document beta", "Document docs"]
        assert payload["plan"]["session"].startswith("document-architecture-")
        assert payload["checks"]["areas"]

    def test_a_missing_sections_line_comes_first(self, adopted, capsys):
        overview = adopted / "docs" / "architecture" / "overview.md"
        overview.write_text("# Toy\n\nalpha\n", encoding="utf-8")
        code, text = run(capsys, "docs", "assess", "--plan")
        assert code == 0, text
        (session,) = parked(adopted)
        assert [e["title"] for e in session.plan] == [
            (
                "Write the missing sections: Layers, Component responsibilities, "
                "Compliance checklist"
            ),
            "Document beta",
            "Document docs",
        ]

    def test_resumed_after_a_stop(self, adopted, capsys, monkeypatch):
        assert run(capsys, "docs", "assess", "--plan")[0] == 0
        (session,) = parked(adopted)
        monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", "host-next")
        code, text = run(capsys, "session", "resume", session.name)
        assert code == 0, text
        code, text = run(capsys, "session", "plan", "show", "--id", session.id)
        assert "Document beta" in text
        assert run(capsys, "session", "plan", "done", "1", "--id", session.id)[0] == 0
        assert parked(adopted) == []

    def test_a_second_plan_is_refused_naming_the_parked_one(self, adopted, capsys):
        assert run(capsys, "docs", "assess", "--plan")[0] == 0
        (session,) = parked(adopted)
        code, text = run(capsys, "docs", "assess", "--plan")
        assert code != 0
        assert session.name in text
        assert len(parked(adopted)) == 1

    def test_no_gaps_records_nothing(self, adopted, capsys):
        overview = adopted / "docs" / "architecture" / "overview.md"
        overview.write_text(HALF + "\nbeta too, and docs.\n", encoding="utf-8")
        code, text = run(capsys, "docs", "assess", "--plan")
        assert code == 0, text
        assert "nothing to plan" in text
        assert parked(adopted) == []

    def test_refused_until_the_manifest_is_committed(self, half, capsys):
        assert init(capsys)[0] == 0
        code, text = run(capsys, "docs", "assess", "--plan")
        assert code == 2
        assert "commit" in text
        assert parked(half) == []

    def test_without_plan_it_is_still_read_only(self, adopted, capsys):
        assert run(capsys, "docs", "assess")[0] == 0
        assert parked(adopted) == []


# -- ruling H: non-interactive init ------------------------------------------


class TestNonInteractiveInit:
    """`--dry-run`, `--json` and no terminal write today's files and ask nothing."""

    @pytest.fixture(autouse=True)
    def refuse_prompts(self, monkeypatch):
        monkeypatch.setattr(prompts, "TerminalPrompter", lambda *a, **k: Refusing())

    @pytest.mark.parametrize("shape", ["absent", "unrelated", "half"])
    def test_no_terminal_writes_todays_files_and_prints_the_assessment(
        self, toy, capsys, shape
    ):
        if shape != "absent":
            docs = toy / "docs" / "architecture"
            docs.mkdir(parents=True)
            (docs / "overview.md").write_text(
                HALF if shape == "half" else "# Holiday\n", encoding="utf-8"
            )
        before = tree(toy / "docs") if shape != "absent" else None
        code, text = init(capsys)
        assert code == 0, text
        assert (toy / MANIFEST_RELPATH).read_text(encoding="utf-8") == TODAYS_MANIFEST
        if shape == "absent":
            assert (toy / "docs" / "architecture" / "overview.md").is_file()
        else:
            assert tree(toy / "docs") == before
        assert "docs assessment:" in text
        assert "beta" in text
        assert parked(toy) == []

    @pytest.mark.parametrize("shape", ["absent", "unrelated"])
    def test_json_carries_the_assessment_as_a_field(self, toy, capsys, shape):
        if shape == "unrelated":
            (toy / "docs" / "architecture").mkdir(parents=True)
        code, text = run(
            capsys, "--json", "init", "--ecosystem", "generic", "--name", "toy"
        )
        assert code == 0, text
        payload = json.loads(text)
        assert "beta" in area_gaps(payload["docs_assessment"])
        assert (toy / MANIFEST_RELPATH).read_text(encoding="utf-8") == TODAYS_MANIFEST

    @pytest.mark.parametrize("shape", ["absent", "half"])
    def test_dry_run_writes_nothing_and_assesses_what_is_there(self, toy, capsys, shape):
        if shape == "half":
            (toy / "docs" / "architecture").mkdir(parents=True)
            (toy / "docs" / "architecture" / "overview.md").write_text(HALF, encoding="utf-8")
        before = tree(toy)
        code, text = init(capsys, "--dry-run")
        assert code == 0, text
        assert tree(toy) == before
        assert "docs assessment:" in text
        code, text = run(
            capsys, "--json", "init", "--ecosystem", "generic", "--name", "toy", "--dry-run"
        )
        payload = json.loads(text)
        assert "beta" in area_gaps(payload["docs_assessment"])
        assert tree(toy) == before

    def test_an_assessment_that_cannot_run_is_reported_not_raised(
        self, toy, capsys, monkeypatch
    ):
        def broken(manifest):
            raise ManifestError("docs.assess cannot be read")

        monkeypatch.setattr("invariant.domains.docs.assess", broken)
        code, text = run(
            capsys, "--json", "init", "--ecosystem", "generic", "--name", "toy"
        )
        assert code == 0, text
        assert "cannot be read" in json.loads(text)["docs_assessment"]["error"]


class TestInteractiveDetection:
    def args(self, **kw):
        return SimpleNamespace(**{"dry_run": False, "json": False, **kw})

    class Tty(io.StringIO):
        def isatty(self):
            return True

    def test_a_terminal_on_both_sides_is_interactive(self):
        assert prompts.interactive(self.args(), self.Tty(), self.Tty()) is True

    @pytest.mark.parametrize("flag", ["dry_run", "json"])
    def test_dry_run_and_json_are_never_interactive(self, flag):
        assert prompts.interactive(self.args(**{flag: True}), self.Tty(), self.Tty()) is False

    def test_no_terminal_on_either_side_is_not_interactive(self):
        assert prompts.interactive(self.args(), io.StringIO(), self.Tty()) is False
        assert prompts.interactive(self.args(), self.Tty(), io.StringIO()) is False

    def test_the_seam_answers_none_without_a_terminal(self):
        assert cli_main._init_prompter(self.args()) is None

    def test_the_seam_answers_a_terminal_prompter_on_one(self, monkeypatch):
        monkeypatch.setattr(prompts, "interactive", lambda args: True)
        assert isinstance(cli_main._init_prompter(self.args()), prompts.TerminalPrompter)


# -- D4.1: reuse, another repo path, or a store ------------------------------


class TestLocationOffer:
    def test_reuse_is_todays_result(self, unrelated, capsys, interactive):
        before = tree(unrelated / "docs")
        scripted = interactive("reuse", "not_now")
        code, text = init(capsys)
        assert code == 0, text
        assert scripted.asked[0][1] == ["reuse", "path", "store"]
        assert any("notes.txt" in said for said in scripted.said)
        assert (unrelated / MANIFEST_RELPATH).read_text(encoding="utf-8") == TODAYS_MANIFEST
        assert tree(unrelated / "docs") == before

    def test_another_repo_path_writes_paths_docs_and_a_starter_there(
        self, unrelated, capsys, interactive
    ):
        before = tree(unrelated / "docs")
        interactive("path", "../outside", "/abs", "design/arch", "not_now")
        code, text = init(capsys)
        assert code == 0, text
        m = Manifest.locate(unrelated)
        assert m.get("paths.docs") == "design/arch"
        assert m.source_of("paths.docs") == f"repo:{MANIFEST_RELPATH}"
        assert (unrelated / "design" / "arch" / "overview.md").is_file()
        assert tree(unrelated / "docs") == before

    def test_another_existing_repo_path_is_reused_untouched(
        self, unrelated, capsys, interactive
    ):
        (unrelated / "design").mkdir()
        (unrelated / "design" / "mine.md").write_text("# Mine\n", encoding="utf-8")
        interactive("path", "design", "not_now")
        assert init(capsys)[0] == 0
        assert Manifest.locate(unrelated).get("paths.docs") == "design"
        assert list((unrelated / "design").iterdir()) == [unrelated / "design" / "mine.md"]

    def test_a_new_store_leaves_records_docs_store_and_the_directory_unchanged(
        self, unrelated, capsys, interactive
    ):
        before = tree(unrelated / "docs")
        interactive("store", "", "", "not_now")
        code, text = init(capsys)
        assert code == 0, text
        assert not (unrelated / MANIFEST_RELPATH).exists()
        m = Manifest.locate(unrelated)
        assert m.store is not None
        assert m.get("records.docs") == "store"
        assert m.path_for("docs") == m.store.root / "docs" / "architecture"
        assert m.get("project.ecosystems") == ["generic"]
        assert m.get("enforcement.profile") == "standard"
        assert (m.store.root / "docs" / "architecture" / "overview.md").is_file()
        assert tree(unrelated / "docs") == before
        assert str(m.store.root) in text
        assert "the manifest lives there" in text

    def test_an_existing_store_is_linked(self, unrelated, capsys, interactive, tmp_path):
        store = tmp_path / "shared"
        store.mkdir()
        git(store, "init", "-q", "-b", "main")
        # The store must list the repo; link writes the repo's local ID first,
        # so list it by the ID it will get.
        from invariant.core.stores import ensure_local_id

        identity = ensure_local_id(unrelated)
        (store / "invariant.yaml").write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "project": {"name": "shared", "ecosystems": ["generic"]},
                    "store": {"repos": {identity.key: {}}},
                }
            ),
            encoding="utf-8",
        )
        before = tree(unrelated / "docs")
        interactive("store", str(store), "notes/arch", "not_now")
        code, text = init(capsys)
        assert code == 0, text
        m = Manifest.locate(unrelated)
        assert m.store.root == store.resolve()
        assert m.get("records.docs") == "store"
        assert m.path_for("docs") == store.resolve() / "notes" / "arch"
        assert m.get("project.name") == "shared"
        body = yaml.safe_load((store / "invariant.yaml").read_text(encoding="utf-8"))
        assert body["store"]["repos"][identity.key] == {
            "records": {"docs": "store"},
            "paths": {"docs": "notes/arch"},
        }
        assert tree(unrelated / "docs") == before

    def commented_store(self, repo, tmp_path) -> Path:
        from invariant.core.stores import ensure_local_id

        store = tmp_path / "shared"
        store.mkdir()
        git(store, "init", "-q", "-b", "main")
        key = ensure_local_id(repo).key
        (store / "invariant.yaml").write_text(
            "# The team's store: every key here was reviewed.\n"
            "version: 1\n"
            "project:\n  name: shared\n  ecosystems: [generic]\n"
            f"store:\n  repos:\n    {key}: {{}}  # added by hand\n",
            encoding="utf-8",
        )
        return store

    def test_a_commented_store_is_left_alone_and_the_block_is_printed(
        self, unrelated, capsys, interactive, tmp_path
    ):
        store = self.commented_store(unrelated, tmp_path)
        before = (store / "invariant.yaml").read_bytes()
        interactive("store", str(store), "notes/arch", "incremental")
        code, text = init(capsys)
        assert code == 0, text
        assert (store / "invariant.yaml").read_bytes() == before
        assert "comment" in text
        key = Manifest.locate(unrelated).store.key
        block = text[text.index("store:\n") :]
        assert yaml.safe_load(block.split("\n\n")[0]) == {
            "store": {
                "repos": {
                    key: {
                        "records": {"docs": "store"},
                        "paths": {"docs": "notes/arch"},
                        "docs": {"onboarding": "incremental"},
                    }
                }
            }
        }
        assert not (unrelated / MANIFEST_RELPATH).exists()

    def test_the_block_is_a_json_field(self, unrelated, capsys, interactive, tmp_path):
        store = self.commented_store(unrelated, tmp_path)
        interactive("store", str(store), "", "not_now")
        code, text = run(
            capsys, "--json", "init", "--ecosystem", "generic", "--name", "toy"
        )
        assert code == 0, text
        by_hand = json.loads(text)["store_settings_by_hand"]
        assert by_hand["path"] == str(store.resolve() / "invariant.yaml")
        assert "comment" in by_hand["reason"]
        key = Manifest.locate(unrelated).store.key
        assert yaml.safe_load(by_hand["yaml"])["store"]["repos"][key] == {
            "records": {"docs": "store"},
            "paths": {"docs": "docs/architecture"},
        }

    def test_an_uncommented_store_carries_no_by_hand_field(
        self, unrelated, capsys, interactive
    ):
        interactive("store", "", "", "not_now")
        code, text = run(
            capsys, "--json", "init", "--ecosystem", "generic", "--name", "toy"
        )
        assert code == 0, text
        assert json.loads(text)["store_settings_by_hand"] is None

    def test_a_store_relative_path_that_climbs_out_is_asked_again(
        self, unrelated, capsys, interactive
    ):
        scripted = interactive("store", "", "../up", "arch", "not_now")
        assert init(capsys)[0] == 0
        assert Manifest.locate(unrelated).get("paths.docs").endswith("/arch")
        assert any("../up" in said for said in scripted.said)

    def test_no_store_is_offered_outside_a_git_repository(
        self, tmp_path, capsys, interactive, monkeypatch
    ):
        root = tmp_path / "plain"
        (root / "docs" / "architecture").mkdir(parents=True)
        monkeypatch.chdir(root)
        scripted = interactive("reuse", "not_now")
        assert init(capsys)[0] == 0
        assert scripted.asked[0][1] == ["reuse", "path"]
        assert any("not inside a git repository" in said for said in scripted.said)

    def test_no_store_is_offered_to_an_in_repo_project(self, unrelated, capsys, interactive):
        (unrelated / MANIFEST_RELPATH).parent.mkdir(parents=True)
        (unrelated / MANIFEST_RELPATH).write_text(TODAYS_MANIFEST, encoding="utf-8")
        scripted = interactive("reuse", "not_now")
        assert init(capsys, "--force")[0] == 0
        assert scripted.asked[0][1] == ["reuse", "path"]
        assert any("store migrate" in said for said in scripted.said)

    def test_absent_docs_ask_no_location(self, toy, capsys, interactive):
        scripted = interactive("not_now")
        assert init(capsys)[0] == 0
        assert [keys for _, keys in scripted.asked] == [
            ["not_now", "incremental", "all_at_once"]
        ]
        assert (toy / "docs" / "architecture" / "overview.md").is_file()


# -- D4.2-3: the assessment and the onboarding offer --------------------------


class TestOnboardingOffer:
    def test_gaps_by_area_and_the_three_offers(self, half, capsys, interactive):
        scripted = interactive("reuse", "not_now")
        assert init(capsys)[0] == 0
        said = "\n".join(scripted.said)
        assert "beta" in said
        assert scripted.asked[1][1] == ["not_now", "incremental", "all_at_once"]

    def test_not_now_writes_nothing(self, half, capsys, interactive):
        interactive("reuse", "not_now")
        assert init(capsys)[0] == 0
        assert (half / MANIFEST_RELPATH).read_text(encoding="utf-8") == TODAYS_MANIFEST
        assert parked(half) == []

    def test_incremental_writes_the_key(self, half, capsys, interactive):
        interactive("reuse", "incremental")
        code, text = init(capsys)
        assert code == 0, text
        m = Manifest.locate(half)
        assert m.get("docs.onboarding") == "incremental"
        assert m.source_of("docs.onboarding") == f"repo:{MANIFEST_RELPATH}"
        assert parked(half) == []

    def test_all_at_once_on_a_fresh_repo_defers_the_plan(self, half, capsys, interactive):
        interactive("reuse", "all_at_once")
        code, text = init(capsys)
        assert code == 0, text
        assert Manifest.locate(half).get("docs.onboarding") == "all_at_once"
        assert parked(half) == []
        assert "invariant docs assess --plan" in text
        assert "commit" in text

    def test_all_at_once_on_a_rerun_records_the_plan(self, half, capsys, interactive):
        assert init(capsys)[0] == 0
        git(half, "add", "-A")
        git(half, "commit", "-qm", "adopt invariant")
        interactive("reuse", "all_at_once")
        code, text = init(capsys, "--force")
        assert code == 0, text
        (session,) = parked(half)
        assert [e["title"] for e in session.plan] == ["Document beta", "Document docs"]
        assert f"invariant session resume {session.name}" in text

    def test_all_at_once_in_a_store_project_records_the_plan(
        self, unrelated, capsys, interactive
    ):
        interactive("store", "", "", "all_at_once")
        code, text = init(capsys)
        assert code == 0, text
        m = Manifest.locate(unrelated)
        assert m.get("docs.onboarding") == "all_at_once"
        assert m.source_of("docs.onboarding").startswith("store:")
        (session,) = parked(unrelated)
        assert "Document beta" in [e["title"] for e in session.plan]

    def test_a_plan_already_parked_is_reported_not_raised(
        self, half, capsys, interactive
    ):
        assert init(capsys)[0] == 0
        git(half, "add", "-A")
        git(half, "commit", "-qm", "adopt invariant")
        assert run(capsys, "docs", "assess", "--plan")[0] == 0
        (first,) = parked(half)
        interactive("reuse", "all_at_once")
        code, text = init(capsys, "--force")
        assert code == 0, text
        assert "the plan was not recorded" in text
        assert first.name in text
        assert len(parked(half)) == 1

    def test_an_assessment_that_cannot_run_is_reported_and_offers_nothing(
        self, half, capsys, interactive, monkeypatch
    ):
        def broken(manifest):
            raise ManifestError("docs.assess cannot be read")

        monkeypatch.setattr("invariant.domains.docs.assess", broken)
        scripted = interactive("reuse")
        code, text = init(capsys)
        assert code == 0, text
        assert "docs assessment not run: docs.assess cannot be read" in text
        assert len(scripted.asked) == 1

    def test_no_gaps_no_offer(self, half, capsys, interactive):
        overview = half / "docs" / "architecture" / "overview.md"
        overview.write_text(HALF + "\nbeta too, and docs.\n", encoding="utf-8")
        scripted = interactive("reuse")
        assert init(capsys)[0] == 0
        assert len(scripted.asked) == 1


# -- the terminal prompter ---------------------------------------------------


class TestTerminalPrompter:
    def prompter(self, *lines):
        replies = iter(lines)
        out: list[str] = []

        def read(prompt=""):
            out.append(prompt)
            try:
                return next(replies)
            except StopIteration:
                raise EOFError from None

        return prompts.TerminalPrompter(read=read, write=out.append), out

    OPTIONS = (("reuse", "reuse it"), ("path", "another path"), ("store", "a store"))

    def test_empty_is_the_default(self):
        p, _ = self.prompter("")
        assert p.choose("where?", self.OPTIONS, "reuse") == "reuse"

    def test_by_number_or_by_key(self):
        p, _ = self.prompter("3", "path")
        assert p.choose("where?", self.OPTIONS, "reuse") == "store"
        assert p.choose("where?", self.OPTIONS, "reuse") == "path"

    def test_junk_asks_again(self):
        p, out = self.prompter("9", "banana", "2")
        assert p.choose("where?", self.OPTIONS, "reuse") == "path"
        assert sum("not one of" in line for line in out) == 2

    def test_end_of_input_is_the_default(self):
        p, _ = self.prompter()
        assert p.choose("where?", self.OPTIONS, "store") == "store"
        assert p.ask("path?", "docs") == "docs"

    def test_ask_strips_and_defaults(self):
        p, out = self.prompter("  arch  ", "")
        assert p.ask("path?", "docs") == "arch"
        assert p.ask("path?", "docs") == "docs"
        p.say("hello")
        assert "hello" in out

    def test_the_options_are_listed_with_the_default_marked(self):
        p, out = self.prompter("")
        p.choose("where?", self.OPTIONS, "path")
        listing = "\n".join(out)
        assert "1) reuse" in listing and "2) path" in listing
        assert "(default)" in next(line for line in out if "2) path" in line)
