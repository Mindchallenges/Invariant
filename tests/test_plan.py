"""The read-only half of a session's deliverables plan.

Lives in `core/` for the reason `core/stats.breakdown` does: a rule will have
to ask these questions, and `core/` may not import `domains/`.
"""

import pytest

from invariant.core.plan import (
    entries,
    next_n,
    progress,
    render_lines,
    summary_lines,
)


def plan(*titles, done=()):
    return [
        {"n": i, "title": t, "done": i in done, "done_at": 1.0 if i in done else None}
        for i, t in enumerate(titles, start=1)
    ]


class TestEntries:
    def test_a_plan_reads_back_in_the_order_it_was_recorded(self):
        got = entries(plan("read", "write", "land"))
        assert [e["title"] for e in got] == ["read", "write", "land"]

    def test_an_empty_plan_reads_as_nothing(self):
        assert entries([]) == []
        assert entries(None) == []

    def test_a_hand_edited_record_does_not_crash_the_reader(self):
        """The state file is documented as hand-fixable, so it can be wrong."""
        got = entries([{"title": "no number"}, "not a dict", {"n": 7}])
        assert [e["n"] for e in got] == [1, 7]
        assert got[1]["title"] == ""
        assert got[0]["done"] is False

    def test_a_number_that_is_not_a_number_falls_back_to_position(self):
        assert [e["n"] for e in entries([{"n": "x", "title": "a"}])] == [1]

    def test_the_reader_hands_back_copies(self):
        raw = plan("read")
        entries(raw)[0]["title"] = "changed"
        assert raw[0]["title"] == "read"


class TestNextN:
    def test_the_first_number_is_one(self):
        assert next_n([]) == 1

    def test_numbers_are_never_reused(self):
        after_removal = [plan("a", "b", "c")[0], plan("a", "b", "c")[2]]
        assert next_n(after_removal) == 4


class TestProgress:
    def test_an_empty_plan_is_unplanned_rather_than_finished(self):
        """The distinction a later mode switch turns on, in exactly one place."""
        p = progress([])
        assert p.total == 0
        assert p.complete is False

    def test_a_partially_done_plan(self):
        p = progress(plan("a", "b", "c", done=(2,)))
        assert (p.total, p.done) == (3, 1)
        assert [e["title"] for e in p.remaining] == ["a", "c"]
        assert p.complete is False

    def test_a_fully_done_plan_is_complete(self):
        assert progress(plan("a", "b", done=(1, 2))).complete is True

    def test_falsey_is_open(self):
        """`done` absent, null or empty all mean the same thing: not yet."""
        assert progress([{"n": 1, "title": "a"}]).done == 0
        assert progress([{"n": 1, "title": "a", "done": None}]).done == 0

    def test_describe_says_which_of_the_three_states_it_is(self):
        assert "no deliverables" in progress([]).describe()
        assert progress(plan("a", "b", done=(1,))).describe() == "1 of 2 done"
        assert progress(plan("a", done=(1,))).describe() == "1 of 1 done"


class TestRenderLines:
    def test_an_empty_plan_says_so_rather_than_rendering_nothing(self):
        assert render_lines([]) == ["(no deliverables recorded)"]

    def test_open_and_done_are_distinguishable(self):
        lines = render_lines(plan("read", "write", done=(1,)))
        assert lines == ["[x] 1  read", "[ ] 2  write"]

    def test_numbers_are_padded_so_the_titles_line_up(self):
        wide = [{"n": 9, "title": "a"}, {"n": 10, "title": "b"}]
        assert render_lines(wide) == ["[ ]  9  a", "[ ] 10  b"]


class TestFraction:
    """`2/5`, written once because four call sites want it.

    The CLI's resume line, its `plan show` header, the host's turn projection
    and the host's resume offer all print this pair. Four hand-built f-strings
    is four chances to print a total that disagrees with the numbers the
    commands take.
    """

    def test_it_is_done_over_total(self):
        assert progress(plan("a", "b", "c", done=(2,))).fraction == "1/3"

    def test_an_empty_plan_is_zero_over_zero_rather_than_a_dash(self):
        """The `-` spelling belongs to a TABLE column, where a cell must be

        filled. A caller with a sentence to write needs the numbers; the
        caller that wants "unplanned" has `total` and `describe()` for it, and
        a reader returning a placeholder would put a dash inside a sentence.
        """
        assert progress([]).fraction == "0/0"


class TestSummaryLines:
    """The turn projection's view of the plan: a headline and what is next.

    Deliberately NOT `render_lines`. That one answers a direct question — a
    person typed `session plan show` — and lists every entry with the number
    the commands take. This one is injected into every turn of every project,
    so it is capped at two lines however long the plan is, and the rest is
    behind the command it names.
    """

    def test_an_empty_plan_renders_nothing_at_all(self):
        """The opposite of `render_lines`, and on purpose.

        `plan show` was asked and must answer, so it says "(no deliverables
        recorded)". This block appears on every turn of every project, most of
        which keep no checklist — a permanent line saying nothing was recorded
        is the noise that teaches a reader to skip the whole block.
        """
        assert summary_lines([]) == []

    def test_the_headline_carries_the_fraction_and_the_next_deliverable(self):
        lines = summary_lines(plan("read the docs", "wire it", done=(1,)))
        assert lines[0] == "plan 1/2 done — next: wire it"

    def test_only_one_then_line_however_long_the_plan_is(self):
        lines = summary_lines(plan("a", "b", "c", "d", "e"))
        assert lines == ["plan 0/5 done — next: a", "  then: b"]

    def test_a_single_open_deliverable_has_no_then_line(self):
        assert summary_lines(plan("a", "b", done=(1,))) == ["plan 1/2 done — next: b"]

    def test_a_complete_plan_says_so_and_names_the_finish(self):
        lines = summary_lines(plan("a", "b", done=(1, 2)))
        assert lines[0] == "plan 2/2 done — COMPLETE"
        assert lines[1] == "  every deliverable is done; land it: invariant session review"

    def test_it_never_exceeds_two_lines(self):
        for candidate in ([], plan("a"), plan("a", "b", "c", "d"), plan("a", done=(1,))):
            assert len(summary_lines(candidate)) <= 2


@pytest.mark.parametrize("bad", [None, 0, "plan", 17])
def test_every_reader_tolerates_a_record_that_is_not_a_list(bad):
    assert entries(bad) == []
    assert next_n(bad) == 1
    assert progress(bad).total == 0
    assert render_lines(bad) == ["(no deliverables recorded)"]
    assert summary_lines(bad) == []
