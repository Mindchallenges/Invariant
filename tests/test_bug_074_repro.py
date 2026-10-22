"""Repro for BUG_074 — see the work-item entry (docs/backlog/BUG_074.md).

Each test fails while its half of the defect is live. When the fix lands,
delete that test's expected-failure wrapper; the assertion must then pass
unchanged.

Every test drives the reported surface, ``invariant item new``, against a
``sphinx_needs`` backlog laid out in two named sections, and asserts what the
caller reads back from the file. None asserts the suspected cause (how
``--field`` pairs are merged, or the BUG_006 insertion rule).

Mechanism (a), ``--field`` naming a key the backend itself writes:

* The literal symptom filed — a second ``:id:`` / ``:status:`` line in one
  directive — no longer fires: 983e0ab (BUG_065) holds ``id``/``status`` out
  of the written fields. What is still live is the other half of the report:
  the named value does not replace the minted one. It is dropped without a
  word, so the entry lands under an id (or status) the caller never asked for
  and the command exits 0.
* An explicit id equal to an open id is not refused.

Mechanism (b), no named-section placement: a new bug always lands after the
last bug in the file, whatever section it belongs in. The placement surface
is the one the entry's Expected names, ``--section "<title>"``; that surface
does not exist yet, so this test is the acceptance criterion for new surface
rather than a regression. Manifest.md ("The sphinx_needs round trip") still
documents the after-the-last-entry rule as the fallback with no placement
given.

Mechanism (c), the closed-store id scan, is implemented: ``items.closed_path``
is a manifest key, and id minting and the explicit-id check read it. Its tests
live in tests/test_bug_074_item_new.py, not here.
"""

import subprocess

import pytest

from invariant.cli.main import main

BACKLOG = (
    "Backlog\n"
    "=======\n"
    "\n"
    "Batch one\n"
    "---------\n"
    "\n"
    ".. bug:: First\n"
    "   :id: BUG_FIRST\n"
    "   :status: filed\n"
    "\n"
    "   Body.\n"
    "\n"
    "Batch two\n"
    "---------\n"
    "\n"
    ".. bug:: Second\n"
    "   :id: BUG_SECOND\n"
    "   :status: filed\n"
    "\n"
    "   Body.\n"
)

MANIFEST = (
    "items:\n"
    "  backend: sphinx_needs\n"
    "  path: docs/backlog.rst\n"
    "  id_strategy: slug\n"
)


@pytest.fixture
def backlog(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    (root / "docs").mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=root, check=True)
    (root / ".claude" / "invariant.yaml").write_text(MANIFEST, encoding="utf-8")
    path = root / "docs" / "backlog.rst"
    path.write_text(BACKLOG, encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
    return path


def item_new(*argv) -> int:
    """Run ``invariant item new`` in-process; an argparse refusal is an exit code."""
    try:
        return main(["item", "new", *argv])
    except SystemExit as exc:
        return int(exc.code or 0)


def directive(text: str, title: str) -> list[str]:
    """The option lines of the directive titled ``title``."""
    lines = text.splitlines()
    start = lines.index(f".. bug:: {title}")
    block = []
    for line in lines[start + 1:]:
        if not line.strip():
            break
        block.append(line.strip())
    return block


class TestBUG_074_a_FieldNamingAWrittenKey:
    def test_BUG_074_a_explicit_id_becomes_the_entry_id(self, backlog):
        rc = item_new("bug", "--title", "Probe one", "--field", "id=BUG_EXPLICIT",
                      "--body", "Probe body.")
        ids = [o for o in directive(backlog.read_text(encoding="utf-8"), "Probe one")
               if o.startswith(":id:")]
        assert (rc, ids) == (0, [":id: BUG_EXPLICIT"])

    def test_BUG_074_a_explicit_id_colliding_with_an_open_id_is_refused(self, backlog):
        rc = item_new("bug", "--title", "Probe two", "--field", "id=BUG_FIRST")
        assert rc != 0, "an explicit id equal to an open id was accepted"
        assert backlog.read_text(encoding="utf-8") == BACKLOG

    def test_BUG_074_a_explicit_status_is_not_silently_dropped(self, backlog):
        """Whether ``item new`` may set a non-initial status at all is the
        fixer's call (the bug machine's ``filed -> reproduced`` edge argues
        for refusing); either refusing or honouring passes. Writing a status
        the caller did not ask for and exiting 0 does not."""
        rc = item_new("bug", "--title", "Probe three", "--field", "status=reproduced")
        text = backlog.read_text(encoding="utf-8")
        if rc != 0:
            assert text == BACKLOG
            return
        statuses = [o for o in directive(text, "Probe three") if o.startswith(":status:")]
        assert statuses == [":status: reproduced"], (
            f"item new exited 0 and wrote {statuses} for --field status=reproduced"
        )


class TestBUG_074_b_SectionPlacement:
    def test_BUG_074_b_new_bug_lands_in_the_named_section(self, backlog):
        rc = item_new("bug", "--title", "Probe four", "--section", "Batch one")
        lines = backlog.read_text(encoding="utf-8").splitlines()
        assert ".. bug:: Probe four" in lines, (
            f"item new --section exited {rc} and filed nothing"
        )
        at = lines.index(".. bug:: Probe four")
        assert lines.index("Batch one") < at < lines.index("Batch two"), (
            "the new bug did not land in section 'Batch one'"
        )
