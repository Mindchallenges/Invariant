"""`domains/guidelines`: rendering the checklist (IDEA_031 A2 increment 3).

`invariant guidelines render` writes the human checklist from the ruleset, in
the project's `docs.format`, with a first-line marker holding the digest of
what it wrote (ADR-012 D3, ruling DN-B). It is one of the commands of the
generated-file write class (ruling 13): it writes only the file the manifest
declares, never a path exempt from isolation, never a file it did not write,
and nothing at all when the privacy scan of the rendered text is not clean.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import write_manifest
from test_cli import payload, run
from test_guidelines import RULESET

from invariant.core import guidelines
from invariant.core.errors import InvariantError
from invariant.core.rules import RuleContext, all_rules
from invariant.domains.guidelines import GuidelinesDomain, RenderRefused

REGISTERED = frozenset(spec.id for spec in all_rules())
RULESET_REL = "docs/guidelines/ruleset.yaml"
CHECKLIST_REL = "docs/guidelines/checklist.md"


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def domain(make_manifest, root, guidelines_cfg=None, *, session=None, **extra):
    body = {
        "version": 1,
        "project": {"name": "demo"},
        "guidelines": {"ruleset": RULESET_REL, **(guidelines_cfg or {})},
        **extra,
    }
    if guidelines_cfg is False:
        body.pop("guidelines")
    manifest = make_manifest(body)
    return GuidelinesDomain(RuleContext(manifest=manifest, cwd=root, session=session))


def expected(fmt="markdown", text=RULESET):
    return guidelines.render_checklist(
        guidelines.parse(text, registered=REGISTERED), fmt, source=RULESET_REL
    )


@pytest.fixture
def tree(tmp_path):
    write(tmp_path, RULESET_REL, RULESET)
    return tmp_path


class TestRender:
    def test_the_default_checklist_sits_beside_the_ruleset_in_markdown(
        self, make_manifest, tree
    ):
        result = domain(make_manifest, tree).render()
        assert result.relpath == CHECKLIST_REL
        assert result.status == "new" and result.written
        assert (tree / CHECKLIST_REL).read_text(encoding="utf-8") == expected()
        assert result.ruleset == guidelines.digest_of(RULESET.encode("utf-8"))

    def test_sphinx_renders_rst_beside_the_ruleset(self, make_manifest, tree):
        result = domain(make_manifest, tree, docs={"format": "sphinx"}).render()
        assert result.relpath == "docs/guidelines/checklist.rst"
        text = (tree / "docs/guidelines/checklist.rst").read_text(encoding="utf-8")
        assert text == expected("sphinx")
        assert text.startswith(".. invariant-managed: ")

    def test_a_configured_checklist_path_is_honoured(self, make_manifest, tree):
        result = domain(make_manifest, tree, {"checklist": "./docs/rules.md"}).render()
        assert result.relpath == "docs/rules.md"
        assert (tree / "docs/rules.md").is_file()

    def test_dry_run_writes_nothing_and_says_what_it_would_do(self, make_manifest, tree):
        result = domain(make_manifest, tree).render(dry_run=True)
        assert result.status == "new" and not result.written
        assert not (tree / CHECKLIST_REL).exists()

    def test_a_second_render_is_current_and_still_written(self, make_manifest, tree):
        domain(make_manifest, tree).render()
        result = domain(make_manifest, tree).render()
        assert result.status == "current" and result.written

    def test_a_render_from_an_older_ruleset_is_stale_and_rewritten(self, make_manifest, tree):
        domain(make_manifest, tree).render()
        changed = RULESET.replace("severity: warn", "severity: blocker")
        write(tree, RULESET_REL, changed)
        result = domain(make_manifest, tree).render()
        assert result.status == "stale" and result.written
        assert (tree / CHECKLIST_REL).read_text(encoding="utf-8") == expected(text=changed)

    def test_hand_edits_are_named_and_replaced(self, make_manifest, tree):
        domain(make_manifest, tree).render()
        path = tree / CHECKLIST_REL
        path.write_text(path.read_text(encoding="utf-8") + "\nA hand edit.\n", encoding="utf-8")
        result = domain(make_manifest, tree).render()
        assert result.status == "edited" and result.written
        assert "A hand edit." not in path.read_text(encoding="utf-8")

    def test_the_session_worktree_is_read_and_written(self, make_manifest, tree):
        worktree = tree / "wt"
        changed = RULESET.replace("severity: warn", "severity: blocker")
        write(worktree, RULESET_REL, changed)
        result = domain(make_manifest, tree, session={"worktree": str(worktree)}).render()
        assert result.path == worktree / CHECKLIST_REL
        assert result.path.read_text(encoding="utf-8") == expected(text=changed)
        assert not (tree / CHECKLIST_REL).exists()

    def test_no_ruleset_configured_renders_nothing(self, make_manifest, tmp_path):
        result = domain(make_manifest, tmp_path, False).render()
        assert result.status == "no-ruleset" and not result.written
        assert result.path is None
        assert not any(tmp_path.rglob("checklist.*"))


class TestRefusals:
    """Each refusal writes nothing and names its fix."""

    def refused(self, dom, *, dry_run=False) -> RenderRefused:
        with pytest.raises(RenderRefused) as caught:
            dom.render(dry_run=dry_run)
        assert isinstance(caught.value, InvariantError)
        assert caught.value.exit_code == 1
        return caught.value

    def test_a_ruleset_that_does_not_validate(self, make_manifest, tree):
        write(tree, RULESET_REL, RULESET.replace("blocker", "fatal"))
        error = self.refused(domain(make_manifest, tree))
        assert "does not validate" in str(error)
        assert "severity 'fatal'" in str(error)
        assert not (tree / CHECKLIST_REL).exists()

    def test_a_configured_ruleset_that_is_missing(self, make_manifest, tmp_path):
        error = self.refused(domain(make_manifest, tmp_path))
        assert "does not exist" in str(error)

    def test_a_privacy_finding_in_the_rendered_text(self, make_manifest, tree):
        write(tree, RULESET_REL, RULESET.replace(
            "A view never reaches", "Ask TICKET-4242 first. A view never reaches"
        ))
        dom = domain(make_manifest, tree, privacy={"extra": ["TICKET-[0-9]+"]})
        error = self.refused(dom, dry_run=True)
        assert "privacy" in str(error)
        assert "TICKET-4242" not in str(error)
        assert f"{CHECKLIST_REL}:" in str(error)
        self.refused(dom)
        assert not (tree / CHECKLIST_REL).exists()

    @pytest.mark.parametrize(
        "privacy",
        [
            {"enabled": False, "extra": ["TICKET-[0-9]+"]},
            {"scope": ["src"], "extra": ["TICKET-[0-9]+"]},
        ],
        ids=["disabled", "checklist-out-of-scope"],
    )
    def test_the_privacy_scan_ignores_enabled_and_scope(self, make_manifest, tree, privacy):
        write(tree, RULESET_REL, RULESET.replace(
            "A view never reaches", "Ask TICKET-4242 first. A view never reaches"
        ))
        error = self.refused(domain(make_manifest, tree, privacy=privacy))
        assert "privacy" in str(error)
        assert not (tree / CHECKLIST_REL).exists()

    def test_an_existing_file_render_did_not_write(self, make_manifest, tree):
        path = write(tree, CHECKLIST_REL, "# Our hand-written checklist\n")
        error = self.refused(domain(make_manifest, tree), dry_run=True)
        assert "no invariant-managed marker" in str(error)
        self.refused(domain(make_manifest, tree))
        assert path.read_text(encoding="utf-8") == "# Our hand-written checklist\n"

    def test_a_checklist_outside_the_project(self, make_manifest, tree):
        error = self.refused(domain(make_manifest, tree, {"checklist": "../elsewhere.md"}))
        assert "names nothing inside the project" in str(error)

    def test_the_checklist_may_not_be_the_ruleset(self, make_manifest, tree):
        error = self.refused(domain(make_manifest, tree, {"checklist": RULESET_REL}))
        assert "is the ruleset" in str(error)
        assert (tree / RULESET_REL).read_text(encoding="utf-8") == RULESET

    def test_a_checklist_exempt_from_isolation(self, make_manifest, tree):
        error = self.refused(domain(make_manifest, tree, {"checklist": ".claude/checklist.md"}))
        assert "exempt from session isolation" in str(error)
        assert not (tree / ".claude/checklist.md").exists()

    def test_a_checklist_suffix_that_disagrees_with_docs_format(self, make_manifest, tree):
        error = self.refused(domain(make_manifest, tree, {"checklist": "docs/rules.rst"}))
        assert "docs.format is markdown" in str(error)

    def test_a_docs_format_with_no_renderer(self, make_manifest, tree):
        error = self.refused(domain(make_manifest, tree, docs={"format": "asciidoc"}))
        assert "docs.format 'asciidoc'" in str(error)


class TestTheCommand:
    """`invariant guidelines render [--dry-run]`, through the real entry point,
    standing in conftest's `project`."""

    @pytest.fixture(autouse=True)
    def _inside(self, project, monkeypatch):
        monkeypatch.chdir(project)

    def configure(self, root: Path, **extra) -> None:
        write_manifest(root, {
            "version": 1,
            "project": {"name": "demo", "ecosystems": ["python"]},
            "guidelines": {"ruleset": RULESET_REL},
            **extra,
        })
        write(root, RULESET_REL, RULESET)

    def test_render_writes_and_reports(self, project, capsys):
        self.configure(project)
        assert run("guidelines", "render", "--json") == 0
        doc = payload(capsys)
        assert doc["path"] == CHECKLIST_REL
        assert doc["status"] == "new" and doc["written"] is True
        assert doc["dry_run"] is False
        assert (project / CHECKLIST_REL).is_file()

    def test_dry_run_reports_and_writes_nothing(self, project, capsys):
        self.configure(project)
        assert run("guidelines", "render", "--dry-run") == 0
        assert f"would write {CHECKLIST_REL}" in capsys.readouterr().out
        assert not (project / CHECKLIST_REL).exists()

    @pytest.mark.parametrize(
        ("prepare", "needle"),
        [
            ("current", "is current"),
            ("stale", "from an older ruleset"),
            ("edited", "hand edits were replaced"),
        ],
    )
    def test_each_status_reads_as_what_happened(self, project, capsys, prepare, needle):
        self.configure(project)
        run("guidelines", "render")
        path = project / CHECKLIST_REL
        if prepare == "stale":
            write(project, RULESET_REL, RULESET.replace("severity: warn", "severity: blocker"))
        if prepare == "edited":
            path.write_text(path.read_text(encoding="utf-8") + "x\n", encoding="utf-8")
        capsys.readouterr()
        assert run("guidelines", "render") == 0
        assert needle in capsys.readouterr().out

    def test_no_ruleset_is_reported_and_succeeds(self, project, capsys):
        write_manifest(project, {"version": 1, "project": {"name": "demo"}})
        assert run("guidelines", "render") == 0
        assert "no guidelines ruleset configured" in capsys.readouterr().out

    def test_a_refusal_exits_one_and_names_the_fix(self, project, capsys):
        self.configure(project)
        write(project, CHECKLIST_REL, "# hand written\n")
        assert run("guidelines", "render") == 1
        assert "no invariant-managed marker" in capsys.readouterr().err
