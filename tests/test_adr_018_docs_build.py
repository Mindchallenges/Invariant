"""ADR-018 increment 3: the docs project's environment (D4), its strict build
and record (D5), and the two landing gates that read the record (D5, D6), with
D10's states: N/A on a migrating Markdown project, warn while migrating on
sphinx, block when enforced.

A fake ``sphinx-build`` stands in the toy project's ``.venv`` for most tests: a
shell script printing what the test asks and writing the needs.json the test
gives it. One class drives a real pinned environment end to end, and runs only
where ``INVARIANT_TEST_SPHINX_BUILD`` names a ``sphinx-build`` with
sphinx-needs 8.x (Heap's pinned environment), so the suite never needs Sphinx.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from test_adr_018_needs import need, needs_json

from invariant.cli.main import main
from invariant.core import needs
from invariant.core.errors import UsageError
from invariant.core.machines import load_pipeline_machine
from invariant.core.manifest import MANIFEST_RELPATH, Level, Manifest
from invariant.core.rules import RuleContext, evaluate
from invariant.domains import docs as docs_domain
from invariant.domains.pipeline import PipelineDomain

STRICT = "docs.builds_strict"
TRACE = "docs.needs_traceable"

REQUIREMENTS = "Sphinx==9.1.0\nsphinx-needs==8.1.1\n"

OVERVIEW = """\
Overview
========

.. comp:: Core
   :id: COMP_CORE

.. mermaid::

   graph TD
     A-->B
"""


def git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def commit(root: Path, message: str = "change") -> None:
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)


def manifest_body(state: str | None = "enforced", fmt: str = "sphinx", **docs_needs) -> dict:
    needs_block = dict(docs_needs)
    if state is not None:
        needs_block["state"] = state
    body = {
        "version": 1,
        "project": {"name": "toy", "ecosystems": ["python"]},
        "docs": {"format": fmt, "needs": needs_block},
        "items": {"backend": "sphinx_needs", "path": "docs/backlog.rst"},
    }
    if fmt == "markdown":
        body["items"] = {"backend": "markdown", "path": "docs/backlog"}
    return body


def write_manifest(root: Path, body: dict) -> None:
    write(root, MANIFEST_RELPATH, yaml.safe_dump(body))


FAKE = """\
#!/bin/sh
echo "$@" >> "{log}"
for a; do last="$a"; done
mkdir -p "$last"
[ -f "{needs}" ] && cp "{needs}" "$last/needs.json"
[ -f "{err}" ] && cat "{err}" >&2
echo "updating environment: [new config] 3 added, 0 changed, 0 removed"
exit $(cat "{code}" 2>/dev/null || echo 0)
"""


class Fake:
    """The toy project's fake sphinx-build: what it prints and writes."""

    def __init__(self, srcdir: Path, scratch: Path) -> None:
        scratch.mkdir(parents=True, exist_ok=True)
        self.log = scratch / "argv.log"
        self.needs = scratch / "needs.json"
        self.err = scratch / "stderr.txt"
        self.code = scratch / "code.txt"
        script = write(
            srcdir,
            ".venv/bin/sphinx-build",
            FAKE.format(log=self.log, needs=self.needs, err=self.err, code=self.code),
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        recorded = hashlib.sha256((srcdir / "requirements.txt").read_bytes()).hexdigest()
        write(srcdir, f".venv/{docs_domain.VENV_HASH}", recorded + "\n")
        self.green()

    def green(self, data=None) -> None:
        self.needs.write_text(json.dumps(data or traceable()), encoding="utf-8")
        self.err.write_text("", encoding="utf-8")
        self.code.write_text("0", encoding="utf-8")

    def red(self, stderr: str, code: int = 1) -> None:
        self.err.write_text(stderr, encoding="utf-8")
        self.code.write_text(str(code), encoding="utf-8")

    def runs(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []


def traceable():
    return needs_json(
        [
            need("COMP_CORE", "comp", "architecture/overview", 4),
            need("DEC_ADR_001", "dec", "decisions/adr-001", 6, "accepted", implements=["COMP_CORE"]),
        ]
    )


@pytest.fixture
def toy(tmp_path):
    """An enforced toy project: main checkout and a session worktree."""
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q", "-b", "master")
    git(main, "config", "user.email", "test@example.invalid")
    git(main, "config", "user.name", "Test")
    write(main, "docs/conf.py", "extensions = ['sphinx_needs']\n")
    write(main, "docs/requirements.txt", REQUIREMENTS)
    write(main, "docs/.gitignore", ".venv/\n")
    write(main, "docs/index.rst", "Toy\n===\n")
    write(main, "docs/architecture/overview.rst", OVERVIEW)
    write(main, "docs/backlog.rst", "Backlog\n=======\n")
    write(main, "src/toy/mod.py", "x = 1\n")
    write_manifest(main, manifest_body())
    commit(main, "init")
    return main


def worktree(main: Path, name: str = "one") -> tuple[Path, Fake]:
    wt = main.parent / name
    git(main, "worktree", "add", "-q", str(wt), "-b", f"session/{name}")
    return wt, Fake(wt / "docs", main.parent / f"fake-{name}")


def ctx(main: Path, wt: Path, files=()) -> RuleContext:
    session = {"worktree": str(wt), "base": "master", "branch": f"session/{wt.name}",
               "files": list(files)}
    return RuleContext(manifest=Manifest.locate(main), cwd=wt, session=session)


def verdicts(context: RuleContext) -> dict:
    return {r.rule_id: r for r in evaluate([STRICT, TRACE], context)}


def build(wt: Path, **kwargs):
    return docs_domain.build_docs(Manifest.locate(wt), **kwargs)


def landing_gates(manifest: Manifest) -> list[str]:
    machine = load_pipeline_machine(manifest, "tdd")
    (landing,) = machine.states_with_role("landing")
    (review,) = machine.states_with_role("review")
    return PipelineDomain.gates_for(None, review, landing, machine)


RED = """\
{root}/docs/architecture/overview.rst:12: WARNING: Need 'COMP_CORE' has unknown outgoing link 'COMP_GONE' in field 'links' [needs.link_outgoing]
{root}/docs/backlog.rst:9: ERROR: Error in "bug" directive:
unknown option: "residue". [docutils]
{root}/docs/index.rst:3: CRITICAL: Title underline too short. [docutils]
"""


# -- the proof, enforced ----------------------------------------------------


class TestEnforced:
    def test_both_gates_ride_the_landing_entry(self, toy):
        gates = landing_gates(Manifest.locate(toy))
        assert STRICT in gates and TRACE in gates
        assert gates.index(STRICT) > gates.index("docs.updated")

    def test_a_docs_change_with_no_build_record_blocks_naming_the_command(self, toy):
        wt, _fake = worktree(toy)
        write(wt, "docs/architecture/overview.rst", OVERVIEW + "\nMore.\n")
        commit(wt)
        results = verdicts(ctx(toy, wt))
        strict = results[STRICT]
        assert strict.level is Level.BLOCK and strict.blocks
        assert "no docs build recorded" in strict.verdict.message
        assert "invariant docs build" in strict.verdict.fixes
        assert results[TRACE].blocks

    def test_each_failure_blocks_named_by_file_and_line_and_the_fix_passes(self, toy):
        wt, fake = worktree(toy)
        write(wt, "docs/architecture/overview.rst", OVERVIEW + "\nMore.\n")
        commit(wt)
        fake.red(RED.format(root=wt))
        record, rebuilt = build(wt)
        assert rebuilt and not record.passed and record.exit_code == 1
        strict = verdicts(ctx(toy, wt))[STRICT]
        assert strict.blocks
        text = strict.verdict.message + " " + (strict.verdict.detail or "")
        assert "docs/architecture/overview.rst:12 [needs.link_outgoing]" in text
        assert "docs/backlog.rst:9 [docutils]" in text
        assert "docs/index.rst:3 [docutils]" in text
        assert str(wt) not in text

        write(wt, "docs/architecture/overview.rst", OVERVIEW + "\nFixed.\n")
        commit(wt)
        fake.green()
        record, rebuilt = build(wt)
        assert record.passed and rebuilt
        results = verdicts(ctx(toy, wt))
        assert results[STRICT].verdict.ok and not results[STRICT].verdict.skipped
        assert results[TRACE].verdict.ok and not results[TRACE].verdict.skipped

    def test_the_build_is_strict_html_with_needs_json_forced(self, toy):
        wt, fake = worktree(toy)
        record, _ = build(wt)
        (argv,) = fake.runs()
        assert argv.split()[:6] == ["-E", "-W", "--keep-going", "-n", "-b", "html"]
        assert "-D needs_build_json=1" in argv
        assert argv.split()[-2] == str((wt / "docs").resolve())
        manifest = Manifest.locate(wt)
        assert argv.split()[-1] == str(manifest.state_root / "docs" / "build" / "html")
        assert record.needs_json and Path(record.needs_json).is_file()
        assert record.documents == 3

    def test_a_landing_with_no_docs_change_is_na(self, toy):
        wt, _ = worktree(toy)
        write(wt, "src/toy/mod.py", "x = 2\n")
        commit(wt)
        results = verdicts(ctx(toy, wt))
        for rule in (STRICT, TRACE):
            assert results[rule].verdict.skipped
            assert "docs/" in results[rule].verdict.message

    def test_a_change_to_docs_needs_alone_applies(self, toy):
        wt, _ = worktree(toy)
        write_manifest(wt, manifest_body(decision_statuses=["accepted"]))
        commit(wt)
        assert verdicts(ctx(toy, wt))[STRICT].blocks

    def test_another_manifest_change_does_not(self, toy):
        wt, _ = worktree(toy)
        body = manifest_body()
        body["project"]["name"] = "renamed"
        write_manifest(wt, body)
        commit(wt)
        assert verdicts(ctx(toy, wt))[STRICT].verdict.skipped

    def test_a_decision_with_no_element_link_is_blocked_by_name(self, toy):
        wt, fake = worktree(toy)
        write(wt, "docs/architecture/overview.rst", OVERVIEW + "\nMore.\n")
        commit(wt)
        data = traceable()
        data["versions"][""]["needs"]["DEC_ADR_001"]["implements"] = []
        fake.green(data)
        build(wt)
        trace = verdicts(ctx(toy, wt))[TRACE]
        assert trace.blocks
        assert "DEC_ADR_001" in trace.verdict.message + (trace.verdict.detail or "")
        assert "docs/decisions/adr-001.rst:6" in (trace.verdict.detail or "")

    def test_an_unmapped_directive_is_named(self, toy):
        wt, fake = worktree(toy)
        write(wt, "docs/architecture/overview.rst", OVERVIEW + "\nMore.\n")
        commit(wt)
        data = traceable()
        data["versions"][""]["needs"]["WP_ONE"] = need("WP_ONE", "wp", "plan", 2)
        fake.green(data)
        build(wt)
        trace = verdicts(ctx(toy, wt))[TRACE]
        assert trace.blocks and "directive 'wp'" in (trace.verdict.detail or "")

    def test_a_role_with_no_needs_passes(self, toy):
        wt, fake = worktree(toy)
        write(wt, "docs/architecture/overview.rst", OVERVIEW + "\nMore.\n")
        commit(wt)
        fake.green(needs_json([need("COMP_CORE", "comp")]))
        build(wt)
        assert verdicts(ctx(toy, wt))[TRACE].verdict.ok

    def test_a_green_digest_is_not_built_again(self, toy):
        wt, fake = worktree(toy)
        first, rebuilt = build(wt)
        assert rebuilt and first.passed
        again, rebuilt = build(wt)
        assert not rebuilt and again.digest == first.digest
        assert len(fake.runs()) == 1
        _, rebuilt = build(wt, force=True)
        assert rebuilt and len(fake.runs()) == 2

    def test_a_red_digest_is_built_again(self, toy):
        wt, fake = worktree(toy)
        fake.red(RED.format(root=wt))
        build(wt)
        fake.green()
        record, rebuilt = build(wt)
        assert rebuilt and record.passed

    def test_traceability_waits_for_a_green_build(self, toy):
        wt, fake = worktree(toy)
        write(wt, "docs/architecture/overview.rst", OVERVIEW + "\nMore.\n")
        commit(wt)
        fake.red(RED.format(root=wt))
        build(wt)
        trace = verdicts(ctx(toy, wt))[TRACE]
        assert trace.blocks and STRICT in trace.verdict.message

    def test_a_record_for_an_older_digest_does_not_count(self, toy):
        wt, _fake = worktree(toy)
        build(wt)
        write(wt, "docs/architecture/overview.rst", OVERVIEW + "\nLater.\n")
        commit(wt)
        strict = verdicts(ctx(toy, wt))[STRICT]
        assert strict.blocks and "no docs build recorded" in strict.verdict.message

    def test_the_venv_is_not_part_of_the_digest(self, toy):
        wt, _ = worktree(toy)
        before = needs.tree_digest(wt / "docs")
        write(wt, "docs/.venv/lib/site.py", "x\n")
        write(wt, "docs/_build/html/index.html", "x\n")
        assert needs.tree_digest(wt / "docs") == before
        write(wt, "docs/index.rst", "Changed\n=======\n")
        assert needs.tree_digest(wt / "docs") != before

    def test_a_docs_project_in_a_store_is_not_established(self, toy):
        wt, _ = worktree(toy)
        write(wt, "docs/index.rst", "Changed\n=======\n")
        commit(wt)
        context = ctx(toy, wt)
        data = json.loads(json.dumps(context.manifest.data))
        data["records"]["docs"] = "store"
        context.manifest = replace(context.manifest, data=data)
        strict = verdicts(context)[STRICT]
        assert strict.blocks and "store" in strict.verdict.message

    def test_an_unanswerable_changed_set_fails(self, toy):
        wt, _ = worktree(toy)
        context = ctx(toy, wt)
        context.session = {"worktree": str(wt), "base": "no-such-base", "branch": "x"}
        assert not verdicts(context)[STRICT].verdict.ok

    def test_nothing_changed_is_na(self, toy):
        wt, _ = worktree(toy)
        assert verdicts(ctx(toy, wt))[STRICT].verdict.skipped


# -- D10: the same toy project migrating, and this repository's shape ----------


class TestMigrating:
    def test_on_sphinx_each_failure_warns(self, toy):
        write_manifest(toy, manifest_body(state=None))
        commit(toy, "migrating")
        wt, fake = worktree(toy)
        write(wt, "docs/architecture/overview.rst", OVERVIEW + "\nMore.\n")
        commit(wt)
        results = verdicts(ctx(toy, wt))
        assert results[STRICT].level is Level.WARN and not results[STRICT].blocks
        assert not results[STRICT].verdict.ok
        fake.red(RED.format(root=wt))
        build(wt)
        results = verdicts(ctx(toy, wt))
        assert not results[STRICT].verdict.ok and not results[STRICT].blocks
        data = traceable()
        data["versions"][""]["needs"]["DEC_ADR_001"]["implements"] = []
        fake.green(data)
        build(wt, force=True)
        trace = verdicts(ctx(toy, wt))[TRACE]
        assert not trace.verdict.ok and not trace.blocks

    def test_on_markdown_both_are_na_and_say_why(self, toy):
        write_manifest(toy, manifest_body(state=None, fmt="markdown"))
        commit(toy, "markdown")
        wt, _ = worktree(toy)
        write(wt, "docs/architecture/overview.rst", OVERVIEW + "\nMore.\n")
        commit(wt)
        for result in verdicts(ctx(toy, wt)).values():
            assert result.verdict.skipped and "markdown" in result.verdict.message

    def test_a_migrating_project_with_no_docs_project_is_na(self, toy):
        write_manifest(toy, manifest_body(state=None))
        git(toy, "rm", "-q", "docs/conf.py")
        commit(toy, "no conf")
        wt, _ = worktree(toy)
        write(wt, "docs/index.rst", "Changed\n=======\n")
        commit(wt)
        assert verdicts(ctx(toy, wt))[STRICT].verdict.skipped


def test_this_repository_is_migrating_on_markdown_and_na(monkeypatch):
    """Increment 3's last proof: invariant itself, unchanged and N/A."""
    root = Path(__file__).resolve().parents[1]
    manifest = Manifest.locate(root)
    assert manifest.get("docs.needs.state") == "migrating"
    assert manifest.get("docs.format") == "markdown"
    context = RuleContext(manifest=manifest, cwd=root, session=None)
    for result in evaluate([STRICT, TRACE], context):
        assert result.verdict.skipped and not result.blocks


# -- D4: the environment ------------------------------------------------------


class FakeUv:
    def __init__(self, fail_at: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail_at = fail_at

    def __call__(self, argv, cwd):
        self.calls.append(list(argv))
        if self.fail_at and self.fail_at in argv:
            return subprocess.CompletedProcess(argv, 2, "", "uv: no network")
        if argv[1] == "venv":
            write(Path(argv[-1]), "bin/python", "")
        return subprocess.CompletedProcess(argv, 0, "", "")


class TestVenv:
    def project(self, tmp_path) -> Path:
        srcdir = tmp_path / "docs"
        write(srcdir, "conf.py", "")
        write(srcdir, "requirements.txt", REQUIREMENTS)
        return srcdir

    def test_a_missing_venv_is_reported(self, tmp_path):
        state = docs_domain.venv_state(self.project(tmp_path))
        assert not state.present and not state.in_sync
        assert "invariant docs venv" in state.describe()

    def test_sync_creates_and_records_the_requirements_hash(self, tmp_path):
        srcdir = self.project(tmp_path)
        uv = FakeUv()
        state = docs_domain.sync_venv(srcdir, run=uv, uv="uv")
        assert [c[1:3] for c in uv.calls] == [["venv", str(srcdir / ".venv")], ["pip", "sync"]]
        assert uv.calls[1][-1] == str(srcdir / "requirements.txt")
        assert state.present and state.in_sync
        again = docs_domain.sync_venv(srcdir, run=uv, uv="uv")
        assert again.in_sync and [c[1] for c in uv.calls[2:]] == ["pip"]

    def test_a_changed_requirements_file_is_out_of_sync(self, tmp_path):
        srcdir = self.project(tmp_path)
        docs_domain.sync_venv(srcdir, run=FakeUv(), uv="uv")
        write(srcdir, "requirements.txt", REQUIREMENTS + "furo==2025.12.19\n")
        state = docs_domain.venv_state(srcdir)
        assert state.present and not state.in_sync
        assert "does not match" in state.describe()

    def test_a_failed_sync_records_nothing(self, tmp_path):
        srcdir = self.project(tmp_path)
        with pytest.raises(UsageError, match="no network"):
            docs_domain.sync_venv(srcdir, run=FakeUv(fail_at="sync"), uv="uv")
        assert not docs_domain.venv_state(srcdir).in_sync

    def test_no_requirements_file_is_refused(self, tmp_path):
        srcdir = tmp_path / "docs"
        write(srcdir, "conf.py", "")
        with pytest.raises(UsageError, match="requirements.txt"):
            docs_domain.sync_venv(srcdir, run=FakeUv(), uv="uv")

    def test_no_uv_is_refused_naming_it(self, tmp_path):
        with pytest.raises(UsageError, match="uv"):
            docs_domain.sync_venv(self.project(tmp_path), run=FakeUv(), uv=None)

    def test_a_build_without_a_venv_fails_naming_docs_venv_and_records_nothing(self, toy):
        manifest = Manifest.locate(toy)
        with pytest.raises(UsageError, match="invariant docs venv"):
            docs_domain.build_docs(manifest)
        digest = needs.tree_digest(toy / "docs")
        assert needs.read_record(manifest.state_root, digest) is None

    def test_a_build_with_a_stale_venv_fails_naming_docs_venv(self, toy):
        wt, _ = worktree(toy)
        write(wt, "docs/requirements.txt", REQUIREMENTS + "furo==2025.12.19\n")
        with pytest.raises(UsageError, match="invariant docs venv"):
            build(wt)

    def test_a_project_with_no_docs_project_is_refused(self, tmp_path):
        write_manifest(tmp_path, manifest_body(state=None, fmt="markdown"))
        with pytest.raises(UsageError, match="docs.needs.srcdir"):
            docs_domain.docs_project(Manifest.locate(tmp_path))


# -- doctor's rows and the commands ------------------------------------------


def rows(capsys) -> dict:
    lines = capsys.readouterr().out
    document = json.loads(lines[lines.index("{"):])
    return {row["check"]: row for row in document["checks"]}


class TestDoctorRows:
    def test_enforced_with_a_synced_venv(self, toy, monkeypatch, capsys):
        wt, _ = worktree(toy)
        monkeypatch.chdir(wt)
        main(["doctor", "--json"])
        found = rows(capsys)
        assert found["docs mandate"]["ok"]
        assert found["docs venv"]["ok"] and "in sync" in found["docs venv"]["detail"]
        assert "plantuml" not in found

    def test_migrating_fails_the_mandate_naming_the_migration(self, toy, monkeypatch, capsys):
        write_manifest(toy, manifest_body(state=None, fmt="markdown"))
        monkeypatch.chdir(toy)
        main(["doctor", "--json"])
        found = rows(capsys)
        assert not found["docs mandate"]["ok"]
        assert "migration" in found["docs mandate"]["detail"]
        assert found["docs venv"]["ok"] and "not required" in found["docs venv"]["detail"]

    def test_a_missing_venv_fails_its_row(self, toy, monkeypatch, capsys):
        monkeypatch.chdir(toy)
        main(["doctor", "--json"])
        venv = rows(capsys)["docs venv"]
        assert not venv["ok"] and "invariant docs venv" in venv["detail"]

    def test_plantuml_listed_gets_its_row(self, toy, monkeypatch, capsys):
        body = manifest_body()
        body["docs"]["diagrams"] = ["mermaid", "plantuml"]
        write_manifest(toy, body)
        write(toy, "docs/conf.py", "extensions = []\nplantuml = 'no-such-plantuml-binary -x'\n")
        monkeypatch.chdir(toy)
        main(["doctor", "--json"])
        row = rows(capsys)["plantuml"]
        assert not row["ok"] and "no-such-plantuml-binary" in row["detail"]

    def test_plantuml_found_passes(self, toy, monkeypatch):
        write(toy, "docs/conf.py", "plantuml = 'sh'\n")
        body = manifest_body()
        body["docs"]["diagrams"] = "plantuml"
        write_manifest(toy, body)
        row = docs_domain.plantuml_row(Manifest.locate(toy))
        assert row[1] and "sh" in row[2]
        write(toy, "docs/conf.py", "extensions = []\n")
        monkeypatch.setattr(docs_domain.shutil, "which", lambda name: None)
        row = docs_domain.plantuml_row(Manifest.locate(toy))
        assert not row[1] and "'plantuml'" in row[2]

    def test_a_venv_whose_sphinx_build_does_not_run_fails(self, toy):
        wt, _fake = worktree(toy)
        script = wt / "docs" / ".venv" / "bin" / "sphinx-build"
        script.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
        label, ok, detail = docs_domain.venv_row(Manifest.locate(wt))
        assert label == "docs venv" and not ok and "does not run" in detail


class TestCommands:
    def test_docs_build_records_and_exits_by_the_result(self, toy, monkeypatch, capsys):
        wt, fake = worktree(toy)
        monkeypatch.chdir(wt)
        assert main(["docs", "build"]) == 0
        assert "green" in capsys.readouterr().out
        assert main(["docs", "build"]) == 0
        assert "not built again" in capsys.readouterr().out
        fake.red(RED.format(root=wt))
        assert main(["docs", "build", "--force", "--json"]) == 1
        document = json.loads(capsys.readouterr().out)
        assert not document["passed"] and len(document["diagnostics"]) == 3

    def test_docs_venv_check_reports_and_exits(self, toy, monkeypatch, capsys):
        wt, _ = worktree(toy)
        monkeypatch.chdir(wt)
        assert main(["docs", "venv", "--check"]) == 0
        assert "in sync" in capsys.readouterr().out
        monkeypatch.chdir(toy)
        assert main(["docs", "venv", "--check"]) == 1

    def test_docs_venv_syncs_through_uv(self, toy, monkeypatch, capsys):
        uv = FakeUv()
        monkeypatch.setattr(docs_domain, "_run", uv)
        monkeypatch.setattr(docs_domain, "_uv", lambda: "uv")
        monkeypatch.chdir(toy)
        assert main(["docs", "venv"]) == 0
        assert [c[1] for c in uv.calls] == ["venv", "pip"]
        assert "in sync" in capsys.readouterr().out


# -- the pinned environment, end to end --------------------------------------


REAL_CONF = """\
project = "toy"
extensions = ["sphinx_needs"]
exclude_patterns = [".venv", "_build"]
needs_id_required = True
needs_types = [
    {"directive": "req", "title": "Requirement", "prefix": "REQ_", "color": "#BFD8D2", "style": "node"},
    {"directive": "dec", "title": "Decision", "prefix": "DEC_", "color": "#FEDCD2", "style": "node"},
    {"directive": "comp", "title": "Component", "prefix": "COMP_", "color": "#DCB239", "style": "node"},
    {"directive": "ui", "title": "UI", "prefix": "UI_", "color": "#DCB239", "style": "node"},
    {"directive": "rule", "title": "Rule", "prefix": "RULE_", "color": "#DCB239", "style": "node"},
    {"directive": "idea", "title": "Idea", "prefix": "IDEA_", "color": "#DCB239", "style": "node"},
    {"directive": "bug", "title": "Bug", "prefix": "BUG_", "color": "#DCB239", "style": "node"},
]
needs_links = {"implements": {"incoming": "implemented by", "outgoing": "implements"}}
"""

REAL_INDEX = """\
Toy
===

.. toctree::

   architecture

.. dec:: Use a core
   :id: DEC_ADR_001
   :status: accepted
   :implements: COMP_CORE
"""

REAL_ARCH = """\
Architecture
============

.. comp:: Core
   :id: COMP_CORE
"""


@pytest.mark.skipif(
    not os.environ.get("INVARIANT_TEST_SPHINX_BUILD"),
    reason="set INVARIANT_TEST_SPHINX_BUILD to a pinned sphinx-build with sphinx-needs 8.x",
)
class TestARealPinnedBuild:
    """Increment 3's proof on a toy docs project in a pinned environment."""

    @pytest.fixture
    def real(self, toy):
        write(toy, "docs/conf.py", REAL_CONF)
        write(toy, "docs/index.rst", REAL_INDEX)
        write(toy, "docs/architecture.rst", REAL_ARCH)
        git(toy, "rm", "-rq", "docs/architecture", "docs/backlog.rst")
        body = manifest_body()
        body["items"] = {"backend": "sphinx_needs", "path": "docs/backlog.rst"}
        write_manifest(toy, body)
        commit(toy, "real")
        wt = toy.parent / "real"
        git(toy, "worktree", "add", "-q", str(wt), "-b", "session/real")
        venv = wt / "docs" / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "sphinx-build").symlink_to(os.environ["INVARIANT_TEST_SPHINX_BUILD"])
        digest = hashlib.sha256((wt / "docs" / "requirements.txt").read_bytes()).hexdigest()
        write(wt, f"docs/.venv/{docs_domain.VENV_HASH}", digest + "\n")
        return wt

    def landing(self, toy, wt, text):
        write(wt, "docs/architecture.rst", text)
        commit(wt)
        record, _ = build(wt)
        return record, verdicts(ctx(toy, wt))

    @pytest.mark.parametrize(
        ("broken", "where", "kind"),
        [
            (REAL_ARCH + "   :links: COMP_GONE\n", "docs/architecture.rst:4", "needs.link_outgoing"),
            (REAL_ARCH + "   :residue: x\n", "docs/architecture.rst:4", None),
            (REAL_ARCH + "\nBad\n==\n\n.. nosuch::\n", "docs/architecture.rst", None),
        ],
        ids=["dangling-link", "unregistered-option", "docutils-error"],
    )
    def test_each_failure_blocks_by_file_and_line_and_the_fix_passes(
        self, toy, real, broken, where, kind
    ):
        record, results = self.landing(toy, real, broken)
        assert not record.passed
        strict = results[STRICT]
        assert strict.blocks
        detail = strict.verdict.detail or ""
        assert where in detail
        if kind:
            assert f"[{kind}]" in detail
        record, results = self.landing(toy, real, REAL_ARCH)
        assert record.passed, record.diagnostics
        assert results[STRICT].verdict.ok and results[TRACE].verdict.ok

    def test_a_decision_with_no_element_link_is_blocked_by_name(self, toy, real):
        write(real, "docs/index.rst", REAL_INDEX.replace("   :implements: COMP_CORE\n", ""))
        record, results = self.landing(toy, real, REAL_ARCH)
        assert record.passed
        assert results[TRACE].blocks and "DEC_ADR_001" in (results[TRACE].verdict.detail or "")


# -- D1: docs.diagrams_current reads the rST markers --------------------------


class TestDiagramsCurrent:
    def diagrams(self, toy, wt):
        (result,) = evaluate(["docs.diagrams_current"], ctx(toy, wt))
        return result.verdict

    @pytest.mark.parametrize(
        ("listed", "body", "ok"),
        [
            ("mermaid", OVERVIEW, True),
            ("mermaid", "Overview\n========\n\n.. uml::\n\n   A -> B\n", False),
            ("plantuml", "Overview\n========\n\n.. uml::\n\n   A -> B\n", True),
            ("plantuml", "Overview\n========\n\n@startuml\nA -> B\n@enduml\n", True),
            (["mermaid", "plantuml"], "Overview\n========\n\n.. uml:: a.puml\n", True),
            (["mermaid", "plantuml"], "Overview\n========\n\nProse only.\n", False),
            ("mermaid", "# Overview\n\n```mermaid\ngraph TD\n```\n", True),
        ],
    )
    def test_either_marker_in_a_listed_format_counts(self, toy, listed, body, ok):
        manifest = manifest_body()
        manifest["docs"]["diagrams"] = listed
        write_manifest(toy, manifest)
        commit(toy, "diagrams")
        wt, _ = worktree(toy)
        write(wt, "docs/architecture/overview.rst", body + "\nEdited.\n")
        commit(wt)
        verdict = self.diagrams(toy, wt)
        assert verdict.ok is ok, verdict.message

    def test_no_listed_format_is_na(self, toy):
        manifest = manifest_body()
        manifest["docs"]["diagrams"] = []
        write_manifest(toy, manifest)
        commit(toy, "none")
        wt, _ = worktree(toy)
        assert self.diagrams(toy, wt).skipped


def test_a_venv_with_no_sphinx_build_is_refused_naming_docs_venv(toy):
    wt, _ = worktree(toy)
    (wt / "docs" / ".venv" / "bin" / "sphinx-build").unlink()
    write(wt, "docs/.venv/bin/python", "")
    with pytest.raises(UsageError, match="no .*sphinx-build; run 'invariant docs venv'"):
        build(wt)


def test_no_requirements_names_what_to_write():
    state = docs_domain.VenvState(Path("/x/docs"), present=False, expected=None, recorded=None)
    assert "pinning every package" in state.describe()
