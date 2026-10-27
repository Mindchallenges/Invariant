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
from test_guidelines import RULESET, WITH_ALWAYS

from invariant.core import guidelines
from invariant.core.errors import InvariantError
from invariant.core.rules import RenderRefused, RuleContext, all_rules
from invariant.domains.guidelines import GuidelinesDomain

REGISTERED = frozenset(spec.id for spec in all_rules())
RULESET_REL = "docs/guidelines/ruleset.yaml"
CHECKLIST_REL = "docs/guidelines/checklist.md"


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def domain(
    make_manifest, root, guidelines_cfg=None, *, session=None, instruction_files=(), **extra
):
    body = {
        "version": 1,
        "project": {"name": "demo"},
        "guidelines": {"ruleset": RULESET_REL, **(guidelines_cfg or {})},
        **extra,
    }
    if guidelines_cfg is False:
        body.pop("guidelines")
    manifest = make_manifest(body)
    return GuidelinesDomain(
        RuleContext(
            manifest=manifest, cwd=root, session=session,
            host_instruction_files=tuple(instruction_files),
        )
    )


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

    @pytest.mark.parametrize(
        "store",
        [
            {"items": {"path": "docs/backlog"}},
            {"paths": {"reports": "docs/backlog"}},
            {"paths": {"sweeps": "docs/backlog"}},
        ],
        ids=["items", "reports", "sweeps"],
    )
    def test_a_checklist_inside_a_declared_store(self, make_manifest, tree, store):
        """A declared store is exempt from isolation, and a generated file
        never is (ruling 13), so the store's own exemption refuses it too."""
        dom = domain(make_manifest, tree, {"checklist": "docs/backlog/checklist.md"}, **store)
        assert "exempt from session isolation" in str(self.refused(dom, dry_run=True))
        assert not (tree / "docs/backlog/checklist.md").exists()

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



# -- managed instruction-file blocks (A2 increment 4, D3, DN-C) -----------------------


INDEX = "guidelines/index"
CLAUDE = "CLAUDE.md"
OURS = "# Our project\n\nHand-written guidance.\n"


def index_body(text=WITH_ALWAYS, max_lines=40):
    return guidelines.render_index_block(
        guidelines.parse(text, registered=REGISTERED), source=RULESET_REL, max_lines=max_lines
    )


@pytest.fixture
def blocks_tree(tmp_path):
    write(tmp_path, RULESET_REL, WITH_ALWAYS)
    write(tmp_path, CLAUDE, OURS)
    return tmp_path


def host_default(make_manifest, root, guidelines_cfg=None, **extra):
    """A domain the CLI built for a claude_code host: CLAUDE.md by default."""
    return domain(make_manifest, root, guidelines_cfg, instruction_files=(CLAUDE,), **extra)


def only_block(result):
    (block,) = result.blocks
    return block


class TestInstructionBlocks:
    def test_render_never_inserts_a_block_unasked(self, make_manifest, blocks_tree):
        result = host_default(make_manifest, blocks_tree).render()
        block = only_block(result)
        assert (block.relpath, block.name, block.status, block.written) == (
            CLAUDE, INDEX, "absent", False
        )
        assert (blocks_tree / CLAUDE).read_text(encoding="utf-8") == OURS

    def test_insert_appends_the_block_after_the_project_text(self, make_manifest, blocks_tree):
        result = host_default(make_manifest, blocks_tree).render(insert=(INDEX,))
        block = only_block(result)
        assert (block.status, block.written) == ("new", True)
        text = (blocks_tree / CLAUDE).read_text(encoding="utf-8")
        assert text == guidelines.append_block(OURS, INDEX, index_body())

    def test_insert_creates_a_missing_instruction_file(self, make_manifest, blocks_tree):
        (blocks_tree / CLAUDE).unlink()
        host_default(make_manifest, blocks_tree).render(insert=(INDEX,))
        text = (blocks_tree / CLAUDE).read_text(encoding="utf-8")
        assert text == guidelines.block_text(INDEX, index_body())

    def test_a_missing_file_without_insert_is_absent_and_not_created(
        self, make_manifest, blocks_tree
    ):
        (blocks_tree / CLAUDE).unlink()
        assert only_block(host_default(make_manifest, blocks_tree).render()).status == "absent"
        assert not (blocks_tree / CLAUDE).exists()

    def test_after_insertion_the_block_is_found_where_the_operator_moved_it(
        self, make_manifest, blocks_tree
    ):
        host_default(make_manifest, blocks_tree).render(insert=(INDEX,))
        block = guidelines.block_text(INDEX, index_body())
        moved = "# Top\n\n" + block + "\n" + OURS
        write(blocks_tree, CLAUDE, moved)
        result = host_default(make_manifest, blocks_tree).render()
        assert (only_block(result).status, only_block(result).written) == ("current", True)
        assert (blocks_tree / CLAUDE).read_text(encoding="utf-8") == moved

    def test_insert_on_a_file_that_has_the_block_rewrites_it_in_place(
        self, make_manifest, blocks_tree
    ):
        host_default(make_manifest, blocks_tree).render(insert=(INDEX,))
        result = host_default(make_manifest, blocks_tree).render(insert=(INDEX,))
        assert only_block(result).status == "current"
        assert (blocks_tree / CLAUDE).read_text(encoding="utf-8").count("invariant:begin") == 1

    def test_a_block_from_an_older_ruleset_is_stale_and_rewritten(
        self, make_manifest, blocks_tree
    ):
        host_default(make_manifest, blocks_tree).render(insert=(INDEX,))
        changed = WITH_ALWAYS.replace("Touched files meet", "Every touched file meets")
        write(blocks_tree, RULESET_REL, changed)
        result = host_default(make_manifest, blocks_tree).render()
        assert (only_block(result).status, only_block(result).written) == ("stale", True)
        text = (blocks_tree / CLAUDE).read_text(encoding="utf-8")
        assert text == guidelines.append_block(OURS, INDEX, index_body(changed))

    def test_a_hand_edited_block_is_skipped_and_named(self, make_manifest, blocks_tree):
        host_default(make_manifest, blocks_tree).render(insert=(INDEX,))
        path = blocks_tree / CLAUDE
        edited = path.read_text(encoding="utf-8").replace(
            "(always):", "(always, as we agreed):"
        )
        path.write_text(edited, encoding="utf-8")
        result = host_default(make_manifest, blocks_tree).render()
        assert (only_block(result).status, only_block(result).written) == ("edited", False)
        assert path.read_text(encoding="utf-8") == edited
        assert result.written  # the checklist is still rendered

    def test_overwrite_local_takes_the_rendered_block_back(self, make_manifest, blocks_tree):
        host_default(make_manifest, blocks_tree).render(insert=(INDEX,))
        path = blocks_tree / CLAUDE
        path.write_text(
            path.read_text(encoding="utf-8").replace("(always):", "(always!):"),
            encoding="utf-8",
        )
        result = host_default(make_manifest, blocks_tree).render(overwrite_local=True)
        assert (only_block(result).status, only_block(result).written) == ("edited", True)
        assert path.read_text(encoding="utf-8") == guidelines.append_block(
            OURS, INDEX, index_body()
        )

    def test_dry_run_writes_no_block(self, make_manifest, blocks_tree):
        result = host_default(make_manifest, blocks_tree).render(insert=(INDEX,), dry_run=True)
        assert (only_block(result).status, only_block(result).written) == ("new", False)
        assert (blocks_tree / CLAUDE).read_text(encoding="utf-8") == OURS

    def test_the_cap_comes_from_the_manifest(self, make_manifest, blocks_tree):
        dom = host_default(make_manifest, blocks_tree, {"injection": {"max_lines": 2}})
        dom.render(insert=(INDEX,))
        text = (blocks_tree / CLAUDE).read_text(encoding="utf-8")
        assert "- 3 more: `invariant guidelines show`" in text
        assert guidelines.find_blocks(text)[INDEX].body == index_body(max_lines=2)

    def test_configured_instruction_files_replace_the_host_default(
        self, make_manifest, blocks_tree
    ):
        write(blocks_tree, "docs/AGENTS.md", "ours\n")
        dom = host_default(make_manifest, blocks_tree, {"instruction_files": ["./docs/AGENTS.md"]})
        result = dom.render(insert=(INDEX,))
        assert [b.relpath for b in result.blocks] == ["docs/AGENTS.md"]
        assert (blocks_tree / CLAUDE).read_text(encoding="utf-8") == OURS

    def test_a_file_named_twice_is_rendered_once(self, make_manifest, blocks_tree):
        dom = host_default(make_manifest, blocks_tree, {"instruction_files": [CLAUDE, "./CLAUDE.md"]})
        assert len(dom.render(insert=(INDEX,)).blocks) == 1

    def test_no_host_default_and_no_configured_files_renders_no_block(
        self, make_manifest, blocks_tree
    ):
        result = domain(make_manifest, blocks_tree).render(insert=(INDEX,))
        assert result.blocks == ()
        assert (blocks_tree / CLAUDE).read_text(encoding="utf-8") == OURS

    def test_the_session_worktree_holds_the_instruction_file_written(
        self, make_manifest, blocks_tree
    ):
        worktree = blocks_tree / "wt"
        write(worktree, RULESET_REL, WITH_ALWAYS)
        write(worktree, CLAUDE, OURS)
        dom = host_default(make_manifest, blocks_tree, session={"worktree": str(worktree)})
        dom.render(insert=(INDEX,))
        assert "invariant:begin" in (worktree / CLAUDE).read_text(encoding="utf-8")
        assert (blocks_tree / CLAUDE).read_text(encoding="utf-8") == OURS

    def test_other_named_blocks_are_left_alone(self, make_manifest, blocks_tree):
        other = guidelines.block_text("someone/else", "theirs\n")
        write(blocks_tree, CLAUDE, OURS + other)
        host_default(make_manifest, blocks_tree).render(insert=(INDEX,))
        text = (blocks_tree / CLAUDE).read_text(encoding="utf-8")
        assert text.startswith(OURS + other)


class TestInstructionBlockRefusals:
    """Each refusal writes nothing anywhere, the checklist included."""

    def refused(self, dom, **kwargs) -> RenderRefused:
        with pytest.raises(RenderRefused) as caught:
            dom.render(**kwargs)
        return caught.value

    def test_a_malformed_pair_names_the_file_and_line(self, make_manifest, blocks_tree):
        bad = OURS + "<!-- invariant:begin guidelines/index digest=0123456789ab -->\nx\n"
        write(blocks_tree, CLAUDE, bad)
        error = self.refused(host_default(make_manifest, blocks_tree))
        assert f"{CLAUDE} line 4: the begin marker of guidelines/index has no end" in str(error)
        assert (blocks_tree / CLAUDE).read_text(encoding="utf-8") == bad
        assert not (blocks_tree / CHECKLIST_REL).exists()

    def test_an_unknown_block_name_to_insert(self, make_manifest, blocks_tree):
        error = self.refused(host_default(make_manifest, blocks_tree), insert=("guidelines/all",))
        assert "guidelines/all" in str(error) and INDEX in str(error)
        assert (blocks_tree / CLAUDE).read_text(encoding="utf-8") == OURS

    def test_an_instruction_file_outside_the_project(self, make_manifest, blocks_tree):
        dom = host_default(make_manifest, blocks_tree, {"instruction_files": ["../CLAUDE.md"]})
        assert "names nothing inside the project" in str(self.refused(dom))

    def test_an_instruction_file_that_is_not_markdown(self, make_manifest, blocks_tree):
        dom = host_default(make_manifest, blocks_tree, {"instruction_files": ["AGENTS.rst"]})
        assert "is not a markdown file" in str(self.refused(dom))

    @pytest.mark.parametrize("target", [RULESET_REL, CHECKLIST_REL], ids=["ruleset", "checklist"])
    def test_an_instruction_file_that_is_another_guidelines_file(
        self, make_manifest, blocks_tree, target
    ):
        if target == RULESET_REL:
            write(blocks_tree, "docs/guidelines/ruleset.md", WITH_ALWAYS)
            cfg = {
                "ruleset": "docs/guidelines/ruleset.md",
                "instruction_files": ["docs/guidelines/ruleset.md"],
            }
        else:
            cfg = {"instruction_files": [CHECKLIST_REL]}
        error = self.refused(host_default(make_manifest, blocks_tree, cfg))
        assert "is also the guidelines" in str(error)

    def test_an_instruction_file_exempt_from_isolation(self, make_manifest, blocks_tree):
        dom = host_default(make_manifest, blocks_tree, {"instruction_files": [".claude/CLAUDE.md"]})
        assert "exempt from session isolation" in str(self.refused(dom, insert=(INDEX,)))
        assert not (blocks_tree / ".claude/CLAUDE.md").exists()

    def test_an_instruction_file_in_a_declared_store_is_refused_unasked(
        self, make_manifest, blocks_tree
    ):
        """Refused with no --insert and on a dry run: the target is checked,
        not only the write."""
        dom = host_default(
            make_manifest, blocks_tree, {"instruction_files": ["docs/backlog/NOTES.md"]},
            items={"path": "docs/backlog"},
        )
        assert "exempt from session isolation" in str(self.refused(dom, dry_run=True))

    def test_a_privacy_finding_in_the_block_refuses_the_render(self, make_manifest, blocks_tree):
        # Only the block spells a rule as "(always): <title>", so the pattern
        # finds nothing in the checklist, which is scanned first.
        dom = host_default(
            make_manifest, blocks_tree,
            privacy={"enabled": False, "extra": [r"always\): Never skip"]},
        )
        error = self.refused(dom, insert=(INDEX,), dry_run=True)
        assert f"{CLAUDE} ({INDEX} block, line 3)" in str(error)
        assert "Never skip" not in str(error)
        self.refused(dom, insert=(INDEX,))
        assert (blocks_tree / CLAUDE).read_text(encoding="utf-8") == OURS
        assert not (blocks_tree / CHECKLIST_REL).exists()

    def test_a_block_that_is_not_written_is_not_refused_for_privacy(
        self, make_manifest, blocks_tree
    ):
        dom = host_default(make_manifest, blocks_tree, privacy={"extra": [r"always\): Never skip"]})
        assert only_block(dom.render()).status == "absent"


class TestShow:
    """`invariant guidelines show`, the place the truncation line points at."""

    def test_it_is_the_checklist_text_without_its_marker(self, make_manifest, tree):
        shown = domain(make_manifest, tree).show()
        assert shown.ruleset == guidelines.digest_of(RULESET.encode("utf-8"))
        assert shown.text == guidelines.split_managed(expected(), "markdown")[1].lstrip("\n")
        assert not (tree / CHECKLIST_REL).exists()

    def test_it_is_markdown_whatever_docs_format_says(self, make_manifest, tree):
        shown = domain(make_manifest, tree, docs={"format": "sphinx"}).show()
        assert shown.text.startswith("# Coding guidelines")

    def test_no_ruleset_shows_nothing(self, make_manifest, tmp_path):
        assert domain(make_manifest, tmp_path, False).show().text is None

    def test_a_ruleset_that_does_not_load_is_refused(self, make_manifest, tmp_path):
        with pytest.raises(RenderRefused, match="does not exist"):
            domain(make_manifest, tmp_path).show()


class TestTheBlockCommands:
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
        write(root, RULESET_REL, WITH_ALWAYS)
        write(root, CLAUDE, OURS)

    def test_the_claude_code_host_defaults_to_claude_md(self, project, capsys):
        self.configure(project)
        assert run("guidelines", "render", "--insert", INDEX, "--json") == 0
        doc = payload(capsys)
        assert doc["blocks"] == [
            {"path": CLAUDE, "block": INDEX, "status": "new", "written": True}
        ]
        assert "invariant:begin guidelines/index" in (project / CLAUDE).read_text(encoding="utf-8")

    def test_another_host_kind_has_no_default(self, project, capsys):
        self.configure(project, host={"kind": "other"})
        assert run("guidelines", "render", "--insert", INDEX, "--json") == 0
        assert payload(capsys)["blocks"] == []

    @pytest.mark.parametrize(
        ("prepare", "args", "needle"),
        [
            ("none", (), "no guidelines/index block in CLAUDE.md; render --insert guidelines/index appends one"),
            ("insert", ("--insert", INDEX), "wrote the guidelines/index block into CLAUDE.md"),
            ("current", (), "wrote the guidelines/index block in CLAUDE.md: it is current"),
            ("stale", (), "from an older ruleset"),
            ("edited", (), "skipped the guidelines/index block in CLAUDE.md: it was edited by hand"),
            ("edited", ("--overwrite-local",), "its hand edits were replaced"),
            ("insert-dry", ("--insert", INDEX, "--dry-run"), "would write the guidelines/index block into CLAUDE.md"),
        ],
    )
    def test_each_block_status_reads_as_what_happened(self, project, capsys, prepare, args, needle):
        self.configure(project)
        if prepare in ("current", "stale", "edited"):
            run("guidelines", "render", "--insert", INDEX)
        if prepare == "stale":
            write(project, RULESET_REL, WITH_ALWAYS.replace("Touched files", "All touched files"))
        if prepare == "edited":
            path = project / CLAUDE
            path.write_text(path.read_text(encoding="utf-8").replace("(always)", "(ALWAYS)"), encoding="utf-8")
        capsys.readouterr()
        assert run("guidelines", "render", *args) == 0
        assert needle in capsys.readouterr().out

    def test_show_prints_the_checklist(self, project, capsys):
        self.configure(project)
        assert run("guidelines", "show") == 0
        out = capsys.readouterr().out
        assert "tests.never-skip" in out and "invariant-managed" not in out

    def test_show_json_carries_the_digest_and_text(self, project, capsys):
        self.configure(project)
        assert run("guidelines", "show", "--json") == 0
        doc = payload(capsys)
        assert doc["ruleset"] == guidelines.digest_of(WITH_ALWAYS.encode("utf-8"))
        assert doc["text"].startswith("# Coding guidelines")

    def test_show_with_no_ruleset(self, project, capsys):
        write_manifest(project, {"version": 1, "project": {"name": "demo"}})
        assert run("guidelines", "show") == 0
        assert "no guidelines ruleset configured" in capsys.readouterr().out
