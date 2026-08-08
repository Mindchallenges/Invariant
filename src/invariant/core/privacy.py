"""Detection of personal and operational data in text.

Scope is configured (``privacy.scope``) and defaults to documents, memories,
and commit messages — the places where a leak becomes permanent. Source code is
not scanned by default: it is full of literals that look like tokens, and a
gate with a high false-positive rate is a gate that gets turned off.

**Findings redact by default.** A report that quotes the secret it found is a
second copy of the secret, and reports get pasted into commits and issues. The
raw match stays available in-process for a caller that genuinely needs it —
`invariant privacy scan --reveal`, which is for a human reading a terminal —
but nothing writes it down by default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Pattern


@dataclass(frozen=True)
class Finding:
    pattern: str
    line: int
    column: int
    redacted: str
    raw: str = field(repr=False, default="")
    path: Path | None = None

    def describe(self, *, reveal: bool = False) -> str:
        where = f"{self.path}:{self.line}:{self.column}" if self.path else f"{self.line}:{self.column}"
        shown = self.raw if reveal else self.redacted
        return f"{where}  [{self.pattern}]  {shown}"


def _redact(text: str, keep: int = 2) -> str:
    """Keep a short prefix so a human can recognise what was found."""
    if len(text) <= keep:
        return "█" * len(text)
    return text[:keep] + "█" * min(len(text) - keep, 12)


# Each pattern is deliberately narrow. Breadth here buys false positives, and a
# noisy privacy gate is one that gets disabled wholesale rather than tuned.
PATTERNS: dict[str, Pattern[str]] = {
    # A home path that embeds a username. The generic prefixes alone are fine.
    "home_paths": re.compile(r"(?:/Users|/home)/(?!$)[A-Za-z0-9][A-Za-z0-9._-]*"),
    "emails": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "ip_addresses": re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"
    ),
    "tokens": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}"
        r"|sk-[A-Za-z0-9-]{20,}"
        r"|AKIA[0-9A-Z]{16}"
        r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b"
    ),
    # A bare date is fine; a clock time is not.
    "clock_times": re.compile(
        r"(?<![\d.])(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?:Z|\s?[AaPp][Mm])?(?![\d.])"
    ),
    "links": re.compile(
        r"\b(?:[a-z][a-z0-9+.-]*://\S+|www\.[A-Za-z0-9.-]+\.[A-Za-z]{2,}\S*"
        r"|[A-Za-z0-9._-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}:\S+)"
    ),
    "device_ids": re.compile(
        r"\b(?:[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
        r"|[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}"
        r"|[0-9A-Fa-f]{40})\b"
    ),
}

#: Substrings whose presence on a line makes a clock-time match a false alarm.
#: Durations, versions, and ratios all look like times without being one.
_TIME_FALSE_ALARM = re.compile(
    r"\b(?:took|elapsed|duration|ratio|version|v\d|timeout|ETA|of\s+\d)\b", re.I
)

#: Addresses that are non-identifying by construction. A no-reply address is not
#: a way to reach a person, and the git co-author trailer carrying one is a
#: convention every agent-assisted repository uses — so flagging it means the
#: gate blocks the commit that records who wrote the change, on every commit,
#: until someone disables the email pattern entirely.
#:
#: Deliberately narrow: only address shapes that cannot identify anyone. A real
#: contributor's address in the same trailer is still a finding, because it
#: still is one.
_NON_IDENTIFYING_ADDRESS = re.compile(
    r"^(?:no-?reply|do-?not-?reply)@|@(?:[\w.-]+\.)?no-?reply\.[\w.-]+$", re.I
)


class PrivacyScanner:
    def __init__(
        self,
        patterns: Iterable[str] | None = None,
        extra: Iterable[str] = (),
        allow: Iterable[str] = (),
    ) -> None:
        names = list(patterns) if patterns is not None else list(PATTERNS)
        unknown = [n for n in names if n not in PATTERNS]
        if unknown:
            raise ValueError(
                f"unknown privacy pattern(s): {', '.join(unknown)}; available: "
                + ", ".join(sorted(PATTERNS))
            )
        self.names = names
        self.compiled: dict[str, Pattern[str]] = {n: PATTERNS[n] for n in names}
        for index, source in enumerate(extra):
            self.compiled[f"custom[{index}]"] = re.compile(source)
        self.allow = {a for a in allow if a}

    # -- scanning ------------------------------------------------------

    def scan_text(self, text: str, path: Path | None = None) -> list[Finding]:
        findings: list[Finding] = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            if self._is_exempt_line(line):
                continue
            for name, pattern in self.compiled.items():
                if name == "clock_times" and _TIME_FALSE_ALARM.search(line):
                    continue
                for match in pattern.finditer(line):
                    raw = match.group(0)
                    if any(allowed in raw for allowed in self.allow):
                        continue
                    if name == "emails" and _NON_IDENTIFYING_ADDRESS.search(raw):
                        continue
                    findings.append(
                        Finding(
                            pattern=name,
                            line=lineno,
                            column=match.start() + 1,
                            redacted=_redact(raw),
                            raw=raw,
                            path=path,
                        )
                    )
        return findings

    def scan_file(self, path: Path) -> list[Finding]:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return []  # binary or unreadable: nothing to leak in text form
        return self.scan_text(text, path=Path(path))

    def scan_paths(self, paths: Iterable[Path]) -> list[Finding]:
        findings: list[Finding] = []
        for path in paths:
            path = Path(path)
            if path.is_dir():
                for child in sorted(path.rglob("*")):
                    if child.is_file():
                        findings.extend(self.scan_file(child))
            elif path.is_file():
                findings.extend(self.scan_file(path))
        return findings

    @staticmethod
    def _is_exempt_line(line: str) -> bool:
        """An explicit inline exemption, for the case the gate gets wrong."""
        return "invariant: allow-privacy" in line

    # -- construction from configuration -------------------------------

    @classmethod
    def from_manifest(cls, manifest) -> "PrivacyScanner":
        cfg: Mapping = manifest.get("privacy") or {}
        return cls(
            patterns=cfg.get("patterns"),
            extra=cfg.get("extra") or (),
            allow=cfg.get("allow") or (),
        )
