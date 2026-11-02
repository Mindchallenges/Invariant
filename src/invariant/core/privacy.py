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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from re import Pattern


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
                # a dotted name ending in a TLD-like suffix: letters, or the
                # punycode form of an internationalised one, which carries
                # digits and hyphens a letters-only suffix cannot consume
                # (IDEA_001),
                [A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:[A-Za-z]{{2,}}|xn--[A-Za-z0-9-]+)
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
    # A countdown or an elapsed time: `45:00`, `59:59`, `1:23:45`. Not a time
    # of day — `clock_times` stops its hour at 23, so `24:10` and `45:00` are
    # out of its reach by design — and off by default for the reason
    # `device_ids` is: the shape is common in prose that leaks nothing (a
    # chapter-and-verse, an aspect ratio). A project whose tree carries mock
    # durations it means to wipe opts in. No duration word excuses one: that
    # word is what introduces exactly what this pattern is for.
    "durations": re.compile(r"(?<![\d.:])\d{1,3}:[0-5]\d(?::[0-5]\d)?(?![\d.:])"),
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

#: A word that, written IMMEDIATELY before a clock-time match, makes it a false
#: alarm: durations, versions and ratios all look like times without being one.
#:
#: Tested against the text left of the MATCH, as `_ROW_NUMBER_REFERENCE` is.
#: It used to be searched over the whole line, so a duration word anywhere on
#: it hid every real time beside it — `took 3m; the crash fired at 14:05` was
#: clean. A separator (`:` or `=`) and a `~` may sit between the word and the
#: time; anything else, and the word is introducing something else.
_TIME_FALSE_ALARM = re.compile(
    r"(?:\b(?:took|elapsed|durations?|ratios?|version|timeouts?|ETA)\s*[:=]?\s*~?"
    r"|\bof\s+|\bv)$",
    re.IGNORECASE,
)

#: The left context of a URI that a parser compares as a STRING and nothing
#: dereferences: an XML namespace name (`xmlns="…"`, `xmlns:xlink="…"`), the
#: URIs of an `xsi:schemaLocation` value, and a DOCTYPE's system identifier
#: (`<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://…">`). Every
#: plist, SVG and XHTML file carries one, and none names a resource the
#: document reaches for — a vendor's spec host, the same for every reader.
#:
#: Adjacency again: the attribute or the DOCTYPE head must sit right against
#: the match, inside the quoted value. The same host in an ordinary `href` is
#: still a link, and so is a URI written after a DOCTYPE on its line.
_XML_IDENTIFIER = re.compile(
    r"""(?:
        \bxmlns(?::[\w.-]+)?\s*=\s*["']
      | \bschemaLocation\s*=\s*["'][^"']*
      | <!DOCTYPE\s+[\w:.-]+\s+(?:PUBLIC\s+(?:"[^"]*"|'[^']*')|SYSTEM)\s+["']
    )$""",
    re.VERBOSE | re.IGNORECASE,
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
    r"^(?:no-?reply|do-?not-?reply)@|@(?:[\w.-]+\.)?no-?reply\.[\w.-]+$", re.IGNORECASE
)


#: The keys a path-scoped allow entry may carry.
_ALLOW_KEYS = ("paths", "patterns", "values", "reason")


@dataclass(frozen=True)
class ScopedAllow:
    """One path-scoped ``privacy.allow`` entry: a finding is exempt when its
    path falls under one of ``paths`` and every narrowing given also holds.

    ``paths`` are canonical project-relative globs (``project_relative``),
    matched as every other declared glob is (``glob_matches``): the path
    itself or anything beneath it. ``patterns`` and ``values`` are ``None``
    when the entry does not narrow by them. ``reason`` is required so the
    manifest says why — a scope that silences every finding under a prefix
    is the widest thing an operator can write here.
    """

    paths: tuple[str, ...]
    reason: str
    patterns: frozenset[str] | None = None
    values: tuple[str, ...] | None = None

    def covers(self, name: str, raw: str, rel: str) -> bool:
        from .manifest import glob_matches

        if self.patterns is not None and name not in self.patterns:
            return False
        if self.values is not None and not any(v in raw for v in self.values):
            return False
        return any(glob_matches(rel, glob) for glob in self.paths)


def parse_allow(entries: object) -> tuple[frozenset[str], tuple[ScopedAllow, ...]]:
    """``privacy.allow`` read into its plain values and its scoped entries.

    Every problem is a ``ValueError`` naming ``privacy.allow``, raised at
    construction and therefore at manifest load. An entry that loaded and
    matched nothing — a mistyped pattern name, a path the comparison could
    never produce — would leave the finding it was written for blocking with
    no word as to why, and an entry that matched more than it says (a bare
    string iterated as characters) would silence findings nobody exempted.
    """
    # ValueError, not TypeError, for a wrong shape too: it is the one error a
    # caller catches for "this configuration cannot build a scanner".
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Iterable):
        raise ValueError(  # noqa: TRY004
            f"privacy.allow must be a list of values and path-scoped entries, "
            f"got {entries!r}"
        )
    values: set[str] = set()
    scoped: list[ScopedAllow] = []
    for index, entry in enumerate(entries):
        if isinstance(entry, str):
            if entry:
                values.add(entry)
        elif isinstance(entry, Mapping):
            scoped.append(_scoped_allow(index, entry))
        else:
            raise ValueError(  # noqa: TRY004
                f"privacy.allow[{index}] must be a string or a mapping with "
                f"paths, got {entry!r}"
            )
    return frozenset(values), tuple(scoped)


def _scoped_allow(index: int, entry: Mapping) -> ScopedAllow:
    from .manifest import project_relative

    where = f"privacy.allow[{index}]"
    unknown = sorted(str(k) for k in entry if k not in _ALLOW_KEYS)
    if unknown:
        raise ValueError(
            f"{where} has unknown key(s) {', '.join(unknown)}; an entry takes "
            + ", ".join(_ALLOW_KEYS)
        )
    paths = _non_empty_strings(where, "paths", entry.get("paths"))
    if paths is None:
        raise ValueError(
            f"{where} needs paths: a non-empty list of project-relative "
            f"prefixes or globs. A value allowed everywhere is a plain string."
        )
    canonical: list[str] = []
    for path in paths:
        if Path(path).is_absolute():
            raise ValueError(
                f"{where} paths entry {path!r} must be spelled project-relative"
            )
        rel = project_relative(path)
        if rel is None:
            raise ValueError(f"{where} paths entry {path!r} names a place outside the project")
        if rel == ".":
            raise ValueError(
                f"{where} paths entry {path!r} names the whole project; a value "
                f"allowed everywhere is a plain string"
            )
        canonical.append(rel)
    reason = entry.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError(
            f"{where} needs a reason: the register entry or the cause that "
            f"makes every finding it scopes legitimate"
        )
    patterns = _non_empty_strings(where, "patterns", entry.get("patterns"))
    if patterns is not None:
        unknown = [n for n in patterns if n not in PATTERNS]
        if unknown:
            raise ValueError(
                f"{where} names unknown pattern(s) {', '.join(unknown)}; available: "
                + ", ".join(sorted(PATTERNS))
            )
    values = _non_empty_strings(where, "values", entry.get("values"))
    return ScopedAllow(
        paths=tuple(canonical),
        reason=reason.strip(),
        patterns=frozenset(patterns) if patterns is not None else None,
        values=tuple(values) if values is not None else None,
    )


def _non_empty_strings(where: str, key: str, value: object) -> list[str] | None:
    """A list of non-empty strings, or ``None`` for a key left out."""
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(v, str) and v.strip() for v in value)
    ):
        raise ValueError(f"{where} {key} must be a non-empty list of non-empty strings")
    return list(value)


class PrivacyScanner:
    def __init__(
        self,
        patterns: Iterable[str] | None = None,
        extra: Iterable[str] = (),
        allow: Iterable[str | Mapping] = (),
        root: Path | None = None,
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
        self.allow, self.scoped_allow = parse_allow(allow)
        #: The project root an absolute path is spelled against before a
        #: scoped entry compares it. ``None``: an absolute path is in no
        #: project, so no scoped entry reaches it.
        self.root = Path(root) if root is not None else None

    # -- scanning ------------------------------------------------------

    def scan_text(self, text: str, path: Path | None = None) -> list[Finding]:
        """Every finding in ``text``.

        ``path`` is where the text lives, if it lives anywhere: relative to
        the project root, or absolute. It is what a path-scoped allow entry
        is compared with, so text with no path — a commit message, a note —
        is reached only by the plain values.
        """
        findings: list[Finding] = []
        rel = self._relative(path)
        lines = text.splitlines()
        exempt = self._exempt_lines(lines)
        for lineno, line in enumerate(lines, start=1):
            if lineno in exempt:
                continue
            clock_spans: set[tuple[int, int]] = set()
            on_line: list[tuple[str, re.Match[str]]] = []
            for name, pattern in self.compiled.items():
                for match in pattern.finditer(line):
                    if self._excused(name, match, line, rel):
                        continue
                    if name == "clock_times":
                        clock_spans.add(match.span())
                    on_line.append((name, match))
            for name, match in on_line:
                # One span, one finding: a time of day `durations` also
                # matches is reported as the clock time it is.
                if name == "durations" and match.span() in clock_spans:
                    continue
                raw = match.group(0)
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

    def _excused(self, name: str, match: re.Match[str], line: str, rel: str | None) -> bool:
        """Whether a match is not a finding: allowed, or structurally not one."""
        raw = match.group(0)
        left = line[: match.start()]
        if any(allowed in raw for allowed in self.allow):
            return True
        if rel is not None and any(e.covers(name, raw, rel) for e in self.scoped_allow):
            return True
        if name == "emails":
            return bool(_NON_IDENTIFYING_ADDRESS.search(raw))
        if name == "ip_addresses":
            return bool(_ROW_NUMBER_REFERENCE.search(left))
        if name == "clock_times":
            return bool(_TIME_FALSE_ALARM.search(left))
        if name == "links":
            return bool(_XML_IDENTIFIER.search(left))
        return False

    def _relative(self, path: Path | None) -> str | None:
        """``path`` spelled as a scoped entry's paths are, or ``None`` when it
        names no place in the project (or the scanner has nothing to compare)."""
        if path is None or not self.scoped_allow:
            return None
        from .manifest import project_relative

        bases = [self.root] if self.root is not None else []
        rel = project_relative(Path(path).as_posix(), bases)
        return None if rel in (None, ".") else rel

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
    def from_manifest(cls, manifest) -> PrivacyScanner:
        cfg: Mapping = manifest.get("privacy") or {}
        root = getattr(manifest, "root", None)
        return cls(
            patterns=cfg.get("patterns"),
            extra=cfg.get("extra") or (),
            allow=cfg.get("allow") or (),
            root=Path(root) if root is not None else None,
        )
