"""Repro for BUG_147 — see the work-item entry (`invariant item show BUG_147`).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

Symptom: ``invariant init --force`` on an adopted Sphinx-needs project, one
whose docs source directory holds its own ``conf.py`` loading
``sphinx_needs``, rewrites the manifest's ``docs.format`` to ``markdown``.

Asserted: the resolved ``docs.format`` after ``init --force`` on a project
adopted the way init's own note tells it to be (BUG_128, DEC_ADR_018_B128:
"set docs.format: sphinx, and the project stays migrating"), in the
repository home (``--home repo``), the shape Dang and Heap are set up in.
NOT asserted: the suspected cause, or any design for the fix.

Contract: the entry's Expected line, "``init --force`` on a project with its
own Sphinx-needs ``conf.py`` keeps ``docs.format`` as the format the
project's docs are in (rST)".
"""

from __future__ import annotations

from test_adr_018_starter import (
    SRC,
    git,
    run,
    toy_python,  # noqa: F401 - the fixture
)

from invariant.core.manifest import MANIFEST_RELPATH, Manifest

CONF = (
    "extensions = ['sphinx_needs']\n"
    "needs_types = [{'directive': 'comp', 'title': 'Component', 'prefix': 'COMP_'}]\n"
)


def test_BUG_147_init_force_keeps_an_adopted_projects_docs_format(
    toy_python, tmp_path, monkeypatch, capsys  # noqa: F811
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    # A hand-built sphinx-needs docs project where the docs project goes.
    (toy_python / SRC / "architecture").mkdir(parents=True)
    (toy_python / SRC / "conf.py").write_text(CONF, encoding="utf-8")
    (toy_python / SRC / "index.rst").write_text("Docs\n====\n", encoding="utf-8")
    git(toy_python, "add", "-A")
    git(toy_python, "commit", "-qm", "their docs")

    # Adopt it: init, then the one setting its note names.
    code, text = run(capsys, "init", "--home", "repo", "--ecosystem", "python")
    assert code == 0, text
    assert "adopted as it stands: set docs.format: sphinx" in text
    manifest = toy_python / MANIFEST_RELPATH
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "\ndocs:\n  format: sphinx\n",
        encoding="utf-8",
    )
    git(toy_python, "add", "-A")
    git(toy_python, "commit", "-qm", "adopt the docs project")
    assert Manifest.locate(toy_python).get("docs.format") == "sphinx"
    assert Manifest.locate(toy_python).get("docs.needs.state") == "migrating"

    code, text = run(capsys, "init", "--force")
    assert code == 0, text
    assert Manifest.locate(toy_python).get("docs.format") == "sphinx"
