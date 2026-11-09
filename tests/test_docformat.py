"""ADR-016 D1, ruling 5: the per-format reader the assessment reads docs through.

Markdown lands in increment 1; reStructuredText, ``docs.format: sphinx``, with
ADR-018 increment 2. A format with no reader answers ``None``, which the
assessment reports as "not assessable", never as a pass.
"""

from __future__ import annotations

from invariant.core.docformat import (
    Heading,
    Link,
    MarkdownReader,
    RstReader,
    detect_format,
    normalise_heading,
    reader_for,
)


class TestReaderFor:
    def test_markdown_has_a_reader(self):
        assert isinstance(reader_for("markdown"), MarkdownReader)

    def test_a_format_without_one_answers_none(self):
        assert reader_for("asciidoc") is None

    def test_sphinx_reads_restructured_text(self):
        assert isinstance(reader_for("sphinx"), RstReader)
        assert RstReader().suffixes == (".rst",)

    def test_markdown_reads_md_files(self):
        assert ".md" in MarkdownReader().suffixes


class TestNormalise:
    def test_whitespace_collapses_and_case_folds(self):
        assert normalise_heading("  Component \t RESPONSIBILITIES ") == (
            "component responsibilities"
        )


class TestMarkdownHeadings:
    def read(self, text: str) -> list[Heading]:
        return MarkdownReader().headings(text)

    def test_atx_headings_with_their_lines(self):
        text = "# Title\n\nbody\n\n## Layers ##\n   ### Deep\n"
        assert self.read(text) == [
            Heading(1, "Title"),
            Heading(5, "Layers"),
            Heading(6, "Deep"),
        ]

    def test_setext_headings(self):
        text = "Overview\n========\n\nLayers\n------\n"
        assert self.read(text) == [Heading(1, "Overview"), Heading(4, "Layers")]

    def test_a_rule_after_a_blank_line_is_no_heading(self):
        assert self.read("para\n\n---\n") == []

    def test_fenced_code_is_skipped(self):
        text = "```\n# not a heading\n```\n~~~\n## nor this\n~~~\n# Real\n"
        assert self.read(text) == [Heading(7, "Real")]

    def test_a_hash_without_a_space_is_no_heading(self):
        assert self.read("#tag\n####### seven\n") == []

    def test_an_empty_atx_heading_is_dropped(self):
        assert self.read("#\n## \n") == []


class TestMarkdownLinks:
    def read(self, text: str) -> list[Link]:
        return MarkdownReader().links(text)

    def test_inline_links_with_their_lines(self):
        text = "see [a](a.md) and\n[b](../b.md \"title\") ![img](<pic.png>)\n"
        assert self.read(text) == [
            Link(1, "a.md"),
            Link(2, "../b.md"),
            Link(2, "pic.png"),
        ]

    def test_reference_definitions(self):
        text = "[x][ref]\n\n[ref]: target.md \"t\"\n  [other]: <spaced.md>\n"
        assert self.read(text) == [Link(3, "target.md"), Link(4, "spaced.md")]

    def test_code_spans_and_fences_are_skipped(self):
        text = "`[a](no.md)` [b](yes.md)\n```\n[c](no2.md)\n```\n"
        assert self.read(text) == [Link(1, "yes.md")]

    def test_an_empty_target_is_dropped(self):
        assert self.read("[a]()\n") == []


class TestDetectFormat:
    """ADR-016 D7: `docs adopt` sets `docs.format` from the source's files."""

    def test_markdown_files_are_markdown(self):
        assert detect_format(["a.md", "b/c.markdown", "pic.png", "d.png", "e.png"]) == "markdown"

    def test_restructured_text_is_sphinx(self):
        assert detect_format(["index.rst", "conf.py", "api.rst", "readme.md"]) == "sphinx"

    def test_the_most_common_document_format_wins_and_ties_sort_by_name(self):
        assert detect_format(["a.adoc", "b.adoc", "c.md"]) == "asciidoc"
        assert detect_format(["a.txt", "b.md"]) == "markdown"

    def test_with_no_known_document_the_commonest_suffix_names_it(self):
        assert detect_format(["a.docx", "b.docx", "c.png"]) == "docx"

    def test_with_no_suffix_at_all_it_is_unknown(self):
        assert detect_format(["README", "LICENSE"]) == "unknown"
        assert detect_format([]) == "unknown"


class TestRstHeadings:
    def read(self, text: str) -> list[Heading]:
        return RstReader().headings(text)

    def test_underlined_titles_with_their_lines(self):
        text = "Overview\n========\n\nbody\n\nLayers\n------\n\nDeep\n~~~~\n"
        assert self.read(text) == [
            Heading(1, "Overview"),
            Heading(6, "Layers"),
            Heading(9, "Deep"),
        ]

    def test_an_overlined_title_is_read_once_at_its_text_line(self):
        text = "=========\n  Inset\n=========\n\n#####\nTitle\n#####\n"
        assert self.read(text) == [Heading(2, "Inset"), Heading(6, "Title")]

    def test_a_short_underline_of_four_or_more_still_makes_a_title(self):
        """docutils warns ("Title underline too short") but keeps the section."""
        assert self.read("Component responsibilities\n----\n") == [
            Heading(1, "Component responsibilities")
        ]

    def test_a_too_short_underline_is_no_title(self):
        assert self.read("Layers\n--\n") == []

    def test_a_transition_after_a_blank_line_is_no_title(self):
        assert self.read("para\n\n--------\n\nmore\n") == []

    def test_an_indented_line_is_no_title(self):
        assert self.read("   quoted\n   ------\n") == []

    def test_mixed_adornment_characters_are_no_title(self):
        assert self.read("Title\n=-=-=-\n") == []

    def test_an_overline_without_a_matching_underline_is_no_title(self):
        """docutils refuses it ("Title overline & underline mismatch")."""
        assert self.read("=====\nTitle\n-----\n") == []

    def test_literal_blocks_are_skipped(self):
        text = (
            "Intro::\n\n    Not\n    ===\n\nReal\n====\n\n"
            "::\n\n  Nor\n  ---\n\nAfter\n-----\n"
        )
        assert self.read(text) == [Heading(6, "Real"), Heading(14, "After")]

    def test_code_and_comment_bodies_are_skipped(self):
        text = (
            ".. code-block:: rst\n\n   Fake\n   ====\n\n"
            "..\n   Hidden\n   ======\n\n"
            "Shown\n=====\n"
        )
        assert self.read(text) == [Heading(10, "Shown")]

    def test_a_title_inside_a_directive_body_is_not_read(self):
        assert self.read(".. note::\n\n   Inner\n   =====\n") == []


class TestRstLinks:
    def read(self, text: str) -> list[Link]:
        return RstReader().links(text)

    def test_doc_roles_name_documents(self):
        text = "See :doc:`intro` and :doc:`the guide <../guide/index>`.\n:doc:`~/abs`\n"
        assert self.read(text) == [
            Link(1, "intro", document=True),
            Link(1, "../guide/index", document=True),
            Link(2, "/abs", document=True),
        ]

    def test_ref_roles_name_labels_not_files(self):
        assert self.read("See :ref:`layers` and :ref:`x <y>`.\n") == []

    def test_download_and_file_directives_name_files(self):
        text = (
            ":download:`script <files/run.sh>`\n"
            ".. include:: parts/a.rst\n"
            ".. literalinclude:: ../src/x.py\n"
            "   :lines: 1-3\n"
            ".. image:: pic.png\n"
            ".. figure:: fig.svg\n"
        )
        assert self.read(text) == [
            Link(1, "files/run.sh"),
            Link(2, "parts/a.rst"),
            Link(3, "../src/x.py"),
            Link(5, "pic.png"),
            Link(6, "fig.svg"),
        ]

    def test_toctree_entries_name_documents(self):
        text = (
            ".. toctree::\n"
            "   :maxdepth: 2\n"
            "   :caption: Contents\n"
            "\n"
            "   overview\n"
            "   Guide <guide/index>\n"
            "   self\n"
            "   api/*\n"
            "   https://example.org\n"
            "\n"
            "after\n"
            "   not an entry\n"
        )
        assert self.read(text) == [
            Link(5, "overview", document=True),
            Link(6, "guide/index", document=True),
            Link(9, "https://example.org"),
        ]

    def test_hyperlink_references_and_targets(self):
        text = (
            "`Spec <spec.rst>`_ and `Web <https://example.org>`__ and `alias <other_>`_\n"
            ".. _other: notes/other.html\n"
            ".. _label:\n"
            ".. _anon: https://example.org/x\n"
        )
        assert self.read(text) == [
            Link(1, "spec.rst"),
            Link(1, "https://example.org"),
            Link(2, "notes/other.html"),
            Link(4, "https://example.org/x"),
        ]

    def test_inline_literals_and_literal_blocks_are_skipped(self):
        text = (
            "``:doc:`no``` :doc:`yes`\n"
            "Example::\n\n   :doc:`no2`\n\n"
            ".. code-block:: rst\n\n   .. include:: no3.rst\n\n"
            ".. include:: last.rst\n"
        )
        assert self.read(text) == [
            Link(1, "yes", document=True),
            Link(10, "last.rst"),
        ]

    def test_links_inside_a_need_directive_body_are_read(self):
        text = ".. comp:: Store\n   :id: COMP_STORE\n\n   Reads :doc:`../data/model`.\n"
        assert self.read(text) == [Link(4, "../data/model", document=True)]

    def test_a_comment_hides_its_body(self):
        text = "..\n   :doc:`hidden`\n.. a comment :doc:`also hidden`\n:doc:`shown`\n"
        assert self.read(text) == [Link(4, "shown", document=True)]


class TestRstIsDeterministic:
    def test_the_same_text_reads_the_same_twice(self):
        text = "Layers\n======\n\n:doc:`a` `b <c.rst>`_\n\n.. toctree::\n\n   d\n"
        reader = RstReader()
        assert reader.headings(text) == reader.headings(text)
        assert reader.links(text) == reader.links(text)
