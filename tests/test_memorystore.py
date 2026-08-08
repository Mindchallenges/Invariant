import pytest

from invariant.core.errors import UsageError
from invariant.domains.memorystore import MemoryStore


def memo(root, name, description="a fact", mem_type="project", body="Body text."):
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n"
        f"metadata:\n  type: {mem_type}\n---\n\n{body}\n",
        encoding="utf-8",
    )


@pytest.fixture
def store(tmp_path):
    return MemoryStore(root=tmp_path / "memory")


class TestLoading:
    def test_valid_memory(self, store):
        memo(store.root, "alpha")
        loaded = store.load(store.root / "alpha.md")
        assert loaded.name == "alpha" and loaded.type == "project"

    def test_missing_frontmatter(self, store):
        store.root.mkdir(parents=True)
        (store.root / "bad.md").write_text("just prose\n", encoding="utf-8")
        with pytest.raises(UsageError, match="no frontmatter"):
            store.load(store.root / "bad.md")

    def test_missing_fields_are_all_named(self, store):
        store.root.mkdir(parents=True)
        (store.root / "bad.md").write_text("---\nname: x\n---\n\nbody\n", encoding="utf-8")
        with pytest.raises(UsageError, match="description.*metadata.type"):
            store.load(store.root / "bad.md")

    def test_unknown_type(self, store):
        memo(store.root, "x", mem_type="rumour")
        with pytest.raises(UsageError, match="not one of"):
            store.load(store.root / "x.md")

    def test_index_is_not_treated_as_a_memory(self, store):
        memo(store.root, "alpha")
        store.index_path.write_text("# Memory Index\n", encoding="utf-8")
        assert [p.name for p in store.files()] == ["alpha.md"]


class TestLinkChecking:
    def test_resolved_link_is_clean(self, store):
        memo(store.root, "alpha", body="see [[beta]]")
        memo(store.root, "beta")
        store.regenerate_index()
        assert store.check().links == []

    def test_dead_link_is_reported(self, store):
        memo(store.root, "alpha", body="see [[ghost]]")
        store.regenerate_index()
        assert any("[[ghost]]" in p for p in store.check().links)


class TestIndexGeneration:
    def test_index_lists_every_memory(self, store):
        memo(store.root, "alpha", description="first")
        memo(store.root, "beta", description="second")
        rendered = store.regenerate_index()
        assert "alpha.md" in rendered and "beta.md" in rendered
        assert "first" in rendered and "second" in rendered

    def test_index_groups_by_type(self, store):
        memo(store.root, "alpha", mem_type="project")
        memo(store.root, "beta", mem_type="feedback")
        rendered = store.regenerate_index()
        assert "## feedback" in rendered and "## project" in rendered

    def test_regenerating_makes_the_check_clean(self, store):
        memo(store.root, "alpha")
        assert store.check().index  # no index yet
        store.regenerate_index()
        assert store.check().index == []

    def test_a_new_file_makes_the_index_stale(self, store):
        memo(store.root, "alpha")
        store.regenerate_index()
        memo(store.root, "beta")
        assert any("beta.md is not in the index" in p for p in store.check().index)

    def test_index_pointing_at_a_deleted_file_is_reported(self, store):
        memo(store.root, "alpha")
        memo(store.root, "beta")
        store.regenerate_index()
        (store.root / "beta.md").unlink()
        assert any("missing file beta.md" in p for p in store.check().index)

    def test_regeneration_is_deterministic(self, store):
        memo(store.root, "alpha")
        memo(store.root, "beta")
        assert store.regenerate_index() == store.regenerate_index()


class TestDuplicates:
    def test_near_identical_descriptions_are_proposed(self, store):
        memo(store.root, "one", description="never push twice in a single session")
        memo(store.root, "two", description="never push twice in one single session")
        store.regenerate_index()
        assert store.check().duplicates

    def test_different_types_are_not_compared(self, store):
        memo(store.root, "one", description="identical text here", mem_type="project")
        memo(store.root, "two", description="identical text here", mem_type="feedback")
        store.regenerate_index()
        assert store.check().duplicates == []

    def test_unrelated_descriptions_are_not_flagged(self, store):
        memo(store.root, "one", description="the push barrier is one per session")
        memo(store.root, "two", description="snapshot tests skip web views")
        store.regenerate_index()
        assert store.check().duplicates == []


class TestWriting:
    def test_write_slugifies(self, store):
        path = store.write("Some Fact Here", "d", "project", "body")
        assert path.name == "some-fact-here.md"
        assert store.load(path).name == "some-fact-here"

    def test_write_rejects_an_unknown_type(self, store):
        with pytest.raises(UsageError, match="not one of"):
            store.write("x", "d", "gossip", "body")

    def test_write_rejects_an_empty_slug(self, store):
        with pytest.raises(UsageError, match="at least one alphanumeric"):
            store.write("!!!", "d", "project", "body")

    def test_written_memory_round_trips(self, store):
        store.write("alpha", "a description", "feedback", "The body.")
        store.regenerate_index()
        report = store.check()
        assert report.schema == [] and report.index == []


class TestPrivacyIntegration:
    def test_a_leak_in_a_memory_is_reported(self, store, make_manifest, tmp_path):
        memo(store.root, "alpha", body="reach me at a.person@example.com")
        store.regenerate_index()
        from invariant.core.privacy import PrivacyScanner

        store.scanner = PrivacyScanner(patterns=["emails"])
        assert store.check().privacy

    def test_no_scanner_means_no_privacy_findings(self, store):
        memo(store.root, "alpha", body="reach me at a.person@example.com")
        store.regenerate_index()
        assert store.check().privacy == []


class TestReport:
    def test_clean_report(self, store):
        memo(store.root, "alpha")
        store.regenerate_index()
        assert store.check().clean

    def test_problems_aggregates(self, store):
        memo(store.root, "alpha", body="[[ghost]]")
        assert store.check().problems()

    def test_empty_directory(self, store):
        store.root.mkdir(parents=True)
        report = store.check()
        assert report.count == 0


def test_from_manifest_wires_paths(make_manifest, tmp_path):
    m = make_manifest(
        {"paths": {"memory": ".claude/memory", "memory_index": "MEMORY.md"}}
    )
    store = MemoryStore.from_manifest(m)
    assert store.root == (tmp_path / ".claude" / "memory").resolve()
    assert store.index_path.name == "MEMORY.md"
