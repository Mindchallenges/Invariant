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
    DECLINED_NO_PIPELINE,
    DECLINED_PRE_INSTRUMENTATION,
    DECLINED_UNJOINED,
    NO_BREAKDOWN,
    NO_BREAKDOWN_NO_PIPELINE,
    NO_BREAKDOWN_UNJOINED,
    NO_BREAKDOWN_UNKNOWN,
    RATES_AS_OF,
    SPEND_NOTE_BACKWARDS,
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
        assert record.session_name == session.name
        assert record.session_name.startswith("stats-domain-")
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
        _, session = self.bind(m)
        stats.refresh()
        assert stats.get(HOST_A).session_name == session.name


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
        record = stats.get(HOST_A)
        assert record.states is None
        assert record.states_reason == DECLINED_PRE_INSTRUMENTATION
        assert NO_BREAKDOWN in stats.render_session(HOST_A)

    def test_a_session_with_no_pipeline_at_all_reports_no_breakdown(self, m):
        self.wired(m)
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        assert stats.get(HOST_A).states is None

    def test_a_session_with_no_pipeline_says_that_and_not_that_it_is_old(self, m):
        """The third fact behind the one `None`, and the one the repro for
        BUG_013 deliberately left to whoever fixed it: a session that has never
        started a pipeline has nothing to attribute for a reason that has
        nothing to do with when it ran."""
        self.wired(m)
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()

        record = stats.get(HOST_A)
        text = stats.render_session(HOST_A)
        assert record.states_reason == DECLINED_NO_PIPELINE
        assert NO_BREAKDOWN_NO_PIPELINE in text
        assert "recorded before per-state attribution" not in text

    def test_the_reason_there_is_no_breakdown_survives_the_store(self, m):
        """It is read back out of the record rather than re-derived, so it has
        to be written down."""
        self.wired(m)
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        assert domain(m, source).get(HOST_A).states_reason == DECLINED_NO_PIPELINE


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


class TestResolvingARecordByName:
    """IDEA_005 — a record is selected by the thing a person remembers.

    A session name carries its id, so the bare task name is a PREFIX of the
    record's `session_name` rather than equal to it, and equality selected
    nothing. The ladder is `resolve_name`'s, minus the rungs that need a live
    set: a record outlives the session that produced it.
    """

    def joined(self, m, *specs):
        """One session, one transcript and one record per `(slug, id, host)`."""
        source = FakeSource()
        for slug, session_id, host in specs:
            SessionDomain(m).start(slug, session_id=session_id, host_session_id=host)
            source.put(host, [sample(host=host)])
        stats = domain(m, source)
        stats.refresh()
        return stats

    def test_a_bare_task_name_selects_the_record(self, m):
        stats = self.joined(m, ("add-widget", "aaaa1111", HOST_A))
        found = stats.resolve("add-widget")
        assert found is not None and found.session_name == "add-widget-aaaa1111"

    def test_a_name_prefix_selects_the_record(self, m):
        stats = self.joined(m, ("add-widget", "aaaa1111", HOST_A))
        found = stats.resolve("add-wid")
        assert found is not None and found.host_session_id == HOST_A

    def test_the_full_name_selects_the_record(self, m):
        stats = self.joined(m, ("add-widget", "aaaa1111", HOST_A))
        assert stats.resolve("add-widget-aaaa1111") is not None

    def test_a_bare_slug_outranks_a_longer_name_it_prefixes(self, m):
        """The rung the flattened predicate cannot express.

        `add-widget` is this record's bare slug and the other record's prefix.
        Both answer the predicate; only one is what the reader typed, and a
        refusal here would be a refusal the ladder had the answer to.
        """
        stats = self.joined(
            m,
            ("add-widget", "aaaa1111", HOST_A),
            ("add-widget-again", "bbbb2222", HOST_B),
        )
        found = stats.resolve("add-widget")
        assert found is not None and found.session_name == "add-widget-aaaa1111"

    def test_a_query_matching_two_records_at_one_rung_refuses_and_names_them(self, m):
        """Never the first hit: a prefix that matches two records has no
        winner, and guessing one would archive the wrong session's telemetry."""
        stats = self.joined(
            m,
            ("add-widget", "aaaa1111", HOST_A),
            ("add-wheel", "bbbb2222", HOST_B),
        )
        with pytest.raises(UsageError) as refusal:
            stats.resolve("add-w")
        text = str(refusal.value)
        assert "add-widget-aaaa1111" in text and "add-wheel-bbbb2222" in text
        assert "invariant stats show add-" in text

    def test_the_refusal_never_prints_a_host_session_identifier(self, m):
        """The rule `stats check` keeps, on the branch beside it (BUG_016 F2)."""
        stats = self.joined(
            m,
            ("add-widget", "aaaa1111", HOST_A),
            ("add-wheel", "bbbb2222", HOST_B),
        )
        with pytest.raises(UsageError) as refusal:
            stats.resolve("add-w")
        assert HOST_A not in str(refusal.value)
        assert HOST_B not in str(refusal.value)

    def test_a_host_session_id_still_selects_its_record(self, m):
        stats = self.joined(m, ("add-widget", "aaaa1111", HOST_A))
        found = stats.resolve(HOST_A)
        assert found is not None and found.host_session_id == HOST_A

    def test_an_invariant_session_id_still_selects_its_record(self, m):
        stats = self.joined(m, ("add-widget", "aaaa1111", HOST_A))
        found = stats.resolve("aaaa1111")
        assert found is not None and found.session_id == "aaaa1111"

    def test_an_identifier_outranks_a_name_that_happens_to_start_with_it(self, m):
        """`resolve_name`'s first rung, kept: an exact id is never beaten."""
        stats = self.joined(
            m,
            ("add-widget", "aaaa1111", HOST_A),
            ("aaaa1111-lookalike", "bbbb2222", HOST_B),
        )
        found = stats.resolve("aaaa1111")
        assert found is not None and found.session_id == "aaaa1111"

    def test_an_unjoined_record_answers_to_no_name(self, m):
        """A record with no session has no name to match, and must not crash."""
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        assert stats.get(HOST_A).session_name is None
        assert stats.resolve("anything") is None

    def test_an_empty_selector_selects_nothing(self, m):
        stats = self.joined(m, ("add-widget", "aaaa1111", HOST_A))
        assert stats.resolve("") is None

    def test_two_records_of_one_session_refuse_with_the_reason(self, m):
        """One name, several host sessions — which no selector separates.

        Unreachable through `refresh` today, because the join reads the
        session's CURRENT binding only. It is reachable the moment the earlier
        hosts on `Session.hosts` are joined too, which is the fix this refusal
        is written to survive rather than to be rewritten by.
        """
        stats = self.joined(m, ("add-widget", "aaaa1111", HOST_A))
        record = stats.get(HOST_A)
        record.host_session_id = HOST_B
        stats._save(record)
        with pytest.raises(UsageError) as refusal:
            stats.resolve("add-widget")
        text = str(refusal.value)
        assert "add-widget-aaaa1111" in text
        assert "several host sessions" in text

    def test_every_surface_refuses_a_miss_with_one_message(self, m):
        """The refusal was written three times, with three accounts of what a
        selector accepts. One of them would have gone stale as the ladder grew
        a rung, and no reader could have told which."""
        stats = self.joined(m, ("add-widget", "aaaa1111", HOST_A))
        messages = []
        for call in (stats.require, stats.archive, stats.render_session):
            with pytest.raises(UsageError) as refusal:
                call("nothing-like-this")
            messages.append(str(refusal.value))
        assert len(set(messages)) == 1
        assert "bare task name" in messages[0]

    def test_show_refuses_an_ambiguous_selector_rather_than_rendering_one(self, m):
        """The renderer resolves too, so the refusal has to reach it."""
        stats = self.joined(
            m,
            ("add-widget", "aaaa1111", HOST_A),
            ("add-wheel", "bbbb2222", HOST_B),
        )
        with pytest.raises(UsageError, match="2 stats records"):
            stats.render_session("add-w")

    def test_archive_refuses_an_ambiguous_selector_rather_than_promoting_one(self, m):
        stats = self.joined(
            m,
            ("add-widget", "aaaa1111", HOST_A),
            ("add-wheel", "bbbb2222", HOST_B),
        )
        with pytest.raises(UsageError, match="2 stats records"):
            stats.archive("add-w")
        assert stats.get(HOST_A).archived is False
        assert stats.get(HOST_B).archived is False


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
        """No invariant session is joined here, so the sentence is the
        unjoined one rather than the pre-instrumentation one (BUG_024)."""
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        assert NO_BREAKDOWN_UNJOINED in stats.render_session(HOST_A)

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
        text = stats.render_session(HOST_A)
        assert "per state:" in text
        assert "designing" in text and "red" in text
        assert "feature-architect" in text

    def test_a_state_with_no_watermark_renders_unknown_and_not_zero(self, m):
        self.wired(m)
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        text = stats.render_session(HOST_A)
        assert "unknown" in text
        assert "0 tokens" not in text

    def test_the_reason_a_state_was_declined_reaches_the_reader(self, m):
        """The sixth finding from the BUG_016 triage. `core/stats` names why it
        declined a state and the merge here used to build a fresh spend and
        overwrite that note with "some transitions carried no watermark" — so
        the reader was told a reading was missing when the code had in fact
        detected two readings that cannot describe one run.

        A single pipeline with one backwards step is enough; no second pipeline
        is needed for the merge to run."""

        class Backwards:
            def __init__(self):
                self.readings = [Usage(input=100), Usage(input=400), Usage(input=250)]

            def watermark(self, host_session_id):
                return self.readings.pop(0) if self.readings else None

        self.wired(m, Backwards())
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()

        [row] = [
            line
            for line in stats.render_session(HOST_A).splitlines()
            if line.strip().startswith("red ")
        ]
        assert SPEND_NOTE_BACKWARDS in row
        assert "carried no watermark" not in row

    def test_a_state_that_did_carry_no_watermark_still_says_so(self, m):
        """The other half: where there really was no reading, the generic note
        is the accurate one and stays."""
        self.wired(m)
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        assert "carried no watermark" in stats.render_session(HOST_A)

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
        assert "do not add up" in stats.render_session(HOST_A)

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


class TestTheNoBreakdownLineDoesNotFabricateAHistory:
    """Repro for BUG_013 — see docs/backlog/BUG_013.md.

    `cli.md` reserves the "no breakdown available" sentence for "a session
    recorded before per-state attribution existed", and `state_model.md` ties
    it to the permanent asymmetry of transitions "recorded before this
    existed". A pipeline that started after the instrumentation landed and has
    simply not transitioned yet is not in that class: it carries a
    `usage_at_start` baseline and an empty history, which is "nothing to
    attribute yet", not "never observed".

    What is asserted is the rendered sentence a reader sees, NOT the internal
    `states is None` flag that produces it — `None` is a legitimate answer for
    both cases, and the entry's expected behaviour explicitly allows both to
    render as no-breakdown. The defect is the fabricated cause attached to it.
    """

    def wired(self, m):
        """A session whose only pipeline was started and never transitioned."""

        class Watermarks:
            def watermark(self, host_session_id):
                return Usage(input=100)

        session = SessionDomain(m).start("a-task", host_session_id=HOST_A)
        pipelines = PipelineDomain(m, watermarks=Watermarks())
        pipelines.start(session.id, task="t", host_session_id=HOST_A)
        return session, pipelines

    def test_the_pipeline_is_post_instrumentation_with_an_empty_history(self, m):
        """The precondition, so a later reader cannot mistake the repro for a
        pre-instrumentation fixture that was never valid."""
        session, pipelines = self.wired(m)
        pipeline = pipelines.require(session.id)
        assert pipeline.history == []
        assert pipeline.usage_at_start == Usage(input=100).to_dict()

    def test_a_never_transitioned_pipeline_is_not_called_pre_instrumentation(self, m):
        self.wired(m)
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()

        text = stats.render_session(HOST_A)

        assert "recorded before per-state attribution" not in text, (
            "the report claims a pre-instrumentation cause for a pipeline "
            "recorded after the instrumentation landed"
        )


class TestTheUnjoinedRecordDoesNotFabricateAHistory:
    """Repro for BUG_024 — the sliver BUG_013's fix deliberately left behind.

    A stats record with no invariant session joined to it was given the
    pre-instrumentation sentence on the ground that nothing is known about
    when that host session ran. That reasoning establishes only that the cause
    is UNKNOWN; it does not license naming one. After a7ebbdc this was the
    last place the renderer printed a cause it had no evidence for, which made
    it the lone exception to the standard BUG_013 established.
    """

    def test_an_unjoined_record_is_not_called_pre_instrumentation(self, m):
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()

        record = stats.get(HOST_A)
        text = stats.render_session(HOST_A)

        assert record.session_id is None
        assert record.states_reason == DECLINED_UNJOINED
        assert "recorded before per-state attribution" not in text, (
            "the report names a pre-instrumentation cause for a record whose "
            "host session it never joined, so it cannot know when it ran"
        )
        assert NO_BREAKDOWN_UNJOINED in text

    def test_a_record_predating_states_reason_claims_no_cause(self, m):
        """The second half of the entry. `states_reason` landed with BUG_013's
        fix, AFTER the D3 instrumentation, so its absence dates a record
        relative to the wrong event and entails nothing about attribution."""
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()

        record = stats.get(HOST_A)
        record.states_reason = None
        stats._save(record)

        text = stats.render_session(HOST_A)
        assert "recorded before per-state attribution" not in text
        assert NO_BREAKDOWN_UNKNOWN in text

    def test_a_freshly_started_second_pipeline_is_not_called_pre_instrumentation(
        self, m
    ):
        """The second surface, same root: one finished pipeline plus one just
        started sets the partial flag, and the partial sentence names the same
        fabricated cause."""
        session = SessionDomain(m).start("a-task", host_session_id=HOST_A)
        pipelines = PipelineDomain(m)
        pipelines.store.write(
            f"pipelines/{session.id}.json",
            {
                # Started after the instrumentation landed, not transitioned yet.
                "current": {
                    "session_id": session.id,
                    "methodology": "tdd",
                    "state": "designing",
                    "history": [],
                    "usage_at_start": Usage(input=300).to_dict(),
                },
                # Fully attributed, so the rows above the sentence are real.
                "completed": [
                    {
                        "session_id": session.id,
                        "methodology": "tdd",
                        "state": "committed",
                        "history": [
                            {
                                "from": "designing",
                                "to": "red",
                                "reason": None,
                                "elapsed": 10.0,
                                "usage": Usage(input=200).to_dict(),
                            }
                        ],
                        "usage_at_start": Usage(input=100).to_dict(),
                    }
                ],
            },
        )
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()

        text = stats.render_session(HOST_A)

        assert "predate per-state attribution" not in text, (
            "the report says some pipelines predate the instrumentation when "
            "the only unattributed one was started after it and has not "
            "transitioned yet"
        )


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

    def test_an_unreadable_record_is_counted_and_never_named(self, m):
        """BUG_016 F2. The un-ingested branch of this same method counts rather
        than names, on the stated grounds that terminal output gets pasted into
        commits. The same rule, one branch up."""
        source = FakeSource()
        source.put(HOST_A, [sample()])
        source.put(HOST_B, [sample(host=HOST_B)])
        stats = domain(m, source)
        stats.refresh()
        for host in (HOST_A, HOST_B):
            (m.state_root / "stats" / f"{host}.json").write_text("{]", encoding="utf-8")

        problems = stats.check().problems()
        assert any("2 record(s) could not be read" in p for p in problems)
        assert not any(HOST_A in p or HOST_B in p for p in problems)


class TestAForcedRebindDoesNotCrossTwoHostSessionsUsageCounters:
    """Repro for BUG_014 — see docs/backlog/BUG_014.md.

    ADR-002 D3 and `state_model.md` both rest per-state spend on one claim:
    "a subtraction of two counts is exact". That holds only while both counts
    come from the same transcript. `session adopt --force` moves the binding
    and leaves `usage_at_start` and the already-recorded history watermarks
    counted against the old host session, so the state spanning the rebind is
    a reading of B's cumulative total minus a reading of A's.

    What is asserted is the figure a reader sees in the per-state table, NOT
    `usage_at_start` being left unchanged — that is the cause, and asserting
    it would prove the plumbing rather than the misreporting. D8's principle
    is what is at stake: a number arrived at by crossing two counters must not
    wear the same units, and the same formatting, as a measured one.

    The direction matters and is the whole point of the entry. Both cases are
    exercised below: B lower than A trips the existing backwards-watermark
    guard and is suppressed, which is correct and stays that way; B higher
    produces a well-behaved positive delta that is rendered as a measurement.
    """

    #: Two unrelated transcripts. B's cumulative total is far the higher, so
    #: the crossing survives the monotonicity guard.
    A_AT_START = 1_000
    A_AT_TRANSITION = 1_200
    B_AT_REBIND = 50_000
    B_AT_TRANSITION = 50_300

    #: 50_300 - 1_200, rendered. B's whole prior history, wearing the units of
    #: what the `red` state spent.
    CROSSED = "49.1k"

    def wired(self, m, b_at_rebind, b_at_transition):
        """A session force-rebound from host A to host B mid-pipeline.

        Driven through the real domain calls — `adopt_host_session(force=True)`
        and `advance` — so that a change to either path is felt here rather
        than being simulated away by a hand-written history.
        """

        class Watermarks:
            """Cumulative counters, one per transcript, as a real source is."""

            def __init__(self):
                self.totals = {}

            def watermark(self, host_session_id):
                return Usage(input=self.totals[host_session_id])

        marks = Watermarks()
        marks.totals = {HOST_A: self.A_AT_START, HOST_B: b_at_rebind}
        sessions = SessionDomain(m, watermarks=marks)
        # A fixed clock: `elapsed` must be present for a breakdown to exist at
        # all, and a real one would make the rendered durations a flake.
        pipelines = PipelineDomain(m, watermarks=marks, clock=lambda: 100.0)

        session = sessions.start("a-task", host_session_id=HOST_A)
        pipelines.start(session.id, task="t", host_session_id=HOST_A)

        # One transition wholly inside A's binding: both readings are A's.
        marks.totals[HOST_A] = self.A_AT_TRANSITION
        pipelines.advance(
            session.id,
            "red",
            force=True,
            reason="fixture",
            session=sessions.require(session.id).to_dict(),
        )

        rebound = sessions.adopt_host_session(session.id, HOST_B, force=True)

        # One transition after the rebind: this reading is B's, and the one it
        # will be subtracted from is still A's.
        marks.totals[HOST_B] = b_at_transition
        pipelines.advance(
            session.id,
            "green",
            force=True,
            reason="fixture",
            session=sessions.require(session.id).to_dict(),
        )

        source = FakeSource()
        source.put(HOST_A, [sample(usage=Usage(input=self.A_AT_TRANSITION))])
        source.put(
            HOST_B,
            [
                sample(
                    host=HOST_B,
                    ts=1_780_000_100.0,
                    usage=Usage(input=b_at_transition),
                )
            ],
        )
        stats = domain(m, source)
        stats.refresh()
        return rebound, stats.render_session(rebound.name)

    def row(self, text, state):
        """The rendered per-state line for one state, as a reader reads it."""
        for line in text.splitlines():
            if line.strip().startswith(state):
                return line
        raise AssertionError(f"no per-state row for {state!r} in:\n{text}")

    def test_the_rebind_happened_and_the_counters_are_incommensurable(self, m):
        """The precondition, so a later reader cannot mistake the repro for a
        fixture that was never valid: the binding really moved, and B's
        transcript really does carry the higher cumulative total."""
        rebound, _ = self.wired(m, self.B_AT_REBIND, self.B_AT_TRANSITION)
        assert rebound.host_session_id == HOST_B
        assert self.B_AT_TRANSITION > self.A_AT_TRANSITION

    def test_a_state_inside_one_binding_is_attributed_normally(self, m):
        """The arithmetic is sound where both readings are the same counter's,
        so the row below fails for the crossing and not for the harness."""
        _, text = self.wired(m, self.B_AT_REBIND, self.B_AT_TRANSITION)
        assert "200 tokens" in self.row(text, "designing")

    def test_the_lower_direction_is_caught_by_the_existing_guard(self, m):
        """The other half of the asymmetry, and the control for the repro
        below: with B's total the LOWER of the two the same crossing violates
        monotonicity visibly, the guard declines to attribute it, and this
        harness renders the declined form. That is the outcome the higher
        direction must also reach, and it stays green throughout — the guard
        is correct and no fix may weaken it."""
        _, text = self.wired(m, 400, 700)
        assert "unknown tokens" in self.row(text, "red")

    def test_the_state_spanning_the_rebind_is_not_rendered_as_a_measurement(self, m):
        """The state either side of the rebind is reported as having spent
        B's entire prior transcript, formatted exactly like the measured row
        above it and carrying no note that would let a reader tell."""
        _, text = self.wired(m, self.B_AT_REBIND, self.B_AT_TRANSITION)

        assert self.CROSSED not in self.row(text, "red"), (
            "the state spanning the rebind is rendered as a measured spend of "
            f"{self.CROSSED} tokens, which is host session B's cumulative "
            "total minus host session A's watermark — two unrelated counters"
        )



class TestAFrozenTranscriptDoesNotRenderAsZeroSpend:
    """Repro for BUG_016 F1 — see docs/backlog/BUG_016.md.

    The entry records this sub-finding's REACHABILITY as CANNOT-VERIFY and asks
    that it be settled before a line is changed. It is settled, and the answer
    is REACHABLE — by a plainer mechanism than the host-side transcript rotation
    the entry hypothesised. Two watermark reads return equal values whenever no
    billed assistant record is appended between them, which is what happens
    whenever two `invariant` invocations are chained in one shell command: both
    processes read the same bytes. No freezing and no rotation is required.

    What that produces is a `Usage()` whose every field is zero rather than
    `None`, and ADR-002 D3 is explicit that an unobservable spend is "the same
    `None`, rendered as 'no breakdown' rather than a zero", because "a blank
    invites the reader to conclude the work was free" — which a rendered zero
    invites more strongly than a blank does.
    """

    class Frozen:
        """A findable transcript that is not growing: every read is the same.

        Deliberately NOT returning `None` — `None` is the path that already
        renders correctly, and conflating the two is the whole defect.
        """

        def __init__(self, reading: Usage) -> None:
            self.reading = reading
            self.reads = 0

        def watermark(self, host_session_id):
            self.reads += 1
            return self.reading

    class Moving:
        """The control: a transcript that grows between reads."""

        def __init__(self) -> None:
            self.readings = [Usage(input=100), Usage(input=250), Usage(input=400)]

        def watermark(self, host_session_id):
            return self.readings.pop(0) if self.readings else None

    def wired(self, m, watermarks):
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
        source = FakeSource()
        source.put(HOST_A, [sample()])
        stats = domain(m, source)
        stats.refresh()
        return stats, stats.render_session(session.name)

    def spend(self, text, state):
        """The spend field of one rendered row, and only that field.

        Asserting on the whole rendering would be a trap: `"0 tokens" not in
        text` is satisfied by neither of the two things it looks like it says,
        because `150 tokens` contains it as a substring.
        """
        for line in text.splitlines():
            if line.strip().startswith(f"{state} "):
                return line.split(" tokens")[0].split()[-1]
        raise AssertionError(f"no rendered row for state {state!r} in:\n{text}")

    def test_the_frozen_transcript_is_findable_and_every_transition_recorded_it(
        self, m
    ):
        """The precondition, and the reason this is not the already-correct
        `None` path: the watermark source ANSWERS on every read, and the
        baseline and both transitions each recorded one.

        Asserted on what was RECORDED rather than on what was derived from it.
        The derived form is the subject of the repro below — a state whose two
        readings were both taken and cannot be subtracted into a spend is
        exactly what the fix produces — so pinning it here would pin the defect
        as the precondition for its own repro."""
        frozen = self.Frozen(Usage(input=1200, output=300))
        stats, _ = self.wired(m, frozen)

        assert frozen.reads == 3, "one baseline read and one per transition"
        record = stats.get(HOST_A)
        pipeline = PipelineDomain(m).require(record.session_id)
        assert pipeline.usage_at_start is not None
        assert all(entry["usage"] is not None for entry in pipeline.history), (
            "every transition recorded a watermark, so nothing here is the "
            "unobservable-watermark path"
        )
        assert record.states is not None, "a breakdown was produced, not declined"

    def test_a_growing_transcript_still_renders_a_real_spend(self, m):
        """Falsifiability control: the assertion below is satisfiable in this
        harness rather than unconditional. With a transcript that moves, the
        same rows render measured numbers."""
        _, text = self.wired(m, self.Moving())
        assert self.spend(text, "designing") == "150"
        assert self.spend(text, "red") == "150"

    def test_no_watermark_at_all_renders_the_form_a_fix_must_produce(self, m):
        """The other control, and the outcome this defect should reach: where
        the watermark cannot be observed the row already renders `unknown`
        rather than a zero. The fix makes the frozen case render this way too,
        and this row must stay green throughout — it is the behaviour the fix
        extends, not one it may weaken."""
        _, text = self.wired(m, None)
        assert self.spend(text, "designing") == "unknown"
        assert self.spend(text, "red") == "unknown"

    def test_a_state_whose_watermark_did_not_move_is_not_rendered_as_zero(self, m):
        """Every state a frozen transcript spans is rendered as a hard `0`,
        formatted identically to a state that genuinely cost nothing and
        carrying no note that would let a reader tell the two apart."""
        _, text = self.wired(m, self.Frozen(Usage(input=1200, output=300)))

        assert self.spend(text, "designing") != "0", (
            "a state whose watermark could not be observed to move renders as "
            "a measured `0 tokens`, which reads as 'this work was free' rather "
            "than as 'this spend was not observed' — the failure mode ADR-002 "
            "D3 names, and the opposite of the `unknown` every other "
            "unobservable path renders"
        )
