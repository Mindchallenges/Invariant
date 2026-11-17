"""Repro for BUG_126 — see the work-item entry (`invariant item show BUG_126`).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrappers; the assertions must then pass unchanged.

Symptom (the half expressible in-target): the gate receipt does not cover a
session file that is new and untracked.

* Mixed file set (one modified tracked file, one new untracked file): the
  architect writes a receipt, the new file is then edited, and the reviewer's
  check still MATCHES. The fast path would fire on a moved tree, which is
  the case the receipt exists to detect.
* All-new file set: the architect's empty-diff guard refuses to write a
  receipt, so a change made only of new files never gets the fast path.

The recipe is not retyped here. Each test runs the fenced shell block that
the shipped agent text carries under feature-architect's "Gate receipt"
heading and compliance-reviewer's "5a" heading. Since the fix (ADR-026) those
blocks are engine commands, ``invariant pipeline coverage`` and ``invariant
pipeline receipt``, so the harness runs their ``invariant`` lines through the
CLI in a throwaway project with a session and a pipeline, and the match rule
is the reviewer's prose: the receipt command exits 0. Only how the test
obtains the receipt changed with the fix; what it asserts did not, except the
control's first check, which looked for the old receipt file under
``.build/gate`` and now asks the record for the receipt instead.

Asserted (SYMPTOM): whether the reviewer's check matches after a session file
moved, and whether an all-new change gets a usable receipt.
NOT asserted: the suspected cause (``git diff HEAD`` not seeing untracked
files) or any fix shape (``git add -N``, an engine command).

Not covered: half (1) of the entry, the host permission system refusing the
architect's write of ``.build/gate/gate-receipt.txt`` on Dang. That is a
property of the host's runtime permission set and is recorded as residue.

Contract (shipped text, feature-architect.md "Gate receipt"): "If the tree
it sees is byte-identical (same ``head`` + same ``diff_sha``), it trusts
these coverage numbers ... If it differs, the receipt is ignored and the
reviewer runs its full build+test+coverage." BUG_126 Expected: "A receipt
never vouches for a file whose content it did not hash."
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import shlex
import shutil
import subprocess

import pytest
import yaml

from invariant.cli.main import main
from invariant.core.manifest import MANIFEST_RELPATH
from invariant.hosts.claude_code import DATA_DIR

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the receipt signs a git tree",
)

ENGINE = re.compile(r"^invariant (.+)$", re.MULTILINE)


def recipe(agent: str, heading: str) -> str:
    """The first fenced bash block after ``heading`` in the shipped agent."""
    text = (DATA_DIR / "agents" / f"{agent}.md").read_text(encoding="utf-8")
    start = text.index(heading)
    body = text[start:]
    m = re.search(r"```bash\n(.*?)```", body, re.DOTALL)
    assert m, f"{agent}: no bash block after {heading!r}"
    block = m.group(1)
    assert ENGINE.search(block), f"{agent}: no invariant command in the block"
    return block


ARCHITECT = recipe("feature-architect", "### Gate receipt")
REVIEWER = recipe("compliance-reviewer", "### 5a")


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def run(block, repo, files):
    """Each ``invariant`` line of the block, through the CLI in ``repo``.

    The engine takes the tree from the session's worktree, so ``files`` names
    nothing here; it is kept so the call sites read as they did. Returns the
    exit code and output of each command, in order.
    """
    results = []
    for line in ENGINE.findall(block):
        argv = shlex.split(line.split("#", 1)[0])
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(argv)
        results.append((argv, code, out.getvalue()))
    return results


def architect(repo, files):
    return "\n".join(out for _argv, _code, out in run(ARCHITECT, repo, files))


def reviewer_matches(repo, files) -> bool:
    """The reviewer's block, then its prose rule: Match iff the receipt
    command exits 0."""
    results = run(REVIEWER, repo, files)
    checks = [code for argv, code, _out in results if argv[:2] == ["pipeline", "receipt"]]
    assert checks, "the reviewer's block runs no receipt check"
    return checks[-1] == 0


def receipt_recorded(repo) -> bool:
    """A coverage run is on record with the tree it measured signed."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["pipeline", "status", "--json"]) == 0
    run_ = json.loads(out.getvalue())["evidence"].get("coverage_run") or {}
    return bool(run_.get("tree"))


#: Every ``*.py`` file in the tree is reported fully covered, so the floor is
#: met and the receipt turns on the tree alone.
FAKE_COVERAGE = """#!/bin/sh
echo '1 passed in 0.01s'
for f in *.py; do
  [ -f "$f" ] && echo "$f      10      0   100%"
done
exit 0
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.invalid")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "tracked.py").write_text("x = 1\n")
    (tmp_path / ".gitignore").write_text(".build/\n")
    (tmp_path / "fake-cov.sh").write_text(FAKE_COVERAGE)
    manifest = tmp_path / MANIFEST_RELPATH
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "project": {"name": "demo", "ecosystems": ["python"]},
                "commands": {"test": "sh fake-cov.sh", "coverage": "sh fake-cov.sh"},
            }
        )
    )
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    (tmp_path / ".build" / "gate").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
    assert main(["session", "start", "x"]) == 0
    assert main(["pipeline", "start", "--task", "t"]) == 0
    return tmp_path


def test_BUG_126_control_the_harness_sees_a_moved_tracked_file(repo):
    """Control, not the repro: the harness discriminates. An unchanged tree
    matches, and an edit to the MODIFIED file is caught. If this fails, the
    two xfails below prove nothing."""
    (repo / "tracked.py").write_text("x = 2\n")
    (repo / "new.py").write_text("y = 1\n")
    files = ["tracked.py", "new.py"]
    architect(repo, files)
    assert receipt_recorded(repo)
    assert reviewer_matches(repo, files)
    (repo / "tracked.py").write_text("x = 3\n")
    assert not reviewer_matches(repo, files)


def test_BUG_126_receipt_stops_matching_when_an_untracked_session_file_moves(repo):
    (repo / "tracked.py").write_text("x = 2\n")
    (repo / "new.py").write_text("y = 1\n")
    files = ["tracked.py", "new.py"]
    architect(repo, files)
    assert reviewer_matches(repo, files), "precondition: unchanged tree matches"

    (repo / "new.py").write_text("y = 'edited after the gate'\n")

    assert not reviewer_matches(repo, files), (
        "fast path fires on a moved tree: new.py was edited after the "
        "architect's gate and the receipt still matches"
    )


def test_BUG_126_an_all_new_change_gets_a_matching_receipt(repo):
    (repo / "new.py").write_text("y = 1\n")
    files = ["new.py"]
    said = architect(repo, files)

    assert reviewer_matches(repo, files), (
        "no fast path for a change made only of new files; architect said: "
        + said.strip()
    )
