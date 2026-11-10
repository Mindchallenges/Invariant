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

Ruling 20 (2026-11-10): a body moving between backends whose body formats
differ is converted, Markdown to rST, by the converter ``docs migrate`` uses
(``core/mdconvert`` with the docs environment's pandoc), and the read-back
compares the converted body. A same-format move stays byte for byte. rST to
Markdown has no converter, so a move that would need one is refused. The
suite stands in for pandoc; the real conversion of this repository's backlog,
built strictly, is proven behind ``INVARIANT_TEST_SPHINX_BUILD``.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from test_adr_018_migrate_docs import (
    FakePandoc,
    doc,
    docs_project,
    link,
    para,
    raw_inline,
    render,
)

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
from invariant.domains import docs as docs_domain
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


class Echo:
    """A pandoc stand-in that converts every body to itself: its tree is one
    raw rST block, which the rewrite keeps and the writer gives back."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args, text: str) -> str:
        args = list(args)
        self.calls.append(args)
        if args[args.index("-t") + 1] == "json":
            return json.dumps(doc({"t": "RawBlock", "c": ["rst", text]}))
        return render(json.loads(text)["blocks"]) + "\n"


SPACE = {"t": "Space"}


def refuse_pandoc(args, text):
    raise AssertionError(f"pandoc was run on a same-format move: {args}")


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
        manifest = markdown_project(tmp_path, docs=True)
        before = snapshot(manifest.root / "backlog")
        done = WorkItemDomain(manifest).migrate(
            "sphinx_needs",
            manifest.root / "docs/backlog.rst",
            closed_path=manifest.root / "docs/closed.rst",
            pandoc=Echo(),
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
        done = WorkItemDomain(manifest).migrate(
            "json", manifest.root / "items.json", pandoc=refuse_pandoc
        )
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
        manifest = markdown_project(tmp_path, docs=True)
        done = WorkItemDomain(manifest).migrate(
            "sphinx_needs", manifest.root / "docs/backlog.rst", dry_run=True, pandoc=Echo()
        )
        assert done.dry_run and len(done.open) == 4
        assert done.converted == ("BUG_001", "BUG_002")
        assert not (manifest.root / "docs/backlog.rst").exists()

    def test_a_read_back_that_differs_writes_nothing(self, tmp_path):
        manifest = markdown_project(tmp_path, docs=True)
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
            WorkItemDomain(manifest).migrate("sphinx_needs", target, pandoc=Echo())
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
        manifest = markdown_project(tmp_path, docs=True)
        target = manifest.root / "backlog.rst"
        target.write_text(
            "Backlog\n=======\n\nIntro.\n\n.. idea:: Held\n   :id: IDEA_050\n   :status: proposed\n",
            encoding="utf-8",
        )
        done = WorkItemDomain(manifest).migrate("sphinx_needs", target, pandoc=Echo())
        assert done.kept == 1
        text = target.read_text(encoding="utf-8")
        assert text.startswith("Backlog\n=======\n\nIntro.\n")
        assert {i.id for i in SphinxNeedsBackend(target).load_all()} == {
            "IDEA_050",
            *(i.id for i in items()),
        }

    def test_an_item_the_target_cannot_store_is_refused(self, tmp_path):
        manifest = markdown_project(tmp_path, docs=True)
        MarkdownBackend(manifest.root / "backlog").save(
            Item(id="TASK_001", type="task", title="A task", status="open")
        )
        with pytest.raises(UsageError, match="TASK_001"):
            WorkItemDomain(manifest).migrate(
                "sphinx_needs", manifest.root / "b.rst", pandoc=Echo()
            )
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
        done = WorkItemDomain(manifest).migrate("sphinx_needs", None, pandoc=Echo())
        assert done.path == manifest.root / "docs" / "backlog.rst"
        assert len(SphinxNeedsBackend(done.path).load_all()) == 4

    def test_no_closed_item_writes_no_closed_store(self, tmp_path):
        manifest = project(tmp_path, {"backend": "json", "path": "open.json"}, docs=True)
        first, *_ = items()
        JsonBackend(manifest.root / "open.json").save(first)
        done = WorkItemDomain(manifest).migrate(
            "sphinx_needs",
            manifest.root / "b.rst",
            closed_path=manifest.root / "c.rst",
            pandoc=Echo(),
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


# -- bodies: converted when the formats differ (ruling 20) ------------------------------


class TestBodies:
    def test_a_markdown_body_is_converted_to_rst_and_read_back_as_converted(self, tmp_path):
        manifest = markdown_project(tmp_path, docs=True)
        (manifest.root / "docs/architecture/overview.rst").write_text(
            "Overview\n========\n", encoding="utf-8"
        )
        fake = FakePandoc(
            {
                "The body.\n\n  indented line\n\nLast.": doc(
                    para("See", SPACE, link("the overview", "../docs/architecture/overview.md#layers")),
                    para("and", SPACE, link("the other bug", "BUG_002.md")),
                ),
                "Closed.": doc(para("Closed", SPACE, link("away", "https://example.org/x"))),
            }
        )
        target = manifest.root / "docs/backlog.rst"
        done = WorkItemDomain(manifest).migrate("sphinx_needs", target, pandoc=fake)
        assert done.converted == ("BUG_001", "BUG_002")
        assert done.unconverted == ()
        assert any("#layers" in note and note.startswith("BUG_001") for note in done.notes)
        back = {i.id: i for i in SphinxNeedsBackend(target).load_all()}
        assert back["BUG_001"].body == (
            "See :doc:`the overview </architecture/overview>`\n\n"
            "and :need:`the other bug <BUG_002>`"
        )
        assert back["BUG_002"].body == "Closed `away <https://example.org/x>`__"
        # Everything else about the item reads back exactly.
        original = {i.id: i for i in items()}
        for item_id in ("BUG_001", "BUG_002"):
            got, want = back[item_id], original[item_id]
            assert differences(want, got) == ["body"]
        assert differences(original["IDEA_001"], back["IDEA_001"]) == []
        # Only bodies are converted, and an empty one never reaches pandoc.
        assert sum(1 for call in fake.calls if call[-1] == "json") == 2

    def test_a_same_format_move_is_byte_for_byte_and_runs_no_pandoc(self, tmp_path):
        manifest = markdown_project(tmp_path)
        done = WorkItemDomain(manifest).migrate(
            "json", manifest.root / "items.json", pandoc=refuse_pandoc
        )
        assert done.converted == () and done.unconverted == ()
        back = {i.id: i for i in JsonBackend(manifest.root / "items.json").load_all()}
        for item in items():
            assert back[item.id].body == item.body

    def test_sphinx_needs_to_sphinx_needs_keeps_the_body_as_written(self, tmp_path):
        manifest = project(tmp_path, {"backend": "sphinx_needs", "path": "a.rst"})
        SphinxNeedsBackend(manifest.root / "a.rst").save(
            Item(id="BUG_001", type="bug", title="t", status="filed", body="A *rst* body.")
        )
        done = WorkItemDomain(manifest).migrate(
            "sphinx_needs", manifest.root / "b.rst", pandoc=refuse_pandoc
        )
        assert done.converted == ()
        assert SphinxNeedsBackend(manifest.root / "b.rst").get("BUG_001").body == "A *rst* body."

    def test_json_bodies_are_markdown_and_are_converted(self, tmp_path):
        manifest = project(tmp_path, {"backend": "json", "path": "open.json"}, docs=True)
        first, *_ = items()
        JsonBackend(manifest.root / "open.json").save(first)
        fake = FakePandoc({first.body: doc(para("Converted."))})
        done = WorkItemDomain(manifest).migrate("sphinx_needs", manifest.root / "b.rst", pandoc=fake)
        assert done.converted == ("BUG_001",)
        assert SphinxNeedsBackend(manifest.root / "b.rst").get("BUG_001").body == "Converted."

    @pytest.mark.parametrize("to", ["markdown", "json"])
    def test_rst_to_markdown_is_refused_where_a_body_would_need_it(self, tmp_path, to):
        manifest = project(tmp_path, {"backend": "sphinx_needs", "path": "a.rst"})
        store = SphinxNeedsBackend(manifest.root / "a.rst")
        store.save(Item(id="BUG_001", type="bug", title="t", status="filed", body="An *rst* body."))
        store.save(Item(id="BUG_002", type="bug", title="u", status="filed"))
        target = manifest.root / ("md" if to == "markdown" else "x.json")
        with pytest.raises(UsageError) as refused:
            WorkItemDomain(manifest).migrate(to, target, pandoc=refuse_pandoc)
        said = str(refused.value)
        assert "BUG_001" in said and "BUG_002" not in said
        assert "rST" in said and "Markdown" in said and "Nothing was written" in said
        assert not target.exists()

    def test_rst_to_markdown_with_no_body_moves(self, tmp_path):
        manifest = project(tmp_path, {"backend": "sphinx_needs", "path": "a.rst"})
        SphinxNeedsBackend(manifest.root / "a.rst").save(
            Item(id="IDEA_001", type="idea", title="t", status="proposed")
        )
        done = WorkItemDomain(manifest).migrate(
            "markdown", manifest.root / "md", pandoc=refuse_pandoc
        )
        assert done.open == ("IDEA_001",) and done.converted == ()

    def test_an_unconverted_construct_is_named_by_item_line_and_reason(self, tmp_path):
        manifest = markdown_project(tmp_path, docs=True)
        fake = FakePandoc(
            {
                "The body.\n\n  indented line\n\nLast.": doc(
                    para("Fine."),
                    para("A", raw_inline("html", "<kbd>x</kbd>")),
                    para("To", link("code", "../src/a.py")),
                ),
                "Closed.": doc(para("Closed.")),
            }
        )
        target = manifest.root / "docs/backlog.rst"
        closed = manifest.root / "docs/closed.rst"
        done = WorkItemDomain(manifest).migrate(
            "sphinx_needs", target, closed_path=closed, pandoc=fake
        )
        lines = target.read_text(encoding="utf-8").splitlines()
        at = [n + 1 for n, text in enumerate(lines) if ".. invariant-unconverted::" in text]
        assert [(u.id, u.path, u.line) for u in done.unconverted] == [
            ("BUG_001", target, at[0]),
            ("BUG_001", target, at[1]),
        ]
        assert done.unconverted[0].reason == "raw html <kbd>x</kbd>"
        assert "../src/a.py" in done.unconverted[1].reason
        assert "not a page of the docs project" in done.unconverted[1].reason

    def test_a_construct_in_a_list_item_or_above_every_need_is_found(self):
        text = (
            ".. invariant-unconverted:: stray\n\n"
            ".. bug:: t\n   :id: BUG_001\n   :status: filed\n\n"
            "   - .. invariant-unconverted:: in a bullet\n\n"
            "   2. .. invariant-unconverted:: in a number\n\n"
            ".. idea:: u\n   :id: IDEA_001\n\n   .. invariant-unconverted:: plain\n"
        )
        assert docs_domain.unconverted_in(text) == [
            ("", 1, "stray"),
            ("BUG_001", 7, "in a bullet"),
            ("BUG_001", 9, "in a number"),
            ("IDEA_001", 14, "plain"),
        ]

    def test_no_pandoc_in_the_environment_is_refused_before_anything_is_written(
        self, tmp_path
    ):
        manifest = environment_project(tmp_path, pandoc=False)
        target = manifest.root / "docs/items.rst"
        with pytest.raises(UsageError) as refused:
            WorkItemDomain(manifest).migrate("sphinx_needs", target)
        said = str(refused.value)
        assert "pypandoc_binary==1.17" in said and "invariant docs venv" in said
        assert not target.exists()

    def test_no_docs_project_is_refused_where_a_body_needs_converting(self, tmp_path):
        manifest = markdown_project(tmp_path)
        target = manifest.root / "b.rst"
        with pytest.raises(UsageError, match="no docs project"):
            WorkItemDomain(manifest).migrate("sphinx_needs", target)
        assert not target.exists()

    def test_the_environments_pandoc_is_used_by_default(self, tmp_path, monkeypatch):
        manifest = environment_project(tmp_path)
        seen = []

        def call(binary):
            seen.append(binary)
            return Echo()

        monkeypatch.setattr(docs_domain, "pandoc_call", call)
        WorkItemDomain(manifest).migrate("sphinx_needs", manifest.root / "docs/items.rst")
        assert seen and seen[0].name == "pandoc"


# -- the CLI ---------------------------------------------------------------------------


def environment_project(tmp_path: Path, pandoc: bool = True) -> Manifest:
    """A docs project with its environment in sync (and pypandoc's pandoc in
    it, unless not), the items in the default markdown store."""
    manifest = docs_project(tmp_path, pandoc=pandoc)
    store = MarkdownBackend(manifest.root / "docs" / "backlog")
    for item in items():
        store.save(item)
    return manifest


class TestCli:
    def test_text_names_the_stores_and_the_switch(self, tmp_path, monkeypatch, capsys):
        manifest = environment_project(tmp_path)
        monkeypatch.setattr(docs_domain, "pandoc_call", lambda binary: Echo())
        monkeypatch.chdir(manifest.root)
        code = main(
            [
                "item", "migrate", "--to", "sphinx_needs",
                "--path", "docs/items.rst", "--closed-path", "docs/closed.rst",
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "migrated 4 item(s) from markdown (docs/backlog) to sphinx_needs" in out
        assert "2 into docs/items.rst, 2 closed into docs/closed.rst" in out
        assert "read back field for field" in out
        assert "2 body(ies) converted from Markdown to rST" in out
        assert "no construct left unconverted" in out
        assert "items.backend: sphinx_needs" in out
        assert "items.path: docs/items.rst" in out
        assert "items.closed_path: docs/closed.rst" in out
        assert "unchanged" in out

    def test_each_unconverted_construct_is_named_and_the_reader_is_passed(
        self, tmp_path, monkeypatch, capsys
    ):
        manifest = environment_project(tmp_path)
        fake = FakePandoc(
            {
                "The body.\n\n  indented line\n\nLast.": doc(
                    para("A", raw_inline("html", "<kbd>x</kbd>"))
                ),
                "Closed.": doc(para("Closed.")),
            }
        )
        monkeypatch.setattr(docs_domain, "pandoc_call", lambda binary: fake)
        monkeypatch.chdir(manifest.root)
        code = main(
            [
                "--json", "item", "migrate", "--to", "sphinx_needs",
                "--path", "docs/items.rst", "--from", "markdown",
            ]
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["converted"] == ["BUG_001", "BUG_002"]
        lines = (manifest.root / "docs/items.rst").read_text(encoding="utf-8").splitlines()
        line = 1 + next(
            n for n, text in enumerate(lines) if ".. invariant-unconverted::" in text
        )
        assert payload["unconverted"] == [
            {
                "id": "BUG_001",
                "path": "docs/items.rst",
                "line": line,
                "reason": "raw html <kbd>x</kbd>",
            }
        ]
        assert ["-f", "markdown", "-t", "json"] in fake.calls
        main(["item", "migrate", "--to", "sphinx_needs", "--path", "docs/again.rst", "--dry-run"])
        out = capsys.readouterr().out
        assert "1 construct(s) left as .. invariant-unconverted::" in out
        assert f"docs/again.rst:{line}  BUG_001  raw html <kbd>x</kbd>" in out

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
    manifest = project(tmp_path, {"backend": "markdown", "path": "backlog"}, docs=True)
    shutil.copytree(source, manifest.root / "backlog")
    expected = MarkdownBackend(manifest.root / "backlog").load_all()
    assert len(expected) == len(files)

    domain = WorkItemDomain(manifest)
    done = domain.migrate(
        "sphinx_needs",
        manifest.root / "docs/backlog.rst",
        closed_path=manifest.root / "docs/closed.rst",
        pandoc=Echo(),
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


# -- the proof in a real pinned environment: converted, built strictly ---------------


def _real_env() -> Path | None:
    built = os.environ.get("INVARIANT_TEST_SPHINX_BUILD")
    return Path(built).resolve().parent.parent if built else None


def _real_pandoc() -> list[str]:
    env = _real_env()
    if env is None:
        return []
    return glob.glob(str(env / "lib" / "python*" / "site-packages" / "pypandoc" / "files" / "pandoc"))


@pytest.mark.skipif(
    not _real_pandoc(),
    reason="set INVARIANT_TEST_SPHINX_BUILD to a pinned sphinx-build (sphinx-needs 9.2, "
    "the starter's) whose environment carries pypandoc_binary",
)
def test_this_repositorys_backlog_converts_and_builds_but_for_what_is_named(
    tmp_path, monkeypatch, capsys
):
    from test_adr_018_starter import repo

    root = repo(
        tmp_path / "toy",
        {"pyproject.toml": '[project]\nname = "toy"\nversion = "0"\n', "pkg/__init__.py": ""},
    )
    monkeypatch.chdir(root)
    assert main(["init", "--ecosystem", "python"]) == 0
    env = _real_env()
    venv = root / "docs" / ".venv"
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    (venv / "bin" / "sphinx-build").symlink_to(os.environ["INVARIANT_TEST_SPHINX_BUILD"])
    (venv / "lib").symlink_to(env / "lib")
    digest = hashlib.sha256((root / "docs/requirements.txt").read_bytes()).hexdigest()
    (venv / docs_domain.VENV_HASH).write_text(digest + "\n", encoding="utf-8")
    # The records the bodies link to, as increment 7 converts them first.
    records = {int(p.name[4:7]) for p in (REPO / "docs/adr").glob("ADR-*.md")}
    for n in range(1, max(records) + 1):
        assert main(["docs", "decision", "new", f"Record {n}"]) == 0
    # The copy sits where this repository keeps its backlog, beside the
    # docs project's pages, and the project is migrating onto it.
    shutil.copytree(REPO / "docs" / "backlog", root / "docs" / "backlog")
    path = root / MANIFEST_RELPATH
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["items"] = {**config.get("items", {}), "backend": "markdown", "path": "docs/backlog"}
    config["docs"]["needs"]["state"] = "migrating"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    capsys.readouterr()

    assert main(["--json", "item", "migrate", "--to", "sphinx_needs"]) == 0
    moved = json.loads(capsys.readouterr().out)
    assert moved["converted"], "this repository's items carry bodies"
    named = {(u["path"], u["line"]) for u in moved["unconverted"]}
    assert len(named) == len(moved["unconverted"])

    code = main(["--json", "docs", "build"])
    record = json.loads(capsys.readouterr().out)
    failures = record["diagnostics"]
    # What increment 7 starts from, kept beside the run.
    (tmp_path / "unconverted.json").write_text(
        json.dumps({"unconverted": moved["unconverted"], "diagnostics": failures}, indent=1),
        encoding="utf-8",
    )
    if not failures:
        assert code == 0 and not moved["unconverted"]
        return
    # Every failure is a construct the migration named, by item and line,
    # and every construct it named fails the build.
    assert code == 1
    for line in failures:
        assert 'Unknown directive type "invariant-unconverted"' in line, line
    assert {line.split(" ", 1)[0] for line in failures} == {f"{p}:{n}" for p, n in named}
