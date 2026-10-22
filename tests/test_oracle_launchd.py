"""The launchd agent that keeps ``oracle watch`` running (IDEA_012 increment 6).

Nothing here runs the real ``launchctl``. The installer takes the program as
an injected runner, the suite's autouse fixture hands the CLI an installer
over a temporary ``LaunchAgents`` directory and a recording launchctl, and the
``conftest.py`` guard refuses the real program by any route. The last class
checks that guard, and its hardening, directly.
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import RefusedProgram

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core.errors import InvariantError
from invariant.oracle import launchd
from invariant.oracle.home import oracle_home
from invariant.oracle.launchd import (
    LABEL,
    LAUNCHCTL,
    LaunchdError,
    LaunchdInstaller,
    RecordingLaunchctl,
    agents_dir,
    plist_bytes,
    plist_for,
    recording_installer,
    run_launchctl,
    search_path,
)
from invariant.oracle.watcher import SNAPSHOT_FILE

PYTHON = "/opt/tools/invariant/bin/python"
HOME = Path("/srv/example/.invariant")


def a_plist(interval: float = 5.0, pin_home: bool = False, home: Path = HOME,
            python: str = PYTHON) -> dict:
    return plist_for(python, home, interval, path="/usr/bin:/bin", pin_home=pin_home)


# -- the plist ----------------------------------------------------------------


class TestThePlist:
    def test_it_names_the_label_the_interpreter_and_the_watch(self):
        plist = a_plist(interval=7)
        assert plist["Label"] == LABEL == "dev.invariant.oracle"
        assert plist["ProgramArguments"] == [
            PYTHON, "-m", "invariant", "oracle", "watch", "--interval", "7",
        ]
        assert plist["RunAtLoad"] is True
        assert plist["KeepAlive"] is True

    def test_a_fractional_interval_is_kept(self):
        assert a_plist(interval=2.5)["ProgramArguments"][-1] == "2.5"

    def test_both_streams_go_to_the_log_under_the_home(self):
        plist = a_plist()
        assert plist["StandardOutPath"] == str(HOME / "oracle" / "watcher.log")
        assert plist["StandardErrorPath"] == plist["StandardOutPath"]

    def test_the_environment_is_an_explicit_path_and_no_home_by_default(self):
        assert a_plist()["EnvironmentVariables"] == {"PATH": "/usr/bin:/bin"}

    def test_a_home_that_was_set_is_pinned(self):
        env = a_plist(pin_home=True)["EnvironmentVariables"]
        assert env == {"PATH": "/usr/bin:/bin", "INVARIANT_HOME": str(HOME)}

    def test_it_round_trips_through_plistlib(self):
        plist = a_plist(pin_home=True)
        assert plistlib.loads(plist_bytes(plist)) == plist
        assert plist_bytes(plist).startswith(b"<?xml")

    def test_a_relative_interpreter_is_refused(self):
        with pytest.raises(LaunchdError, match="absolute"):
            a_plist(python="python3")

    def test_a_non_positive_interval_is_refused(self):
        with pytest.raises(LaunchdError, match="positive"):
            a_plist(interval=0)


class TestTheSearchPath:
    def test_git_and_osascript_come_first_then_the_system(self):
        found = {"git": "/opt/homebrew/bin/git", "osascript": "/usr/bin/osascript"}
        path = search_path(which=found.get)
        assert path.split(":") == [
            "/opt/homebrew/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin",
        ]

    def test_a_missing_program_still_leaves_the_system_dirs(self):
        assert search_path(which=lambda name: None) == "/usr/bin:/bin:/usr/sbin:/sbin"

    def test_the_real_one_finds_git(self):
        import shutil

        git = shutil.which("git")
        assert git is not None
        assert str(Path(git).parent) in search_path().split(":")


def test_the_agents_directory_is_the_users_launch_agents():
    assert agents_dir() == Path.home() / "Library" / "LaunchAgents"


# -- the installer ------------------------------------------------------------


@pytest.fixture
def installer(tmp_path) -> LaunchdInstaller:
    return recording_installer(tmp_path / "LaunchAgents", uid=501)


def home_plist(tmp_path) -> dict:
    return a_plist(home=tmp_path / "home")


SERVICE = f"gui/501/{LABEL}"


class TestInstall:
    def test_a_fresh_install_writes_then_bootstraps(self, installer, tmp_path):
        result = installer.install(home_plist(tmp_path))
        plist_path = tmp_path / "LaunchAgents" / f"{LABEL}.plist"
        assert installer.plist_path == plist_path
        assert plistlib.loads(plist_path.read_bytes()) == home_plist(tmp_path)
        assert installer.run.calls == [
            [LAUNCHCTL, "print", SERVICE],
            [LAUNCHCTL, "bootstrap", "gui/501", str(plist_path)],
        ]
        assert result.steps == [[LAUNCHCTL, "bootstrap", "gui/501", str(plist_path)]]
        assert result.was_loaded is False and result.wrote is True
        assert installer.loaded()

    def test_it_creates_the_log_directory(self, installer, tmp_path):
        installer.install(home_plist(tmp_path))
        assert (tmp_path / "home" / "oracle").is_dir()

    def test_a_reinstall_boots_the_old_agent_out_first(self, installer, tmp_path):
        installer.install(home_plist(tmp_path))
        installer.run.calls.clear()
        newer = a_plist(interval=9, home=tmp_path / "home")
        result = installer.install(newer)
        plist_path = str(installer.plist_path)
        assert installer.run.calls == [
            [LAUNCHCTL, "print", SERVICE],
            [LAUNCHCTL, "bootout", SERVICE],
            [LAUNCHCTL, "print", SERVICE],
            [LAUNCHCTL, "bootstrap", "gui/501", plist_path],
        ]
        assert result.was_loaded is True
        assert result.steps == [
            [LAUNCHCTL, "bootout", SERVICE],
            [LAUNCHCTL, "bootstrap", "gui/501", plist_path],
        ]
        assert plistlib.loads(installer.plist_path.read_bytes()) == newer

    def test_the_reinstall_waits_for_the_old_agent_to_go(self, installer, tmp_path):
        installer.install(home_plist(tmp_path))
        installer.run.linger = 3
        installer.install(home_plist(tmp_path))
        prints = [c for c in installer.run.calls if c[1] == "print"]
        assert len(prints) == 1 + 1 + 4
        assert installer.loaded()

    def test_an_agent_that_never_goes_is_an_error(self, installer, tmp_path):
        installer.install(home_plist(tmp_path))
        installer.run.linger = 10_000
        with pytest.raises(LaunchdError, match="still loaded"):
            installer.install(home_plist(tmp_path))

    def test_a_refused_bootout_is_an_error(self, installer, tmp_path):
        installer.install(home_plist(tmp_path))
        installer.run.refuse["bootout"] = (5, "Boot-out failed: 5: Input/output error")
        with pytest.raises(LaunchdError, match="Input/output error"):
            installer.install(home_plist(tmp_path))

    def test_a_refused_bootstrap_is_an_error_naming_the_plist(self, installer, tmp_path):
        installer.run.refuse["bootstrap"] = (5, "Bootstrap failed: 5: Input/output error")
        with pytest.raises(LaunchdError) as caught:
            installer.install(home_plist(tmp_path))
        assert "Bootstrap failed" in str(caught.value)
        assert str(installer.plist_path) in str(caught.value)
        assert caught.value.exit_code == 1
        assert issubclass(LaunchdError, InvariantError)

    def test_a_dry_run_asks_and_touches_nothing(self, installer, tmp_path):
        result = installer.install(home_plist(tmp_path), dry_run=True)
        assert installer.run.calls == [[LAUNCHCTL, "print", SERVICE]]
        assert not installer.plist_path.parent.exists()
        assert not (tmp_path / "home").exists()
        assert result.dry_run and not result.wrote
        assert result.steps == [
            [LAUNCHCTL, "bootstrap", "gui/501", str(installer.plist_path)]
        ]
        assert plistlib.loads(result.xml) == home_plist(tmp_path)

    def test_a_dry_run_over_a_loaded_agent_names_the_bootout(self, installer, tmp_path):
        installer.install(home_plist(tmp_path))
        before = installer.plist_path.read_bytes()
        installer.run.calls.clear()
        result = installer.install(a_plist(interval=9, home=tmp_path / "home"), dry_run=True)
        assert [s[1] for s in result.steps] == ["bootout", "bootstrap"]
        assert installer.run.calls == [[LAUNCHCTL, "print", SERVICE]]
        assert installer.plist_path.read_bytes() == before

    def test_the_result_serialises(self, installer, tmp_path):
        body = installer.install(home_plist(tmp_path)).to_dict()
        json.dumps(body)
        assert body["plist_path"] == str(installer.plist_path)
        assert body["plist"]["Label"] == LABEL
        assert body["was_loaded"] is False


class TestUninstall:
    def test_it_boots_out_and_removes_the_plist(self, installer, tmp_path):
        installer.install(home_plist(tmp_path))
        installer.run.calls.clear()
        result = installer.uninstall()
        assert installer.run.calls == [
            [LAUNCHCTL, "print", SERVICE],
            [LAUNCHCTL, "bootout", SERVICE],
            [LAUNCHCTL, "print", SERVICE],
        ]
        assert result.steps == [[LAUNCHCTL, "bootout", SERVICE]]
        assert result.removed is True
        assert not installer.plist_path.exists()
        assert not installer.loaded()

    def test_a_plist_that_is_not_loaded_is_only_removed(self, installer, tmp_path):
        installer.plist_path.parent.mkdir(parents=True)
        installer.plist_path.write_bytes(plist_bytes(home_plist(tmp_path)))
        result = installer.uninstall()
        assert installer.run.calls == [[LAUNCHCTL, "print", SERVICE]]
        assert result.steps == [] and result.removed is True
        assert not installer.plist_path.exists()

    def test_nothing_installed_is_nothing_to_do(self, installer):
        result = installer.uninstall()
        assert result.steps == [] and result.removed is False
        assert result.was_loaded is False

    def test_a_dry_run_touches_nothing(self, installer, tmp_path):
        installer.install(home_plist(tmp_path))
        installer.run.calls.clear()
        result = installer.uninstall(dry_run=True)
        assert installer.run.calls == [[LAUNCHCTL, "print", SERVICE]]
        assert installer.plist_path.exists() and installer.loaded()
        assert result.removed is False and result.dry_run
        assert result.steps == [[LAUNCHCTL, "bootout", SERVICE]]

    def test_a_refused_bootout_keeps_the_plist(self, installer, tmp_path):
        installer.install(home_plist(tmp_path))
        installer.run.refuse["bootout"] = (5, "nope")
        with pytest.raises(LaunchdError, match="nope"):
            installer.uninstall()
        assert installer.plist_path.exists()

    def test_a_bootout_that_fails_because_it_already_went_is_fine(self, installer, tmp_path):
        installer.install(home_plist(tmp_path))
        installer.run.refuse["bootout"] = (3, "No such process")
        installer.run.unload_on_refusal = True
        installer.uninstall()
        assert not installer.plist_path.exists()


class TestStatus:
    def test_not_installed(self, installer):
        status = installer.status()
        assert status.installed is False and status.loaded is False
        assert status.interpreter is None and status.interpreter_exists is None
        assert installer.interpreter_check() is None

    def test_installed_and_loaded(self, installer, tmp_path):
        installer.install(a_plist(home=tmp_path / "home", python=sys.executable, interval=3))
        status = installer.status()
        assert status.installed and status.loaded
        assert status.interpreter == sys.executable and status.interpreter_exists
        assert status.interval == 3.0
        assert installer.interpreter_check() == (True, sys.executable)
        json.dumps(status.to_dict())

    def test_an_interpreter_that_moved_fails_the_check(self, installer, tmp_path):
        gone = str(tmp_path / "moved" / "python")
        installer.install(a_plist(home=tmp_path / "home", python=gone))
        ok, detail = installer.interpreter_check()
        assert ok is False
        assert gone in detail and "invariant oracle install" in detail

    def test_an_unreadable_plist_fails_the_check(self, installer):
        installer.plist_path.parent.mkdir(parents=True)
        installer.plist_path.write_text("not a plist", "utf-8")
        ok, detail = installer.interpreter_check()
        assert ok is False and "cannot read" in detail
        assert installer.status().error

    def test_a_plist_without_arguments_has_no_interpreter(self, installer):
        installer.plist_path.parent.mkdir(parents=True)
        installer.plist_path.write_bytes(plistlib.dumps({"Label": LABEL}))
        ok, detail = installer.interpreter_check()
        assert ok is False and "no interpreter" in detail
        assert installer.status().interval is None


class TestTheRealRunner:
    def test_it_runs_launchctl_and_returns_the_status_and_text(self):
        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"], seen["kwargs"] = argv, kwargs
            return subprocess.CompletedProcess(argv, 113, "", "Could not find service\n")

        code, text = run_launchctl([LAUNCHCTL, "print", SERVICE], run=fake_run)
        assert (code, text) == (113, "Could not find service")
        assert seen["argv"] == [LAUNCHCTL, "print", SERVICE]
        assert seen["kwargs"]["check"] is False and seen["kwargs"]["timeout"] > 0

    def test_stdout_is_the_text_when_stderr_is_empty(self):
        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, "state = running\n", "")

        assert run_launchctl([LAUNCHCTL, "print", SERVICE], run=fake_run) == (
            0, "state = running"
        )

    def test_a_missing_launchctl_is_an_error(self):
        def fake_run(argv, **kwargs):
            raise FileNotFoundError(argv[0])

        with pytest.raises(LaunchdError, match="could not run launchctl"):
            run_launchctl([LAUNCHCTL, "print", SERVICE], run=fake_run)

    def test_the_real_one_is_refused_in_the_suite(self):
        with pytest.raises(RefusedProgram, match="launchctl"):
            run_launchctl([LAUNCHCTL, "print", SERVICE])

    def test_the_default_installer_settles_with_real_sleep(self, tmp_path):
        made = LaunchdInstaller(tmp_path, 501, run=RecordingLaunchctl())
        assert made.sleep is time.sleep


class TestTheRecorder:
    def test_an_unknown_verb_is_refused(self):
        recorder = RecordingLaunchctl()
        assert recorder(["launchctl", "kickstart", "x"])[0] != 0

    def test_a_bootout_of_nothing_loaded_fails_like_launchctl(self):
        recorder = RecordingLaunchctl()
        assert recorder([LAUNCHCTL, "bootout", SERVICE])[0] == 3


# -- the CLI ------------------------------------------------------------------


def last_json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


class TestTheCliSeam:
    def test_the_suite_replaces_it_with_a_recording_installer(self, _no_real_launchd):
        assert cli_main._oracle_installer() is _no_real_launchd
        assert isinstance(_no_real_launchd.run, RecordingLaunchctl)
        assert Path.home() not in _no_real_launchd.plist_path.parents

    def test_the_real_seam_is_the_users_agents_dir_and_real_launchctl(self):
        real = cli_main._real_oracle_installer()
        assert isinstance(real, LaunchdInstaller)
        assert real.agents_dir == agents_dir()
        assert real.uid == os.getuid()
        assert real.run is run_launchctl


class TestInstallCommand:
    def test_install_writes_and_bootstraps(self, _no_real_launchd, capsys):
        assert main(["oracle", "install", "--json"]) == 0
        body = last_json(capsys)
        plist = plistlib.loads(_no_real_launchd.plist_path.read_bytes())
        assert body["plist"] == plist
        assert plist["ProgramArguments"][0] == os.path.abspath(sys.executable)
        assert plist["ProgramArguments"][1:] == [
            "-m", "invariant", "oracle", "watch", "--interval", "5",
        ]
        assert plist["StandardOutPath"] == str(oracle_home() / "oracle" / "watcher.log")
        assert plist["EnvironmentVariables"]["INVARIANT_HOME"] == str(oracle_home())
        assert [s[1] for s in body["steps"]] == ["bootstrap"]
        assert _no_real_launchd.loaded()

    def test_the_home_is_not_pinned_when_it_was_not_set(
        self, _no_real_launchd, monkeypatch, capsys
    ):
        monkeypatch.delenv("INVARIANT_HOME")
        assert main(["oracle", "install", "--dry-run", "--json"]) == 0
        env = last_json(capsys)["plist"]["EnvironmentVariables"]
        assert "INVARIANT_HOME" not in env
        assert env["PATH"] == search_path()

    def test_the_interval_reaches_the_watch(self, _no_real_launchd, capsys):
        assert main(["oracle", "install", "--interval", "12", "--json"]) == 0
        assert last_json(capsys)["plist"]["ProgramArguments"][-1] == "12"

    def test_a_non_positive_interval_is_a_usage_error(self, _no_real_launchd, capsys):
        assert main(["oracle", "install", "--interval", "0"]) == 2
        assert _no_real_launchd.run.calls == []

    def test_dry_run_prints_the_plist_and_argv_and_touches_nothing(
        self, _no_real_launchd, capsys
    ):
        assert main(["oracle", "install", "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "<key>Label</key>" in out and LABEL in out
        assert f"{LAUNCHCTL} bootstrap gui/" in out
        assert "would" in out
        assert not _no_real_launchd.plist_path.exists()
        assert not oracle_home().exists()
        assert [c[1] for c in _no_real_launchd.run.calls] == ["print"]

    def test_the_text_of_a_real_install(self, _no_real_launchd, capsys):
        assert main(["oracle", "install"]) == 0
        out = capsys.readouterr().out
        assert "installed" in out and str(_no_real_launchd.plist_path) in out
        assert "watcher.log" in out

    def test_a_reinstall_says_it_replaced_the_agent(self, _no_real_launchd, capsys):
        assert main(["oracle", "install"]) == 0
        capsys.readouterr()
        assert main(["oracle", "install"]) == 0
        assert "replaced" in capsys.readouterr().out

    def test_a_refusal_exits_1(self, _no_real_launchd, capsys):
        _no_real_launchd.run.refuse["bootstrap"] = (5, "Bootstrap failed")
        assert main(["oracle", "install"]) == 1
        assert "Bootstrap failed" in capsys.readouterr().err


class TestUninstallCommand:
    def test_uninstall_boots_out_and_removes(self, _no_real_launchd, capsys):
        assert main(["oracle", "install"]) == 0
        capsys.readouterr()
        assert main(["oracle", "uninstall", "--json"]) == 0
        body = last_json(capsys)
        assert body["removed"] is True
        assert [s[1] for s in body["steps"]] == ["bootout"]
        assert not _no_real_launchd.plist_path.exists()

    def test_the_text(self, _no_real_launchd, capsys):
        assert main(["oracle", "install"]) == 0
        capsys.readouterr()
        assert main(["oracle", "uninstall"]) == 0
        out = capsys.readouterr().out
        assert "removed" in out and "bootout" in out

    def test_nothing_installed(self, _no_real_launchd, capsys):
        assert main(["oracle", "uninstall"]) == 0
        assert "not installed" in capsys.readouterr().out

    def test_dry_run_touches_nothing(self, _no_real_launchd, capsys):
        assert main(["oracle", "install"]) == 0
        capsys.readouterr()
        _no_real_launchd.run.calls.clear()
        assert main(["oracle", "uninstall", "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "would" in out and f"{LAUNCHCTL} bootout" in out
        assert [c[1] for c in _no_real_launchd.run.calls] == ["print"]
        assert _no_real_launchd.plist_path.exists() and _no_real_launchd.loaded()


class TestStatusCommand:
    def write_snapshot(self, age: float, interval: float = 5.0) -> Path:
        path = oracle_home() / SNAPSHOT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"interval": interval, "watcher_pid": 4242}), "utf-8")
        then = time.time() - age
        os.utime(path, (then, then))
        return path

    def test_nothing_installed_and_no_snapshot(self, _no_real_launchd, capsys):
        assert main(["oracle", "status", "--json"]) == 0
        body = last_json(capsys)
        assert body["agent"]["installed"] is False
        assert body["snapshot"]["exists"] is False
        assert body["snapshot"]["stale"] is True

    def test_a_running_agent_with_a_fresh_snapshot(self, _no_real_launchd, capsys):
        assert main(["oracle", "install"]) == 0
        self.write_snapshot(age=2)
        capsys.readouterr()
        assert main(["oracle", "status", "--json"]) == 0
        body = last_json(capsys)
        assert body["agent"]["loaded"] is True
        assert body["agent"]["interpreter_exists"] is True
        assert body["snapshot"]["stale"] is False
        assert body["snapshot"]["watcher_pid"] == 4242

    def test_the_text_flags_a_stale_snapshot(self, _no_real_launchd, capsys):
        assert main(["oracle", "install"]) == 0
        self.write_snapshot(age=60)
        capsys.readouterr()
        assert main(["oracle", "status"]) == 0
        out = capsys.readouterr().out
        assert "loaded" in out and "STALE" in out

    def test_the_text_with_nothing(self, _no_real_launchd, capsys):
        assert main(["oracle", "status"]) == 0
        out = capsys.readouterr().out
        assert "not installed" in out and "no snapshot" in out

    def test_the_text_of_a_fresh_snapshot(self, _no_real_launchd, capsys):
        assert main(["oracle", "install"]) == 0
        self.write_snapshot(age=1)
        capsys.readouterr()
        assert main(["oracle", "status"]) == 0
        out = capsys.readouterr().out
        assert "fresh" in out and "STALE" not in out

    def test_the_text_of_an_unreadable_plist(self, _no_real_launchd, capsys):
        _no_real_launchd.plist_path.parent.mkdir(parents=True)
        _no_real_launchd.plist_path.write_text("junk", "utf-8")
        assert main(["oracle", "status"]) == 0
        assert "cannot read" in capsys.readouterr().out

    def test_a_moved_interpreter_is_flagged(self, _no_real_launchd, tmp_path, capsys):
        _no_real_launchd.install(a_plist(home=tmp_path / "h", python=str(tmp_path / "gone")))
        assert main(["oracle", "status"]) == 0
        assert "MISSING" in capsys.readouterr().out


class TestDoctor:
    def rows(self, capsys) -> dict[str, dict]:
        body = json.loads(capsys.readouterr().out)
        return {c["check"]: c for c in body["checks"]}

    def test_no_row_without_a_plist(self, project, monkeypatch, capsys):
        monkeypatch.chdir(project)
        main(["doctor", "--json"])
        assert "oracle agent interpreter" not in self.rows(capsys)

    def test_a_present_interpreter_passes(self, project, monkeypatch, capsys, _no_real_launchd):
        _no_real_launchd.install(a_plist(home=project / "h", python=sys.executable))
        monkeypatch.chdir(project)
        main(["doctor", "--json"])
        row = self.rows(capsys)["oracle agent interpreter"]
        assert row["ok"] is True and row["detail"] == sys.executable

    def test_a_moved_interpreter_fails(self, project, monkeypatch, capsys, _no_real_launchd):
        gone = str(project / "moved" / "python")
        _no_real_launchd.install(a_plist(home=project / "h", python=gone))
        monkeypatch.chdir(project)
        assert main(["doctor", "--json"]) == 1
        row = self.rows(capsys)["oracle agent interpreter"]
        assert row["ok"] is False and gone in row["detail"]

    def test_the_check_reads_no_process_table(self, project, monkeypatch, capsys,
                                              _no_real_launchd):
        _no_real_launchd.install(a_plist(home=project / "h", python=sys.executable))
        _no_real_launchd.run.calls.clear()
        monkeypatch.chdir(project)
        main(["doctor", "--json"])
        assert _no_real_launchd.run.calls == []


class TestLayering:
    def test_the_module_imports_no_host(self):
        source = Path(launchd.__file__).read_text("utf-8")
        assert "hosts" not in [
            part for line in source.splitlines()
            if line.startswith(("from ", "import ")) for part in line.split(".")
        ]


# -- the guard ----------------------------------------------------------------


class TestTheGuard:
    """Real ``launchctl`` and ``osascript`` never run in the suite."""

    @pytest.mark.parametrize("argv", [
        ["launchctl", "print", "system"],
        ["/bin/launchctl", "bootout", SERVICE],
        [Path("/bin/launchctl"), "list"],
        ["osascript", "-e", "1"],
        [Path("/usr/bin/osascript"), "-e", "1"],
        [b"/usr/bin/osascript", b"-e", b"1"],
    ])
    def test_a_list_naming_either_program_is_refused(self, argv):
        with pytest.raises(RefusedProgram):
            subprocess.run(argv, check=False)

    @pytest.mark.parametrize("args", [
        "launchctl",
        Path("/bin/launchctl"),
        "/usr/bin/osascript",
        Path("/usr/bin/osascript"),
        b"/bin/launchctl",
    ])
    def test_a_bare_string_or_path_is_refused_without_a_type_error(self, args):
        with pytest.raises(RefusedProgram):
            subprocess.run(args, check=False)

    @pytest.mark.parametrize("argv", [
        ["sh", "-c", "launchctl print system"],
        ["/bin/bash", "-lc", "cd / && osascript -e 1"],
        ["zsh", "-c", "FOO=1 exec /bin/launchctl list"],
        ["sh", "-c", "true; env A=b osascript -e 1"],
        ["sh", "-c", "sh -c 'launchctl list'"],
    ])
    def test_a_shell_command_string_is_inspected(self, argv):
        with pytest.raises(RefusedProgram):
            subprocess.run(argv, check=False)

    @pytest.mark.parametrize("command", [
        "launchctl list",
        "true && /bin/launchctl list",
        "echo x | osascript -e 1",
        "osascript -e 'unbalanced",
        "true\nlaunchctl list",
    ])
    def test_shell_true_is_inspected(self, command):
        with pytest.raises(RefusedProgram):
            subprocess.run(command, shell=True, check=False)

    def test_shell_true_with_a_list_reads_its_first_element(self):
        with pytest.raises(RefusedProgram):
            subprocess.run(["launchctl list"], shell=True, check=False)

    def test_an_executable_override_is_inspected(self):
        with pytest.raises(RefusedProgram):
            subprocess.run(["harmless"], executable="/bin/launchctl", check=False)

    def test_it_is_not_an_exception_so_a_broad_handler_cannot_swallow_it(self):
        assert issubclass(RefusedProgram, BaseException)
        assert not issubclass(RefusedProgram, Exception)
        with pytest.raises(RefusedProgram):
            try:
                subprocess.run(["osascript", "-e", "1"], check=False)
            except Exception:  # noqa: BLE001 - the point: this must not catch it
                pytest.fail("a broad handler swallowed the refusal")

    @pytest.mark.parametrize("argv", [
        ["true"],
        ["sh", "-c", "echo launchctl osascript"],
        ["sh", "-c", "true"],
        ["sh", "-c"],
        ["sh", "-c", "'"],
    ])
    def test_other_programs_still_run(self, argv):
        subprocess.run(argv, check=False, capture_output=True)

    def test_an_empty_shell_string_still_runs(self):
        subprocess.run("true", shell=True, check=False)

    @pytest.mark.real_osascript
    def test_the_osascript_marker_does_not_admit_launchctl(self):
        with pytest.raises(RefusedProgram, match="launchctl"):
            subprocess.run(["launchctl", "list"], check=False)
