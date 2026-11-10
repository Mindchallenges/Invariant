"""ADR-018 increment 6: `docs migrate adr` and `docs migrate prose` (D14).

The converter is pandoc, pinned in the docs environment (``pypandoc_binary``),
so the engine gains no dependency. invariant reads pandoc's document tree
(``-t json``), rewrites it (`core/mdconvert`), and has pandoc write the
reStructuredText. The suite never needs pandoc: a stand-in answers each
Markdown text with the tree a test gives it and writes a plain rendering of
the tree back. What the stand-in cannot prove, a real conversion building
strictly, is proven behind ``INVARIANT_TEST_SPHINX_BUILD`` when the pinned
environment it names carries ``pypandoc_binary``: a copy of ADR-016 converted
with D1 to D9 as needs, and an unconvertible construct failing the build.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.cli.main import main
from invariant.core import mdconvert
from invariant.core.errors import UsageError
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.domains import docs as docs_domain

REPO = Path(__file__).resolve().parents[1]
API = [1, 23, 1, 1]

# -- building pandoc trees by hand -------------------------------------------------


def words(text: str) -> list[dict]:
    out: list[dict] = []
    for n, word in enumerate(text.split(" ")):
        if n:
            out.append({"t": "Space"})
        out.append({"t": "Str", "c": word})
    return out


def header(level: int, text: str, ident: str = "h") -> dict:
    return {"t": "Header", "c": [level, [ident, [], []], words(text)]}


def para(*parts) -> dict:
    return {"t": "Para", "c": [p for part in parts for p in (words(part) if isinstance(part, str) else [part])]}


def plain(*parts) -> dict:
    block = para(*parts)
    block["t"] = "Plain"
    return block


def link(text: str, url: str) -> dict:
    return {"t": "Link", "c": [["", [], []], words(text), [url, ""]]}


def strong(text: str) -> dict:
    return {"t": "Strong", "c": words(text)}


def bullets(*items: list[dict]) -> dict:
    return {"t": "BulletList", "c": [list(i) for i in items]}


def ordered(start: int, *items: list[dict]) -> dict:
    return {"t": "OrderedList", "c": [[start, {"t": "Decimal"}, {"t": "Period"}], [list(i) for i in items]]}


def code(text: str, lang: str = "") -> dict:
    return {"t": "CodeBlock", "c": [["", [lang] if lang else [], []], text]}


def raw_inline(fmt: str, text: str) -> dict:
    return {"t": "RawInline", "c": [fmt, text]}


def doc(*blocks: dict) -> dict:
    return {"pandoc-api-version": API, "meta": {}, "blocks": list(blocks)}


def inline_text(inlines) -> str:
    out = []
    for node in inlines:
        kind = node["t"]
        if kind == "Str":
            out.append(node["c"])
        elif kind in ("Space", "SoftBreak", "LineBreak"):
            out.append(" ")
        elif kind == "Code":
            (_, classes, attrs), text = node["c"]
            role = dict(attrs).get("role")
            out.append(f":{role}:`{text}`" if "interpreted-text" in classes else f"``{text}``")
        elif kind in ("Strong", "Emph"):
            out.append(f"**{inline_text(node['c'])}**")
        elif kind == "Link":
            out.append(f"`{inline_text(node['c'][1])} <{node['c'][2][0]}>`__")
        elif kind == "RawInline":
            out.append(node["c"][1])
    return "".join(out)


def render(blocks) -> str:
    """The stand-in's writer: a plain, deterministic rendering of a tree."""
    out = []
    for block in blocks:
        kind, c = block["t"], block.get("c")
        if kind == "RawBlock":
            out.append(c[1])
        elif kind == "Header":
            text = inline_text(c[2])
            label = f".. _{c[1][0]}:\n\n" if c[1][0] else ""
            out.append(f"{label}{text}\n{'=-~^'[c[0] - 1] * len(text)}")
        elif kind in ("Para", "Plain"):
            out.append(inline_text(c))
        elif kind == "CodeBlock":
            out.append("::\n\n" + "\n".join("   " + line for line in c[1].split("\n")))
        elif kind == "BulletList":
            out.append("\n".join("- " + render(item) for item in c))
        elif kind == "OrderedList":
            start = c[0][0]
            out.append("\n".join(f"{start + n}. " + render(item) for n, item in enumerate(c[1])))
        else:
            out.append(f"<{kind}>")
    return "\n\n".join(out)


class FakePandoc:
    """Answers ``-t json`` from the trees it is given, renders the rest."""

    def __init__(self, trees: dict[str, dict] | None = None) -> None:
        self.trees = dict(trees or {})
        self.calls: list[list[str]] = []

    def __call__(self, args, text: str) -> str:
        self.calls.append(list(args))
        args = list(args)
        if args[args.index("-t") + 1] == "json":
            return json.dumps(self.trees[text])
        tree = json.loads(text)
        return render(tree["blocks"]) + "\n"


def target(role=None, ref=None, problem=None, note=None):
    return mdconvert.Target(role=role, target=ref, problem=problem, note=note)


def linker(mapping: dict[str, mdconvert.Target]):
    return lambda url: mapping.get(url, mdconvert.Target())


# -- core/mdconvert: prose -------------------------------------------------------


class TestProse:
    def convert(self, tree, links=None):
        fake = FakePandoc({"md": tree})
        return mdconvert.convert_prose("md", pandoc=fake, link=linker(links or {})), fake

    def test_headers_lose_their_ids_and_links_become_roles(self):
        tree = doc(
            header(1, "Title", "title"),
            para("See ", link("the record", "ADR-014-x.md#d1"), " and ", link("cli", "cli.md"), "."),
            para(link("site", "https://example.invalid/")),
        )
        done, fake = self.convert(
            tree,
            {
                "ADR-014-x.md#d1": target("need", "DEC_ADR_014", note="anchor #d1 dropped"),
                "cli.md": target("doc", "/architecture/cli"),
            },
        )
        assert done.text.startswith("Title\n=====\n")
        assert ".. _title:" not in done.text
        assert ":need:`the record <DEC_ADR_014>`" in done.text
        assert ":doc:`cli </architecture/cli>`" in done.text
        assert "<https://example.invalid/>" in done.text
        assert done.notes == ("anchor #d1 dropped",)
        assert done.unconverted == ()
        assert fake.calls[0] == ["-f", "gfm", "-t", "json"]
        assert fake.calls[-1][:4] == ["-f", "json", "-t", "rst"]

    def test_role_text_is_escaped(self):
        tree = doc(para(link("a <b> `c`", "x.md")))
        done, _ = self.convert(tree, {"x.md": target("doc", "/x")})
        assert ":doc:`a \\<b> \\`c\\` </x>`" in done.text

    def test_a_link_with_no_text_names_its_target_alone(self):
        tree = doc(para({"t": "Link", "c": [["", [], []], [], ["x.md", ""]]}))
        done, _ = self.convert(tree, {"x.md": target("doc", "/x")})
        assert ":doc:`/x`" in done.text

    def test_a_diagram_becomes_its_directive(self):
        tree = doc(code("graph TD\n  A-->B", "mermaid"), code("@startuml\n@enduml", "plantuml"), code("x = 1", "python"))
        done, _ = self.convert(tree)
        assert ".. mermaid::\n\n   graph TD\n     A-->B" in done.text
        assert ".. uml::\n\n   @startuml\n   @enduml" in done.text
        assert "::\n\n   x = 1" in done.text

    def test_an_unconvertible_block_is_left_as_the_directive(self):
        tree = doc(
            para("Keep this."),
            bullets([para("fine")], [para("Read ", link("the rules", "../rules.yaml"), ".")]),
            para("Raw ", raw_inline("html", "<area>"), " text."),
            {"t": "RawBlock", "c": ["html", "<div>\nx\n</div>"]},
            {"t": "RawBlock", "c": ["rst", ".. note:: kept"]},
        )
        done, fake = self.convert(
            tree, {"../rules.yaml": target(problem="a link to ../rules.yaml, not a page")}
        )
        text = done.text
        assert "Keep this." in text and "- fine" in text
        assert text.count(".. invariant-unconverted::") == 3
        assert ".. invariant-unconverted:: a link to ../rules.yaml, not a page\n\n   Read" in text
        assert "raw html <area>" in text
        assert ".. note:: kept" in text
        assert len(done.unconverted) == 3
        # The original Markdown is what the directive holds: re-rendered by pandoc.
        assert ["-f", "json", "-t", "gfm"] in fake.calls

    def test_a_header_with_an_unconvertible_link_is_left_too(self):
        tree = doc(
            {"t": "Header", "c": [2, ["x", [], []], [link("bad", "#x")]]},
        )
        done, _ = self.convert(tree, {"#x": target(problem="an in-page anchor")})
        assert ".. invariant-unconverted:: an in-page anchor" in done.text

    def test_a_problem_inside_a_table_cell_or_note_is_found(self):
        note = {"t": "Note", "c": [para(link("x", "bad"))]}
        tree = doc(para("Text", note), {"t": "Table", "c": [None, [link("y", "bad")]]})
        done, _ = self.convert(tree, {"bad": target(problem="bad link")})
        assert len(done.unconverted) == 2

    def test_a_block_quote_and_a_div_are_recursed_into(self):
        tree = doc(
            {"t": "BlockQuote", "c": [para(raw_inline("html", "<b>"))]},
            {"t": "Div", "c": [["", [], []], [para("ok")]]},
            {"t": "HorizontalRule"},
        )
        done, _ = self.convert(tree)
        assert len(done.unconverted) == 1

    def test_an_html_comment_stays_a_comment(self):
        tree = doc(
            {"t": "RawBlock", "c": ["html", "<!-- invariant: allow-privacy -->"]},
            para("Covered."),
            {"t": "RawBlock", "c": ["html", "<!--\n  two\n  lines\n-->"]},
            {"t": "RawBlock", "c": ["html", "<!---->"]},
        )
        done, _ = self.convert(tree)
        assert ".. invariant: allow-privacy\n\nCovered." in done.text
        assert "..\n   two\n   lines" not in done.text and ".. two\n   lines" in done.text
        assert done.unconverted == ()

    def test_plain_text_reads_through_every_inline(self):
        inlines = [
            {"t": "Quoted", "c": [{"t": "DoubleQuote"}, words("q")]},
            {"t": "Space"},
            {"t": "Math", "c": [{"t": "InlineMath"}, "x+1"]},
            {"t": "SoftBreak"},
            {"t": "Image", "c": [["", [], []], words("alt"), ["i.png", ""]]},
            {"t": "Span", "c": [["", [], []], [{"t": "Emph", "c": words("em")}]]},
            {"t": "Note", "c": [para("dropped")]},
        ]
        assert mdconvert.plain(inlines) == '"q" x+1 altem'

    def test_nested_text_takes_rubrics_and_leaves_a_rule(self):
        tree = doc(header(2, "Symptom"), para("Text."), {"t": "HorizontalRule"}, para("More."))
        fake = FakePandoc({"md": tree})
        done = mdconvert.convert_prose("md", pandoc=fake, link=linker({}), nested=True)
        assert ".. rubric:: Symptom" in done.text
        assert "Symptom\n-------" not in done.text
        assert done.unconverted == (mdconvert.NESTED_RULE,)
        assert f".. invariant-unconverted:: {mdconvert.NESTED_RULE}" in done.text
        # A page is not nested: the heading stays a section and the rule a transition.
        page = mdconvert.convert_prose("md", pandoc=FakePandoc({"md": tree}), link=linker({}))
        assert "rubric" not in page.text and page.unconverted == ()

    def test_pandoc_reader_is_configurable(self):
        fake = FakePandoc({"md": doc(para("x"))})
        mdconvert.convert_prose("md", pandoc=fake, link=linker({}), reader="markdown")
        assert fake.calls[0] == ["-f", "markdown", "-t", "json"]

    @pytest.mark.parametrize("out", ["not json", "[]", '{"blocks": null}'])
    def test_a_tree_pandoc_cannot_give_is_refused(self, out):
        with pytest.raises(UsageError, match="pandoc gave no document tree"):
            mdconvert.convert_prose("md", pandoc=lambda a, t: out, link=linker({}))


# -- core/mdconvert: a decision record ------------------------------------------------


ADR_TREE = doc(
    header(1, "ADR-016 — The docs are assessed", "adr-016"),
    bullets(
        [plain(strong("Status:"), " Accepted (2026-11-07). Eleven rulings.")],
        [plain(strong("Date:"), " 2026-11-07")],
        [
            plain(
                strong("Supersedes:"),
                " ",
                link("ADR-003", "ADR-003-x.md"),
                ". Amends ",
                link("ADR-012", "ADR-012-y.md"),
                " and cli.md. Builds on ",
                link("ADR-014", "ADR-014-z.md"),
                ".",
            )
        ],
    ),
    header(2, "Context", "context"),
    para("Some context."),
    header(3, "Rulings (user)", "rulings"),
    bullets([plain("A. Only the docs.")], [plain("B. Deterministic.")], [plain("plain item")]),
    ordered(1, [plain(strong("Incremental."), " one")], [plain("two")]),
    header(2, "Decisions", "decisions"),
    header(3, "D1 — One assessment", "d1"),
    para("First decision."),
    header(4, "A detail", "detail"),
    para("More."),
    header(3, "D2: Areas", "d2"),
    para("Second, see ", link("ADR-014", "ADR-014-z.md"), "."),
    header(2, "Increments", "increments"),
    para("Table."),
)


def links_for(prefix="DEC_"):
    return linker(
        {
            f"ADR-{n:03d}-{s}.md": target("need", f"{prefix}ADR_{n:03d}")
            for n, s in ((3, "x"), (12, "y"), (14, "z"))
        }
    )


def convert_adr(tree=ADR_TREE, **kw):
    fake = FakePandoc({"md": tree})
    args = {
        "number": 16,
        "directive": "dec",
        "prefix": "DEC_",
        "statuses": ("proposed", "accepted", "superseded", "rejected"),
        "pandoc": fake,
        "link": links_for(),
    }
    args.update(kw)
    return mdconvert.convert_adr("md", **args)


class TestAdr:
    def test_the_record_and_each_decision_are_needs(self):
        done = convert_adr()
        text = done.text
        assert done.number == 16 and done.title == "The docs are assessed"
        assert done.needs == ("DEC_ADR_016", "DEC_ADR_016_D1", "DEC_ADR_016_D2")
        assert text.startswith(".. _adr-016:\n\nADR-016 — The docs are assessed\n===")
        assert (
            ".. dec:: The docs are assessed\n"
            "   :id: DEC_ADR_016\n"
            "   :status: accepted\n"
            "   :supersedes: DEC_ADR_003\n"
            "   :amends: DEC_ADR_012\n"
        ) in text
        assert "   - **Status:** Accepted" in text
        assert (
            ".. dec:: One assessment\n"
            "   :id: DEC_ADR_016_D1\n"
            "   :status: accepted\n"
            "   :part_of: DEC_ADR_016\n"
            "\n"
            "   First decision.\n"
            "\n"
            "   .. rubric:: A detail\n"
        ) in text
        assert ".. dec:: Areas\n   :id: DEC_ADR_016_D2" in text
        assert "   Second, see :need:`ADR-014 <DEC_ADR_014>`." in text
        # The decisions' own headings are the needs; other sections stay.
        assert "D1 — One assessment\n" not in text
        assert "Increments\n----------" in text

    def test_each_ruling_is_its_own_paragraph_under_a_label(self):
        text = convert_adr().text
        assert ".. _adr-016-ruling-a:\n\n- A. Only the docs." in text
        assert ".. _adr-016-ruling-b:\n\n- B. Deterministic." in text
        assert "- plain item" in text and "ruling-plain" not in text
        assert ".. _adr-016-ruling-1:\n\n1. **Incremental.** one" in text
        assert ".. _adr-016-ruling-2:\n\n2. two" in text

    def test_a_ruling_numbered_twice_is_labelled_once(self):
        tree = doc(
            header(1, "ADR-016 — T"),
            bullets([plain(strong("Status:"), " Proposed")]),
            header(2, "Rulings"),
            ordered(1, [plain("one")]),
            header(2, "Ruling, again"),
            ordered(1, [plain("again")]),
            header(2, "After"),
            ordered(1, [plain("not a ruling")]),
        )
        text = convert_adr(tree).text
        assert text.count(".. _adr-016-ruling-1:") == 1
        assert "1. not a ruling" in text

    def test_the_prefix_and_directive_follow_the_decision_type(self):
        done = convert_adr(directive="decision", prefix="HEAP_DEC_", link=links_for("HEAP_DEC_"))
        assert done.needs[0] == "HEAP_DEC_ADR_016"
        assert ".. decision:: The docs are assessed" in done.text
        assert ":supersedes: HEAP_DEC_ADR_003" in done.text

    def test_the_number_comes_from_the_title_when_the_name_has_none(self):
        assert convert_adr(number=None).number == 16

    def test_no_amends_sentence_and_no_metadata_links(self):
        tree = doc(
            header(1, "ADR-002 — Telemetry"),
            bullets(
                [plain(strong("Status:"), " Accepted")],
                [plain(strong("Supersedes:"), " nothing. Extends ADR-001's model.")],
                [plain(strong("Amends:"), " ", link("ADR-001", "ADR-001-a.md"), " and ADR-005.")],
                [plain(strong("Amended by:"), " ADR-008")],
            ),
            para("Body."),
        )
        done = convert_adr(tree, number=2, link=linker({"ADR-001-a.md": target("need", "DEC_ADR_001")}))
        assert ":supersedes:" not in done.text
        assert ":amends: DEC_ADR_001, DEC_ADR_005" in done.text
        assert "DEC_ADR_008" not in done.text.split("Body.")[0].split(":amends:")[1]

    def test_a_metadata_note_is_content_and_a_sentence_saying_amends_links(self):
        tree = doc(
            header(1, "ADR-008 — Tailnet"),
            bullets(
                [plain(strong("Status:"), " Accepted.")],
                [plain(strong("Supersedes:"), " nothing. It amends ADR-007 in two places.")],
                [plain(strong("A note."), " Not a key.")],
            ),
        )
        done = convert_adr(tree, number=8)
        assert ":amends: DEC_ADR_007" in done.text
        assert "   - **A note.** Not a key." in done.text

    def test_a_list_opening_without_a_key_is_no_metadata(self):
        tree = doc(header(1, "ADR-008 — T"), bullets([plain("plain")]))
        with pytest.raises(UsageError, match="Status"):
            convert_adr(tree, number=8)

    @pytest.mark.parametrize(
        ("tree", "number", "said"),
        [
            (doc(para("no title")), 16, "no title"),
            (doc(header(1, "Something else")), None, "number"),
            (doc(header(1, "ADR-017 — T"), bullets([plain(strong("Status:"), " Accepted")])), 16, "ADR-016"),
            (doc(header(1, "ADR-016 — T"), para("x")), 16, "Status"),
            (doc(header(1, "ADR-016 — T"), bullets([plain(strong("Status:"), " Draft")])), 16, "draft"),
            (
                doc(
                    header(1, "ADR-016 — T"),
                    bullets([plain(strong("Status:"), " Accepted")]),
                    header(3, "D1 — a"),
                    header(3, "D1 — b"),
                ),
                16,
                "D1",
            ),
        ],
        ids=["no-title", "no-number", "two-numbers", "no-status", "unknown-status", "d-twice"],
    )
    def test_refusals(self, tree, number, said):
        with pytest.raises(UsageError, match=said):
            convert_adr(tree, number=number)

    def test_a_heading_with_no_title_after_the_number_is_the_record(self):
        tree = doc(header(1, "ADR-016"), bullets([plain(strong("Status:"), " Accepted")]))
        assert convert_adr(tree).title == "ADR-016"


# -- domains/docs: pandoc in the docs environment ---------------------------------------


def docs_project(tmp_path: Path, *, sync: bool = True, pandoc: bool = True) -> Manifest:
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=root, check=True)
    docs = root / "docs"
    (docs / "architecture").mkdir(parents=True)
    (docs / "decisions").mkdir()
    (docs / "conf.py").write_text("extensions = ['sphinx_needs']\n", encoding="utf-8")
    (docs / "requirements.txt").write_text("sphinx-needs==9.2.0\n", encoding="utf-8")
    (docs / "index.rst").write_text(
        "Toy\n===\n\n.. toctree::\n\n   architecture/overview\n   decisions/index\n",
        encoding="utf-8",
    )
    (docs / "decisions" / "index.rst").write_text("Decisions\n=========\n", encoding="utf-8")
    (docs / "architecture" / "overview.rst").write_text("Overview\n========\n", encoding="utf-8")
    venv = docs / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("", encoding="utf-8")
    if sync:
        digest = hashlib.sha256((docs / "requirements.txt").read_bytes()).hexdigest()
        (venv / docs_domain.VENV_HASH).write_text(digest + "\n", encoding="utf-8")
    if pandoc:
        binary = venv / "lib" / "python3.12" / "site-packages" / "pypandoc" / "files" / "pandoc"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\ncat\n", encoding="utf-8")
        binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    body = {
        "version": 1,
        "paths": {"docs": "docs/architecture"},
        "docs": {"format": "sphinx", "needs": {"srcdir": "docs"}},
    }
    (root / MANIFEST_RELPATH).write_text(yaml.safe_dump(body), encoding="utf-8")
    return Manifest.locate(root)


class TestPandocOf:
    def test_the_environments_own_pandoc_is_found(self, tmp_path):
        manifest = docs_project(tmp_path)
        found = docs_domain.pandoc_of(manifest.root / "docs")
        assert found.name == "pandoc" and "pypandoc" in found.parts

    def test_a_stale_environment_is_refused_naming_docs_venv(self, tmp_path):
        manifest = docs_project(tmp_path, sync=False)
        with pytest.raises(UsageError, match="invariant docs venv"):
            docs_domain.pandoc_of(manifest.root / "docs")

    def test_no_pypandoc_names_the_pin_to_add(self, tmp_path):
        manifest = docs_project(tmp_path, pandoc=False)
        with pytest.raises(UsageError) as refused:
            docs_domain.pandoc_of(manifest.root / "docs")
        assert docs_domain.PANDOC_PIN in str(refused.value)
        assert "requirements.txt" in str(refused.value) and "docs venv" in str(refused.value)

    def test_the_call_runs_the_binary_and_refuses_a_failure(self, tmp_path):
        manifest = docs_project(tmp_path)
        call = docs_domain.pandoc_call(docs_domain.pandoc_of(manifest.root / "docs"))
        assert call(["-f", "gfm"], "echoed\n") == "echoed\n"
        failing = tmp_path / "failing"
        failing.write_text("#!/bin/sh\necho broken >&2\nexit 3\n", encoding="utf-8")
        failing.chmod(failing.stat().st_mode | stat.S_IEXEC)
        with pytest.raises(UsageError, match="exit 3.*broken"):
            docs_domain.pandoc_call(failing)(["-t", "rst"], "x")


# -- domains/docs: migrate adr ---------------------------------------------------------


def adr_source(root: Path, name: str = "ADR-016-the-docs.md", text: str = "md") -> Path:
    path = root / "old" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestMigrateAdr:
    def test_the_page_is_written_and_listed(self, tmp_path):
        manifest = docs_project(tmp_path)
        source = adr_source(manifest.root)
        fake = FakePandoc({"md": ADR_TREE})
        (page,) = docs_domain.migrate_adr(manifest, [source], pandoc=fake)
        assert page.rel == "docs/decisions/ADR-016-the-docs-are-assessed.rst"
        assert page.needs[0] == "DEC_ADR_016" and len(page.needs) == 3
        assert page.path.read_text(encoding="utf-8") == page.text
        index = (manifest.root / "docs/decisions/index.rst").read_text(encoding="utf-8")
        assert "   ADR-016-the-docs-are-assessed" in index
        assert page.index == manifest.root / "docs/decisions/index.rst"
        assert page.source == "old/ADR-016-the-docs.md"
        # The Markdown is not deleted: the session deletes it (D14).
        assert source.exists()

    def test_links_to_other_records_and_pages(self, tmp_path):
        manifest = docs_project(tmp_path)
        (manifest.root / "docs/architecture/cli.rst").write_text("CLI\n===\n", encoding="utf-8")
        (manifest.root / "docs/guide").mkdir()
        (manifest.root / "docs/guide/usage.md").write_text("# Usage\n", encoding="utf-8")
        (manifest.root / "docs/rules.yaml").write_text("x: 1\n", encoding="utf-8")
        tree = doc(
            header(1, "ADR-016 — T"),
            bullets([plain(strong("Status:"), " Accepted")]),
            para(link("r", "ADR-014-z.md#d3")),
            para(link("c", "../docs/architecture/cli.md")),
            para(link("g", "../docs/guide/usage.md")),
            para(link("y", "../docs/rules.yaml")),
            para(link("i", "#context")),
            para(link("o", "../elsewhere.md")),
            para(link("e", "mailto:someone@example.invalid")),
        )
        source = adr_source(manifest.root)
        (page,) = docs_domain.migrate_adr(manifest, [source], pandoc=FakePandoc({"md": tree}))
        text = page.text
        assert ":need:`r <DEC_ADR_014>`" in text
        assert ":doc:`c </architecture/cli>`" in text
        assert ":doc:`g </guide/usage>`" in text
        assert text.count(".. invariant-unconverted::") == 3
        assert "rules.yaml" in text and "in-page anchor" in text and "elsewhere.md" in text
        assert "mailto:someone@example.invalid" in text
        assert any("#d3" in note for note in page.notes)

    def test_two_records_in_one_batch_are_listed_in_order(self, tmp_path):
        manifest = docs_project(tmp_path)
        second = doc(header(1, "ADR-002 — Two"), bullets([plain(strong("Status:"), " Accepted")]))
        first = doc(header(1, "ADR-001 — One"), bullets([plain(strong("Status:"), " Accepted")]))
        fake = FakePandoc({"two": second, "one": first})
        pages = docs_domain.migrate_adr(
            manifest,
            [adr_source(manifest.root, "ADR-002-two.md", "two"), adr_source(manifest.root, "ADR-001-one.md", "one")],
            pandoc=fake,
        )
        assert [p.needs[0] for p in pages] == ["DEC_ADR_001", "DEC_ADR_002"]
        index = (manifest.root / "docs/decisions/index.rst").read_text(encoding="utf-8")
        assert index.index("ADR-001-one") < index.index("ADR-002-two")

    def test_a_globbing_index_is_left_alone(self, tmp_path):
        manifest = docs_project(tmp_path)
        globbing = "Decisions\n=========\n\n.. toctree::\n   :glob:\n\n   ADR-*\n"
        (manifest.root / "docs/decisions/index.rst").write_text(globbing, encoding="utf-8")
        (page,) = docs_domain.migrate_adr(
            manifest, [adr_source(manifest.root)], pandoc=FakePandoc({"md": ADR_TREE})
        )
        assert page.index is None and page.path.exists()
        assert (manifest.root / "docs/decisions/index.rst").read_text(encoding="utf-8") == globbing

    def test_a_dry_run_writes_nothing(self, tmp_path):
        manifest = docs_project(tmp_path)
        before = (manifest.root / "docs/decisions/index.rst").read_text(encoding="utf-8")
        (page,) = docs_domain.migrate_adr(
            manifest, [adr_source(manifest.root)], pandoc=FakePandoc({"md": ADR_TREE}), dry_run=True
        )
        assert not page.path.exists()
        assert (manifest.root / "docs/decisions/index.rst").read_text(encoding="utf-8") == before

    def test_a_spent_number_is_refused_and_nothing_written(self, tmp_path):
        manifest = docs_project(tmp_path)
        (manifest.root / "docs/decisions/ADR-016-held.rst").write_text("x\n", encoding="utf-8")
        fresh = doc(header(1, "ADR-001 — One"), bullets([plain(strong("Status:"), " Accepted")]))
        with pytest.raises(UsageError, match="ADR-016.*spent"):
            docs_domain.migrate_adr(
                manifest,
                [adr_source(manifest.root, "ADR-001-one.md", "one"), adr_source(manifest.root)],
                pandoc=FakePandoc({"md": ADR_TREE, "one": fresh}),
            )
        assert not list((manifest.root / "docs/decisions").glob("ADR-001*"))

    def test_a_number_spent_by_a_need_id_is_refused(self, tmp_path):
        manifest = docs_project(tmp_path)
        (manifest.root / "docs/decisions/page.rst").write_text(
            ".. dec:: Held\n   :id: DEC_ADR_016\n", encoding="utf-8"
        )
        with pytest.raises(UsageError, match="spent"):
            docs_domain.migrate_adr(
                manifest, [adr_source(manifest.root)], pandoc=FakePandoc({"md": ADR_TREE})
            )

    def test_one_number_twice_in_a_batch_is_refused(self, tmp_path):
        manifest = docs_project(tmp_path)
        with pytest.raises(UsageError, match="twice"):
            docs_domain.migrate_adr(
                manifest,
                [adr_source(manifest.root), adr_source(manifest.root, "ADR-016-again.md")],
                pandoc=FakePandoc({"md": ADR_TREE}),
            )

    def test_no_decisions_index_is_refused(self, tmp_path):
        manifest = docs_project(tmp_path)
        (manifest.root / "docs/decisions/index.rst").unlink()
        with pytest.raises(UsageError, match="decisions/index.rst"):
            docs_domain.migrate_adr(
                manifest, [adr_source(manifest.root)], pandoc=FakePandoc({"md": ADR_TREE})
            )

    def test_a_missing_source_is_refused(self, tmp_path):
        manifest = docs_project(tmp_path)
        with pytest.raises(UsageError, match="no such file"):
            docs_domain.migrate_adr(manifest, [manifest.root / "nope.md"], pandoc=FakePandoc())

    def test_the_environments_pandoc_is_used_by_default(self, tmp_path, monkeypatch):
        manifest = docs_project(tmp_path)
        seen = []
        monkeypatch.setattr(
            docs_domain,
            "pandoc_call",
            lambda binary: seen.append(binary) or FakePandoc({"md": ADR_TREE}),
        )
        docs_domain.migrate_adr(manifest, [adr_source(manifest.root)], dry_run=True)
        assert seen and seen[0].name == "pandoc"


# -- domains/docs: migrate prose ---------------------------------------------------------


class TestMigrateProse:
    def test_beside_its_source_listed_in_the_nearest_index(self, tmp_path):
        manifest = docs_project(tmp_path)
        (manifest.root / "docs/architecture/index.rst").write_text(
            "Architecture\n============\n\n.. toctree::\n\n   overview\n", encoding="utf-8"
        )
        cli = manifest.root / "docs/architecture/cli.md"
        cli.write_text("cli", encoding="utf-8")
        state = manifest.root / "docs/architecture/state.md"
        state.write_text("state", encoding="utf-8")
        trees = {
            "cli": doc(header(1, "CLI"), para(link("state", "state.md#x"))),
            "state": doc(header(1, "State"), code("graph TD", "mermaid")),
        }
        pages = docs_domain.migrate_prose(manifest, [cli, state], pandoc=FakePandoc(trees))
        assert [p.rel for p in pages] == ["docs/architecture/cli.rst", "docs/architecture/state.rst"]
        assert ":doc:`state </architecture/state>`" in pages[0].text
        assert pages[0].element_owed and pages[1].element_owed
        index = (manifest.root / "docs/architecture/index.rst").read_text(encoding="utf-8")
        assert "   overview\n   cli\n   state\n" in index
        assert pages[0].index == manifest.root / "docs/architecture/index.rst"

    def test_into_a_directory_listed_in_the_root_index(self, tmp_path):
        manifest = docs_project(tmp_path)
        source = tmp_path / "elsewhere" / "guide.md"
        source.parent.mkdir()
        source.write_text("g", encoding="utf-8")
        (page,) = docs_domain.migrate_prose(
            manifest,
            [source],
            into=manifest.root / "docs/guide",
            pandoc=FakePandoc({"g": doc(header(1, "Guide"))}),
        )
        assert page.rel == "docs/guide/guide.rst"
        assert not page.element_owed
        root_index = (manifest.root / "docs/index.rst").read_text(encoding="utf-8")
        assert "   guide/guide" in root_index

    def test_no_index_is_a_note_not_a_refusal(self, tmp_path):
        manifest = docs_project(tmp_path)
        (manifest.root / "docs/index.rst").unlink()
        source = manifest.root / "docs/architecture/x.md"
        source.write_text("x", encoding="utf-8")
        (page,) = docs_domain.migrate_prose(
            manifest, [source], pandoc=FakePandoc({"x": doc(header(1, "X"))})
        )
        assert page.index is None
        assert any("toctree" in note for note in page.notes)

    def test_a_page_outside_the_docs_project_is_refused(self, tmp_path):
        manifest = docs_project(tmp_path)
        source = manifest.root / "README.md"
        source.write_text("r", encoding="utf-8")
        with pytest.raises(UsageError, match="--into"):
            docs_domain.migrate_prose(manifest, [source], pandoc=FakePandoc())

    def test_an_existing_page_is_never_written_over(self, tmp_path):
        manifest = docs_project(tmp_path)
        source = manifest.root / "docs/architecture/overview.md"
        source.write_text("o", encoding="utf-8")
        with pytest.raises(UsageError, match="exists"):
            docs_domain.migrate_prose(manifest, [source], pandoc=FakePandoc())

    def test_two_sources_onto_one_page_are_refused(self, tmp_path):
        manifest = docs_project(tmp_path)
        a = tmp_path / "a" / "x.md"
        b = tmp_path / "b" / "x.md"
        for p in (a, b):
            p.parent.mkdir()
            p.write_text("x", encoding="utf-8")
        with pytest.raises(UsageError, match="twice"):
            docs_domain.migrate_prose(manifest, [a, b], into=manifest.root / "docs/architecture", pandoc=FakePandoc())


# -- the CLI ----------------------------------------------------------------------------------


class TestCli:
    def test_migrate_adr_names_what_each_page_owes(self, tmp_path, monkeypatch, capsys):
        manifest = docs_project(tmp_path)
        adr_source(manifest.root)
        tree = doc(*ADR_TREE["blocks"], para(link("bad", "#x")))
        monkeypatch.setattr(docs_domain, "pandoc_call", lambda binary: FakePandoc({"md": tree}))
        monkeypatch.chdir(manifest.root)
        assert main(["docs", "migrate", "adr", "old/ADR-016-the-docs.md"]) == 0
        out = capsys.readouterr().out
        assert "wrote docs/decisions/ADR-016-the-docs-are-assessed.rst" in out
        assert "DEC_ADR_016 and 2 decision need(s)" in out
        assert "1 construct(s) left as .. invariant-unconverted::" in out
        assert "require_links" in out and "invariant docs build" in out
        assert "old/ADR-016-the-docs.md" in out

    def test_migrate_prose_json(self, tmp_path, monkeypatch, capsys):
        manifest = docs_project(tmp_path)
        (manifest.root / "docs/architecture/x.md").write_text("x", encoding="utf-8")
        monkeypatch.setattr(
            docs_domain, "pandoc_call", lambda binary: FakePandoc({"x": doc(header(1, "X"))})
        )
        monkeypatch.chdir(manifest.root / "docs")
        assert main(["--json", "docs", "migrate", "prose", "architecture/x.md", "--dry-run"]) == 0
        payload = json.loads(capsys.readouterr().out)
        (page,) = payload["pages"]
        assert payload["dry_run"] is True
        assert page["path"] == "docs/architecture/x.rst"
        assert page["element_owed"] is True and page["unconverted"] == []
        assert not (manifest.root / "docs/architecture/x.rst").exists()
        main(["docs", "migrate", "prose", "architecture/x.md", "--dry-run"])
        out = capsys.readouterr().out
        assert "would write docs/architecture/x.rst" in out
        assert "element needs" in out


# -- the proof, in a real pinned environment ---------------------------------------------


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
class TestARealConversion:
    @pytest.fixture
    def starter(self, tmp_path, monkeypatch):
        from test_adr_018_starter import repo

        root = repo(
            tmp_path / "toy",
            {
                "pyproject.toml": '[project]\nname = "toy"\nversion = "0"\n',
                "pkg/__init__.py": "",
            },
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
        return root

    def test_adr_016_converts_and_builds_strictly(self, starter, tmp_path, capsys):
        copy = tmp_path / "copy" / "ADR-016.md"
        copy.parent.mkdir()
        source = next((REPO / "docs/adr").glob("ADR-016-*.md"))
        shutil.copyfile(source, copy)
        # The records it cites, ADR-014 and ADR-015, numbered past ADR-001 to 013.
        for n in range(1, 16):
            assert main(["docs", "decision", "new", f"Record {n}"]) == 0
        capsys.readouterr()
        # ADR-016 holds one construct that is not Markdown text: "<format>" in
        # D1 is an HTML tag to a Markdown reader, and GitHub renders it as
        # nothing. The converter leaves it, as it must ...
        assert main(["--json", "docs", "migrate", "adr", str(copy), "--dry-run"]) == 0
        (page,) = json.loads(capsys.readouterr().out)["pages"]
        assert page["unconverted"] == ["raw html <format>"]
        # ... and the session rewrites it, here in the copy's Markdown.
        text = copy.read_text(encoding="utf-8")
        copy.write_text(text.replace("<format>", "`<format>`"), encoding="utf-8")
        assert main(["--json", "docs", "migrate", "adr", str(copy)]) == 0
        (page,) = json.loads(capsys.readouterr().out)["pages"]
        assert page["unconverted"] == []
        assert page["needs"] == ["DEC_ADR_016", *(f"DEC_ADR_016_D{k}" for k in range(1, 10))]
        code = main(["--json", "docs", "build"])
        record = json.loads(capsys.readouterr().out)
        assert code == 0 and record["passed"], record["diagnostics"]
        held = json.loads(Path(record["needs_json"]).read_text(encoding="utf-8"))
        (version,) = held["versions"].values()
        needs = version["needs"]
        for k in range(1, 10):
            need = needs[f"DEC_ADR_016_D{k}"]
            assert need["type"] == "dec" and need["part_of"] == ["DEC_ADR_016"]
        assert needs["DEC_ADR_016"]["status"] == "accepted"

    def test_an_unconvertible_construct_fails_the_build(self, starter, tmp_path, capsys):
        copy = tmp_path / "copy" / "notes.md"
        copy.parent.mkdir()
        copy.write_text(
            "# Notes\n\nConvertible prose.\n\nA <kbd>raw</kbd> construct.\n", encoding="utf-8"
        )
        assert main(
            ["docs", "migrate", "prose", str(copy), "--into", str(starter / "docs/architecture")]
        ) == 0
        out = capsys.readouterr().out
        assert "1 construct(s) left as .. invariant-unconverted::" in out
        assert main(["--json", "docs", "build"]) == 1
        record = json.loads(capsys.readouterr().out)
        assert not record["passed"]
        assert any(
            "docs/architecture/notes.rst" in line and "invariant-unconverted" in line
            for line in record["diagnostics"]
        ), record["diagnostics"]

    def test_a_migrated_residue_with_a_semicolon_builds_strictly(self, starter, capsys):
        from invariant.core.items import Item, SphinxNeedsBackend

        SphinxNeedsBackend(starter / "docs/backlog.rst").add(
            Item(
                id="BUG_001",
                type="bug",
                title="A bug",
                status="filed",
                fields={"severity": "low"},
                residue=["one; two", "three"],
                history=[{"from": "filed", "to": "filed", "reason": None, "forced": False}],
                body="The body.",
            )
        )
        assert main(["--json", "docs", "build"]) == 0, capsys.readouterr().out

