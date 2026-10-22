"""How the oracle tells a person something: one seam, one real channel.

The watcher hands a :class:`Notification` to whatever :class:`Notifier` it was
built with and knows nothing else about delivery. v1 has one channel, a macOS
notification posted by ``osascript``; a phone push (IDEA_013) would be a
second implementation of the same protocol, not a change to the watcher.

**The AppleScript quoting is injected.** Turning text into an AppleScript
string literal already has an owner, ``hosts/claude_code_launch.applescript_string``,
which ``session handoff`` relies on. The oracle may not import ``hosts/``
(ADR-007 D11), and a second copy of the escaping rule here is two chances for
them to disagree, so :class:`OsascriptNotifier` takes the quoting function as
an argument and the CLI passes the host's (ADR-007 D13) — the same shape as the
``HostProbe`` the scan is given.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ..core.errors import InvariantError

#: How long ``osascript`` may take to post one notification.
OSASCRIPT_TIMEOUT_SECONDS = 10


class NotifyError(InvariantError):
    """A notification could not be delivered."""


@dataclass(frozen=True)
class Notification:
    """One message for the person. ``sound`` ``None`` is a silent banner."""

    title: str
    message: str
    subtitle: str = ""
    sound: str | None = None


class Notifier(Protocol):
    """Delivers a notification, or raises :class:`NotifyError`."""

    def notify(self, notification: Notification) -> None: ...


class OsascriptNotifier:
    """A macOS notification, through ``osascript -e 'display notification …'``.

    The script is ONE argument and no shell runs between this process and
    ``osascript``, so the injected quoting is the only escaping in play.
    """

    def __init__(
        self,
        quote: Callable[[str], str],
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        self.quote = quote
        self.run = run

    def script(self, notification: Notification) -> str:
        q = self.quote
        script = (
            f"display notification {q(notification.message)} "
            f"with title {q(notification.title)}"
        )
        if notification.subtitle:
            script += f" subtitle {q(notification.subtitle)}"
        if notification.sound:
            script += f" sound name {q(notification.sound)}"
        return script

    def notify(self, notification: Notification) -> None:
        try:
            proc = self.run(
                ["osascript", "-e", self.script(notification)],
                capture_output=True,
                text=True,
                check=False,
                timeout=OSASCRIPT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise NotifyError(f"osascript could not post the notification: {exc}") from exc
        if proc.returncode != 0:
            reason = (proc.stderr or proc.stdout or "").strip() or "no reason given"
            raise NotifyError(f"osascript refused the notification: {reason}")


class RecordingNotifier:
    """Keeps what it was asked to send. For tests; ``fail`` makes it refuse."""

    def __init__(self) -> None:
        self.sent: list[Notification] = []
        self.fail = False

    def notify(self, notification: Notification) -> None:
        if self.fail:
            raise NotifyError("the recording notifier was told to fail")
        self.sent.append(notification)
