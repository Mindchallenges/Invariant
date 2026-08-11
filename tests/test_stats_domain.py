"""ADR-002 D1, D5, D6, D7, D8 — the store, its invariants, and its report.

`domains/stats` is not a state machine. It is a store with invariants over it
and operations that repair them, on the `memorystore` precedent — so what is
under test is the invariants, not a lifecycle.
"""

from __future__ import annotations

import pytest

from invariant.core.errors import UsageError
from invariant.core.privacy import PATTERNS, PrivacyScanner
from invariant.core.stats import (
    COST_FLOOR_LABEL,
    NO_BREAKDOWN,
    RATES_AS_OF,
    Sample,
    SourceRef,
    Usage,
)
from invariant.domains.pipeline import PipelineDomain
from invariant.domains.session import SessionDomain
from invariant.domains.stats import StatsDomain

from test_domains import git_project

#: Obviously synthetic, and UUID-shaped on purpose: D7 says a host identifier
#: never reaches the rendered report, and a shape the privacy scanner
#: recognises is what makes that claim testable rather than asserted.
HOST_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
HOST_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"


@pytest.fixture
def m(tmp_path):
    return git_project(tmp_path / "proj")


class FakeSource:
    """A :class:`SampleSource` with no disk behind it."""

    def __init__(self, sessions=None):
        #: host id -> (watermark, [samples])
        self.data = dict(sessions or {})

    def put(self, host_session_id, samples, watermark="w1"):
        self.data[host_session_id] = (watermark, list(samples))

    def drop(self, host_session_id):
        self.data.pop(host_session_id, None)

    def sessions(self):
        return [
            SourceRef(host_session_id=k, watermark=v[0]) for k, v in self.data.items()
        ]

    def find(self, host_session_id):
        entry = self.data.get(host_session_id)
        if entry is None:
            return None
        return SourceRef(host_session_id=host_session_id, watermark=entry[0])

    def samples(self, ref):
        entry = self.data.get(ref.host_session_id)
        return list(entry[1]) if entry else []


def sample(host=HOST_A, ts=1_780_000_000.0, model="claude-opus-5", usage=None, **kw):
    return Sample(
        host_session_id=host,
        timestamp=ts,
        model=model,
        usage=usage or Usage(input=1000, output=500),
        **kw,
    )


def domain(m, source, **kw):
    return StatsDomain(m, source=source, **kw)


class TestRefreshBuildsTheStore:
    def test_a_record_is_written_per_host_session(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        source.put(HOST_B, [sample(host=HOST_B)])
        result = domain(m, source).refresh()
        assert result["rebuilt"] == 2
        assert {r.host_session_id for r in domain(m, source).records()} == {HOST_A, HOST_B}

    def test_tokens_land_in_the_record(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample(usage=Usage(input=10)), sample(usage=Usage(input=5))])
        stats = domain(m, source).refresh() and domain(m, source).get(HOST_A).stats
        assert stats.total_usage.input == 15

    def test_an_unchanged_watermark_is_not_re_parsed(self, m):
        """D5: the store is a cache keyed on a watermark of the source."""
        source = FakeSource()
        source.put(HOST_A, [sample()], watermark="w1")
        stats = domain(m, source)
        stats.refresh()
        assert stats.refresh()["unchanged"] == 1

    def test_a_changed_watermark_rebuilds_the_record(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample(usage=Usage(input=1))], watermark="w1")
        domain(m, source).refresh()
        source.put(
            HOST_A, [sample(usage=Usage(input=1)), sample(usage=Usage(input=9))], "w2"
        )
        assert domain(m, source).refresh()["rebuilt"] == 1
        assert domain(m, source).get(HOST_A).stats.total_usage.input == 10

    def test_force_rebuilds_an_unchanged_record(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        assert stats.refresh(force=True)["rebuilt"] == 1

    def test_refresh_without_a_source_says_so_rather_than_reporting_nothing(self, m):
        with pytest.raises(UsageError, match="no telemetry source"):
            StatsDomain(m, source=None).refresh()

    def test_a_transcript_that_yields_no_samples_still_records_the_session(self, m):
        source = FakeSource()
        source.put(HOST_A, [])
        domain(m, source).refresh()
        assert domain(m, source).get(HOST_A).stats.message_count == 0


class TestTheJoinToAnInvariantSession:
    def bind(self, m, host=HOST_A, name="stats-domain"):
        sessions = SessionDomain(m)
        session = sessions.start(name, host_session_id=host)
        return sessions, session

    def test_a_bound_session_contributes_its_name(self, m):
        _, session = self.bind(m)
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        record = stats.get(HOST_A)
        assert record.session_name == "stats-domain"
        assert record.session_id == session.id

    def test_an_unbound_host_session_still_gets_a_record(self, m):
        """Work predating the binding still happened and still cost something."""
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        assert stats.get(HOST_A).session_name is None

    def test_the_join_refreshes_even_when_the_transcript_has_not_changed(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        self.bind(m)
        stats.refresh()
        assert stats.get(HOST_A).session_name == "stats-domain"


class TestPerStateAttribution:
    def wired(self, m, host=HOST_A):
        sessions = SessionDomain(m)
        session = sessions.start("a-task", host_session_id=host)
        return sessions, session

    def test_a_pipeline_with_elapsed_and_watermarks_yields_a_breakdown(self, m):
        _, session = self.wired(m)

        class Watermarks:
            readings = [Usage(input=100), Usage(input=250), Usage(input=400)]

            def watermark(self, host_session_id):
                return self.readings.pop(0) if self.readings else None

        pipelines = PipelineDomain(m, watermarks=Watermarks())
        pipelines.start(session.id, task="t", host_session_id=HOST_A)
        for target in ("red", "green"):
            pipelines.advance(
                session.id,
                target,
                force=True,
                reason="gates are not the subject",
                session=session.to_dict(),
            )

        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        states = stats.get(HOST_A).states
        assert [s["state"] for s in states] == ["designing", "red"]
        assert states[0]["usage"]["input"] == 150

    def test_a_pipeline_predating_the_change_reports_no_breakdown(self, m):
        _, session = self.wired(m)
        pipelines = PipelineDomain(m)
        pipelines.start(session.id, task="t")
        pipeline = pipelines.require(session.id)
        pipeline.history.append({"from": "designing", "to": "red", "reason": None})
        pipeline.state = "red"
        pipelines._save(session.id, pipeline)

        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        assert stats.get(HOST_A).states is None

    def test_a_session_with_no_pipeline_at_all_reports_no_breakdown(self, m):
        self.wired(m)
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        assert stats.get(HOST_A).states is None


class TestArchiving:
    def test_a_record_starts_rebuildable_and_not_permanent(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        assert stats.get(HOST_A).archived is False

    def test_archiving_promotes_one_record(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        stats.archive(HOST_A)
        assert domain(m, source).get(HOST_A).archived is True

    def test_a_record_can_be_archived_by_its_invariant_session_name(self, m):
        SessionDomain(m).start("a-task", host_session_id=HOST_A)
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        assert stats.archive("a-task").host_session_id == HOST_A

    def test_archiving_an_unknown_selector_names_what_it_looked_for(self, m):
        with pytest.raises(UsageError, match="no stats record"):
            domain(m, FakeSource()).archive("nothing-like-this")

    def test_an_archived_record_survives_the_transcript_disappearing(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        stats.archive(HOST_A)
        source.drop(HOST_A)
        stats.refresh()
        assert stats.get(HOST_A) is not None

    def test_archiving_is_idempotent(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        stats.archive(HOST_A)
        assert stats.archive(HOST_A).archived is True


class TestCheck:
    def test_a_freshly_refreshed_store_is_clean(self, m, tmp_path):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        stats.write_report()
        assert stats.check().clean

    def test_a_moved_watermark_reports_as_stale(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()], watermark="w1")
        stats = domain(m, source)
        stats.refresh()
        stats.write_report()
        source.put(HOST_A, [sample()], watermark="w2")
        assert any("stale" in p for p in stats.check().problems())

    def test_a_host_session_with_no_record_is_reported(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        stats.write_report()
        source.put(HOST_B, [sample(host=HOST_B)])
        assert any("not been ingested" in p for p in stats.check().problems())

    def test_a_record_whose_transcript_is_gone_and_is_not_archived_is_reported(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        stats.write_report()
        source.drop(HOST_A)
        problems = stats.check().problems()
        assert any("only copy" in p for p in problems)

    def test_an_unpriced_model_is_reported_and_never_silently_zeroed(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample(model="claude-from-the-future")])
        stats = domain(m, source)
        stats.refresh()
        stats.write_report()
        assert any("claude-from-the-future" in p for p in stats.check().problems())

    def test_a_missing_report_is_reported(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        assert any("no rendered report" in p for p in stats.check().problems())

    def test_a_report_written_before_the_store_moved_is_reported_stale(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        stats.write_report()
        source.put(HOST_B, [sample(host=HOST_B)])
        stats.refresh()
        assert any("report is stale" in p for p in stats.check().problems())


class TestTheRenderedReport:
    def rendered(self, m, source=None, **kw):
        source = source or FakeSource()
        stats = domain(m, source, **kw)
        stats.refresh()
        return stats, stats.render()

    def test_a_row_is_keyed_on_the_session_name_and_a_bare_date(self, m):
        SessionDomain(m).start("stats-domain", host_session_id=HOST_A)
        source = FakeSource()
        source.put(HOST_A, [sample()])
        _, text = self.rendered(m, source)
        assert "stats-domain" in text
        assert "2026-05-28" in text

    def test_a_host_session_uuid_never_reaches_the_report(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        _, text = self.rendered(m, source)
        assert HOST_A not in text

    def test_the_report_passes_the_projects_own_privacy_gate(self, m):
        """Rendered with every shipped pattern on, including device_ids."""
        SessionDomain(m).start("stats-domain", host_session_id=HOST_A)
        source = FakeSource()
        source.put(
            HOST_A,
            [
                sample(ts=1_780_000_000.0, tools=("Read",), agents=(("x", "claude-opus-5"),)),
                sample(ts=1_780_050_000.0),
            ],
        )
        _, text = self.rendered(m, source)
        findings = PrivacyScanner(patterns=list(PATTERNS)).scan_text(text)
        assert findings == []

    def test_every_currency_figure_is_labelled_a_floor(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        _, text = self.rendered(m, source)
        assert COST_FLOOR_LABEL in text
        assert ">= $" in text

    def test_the_rates_date_is_rendered_beside_the_figures(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        _, text = self.rendered(m, source)
        assert RATES_AS_OF in text

    def test_a_session_with_an_unpriced_model_reports_no_total(self, m):
        source = FakeSource()
        source.put(
            HOST_A,
            [sample(model="claude-opus-5"), sample(model="claude-from-the-future")],
        )
        _, text = self.rendered(m, source)
        assert "unpriced" in text
        assert "claude-from-the-future" in text

    def test_a_session_with_no_breakdown_says_so_explicitly(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        assert NO_BREAKDOWN in stats.render_session(HOST_A)

    def test_the_agents_view_reports_launches_and_never_cost(self, m):
        source = FakeSource()
        source.put(
            HOST_A,
            [
                sample(agents=(("feature-architect", "claude-opus-5"),)),
                sample(agents=(("feature-architect", "claude-opus-5"),)),
            ],
        )
        stats = domain(m, source)
        stats.refresh()
        text = stats.render()
        agents_section = text.split("## Agents")[1].split("##")[0]
        assert "feature-architect" in agents_section
        assert "$" not in agents_section

    def test_the_agents_view_explains_why_it_shows_no_cost(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample(agents=(("curator", "inherited"),))])
        _, text = self.rendered(m, source)
        assert "unrecoverable" in text or "not recorded" in text

    def test_an_empty_store_renders_a_report_rather_than_failing(self, m):
        _, text = self.rendered(m)
        assert "no sessions" in text.lower()

    def test_the_header_describes_what_the_report_covers(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        source.put(HOST_B, [sample(host=HOST_B)])
        _, text = self.rendered(m, source)
        assert "2 session" in text


class TestWritingTheReport:
    def test_the_report_lands_in_the_manifest_declared_store(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        path = stats.write_report()
        assert path.is_file()
        assert path.parent == m.path_for("reports")

    def test_writing_twice_produces_the_same_bytes(self, m):
        """A report that churns cannot be committed, and D5 makes it derived."""
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        first = stats.write_report().read_text(encoding="utf-8")
        assert stats.write_report().read_text(encoding="utf-8") == first

    def test_the_written_report_carries_a_fingerprint_of_the_store(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        text = stats.write_report().read_text(encoding="utf-8")
        assert stats.fingerprint() in text


class TestRenderingASessionInFull:
    def wired(self, m, watermarks=None):
        session = SessionDomain(m).start("a-task", host_session_id=HOST_A)
        pipelines = PipelineDomain(m, watermarks=watermarks)
        pipelines.start(session.id, task="t", host_session_id=HOST_A)
        for target in ("red", "green"):
            pipelines.advance(
                session.id,
                target,
                force=True,
                reason="gates are not the subject",
                session=session.to_dict(),
            )
        return session

    def test_a_breakdown_renders_one_row_per_state(self, m):
        class Watermarks:
            readings = [Usage(input=100), Usage(input=250), Usage(input=400)]

            def watermark(self, host_session_id):
                return self.readings.pop(0) if self.readings else None

        self.wired(m, Watermarks())
        source = FakeSource()
        source.put(HOST_A, [sample(agents=(("feature-architect", "claude-opus-5"),))])
        stats = domain(m, source)
        stats.refresh()
        text = stats.render_session("a-task")
        assert "per state:" in text
        assert "designing" in text and "red" in text
        assert "feature-architect" in text

    def test_a_state_with_no_watermark_renders_unknown_and_not_zero(self, m):
        self.wired(m)
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        text = stats.render_session("a-task")
        assert "unknown" in text
        assert "0 tokens" not in text

    def test_a_partial_breakdown_says_the_rows_do_not_add_up(self, m):
        """One pipeline attributed and one not: reporting the second as zero
        would claim its states were free."""
        session = self.wired(m)
        pipelines = PipelineDomain(m)
        raw = pipelines.store.read(f"pipelines/{session.id}.json")
        raw["completed"] = [
            {
                "session_id": session.id,
                "methodology": "tdd",
                "state": "committed",
                "history": [{"from": "designing", "to": "red", "reason": None}],
            }
        ]
        pipelines.store.write(f"pipelines/{session.id}.json", raw)

        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        assert stats.get(HOST_A).states_partial is True
        assert "do not add up" in stats.render_session("a-task")

    def test_rendering_an_unknown_session_names_what_it_looked_for(self, m):
        with pytest.raises(UsageError, match="no stats record"):
            domain(m, FakeSource()).render_session("nothing-like-this")

    def test_spend_sums_across_a_sessions_pipelines(self, m):
        """A session runs pipelines in sequence; one state's total is the sum."""
        session = SessionDomain(m).start("a-task", host_session_id=HOST_A)
        pipelines = PipelineDomain(m)
        history = [
            {
                "from": "designing",
                "to": "red",
                "reason": None,
                "elapsed": 10.0,
                "usage": Usage(input=200).to_dict(),
            }
        ]
        pipelines.store.write(
            f"pipelines/{session.id}.json",
            {
                "current": {
                    "session_id": session.id,
                    "methodology": "tdd",
                    "state": "red",
                    "history": history,
                    "usage_at_start": Usage(input=100).to_dict(),
                },
                "completed": [
                    {
                        "session_id": session.id,
                        "methodology": "tdd",
                        "state": "committed",
                        "history": history,
                        "usage_at_start": Usage(input=100).to_dict(),
                    }
                ],
            },
        )
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        [state] = stats.get(HOST_A).states
        assert state["usage"]["input"] == 200
        assert state["elapsed"] == 20.0


class TestFaultTolerance:
    def test_a_source_that_raises_while_checking_degrades_the_check(self, m):
        class Exploding:
            def sessions(self):
                raise OSError("the host directory went away")

            def find(self, host_session_id):
                raise OSError("the host directory went away")

            def samples(self, ref):
                raise OSError("the host directory went away")

        stats = StatsDomain(m, source=Exploding())
        report = stats.check()
        assert report.records == 0
        assert any("no rendered report" in p for p in report.problems())

    def test_the_watermark_source_is_built_from_the_injected_sample_source(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample(usage=Usage(input=42))])
        assert domain(m, source).watermarks().watermark(HOST_A) == Usage(input=42)

    def test_a_report_path_outside_the_project_still_renders_a_message(
        self, m, tmp_path
    ):
        """`_relative` falls back to a name rather than raising on a path that
        is not under the project root."""
        m.data["paths"]["reports"] = str(tmp_path / "elsewhere")
        stats = StatsDomain(m, source=FakeSource())
        assert any("no rendered report" in p for p in stats.check().problems())

    def test_an_empty_selector_resolves_to_nothing(self, m):
        assert domain(m, FakeSource()).resolve("") is None


class TestStoreRoundTrip:
    def test_a_record_survives_a_round_trip_through_json(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample(tools=("Read",), agents=(("a", "claude-opus-5"),))])
        stats = domain(m, source)
        stats.refresh()
        assert domain(m, source).get(HOST_A).to_dict() == stats.get(HOST_A).to_dict()

    def test_a_corrupt_record_is_skipped_rather_than_fatal(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        (m.state_root / "stats" / f"{HOST_A}.json").write_text("{]", encoding="utf-8")
        assert stats.records() == []
        assert any("could not be read" in p for p in stats.check().problems())
