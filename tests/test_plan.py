"""The read-only half of a session's deliverables plan.

Lives in `core/` for the reason `core/stats.breakdown` does: a rule will have
to ask these questions, and `core/` may not import `domains/`.
"""

import pytest

from invariant.core.plan import entries, next_n, progress, render_lines


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


@pytest.mark.parametrize("bad", [None, 0, "plan", 17])
def test_every_reader_tolerates_a_record_that_is_not_a_list(bad):
    assert entries(bad) == []
    assert next_n(bad) == 1
    assert progress(bad).total == 0
    assert render_lines(bad) == ["(no deliverables recorded)"]
