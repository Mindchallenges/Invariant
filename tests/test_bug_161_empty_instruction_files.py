"""Repro for BUG_161 — see the work-item entry.

``guidelines.instruction_files`` cannot express "no instruction files". Null
is the host default (CLAUDE.md for claude_code), so a project that exempts
CLAUDE.md from session isolation has ``guidelines render`` refuse (ADR-012
ruling 13), and an empty list, which should mean none, is rejected at load.

Each test fails while the defect is live. When the fix lands, delete the
expected-failure marker; the assertion must then pass unchanged.
"""

from __future__ import annotations

import pytest
from test_guidelines import WITH_ALWAYS
from test_guidelines_domain import CHECKLIST_REL, CLAUDE, OURS, RULESET_REL, write

from invariant.core.rules import RenderRefused, RuleContext, evaluate
from invariant.domains.guidelines import GuidelinesDomain

# A project that exempts its CLAUDE.md from session isolation, on a host whose
# default instruction file is CLAUDE.md.
EXEMPT_CLAUDE = {"exempt_paths": [CLAUDE]}


def manifest_body(guidelines_cfg):
    return {
        "version": 1,
        "project": {"name": "demo", "ecosystems": ["python"]},
        "guidelines": {"ruleset": RULESET_REL, **guidelines_cfg},
        "session": EXEMPT_CLAUDE,
    }


def context(make_manifest, root, guidelines_cfg):
    return RuleContext(
        manifest=make_manifest(manifest_body(guidelines_cfg)),
        cwd=root,
        host_instruction_files=(CLAUDE,),
    )


@pytest.fixture
def tree(tmp_path):
    write(tmp_path, RULESET_REL, WITH_ALWAYS)
    write(tmp_path, CLAUDE, OURS)
    return tmp_path


def test_BUG_161_an_empty_instruction_files_list_is_accepted(make_manifest):
    manifest = make_manifest({"version": 1, "guidelines": {"instruction_files": []}})
    assert manifest.get("guidelines.instruction_files") == []


def test_BUG_161_render_with_none_under_an_exempt_claude_md_writes_no_block(
    make_manifest, tree
):
    result = GuidelinesDomain(context(make_manifest, tree, {"instruction_files": []})).render()
    assert result.written and result.relpath == CHECKLIST_REL
    assert (tree / CHECKLIST_REL).is_file()
    assert result.blocks == ()
    assert (tree / CLAUDE).read_text(encoding="utf-8") == OURS


def test_BUG_161_generated_current_checks_only_the_ruleset_outputs(make_manifest, tree):
    GuidelinesDomain(context(make_manifest, tree, {"instruction_files": []})).render()
    (result,) = evaluate(
        ["guidelines.generated_current"], context(make_manifest, tree, {"instruction_files": []})
    )
    assert result.verdict.ok and not result.verdict.skipped, result.verdict.message
    assert CLAUDE not in (result.verdict.message + (result.verdict.detail or ""))


def test_BUG_161_the_exempt_default_refusal_names_the_empty_list(make_manifest, tree):
    with pytest.raises(RenderRefused) as refused:
        GuidelinesDomain(context(make_manifest, tree, {})).render()
    assert "exempt from session isolation" in refused.value.reason
    assert "instruction_files: []" in refused.value.fix
