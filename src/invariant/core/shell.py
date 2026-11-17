"""Reading a shell command the way bash would, without running it.

Two callers read the same command line and must not disagree about it: the
commit route of ``privacy.clean`` in ``core/rules`` (which ``git commit`` a
command runs, in which directory, with which message), and the host adapter
placing a subagent's shell call for an actor row (the directories the
command's commands run in, BUG_125). One lexer serves both, so a quoting or
operator case fixed for one is fixed for the other (BUG_132, BUG_136).

Everything here is a pure function of the command text and a starting
directory: nothing is executed, no file is read, and an expansion stays
literal text. Where bash's own reading could not be told from the text, the
readers answer "cannot tell" rather than a guess.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

#: Characters that make up the shell's control and redirection operators when
#: they are not quoted.
_OPERATOR_CHARS = frozenset("();&|<>")

#: The single-character escapes of bash's ``$'...'`` quoting.
_ANSI_C_ESCAPES = {
    "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n",
    "r": "\r", "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?",
}
_ANSI_C_ESCAPE = re.compile(
    r"\\(x[0-9a-fA-F]{1,2}|u[0-9a-fA-F]{1,4}|U[0-9a-fA-F]{1,8}|[0-7]{1,3}|c.|.)",
    re.DOTALL,
)


def _ansi_c(body: str) -> str:
    """The text bash makes of a ``$'...'`` body: ``\\x40`` is ``@``."""

    def one(match: re.Match[str]) -> str:
        code = match.group(1)
        if code[0] in "xuU":
            try:
                return chr(int(code[1:], 16))
            except (ValueError, OverflowError):
                return match.group(0)
        if code[0] in "01234567":
            return chr(int(code, 8) & 0xFF)
        if code[0] == "c" and len(code) == 2:
            return chr(ord(code[1].upper()) ^ 0x40) if code[1] != "?" else "\x7f"
        return _ANSI_C_ESCAPES.get(code, match.group(0))

    return _ANSI_C_ESCAPE.sub(one, body)


def _substitution_end(command: str, start: int) -> int:
    """Where the ``$(...)`` or backquoted substitution at ``start`` ends.

    Parentheses are counted and quotes are not read: a heredoc body inside
    the substitution routinely holds a lone apostrophe. One that never closes
    runs to the end of the command, all of it one literal piece of the word.
    """
    if command[start] == "`":
        at = start + 1
        while at < len(command) and command[at] != "`":
            at += 2 if command[at] == "\\" else 1
        return min(at + 1, len(command))
    depth, at = 0, start + 1
    while at < len(command):
        char = command[at]
        if char == "\\":
            at += 2
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return at + 1
        at += 1
    return len(command)


def lex(command: str) -> list[tuple[str, bool]]:
    """The command as bash splits it: ``(word, is_operator)`` pairs.

    An operator is a run of unquoted ``( ) ; & | < >``; the same characters
    inside quotes are a word's, which is how ``-m ';'`` stays a message. The
    text of each word is what bash hands the program, as far as it can be told
    without running anything: quotes removed, ``\\<newline>`` joined away
    outside single quotes, and ``$'...'`` decoded, so ``$'a\\x40b'`` reads
    as the address it is. Expansions (``$VAR``, ``$(...)``) stay literal text
    (BUG_136). A newline is read as a space, so the words of a later line run
    on into the commit's: more is read, never less. Nothing when the quotes do
    not balance, which the shell refuses too.
    """
    out: list[tuple[str, bool]] = []
    word: list[str] = []
    in_word = False
    index, length = 0, len(command)

    def flush() -> None:
        nonlocal in_word
        if in_word:
            out.append(("".join(word), False))
        word.clear()
        in_word = False

    while index < length:
        char = command[index]
        if char == "\\":
            if index + 1 < length:
                if command[index + 1] != "\n":
                    word.append(command[index + 1])
                    in_word = True
                index += 2
            else:
                word.append(char)
                in_word = True
                index += 1
            continue
        if char == "'":
            end = command.find("'", index + 1)
            if end < 0:
                return []
            word.append(command[index + 1 : end])
            in_word = True
            index = end + 1
            continue
        if char == "$" and command.startswith("$'", index):
            at = index + 2
            while at < length and command[at] != "'":
                at += 2 if command[at] == "\\" else 1
            if at >= length:
                return []
            word.append(_ansi_c(command[index + 2 : at]))
            in_word = True
            index = at + 1
            continue
        if char == "$" and command.startswith('$"', index):
            index += 1  # a locale string: the text is the quoted text
            continue
        if char == "`" or command.startswith("$(", index):
            # A substitution is one piece of its word, kept as literal text
            # (BUG_136): its parentheses do not end the command around it.
            at = _substitution_end(command, index)
            word.append(command[index:at])
            in_word = True
            index = at
            continue
        if char == '"':
            at = index + 1
            while at < length and command[at] != '"':
                if command[at] == "\\" and at + 1 < length:
                    following = command[at + 1]
                    if following != "\n":
                        word.append(following if following in '$`"\\' else "\\" + following)
                    at += 2
                    continue
                word.append(command[at])
                at += 1
            if at >= length:
                return []
            in_word = True
            index = at + 1
            continue
        if char in " \t\n":
            flush()
            index += 1
            continue
        if char in _OPERATOR_CHARS:
            flush()
            at = index
            while at < length and command[at] in _OPERATOR_CHARS:
                at += 1
            out.append((command[index:at], True))
            index = at
            continue
        word.append(char)
        in_word = True
        index += 1
    flush()
    return out


#: git options that take their value as the NEXT token and are not `-C`.
#: They have to be listed only so the scan steps over their value: a bare
#: word is otherwise read as the subcommand, and `git -c commit.gpgsign=false
#: -C <other> commit` would stop at the config pair and never see the `-C`.
GIT_VALUE_OPTIONS = frozenset({"-c", "--namespace", "--exec-path", "--super-prefix"})

#: Redirections of the same class as `-C` that no reader here will follow.
#: Resolving them means reproducing git's own gitdir/worktree pairing, and a
#: half-right resolution reports a tree nobody asked about. Naming the option
#: and failing closed is the honest floor (AUD-09).
GIT_LAYOUT_OPTIONS = frozenset({"--git-dir", "--work-tree"})


@dataclass(frozen=True)
class GitOptions:
    """What git's own options say, for one invocation.

    ``directory`` is where the invocation runs after every ``-C``; ``end`` the
    index of the first word that is not git's (its subcommand, or the length
    of ``words`` when none follows); ``layout`` the first
    :data:`GIT_LAYOUT_OPTIONS` member named, which no reader follows.
    """

    directory: Path
    end: int
    layout: str | None = None


def git_options(words: list[str], start: int, cwd: Path) -> GitOptions:
    """Read git's own options from ``words[start]`` on, ``cwd`` the start.

    ``-C`` is REPEATABLE and cumulative within one invocation, each path
    resolved against the one before: ``git -C a -C b`` runs in ``a/b``. A
    reader that took the first and stopped would land one directory up, which
    is usually a real directory with a real index, so the mistake produces a
    confident answer about the wrong tree rather than an error anyone notices.

    The scan stops at the subcommand, because only git's OWN options are read:
    ``commit -C <commit>`` reuses another commit's message and has nothing to
    do with directories. ``-C`` with nothing after it, or the literal
    ``-C ""``, which git reads as no change of directory, also stops the scan
    there and leaves the directory where it is.
    """
    directory = Path(cwd)
    layout: str | None = None
    index = start
    while index < len(words):
        token = words[index]
        head = token.split("=", 1)[0]
        if head in GIT_LAYOUT_OPTIONS:
            layout = layout or head
            index += 2 if "=" not in token else 1
            continue
        if token == "-C":
            following = words[index + 1] if index + 1 < len(words) else ""
            if not following:
                break
            candidate = Path(following)
            directory = candidate if candidate.is_absolute() else directory / candidate
            index += 2
            continue
        if token in GIT_VALUE_OPTIONS:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    return GitOptions(directory, index, layout)


#: The operators that run the next command after this one, in the same shell.
#: A ``cd`` between two of them moves every command that follows.
_SEQUENCE = frozenset({"&&", ";"})

#: The builtins that change the shell's directory.
_DIRECTORY_CHANGES = frozenset({"cd", "pushd", "popd"})

#: Words that may stand before a command word without being one: the shell's
#: reserved words and the builtins that run the word after them. A directory
#: change behind one of them (``if cd x; then``, ``command cd x``) is a change
#: this reader does not follow, so it answers "cannot tell". The closing
#: words (``fi``, ``done``) run nothing.
_PREFIX_WORDS = frozenset(
    {"if", "then", "elif", "else", "fi", "while", "until", "do", "done", "!",
     "{", "}", "time", "builtin", "command", "exec"}
)

_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


def working_directories(command: str, cwd: Path) -> list[Path] | None:
    """The directory each command of ``command`` runs in, in order.

    ``cwd`` is where the shell starts. Two things move a command, and only
    these two (BUG_125):

    - a ``cd <dir>`` or ``pushd <dir>`` at the top level, run in sequence
      (``&&`` or ``;`` on both sides of it, or the command's start or end),
      moves every command after it;
    - ``git -C <dir>`` moves that one git invocation, read by
      :func:`git_options`.

    A path a command merely NAMES moves nothing: ``echo <dir>`` and
    ``grep x <dir>`` run where the shell stands. The directory change itself
    is not a command run anywhere, so ``cd <dir>`` alone runs nothing.

    ``None`` means bash's own reading could not be told from the text: a
    directory change in a pipe, in the background, behind ``||``, in a
    subshell or after a reserved word; one with options, more than one
    argument, or none; a target that bash expands (``~``, ``$VAR``, a
    substitution) or ``-``; ``popd``; a git invocation naming
    :data:`GIT_LAYOUT_OPTIONS`; and quotes that do not balance. A change of
    directory inside a nested shell string (``bash -c '...'``, ``eval``) is
    one word of the outer command and is not read: those commands are taken
    to run where the outer one stands.

    A newline ends a command here, as it does in bash: :func:`lex` reads it as
    a space, which is right for the words of a commit but would carry the
    directory of one line onto the next, so ``cd <wt> && a`` then a line
    ``cd <elsewhere> && b`` would place ``b`` in the worktree. A newline
    inside quotes stays in its word.

    A ``cd`` moves the shell LOGICALLY, as bash's default ``cd -L`` does, so
    ``..`` is taken off the path as written. ``git -C`` is a ``chdir``, which
    is PHYSICAL, so its directory is returned unnormalised and ``..`` after a
    symlink is left for the caller's resolution to read as the kernel does.
    """
    lexed = lex(command.replace("\n", ";"))
    if not lexed and command.strip():
        return None
    here = Path(cwd)
    places: list[Path] = []
    depth = 0
    segment: list[str] = []
    before: str | None = None

    def close(after: str | None) -> bool:
        """Read one simple command. ``False`` when it cannot be told."""
        nonlocal here
        words = list(segment)
        segment.clear()
        at = 0
        while at < len(words) and _ASSIGNMENT.match(words[at]):
            at += 1
        prefixed = False
        while at < len(words) and words[at] in _PREFIX_WORDS:
            prefixed = True
            at += 1
        if at >= len(words):
            return True
        name = words[at]
        if name in _DIRECTORY_CHANGES:
            target = words[at + 1] if at + 1 < len(words) else ""
            plain = (
                name != "popd"
                and not prefixed
                and at == 0
                and depth == 0
                and len(words) == 2
                and _in_sequence(before)
                and _in_sequence(after)
            )
            if not plain or not _literal_directory(target):
                return False
            moved = Path(target)
            here = Path(posixpath.normpath(str(
                moved if moved.is_absolute() else here / moved
            )))
            return True
        if PurePosixPath(name).name == "git":
            options = git_options(words, at + 1, here)
            if options.layout:
                return False
            places.append(options.directory)
            return True
        places.append(here)
        return True

    for word, operator in lexed:
        if not operator:
            segment.append(word)
            continue
        if not close(word):
            return None
        depth = max(0, depth + word.count("(") - word.count(")"))
        before = word
    if not close(None):
        return None
    return places


def _in_sequence(operator: str | None) -> bool:
    """Whether a directory change next to ``operator`` holds for what follows.

    The start or end of the command, ``&&`` or ``;``. A newline is read as
    ``;``, so the runs it makes (``&&;`` for ``&&`` at a line's end, ``;;``
    for a blank line) count as the sequence they are.
    """
    return operator is None or operator.rstrip(";") in ("", *_SEQUENCE)


def _literal_directory(target: str) -> bool:
    """Whether bash would hand ``cd`` this target as written."""
    return bool(target) and target != "-" and not target.startswith("~") and not any(
        char in target for char in "$`*?["
    )
