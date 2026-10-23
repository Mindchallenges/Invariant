"""What a confirmed dashboard action does, and what it shows before it does it.

Every action is confirmed first (ADR-007 D18). A row's recommendation becomes
a :class:`Step`; :func:`confirm_lines` is what the confirm dialog prints — the
exact ``argv``, ``cwd`` and the variables the step adds to the environment —
and nothing runs until the person confirms. Then :class:`Actions` dispatches
on the step's kind:

* ``run`` — the injected runner starts the command as a subprocess: no
  shell, no stdin, the host markers stripped from the environment and the
  dashboard's own ``INVARIANT_SESSION_ID`` dropped, so the command names its
  session only through the step's own ``env``. :func:`run_subprocess` is the
  real runner; the CLI reaches it through ``_oracle_runner``, which the suite
  replaces with :class:`RecordingRunner`;
* ``terminal`` — the injected launcher opens a new host session (the CLI's,
  over the ``session handoff`` launcher);
* ``info`` — nothing. The dialog shows the text and offers no action.

**The oracle never answers a prompt.** A row whose host is waiting on a
permission prompt or a question is told to answer it in its own terminal, and
:func:`refusal` refuses any runnable step on such a row as a second line of
defence. It also refuses an ``argv`` that is not an ``invariant`` command: the
snapshot is a file, and a file is not a reason to run a program.

The oracle may not import ``hosts/``, so the two host facts an action needs —
which variables are host markers, and how a terminal is opened — are injected
by the CLI, as the scan's probe and the notifier's quoting are (ADR-007 D11,
D13).
"""

from __future__ import annotations

import re
import shlex
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.errors import InvariantError
from .recommend import CLI, INFO, RUN, TERMINAL, row_label
from .signals import WAITING

#: Variables of the dashboard's own environment a command must not inherit.
#: The step pins its session explicitly when it needs one; an ambient id would
#: point the command at whichever session ran the dashboard (the
#: ``domains/pipeline`` child-environment rule, BUG_035).
CHILD_ENV_STRIPPED = ("INVARIANT_SESSION_ID",)

#: Seconds a confirmed command may run before it is abandoned.
RUN_TIMEOUT_SECONDS = 600

#: A session name made safe for ``claude --name``.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

KINDS = (RUN, TERMINAL, INFO)

#: (argv, cwd, env) -> (exit status, output)
Runner = Callable[[tuple[str, ...], str, dict[str, str]], tuple[int, str]]


@dataclass(frozen=True)
class Step:
    """A row's recommendation, as the confirm dialog shows it."""

    label: str
    kind: str
    argv: tuple[str, ...]
    cwd: str | None
    env: dict[str, str] = field(default_factory=dict)
    detail: str = ""
    subject: str = ""
    session_id: str | None = None
    session_name: str | None = None


@dataclass(frozen=True)
class Outcome:
    """What a confirmed step did."""

    ok: bool
    text: str
    #: The exit status, for a ``run`` step that ran.
    status: int | None = None


def step_for(row: dict[str, Any]) -> Step | None:
    advice = row.get("recommendation")
    if not advice:
        return None
    return Step(
        label=advice.get("label", ""),
        kind=advice.get("kind", INFO),
        argv=tuple(advice.get("argv") or ()),
        cwd=advice.get("cwd"),
        env=dict(advice.get("env") or {}),
        detail=advice.get("detail") or "",
        subject=row_label(row),
        session_id=row.get("session_id"),
        session_name=row.get("name"),
    )


def command_of(step: Step) -> tuple[Any, ...]:
    """What confirming ``step`` agrees to run, and nothing it only says.

    The kind, the ``argv``, the ``cwd``, the added environment, and for a
    ``terminal`` step the session name the new host is launched under. The
    label and the detail are left out: they describe the step, and the gate
    detail in them moves while the command stays the same.
    """
    name = step.session_name if step.kind == TERMINAL else None
    return (step.kind, step.argv, step.cwd, tuple(sorted(step.env.items())), name)


def refusal(step: Step, row: dict[str, Any]) -> str | None:
    """Why ``step`` may not run from the dashboard, or ``None``."""
    if step.kind not in KINDS:
        return f"unknown kind of step {step.kind!r}; nothing runs"
    if step.kind == INFO:
        return None
    if any(s.get("kind") == WAITING for s in row.get("signals") or []):
        return (
            "its host is waiting on a prompt; answer it in its own terminal. "
            "The oracle never answers one"
        )
    if not step.argv or step.argv[0] != CLI:
        program = step.argv[0] if step.argv else "nothing"
        return f"only invariant commands run from here, not {program!r}"
    if not step.cwd or not Path(step.cwd).is_dir():
        return f"its directory {step.cwd or '(none)'} does not exist"
    return None


def confirm_lines(step: Step, launch: str | None) -> list[str]:
    """The exact command, where it runs, and what its environment adds."""
    lines = [
        f"run   {shlex.join(step.argv)}",
        f"in    {step.cwd}",
    ]
    if step.env:
        lines.extend(f"env   {key}={value}" for key, value in sorted(step.env.items()))
    else:
        lines.append("env   (nothing added)")
    lines.append(
        "      on top of this environment, with Claude Code's host markers and "
        "INVARIANT_SESSION_ID removed"
    )
    if step.kind == TERMINAL:
        lines.append("opens a new Terminal window running:")
        lines.append(f"  {launch}")
    return lines


def child_env(
    base: Mapping[str, str],
    added: Mapping[str, str],
    strip: Callable[[dict[str, str]], dict[str, str]],
) -> dict[str, str]:
    """``base`` without the host markers and the ambient session, plus ``added``."""
    env = strip(dict(base))
    for name in CHILD_ENV_STRIPPED:
        env.pop(name, None)
    env.update(added)
    return env


def terminal_request(step: Step) -> tuple[str, str]:
    """The ``claude --name`` and the first prompt for a step that resumes."""
    name = _UNSAFE.sub("-", step.session_name or step.session_id or "").strip("-.")
    note = (
        f"Resume this invariant session: run `{shlex.join(step.argv)}`, "
        f"then carry on from its plan."
    )
    return name or "invariant-session", note


def run_subprocess(argv: tuple[str, ...], cwd: str, env: dict[str, str]) -> tuple[int, str]:
    """Run ``argv`` in ``cwd``: no shell, no stdin, output captured."""
    proc = subprocess.run(
        list(argv),
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=RUN_TIMEOUT_SECONDS,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


class RecordingRunner:
    """A runner for tests: runs nothing, records each call, answers as told."""

    def __init__(self, status: int = 0, output: str = "") -> None:
        self.status = status
        self.output = output
        self.calls: list[tuple[tuple[str, ...], str, dict[str, str]]] = []

    def __call__(self, argv, cwd, env) -> tuple[int, str]:
        self.calls.append((tuple(argv), cwd, dict(env)))
        return self.status, self.output


class Actions:
    """The dispatch behind the confirm dialog. Every effect is injected."""

    def __init__(
        self,
        runner: Runner,
        environ: Callable[[], Mapping[str, str]],
        strip: Callable[[dict[str, str]], dict[str, str]],
        terminal: Callable[[Step], str] | None = None,
        preview: Callable[[Step], str] | None = None,
    ) -> None:
        self.runner = runner
        self.environ = environ
        self.strip = strip
        self.terminal = terminal
        self.preview = preview

    def launch_line(self, step: Step) -> str | None:
        """The command a ``terminal`` step opens its window with, as previewed."""
        if step.kind != TERMINAL or self.preview is None:
            return None
        try:
            return self.preview(step)
        except InvariantError as exc:
            return f"(cannot preview the launch: {exc})"

    def describe(self, step: Step) -> list[str]:
        """What the confirm dialog shows for ``step``."""
        return confirm_lines(step, self.launch_line(step))

    def perform(self, step: Step) -> Outcome:
        """Do ``step``, which the person has confirmed. ``info`` has no action."""
        if step.kind == RUN:
            env = child_env(self.environ(), step.env, self.strip)
            try:
                status, output = self.runner(step.argv, step.cwd or "", env)
            except subprocess.TimeoutExpired:
                return Outcome(False, f"timed out after {RUN_TIMEOUT_SECONDS}s")
            except OSError as exc:
                return Outcome(False, f"could not start {step.argv[0]}: {exc}")
            return Outcome(status == 0, output, status)
        if step.kind == TERMINAL:
            if self.terminal is None:
                return Outcome(False, "no terminal launcher is available here")
            try:
                return Outcome(True, self.terminal(step))
            except InvariantError as exc:
                return Outcome(False, str(exc))
        raise ValueError(f"a {step.kind!r} step has no action")
