"""ADR-027 increment 5: the whole backlog reaches the chain (rulings 18 to 20).

Three things, each where it lives. The **mirror** is the engine's: every open
backlog item in a mirrored state gets one queue entry (``added_by: backlog``),
inserted by backlog rank, skipped while a live entry serves it, removed when
it closes (``domains/queue.mirror``, run by the chain step's hands and by
``invariant queue mirror``). **Backlog work starts on its own**: mirrored
entries take ``oracle.chain.backlog``, now ``auto`` by default. **A bug is
reproduced first**: a filed bug's entry starts with the reproduce prompt, and
the verdict is read back from the item when that session ends
(``chain.reproduce_outcome``), then routed through the hands
(``domains/queue.after_reproduce``). The oracle writes no record (D18).
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core import queue as queue_record
from invariant.core.errors import ManifestError, StateError, UsageError
from invariant.core.machines import load_item_machine
from invariant.core.manifest import DEFAULT_REPRODUCE_PROMPT, DEFAULTS, Manifest
from invariant.core.queue import BACKLOG, Queue
from invariant.core.store import JsonStore
from invariant.domains.queue import QueueDomain
from invariant.oracle import chain, chainview, panel
from invariant.oracle.chain import Candidate, ChainSettings
from invariant.oracle.chainstep import ChainHands, ChainStep
from invariant.oracle.notify import RecordingNotifier
from invariant.oracle.scan import ScanCache

NOON = time.mktime((2026, 11, 20, 12, 0, 0, 0, 0, -1))


def _git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _scan_without_a_probe(monkeypatch):
    monkeypatch.setattr(cli_main, "_oracle_cache", lambda: ScanCache())


@pytest.fixture(autouse=True)
def _fresh_usage(monkeypatch):
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


def _file(item_type: str, title: str, tier=None, priority=None) -> str:
    argv = ["item", "new", item_type, "--title", title, "--json"]
    if tier is not None:
        argv += ["--field", f"tier={tier}"]
    if priority is not None:
        argv += ["--field", f"priority={priority}"]
    assert main(argv) == 0
    return _domain(Path.cwd()).items.list()[-1].id


def _refs(root: Path) -> list[tuple[str, str, str]]:
    return [(e.ref, e.state, e.added_by) for e in _domain(root).list().entries]


def _set_chain(root: Path, **values) -> None:
    path = root / ".claude" / "invariant.yaml"
    text = path.read_text(encoding="utf-8")
    lines = ["oracle:", "  chain:"] + [f"    {k}: {json.dumps(v)}" for k, v in values.items()]
    path.write_text(text + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


# -- the record -------------------------------------------------------------------


class TestTheRecord:
    def test_serves_and_blind_are_left_out_while_unset(self):
        entry = Queue().add("IDEA_1", added_by="user")
        assert "serves" not in entry.to_dict() and "blind" not in entry.to_dict()

    def test_serves_blind_and_mirrored_round_trip(self):
        q = Queue()
        q.add("ADR-029 inc 1", added_by="user", serves="IDEA_60")
        q.entries[0].blind = 1
        q.mirrored = ["IDEA_9", "BUG_10"]
        again = queue_record.parse(json.loads(json.dumps(q.to_dict())))
        assert (again.entries[0].serves, again.entries[0].blind) == ("IDEA_60", 1)
        assert again.mirrored == ["BUG_10", "IDEA_9"]

    def test_an_empty_mirrored_list_is_left_out(self):
        assert "mirrored" not in Queue().to_dict()

    def test_a_mirrored_list_that_is_not_ids_is_refused(self):
        with pytest.raises(StateError, match="mirrored"):
            queue_record.parse({"entries": [], "mirrored": "IDEA_1"})
        with pytest.raises(StateError, match="mirrored"):
            queue_record.parse({"entries": [], "mirrored": [3]})

    def test_serving_is_a_live_entry_naming_or_serving_the_item(self):
        q = Queue()
        q.add("ADR-029 inc 1", added_by="user", serves="IDEA_60")
        q.add("IDEA_7", added_by="user")
        assert q.serving("IDEA_60").id == "Q1"
        assert q.serving("IDEA_7").id == "Q2"
        q.mark("Q1", "done")
        assert q.serving("IDEA_60") is None

    def test_the_rank_key_is_tier_priority_then_natural_id(self):
        keys = sorted(
            [("IDEA_10", 1, "low"), ("IDEA_9", 1, "low"), ("BUG_2", None, "high"),
             ("IDEA_3", "2", "high"), ("IDEA_4", 1, "high")],
            key=lambda t: queue_record.rank_key(t[0], t[1], t[2]))
        assert [k[0] for k in keys] == ["IDEA_4", "IDEA_9", "IDEA_10", "IDEA_3", "BUG_2"]


# -- the settings -------------------------------------------------------------------


class TestTheSettings:
    def test_the_shipped_defaults_are_on(self):
        chain_defaults = DEFAULTS["oracle"]["chain"]
        assert chain_defaults["backlog"] == "auto"
        assert chain_defaults["mirror"] is True
        assert chain_defaults["mirror_states"] is None
        assert chain_defaults["reproduce_first"] is True
        assert chain_defaults["reproduce_prompt"] is None
        settings = ChainSettings()
        assert (settings.backlog, settings.mirror, settings.reproduce_first) == (
            "auto", True, True)
        assert settings.reproduce_prompt == DEFAULT_REPRODUCE_PROMPT

    def test_the_settings_are_read_from_the_manifest(self, project):
        _set_chain(project, backlog="propose", mirror=False, reproduce_first=False,
                   reproduce_prompt="Prove {ref} ({entry}), attempt {attempt}: {title}")
        settings = ChainSettings.from_manifest(Manifest.locate(project))
        assert (settings.backlog, settings.mirror, settings.reproduce_first) == (
            "propose", False, False)
        assert settings.reproduce_prompt.startswith("Prove {ref}")

    @pytest.mark.parametrize(("values", "needle"), [
        ({"mirror": "yes"}, "oracle.chain.mirror"),
        ({"reproduce_first": 1}, "oracle.chain.reproduce_first"),
        ({"mirror_states": ["filed"]}, "oracle.chain.mirror_states"),
        ({"mirror_states": {"task": ["open"]}}, "task"),
        ({"mirror_states": {"bug": []}}, "oracle.chain.mirror_states.bug"),
        ({"mirror_states": {"bug": ["filed", 3]}}, "oracle.chain.mirror_states.bug"),
        ({"reproduce_prompt": "Prove {ref} {why}"}, "oracle.chain.reproduce_prompt"),
        ({"reproduce_prompt": "two\nlines"}, "oracle.chain.reproduce_prompt"),
    ])
    def test_a_bad_value_is_refused_at_load(self, project, values, needle):
        _set_chain(project, **values)
        with pytest.raises(ManifestError, match=needle.replace(".", r"\.")):
            Manifest.locate(project)

    def test_the_shipped_machines_mark_what_is_mirrored_and_reproduced(self):
        bug, idea = load_item_machine("bug"), load_item_machine("idea")
        assert bug.states_with_role("mirror") == ("filed", "reproduced")
        assert bug.states_with_role("reproduce") == ("filed",)
        assert bug.states_with_role("ready") == ("reproduced",)
        assert idea.states_with_role("mirror") == ("proposed", "accepted", "in_progress")


# -- the mirror -----------------------------------------------------------------------


class TestTheMirror:
    def test_the_backfill_goes_behind_the_queue_in_rank_order(self, project):
        domain = _domain(project)
        domain.add("hand work")
        low = _file("idea", "Later", tier=2, priority="low")
        high = _file("idea", "Sooner", tier=1, priority="high")
        bug = _file("bug", "Broken", tier=1, priority="medium")
        out = domain.mirror()
        assert [ref for _, ref in out["added"]] == [high, bug, low]
        assert _refs(project) == [("hand work", "queued", "user"),
                                  (high, "queued", BACKLOG), (bug, "queued", BACKLOG),
                                  (low, "queued", BACKLOG)]
        assert sorted(domain.list().mirrored) == sorted([high, bug, low])

    def test_it_is_idempotent_and_writes_nothing_when_nothing_changed(self, project):
        _file("idea", "One")
        domain = _domain(project)
        domain.mirror()
        path = project / "docs" / "queue.json"
        before = path.stat().st_mtime_ns
        assert domain.mirror() == {"added": [], "removed": []}
        assert path.stat().st_mtime_ns == before

    def test_a_newly_filed_item_goes_in_by_rank_and_hand_entries_keep_their_places(
            self, project):
        domain = _domain(project)
        first = _file("idea", "First", tier=1)
        third = _file("idea", "Third", tier=3)
        domain.mirror()
        domain.add("hand work", at=2)
        second = _file("idea", "Second", tier=2)
        domain.mirror()
        assert [e.ref for e in domain.list().entries] == [first, "hand work", second, third]

    def test_an_item_any_entry_names_is_not_mirrored(self, project):
        domain = _domain(project)
        item = _file("idea", "Named")
        domain.add(item)
        domain.mark("Q1", "done")
        assert domain.mirror()["added"] == []

    def test_an_item_a_live_entry_serves_is_skipped_then_mirrored_once_it_ends(
            self, project):
        domain = _domain(project)
        item = _file("idea", "Big one")
        domain.add("ADR-9 inc 1", serves=item)
        assert domain.mirror()["added"] == []
        domain.mark("Q1", "done")
        assert [ref for _, ref in domain.mirror()["added"]] == [item]

    def test_a_mirrored_entry_another_live_entry_serves_leaves(self, project):
        domain = _domain(project)
        item = _file("idea", "Big one")
        domain.mirror()
        domain.add("ADR-9 inc 1")
        domain.link("Q2", item)
        out = domain.mirror()
        assert out["removed"] == [("Q1", item)]
        assert [e.ref for e in domain.list().entries] == ["ADR-9 inc 1"]

    def test_a_closed_item_leaves_the_queue(self, project):
        domain = _domain(project)
        item = _file("idea", "Dropped later")
        domain.mirror()
        assert main(["item", "transition", item, "dropped", "--reason", "no"]) == 0
        assert domain.mirror()["removed"] == [("Q1", item)]
        assert domain.list().entries == []
        assert item not in domain.list().mirrored

    def test_a_fixed_bug_is_not_mirrored_and_leaves(self, project):
        domain = _domain(project)
        bug = _file("bug", "Gone")
        domain.mirror()
        assert main(["item", "transition", bug, "fixed", "--ruling", "T, 2026-11-20",
                     "--evidence", "HEAD", "--reason", "red with it reverted"]) == 0
        assert domain.mirror()["removed"] == [("Q1", bug)]

    def test_an_entry_the_user_removed_is_not_mirrored_again(self, project):
        domain = _domain(project)
        _file("idea", "Not now")
        domain.mirror()
        domain.remove("Q1")
        assert domain.mirror()["added"] == []

    def test_an_item_a_live_session_names_is_skipped(self, project):
        domain = _domain(project)
        item = _file("idea", "Under way")
        assert main(["session", "start", f"{item.lower().replace('_', '-')}-work"]) == 0
        assert domain.mirror()["added"] == []

    def test_a_queue_entry_whose_item_is_not_found_is_left_alone(self, project):
        domain = _domain(project)
        with queue_record.editing(domain.path, domain.store) as queue:
            queue.add("IDEA_404", added_by=BACKLOG)
        assert domain.mirror()["removed"] == []

    def test_mirror_off_writes_nothing(self, project):
        _set_chain(project, mirror=False)
        _file("idea", "Quiet")
        assert _domain(project).mirror() == {"added": [], "removed": [], "off": True}
        assert not (project / "docs" / "queue.json").exists()

    def test_the_mirrored_states_are_configurable(self, project):
        _set_chain(project, mirror_states={"bug": ["reproduced"]})
        _file("idea", "Not mirrored")
        _file("bug", "Filed, not mirrored")
        assert _domain(project).mirror()["added"] == []

    def test_a_state_the_machine_does_not_have_is_refused(self, project):
        _set_chain(project, mirror_states={"bug": ["triaged"]})
        _file("bug", "x")
        with pytest.raises(UsageError, match="triaged"):
            _domain(project).mirror()

    def test_a_worktree_mirrors_the_main_checkout_backlog(self, project, tmp_path):
        worktree = tmp_path / "wt"
        _git(project, "worktree", "add", "-q", str(worktree), "-b", "side")
        manifest = Manifest.locate(worktree)
        side = QueueDomain(manifest, JsonStore(manifest.state_root))
        (worktree / "docs").mkdir(exist_ok=True)
        assert side.mirror()["added"] == []
        main_item = _file("idea", "On main")
        assert [ref for _, ref in side.mirror()["added"]] == [main_item]


# -- serves and link ----------------------------------------------------------------------


class TestServes:
    def test_add_with_serves_needs_an_open_item(self, project):
        domain = _domain(project)
        with pytest.raises(UsageError, match="IDEA_404"):
            domain.add("ADR-9 inc 1", serves="IDEA_404")
        with pytest.raises(UsageError, match="work item"):
            domain.add("ADR-9 inc 1", serves="a label")
        item = _file("idea", "Big")
        assert domain.add("ADR-9 inc 1", serves=item).serves == item

    def test_link_sets_and_clears_serves(self, project):
        domain = _domain(project)
        item = _file("idea", "Big")
        domain.add("ADR-9 inc 2")
        assert domain.link("Q1", item).serves == item
        assert domain.link("Q1", None).serves is None
        with pytest.raises(UsageError):
            domain.link("Q9", item)


# -- after a reproduction -----------------------------------------------------------------


class TestAfterReproduce:
    def _queue(self, project):
        domain = _domain(project)
        bug = _file("bug", "Broken")
        domain.add("first")
        domain.add(bug)
        domain.begin("Q2", bug)
        domain.mark("Q2", "started", session="s1")
        return domain, bug

    def test_red_puts_the_same_entry_back_at_the_head(self, project):
        domain, _ = self._queue(project)
        domain.after_reproduce("Q2", queue_record.REPRO_RED)
        head = domain.list().entries[0]
        assert (head.id, head.state, head.session, head.mode) == ("Q2", "queued", None, None)

    def test_closed_marks_it_done(self, project):
        domain, _ = self._queue(project)
        domain.after_reproduce("Q2", queue_record.REPRO_CLOSED)
        assert domain.list().find("Q2").state == "done"

    def test_owed_makes_it_a_proposal_in_its_place(self, project):
        domain, _ = self._queue(project)
        domain.after_reproduce("Q2", queue_record.REPRO_OWED)
        entries = domain.list().entries
        assert [e.id for e in entries] == ["Q1", "Q2"]
        assert (entries[1].state, entries[1].mode) == ("queued", "propose")

    def test_blind_runs_once_more_at_the_head_then_becomes_a_proposal(self, project):
        domain, _ = self._queue(project)
        domain.after_reproduce("Q2", queue_record.REPRO_BLIND)
        head = domain.list().entries[0]
        assert (head.id, head.state, head.blind, head.mode) == ("Q2", "queued", 1, None)
        domain.mark("Q2", "started", session="s2")
        domain.after_reproduce("Q2", queue_record.REPRO_BLIND)
        entry = domain.list().find("Q2")
        assert (entry.state, entry.blind, entry.mode) == ("queued", 2, "propose")

    def test_an_unknown_outcome_is_refused(self, project):
        domain, _ = self._queue(project)
        with pytest.raises(UsageError):
            domain.after_reproduce("Q2", "maybe")


# -- the decision ----------------------------------------------------------------------------


def _mirrored(*refs: str, hand: tuple[str, ...] = ()) -> Queue:
    q = Queue()
    for ref in hand:
        q.add(ref, added_by="user")
    for ref in refs:
        q.add(ref, added_by=BACKLOG)
    return q


def _decide(queue, settings=None, ready=(), unreproduced=None):
    from datetime import datetime, timezone

    return chain.decide(queue=queue, ready=list(ready), settings=settings or ChainSettings(),
                        live=[], windows=0, now=datetime(2026, 11, 20, 12, tzinfo=timezone.utc),
                        unreproduced=unreproduced)


class TestTheDecision:
    def test_a_mirrored_head_starts_under_backlog_auto(self):
        verdict = _decide(_mirrored("IDEA_1"))
        assert (verdict.verdict, verdict.mode, verdict.candidate.ref) == ("start", "auto",
                                                                          "IDEA_1")
        assert any("backlog" in r for r in verdict.reasons)

    def test_under_backlog_propose_the_first_mirrored_entry_is_offered_and_hand_work_runs(
            self):
        q = Queue()
        q.add("IDEA_1", added_by=BACKLOG)
        q.add("IDEA_2", added_by=BACKLOG)
        q.add("hand", added_by="user")
        verdict = _decide(q, ChainSettings(backlog="propose"))
        assert (verdict.verdict, verdict.mode, verdict.candidate.ref) == ("start", "auto",
                                                                          "hand")
        assert [p.entry for p in verdict.proposals] == ["Q1"]

    def test_under_backlog_off_mirrored_entries_wait_and_hand_work_runs(self):
        q = Queue()
        q.add("IDEA_1", added_by=BACKLOG)
        q.add("hand", added_by="user")
        verdict = _decide(q, ChainSettings(backlog="off"))
        assert verdict.candidate.ref == "hand"
        assert _decide(_mirrored("IDEA_1"), ChainSettings(backlog="off")).verdict == "idle"

    def test_a_filed_bug_is_started_to_reproduce_it(self):
        q = _mirrored("BUG_3")
        verdict = _decide(q, unreproduced={"BUG_3": 2})
        assert verdict.candidate.reproduce is True and verdict.candidate.residue == 2
        assert any("reproduce" in r for r in verdict.reasons)

    def test_reproduce_first_off_starts_it_plainly(self):
        verdict = _decide(_mirrored("BUG_3"), ChainSettings(reproduce_first=False),
                          unreproduced={"BUG_3": 0})
        assert verdict.candidate.reproduce is False

    def test_the_fallback_skips_an_item_a_live_entry_serves(self):
        from invariant.oracle.chain import Ready

        q = Queue()
        q.add("ADR-9 inc 1", added_by="user", serves="IDEA_5")
        q.mark("Q1", "started", session="s1")
        verdict = _decide(q, ready=[Ready("IDEA_5", "Five", "proposed")])
        assert verdict.verdict == "idle"

    def test_the_reproduce_prompt_names_the_attempt(self):
        candidate = Candidate("queue", "BUG_3", entry="Q4", reproduce=True, residue=0,
                              blind=1)
        prompt = chain.render_prompt(ChainSettings(), candidate)
        assert "BUG_3" in prompt and "Q4" in prompt and "2" in prompt
        assert "bug-reproducer" in prompt
        plain = chain.render_prompt(ChainSettings(), Candidate("queue", "BUG_3", entry="Q4"))
        assert "bug-reproducer" not in plain

    def test_reproducing_marks_a_candidate_by_hand(self):
        candidate = Candidate("queue", "BUG_3", entry="Q4")
        marked = chain.reproducing(candidate, {"BUG_3": 1}, ChainSettings())
        assert marked.reproduce and marked.residue == 1
        assert chain.reproducing(candidate, {}, ChainSettings()).reproduce is False


class TestTheReaders:
    def test_read_inputs_lists_the_unreproduced_bugs(self, project):
        bug = _file("bug", "Broken")
        idea = _file("idea", "Idea")
        assert main(["item", "residue", bug, "--add", "a live leg"]) == 0
        inputs = chain.read_inputs(project)
        assert inputs.unreproduced == {bug: 1}
        assert idea not in inputs.unreproduced

    def test_reproduce_outcome_reads_the_verdict_from_the_item(self, project):
        red = _file("bug", "Red")
        assert main(["item", "transition", red, "reproduced"]) == 0
        closed = _file("bug", "Gone")
        assert main(["item", "transition", closed, "not_reproducible", "--reason",
                     "passes"]) == 0
        owed = _file("bug", "Device")
        assert main(["item", "residue", owed, "--add", "on the phone"]) == 0
        blind = _file("bug", "Blind")
        assert chain.reproduce_outcome(project, red, 0) == queue_record.REPRO_RED
        assert chain.reproduce_outcome(project, closed, 0) == queue_record.REPRO_CLOSED
        assert chain.reproduce_outcome(project, owed, 0) == queue_record.REPRO_OWED
        assert chain.reproduce_outcome(project, blind, 0) == queue_record.REPRO_BLIND
        assert chain.reproduce_outcome(project, "BUG_404", 0) == queue_record.REPRO_CLOSED


# -- the step ---------------------------------------------------------------------------


class Hands:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.mirror_out = {"added": [("Q7", "IDEA_7")], "removed": []}

    def start(self, key, label, prompt):
        self.calls.append(("start", key, label, prompt))
        return {"name": f"demo-{label}", "pid": 4242}

    def begin(self, key, entry, ref):
        self.calls.append(("begin", key, entry, ref))
        return entry or "Q50"

    def mark(self, key, entry, state, session=None, reason=None):
        self.calls.append(("mark", key, entry, state, session, reason))

    def fail(self, key, entry, reason, template, retry):
        self.calls.append(("fail", key, entry))
        return {"failed": entry, "triage": None, "retry": None}

    def mirror(self, key):
        self.calls.append(("mirror", key))
        return self.mirror_out

    def reproduced(self, key, entry, outcome):
        self.calls.append(("reproduced", key, entry, outcome))

    def kinds(self) -> list[str]:
        return [c[0] for c in self.calls]

    def as_hands(self, mirror=True) -> ChainHands:
        return ChainHands(start=self.start, begin=self.begin, mark=self.mark, fail=self.fail,
                          mirror=self.mirror if mirror else None,
                          reproduced=self.reproduced if mirror else None)


KEY = "/state/demo"


class Inputs:
    def __init__(self, queue=None, settings=None, unreproduced=None):
        self.queue = queue if queue is not None else Queue()
        self.ready = []
        self.settings = settings or ChainSettings()
        self.unreproduced = unreproduced or {}


def _doc(rows=()) -> dict:
    return {"projects": [{"project": {"key": KEY, "name": "demo", "root": "/work/demo"},
                          "rows": list(rows)}]}


def _row(sid, name):
    return {"kind": "session", "session_id": sid, "name": "work", "state": "working",
            "hosts": [{"host_session_id": "h1", "launched_name": name, "pid": None,
                       "alive": True}]}


def _step(home, hands, inputs, outcome=None, ends=None, mirror=True):
    holder = {"inputs": inputs}
    step = ChainStep(home, hands.as_hands(mirror), RecordingNotifier(), machine_cap=10,
                     reader=lambda root: holder["inputs"],
                     session_end=lambda root, sid: (ends or {}).get(sid, "live"),
                     pid_alive=lambda pid: True, log=lambda message: None,
                     outcome=outcome or (lambda root, ref, residue: queue_record.REPRO_RED))
    return step, holder


def _state(home) -> dict:
    return json.loads((home / "oracle" / "chain.json").read_text())["projects"][KEY]


@pytest.fixture
def home(tmp_path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


class TestTheStep:
    def test_an_idle_tick_mirrors_first_then_once_a_minute(self, home):
        hands = Hands()
        step, _ = _step(home, hands, Inputs())
        actions = step.step(_doc(), NOON)
        assert hands.calls[0] == ("mirror", KEY)
        assert [a["action"] for a in actions] == ["mirrored"]
        assert "Q7 IDEA_7" in actions[0]["detail"]
        step.step(_doc(), NOON + 30)
        assert hands.kinds().count("mirror") == 1
        step.step(_doc(), NOON + 61)
        assert hands.kinds().count("mirror") == 2

    def test_a_mirror_that_changed_nothing_says_nothing(self, home):
        hands = Hands()
        hands.mirror_out = {"added": [], "removed": []}
        step, _ = _step(home, hands, Inputs())
        assert step.step(_doc(), NOON) == []

    def test_a_mirror_that_fails_is_logged_and_the_step_goes_on(self, home):
        hands = Hands()
        logged: list[str] = []

        def broken(key):
            raise UsageError("oracle.chain.mirror_states names triaged")

        hands.mirror = broken
        step, _ = _step(home, hands, Inputs(_mirrored("IDEA_1")))
        step.log = logged.append
        actions = step.step(_doc(), NOON)
        assert any("mirror" in line for line in logged)
        assert [a["action"] for a in actions] == ["started"]

    def test_hands_without_a_mirror_mirror_nothing(self, home):
        hands = Hands()
        step, _ = _step(home, hands, Inputs(), mirror=False)
        assert step.step(_doc(), NOON) == []
        assert "mirror" not in hands.kinds()

    def test_a_reproduce_start_is_recorded_and_prompted(self, home):
        hands = Hands()
        step, _ = _step(home, hands, Inputs(_mirrored("BUG_3"), unreproduced={"BUG_3": 1}))
        actions = step.step(_doc(), NOON)
        assert "reproduce" in actions[-1]["detail"]
        current = _state(home)["current"]
        assert (current["reproduce"], current["residue"]) == (True, 1)
        start = next(c for c in hands.calls if c[0] == "start")
        assert "bug-reproducer" in start[3]

    @pytest.mark.parametrize("outcome", [queue_record.REPRO_RED, queue_record.REPRO_CLOSED,
                                         queue_record.REPRO_OWED, queue_record.REPRO_BLIND])
    def test_the_end_of_a_reproduce_session_routes_its_verdict(self, home, outcome):
        hands = Hands()
        seen = []

        def read(root, ref, residue):
            seen.append((root, ref, residue))
            return outcome

        step, holder = _step(home, hands, Inputs(_mirrored("BUG_3"),
                                                 unreproduced={"BUG_3": 1}),
                             outcome=read, ends={"s9": "done"})
        step.step(_doc(), NOON)
        holder["inputs"] = Inputs(Queue())
        step.step(_doc([_row("s9", "demo-BUG_3")]), NOON + 30)
        actions = step.step(_doc(), NOON + 60)
        assert ("reproduced", KEY, "Q1", outcome) in hands.calls
        assert ("mark", KEY, "Q1", "done", None, None) not in hands.calls
        assert seen == [("/work/demo", "BUG_3", 1)]
        assert any(a["action"] == "reproduced" for a in actions)
        assert _state(home)["starts"][-1]["outcome"] == "done"

    def test_a_verdict_that_cannot_be_read_marks_it_done(self, home):
        hands = Hands()

        def broken(root, ref, residue):
            raise StateError("the backlog cannot be read")

        step, holder = _step(home, hands, Inputs(_mirrored("BUG_3"),
                                                 unreproduced={"BUG_3": 0}),
                             outcome=broken, ends={"s9": "done"})
        step.step(_doc(), NOON)
        holder["inputs"] = Inputs(Queue())
        step.step(_doc([_row("s9", "demo-BUG_3")]), NOON + 30)
        step.step(_doc(), NOON + 60)
        assert ("mark", KEY, "Q1", "done", None, None) in hands.calls

    def test_a_plain_start_ends_done_as_before(self, home):
        hands = Hands()
        step, holder = _step(home, hands, Inputs(_mirrored("IDEA_3")), ends={"s9": "done"})
        step.step(_doc(), NOON)
        holder["inputs"] = Inputs(Queue())
        step.step(_doc([_row("s9", "demo-IDEA_3")]), NOON + 30)
        step.step(_doc(), NOON + 60)
        assert ("mark", KEY, "Q1", "done", None, None) in hands.calls
        assert "reproduced" not in hands.kinds()


# -- the commands --------------------------------------------------------------------------


def _json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


class TestTheCommands:
    def test_queue_mirror_prints_what_it_did(self, project, capsys):
        item = _file("idea", "One")
        capsys.readouterr()
        assert main(["queue", "mirror", "--json"]) == 0
        out = _json(capsys)
        assert out["added"] == [{"entry": "Q1", "ref": item}]
        assert main(["queue", "mirror"]) == 0
        assert "nothing to mirror" in capsys.readouterr().out

    def test_queue_mirror_off_says_so(self, project, capsys):
        _set_chain(project, mirror=False)
        assert main(["queue", "mirror"]) == 0
        assert "oracle.chain.mirror is off" in capsys.readouterr().out

    def test_queue_add_serves_and_link(self, project, capsys):
        item = _file("idea", "Big")
        capsys.readouterr()
        assert main(["queue", "add", "ADR-9 inc 1", "--serves", item, "--json"]) == 0
        assert _json(capsys)["entry"]["serves"] == item
        assert main(["queue", "add", "ADR-9 inc 2"]) == 0
        capsys.readouterr()
        assert main(["queue", "link", "Q2", item, "--json"]) == 0
        assert _json(capsys)["entry"]["serves"] == item
        assert main(["queue", "link", "Q2", "--clear"]) == 0
        assert "no longer serves" in capsys.readouterr().out
        assert main(["queue", "list"]) == 0
        assert f"serves {item}" in capsys.readouterr().out

    def test_queue_link_needs_an_item_or_clear(self, project, capsys):
        assert main(["queue", "add", "x"]) == 0
        assert main(["queue", "link", "Q1"]) == 2

    def test_the_real_hands_mirror_and_route_through_the_domain(self, project, monkeypatch):
        bug = _file("bug", "Broken")
        assert main(["oracle", "projects", "add", str(project)]) == 0
        index = cli_main._project_index()
        key = index.match(str(project)).key
        hands = cli_main._real_chain_hands(index)
        out = hands.mirror(key)
        assert [ref for _, ref in out["added"]] == [bug]
        _domain(project).begin("Q1", bug)
        hands.reproduced(key, "Q1", queue_record.REPRO_OWED)
        assert _domain(project).list().find("Q1").mode == "propose"

    def test_oracle_next_says_a_filed_bug_is_reproduced_first(self, project, capsys):
        bug = _file("bug", "Broken")
        assert main(["oracle", "projects", "add", str(project)]) == 0
        _domain(project).mirror()
        capsys.readouterr()
        assert main(["oracle", "next", "demo"]) == 0
        text = capsys.readouterr().out
        assert bug in text and "reproduce first" in text


# -- the phone ------------------------------------------------------------------------------


class TestThePhone:
    def test_the_entry_line_says_backlog_serves_and_blind(self):
        line = panel._entry_line({"added_by": BACKLOG, "serves": "IDEA_60", "blind": 1})
        assert "Added from the backlog" in line
        assert "Serves IDEA_60" in line
        assert "saw nothing" in line

    def test_the_now_line_says_reproducing(self):
        assert panel._now_line({"state": "running", "reproduce": True}, NOON) == (
            "Reproducing in a window on the Mac")
        assert panel._now_line({"state": "running"}, NOON) == "Running in a window on the Mac"

    def test_the_view_carries_serves_blind_and_reproduce(self):
        q = Queue()
        entry = q.add("ADR-9 inc 1", added_by="user", serves="IDEA_5")
        entry.blind = 1
        assert chainview._entry(entry, 1)["serves"] == "IDEA_5"
        assert chainview._entry(entry, 1)["blind"] == 1
        assert chainview._current({"id": "C1", "reproduce": True})["reproduce"] is True
