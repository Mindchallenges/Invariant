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

**The escape hatch is block-scoped, not file-scoped.** A marker exempts its
own line; written on a line nothing but comment punctuation precedes, it also
exempts the rest of its own block and the whole of the next one. That is what
makes it usable from reStructuredText and Markdown, which have no inline
comment — and it is deliberately not "the rest of the file" or "the rest of
the section", because a reach a reviewer cannot see the end of is a place to
hide a leak. See `PrivacyScanner._exempt_lines`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Pattern, Sequence


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


#: The exemption marker — the ONE authoritative spelling, read by the scanner
#: and by the rule that prints the advice, so the two cannot drift apart the
#: way the two construction paths did (AUD-15). Its SCOPE is stated in
#: ``PrivacyScanner._exempt_lines`` and nowhere else.
EXEMPTION_MARKER = "invariant: allow-privacy"


#: A dotted-quad address, octet by octet. Named because two patterns need the
#: same notion of "an IP literal" — ``ip_addresses`` is the address itself, and
#: ``links`` asks whether a URI's authority is one — and two spellings of one
#: fact are two things that drift.
_IPV4 = (
    r"(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
)

# Each pattern is deliberately narrow. Breadth here buys false positives, and a
# noisy privacy gate is one that gets disabled wholesale rather than tuned.
PATTERNS: dict[str, Pattern[str]] = {
    # A home path that embeds a username. The generic prefixes alone are fine.
    "home_paths": re.compile(r"(?:/Users|/home)/(?!$)[A-Za-z0-9][A-Za-z0-9._-]*"),
    "emails": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    # An address, and not merely four numbers with dots between them. The
    # guards are on THIS entry rather than in `_IPV4`, which `links` shares:
    # there a scheme has already proved the authority is an address, and a
    # narrowing that reached it would cost a real class of finding.
    #
    # `(?<![\w.])` keeps the word boundary that stops `v1.2.3.4` reading as
    # an address, and adds the dot that `\b` could not see: a four-segment
    # run preceded by one is the TAIL of a longer number, not an address.
    # `(?!\.?\d)` is the same test on the other end, and is spelled around
    # the digit rather than around the dot on purpose — a trailing dot with
    # nothing after it is how prose ends a sentence, and rejecting that
    # would have cost every address written at the end of one (BUG_053).
    "ip_addresses": re.compile(rf"(?<![\w.]){_IPV4}(?!\.?\d)"),
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
    # A link is a URI whose AUTHORITY names a network host. The scheme is not
    # the axis: this used to match anything before a `://`, which made every
    # documented private-scheme identifier a finding — 20 false out of 26 on
    # two architecture documents in one consuming project, several of them with
    # no authority at all (BUG_044). An empty authority, or a bare label with
    # no dot, addresses nothing reachable and can leak nothing.
    #
    # Narrowing the SCHEME instead — an allowlist of the web's own — would have
    # been the smaller edit and a loss of protection: a custom scheme aimed at
    # a real host is exactly the leak worth catching, and the authority test
    # keeps it caught.
    #
    # Two consequences follow from the test being structural, and both are
    # meant. A single-label authority is not a host here, so a loopback or
    # container-network address is no longer a finding — it names the reader's
    # own machine and identifies nobody. A reverse-DNS identifier sitting in
    # the authority position IS one, because nothing in the text distinguishes
    # it from a hostname; that is the narrow residue, and it is on the
    # noisy-not-silent side of the trade.
    "links": re.compile(
        rf"""\b(?:
            # scheme://[userinfo@]authority...
            [a-z][a-z0-9+.-]*://
            (?:[^\s/?\#@]*@)?
            (?:
                # a dotted name ending in a TLD-like suffix,
                [A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{{2,}}
                # or an IP literal, v4 or bracketed v6.
              | {_IPV4}
              | \[[0-9A-Fa-f:]+\]
            )
            # The host must END here: a trailing label the suffix rule could
            # not consume (`x.dev1`) is not a host with a stray character after
            # it. A dot is deliberately absent from this set, so a URI ending a
            # sentence keeps matching.
            (?![A-Za-z0-9_-])
            \S*
            # Unaffected: the schemeless `www.` form, and the scp-style
            # `user@host:path` a git remote is written in.
          | www\.[A-Za-z0-9.-]+\.[A-Za-z]{{2,}}\S*
          | [A-Za-z0-9._-]+@[A-Za-z0-9.-]+\.[A-Za-z]{{2,}}:\S+
        )""",
        re.VERBOSE,
    ),
    "device_ids": re.compile(
        r"\b(?:[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
        r"|[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}"
        r"|[0-9A-Fa-f]{40})\b"
    ),
}

#: The patterns a scanner runs when nobody chose — the ONE authoritative
#: default set. ``PrivacyScanner()`` and the manifest default both read this
#: name, because they used to disagree: a bare construction enabled all seven
#: patterns while the manifest path enabled six, so which gate a text faced
#: depended on which code path built the scanner (AUD-15).
#:
#: ``device_ids`` is implemented and deliberately NOT here. Its 40-hex arm
#: matches every full git commit hash and its UUID arm matches tooling
#: identifiers that documents cite routinely, so as a default it flags the
#: ordinary contents of a changelog — and a noisy privacy gate is one that
#: gets disabled wholesale rather than tuned. A project whose documents could
#: leak simulator or hardware identifiers opts in via ``privacy.patterns``.
DEFAULT_PATTERNS: tuple[str, ...] = (
    "home_paths",
    "emails",
    "ip_addresses",
    "tokens",
    "clock_times",
    "links",
)

#: Substrings whose presence on a line makes a clock-time match a false alarm.
#: Durations, versions, and ratios all look like times without being one.
_TIME_FALSE_ALARM = re.compile(
    r"\b(?:took|elapsed|duration|ratio|version|v\d|timeout|ETA|of\s+\d)\b", re.I
)

#: A number introduced by a reference marker is a row or a section, not an
#: address. A release plan numbering its rows four levels deep writes
#: `#10.14.6.1`, which is a valid dotted quad, and the gate fired on the plan's
#: own numbering — 55 matches in one consuming project's corpus, none of them
#: an address (BUG_053).
#:
#: ADJACENCY is the whole of the rule, and it is what keeps this narrow. A
#: marker with a space after it opens a heading or a shell comment, and either
#: may be quoting a real address; only a marker written hard against the digits
#: is numbering something. Anchored to the text immediately left of the MATCH
#: rather than searched over the whole line, which is strictly tighter than the
#: `_TIME_FALSE_ALARM` precedent it is modelled on.
#:
#: Deliberately not covered: a row number written BARE, in a table cell or a
#: field name. Nothing local distinguishes it from an address, and this pattern
#: errs noisy rather than silent where that is true — the same call `links`
#: makes about a reverse-DNS identifier.
_ROW_NUMBER_REFERENCE = re.compile(r"[#\u00a7\u00b6]$")

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
        names = list(patterns) if patterns is not None else list(DEFAULT_PATTERNS)
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
        lines = text.splitlines()
        exempt = self._exempt_lines(lines)
        for lineno, line in enumerate(lines, start=1):
            if lineno in exempt:
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
                    if name == "ip_addresses" and _ROW_NUMBER_REFERENCE.search(
                        line[: match.start()]
                    ):
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
    def _exempt_lines(lines: Sequence[str]) -> set[int]:
        """The 1-based line numbers an exemption marker covers.

        **The rule, in one sentence:** a marker exempts its own line; and when
        nothing but comment punctuation precedes it on that line, it also
        exempts the rest of the block it opens and the whole of the next
        block, where a block is a run of consecutive non-blank lines.

        The same-line form is the first half and is unchanged: a marker with
        text to its left reaches that line and no further. The second half
        exists because a same-line marker is unwritable in the markup the gate
        mostly scans. reStructuredText and Markdown have no inline comment — a
        comment there owns its line — so the only way to exempt a finding in
        prose was to append the marker to the prose, where it renders into the
        published output. Six of those went out in one consuming project's
        documentation before this was fixed (BUG_045).

        A block rather than a line, because in RST the marker cannot be put
        next to what it covers. Inserted mid-paragraph a comment SPLITS the
        paragraph; inserted between list items it ENDS the list and starts a
        second one. Both are visible in the rendered output, which is the thing
        the marker is supposed to leave alone, so the comment can only go
        before the whole block — and every real case sits mid-block.

        Two blocks and not the rest of the file or of the section, because a
        marker whose reach a reviewer cannot see the end of is a place to hide
        a leak rather than an escape hatch for a false positive. Its own block
        is included so a wrapped comment covers what follows it rather than
        only itself; the reach runs FORWARD from the marker only, so a line
        above it in the same block is still scanned.

        Blank lines between the marker and the block it covers are the normal
        shape — RST needs one to close the comment — so they are skipped, and
        what follows them is still exactly one block.
        """
        exempt: set[int] = set()
        total = len(lines)
        for index, line in enumerate(lines):
            at = line.find(EXEMPTION_MARKER)
            if at < 0:
                continue
            exempt.add(index + 1)  # the marker's own line, in either form
            if any(char.isalnum() for char in line[:at]):
                continue  # inline: something else is on this line
            cursor = index
            while cursor < total and lines[cursor].strip():
                exempt.add(cursor + 1)  # the rest of the marker's own block
                cursor += 1
            while cursor < total and not lines[cursor].strip():
                cursor += 1  # the blank run the markup requires
            while cursor < total and lines[cursor].strip():
                exempt.add(cursor + 1)  # the next block, and nothing past it
                cursor += 1
        return exempt

    # -- construction from configuration -------------------------------

    @classmethod
    def from_manifest(cls, manifest) -> "PrivacyScanner":
        cfg: Mapping = manifest.get("privacy") or {}
        return cls(
            patterns=cfg.get("patterns"),
            extra=cfg.get("extra") or (),
            allow=cfg.get("allow") or (),
        )
