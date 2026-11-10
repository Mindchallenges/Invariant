"""ADR-018 increment 6: `invariant item migrate` (D14).

Every item, open and closed, moves to another backend with its id, fields,
body and history, and is read back field for field and history row for row
before anything is written; a read-back that differs refuses the whole move.
The proof is a copy of this repository's own backlog, migrated to
``sphinx_needs`` in a temporary directory: the repository's backlog itself is
never touched (that is increment 7).

The ``sphinx_needs`` residue is written lossless here too: an entry holding a
``;`` is escaped, because the option joins entries with ``; `` and eleven of
this repository's items carry one.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.cli.main import main
from invariant.core.errors import UsageError
from invariant.core.items import (
    Item,
    JsonBackend,
    MarkdownBackend,
    SphinxNeedsBackend,
    differences,
)
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.domains.workitem import WorkItemDomain

REPO = Path(__file__).resolve().parents[1]

RULED = {
    "from": "filed",
    "to": "reproduced",
    "reason": None,
    "forced": False,
    "ruling": "maintainer, 2026-11-09",
    "evidence": "0123456789abcdef0123456789abcdef01234567",
    "intermediate": True,
}


def items() -> list[Item]:
    return [
        Item(
            id="BUG_001",
            type="bug",
            title="An open bug",
            status="reproduced",
            fields={"severity": "high", "area": "core/items"},
            residue=["one; with a semicolon", "a back\\slash"],
            history=[RULED],
            body="The body.\n\n  indented line\n\nLast.",
        ),
        Item(
            id="BUG_002",
            type="bug",
            title="A closed bug",
            status="wont_fix",
            history=[{"from": "filed", "to": "wont_fix", "reason": "no", "forced": False}],
            body="Closed.",
        ),
        Item(id="IDEA_001", type="idea", title="An open idea", status="proposed"),
        Item(
            id="IDEA_002",
            type="idea",
            title="A finished idea",
            status="done",
            fields={"priority": "low"},
        ),
    ]


def project(tmp_path: Path, items_block: dict, docs: bool = False) -> Manifest:
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=root, check=True)
    body = {"version": 1, "items": items_block}
    if docs:
        (root / "docs").mkdir()
        (root / "docs" / "conf.py").write_text("extensions = []\n", encoding="utf-8")
        (root / "docs" / "architecture").mkdir()
        body["docs"] = {"needs": {"srcdir": "docs"}}
    (root / MANIFEST_RELPATH).write_text(yaml.safe_dump(body), encoding="utf-8")
    return Manifest.locate(root)


def markdown_project(tmp_path: Path, docs: bool = False) -> Manifest:
    manifest = project(
        tmp_path, {"backend": "markdown", "path": "backlog", "id_prefix": ""}, docs=docs
    )
    store = MarkdownBackend(manifest.root / "backlog")
    for item in items():
        store.save(item)
    return manifest


def snapshot(path: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(path.iterdir())}


# -- the sphinx_needs residue, lossless ---------------------------------------


class TestResidue:
    def test_a_semicolon_and_a_backslash_round_trip(self, tmp_path):
        store = SphinxNeedsBackend(tmp_path / "b.rst")
        item = Item(
            id="BUG_001",
            type="bug",
            title="t",
            status="filed",
            residue=["a; b", "c\\d", "e\\;f", "tail\\"],
        )
        store.add(item)
        assert store.get("BUG_001").residue == item.residue
        assert ":residue: a\\; b; c\\\\d; e\\\\\\;f; tail\\\\" in (tmp_path / "b.rst").read_text()

    def test_an_unescaped_residue_reads_as_it_always_has(self, tmp_path):
        (tmp_path / "b.rst").write_text(
            ".. bug:: t\n   :id: BUG_001\n   :status: filed\n   :residue: one; two;; C:\\x\n",
            encoding="utf-8",
        )
        assert SphinxNeedsBackend(tmp_path / "b.rst").get("BUG_001").residue == [
            "one",
            "two",
            "C:\\x",
        ]


# -- differences: what a read-back compares -------------------------------------


class TestDifferences:
    def test_equal_items_differ_in_nothing(self):
        (item, *_) = items()
        assert differences(item, Item.from_dict(item.to_dict())) == []

    def test_a_missing_item_and_each_part_are_named(self):
        (item, *_) = items()
        assert differences(item, None) == ["not found"]
        other = Item.from_dict(item.to_dict())
        other.title = "x"
        other.residue = []
        other.fields = {"severity": "high", "extra": "1"}
        other.history = []
        named = differences(item, other)
        assert named[:2] == ["title", "residue"]
        assert "field area" in named and "field extra" in named and "history" in named


# -- the migration -------------------------------------------------------------------


class TestMigrate:
    def test_markdown_to_sphinx_needs_with_a_closed_store(self, tmp_path):
        manifest = markdown_project(tmp_path)
        before = snapshot(manifest.root / "backlog")
        done = WorkItemDomain(manifest).migrate(
            "sphinx_needs",
            manifest.root / "docs/backlog.rst",
            closed_path=manifest.root / "docs/closed.rst",
        )
        assert done.open == ("BUG_001", "IDEA_001")
        assert done.closed == ("BUG_002", "IDEA_002")
        assert not done.dry_run
        opened = {i.id: i for i in SphinxNeedsBackend(manifest.root / "docs/backlog.rst").load_all()}
        closed = {i.id: i for i in SphinxNeedsBackend(manifest.root / "docs/closed.rst").load_all()}
        for item in items():
            got = (closed if item.id in done.closed else opened)[item.id]
            assert differences(item, got) == [], item.id
        # The source store is never written.
        assert snapshot(manifest.root / "backlog") == before

    def test_without_a_closed_store_every_item_goes_to_the_open_one(self, tmp_path):
        manifest = markdown_project(tmp_path)
        done = WorkItemDomain(manifest).migrate("json", manifest.root / "items.json")
        assert done.closed == () and len(done.open) == 4
        back = JsonBackend(manifest.root / "items.json").load_all()
        assert [i.id for i in back] == [i.id for i in items()]

    def test_the_source_closed_store_is_migrated_too(self, tmp_path):
        manifest = project(
            tmp_path,
            {"backend": "json", "path": "open.json", "closed_path": "closed.json"},
        )
        first, second, *_ = items()
        JsonBackend(manifest.root / "open.json").save(first)
        JsonBackend(manifest.root / "closed.json").save(second)
        done = WorkItemDomain(manifest).migrate("markdown", manifest.root / "md")
        assert done.open == ("BUG_001", "BUG_002")
        assert {p.name for p in (manifest.root / "md").iterdir()} == {"BUG_001.md", "BUG_002.md"}

    def test_a_dry_run_writes_nothing(self, tmp_path):
        manifest = markdown_project(tmp_path)
        done = WorkItemDomain(manifest).migrate(
            "sphinx_needs", manifest.root / "docs/backlog.rst", dry_run=True
        )
        assert done.dry_run and len(done.open) == 4
        assert not (manifest.root / "docs").exists()

    def test_a_read_back_that_differs_writes_nothing(self, tmp_path):
        manifest = markdown_project(tmp_path)
        broken = Item(
            id="BUG_009",
            type="bug",
            title="Typed field",
            status="filed",
            fields={"count": 3, "note": "two\nlines"},
        )
        MarkdownBackend(manifest.root / "backlog").save(broken)
        target = manifest.root / "docs/backlog.rst"
        with pytest.raises(UsageError) as refused:
            WorkItemDomain(manifest).migrate("sphinx_needs", target)
        said = str(refused.value)
        assert "BUG_009" in said and "field count" in said and "field note" in said
        assert "nothing was written" in said
        assert not target.exists()

    def test_an_id_the_target_holds_already_is_refused(self, tmp_path):
        manifest = markdown_project(tmp_path)
        target = manifest.root / "backlog.rst"
        target.write_text(
            "Backlog\n=======\n\n.. idea:: Held\n   :id: IDEA_001\n   :status: proposed\n",
            encoding="utf-8",
        )
        held = target.read_bytes()
        with pytest.raises(UsageError, match="IDEA_001.*already"):
            WorkItemDomain(manifest).migrate("sphinx_needs", target)
        assert target.read_bytes() == held

    def test_items_the_target_holds_are_kept(self, tmp_path):
        manifest = markdown_project(tmp_path)
        target = manifest.root / "backlog.rst"
        target.write_text(
            "Backlog\n=======\n\nIntro.\n\n.. idea:: Held\n   :id: IDEA_050\n   :status: proposed\n",
            encoding="utf-8",
        )
        done = WorkItemDomain(manifest).migrate("sphinx_needs", target)
        assert done.kept == 1
        text = target.read_text(encoding="utf-8")
        assert text.startswith("Backlog\n=======\n\nIntro.\n")
        assert {i.id for i in SphinxNeedsBackend(target).load_all()} == {
            "IDEA_050",
            *(i.id for i in items()),
        }

    def test_an_item_the_target_cannot_store_is_refused(self, tmp_path):
        manifest = markdown_project(tmp_path)
        MarkdownBackend(manifest.root / "backlog").save(
            Item(id="TASK_001", type="task", title="A task", status="open")
        )
        with pytest.raises(UsageError, match="TASK_001"):
            WorkItemDomain(manifest).migrate("sphinx_needs", manifest.root / "b.rst")
        assert not (manifest.root / "b.rst").exists()

    def test_an_item_held_by_both_source_stores_is_refused(self, tmp_path):
        manifest = project(
            tmp_path,
            {"backend": "json", "path": "open.json", "closed_path": "closed.json"},
        )
        first, *_ = items()
        JsonBackend(manifest.root / "open.json").save(first)
        JsonBackend(manifest.root / "closed.json").save(first)
        with pytest.raises(UsageError, match="BUG_001.*both"):
            WorkItemDomain(manifest).migrate("markdown", manifest.root / "md")

    @pytest.mark.parametrize(
        ("backend", "target", "said"),
        [
            ("none", "x.json", "backend"),
            ("markdown", "backlog", "source"),
            ("sphinx_needs", "", "path"),
        ],
    )
    def test_refusals_before_anything_is_read(self, tmp_path, backend, target, said):
        manifest = markdown_project(tmp_path)
        path = manifest.root / target if target else None
        with pytest.raises(UsageError, match=said):
            WorkItemDomain(manifest).migrate(backend, path)

    def test_the_closed_path_must_differ_from_the_open_one(self, tmp_path):
        manifest = markdown_project(tmp_path)
        same = manifest.root / "b.rst"
        with pytest.raises(UsageError, match="closed"):
            WorkItemDomain(manifest).migrate("sphinx_needs", same, closed_path=same)

    def test_a_declared_source_closed_store_that_is_missing_is_refused(self, tmp_path):
        manifest = project(
            tmp_path,
            {"backend": "json", "path": "open.json", "closed_path": "gone.json"},
        )
        JsonBackend(manifest.root / "open.json").save(items()[0])
        with pytest.raises(UsageError, match="gone.json.*does not exist"):
            WorkItemDomain(manifest).migrate("markdown", manifest.root / "md")

    def test_a_disabled_source_is_refused(self, tmp_path):
        manifest = project(tmp_path, {"backend": "none"})
        with pytest.raises(UsageError, match="disabled"):
            WorkItemDomain(manifest).migrate("json", manifest.root / "x.json")

    def test_sphinx_needs_defaults_to_the_docs_projects_items_page(self, tmp_path):
        manifest = markdown_project(tmp_path, docs=True)
        done = WorkItemDomain(manifest).migrate("sphinx_needs", None)
        assert done.path == manifest.root / "docs" / "backlog.rst"
        assert len(SphinxNeedsBackend(done.path).load_all()) == 4

    def test_no_closed_item_writes_no_closed_store(self, tmp_path):
        manifest = project(tmp_path, {"backend": "json", "path": "open.json"})
        first, *_ = items()
        JsonBackend(manifest.root / "open.json").save(first)
        done = WorkItemDomain(manifest).migrate(
            "sphinx_needs", manifest.root / "b.rst", closed_path=manifest.root / "c.rst"
        )
        assert done.closed == ()
        assert not (manifest.root / "c.rst").exists()

    def test_a_markdown_target_directory_keeps_its_files(self, tmp_path):
        manifest = project(tmp_path, {"backend": "json", "path": "open.json"})
        first, second, *_ = items()
        JsonBackend(manifest.root / "open.json").save(first)
        held = MarkdownBackend(manifest.root / "md")
        held.save(second)
        before = (manifest.root / "md" / "BUG_002.md").read_bytes()
        done = WorkItemDomain(manifest).migrate("markdown", manifest.root / "md")
        assert done.kept == 1
        assert (manifest.root / "md" / "BUG_002.md").read_bytes() == before
        assert differences(first, held.get("BUG_001")) == []


# -- the CLI ---------------------------------------------------------------------------


class TestCli:
    def test_text_names_the_stores_and_the_switch(self, tmp_path, monkeypatch, capsys):
        manifest = markdown_project(tmp_path)
        monkeypatch.chdir(manifest.root)
        code = main(
            [
                "item", "migrate", "--to", "sphinx_needs",
                "--path", "docs/backlog.rst", "--closed-path", "docs/closed.rst",
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "migrated 4 item(s) from markdown (backlog) to sphinx_needs" in out
        assert "2 into docs/backlog.rst, 2 closed into docs/closed.rst" in out
        assert "read back field for field" in out
        assert "items.backend: sphinx_needs" in out
        assert "items.path: docs/backlog.rst" in out
        assert "items.closed_path: docs/closed.rst" in out
        assert "unchanged" in out

    def test_json_and_dry_run(self, tmp_path, monkeypatch, capsys):
        manifest = markdown_project(tmp_path)
        monkeypatch.chdir(manifest.root)
        code = main(
            ["--json", "item", "migrate", "--to", "json", "--path", "x.json", "--dry-run"]
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["dry_run"] is True and payload["backend"] == "json"
        assert payload["path"] == "x.json" and payload["closed_path"] is None
        assert payload["open"] == [i.id for i in items()] and payload["closed"] == []
        assert payload["settings"] == {"items.backend": "json", "items.path": "x.json"}
        assert not (manifest.root / "x.json").exists()
        main(["item", "migrate", "--to", "json", "--path", "x.json", "--dry-run"])
        assert "would migrate 4 item(s)" in capsys.readouterr().out


# -- the proof: this repository's backlog, on a copy ------------------------------


def test_this_repositorys_backlog_migrates_and_reads_back_on_a_copy(tmp_path):
    source = REPO / "docs" / "backlog"
    files = sorted(source.glob("*.md"))
    assert len(files) >= 161
    manifest = project(tmp_path, {"backend": "markdown", "path": "backlog"})
    shutil.copytree(source, manifest.root / "backlog")
    expected = MarkdownBackend(manifest.root / "backlog").load_all()
    assert len(expected) == len(files)

    domain = WorkItemDomain(manifest)
    done = domain.migrate(
        "sphinx_needs",
        manifest.root / "docs/backlog.rst",
        closed_path=manifest.root / "docs/closed.rst",
    )
    assert len(done.open) + len(done.closed) == len(files)
    assert done.closed, "the backlog holds closed items"

    back = {
        i.id: i
        for path in (done.path, done.closed_path)
        for i in SphinxNeedsBackend(path).load_all()
    }
    assert len(back) == len(files)
    rows = 0
    for item in expected:
        got = back[item.id]
        assert differences(item, got) == [], item.id
        assert got.fields == item.fields
        assert got.history == item.history
        rows += len(got.history)
    assert rows > 200
    terminal = {"done", "dropped", "closed", "not_reproducible", "wont_fix"}
    assert {i.status for i in SphinxNeedsBackend(done.closed_path).load_all()} <= terminal
    # The repository's own backlog is untouched by the proof.
    assert not (REPO / "docs" / "backlog.rst").exists()
