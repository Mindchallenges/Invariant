"""Repro for BUG_083 — see the work-item entry (docs/backlog/BUG_083.md).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

Symptom asserted: with a kept certificate, ``oracle web`` answers nothing.
The test runs the command itself, with the suite's certificate recorder
handing it the self-signed fixture, and lets the command build its listener
through the REAL ``WebServer`` constructor, with exactly the arguments the
command passes. Only the bind is moved to loopback port 0 (no test binds a
tailnet address). Then two clients connect, as the live page was:

* a TLS client asking for ``/`` at the certificate's name must get an HTTP
  answer (401: it is not paired);
* a plain-HTTP request must get the ``307`` to ``https://<name>:<port>/``.

The contract is docs/architecture/oracle.md, "Over HTTPS (IDEA_026)": "A
valid certificate | HTTPS on the port, at the certificate's name only. A
plain-HTTP request gets ``307`` to ``https://<name>:<port>/``".

Explicitly NOT asserted: the suspected cause (that the holder lands in
``request_timeout`` positionally). The test reads only what the clients get.
"""

from __future__ import annotations

import http.client
import ssl
from pathlib import Path
from typing import ClassVar

from invariant.cli.main import main
from invariant.oracle.tls import Issued
from invariant.oracle.web import LOOPBACK, WebServer

TLS_FIXTURES = Path(__file__).parent / "fixtures" / "tls"
TS_NAME = "mac.example.ts.net"
TS4 = "100.101.102.103"


def _tls_get(port: int) -> object:
    """``/`` over TLS at the certificate's name: the status, or what went wrong."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    connection = http.client.HTTPSConnection(LOOPBACK, port, timeout=5, context=context)
    try:
        connection.request("GET", "/", headers={"Host": f"{TS_NAME}:{port}"})
        return connection.getresponse().status
    except (OSError, http.client.HTTPException) as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        connection.close()


def _http_get(port: int) -> object:
    """``/`` over plain HTTP: the status and Location, or what went wrong."""
    connection = http.client.HTTPConnection(LOOPBACK, port, timeout=5)
    try:
        connection.request("GET", "/", headers={"Host": f"{TS4}:{port}"})
        answer = connection.getresponse()
        return answer.status, answer.getheader("Location")
    except (OSError, http.client.HTTPException) as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        connection.close()


class _Probe(WebServer):
    """The real listener; ``serve`` connects to it twice and returns."""

    seen: ClassVar[dict] = {}

    def serve(self) -> None:
        self.serve_in_background()
        type(self).seen = {"https": _tls_get(self.port), "http": _http_get(self.port),
                           "port": self.port}


def test_BUG_083_a_kept_certificate_serves_https_and_redirects_http(
        capsys, oracle_web, oracle_certificates):
    oracle_certificates.issued = Issued(
        TS_NAME, (TLS_FIXTURES / "a.crt").read_text(), (TLS_FIXTURES / "a.key").read_text())
    oracle_web.addresses = [TS4]

    def factory(app, addresses, port, *rest, **keywords):
        # The command's own arguments, unchanged, to the real constructor;
        # only the address and port are loopback's.
        return _Probe(app, [LOOPBACK], 0, *rest, **keywords)

    oracle_web.factory = factory
    _Probe.seen = {}
    assert main(["oracle", "web", "--port", "7400"]) == 0
    seen = _Probe.seen
    port = seen["port"]
    assert seen["https"] == 401 and seen["http"] == (307, f"https://{TS_NAME}:{port}/"), seen
