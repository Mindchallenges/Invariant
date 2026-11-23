"""ADR-027 D3 and D12: what runs next is a pure decision.

``oracle/chain.decide`` takes one project's queue, its ready backlog items,
its chain settings, the live sessions and windows the scan saw, a pause and a
clock, and returns one verdict: start, wait, paused or idle, with every reason.
``invariant oracle next`` prints it and starts nothing. No test here opens a
window: the suite's launcher is a recorder (``tests/conftest.py``), and this
file asserts it was never called.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core.errors import ManifestError
from invariant.core.machines import load_item_machine
from invariant.core.manifest import Manifest
from invariant.core.queue import Queue
from invariant.oracle import chain
from invariant.oracle.chain import ChainSettings, Live, Ready
from invariant.oracle.scan import ScanCache

NOON = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def _queue(*refs: str) -> Queue:
    q = Queue()
    for ref in refs:
        q.add(ref, added_by="user", brief=f"brief of {ref}")
    return q


def _decide(queue=None, ready=(), settings=None, live=(), windows=0, now=NOON, paused=None):
    return chain.decide(
        queue=queue or Queue(),
        ready=list(ready),
        settings=settings or ChainSettings(),
        live=list(live),
        windows=windows,
        now=now,
        paused=paused,
    )


# -- the order ----------------------------------------------------------------


class TestTheOrder:
    def test_nothing_anywhere_is_idle(self):
        verdict = _decide()
        assert verdict.verdict == "idle"
        assert verdict.candidate is None
        assert any("nothing" in r for r in verdict.reasons)

    def test_the_queue_head_starts_auto_by_default(self):
        verdict = _decide(queue=_queue("IDEA_1", "IDEA_2"))
        assert (verdict.verdict, verdict.mode) == ("start", "auto")
        assert verdict.candidate.source == "queue"
        assert verdict.candidate.entry == "Q1"
        assert verdict.candidate.ref == "IDEA_1"
        assert verdict.candidate.brief == "brief of IDEA_1"

    def test_a_started_entry_is_not_the_head(self):
        q = _queue("a", "b")
        q.entries[0].state = "started"
        assert _decide(queue=q).candidate.ref == "b"

    def test_an_empty_queue_falls_back_to_the_backlog_as_a_proposal(self):
        # Increment 5 (ruling 19): the shipped default is auto; propose is a choice.
        verdict = _decide(ready=[Ready("IDEA_3", "Three", "proposed", tier=2)],
                          settings=ChainSettings(backlog="propose"))
        assert (verdict.verdict, verdict.mode) == ("start", "propose")
        assert verdict.candidate.source == "backlog"
        assert verdict.candidate.ref == "IDEA_3"
        assert verdict.candidate.title == "Three"
        assert verdict.candidate.brief is None

    def test_an_item_on_the_queue_is_skipped_in_the_fallback(self):
        q = _queue("IDEA_1")
        q.entries[0].state = "started"
        verdict = _decide(
            queue=q, ready=[Ready("IDEA_1", "One", "accepted"), Ready("IDEA_2", "Two", "proposed")]
        )
        assert verdict.candidate.ref == "IDEA_2"

    def test_a_done_entry_holds_its_item_back_too(self):
        # Increment 2 (DEC_ADR_027_D18): an item any entry names is skipped,
        # so a finished item still in a ready state does not start again.
        q = _queue("IDEA_1")
        q.entries[0].state = "done"
        verdict = _decide(queue=q, ready=[Ready("IDEA_1", "One", "accepted")])
        assert verdict.verdict == "idle"


class TestTheRank:
    def test_tier_then_priority_then_natural_id(self):
        items = [
            Ready("IDEA_10", "", "proposed", tier=1, priority="low"),
            Ready("IDEA_9", "", "proposed", tier=1, priority="low"),
            Ready("BUG_2", "", "reproduced", tier=1, priority="high"),
            Ready("IDEA_1", "", "proposed", tier=None, priority="high"),
            Ready("IDEA_5", "", "proposed", tier=2, priority="medium"),
            Ready("IDEA_6", "", "proposed", tier=1, priority="urgent"),
            Ready("IDEA_7", "", "proposed", tier="x", priority="high"),
        ]
        assert [i.id for i in chain.rank(items)] == [
            "BUG_2", "IDEA_9", "IDEA_10", "IDEA_6", "IDEA_5", "IDEA_1", "IDEA_7",
        ]

    def test_a_numeric_string_tier_is_a_number(self):
        items = [Ready("A_1", "", "s", tier="3"), Ready("A_2", "", "s", tier="2")]
        assert [i.id for i in chain.rank(items)] == ["A_2", "A_1"]


# -- the settings -------------------------------------------------------------


class TestTheSettings:
    def test_queue_off_is_idle_and_does_not_jump_to_the_backlog(self):
        verdict = _decide(
            queue=_queue("a"), ready=[Ready("IDEA_1", "", "proposed")],
            settings=ChainSettings(queue="off"),
        )
        assert verdict.verdict == "idle"
        assert verdict.candidate.ref == "a"
        assert any("oracle.chain.queue is off" in r for r in verdict.reasons)

    def test_backlog_off_is_idle(self):
        verdict = _decide(
            ready=[Ready("IDEA_1", "", "proposed")], settings=ChainSettings(backlog="off")
        )
        assert verdict.verdict == "idle"
        assert any("oracle.chain.backlog is off" in r for r in verdict.reasons)

    def test_backlog_auto_starts_auto(self):
        verdict = _decide(
            ready=[Ready("IDEA_1", "", "proposed")], settings=ChainSettings(backlog="auto")
        )
        assert verdict.mode == "auto"

    def test_queue_propose_proposes(self):
        verdict = _decide(queue=_queue("a"), settings=ChainSettings(queue="propose"))
        assert (verdict.verdict, verdict.mode) == ("start", "propose")

    def test_from_the_manifest(self, tmp_path):
        m = Manifest.from_raw(
            {"oracle": {"chain": {"queue": "propose", "backlog": "off", "hours": [9, 18]}}},
            root=tmp_path, path=tmp_path / "m.yaml", env={},
        )
        assert ChainSettings.from_manifest(m) == ChainSettings("propose", "off", (9, 18))

    def test_the_defaults(self, tmp_path):
        m = Manifest.from_raw({}, root=tmp_path, path=tmp_path / "m.yaml", env={})
        # Increment 5 (ruling 19): backlog work starts on its own by default.
        assert ChainSettings.from_manifest(m) == ChainSettings("auto", "auto", None)

    @pytest.mark.parametrize(
        ("oracle", "needle"),
        [
            ({"chain": {"queue": "sometimes"}}, "oracle.chain.queue"),
            ({"chain": {"backlog": True}}, "oracle.chain.backlog"),
            ({"chain": {"daily_cap": 0}}, "oracle.chain.daily_cap"),
            ({"chain": {"stall_after": 0}}, "oracle.chain.stall_after"),
            ({"chain": {"nudge": 1}}, "oracle.chain.nudge"),
            ({"elsewhere": 1}, "oracle.elsewhere"),
            ({"chain": []}, "oracle.chain"),
            ([], "oracle must be a mapping"),
            ({"chain": {"hours": [9]}}, "oracle.chain.hours"),
            ({"chain": {"hours": [9, 25]}}, "oracle.chain.hours"),
            ({"chain": {"hours": [9, 9]}}, "oracle.chain.hours"),
            ({"chain": {"hours": ["9", 18]}}, "oracle.chain.hours"),
            ({"chain": {"hours": [True, 18]}}, "oracle.chain.hours"),
            ({"chain": {"hours": "9-18"}}, "oracle.chain.hours"),
        ],
    )
    def test_a_bad_setting_is_refused_at_load(self, tmp_path, oracle, needle):
        with pytest.raises(ManifestError, match=needle):
            Manifest.from_raw(
                {"oracle": oracle}, root=tmp_path, path=tmp_path / "m.yaml", env={}
            )


# -- the limits -----------------------------------------------------------------


class TestOneAtATime:
    def test_a_live_session_holds_the_start(self):
        verdict = _decide(
            queue=_queue("a"), live=[Live("s1", "work", "working", None)]
        )
        assert verdict.verdict == "wait"
        assert verdict.candidate.ref == "a"
        assert any("work" in r and "one at a time" in r for r in verdict.reasons)

    def test_a_reported_done_session_waits_for_its_teardown(self):
        verdict = _decide(
            queue=_queue("a"),
            live=[Live("s1", "work", "reviewed", {"outcome": "done", "note": None})],
        )
        assert verdict.verdict == "wait"
        assert any("teardown" in r for r in verdict.reasons)

    def test_an_open_window_holds_the_start(self):
        verdict = _decide(queue=_queue("a"), windows=2)
        assert verdict.verdict == "wait"
        assert any("2 Claude windows" in r for r in verdict.reasons)

    def test_one_open_window_is_named_singular(self):
        verdict = _decide(queue=_queue("a"), windows=1)
        assert any("1 Claude window " in r for r in verdict.reasons)

    def test_nothing_to_start_is_idle_even_with_a_live_session(self):
        verdict = _decide(live=[Live("s1", "work", "working", None)])
        assert verdict.verdict == "idle"


class TestPaused:
    @pytest.mark.parametrize("outcome", ["blocked", "needs-user"])
    def test_a_live_session_reporting_a_stop_pauses(self, outcome):
        verdict = _decide(
            queue=_queue("a"),
            live=[Live("s1", "work", "working", {"outcome": outcome, "note": "why"})],
        )
        assert verdict.verdict == "paused"
        assert any(outcome in r and "why" in r for r in verdict.reasons)

    def test_a_ledger_pause_wins(self):
        verdict = _decide(queue=_queue("a"), paused="a launch failed")
        assert verdict.verdict == "paused"
        assert verdict.reasons[0] == "the chain is paused: a launch failed"


class TestHours:
    @pytest.mark.parametrize(
        ("hours", "hour", "inside"),
        [
            (None, 3, True),
            ((9, 18), 9, True),
            ((9, 18), 17, True),
            ((9, 18), 18, False),
            ((9, 18), 8, False),
            ((22, 6), 23, True),
            ((22, 6), 2, True),
            ((22, 6), 12, False),
            ((0, 24), 23, True),
        ],
    )
    def test_the_window(self, hours, hour, inside):
        assert chain.in_hours(hours, datetime(2026, 11, 18, hour, 30, tzinfo=timezone.utc)) is inside

    def test_outside_the_hours_an_auto_start_is_a_proposal(self):
        verdict = _decide(
            queue=_queue("a"), settings=ChainSettings(hours=(9, 18)),
            now=datetime(2026, 11, 18, 20, 0, tzinfo=timezone.utc),
        )
        assert (verdict.verdict, verdict.mode) == ("start", "propose")
        assert any("hours" in r for r in verdict.reasons)


class TestTheVerdictDocument:
    def test_to_dict(self):
        doc = _decide(queue=_queue("a")).to_dict()
        assert doc["verdict"] == "start"
        assert doc["mode"] == "auto"
        assert doc["candidate"] == {
            "source": "queue", "ref": "a", "entry": "Q1", "brief": "brief of a", "title": None,
            "triage_of": None, "retry_of": None, "reproduce": False, "residue": None,
            "blind": 0,
        }
        assert (doc["limit"], doc["proposals"]) == (None, [])
        assert doc["reasons"]
        assert _decide().to_dict()["candidate"] is None


# -- the facts it reads ---------------------------------------------------------


class TestTheMachinesMarkReady:
    def test_ideas_and_bugs(self):
        assert load_item_machine("idea").states_with_role("ready") == ("proposed", "accepted")
        assert load_item_machine("bug").states_with_role("ready") == ("reproduced",)


class TestLiveFromTheDocument:
    def test_session_rows_only(self):
        project = {"rows": [
            {"kind": "session", "session_id": "s1", "name": "n", "state": "working",
             "report": {"outcome": "done"}},
            {"kind": "host", "hosts": []},
            {"kind": "session", "session_id": "s2", "name": "m", "state": "isolated"},
        ]}
        assert chain.live_sessions(project) == [
            Live("s1", "n", "working", {"outcome": "done"}),
            Live("s2", "m", "isolated", None),
        ]


# -- the command ------------------------------------------------------------------


def _git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _scan_without_a_probe(monkeypatch):
    monkeypatch.setattr(cli_main, "_oracle_cache", lambda: ScanCache())


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "master")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "T")
    monkeypatch.chdir(root)
    monkeypatch.delenv("INVARIANT_HOST_SESSION_ID", raising=False)
    assert main(["init", "--home", "repo", "--name", "demo"]) == 0
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "manifest")
    return root


def _json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


@pytest.fixture(autouse=True)
def _fresh_usage(monkeypatch):
    """A plan's usage read just now and well under the cap: increment 2b's
    weekly hold is ``test_adr_027_inc2b``'s, not these tests'."""
    import time

    from invariant.oracle.usage import PlanUsage

    monkeypatch.setattr(cli_main, "_oracle_plan_usage", lambda index: lambda: PlanUsage(
        weekly=10.0, weekly_resets_at=None, five_hour=1.0, five_hour_resets_at=None,
        read_at=time.time()))


class TestTheCommand:
    def test_an_empty_project_is_idle(self, project, capsys, _no_real_terminal_launch):
        assert main(["oracle", "next", "demo", "--json"]) == 0
        out = _json(capsys)
        assert out["verdict"] == "idle"
        assert out["project"]["name"] == "demo"
        usage = out["facts"].pop("usage")
        assert (usage["weekly"], usage["hold"], usage["weekly_usage_max"]) == (10.0, None, 95)
        assert out["facts"] == {"live_sessions": [], "open_windows": 0, "queued": 0,
                                "ready": 0, "paused": None, "started_today": 0,
                                "daily_cap": 20, "machine_live": 0, "machine_cap": 10,
                                "released": []}
        assert _no_real_terminal_launch.plans == []

    def test_the_queue_head_would_start(self, project, capsys, _no_real_terminal_launch):
        assert main(["queue", "add", "ADR-027 inc 2", "--brief", "build it"]) == 0
        capsys.readouterr()
        assert main(["oracle", "next", "demo"]) == 0
        text = capsys.readouterr().out
        assert "start" in text and "ADR-027 inc 2" in text and "auto" in text
        assert "nothing was started" in text
        assert _no_real_terminal_launch.plans == []

    def test_a_ready_item_is_proposed(self, project, capsys):
        # Under backlog: propose with the mirror off, the fallback of
        # increment 1 (increment 5 turned both defaults on).
        manifest = project / ".claude" / "invariant.yaml"
        manifest.write_text(manifest.read_text(encoding="utf-8")
                            + "\noracle:\n  chain:\n    backlog: propose\n    mirror: false\n",
                            encoding="utf-8")
        assert main(["item", "new", "bug", "--title", "Broken"]) == 0
        assert main(["item", "new", "idea", "--title", "Wanted"]) == 0
        capsys.readouterr()
        assert main(["oracle", "next", "demo", "--json"]) == 0
        out = _json(capsys)
        assert (out["verdict"], out["mode"]) == ("start", "propose")
        assert out["candidate"]["source"] == "backlog"
        assert out["candidate"]["ref"].startswith("IDEA_")
        assert out["facts"]["ready"] == 1

    def test_a_live_session_makes_it_wait(self, project, capsys):
        assert main(["queue", "add", "next"]) == 0
        assert main(["session", "start", "work"]) == 0
        capsys.readouterr()
        assert main(["oracle", "next", "demo", "--json"]) == 0
        out = _json(capsys)
        assert out["verdict"] == "wait"
        assert out["facts"]["live_sessions"][0]["name"].startswith("work")

    def test_a_live_session_reporting_blocked_pauses(self, project, capsys):
        assert main(["queue", "add", "next"]) == 0
        assert main(["session", "start", "work"]) == 0
        assert main(["session", "report", "--outcome", "blocked", "--note", "stuck"]) == 0
        capsys.readouterr()
        assert main(["oracle", "next", "demo"]) == 0
        text = capsys.readouterr().out
        assert "paused" in text and "stuck" in text

    def test_an_unknown_project_is_refused(self, project, capsys):
        assert main(["oracle", "next", "nope"]) == 2
        assert "nope" in capsys.readouterr().err

    def test_a_project_that_lost_its_manifest_is_refused(self, project, capsys):
        (project / ".claude" / "invariant.yaml").unlink()
        assert main(["oracle", "next", "demo"]) == 2
        assert "manifest" in capsys.readouterr().err
