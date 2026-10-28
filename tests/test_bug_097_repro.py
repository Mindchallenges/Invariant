"""Repro for BUG_097 — see the work-item entry (docs/backlog/BUG_097.md).

Both tests fail while the defect is live. When the fix lands, delete the
expected-failure wrapper; each assertion must then pass unchanged.

Two faces, two symptoms:

1. A manifest listing ``[python, node]`` with no explicit commands resolves
   the primary's suite command to node's (``npm test``): the primary
   ecosystem's run is the wrong runner.
2. ``pipeline test --selector <a node test>`` in a ``[python, node]`` project
   does not hand the run to the node adapter, so the second ecosystem's red
   run is read by the primary's adapter.

Neither asserts the suspected cause (``get_adapter(ecosystems[0])``, the
later-wins merge of adapter defaults) — each asserts what a two-ecosystem
project observes.
"""

import subprocess

import pytest
from test_domains import git_project

from invariant.adapters.node import NodeAdapter
from invariant.adapters.python_ import PythonAdapter
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.domains import pipeline as pipeline_module
from invariant.domains.pipeline import PipelineDomain
from invariant.domains.session import SessionDomain

TWO_ECOSYSTEMS = {"version": 1, "project": {"name": "demo", "ecosystems": ["python", "node"]}}
NODE_TEST = "web/src/render.test.ts"


@pytest.mark.xfail(
    strict=True,
    reason="BUG_097 is live — delete this marker with the fix",
)
def test_BUG_097_a_stacked_second_ecosystem_keeps_the_primarys_suite_command(tmp_path):
    m = Manifest.from_raw(
        {"project": {"ecosystems": ["python", "node"]}},
        root=tmp_path,
        path=tmp_path / MANIFEST_RELPATH,
        env={},
    )
    # The primary is python; its suite command is python's default.
    assert m.command_for("test") == PythonAdapter.manifest_defaults["commands"]["test"]


@pytest.mark.xfail(
    strict=True,
    reason="BUG_097 is live — delete this marker with the fix",
)
def test_BUG_097_a_second_ecosystem_test_is_run_and_read_by_its_own_adapter(
    tmp_path, monkeypatch
):
    m = git_project(tmp_path / "proj", TWO_ECOSYSTEMS)
    (m.root / "web" / "src").mkdir(parents=True)
    (m.root / "web" / "src" / "render.test.ts").write_text(
        "test('renders', () => { expect(1).toBe(2) })\n", encoding="utf-8"
    )

    ran: list[str] = []

    def fake_run(command, root):
        ran.append(command)
        return subprocess.CompletedProcess(
            command, 1, stdout="Tests:       1 failed, 1 total\n", stderr=""
        )

    monkeypatch.setattr(pipeline_module, "run_command", fake_run)

    readers: list[str] = []
    for cls in (NodeAdapter, PythonAdapter):
        original = cls.read_run

        def spy(self, *args, _original=original, _id=cls.id, **kwargs):
            readers.append(_id)
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(cls, "read_run", spy)

    session = SessionDomain(m).start("x")
    pipelines = PipelineDomain(m)
    pipelines.start(session.id)
    pipelines.run_tests(session.id, phase="red", selectors=[NODE_TEST], cwd=m.root)

    node_test_one = NodeAdapter.manifest_defaults["commands"]["test_one"]
    assert ran == [node_test_one.format(selector=NODE_TEST)], ran
    assert readers == ["node"], f"the node test's run was read by: {readers}"
