# ruff: noqa: F811 - the fixtures imported below are named as parameters
"""BUG_141's fix (DEC_ADR_014_B141): the base reader resolves the user level too.

No base tree holds the user level, so ``guidelines.baseline_only_shrinks``
reads the base's own level and, beneath it, the user's ``guidelines``
defaults as they stand now: the order the working configuration resolves
them in (ADR-014 D3). The repro is ``tests/test_bug_141_repro.py``; here, a
level above the user's still wins at the base, a ruleset set only by the
user is the base's ruleset too, and an in-repo project reads the same way.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from test_adr_014_docs_lock import WIDGETS, two  # noqa: F401 - the fixture, by name
from test_adr_014_docs_lock_host import hosts  # noqa: F401
from test_adr_014_guidelines_store import (
    BASELINE_REL,
    LINE,
    PRINT,
    RULESET,
    RULESET_REL,
    SHRINKS,
    baseline_text,
    commit,
    keep_guidelines_in_store,
    write,
)
from test_adr_014_per_leg_gates import gate
from test_adr_014_user_level import write_user_config
from test_bug_141_repro import user_baseline  # noqa: F401
from test_domains import git_project
from test_guidelines_domain import write as gwrite
from test_guidelines_findings import RULESET as REPO_RULESET
from test_guidelines_ratchet import RECORDED, entry_line, manifest_body, shrinks, stamp
from test_guidelines_ratchet import sh as rsh

from invariant.core import guidelines
from invariant.core.home import user_config_path
from invariant.core.rules import _user_guidelines

OTHER_REL = "docs/guidelines/other-baseline.txt"


def user_defaults(guidelines_: dict) -> None:
    path = user_config_path()
    body = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    body = body or {}
    body["defaults"] = {"guidelines": guidelines_}
    write_user_config(body)


class TestTheLevels:
    def test_a_store_level_baseline_wins_over_the_users_at_the_base(self, user_baseline):
        hosts = user_baseline
        # The store's block names another baseline, already at 2 on the base.
        body = yaml.safe_load((hosts.store / "invariant.yaml").read_text(encoding="utf-8"))
        body["store"]["repos"][WIDGETS]["guidelines"]["baseline"] = OTHER_REL
        (hosts.store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
        write(hosts.store, OTHER_REL, baseline_text(2))
        commit(hosts.store, "the store's own baseline")
        write(hosts.widgets, "app/m.py", f"{LINE}\n{LINE}\n")
        verdict = gate(hosts.widgets, SHRINKS)
        assert verdict.ok, f"{verdict.message} | {verdict.detail}"
        assert "grew" not in verdict.message

    def test_a_ruleset_set_only_by_the_user_is_the_bases_ruleset(self, hosts):
        keep_guidelines_in_store(hosts.store, WIDGETS)
        write(hosts.store, RULESET_REL, RULESET)
        write(hosts.store, BASELINE_REL, baseline_text(1))
        commit(hosts.store, "guidelines")
        user_defaults({"ruleset": RULESET_REL, "baseline": BASELINE_REL})
        write(hosts.widgets, "app/m.py", f"{LINE}\n")
        commit(hosts.widgets, "app")
        write(hosts.widgets, "app/m.py", f"{LINE}\n{LINE}\n")
        write(hosts.store, BASELINE_REL, baseline_text(2))
        verdict = gate(hosts.widgets, SHRINKS)
        # Read with no ruleset at the base, the rule would be one the change
        # introduces, and its entry admitted (DN-6).
        assert not verdict.ok, f"{verdict.message} | {verdict.detail}"
        assert "grew" in verdict.message and PRINT in verdict.message

    def test_only_a_user_level_is_read(self):
        class Fake:
            levels = (
                ("user:~/.invariant/config.yaml", {"guidelines": {"baseline": "a"}}),
                ("store:s", {"guidelines": {"baseline": "b"}}),
                ("user:other", {"guidelines": "not a mapping"}),
                ("repo:x", {}),
            )

        assert _user_guidelines(Fake()) == {"baseline": "a"}
        assert _user_guidelines(object()) == {}


def test_an_in_repo_project_reads_the_users_baseline_at_the_base(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    gwrite(root, RULESET_REL, REPO_RULESET)
    gwrite(root, "app/m.py", "print(x)\n")
    gwrite(root, BASELINE_REL, guidelines.BASELINE_HEADER + "\n" + RECORDED)
    git_project(root, manifest_body(baseline=None))
    user_defaults({"baseline": BASELINE_REL})
    rsh(root, "checkout", "-q", "-b", "work")
    gwrite(root, "app/m.py", "print(x)\nprint(x)\n")
    gwrite(root, BASELINE_REL, entry_line(PRINT, "app/m.py", stamp("print(x)"), 2))
    rsh(root, "add", "-A")
    rsh(root, "commit", "-qm", "raise")
    verdict = shrinks(root)
    assert not verdict.ok, f"{verdict.message} | {verdict.detail}"
    assert f"raised {PRINT} app/m.py {stamp('print(x)')} from 1 to 2" in verdict.message
