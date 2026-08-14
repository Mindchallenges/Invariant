"""Reading another program's undocumented on-disk format.

The contract this suite exists to hold, from ADR-002's consequences: *every
field access tolerates absence and an unparseable record is skipped, so a
format change degrades a report and never breaks a session.* Most of what
follows is therefore malformed input, which is the input this parser will
actually meet the day the format moves.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from invariant.core.stats import SampleSource, Usage, fold
from invariant.hosts.claude_code_telemetry import (
    ClaudeCodeTranscripts,
    read_transcript,
)


def assistant(model="claude-opus-5", usage=None, timestamp="2026-06-24T09:00:00Z", **extra):
    record = {
        "type": "assistant",
        "timestamp": timestamp,
        "message": {
            "model": model,
            "usage": usage
            if usage is not None
            else {"input_tokens": 10, "output_tokens": 20},
        },
    }
    record.update(extra)
    return record


def write_jsonl(path: Path, records) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            (r if isinstance(r, str) else json.dumps(r)) + "\n" for r in records
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def host_home(tmp_path, monkeypatch):
    home = tmp_path / "host"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    return home


def project_dir(home: Path, origin: Path) -> Path:
    return home / "projects" / str(origin.resolve()).replace("/", "-")


class TestProtocolConformance:
    def test_the_transcript_reader_is_a_sample_source(self, host_home):
        assert isinstance(ClaudeCodeTranscripts(), SampleSource)


class TestReadingOneTranscript:
    def test_a_well_formed_record_becomes_a_sample(self, tmp_path):
        path = write_jsonl(
            tmp_path / "hs-alpha.jsonl",
            [
                assistant(
                    usage={
                        "input_tokens": 1,
                        "output_tokens": 2,
                        "cache_read_input_tokens": 3,
                        "cache_creation": {
                            "ephemeral_5m_input_tokens": 4,
                            "ephemeral_1h_input_tokens": 5,
                        },
                    }
                )
            ],
        )
        [sample] = list(read_transcript(path))
        assert sample.model == "claude-opus-5"
        assert sample.usage == Usage(1, 2, 3, 4, 5)
        assert sample.host_session_id == "hs-alpha"

    def test_the_two_cache_write_tiers_stay_apart(self, tmp_path):
        path = write_jsonl(
            tmp_path / "hs-alpha.jsonl",
            [
                assistant(
                    usage={
                        "cache_creation": {
                            "ephemeral_5m_input_tokens": 100,
                            "ephemeral_1h_input_tokens": 900,
                        }
                    }
                )
            ],
        )
        [sample] = list(read_transcript(path))
        assert sample.usage.cache_write_5m == 100
        assert sample.usage.cache_write_1h == 900

    def test_an_older_flat_cache_field_lands_in_the_five_minute_tier(self, tmp_path):
        path = write_jsonl(
            tmp_path / "hs-alpha.jsonl",
            [assistant(usage={"cache_creation_input_tokens": 500})],
        )
        [sample] = list(read_transcript(path))
        assert sample.usage.cache_write_5m == 500
        assert sample.usage.cache_write_1h == 0

    def test_the_session_id_in_the_record_beats_the_file_name(self, tmp_path):
        path = write_jsonl(
            tmp_path / "hs-alpha.jsonl", [assistant(sessionId="hs-beta")]
        )
        [sample] = list(read_transcript(path))
        assert sample.host_session_id == "hs-beta"

    def test_tools_and_agent_launches_are_read_from_the_content_blocks(self, tmp_path):
        record = assistant()
        record["message"]["content"] = [
            {"type": "text", "text": "thinking"},
            {"type": "tool_use", "name": "Read", "input": {}},
            {
                "type": "tool_use",
                "name": "Agent",
                "input": {"subagent_type": "feature-architect", "model": "claude-opus-5"},
            },
        ]
        path = write_jsonl(tmp_path / "hs-alpha.jsonl", [record])
        [sample] = list(read_transcript(path))
        assert sample.tools == ("Read", "Agent")
        assert sample.agents == (("feature-architect", "claude-opus-5"),)

    def test_an_agent_launch_with_no_declared_model_is_still_recorded(self, tmp_path):
        record = assistant()
        record["message"]["content"] = [
            {"type": "tool_use", "name": "Agent", "input": {"subagent_type": "curator"}}
        ]
        path = write_jsonl(tmp_path / "hs-alpha.jsonl", [record])
        [sample] = list(read_transcript(path))
        assert sample.agents == (("curator", "inherited"),)


class TestMalformedInput:
    """Each case must be SKIPPED and must not raise.

    The unit under test is the whole file: one bad line may not cost the good
    ones. Every case therefore writes one good record alongside the bad one and
    asserts the good one survived.
    """

    good = assistant(usage={"input_tokens": 7})

    @pytest.mark.parametrize(
        "bad",
        [
            pytest.param("{not json at all", id="unparseable-line"),
            pytest.param("", id="blank-line"),
            pytest.param("[1, 2, 3]", id="json-that-is-not-an-object"),
            pytest.param('"a bare string"', id="json-scalar"),
            pytest.param({"type": "user"}, id="no-message"),
            pytest.param({"message": "not-a-mapping"}, id="message-is-a-string"),
            pytest.param({"message": {"model": "claude-opus-5"}}, id="no-usage"),
            pytest.param(
                {"message": {"model": "claude-opus-5", "usage": []}}, id="usage-is-a-list"
            ),
            pytest.param({"message": {"usage": {"input_tokens": 1}}}, id="no-model"),
            pytest.param(
                {"message": {"model": "", "usage": {"input_tokens": 1}}}, id="empty-model"
            ),
            pytest.param(
                {"message": {"model": "<synthetic>", "usage": {"input_tokens": 1}}},
                id="synthetic-model",
            ),
        ],
    )
    def test_a_bad_record_is_skipped_and_the_good_one_survives(self, tmp_path, bad):
        path = write_jsonl(tmp_path / "hs-alpha.jsonl", [bad, self.good])
        samples = list(read_transcript(path))
        assert [s.usage.input for s in samples] == [7]

    def test_a_record_with_an_unparseable_time_keeps_its_tokens(self, tmp_path):
        """Dropping it would under-count spend to keep the timing tidy."""
        path = write_jsonl(
            tmp_path / "hs-alpha.jsonl",
            [assistant(timestamp="not a time", usage={"input_tokens": 11})],
        )
        [sample] = list(read_transcript(path))
        assert sample.timestamp is None
        assert sample.usage.input == 11

    def test_a_record_with_no_time_at_all_keeps_its_tokens(self, tmp_path):
        record = assistant(usage={"input_tokens": 11})
        record.pop("timestamp")
        path = write_jsonl(tmp_path / "hs-alpha.jsonl", [record])
        [sample] = list(read_transcript(path))
        assert sample.timestamp is None and sample.usage.input == 11

    def test_junk_in_a_usage_field_reads_as_zero_rather_than_raising(self, tmp_path):
        path = write_jsonl(
            tmp_path / "hs-alpha.jsonl",
            [assistant(usage={"input_tokens": "lots", "output_tokens": None})],
        )
        [sample] = list(read_transcript(path))
        assert sample.usage == Usage()

    def test_a_cache_creation_block_of_the_wrong_shape_is_tolerated(self, tmp_path):
        path = write_jsonl(
            tmp_path / "hs-alpha.jsonl",
            [assistant(usage={"cache_creation": "surprise", "input_tokens": 3})],
        )
        [sample] = list(read_transcript(path))
        assert sample.usage.input == 3

    def test_content_blocks_of_the_wrong_shape_are_tolerated(self, tmp_path):
        record = assistant()
        record["message"]["content"] = ["a string", None, {"type": "tool_use"}, 42]
        path = write_jsonl(tmp_path / "hs-alpha.jsonl", [record])
        [sample] = list(read_transcript(path))
        assert sample.tools == ()

    def test_an_agent_input_of_the_wrong_shape_is_tolerated(self, tmp_path):
        record = assistant()
        record["message"]["content"] = [
            {"type": "tool_use", "name": "Agent", "input": "not a mapping"}
        ]
        path = write_jsonl(tmp_path / "hs-alpha.jsonl", [record])
        [sample] = list(read_transcript(path))
        assert sample.agents == (("unspecified", "inherited"),)

    def test_a_missing_file_yields_nothing_rather_than_raising(self, tmp_path):
        assert list(read_transcript(tmp_path / "absent.jsonl")) == []

    def test_a_directory_where_a_file_was_expected_yields_nothing(self, tmp_path):
        (tmp_path / "hs-alpha.jsonl").mkdir()
        assert list(read_transcript(tmp_path / "hs-alpha.jsonl")) == []

    def test_undecodable_bytes_do_not_stop_the_reader(self, tmp_path):
        path = tmp_path / "hs-alpha.jsonl"
        path.write_bytes(b"\xff\xfe not utf-8 at all\n" + json.dumps(good_record()).encode())
        assert [s.usage.input for s in read_transcript(path)] == [7]

    def test_a_wholly_empty_file_yields_nothing(self, tmp_path):
        path = write_jsonl(tmp_path / "hs-alpha.jsonl", [])
        assert list(read_transcript(path)) == []


def good_record():
    return assistant(usage={"input_tokens": 7})


class TestDiscovery:
    def test_sessions_are_found_under_the_configured_home(self, host_home, tmp_path):
        origin = tmp_path / "repo"
        write_jsonl(project_dir(host_home, origin) / "hs-alpha.jsonl", [assistant()])
        refs = ClaudeCodeTranscripts(origin=origin).sessions()
        assert [r.host_session_id for r in refs] == ["hs-alpha"]

    def test_a_worktree_of_the_project_is_attributed_to_the_project(
        self, host_home, tmp_path
    ):
        """The host names a project after its path, and a worktree is a different
        path — narrowing by prefix keeps its sessions with the project's."""
        origin = tmp_path / "repo"
        worktree = origin / ".claude" / "worktrees" / "repo-wt-1"
        write_jsonl(project_dir(host_home, origin) / "hs-alpha.jsonl", [assistant()])
        write_jsonl(project_dir(host_home, worktree) / "hs-beta.jsonl", [assistant()])
        refs = ClaudeCodeTranscripts(origin=origin).sessions()
        assert {r.host_session_id for r in refs} == {"hs-alpha", "hs-beta"}

    def test_another_project_is_excluded(self, host_home, tmp_path):
        origin = tmp_path / "repo"
        other = tmp_path / "elsewhere"
        write_jsonl(project_dir(host_home, origin) / "hs-alpha.jsonl", [assistant()])
        write_jsonl(project_dir(host_home, other) / "hs-other.jsonl", [assistant()])
        refs = ClaudeCodeTranscripts(origin=origin).sessions()
        assert [r.host_session_id for r in refs] == ["hs-alpha"]

    @pytest.mark.xfail(
        strict=True,
        reason="BUG_015 is live — startswith() has no boundary check; "
        "delete this marker with the fix",
    )
    def test_BUG_015_a_sibling_whose_name_extends_the_origins_is_excluded_but_the_worktree_stays_in(
        self, host_home, tmp_path
    ):
        """A sibling checkout whose directory name merely EXTENDS the origin's
        (no separator between them) must not be swept in, even though a real
        worktree — whose slug also extends the origin's, but after the path
        separator — legitimately must be. Pins both sides so a fix that
        satisfies one by breaking the other still fails this test."""
        origin = tmp_path / "app"
        worktree = origin / ".claude" / "worktrees" / "wt-1"
        sibling = tmp_path / "app-v2"
        write_jsonl(project_dir(host_home, origin) / "hs-origin.jsonl", [assistant()])
        write_jsonl(project_dir(host_home, worktree) / "hs-worktree.jsonl", [assistant()])
        write_jsonl(project_dir(host_home, sibling) / "hs-sibling.jsonl", [assistant()])

        selected = {p.stem for p in ClaudeCodeTranscripts(origin=origin).paths()}

        assert selected == {"hs-origin", "hs-worktree"}

    def test_with_no_origin_every_project_is_in_scope(self, host_home, tmp_path):
        write_jsonl(project_dir(host_home, tmp_path / "a") / "hs-a.jsonl", [assistant()])
        write_jsonl(project_dir(host_home, tmp_path / "b") / "hs-b.jsonl", [assistant()])
        refs = ClaudeCodeTranscripts().sessions()
        assert {r.host_session_id for r in refs} == {"hs-a", "hs-b"}

    def test_an_absent_host_home_is_an_empty_list_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nowhere"))
        assert ClaudeCodeTranscripts().sessions() == []

    def test_a_stray_file_in_the_projects_root_is_ignored(self, host_home, tmp_path):
        (host_home / "projects").mkdir(parents=True)
        (host_home / "projects" / "loose.txt").write_text("x", encoding="utf-8")
        assert ClaudeCodeTranscripts().sessions() == []

    def test_find_returns_none_for_an_unknown_session(self, host_home, tmp_path):
        write_jsonl(
            project_dir(host_home, tmp_path / "repo") / "hs-alpha.jsonl", [assistant()]
        )
        assert ClaudeCodeTranscripts().find("hs-nope") is None

    def test_find_returns_the_same_watermark_as_the_listing(self, host_home, tmp_path):
        write_jsonl(
            project_dir(host_home, tmp_path / "repo") / "hs-alpha.jsonl", [assistant()]
        )
        source = ClaudeCodeTranscripts()
        [listed] = source.sessions()
        assert source.find("hs-alpha") == listed


class TestWatermarks:
    def test_the_watermark_changes_when_the_transcript_does(self, host_home, tmp_path):
        path = project_dir(host_home, tmp_path / "repo") / "hs-alpha.jsonl"
        write_jsonl(path, [assistant()])
        source = ClaudeCodeTranscripts()
        before = source.find("hs-alpha").watermark
        write_jsonl(path, [assistant(), assistant()])
        assert source.find("hs-alpha").watermark != before

    def test_the_watermark_is_stable_for_unchanged_content(self, host_home, tmp_path):
        path = project_dir(host_home, tmp_path / "repo") / "hs-alpha.jsonl"
        write_jsonl(path, [assistant()])
        source = ClaudeCodeTranscripts()
        assert source.find("hs-alpha").watermark == source.find("hs-alpha").watermark

    def test_the_watermark_is_not_a_modification_time(self, host_home, tmp_path):
        """A stored mtime would be a stored clock time; a fingerprint is not."""
        path = project_dir(host_home, tmp_path / "repo") / "hs-alpha.jsonl"
        write_jsonl(path, [assistant()])
        source = ClaudeCodeTranscripts()
        before = source.find("hs-alpha").watermark
        import os

        os.utime(path, (0, 0))
        assert source.find("hs-alpha").watermark == before

    def test_an_unreadable_transcript_still_gets_a_watermark(self, host_home, tmp_path):
        directory = project_dir(host_home, tmp_path / "repo")
        directory.mkdir(parents=True)
        (directory / "hs-alpha.jsonl").mkdir()
        [ref] = ClaudeCodeTranscripts().sessions()
        assert ref.watermark


class TestEndToEnd:
    def test_samples_fold_into_one_record_per_session(self, host_home, tmp_path):
        origin = tmp_path / "repo"
        write_jsonl(
            project_dir(host_home, origin) / "hs-alpha.jsonl",
            [
                assistant(usage={"input_tokens": 1}, timestamp="2026-06-24T09:00:00Z"),
                assistant(usage={"input_tokens": 2}, timestamp="2026-06-24T09:01:00Z"),
            ],
        )
        source = ClaudeCodeTranscripts(origin=origin)
        samples = [s for ref in source.sessions() for s in source.samples(ref)]
        folded = fold(samples)
        assert folded["hs-alpha"].total_usage.input == 3
        assert folded["hs-alpha"].date == "2026-06-24"
        assert folded["hs-alpha"].wall_seconds == 60.0

    def test_samples_for_a_ref_the_source_no_longer_has_is_empty(self, host_home, tmp_path):
        from invariant.core.stats import SourceRef

        source = ClaudeCodeTranscripts()
        assert list(source.samples(SourceRef("hs-gone", "w"))) == []
