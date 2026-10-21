"""Regression guard for BUG_054 (docs/backlog/BUG_054.md), closed not_reproducible.

BUG_054 reported that a low-ceiling file beside high-coverage neighbours could
not be held to its own lower floor. It did not reproduce: `coverage_baseline`
already expresses a per-path number. This test passes on main and guards that
per-path hold; if it goes red, the capability BUG_054 asked for has regressed.

Symptom asserted: a file measured at 14.83% beside neighbours at 95%, under a
global floor of 90, "blocks indefinitely" and "no key the manifest offers can
express" that this one path is held to a lower number while the floor keeps
binding its neighbours. The test asks the OBSERVABLE verdict of
`coverage.meets_floor` whether, under the best configuration the current
manifest allows, the low-ceiling file can be held to a stated per-path number
N = 14: passes at or above N, BLOCKS below N (so the floor is not simply lost,
as `coverage_exclude` loses it), and an undeclared neighbour below 90 still
blocks.

Explicitly NOT asserted: the suspected cause (that `floor` is read once at the
top of `_coverage_meets_floor`), any new manifest key, or verdict wording.

Salvaged from d8313ac (an abandoned session's repro), adapted: that test
asserted that two baseline entries with the same input produce different
verdict sentences, which is red by construction — the rule receives identical
input for both — and could not pass unchanged after any fix.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.core.rules import RuleContext, evaluate

FLOOR = 90
CEILING_FILE = "src/app/export_recovery_phrase_sheet.py"
NEIGHBOURS = ("src/app/recovery_phrase_copy.py", "src/app/library_model.py")
LIVE = "def f():\n    return g()\n"
RECORD = "scripts/coverage_floor_ratchet.txt"
#: The per-path number the project wants to hold the low-ceiling file to.
PER_PATH_FLOOR = 14


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "master")
    _git(main, "config", "user.email", "test@example.invalid")
    _git(main, "config", "user.name", "Test")
    (main / "README.md").write_text("x\n", encoding="utf-8")
    _git(main, "add", "-A")
    _git(main, "commit", "-qm", "init")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", str(wt), "-b", "session/x")
    return main, wt


def verdict_for(repo, measured: dict[str, float], *, per_path: bool):
    """The verdict of coverage.meets_floor on one measured, touched file set."""
    main, wt = repo
    gates = {"coverage_min": FLOOR, "coverage_scope": "touched"}
    if per_path:
        # The best carrier the CURRENT manifest has for "this path is held to
        # N": a recorded per-file number. Nothing else (coverage_min,
        # coverage_exclude, non_source) can name a number for one path.
        gates["coverage_baseline"] = RECORD
        record = wt / RECORD
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(
            "# measured ceiling, dated; view-body lines no runner executes\n"
            f"{CEILING_FILE} {PER_PATH_FLOOR}\n",
            encoding="utf-8",
        )
    path = main / MANIFEST_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "project": {"name": "demo", "ecosystems": ["python"]},
                "methodology": {"gates": gates},
            }
        ),
        encoding="utf-8",
    )
    coverage = {}
    for rel, pct in measured.items():
        source = wt / rel
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(LIVE, encoding="utf-8")
        coverage[str(source)] = pct
    ctx = RuleContext(
        manifest=Manifest.load(main),
        cwd=main,
        session={
            "id": "s1",
            "files": list(measured),
            "worktree": str(wt),
            "base": "",
        },
        pipeline={"evidence": {"coverage": coverage}},
        payload={},
        extra={},
    )
    (result,) = evaluate(["coverage.meets_floor"], ctx)
    return result.verdict


def neighbours_at(pct: float) -> dict[str, float]:
    return {rel: pct for rel in NEIGHBOURS}


def test_BUG_054_a_low_ceiling_file_can_be_held_to_its_own_floor(repo):
    # 1. Without any per-path statement the file blocks (the reported state).
    plain = verdict_for(repo, {CEILING_FILE: 14.83, **neighbours_at(95.0)},
                        per_path=False)
    assert not plain.ok, plain.message

    # 2. The symptom: held to its own number N, the file at 14.83 PASSES...
    held = verdict_for(repo, {CEILING_FILE: 14.83, **neighbours_at(95.0)},
                       per_path=True)
    assert held.ok, f"blocks forever: {held.message} / {held.detail}"

    # 3. ...and is still HELD: below N it blocks, so the floor was not lost
    #    the way coverage_exclude loses it.
    fell = verdict_for(repo, {CEILING_FILE: 10.0, **neighbours_at(95.0)},
                       per_path=True)
    assert not fell.ok, f"floor lost on the path: {fell.message}"

    # 4. The global floor still binds the undeclared neighbours.
    neighbour = verdict_for(repo, {CEILING_FILE: 14.83, **neighbours_at(85.0)},
                            per_path=True)
    assert not neighbour.ok, f"floor stopped binding neighbours: {neighbour.message}"
    assert f"below the {FLOOR}% floor" in neighbour.message
