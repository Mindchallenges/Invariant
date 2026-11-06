"""The ratchet baseline (IDEA_031 A3 increment 5, ADR-012 D6).

`guidelines.baseline` names a file of pre-existing pattern and boundary
findings, one entry per line: `rule-id  path  fingerprint  count`. The file
is read in full or not at all: a line that does not parse, a duplicate, a
path outside the project, a rule the ruleset lacks and a rule that is not
pattern or boundary (DN-4) each fail the gates that read it, and none of them
yields a smaller record.

Inside `guidelines.mechanical_clean`, F findings of one (rule, path,
fingerprint) against N recorded entries give min(F, N) baselined, F - N new
when F > N, and N - F stale when N > F, for each touched file. Baselined
findings do not block, whether or not their file is touched (ruling 5).
Stale entries are reported only; failing them is increment 6's gate (DN-5).

`doctor` carries a `guidelines baseline` row that warns, never fails.
"""

from __future__ import annotations

import pytest
from conftest import write_manifest
from test_cli import out, payload, run
from test_domains import git_project
from test_guidelines_domain import RULESET_REL, write
from test_guidelines_findings import RULESET as FINDINGS_RULESET

from invariant.core import findings, guidelines
from invariant.core.findings import Finding, fingerprint, ratchet
from invariant.core.guidelines import (
    BASELINE_KINDS,
    BaselineEntry,
    BaselineError,
    baseline_rule_problems,
    parse_baseline,
    render_baseline,
)
from invariant.core.manifest import Manifest
from invariant.core.rules import RuleContext, all_rules
from invariant.core.rules import evaluate as evaluate_rules

RULE = "guidelines.mechanical_clean"
REGISTERED = frozenset(spec.id for spec in all_rules())
BASELINE_REL = "docs/guidelines/baseline.txt"
PRINT = "style.no-print"
TODO = "style.no-todo"
FP = "3f2a9c1b7e44"
FP2 = "0123456789ab"

#: The findings tests' ruleset, plus a cochange pair and a judgement rule, so a
#: baseline entry naming a kind that has no fingerprint can be refused.
RULESET = FINDINGS_RULESET + """\
  - id: tests.paired
    section: style
    title: A module change touches its test
    statement: A change to app/X.py touches tests/test_X.py.
    severity: warn
    phases: [landing]
    applies_to: ["app/*.py"]
    check: {kind: cochange, form: pair, when: {change: any}, then: {each: "tests/test_{stem}.py"}}
  - id: style.judged
    section: style
    title: Judged by a reviewer
    statement: Something a model judges.
    severity: warn
    phases: [review]
    applies_to: ["app/**"]
    check: {kind: judgement, evidence: citation, scope: diff}
"""


def stamp(line: str, rule: str = PRINT) -> str:
    return fingerprint(rule, findings.collapse(line))


def entry_line(rule: str, path: str, fp: str, count: int) -> str:
    return f"{rule}  {path}  {fp}  {count}\n"


# -- the format (D6) --------------------------------------------------------------


class TestTheFormat:
    def test_one_entry_per_line(self):
        (entry,) = parse_baseline(entry_line(PRINT, "app/m.py", FP, 2))
        assert (entry.rule, entry.path, entry.fingerprint, entry.count) == (
            PRINT, "app/m.py", FP, 2,
        )
        assert entry.key == (PRINT, "app/m.py", FP)

    def test_everything_between_the_rule_and_the_last_two_fields_is_the_path(self):
        (entry,) = parse_baseline(f"{PRINT}  app/my  odd\tfile.py   {FP}  1\n")
        assert entry.path == "app/my  odd\tfile.py"

    def test_a_line_is_a_comment_only_when_its_first_non_blank_is_a_hash(self):
        text = (
            "# header\n"
            "   # an indented comment\n"
            "\n"
            "   \n"
            + entry_line(PRINT, "app/#tagged.py", FP, 1)
        )
        (entry,) = parse_baseline(text)
        assert entry.path == "app/#tagged.py"
        assert entry.line == 5

    def test_an_empty_file_is_an_empty_baseline(self):
        assert parse_baseline("") == ()
        assert parse_baseline("# only a header\n") == ()

    def test_a_path_is_kept_in_its_canonical_spelling(self):
        (entry,) = parse_baseline(entry_line(PRINT, "./app//m.py", FP, 1))
        assert entry.path == "app/m.py"

    @pytest.mark.parametrize(
        ("line", "needle"),
        [
            (f"{PRINT} app/m.py 1", "fewer than four fields"),
            (f"{PRINT} {FP} 1", "fewer than four fields"),
            (f"{PRINT} app/m.py {FP.upper()} 1", "fingerprint"),
            (f"{PRINT} app/m.py {FP[:-1]} 1", "fingerprint"),
            (f"{PRINT} app/m.py {FP}0 1", "fingerprint"),
            (f"{PRINT} app/m.py {FP} 0", "positive integer"),
            (f"{PRINT} app/m.py {FP} -1", "positive integer"),
            (f"{PRINT} app/m.py {FP} 01", "positive integer"),
            (f"{PRINT} app/m.py {FP} +1", "positive integer"),
            (f"{PRINT} app/m.py {FP} 1.0", "positive integer"),
            (f"{PRINT} app/m.py {FP} two", "positive integer"),
            (f"{PRINT} ../out.py {FP} 1", "outside the project"),
            (f"{PRINT} /elsewhere/out.py {FP} 1", "outside the project"),
            (f"{PRINT} . {FP} 1", "outside the project"),
        ],
    )
    def test_a_line_that_does_not_parse_refuses_the_file(self, line, needle):
        with pytest.raises(BaselineError) as caught:
            parse_baseline("# header\n" + line + "\n")
        (problem,) = caught.value.problems
        assert "line 2" in problem
        assert needle in problem

    def test_an_entry_recorded_twice_is_refused(self):
        text = entry_line(PRINT, "app/m.py", FP, 1) + entry_line(PRINT, "./app/m.py", FP, 2)
        with pytest.raises(BaselineError) as caught:
            parse_baseline(text)
        (problem,) = caught.value.problems
        assert "line 2" in problem and "already recorded on line 1" in problem

    def test_the_same_path_under_another_fingerprint_is_not_a_duplicate(self):
        text = entry_line(PRINT, "app/m.py", FP, 1) + entry_line(PRINT, "app/m.py", FP2, 1)
        assert len(parse_baseline(text)) == 2

    def test_every_problem_is_named_never_a_smaller_record(self):
        text = (
            f"{PRINT} app/m.py 1\n"
            + entry_line(PRINT, "app/n.py", FP, 1)
            + f"{PRINT} app/o.py {FP} 0\n"
        )
        with pytest.raises(BaselineError) as caught:
            parse_baseline(text)
        assert len(caught.value.problems) == 2
        assert "line 1" in caught.value.problems[0]
        assert "line 3" in caught.value.problems[1]


class TestTheWriter:
    def test_entries_are_sorted_by_path_rule_and_fingerprint_under_one_header(self):
        text = render_baseline([
            BaselineEntry(TODO, "app/b.py", FP, 1),
            BaselineEntry(PRINT, "app/b.py", FP2, 1),
            BaselineEntry(PRINT, "app/b.py", FP, 3),
            BaselineEntry(PRINT, "app/a.py", FP2, 2),
        ])
        lines = text.splitlines()
        assert lines[0].startswith("# ")
        assert sum(1 for line in lines if line.lstrip().startswith("#")) == 1
        assert [tuple(line.split()) for line in lines[1:]] == [
            (PRINT, "app/a.py", FP2, "2"),
            (PRINT, "app/b.py", FP2, "1"),
            (PRINT, "app/b.py", FP, "3"),
            (TODO, "app/b.py", FP, "1"),
        ]
        assert text.endswith("\n")

    def test_an_empty_baseline_is_its_header_alone(self):
        text = render_baseline([])
        assert len(text.splitlines()) == 1 and text.startswith("# ")
        assert parse_baseline(text) == ()

    def test_what_it_writes_parses_back_unchanged(self):
        entries = [
            BaselineEntry(PRINT, "app/my  odd file.py", FP, 2),
            BaselineEntry(TODO, "app/#x.py", FP2, 1),
        ]
        parsed = parse_baseline(render_baseline(entries))
        assert sorted(e.key + (e.count,) for e in parsed) == sorted(
            e.key + (e.count,) for e in entries
        )

    def test_it_carries_no_managed_marker(self):
        assert "invariant-managed" not in render_baseline([BaselineEntry(PRINT, "a.py", FP, 1)])

    @pytest.mark.parametrize("path", [" app/m.py", "app/m.py ", "app/a\nb.py", "app/a\rb.py"])
    def test_a_path_the_format_cannot_hold_is_refused(self, path):
        with pytest.raises(BaselineError, match="cannot be written"):
            render_baseline([BaselineEntry(PRINT, path, FP, 1)])


class TestTheRulesAnEntryNames:
    def ruleset(self):
        return guidelines.parse(RULESET, registered=REGISTERED)

    def test_only_pattern_and_boundary_are_baselined(self):
        assert BASELINE_KINDS == ("pattern", "boundary")

    def test_pattern_and_boundary_rules_are_accepted(self):
        entries = parse_baseline(
            entry_line(PRINT, "app/m.py", FP, 1)
            + entry_line("layers.app-never-imports-tools", "app/m.py", FP, 1)
        )
        assert baseline_rule_problems(entries, self.ruleset()) == []

    def test_a_rule_the_ruleset_lacks_is_named(self):
        entries = parse_baseline("# h\n" + entry_line("style.gone", "app/m.py", FP, 1))
        (problem,) = baseline_rule_problems(entries, self.ruleset())
        assert "line 2" in problem and "style.gone" in problem
        assert "no rule" in problem

    def test_a_cochange_rule_is_refused_because_its_findings_have_no_fingerprint(self):
        entries = parse_baseline(entry_line("tests.paired", "app/m.py", FP, 1))
        (problem,) = baseline_rule_problems(entries, self.ruleset())
        assert "cochange" in problem and "DN-4" in problem

    def test_a_judgement_rule_is_refused(self):
        entries = parse_baseline(entry_line("style.judged", "app/m.py", FP, 1))
        (problem,) = baseline_rule_problems(entries, self.ruleset())
        assert "judgement" in problem


# -- the ratchet ------------------------------------------------------------------


def found(*, rule=PRINT, path="app/m.py", line=1, fp=FP, status="new", severity="blocker",
          kind="pattern") -> Finding:
    return Finding(rule=rule, kind=kind, severity=severity, path=path, status=status,
                   line=line, fingerprint=fp)


def recorded(count, *, rule=PRINT, path="app/m.py", fp=FP) -> BaselineEntry:
    return BaselineEntry(rule, path, fp, count)


class TestTheRatchet:
    def test_f_over_n_baselines_n_and_leaves_f_minus_n_new(self):
        result = ratchet([found(line=1), found(line=2), found(line=3)], [recorded(2)],
                         touched={"app/m.py"})
        assert [f.status for f in result.findings] == ["baselined", "baselined", "new"]
        assert result.stale == ()

    def test_n_over_f_baselines_f_and_leaves_n_minus_f_stale(self):
        result = ratchet([found()], [recorded(3)], touched={"app/m.py"})
        assert [f.status for f in result.findings] == ["baselined"]
        (stale,) = result.stale
        assert (stale.rule, stale.path, stale.fingerprint) == (PRINT, "app/m.py", FP)
        assert (stale.recorded, stale.found) == (3, 1)

    def test_an_entry_in_a_touched_file_with_no_finding_is_wholly_stale(self):
        (stale,) = ratchet([], [recorded(2)], touched={"app/m.py"}).stale
        assert (stale.recorded, stale.found) == (2, 0)

    def test_an_entry_in_an_untouched_file_is_never_stale(self):
        assert ratchet([], [recorded(2)], touched={"app/other.py"}).stale == ()

    def test_a_finding_with_no_entry_stays_new(self):
        result = ratchet([found(fp=FP2)], [recorded(1)], touched={"app/m.py"})
        assert [f.status for f in result.findings] == ["new"]

    def test_touching_the_file_never_forfeits_its_baseline(self):
        """Ruling 5: the finding is in a touched file and stays protected."""
        result = ratchet([found()], [recorded(1)], touched={"app/m.py"})
        assert not result.findings[0].counts

    def test_a_warn_finding_is_baselined_too(self):
        result = ratchet([found(rule=TODO, severity="warn")], [recorded(1, rule=TODO)],
                         touched={"app/m.py"})
        assert result.findings[0].status == "baselined"

    def test_an_excepted_finding_consumes_no_entry(self):
        result = ratchet([found(status="excepted")], [recorded(1)], touched={"app/m.py"})
        assert result.findings[0].status == "excepted"
        (stale,) = result.stale
        assert stale.found == 0

    def test_an_unanswerable_file_has_no_stale_entries(self):
        """Nothing can say what the file holds, so nothing is called stale."""
        result = ratchet([found(status="unanswerable", line=None, fp=None)], [recorded(2)],
                         touched={"app/m.py"})
        assert result.stale == ()
        assert result.findings[0].counts

    def test_a_cochange_finding_is_never_baselined(self):
        finding = found(kind="cochange", line=None, fp=None)
        result = ratchet([finding], [recorded(1)], touched={"app/m.py"})
        assert result.findings[0].status == "new"

    def test_a_baselined_finding_records_as_a_pass(self):
        (finding,) = ratchet([found()], [recorded(1)], touched={"app/m.py"}).findings
        record = finding.record("abcdefabcdef")
        assert record["status"] == "pass" and record["finding"] == "baselined"
        assert not finding.blocks

    def test_counted_keys_only_new_fingerprinted_findings(self):
        assert findings.counted([
            found(line=1), found(line=2), found(fp=FP2),
            found(status="excepted", fp=FP2),
            found(status="unanswerable", fp=None),
            found(kind="cochange", fp=None),
        ]) == {(PRINT, "app/m.py", FP): 2, (PRINT, "app/m.py", FP2): 1}


# -- the rule ---------------------------------------------------------------------


PYTHON = {"name": "demo", "ecosystems": ["python"]}


def context(make_manifest, root, files, *, baseline=BASELINE_REL, **extra):
    cfg = {"ruleset": RULESET_REL}
    if baseline is not None:
        cfg["baseline"] = baseline
    body = {"version": 1, "project": PYTHON, "guidelines": cfg, **extra}
    return RuleContext(
        manifest=make_manifest(body, root), cwd=root,
        session={"files": list(files), "base": ""},
    )


def clean_of(ctx):
    (result,) = evaluate_rules([RULE], ctx)
    return result.verdict


@pytest.fixture
def tree(tmp_path):
    write(tmp_path, RULESET_REL, RULESET)
    return tmp_path


class TestTheRule:
    def test_no_baseline_configured_makes_every_finding_new(self, make_manifest, tree):
        """DN-1: null is no baseline."""
        write(tree, "app/m.py", "print(x)\n")
        verdict = clean_of(context(make_manifest, tree, ["app/m.py"], baseline=None))
        assert not verdict.ok
        assert "style.no-print app/m.py:1" in verdict.message

    def test_a_baselined_finding_does_not_block_and_is_named(self, make_manifest, tree):
        write(tree, "app/m.py", "print(x)\n")
        write(tree, BASELINE_REL, entry_line(PRINT, "app/m.py", stamp("print(x)"), 1))
        verdict = clean_of(context(make_manifest, tree, ["app/m.py"]))
        assert verdict.ok and not verdict.skipped
        assert "1 baselined" in verdict.message
        assert "style.no-print app/m.py:1" in verdict.detail
        (record,) = [r for r in verdict.findings if r["rule"] == PRINT]
        assert record["finding"] == "baselined" and record["status"] == "pass"

    def test_findings_beyond_the_recorded_count_are_new(self, make_manifest, tree):
        write(tree, "app/m.py", "print(x)\nprint(x)\n")
        write(tree, BASELINE_REL, entry_line(PRINT, "app/m.py", stamp("print(x)"), 1))
        verdict = clean_of(context(make_manifest, tree, ["app/m.py"]))
        assert not verdict.ok
        assert "style.no-print app/m.py:2" in verdict.message
        assert "app/m.py:1" not in verdict.message

    def test_a_stale_entry_is_reported_and_does_not_fail(self, make_manifest, tree):
        """DN-5: failing a stale entry is `baseline_only_shrinks`' job."""
        write(tree, "app/m.py", "x = 1\n")
        write(tree, BASELINE_REL, entry_line(PRINT, "app/m.py", FP, 2))
        verdict = clean_of(context(make_manifest, tree, ["app/m.py"]))
        assert verdict.ok
        assert "1 stale baseline entr" in verdict.detail
        assert f"{PRINT} app/m.py {FP} (recorded 2, found 0)" in verdict.detail
        assert "invariant guidelines baseline --prune" in verdict.detail

    def test_an_entry_for_an_untouched_file_is_not_named(self, make_manifest, tree):
        write(tree, "app/m.py", "x = 1\n")
        write(tree, BASELINE_REL, entry_line(PRINT, "app/other.py", FP, 2))
        verdict = clean_of(context(make_manifest, tree, ["app/m.py"]))
        assert verdict.ok
        assert "stale" not in verdict.detail
        assert "baselined" not in verdict.message

    @pytest.mark.parametrize(
        ("text", "needle"),
        [
            (f"{PRINT} app/m.py {FP}\n", "fewer than four fields"),
            (entry_line(PRINT, "app/m.py", FP, 1) * 2, "already recorded"),
            (entry_line("style.gone", "app/m.py", FP, 1), "style.gone"),
            (entry_line("tests.paired", "app/m.py", FP, 1), "DN-4"),
            (entry_line(PRINT, "../m.py", FP, 1), "outside the project"),
        ],
    )
    def test_a_baseline_that_does_not_parse_fails_the_gate(self, make_manifest, tree, text,
                                                            needle):
        write(tree, "app/m.py", "x = 1\n")
        write(tree, BASELINE_REL, text)
        verdict = clean_of(context(make_manifest, tree, ["app/m.py"]))
        assert not verdict.ok and not verdict.skipped
        assert f"the guidelines baseline {BASELINE_REL}" in verdict.message
        assert needle in verdict.detail

    def test_a_configured_baseline_that_is_missing_fails(self, make_manifest, tree):
        """DN-1: a configured path whose file does not exist is an error."""
        write(tree, "app/m.py", "x = 1\n")
        verdict = clean_of(context(make_manifest, tree, ["app/m.py"]))
        assert not verdict.ok
        assert "does not exist" in verdict.message
        assert any("--record" in fix for fix in verdict.fixes)

    def test_a_baseline_outside_the_project_fails(self, make_manifest, tree):
        verdict = clean_of(context(make_manifest, tree, [], baseline="../baseline.txt"))
        assert not verdict.ok
        assert "names nothing inside the project" in verdict.message

    def test_a_baseline_exempt_from_isolation_fails(self, make_manifest, tree):
        """Ruling 13: a generated file is never exempt, read or written."""
        write(tree, ".claude/baseline.txt", "")
        verdict = clean_of(context(make_manifest, tree, [], baseline=".claude/baseline.txt"))
        assert not verdict.ok
        assert "exempt from session isolation" in verdict.message


# -- the doctor row ---------------------------------------------------------------


class TestTheDoctorRow:
    @pytest.fixture(autouse=True)
    def _inside(self, project, monkeypatch):
        monkeypatch.chdir(project)

    def configure(self, root, baseline=BASELINE_REL):
        cfg = {"ruleset": RULESET_REL}
        if baseline is not None:
            cfg["baseline"] = baseline
        write_manifest(root, {"version": 1, "project": PYTHON, "guidelines": cfg})
        write(root, RULESET_REL, RULESET)

    def row(self, capsys):
        return next(ln for ln in out(capsys).splitlines() if "guidelines baseline" in ln)

    def rows(self, capsys):
        return {c["check"]: c for c in payload(capsys)["checks"]}

    def test_no_ruleset_passes(self, project, capsys):
        capsys.readouterr()
        run("doctor")
        assert "no guidelines ruleset configured" in self.row(capsys)

    def test_no_baseline_configured_passes(self, project, capsys):
        self.configure(project, baseline=None)
        capsys.readouterr()
        run("--json", "doctor")
        row = self.rows(capsys)["guidelines baseline"]
        assert row["ok"] is True and row["warn"] is False
        assert "no baseline configured" in row["detail"]

    def test_a_baseline_passes_with_its_counts(self, project, capsys):
        self.configure(project)
        write(project, BASELINE_REL,
              entry_line(PRINT, "app/m.py", FP, 2) + entry_line(TODO, "app/n.py", FP2, 1))
        capsys.readouterr()
        run("--json", "doctor")
        row = self.rows(capsys)["guidelines baseline"]
        assert row["ok"] is True and row["warn"] is False
        assert "2 entries" in row["detail"]
        assert "3 finding(s)" in row["detail"]
        assert "2 file(s)" in row["detail"]
        assert BASELINE_REL in row["detail"]

    @pytest.mark.parametrize(
        "text",
        [f"{PRINT} app/m.py {FP} 0\n", None],
        ids=["malformed", "missing"],
    )
    def test_an_unreadable_baseline_warns_and_never_fails(self, project, capsys, text):
        self.configure(project)
        write(project, BASELINE_REL, "")
        capsys.readouterr()
        clean_code = run("doctor")
        if text is None:
            (project / BASELINE_REL).unlink()
        else:
            write(project, BASELINE_REL, text)
        capsys.readouterr()
        code = run("doctor")
        row = self.row(capsys)
        assert row.lstrip().startswith("warn")
        assert code == clean_code
        capsys.readouterr()
        run("--json", "doctor")
        row = self.rows(capsys)["guidelines baseline"]
        assert row["ok"] is True and row["warn"] is True


# -- recording through a real repository -------------------------------------------


class TestRecordingOverARepository:
    def test_record_then_the_gate_passes_the_recorded_findings(self, tmp_path, monkeypatch,
                                                               capsys):
        """The record and the gate use one evaluator, so what --record wrote
        is exactly what the gate then holds as pre-existing."""
        root = tmp_path / "proj"
        root.mkdir()
        write(root, RULESET_REL, RULESET)
        write(root, "app/m.py", "print(x)\nx = 1  # TODO later\n")
        write(root, BASELINE_REL, "")
        git_project(root, {"version": 1, "project": PYTHON,
                           "guidelines": {"ruleset": RULESET_REL, "baseline": BASELINE_REL}})
        monkeypatch.chdir(root)
        assert run("guidelines", "baseline", "--record") == 0
        text = (root / BASELINE_REL).read_text(encoding="utf-8")
        assert entry_line(PRINT, "app/m.py", stamp("print(x)"), 1).split() in [
            line.split() for line in text.splitlines()
        ]
        ctx = RuleContext(manifest=Manifest.locate(root), cwd=root,
                          session={"files": ["app/m.py"], "base": ""})
        verdict = clean_of(ctx)
        assert verdict.ok
        assert "2 baselined" in verdict.message


def test_a_baseline_that_is_not_text_fails_the_gate(make_manifest, tree):
    write(tree, "app/m.py", "x = 1\n")
    (tree / BASELINE_REL).write_bytes(b"\xff\xfe")
    verdict = clean_of(context(make_manifest, tree, ["app/m.py"]))
    assert not verdict.ok
    assert "could not be read" in verdict.message
