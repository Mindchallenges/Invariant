"""Repro for BUG_084 — see the work-item entry (docs/backlog/BUG_084.md).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrappers; the assertions must then pass unchanged.

Nothing here runs ``tailscale``. The macOS app's binary is played by a
stand-in for ``subprocess.run`` that behaves as the entry records it does,
verified live: with no ``TERM`` in its environment it tries to start its GUI,
prints "The Tailscale GUI failed to start: ... Tailscale.CLIError" and exits
0; with ``TERM`` set it answers as the CLI.

Symptom asserted, in two tests:

1. Under the web agent's environment (the plist's ``EnvironmentVariables``,
   and no ``TERM``, which launchd never provides), the real certificate seam
   (``_real_oracle_web_certificates``, through the real ``run_program``)
   fetches the certificate. It passes whether the fix puts ``TERM`` in
   ``run_program``'s environment or in the plist.
2. A ``status --json`` answer that is not JSON is reported by the CLI's own
   first line, not by a JSON parse error.

The contract is docs/architecture/oracle.md, "Over HTTPS (IDEA_026)": with
HTTPS Certificates on, the agent fetches the certificate and the page is
served over HTTPS; "A renewal fails | ... the failure in ``status`` and the
log", which a JSON parse error does not name.

Explicitly NOT asserted: how the binary decides it is the GUI. The real
launchd leg against the real app is owed residue on the entry.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from invariant.cli import main as cli_main
from invariant.oracle import launchd, tls, web
from invariant.oracle.tls import CertError, issue

FIXTURES = Path(__file__).parent / "fixtures" / "tls"
NAME = "mac.example.ts.net"
GUI_FAILURE = ("The Tailscale GUI failed to start: The operation couldn't be completed. "
               "(Tailscale.CLIError error 1.)\n")


def _app_binary(argv, *, env=None, **_):
    """The app bundle's binary: the GUI's failure unless ``TERM`` is set."""
    import os

    environment = os.environ if env is None else env
    if "TERM" not in environment:
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr=GUI_FAILURE)
    if argv[1:3] == ["status", "--json"]:
        body = {"Self": {"DNSName": NAME + "."}, "CertDomains": [NAME]}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(body), stderr="")
    if argv[1] == "cert":
        pem = (FIXTURES / "a.crt").read_text() + (FIXTURES / "a.key").read_text()
        return subprocess.CompletedProcess(argv, 0, stdout=pem, stderr="")
    return subprocess.CompletedProcess(argv, 1, stdout="", stderr="unexpected")


def test_BUG_084_the_web_agent_fetches_a_certificate_with_no_TERM_from_launchd(
        monkeypatch, tmp_path):
    plist = launchd.web_plist_for("/usr/bin/python3", tmp_path, 7341,
                                  path="/usr/bin:/bin", pin_home=False)
    monkeypatch.delenv("TERM", raising=False)
    for key, value in plist["EnvironmentVariables"].items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(tls, "tailscale_binaries", lambda: [web.MACOS_APP_BINARY])
    monkeypatch.setattr(web.subprocess, "run", _app_binary)
    issued = cli_main._real_oracle_web_certificates()()
    assert issued.name == NAME


def test_BUG_084_an_answer_that_is_not_json_is_reported_by_its_first_line():
    def run(argv):
        return 0, GUI_FAILURE

    with pytest.raises(CertError) as caught:
        issue(run, binaries=[web.MACOS_APP_BINARY])
    assert "The Tailscale GUI failed to start" in str(caught.value), str(caught.value)
