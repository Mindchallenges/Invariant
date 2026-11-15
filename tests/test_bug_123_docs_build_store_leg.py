# ruff: noqa: F811 - the fixtures imported below are named as parameters
"""BUG_123: in a store project the docs-project gates read the store leg.

In a project whose ``records.docs`` is ``store``, a session whose repository
changed set holds only a code file, with a readable store leg that carries no
docs change, gets N/A from both docs-project gates (``docs.builds_strict``,
``docs.needs_traceable``): the change touches neither the docs project's
source directory nor ``docs.needs`` (state_model.rst, ADR-018 increment 3).
Before the fix both failed with "the changed set could not be established for
a docs project in a store". The first test is the reproducer's, its
assertion unchanged.

The rest prove the fix through the store leg rather than by the empty leg
alone (DEC_ADR_014_B123): the docs project resolves in the store's main
checkout, a docs change on the store leg is judged against the strict build
recorded in the project's state root for the store tree's digest (none, green
and red, with a fake ``sphinx-build`` and, where ``INVARIANT_TEST_SPHINX_BUILD``
names one, a real one), and a store leg that cannot be read still fails.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest
import yaml
from test_adr_014_docs_lock import (  # noqa: F401 - fixtures are used by name
    git,
    run_json,
    two,
)
from test_adr_014_docs_lock_host import (  # noqa: F401 - fixtures are used by name
    hosts,
)
from test_adr_014_per_leg_gates import commit, ctx_for, gate, read_doc, write_doc
from test_adr_018_docs_build import REQUIREMENTS, Fake

from invariant.core import needs
from invariant.core.manifest import Manifest
from invariant.core.rules import STORE_LEG, _changed_set, get_rule
from invariant.domains import docs as docs_domain


def on_sphinx(store: Path) -> None:
    """Put every linked repository's docs on sphinx, so neither gate is
    settled by ADR-018 D10 before the changed set is read."""
    config = store / "invariant.yaml"
    body = yaml.safe_load(config.read_text(encoding="utf-8"))
    for entry in body["store"]["repos"].values():
        entry.setdefault("docs", {})["format"] = "sphinx"
    config.write_text(yaml.safe_dump(body), encoding="utf-8")
    git(store, "add", "-A")
    git(store, "commit", "-q", "--no-verify", "-m", "docs on sphinx")


def change_code_only(repo: Path) -> None:
    code = repo / "src" / "app.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("x = 2\n", encoding="utf-8")
    git(repo, "add", "src/app.py")


def test_BUG_123_code_only_session_in_a_docs_store_is_na_for_both_docs_build_gates(hosts):
    on_sphinx(hosts.store)
    change_code_only(hosts.widgets)
    ctx = ctx_for(hosts.widgets)
    assert ctx.manifest.get("records.docs") == "store"
    assert ctx.manifest.get("docs.format") == "sphinx"
    # The world the symptom needs: a non-empty repository changed set holding
    # only code, and a store leg that can be read and has no docs change.
    repo_leg = _changed_set(ctx)
    store_leg = _changed_set(ctx, STORE_LEG)
    assert repo_leg.answerable and repo_leg.paths
    assert all(not str(p).startswith("docs") for p in repo_leg.paths)
    assert store_leg.answerable and not store_leg.paths

    refused = {
        rule: verdict.message
        for rule in ("docs.builds_strict", "docs.needs_traceable")
        if not (verdict := gate(hosts.widgets, rule)).skipped
    }
    assert refused == {}


# -- the fix, proved through the store leg ------------------------------------

STRICT, TRACE = "docs.builds_strict", "docs.needs_traceable"

CONF = (
    "project = 'widgets'\n"
    "extensions = ['sphinx_needs']\n"
    "exclude_patterns = ['architecture/cli.rst', 'notes.rst', 'guidelines']\n"
)
INDEX = "Widgets\n=======\n\n.. toctree::\n\n   architecture/overview\n"
GOOD = "Overview\n========\n\nThe widgets.\n"
#: Line 4 names a document there is none of: a warning, so a strict build fails.
BAD = "Overview\n========\n\nSee :doc:`missing`.\n"


def docs_project_in_store(hosts) -> Path:
    """The store's docs project (``docs.needs.srcdir: docs``) on sphinx,
    committed before any span, so the store leg holds only what a test
    changes under the lock."""
    on_sphinx(hosts.store)
    write_doc(hosts.store, "docs/conf.py", CONF)
    write_doc(hosts.store, "docs/index.rst", INDEX)
    write_doc(hosts.store, "docs/requirements.txt", REQUIREMENTS)
    write_doc(hosts.store, "docs/.gitignore", ".venv/\n")
    commit(hosts.store, "the docs project")
    return hosts.store / "docs"


def change_overview(hosts, text: str, *, first: bool = True) -> None:
    """A docs change on the store leg: read under the lock (the first time),
    written, and committed under the span holding the read."""
    if first:
        read_doc(hosts, hosts.overview)
    write_doc(hosts.store, "docs/architecture/overview.rst", text)
    commit(hosts.store, "overview")


def build():
    return docs_domain.build_docs(Manifest.locate(Path.cwd()))


def judged(hosts) -> dict:
    return {rule: gate(hosts.widgets, rule) for rule in (STRICT, TRACE)}


def real_venv(srcdir: Path, sphinx_build: str) -> None:
    """``<srcdir>/.venv`` in sync with its requirements, running ``sphinx_build``."""
    script = write_doc(
        srcdir, ".venv/bin/sphinx-build", f'#!/bin/sh\nexec "{sphinx_build}" "$@"\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    recorded = hashlib.sha256((srcdir / "requirements.txt").read_bytes()).hexdigest()
    write_doc(srcdir, f".venv/{docs_domain.VENV_HASH}", recorded + "\n")


class TestTheStoreLegIsBuilt:
    def test_the_docs_project_is_found_in_the_store_whichever_tree_is_asked(self, hosts):
        srcdir = docs_project_in_store(hosts)
        manifest = Manifest.locate(hosts.widgets)
        assert needs.srcdir_of(manifest) == srcdir.resolve()
        assert needs.srcdir_of(manifest, hosts.widgets) == srcdir.resolve()

    def test_paths_docs_alone_finds_the_store_docs_project(self, hosts):
        srcdir = docs_project_in_store(hosts)
        body = yaml.safe_load((hosts.store / "invariant.yaml").read_text(encoding="utf-8"))
        for entry in body["store"]["repos"].values():
            entry["docs"]["needs"].pop("srcdir")
            entry.setdefault("paths", {})["docs"] = "docs/architecture"
        (hosts.store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
        manifest = Manifest.locate(hosts.widgets)
        assert manifest.get("docs.needs.srcdir") is None
        assert needs.srcdir_of(manifest) == srcdir.resolve()
        assert needs.srcdir_of(manifest, hosts.widgets) == srcdir.resolve()

    def test_a_store_docs_change_with_no_build_recorded_fails_naming_the_command(self, hosts):
        docs_project_in_store(hosts)
        change_overview(hosts, GOOD)
        for rule, verdict in judged(hosts).items():
            assert not verdict.ok and not verdict.skipped, rule
            assert "no docs build recorded for docs/" in verdict.message
            assert verdict.fixes == ("invariant docs build",)

    def test_a_green_store_build_recorded_in_the_projects_state_root_passes(
        self, hosts, tmp_path
    ):
        srcdir = docs_project_in_store(hosts)
        fake = Fake(srcdir, tmp_path / "fake")
        change_overview(hosts, GOOD)
        hosts.at(hosts.widgets)
        record, built = build()
        assert built and record.passed and fake.runs()
        assert record.digest == needs.tree_digest(srcdir)
        # The project's state root, never the store's (DEC_ADR_014_B123).
        state_root = Manifest.locate(hosts.widgets).state_root
        assert needs.read_record(state_root, record.digest) == record
        assert not (hosts.store / ".git" / "invariant" / "state" / "docs").exists()
        for rule, verdict in judged(hosts).items():
            assert verdict.ok and not verdict.skipped, (rule, verdict.message)
        assert "strict build green for docs/" in gate(hosts.widgets, STRICT).message

    def test_a_red_store_build_fails_naming_the_file_and_line(self, hosts, tmp_path):
        srcdir = docs_project_in_store(hosts)
        fake = Fake(srcdir, tmp_path / "fake")
        change_overview(hosts, BAD)
        fake.red(
            f"{srcdir}/architecture/overview.rst:4: WARNING: unknown document: "
            "'missing' [ref.doc]\n"
        )
        hosts.at(hosts.widgets)
        record, _ = build()
        assert not record.passed
        strict, trace = judged(hosts).values()
        assert not strict.ok and "the strict build of docs/ failed" in strict.message
        # Spelled from the store's checkout, never the machine's path to it.
        assert (strict.detail or "").startswith("docs/architecture/overview.rst:4 ")
        assert not trace.ok and "no green build of docs/" in trace.message

    def test_a_store_build_for_an_older_tree_does_not_count(self, hosts, tmp_path):
        srcdir = docs_project_in_store(hosts)
        Fake(srcdir, tmp_path / "fake")
        change_overview(hosts, GOOD)
        hosts.at(hosts.widgets)
        build()
        assert gate(hosts.widgets, STRICT).ok
        change_overview(hosts, GOOD + "More.\n", first=False)
        assert "no docs build recorded" in gate(hosts.widgets, STRICT).message

    def test_an_unreadable_store_leg_fails_both_naming_why(self, hosts):
        docs_project_in_store(hosts)
        change_overview(hosts, GOOD)
        ctx = ctx_for(hosts.widgets)
        for leg in ctx.session["legs"]:
            for span in leg.get("lock_spans") or []:
                span["acquired_at_sha"] = "0" * 40
        for rule in (STRICT, TRACE):
            verdict = get_rule(rule).fn(ctx)
            assert not verdict.ok and not verdict.skipped, rule
            assert verdict.message.startswith("the store leg's changes could not be read")
            assert "cannot be read" in (verdict.detail or "")


@pytest.mark.skipif(
    not os.environ.get("INVARIANT_TEST_SPHINX_BUILD"),
    reason="set INVARIANT_TEST_SPHINX_BUILD to a sphinx-build with sphinx-needs "
    "to build the store's docs project for real",
)
def test_a_store_docs_change_is_built_strictly_for_real(hosts):
    srcdir = docs_project_in_store(hosts)
    real_venv(srcdir, os.environ["INVARIANT_TEST_SPHINX_BUILD"])
    hosts.at(hosts.widgets)

    change_overview(hosts, GOOD)
    record, built = build()
    assert built and record.passed, record.diagnostics
    for rule, verdict in judged(hosts).items():
        assert verdict.ok and not verdict.skipped, (rule, verdict.message)

    change_overview(hosts, BAD, first=False)
    assert "no docs build recorded" in gate(hosts.widgets, STRICT).message
    record, built = build()
    assert built and not record.passed
    strict = gate(hosts.widgets, STRICT)
    assert not strict.ok and "the strict build of docs/ failed" in strict.message
    assert (strict.detail or "").startswith("docs/architecture/overview.rst:4 "), strict.detail
    assert str(hosts.store) not in (strict.detail or "")
