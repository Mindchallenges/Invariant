"""The shipped neutral agents and the Claude Code renderer (ADR-012 D8, A6.2).

The golden half pins the migration: a shipped source rendered with no overlay
is the text A6.1 installed, byte for byte, with only the differences the A6
rulings asked for. Those are declared here, one by one, so a body change cannot
ride in under the format change unseen.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from invariant.core.agents import parse
from invariant.hosts import assets, claude_code
from invariant.hosts.claude_code import (
    HOST_VOCABULARY,
    ClaudeCodeHost,
    render_agent,
)

FIXTURES = Path(__file__).parent / "fixtures" / "agents_a6_1"
LIBRARY = claude_code.DATA_DIR / "agents"
NAMES = sorted(p.stem for p in LIBRARY.glob("*.md"))

#: The two sentences in backlog-curator that named the host's question tool,
#: rewritten host-neutrally (A6 ruling 12). The meaning is kept: the parent
#: relays the interview questions to the user verbatim.
NEUTRALISED = {
    "backlog-curator": (
        (
            (
                "verbatim — using `AskUserQuestion` when the answers map cleanly to "
                "choices, otherwise as a plain numbered list."
            ),
            (
                "verbatim — as a multiple-choice question when the answers map "
                "cleanly to choices and your host offers one, otherwise as a plain "
                "numbered list."
            ),
        ),
        (
            (
                "  • Use AskUserQuestion when the answer maps cleanly to 2–4 choices\n"
                "    (severity, priority, item_type, in/out scope toggle)."
            ),
            (
                "  • Ask it as a multiple-choice question, where your host offers one,\n"
                "    when the answer maps cleanly to 2–4 choices (severity, priority,\n"
                "    item_type, in/out scope toggle)."
            ),
        ),
    ),
}

#: The sections each shipped agent offers an overlay, in order. A rename here
#: breaks every overlay that names the old id, so it is pinned.
ANCHORS = {
    "backlog-curator": (
        "intro",
        "phase-0",
        "phase-1",
        "phase-2",
        "phase-3",
        "phase-4",
        "phase-5",
        "phase-6",
        "phase-7",
        "phase-8",
        "operating-rules",
        "edge-cases",
    ),
    "bug-reproducer": (
        "intro",
        "outcomes",
        "phase-0",
        "phase-1",
        "phase-2",
        "phase-3",
        "phase-4",
        "phase-5",
        "phase-6",
        "phase-7",
        "phase-8",
        "report-format",
        "operating-constraints",
    ),
    "compliance-reviewer": (
        "intro",
        "phase-0",
        "phase-0b",
        "phase-1",
        "phase-2",
        "phase-3",
        "phase-4",
        "phase-5",
        "phase-6",
        "phase-7",
        "phase-8",
        "phase-8b",
        "phase-8e",
        "phase-8c",
        "phase-8d",
        "phase-9",
        "operating-constraints",
    ),
    "feature-architect": (
        "intro",
        "phase-1",
        "phase-1b",
        "phase-2",
        "phase-3",
        "phase-4",
        "phase-5",
        "phase-6",
        "phase-7",
        "phase-8",
        "phase-9",
        "operating-constraints",
    ),
}


def one_digest_copy(text):
    """A copy as A6.1 or A6.2 installed it: a one-digest marker holding the
    digest of the very text written. A6.3 writes three, and must still read
    these with no history (the A6 ruling 1)."""
    from invariant.hosts import assets

    front, body = assets.split_frontmatter(text)
    marker = f"{assets.MARKER_PREFIX} {assets.digest_of(text)}"
    return "\n".join([assets.FENCE, marker, *front, assets.FENCE, body])



def source_text(name):
    return (LIBRARY / f"{name}.md").read_text(encoding="utf-8")


def a6_1_text(name):
    return (FIXTURES / f"{name}.md").read_text(encoding="utf-8")


def expected_render(name):
    """What A6.1 installed, minus its model line, with the declared rewrites."""
    lines = a6_1_text(name).split("\n")
    close = lines.index("---", 1)
    front = [line for line in lines[1:close] if not line.startswith("model:")]
    text = "\n".join([lines[0], *front, *lines[close:]])
    for old, new in NEUTRALISED.get(name, ()):
        assert text.count(old) == 1, f"{name}: the rewrite no longer matches {old!r}"
        text = text.replace(old, new)
    return text


def rendered(name):
    return render_agent(parse(source_text(name), name))


def test_the_library_ships_the_four_agents():
    assert NAMES == sorted(ANCHORS)


class TestTheGolden:
    @pytest.mark.parametrize("name", NAMES)
    def test_a_source_renders_to_what_a6_1_installed(self, name):
        assert rendered(name) == expected_render(name)

    def test_only_the_reviewer_carried_a_model_line(self):
        carried = [n for n in NAMES if "\nmodel:" in a6_1_text(n).split("\n---\n")[0]]
        assert carried == ["compliance-reviewer"]

    @pytest.mark.parametrize("name", NAMES)
    def test_no_model_line_is_ever_written(self, name):
        front = assets.frontmatter(rendered(name))
        assert "model" not in front

    @pytest.mark.parametrize("name", NAMES)
    def test_no_anchor_survives_rendering(self, name):
        assert "invariant:section" not in rendered(name)


class TestTheSources:
    @pytest.mark.parametrize("name", NAMES)
    def test_each_source_declares_its_anchor_set(self, name):
        assert parse(source_text(name), name).section_ids() == ANCHORS[name]

    @pytest.mark.parametrize("name", NAMES)
    def test_every_anchor_sits_on_a_heading(self, name):
        """An anchor names the section that follows it, so it is placed just
        above that section's heading; the intro anchor opens the body."""
        lines = source_text(name).split("\n")
        for index, line in enumerate(lines):
            if line.startswith("<!-- invariant:section ") and not line.endswith(" intro -->"):
                assert lines[index + 1].startswith("##"), (name, index + 1)

    @pytest.mark.parametrize("name", NAMES)
    def test_each_source_declares_its_capabilities(self, name):
        assert parse(source_text(name), name).capabilities == ("read", "edit", "shell")

    @pytest.mark.parametrize("name", ("feature-architect", "bug-reproducer"))
    def test_the_model_hint_prose_is_carried_for_a7(self, name):
        assert "MODEL:" in parse(source_text(name), name).description

    @pytest.mark.parametrize("name", NAMES)
    def test_the_host_reader_still_reads_the_description_as_one_line(self, name):
        """The parent reads the description off the rendered file through the
        host's line reader, so it must stay a single line."""
        front = assets.frontmatter(rendered(name))
        assert front["description"] == parse(source_text(name), name).description


def vocabulary_hits(text, vocabulary):
    """Where a neutral source speaks a host's own words.

    A term with an inner capital (``AskUserQuestion``) is no English word, so it
    is a hit anywhere. A tool named by an English word (``Read``, ``Task``) is a
    hit only where it is used as a tool: in a code span, before "tool", or in a
    ``tools:`` list. "Read the docs FIRST" is prose, not the host's vocabulary.
    """
    hits = []
    for number, line in enumerate(text.split("\n"), 1):
        for term in vocabulary:
            if term[1:] != term[1:].lower():
                pattern = rf"\b{term}\b"
            else:
                pattern = rf"`{term}`|\b{term} tools?\b|^tools:.*\b{term}\b"
            if re.search(pattern, line):
                hits.append((number, term))
    return hits


class TestTheHostVocabularyLint:
    def test_the_adapter_supplies_its_tool_names_and_its_question_tool(self):
        assert {"Read", "Edit", "Write", "Bash", "Grep", "Glob", "Agent"} <= set(HOST_VOCABULARY)
        assert "AskUserQuestion" in HOST_VOCABULARY

    @pytest.mark.parametrize(
        "line",
        [
            "use AskUserQuestion here",
            "open it with the `Read` tool",
            "the Bash tool runs it",
            "tools: Bash, Read",
        ],
    )
    def test_the_lint_goes_red_on_host_vocabulary(self, line):
        assert vocabulary_hits(line, HOST_VOCABULARY)

    @pytest.mark.parametrize(
        "line", ["## Phase 1 — Read the docs FIRST", "1. **Task restated** — one paragraph"]
    )
    def test_the_lint_passes_prose(self, line):
        assert vocabulary_hits(line, HOST_VOCABULARY) == []

    @pytest.mark.parametrize("name", NAMES)
    def test_no_neutral_source_speaks_the_hosts_vocabulary(self, name):
        assert vocabulary_hits(source_text(name), HOST_VOCABULARY) == []


class TestTheRenderer:
    def agent(self, capabilities):
        return parse(
            "---\nname: a\ndescription: d\n"
            f"capabilities: [{', '.join(capabilities)}]\n---\nBody.\n"
        )

    @pytest.mark.parametrize(
        ("capabilities", "tools"),
        [
            (["read"], "Read, Grep, Glob"),
            (["edit"], "Edit, Write"),
            (["shell"], "Bash"),
            (["dispatch"], "Agent"),
            (["dispatch", "shell", "read", "edit"], "Bash, Read, Edit, Write, Grep, Glob, Agent"),
        ],
    )
    def test_capabilities_map_to_the_hosts_tools_in_one_order(self, capabilities, tools):
        text = render_agent(self.agent(capabilities))
        assert text == f"---\nname: a\ndescription: d\ntools: {tools}\n---\nBody.\n"


# -- the install plan renders through the renderer -------------------------


@pytest.fixture
def host(tmp_path):
    return ClaudeCodeHost(tmp_path)


def target(host, name):
    return host.claude_dir / "agents" / f"{name}.md"


class TestThePlanRenders:
    @pytest.mark.parametrize("name", NAMES)
    def test_an_install_writes_the_rendered_text_under_its_marker(self, host, name):
        host.install_assets(only="agent")
        installed = target(host, name).read_text(encoding="utf-8")
        assert assets.reconstruct_source(installed) == rendered(name)
        assert assets.marker_digest(installed) == assets.digest_of(rendered(name))

    def test_a_fresh_install_is_current(self, host):
        host.install_assets(only="agent")
        assert {s.state for s in host.plan_assets(only="agent")} == {assets.CURRENT}

    def test_a_broken_neutral_source_is_refused_naming_its_problems(
        self, host, tmp_path, monkeypatch
    ):
        data = tmp_path / "data"
        (data / "agents").mkdir(parents=True)
        (data / "agents" / "odd.md").write_text(
            "---\nname: odd\ndescription: d\nmodel: sonnet\n---\nB.\n", encoding="utf-8"
        )
        monkeypatch.setattr(claude_code, "DATA_DIR", data)
        with pytest.raises(Exception) as exc:
            host.install_assets(only="agent")
        assert "odd" in str(exc.value)
        assert "no capabilities" in str(exc.value)
        assert "never names a model" in str(exc.value)
        assert not target(host, "odd").exists()


class TestACopyFromBeforeA6_2:
    """The A6.1 classifier still tells an older release from an edit."""

    def install_a6_1(self, host, name, extra=""):
        old = a6_1_text(name)
        path = target(host, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(one_digest_copy(old) + extra, encoding="utf-8")

    def states(self, host):
        return {s.identity: s.state for s in host.plan_assets(only="agent")}

    def test_an_untouched_copy_reads_as_stale_where_the_text_changed(self, host):
        for name in NAMES:
            self.install_a6_1(host, name)
        assert self.states(host) == {
            "backlog-curator": assets.OLDER,
            "compliance-reviewer": assets.OLDER,
            "bug-reproducer": assets.CURRENT,
            "feature-architect": assets.CURRENT,
        }

    def test_an_untouched_copy_is_rewritten_unasked(self, host):
        for name in NAMES:
            self.install_a6_1(host, name)
        result = host.install_assets(only="agent")
        assert result.skipped == []
        assert self.states(host) == dict.fromkeys(NAMES, assets.CURRENT)

    @pytest.mark.parametrize("name", NAMES)
    def test_an_edited_copy_reads_as_edited(self, host, name):
        self.install_a6_1(host, name, extra="\nmine\n")
        assert self.states(host)[name] == assets.LOCAL
        result = host.install_assets(only="agent")
        assert (f"agent:{name}", assets.LOCAL) in result.skipped
        assert target(host, name).read_text(encoding="utf-8").endswith("\nmine\n")


# -- invariant agents show ---------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    from invariant.cli.main import main

    root = tmp_path / "proj"
    (root / "docs" / "architecture").mkdir(parents=True)
    monkeypatch.chdir(root)
    assert main(["init", "--name", "demo"]) == 0
    return root


def files_under(root):
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


class TestAgentsShow:
    def test_it_prints_the_rendered_text_and_writes_nothing(self, project, capsys):
        from invariant.cli.main import main

        before = files_under(project)
        capsys.readouterr()
        assert main(["agents", "show", "compliance-reviewer"]) == 0
        out = capsys.readouterr().out
        assert out == rendered("compliance-reviewer")
        assert files_under(project) == before

    def test_json_carries_the_text(self, project, capsys):
        from invariant.cli.main import main

        capsys.readouterr()
        assert main(["agents", "show", "feature-architect", "--json"]) == 0
        body = json.loads(capsys.readouterr().out)
        assert body == {"agent": "feature-architect", "text": rendered("feature-architect")}

    def test_sections_lists_the_anchor_ids(self, project, capsys):
        from invariant.cli.main import main

        capsys.readouterr()
        assert main(["agents", "show", "bug-reproducer", "--sections"]) == 0
        assert capsys.readouterr().out.split() == list(ANCHORS["bug-reproducer"])

        assert main(["agents", "show", "bug-reproducer", "--sections", "--json"]) == 0
        body = json.loads(capsys.readouterr().out)
        assert body == {"agent": "bug-reproducer", "sections": list(ANCHORS["bug-reproducer"])}

    def test_an_unknown_agent_is_refused_naming_the_shipped_ones(self, project, capsys):
        from invariant.cli.main import main

        capsys.readouterr()
        assert main(["agents", "show", "nobody"]) == 2
        err = capsys.readouterr().err
        assert "'nobody'" in err
        assert "feature-architect" in err

    def test_a_host_kind_with_no_adapter_is_refused(self, project, capsys):
        import yaml

        from invariant.cli.main import main

        path = project / ".claude" / "invariant.yaml"
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
        body["host"] = {"kind": "some-other-host"}
        path.write_text(yaml.safe_dump(body), encoding="utf-8")
        capsys.readouterr()
        assert main(["agents", "show", "feature-architect"]) == 2
        assert "'some-other-host'" in capsys.readouterr().err
