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
        # The ruleset's pattern rule reads code, which needs a masking adapter.
        "project": {"name": "demo", "ecosystems": ["python"]},
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

    def test_an_allow_entry_scoped_to_the_checklist_reaches_it(self, make_manifest, tree):
        write(tree, RULESET_REL, RULESET.replace(
            "A view never reaches", "Ask TICKET-4242 first. A view never reaches"
        ))
        allow = [{"paths": [CHECKLIST_REL], "reason": "r"}]
        dom = domain(make_manifest, tree, privacy={"extra": ["TICKET-[0-9]+"], "allow": allow})
        dom.render(dry_run=True)

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

    def test_an_allow_entry_scoped_to_the_instruction_file_reaches_its_block(
        self, make_manifest, blocks_tree
    ):
        extra = [r"always\): Never skip"]
        scoped = [{"paths": [CLAUDE], "reason": "r"}]
        dom = host_default(make_manifest, blocks_tree, privacy={"extra": extra, "allow": scoped})
        dom.render(insert=(INDEX,), dry_run=True)
        elsewhere = [{"paths": ["docs/"], "reason": "r"}]
        dom = host_default(
            make_manifest, blocks_tree, privacy={"extra": extra, "allow": elsewhere}
        )
        assert f"{CLAUDE} ({INDEX} block" in str(self.refused(dom, insert=(INDEX,), dry_run=True))

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


# -- the ratchet baseline (A3 increment 5, D6, DN-1, DN-4) ---------------------------


BASELINE_REL = "docs/guidelines/baseline.txt"
BASELINE_RULESET = """\
version: 1
sections:
  - id: style
    title: Style
rules:
  - id: style.no-print
    section: style
    title: No print calls in the application
    statement: The application never prints.
    severity: blocker
    phases: [review]
    applies_to: ["app/**"]
    check: {kind: pattern, engine: regex, in: code, match: '\\bprint\\('}
  - id: style.no-todo
    section: style
    title: No inline TODO
    statement: A TODO is filed as an idea, never left in a comment.
    severity: warn
    phases: [review]
    applies_to: ["app/**"]
    check: {kind: pattern, engine: regex, in: comments, match: 'TODO'}
  - id: style.app-never-imports-tools
    section: style
    title: The application imports nothing from tools
    statement: No module of the application reaches into tools.
    severity: blocker
    phases: [review]
    applies_to: ["app/*.py"]
    check: {kind: boundary, engine: imports, forbid: ["tools/**"]}
  - id: style.paired
    section: style
    title: A module change touches its test
    statement: A change to app/X.py touches tests/test_X.py.
    severity: warn
    phases: [landing]
    applies_to: ["app/*.py"]
    check: {kind: cochange, form: pair, when: {change: any}, then: {each: "tests/test_{stem}.py"}}
"""
PRINT_ID = "style.no-print"
TODO_ID = "style.no-todo"


def stamp(line: str, rule: str = PRINT_ID) -> str:
    from invariant.core.findings import collapse, fingerprint

    return fingerprint(rule, collapse(line))


def baseline_line(rule: str, path: str, fp: str, count: int) -> str:
    return f"{rule}  {path}  {fp}  {count}\n"


def baseline_entries(root: Path) -> list[tuple[str, str, str, int]]:
    text = (root / BASELINE_REL).read_text(encoding="utf-8")
    return [(e.rule, e.path, e.fingerprint, e.count) for e in guidelines.parse_baseline(text)]


@pytest.fixture
def repo(tmp_path):
    """A committed project whose app carries pre-existing findings."""
    from test_domains import git_project

    root = tmp_path / "proj"
    root.mkdir()
    write(root, RULESET_REL, BASELINE_RULESET)
    write(root, "app/m.py", "print(x)\nprint(x)\n")
    write(root, "app/n.py", "y = 1  # TODO later\n")
    write(root, "tools/__init__.py", "")
    write(root, "tools/helper.py", "X = 1\n")
    git_project(root, {
        "version": 1, "project": {"name": "demo", "ecosystems": ["python"]},
        "guidelines": {"ruleset": RULESET_REL, "baseline": BASELINE_REL},
    })
    return root


def baseline_domain(root, *, baseline=BASELINE_REL, files=None, ruleset=RULESET_REL, base="",
                    **extra):
    from invariant.core.manifest import Manifest

    cfg = {"ruleset": ruleset}
    if baseline is not None:
        cfg["baseline"] = baseline
    write_manifest(root, {
        "version": 1, "project": {"name": "demo", "ecosystems": ["python"]},
        "guidelines": cfg, **extra,
    })
    session = None if files is None else {"files": list(files), "base": base}
    return GuidelinesDomain(RuleContext(manifest=Manifest.locate(root), cwd=root, session=session))


class TestBaselineRecord:
    def test_it_records_every_tracked_finding_and_only_tracked_files(self, repo):
        write(repo, "app/untracked.py", "print(z)\n")
        result = baseline_domain(repo).baseline(mode="record")
        assert result.status == "new" and result.written
        assert result.relpath == BASELINE_REL
        assert result.entries == 2
        assert baseline_entries(repo) == [
            (PRINT_ID, "app/m.py", stamp("print(x)"), 2),
            (TODO_ID, "app/n.py", stamp("y = 1  # TODO later", TODO_ID), 1),
        ]

    def test_a_second_record_is_current(self, repo):
        baseline_domain(repo).baseline(mode="record")
        result = baseline_domain(repo).baseline(mode="record")
        assert result.status == "current"

    def test_rule_replaces_only_the_named_rules_entries(self, repo):
        write(repo, BASELINE_REL,
              baseline_line(PRINT_ID, "app/m.py", "abcdefabcdef", 9)
              + baseline_line(TODO_ID, "app/kept.py", "0123456789ab", 4))
        result = baseline_domain(repo).baseline(mode="record", rules=(PRINT_ID,))
        assert result.status == "changed"
        assert baseline_entries(repo) == [
            (TODO_ID, "app/kept.py", "0123456789ab", 4),
            (PRINT_ID, "app/m.py", stamp("print(x)"), 2),
        ]

    def test_rule_drops_the_entries_of_a_rule_that_is_gone_and_names_them(self, repo):
        write(repo, BASELINE_REL, baseline_line("style.gone", "app/m.py", "abcdefabcdef", 1))
        result = baseline_domain(repo).baseline(mode="record", rules=(PRINT_ID,))
        (change,) = result.changes
        assert change.rule == "style.gone" and change.after == 0
        assert "gone" in change.reason

    def test_a_full_record_replaces_a_malformed_file(self, repo):
        write(repo, BASELINE_REL, "not a baseline\n")
        assert baseline_domain(repo).baseline(mode="record").status == "changed"
        assert len(baseline_entries(repo)) == 2

    def test_rule_refuses_a_malformed_file(self, repo):
        write(repo, BASELINE_REL, "not a baseline\n")
        with pytest.raises(RenderRefused, match="fewer than four fields"):
            baseline_domain(repo).baseline(mode="record", rules=(PRINT_ID,))

    def test_record_creates_the_configured_file(self, repo):
        """DN-1: --record is how a configured baseline comes to exist."""
        assert not (repo / BASELINE_REL).exists()
        baseline_domain(repo).baseline(mode="record")
        assert (repo / BASELINE_REL).is_file()

    def test_dry_run_writes_nothing(self, repo):
        result = baseline_domain(repo).baseline(mode="record", dry_run=True)
        assert result.status == "new" and not result.written
        assert not (repo / BASELINE_REL).exists()

    def test_an_unanswerable_file_refuses_the_record(self, repo):
        write(repo, "app/broken.py", "def (:\n")
        import subprocess

        subprocess.run(["git", "add", "app/broken.py"], cwd=repo, check=True)
        with pytest.raises(RenderRefused, match="app/broken.py") as caught:
            baseline_domain(repo).baseline(mode="record")
        assert "unanswerable" in str(caught.value)
        assert not (repo / BASELINE_REL).exists()

    @pytest.mark.parametrize(
        ("rule", "needle"),
        [
            ("style.nothing", "no guideline"),
            ("style.paired", "DN-4"),
        ],
    )
    def test_a_rule_that_cannot_be_baselined_is_refused(self, repo, rule, needle):
        with pytest.raises(RenderRefused, match=needle):
            baseline_domain(repo).baseline(mode="record", rules=(rule,))


class TestBaselinePrune:
    @pytest.fixture
    def recorded(self, repo):
        write(repo, "app/other.py", "print(o)\n")
        write(repo, BASELINE_REL,
              baseline_line(PRINT_ID, "app/m.py", stamp("print(x)"), 3)
              + baseline_line(TODO_ID, "app/n.py", stamp("y = 1  # TODO later", TODO_ID), 1)
              + baseline_line(PRINT_ID, "app/other.py", stamp("print(o)"), 1)
              + baseline_line(PRINT_ID, "app/deleted.py", stamp("print(d)"), 1)
              + baseline_line("style.gone", "app/m.py", "abcdefabcdef", 1))
        return repo

    def test_it_lowers_drops_and_never_adds(self, recorded):
        write(recorded, "app/m.py", "print(x)\nprint(new)\n")
        write(recorded, "app/n.py", "y = 1\n")
        result = baseline_domain(recorded, files=["app/m.py", "app/n.py"]).baseline(mode="prune")
        assert result.written and result.status == "changed"
        assert baseline_entries(recorded) == [
            (PRINT_ID, "app/m.py", stamp("print(x)"), 1),
            (PRINT_ID, "app/other.py", stamp("print(o)"), 1),
        ]
        reasons = {(c.rule, c.path): (c.before, c.after, c.reason) for c in result.changes}
        assert reasons[(PRINT_ID, "app/m.py")][:2] == (3, 1)
        assert reasons[(TODO_ID, "app/n.py")][:2] == (1, 0)
        assert "deleted" in reasons[(PRINT_ID, "app/deleted.py")][2]
        assert "gone" in reasons[("style.gone", "app/m.py")][2]

    def test_an_untouched_file_keeps_its_entries(self, recorded):
        result = baseline_domain(recorded, files=["tools/helper.py"]).baseline(
            mode="prune", dry_run=True
        )
        assert (PRINT_ID, "app/other.py") not in {(c.rule, c.path) for c in result.changes}
        assert (PRINT_ID, "app/m.py") not in {(c.rule, c.path) for c in result.changes}
        assert not result.written

    def test_prune_refuses_a_missing_file(self, repo):
        with pytest.raises(RenderRefused, match="does not exist"):
            baseline_domain(repo, files=[]).baseline(mode="prune")

    def test_prune_refuses_a_malformed_file(self, repo):
        write(repo, BASELINE_REL, "not a baseline\n")
        with pytest.raises(RenderRefused, match="fewer than four fields"):
            baseline_domain(repo, files=[]).baseline(mode="prune")

    def test_prune_refuses_an_unanswerable_touched_file_with_entries(self, recorded):
        write(recorded, "app/m.py", "def (:\n")
        with pytest.raises(RenderRefused, match="unanswerable"):
            baseline_domain(recorded, files=["app/m.py"]).baseline(mode="prune")

    def test_prune_refuses_a_changed_set_that_cannot_be_established(self, recorded):
        with pytest.raises(RenderRefused, match="changed set"):
            baseline_domain(recorded).baseline(mode="prune")


class TestBaselinePruneFollowsRenames:
    """DN-7: an entry follows a rename git detects from the merge base, and
    --prune rewrites its path."""

    @pytest.fixture
    def moved(self, repo):
        import subprocess

        write(repo, BASELINE_REL, baseline_line(PRINT_ID, "app/m.py", stamp("print(x)"), 2))
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
        subprocess.run(["git", "checkout", "-q", "-b", "work"], cwd=repo, check=True)
        subprocess.run(["git", "mv", "app/m.py", "app/k.py"], cwd=repo, check=True)
        return repo

    def test_prune_moves_the_entry_to_the_new_path(self, moved):
        result = baseline_domain(moved, files=[], base="master").baseline(mode="prune")
        assert baseline_entries(moved) == [(PRINT_ID, "app/k.py", stamp("print(x)"), 2)]
        (change,) = result.changes
        assert (change.path, change.moved_to) == ("app/m.py", "app/k.py")
        assert (change.before, change.after) == (2, 2)
        assert "renamed" in change.reason
        assert change.describe() == (
            f"moved {PRINT_ID} app/m.py {stamp('print(x)')} to app/k.py: {change.reason}"
        )

    def test_a_moved_entry_is_lowered_to_what_the_new_path_holds(self, moved):
        write(moved, "app/k.py", "print(x)\n")
        result = baseline_domain(moved, files=[], base="master").baseline(mode="prune")
        assert baseline_entries(moved) == [(PRINT_ID, "app/k.py", stamp("print(x)"), 1)]
        assert [(c.path, c.before, c.after) for c in result.changes] == [
            ("app/m.py", 2, 2), ("app/k.py", 2, 1),
        ]

    def test_a_moved_entry_merges_with_one_already_at_the_new_path(self, moved):
        write(moved, BASELINE_REL,
              baseline_line(PRINT_ID, "app/m.py", stamp("print(x)"), 1)
              + baseline_line(PRINT_ID, "app/k.py", stamp("print(x)"), 1))
        baseline_domain(moved, files=[], base="master").baseline(mode="prune")
        assert baseline_entries(moved) == [(PRINT_ID, "app/k.py", stamp("print(x)"), 2)]

    def test_a_move_out_of_the_rule_s_scope_drops_the_entry(self, moved):
        import subprocess

        subprocess.run(["git", "mv", "app/k.py", "tools/k.py"], cwd=moved, check=True)
        result = baseline_domain(moved, files=[], base="master").baseline(mode="prune")
        assert baseline_entries(moved) == []
        assert any("no longer applies" in c.reason for c in result.changes)

    def test_prune_refuses_when_git_cannot_say_whether_a_file_moved(self, moved, monkeypatch):
        from invariant.core import git

        monkeypatch.setattr(git, "renamed_files", lambda cwd, base: None)
        with pytest.raises(RenderRefused, match="renamed"):
            baseline_domain(moved, files=[], base="master").baseline(mode="prune")

    def test_the_command_names_the_move(self, moved, monkeypatch, capsys):
        monkeypatch.chdir(moved)
        assert run("session", "start", "move", "--base", "master") == 0
        capsys.readouterr()
        assert run("guidelines", "baseline", "--prune", "--json") == 0
        (change,) = payload(capsys)["changes"]
        assert change["path"] == "app/m.py" and change["moved_to"] == "app/k.py"


class TestBaselineRefusals:
    """The generated-file write class (ruling 13): each refusal writes nothing."""

    def test_no_baseline_configured(self, repo):
        """DN-1: --record refuses until the key names a path."""
        with pytest.raises(RenderRefused, match="guidelines.baseline") as caught:
            baseline_domain(repo, baseline=None).baseline(mode="record")
        assert "set guidelines.baseline" in caught.value.fix

    def test_no_ruleset_records_nothing(self, tmp_path):
        from test_domains import git_project

        git_project(tmp_path / "p")
        dom = baseline_domain(tmp_path / "p", baseline=None, ruleset=None)
        assert dom.baseline(mode="record").status == "no-ruleset"

    @pytest.mark.parametrize(
        ("target", "needle"),
        [
            (".claude/baseline.txt", "exempt from session isolation"),
            ("docs/backlog/baseline.txt", "exempt from session isolation"),
            ("../baseline.txt", "names nothing inside the project"),
            (RULESET_REL, "is the guidelines ruleset"),
            (CHECKLIST_REL, "is the guidelines checklist"),
            ("CLAUDE.md", "is a guidelines instruction file"),
        ],
    )
    def test_a_target_it_may_not_write(self, repo, target, needle):
        dom = baseline_domain(repo, baseline=target, items={"path": "docs/backlog"})
        dom = GuidelinesDomain(RuleContext(
            manifest=dom.manifest, cwd=repo, host_instruction_files=("CLAUDE.md",),
        ))
        before = (repo / RULESET_REL).read_text(encoding="utf-8")
        with pytest.raises(RenderRefused, match=needle):
            dom.baseline(mode="record", dry_run=True)
        assert (repo / RULESET_REL).read_text(encoding="utf-8") == before
        assert not (repo / ".claude/baseline.txt").exists()

    def test_a_privacy_finding_in_the_text(self, repo):
        import subprocess

        write(repo, "app/TICKET-4242.py", "print(t)\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        dom = baseline_domain(repo, privacy={"enabled": False, "extra": ["TICKET-[0-9]+"]})
        with pytest.raises(RenderRefused, match="privacy") as caught:
            dom.baseline(mode="record")
        assert "TICKET-4242" not in str(caught.value)
        assert not (repo / BASELINE_REL).exists()

    def test_an_allow_entry_scoped_to_the_baseline_reaches_it(self, repo):
        import subprocess

        write(repo, "app/TICKET-4242.py", "print(t)\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        privacy = {"extra": ["TICKET-[0-9]+"], "allow": [{"paths": [BASELINE_REL], "reason": "r"}]}
        baseline_domain(repo, privacy=privacy).baseline(mode="record", dry_run=True)


class TestTheBaselineCommand:
    @pytest.fixture(autouse=True)
    def _inside(self, repo, monkeypatch):
        monkeypatch.chdir(repo)

    def test_record_writes_and_reports(self, repo, capsys):
        assert run("guidelines", "baseline", "--record", "--json") == 0
        doc = payload(capsys)
        assert doc["mode"] == "record"
        assert doc["status"] == "new" and doc["written"] is True and doc["dry_run"] is False
        assert doc["path"] == BASELINE_REL
        assert doc["entries"] == 2
        assert doc["ruleset"] == guidelines.digest_of(BASELINE_RULESET.encode("utf-8"))

    def test_record_text_names_the_file_and_count(self, repo, capsys):
        assert run("guidelines", "baseline", "--record", "--dry-run") == 0
        text = capsys.readouterr().out
        assert f"would write {BASELINE_REL}" in text and "2 entries" in text
        assert not (repo / BASELINE_REL).exists()

    def test_record_with_rules(self, repo, capsys):
        assert run("guidelines", "baseline", "--record", "--rule", PRINT_ID, "--json") == 0
        assert payload(capsys)["entries"] == 1

    def test_prune_reports_each_change(self, repo, capsys):
        write(repo, BASELINE_REL, baseline_line(PRINT_ID, "app/deleted.py", "abcdefabcdef", 1))
        assert run("session", "start", "prune") == 0
        capsys.readouterr()
        assert run("guidelines", "baseline", "--prune", "--json") == 0
        doc = payload(capsys)
        assert doc["mode"] == "prune" and doc["entries"] == 0
        (change,) = doc["changes"]
        assert change["path"] == "app/deleted.py" and change["after"] == 0
        assert "deleted" in change["reason"]

    def test_rule_without_record_is_refused(self, repo, capsys):
        assert run("guidelines", "baseline", "--prune", "--rule", PRINT_ID) == 1
        assert "--rule" in capsys.readouterr().err

    def test_a_refusal_exits_one_and_names_the_fix(self, repo, capsys):
        write_manifest(repo, {
            "version": 1, "project": {"name": "demo", "ecosystems": ["python"]},
            "guidelines": {"ruleset": RULESET_REL},
        })
        assert run("guidelines", "baseline", "--record") == 1
        assert "set guidelines.baseline" in capsys.readouterr().err

    def test_no_ruleset_is_reported_and_succeeds(self, repo, capsys):
        write_manifest(repo, {"version": 1, "project": {"name": "demo"}})
        assert run("guidelines", "baseline", "--record") == 0
        assert "no guidelines ruleset configured" in capsys.readouterr().out


class TestBaselineEdges:
    """The branches the main cases do not reach, each still a refusal or a
    drop that names itself."""

    def test_prune_drops_a_rule_that_is_no_longer_baselined_and_one_that_no_longer_applies(
        self, repo
    ):
        write(repo, BASELINE_REL,
              baseline_line("style.paired", "app/m.py", "abcdefabcdef", 1)
              + baseline_line(PRINT_ID, "tools/helper.py", "0123456789ab", 1))
        result = baseline_domain(repo, files=["app/n.py"]).baseline(mode="prune")
        reasons = {(c.rule, c.path): c.reason for c in result.changes}
        assert "now a cochange rule" in reasons[("style.paired", "app/m.py")]
        assert "no longer applies" in reasons[(PRINT_ID, "tools/helper.py")]
        assert result.entries == 0

    def test_prune_never_raises_a_count(self, repo):
        write(repo, BASELINE_REL, baseline_line(PRINT_ID, "app/m.py", stamp("print(x)"), 1))
        result = baseline_domain(repo, files=["app/m.py"]).baseline(mode="prune")
        assert result.changes == ()
        assert baseline_entries(repo) == [(PRINT_ID, "app/m.py", stamp("print(x)"), 1)]

    def test_a_baseline_that_is_not_text_is_refused(self, repo):
        (repo / BASELINE_REL).parent.mkdir(parents=True, exist_ok=True)
        (repo / BASELINE_REL).write_bytes(b"\xff\xfe")
        with pytest.raises(RenderRefused, match="could not be read"):
            baseline_domain(repo, files=["app/m.py"]).baseline(mode="prune")

    def test_git_that_cannot_list_tracked_files_refuses_the_record(self, repo, monkeypatch):
        from invariant.core import git

        monkeypatch.setattr(git, "tracked_files", lambda cwd: None)
        with pytest.raises(RenderRefused, match="tracked files"):
            baseline_domain(repo).baseline(mode="record")

    def test_an_unknown_mode_is_a_programming_error(self, repo):
        with pytest.raises(ValueError, match="mode"):
            baseline_domain(repo).baseline(mode="grow")

    def test_a_configured_checklist_is_refused_as_a_target(self, repo):
        dom = baseline_domain(repo, baseline="docs/rules.md")
        dom.ctx.manifest.data["guidelines"]["checklist"] = "docs/rules.md"
        with pytest.raises(RenderRefused, match="is the guidelines checklist"):
            dom.baseline(mode="record", dry_run=True)

    def test_a_docs_format_with_no_renderer_still_records(self, repo):
        result = baseline_domain(repo, docs={"format": "asciidoc"}).baseline(
            mode="record", dry_run=True
        )
        assert result.entries == 2

    def test_an_instruction_file_outside_the_project_is_not_a_target_clash(self, repo):
        dom = baseline_domain(repo)
        dom.ctx.manifest.data["guidelines"]["instruction_files"] = ["../elsewhere.md"]
        assert dom.baseline(mode="record", dry_run=True).entries == 2

    def test_a_registry_the_ruleset_names_is_refused_as_a_target(self, repo):
        registry = "docs/guidelines/dup.yaml"
        write(repo, registry, "version: 1\nentries: []\n")
        write(repo, RULESET_REL, BASELINE_RULESET + f"""\
  - id: style.dup
    section: style
    title: Not left in an open duplication entry
    statement: A touched file is not left in an open duplication entry.
    severity: warn
    phases: [review]
    applies_to: ["app/**"]
    check: {{kind: cochange, form: registry, registry: {registry}, outcomes: {{contained: warn}}}}
""")
        with pytest.raises(RenderRefused, match="a registry the ruleset names"):
            baseline_domain(repo, baseline=registry).baseline(mode="record", dry_run=True)

    def test_a_path_the_format_cannot_hold_refuses_the_record(self, repo):
        import subprocess

        write(repo, RULESET_REL, BASELINE_RULESET + """\
  - id: style.no-secret
    section: style
    title: No secrets in the application
    statement: Nothing under app says SECRET.
    severity: warn
    phases: [review]
    applies_to: ["app/**"]
    check: {kind: pattern, engine: regex, in: any, match: 'SECRET'}
""")
        write(repo, "app/two\nlines.txt", "SECRET\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        with pytest.raises(RenderRefused, match="cannot be written"):
            baseline_domain(repo).baseline(mode="record", rules=("style.no-secret",))
        assert not (repo / BASELINE_REL).exists()


# -- guidelines verdict and verdicts submit, through main (A4 increment 1) ----------


def _review(root: Path) -> tuple[str, object]:
    """Move the running pipeline to its review state, as an advance would."""
    from invariant.core.manifest import Manifest
    from invariant.domains.pipeline import PipelineDomain
    from invariant.domains.session import SessionDomain

    manifest = Manifest.locate(root)
    session = SessionDomain(manifest).current(root)
    pipelines = PipelineDomain(manifest)
    record = pipelines.current(session.id)
    record.state = "reviewing"
    pipelines._save(session.id, record)
    return session.id, pipelines


def _stored(root: Path) -> list:
    from invariant.core.manifest import Manifest
    from invariant.domains.pipeline import PipelineDomain
    from invariant.domains.session import SessionDomain

    manifest = Manifest.locate(root)
    session = SessionDomain(manifest).current(root)
    return PipelineDomain(manifest).current(session.id).evidence.get("verdicts", [])


class TestVerdictCommands:
    @pytest.fixture
    def project(self, tmp_path, monkeypatch, capsys):
        from test_domains import git_project
        from test_guidelines_verdicts import PYTHON, sh
        from test_guidelines_verdicts import RULESET as VERDICTS

        root = tmp_path / "proj"
        root.mkdir()
        write(root, RULESET_REL, VERDICTS)
        write(root, "app/m.py", "one\ntwo\nthree\n")
        git_project(root, {"version": 1, "project": PYTHON,
                           "guidelines": {"ruleset": RULESET_REL}})
        sh(root, "checkout", "-q", "-b", "work")
        monkeypatch.chdir(root)
        assert run("session", "start", "verdicts") == 0
        assert run("pipeline", "start", "--task", "t") == 0
        write(root, "app/m.py", "one\nTWO\nthree\n")
        capsys.readouterr()
        return root

    def test_a_cited_verdict_is_recorded(self, project, capsys):
        _review(project)
        code = run("guidelines", "verdict", "data.single-open", "--status", "pass",
                   "--cite", "app/m.py", "2", "2", "--note", "closes first")
        assert code == 0
        text = capsys.readouterr().out
        assert "recorded 1 verdict" in text and "data.single-open pass" in text
        (stored,) = _stored(project)
        assert stored["citations"][0]["lines"] == [2, 2]
        assert stored["surface"] == "cli" and stored["state"] == "reviewing"
        assert stored["note"] == "closes first"

    def test_json_carries_the_records(self, project, capsys):
        _review(project)
        assert run("guidelines", "verdict", "data.single-open", "--status", "pass",
                   "--cite", "app/m.py", "2", "2", "--json") == 0
        body = payload(capsys)
        assert [r["rule"] for r in body["recorded"]] == ["data.single-open"]
        assert body["state"] == "reviewing" and len(body["ruleset"]) == 12

    def test_a_vague_verdict_is_refused_and_nothing_is_recorded(self, project, capsys):
        _review(project)
        code = run("guidelines", "verdict", "data.single-open", "--status", "pass",
                   "--cite", "app/m.py", "1", "1")
        assert code == 1
        err = capsys.readouterr().err
        assert "P3" in err and "nothing was recorded" in err
        assert _stored(project) == []

    def test_json_names_each_refused_predicate_and_records_nothing(self, project, capsys):
        """A refusal under --json is a document (A4 increment 7): the entry,
        the rule and the predicate of each problem, so a host, a script or
        the conformance harness reads which predicate failed without the
        prose. The exit is still 1, and nothing is recorded."""
        import json

        _review(project)
        path = project / "verdicts.json"
        path.write_text(json.dumps([
            {"rule": "data.single-open", "status": "pass",
             "citations": [{"path": "app/m.py", "lines": [2, 2]}]},
            {"rule": "data.tested", "status": "pass"},
            {"rule": "data.single-opn", "status": "pass", "colour": 1},
        ]), encoding="utf-8")
        assert run("guidelines", "verdicts", "submit", str(path), "--json") == 1
        body = payload(capsys)
        assert body["recorded"] == []
        named = [(r["entry"], r["rule"], r["predicate"]) for r in body["refused"]]
        assert (2, "data.tested", "P2") in named
        assert (3, None, "shape") in named
        assert all(r["problem"].startswith(f"entry {r['entry']}") for r in body["refused"])
        assert _stored(project) == []

    def test_json_names_a_refusal_of_the_whole(self, project, capsys):
        _review(project)
        assert run("guidelines", "verdicts", "submit", str(project / "missing.json"),
                   "--json") == 1
        (only,) = payload(capsys)["refused"]
        assert (only["entry"], only["rule"], only["predicate"]) == (None, None, "submission")
        path = project / "verdicts.json"
        path.write_text("[{", encoding="utf-8")
        assert run("guidelines", "verdicts", "submit", str(path), "--json") == 1
        assert [r["predicate"] for r in payload(capsys)["refused"]] == ["submission"]
        path.write_text("{}", encoding="utf-8")
        assert run("guidelines", "verdicts", "submit", str(path), "--json") == 1
        assert [r["predicate"] for r in payload(capsys)["refused"]] == ["shape"]

    def test_json_names_the_precondition_with_no_pipeline(self, project, capsys):
        sid, pipelines = _review(project)
        raw = pipelines.store.read(pipelines.relpath(sid), {})
        raw["current"] = None
        pipelines.store.write(pipelines.relpath(sid), raw)
        assert run("guidelines", "verdict", "data.single-open", "--status", "pass",
                   "--json") == 1
        assert [r["predicate"] for r in payload(capsys)["refused"]] == ["pipeline"]

    def test_without_json_a_refusal_prints_no_document(self, project, capsys):
        _review(project)
        assert run("guidelines", "verdict", "data.single-open", "--status", "pass") == 1
        assert capsys.readouterr().out == ""

    def test_a_line_that_is_not_a_number_is_refused(self, project, capsys):
        _review(project)
        assert run("guidelines", "verdict", "data.single-open", "--status", "pass",
                   "--cite", "app/m.py", "two", "2") == 1
        assert "two integers" in capsys.readouterr().err

    def test_an_na_names_its_claim(self, project, capsys):
        _review(project)
        assert run("guidelines", "verdict", "data.single-open", "--status", "na",
                   "--na-condition", "read-only") == 0
        (stored,) = _stored(project)
        assert stored["na_condition"] == "read-only" and stored["citations"] == []

    def test_a_test_no_run_named_is_refused(self, project, capsys):
        _review(project)
        assert run("guidelines", "verdict", "data.tested", "--status", "pass",
                   "--test", "tests/test_m.py::test_open") == 1
        assert "P4" in capsys.readouterr().err

    def test_outside_a_review_state_it_is_refused(self, project, capsys):
        assert run("guidelines", "verdict", "data.single-open", "--status", "pass",
                   "--cite", "app/m.py", "2", "2") == 1
        assert "P1" in capsys.readouterr().err
        assert _stored(project) == []

    def test_a_bulk_submission_is_recorded_whole(self, project, capsys):
        import json

        _review(project)
        path = project / "verdicts.json"
        path.write_text(json.dumps([
            {"rule": "data.single-open", "status": "pass",
             "citations": [{"path": "app/m.py", "lines": [1, 2]}]},
            {"rule": "data.single-open", "status": "na", "na_condition": "read-only"},
        ]), encoding="utf-8")
        assert run("guidelines", "verdicts", "submit", str(path)) == 1
        assert "twice" in capsys.readouterr().err
        path.write_text(json.dumps([
            {"rule": "data.single-open", "status": "pass",
             "citations": [{"path": "app/m.py", "lines": [1, 2]}]},
            {"rule": "data.either", "status": "fail",
             "citations": [{"path": "app/m.py", "lines": [3, 3]}]},
        ]), encoding="utf-8")
        assert run("guidelines", "verdicts", "submit", str(path)) == 0
        assert "recorded 2 verdicts" in capsys.readouterr().out
        assert [v["rule"] for v in _stored(project)] == ["data.single-open", "data.either"]

    def test_a_bulk_submission_with_an_invalid_entry_records_nothing(self, project, capsys):
        import json

        _review(project)
        path = project / "verdicts.json"
        path.write_text(json.dumps([
            {"rule": "data.single-open", "status": "pass",
             "citations": [{"path": "app/m.py", "lines": [2, 2]}]},
            {"rule": "data.tested", "status": "pass"},
        ]), encoding="utf-8")
        assert run("guidelines", "verdicts", "submit", str(path)) == 1
        err = capsys.readouterr().err
        assert "entry 2 (data.tested): P2" in err
        assert _stored(project) == []

    def test_a_file_that_is_not_json_is_refused(self, project, capsys):
        _review(project)
        path = project / "verdicts.json"
        path.write_text("[{", encoding="utf-8")
        assert run("guidelines", "verdicts", "submit", str(path)) == 1
        assert "is not JSON" in capsys.readouterr().err

    def test_a_file_that_cannot_be_read_is_refused(self, project, capsys):
        _review(project)
        assert run("guidelines", "verdicts", "submit", str(project / "missing.json")) == 1
        assert "could not be read" in capsys.readouterr().err

    def test_no_pipeline_is_refused(self, project, capsys):
        sid, pipelines = _review(project)
        raw = pipelines.store.read(pipelines.relpath(sid), {})
        raw["current"] = None
        pipelines.store.write(pipelines.relpath(sid), raw)
        assert run("guidelines", "verdict", "data.single-open", "--status", "pass",
                   "--cite", "app/m.py", "2", "2") == 1
        assert "no pipeline is running" in capsys.readouterr().err
