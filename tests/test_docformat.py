"""ADR-016 D1, ruling 5: the per-format reader the assessment reads docs through.

Markdown lands in increment 1. A format with no reader answers ``None``, which
the assessment reports as "not assessable", never as a pass.
"""

from __future__ import annotations

from invariant.core.docformat import (
    Heading,
    Link,
    MarkdownReader,
    normalise_heading,
    reader_for,
)


class TestReaderFor:
    def test_markdown_has_a_reader(self):
        assert isinstance(reader_for("markdown"), MarkdownReader)

    def test_a_format_without_one_answers_none(self):
        assert reader_for("sphinx") is None
        assert reader_for("asciidoc") is None

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
