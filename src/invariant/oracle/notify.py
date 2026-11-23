"""How the oracle tells a person something: one seam, and the channels behind it.

The watcher and the chain step hand a :class:`Notification` to whatever
:class:`Notifier` they were built with and know nothing else about delivery.
The channels are a macOS notification posted by ``osascript`` and, since
ADR-027 increment 4, web push to each phone that turned it on
(``oracle/push``). :class:`FanOutNotifier` joins them behind the one seam, so
both get every alert and every chain notice under the same ledgers, and
neither channel's failure stops the other (ruling 17).

**Delivered means one channel delivered** (DEC_ADR_027_D38). The ledgers
re-send an owed notification on the next tick; one the Mac showed is not
owed, or the Mac would show it twice. A channel that failed while another
delivered is reported, through the fan-out's ``log`` and its :attr:`last`
outcomes, and not retried. A channel with nobody to send to
(:class:`NobodyToNotify`) is neither a delivery nor a failure.

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



class NobodyToNotify(NotifyError):
    """A channel with nobody to send to: no device has turned push on."""


@dataclass(frozen=True)
class Outcome:
    """What one channel did with one notification, for one target.

    ``ok`` is ``None`` for a channel that had nobody to send to. ``target`` is
    the device a push went to, empty for the Mac.
    """

    channel: str
    target: str
    ok: bool | None
    detail: str


class FanOutNotifier:
    """Every channel, in order, each on its own: one failing does not stop the next.

    ``channels`` are ``(name, notifier)`` pairs; the Mac banner goes first, so
    a slow push service never delays it. A channel that keeps its own
    per-target outcomes (``last``, as the push notifier does) has them carried
    here. :meth:`notify` raises only when no channel delivered: a
    :class:`NotifyError` naming every failure, or :class:`NobodyToNotify`
    when no channel had anyone to send to.
    """

    def __init__(self, channels: list[tuple[str, Notifier]],
                 log: Callable[[str], None] | None = None) -> None:
        self.channels = list(channels)
        self.log = log
        #: The outcomes of the last :meth:`notify`, in channel order.
        self.last: list[Outcome] = []

    def notify(self, notification: Notification) -> None:
        self.last = []
        for name, channel in self.channels:
            try:
                channel.notify(notification)
                own = list(getattr(channel, "last", None) or [])
                self.last.extend(own or [Outcome(name, "", True, "sent")])
            except NobodyToNotify as exc:
                self.last.append(Outcome(name, "", None, str(exc)))
            except Exception as exc:  # noqa: BLE001 - one channel must not stop the next
                own = [o for o in getattr(channel, "last", None) or [] if o.ok is not None]
                self.last.extend(own or [Outcome(name, "", False, str(exc))])
        failed = [o for o in self.last if o.ok is False]
        if self.log is not None:
            for outcome in failed:
                to = f" to {outcome.target}" if outcome.target else ""
                self.log(f"{outcome.channel}{to} could not notify: {outcome.detail}")
        if any(o.ok for o in self.last):
            return
        if failed:
            raise NotifyError("; ".join(
                f"{o.channel}{f' to {o.target}' if o.target else ''}: {o.detail}"
                for o in failed))
        raise NobodyToNotify("no channel had anyone to notify")
