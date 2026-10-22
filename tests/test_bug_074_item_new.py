"""`item new` on a sectioned backlog with a closed store (BUG_074).

The repro (tests/test_bug_074_repro.py) pins the reported surface. These pin
the design around it:

* (a) a ``--field`` naming one of the item's own keys is honoured (``id``) or
  refused with the surface that does set it (``status``, ``type``, ``title``,
  ``residue``, ``history``) — never dropped;
* (b) ``--section`` / ``--after`` are honoured or refused, and a store with
  no sections refuses them rather than ignoring them;
* (c) ``items.closed_path`` is read by minting and by the explicit-id check.
"""

import subprocess

import pytest
import yaml

from invariant.cli.main import main
from invariant.core.errors import UsageError
from invariant.core.items import (
    Item,
    JsonBackend,
    MarkdownBackend,
    NullBackend,
    SphinxNeedsBackend,
    _headings,
    _split_lines,
    backend_for,
)
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.domains.workitem import WorkItemDomain

SECTIONED = (
    "Backlog\n"
    "=======\n"
    "\n"
    "Batch one\n"
    "---------\n"
    "\n"
    ".. bug:: First\n"
    "   :id: BUG_001\n"
    "   :status: filed\n"
    "\n"
    "   Body.\n"
    "\n"
    "Batch two\n"
    "---------\n"
    "\n"
    ".. bug:: Second\n"
    "   :id: BUG_002\n"
    "   :status: filed\n"
)


def raw(path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def rst(tmp_path, text: str, newline: str = "", **kw) -> SphinxNeedsBackend:
    path = tmp_path / "backlog.rst"
    with path.open("w", encoding="utf-8", newline=newline) as handle:
        handle.write(text)
    return SphinxNeedsBackend(path, **kw)


def new(item_id: str, title: str = "New") -> Item:
    return Item(id=item_id, type="bug", title=title, status="filed")


def project(tmp_path, items: dict) -> Manifest:
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=root, check=True)
    (root / MANIFEST_RELPATH).write_text(
        yaml.safe_dump({"version": 1, "items": items}), encoding="utf-8"
    )
    return Manifest.load(root)


# -- (b) placement on sphinx_needs ----------------------------------------


class TestSectionPlacement:
    def test_end_of_a_named_section_is_byte_exact(self, tmp_path):
        b = rst(tmp_path, SECTIONED)
        b.add(new("BUG_003"), section="Batch one")
        assert raw(b.path) == SECTIONED.replace(
            "   Body.\n\nBatch two",
            "   Body.\n\n.. bug:: New\n   :id: BUG_003\n   :status: filed\n\nBatch two",
        )

    def test_the_last_section_of_the_file(self, tmp_path):
        b = rst(tmp_path, SECTIONED)
        b.add(new("BUG_003"), section="Batch two")
        assert raw(b.path) == SECTIONED + (
            "\n.. bug:: New\n   :id: BUG_003\n   :status: filed\n"
        )

    def test_a_section_with_a_subsection_keeps_its_own_content(self, tmp_path):
        """After a subsection's title, content belongs to the subsection."""
        text = (
            "Top\n===\n\nIntro.\n\nSub\n---\n\n.. bug:: In sub\n   :id: BUG_001\n"
        )
        b = rst(tmp_path, text)
        b.add(new("BUG_002"), section="Top")
        assert raw(b.path) == (
            "Top\n===\n\nIntro.\n\n.. bug:: New\n   :id: BUG_002\n   :status: filed\n"
            "\nSub\n---\n\n.. bug:: In sub\n   :id: BUG_001\n"
        )

    def test_an_empty_section_and_an_overlined_title(self, tmp_path):
        text = "=====\nOuter\n=====\nEmpty\n-----\nNext\n----\n"
        b = rst(tmp_path, text)
        b.add(new("BUG_001"), section="Empty")
        assert raw(b.path) == (
            "=====\nOuter\n=====\nEmpty\n-----\n\n.. bug:: New\n   :id: BUG_001\n"
            "   :status: filed\n\nNext\n----\n"
        )
        b.add(new("BUG_002", "Two"), section="Outer")
        assert ".. bug:: Two" in raw(b.path).split("Empty")[0]

    def test_crlf_survives_and_an_unterminated_tail_stays_so(self, tmp_path):
        b = rst(tmp_path, "Batch\r\n-----\r\n\r\nProse.", newline="")
        b.add(new("BUG_001"), section="Batch")
        assert raw(b.path) == (
            "Batch\r\n-----\r\n\r\nProse.\r\n\r\n.. bug:: New\r\n   :id: BUG_001\r\n"
            "   :status: filed"
        )

    def test_a_missing_section_is_refused_and_names_the_ones_there(self, tmp_path):
        b = rst(tmp_path, SECTIONED)
        with pytest.raises(UsageError, match=r"no section titled 'Batch three'.*'Batch one'"):
            b.add(new("BUG_003"), section="Batch three")
        assert raw(b.path) == SECTIONED

    def test_a_missing_section_in_a_file_without_headings(self, tmp_path):
        b = rst(tmp_path, ".. bug:: A\n   :id: BUG_001\n")
        with pytest.raises(UsageError, match="sections here: none"):
            b.add(new("BUG_002"), section="Any")

    def test_two_sections_of_one_title_are_refused(self, tmp_path):
        text = "Batch\n-----\n\nA.\n\nBatch\n-----\n\nB.\n"
        b = rst(tmp_path, text)
        with pytest.raises(UsageError, match=r"2 sections are titled 'Batch' \(lines 1, 6\)"):
            b.add(new("BUG_001"), section="Batch")
        assert raw(b.path) == text

    def test_a_short_underline_is_not_read_as_a_title(self, tmp_path):
        b = rst(tmp_path, "Batch one\n---\n\nProse.\n")
        with pytest.raises(UsageError, match="no section"):
            b.add(new("BUG_001"), section="Batch one")

    def test_what_is_not_a_title(self):
        lines = _split_lines(
            ".. bug:: T\n=========\n   indented\n   --------\n=====\n=====\n"
            "-----\nnot over\n=====\n"
        )
        assert [h.title for h in _headings(lines)] == []


class TestAfterPlacement:
    def test_directly_after_the_named_entry(self, tmp_path):
        b = rst(tmp_path, SECTIONED)
        b.add(new("BUG_003"), after="BUG_001")
        assert raw(b.path) == SECTIONED.replace(
            "   Body.\n\nBatch two",
            "   Body.\n\n.. bug:: New\n   :id: BUG_003\n   :status: filed\n\nBatch two",
        )

    def test_an_unknown_anchor_is_refused(self, tmp_path):
        b = rst(tmp_path, SECTIONED)
        with pytest.raises(UsageError, match="--after 'BUG_009' names no entry"):
            b.add(new("BUG_003"), after="BUG_009")
        assert raw(b.path) == SECTIONED

    def test_section_and_after_together_are_refused(self, tmp_path):
        b = rst(tmp_path, SECTIONED)
        with pytest.raises(UsageError, match="two placements"):
            b.add(new("BUG_003"), section="Batch one", after="BUG_001")


class TestAddIsForNewItems:
    def test_no_placement_keeps_the_bug_006_rule(self, tmp_path):
        b = rst(tmp_path, SECTIONED)
        b.add(new("BUG_003"))
        assert raw(b.path) == SECTIONED + (
            "\n.. bug:: New\n   :id: BUG_003\n   :status: filed\n"
        )

    @pytest.mark.parametrize("make", ["rst", "markdown", "json"])
    def test_an_existing_id_is_refused(self, tmp_path, make):
        if make == "rst":
            b = rst(tmp_path, SECTIONED)
        elif make == "markdown":
            b = MarkdownBackend(tmp_path / "backlog")
            b.save(new("BUG_001"))
        else:
            b = JsonBackend(tmp_path / "backlog.json")
            b.save(new("BUG_001"))
        with pytest.raises(UsageError, match="BUG_001 already exists"):
            b.add(new("BUG_001", "Again"))

    @pytest.mark.parametrize("option", ["section", "after"])
    @pytest.mark.parametrize(
        "make, why",
        [
            (lambda p: MarkdownBackend(p / "backlog"), "one file per item"),
            (lambda p: JsonBackend(p / "backlog.json"), "JSON document has no sections"),
        ],
    )
    def test_a_store_without_sections_refuses_a_placement(self, tmp_path, make, why, option):
        b = make(tmp_path)
        with pytest.raises(UsageError, match=why):
            b.add(new("BUG_001"), **{option: "x"})
        assert b.load_all() == []

    def test_markdown_and_json_add_without_placement(self, tmp_path):
        for b in (MarkdownBackend(tmp_path / "backlog"), JsonBackend(tmp_path / "b.json")):
            b.add(new("BUG_001"))
            assert [i.id for i in b.load_all()] == ["BUG_001"]

    def test_the_null_backend(self):
        b = NullBackend()
        assert b.taken_ids() == {}
        with pytest.raises(UsageError, match="disabled"):
            b.add(new("BUG_001"))


# -- (c) the closed store ------------------------------------------------


class TestClosedStore:
    def test_numeric_minting_reads_the_closed_rst(self, tmp_path):
        closed = tmp_path / "closed.rst"
        closed.write_text(".. bug:: Old\n   :id: BUG_007\n   :status: closed\n")
        b = rst(tmp_path, SECTIONED, closed_path=closed)
        assert b.next_id("bug") == "BUG_008"
        assert b.taken_ids() == {
            "BUG_007": closed,
            "BUG_001": b.path,
            "BUG_002": b.path,
        }

    def test_slug_minting_reads_a_closed_markdown_directory(self, tmp_path):
        closed = MarkdownBackend(tmp_path / "closed")
        closed.save(Item(id="BUG_DRAIN_STALLS", type="bug", title="x", status="closed"))
        b = MarkdownBackend(
            tmp_path / "open", id_strategy="slug", closed_path=tmp_path / "closed"
        )
        assert b.next_id("bug", "Drain stalls") == "BUG_DRAIN_STALLS_2"

    def test_a_declared_closed_store_that_is_absent_is_an_error(self, tmp_path):
        b = JsonBackend(tmp_path / "b.json", closed_path=tmp_path / "gone.json")
        with pytest.raises(UsageError, match="items.closed_path names .*gone.json"):
            b.next_id("bug")

    def test_unset_reads_only_the_open_store(self, tmp_path):
        assert rst(tmp_path, SECTIONED).next_id("bug") == "BUG_003"

    def test_backend_for_passes_the_key(self, tmp_path):
        m = project(
            tmp_path,
            {"backend": "sphinx_needs", "path": "b.rst", "closed_path": "closed.rst"},
        )
        b = backend_for(m)
        assert b.closed_path == (m.root / "closed.rst").resolve()
        assert backend_for(project(tmp_path / "x", {"backend": "json"})).closed_path is None


# -- (a) the domain: reserved keys and the explicit id --------------------


@pytest.fixture
def domain(tmp_path):
    closed = tmp_path / "proj" / "docs" / "closed.rst"
    m = project(
        tmp_path,
        {
            "backend": "sphinx_needs",
            "path": "docs/backlog.rst",
            "closed_path": "docs/closed.rst",
        },
    )
    closed.parent.mkdir(parents=True)
    closed.write_text(".. bug:: Old\n   :id: BUG_RETIRED\n   :status: closed\n")
    (m.root / "docs" / "backlog.rst").write_text(SECTIONED)
    return WorkItemDomain(m)


class TestReservedFields:
    @pytest.mark.parametrize(
        "key, says",
        [
            ("status", r"initial state \(filed\).*item transition"),
            ("type", "positional"),
            ("title", "--title"),
            ("residue", "item residue"),
            ("history", "item transition"),
        ],
    )
    def test_refused_naming_where_it_is_set(self, domain, key, says):
        before = (domain.manifest.root / "docs" / "backlog.rst").read_text()
        with pytest.raises(UsageError, match=says):
            domain.new("bug", "x", fields={key: "v"})
        assert (domain.manifest.root / "docs" / "backlog.rst").read_text() == before

    def test_an_empty_title_is_still_refused(self, domain):
        with pytest.raises(UsageError, match="needs a title"):
            domain.new("bug", "  ")


class TestExplicitId:
    def test_honoured_and_written_once(self, domain):
        item = domain.new("bug", "x", fields={"id": " BUG_MINE ", "area": "a"})
        assert (item.id, item.fields) == ("BUG_MINE", {"area": "a"})
        assert domain.get("BUG_MINE").fields == {"area": "a"}

    def test_a_retired_id_is_refused_naming_the_closed_store(self, domain):
        with pytest.raises(UsageError, match=r"clashes with BUG_RETIRED, already in docs/closed.rst"):
            domain.new("bug", "x", fields={"id": "BUG_RETIRED"})

    def test_a_clash_in_case_only_is_refused(self, domain):
        with pytest.raises(UsageError, match=r"clashes with BUG_001, already in docs/backlog.rst"):
            domain.new("bug", "x", fields={"id": "bug_001"})

    @pytest.mark.parametrize("bad", ["", "../x", "two words", "_lead"])
    def test_a_malformed_id_is_refused(self, domain, bad):
        with pytest.raises(UsageError, match="is not an id"):
            domain.new("bug", "x", fields={"id": bad})

    def test_placement_reaches_the_backend(self, domain):
        domain.new("bug", "Placed", section="Batch one")
        text = (domain.manifest.root / "docs" / "backlog.rst").read_text()
        assert text.index("Placed") < text.index("Batch two")

    def test_a_store_outside_the_root_is_shown_whole(self, tmp_path):
        outside = tmp_path / "elsewhere.json"
        outside.write_text('{"items": [{"id": "BUG_X", "type": "bug", "title": "t", "status": "s"}]}')
        m = project(tmp_path, {"backend": "json", "closed_path": str(outside)})
        with pytest.raises(UsageError, match=f"already in {outside.resolve()}"):
            WorkItemDomain(m).new("bug", "x", fields={"id": "BUG_X"})


# -- the CLI surface -----------------------------------------------------


@pytest.fixture
def markdown_project(tmp_path, monkeypatch):
    m = project(tmp_path, {"backend": "markdown", "path": "docs/backlog"})
    monkeypatch.chdir(m.root)
    monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
    return m


def cli(*argv) -> int:
    try:
        return main(["item", "new", *argv])
    except SystemExit as exc:
        return int(exc.code or 0)


class TestCli:
    @pytest.mark.parametrize(
        "argv, says",
        [
            (["--field", "severity"], "is not key=value"),
            (["--field", "=x"], "is not key=value"),
            (["--field", "a=1", "--field", "a=2"], "given twice"),
            (["--section", "Batch"], "one file per item"),
            (["--after", "BUG_001"], "one file per item"),
        ],
    )
    def test_refused_and_nothing_filed(self, markdown_project, capsys, argv, says):
        assert cli("bug", "--title", "x", *argv) == 2
        assert says in capsys.readouterr().err
        assert not (markdown_project.root / "docs" / "backlog").exists()

    def test_an_explicit_id_on_markdown(self, markdown_project):
        assert cli("bug", "--title", "x", "--field", "id=BUG_NAMED", "--field", "a=b=c") == 0
        item = WorkItemDomain(markdown_project).get("BUG_NAMED")
        assert item.fields == {"a": "b=c"}
