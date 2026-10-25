"""Every live host's pid, from Claude Code's per-process records (IDEA_024).

Claude Code keeps ``<claude home>/sessions/<pid>.json`` for each running
process. The oracle joins it with the hosts it already knows, so a host that
never wrote an attention record still has a pid and a liveness. The records
are undocumented Claude Code internals: they are read only when present and
well-formed, and otherwise the scan behaves exactly as before and says the
source was not used.

Two halves, as in the code. The probe half (``ClaudeCodeProbe``) reads
synthetic records under ``tmp_path`` and canned ``ps`` output; nothing here
reads the real ``~/.claude``. The scan half injects a fake probe and a fake
``pid_alive``, so no real process is asked anything but ``ps`` about this
test's own pid.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import test_oracle_scan as world_kit
from test_oracle_scan import (
    HOUR,
    NOW,
    FakeProbe,
    make_project,
    put_attention,
    put_session,
    rows_of,
    tree_fingerprint,
)

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.hosts import claude_code_launch as launch
from invariant.oracle import dashboard
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectIndex
from invariant.oracle.recommend import assess
from invariant.oracle.scan import ROW_HOST, ROW_SESSION, ScanCache, scan_all

LIVE = 41001
OTHER_LIVE = 41002
DEAD = 41003
NOT_CLAUDE = 41004
REUSED = 41005
ASK_FAILS = 41006
START = "2026-10-20T09:00:00"


@dataclass
class Rec:
    """A per-process record as the host hands it over."""

    session_id: str
    pid: int
    name: str | None = None
    cwd: str | None = None
    started: float | None = None
    start: str | None = START


@dataclass
class Facts:
    is_host: bool
    start: str | None = START
    tty: str | None = "ttys004"


#: What ``ps`` would say about each pid the fake knows.
FACTS = {
    LIVE: Facts(True, tty="ttys004"),
    OTHER_LIVE: Facts(True, tty="ttys007"),
    NOT_CLAUDE: Facts(False),
    REUSED: Facts(True, start="2026-10-25T08:00:00"),
}


class RecordProbe(FakeProbe):
    """The scan's fake probe, plus per-process records and ``ps`` facts."""

    def __init__(self, records=None, transcripts=None, fail: Exception | None = None):
        super().__init__(transcripts)
        self.records = list(records or [])
        self.fail = fail
        self.read = 0
        self.facts_asked: list[int] = []

    def host_processes(self):
        self.read += 1
        if self.fail is not None:
            raise self.fail
        return list(self.records)

    def process_facts(self, pid: int):
        self.facts_asked.append(pid)
        if pid == ASK_FAILS:
            raise OSError("ps went away")
        return FACTS.get(pid)


def alive_pids(pid) -> bool:
    return pid in (LIVE, OTHER_LIVE, NOT_CLAUDE, REUSED, ASK_FAILS, world_kit.THE_HOST_PID)


@pytest.fixture
def project(tmp_path):
    """One project with a live session and three hosts known only by transcript."""
    alpha = make_project(tmp_path, "alpha")
    put_session(alpha, id="s1", name="one-s1", state="working", origin=str(alpha.root),
                host_session_id="h-bound", heartbeat=NOW - 60)
    return alpha


def scan(probe, **cache_args):
    cache = ScanCache(probe=probe, pid_alive=alive_pids, **cache_args)
    [snapshot] = scan_all(ProjectIndex(oracle_home()), clock=lambda: NOW, cache=cache)
    return snapshot


def transcripts(**hosts) -> dict:
    return {"alpha": dict(hosts)}


# -- the join ---------------------------------------------------------------------


class TestAHostKnownOnlyByItsRecord:
    def test_an_old_orphan_with_no_attention_record_is_alive_with_its_pid(self, project):
        probe = RecordProbe(
            [Rec("h-quiet", LIVE, name="quiet-one", cwd="/w/alpha", started=NOW - 16 * 24 * HOUR)],
            transcripts(**{"h-quiet": NOW - 10 * 24 * HOUR}),
        )
        row = rows_of(scan(probe), ROW_HOST)["h-quiet"]
        host = row.hosts[0]
        assert host.attention is None
        assert host.alive is True
        assert host.pid == LIVE
        assert host.pid_source == "record"
        assert host.pid_conflict == []
        assert host.launched_name == "quiet-one"
        assert host.cwd == "/w/alpha"
        assert host.started == NOW - 16 * 24 * HOUR
        assert host.process_start == START
        assert host.tty == "ttys004"

    def test_the_bound_host_of_a_session_gets_its_pid_too(self, project):
        probe = RecordProbe([Rec("h-bound", OTHER_LIVE)])
        row = rows_of(scan(probe), ROW_SESSION)["s1"]
        assert row.hosts[0].pid == OTHER_LIVE
        assert row.hosts[0].alive is True
        assert row.hosts[0].tty == "ttys007"

    def test_a_record_for_a_host_the_project_does_not_know_adds_no_row(self, project):
        probe = RecordProbe([Rec("h-elsewhere", LIVE)])
        assert rows_of(scan(probe), ROW_HOST) == {}

    @pytest.mark.parametrize("pid", [DEAD, NOT_CLAUDE, REUSED])
    def test_a_gone_pid_a_non_claude_pid_or_a_reused_pid_is_not_alive(self, project, pid):
        probe = RecordProbe([Rec("h-x", pid)], transcripts(**{"h-x": NOW - HOUR}))
        host = rows_of(scan(probe), ROW_HOST)["h-x"].hosts[0]
        assert host.pid == pid
        assert host.alive is False
        assert host.tty is None

    def test_a_record_without_a_start_is_judged_by_the_name_alone(self, project):
        probe = RecordProbe([Rec("h-x", REUSED, start=None)],
                            transcripts(**{"h-x": NOW - HOUR}))
        assert rows_of(scan(probe), ROW_HOST)["h-x"].hosts[0].alive is True

    def test_a_process_whose_start_ps_cannot_say_is_judged_by_the_name(self, project):
        FACTS[OTHER_LIVE + 100] = Facts(True, start=None)
        try:
            probe = RecordProbe([Rec("h-x", OTHER_LIVE + 100)],
                                transcripts(**{"h-x": NOW - HOUR}))
            cache = ScanCache(probe=probe, pid_alive=lambda pid: True)
            [snapshot] = scan_all(ProjectIndex(oracle_home()), clock=lambda: NOW, cache=cache)
            assert rows_of(snapshot, ROW_HOST)["h-x"].hosts[0].alive is True
        finally:
            del FACTS[OTHER_LIVE + 100]

    @pytest.mark.parametrize("pid", [ASK_FAILS, 41999])
    def test_a_live_pid_ps_cannot_describe_is_unknown(self, project, pid):
        probe = RecordProbe([Rec("h-x", pid)], transcripts(**{"h-x": NOW - HOUR}))
        cache = ScanCache(probe=probe, pid_alive=lambda p: True)
        [snapshot] = scan_all(ProjectIndex(oracle_home()), clock=lambda: NOW, cache=cache)
        host = rows_of(snapshot, ROW_HOST)["h-x"].hosts[0]
        assert host.pid == pid
        assert host.alive is None


class TestTwoSourcesForOnePid:
    def test_agreeing_sources_are_both(self, project):
        put_attention(project, "h-bound", pid=LIVE, turn="running")
        probe = RecordProbe([Rec("h-bound", LIVE)])
        host = rows_of(scan(probe), ROW_SESSION)["s1"].hosts[0]
        assert host.pid == LIVE
        assert host.pid_source == "both"
        assert host.alive is True
        assert host.pid_conflict == []

    def test_the_record_can_say_an_agreed_pid_was_reused(self, project):
        put_attention(project, "h-bound", pid=REUSED, turn="running")
        probe = RecordProbe([Rec("h-bound", REUSED)])
        host = rows_of(scan(probe), ROW_SESSION)["s1"].hosts[0]
        assert host.pid_source == "both"
        assert host.alive is False

    def test_disagreeing_sources_are_shown_and_neither_is_picked(self, project):
        put_attention(project, "h-bound", pid=DEAD, turn="running")
        probe = RecordProbe([Rec("h-bound", LIVE)])
        row = rows_of(scan(probe), ROW_SESSION)["s1"]
        host = row.hosts[0]
        assert host.pid is None
        assert host.alive is None
        assert host.pid_source == "conflict"
        assert host.pid_conflict == [
            {"source": "attention", "pid": DEAD, "alive": False},
            {"source": "record", "pid": LIVE, "alive": True},
        ]
        body = assess([scan(probe)], NOW)["projects"][0]["rows"][0]
        assert "host_gone" not in {s["kind"] for s in body["signals"]}

    def test_without_a_record_the_attention_pid_stands_as_today(self, project):
        put_attention(project, "h-bound", pid=world_kit.THE_HOST_PID, turn="running")
        host = rows_of(scan(RecordProbe([])), ROW_SESSION)["s1"].hosts[0]
        assert host.pid == world_kit.THE_HOST_PID
        assert host.pid_source == "attention"
        assert host.alive is True


class TestSeveralRecordsForOneHost:
    def test_the_one_live_record_wins_over_a_dead_leftover(self, project):
        probe = RecordProbe([Rec("h-bound", DEAD), Rec("h-bound", LIVE)])
        host = rows_of(scan(probe), ROW_SESSION)["s1"].hosts[0]
        assert host.pid == LIVE and host.alive is True
        assert host.pid_source == "record"

    def test_all_dead_reads_the_newest_and_not_alive(self, project):
        probe = RecordProbe([Rec("h-bound", DEAD, started=NOW - 5 * HOUR),
                             Rec("h-bound", DEAD + 50, started=NOW - HOUR)])
        host = rows_of(scan(probe), ROW_SESSION)["s1"].hosts[0]
        assert host.pid == DEAD + 50
        assert host.alive is False

    def test_two_live_records_are_a_conflict(self, project):
        probe = RecordProbe([Rec("h-bound", LIVE), Rec("h-bound", OTHER_LIVE)])
        host = rows_of(scan(probe), ROW_SESSION)["s1"].hosts[0]
        assert host.pid is None and host.alive is None
        assert host.pid_source == "conflict"
        assert [c["pid"] for c in host.pid_conflict] == [LIVE, OTHER_LIVE]


# -- the fallback ---------------------------------------------------------------------


class TestTheSourceNotUsed:
    def expected_rows(self, project):
        put_attention(project, "h-bound", pid=world_kit.THE_HOST_PID, turn="running")
        put_attention(project, "h-old", pid=DEAD, turn="ended")
        return [r.to_dict() for r in scan(FakeProbe(transcripts(**{"h-t": NOW - HOUR}))).rows]

    def strip(self, rows):
        return [
            {**r, "hosts": [{k: v for k, v in h.items() if k != "pid_source"} for h in r["hosts"]]}
            for r in rows
        ]

    def test_a_probe_without_the_read_is_today_and_says_so(self, project):
        before = self.expected_rows(project)
        snapshot = scan(FakeProbe(transcripts(**{"h-t": NOW - HOUR})))
        assert [r.to_dict() for r in snapshot.rows] == before
        assert snapshot.host_processes["used"] is False
        assert "does not read" in snapshot.host_processes["detail"]

    @pytest.mark.parametrize("error", [FileNotFoundError("no sessions directory"),
                                       ValueError("3 records, none in the expected shape"),
                                       PermissionError("denied")])
    def test_a_read_that_fails_is_today_and_says_why(self, project, error):
        before = self.expected_rows(project)
        probe = RecordProbe(fail=error, transcripts=transcripts(**{"h-t": NOW - HOUR}))
        snapshot = scan(probe)
        assert self.strip([r.to_dict() for r in snapshot.rows]) == self.strip(before)
        assert snapshot.host_processes == {"used": False, "records": 0,
                                           "detail": f"not used: {error}"}

    def test_records_in_a_shape_the_scan_cannot_join_are_not_used(self, project):
        probe = RecordProbe([object()])
        snapshot = scan(probe)
        assert snapshot.host_processes["used"] is False
        assert rows_of(snapshot, ROW_SESSION)["s1"].hosts[0].pid is None

    def test_no_probe_at_all_says_so(self, project):
        [snapshot] = scan_all(ProjectIndex(oracle_home()), clock=lambda: NOW, cache=ScanCache())
        assert snapshot.host_processes["used"] is False

    def test_a_used_source_counts_its_records(self, project):
        snapshot = scan(RecordProbe([Rec("a", LIVE), Rec("b", DEAD)]))
        assert snapshot.host_processes == {"used": True, "records": 2,
                                           "detail": "2 per-process records read"}

    def test_the_document_carries_it_per_project(self, project):
        document = assess([scan(RecordProbe(fail=ValueError("odd shape")))], NOW)
        assert document["projects"][0]["host_processes"]["detail"] == "not used: odd shape"


class TestTheCache:
    def test_the_records_are_read_once_per_ttl(self, project):
        ticks = [0.0]
        probe = RecordProbe([Rec("h-bound", LIVE)])
        cache = ScanCache(probe=probe, pid_alive=alive_pids, monotonic=lambda: ticks[0])
        for _ in range(3):
            scan_all(ProjectIndex(oracle_home()), clock=lambda: NOW, cache=cache)
        assert probe.read == 1
        assert probe.facts_asked == [LIVE]
        ticks[0] = 61.0
        scan_all(ProjectIndex(oracle_home()), clock=lambda: NOW, cache=cache)
        assert probe.read == 2

    def test_a_dead_pid_drops_its_facts(self, project):
        alive = {"now": True}
        probe = RecordProbe([Rec("h-bound", LIVE)])
        cache = ScanCache(probe=probe, pid_alive=lambda pid: alive["now"])
        scan_all(ProjectIndex(oracle_home()), clock=lambda: NOW, cache=cache)
        alive["now"] = False
        scan_all(ProjectIndex(oracle_home()), clock=lambda: NOW, cache=cache)
        alive["now"] = True
        scan_all(ProjectIndex(oracle_home()), clock=lambda: NOW, cache=cache)
        assert probe.facts_asked == [LIVE, LIVE]

    def test_a_probe_without_facts_counts_a_live_pid_by_the_name_check(self):
        class NamesOnly(FakeProbe):
            def host_processes(self):
                return []

        cache = ScanCache(probe=NamesOnly(), pid_alive=lambda pid: True)
        assert cache.process_alive(world_kit.THE_HOST_PID, None) == (True, None)
        assert cache.process_alive(7, None) == (False, None)

    def test_no_pid_is_unknown(self):
        assert ScanCache().process_alive(None, None) == (None, None)


class TestNothingIsWritten:
    def test_the_scan_with_records_writes_nothing(self, project, tmp_path):
        records = tmp_path / "claude" / "sessions"
        records.mkdir(parents=True)
        (records / f"{LIVE}.json").write_text(json.dumps(
            {"pid": LIVE, "sessionId": "h-bound", "procStart": "Tue Oct 20 09:00:00 2026"}))
        before = tree_fingerprint(project.state_root, oracle_home(), records)
        probe = launch.ClaudeCodeProbe(home=tmp_path / "claude", facts_of=lambda pid: None)
        scan(probe)
        scan(probe)
        assert tree_fingerprint(project.state_root, oracle_home(), records) == before


# -- the surfaces ---------------------------------------------------------------------


class TestTheSurfaces:
    def test_the_tui_names_the_pid(self):
        assert dashboard.host_status({"alive": True, "pid": 7}) == "alive, pid 7"
        assert dashboard.host_status({"alive": False, "pid": 7}) == "gone, pid 7"
        assert dashboard.host_status({"alive": None, "pid": None}) == "pid unknown"

    def test_the_tui_says_when_the_sources_disagree(self):
        host = {"alive": None, "pid": None, "pid_source": "conflict", "pid_conflict": [
            {"source": "attention", "pid": 3, "alive": False},
            {"source": "record", "pid": 4, "alive": True}]}
        assert dashboard.host_status(host) == (
            "pids disagree: attention 3 (gone), record 4 (alive)"
        )

    def test_an_orphan_host_line_says_its_liveness(self):
        row = {"kind": "host", "hosts": [{"alive": True, "pid": 7}]}
        assert dashboard.stage_text(row) == "orphan host · alive, pid 7"

    def test_oracle_list_says_when_the_source_was_not_used(self, project, monkeypatch,
                                                           capsys):
        probe = RecordProbe(fail=FileNotFoundError("no sessions directory"))
        monkeypatch.setattr(cli_main, "_oracle_cache",
                            lambda: ScanCache(probe=probe, pid_alive=alive_pids))
        monkeypatch.chdir(project.root)
        assert main(["oracle", "list"]) == 0
        out = capsys.readouterr().out
        assert "host process records: not used: no sessions directory" in out

    def test_oracle_list_json_carries_the_pid(self, project, monkeypatch, capsys):
        probe = RecordProbe([Rec("h-quiet", LIVE)],
                            transcripts(**{"h-quiet": NOW - 3 * 24 * HOUR}))
        monkeypatch.setattr(cli_main, "_oracle_cache",
                            lambda: ScanCache(probe=probe, pid_alive=alive_pids))
        monkeypatch.chdir(project.root)
        assert main(["oracle", "list", "--json"]) == 0
        body = json.loads(capsys.readouterr().out)
        [orphan] = [r for r in body["projects"][0]["rows"] if r["kind"] == "host"]
        assert orphan["hosts"][0]["pid"] == LIVE
        assert orphan["hosts"][0]["alive"] is True


# -- the Claude Code side -------------------------------------------------------------


def write_record(home: Path, pid, body) -> Path:
    path = home / "sessions" / f"{pid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if isinstance(body, str) else json.dumps(body), "utf-8")
    return path


GOOD = {
    "pid": 52001, "sessionId": "h-one", "name": "quiet-one", "cwd": "/w/alpha",
    "startedAt": 1_760_000_000_000, "procStart": "Sun Oct  5 09:00:00 2026",
    "pidDomain": "darwin", "kind": "interactive", "status": "idle",
}


class TestTheRecords:
    def probe(self, tmp_path):
        return launch.ClaudeCodeProbe(home=tmp_path, platform=lambda: "darwin")

    def test_a_well_formed_record_is_read(self, tmp_path):
        write_record(tmp_path, 52001, GOOD)
        [record] = self.probe(tmp_path).host_processes()
        assert record == launch.HostProcessRecord(
            session_id="h-one", pid=52001, name="quiet-one", cwd="/w/alpha",
            started=1_760_000_000.0, start="2026-10-05T09:00:00",
        )

    def test_the_key_files_are_never_opened(self, tmp_path, monkeypatch):
        write_record(tmp_path, 52001, GOOD)
        key = tmp_path / "sessions" / "52001.abcdef0123.key"
        key.write_text("secret", "utf-8")
        opened: list[str] = []
        real = Path.read_bytes

        def spy(self):
            opened.append(self.name)
            return real(self)

        monkeypatch.setattr(Path, "read_bytes", spy)
        assert len(self.probe(tmp_path).host_processes()) == 1
        assert opened == ["52001.json"]

    def test_optional_fields_that_are_missing_or_odd_are_none(self, tmp_path):
        write_record(tmp_path, 52002, {"pid": 52002, "sessionId": "h-two", "name": 5,
                                       "startedAt": "soon", "procStart": "whenever",
                                       "cwd": None})
        [record] = self.probe(tmp_path).host_processes()
        assert (record.name, record.cwd, record.started, record.start) == (
            None, None, None, None)

    @pytest.mark.parametrize("body", [
        "{not json",
        "[1, 2]",
        {"sessionId": "h"},
        {"pid": True, "sessionId": "h"},
        {"pid": "52003", "sessionId": "h"},
        {"pid": 52003},
        {"pid": 52003, "sessionId": ""},
        {"pid": 52003, "sessionId": "h", "pidDomain": "linux"},
        {"pid": 99, "sessionId": "h"},
    ])
    def test_a_malformed_record_is_skipped(self, tmp_path, body):
        write_record(tmp_path, 52003, body)
        write_record(tmp_path, 52001, GOOD)
        assert [r.pid for r in self.probe(tmp_path).host_processes()] == [52001]

    def test_an_oversized_record_is_skipped(self, tmp_path):
        write_record(tmp_path, 52004, {**GOOD, "pid": 52004, "pad": "x" * 70_000})
        write_record(tmp_path, 52001, GOOD)
        assert [r.pid for r in self.probe(tmp_path).host_processes()] == [52001]

    def test_a_name_that_is_not_a_pid_is_not_a_record(self, tmp_path):
        write_record(tmp_path, "notes", GOOD)
        (tmp_path / "sessions" / "52005.json").mkdir()
        write_record(tmp_path, 52001, GOOD)
        assert [r.pid for r in self.probe(tmp_path).host_processes()] == [52001]

    def test_an_empty_directory_is_a_used_source_with_nothing_running(self, tmp_path):
        (tmp_path / "sessions").mkdir()
        assert self.probe(tmp_path).host_processes() == []

    def test_no_directory_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="no sessions directory"):
            self.probe(tmp_path).host_processes()

    def test_records_none_of_which_parse_raise(self, tmp_path):
        write_record(tmp_path, 52006, "{")
        write_record(tmp_path, 52007, {"pid": "x"})
        with pytest.raises(ValueError, match="2 per-process records, none in the expected shape"):
            self.probe(tmp_path).host_processes()

    def test_the_real_platform_is_the_default(self, tmp_path):
        assert launch.ClaudeCodeProbe(home=tmp_path).platform() == launch.host_platform()

    def test_the_default_home_is_claude_codes(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        write_record(tmp_path, 52001, GOOD)
        probe = launch.ClaudeCodeProbe(platform=lambda: "darwin")
        assert [r.pid for r in probe.host_processes()] == [52001]


class TestProcessStart:
    @pytest.mark.parametrize(("text", "token"), [
        ("Sun Oct  5 09:00:00 2026", "2026-10-05T09:00:00"),
        ("Sun Oct 25 12:21:28 2026", "2026-10-25T12:21:28"),
        ("  Sun   Oct 25 12:21:28   2026 ", "2026-10-25T12:21:28"),
        ("So 25 Okt 12:21:28 2026", None),
        ("", None),
        (None, None),
        (42, None),
    ])
    def test_the_start_is_one_spelling_or_none(self, text, token):
        assert launch.process_start_token(text) == token


class TestProcessFacts:
    def canned(self, monkeypatch, stdout="", returncode=0, raises=None):
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            if raises:
                raise raises
            return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

        monkeypatch.setattr(launch.subprocess, "run", run)
        return calls

    def test_tty_start_and_name_come_from_one_ps(self, monkeypatch):
        calls = self.canned(
            monkeypatch, "ttys004  Sun Oct  5 09:00:00 2026     /opt/bin/claude\n")
        facts = launch.process_facts(52001)
        assert facts == launch.ProcessFacts(is_host=True, start="2026-10-05T09:00:00",
                                            tty="ttys004")
        assert calls == [["ps", "-o", "tty=,lstart=,comm=", "-p", "52001"]]

    def test_no_terminal_is_no_tty(self, monkeypatch):
        self.canned(monkeypatch, "??       Sun Oct 25 12:21:28 2026     zsh\n")
        assert launch.process_facts(1) == launch.ProcessFacts(
            is_host=False, start="2026-10-25T12:21:28", tty=None)

    def test_an_unparseable_line_still_judges_the_name(self, monkeypatch):
        self.canned(monkeypatch, "ttys001 claude\n")
        assert launch.process_facts(1) == launch.ProcessFacts(
            is_host=True, start=None, tty="ttys001")

    @pytest.mark.parametrize("kwargs", [{"returncode": 1},
                                        {"stdout": "   \n"},
                                        {"raises": OSError("no ps")},
                                        {"raises": subprocess.TimeoutExpired("ps", 5)}])
    def test_no_answer_is_none(self, monkeypatch, kwargs):
        self.canned(monkeypatch, **kwargs)
        assert launch.process_facts(1) is None

    def test_the_real_ps_describes_this_process(self):
        facts = launch.process_facts(os.getpid())
        assert facts is not None
        assert facts.is_host is False
        assert facts.start is not None

    def test_the_probe_asks_through_its_seam(self):
        probe = launch.ClaudeCodeProbe(facts_of=lambda pid: f"facts-{pid}")
        assert probe.process_facts(9) == "facts-9"
