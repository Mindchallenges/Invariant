"""Error types, each carrying the process exit code it maps to.

The exit codes are part of the CLI contract (``docs/architecture/cli.md``): a
shim must be able to tell "a gate said no" apart from "the tool broke", so
those never share a code.
"""

from __future__ import annotations


class InvariantError(Exception):
    """Base for every error this library raises deliberately."""

    exit_code = 1


class GateFailure(InvariantError):
    """A gate evaluated unclean. Not a crash — a verdict."""

    exit_code = 1

    def __init__(self, message: str, verdicts: list | None = None) -> None:
        super().__init__(message)
        self.verdicts = verdicts or []


class TransitionError(InvariantError):
    """A transition the machine does not permit structurally.

    Distinct from :class:`GateFailure`: a gate failure means "not yet", this
    means "never from here". Policy can downgrade a gate; it cannot make an
    edge exist that the machine does not declare.
    """

    exit_code = 1


class UsageError(InvariantError):
    """The caller asked for something malformed."""

    exit_code = 2


class ManifestError(UsageError):
    """The manifest is missing, unparseable, or invalid."""

    exit_code = 2


class StateError(InvariantError):
    """The state store is unreadable, unwritable, or wedged.

    Always reported rather than worked around. A store problem that gets
    silently papered over produces exactly the invisible non-compliance this
    library exists to eliminate.
    """

    exit_code = 3


class LockTimeout(StateError):
    """The registry lock could not be acquired in time.

    Deliberately an error rather than an unbounded wait: a wedged lock must be
    visible, because the recovery path is a human running ``state repair``.
    """

    exit_code = 3
