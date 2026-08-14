"""ADR-002 D3 — elapsed durations and usage watermarks, never a stored time.

Two claims are under test and they fail independently:

* every recorded transition carries how long the state it left had been
  running, and the cumulative token counts at that moment;
* neither is a clock time, and a history that predates both reports *no
  breakdown* rather than a zero.
"""

from __future__ import annotations

import pytest

from invariant.core.stats import Usage, breakdown
from invariant.domains.pipeline import PipelineDomain
from invariant.domains.session import SessionDomain

from test_domains import git_project


@pytest.fixture
def m(tmp_path):
    return git_project(tmp_path / "proj")


class FrozenWatermarks:
    """A :class:`WatermarkSource` that returns whatever it is told to."""

    def __init__(self, *readings):
        self.readings = list(readings)
        self.asked = []

    def watermark(self, host_session_id):
        self.asked.append(host_session_id)
        if not self.readings:
            return None
        return self.readings.pop(0)


class Clock:
    """A monotonic stand-in, so a duration under test is a fact not a race."""

    def __init__(self, *ticks):
        self.ticks = list(ticks)

    def __call__(self):
        return self.ticks.pop(0) if len(self.ticks) > 1 else self.ticks[0]


def advance(domain, session_id, target, **kw):
    """Move the pipeline without the gates being the subject.

    What is under test here is the *record* a transition leaves, not whether
    the transition was allowed — so the gates are overridden explicitly rather
    than satisfied one by one, which would make every case in this file a test
    of the gate it happened to trip on.
    """
    kw.setdefault("force", True)
    kw.setdefault("reason", "the gates are not what this test is about")
    return domain.advance(session_id, target, **kw)


class TestPipelineRecordsElapsed:
    def test_a_transition_records_the_seconds_spent_in_the_state_it_left(self, m):
        domain = PipelineDomain(m, clock=Clock(1000.0, 1060.0))
        domain.start("s1", task="t")
        pipeline, _ = advance(domain, "s1", "red")
        assert pipeline.history[-1]["elapsed"] == pytest.approx(60.0)

    def test_consecutive_transitions_each_measure_their_own_state(self, m):
        domain = PipelineDomain(m, clock=Clock(1000.0, 1060.0, 1075.0))
        domain.start("s1", task="t")
        advance(domain, "s1", "red")
        pipeline, _ = advance(domain, "s1", "green")
        assert [e["elapsed"] for e in pipeline.history] == [
            pytest.approx(60.0),
            pytest.approx(15.0),
        ]

    def test_no_transition_record_carries_a_timestamp(self, m):
        """A whitelist rather than an absence check, so a field added to the
        record has to be looked at rather than merely not called `timestamp`.
        `usage_host` names the counter `usage` was read from and is no more a
        time than `usage` is."""
        domain = PipelineDomain(m, clock=Clock(1000.0, 1060.0))
        domain.start("s1", task="t")
        pipeline, _ = advance(domain, "s1", "red")
        assert set(pipeline.history[-1]) == {
            "from",
            "to",
            "reason",
            "forced",
            "elapsed",
            "usage",
            "usage_host",
        }

    def test_a_pipeline_started_before_this_existed_reports_no_elapsed(self, m):
        """The permanent asymmetry D3 accepts: the information was never
        observed, so no later work can supply it."""
        domain = PipelineDomain(m)
        domain.start("s1", task="t")
        pipeline = domain.require("s1")
        pipeline.history.append({"from": "designing", "to": "red", "reason": None})
        pipeline.state = "red"
        domain._save("s1", pipeline)

        pipeline, _ = advance(domain, "s1", "green")
        assert pipeline.history[-1]["elapsed"] is None
        assert breakdown(pipeline.history) is None


class TestPipelineRecordsAWatermark:
    def test_the_watermark_is_the_running_total_at_that_moment(self, m):
        domain = PipelineDomain(
            m, watermarks=FrozenWatermarks(Usage(input=500), Usage(input=900))
        )
        domain.start("s1", task="t")
        advance(domain, "s1", "red", session={"host_session_id": "hs-1"})
        pipeline, _ = advance(domain, "s1", "green", session={"host_session_id": "hs-1"})
        assert [e["usage"]["input"] for e in pipeline.history] == [500, 900]

    def test_the_baseline_is_taken_when_the_pipeline_starts(self, m):
        domain = PipelineDomain(m, watermarks=FrozenWatermarks(Usage(input=100)))
        pipeline = domain.start("s1", task="t", host_session_id="hs-1")
        assert pipeline.usage_at_start == Usage(input=100).to_dict()

    def test_per_state_spend_is_the_difference_of_consecutive_watermarks(self, m):
        domain = PipelineDomain(
            m,
            clock=Clock(1000.0, 1060.0, 1120.0),
            watermarks=FrozenWatermarks(
                Usage(input=100), Usage(input=250), Usage(input=400)
            ),
        )
        domain.start("s1", task="t", host_session_id="hs-1")
        advance(domain, "s1", "red", session={"host_session_id": "hs-1"})
        pipeline, _ = advance(domain, "s1", "green", session={"host_session_id": "hs-1"})

        spends = breakdown(pipeline.history, Usage.from_dict(pipeline.usage_at_start))
        assert [(s.state, s.usage.input) for s in spends] == [
            ("designing", 150),
            ("red", 150),
        ]

    def test_an_unavailable_watermark_records_none_and_never_zero(self, m):
        domain = PipelineDomain(m, watermarks=FrozenWatermarks(None))
        domain.start("s1", task="t")
        pipeline, _ = advance(domain, "s1", "red")
        assert pipeline.history[-1]["usage"] is None

    def test_with_no_watermark_source_the_transition_still_happens(self, m):
        domain = PipelineDomain(m)
        domain.start("s1", task="t")
        pipeline, _ = advance(domain, "s1", "red")
        assert pipeline.state == "red" and pipeline.history[-1]["usage"] is None

    def test_a_watermark_source_that_raises_never_fails_the_transition(self, m):
        class Exploding:
            def watermark(self, host_session_id):
                raise RuntimeError("telemetry is not a reason to refuse a transition")

        domain = PipelineDomain(m, watermarks=Exploding())
        domain.start("s1", task="t")
        pipeline, _ = advance(domain, "s1", "red")
        assert pipeline.state == "red" and pipeline.history[-1]["usage"] is None

    def test_the_host_session_id_comes_from_the_session_record(self, m):
        watermarks = FrozenWatermarks(Usage(input=1), Usage(input=2))
        domain = PipelineDomain(m, watermarks=watermarks)
        domain.start("s1", task="t")
        advance(domain, "s1", "red", session={"host_session_id": "hs-xyz"})
        assert watermarks.asked[-1] == "hs-xyz"

    def test_a_watermark_records_the_counter_it_was_read_from(self, m):
        """A cumulative count is only commensurable with another taken from the
        same transcript, and `session adopt --force` can move the binding
        between two transitions of one pipeline. The counter's identity is
        recorded with the reading rather than assumed from whatever the session
        is bound to when the report is rendered (BUG_014)."""
        domain = PipelineDomain(
            m, watermarks=FrozenWatermarks(Usage(input=1), Usage(input=2))
        )
        pipeline = domain.start("s1", task="t", host_session_id="hs-a")
        assert pipeline.usage_at_start_host == "hs-a"
        pipeline, _ = advance(domain, "s1", "red", session={"host_session_id": "hs-b"})
        assert pipeline.history[-1]["usage_host"] == "hs-b"

    def test_a_reading_that_could_not_be_taken_names_no_counter(self, m):
        """The pair is written together or not at all: a counter beside no
        reading is a fact about nothing."""
        domain = PipelineDomain(m, watermarks=FrozenWatermarks(None))
        pipeline = domain.start("s1", task="t", host_session_id="hs-a")
        assert pipeline.usage_at_start is None and pipeline.usage_at_start_host is None
        pipeline, _ = advance(domain, "s1", "red", session={"host_session_id": "hs-a"})
        assert pipeline.history[-1]["usage_host"] is None


class TestSessionRecordsElapsed:
    def test_a_transition_records_the_seconds_spent_in_the_state_it_left(self, m):
        domain = SessionDomain(m, clock=Clock(2000.0, 2045.0))
        session = domain.start("a-task")
        domain.transition(session.id, "abandoned", reason="done")
        assert domain.get(session.id).history[-1]["elapsed"] == pytest.approx(45.0)

    def test_no_session_transition_carries_a_timestamp(self, m):
        domain = SessionDomain(m)
        session = domain.start("a-task")
        domain.transition(session.id, "abandoned", reason="done")
        assert set(domain.get(session.id).history[-1]) == {
            "from",
            "to",
            "reason",
            "elapsed",
            "usage",
            "usage_host",
        }

    def test_a_session_records_its_baseline_watermark_at_registration(self, m):
        domain = SessionDomain(m, watermarks=FrozenWatermarks(Usage(output=7)))
        session = domain.start("a-task", host_session_id="hs-1")
        assert session.usage_at_start == Usage(output=7).to_dict()

    def test_a_session_transition_records_a_watermark(self, m):
        domain = SessionDomain(
            m, watermarks=FrozenWatermarks(Usage(output=7), Usage(output=19))
        )
        session = domain.start("a-task", host_session_id="hs-1")
        domain.transition(session.id, "abandoned", reason="done")
        assert domain.get(session.id).history[-1]["usage"]["output"] == 19

    def test_a_session_watermark_records_its_counter_as_a_pipeline_does(self, m):
        """The session machine has no consumer for its breakdown yet (ADR-002's
        Consequences records it as forward-looking), and it records the counter
        anyway — a future reader inherits the commensurability guard rather than
        the bug it was written for."""
        domain = SessionDomain(
            m, watermarks=FrozenWatermarks(Usage(output=7), Usage(output=19))
        )
        session = domain.start("a-task", host_session_id="hs-1")
        assert session.usage_at_start_host == "hs-1"
        domain.transition(session.id, "abandoned", reason="done")
        assert domain.get(session.id).history[-1]["usage_host"] == "hs-1"

    def test_a_watermark_source_that_raises_never_fails_a_session_transition(self, m):
        class Exploding:
            def watermark(self, host_session_id):
                raise RuntimeError("nope")

        domain = SessionDomain(m, watermarks=Exploding())
        session = domain.start("a-task")
        domain.transition(session.id, "abandoned", reason="done")
        assert domain.get(session.id).state == "abandoned"

    def test_a_history_written_before_this_existed_reports_no_breakdown(self, m):
        domain = SessionDomain(m)
        session = domain.start("a-task")
        session.history.append({"from": "registered", "to": "isolated", "reason": None})
        domain._save(session)
        assert breakdown(domain.get(session.id).history) is None


class TestNothingWrittenLooksLikeAClockTime:
    def test_a_stored_pipeline_history_holds_no_epoch_shaped_number(self, m):
        domain = PipelineDomain(m, clock=Clock(1_780_000_000.0, 1_780_000_060.0))
        domain.start("s1", task="t")
        pipeline, _ = advance(domain, "s1", "red")
        for entry in pipeline.history:
            assert entry["elapsed"] < 1_000_000_000

    def test_a_stored_session_history_holds_no_epoch_shaped_number(self, m):
        domain = SessionDomain(m, clock=Clock(1_780_000_000.0, 1_780_000_045.0))
        session = domain.start("a-task")
        domain.transition(session.id, "abandoned", reason="done")
        for entry in domain.get(session.id).history:
            assert entry["elapsed"] is None or entry["elapsed"] < 1_000_000_000
