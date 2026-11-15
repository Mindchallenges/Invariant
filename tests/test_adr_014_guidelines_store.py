# ruff: noqa: F811 - the fixtures imported below are named as parameters
"""ADR-014 increment 4c-2: the guidelines in a store (D9).

On 4b's two repositories linked to one store, widgets keeps its guidelines in
the store: the ruleset and the baseline are store-relative, read from the
store's main checkout (the policy tree), while the change's files are read
from the session's tree (the change tree). The shrink gate reads the base
copy in the store at the session's first docs-lock span's
``acquired_at_sha``, or at the store's HEAD when the session held no span. A
CLI write into store policy takes the on-access grant, or refuses naming
``docs lock``. Both guidelines refusals under a store are lifted, and a store
project's starter writes its ruleset into the store. An in-repo project, this
repository's pin included, is unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from test_adr_014_docs_lock import (  # noqa: F401 - fixtures are used by name
    GADGETS,
    WIDGETS,
    git,
    run,
    run_json,
    two,
)
from test_adr_014_docs_lock_host import hosts  # noqa: F401 - the fixture, by name
from test_adr_014_per_leg_gates import ctx_for, gate, passed
from test_docs_onboarding import (  # noqa: F401 - fixtures are used by name
    SRC,
    init,
    interactive,
    toy,
    unrelated,
)

from invariant.core import findings
from invariant.core.errors import UsageError
from invariant.core.findings import fingerprint
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.core.rules import RuleContext, get_rule, ruleset_pin
from invariant.domains.session import SessionDomain

SID = "aaaa1111"
OTHER = "bbbb2222"
RULESET_REL = "docs/guidelines/ruleset.yaml"
BASELINE_REL = "docs/guidelines/baseline.txt"
PRINT = "style.no-print"
SHRINKS = "guidelines.baseline_only_shrinks"

RULESET = """\
version: 1
sections:
  - id: style
    title: Style
rules:
  - id: style.no-print
    section: style
    title: No print calls in the application
    statement: The application never prints.
    severity: blocker
    phases: [review]
    applies_to: ["app/**"]
    check: {kind: pattern, engine: regex, in: any, match: '\\bprint\\('}
"""

LINE = "print(1)"


def stamp(line: str = LINE) -> str:
    return fingerprint(PRINT, findings.collapse(line))


def baseline_text(count: int) -> str:
    return f"{PRINT}  app/m.py  {stamp()}  {count}\n" if count else ""


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def commit(root: Path, message: str = "change") -> None:
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)


def keep_guidelines_in_store(store: Path, identity: str, **guidelines: str) -> None:
    body = yaml.safe_load((store / "invariant.yaml").read_text(encoding="utf-8"))
    block = body["store"]["repos"][identity]
    block.setdefault("records", {})["guidelines"] = "store"
    block["guidelines"] = dict(guidelines)
    (store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


@pytest.fixture
def gstore(hosts):
    """``hosts``, widgets keeping its ruleset and baseline in the store: the
    base branch's ``app/m.py`` prints once, and the baseline records it."""
    keep_guidelines_in_store(
        hosts.store, WIDGETS, ruleset=RULESET_REL, baseline=BASELINE_REL
    )
    write(hosts.store, RULESET_REL, RULESET)
    write(hosts.store, BASELINE_REL, baseline_text(1))
    commit(hosts.store, "guidelines")
    write(hosts.widgets, "app/m.py", f"{LINE}\n")
    commit(hosts.widgets, "app")
    hosts.baseline = hosts.store / BASELINE_REL
    return hosts


def lock(hosts, capsys, *units: str, sid: str = SID, repo: Path | None = None) -> None:
    hosts.at(repo or hosts.widgets)
    code, out = run_json(capsys, "docs", "lock", *units, "--id", sid)
    assert code == 0, out


def store_spans(repo: Path, sid: str = SID) -> list[dict]:
    record = SessionDomain(Manifest.locate(repo)).require(sid).to_dict()
    legs = [leg for leg in record.get("legs") or [] if leg.get("kind") == "store"]
    return legs[0]["lock_spans"] if legs else []


# -- the proof ---------------------------------------------------------------


class TestTheProof:
    def test_a_grown_store_baseline_is_refused(self, gstore, capsys):
        write(gstore.widgets, "app/m.py", f"{LINE}\n{LINE}\n")
        lock(gstore, capsys, "guidelines")
        write(gstore.store, BASELINE_REL, baseline_text(2))
        commit(gstore.store, "raise the count")
        refused = gate(gstore.widgets, SHRINKS)
        assert not refused.ok
        assert "grew" in refused.message
        assert PRINT in refused.message
        # The base copy is the store's, at the span the session took.
        start = store_spans(gstore.widgets)[0]["acquired_at_sha"]
        assert start[:12] in (refused.detail or "")
        assert "store" in (refused.detail or "")

    def test_a_pruned_store_baseline_is_accepted(self, gstore, capsys):
        write(gstore.widgets, "app/m.py", "x = 1\n")
        gstore.at(gstore.widgets)
        code, text = run(capsys, "guidelines", "baseline", "--prune", "--id", SID)
        assert code == 0, text
        assert gstore.baseline.read_text(encoding="utf-8").count(PRINT) == 0
        # The write took the unit on access, so the session holds a span of it.
        assert [s["unit"] for s in store_spans(gstore.widgets)] == ["area:guidelines"]
        commit(gstore.store, "prune")
        accepted = gate(gstore.widgets, SHRINKS)
        assert passed(accepted), accepted.message
        assert "only shrank" in accepted.message

    def test_this_repositorys_ruleset_pin_is_unchanged(self):
        root = Path(__file__).resolve().parents[1]
        manifest = Manifest.locate(root)
        assert manifest.location.kind == "repo"
        pin = ruleset_pin(RuleContext(manifest=manifest, cwd=root))
        assert pin == {
            "path": "docs/guidelines/ruleset.yaml",
            "digest": "02ed45e4db9d",
            "error": None,
        }


# -- the base the shrink gate reads -----------------------------------------------


class TestTheStoreBase:
    def test_a_session_that_held_no_span_compares_against_the_stores_head(self, gstore):
        write(gstore.widgets, "app/m.py", f"{LINE}\n{LINE}\n")
        write(gstore.store, BASELINE_REL, baseline_text(2))  # uncommitted, no lock
        assert store_spans(gstore.widgets) == []
        refused = gate(gstore.widgets, SHRINKS)
        assert not refused.ok
        assert "grew" in refused.message
        assert "HEAD" in (refused.detail or "")

    def test_growth_committed_before_the_span_is_the_base(self, gstore, capsys):
        """The base is where the session's first span began: what the store
        held then, not what the session committed under the lock."""
        write(gstore.widgets, "app/m.py", f"{LINE}\n{LINE}\n")
        write(gstore.store, BASELINE_REL, baseline_text(2))
        commit(gstore.store, "another session's growth, landed earlier")
        lock(gstore, capsys, "guidelines")
        assert passed(gate(gstore.widgets, SHRINKS))

    def test_the_first_span_is_the_base_when_a_unit_is_taken_again(self, gstore, capsys):
        lock(gstore, capsys, "guidelines")
        first = store_spans(gstore.widgets)[0]["acquired_at_sha"]
        gstore.at(gstore.widgets)
        code, out = run_json(capsys, "docs", "unlock", "--id", SID)
        assert code == 0, out
        write(gstore.store, "docs/notes.rst", "moved on\n")
        commit(gstore.store, "the store moves on")
        lock(gstore, capsys, "guidelines")
        spans = store_spans(gstore.widgets)
        assert len(spans) == 2 and spans[1]["acquired_at_sha"] != first
        write(gstore.widgets, "app/m.py", f"{LINE}\n{LINE}\n")
        write(gstore.store, BASELINE_REL, baseline_text(2))
        refused = gate(gstore.widgets, SHRINKS)
        assert not refused.ok
        assert first[:12] in (refused.detail or "")

    def test_a_store_manifest_absent_at_the_base_fails_and_admits_nothing(
        self, gstore, capsys, monkeypatch
    ):
        lock(gstore, capsys, "guidelines")
        empty_tree = git(gstore.store, "hash-object", "-t", "tree", "/dev/null")
        bare = git(gstore.store, "commit-tree", empty_tree, "-m", "nothing")
        manifest = Manifest.locate(gstore.widgets)
        record = SessionDomain(manifest).require(SID)
        record.legs[0]["lock_spans"][0]["acquired_at_sha"] = bare
        ctx = ctx_for(gstore.widgets)
        ctx = RuleContext(
            manifest=ctx.manifest, cwd=ctx.cwd, session=record.to_dict(),
            pipeline=ctx.pipeline,
        )
        write(gstore.widgets, "app/m.py", f"{LINE}\n{LINE}\n")
        write(gstore.store, BASELINE_REL, baseline_text(2))
        verdict = get_rule(SHRINKS).fn(ctx)
        assert not verdict.ok
        assert "invariant.yaml" in verdict.message
        assert "admitted" not in (verdict.detail or "")

    def test_renames_are_read_in_the_repository(self, gstore, capsys):
        """An entry names a code path, so a move is the repository's rename."""
        git(gstore.widgets, "mv", "app/m.py", "app/n.py")
        lock(gstore, capsys, "guidelines")
        write(gstore.store, BASELINE_REL, f"{PRINT}  app/n.py  {stamp()}  1\n")
        commit(gstore.store, "follow the move")
        verdict = gate(gstore.widgets, SHRINKS)
        assert passed(verdict), verdict.message
        assert "app/m.py -> app/n.py" in (verdict.detail or "")


# -- the two trees ----------------------------------------------------------------


class TestThePolicyTreeAndTheChangeTree:
    def test_the_ruleset_is_read_from_the_store(self, gstore):
        verdict = gate(gstore.widgets, "guidelines.ruleset_valid")
        assert passed(verdict), verdict.message
        pin = ruleset_pin(ctx_for(gstore.widgets))
        assert pin["path"] == RULESET_REL and pin["error"] is None

    def test_the_change_is_scanned_in_the_repository(self, gstore):
        write(gstore.widgets, "app/m.py", f"{LINE}\nprint(2)\n")
        verdict = gate(gstore.widgets, "guidelines.mechanical_clean")
        assert not verdict.ok
        assert "app/m.py:2" in verdict.message
        assert "1 baselined" in verdict.message

    def test_render_writes_the_checklist_into_the_store_under_the_grant(
        self, gstore, capsys
    ):
        gstore.at(gstore.widgets)
        code, text = run(capsys, "guidelines", "render", "--id", SID)
        assert code == 0, text
        assert (gstore.store / "docs" / "guidelines" / "checklist.md").is_file()
        assert not (gstore.widgets / "docs").exists()
        assert [s["unit"] for s in store_spans(gstore.widgets)] == ["area:guidelines"]
        assert passed(gate(gstore.widgets, "guidelines.generated_current"))

    def test_an_edit_in_the_repository_is_advised_against_the_store_ruleset(self, gstore):
        from invariant.core.rules import edit_advisory

        target = write(gstore.widgets, "app/m.py", f"{LINE}\nprint(2)\n")
        ctx = ctx_for(gstore.widgets)
        advisory = edit_advisory(ctx.manifest, target, lambda: ctx)
        assert advisory is not None and advisory.problem is None
        assert [f.line for f in advisory.findings] == [2]


# -- CLI writes into store policy ---------------------------------------------------


class TestCliWrites:
    def test_a_write_into_a_unit_another_session_holds_is_refused_naming_docs_lock(
        self, gstore, capsys
    ):
        lock(gstore, capsys, "guidelines", sid=OTHER, repo=gstore.gadgets)
        write(gstore.widgets, "app/m.py", "x = 1\n")
        before = gstore.baseline.read_text(encoding="utf-8")
        gstore.at(gstore.widgets)
        code, text = run(capsys, "guidelines", "baseline", "--prune", "--id", SID)
        assert code == 2, text
        assert "docs lock" in text and OTHER in text
        assert gstore.baseline.read_text(encoding="utf-8") == before

    def test_a_dry_run_takes_no_unit(self, gstore, capsys):
        write(gstore.widgets, "app/m.py", "x = 1\n")
        gstore.at(gstore.widgets)
        code, text = run(
            capsys, "guidelines", "baseline", "--prune", "--dry-run", "--id", SID
        )
        assert code == 0, text
        assert store_spans(gstore.widgets) == []

    def test_a_write_with_no_session_goes_only_into_a_free_unit(self, gstore, capsys):
        sessions = SessionDomain(Manifest.locate(gstore.widgets))
        sessions.docs_write(None, [gstore.baseline])  # free: no refusal
        lock(gstore, capsys, "guidelines", sid=OTHER, repo=gstore.gadgets)
        with pytest.raises(UsageError, match="docs lock") as caught:
            sessions.docs_write(None, [gstore.baseline])
        assert OTHER in str(caught.value)

    def test_a_write_outside_the_lock_is_not_governed(self, gstore):
        sessions = SessionDomain(Manifest.locate(gstore.widgets))
        sessions.docs_write(SID, [gstore.widgets / "CLAUDE.md"])
        assert store_spans(gstore.widgets) == []


# -- the refusals lifted, and the manifest's location ------------------------------


class TestTheRefusalsAndTheLocation:
    def test_a_store_project_loads_its_guidelines_from_the_store(self, gstore):
        manifest = Manifest.locate(gstore.widgets)
        assert manifest.get("records.guidelines") == "store"
        joined = Path(manifest.get("guidelines.baseline"))
        assert joined.resolve() == (gstore.store / BASELINE_REL).resolve()

    def test_the_manifests_location_is_a_property(self, gstore, tmp_path):
        store = Manifest.locate(gstore.widgets).location
        assert (store.tree.resolve(), store.relpath, store.kind) == (
            gstore.store.resolve(), "invariant.yaml", "store"
        )
        raw = yaml.safe_load((gstore.store / "invariant.yaml").read_text(encoding="utf-8"))
        assert store.level(raw)["guidelines"] == {
            "ruleset": RULESET_REL, "baseline": BASELINE_REL,
        }
        root = tmp_path / "plain"
        (root / ".claude").mkdir(parents=True)
        (root / MANIFEST_RELPATH).write_text("version: 1\n", encoding="utf-8")
        here = Manifest.locate(root).location
        assert (here.tree.resolve(), here.relpath, here.kind) == (
            root.resolve(), ".claude/invariant.yaml", "repo"
        )
        assert here.level({"guidelines": {"baseline": "b"}}) == {"guidelines": {"baseline": "b"}}


# -- the store starter --------------------------------------------------------------


class TestTheStoreStarter:
    def test_the_starter_writes_its_ruleset_into_the_store(
        self, unrelated, capsys, interactive
    ):
        interactive("store", "", "", "not_now")
        code, text = init(capsys)
        assert code == 0, text
        m = Manifest.locate(unrelated)
        assert m.get("records.guidelines") == "store"
        ruleset = m.store.root / SRC / "guidelines" / "ruleset.yaml"
        assert ruleset.is_file()
        assert Path(m.get("guidelines.ruleset")).resolve() == ruleset.resolve()
        assert not (unrelated / SRC / "guidelines").exists()
        assert passed(get_rule("guidelines.ruleset_valid").fn(
            RuleContext(manifest=m, cwd=unrelated)
        ))


# -- the edges ------------------------------------------------------------------------


class TestTheEdges:
    def test_a_guessed_session_is_granted_nothing(self, gstore):
        sessions = SessionDomain(Manifest.locate(gstore.widgets))
        with pytest.raises(UsageError, match="does not hold"):
            sessions.docs_write(SID, [gstore.baseline], proven=False)
        assert store_spans(gstore.widgets) == []

    def test_a_queued_session_is_refused_naming_its_place(self, gstore, capsys):
        lock(gstore, capsys, "guidelines", sid=OTHER, repo=gstore.gadgets)
        lock(gstore, capsys, "guidelines")
        sessions = SessionDomain(Manifest.locate(gstore.widgets))
        with pytest.raises(UsageError, match="queued"):
            sessions.docs_write(SID, [gstore.baseline])
        gstore.at(gstore.gadgets)
        code, out = run_json(capsys, "docs", "unlock", "--id", OTHER)
        assert code == 0, out
        # Handed over to the queue's head: now the write is under its lease.
        sessions.docs_write(SID, [gstore.baseline])
        with pytest.raises(UsageError, match=f"held by session {SID}"):
            sessions.docs_write(None, [gstore.store / "docs/guidelines/other.txt"])
        sessions.docs_write(None, [gstore.store / "docs/architecture/new.rst"])

    def test_a_store_path_is_exempt_only_under_a_data_record(self, gstore):
        from invariant.core.rules import _policy_exempt

        ctx = ctx_for(gstore.widgets)
        assert _policy_exempt(ctx, gstore.store, gstore.store / "memory" / "x.md")
        assert not _policy_exempt(ctx, gstore.store, gstore.store / BASELINE_REL)

    def test_the_mandate_is_read_from_the_stores_level(self, gstore):
        verdict = gate(gstore.widgets, "docs.mandate_kept")
        assert verdict.ok
        assert "HEAD" in verdict.message

    def test_an_overlay_is_read_from_the_store(self, gstore):
        from invariant.hosts.claude_code import ClaudeCodeHost

        body = yaml.safe_load((gstore.store / "invariant.yaml").read_text(encoding="utf-8"))
        body["store"]["repos"][WIDGETS]["agents"] = {
            "overlays": {"feature-architect": "docs/guidelines/fa.yaml"}
        }
        (gstore.store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
        write(gstore.store, "docs/guidelines/fa.yaml", "version: 1\n")
        manifest = Manifest.locate(gstore.widgets)
        host = ClaudeCodeHost(manifest.root, manifest=manifest)
        assert host._overlay_text("feature-architect") == (
            "docs/guidelines/fa.yaml", "version: 1\n"
        )

    def test_a_pointer_only_project_is_located_at_its_pointer(self, tmp_path):
        from invariant.cli.onboarding import carries_manifest

        repo = tmp_path / "solo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        (repo / ".invariant.yaml").write_text("version: 1\n", encoding="utf-8")
        manifest = Manifest.locate(repo)
        assert (manifest.location.kind, manifest.location.relpath) == (
            "pointer", ".invariant.yaml"
        )
        assert not carries_manifest(manifest)
        git(repo, "add", "-A")
        git(repo, "-c", "user.email=t@example.invalid", "-c", "user.name=T", "commit", "-qm", "p")
        assert carries_manifest(manifest)
