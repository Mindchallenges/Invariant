"""Whether a long-lived oracle still runs the installed invariant (BUG_089)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from invariant import __version__
from invariant.cli.main import main
from invariant.oracle import build as build_module
from invariant.oracle.build import (
    CHANGED,
    EXIT_RESTART,
    PACKAGE_DIR,
    RULES,
    SETTLE_SECONDS,
    BuildWatch,
    Skew,
    Stamp,
    behind,
    build_text,
    reexec,
    stamp,
)
from invariant.oracle.home import oracle_home
from invariant.oracle.launchd import plist_for, web_plist_for
from invariant.oracle.watcher import SNAPSHOT_FILE
from invariant.oracle.webservice import WEB_STATE_FILE


def tree(base: Path) -> Path:
    root = base / "pkg"
    (root / "data" / "profiles").mkdir(parents=True)
    (root / "__pycache__").mkdir()
    (root / "a.py").write_text("x = 1\n", "utf-8")
    (root / "data" / "profiles" / "standard.yaml").write_text(
        "rules:\n  test.all_pass: block\n", "utf-8")
    return root


def touch(path: Path, text: str) -> None:
    path.write_text(text, "utf-8")
    info = path.stat()
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def watch(root: Path, clock: Clock | None = None, known=None) -> BuildWatch:
    return BuildWatch(root, root / "data" / "profiles", known=known or (lambda r: object()),
                      monotonic=clock or Clock())


class TestStamp:
    def test_the_installed_package_is_stamped_with_its_version(self):
        installed = stamp()
        assert installed.version == __version__
        assert installed.files > 10 and len(installed.digest) == 16
        assert stamp(PACKAGE_DIR) == installed

    def test_a_changed_file_changes_the_digest(self, tmp_path):
        root = tree(tmp_path)
        before = stamp(root)
        touch(root / "a.py", "x = 2\n")
        assert stamp(root).digest != before.digest

    def test_an_added_data_file_changes_the_digest(self, tmp_path):
        root = tree(tmp_path)
        before = stamp(root)
        (root / "data" / "profiles" / "loose.yaml").write_text("rules: {}\n", "utf-8")
        after = stamp(root)
        assert after.digest != before.digest and after.files == before.files + 1

    def test_bytecode_is_not_part_of_the_tree(self, tmp_path):
        root = tree(tmp_path)
        before = stamp(root)
        (root / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"\0")
        (root / "b.pyc").write_bytes(b"\0")
        assert stamp(root) == before

    def test_a_file_gone_between_listing_and_stat_is_skipped(self, tmp_path, monkeypatch):
        root = tree(tmp_path)
        real = os.stat

        def flaky(path, *args, **kwargs):
            if str(path).endswith("a.py"):
                raise FileNotFoundError(path)
            return real(path, *args, **kwargs)

        monkeypatch.setattr(build_module.os, "stat", flaky)
        assert stamp(root).files == 1


class TestBuildWatch:
    def test_an_unchanged_install_has_no_skew(self, tmp_path):
        bw = watch(tree(tmp_path))
        assert bw.check() is None
        assert not bw.restart_due() and not bw.pending

    def test_a_moved_tree_is_a_skew_and_a_restart_once_it_holds_still(self, tmp_path):
        root, clock = tree(tmp_path), Clock()
        bw = watch(root, clock)
        touch(root / "a.py", "x = 2\n")
        skew = bw.check()
        assert skew is not None and skew.reasons == (CHANGED,)
        assert skew.started == bw.started and skew.installed == stamp(root)
        assert bw.pending and not bw.restart_due()
        clock.now += SETTLE_SECONDS
        assert bw.check() is not None
        assert bw.restart_due() and not bw.pending

    def test_a_tree_still_moving_starts_the_wait_again(self, tmp_path):
        root, clock = tree(tmp_path), Clock()
        bw = watch(root, clock)
        touch(root / "a.py", "x = 2\n")
        bw.check()
        clock.now += SETTLE_SECONDS - 1
        touch(root / "a.py", "x = 3\n")
        bw.check()
        clock.now += 2
        assert not bw.restart_due()
        clock.now += SETTLE_SECONDS
        assert bw.restart_due()

    def test_a_tree_moved_back_is_no_skew(self, tmp_path):
        root, clock = tree(tmp_path), Clock()
        bw = watch(root, clock)
        original = (root / "a.py").stat()
        touch(root / "a.py", "x = 2\n")
        bw.check()
        (root / "a.py").write_text("x = 1\n", "utf-8")
        os.utime(root / "a.py", ns=(original.st_atime_ns, original.st_mtime_ns))
        clock.now += SETTLE_SECONDS
        assert bw.check() is None
        assert not bw.restart_due()

    def test_a_profile_naming_a_rule_the_registry_lacks_is_a_rule_skew(self, tmp_path):
        root = tree(tmp_path)
        bw = watch(root, known=lambda r: None if r == "sweep.no_full_run" else object())
        assert bw.check() is None
        touch(root / "data" / "profiles" / "standard.yaml",
              "rules:\n  test.all_pass: block\n  sweep.no_full_run: warn\n")
        skew = bw.check()
        assert skew.reasons == (CHANGED, RULES) and skew.gaps == ("sweep.no_full_run",)

    def test_a_rule_gap_alone_never_makes_a_restart_due(self, tmp_path):
        clock = Clock()
        bw = watch(tree(tmp_path), clock, known=lambda r: None)
        skew = bw.check()
        assert skew.reasons == (RULES,) and skew.gaps == ("test.all_pass",)
        clock.now += 10 * SETTLE_SECONDS
        bw.check()
        assert not bw.restart_due()

    def test_the_profiles_are_parsed_again_only_when_one_changes(self, tmp_path, monkeypatch):
        root = tree(tmp_path)
        parsed = []
        real = build_module._profile_rules
        monkeypatch.setattr(build_module, "_profile_rules",
                            lambda d: parsed.append(d) or real(d))
        bw = watch(root)
        bw.check()
        bw.check()
        assert len(parsed) == 1
        touch(root / "data" / "profiles" / "standard.yaml", "rules: {}\n")
        bw.check()
        assert len(parsed) == 2

    def test_an_unreadable_profile_names_nothing(self, tmp_path):
        root = tree(tmp_path)
        (root / "data" / "profiles" / "broken.yaml").write_text("rules: [\n", "utf-8")
        (root / "data" / "profiles" / "list.yaml").write_text("- a\n", "utf-8")
        assert watch(root, known=lambda r: None).check().gaps == ("test.all_pass",)

    def test_a_dangling_profile_is_read_again_each_time_and_names_nothing(self, tmp_path):
        root = tree(tmp_path)
        (root / "data" / "profiles" / "gone.yaml").symlink_to(tmp_path / "nowhere.yaml")
        bw = watch(root, known=lambda r: None)
        assert bw.check().gaps == ("test.all_pass",)
        assert bw._gap_key is None
        assert bw.check().gaps == ("test.all_pass",)

    def test_a_missing_profiles_directory_names_nothing(self, tmp_path):
        bw = watch(tree(tmp_path), known=lambda r: None)
        bw.profiles = tmp_path / "missing"
        assert bw.check() is None

    def test_the_shipped_profiles_name_only_rules_this_build_carries(self):
        assert BuildWatch().check() is None


class TestSkew:
    def stamps(self):
        return Stamp("0.1.0", "aaaa", 3), Stamp("0.1.0", "bbbb", 4)

    def test_its_detail_names_each_reason(self):
        started, installed = self.stamps()
        skew = Skew((CHANGED, RULES), started, installed, ("sweep.no_full_run",))
        assert skew.detail == (
            "the installed invariant changed since this process started (aaaa -> bbbb); "
            "the installed profiles name rules this build does not carry: sweep.no_full_run")
        assert skew.to_dict() == {
            "reasons": ["changed", "rules"], "started": started.to_dict(),
            "installed": installed.to_dict(), "rules": ["sweep.no_full_run"],
            "detail": skew.detail}


class TestBehind:
    installed = Stamp("0.1.0", "bbbb", 4)

    def test_nothing_reported_is_unknown(self):
        assert behind(None, self.installed) is None
        assert build_text(None, self.installed, "the watcher") == "not reported"

    def test_the_same_digest_is_current(self):
        assert behind({"digest": "bbbb"}, self.installed) is False
        assert build_text({"digest": "bbbb"}, self.installed, "the watcher") == "current (bbbb)"

    def test_another_digest_is_older(self):
        assert behind({"digest": "aaaa"}, self.installed) is True
        text = build_text({"digest": "aaaa"}, self.installed, "the watcher")
        assert text.startswith("OLDER than the installed invariant (started from aaaa, "
                               "installed bbbb): the watcher restarts itself")

    @pytest.mark.parametrize("reported", [{}, "x", {"digest": ""}])
    def test_a_report_without_a_stamp_is_older(self, reported):
        assert behind(reported, self.installed) is True
        assert "it reports no build stamp" in build_text(reported, self.installed, "the page")


class TestReexec:
    def test_it_runs_the_same_command_again_in_this_process(self, monkeypatch):
        calls = []
        monkeypatch.setattr(sys, "orig_argv", ["/py", "-m", "invariant", "oracle", "watch"])
        status = reexec(execv=lambda program, args: calls.append((program, args)),
                        executable="/venv/python")
        assert calls == [("/venv/python", ["/venv/python", "-m", "invariant", "oracle", "watch"])]
        assert status == EXIT_RESTART

    def test_an_exec_that_fails_exits_for_launchd(self, capsys):
        def refuse(program, args):
            raise OSError("no such interpreter")

        assert reexec(["oracle", "web", "--wait"], execv=refuse) == EXIT_RESTART
        assert "could not restart (no such interpreter)" in capsys.readouterr().err


# -- oracle status, doctor and oracle list ---------------------------------------------

def snapshot(build="current", skew=None, age: float = 2.0) -> None:
    body: dict = {"interval": 5.0, "watcher_pid": 4242, "oracle": {"skew": skew, "unread": []}}
    if build == "current":
        body["build"] = stamp().to_dict()
    elif build is not None:
        body["build"] = {"version": "0.1.0", "digest": build, "files": 1}
    path = oracle_home() / SNAPSHOT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), "utf-8")
    then = time.time() - age
    os.utime(path, (then, then))


def web_report(build="current", pid: int | None = None) -> None:
    body: dict = {"version": 1, "state": "serving", "pid": pid or os.getpid(), "port": 7341,
                  "addresses": [], "urls": ["http://100.101.102.103:7341/"],
                  "since": "2026-10-22T10:00:00Z", "error": None}
    if build == "current":
        body["build"] = stamp().to_dict()
    elif build is not None:
        body["build"] = {"version": "0.1.0", "digest": build, "files": 1}
    path = oracle_home() / WEB_STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), "utf-8")


def status_text(capsys) -> str:
    capsys.readouterr()
    assert main(["oracle", "status"]) == 0
    return capsys.readouterr().out


class TestStatusSaysWhetherTheOracleIsBehind:
    def test_no_watcher_no_build_line(self, capsys):
        assert main(["oracle", "status", "--json"]) == 0
        body = json.loads(capsys.readouterr().out)
        assert body["build"]["installed"] == stamp().to_dict()
        assert body["build"]["watcher"] == {"older": None, "text": None}
        assert body["build"]["web"] == {"older": None, "text": None}
        assert "build" not in status_text(capsys)

    def test_a_stale_snapshot_says_nothing_of_a_build(self, capsys):
        snapshot(build="aaaa", age=600)
        assert "  build" not in status_text(capsys)

    def test_a_current_watcher(self, capsys):
        snapshot()
        assert f"build        current ({stamp().digest})" in status_text(capsys)

    def test_an_older_watcher(self, capsys):
        snapshot(build="aaaa")
        out = status_text(capsys)
        assert "build        OLDER than the installed invariant (started from aaaa" in out
        assert "the watcher restarts itself" in out

    def test_a_watcher_from_before_stamps_is_older(self, capsys):
        snapshot(build=None)
        assert "it reports no build stamp" in status_text(capsys)

    def test_a_watcher_that_says_it_is_behind(self, capsys):
        snapshot(skew={"detail": "the installed profiles name rules this build does not "
                                 "carry: sweep.no_full_run"})
        main(["oracle", "status", "--json"])
        assert json.loads(capsys.readouterr().out)["build"]["watcher"]["older"] is True
        assert "OLDER than the installed invariant: the installed profiles" in (
            status_text(capsys))

    def test_the_pages_build(self, capsys):
        web_report()
        assert "web build    current" in status_text(capsys)
        web_report(build=None)
        assert "web build    OLDER" in status_text(capsys)
        web_report(pid=2**22 + 12345)
        assert "web build" not in status_text(capsys)


class TestDoctorSaysWhetherTheOracleIsBehind:
    def rows(self, capsys) -> dict[str, dict]:
        body = json.loads(capsys.readouterr().out)
        return {c["check"]: c for c in body["checks"]}

    def test_no_row_without_a_watcher_agent(self, project, monkeypatch, capsys):
        snapshot(build="aaaa")
        monkeypatch.chdir(project)
        main(["doctor", "--json"])
        assert "oracle watcher build" not in self.rows(capsys)

    def test_an_older_watcher_fails(self, project, monkeypatch, capsys, _no_real_launchd):
        _no_real_launchd.install(plist_for(sys.executable, project / "h", 5.0,
                                           path="/bin", pin_home=False))
        snapshot(build="aaaa")
        monkeypatch.chdir(project)
        assert main(["doctor", "--json"]) == 1
        row = self.rows(capsys)["oracle watcher build"]
        assert row["ok"] is False and "OLDER" in row["detail"]

    def test_a_current_watcher_passes_and_none_running_has_no_row(
        self, project, monkeypatch, capsys, _no_real_launchd
    ):
        _no_real_launchd.install(plist_for(sys.executable, project / "h", 5.0,
                                           path="/bin", pin_home=False))
        monkeypatch.chdir(project)
        main(["doctor", "--json"])
        assert "oracle watcher build" not in self.rows(capsys)
        snapshot()
        main(["doctor", "--json"])
        assert self.rows(capsys)["oracle watcher build"]["ok"] is True

    def test_an_older_page_fails(self, project, monkeypatch, capsys, _no_real_web_launchd):
        _no_real_web_launchd.install(web_plist_for(sys.executable, oracle_home(), 7341,
                                                   path="/bin", pin_home=False))
        web_report(build="aaaa")
        monkeypatch.chdir(project)
        main(["doctor", "--json"])
        row = self.rows(capsys)["oracle web build"]
        assert row["ok"] is False and "the page restarts itself" in row["detail"]


class TestListSaysTheOracleIsBehind:
    def test_one_line_names_the_skew_and_the_projects_left(self, tmp_path, monkeypatch,
                                                           capsys):
        from test_oracle_scan import make_project

        from invariant.core import rules as rules_module

        make_project(tmp_path, "one")
        monkeypatch.delitem(rules_module._REGISTRY, "sweep.no_full_run")
        assert main(["oracle", "list"]) == 0
        out = capsys.readouterr().out
        assert ("oracle OLDER than the installed invariant: the installed profiles name "
                "rules this build does not carry: sweep.no_full_run; not read: one") in out
        assert "ALERT" not in out
