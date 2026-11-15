"""Questions asked at a terminal, and when a command may ask them.

A command that offers choices (`init`'s docs offers, ADR-016 D4) asks them
through a :class:`Prompter`, never through ``input`` directly, so the
interactive path runs in the suite with a scripted prompter and no terminal.
:func:`interactive` is the one answer to "may this run ask": never under
``--dry-run`` or ``--json``, and only with a terminal on both sides (ruling
H). A run that may not ask does what it did before the offers existed.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from typing import Protocol, TextIO


class Prompter(Protocol):
    """What an offering command asks through."""

    def say(self, text: str) -> None:
        """Show ``text`` before a question."""

    def choose(
        self, question: str, options: Sequence[tuple[str, str]], default: str
    ) -> str:
        """One of ``options``' keys; ``default`` when nothing is chosen."""

    def ask(self, question: str, default: str = "") -> str:
        """A line of text, stripped; ``default`` when it is empty."""


def interactive(args, stdin: TextIO | None = None, stdout: TextIO | None = None) -> bool:
    """May this run ask? Not with ``--dry-run`` or ``--json``, and only when
    both standard input and output are a terminal."""
    if getattr(args, "dry_run", False) or getattr(args, "json", False):
        return False
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    return bool(stdin.isatty() and stdout.isatty())


class TerminalPrompter:
    """Asks at the terminal. End of input answers the default, so a closed
    stdin can never loop."""

    def __init__(
        self,
        read: Callable[[str], str] = input,
        write: Callable[[str], None] = print,
    ) -> None:
        self._read = read
        self._write = write

    def say(self, text: str) -> None:
        self._write(text)

    def _line(self, prompt: str) -> str | None:
        try:
            return self._read(prompt).strip()
        except EOFError:
            return None

    def choose(
        self, question: str, options: Sequence[tuple[str, str]], default: str
    ) -> str:
        keys = [key for key, _ in options]
        self._write(question)
        for number, (key, label) in enumerate(options, start=1):
            marker = "  (default)" if key == default else ""
            described = f": {label}" if label else ""
            self._write(f"  {number}) {key}{described}{marker}")
        while True:
            answer = self._line(f"choose 1-{len(options)} or a name [{default}]: ")
            if not answer:
                return default
            if answer in keys:
                return answer
            if answer.isdigit() and 1 <= int(answer) <= len(keys):
                return keys[int(answer) - 1]
            self._write(f"{answer!r} is not one of {', '.join(keys)}")

    def ask(self, question: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        return self._line(f"{question}{suffix}: ") or default
