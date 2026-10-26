"""The sweep domain — the whole suite on a cadence, and what it files (ADR-010 D4, D9).

Not an FSM, like ``stats``: there is no lifecycle to advance. A sweep is one
command that runs the suite once, through the same run-and-read seam a
recorded pipeline run uses (``domains/pipeline``), re-runs each new red alone,
diffs against the last COMMITTED sweep (``core/sweep``), files each new finding
through the work-item domain, and commits its own record and the items it
filed. The arithmetic is ``core/sweep``'s; this module owns the effects.

Two guarantees shape every step. It runs only where the commit range it
records is true of a committed tree: the main checkout, on a branch, with no
tracked change (ruling 11). And it never writes outside the stores the
manifest declares (``paths.sweeps``, ``items.path``), never commits anything
it did not write, and never pushes.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from ..adapters import get_adapter
from ..core import git
from ..core import sweep as core
from ..core.errors import LockTimeout, ManifestError, StateError, UsageError
from ..core.manifest import project_relative
from ..core.privacy import PrivacyScanner
from ..core.rules import sweep_below_floor
from ..core.store import JsonStore
from .pipeline import fresh_bundle, joined_selectors, measure_coverage, run_command
from .workitem import WorkItemDomain

#: The store document whose lock keeps two sweeps from filing at once.
LOCK = "sweep.json"


@dataclass
class SweepReport:
    """What one sweep found, filed and committed — the command's whole answer."""

    record: core.SweepRecord
    record_path: str
    baseline: bool
    dry_run: bool
    current: list[str]
    filed: list[dict]
    still_broken: list[dict]
    recovered: list[str]
    unbaselined: list[str]
    flaky: list[str]
    over_cap: bool
    committed: bool = False
    commit: str | None = None
    commit_error: str = ""
    privacy: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    outside_project: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def filed_refs(self) -> list[dict]:
        return [{"key": f["key"], "item": f["item"]} for f in self.filed]

    @property
    def regressions(self) -> list[str]:
        return [
            f"{s['key']} ({s['item']}, {s['status']})"
            for s in self.still_broken
            if s.get("regression")
        ]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["record"] = self.record.to_dict()
        data["regressions"] = self.regressions
        return data


class SweepDomain:
    def __init__(
        self,
        manifest,
        store: JsonStore,
        *,
        runner: Callable | None = None,
        adapter=None,
        today: date | None = None,
    ) -> None:
        self.manifest = manifest
        self.store = store
        self.runner = runner or run_command
        self.adapter = adapter or get_adapter(manifest.ecosystems[0])
        self.today = today or datetime.now(timezone.utc).date()
        self.root = Path(manifest.root)

    @property
    def directory(self) -> str:
        """``paths.sweeps``, spelled project-relative."""
        declared = self.manifest.get("paths.sweeps") or "docs/sweeps"
        return project_relative(declared, [self.root]) or "docs/sweeps"

    # -- reads ---------------------------------------------------------

    def records(self, ref: str = "HEAD") -> tuple[list[core.SweepRecord], list[str]]:
        read = core.read_records(self.root, ref, self.directory)
        if read is None:
            raise UsageError(f"git could not read {ref!r} here, so no sweep record can be read")
        return read

    def status(self, branch: str | None = None) -> dict:
        """The last sweep on ``branch`` (default: this one), its age, and the cadence."""
        branch = branch or git.current_branch(self.root)
        records, unreadable = self.records()
        last = core.latest(records, branch) if branch else None
        schedule = self.manifest.get("methodology.gates.schedule") or {}
        cadence = schedule.get("cadence_days")
        grace = int(schedule.get("grace_days", 3) or 0)
        age = core.age_days(last.ran_on, self.today) if last else None
        return {
            "branch": branch,
            "last": last.to_dict() if last else None,
            "age_days": age,
            "cadence_days": cadence,
            "grace_days": grace,
            "overdue": core.overdue(age, cadence_days=cadence, grace_days=grace),
            "unreadable": unreadable,
        }

    def show(self, which: str | None, branch: str | None = None) -> core.SweepRecord:
        """One record: the latest, or one named by seq, file name or head prefix."""
        branch = branch or git.current_branch(self.root)
        records, _ = self.records()
        mine = [r for r in records if r.branch == branch]
        if not mine:
            raise UsageError(f"no sweep recorded on {branch or 'a detached HEAD'}")
        if not which:
            return core.latest(mine, branch)
        wanted = which.strip().removesuffix(".json")
        for rec in mine:
            if (wanted.isdigit() and rec.seq == int(wanted)) or rec.name.removesuffix(
                ".json"
            ) == wanted or (len(wanted) >= 7 and rec.head.startswith(wanted)):
                return rec
        raise UsageError(f"no sweep record on {branch} matches {which!r}")

    # -- the sweep -----------------------------------------------------

    def run(self, dry_run: bool = False) -> SweepReport:
        branch = self._preconditions()
        # A dry run takes the lock too: it files nothing, but it runs the suite
        # into the same per-run bundles a real sweep would be reading.
        try:
            with self.store.locked(LOCK, timeout=0):
                return self._sweep(branch, dry_run=dry_run)
        except LockTimeout:
            raise StateError(
                "another sweep is running against this repository; two at once "
                "would file the same findings twice. Nothing was run."
            ) from None

    def _preconditions(self) -> str:
        """Refuse before running unless the commit range will be true (ruling 11)."""
        if not git.is_repo(self.root):
            raise UsageError(
                f"{self.root} is not inside a git repository; a sweep records a "
                "commit range and commits its record, so it runs only in one"
            )
        if git.in_linked_worktree(self.root) is not False:
            raise UsageError(
                "a sweep runs only in the main checkout, never in a session "
                "worktree: there it would be a whole-suite run on a change by "
                f"another name. Run it from {git.main_checkout(self.root) or 'the main checkout'}."
            )
        branch = git.current_branch(self.root)
        if branch is None:
            raise UsageError(
                "HEAD is detached; a sweep records its findings against a branch, "
                "so check one out first"
            )
        changed = git.tracked_changes(self.root)
        if changed is None:
            raise UsageError("git could not say whether the tree is clean; nothing was run")
        if changed:
            raise UsageError(
                "the tree has tracked changes, and a sweep's commit range is only "
                "true of a committed tree. Commit or stash them first:\n"
                + "".join(f"  {path}\n" for path in changed)
                + "Untracked files do not count."
            )
        if self.manifest.get("items.backend", "markdown") == "none":
            raise UsageError(
                "items.backend is 'none', and a sweep files its findings as work "
                "items. Configure a backend first; nothing was run."
            )
        _, problem = sweep_below_floor(self.manifest, {})
        if problem:
            raise UsageError(f"{problem}. Nothing was run.")
        return branch

    def _sweep(self, branch: str, dry_run: bool) -> SweepReport:
        head = git.resolve_commit(self.root, "HEAD") or ""
        records, unreadable = self.records()
        previous = core.latest(records, branch)
        notes = [f"unreadable sweep record skipped: {p}" for p in unreadable]

        key = "coverage" if "coverage.meets_floor" in self.manifest.scheduled_gates() else "test"
        outcome, stdout, stderr, measured, bundle = self._run_suite(key)
        failing = self.adapter.failing_test_ids(stdout, stderr, bundle)
        files, outside = self._relative(measured)
        floor = float(self.manifest.get("methodology.gates.coverage_min", 0) or 0)

        below_now = self._below(files) if key == "coverage" else {}
        below_before = (
            self._below(previous.files)
            if previous is not None and previous.command == "coverage"
            else None
        )
        found = core.diff(
            previous,
            passed=outcome.passed,
            failing=failing,
            below_now=below_now,
            below_before=below_before,
        )
        red_again = None
        if core.needs_reruns(found):
            red_again = {}
            for test_id in found.new_test_ids:
                answer = self._red_alone(test_id)
                if answer is None:
                    notes.append(f"{test_id} could not be re-run alone; kept as red")
                else:
                    red_again[test_id] = answer
        settled = core.settle(found, red_again)
        if found.unbaselined:
            notes.append(
                "the last sweep measured no coverage, so files under the floor "
                "now are reported and not filed as drops"
            )

        open_items = [] if found.baseline else self._open_items()
        to_file, claimed = core.dedup(settled.to_file, open_items)
        _, known = core.dedup(found.still_broken, open_items)
        known_keys = {k["key"] for k in known}
        still = claimed + known + [
            {"key": k, "item": None, "status": None, "regression": False}
            for k in found.still_broken
            if k not in known_keys
        ]

        rng = f"{previous.head if previous else ''}..{head}"
        context = _Context(self.today.isoformat(), key, outcome, rng, floor, below_now)
        filed, item_paths = self._file(to_file, context, settled, dry_run)

        recorded_failing = (
            None if failing is None else [t for t in failing if t not in settled.flaky]
        )
        record = core.SweepRecord(
            seq=(previous.seq + 1) if previous else 1,
            ran_on=self.today.isoformat(),
            branch=branch,
            head=head,
            previous=previous.head if previous else None,
            command=key,
            executed=outcome.total,
            failed=outcome.failed,
            passed=outcome.passed,
            failing=recorded_failing,
            flaky=list(settled.flaky),
            files=files,
            floor=floor,
            hazards=[h.id for h in outcome.hazards],
            filed=[{"key": f["key"], "item": f["item"]} for f in filed],
            still_broken=[{"key": s["key"], "item": s["item"], "status": s["status"]} for s in still],
            recovered=list(found.recovered),
        )
        record_path = f"{self.directory}/{record.name}"
        report = SweepReport(
            record=record,
            record_path=record_path,
            baseline=found.baseline,
            dry_run=dry_run,
            current=list(found.current),
            filed=filed,
            still_broken=still,
            recovered=list(found.recovered),
            unbaselined=list(found.unbaselined),
            flaky=list(settled.flaky),
            over_cap=settled.over_cap,
            unreadable=list(unreadable),
            outside_project=outside,
            notes=notes,
        )
        if not dry_run:
            self._record_and_commit(report, item_paths)
        return report

    # -- the run -------------------------------------------------------

    def _bundle(self, kind: str) -> Path:
        """A per-run result bundle under the state root; the lock keeps it unshared."""
        return fresh_bundle(Path(self.store.root) / "sweeps" / f"{kind}.xcresult")

    def _run_suite(self, key: str):
        """One run of the configured command, through the shared seam (D4 step 2)."""
        bundle = self._bundle(key)
        command = self.manifest.command_for(key, result_bundle=str(bundle))
        if key == "coverage":
            run = measure_coverage(
                self.manifest, self.adapter, command, bundle, self.root, runner=self.runner
            )
            return run.outcome, run.stdout, run.stderr, run.measured, bundle
        proc = self.runner(command, self.root)
        outcome = self.adapter.read_run(proc.stdout, proc.stderr, proc.returncode, bundle=bundle)
        if outcome.total == 0:
            raise UsageError(
                "the sweep's test run executed no tests, so it describes nothing.\n"
                f"  exit: {proc.returncode}\n"
                "  Usually a build or launch failure. Nothing was recorded and "
                "nothing was filed."
            )
        return outcome, proc.stdout, proc.stderr, {}, bundle

    def _red_alone(self, test_id: str) -> bool | None:
        """Whether a new red is red again when run alone; ``None`` if it cannot be run.

        A re-run that executed nothing is not proof of green: the selector may
        simply have matched nothing, so it is read as red.
        """
        bundle = self._bundle("rerun")
        try:
            selector = joined_selectors(self.adapter, [shlex.quote(test_id)])
            command = self.manifest.command_for(
                "test_one", result_bundle=str(bundle), selector=selector
            )
        except (ManifestError, UsageError):
            return None
        proc = self.runner(command, self.root)
        result = self.adapter.read_run(proc.stdout, proc.stderr, proc.returncode, bundle=bundle)
        return not (result.passed and result.total != 0)

    def _relative(self, measured: dict[str, float]) -> tuple[dict[str, float], int]:
        """Measured rows by project-relative path; rows outside the project dropped.

        The record is committed, so an absolute path in it would be a home path
        published in history (D9).
        """
        files: dict[str, float] = {}
        outside = 0
        for path, pct in measured.items():
            rel = project_relative(path, [self.root])
            if rel is None or rel == ".":
                outside += 1
                continue
            files[rel] = float(pct)
        return files, outside

    def _below(self, files: dict[str, float]) -> dict[str, float]:
        below, _ = sweep_below_floor(self.manifest, files)
        return below or {}

    # -- filing --------------------------------------------------------

    def _open_items(self) -> list[tuple[str, str, str]]:
        return [
            (str(item.fields["sweep_key"]), item.id, item.status)
            for item in WorkItemDomain(self.manifest).list(open_only=True)
            if item.fields.get("sweep_key")
        ]

    def _file(self, keys, context: _Context, settled: core.Settled, dry_run: bool):
        """File each key as a bug, and say which store paths the filing changed."""
        store = project_relative(self.manifest.get("items.path") or "docs/backlog", [self.root])
        before = None if dry_run else git.status_paths(self.root, store or ".")
        filed: list[dict] = []
        workitems = WorkItemDomain(self.manifest)
        for key in keys:
            title, body = _describe(key, context, settled)
            item = None
            if not dry_run:
                item = workitems.new(
                    "bug",
                    title,
                    fields={"sweep_key": key, "sweep_range": context.range},
                    body=body,
                ).id
            filed.append({"key": key, "item": item, "title": title})
        if dry_run or not keys:
            return filed, []
        after = git.status_paths(self.root, store or ".") or set()
        changed = sorted(line[3:] for line in after - (before or set()))
        return filed, changed

    # -- the record and its commit (D9) ------------------------------------

    def _record_and_commit(self, report: SweepReport, item_paths: list[str]) -> None:
        target = self.root / report.record_path
        target.parent.mkdir(parents=True, exist_ok=True)
        text = report.record.to_json()
        target.write_text(text, encoding="utf-8")

        message = (
            f"Sweep: {report.record.ran_on} {report.record.head[:12]} "
            f"({len(report.filed)} filed)"
        )
        if self.manifest.get("privacy.enabled", True):
            scanner = PrivacyScanner.from_manifest(self.manifest)
            findings = scanner.scan_text(text, path=Path(report.record_path))
            findings += scanner.scan_text(message, path=Path("commit message"))
            for rel in item_paths:
                findings += scanner.scan_file(self.root / rel)
            report.privacy = [f.describe() for f in findings]
        if report.privacy:
            report.commit_error = (
                "the privacy scan found personal data, so nothing was committed; "
                "the record and the filed items are left in the working tree"
            )
            return

        ok, output = git.commit_only(self.root, [report.record_path, *item_paths], message)
        report.committed = ok
        if ok:
            report.commit = git.resolve_commit(self.root, "HEAD")
        else:
            report.commit_error = output or "git commit failed"


@dataclass(frozen=True)
class _Context:
    """What every filed item's body names about the run that found it."""

    ran_on: str
    command: str
    outcome: object
    range: str
    floor: float
    below: dict


def _describe(key: str, ctx: _Context, settled: core.Settled) -> tuple[str, str]:
    """A filed item's title and body: what was found, on which run, in which range."""
    outcome = ctx.outcome
    run = (
        f"The {ctx.ran_on} sweep ran `commands.{ctx.command}`: "
        f"{outcome.total if outcome.total is not None else '?'} executed, "
        f"{outcome.failed if outcome.failed is not None else '?'} failing."
    )
    tail = (
        f"\n\nCommit range since the last sweep: `{ctx.range}`. Filed by "
        "`invariant sweep run`; this key is not filed again while this item is open."
    )
    if outcome.hazards:
        tail += "\n\nHazards on the run: " + ", ".join(h.id for h in outcome.hazards) + "."
    test_id = core.test_id_of(key)
    if key.startswith("flaky:"):
        return (
            f"Sweep: {test_id} is flaky",
            f"{run} `{test_id}` failed in the whole-suite run and passed when "
            "re-run alone: red under load, green alone." + tail,
        )
    if test_id is not None:
        return (
            f"Sweep: {test_id} fails",
            f"{run} `{test_id}` failed in the whole-suite run and again when "
            "re-run alone." + tail,
        )
    if key.startswith("floor:"):
        path = key[len("floor:"):]
        pct = ctx.below.get(path, 0.0)
        failing = (
            f" The run that measured it had {outcome.failed} failing test(s), and "
            "a test that aborts early lowers coverage it did not cause."
            if outcome.failed
            else ""
        )
        return (
            f"Sweep: {path} is below the coverage floor ({pct:g}% < {ctx.floor:g}%)",
            f"{run} `{path}` measured {pct:g}% against the {ctx.floor:g}% floor, "
            "and was not below it at the last sweep." + failing + tail,
        )
    if settled.over_cap:
        new = settled.reds
        return (
            f"Sweep: {new} tests went red in one run",
            f"{run} {new} tests failed that were not red at the last sweep, more "
            f"than the {core.RERUN_CAP} the sweep re-runs one by one, so this one "
            "item stands for all of them: the run looks systemic (a broken build, "
            "a fixture, an environment leak) rather than many independent defects."
            + tail,
        )
    return (
        "Sweep: the suite is red and its failing tests cannot be named",
        f"{run} The adapter could not name the failing tests, so the run is filed "
        "once, as `suite:red`." + tail,
    )
