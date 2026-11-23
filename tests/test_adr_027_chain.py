"""ADR-027 increment 2: the watcher's chain step, its ledger, and ruling 9's triage.

The step follows the chained start it made in each project, decides what comes
next and acts on it through the hands the CLI injects (D9): it never writes the
queue itself and never imports a host. A failed start is triaged, not paused
(ruling 9); only a stop report, a failed triage or nothing left to start stops
the chain. No test here opens a window: the step's hands are recorders, and the
CLI's tests run behind the suite's inert launcher and successor watch.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core import queue as queue_record
from invariant.core.errors import InvariantError, ManifestError, StateError, UsageError
from invariant.core.home import load_user_config
from invariant.core.manifest import Manifest
from invariant.core.queue import Queue
from invariant.core.store import JsonStore
from invariant.domains.queue import QueueDomain
from invariant.oracle import chain, chainstep
from invariant.oracle.chain import Candidate, ChainSettings, Live, Ready
from invariant.oracle.chainstep import ChainHands, ChainStep
from invariant.oracle.notify import RecordingNotifier
from invariant.oracle.scan import ScanCache

#: A fixed local noon, as epoch seconds.
NOON = time.mktime((2026, 11, 18, 12, 0, 0, 0, 0, -1))


# -- the queue entry: mode, triage and retry ---------------------------------------


class TestTheEntry:
    def test_new_keys_are_left_out_while_unset(self):
        q = Queue()
        entry = q.add("IDEA_1", added_by="user")
        assert set(entry.to_dict()) == {"id", "ref", "added_by", "state"}

    def test_mode_triage_and_retry_round_trip(self):
        q = Queue()
        q.add("BUG_9", added_by="s1", mode="propose")
        q.add("triage Q1", added_by="oracle", triage_of="Q1")
        q.add("IDEA_1", added_by="oracle", retry_of="Q1")
        again = queue_record.parse(json.loads(json.dumps(q.to_dict())))
        assert [(e.mode, e.triage_of, e.retry_of) for e in again.entries] == [
            ("propose", None, None), (None, "Q1", None), (None, None, "Q1")]

    def test_an_unknown_mode_is_refused(self):
        with pytest.raises(UsageError, match="mode"):
            Queue().add("x", added_by="user", mode="later")
        with pytest.raises(StateError, match="mode"):
            queue_record.parse({"entries": [{"id": "Q1", "ref": "x", "mode": "later"}]})

    def test_mark_sets_state_session_and_reason(self):
        q = Queue()
        q.add("a", added_by="user")
        q.mark("Q1", "started", session="s1")
        assert (q.entries[0].state, q.entries[0].session) == ("started", "s1")
        q.mark("Q1", "failed", reason="the launch failed")
        assert q.entries[0].reason == "the launch failed"
        q.mark("Q1", "done")
        assert q.entries[0].reason is None
        with pytest.raises(UsageError):
            q.mark("Q1", "lost")

    def test_a_started_entry_can_be_minted(self):
        q = Queue()
        entry = q.add("IDEA_3", added_by="oracle", state="started")
        assert entry.state == "started"
        with pytest.raises(UsageError):
            q.add("IDEA_4", added_by="oracle", state="lost")


def _git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _scan_without_a_probe(monkeypatch):
    monkeypatch.setattr(cli_main, "_oracle_cache", lambda: ScanCache())


@pytest.fixture(autouse=True)
def _fresh_usage(monkeypatch):
    """A plan's usage read just now and well under the cap: increment 2b's
    weekly hold is ``test_adr_027_inc2b``'s, not these tests'."""
    from invariant.oracle.usage import PlanUsage

    monkeypatch.setattr(cli_main, "_oracle_plan_usage", lambda index: lambda: PlanUsage(
        weekly=10.0, weekly_resets_at=None, five_hour=1.0, five_hour_resets_at=None,
        read_at=time.time()))


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "master")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "T")
    monkeypatch.chdir(root)
    monkeypatch.delenv("INVARIANT_HOST_SESSION_ID", raising=False)
    monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
    assert main(["init", "--home", "repo", "--name", "demo"]) == 0
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "manifest")
    return root


def _domain(root: Path) -> QueueDomain:
    manifest = Manifest.locate(root)
    return QueueDomain(manifest, JsonStore(manifest.state_root))


class TestTheDomain:
    def test_add_takes_a_mode(self, project):
        domain = _domain(project)
        assert domain.add("fix it", mode="propose").mode == "propose"
        with pytest.raises(UsageError):
            domain.add("other", mode="soon")

    def test_begin_marks_a_queue_entry_started(self, project):
        domain = _domain(project)
        domain.add("work")
        assert domain.begin("Q1", "work") == "Q1"
        assert domain.list().entries[0].state == "started"

    def test_begin_records_a_backlog_start_as_an_entry(self, project):
        domain = _domain(project)
        entry_id = domain.begin(None, "IDEA_7")
        entry = domain.list().find(entry_id)
        assert (entry.ref, entry.state, entry.added_by) == ("IDEA_7", "started", "oracle")

    def test_mark(self, project):
        domain = _domain(project)
        domain.add("work")
        domain.mark("Q1", "started", session="s1")
        domain.mark("Q1", "done")
        assert domain.list().entries[0].state == "done"

    def test_fail_queues_triage_at_the_head_and_one_retry_behind_it(self, project):
        domain = _domain(project)
        domain.add("first", brief="do the first")
        domain.add("second")
        domain.begin("Q1", "first")
        out = domain.fail("Q1", "the launch failed", "triage {entry} {ref} {reason} {retry}",
                          retry=True)
        entries = domain.list().entries
        assert [e.id for e in entries] == ["Q4", "Q3", "Q1", "Q2"]
        triage, retry = entries[0], entries[1]
        assert (out["triage"], out["retry"]) == ("Q4", "Q3")
        assert triage.ref == "triage Q1" and triage.triage_of == "Q1"
        assert triage.brief == "triage Q1 first the launch failed Q3"
        assert (retry.ref, retry.retry_of, retry.brief) == ("first", "Q1", "do the first")
        assert (entries[2].state, entries[2].reason) == ("failed", "the launch failed")

    def test_a_retry_that_fails_is_triaged_but_not_retried(self, project):
        domain = _domain(project)
        domain.add("first")
        domain.fail("Q1", "gone", "t {retry}", retry=True)
        domain.begin("Q2", "first")
        out = domain.fail("Q2", "gone again", "t {retry}", retry=False)
        assert out["retry"] is None
        triage = domain.list().find(out["triage"])
        assert triage.brief == "t none"
        assert triage.ref == "triage Q2"

    def test_fail_without_a_template_only_marks(self, project):
        domain = _domain(project)
        domain.add("t")
        out = domain.fail("Q1", "the triage failed", None, retry=False)
        assert out == {"failed": "Q1", "triage": None, "retry": None}
        assert [e.id for e in domain.list().entries] == ["Q1"]

    def test_a_triage_brief_with_a_privacy_finding_writes_nothing(self, project):
        domain = _domain(project)
        domain.add("work")
        before = domain.list().to_dict()
        with pytest.raises(InvariantError, match="not queued"):
            domain.fail("Q1", "x", "mail someone@example.com about {ref}", retry=True)
        assert domain.list().to_dict() == before


# -- the settings ------------------------------------------------------------------


class TestTheSettings:
    def test_the_new_defaults(self, tmp_path):
        m = Manifest.from_raw({}, root=tmp_path, path=tmp_path / "m.yaml", env={})
        settings = ChainSettings.from_manifest(m)
        assert settings.daily_cap == 20
        assert "{ref}" in settings.prompt
        assert "{retry}" in settings.triage_prompt
        assert "backlog-curator" in settings.triage_prompt
        assert "bug-reproducer" in settings.triage_prompt
        assert "needs-user" in settings.triage_prompt

    def test_set(self, tmp_path):
        m = Manifest.from_raw(
            {"oracle": {"chain": {"daily_cap": 3, "prompt": "go {ref}",
                                  "triage_prompt": "look at {entry}"}}},
            root=tmp_path, path=tmp_path / "m.yaml", env={},
        )
        settings = ChainSettings.from_manifest(m)
        assert (settings.daily_cap, settings.prompt, settings.triage_prompt) == (
            3, "go {ref}", "look at {entry}")

    @pytest.mark.parametrize(
        ("chain_keys", "needle"),
        [
            ({"daily_cap": 0}, "oracle.chain.daily_cap"),
            ({"daily_cap": True}, "oracle.chain.daily_cap"),
            ({"daily_cap": "5"}, "oracle.chain.daily_cap"),
            ({"prompt": ""}, "oracle.chain.prompt"),
            ({"prompt": "two\nlines"}, "oracle.chain.prompt"),
            ({"prompt": "go {reff}"}, "oracle.chain.prompt"),
            ({"prompt": "go {ref!r}"}, "oracle.chain.prompt"),
            ({"triage_prompt": "{title}"}, "oracle.chain.triage_prompt"),
            ({"triage_prompt": 3}, "oracle.chain.triage_prompt"),
            ({"stall_after": "2h"}, "oracle.chain.stall_after"),
        ],
    )
    def test_refused_at_load(self, tmp_path, chain_keys, needle):
        with pytest.raises(ManifestError, match=needle):
            Manifest.from_raw({"oracle": {"chain": chain_keys}}, root=tmp_path,
                              path=tmp_path / "m.yaml", env={})

    def test_the_machine_cap_lives_in_the_user_config(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        assert load_user_config({"INVARIANT_HOME": str(home)}).chain == {}
        (home / "config.yaml").write_text("chain:\n  machine_cap: 3\n")
        assert load_user_config({"INVARIANT_HOME": str(home)}).chain == {"machine_cap": 3}
        assert chainstep.machine_cap(load_user_config({"INVARIANT_HOME": str(home)})) == 3

    def test_the_machine_cap_defaults_to_ten(self, tmp_path):
        assert chainstep.machine_cap(load_user_config({"INVARIANT_HOME": str(tmp_path)})) == 10

    @pytest.mark.parametrize(
        "body", ["chain: 3\n", "chain:\n  machine_cap: 0\n", "chain:\n  machine_cap: true\n",
                 "chain:\n  other: 1\n"],
    )
    def test_a_bad_machine_cap_is_refused(self, tmp_path, body):
        (tmp_path / "config.yaml").write_text(body)
        with pytest.raises(ManifestError, match="chain"):
            load_user_config({"INVARIANT_HOME": str(tmp_path)})


# -- the decision, extended --------------------------------------------------------


NOON_DT = datetime.fromtimestamp(NOON, timezone.utc).astimezone()


def _queue(*refs: str) -> Queue:
    q = Queue()
    for ref in refs:
        q.add(ref, added_by="user")
    return q


def _decide(queue=None, ready=(), settings=None, live=(), windows=0, **kw):
    return chain.decide(queue=queue or Queue(), ready=list(ready),
                        settings=settings or ChainSettings(), live=list(live),
                        windows=windows, now=NOON_DT, **kw)


class TestTheDecision:
    def test_the_daily_cap_holds_an_auto_start(self):
        verdict = _decide(queue=_queue("a"), started_today=20)
        assert (verdict.verdict, verdict.limit) == ("wait", "daily_cap")
        assert any("daily cap" in r for r in verdict.reasons)

    def test_the_machine_cap_holds_an_auto_start(self):
        verdict = _decide(queue=_queue("a"), machine_live=2, machine_cap=2)
        assert (verdict.verdict, verdict.limit) == ("wait", "machine_cap")

    def test_a_cap_does_not_hold_a_proposal(self):
        verdict = _decide(ready=[Ready("IDEA_1", "One", "proposed")], started_today=99,
                          settings=ChainSettings(backlog="propose"))
        assert (verdict.verdict, verdict.mode, verdict.limit) == ("start", "propose", None)

    def test_a_propose_entry_is_proposed_and_the_next_one_runs(self):
        q = Queue()
        q.add("BUG_9", added_by="s1", mode="propose")
        q.add("IDEA_2", added_by="user")
        verdict = _decide(queue=q)
        assert (verdict.verdict, verdict.mode) == ("start", "auto")
        assert verdict.candidate.ref == "IDEA_2"
        assert [p.ref for p in verdict.proposals] == ["BUG_9"]
        assert verdict.to_dict()["proposals"][0]["entry"] == "Q1"

    def test_only_propose_entries_left_is_idle_with_proposals(self):
        q = Queue()
        q.add("BUG_9", added_by="s1", mode="propose")
        verdict = _decide(queue=q)
        assert verdict.verdict == "idle"
        assert [p.entry for p in verdict.proposals] == ["Q1"]

    def test_a_report_before_the_resume_no_longer_pauses(self):
        live = [Live("s1", "w", "working", {"outcome": "blocked", "at": 100.0})]
        assert _decide(queue=_queue("a"), live=live).verdict == "paused"
        assert _decide(queue=_queue("a"), live=live, resumed=100.0).verdict == "wait"
        assert _decide(queue=_queue("a"), live=live, resumed=99.0).verdict == "paused"

    def test_the_backlog_skips_an_item_any_entry_names(self):
        q = _queue("IDEA_1")
        q.entries[0].state = "done"
        verdict = _decide(queue=q, ready=[Ready("IDEA_1", "One", "accepted"),
                                          Ready("IDEA_2", "Two", "accepted")])
        assert verdict.candidate.ref == "IDEA_2"

    def test_a_candidate_carries_the_entry_lineage(self):
        q = Queue()
        q.add("triage Q1", added_by="oracle", triage_of="Q1")
        q.add("x", added_by="oracle", retry_of="Q1")
        assert _decide(queue=q).candidate.triage_of == "Q1"
        q.entries[0].state = "done"
        assert _decide(queue=q).candidate.retry_of == "Q1"


class TestThePrompt:
    def test_a_brief_wins(self):
        c = Candidate("queue", "IDEA_1", entry="Q1", brief="do it")
        assert chain.render_prompt(ChainSettings(), c) == "do it"

    def test_the_template_otherwise(self):
        c = Candidate("backlog", "IDEA_1", entry="Q4", title="One")
        settings = ChainSettings(prompt="take {ref} ({entry}): {title}")
        assert chain.render_prompt(settings, c) == "take IDEA_1 (Q4): One"

    def test_no_title_reads_as_the_ref_and_no_entry_as_new(self):
        c = Candidate("backlog", "IDEA_1")
        assert chain.render_prompt(ChainSettings(prompt="{title}|{entry}"), c) == "IDEA_1|new"

    def test_the_label(self):
        assert chain.label_for(Candidate("queue", "ADR-027 inc 2", entry="Q3")) == "ADR-027 inc 2"
        assert chain.label_for(Candidate("queue", "---", entry="Q3")) == "Q3"
        assert len(chain.label_for(Candidate("queue", "x" * 90, entry="Q3"))) == 40


class TestTheFailurePolicy:
    def test_a_first_failure_is_triaged_and_retried(self):
        assert chain.on_failure(None, None) == ("triage", True)

    def test_a_retry_that_fails_is_triaged_not_retried(self):
        assert chain.on_failure(None, "Q1") == ("triage", False)

    def test_a_failed_triage_pauses(self):
        assert chain.on_failure("Q1", None) == ("pause", False)


# -- the step ----------------------------------------------------------------------


class Hands:
    """Recording hands: what the CLI's recorder and launcher would have been asked."""

    def __init__(self, root: Path | None = None) -> None:
        self.calls: list[tuple] = []
        self.fail_launch: Exception | None = None
        self.pid = 4242
        self.next_entry = 50

    def start(self, key, label, prompt):
        self.calls.append(("start", key, label, prompt))
        if self.fail_launch is not None:
            raise self.fail_launch
        return {"name": f"demo-{label}", "pid": self.pid}

    def begin(self, key, entry, ref):
        self.calls.append(("begin", key, entry, ref))
        if entry is None:
            self.next_entry += 1
            return f"Q{self.next_entry}"
        return entry

    def mark(self, key, entry, state, session=None, reason=None):
        self.calls.append(("mark", key, entry, state, session, reason))

    def fail(self, key, entry, reason, template, retry):
        self.calls.append(("fail", key, entry, reason, template is not None, retry))
        return {"failed": entry, "triage": "Q90" if template else None,
                "retry": "Q91" if retry else None}

    def kinds(self) -> list[str]:
        return [c[0] for c in self.calls]

    def as_hands(self) -> ChainHands:
        return ChainHands(start=self.start, begin=self.begin, mark=self.mark, fail=self.fail)


KEY = "/state/demo"


def _doc(rows=(), key=KEY, name="demo", root="/work/demo", **extra) -> dict:
    project = {"project": {"key": key, "name": name, "root": root}, "rows": list(rows)}
    project.update(extra)
    return {"projects": [project]}


def _session_row(sid="s1", hosts=(), report=None, name="work", state="working"):
    row = {"kind": "session", "session_id": sid, "name": name, "state": state,
           "hosts": list(hosts)}
    if report is not None:
        row["report"] = report
    return row


def _host(name=None, pid=None, alive=True, hid="h1"):
    return {"host_session_id": hid, "launched_name": name, "pid": pid, "alive": alive}


class Inputs:
    def __init__(self, queue=None, ready=(), settings=None):
        self.queue = queue if queue is not None else Queue()
        self.ready = list(ready)
        self.settings = settings or ChainSettings()


@pytest.fixture
def home(tmp_path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


def _step(home, hands, inputs=None, notifier=None, ends=None, machine_cap=10, alive=None):
    holder = {"inputs": inputs or Inputs()}
    return ChainStep(
        home,
        hands.as_hands(),
        notifier if notifier is not None else RecordingNotifier(),
        machine_cap=machine_cap,
        reader=lambda root: holder["inputs"],
        session_end=lambda root, sid: (ends or {}).get(sid, "live"),
        pid_alive=alive or (lambda pid: True),
        log=lambda message: None,
    ), holder


def _ledger(home) -> dict:
    return json.loads((home / "oracle" / "chain.json").read_text())


def _state(home, key=KEY) -> dict:
    return _ledger(home)["projects"][key]


class TestAutoStart:
    def test_the_queue_head_starts_and_is_recorded(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        q = _queue("IDEA_1")
        step, _ = _step(home, hands, Inputs(q), notifier)
        actions = step.step(_doc(), NOON)
        assert [a["action"] for a in actions] == ["started"]
        assert hands.kinds() == ["begin", "start"]
        assert hands.calls[0] == ("begin", KEY, "Q1", "IDEA_1")
        assert hands.calls[1][2] == "IDEA_1"
        assert "IDEA_1" in hands.calls[1][3]
        current = _state(home)["current"]
        assert (current["state"], current["name"], current["pid"], current["entry"]) == (
            "running", "demo-IDEA_1", 4242, "Q1")
        assert _state(home)["starts"][0]["day"] == "2026-11-18"
        assert notifier.sent and "started" in notifier.sent[0].message

    def test_a_backlog_auto_start_mints_an_entry(self, home):
        hands = Hands()
        inputs = Inputs(ready=[Ready("IDEA_5", "Five", "proposed")],
                        settings=ChainSettings(backlog="auto"))
        step, _ = _step(home, hands, inputs)
        step.step(_doc(), NOON)
        assert hands.calls[0] == ("begin", KEY, None, "IDEA_5")
        assert _state(home)["current"]["entry"] == "Q51"

    def test_nothing_reads_while_something_is_live(self, home):
        hands = Hands()
        step, holder = _step(home, hands)
        holder["inputs"] = None  # a read would fail
        assert step.step(_doc([_session_row()]), NOON) == []
        assert hands.calls == []

    def test_an_open_window_holds_it(self, home):
        hands = Hands()
        step, _ = _step(home, hands, Inputs(_queue("a")))
        doc = _doc([{"kind": "host", "hosts": [_host(alive=True)]}])
        assert step.step(doc, NOON) == []
        assert hands.calls == []

    def test_an_unreadable_project_is_left_alone(self, home):
        hands = Hands()
        step, _ = _step(home, hands, Inputs(_queue("a")))
        assert step.step(_doc(unread={"error": "x"}), NOON) == []
        assert hands.calls == []

    def test_a_read_that_fails_is_logged_and_skipped(self, home):
        hands = Hands()
        logged = []
        step = ChainStep(home, hands.as_hands(), None, reader=_raise_state,
                         session_end=lambda r, s: "live", log=logged.append)
        assert step.step(_doc(), NOON) == []
        assert logged and "cannot read" in logged[0]

    def test_a_begin_that_fails_starts_nothing(self, home):
        hands = Hands()
        hands.begin = _raise_usage
        step, _ = _step(home, hands, Inputs(_queue("a")))
        assert step.step(_doc(), NOON) == []
        assert _state(home)["current"] is None
        assert "start" not in hands.kinds()


def _raise_state(root):
    raise StateError("cannot read the queue")


def _raise_usage(*args):
    raise UsageError("the queue has no entry 'Q1'")


class TestProposals:
    def test_a_backlog_proposal_notifies_once(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        inputs = Inputs(ready=[Ready("IDEA_5", "Five", "proposed")],
                        settings=ChainSettings(backlog="propose"))
        step, _ = _step(home, hands, inputs, notifier)
        first = step.step(_doc(), NOON)
        second = step.step(_doc(), NOON + 5)
        assert [a["action"] for a in first] == ["proposed"]
        assert second == []
        assert len(notifier.sent) == 1
        assert "oracle chain start demo" in notifier.sent[0].message
        assert hands.calls == []

    def test_a_propose_entry_notifies_while_the_next_starts(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        q = Queue()
        q.add("BUG_9", added_by="s1", mode="propose")
        q.add("IDEA_2", added_by="user")
        step, _ = _step(home, hands, Inputs(q), notifier)
        actions = step.step(_doc(), NOON)
        assert sorted(a["action"] for a in actions) == ["proposed", "started"]
        assert hands.calls[0][2] == "Q2"

    def test_outside_the_hours_an_auto_start_is_proposed(self, home):
        hands = Hands()
        inputs = Inputs(_queue("a"), settings=ChainSettings(hours=(1, 2)))
        step, _ = _step(home, hands, inputs)
        assert [a["action"] for a in step.step(_doc(), NOON)] == ["proposed"]
        assert hands.calls == []


class TestTheCaps:
    def test_the_daily_cap_holds_and_says_so_once(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        step, _ = _step(home, hands, Inputs(_queue("a", "b")), notifier)
        store = JsonStore(home)
        store.ensure_root = lambda: None
        ledger = {"version": 1, "projects": {KEY: {
            "starts": [{"id": f"C{i}", "day": "2026-11-18", "at": "x"} for i in range(20)]}}}
        (home / "oracle").mkdir()
        (home / "oracle" / "chain.json").write_text(json.dumps(ledger))
        first = step.step(_doc(), NOON)
        assert [a["action"] for a in first] == ["held"]
        assert step.step(_doc(), NOON + 5) == []
        assert hands.calls == []
        assert len(notifier.sent) == 1

    def test_yesterdays_starts_do_not_count(self, home):
        hands = Hands()
        step, _ = _step(home, hands, Inputs(_queue("a")))
        (home / "oracle").mkdir()
        (home / "oracle" / "chain.json").write_text(json.dumps({"version": 1, "projects": {
            KEY: {"starts": [{"id": f"C{i}", "day": "2026-11-17"} for i in range(20)]}}}))
        assert [a["action"] for a in step.step(_doc(), NOON)] == ["started"]

    def test_the_machine_cap_counts_running_chains_in_other_projects(self, home):
        hands = Hands()
        step, _ = _step(home, hands, Inputs(_queue("a")), machine_cap=1)
        (home / "oracle").mkdir()
        (home / "oracle" / "chain.json").write_text(json.dumps({"version": 1, "projects": {
            "/state/other": {"current": {"id": "C1", "state": "running", "name": "o",
                                         "pid": 1, "at": "x", "appeared": None}}}}))
        actions = step.step(_doc(), NOON)
        assert [a["action"] for a in actions] == ["held"]
        assert "machine" in actions[0]["detail"]


class TestFollowing:
    def _started(self, home, hands, inputs=None, **kw):
        step, holder = _step(home, hands, inputs or Inputs(_queue("IDEA_1")), **kw)
        step.step(_doc(), NOON)
        holder["inputs"] = Inputs(Queue())
        return step

    def test_binding_by_the_launched_name(self, home):
        hands = Hands()
        step = self._started(home, hands)
        row = _session_row("s9", hosts=[_host(name="demo-IDEA_1", alive=True)])
        actions = step.step(_doc([row]), NOON + 30)
        assert [a["action"] for a in actions] == ["bound"]
        assert hands.calls[-1] == ("mark", KEY, "Q1", "started", "s9", None)
        assert _state(home)["current"]["session"] == "s9"

    def test_binding_by_the_pid(self, home):
        hands = Hands()
        step = self._started(home, hands)
        row = _session_row("s9", hosts=[_host(pid=4242, alive=True)])
        step.step(_doc([row]), NOON + 30)
        assert _state(home)["current"]["session"] == "s9"

    def test_a_torn_down_session_is_done(self, home):
        hands = Hands()
        step = self._started(home, hands, ends={"s9": "done"})
        step.step(_doc([_session_row("s9", hosts=[_host(name="demo-IDEA_1")])]), NOON + 30)
        actions = step.step(_doc(), NOON + 60)
        assert [a["action"] for a in actions] == ["done"]
        assert hands.calls[-1] == ("mark", KEY, "Q1", "done", None, None)
        assert _state(home)["current"] is None
        assert _state(home)["starts"][-1]["outcome"] == "done"

    @pytest.mark.parametrize(("end", "needle"), [("open", "deliverables open"),
                                                 ("abandoned", "abandoned"),
                                                 ("missing", "cannot be found")])
    def test_other_endings_are_failures(self, home, end, needle):
        hands = Hands()
        step = self._started(home, hands, ends={"s9": end})
        step.step(_doc([_session_row("s9", hosts=[_host(name="demo-IDEA_1")])]), NOON + 30)
        actions = step.step(_doc(), NOON + 60)
        assert [a["action"] for a in actions] == ["failed"]
        assert needle in actions[0]["detail"]
        fail = hands.calls[-1]
        assert fail[:3] == ("fail", KEY, "Q1") and fail[4:] == (True, True)

    def test_unbound_after_ten_minutes_fails_and_is_triaged(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        step = self._started(home, hands, notifier=notifier)
        assert step.step(_doc([{"kind": "host", "hosts": [_host(name="demo-IDEA_1")]}]),
                         NOON + 599) == []
        actions = step.step(_doc([{"kind": "host", "hosts": [_host(name="demo-IDEA_1")]}]),
                            NOON + 600)
        assert [a["action"] for a in actions] == ["failed"]
        assert "no invariant session" in actions[0]["detail"]
        assert "triage Q90" in actions[0]["detail"] and "retry Q91" in actions[0]["detail"]
        assert _state(home)["current"] is None
        assert _state(home)["paused"] is None
        assert any("triage" in n.message for n in notifier.sent)

    def test_a_window_gone_for_a_minute_fails(self, home):
        hands = Hands()
        step = self._started(home, hands)
        gone = _doc([_session_row("s9", hosts=[_host(name="demo-IDEA_1", alive=False)])])
        assert step.step(gone, NOON + 30) == [{"project": "demo", "action": "bound",
                                               "detail": "Q1 IDEA_1 is session s9"}]
        assert step.step(gone, NOON + 60) == []
        actions = step.step(gone, NOON + 91)
        assert [a["action"] for a in actions] == ["failed"]
        assert "window closed" in actions[0]["detail"]

    def test_a_window_that_comes_back_clears_the_count(self, home):
        hands = Hands()
        step = self._started(home, hands)
        gone = _doc([_session_row("s9", hosts=[_host(name="demo-IDEA_1", alive=False)])])
        back = _doc([_session_row("s9", hosts=[_host(name="demo-IDEA_1", alive=True)])])
        step.step(gone, NOON + 30)
        step.step(back, NOON + 60)
        assert step.step(gone, NOON + 100) == []

    def test_unknown_liveness_is_not_gone(self, home):
        hands = Hands()
        step = self._started(home, hands)
        unknown = _doc([_session_row("s9", hosts=[_host(name="demo-IDEA_1", alive=None)])])
        step.step(unknown, NOON + 30)
        assert step.step(unknown, NOON + 500) == []

    def test_another_live_window_in_the_project_is_not_gone(self, home):
        hands = Hands()
        step = self._started(home, hands)
        doc = _doc([_session_row("s9", hosts=[_host(name="demo-IDEA_1", alive=False)]),
                    {"kind": "host", "hosts": [_host(hid="h2", alive=True)]}])
        step.step(doc, NOON + 30)
        assert step.step(doc, NOON + 500) == []

    def test_a_session_that_reported_done_is_not_gone(self, home):
        hands = Hands()
        step = self._started(home, hands)
        doc = _doc([_session_row("s9", hosts=[_host(name="demo-IDEA_1", alive=False)],
                                 report={"outcome": "done", "at": NOON})])
        step.step(doc, NOON + 30)
        assert step.step(doc, NOON + 500) == []

    def test_a_launch_that_fails_is_triaged(self, home):
        hands = Hands()
        hands.fail_launch = InvariantError("the launch did not start a successor")
        step, _ = _step(home, hands, Inputs(_queue("IDEA_1")))
        actions = step.step(_doc(), NOON)
        assert [a["action"] for a in actions] == ["failed"]
        fail = hands.calls[-1]
        assert fail[0] == "fail" and fail[3] == "the launch failed"
        assert _state(home)["starts"][-1]["detail"] == "the launch did not start a successor"
        assert _state(home)["current"] is None

    def test_a_retry_that_fails_is_not_retried(self, home):
        hands = Hands()
        q = Queue()
        q.add("IDEA_1", added_by="oracle", retry_of="Q0")
        hands.fail_launch = InvariantError("no")
        step, _ = _step(home, hands, Inputs(q))
        step.step(_doc(), NOON)
        assert hands.calls[-1][4:] == (True, False)

    def test_a_triage_that_fails_pauses_the_chain(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        q = Queue()
        q.add("triage Q1", added_by="oracle", triage_of="Q1", brief="look")
        hands.fail_launch = InvariantError("no")
        step, _ = _step(home, hands, Inputs(q), notifier)
        actions = step.step(_doc(), NOON)
        assert [a["action"] for a in actions] == ["paused"]
        assert hands.calls[-1][4:] == (False, False)
        assert _state(home)["paused"]["by"] == "failure"
        assert any("paused" in n.message for n in notifier.sent)
        assert step.step(_doc(), NOON + 5) == []

    def test_a_failed_triage_pauses_even_when_its_entry_cannot_be_marked(self, home):
        hands = Hands()
        hands.fail = lambda *a: (_ for _ in ()).throw(UsageError("gone"))
        q = Queue()
        q.add("triage Q1", added_by="oracle", triage_of="Q1", brief="look")
        hands.fail_launch = InvariantError("no")
        logged: list[str] = []
        step, _ = _step(home, hands, Inputs(q))
        step.log = logged.append
        actions = step.step(_doc(), NOON)
        assert [a["action"] for a in actions] == ["paused"]
        assert _state(home)["paused"]["by"] == "failure"
        assert _state(home)["current"] is None
        assert any("could not mark" in line for line in logged)

    def test_a_fail_the_recorder_refuses_pauses(self, home):
        hands = Hands()
        hands.fail = lambda *a: (_ for _ in ()).throw(UsageError("privacy"))
        hands.fail_launch = InvariantError("no")
        step, _ = _step(home, hands, Inputs(_queue("IDEA_1")))
        actions = step.step(_doc(), NOON)
        assert [a["action"] for a in actions] == ["paused"]
        assert "could not be queued" in actions[0]["detail"]

    def test_a_stale_reservation_of_a_dead_watcher_is_a_failure(self, home):
        hands = Hands()
        step, _ = _step(home, hands, Inputs(Queue()), alive=lambda pid: False)
        (home / "oracle").mkdir()
        (home / "oracle" / "chain.json").write_text(json.dumps({"version": 1, "projects": {
            KEY: {"current": {"id": "C1", "state": "launching", "entry": "Q1", "ref": "a",
                              "by_pid": 999999, "at_epoch": NOON - 5, "name": None}}}}))
        actions = step.step(_doc(), NOON)
        assert [a["action"] for a in actions] == ["failed"]
        assert "interrupted" in actions[0]["detail"]

    def test_a_fresh_reservation_of_a_live_watcher_holds(self, home):
        hands = Hands()
        step, _ = _step(home, hands, Inputs(_queue("b")))
        (home / "oracle").mkdir()
        (home / "oracle" / "chain.json").write_text(json.dumps({"version": 1, "projects": {
            KEY: {"current": {"id": "C1", "state": "launching", "entry": "Q1", "ref": "a",
                              "by_pid": os.getpid() + 1, "at_epoch": NOON - 5}}}}))
        assert step.step(_doc(), NOON) == []
        assert hands.calls == []

    def test_an_old_reservation_is_a_failure_whoever_holds_it(self, home):
        hands = Hands()
        step, _ = _step(home, hands, Inputs(Queue()))
        (home / "oracle").mkdir()
        (home / "oracle" / "chain.json").write_text(json.dumps({"version": 1, "projects": {
            KEY: {"current": {"id": "C1", "state": "launching", "entry": "Q1", "ref": "a",
                              "by_pid": os.getpid(), "at_epoch": NOON - 121}}}}))
        assert [a["action"] for a in step.step(_doc(), NOON)] == ["failed"]


class TestStopReports:
    def test_a_blocked_report_pauses_and_outlives_the_session(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        step, _ = _step(home, hands, Inputs(_queue("a")), notifier)
        doc = _doc([_session_row("s1", report={"outcome": "blocked", "note": "stuck",
                                               "at": NOON - 10})])
        actions = step.step(doc, NOON)
        assert [a["action"] for a in actions] == ["paused"]
        assert _state(home)["paused"]["by"] == "report"
        assert "stuck" in notifier.sent[0].message
        assert step.step(_doc(), NOON + 5) == []
        assert hands.calls == []

    def test_resume_clears_it_and_the_old_report_stays_quiet(self, home):
        hands = Hands()
        step, _ = _step(home, hands, Inputs(_queue("a")))
        report = {"outcome": "needs-user", "at": NOON - 10}
        step.step(_doc([_session_row("s1", report=report)]), NOON)
        chainstep.resume(home, KEY, NOON + 1)
        assert _state(home)["paused"] is None
        assert step.step(_doc([_session_row("s1", report=report)]), NOON + 2) == []
        assert [a["action"] for a in step.step(_doc(), NOON + 3)] == ["started"]


class TestTheLedger:
    def test_an_unreadable_ledger_halts_the_step_and_says_so_once(self, home):
        hands, notifier, logged = Hands(), RecordingNotifier(), []
        step = ChainStep(home, hands.as_hands(), notifier,
                         reader=lambda root: Inputs(_queue("a")),
                         session_end=lambda r, s: "live", log=logged.append)
        (home / "oracle").mkdir()
        (home / "oracle" / "chain.json").write_text("{not json")
        assert step.step(_doc(), NOON) == []
        assert step.step(_doc(), NOON + 5) == []
        assert hands.calls == []
        assert len(notifier.sent) == 1
        assert (home / "oracle" / "chain.json").read_text() == "{not json"

    def test_a_newer_ledger_is_not_rewritten(self, home):
        (home / "oracle").mkdir()
        (home / "oracle" / "chain.json").write_text(json.dumps({"version": 2}))
        with pytest.raises(StateError, match="version"):
            chainstep.status(home, NOON)

    def test_owed_notices_are_delivered_later(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        notifier.fail = True
        step, _ = _step(home, hands, Inputs(_queue("a")), notifier)
        step.step(_doc(), NOON)
        assert _ledger(home)["notices"]
        notifier.fail = False
        step.step(_doc([_session_row("s1", hosts=[_host(name="demo-a")])]), NOON + 5)
        assert any("started" in n.message for n in notifier.sent)
        assert _ledger(home)["notices"] == []

    def test_no_notifier_keeps_them_owed(self, home):
        hands = Hands()
        step = ChainStep(home, hands.as_hands(), None, reader=lambda root: Inputs(_queue("a")),
                         session_end=lambda r, s: "live", log=lambda m: None)
        step.step(_doc(), NOON)
        assert len(_ledger(home)["notices"]) == 1

    def test_pause_by_hand_keeps_the_first_reason(self, home):
        assert chainstep.pause(home, KEY, "lunch", NOON)["paused"]["reason"] == "lunch"
        assert chainstep.pause(home, KEY, "later", NOON + 1)["paused"]["reason"] == "lunch"
        assert chainstep.resume(home, KEY, NOON + 2)["was"]["reason"] == "lunch"
        assert chainstep.resume(home, KEY, NOON + 3)["was"] is None

    def test_status_and_limits(self, home):
        chainstep.pause(home, KEY, "lunch", NOON)
        status = chainstep.status(home, NOON)
        assert status["projects"][KEY]["paused"]["reason"] == "lunch"
        limits = chainstep.limits(home, KEY, NOON)
        assert limits["paused"] == "lunch"
        assert (limits["started_today"], limits["machine_live"]) == (0, 0)

    def test_limits_of_an_unreadable_ledger_pause(self, home):
        (home / "oracle").mkdir()
        (home / "oracle" / "chain.json").write_text("[]")
        assert "ledger" in chainstep.limits(home, KEY, NOON)["paused"]

    def test_starts_are_bounded(self, home):
        hands = Hands()
        step, _ = _step(home, hands, Inputs(_queue("a")), machine_cap=1000)
        (home / "oracle").mkdir()
        (home / "oracle" / "chain.json").write_text(json.dumps({"version": 1, "projects": {
            KEY: {"starts": [{"id": f"C{i}", "day": "2020-01-01"} for i in range(100)]}}}))
        step.step(_doc(), NOON)
        assert len(_state(home)["starts"]) == chainstep.STARTS_KEPT


class TestSessionEnd:
    def test_the_answers(self, project):
        root = str(project)
        assert chain.session_end(root, "nope") == "missing"
        assert main(["session", "start", "work"]) == 0
        domain = cli_main.Context().sessions
        sid = domain.list()[0].id
        assert chain.session_end(root, sid) == "live"
        session = domain.get(sid)
        session.state = "torn_down"
        domain._save(session)
        assert chain.session_end(root, sid) == "done"
        session.plan = [{"n": 1, "title": "a", "done": False, "done_at": None}]
        domain._save(session)
        assert chain.session_end(root, sid) == "open"
        session.plan = [{"n": 1, "title": "a", "done": True, "done_at": 1.0}]
        domain._save(session)
        assert chain.session_end(root, sid) == "done"
        session.state = "abandoned"
        domain._save(session)
        assert chain.session_end(root, sid) == "abandoned"


# -- the commands --------------------------------------------------------------------


def _json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


class TestTheCommands:
    def test_pause_resume_and_status(self, project, capsys):
        assert main(["oracle", "chain", "pause", "demo", "--reason", "lunch", "--json"]) == 0
        assert _json(capsys)["paused"]["reason"] == "lunch"
        assert main(["oracle", "chain", "status", "demo", "--json"]) == 0
        out = _json(capsys)
        assert out["projects"][0]["paused"]["reason"] == "lunch"
        assert out["projects"][0]["daily_cap"] == 20
        assert out["machine_cap"] == 10
        assert main(["oracle", "next", "demo", "--json"]) == 0
        nxt = _json(capsys)
        assert nxt["verdict"] == "paused" and nxt["facts"]["paused"] == "lunch"
        assert main(["oracle", "chain", "resume", "demo"]) == 0
        assert "resumed" in capsys.readouterr().out
        assert main(["oracle", "chain", "resume", "demo"]) == 0
        assert "not paused" in capsys.readouterr().out
        assert main(["oracle", "chain", "status"]) == 0
        assert "demo" in capsys.readouterr().out

    def test_an_unknown_project_is_refused(self, project, capsys):
        for verb in ("pause", "resume", "start", "status"):
            assert main(["oracle", "chain", verb, "nope"]) == 2

    def test_start_with_nothing_to_start_is_refused(self, project, capsys):
        assert main(["oracle", "chain", "start", "demo"]) == 2
        assert "nothing" in capsys.readouterr().err

    def test_start_launches_the_head_through_the_start_session_path(
            self, project, capsys, monkeypatch):
        assert main(["queue", "add", "fix it", "--propose", "--brief", "fix the thing"]) == 0
        capsys.readouterr()
        launched = []

        def fake_launch(result):
            launched.append(result)
            return {**result, "successor": {"pid": 77}}, ["opened"]

        monkeypatch.setattr(cli_main, "_start_launch", fake_launch)
        assert main(["oracle", "chain", "start", "demo", "--json"]) == 0
        out = _json(capsys)
        assert out["started"]["entry"] == "Q1" and out["started"]["pid"] == 77
        assert launched[0]["prompt"] == "fix the thing"
        assert launched[0]["name"] == "demo-fix-it"
        assert _domain(project).list().entries[0].state == "started"
        assert main(["oracle", "chain", "start", "demo"]) == 2
        assert "already" in capsys.readouterr().err

    def test_start_names_an_entry(self, project, capsys, monkeypatch):
        assert main(["queue", "add", "one"]) == 0
        assert main(["queue", "add", "two"]) == 0
        monkeypatch.setattr(cli_main, "_start_launch",
                            lambda r: ({**r, "successor": {"pid": 5}}, []))
        assert main(["oracle", "chain", "start", "demo", "--entry", "Q2"]) == 0
        assert _domain(project).list().find("Q2").state == "started"
        assert main(["oracle", "chain", "start", "demo", "--entry", "Q9"]) == 2

    def test_a_start_whose_launch_fails_exits_one_and_is_triaged(self, project, capsys):
        assert main(["queue", "add", "work"]) == 0
        capsys.readouterr()
        assert main(["oracle", "chain", "start", "demo"]) == 1
        entries = _domain(project).list().entries
        assert entries[0].ref == "triage Q1" and entries[0].triage_of == "Q1"
        assert entries[1].retry_of == "Q1"

    def test_watch_once_runs_the_step_and_no_chain_does_not(
            self, project, capsys, _no_real_terminal_launch):
        assert main(["queue", "add", "work"]) == 0
        capsys.readouterr()
        assert main(["oracle", "watch", "--once", "--no-chain", "--json"]) == 0
        assert _json(capsys).get("chain") == []
        assert _domain(project).list().entries[0].state == "queued"
        assert main(["oracle", "watch", "--once", "--json"]) == 0
        out = _json(capsys)
        assert [a["action"] for a in out["chain"]] == ["failed"]
        assert _domain(project).list().entries[0].ref == "triage Q1"

    def test_queue_add_propose_and_project(self, project, tmp_path, capsys, monkeypatch):
        assert main(["queue", "add", "fix", "--propose", "--json"]) == 0
        assert _json(capsys)["entry"]["mode"] == "propose"
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        assert main(["queue", "add", "next one", "--project", "demo", "--json"]) == 0
        assert _json(capsys)["entry"]["id"] == "Q2"
        assert main(["queue", "list", "--project", "demo", "--json"]) == 0
        assert [e["ref"] for e in _json(capsys)["entries"]] == ["fix", "next one"]
        assert main(["queue", "list", "--project", "nope"]) == 2


class TestTheEdges:
    def test_pid_alive(self):
        assert chainstep._pid_alive(os.getpid()) is True
        assert chainstep._pid_alive(None) is False
        assert chainstep._pid_alive(0) is False
        assert chainstep._pid_alive(2**22 + 12345) is False

    def test_a_user_start_is_refused_while_one_is_followed(self, home):
        hands = Hands()
        step, _ = _step(home, hands, Inputs(_queue("a")))
        step.step(_doc(), NOON)
        with pytest.raises(InvariantError, match="one at a time"):
            step.start_now(KEY, "demo", "/work/demo", Candidate("queue", "b", entry="Q2"),
                           ChainSettings(), NOON + 1)
        assert step.current(KEY)["entry"] == "Q1"

    def test_a_user_start_whose_entry_cannot_be_marked_is_refused(self, home):
        hands = Hands()
        hands.begin = _raise_usage
        step, _ = _step(home, hands)
        with pytest.raises(InvariantError, match="could not be marked"):
            step.start_now(KEY, "demo", "/work/demo", Candidate("queue", "b", entry="Q2"),
                           ChainSettings(), NOON)

    def test_a_user_start_is_recorded_as_accepted(self, home):
        hands = Hands()
        step, _ = _step(home, hands)
        actions, opened = step.start_now(KEY, "demo", "/work/demo",
                                         Candidate("queue", "b", entry="Q2"),
                                         ChainSettings(), NOON)
        assert opened == {"name": "demo-b", "pid": 4242}
        assert [a["action"] for a in actions] == ["started"]
        assert step.current(KEY)["mode"] == "accepted"

    def test_current_of_an_unreadable_ledger_is_none(self, home):
        (home / "oracle").mkdir()
        (home / "oracle" / "chain.json").write_text("{")
        step, _ = _step(home, Hands())
        assert step.current(KEY) is None


class TestMoreCommands:
    def test_a_started_entry_cannot_be_started_again(self, project, capsys, monkeypatch):
        assert main(["queue", "add", "one"]) == 0
        _domain(project).mark("Q1", "done")
        assert main(["oracle", "chain", "start", "demo", "--entry", "Q1"]) == 2
        assert "only a queued entry" in capsys.readouterr().err

    def test_a_pause_reason_is_one_line(self, project, capsys):
        assert main(["oracle", "chain", "pause", "demo", "--reason", "a\nb"]) == 2

    def test_a_backlog_item_is_started_by_hand(self, project, capsys, monkeypatch):
        assert main(["item", "new", "idea", "--title", "Wanted"]) == 0
        capsys.readouterr()
        monkeypatch.setattr(cli_main, "_start_launch",
                            lambda r: ({**r, "successor": {"pid": 5}}, []))
        assert main(["oracle", "chain", "start", "demo", "--json"]) == 0
        out = _json(capsys)
        assert out["started"]["ref"].startswith("IDEA_")
        entry = _domain(project).list().entries[0]
        assert (entry.ref, entry.state, entry.added_by) == (
            out["started"]["ref"], "started", "oracle")
        assert out["started"]["entry"] == entry.id


# -- BUG_158: a chained start on a default prompt never binds ----------------------
#
# Repro for BUG_158 — see the work-item entry. The default first prompts open
# with the session start, so a default-prompt start binds.

#: The step every user-written brief that binds carries, and the only thing that
#: creates the session row the chain step binds to.
SESSION_START = "invariant session start"


def _bug_158_default_prompts() -> dict[str, str]:
    """The first prompt each default-prompt start is launched with, rendered
    the way the chain step renders it (``chain.render_prompt``)."""
    from invariant.domains.queue import TRIAGE_REF

    settings = ChainSettings()
    # A backlog (mirrored) entry: no brief, so oracle.chain.prompt.
    backlog = chain.render_prompt(settings, Candidate("queue", "IDEA_12", entry="Q17",
                                                      title="Twelve"))
    # A triage entry: its brief is oracle.chain.triage_prompt, filled as
    # QueueDomain.fail fills it, and render_prompt hands the brief on.
    brief = settings.triage_prompt.format(
        entry="Q17", ref="IDEA_12",
        reason="no invariant session was bound 10 minutes after the window appeared",
        retry="Q116")
    triage = chain.render_prompt(settings, Candidate(
        "queue", TRIAGE_REF.format(entry="Q17"), entry="Q117", brief=brief))
    # A reproduce-first start of a filed bug: oracle.chain.reproduce_prompt.
    reproduce = chain.render_prompt(settings, Candidate(
        "queue", "BUG_3", entry="Q4", reproduce=True, residue=0))
    return {"chain": backlog, "triage": triage, "reproduce": reproduce}


@pytest.mark.parametrize("kind", ["chain", "triage", "reproduce"])
def test_BUG_158_default_first_prompt_carries_a_session_start(kind):
    prompt = _bug_158_default_prompts()[kind]
    assert SESSION_START in prompt, f"the default {kind} prompt has no session start: {prompt!r}"


def _bug_158_window(name: str, prompt: str) -> dict:
    """A fake window: it runs ``invariant session start`` if and only if its
    first prompt tells it to. Then the session row carries its host; otherwise
    only the bare host row exists, as on the user's machine."""
    host = _host(name=name, alive=True)
    if SESSION_START in prompt:
        return _session_row("s158", hosts=[host])
    return {"kind": "host", "hosts": [host]}


def _bug_158_inputs(kind: str, brief: str | None = None) -> chain.Inputs:
    q = Queue()
    if kind == "chain":
        q.add("IDEA_12", added_by="oracle")
        return chain.Inputs(q, [], ChainSettings())
    if kind == "triage":
        settings = ChainSettings()
        q.add("triage Q17", added_by="oracle", brief=settings.triage_prompt.format(
            entry="Q17", ref="IDEA_12", reason="no invariant session was bound",
            retry="Q116"))
        return chain.Inputs(q, [], settings)
    if kind == "reproduce":
        q.add("BUG_3", added_by="oracle")
        return chain.Inputs(q, [], ChainSettings(), unreproduced={"BUG_3": 0})
    q.add("IDEA_12", added_by="user", brief=brief)
    return chain.Inputs(q, [], ChainSettings())


def _bug_158_follow(home, kind: str, brief: str | None = None) -> list[str]:
    """Start the head entry, open the fake window on the prompt it was given,
    and follow it to the bind timeout: the actions of every tick."""
    hands = Hands()
    step, holder = _step(home, hands, _bug_158_inputs(kind, brief))
    started = step.step(_doc(), NOON)
    assert [a["action"] for a in started] == ["started"]
    _, _, label, prompt = next(c for c in hands.calls if c[0] == "start")
    holder["inputs"] = chain.Inputs(Queue(), [], ChainSettings())
    window = _bug_158_window(f"demo-{label}", prompt)
    actions = []
    for at in (NOON + 30, NOON + 600, NOON + 630):
        actions += [a["action"] if a["action"] != "failed" else a["detail"]
                    for a in step.step(_doc([window]), at)]
    return actions


@pytest.mark.parametrize("kind", ["chain", "triage", "reproduce"])
def test_BUG_158_a_default_prompt_start_binds_a_session(home, kind):
    actions = _bug_158_follow(home, kind)
    assert actions and actions[0] == "bound", f"the {kind} start never bound: {actions}"
    assert not any("failed" in a for a in actions)


def test_BUG_158_control_a_brief_with_a_session_start_binds(home):
    """The same harness, on a user-written brief that carries the step, binds:
    the harness can go green, so the red above is the prompt's."""
    actions = _bug_158_follow(home, "brief", brief=(
        "First run invariant session start, session isolate --execute and session "
        "plan add, then take up IDEA_12."))
    assert actions == ["bound"]
