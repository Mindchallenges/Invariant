"""core/shell: the one shell reader the commit route and the host share.

The lexer's cases moved here with it from tests/test_rules.py (BUG_132,
BUG_136); the readers built on it are BUG_125's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from invariant.core import shell

HERE = Path("/repo")


@pytest.mark.parametrize(
    ("command", "words"),
    [
        ("a -m 'x; y' ; b", [("a", False), ("-m", False), ("x; y", False), (";", True),
                             ("b", False)]),
        ("a 2>&1|b", [("a", False), ("2", False), (">&", True), ("1", False),
                      ("|", True), ("b", False)]),
        ("a $'\\x41\\n\\'' \"\\$x\\q\"", [("a", False), ("A\n'", False), ("$x\\q", False)]),
        ("a 'p\\\nq' \"p\\\nq\" p\\\nq", [("a", False), ("p\\\nq", False), ("pq", False),
                                        ("pq", False)]),
        ("a $(b (c) 'd) e", [("a", False), ("$(b (c) 'd)", False), ("e", False)]),
        ("a $(b", [("a", False), ("$(b", False)]),
        ("a $\"x y\"", [("a", False), ("x y", False)]),
        ("a \\x b\\", [("a", False), ("x", False), ("b\\", False)]),
        ("a $(b \\) c) d", [("a", False), ("$(b \\) c)", False), ("d", False)]),
        ("a \"open", []),
        ("a $'open", []),
    ],
)
def test_the_lexer_splits_as_bash_does(command, words):
    assert shell.lex(command) == words

@pytest.mark.parametrize(
    ("body", "text"),
    [("\\x40", "@"), ("\\u00e9", "é"), ("\\101", "A"), ("\\cA", "\x01"),
     ("\\c?", "\x7f"), ("\\e", "\x1b"), ("\\z", "\\z"), ("\\U110000", "\\U110000")],
)
def test_ansi_c_quoting_decodes_as_bash_does(body, text):
    assert shell._ansi_c(body) == text

@pytest.mark.parametrize(
    ("words", "directory", "end", "layout"),
    [
        (["git", "status"], "/repo", 1, None),
        (["git", "-C", "a", "-C", "b", "commit"], "/repo/a/b", 5, None),
        (["git", "-C", "/abs", "log"], "/abs", 3, None),
        (["git", "-c", "x=y", "-C", "a", "diff"], "/repo/a", 5, None),
        (["git", "--no-pager", "log"], "/repo", 2, None),
        (["git", "--git-dir", "g", "-C", "a", "x"], "/repo/a", 5, "--git-dir"),
        (["git", "--work-tree=w", "x"], "/repo", 2, "--work-tree"),
        (["git", "-C", "", "commit"], "/repo", 1, None),
        (["git", "-C"], "/repo", 1, None),
        (["git"], "/repo", 1, None),
    ],
)
def test_git_options_read_gits_own_options(words, directory, end, layout):
    options = shell.git_options(words, 1, HERE)

    assert (options.directory, options.end, options.layout) == (
        Path(directory), end, layout,
    )


@pytest.mark.parametrize(
    ("command", "places"),
    [
        ("ls", ["/repo"]),
        ("", []),
        ("cd /wt && invariant check", ["/wt"]),
        ("cd /wt; git diff | head", ["/wt", "/wt"]),
        ("cd sub && ls", ["/repo/sub"]),
        ("cd .. && ls", ["/"]),
        ("pushd /wt && ls", ["/wt"]),
        ("cd /wt && cd src && ls", ["/wt/src"]),
        ("ls; cd /wt && ls", ["/repo", "/wt"]),
        ("git -C /wt status", ["/wt"]),
        ("cd /wt && git -C sub log", ["/wt/sub"]),
        ("FOO=1 git -C /wt status", ["/wt"]),
        ("if git -C /wt diff; then echo y; fi", ["/wt", "/repo"]),
        ("cd /wt && if true; then ls; fi", ["/wt", "/wt"]),
        ("echo /wt", ["/repo"]),
        ("grep -rn x /wt/src", ["/repo"]),
        ("echo cd /wt", ["/repo"]),
        ("bash -c 'cd /wt && ls'", ["/repo"]),
        ("cd /wt", []),
        ("ls > /wt/out", ["/repo", "/repo"]),
        # A newline ends a command: one line's directory change is the next
        # line's only when bash would keep it.
        ("cd /wt && ls\ncd /main && ls", ["/wt", "/main"]),
        ("cd /wt\nls", ["/wt"]),
        ("cd /wt &&\n  ls", ["/wt"]),
        ("cd /wt\n\nls", ["/wt"]),
        ("ls 'a\nb'", ["/repo"]),
        # cd is logical: `..` comes off the path as written.
        ("cd /wt/../main && ls", ["/main"]),
        ("cd /wt; cd /main && ls", ["/main"]),
        ("cd /wt && cd ../main && ls", ["/main"]),
        ("git diff -- /wt", ["/repo"]),
    ],
)
def test_working_directories_follow_cd_and_git_C(command, places):
    assert shell.working_directories(command, HERE) == [Path(p) for p in places]


@pytest.mark.parametrize(
    "command",
    [
        "cd /wt | ls",
        "cd /wt & ls",
        "cd /wt || exit 1",
        "true && cd /wt || ls",
        "(cd /wt && ls)",
        "if cd /wt; then ls; fi",
        "command cd /wt && ls",
        "FOO=1 cd /wt && ls",
        "cd -P /wt && ls",
        "cd && ls",
        "cd - && ls",
        "cd ~/wt && ls",
        "cd $WT && ls",
        "cd $(pwd)/x && ls",
        "cd /w* && ls",
        "popd && ls",
        "cd /wt 2>/dev/null && ls",
        "git --git-dir /g status",
        "cd '/wt",
        "cd /wt\n| ls",
        "cd /wt &\nls",
    ],
)
def test_working_directories_cannot_tell(command):
    assert shell.working_directories(command, HERE) is None


def test_git_C_is_left_for_a_physical_resolution():
    """``git -C`` is a chdir, which reads ``..`` after a symlink physically;
    a lexical normalisation here would hand the caller a different directory
    from the one git runs in."""
    assert shell.working_directories("cd /wt/x/.. && git -C link/.. log", HERE) == [
        Path("/wt") / "link" / "..",
    ]
