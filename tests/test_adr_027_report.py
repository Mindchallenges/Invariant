"""ADR-027 D1 and D11: a session reports how it ended.

``session report --outcome done|blocked|needs-user`` is an event on the
session record, never an edge: legal in every state, a ``reports`` entry and a
standstill row in ``history``. The note is vetted as a handoff note is. The
oracle's scan carries the latest report on the session's row.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from invariant.cli.main import main
from invariant.core.errors import GateFailure, UsageError
from invariant.core.manifest import Manifest
from invariant.core.store import JsonStore
from invariant.domains.session import REPORT_OUTCOMES, Session, SessionDomain
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectIndex
from invariant.oracle.scan import ScanCache, scan_all


def _git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


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


def domain(root: Path, clock=None) -> SessionDomain:
    manifest = Manifest.locate(root)
    kwargs = {"clock": clock} if clock else {}
    return SessionDomain(manifest, JsonStore(manifest.state_root), **kwargs)


def started(capsys, name="add-widget") -> dict:
    assert main(["session", "start", name, "--json"]) == 0
    return json.loads(capsys.readouterr().out)["session"]


class TestTheReportIsRecorded:
    def test_the_outcomes(self):
        assert REPORT_OUTCOMES == ("done", "blocked", "needs-user")

    def test_it_appends_an_entry_and_a_standstill_row(self, project, capsys):
        session = started(capsys)
        sessions = domain(project, clock=lambda: 1234.5)
        updated = sessions.record_report(session["id"], "blocked", "the fixture is gone")
        assert updated.reports == [
            {"outcome": "blocked", "note": "the fixture is gone", "at": 1234.5,
             "state": "registered"}
        ]
        row = updated.history[-1]
        assert row["from"] == row["to"] == "registered"
        assert row["report"] == "blocked"
        assert sessions.get(session["id"]).reports[-1]["outcome"] == "blocked"

    def test_a_note_is_optional(self, project, capsys):
        session = started(capsys)
        updated = domain(project).record_report(session["id"], "done")
        assert updated.reports[-1]["note"] is None

    def test_an_unknown_outcome_is_refused(self, project, capsys):
        session = started(capsys)
        with pytest.raises(UsageError, match="needs-user"):
            domain(project).record_report(session["id"], "finished")

    def test_the_note_is_vetted(self, project, capsys):
        session = started(capsys)
        with pytest.raises(GateFailure):
            domain(project).record_report(session["id"], "done", "ping someone@example.org")
        with pytest.raises(UsageError, match="one line"):
            domain(project).record_report(session["id"], "done", "a\nb")
        assert domain(project).get(session["id"]).reports == []

    def test_a_torn_down_session_reports_and_stays_deregistered(self, project, capsys):
        session = started(capsys)
        sessions = domain(project)
        sessions.transition(session["id"], "merged", force=True, reason="fixture")
        sessions.teardown(session["id"])
        sessions.record_report(session["id"], "done")
        registry = sessions.store.read("registry.json", {"sessions": {}})
        assert session["id"] not in registry["sessions"]
        assert sessions.get(session["id"]).reports[-1]["state"] == "torn_down"


class TestTheRoundTrip:
    def test_a_record_without_reports_loads_with_none(self):
        assert Session.from_dict({"id": "x", "name": "x", "state": "registered"}).reports == []

    def test_an_older_build_keeps_the_reports_it_does_not_know(self):
        """The BUG_061 contract: a build without the field writes it back."""
        raw = Session(id="x", name="x", state="registered", reports=[{"outcome": "done"}])
        doc = raw.to_dict()
        fields = {k: v for k, v in doc.items() if k != "reports"}

        class Older(Session):
            pass

        Older.__dataclass_fields__ = {  # type: ignore[misc]
            k: v for k, v in Session.__dataclass_fields__.items() if k != "reports"
        }
        older = Older.from_dict({**fields, "reports": doc["reports"]})
        assert older._extra == {"reports": [{"outcome": "done"}]}


class TestTheCommand:
    def test_it_records_on_the_named_session(self, project, capsys):
        session = started(capsys)
        assert main(["session", "report", "--outcome", "needs-user", "--note",
                     "pick a name", "--id", session["id"], "--json"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["session"] == session["id"]
        assert out["report"]["outcome"] == "needs-user"
        assert domain(project).get(session["id"]).reports[-1]["note"] == "pick a name"

    def test_the_text_never_spells_the_binding_contract(self, project, capsys):
        session = started(capsys)
        assert main(["session", "report", "--outcome", "done", "--id", session["id"]]) == 0
        text = capsys.readouterr().out
        assert f"session {session['id']} (" not in text
        assert "done" in text

    def test_without_an_id_it_takes_the_latest_session_here(self, project, capsys):
        session = started(capsys)
        assert main(["session", "report", "--outcome", "done", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["session"] == session["id"]

    def test_no_session_here_is_refused(self, project, capsys):
        assert main(["session", "report", "--outcome", "done"]) == 2
        assert "no session" in capsys.readouterr().err

    def test_an_outcome_is_required(self, project, capsys):
        with pytest.raises(SystemExit):
            main(["session", "report"])


class TestTheScanCarriesIt:
    def _row(self, project) -> dict:
        index = ProjectIndex(oracle_home())
        (snapshot,) = scan_all(index, cache=ScanCache(), select="demo")
        (row,) = [r for r in snapshot.rows if r.kind == "session"]
        return row.to_dict()

    def test_no_report_leaves_the_key_out(self, project, capsys):
        started(capsys)
        assert "report" not in self._row(project)

    def test_the_latest_report_is_on_the_row(self, project, capsys):
        session = started(capsys)
        sessions = domain(project)
        sessions.record_report(session["id"], "blocked", "first")
        sessions.record_report(session["id"], "done", "second")
        report = self._row(project)["report"]
        assert (report["outcome"], report["note"]) == ("done", "second")
