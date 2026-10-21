"""Repro for BUG_059 — see the work-item entry (docs/backlog/BUG_059.md).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

Symptom: running ``invariant doctor`` marks the caller's own host binding
SPENT — the session document's ``host_ended`` goes from ``false`` to ``true``,
although nothing ended. ``doctor`` is documented (docs/architecture/cli.md) as
a check of the installation: "manifest valid, shims present and executable,
host settings wired, state root writable, ...".

Asserts the user-visible outcome — ``host_ended`` on the caller's session
before and after ``doctor`` — not the suspected cause (the shim liveness probe
delivering a real ``session-end`` through ``end_host_session``). The second
hazard the same repro exposed is BUG_061 and is not asserted here.

SAFETY: everything this test touches is under ``tmp_path``. The state root is
pinned there explicitly, the host-session id is a synthetic one set by the
test, ``INVARIANT_SESSION_ID`` is removed, and the ``invariant`` the shims
exec is a wrapper running THIS checkout's source — so the probe can never
reach a real state root or spend a real host binding.
"""

import os
import subprocess
import sys
from pathlib import Path

from invariant.cli.main import main
from invariant.core.manifest import Manifest
from invariant.domains.session import SessionDomain

HOST_ID = "host-bug059-synthetic"
SRC = Path(__file__).resolve().parent.parent / "src"


def test_BUG_059_doctor_does_not_spend_the_callers_host_binding(
    tmp_path, monkeypatch
):
    root = tmp_path / "proj"
    root.mkdir()
    for argv in (
        ["git", "init", "-q", "-b", "master"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(argv, cwd=root, check=True)
    (root / "docs" / "architecture").mkdir(parents=True)
    (root / "docs" / "architecture" / "overview.md").write_text(
        "# Overview\n\n```mermaid\ngraph TD\n  A-->B\n```\n", encoding="utf-8"
    )
    (root / "app.py").write_text("print(1)\n", encoding="utf-8")

    # The `invariant` the installed shims will exec: this checkout's source,
    # first on PATH, so neither the editable install nor any other build runs.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "invariant"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'PYTHONPATH="{SRC}" exec "{sys.executable}" -m invariant "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    state_root = tmp_path / "state"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("INVARIANT_STATE_ROOT", str(state_root))
    monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", HOST_ID)
    monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(root)

    # 1. init, commit, start a session with the host id exported.
    main(["init", "--ecosystem", "python"])
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    main(["session", "start", "x"])

    manifest = Manifest.load(root)
    assert manifest.state_root == state_root.resolve()  # never a live root
    session = SessionDomain(manifest).by_host_session(HOST_ID)
    assert session is not None, "precondition: session start bound the host id"

    # 2. host_ended is false.
    assert session.host_ended is False

    # 3. invariant doctor.
    main(["doctor"])

    # 4. Read it again.
    after = SessionDomain(Manifest.load(root)).get(session.id)
    assert after is not None
    assert after.host_ended is False, (
        "`invariant doctor` marked the caller's host binding spent: "
        f"host_ended {session.host_ended} -> {after.host_ended}"
    )
