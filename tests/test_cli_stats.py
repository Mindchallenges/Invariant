"""The `invariant stats` surface, and D4's write-scope amendment.

The CLI is where the concrete host reader is injected into the domain (D2), so
this is also the only place the two halves meet.
"""

from __future__ import annotations

import json

import pytest

from test_cli import out, payload, project, run  # noqa: F401 - fixtures

HOST_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"


def transcript(host_home, origin, host_session_id, records):
    directory = host_home / "projects" / str(origin.resolve()).replace("/", "-")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{host_session_id}.jsonl"
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    return path


def assistant(input_tokens=1000, output_tokens=500, ts="2026-06-24T09:00:00Z", **extra):
    message = {
        "model": "claude-opus-5",
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
    message.update(extra)
    return {"type": "assistant", "timestamp": ts, "message": message}


@pytest.fixture
def wired(project, tmp_path, monkeypatch):
    """A project with a manifest, a bound session, and one transcript."""
    home = tmp_path / "host"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", HOST_A)
    run("init", "--ecosystem", "python")
    run("session", "start", "stats-domain")
    transcript(home, project, HOST_A, [assistant(), assistant(ts="2026-06-24T09:05:00Z")])
    return project


class TestRefresh:
    def test_it_ingests_what_the_host_wrote_down(self, wired, capsys):
        assert run("stats", "refresh", "--json") == 0
        assert payload(capsys)["rebuilt"] == 1

    def test_a_second_refresh_re_reads_nothing(self, wired, capsys):
        run("stats", "refresh")
        assert run("stats", "refresh", "--json") == 0
        assert payload(capsys)["unchanged"] == 1

    def test_force_rebuilds(self, wired, capsys):
        run("stats", "refresh")
        assert run("stats", "refresh", "--force", "--json") == 0
        assert payload(capsys)["rebuilt"] == 1


class TestAHostWithNoReader:
    def test_ingest_refuses_and_says_why(self, wired, capsys):
        """The protocol's other arm: a host this release cannot read back.

        Nothing crashes and nothing reports zero — the store simply has no way
        to grow, and the refusal names that rather than an empty result."""
        import yaml

        path = wired / ".claude" / "invariant.yaml"
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
        body["host"] = {"kind": "some-other-host"}
        path.write_text(yaml.safe_dump(body), encoding="utf-8")

        assert run("stats", "refresh") == 2
        assert "no telemetry source" in capsys.readouterr().err

    def test_reading_the_store_still_works_without_a_reader(self, wired, capsys):
        import yaml

        run("stats", "refresh")
        path = wired / ".claude" / "invariant.yaml"
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
        body["host"] = {"kind": "some-other-host"}
        path.write_text(yaml.safe_dump(body), encoding="utf-8")

        assert run("stats", "report") == 0
        assert "stats-domain" in out(capsys)


class TestReport:
    def test_the_report_prints_without_writing_anything(self, wired, capsys):
        run("stats", "refresh")
        assert run("stats", "report") == 0
        text = out(capsys)
        assert "stats-domain" in text
        assert not (wired / "docs" / "reports").exists()

    def test_write_puts_it_in_the_manifest_declared_store(self, wired, capsys):
        run("stats", "refresh")
        assert run("stats", "report", "--write") == 0
        assert (wired / "docs" / "reports" / "sessions.md").is_file()

    def test_a_host_session_uuid_never_reaches_the_written_report(self, wired):
        run("stats", "refresh")
        run("stats", "report", "--write")
        text = (wired / "docs" / "reports" / "sessions.md").read_text(encoding="utf-8")
        assert HOST_A not in text

    def test_the_written_report_passes_the_privacy_scanner(self, wired, capsys):
        run("stats", "refresh")
        run("stats", "report", "--write")
        assert run("privacy", "scan", "docs/reports/sessions.md") == 0


class TestShow:
    def test_one_session_by_name(self, wired, capsys):
        run("stats", "refresh")
        assert run("stats", "show", "stats-domain") == 0
        text = out(capsys)
        assert "stats-domain" in text
        assert "excludes subagent spend" in text

    def test_an_unknown_name_is_a_usage_error_not_a_crash(self, wired, capsys):
        run("stats", "refresh")
        assert run("stats", "show", "nothing-like-this") == 2
        assert "no stats record" in capsys.readouterr().err


class TestArchive:
    def test_archiving_promotes_a_record(self, wired, capsys):
        run("stats", "refresh")
        assert run("stats", "archive", "stats-domain", "--json") == 0
        assert payload(capsys)["archived"] is True


class TestCheck:
    def test_a_store_with_no_report_written_is_reported(self, wired, capsys):
        run("stats", "refresh")
        assert run("stats", "check") == 1
        assert "no rendered report" in out(capsys)

    def test_a_refreshed_and_written_store_is_clean(self, wired, capsys):
        run("stats", "refresh")
        run("stats", "report", "--write")
        assert run("stats", "check") == 0

    def test_check_reports_staleness_after_the_transcript_moves(
        self, wired, capsys, tmp_path
    ):
        run("stats", "refresh")
        run("stats", "report", "--write")
        transcript(
            tmp_path / "host",
            wired,
            HOST_A,
            [assistant(), assistant(), assistant()],
        )
        assert run("stats", "check") == 1
        assert "stale" in out(capsys)


class TestTheTransitionRecordIsWiredThrough:
    def test_a_pipeline_transition_records_a_watermark_from_the_host(
        self, wired, capsys
    ):
        run("pipeline", "start", "--task", "measure me")
        run(
            "pipeline",
            "advance",
            "red",
            "--force",
            "--reason",
            "gates are not the subject",
            "--json",
        )
        history = payload(capsys)["pipeline"]["history"]
        assert history[-1]["usage"]["input"] == 2000
        assert history[-1]["elapsed"] is not None


class TestTheReportStoreIsAWritableStore:
    def test_the_declared_report_store_is_exempt_from_worktree_isolation(
        self, wired, capsys
    ):
        """D4: the write scope is the state root and the stores the manifest
        declares — so regenerating the report is not an isolation violation."""
        from invariant.core.manifest import Manifest
        from invariant.core.rules import RuleContext, evaluate

        manifest = Manifest.load(wired)
        ctx = RuleContext(
            manifest=manifest,
            cwd=wired,
            session={"id": "s1", "state": "registered", "worktree": None},
            payload={
                "tool_name": "Write",
                "tool_input": {"file_path": str(wired / "docs/reports/sessions.md")},
            },
        )
        [verdict] = evaluate(["session.no_edit_in_main_checkout"], ctx)
        assert verdict.verdict.ok


class TestConfigShowsTheNewKey:
    def test_the_report_store_is_in_the_resolved_configuration(self, wired, capsys):
        assert run("config", "show", "--json") == 0
        assert payload(capsys)["config"]["paths"]["reports"] == "docs/reports"


class TestStatsCheckDoesNotPrintAHostSessionIdentifier:
    """Repro for BUG_016 F2 — see docs/backlog/BUG_016.md.

    `domains/stats.py` declines to name un-ingested sessions with the reasoning
    "Naming them would mean printing host session identifiers, and terminal
    output gets pasted into commits". Thirty lines above, in the same function,
    the unreadable-record branch interpolates a raw `host_session_id` into a
    message that reaches the same terminal.

    This is deliberately NOT filed as an ADR-002 D7 breach and is not asserted
    as one: D7 scopes to the COMMITTED report, which is clean and has its own
    scanner test above. What is asserted here is that one hygiene rule is
    applied to both branches of the one function that states it.
    """

    def record(self, project):
        return project / ".git" / "invariant" / "state" / "stats" / f"{HOST_A}.json"

    def test_the_unreadable_branch_is_reached_and_says_what_to_do(
        self, wired, capsys
    ):
        """Precondition: the corruption lands on the real stored record, the
        unreadable branch fires, and its advice reaches the terminal. Without
        this the assertion below could pass by printing nothing at all."""
        run("stats", "refresh")
        assert self.record(wired).is_file()
        self.record(wired).write_text("{]", encoding="utf-8")

        assert run("stats", "check") == 1
        text = out(capsys)
        assert "could not be read" in text
        assert "stats refresh --force" in text

    def test_the_un_ingested_branch_prints_a_count_and_no_identifier(
        self, wired, capsys
    ):
        """Falsifiability control, and the form the fix must produce: the other
        branch of the same function reports the same class of problem, gives
        the same single-command repair, and names nobody. It stays green
        throughout — this entry proposes extending it, never weakening it."""
        assert run("stats", "check") == 1
        text = out(capsys)
        assert "have not been ingested" in text
        assert HOST_A not in text

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG_016 F2 is live — delete this marker with the fix; the "
            "assertion below must then pass unchanged"
        ),
    )
    def test_the_unreadable_record_message_does_not_name_the_host_session(
        self, wired, capsys
    ):
        """The message printed for an unreadable record leads with the raw host
        session identifier, which is the one thing the branch below it refuses
        to print for exactly the reason that applies here too."""
        run("stats", "refresh")
        self.record(wired).write_text("{]", encoding="utf-8")
        run("stats", "check")

        assert HOST_A not in out(capsys), (
            "'invariant stats check' prints a raw host session identifier to "
            "the terminal, while the same function's un-ingested branch "
            "deliberately counts rather than names for the stated reason that "
            "terminal output gets pasted into commits"
        )
