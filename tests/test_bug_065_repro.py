"""Repro for BUG_065 — see the work-item entry (docs/backlog/BUG_065.md).

Both tests fail while the defect is live. When the fix lands, delete the
expected-failure wrappers; each assertion must then pass unchanged.

Two independently failing symptoms, on a sphinx-needs backlog whose entry has
a wrapped ``:area:`` option whose continuation line opens with an inline role
(``:doc:``), followed by one blank line and a section heading:

1. READ: ``show`` reports a truncated ``area`` and a bogus separate ``doc``
   field before anything is written.
2. WRITE: ``transition filed -> reproduced`` rewrites bytes other than the
   status line — the wrapped option is reflowed onto one line, a ``:doc:``
   option line is emitted, and the blank line before the next heading is lost.

Neither asserts the suspected cause (an option regex that matches a leading
role; a renderer that re-emits from parsed fields) — each asserts what the
user saw: the field ``show`` prints, and the file diff after a transition.
"""

import difflib

from test_domains import git_project

from invariant.domains.workitem import WorkItemDomain

ENTRY_STATUS_LINE = "   :status: filed"

BACKLOG = """Backlog
=======

Open bugs
---------

.. bug:: Neighbour entry
   :id: BUG_NEIGHBOUR
   :status: filed
   :severity: low

   A plain single-line entry beside the one under test.

.. bug:: Wrapped area with a leading role
   :id: BUG_WRAPPED
   :status: filed
   :severity: high
   :area: core/items,
          the store backends, see
          :doc:`architecture/overview` for the owner

   The body of the entry under test.

Closed bugs
-----------

Nothing here yet.
"""


def _domain(tmp_path):
    m = git_project(
        tmp_path / "proj",
        {
            "version": 1,
            "project": {"name": "demo"},
            "items": {"backend": "sphinx_needs", "path": "docs/backlog.rst"},
        },
    )
    path = m.root / "docs" / "backlog.rst"
    path.write_text(BACKLOG, encoding="utf-8")
    return WorkItemDomain(m), path, m


def test_BUG_065_show_reads_the_whole_wrapped_area_and_no_doc_field(tmp_path):
    domain, _, _ = _domain(tmp_path)
    item = domain.get("BUG_WRAPPED")
    assert "doc" not in item.fields, f"bogus fields: {item.fields!r}"
    assert item.fields["area"] == (
        "core/items, the store backends, see "
        ":doc:`architecture/overview` for the owner"
    )


def test_BUG_065_transition_changes_only_the_status_line(tmp_path):
    domain, path, m = _domain(tmp_path)
    before = path.read_text(encoding="utf-8").splitlines()

    domain.transition("BUG_WRAPPED", "reproduced", cwd=m.root)

    after = path.read_text(encoding="utf-8").splitlines()
    diff = list(difflib.unified_diff(before, after, lineterm="", n=0))
    removed = [
        line[1:] for line in diff if line.startswith("-") and not line.startswith("---")
    ]
    # Every original line survives in order except the entry's own status line.
    # (Lines ADDED for status/history are the transition's business; a removed
    # line is always corruption.)
    assert removed == [ENTRY_STATUS_LINE], "\n".join(diff)
    assert "   :status: reproduced" in after
