"""The sphinx-needs backend reads what reStructuredText means and writes back
only what changed (BUG_065, following BUG_006 and BUG_007).

A save is a patch: the lines holding a value that changed are rewritten and
every other byte of the file is its own. The corpus test at the bottom is the
property — for every entry shape, a status change and a re-read differ from
the original by exactly the status line.
"""

from collections import Counter

import pytest

from invariant.core.errors import UsageError
from invariant.core.items import Item, SphinxNeedsBackend


def backend(tmp_path, text: str, newline: str = "") -> tuple[SphinxNeedsBackend, object]:
    path = tmp_path / "backlog.rst"
    with path.open("w", encoding="utf-8", newline=newline) as handle:
        handle.write(text)
    return SphinxNeedsBackend(path), path


def raw(path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def entry(options: str, body: str = "   Body.\n") -> str:
    return f".. bug:: T\n   :id: BUG_X\n   :status: filed\n{options}\n{body}"


class TestRolesAndOptions:
    """A line is a new option only if it is an option marker at the option
    column. Deeper, it continues the line above it — whatever it opens with.
    At the column but not an option, it is preserved and read as nothing."""

    @pytest.mark.parametrize(
        "role", [":doc:`architecture/overview`", ":ref:`a-label`", ":term:`backlog`"]
    )
    def test_a_role_opening_a_continuation_stays_in_the_value(self, tmp_path, role):
        b, _ = backend(tmp_path, entry(f"   :area: core/items, see\n          {role} here\n"))
        item = b.get("BUG_X")
        assert item.fields == {"area": f"core/items, see {role} here"}

    @pytest.mark.parametrize("role", [":doc:`overview`", ":py:func:`run` here"])
    def test_a_role_at_the_option_column_is_not_an_option(self, tmp_path, role):
        """A role has a backtick right after its closing colon; no option does.
        Not a continuation either — that would need deeper indentation."""
        b, _ = backend(tmp_path, entry(f"   :area: core/items\n   {role}\n"))
        assert b.get("BUG_X").fields == {"area": "core/items"}

    def test_a_marker_with_no_space_is_an_option(self, tmp_path):
        """``:area:value`` is read leniently, as this backend always has."""
        text = ".. bug:: T\n   :id: B1\n   :status: open\n   :area:value\n   :tags: a\n"
        item = backend(tmp_path, text)[0].get("B1")
        assert item.status == "open"
        assert item.fields == {"area": "value", "tags": "a"}

    def test_a_marker_with_no_space_may_come_first(self, tmp_path):
        text = ".. bug:: T\n   :area:value\n   :id: B1\n   :status: open\n\n   body\n"
        item = backend(tmp_path, text)[0].get("B1")
        assert (item.status, item.fields, item.body) == ("open", {"area": "value"}, "body")

    def test_prose_at_the_option_column_joins_no_value(self, tmp_path):
        text = ".. bug:: T\n   :id: B1\n   :status: open\n   stray prose\n      deeper\n"
        item = backend(tmp_path, text)[0].get("B1")
        assert (item.status, item.fields, item.body) == ("open", {}, "")

    def test_an_option_after_prose_at_the_column_is_still_read(self, tmp_path):
        text = ".. bug:: T\n   :id: B1\n   :doc:`x`\n   :status: open\n"
        assert backend(tmp_path, text)[0].get("B1").status == "open"

    def test_a_marker_shaped_line_indented_deeper_is_a_continuation(self, tmp_path):
        b, _ = backend(tmp_path, entry("   :area: core/items,\n      :note: still area\n"))
        assert b.get("BUG_X").fields == {"area": "core/items, :note: still area"}

    def test_a_genuine_option_after_a_wrapped_one_is_its_own(self, tmp_path):
        text = entry("   :area: core/items,\n          :doc:`x`\n   :severity: high\n")
        assert backend(tmp_path, text)[0].get("BUG_X").fields == {
            "area": "core/items, :doc:`x`",
            "severity": "high",
        }

    def test_a_value_holding_a_colon_keeps_it(self, tmp_path):
        text = entry("   :area: core: items\n          see: overview\n")
        assert backend(tmp_path, text)[0].get("BUG_X").fields == {
            "area": "core: items see: overview"
        }

    def test_an_empty_option_is_kept_and_does_not_swallow_the_next(self, tmp_path):
        text = entry("   :tags:\n   :severity: high\n")
        assert backend(tmp_path, text)[0].get("BUG_X").fields == {
            "tags": "",
            "severity": "high",
        }

    def test_tab_indented_options_parse(self, tmp_path):
        text = ".. bug:: T\n\t:id: BUG_X\n\t:status: filed\n\t\tcontinued\n"
        item = backend(tmp_path, text)[0].get("BUG_X")
        assert item.status == "filed continued"

    def test_padding_before_the_first_option_is_allowed(self, tmp_path):
        text = ".. bug:: T\n\n   :id: BUG_X\n   :status: filed\n\n   Body.\n"
        item = backend(tmp_path, text)[0].get("BUG_X")
        assert item.status == "filed" and item.body == "Body."


    def test_an_entry_with_no_options_has_only_a_body(self, tmp_path):
        b, _ = backend(tmp_path, ".. bug:: T\n   :doc:`x` is prose here.\n")
        item = b.load_all()[0]
        assert item.id == "" and item.fields == {}
        assert item.body == ":doc:`x` is prose here."

    def test_next_id_counts_the_entries_read(self, tmp_path):
        b, _ = backend(tmp_path, entry(""))
        assert b.next_id("bug") == "BUG_001"
        b, _ = backend(tmp_path, ".. bug:: T\n   :id: BUG_007\n")
        assert b.next_id("bug") == "BUG_008"


class TestASaveIsAPatch:
    def transition(self, b, target="reproduced", item_id="BUG_X"):
        item = b.get(item_id)
        item.history.append({"from": item.status, "to": target})
        item.status = target
        b.save(item)

    def test_only_the_status_line_changes(self, tmp_path):
        text = entry("   :area: core/items,\n          :doc:`x` too\n") + "\nNext\n----\n"
        b, path = backend(tmp_path, text)
        self.transition(b)
        assert raw(path) == text.replace(":status: filed", ":status: reproduced")

    def test_a_missing_status_is_inserted_after_the_id(self, tmp_path):
        text = ".. bug:: T\n   :id: BUG_X\n   :area: a\n\n   Body.\n"
        b, path = backend(tmp_path, text)
        self.transition(b)
        assert raw(path) == (
            ".. bug:: T\n   :id: BUG_X\n   :status: reproduced\n   :area: a\n\n   Body.\n"
        )

    def test_a_changed_wrapped_field_is_rewritten_alone(self, tmp_path):
        text = entry("   :area: a,\n          b\n   :severity: high\n")
        b, path = backend(tmp_path, text)
        item = b.get("BUG_X")
        item.fields["area"] = "c"
        b.save(item)
        assert raw(path) == entry("   :area: c\n   :severity: high\n")

    def test_a_rewritten_option_keeps_its_own_indentation(self, tmp_path):
        text = ".. bug:: T\n   :id: BUG_X\n  :status: filed\n"
        b, path = backend(tmp_path, text)
        item = b.get("BUG_X")
        item.status = "reproduced"
        item.fields["area"] = "new"
        b.save(item)
        assert raw(path) == (
            ".. bug:: T\n   :id: BUG_X\n  :status: reproduced\n   :area: new\n"
        )

    def test_a_removed_field_takes_all_its_lines_and_nothing_else(self, tmp_path):
        b, path = backend(tmp_path, entry("   :area: a,\n          b\n   :severity: high\n"))
        item = b.get("BUG_X")
        del item.fields["area"]
        b.save(item)
        assert raw(path) == entry("   :severity: high\n")

    def test_a_new_field_and_residue_follow_the_last_option(self, tmp_path):
        b, path = backend(tmp_path, entry("   :area: a,\n          b\n"))
        item = b.get("BUG_X")
        item.fields["severity"] = "low"
        item.residue = ["a leg is owed"]
        b.save(item)
        assert raw(path) == entry(
            "   :area: a,\n          b\n   :severity: low\n   :residue: a leg is owed\n"
        )

    def test_residue_spelled_differently_but_equal_is_not_rewritten(self, tmp_path):
        text = entry("   :residue: one;two\n")
        b, path = backend(tmp_path, text)
        self.transition(b)
        assert raw(path) == text.replace(":status: filed", ":status: reproduced")

    def test_clearing_residue_removes_its_line(self, tmp_path):
        b, path = backend(tmp_path, entry("   :residue: one\n"))
        item = b.get("BUG_X")
        item.residue = []
        b.save(item)
        assert raw(path) == entry("")

    def test_a_retitle_rewrites_the_directive_line_alone(self, tmp_path):
        text = ".. bug::   Old title\n   :id: BUG_X\n   :area: a,\n     b\n"
        b, path = backend(tmp_path, text)
        item = b.get("BUG_X")
        item.title = "New title"
        b.save(item)
        assert raw(path) == text.replace("Old title", "New title")

    def test_a_title_added_where_there_was_none_is_spaced(self, tmp_path):
        b, path = backend(tmp_path, ".. bug::\n   :id: BUG_X\n")
        item = b.get("BUG_X")
        item.title = "Named"
        b.save(item)
        assert raw(path) == ".. bug:: Named\n   :id: BUG_X\n"

    def test_a_changed_body_keeps_the_blank_line_before_the_next_heading(self, tmp_path):
        text = entry("", "   Old body.\n\n   Second paragraph.\n") + "\nNext\n----\n"
        b, path = backend(tmp_path, text)
        item = b.get("BUG_X")
        item.body = "New body."
        b.save(item)
        assert raw(path) == entry("", "   New body.\n") + "\nNext\n----\n"

    def test_a_removed_body_leaves_the_options_and_the_separation(self, tmp_path):
        b, path = backend(tmp_path, entry("   :area: a\n") + "\nNext\n----\n")
        item = b.get("BUG_X")
        item.body = ""
        b.save(item)
        expected = ".. bug:: T\n   :id: BUG_X\n   :status: filed\n   :area: a\n"
        assert raw(path) == expected + "\nNext\n----\n"

    def test_a_body_added_to_the_last_entry_without_a_newline(self, tmp_path):
        b, path = backend(tmp_path, ".. bug:: T\n   :id: BUG_X")
        item = b.get("BUG_X")
        item.body = "Line one.\n\nLine two."
        b.save(item)
        assert raw(path) == (
            ".. bug:: T\n   :id: BUG_X\n\n   Line one.\n\n   Line two."
        )

    def test_crlf_line_endings_survive(self, tmp_path):
        text = entry("   :area: a,\n          :doc:`b`\n") + "\nNext\n----\n"
        b, path = backend(tmp_path, text, newline="\r\n")
        item = b.get("BUG_X")
        assert item.fields == {"area": "a, :doc:`b`"}
        item.status = "reproduced"
        item.fields["severity"] = "low"
        b.save(item)
        expected = text.replace(":status: filed", ":status: reproduced").replace(
            "          :doc:`b`\n", "          :doc:`b`\n   :severity: low\n"
        )
        assert raw(path) == expected.replace("\n", "\r\n")

    def test_a_marker_with_no_space_survives_a_status_save(self, tmp_path):
        """Blocker: it was folded into the status value, then deleted with it."""
        text = ".. bug:: T\n   :id: B1\n   :status: open\n   :area:value\n   :tags: a\n\n   body\n"
        b, path = backend(tmp_path, text)
        self.transition(b, "fixed", "B1")
        assert raw(path) == text.replace(":status: open", ":status: fixed")

    def test_a_marker_with_no_space_first_survives_a_status_save(self, tmp_path):
        """Blocker: the entry read with no id, and a save wrote a stray status."""
        text = ".. bug:: T\n   :area:value\n   :id: B1\n   :status: open\n\n   body\n"
        b, path = backend(tmp_path, text)
        self.transition(b, "fixed", "B1")
        assert raw(path) == text.replace(":status: open", ":status: fixed")

    def test_lines_at_the_option_column_survive_every_kind_of_save(self, tmp_path):
        text = (
            ".. bug:: T\n   :id: B1\n   :status: open\n   :doc:`x` more\n"
            "   :area: a\n   stray\n\n   Old body.\n"
        )
        b, path = backend(tmp_path, text)
        item = b.get("B1")
        item.status = "fixed"
        item.fields["area"] = "b"
        item.fields["new"] = "n"
        item.body = "New body."
        b.save(item)
        assert raw(path) == (
            ".. bug:: T\n   :id: B1\n   :status: fixed\n   :doc:`x` more\n"
            "   :area: b\n   :new: n\n   stray\n\n   New body.\n"
        )

    def test_a_lone_cr_file_is_read_and_stays_cr(self, tmp_path):
        """Blocker: one line, no entry found, and a save appended junk."""
        text = ".. bug:: T\r   :id: B1\r   :status: open\r\r   body\r"
        b, path = backend(tmp_path, text)
        item = b.get("B1")
        assert (item.status, item.body) == ("open", "body")
        self.transition(b, "fixed", "B1")
        assert raw(path) == text.replace(":status: open", ":status: fixed")

    def test_each_line_keeps_its_own_ending(self, tmp_path):
        text = ".. bug:: T\r\n   :id: B1\n   :status: open\r   :area: a\r\n"
        b, path = backend(tmp_path, text)
        item = b.get("B1")
        item.status = "fixed"
        item.fields["area"] = "b"
        b.save(item)
        assert raw(path) == ".. bug:: T\r\n   :id: B1\n   :status: fixed\r   :area: b\r\n"

    def test_a_multi_line_value_is_written_on_one_line(self, tmp_path):
        b, path = backend(tmp_path, entry(""))
        item = b.get("BUG_X")
        item.fields["area"] = "one\ntwo\r\n three"
        b.save(item)
        assert raw(path) == entry("   :area: one two three\n")
        assert b.get("BUG_X").fields == {"area": "one two three"}

    def test_the_neighbouring_entry_is_untouched(self, tmp_path):
        neighbour = ".. bug:: N\n   :id: BUG_N\n   :status: filed\n   :a: x,\n     y\n\n"
        text = neighbour + entry("")
        b, path = backend(tmp_path, text)
        self.transition(b)
        assert raw(path).startswith(neighbour)


class TestASaveThatCannotLandIsRefused:
    """A save that would not read back as the item saved writes nothing."""

    def test_an_item_without_an_id(self, tmp_path):
        text = ".. bug:: T\n   :status: open\n"
        b, path = backend(tmp_path, text)
        item = b.load_all()[0]
        item.status = "fixed"
        with pytest.raises(UsageError, match="needs an id"):
            b.save(item)
        assert raw(path) == text

    def test_a_field_name_no_option_can_spell(self, tmp_path):
        text = entry("")
        b, path = backend(tmp_path, text)
        item = b.get("BUG_X")
        item.fields["two words"] = "x"
        with pytest.raises(UsageError, match="refusing to save BUG_X.*two words"):
            b.save(item)
        assert raw(path) == text

    def test_a_field_name_holding_a_colon(self, tmp_path):
        """``:a:b: c`` reads as option ``a`` holding ``b: c``, not as ``a:b``."""
        text = entry("")
        b, path = backend(tmp_path, text)
        item = b.get("BUG_X")
        item.fields["a:b"] = "c"
        with pytest.raises(UsageError, match="different a:b"):
            b.save(item)
        assert raw(path) == text

    def test_a_value_opening_with_a_backtick_still_lands(self, tmp_path):
        """The space the writer puts after the marker keeps it an option."""
        b, _ = backend(tmp_path, entry(""))
        item = b.get("BUG_X")
        item.status = "`odd`"
        b.save(item)
        assert b.get("BUG_X").status == "`odd`"

    def test_a_new_entry_that_would_not_read_back(self, tmp_path):
        b, path = backend(tmp_path, entry(""))
        with pytest.raises(UsageError, match="refusing to save BUG_Y"):
            b.save(
                Item(id="BUG_Y", type="bug", title="T", status="filed", fields={"no good": "x"})
            )
        assert raw(path) == entry("")

    @pytest.mark.parametrize(
        ("result", "problem"),
        [
            (entry("") + ".. bug:: U\n   :id: BUG_U\n", "2 entries where 1"),
            (".. bug:: T\n   :id: BUG_OTHER\n", "not found again"),
            (entry("").replace(".. bug:: T", ".. bug:: Other"), "different title"),
        ],
    )
    def test_the_guard_names_what_would_not_read_back(self, tmp_path, result, problem):
        """These cannot be provoked through a save today; the guard is there
        for the day the read and the patch disagree."""
        b, _ = backend(tmp_path, entry(""))
        item = b.get("BUG_X")
        with pytest.raises(UsageError, match=problem):
            b._check(result, item, 1)


class TestNewEntries:
    def test_a_new_entry_in_an_empty_file(self, tmp_path):
        b = SphinxNeedsBackend(tmp_path / "docs" / "backlog.rst")
        b.save(
            Item(
                id="BUG_001",
                type="bug",
                title="A defect",
                status="filed",
                fields={"severity": "high"},
                residue=["owed"],
                body="Steps.\n\nMore.",
            )
        )
        assert raw(tmp_path / "docs" / "backlog.rst") == (
            ".. bug:: A defect\n   :id: BUG_001\n   :status: filed\n"
            "   :severity: high\n   :residue: owed\n\n   Steps.\n\n   More.\n"
        )

    def test_after_an_anchor_followed_directly_by_a_heading(self, tmp_path):
        b, path = backend(tmp_path, ".. bug:: A\n   :id: BUG_A\nHeading\n-------\n")
        b.save(Item(id="BUG_B", type="bug", title="B", status="filed"))
        assert raw(path) == (
            ".. bug:: A\n   :id: BUG_A\n\n.. bug:: B\n   :id: BUG_B\n"
            "   :status: filed\n\nHeading\n-------\n"
        )

    def test_after_an_anchor_keeps_the_existing_separation(self, tmp_path):
        text = ".. bug:: A\n   :id: BUG_A\n\n   Body.\n\nHeading\n-------\n"
        b, path = backend(tmp_path, text)
        b.save(Item(id="BUG_B", type="bug", title="B", status="filed"))
        assert raw(path) == (
            ".. bug:: A\n   :id: BUG_A\n\n   Body.\n\n.. bug:: B\n   :id: BUG_B\n"
            "   :status: filed\n\nHeading\n-------\n"
        )

    def test_appended_to_a_file_without_a_trailing_newline(self, tmp_path):
        b, path = backend(tmp_path, "Backlog\n=======\n\nIntro.")
        b.save(Item(id="IDEA_001", type="idea", title="I", status="proposed"))
        assert raw(path) == (
            "Backlog\n=======\n\nIntro.\n\n.. idea:: I\n   :id: IDEA_001\n"
            "   :status: proposed"
        )

    def test_appended_after_trailing_blank_lines_adds_none(self, tmp_path):
        b, path = backend(tmp_path, "Intro.\n\n")
        b.save(Item(id="IDEA_001", type="idea", title="I", status="proposed"))
        assert raw(path) == "Intro.\n\n.. idea:: I\n   :id: IDEA_001\n   :status: proposed\n"

    def test_reserved_names_in_fields_are_not_written_twice(self, tmp_path):
        b = SphinxNeedsBackend(tmp_path / "backlog.rst")
        b.save(
            Item(id="BUG_1", type="bug", title="T", status="filed", fields={"status": "x"})
        )
        assert raw(tmp_path / "backlog.rst").count(":status:") == 1


# -- the property --------------------------------------------------------

CORPUS = {
    "wrapped options and roles": (
        ".. bug:: Wrapped\n   :id: BUG_X\n   :status: filed\n   :area: core/items,\n"
        "          :doc:`overview` and :ref:`label`,\n          :term:`backlog`\n"
        "   :severity: high\n\n   Body with :doc:`a role` and a: colon.\n\nNext\n----\n"
    ),
    "unicode and odd separators": (
        "Intro – naïve café\n\n.. bug:: Ünïcode   title \x0c here\n   :id: BUG_X\n"
        "   :status: filed\n   :owner: Zoë — 東京\n\n   Body \x85 with   marks.\n"
    ),
    "last entry with a trailing newline": (
        ".. idea:: I\n   :id: IDEA_1\n   :status: proposed\n\n"
        ".. bug:: B\n   :id: BUG_X\n   :status: filed\n   :area: a,\n     b\n"
    ),
    "last entry without a trailing newline": (
        ".. bug:: B\n   :id: BUG_X\n   :area: a,\n     b\n   :status: filed"
    ),
    "trailing whitespace and extra blank lines": (
        ".. bug:: B   \n   :id: BUG_X  \n   :status: filed\n   :area: a  \n\n\n"
        "   Body.  \n\n\n\nHeading\n=======\n"
    ),
    "padded and tabbed": (
        ".. bug:: B\n\n\t:id: BUG_X\n\t:status: filed\n\t:area: a\n\t\t:doc:`b`\n"
    ),
    "markers with no space": (
        ".. bug:: B\n   :area:value\n   :id: BUG_X\n   :status: filed\n"
        "   :tags:a, b\n\n   Body.\n"
    ),
    "roles and prose at the option column": (
        ".. bug:: B\n   :id: BUG_X\n   :status: filed\n   :doc:`x` stray\n"
        "      deeper stray\n   :area: a\n   plain stray\n\n   Body.\n"
    ),
}

NEWLINES = pytest.mark.parametrize(
    "newline", ["\n", "\r\n", "\r"], ids=["lf", "crlf", "cr"]
)


@NEWLINES
@pytest.mark.parametrize("shape", sorted(CORPUS))
def test_a_status_change_is_byte_stable_on_every_shape(tmp_path, shape, newline):
    text = CORPUS[shape].replace("\n", newline)
    b, path = backend(tmp_path, text)
    before = b.get("BUG_X")

    after_item = b.get("BUG_X")
    after_item.history.append({"from": "filed", "to": "reproduced"})
    after_item.status = "reproduced"
    b.save(after_item)

    assert raw(path) == text.replace(":status: filed", ":status: reproduced", 1)
    reread = b.get("BUG_X")
    assert reread.status == "reproduced"
    assert (reread.title, reread.fields, reread.residue, reread.body) == (
        before.title,
        before.fields,
        before.residue,
        before.body,
    )
    b.save(reread)
    assert raw(path) == text.replace(":status: filed", ":status: reproduced", 1)


@NEWLINES
@pytest.mark.parametrize("shape", sorted(CORPUS))
def test_a_status_save_removes_no_line_but_the_status_line(tmp_path, shape, newline):
    """The guard: whatever the reading rules decide, a transition takes out the
    status line and nothing else, and puts back the new one and nothing else."""
    text = CORPUS[shape].replace("\n", newline)
    b, path = backend(tmp_path, text)
    item = b.get("BUG_X")
    item.status = "reproduced"
    b.save(item)

    before, after = Counter(text.splitlines()), Counter(raw(path).splitlines())
    status = next(line for line in before if line.strip() == ":status: filed")
    assert before - after == Counter([status])
    assert after - before == Counter([status.replace("filed", "reproduced")])
