"""Running a project's own steps, and recording what they did (ADR-021).

The lifecycle command whose point a step is declared at runs it through
here: ``pipeline advance`` for ``pipeline.enter.<state>``, ``session merge``
and ``session transition`` for ``session.before_merge``, ``session merge``
after the fast-forward for ``session.after_merge``, ``session teardown`` for
``session.before_teardown`` and ``session handoff`` for
``session.before_handoff``. ``invariant step run`` runs one by hand. Each run
is recorded, and the step's rule (``core/rules``) reads the record; nothing
else runs a step's command (D3).

A run that exits with the step's ``owed_exit`` is *owed*: it passes, and a
debt is recorded for the project, which the next pass of the step clears
(D4). A timeout, a missing command and a crash all fail.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..core import git
from ..core import steps as steps_reader
from ..core.errors import ManifestError, UsageError
from ..core.store import JsonStore
from .pipeline import run_command


class StepsDomain:
    def __init__(self, manifest, store: JsonStore | None = None, clock=time.time) -> None:
        self.manifest = manifest
        self.store = store or JsonStore(manifest.state_root)
        self.clock = clock

    def step(self, name: str) -> steps_reader.Step:
        """The declaration ``name`` names, or a refusal naming the declared ones."""
        found = steps_reader.declared(self.manifest)
        if name not in found:
            known = ", ".join(sorted(found)) or "none"
            raise UsageError(
                f"no step named {name!r} is declared under steps; declared: {known}"
            )
        return found[name]

    def run_due(
        self, at: str, *, session: Mapping[str, Any] | None, cwd: Path
    ) -> list[dict]:
        """Run every step due at ``at`` (every one not ``off``), in name order."""
        return [
            self.run(step.name, session=session, cwd=cwd)
            for step in sorted(steps_reader.due(self.manifest, at), key=lambda s: s.name)
        ]

    def run(self, name: str, *, session: Mapping[str, Any] | None, cwd: Path) -> dict:
        """Run one step in the tree its point names, and record the run."""
        step = self.step(name)
        session = dict(session or {})
        tree = Path(steps_reader.tree_of(step.at, session, Path(cwd)))
        before = git.tree_signature(tree) if tree.is_dir() else None
        began = time.monotonic()
        try:
            command = self.manifest.step_command(name)
        except ManifestError as exc:
            command = step.run
            exit_code, tail = None, str(exc)
        else:
            exit_code, tail = self._execute(
                step, command, tree, session, commit=before[0] if before else None
            )
        duration = round(time.monotonic() - began, 3)
        after = git.tree_signature(tree) if tree.is_dir() else None
        if exit_code == 0:
            outcome = steps_reader.PASS
        elif exit_code is not None and exit_code == step.owed_exit:
            outcome = steps_reader.OWED
        else:
            outcome = steps_reader.FAIL
        record = {
            "name": name,
            "at": step.at,
            "command": command,
            "cwd": str(tree),
            "session": session.get("id"),
            "exit_code": exit_code,
            "outcome": outcome,
            "commit": (before or after or (None, None))[0],
            "tree": before[1] if before else None,
            "tree_after": after[1] if after else None,
            "duration": duration,
            "tail": tail,
            "at_time": self.clock(),
            "cleared": 0,
        }
        return self._record(name, record)

    def _execute(
        self,
        step: steps_reader.Step,
        command: str,
        tree: Path,
        session: Mapping[str, Any],
        commit: str | None,
    ) -> tuple[int | None, str]:
        if not tree.is_dir():
            return None, f"the tree {step.name} runs in, {tree}, does not exist"
        env = {
            "INVARIANT_STEP": step.name,
            "INVARIANT_STEP_SESSION": str(session.get("id") or ""),
            "INVARIANT_WORKTREE": str(session.get("worktree") or ""),
        }
        if step.at == steps_reader.AFTER_MERGE:
            # The main checkout's commit: the merged one when the merge runs
            # it, the newest when `step run` runs it again (D2).
            env["INVARIANT_MERGED_SHA"] = commit or ""
        try:
            proc = run_command(command, tree, timeout=float(step.timeout), env=env)
        except subprocess.TimeoutExpired as exc:
            printed = _text(exc.output) + _text(exc.stderr)
            return None, _tail(printed + f"\ntimed out after {step.timeout:g}s")
        except OSError as exc:
            return None, f"could not run {command!r}: {exc}"
        return proc.returncode, _tail((proc.stdout or "") + (proc.stderr or ""))

    def _record(self, name: str, record: dict) -> dict:
        """Append the run; an owed run adds a debt, a pass clears them all."""
        self.store.ensure_root()
        with self.store.locked(steps_reader.RECORD, {}) as data:
            entry = data.get(name)
            if not isinstance(entry, dict):
                entry = data[name] = {}
            runs = [r for r in entry.get("runs") or [] if isinstance(r, dict)]
            debts = [d for d in entry.get("debts") or [] if isinstance(d, dict)]
            if record["outcome"] == steps_reader.OWED:
                debts.append(
                    {
                        "step": name,
                        "session": record["session"],
                        "commit": record["commit"],
                        "line": steps_reader.last_line(record["tail"]),
                        "at": record["at_time"],
                    }
                )
            elif record["outcome"] == steps_reader.PASS:
                record["cleared"] = len(debts)
                debts = []
            runs.append(record)
            entry["runs"] = runs[-steps_reader.RUNS_KEPT:]
            entry["debts"] = debts
        return record

    def listing(self) -> list[dict]:
        """Each declared step with its point, level, last run and open debts."""
        rows = []
        for name, step in sorted(steps_reader.declared(self.manifest).items()):
            rows.append(
                {
                    "name": name,
                    "at": step.at,
                    "level": self.manifest.rule_level(step.rule_id).value,
                    "summary": step.summary,
                    "last": steps_reader.last_run(self.store, name),
                    "debts": len(steps_reader.open_debts(self.store, name)),
                }
            )
        return rows


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _tail(text: str) -> str:
    lines = text.rstrip().splitlines()[-steps_reader.TAIL_LINES:]
    return "\n".join(lines)[-steps_reader.TAIL_CHARS:]
