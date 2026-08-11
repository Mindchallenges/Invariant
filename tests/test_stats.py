"""The measured/modelled split, the arithmetic, and the two markers.

Every fixture here is synthetic. The host session identifiers are obviously
fake by construction, because a real one is a device identifier and this suite
is subject to the same privacy rule the library enforces on its consumers.
"""

from __future__ import annotations

import random

import pytest

from invariant.core.stats import (
    NO_BREAKDOWN,
    PRICES,
    RATES_AS_OF,
    AgentLaunches,
    Sample,
    SessionStats,
    SourceRef,
    StateSpend,
    TranscriptWatermarks,
    Usage,
    breakdown,
    estimate_cost,
    fold,
    format_cost,
    format_duration,
)


def sample(
    host="hs-1",
    timestamp=0.0,
    model="claude-opus-5",
    usage=None,
    tools=(),
    agents=(),
):
    return Sample(
        host_session_id=host,
        timestamp=timestamp,
        model=model,
        usage=usage or Usage(input=10, output=20),
        tools=tuple(tools),
        agents=tuple(agents),
    )


class FakeSource:
    """A :class:`SampleSource` that never touches a disk."""

    def __init__(self, by_session):
        self.by_session = dict(by_session)
        self.calls = 0

    def sessions(self):
        return [SourceRef(host_session_id=k, watermark=f"w-{k}") for k in self.by_session]

    def find(self, host_session_id):
        if host_session_id not in self.by_session:
            return None
        return SourceRef(host_session_id=host_session_id, watermark=f"w-{host_session_id}")

    def samples(self, ref):
        self.calls += 1
        return list(self.by_session.get(ref.host_session_id, []))


class TestUsage:
    def test_addition_is_componentwise(self):
        a = Usage(input=1, output=2, cache_read=3, cache_write_5m=4, cache_write_1h=5)
        b = Usage(input=10, output=20, cache_read=30, cache_write_5m=40, cache_write_1h=50)
        assert (a + b) == Usage(11, 22, 33, 44, 55)

    def test_addition_does_not_mutate_either_operand(self):
        a = Usage(input=1)
        b = Usage(input=2)
        a + b
        assert a.input == 1 and b.input == 2

    def test_subtraction_is_componentwise(self):
        a = Usage(input=10, output=20, cache_read=30, cache_write_5m=40, cache_write_1h=50)
        b = Usage(input=1, output=2, cache_read=3, cache_write_5m=4, cache_write_1h=5)
        assert (a - b) == Usage(9, 18, 27, 36, 45)

    def test_cache_tiers_stay_separate(self):
        """Collapsing 5m into 1h under-reports; the tiers are priced 1.25x and 2x."""
        assert Usage(cache_write_5m=1).to_dict() != Usage(cache_write_1h=1).to_dict()

    def test_round_trips_through_a_dict(self):
        usage = Usage(1, 2, 3, 4, 5)
        assert Usage.from_dict(usage.to_dict()) == usage

    def test_from_dict_tolerates_absence(self):
        assert Usage.from_dict({}) == Usage()

    def test_from_dict_tolerates_junk(self):
        assert Usage.from_dict({"input": "nonsense", "output": None}) == Usage()

    def test_cache_read_share_of_nothing_is_zero_not_an_error(self):
        assert Usage().cache_read_share == 0.0

    def test_cache_read_share_counts_every_input_tier(self):
        usage = Usage(input=10, cache_read=30, cache_write_5m=10, cache_write_1h=50)
        assert usage.cache_read_share == pytest.approx(0.3)


class TestPricing:
    def test_every_tier_is_priced_at_its_own_multiplier(self):
        usage = Usage(
            input=1_000_000,
            output=1_000_000,
            cache_read=1_000_000,
            cache_write_5m=1_000_000,
            cache_write_1h=1_000_000,
        )
        rate = PRICES["claude-opus-5"]
        expected = (
            rate.input
            + rate.output
            + rate.input * 0.1
            + rate.input * 1.25
            + rate.input * 2.0
        )
        assert estimate_cost(usage, "claude-opus-5") == pytest.approx(expected)

    def test_an_unknown_model_prices_none_and_never_zero(self):
        assert estimate_cost(Usage(input=1000), "claude-not-in-the-table") is None

    def test_the_table_carries_the_date_it_was_true(self):
        assert RATES_AS_OF and RATES_AS_OF.count("-") == 2

    def test_a_one_hour_write_costs_more_than_a_five_minute_one(self):
        five = estimate_cost(Usage(cache_write_5m=1_000_000), "claude-opus-5")
        hour = estimate_cost(Usage(cache_write_1h=1_000_000), "claude-opus-5")
        assert hour > five


class TestFold:
    def test_one_session_per_host_id(self):
        folded = fold([sample(host="a"), sample(host="b"), sample(host="a")])
        assert set(folded) == {"a", "b"}
        assert folded["a"].message_count == 2

    def test_tokens_accumulate_per_model(self):
        folded = fold(
            [
                sample(model="claude-opus-5", usage=Usage(input=1)),
                sample(model="claude-sonnet-5", usage=Usage(input=2)),
                sample(model="claude-opus-5", usage=Usage(input=4)),
            ]
        )
        stats = folded["hs-1"]
        assert stats.usage_by_model["claude-opus-5"].input == 5
        assert stats.usage_by_model["claude-sonnet-5"].input == 2

    def test_result_does_not_depend_on_arrival_order(self):
        """The defect the prior-art sketch shipped: an order-dependent accumulator."""
        samples = [sample(timestamp=float(t)) for t in (0, 10, 20, 1000, 1010)]
        ordered = fold(samples)["hs-1"].to_dict()
        for seed in range(25):
            shuffled = list(samples)
            random.Random(seed).shuffle(shuffled)
            assert fold(shuffled)["hs-1"].to_dict() == ordered

    def test_active_time_caps_each_gap_at_the_idle_threshold(self):
        stats = fold([sample(timestamp=0.0), sample(timestamp=10_000.0)])["hs-1"]
        assert stats.wall_seconds == 10_000.0
        assert stats.active_seconds == 300.0

    def test_a_sample_with_no_timestamp_still_counts_its_tokens(self):
        """Dropping it would silently under-count, which is the worse failure."""
        stats = fold(
            [sample(timestamp=None, usage=Usage(input=7)), sample(timestamp=0.0)]
        )["hs-1"]
        assert stats.message_count == 2
        assert stats.total_usage.input == 17

    def test_a_session_with_no_timestamps_has_no_date_rather_than_a_wrong_one(self):
        stats = fold([sample(timestamp=None)])["hs-1"]
        assert stats.date is None
        assert stats.wall_seconds == 0.0

    def test_the_date_is_bare_and_never_a_clock_time(self):
        stats = fold([sample(timestamp=1_780_000_000.0)])["hs-1"]
        assert ":" not in stats.date
        assert len(stats.date) == len("2026-06-24")

    def test_agent_launches_are_counted_with_their_declared_model(self):
        stats = fold(
            [
                sample(agents=(("feature-architect", "claude-opus-5"),)),
                sample(agents=(("feature-architect", "claude-opus-5"),)),
                sample(agents=(("bug-reproducer", "inherited"),)),
            ]
        )["hs-1"]
        assert stats.agents["feature-architect"] == AgentLaunches(
            launches=2, models={"claude-opus-5": 2}
        )
        assert stats.agents["bug-reproducer"].launches == 1

    def test_tools_are_counted(self):
        stats = fold([sample(tools=("Read", "Edit")), sample(tools=("Read",))])["hs-1"]
        assert stats.tools == {"Read": 2, "Edit": 1}

    def test_folding_nothing_is_an_empty_mapping_not_an_error(self):
        assert fold([]) == {}


class TestSessionCost:
    def test_a_priced_session_totals_its_models(self):
        stats = fold(
            [
                sample(model="claude-opus-5", usage=Usage(input=1_000_000)),
                sample(model="claude-sonnet-5", usage=Usage(input=1_000_000)),
            ]
        )["hs-1"]
        assert stats.cost == pytest.approx(PRICES["claude-opus-5"].input + PRICES["claude-sonnet-5"].input)
        assert stats.unpriced_models == []

    def test_one_unpriced_model_makes_the_whole_total_none(self):
        """A total that silently omits a model's spend looks like a total."""
        stats = fold(
            [
                sample(model="claude-opus-5", usage=Usage(input=1_000_000)),
                sample(model="claude-from-the-future", usage=Usage(input=1_000_000)),
            ]
        )["hs-1"]
        assert stats.cost is None
        assert stats.unpriced_models == ["claude-from-the-future"]

    def test_stats_round_trip_through_a_dict(self):
        stats = fold([sample(tools=("Read",), agents=(("x", "claude-opus-5"),))])["hs-1"]
        assert SessionStats.from_dict(stats.to_dict()).to_dict() == stats.to_dict()

    def test_from_dict_tolerates_a_record_missing_every_optional_field(self):
        assert SessionStats.from_dict({"host_session_id": "hs-1"}).message_count == 0

    def test_a_record_never_stores_a_clock_time(self):
        stats = fold([sample(timestamp=1_780_000_000.0)])["hs-1"]
        stored = stats.to_dict()
        assert "first_seen" not in stored and "last_seen" not in stored
        assert all(
            not isinstance(v, float) or k.endswith("_seconds")
            for k, v in stored.items()
        )


class TestBreakdown:
    def test_history_with_no_elapsed_yields_no_breakdown_at_all(self):
        history = [{"from": "designing", "to": "red", "reason": None}]
        assert breakdown(history) is None

    def test_one_missing_elapsed_poisons_the_whole_breakdown(self):
        history = [
            {"from": "designing", "to": "red", "elapsed": 10.0},
            {"from": "red", "to": "green"},
        ]
        assert breakdown(history) is None

    def test_empty_history_yields_no_breakdown(self):
        assert breakdown([]) is None

    def test_elapsed_attributes_to_the_state_that_was_left(self):
        history = [
            {"from": "designing", "to": "red", "elapsed": 60.0},
            {"from": "red", "to": "green", "elapsed": 30.0},
        ]
        spends = breakdown(history)
        assert [(s.state, s.elapsed) for s in spends] == [
            ("designing", 60.0),
            ("red", 30.0),
        ]

    def test_spend_is_the_difference_of_consecutive_watermarks(self):
        baseline = Usage(input=100)
        history = [
            {"from": "designing", "to": "red", "elapsed": 1.0, "usage": Usage(input=250).to_dict()},
            {"from": "red", "to": "green", "elapsed": 1.0, "usage": Usage(input=400).to_dict()},
        ]
        spends = breakdown(history, baseline=baseline)
        assert spends[0].usage == Usage(input=150)
        assert spends[1].usage == Usage(input=150)

    def test_without_a_baseline_the_first_state_has_a_duration_and_no_spend(self):
        history = [
            {"from": "designing", "to": "red", "elapsed": 1.0, "usage": Usage(input=250).to_dict()},
            {"from": "red", "to": "green", "elapsed": 1.0, "usage": Usage(input=400).to_dict()},
        ]
        spends = breakdown(history)
        assert spends[0].usage is None and spends[0].elapsed == 1.0
        assert spends[1].usage == Usage(input=150)

    def test_a_watermark_that_went_backwards_is_reported_not_negated(self):
        history = [
            {"from": "a", "to": "b", "elapsed": 1.0, "usage": Usage(input=400).to_dict()},
            {"from": "b", "to": "c", "elapsed": 1.0, "usage": Usage(input=100).to_dict()},
        ]
        spends = breakdown(history)
        assert spends[1].usage is None
        assert "went backwards" in spends[1].note

    def test_a_missing_watermark_leaves_the_duration_intact(self):
        history = [
            {"from": "a", "to": "b", "elapsed": 5.0, "usage": None},
            {"from": "b", "to": "c", "elapsed": 6.0, "usage": None},
        ]
        spends = breakdown(history)
        assert [s.elapsed for s in spends] == [5.0, 6.0]
        assert all(s.usage is None for s in spends)

    def test_a_spend_prices_like_any_other_usage(self):
        spend = StateSpend(state="red", elapsed=1.0, usage=Usage(input=1_000_000))
        assert spend.cost("claude-opus-5") == pytest.approx(PRICES["claude-opus-5"].input)


class TestFormatting:
    @pytest.mark.parametrize(
        "seconds,expected",
        [(0, "0s"), (45, "45s"), (60, "1m 00s"), (3599, "59m 59s"), (3600, "1h 00m"),
         (90_000, "1d 01h")],
    )
    def test_durations_render_as_durations(self, seconds, expected):
        assert format_duration(seconds) == expected

    def test_an_unknown_duration_says_so(self):
        assert format_duration(None) == "unknown"

    def test_no_duration_can_be_mistaken_for_a_clock_time(self):
        """A rendered `12:04` would trip the project's own privacy gate."""
        for seconds in (0, 59, 60, 3600, 43_200, 86_400, 123_456):
            assert ":" not in format_duration(seconds)

    def test_a_cost_is_labelled_a_floor(self):
        assert format_cost(12.5).startswith(">=")

    def test_an_unpriced_cost_is_never_rendered_as_zero(self):
        rendered = format_cost(None, unpriced=["claude-from-the-future"])
        assert "0" not in rendered.split("claude-from-the-future")[0]
        assert "unpriced" in rendered
        assert "claude-from-the-future" in rendered

    def test_a_genuinely_zero_cost_is_not_confused_with_an_unknown_one(self):
        assert format_cost(0.0) != format_cost(None)


class TestTranscriptWatermarks:
    def test_the_watermark_is_the_running_total_across_every_model(self):
        source = FakeSource(
            {
                "hs-1": [
                    sample(model="claude-opus-5", usage=Usage(input=10)),
                    sample(model="claude-sonnet-5", usage=Usage(output=5)),
                ]
            }
        )
        assert TranscriptWatermarks(source).watermark("hs-1") == Usage(input=10, output=5)

    def test_an_unknown_host_session_watermarks_none_rather_than_zero(self):
        assert TranscriptWatermarks(FakeSource({})).watermark("hs-nope") is None

    def test_no_host_session_id_watermarks_none(self):
        assert TranscriptWatermarks(FakeSource({})).watermark(None) is None

    def test_a_source_that_raises_never_breaks_the_caller(self):
        class Exploding:
            def sessions(self):
                raise OSError("the disk went away")

            def find(self, host_session_id):
                raise OSError("the disk went away")

            def samples(self, ref):
                raise OSError("the disk went away")

        assert TranscriptWatermarks(Exploding()).watermark("hs-1") is None


class TestMarkers:
    def test_the_no_breakdown_marker_is_neither_a_zero_nor_a_blank(self):
        assert NO_BREAKDOWN.strip()
        assert "0" not in NO_BREAKDOWN
