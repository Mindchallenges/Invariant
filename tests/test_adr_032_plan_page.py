"""ADR-032 D11 and increment 6: the generated plan page.

``core/queue`` renders the page (the queue in order, then each open session's
deliverables as of a date) under the managed marker the rules page carries,
naming the queue record's digest; ``domains/queue`` writes it beside the
queue, under the queue's lock, again after every edit; ``invariant queue
render`` writes it on demand and ``session merge`` at the landing;
``docs.plan_current`` fails when the page's queue part does not name the
record's digest. The starter writes the page and its index links it.

The deliverable tests of the increments table: a toy queue renders a page
whose items are need references (and, behind ``INVARIANT_TEST_SPHINX_BUILD``,
builds strictly); editing the queue without rendering fails
``docs.plan_current``; this repository's page matches its queue.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core import doclock, guidelines, needs
from invariant.core import queue as queue_record
from invariant.core.manifest import Manifest
from invariant.core.rules import RuleContext, get_rule
from invariant.core.store import JsonStore
from invariant.domains import docs as docs_domain
from invariant.domains.queue import QueueDomain

REPO = Path(__file__).resolve().parent.parent
SRC = "invariant-docs"
TODAY = "2026-11-23"


def _git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A fresh project with the Sphinx starter in ``invariant-docs``."""
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


def _domain(root: Path) -> QueueDomain:
    manifest = Manifest.locate(root)
    return QueueDomain(manifest, JsonStore(manifest.state_root), today=lambda: TODAY)


def _file(root: Path, title: str) -> str:
    assert main(["item", "new", "idea", "--title", title, "--json"]) == 0
    return _domain(root).items.list()[-1].id


def _page(root: Path) -> str:
    return (root / SRC / "plan.rst").read_text(encoding="utf-8")


class _Judged:
    """``docs.plan_current``'s verdict, with its outcome as one word."""

    def __init__(self, verdict) -> None:
        self.verdict = verdict
        self.message = verdict.message
        self.fixes = verdict.fixes
        self.status = ("n/a" if verdict.skipped else "pass") if verdict.ok else "fail"


def _gate(root: Path) -> _Judged:
    ctx = RuleContext(manifest=Manifest.locate(root), cwd=root)
    return _Judged(get_rule("docs.plan_current").fn(ctx))


def _entry_rows(text: str) -> list[list[str]]:
    """The queue table's body rows, each as its cells."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        if line.startswith("   * - "):
            rows.append([line[len("   * - "):]])
        elif line.startswith("     - ") and rows:
            rows[-1].append(line[len("     - "):])
    return rows[1:]


def _is_item(ref: str) -> bool:
    return ref.startswith(("IDEA_", "BUG_"))


# -- the record's digest and the page's text --------------------------------------


class TestTheRender:
    def queue(self) -> queue_record.Queue:
        q = queue_record.Queue()
        q.add("IDEA_001", added_by="user")
        q.add("ADR-9 inc 1 *bold* `tick` ref_", added_by="user", serves="IDEA_002")
        q.add("BUG_404", added_by="backlog", mode="propose")
        q.add("triage Q1", added_by="oracle", triage_of="Q1")
        q.add("IDEA_001x", added_by="oracle", retry_of="Q9", state="failed")
        q.mark("Q1", "done")
        return q

    def render(self, q=None, **kw) -> str:
        args = {"source": "docs/queue.json", "needs": {"IDEA_001", "IDEA_002"},
                "is_item": _is_item, "sessions": (), "on": TODAY}
        args.update(kw)
        return queue_record.render_page(self.queue() if q is None else q, **args)

    def test_the_digest_ignores_formatting_and_follows_every_field(self, tmp_path):
        q = self.queue()
        path = tmp_path / "queue.json"
        queue_record.write_queue(path, q)
        again = queue_record.read_queue(path)
        assert queue_record.queue_digest(again) == queue_record.queue_digest(q)
        path.write_text(json.dumps(q.to_dict(), indent=7), encoding="utf-8")
        assert queue_record.queue_digest(queue_record.read_queue(path)) == \
            queue_record.queue_digest(q)
        q.next += 1
        assert queue_record.queue_digest(again) != queue_record.queue_digest(q)
        assert len(queue_record.queue_digest(q)) == 12

    def test_the_page_carries_the_rules_page_marker_and_the_queue_digest(self):
        text = self.render()
        digest, body = guidelines.split_managed(text, "sphinx")
        assert digest == guidelines.digest_of(body.encode("utf-8"))
        assert queue_record.page_digest(text) == queue_record.queue_digest(self.queue())
        assert "Generated by ``invariant queue render`` from ``docs/queue.json``" in text

    def test_the_queue_in_order_with_kind_linked_item_and_status(self):
        rows = _entry_rows(self.render())
        assert rows == [
            ["1", "Q1", "IDEA\\_001", "item", ":need:`IDEA_001`", "done"],
            ["2", "Q2", "ADR\\-9 inc 1 \\*bold\\* \\`tick\\` ref\\_", "label",
             ":need:`IDEA_002`", "queued"],
            ["3", "Q3", "BUG\\_404", "item", "BUG\\_404", "queued, proposed"],
            ["4", "Q4", "triage Q1", "triage", "none", "queued"],
            ["5", "Q5", "IDEA\\_001x", "retry", "IDEA\\_001x", "failed"],
        ]

    def test_an_empty_queue_is_one_sentence(self):
        text = self.render(queue_record.Queue())
        assert "The queue is empty." in text and "list-table" not in text

    def test_session_plans_are_a_dated_literal_snapshot(self):
        sessions = [
            queue_record.SessionPlan(id="bbbb", name="zeta", plan=(
                {"n": 1, "title": "first `odd` *thing*", "done": True},
                {"n": 2, "title": "second\nline", "done": False},
            )),
            queue_record.SessionPlan(id="aaaa", name="alpha", plan=(
                {"n": 1, "title": "only", "done": False},
            )),
            queue_record.SessionPlan(id="cccc", name="unplanned", plan=()),
        ]
        text = self.render(sessions=sessions)
        assert f"as of {TODAY}" in text
        assert text.index("alpha (aaaa)") < text.index("zeta (bbbb)")
        assert "unplanned" not in text
        assert "   [x] 1  first `odd` *thing*" in text
        assert "   [ ] 2  second\n   line" in text

    def test_no_open_plan_and_no_date_each_say_so(self):
        assert "No open session records a deliverable." in self.render()
        undated = self.render(on=None)
        assert "Rendered without the session plans" in undated
        assert "as of" not in undated

    def test_a_session_plan_does_not_change_the_queue_part(self):
        one = self.render()
        two = self.render(sessions=[queue_record.SessionPlan(
            id="a", name="a", plan=({"n": 1, "title": "t", "done": False},))], on="2030-01-01")
        assert queue_record.page_digest(one) == queue_record.page_digest(two)


class TestStanding:
    def test_each_standing(self):
        q = queue_record.Queue()
        q.add("label", added_by="user")
        page = queue_record.render_page(q, source="q", needs=set(), is_item=_is_item)
        assert queue_record.page_standing(None, q) == queue_record.ABSENT
        assert queue_record.page_standing("Plan\n====\n", q) == queue_record.UNMANAGED
        assert queue_record.page_standing(page + "edit\n", q) == queue_record.EDITED
        assert queue_record.page_standing(page, q) == queue_record.CURRENT
        q.add("another", added_by="user")
        assert queue_record.page_standing(page, q) == queue_record.STALE

    def test_a_page_naming_no_digest_is_stale(self):
        text = guidelines.managed_text("Plan\n====\n", "sphinx")
        assert queue_record.page_digest(text) is None
        assert queue_record.page_standing(text, queue_record.Queue()) == queue_record.STALE


class TestManagedState:
    def test_the_states(self):
        text = guidelines.managed_text("Page\n====\n", "sphinx")
        assert guidelines.managed_state(None, text, "sphinx") == "new"
        assert guidelines.managed_state(text, text, "sphinx") == "current"
        other = guidelines.managed_text("Other\n=====\n", "sphinx")
        assert guidelines.managed_state(other, text, "sphinx") == "stale"
        assert guidelines.managed_state(other + "x\n", text, "sphinx") == "edited"
        assert guidelines.managed_state("hand written\n", text, "sphinx") == "unmanaged"

    def test_markdown_carries_its_own_marker(self):
        text = guidelines.managed_text("# Page\n", "markdown")
        assert text.startswith("<!-- invariant-managed: ")
        assert guidelines.managed_state(text, text, "markdown") == "current"


# -- where the page lives -----------------------------------------------------------


def _fake(tmp_path: Path, **settings):
    """A manifest stand-in for the store homes: what ``plan_page`` and
    ``data_paths`` read, and nothing else."""
    store = tmp_path / "store"
    (store / "docs").mkdir(parents=True)
    (store / "docs" / "conf.py").write_text("", encoding="utf-8")
    values = {
        "docs.format": "sphinx",
        "docs.needs.srcdir": "docs",
        "paths.docs": "docs/architecture",
        "paths.queue": str(store / "queue.json"),
        "records": {"docs": "store", "queue": "store"},
        "records.docs": "store",
        "records.queue": "store",
    }
    values.update(settings)
    values["records"] = {"docs": values["records.docs"], "queue": values["records.queue"]}
    return SimpleNamespace(
        get=lambda key, default=None: values.get(key, default),
        root=tmp_path / "repo",
        store_root=store,
        store=SimpleNamespace(root=store),
    )


class TestTheHome:
    def test_beside_the_queue_in_the_main_checkout(self, project, tmp_path):
        worktree = tmp_path / "wt"
        _git(project, "worktree", "add", "-q", str(worktree), "-b", "side")
        page, why = queue_record.plan_page(Manifest.locate(worktree))
        assert why is None
        assert page == (project / SRC / "plan.rst").resolve()

    def test_no_sphinx_no_page(self, project):
        manifest = Manifest.locate(project)
        stand_in = SimpleNamespace(get=lambda k, d=None: "markdown" if k == "docs.format"
                                   else manifest.get(k, d), root=manifest.root)
        page, why = queue_record.plan_page(stand_in)
        assert page is None and "sphinx" in why

    def test_no_source_directory_no_page(self, project):
        shutil.rmtree(project / SRC)
        page, why = queue_record.plan_page(Manifest.locate(project))
        assert page is None and "source directory" in why

    def test_the_store_docs_project_when_both_are_kept_there(self, tmp_path):
        manifest = _fake(tmp_path)
        page, why = queue_record.plan_page(manifest)
        assert why is None
        assert page == (tmp_path / "store" / "docs" / "plan.rst").resolve()
        assert ("queue", "docs/plan.rst") in doclock.data_paths(manifest)
        kind, unit = doclock.classify(manifest, doclock.DocsLockConfig(), page)
        assert (kind, unit) == (doclock.DATA, None)

    @pytest.mark.parametrize("docs, queue", [("store", "repo"), ("repo", "store")])
    def test_two_homes_no_page(self, tmp_path, docs, queue):
        manifest = _fake(tmp_path, **{"records.docs": docs, "records.queue": queue})
        page, why = queue_record.plan_page(manifest)
        assert page is None
        assert "records.docs" in why and "records.queue" in why
        assert all(record != "queue" or not rel.endswith("plan.rst")
                   for record, rel in doclock.data_paths(manifest))


# -- the write ----------------------------------------------------------------------


class TestTheWrite:
    def test_the_starter_writes_the_page_and_links_it(self, project):
        text = _page(project)
        assert queue_record.page_standing(text, queue_record.Queue()) == queue_record.CURRENT
        assert "Rendered without the session plans" in text
        index = (project / SRC / "index.rst").read_text(encoding="utf-8")
        assert "   plan\n" in index

    def test_a_toy_queue_renders_items_as_need_references(self, project):
        item = _file(project, "Something to do")
        domain = _domain(project)
        domain.add(item)
        domain.add("free label")
        domain.add("IDEA_999-ish label", serves=item)
        text = _page(project)
        rows = _entry_rows(text)
        assert rows[0][1:] == ["Q1", item.replace("_", "\\_"), "item", f":need:`{item}`",
                               "queued"]
        assert rows[1][4] == "none"
        assert rows[2][3:5] == ["label", f":need:`{item}`"]
        assert queue_record.page_standing(text, domain.list()) == queue_record.CURRENT
        assert _gate(project).status == "pass"

    def test_every_edit_renders_again(self, project):
        domain = _domain(project)
        domain.add("a")
        domain.add("b")
        domain.move("Q2", 1)
        assert [r[1] for r in _entry_rows(_page(project))] == ["Q2", "Q1"]
        domain.mark("Q2", "started", session="s1")
        assert _entry_rows(_page(project))[0][5] == "started"
        domain.remove("Q1")
        assert [r[1] for r in _entry_rows(_page(project))] == ["Q2"]
        domain.requeue("Q2")
        domain.fail("Q2", "it broke", triage=None, retry=True)
        assert [r[1] for r in _entry_rows(_page(project))] == ["Q3", "Q2"]
        assert queue_record.page_standing(_page(project), domain.list()) == \
            queue_record.CURRENT

    def test_the_mirror_renders_when_it_wrote(self, project):
        item = _file(project, "Mirrored")
        domain = _domain(project)
        out = domain.mirror()
        assert out["added"]
        assert item in _page(project)
        before = (project / SRC / "plan.rst").stat().st_mtime_ns
        assert domain.mirror() == {"added": [], "removed": []}
        assert (project / SRC / "plan.rst").stat().st_mtime_ns == before

    def test_open_session_plans_are_rendered(self, project):
        assert main(["session", "start", "plan-holder", "--id", "abcd1234"]) == 0
        assert main(["session", "plan", "add", "write the page", "--id", "abcd1234"]) == 0
        result = _domain(project).render()
        assert result.written and result.status == "stale"
        text = _page(project)
        assert f"as of {TODAY}" in text
        assert "plan\\-holder\\-abcd1234 (abcd1234)" in text
        assert "[ ] 1  write the page" in text

    def test_a_current_page_is_not_written_again(self, project):
        domain = _domain(project)
        domain.add("a")
        result = domain.render()
        assert (result.status, result.written) == ("current", False)
        assert result.relpath == f"{SRC}/plan.rst"

    def test_a_hand_edit_is_written_over_and_named(self, project):
        domain = _domain(project)
        domain.add("a")
        page = project / SRC / "plan.rst"
        page.write_text(page.read_text(encoding="utf-8") + "hand\n", encoding="utf-8")
        result = domain.render()
        assert (result.status, result.written) == ("edited", True)
        assert "hand\n" not in page.read_text(encoding="utf-8")

    def test_a_hand_written_page_is_never_overwritten(self, project):
        page = project / SRC / "plan.rst"
        page.write_text("Our plan\n========\n", encoding="utf-8")
        domain = _domain(project)
        with pytest.raises(queue_record.PageRefused, match="marker"):
            domain.render()
        assert page.read_text(encoding="utf-8") == "Our plan\n========\n"
        entry = domain.add("still queued")
        assert entry.id == "Q1" and domain.list().entries[0].ref == "still queued"
        assert any("marker" in note for note in domain.notes)

    def test_a_privacy_finding_is_refused_whole(self, project, monkeypatch):
        domain = _domain(project)
        domain.add("a")
        before = _page(project)
        monkeypatch.setattr(queue_record, "render_page",
                            lambda *a, **k: before + "mail someone@example.org\n")
        with pytest.raises(queue_record.PageRefused, match="privacy"):
            domain.render()
        assert _page(project) == before

    def test_no_home_renders_nothing(self, project):
        shutil.rmtree(project / SRC)
        domain = _domain(project)
        domain.add("a")
        assert domain.notes == []
        result = domain.render()
        assert result.path is None and not result.written and "source directory" in result.reason

    def test_a_worktree_renders_the_main_checkout_page(self, project, tmp_path):
        worktree = tmp_path / "wt"
        _git(project, "worktree", "add", "-q", str(worktree), "-b", "side")
        manifest = Manifest.locate(worktree)
        QueueDomain(manifest, JsonStore(manifest.state_root)).add("from the side")
        assert "from the side" in _page(project)
        assert "from the side" not in (worktree / SRC / "plan.rst").read_text(encoding="utf-8")


# -- docs.plan_current ---------------------------------------------------------------


class TestTheGate:
    def test_editing_the_queue_without_rendering_fails(self, project):
        domain = _domain(project)
        domain.add("a")
        assert _gate(project).status == "pass"
        path = project / "docs" / "queue.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["entries"][0]["state"] = "done"
        path.write_text(json.dumps(raw), encoding="utf-8")
        verdict = _gate(project)
        assert verdict.status == "fail"
        assert "stale" in verdict.message
        assert any("invariant queue render" in fix for fix in verdict.fixes)

    def test_only_the_queue_part_is_checked(self, project):
        _domain(project).add("a")
        assert main(["session", "start", "plan-holder", "--id", "abcd1234"]) == 0
        assert main(["session", "plan", "add", "later", "--id", "abcd1234"]) == 0
        assert _gate(project).status == "pass"

    def test_a_missing_page_with_a_queue_fails(self, project):
        _domain(project).add("a")
        (project / SRC / "plan.rst").unlink()
        verdict = _gate(project)
        assert verdict.status == "fail" and "missing" in verdict.message

    def test_no_queue_and_no_page_is_na(self, project):
        (project / SRC / "plan.rst").unlink()
        assert _gate(project).status == "n/a"

    def test_a_hand_edit_and_a_hand_written_page_fail(self, project):
        page = project / SRC / "plan.rst"
        page.write_text(page.read_text(encoding="utf-8") + "x\n", encoding="utf-8")
        assert "by hand" in _gate(project).message
        page.write_text("Plan\n====\n", encoding="utf-8")
        verdict = _gate(project)
        assert verdict.status == "fail" and "marker" in verdict.message

    def test_an_unreadable_queue_fails(self, project):
        (project / "docs").mkdir(exist_ok=True)
        (project / "docs" / "queue.json").write_text("{", encoding="utf-8")
        assert _gate(project).status == "fail"

    def test_no_home_is_na(self, project):
        shutil.rmtree(project / SRC)
        assert _gate(project).status == "n/a"

    def test_warn_in_every_profile(self):
        import yaml

        from invariant.core.manifest import DATA_DIR
        for name in ("loose", "standard", "strict"):
            levels = yaml.safe_load((DATA_DIR / "profiles" / f"{name}.yaml").read_text())
            assert levels["rules"]["docs.plan_current"] == "warn", name


# -- the commands ---------------------------------------------------------------------


def _run(capsys, *argv: str) -> tuple[int, str, str]:
    capsys.readouterr()
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


class TestTheCommands:
    def test_queue_render(self, project, capsys):
        code, out, _ = _run(capsys, "queue", "add", "a")
        assert code == 0
        code, out, _ = _run(capsys, "queue", "render")
        assert code == 0 and f"{SRC}/plan.rst: " in out
        code, out, _ = _run(capsys, "--json", "queue", "render")
        doc = json.loads(out)
        assert doc["path"] == f"{SRC}/plan.rst" and doc["status"] in ("current", "stale")

    def test_queue_render_with_no_home(self, project, capsys):
        shutil.rmtree(project / SRC)
        code, out, _ = _run(capsys, "queue", "render")
        assert code == 0 and "no plan page" in out
        code, out, _ = _run(capsys, "--json", "queue", "render")
        assert json.loads(out)["path"] is None

    def test_a_refused_render_fails_the_command_and_not_an_edit(self, project, capsys):
        (project / SRC / "plan.rst").write_text("Ours\n====\n", encoding="utf-8")
        code, _out, err = _run(capsys, "queue", "add", "a")
        assert code == 0 and "plan page not rendered" in err
        code, _out, err = _run(capsys, "queue", "render")
        assert code == 2 and "marker" in err

    def test_merge_renders_the_page_last(self, project, capsys, tmp_path):
        domain = _domain(project)
        domain.add("a")
        path = project / "docs" / "queue.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["entries"][0]["state"] = "done"
        path.write_text(json.dumps(raw), encoding="utf-8")
        ctx = SimpleNamespace(manifest=Manifest.locate(project),
                              store=JsonStore(Manifest.locate(project).state_root))
        lines = cli_main._plan_page_lines(ctx)
        assert lines == [f"plan page {SRC}/plan.rst: stale"]
        assert _gate(project).status == "pass"

    def test_merge_lines_say_why_not(self, project, monkeypatch):
        ctx = SimpleNamespace(manifest=Manifest.locate(project),
                              store=JsonStore(Manifest.locate(project).state_root))

        def refuse(self):
            raise queue_record.PageRefused("no marker there")

        monkeypatch.setattr(QueueDomain, "render", refuse)
        assert cli_main._plan_page_lines(ctx) == ["plan page not rendered: no marker there"]
        shutil.rmtree(project / SRC)
        monkeypatch.undo()
        assert cli_main._plan_page_lines(ctx) == []


# -- the starter --------------------------------------------------------------------


def test_the_starter_template_names_the_plan_page():
    template = (Path(docs_domain.__file__).resolve().parent.parent / "data" / "scaffold"
                / "sphinx" / "index.rst").read_text(encoding="utf-8")
    assert "   backlog\n   plan\n" in template


# -- this repository -----------------------------------------------------------------


def test_this_repositorys_page_matches_its_queue():
    """The committed page names the committed queue's digest: the reviewer's
    sync commits the two together (DEC_ADR_032_I6)."""
    queue = queue_record.read_queue(REPO / "docs" / "queue.json")
    text = (REPO / "docs" / "plan.rst").read_text(encoding="utf-8")
    assert queue_record.page_standing(text, queue) == queue_record.CURRENT
    index = (REPO / "docs" / "index.rst").read_text(encoding="utf-8")
    assert "\n   plan\n" in index


def test_this_repositorys_page_links_only_declared_needs():
    text = (REPO / "docs" / "plan.rst").read_text(encoding="utf-8")
    manifest = Manifest.locate(REPO)
    declared = QueueDomain.declared_needs(REPO / "docs", needs.types_of(manifest))
    linked = {line.split("`")[1] for line in text.splitlines() if ":need:`" in line}
    assert linked and linked <= declared


# -- the strict build ------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("INVARIANT_TEST_SPHINX_BUILD") or shutil.which("uv") is None,
    reason="set INVARIANT_TEST_SPHINX_BUILD (and have uv) to build the toy page strictly",
)
def test_a_toy_queue_page_builds_strictly(project, capsys, monkeypatch):
    from test_docs_onboarding import Scripted

    item = _file(project, "Something to do")
    domain = _domain(project)
    domain.add(item)
    domain.add("odd *label* `with` marks_ |x| [1]_", serves=item)
    domain.add("IDEA_404 is not a need")
    monkeypatch.setattr(cli_main, "_init_prompter", lambda args: Scripted("not_now"))
    monkeypatch.setattr(docs_domain, "_uv", lambda: shutil.which("uv"))
    code, out, err = _run(capsys, "docs", "venv")
    assert code == 0, out + err
    code, out, err = _run(capsys, "--json", "docs", "build")
    record = json.loads(out)
    assert code == 0 and record["passed"], record
    assert record["diagnostics"] == []


# -- doctor's row ----------------------------------------------------------------------


class TestTheDoctorRow:
    def row(self, root: Path):
        manifest = Manifest.locate(root)
        return cli_main.plan_page_row(manifest, RuleContext(manifest=manifest, cwd=root))

    def test_current_is_a_passing_row(self, project):
        _domain(project).add("a")
        label, ok, detail, warns = self.row(project)
        assert (label, ok, warns) == ("plan page", True, False) and "current" in detail

    def test_stale_warns_with_the_fix(self, project):
        _domain(project).add("a")
        path = project / "docs" / "queue.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["next"] = 9
        path.write_text(json.dumps(raw), encoding="utf-8")
        _label, ok, detail, warns = self.row(project)
        assert ok and warns and "try: invariant queue render" in detail

    def test_na_is_no_row(self, project):
        shutil.rmtree(project / SRC)
        assert self.row(project) is None

    def test_doctor_prints_it(self, project, capsys):
        _domain(project).add("a")
        _code, out, _err = _run(capsys, "doctor")
        assert "plan page" in out
