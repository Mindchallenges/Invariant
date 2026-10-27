"""The web page under launchd (IDEA_023 increment 3, ADR-008 D15-D18).

Four layers:

* ``oracle/launchd`` - the second agent, ``dev.invariant.oracle.web``: its
  plist, and the installer keyed by label so both agents share one code path;
* ``oracle/webservice`` - the always-on loop: it waits for a Tailscale
  address instead of exiting, binds when one appears, listens again when the
  addresses change, and reports what it is doing in ``oracle/web.json``;
  plus ``web_status``, the report ``oracle status`` and ``doctor`` read;
* ``oracle/webactions.AuditLog.last`` - the last line of the audit log;
* the CLI: ``oracle install --web``, ``uninstall [--web]``, ``web --wait``,
  ``status``, ``doctor`` and ``web pair``'s port.

Nothing here runs ``launchctl``, binds a socket or asks ``tailscale``: the
installers are recording ones over a temporary directory, the server is the
suite's ``RecordingWeb``, and discovery is a list a test sets.
"""

from __future__ import annotations

import json
import os
import plistlib
import sys
from pathlib import Path

import pytest

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.oracle import launchd, tls, webservice
from invariant.oracle.devices import DeviceStore
from invariant.oracle.home import oracle_home
from invariant.oracle.launchd import (
    LABEL,
    WEB_LABEL,
    LaunchdInstaller,
    RecordingLaunchctl,
    recording_installer,
    search_path,
    web_plist_for,
)
from invariant.oracle.tls import Certificates, Issued, RecordingIssuer
from invariant.oracle.web import WebError
from invariant.oracle.webactions import AUDIT_FILE, AuditLog
from invariant.oracle.webservice import (
    SERVING,
    WAITING,
    WEB_STATE_FILE,
    WebService,
    last_action_text,
    page_ok,
    page_text,
    web_status,
)

PYTHON = "/opt/tools/invariant/bin/python"
TS4 = "100.101.102.103"
TS4B = "100.101.102.104"
TS6 = "fd7a:115c:a1e0::1234:5678"


def last_json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


# -- the second agent ---------------------------------------------------------------


class TestTheWebPlist:
    def plist(self, port: int = 7341, pin_home: bool = False) -> dict:
        return web_plist_for(PYTHON, Path("/srv/h"), port, path="/usr/bin:/bin",
                             pin_home=pin_home)

    def test_it_runs_the_page_waiting_for_the_tailnet(self):
        plist = self.plist(port=7400)
        assert plist["Label"] == WEB_LABEL == "dev.invariant.oracle.web"
        assert plist["ProgramArguments"] == [
            PYTHON, "-m", "invariant", "oracle", "web", "--port", "7400", "--wait",
        ]
        assert "--allow-loopback" not in plist["ProgramArguments"]
        assert plist["RunAtLoad"] is True and plist["KeepAlive"] is True

    def test_it_logs_to_its_own_file_under_the_home(self):
        plist = self.plist()
        assert plist["StandardOutPath"] == "/srv/h/oracle/web.log"
        assert plist["StandardErrorPath"] == plist["StandardOutPath"]

    def test_the_environment_is_the_same_rule_as_the_watchers(self):
        assert self.plist()["EnvironmentVariables"] == {"PATH": "/usr/bin:/bin"}
        assert self.plist(pin_home=True)["EnvironmentVariables"]["INVARIANT_HOME"] == "/srv/h"

    def test_a_relative_interpreter_or_a_bad_port_is_refused(self):
        with pytest.raises(launchd.LaunchdError, match="absolute"):
            web_plist_for("python", Path("/h"), 7341, path="/bin", pin_home=False)
        for port in (0, 70000):
            with pytest.raises(launchd.LaunchdError, match="port"):
                web_plist_for(PYTHON, Path("/h"), port, path="/bin", pin_home=False)

    def test_the_search_path_takes_its_programs_and_extra_directories(self):
        found = {"invariant": "/x/bin/invariant", "tailscale": "/opt/ts/tailscale"}
        path = search_path(found.get, programs=launchd.WEB_PROGRAMS, extra=("/venv/bin",))
        assert path.split(":") == ["/x/bin", "/opt/ts", "/venv/bin", *launchd.SYSTEM_PATH]

    def test_the_pages_search_path_finds_claude_for_a_start_from_the_phone(self):
        # A session started from the page opens Claude from inside this agent
        # (IDEA_030), so the directory claude is in must be on its PATH.
        found = {"invariant": "/x/bin/invariant", "claude": "/c/bin/claude"}
        path = search_path(found.get, programs=launchd.WEB_PROGRAMS)
        assert "claude" in launchd.WEB_PROGRAMS
        assert path.split(":")[:2] == ["/x/bin", "/c/bin"]

    def test_the_watchers_search_path_is_unchanged(self):
        found = {"git": "/g/git", "osascript": "/usr/bin/osascript"}
        assert search_path(found.get).split(":")[:2] == ["/g", "/usr/bin"]


class TestTheInstallerByLabel:
    def test_two_labels_share_a_directory_and_a_launchd(self, tmp_path):
        run = RecordingLaunchctl()
        watcher = recording_installer(tmp_path, run=run)
        page = recording_installer(tmp_path, label=WEB_LABEL, run=run)
        assert page.plist_path == tmp_path / f"{WEB_LABEL}.plist"
        assert page.service == f"gui/501/{WEB_LABEL}"
        page.install(web_plist_for(PYTHON, tmp_path / "h", 7341, path="/bin", pin_home=False))
        assert page.loaded() and not watcher.loaded()
        watcher.install(launchd.plist_for(PYTHON, tmp_path / "h", 5, path="/bin",
                                          pin_home=False))
        page.uninstall()
        assert watcher.loaded() and not page.loaded()
        assert not page.plist_path.exists() and watcher.plist_path.exists()

    def test_status_reads_the_port(self, tmp_path):
        page = recording_installer(tmp_path, label=WEB_LABEL)
        page.install(web_plist_for(PYTHON, tmp_path / "h", 7400, path="/bin", pin_home=False))
        status = page.status()
        assert status.port == 7400 and status.interval is None
        assert status.label == WEB_LABEL
        assert status.to_dict()["port"] == 7400

    def test_the_remedy_names_the_web_install(self, tmp_path):
        page = recording_installer(tmp_path, label=WEB_LABEL)
        page.install(web_plist_for(str(tmp_path / "gone"), tmp_path / "h", 7341,
                                   path="/bin", pin_home=False))
        ok, detail = page.interpreter_check()
        assert ok is False and "invariant oracle install --web" in detail

    def test_the_default_label_is_the_watchers(self, tmp_path):
        assert LaunchdInstaller(tmp_path, 501).label == LABEL


# -- the always-on loop -------------------------------------------------------------


class FakeServer:
    def __init__(self, addresses: list[str], port: int, fail: str | None = None,
                 served=None) -> None:
        self.addresses = addresses
        self.port = port
        self.fail = fail
        self.served = served
        self.background = False
        self.closed = False

    def start(self) -> list[str]:
        if self.fail:
            raise WebError(self.fail)
        if self.served is not None:
            return [f"https://{self.served.name}:{self.port}/"]
        return [f"http://{a}:{self.port}/" for a in self.addresses]

    def serve_in_background(self) -> None:
        self.background = True

    def close(self) -> None:
        self.closed = True


class World:
    """A tailnet a test moves, and the service over it."""

    def __init__(self, home: Path, certificates=None) -> None:
        self.home = home
        self.found: list[str] = []
        self.servers: list[FakeServer] = []
        self.fail: str | None = None
        self.logged: list[str] = []
        self.now = 1_792_640_000.0
        self.service = WebService(
            self.discover, self.serve, home, port=7341, wait=lambda s: True,
            log=self.logged.append, clock=lambda: self.now, pid=4242,
            certificates=certificates,
        )

    def discover(self) -> list[str]:
        if self.found == ["boom"]:
            raise RuntimeError("tailscale hung")
        return list(self.found)

    def serve(self, addresses: list[str], served=None) -> FakeServer:
        server = FakeServer(addresses, 7341, self.fail, served)
        self.servers.append(server)
        return server

    def state(self) -> dict | None:
        path = self.home / WEB_STATE_FILE
        return json.loads(path.read_text()) if path.exists() else None


@pytest.fixture
def world(tmp_path) -> World:
    return World(tmp_path / "home")


class TestTheService:
    def test_with_no_tailnet_it_waits_and_binds_nothing(self, world):
        assert world.service.step() == webservice.WAIT_SECONDS
        assert world.servers == []
        state = world.state()
        assert state["state"] == WAITING and state["pid"] == 4242
        assert state["addresses"] == [] and state["error"] is None
        assert "waiting for one" in world.logged[0]

    def test_waiting_is_logged_once_not_every_pass(self, world):
        world.service.step()
        world.service.step()
        world.service.step()
        assert len(world.logged) == 1

    def test_when_the_tailnet_comes_up_it_binds_then(self, world):
        world.service.step()
        world.found = [TS4]
        assert world.service.step() == webservice.RECHECK_SECONDS
        (server,) = world.servers
        assert server.addresses == [TS4] and server.background
        state = world.state()
        assert state["state"] == SERVING
        assert state["urls"] == [f"http://{TS4}:7341/"] and state["port"] == 7341
        assert "serving the oracle on" in world.logged[-1]

    def test_it_never_binds_an_address_outside_the_tailnet(self, world):
        world.found = ["192.168.1.20", "0.0.0.0", "127.0.0.1"]
        world.service.step()
        assert world.servers == []
        assert world.state()["state"] == WAITING

    def test_steady_addresses_keep_the_listener(self, world):
        world.found = [TS4]
        world.service.step()
        world.service.step()
        assert len(world.servers) == 1 and not world.servers[0].closed

    def test_changed_addresses_listen_again(self, world):
        world.found = [TS4]
        world.service.step()
        world.found = [TS4B, TS6]
        world.service.step()
        first, second = world.servers
        assert first.closed and not second.closed
        assert second.addresses == [TS4B, TS6]
        assert any("changed" in line for line in world.logged)

    def test_a_tailnet_that_goes_away_closes_and_waits(self, world):
        world.found = [TS4]
        world.service.step()
        world.found = []
        assert world.service.step() == webservice.WAIT_SECONDS
        assert world.servers[0].closed
        assert world.state()["state"] == WAITING
        assert any("went away" in line for line in world.logged)

    def test_a_bind_failure_waits_and_retries_without_exiting(self, world):
        world.found = [TS4]
        world.fail = "cannot listen on 100.101.102.103 port 7341: in use"
        assert world.service.step() == webservice.WAIT_SECONDS
        assert world.service.step() == webservice.WAIT_SECONDS
        state = world.state()
        assert state["state"] == WAITING and "in use" in state["error"]
        assert sum("in use" in line for line in world.logged) == 1
        world.fail = None
        world.service.step()
        assert world.state()["state"] == SERVING and world.state()["error"] is None

    def test_a_discovery_that_raises_is_no_tailnet(self, world):
        world.found = ["boom"]
        assert world.service.step() == webservice.WAIT_SECONDS
        assert world.state()["state"] == WAITING
        assert any("tailscale hung" in line for line in world.logged)

    def test_run_loops_until_wait_says_stop_and_then_cleans_up(self, world):
        waits: list[float] = []
        plan = [lambda: world.found.append(TS4), lambda: None]

        def wait(seconds: float) -> bool:
            waits.append(seconds)
            if plan:
                plan.pop(0)()
                return False
            return True

        world.service.wait = wait
        world.service.run()
        assert waits == [webservice.WAIT_SECONDS, webservice.RECHECK_SECONDS,
                         webservice.RECHECK_SECONDS]
        assert world.servers[0].closed
        assert world.state() is None

    def test_close_leaves_another_processes_report(self, world):
        world.service.step()
        path = world.home / WEB_STATE_FILE
        body = json.loads(path.read_text())
        body["pid"] = 999
        path.write_text(json.dumps(body))
        world.service.close()
        assert path.exists()

    def test_a_report_that_cannot_be_written_does_not_stop_the_page(self, world, monkeypatch):
        def broken(*_a, **_k):
            raise webservice.StateError("disk full")

        monkeypatch.setattr(world.service.store, "write", broken)
        world.found = [TS4]
        world.service.step()
        assert world.servers[0].background
        assert any("disk full" in line for line in world.logged)


# -- what status and doctor read -----------------------------------------------------


class TestWebStatus:
    def test_no_report(self, tmp_path):
        status = web_status(tmp_path, alive=lambda pid: True)
        assert status["exists"] is False and status["running"] is False
        assert status["devices"] == 0 and status["last_action"] is None
        assert page_ok(status) is False
        assert "no report" in page_text(status)

    def test_a_serving_report_from_a_live_process(self, world):
        world.found = [TS4]
        world.service.step()
        status = web_status(world.home, alive=lambda pid: pid == 4242)
        assert status["running"] is True and status["state"] == SERVING
        assert status["urls"] == [f"http://{TS4}:7341/"] and status["port"] == 7341
        assert page_ok(status) is True
        assert page_text(status).startswith(f"serving http://{TS4}:7341/")

    def test_a_waiting_report_is_ok_and_says_so(self, world):
        world.service.step()
        status = web_status(world.home, alive=lambda pid: True)
        assert page_ok(status) is True
        assert "waiting for a Tailscale address" in page_text(status)

    def test_a_waiting_report_with_an_error_is_not_ok(self, world):
        world.found = [TS4]
        world.fail = "cannot listen: in use"
        world.service.step()
        status = web_status(world.home, alive=lambda pid: True)
        assert page_ok(status) is False and "in use" in page_text(status)

    def test_a_report_whose_process_is_gone(self, world):
        world.found = [TS4]
        world.service.step()
        status = web_status(world.home, alive=lambda pid: False)
        assert status["running"] is False and page_ok(status) is False
        assert "NOT running" in page_text(status) and "4242" in page_text(status)

    def test_an_unreadable_report(self, tmp_path):
        path = tmp_path / WEB_STATE_FILE
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        status = web_status(tmp_path, alive=lambda pid: True)
        assert status["exists"] is True and status["unreadable"]
        assert page_ok(status) is False and "cannot read" in page_text(status)

    def test_a_report_of_the_wrong_shape(self, tmp_path):
        path = tmp_path / WEB_STATE_FILE
        path.parent.mkdir(parents=True)
        path.write_text("[1, 2]")
        status = web_status(tmp_path, alive=lambda pid: True)
        assert page_ok(status) is False and "cannot read" in page_text(status)

    def test_it_counts_the_paired_devices_and_reads_the_last_action(self, tmp_path):
        store = DeviceStore(tmp_path)
        store.redeem(store.issue("phone", ttl=60).code)
        store.redeem(store.issue("tablet", ttl=60).code)
        log = AuditLog(tmp_path, clock=lambda: 1_792_640_000.0)
        log.record({"event": "start", "argv": ["invariant", "session", "merge"], "cwd": "/p"})
        log.record({"event": "done", "argv": ["invariant", "session", "merge"], "cwd": "/p",
                    "exit": 0, "ok": True})
        status = web_status(tmp_path, alive=lambda pid: True)
        assert status["devices"] == 2
        assert status["last_action"]["event"] == "done"
        text = last_action_text(status["last_action"])
        assert "invariant session merge" in text and "exit 0" in text

    def test_an_unreadable_device_store_is_reported_not_raised(self, tmp_path):
        path = tmp_path / "web" / "devices.json"
        path.parent.mkdir(parents=True)
        path.write_text("{broken")
        status = web_status(tmp_path, alive=lambda pid: True)
        assert status["devices"] is None and status["devices_error"]

    def test_last_action_text(self):
        assert last_action_text(None) == "none yet"
        start = {"at": "2026-10-22T10:00:00Z", "event": "start", "argv": ["invariant", "x"]}
        assert "still running" in last_action_text(start)
        terminal = {"at": "t", "event": "done", "argv": ["invariant", "x"], "exit": None,
                    "ok": True}
        assert "launched" in last_action_text(terminal)
        failed = {"at": "t", "event": "done", "argv": ["invariant", "x"], "exit": 3,
                  "ok": False}
        assert "exit 3" in last_action_text(failed)


class TestTheAuditLogTail:
    def test_no_log_is_none(self, tmp_path):
        assert AuditLog(tmp_path).last() is None

    def test_it_is_the_last_whole_line(self, tmp_path):
        log = AuditLog(tmp_path, clock=lambda: 0.0)
        for n in range(500):
            log.record({"event": "start", "job": str(n)})
        assert log.last()["job"] == "499"

    def test_a_torn_last_line_falls_back_to_the_one_before(self, tmp_path):
        log = AuditLog(tmp_path, clock=lambda: 0.0)
        log.record({"event": "done", "job": "a"})
        with (tmp_path / AUDIT_FILE).open("a") as handle:
            handle.write('{"event": "start", "jo')
        assert log.last()["job"] == "a"

    def test_an_unreadable_log_is_none(self, tmp_path):
        (tmp_path / AUDIT_FILE).mkdir(parents=True)
        assert AuditLog(tmp_path).last() is None


# -- the commands ---------------------------------------------------------------------


@pytest.fixture
def web_agent(_no_real_web_launchd) -> LaunchdInstaller:
    return _no_real_web_launchd


class TestTheSeams:
    def test_the_suite_replaces_the_web_installer(self, web_agent, _no_real_launchd):
        assert cli_main._oracle_web_installer() is web_agent
        assert web_agent.label == WEB_LABEL
        assert web_agent.run is _no_real_launchd.run
        assert Path.home() not in web_agent.plist_path.parents

    def test_the_real_web_installer(self):
        real = cli_main._real_oracle_web_installer()
        assert real.label == WEB_LABEL and real.agents_dir == launchd.agents_dir()
        assert real.run is launchd.run_launchctl

    def test_the_wait_seam_is_the_recorder(self, oracle_web):
        assert cli_main._oracle_web_wait() == oracle_web.wait

    def test_the_real_wait_sleeps_and_never_stops(self, monkeypatch):
        slept = []
        monkeypatch.setattr(cli_main.time, "sleep", slept.append)
        assert cli_main._real_oracle_web_wait()(2.5) is False
        assert slept == [2.5]


class TestInstallWeb:
    def test_it_installs_the_page_agent_only(self, web_agent, _no_real_launchd, capsys):
        assert main(["oracle", "install", "--web", "--port", "7400", "--json"]) == 0
        body = last_json(capsys)
        plist = plistlib.loads(web_agent.plist_path.read_bytes())
        assert body["plist"] == plist
        assert plist["ProgramArguments"] == [
            os.path.abspath(sys.executable), "-m", "invariant", "oracle", "web",
            "--port", "7400", "--wait",
        ]
        assert plist["StandardOutPath"] == str(oracle_home() / "oracle" / "web.log")
        path = plist["EnvironmentVariables"]["PATH"].split(":")
        assert os.path.dirname(os.path.abspath(sys.executable)) in path
        assert web_agent.loaded()
        assert not _no_real_launchd.plist_path.exists()

    def test_the_text_names_the_log_and_the_next_steps(self, web_agent, capsys):
        code, out = main(["oracle", "install", "--web"]), capsys.readouterr().out
        assert code == 0
        assert "installed the oracle web page agent" in out
        assert "web.log" in out and "invariant oracle web pair" in out
        assert "the watcher agent is not installed" in out

    def test_with_the_watcher_installed_there_is_no_note(self, web_agent, capsys):
        main(["oracle", "install"])
        capsys.readouterr()
        main(["oracle", "install", "--web"])
        assert "watcher agent is not installed" not in capsys.readouterr().out

    def test_a_second_install_replaces_it(self, web_agent, capsys):
        main(["oracle", "install", "--web"])
        capsys.readouterr()
        main(["oracle", "install", "--web", "--port", "7401"])
        assert "replaced the oracle web page agent" in capsys.readouterr().out
        assert web_agent.status().port == 7401

    def test_dry_run(self, web_agent, capsys):
        assert main(["oracle", "install", "--web", "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "would write" in out and WEB_LABEL in out
        assert not web_agent.plist_path.exists()

    @pytest.mark.parametrize("argv", [
        ["oracle", "install", "--port", "7400"],
        ["oracle", "install", "--web", "--interval", "3"],
        ["oracle", "install", "--web", "--port", "0"],
    ])
    def test_flags_that_do_not_fit_are_usage_errors(self, web_agent, argv, capsys):
        assert main(argv) == 2
        assert not web_agent.plist_path.exists()

    def test_the_watchers_install_is_unchanged_and_leaves_the_page(self, web_agent,
                                                                   _no_real_launchd, capsys):
        main(["oracle", "install", "--web"])
        main(["oracle", "install"])
        assert web_agent.loaded() and _no_real_launchd.loaded()
        assert plistlib.loads(_no_real_launchd.plist_path.read_bytes())[
            "ProgramArguments"][4] == "watch"


class TestUninstallWeb:
    def serving(self) -> Path:
        path = oracle_home() / WEB_STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "state": SERVING, "pid": 4242}))
        return path

    def test_uninstall_web_removes_only_the_page(self, web_agent, _no_real_launchd, capsys):
        main(["oracle", "install"])
        main(["oracle", "install", "--web"])
        report = self.serving()
        capsys.readouterr()
        assert main(["oracle", "uninstall", "--web", "--json"]) == 0
        body = last_json(capsys)
        assert body["removed"] is True and body["plist_path"] == str(web_agent.plist_path)
        assert not web_agent.plist_path.exists() and not web_agent.loaded()
        assert not report.exists()
        assert _no_real_launchd.loaded()

    def test_plain_uninstall_removes_both(self, web_agent, _no_real_launchd, capsys):
        main(["oracle", "install"])
        main(["oracle", "install", "--web"])
        report = self.serving()
        capsys.readouterr()
        assert main(["oracle", "uninstall", "--json"]) == 0
        body = last_json(capsys)
        assert body["removed"] is True and body["web"]["removed"] is True
        assert not web_agent.loaded() and not _no_real_launchd.loaded()
        assert not web_agent.plist_path.exists() and not report.exists()

    def test_plain_uninstall_text_names_both(self, web_agent, capsys):
        main(["oracle", "install"])
        main(["oracle", "install", "--web"])
        capsys.readouterr()
        main(["oracle", "uninstall"])
        out = capsys.readouterr().out
        assert str(web_agent.plist_path) in out and LABEL in out

    def test_nothing_installed_is_nothing_to_do(self, web_agent, capsys):
        assert main(["oracle", "uninstall", "--web"]) == 0
        assert "not installed; nothing to do" in capsys.readouterr().out
        assert main(["oracle", "uninstall"]) == 0
        out = capsys.readouterr().out
        assert out.count("nothing to do") == 2

    def test_dry_run_touches_nothing(self, web_agent, capsys):
        main(["oracle", "install", "--web"])
        report = self.serving()
        capsys.readouterr()
        assert main(["oracle", "uninstall", "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert f"bootout gui/501/{WEB_LABEL}" in out and "would remove" in out
        assert web_agent.loaded() and report.exists()


class TestWebMode:
    def test_wait_with_no_tailnet_waits_instead_of_refusing(self, oracle_web, capsys):
        code = main(["oracle", "web", "--wait"])
        out = capsys.readouterr().out
        assert code == 0
        assert oracle_web.servers == []
        assert oracle_web.waits == [webservice.WAIT_SECONDS]
        assert "waiting for one" in out

    def test_it_binds_when_the_tailnet_comes_up(self, oracle_web, capsys):
        oracle_web.script = [lambda: oracle_web.addresses.append(TS4)]
        assert main(["oracle", "web", "--wait", "--port", "7400"]) == 0
        (server,) = oracle_web.servers
        assert server.addresses == [TS4] and server.port == 7400
        assert server.background and server.closed
        assert server.app.bound == [TS4] and server.app.remote is not None
        assert f"http://{TS4}:7400/" in capsys.readouterr().out
        assert not (oracle_home() / WEB_STATE_FILE).exists()

    def test_a_rebind_keeps_one_set_of_actions(self, oracle_web, capsys):
        oracle_web.addresses = [TS4]
        oracle_web.script = [lambda: oracle_web.addresses.__setitem__(0, TS4B)]
        main(["oracle", "web", "--wait"])
        first, second = oracle_web.servers
        assert first.app.remote is second.app.remote
        assert second.app.bound == [TS4B]

    def test_the_report_is_written_while_it_runs(self, oracle_web, capsys):
        seen = []
        oracle_web.addresses = [TS4]
        oracle_web.script = [
            lambda: seen.append(json.loads((oracle_home() / WEB_STATE_FILE).read_text()))
        ]
        main(["oracle", "web", "--wait"])
        assert seen[0]["state"] == SERVING and seen[0]["pid"] == os.getpid()

    def test_an_interrupt_stops_it_cleanly(self, oracle_web, capsys):
        oracle_web.addresses = [TS4]

        def interrupt():
            raise KeyboardInterrupt

        oracle_web.script = [interrupt]
        assert main(["oracle", "web", "--wait"]) == 0
        assert oracle_web.servers[0].closed

    def test_wait_and_allow_loopback_do_not_mix(self, oracle_web, capsys):
        assert main(["oracle", "web", "--wait", "--allow-loopback"]) == 2
        assert oracle_web.servers == []

    def test_without_wait_it_still_refuses(self, oracle_web, capsys):
        assert main(["oracle", "web"]) == 1


class TestStatusWeb:
    def report(self, state: str = SERVING, pid: int | None = None, error=None) -> None:
        path = oracle_home() / WEB_STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "version": 1, "state": state, "pid": pid or os.getpid(), "port": 7400,
            "addresses": [TS4], "urls": [f"http://{TS4}:7400/"], "since": "2026-10-22T10:00:00Z",
            "error": error,
        }))

    def test_not_installed(self, web_agent, capsys):
        assert main(["oracle", "status", "--json"]) == 0
        body = last_json(capsys)
        assert body["web"]["agent"]["installed"] is False
        assert body["web"]["page"]["exists"] is False
        assert main(["oracle", "status"]) == 0
        assert "invariant oracle install --web" in capsys.readouterr().out

    def test_installed_and_serving(self, web_agent, capsys):
        main(["oracle", "install", "--web", "--port", "7400"])
        self.report()
        store = DeviceStore(oracle_home())
        store.redeem(store.issue("phone", ttl=60).code)
        AuditLog(oracle_home(), clock=lambda: 1_792_640_000.0).record(
            {"event": "done", "argv": ["invariant", "session", "review"], "exit": 0,
             "ok": True})
        capsys.readouterr()
        main(["oracle", "status", "--json"])
        body = last_json(capsys)["web"]
        assert body["agent"]["loaded"] is True and body["agent"]["port"] == 7400
        assert body["agent"]["interpreter_exists"] is True
        assert body["page"]["running"] is True and body["page"]["state"] == SERVING
        assert body["page"]["devices"] == 1
        assert body["page"]["last_action"]["argv"][1:] == ["session", "review"]
        main(["oracle", "status"])
        out = capsys.readouterr().out
        assert "web agent" in out and "installed, loaded" in out
        assert f"serving http://{TS4}:7400/" in out
        assert "1 paired device" in out and "invariant session review" in out

    def test_a_moved_interpreter_and_a_dead_server(self, web_agent, tmp_path, capsys):
        web_agent.install(web_plist_for(str(tmp_path / "gone"), oracle_home(), 7341,
                                        path="/bin", pin_home=False))
        self.report(pid=2**22 + 12345)
        main(["oracle", "status"])
        out = capsys.readouterr().out
        assert "MISSING" in out and "NOT running" in out

    def test_an_unreadable_web_plist(self, web_agent, capsys):
        web_agent.plist_path.parent.mkdir(parents=True, exist_ok=True)
        web_agent.plist_path.write_text("not a plist")
        main(["oracle", "status"])
        assert "cannot read" in capsys.readouterr().out


class TestDoctorWeb:
    def rows(self, capsys) -> dict[str, dict]:
        body = json.loads(capsys.readouterr().out)
        return {c["check"]: c for c in body["checks"]}

    def test_no_rows_without_the_web_plist(self, project, monkeypatch, capsys):
        monkeypatch.chdir(project)
        main(["doctor", "--json"])
        rows = self.rows(capsys)
        assert "oracle web interpreter" not in rows and "oracle web page" not in rows

    def test_a_serving_page_passes_and_says_where(self, project, monkeypatch, capsys,
                                                  web_agent):
        web_agent.install(web_plist_for(sys.executable, oracle_home(), 7341,
                                        path="/bin", pin_home=False))
        TestStatusWeb().report()
        monkeypatch.chdir(project)
        main(["doctor", "--json"])
        rows = self.rows(capsys)
        assert rows["oracle web interpreter"]["ok"] is True
        page = rows["oracle web page"]
        assert page["ok"] is True
        assert f"http://{TS4}:7400/" in page["detail"]
        assert "0 paired devices" in page["detail"] and "last action none yet" in page["detail"]

    def test_a_page_that_is_not_running_fails(self, project, monkeypatch, capsys, web_agent):
        web_agent.install(web_plist_for(sys.executable, oracle_home(), 7341,
                                        path="/bin", pin_home=False))
        monkeypatch.chdir(project)
        assert main(["doctor", "--json"]) == 1
        page = self.rows(capsys)["oracle web page"]
        assert page["ok"] is False and "no report" in page["detail"]

    def test_the_check_asks_launchd_nothing(self, project, monkeypatch, capsys, web_agent):
        web_agent.install(web_plist_for(sys.executable, oracle_home(), 7341,
                                        path="/bin", pin_home=False))
        web_agent.run.calls.clear()
        monkeypatch.chdir(project)
        main(["doctor", "--json"])
        assert web_agent.run.calls == []


class TestPairPort:
    def test_pair_names_the_port_the_page_serves_on(self, oracle_web, capsys):
        oracle_web.addresses = [TS4]
        TestStatusWeb().report()
        main(["oracle", "web", "pair", "--json"])
        assert last_json(capsys)["links"][0].startswith(f"http://{TS4}:7400/pair?code=")

    def test_else_the_port_the_agent_was_installed_with(self, oracle_web, web_agent, capsys):
        oracle_web.addresses = [TS4]
        main(["oracle", "install", "--web", "--port", "7402"])
        capsys.readouterr()
        main(["oracle", "web", "pair", "--json"])
        assert last_json(capsys)["links"][0].startswith(f"http://{TS4}:7402/pair?code=")

    def test_an_explicit_port_wins(self, oracle_web, web_agent, capsys):
        oracle_web.addresses = [TS4]
        main(["oracle", "install", "--web", "--port", "7402"])
        capsys.readouterr()
        main(["oracle", "web", "pair", "--port", "7403", "--json"])
        assert last_json(capsys)["links"][0].startswith(f"http://{TS4}:7403/pair?code=")

    def test_with_nothing_installed_the_default_port(self, oracle_web, capsys):
        oracle_web.addresses = [TS4]
        main(["oracle", "web", "pair", "--json"])
        assert last_json(capsys)["links"][0].startswith(f"http://{TS4}:7341/pair?code=")

    def test_the_text_mentions_the_agent(self, oracle_web, capsys):
        oracle_web.addresses = [TS4]
        main(["oracle", "web", "pair"])
        assert "oracle install --web" in capsys.readouterr().out


# -- IDEA_026: the certificate, inside the loop ------------------------------------

TLS_FIXTURES = Path(__file__).parent / "fixtures" / "tls"
TS_NAME = "mac.example.ts.net"
#: When fixture a expires (``openssl x509 -enddate``), and a moment early in its life.
A_EXPIRES = 2_108_058_344.0
EARLY = 1_800_000_000.0


def fixture_issued(which: str = "a", name: str = TS_NAME) -> Issued:
    return Issued(name, (TLS_FIXTURES / f"{which}.crt").read_text(),
                  (TLS_FIXTURES / f"{which}.key").read_text())


class TlsWorld(World):
    def __init__(self, home: Path, issued: Issued | None = None) -> None:
        self.issuer = RecordingIssuer(issued)
        self.certificates = Certificates(home, self.issuer, clock=lambda: self.now)
        super().__init__(home, certificates=self.certificates)
        self.now = EARLY


@pytest.fixture
def tls_world(tmp_path) -> TlsWorld:
    return TlsWorld(tmp_path / "home", fixture_issued("a"))


def test_the_fixture_expiry_is_what_the_certificate_says():
    assert tls.cert_info((TLS_FIXTURES / "a.crt").read_text()).not_after == A_EXPIRES


class TestTheServiceOverTls:
    def test_it_serves_https_from_the_start_and_reports_the_expiry(self, tls_world):
        tls_world.found = [TS4]
        assert tls_world.service.step() == webservice.RECHECK_SECONDS
        (server,) = tls_world.servers
        assert server.served is not None and server.served.name == TS_NAME
        state = tls_world.state()
        assert state["state"] == SERVING and state["scheme"] == "https"
        assert state["urls"] == [f"https://{TS_NAME}:7341/"]
        assert state["certificate"] == {
            "name": TS_NAME, "not_after": A_EXPIRES, "expires": "2036-10-19T19:45:44Z",
            "error": None,
        }
        assert any("certificate" in line and TS_NAME in line for line in tls_world.logged)

    def test_no_tailnet_asks_tailscale_for_nothing(self, tls_world):
        tls_world.service.step()
        assert tls_world.issuer.calls == 0

    def test_with_no_certificate_it_serves_http_and_says_why(self, tmp_path):
        world = TlsWorld(tmp_path / "home", None)
        world.found = [TS4]
        world.service.step()
        (server,) = world.servers
        assert server.served is None
        state = world.state()
        assert state["scheme"] == "http" and state["urls"] == [f"http://{TS4}:7341/"]
        assert state["certificate"]["name"] is None
        assert "no certificate in the test suite" in state["certificate"]["error"]
        assert sum("plain http" in line for line in world.logged) == 1
        world.service.step()
        assert sum("plain http" in line for line in world.logged) == 1

    def test_when_a_certificate_arrives_it_listens_again_over_https(self, tmp_path):
        world = TlsWorld(tmp_path / "home", None)
        world.found = [TS4]
        world.service.step()
        world.issuer.issued = fixture_issued("a")
        world.now += tls.RETRY_MISSING
        world.service.step()
        first, second = world.servers
        assert first.closed and first.served is None
        assert not second.closed and second.served.name == TS_NAME
        assert world.state()["scheme"] == "https"
        assert any("https" in line and "listening again" in line for line in world.logged)

    def test_a_renewal_swaps_the_certificate_without_listening_again(self, tls_world):
        tls_world.found = [TS4]
        tls_world.service.step()
        (server,) = tls_world.servers
        old_context = server.served.context
        tls_world.issuer.issued = fixture_issued("b")
        tls_world.now = A_EXPIRES - tls.RENEW_BEFORE + 60
        assert tls_world.service.step() == webservice.RECHECK_SECONDS
        assert tls_world.servers == [server] and not server.closed
        assert server.served.context is not old_context
        assert server.served.not_after > A_EXPIRES
        state = tls_world.state()
        assert state["certificate"]["not_after"] == server.served.not_after
        assert any(line.startswith("renewed the certificate") for line in tls_world.logged)

    def test_a_steady_certificate_writes_nothing_new(self, tls_world):
        tls_world.found = [TS4]
        tls_world.service.step()
        before = list(tls_world.logged)
        tls_world.service.step()
        assert tls_world.logged == before and tls_world.issuer.calls == 1

    def test_a_failed_renewal_keeps_https_and_reports_the_error(self, tls_world):
        tls_world.found = [TS4]
        tls_world.service.step()
        tls_world.issuer.issued = None
        tls_world.now = A_EXPIRES - 3 * 86400
        tls_world.service.step()
        (server,) = tls_world.servers
        assert not server.closed
        state = tls_world.state()
        assert state["scheme"] == "https"
        assert "no certificate in the test suite" in state["certificate"]["error"]

    def test_an_expired_certificate_falls_back_to_http(self, tls_world):
        tls_world.found = [TS4]
        tls_world.service.step()
        tls_world.issuer.issued = None
        tls_world.now = A_EXPIRES + 1
        tls_world.service.step()
        first, second = tls_world.servers
        assert first.closed and second.served is None
        assert tls_world.state()["scheme"] == "http"
        assert any("plain http" in line for line in tls_world.logged)

    def test_a_renamed_machine_listens_again_under_the_new_name(self, tls_world):
        tls_world.found = [TS4]
        tls_world.service.step()
        tls_world.issuer.issued = fixture_issued("c", name="renamed.example.ts.net")
        tls_world.now = A_EXPIRES - tls.RENEW_BEFORE + 60
        tls_world.service.step()
        first, second = tls_world.servers
        assert first.closed and second.served.name == "renamed.example.ts.net"

    def test_a_context_that_will_not_build_is_http(self, tls_world, monkeypatch):
        def broken(path):
            raise tls.CertError("cannot load: broken")

        monkeypatch.setattr(tls, "context_for", broken)
        tls_world.found = [TS4]
        tls_world.service.step()
        assert tls_world.servers[0].served is None
        assert "broken" in tls_world.state()["certificate"]["error"]


class TestCertificateStatus:
    def test_serving_https_with_time_to_spare(self, tls_world):
        tls_world.found = [TS4]
        tls_world.service.step()
        status = web_status(tls_world.home, alive=lambda pid: True)
        assert status["scheme"] == "https"
        assert status["certificate"]["name"] == TS_NAME
        assert webservice.cert_ok(status, EARLY) is True
        text = webservice.cert_text(status, EARLY)
        assert TS_NAME in text and "expires 2036-10-19T19:45:44Z" in text and "days" in text
        assert "renew" not in text

    def test_inside_the_renewal_window_it_says_so(self, tls_world):
        tls_world.found = [TS4]
        tls_world.service.step()
        status = web_status(tls_world.home, alive=lambda pid: True)
        now = A_EXPIRES - 20 * 86400
        assert webservice.cert_ok(status, now) is True
        assert "renewing" in webservice.cert_text(status, now)

    def test_close_to_expiry_with_a_failing_renewal_fails(self, tls_world):
        tls_world.found = [TS4]
        tls_world.service.step()
        tls_world.issuer.issued = None
        tls_world.now = A_EXPIRES - 3 * 86400
        tls_world.service.step()
        status = web_status(tls_world.home, alive=lambda pid: True)
        assert webservice.cert_ok(status, tls_world.now) is False
        text = webservice.cert_text(status, tls_world.now)
        assert "last renewal failed" in text and "no certificate in the test suite" in text

    def test_plain_http_fails_and_says_why(self, tmp_path):
        world = TlsWorld(tmp_path / "home", None)
        world.found = [TS4]
        world.service.step()
        status = web_status(world.home, alive=lambda pid: True)
        assert webservice.cert_ok(status, EARLY) is False
        text = webservice.cert_text(status, EARLY)
        assert text.startswith("NONE: serving plain http") and "test suite" in text

    def test_a_report_without_a_certificate(self, world):
        world.found = [TS4]
        world.service.step()
        status = web_status(world.home, alive=lambda pid: True)
        assert status["certificate"] is None
        assert webservice.cert_text(status, EARLY) == "not reported"
        assert webservice.cert_ok(status, EARLY) is False

    def test_an_expired_one_in_the_report(self, tls_world):
        tls_world.found = [TS4]
        tls_world.service.step()
        status = web_status(tls_world.home, alive=lambda pid: True)
        assert "EXPIRED" in webservice.cert_text(status, A_EXPIRES + 1)


class TestCertificateCommands:
    def report(self, certificate: dict | None, scheme: str = "https") -> None:
        path = oracle_home() / WEB_STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "version": 1, "state": SERVING, "pid": os.getpid(), "port": 7400,
            "addresses": [TS4], "urls": [f"https://{TS_NAME}:7400/"],
            "since": "2026-10-22T10:00:00Z", "error": None, "scheme": scheme,
            "certificate": certificate,
        }))

    def test_status_prints_the_certificate_line(self, web_agent, capsys, monkeypatch):
        main(["oracle", "install", "--web", "--port", "7400"])
        self.report({"name": TS_NAME, "not_after": A_EXPIRES,
                     "expires": "2036-10-19T19:45:44Z", "error": None})
        monkeypatch.setattr(cli_main.time, "time", lambda: EARLY)
        capsys.readouterr()
        main(["oracle", "status"])
        out = capsys.readouterr().out
        assert "certificate" in out and "expires 2036-10-19T19:45:44Z" in out
        main(["oracle", "status", "--json"])
        assert last_json(capsys)["web"]["page"]["certificate"]["name"] == TS_NAME

    def test_doctor_has_a_certificate_row(self, project, monkeypatch, capsys, web_agent):
        web_agent.install(web_plist_for(sys.executable, oracle_home(), 7341,
                                        path="/bin", pin_home=False))
        self.report({"name": None, "not_after": None, "expires": None,
                     "error": "HTTPS Certificates are off"}, scheme="http")
        monkeypatch.chdir(project)
        assert main(["doctor", "--json"]) == 1
        rows = TestDoctorWeb().rows(capsys)
        row = rows["oracle web certificate"]
        assert row["ok"] is False and "HTTPS Certificates are off" in row["detail"]

    def test_doctor_passes_a_healthy_certificate(self, project, monkeypatch, capsys,
                                                 web_agent):
        web_agent.install(web_plist_for(sys.executable, oracle_home(), 7341,
                                        path="/bin", pin_home=False))
        self.report({"name": TS_NAME, "not_after": A_EXPIRES,
                     "expires": "2036-10-19T19:45:44Z", "error": None})
        monkeypatch.setattr(cli_main.time, "time", lambda: EARLY)
        monkeypatch.chdir(project)
        main(["doctor", "--json"])
        assert TestDoctorWeb().rows(capsys)["oracle web certificate"]["ok"] is True

    def test_doctor_has_no_certificate_row_without_a_report(self, project, monkeypatch,
                                                            capsys, web_agent):
        web_agent.install(web_plist_for(sys.executable, oracle_home(), 7341,
                                        path="/bin", pin_home=False))
        monkeypatch.chdir(project)
        main(["doctor", "--json"])
        assert "oracle web certificate" not in TestDoctorWeb().rows(capsys)

    def test_wait_mode_serves_https_through_the_seam(self, oracle_web, oracle_certificates,
                                                     capsys):
        oracle_certificates.issued = fixture_issued("a")
        oracle_web.addresses = [TS4]
        seen = []
        oracle_web.script = [
            lambda: seen.append(json.loads((oracle_home() / WEB_STATE_FILE).read_text()))
        ]
        assert main(["oracle", "web", "--wait", "--port", "7400"]) == 0
        (server,) = oracle_web.servers
        assert server.served.name == TS_NAME and server.app.served is server.served
        assert seen[0]["scheme"] == "https"
        assert f"https://{TS_NAME}:7400/" in capsys.readouterr().out

    def test_wait_mode_without_a_certificate_warns_on_the_page(self, oracle_web, capsys):
        oracle_web.addresses = [TS4]
        main(["oracle", "web", "--wait"])
        (server,) = oracle_web.servers
        assert server.served is None
        assert "no certificate in the test suite" in server.app.insecure


# -- an older page restarts itself (BUG_089) ------------------------------------------


class Moving:
    """A build watch a test moves: whether the tree moved, and whether it settled."""

    def __init__(self) -> None:
        from invariant.oracle.build import Stamp

        self.started = Stamp("0.1.0", "old", 1)
        self.pending = False
        self.due = False
        self.checks = 0

    def check(self):
        self.checks += 1

    def restart_due(self) -> bool:
        return self.due


class Remote:
    def __init__(self, running: int = 0) -> None:
        self.left = running
        self.closed: list[str] = []

    def close(self, reason: str) -> int:
        self.closed.append(reason)
        return self.left


def build_world(tmp_path, remote: Remote | None = None) -> tuple[World, Moving]:
    built = World(tmp_path / "home")
    moving = Moving()
    built.service.build, built.service.remote = moving, remote
    return built, moving


class TestTheServiceRestarts:
    def test_the_report_names_the_tree_the_page_was_started_from(self, tmp_path):
        built, moving = build_world(tmp_path)
        built.found = [TS4]
        assert built.service.step() == webservice.RECHECK_SECONDS
        assert built.state()["build"] == moving.started.to_dict()
        assert moving.checks == 1 and built.service.restarting is False

    def test_without_a_build_watch_the_report_has_no_build(self, world):
        world.service.step()
        assert world.state()["build"] is None

    def test_while_the_tree_moves_it_looks_again_sooner(self, tmp_path):
        built, moving = build_world(tmp_path)
        built.found = [TS4]
        moving.pending = True
        assert built.service.step() == webservice.BUILD_SECONDS
        assert built.servers[0].background

    def test_once_settled_with_nothing_running_it_restarts(self, tmp_path):
        remote = Remote()
        built, moving = build_world(tmp_path, remote)
        built.found = [TS4]
        built.service.step()
        moving.due = True
        built.service.run()
        assert built.service.restarting is True
        assert remote.closed == [webservice.CLOSING]
        assert built.servers[0].closed and built.state() is None
        assert "restarting to run the installed code" in built.logged[-1]

    def test_running_actions_finish_before_the_restart(self, tmp_path):
        remote = Remote(running=2)
        built, moving = build_world(tmp_path, remote)
        built.found = [TS4]
        built.service.step()
        moving.due = True
        assert built.service.step() == webservice.BUILD_SECONDS
        assert built.service.step() == webservice.BUILD_SECONDS
        assert built.service.restarting is False
        assert not built.servers[0].closed
        assert [line for line in built.logged if "2 running actions" in line] != []
        assert len([line for line in built.logged if "running action" in line]) == 1
        remote.left = 1
        built.service.step()
        assert any("1 running action finish" in line for line in built.logged)
        remote.left = 0
        built.service.step()
        assert built.service.restarting is True

    def test_with_no_actions_at_all_it_restarts_once_settled(self, tmp_path):
        built, moving = build_world(tmp_path)
        moving.due = True
        built.service.step()
        assert built.service.restarting is True and built.servers == []

    def test_web_wait_runs_itself_again_through_the_seam(self, oracle_web, monkeypatch,
                                                         oracle_restarts, capsys):
        from invariant.oracle.build import BuildWatch, Stamp
        from invariant.oracle.scan import ScanCache

        stamps = iter([Stamp("0.1.0", "old", 1)])
        moved = BuildWatch(stamper=lambda root: next(stamps, Stamp("0.1.0", "new", 1)),
                           settle=0.0)
        monkeypatch.setattr(cli_main, "_oracle_cache", lambda: ScanCache(build=moved))
        oracle_web.addresses = [TS4]
        oracle_web.script = [lambda: None]
        assert main(["oracle", "web", "--wait"]) == 0
        assert oracle_restarts == ["restart"]
        assert oracle_web.servers == []
        assert "restarting" in capsys.readouterr().out
