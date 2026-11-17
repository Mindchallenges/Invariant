"""Repro for BUG_124 — see the work-item entry (`invariant item show BUG_124`).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

Symptom: ``invariant docs venv`` over a ``requirements.txt`` that lists only
top-level ranges (Dang's shape) succeeds, records the environment as in sync,
and leaves it without Sphinx's dependencies, so ``sphinx-build`` dies
importing ``docutils``.

Asserted: ``sync_venv`` over a ranges-only file either refuses it (and the
environment does not read in sync), or returns an environment that holds
``docutils``. It must never return "in sync" over an environment that cannot
import ``docutils``.
NOT asserted: the suspected cause (``uv pip sync`` installing no dependencies)
or which remedy a fix picks (refusing unpinned files, installing with
dependencies, compiling a lock, checking the environment afterwards) — that
is ``feature-architect``'s decision.

Contract: ADR-018 D4 (DEC_ADR_018_D4) — ``==`` pins are *recommended* and
"dang's uses ``>=``"; ``doctor``'s docs-venv row means "present, in sync with
``requirements.txt``, ``sphinx-build`` runs". The entry's Expected line:
"After ``docs venv`` succeeds, the environment can run ``sphinx-build``. A
``requirements.txt`` the command cannot build a working environment from is
refused, naming the problem, and the environment is not recorded as in sync."

The ``run`` seam is a recording fake that models how uv treats each command
(observed against real uv 2026-11-17: ``uv pip sync`` over this file installs
exactly the four listed packages and ``sphinx-build`` fails importing
``docutils``). If a fix drives uv through a command the model does not know,
extend the model with that command's real behaviour; do not weaken the
assertion.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from invariant.core.errors import UsageError
from invariant.domains import docs as docs_domain

#: Dang's file: top-level ranges, no dependencies listed.
RANGES_ONLY = "sphinx>=9.1\nsphinx-needs>=9.2\nsphinxcontrib-mermaid>=1.0\nfuro>=2025.1\n"

#: A slice of the real dependency graph, enough to make the symptom fire.
DEPENDS = {
    "sphinx": ["docutils", "jinja2", "pygments", "babel", "alabaster"],
    "sphinx-needs": ["sphinx", "jsonschema", "requests"],
    "sphinxcontrib-mermaid": ["sphinx", "pyyaml"],
    "furo": ["sphinx", "beautifulsoup4", "pygments"],
    "jinja2": ["markupsafe"],
    "jsonschema": ["attrs", "referencing"],
}
VERSIONS = {"sphinx": "9.2.0", "sphinx-needs": "9.2.0", "sphinxcontrib-mermaid": "2.1.1", "furo": "2025.12.19"}


def _names(text: str) -> list[str]:
    names = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        names.append(re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].strip().lower())
    return names


def _closure(names) -> set[str]:
    seen: set[str] = set()
    todo = list(names)
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            todo.extend(DEPENDS.get(name, []))
    return seen


class ModelUv:
    """Records each command and models what real uv leaves installed."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.installed: set[str] = set()

    def _files(self, argv, flag_r: bool):
        files = []
        for i, arg in enumerate(argv):
            if flag_r and arg in ("-r", "--requirement") and i + 1 < len(argv):
                files.append(Path(argv[i + 1]))
            elif not flag_r and arg.endswith(".txt") and argv[i - 1] not in ("-o", "--output-file"):
                files.append(Path(arg))
        return files

    def __call__(self, argv, cwd):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        ok = subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0].endswith("sphinx-build") or argv[-2:] == ["-c", "import docutils"]:
            if "docutils" in self.installed:
                return ok
            return subprocess.CompletedProcess(
                argv, 1, "", "ModuleNotFoundError: No module named 'docutils'"
            )
        if argv[1] == "venv":
            venv = Path(argv[-1])
            (venv / "bin").mkdir(parents=True, exist_ok=True)
            (venv / "bin" / "python").write_text("")
            (venv / "bin" / "sphinx-build").write_text("")
            return ok
        sub = argv[1:3]
        if sub == ["pip", "sync"]:
            # Installs exactly what the files list, and nothing they depend on.
            listed = [n for f in self._files(argv, False) for n in _names(f.read_text())]
            self.installed = set(listed)
            return ok
        if sub == ["pip", "install"]:
            listed = [n for f in self._files(argv, True) for n in _names(f.read_text())]
            listed += [_names(a)[0] for a in argv[3:] if not a.startswith("-") and _names(a)
                       and not a.endswith(".txt") and "/" not in a]
            self.installed |= set(listed) if "--no-deps" in argv else _closure(listed)
            return ok
        if sub == ["pip", "compile"]:
            listed = [n for f in self._files(argv, False) for n in _names(f.read_text())]
            pinned = "".join(f"{n}=={VERSIONS.get(n, '1.0')}\n" for n in sorted(_closure(listed)))
            if "-o" in argv or "--output-file" in argv:
                flag = "-o" if "-o" in argv else "--output-file"
                Path(argv[argv.index(flag) + 1]).write_text(pinned)
                return ok
            return subprocess.CompletedProcess(argv, 0, pinned, "")
        if sub == ["pip", "check"]:
            missing = _closure(self.installed) - self.installed
            if missing:
                return subprocess.CompletedProcess(
                    argv, 1, "", "".join(f"missing {m}\n" for m in sorted(missing))
                )
            return ok
        return ok


def test_BUG_124_ranges_only_requirements_never_read_in_sync_without_dependencies(tmp_path):
    srcdir = tmp_path / "docs"
    srcdir.mkdir()
    (srcdir / "conf.py").write_text("")
    (srcdir / "requirements.txt").write_text(RANGES_ONLY)
    uv = ModelUv()

    try:
        state = docs_domain.sync_venv(srcdir, run=uv, uv="uv")
    except UsageError as refused:
        # Refused: it must say why, and leave nothing reading in sync.
        assert "requirements.txt" in str(refused)
        assert not docs_domain.venv_state(srcdir).in_sync
        return

    assert not (state.in_sync and "docutils" not in uv.installed), (
        "docs venv reported the environment in sync with requirements.txt, "
        f"but it holds only {sorted(uv.installed)}: sphinx-build would fail "
        "importing docutils"
    )
    assert "docutils" in uv.installed, (
        f"docs venv succeeded over a ranges-only requirements.txt and left "
        f"{sorted(uv.installed)}, without docutils"
    )
