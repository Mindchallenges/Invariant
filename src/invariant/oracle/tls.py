"""The web page's HTTPS certificate, from Tailscale (IDEA_026, ADR-008 D19-D24).

The page is served to a phone at the Mac's MagicDNS name,
``<host>.<tailnet>.ts.net``, with the Let's Encrypt certificate Tailscale
issues for that name. This module owns that certificate:

* **Where it comes from.** :func:`issue` asks Tailscale's CLI: ``status
  --json`` for the name (its ``CertDomains``, else ``Self.DNSName``), then
  ``cert --cert-file - --key-file - <name>``, which prints the certificate
  chain and then the private key to stdout. Stdout, not a file path, because
  the macOS app's CLI is sandboxed and may write only inside its own
  container. The CLI is found by the address discovery's own lookup
  (``web.tailscale_binaries``), which includes the app bundle's path, so a
  LaunchAgent's minimal ``PATH`` does not hide it.
* **Where it is kept.** One file, ``<oracle home>/web/tls/oracle.pem``, the
  chain followed by the key, born ``0600`` in ``0700`` directories and
  replaced atomically. One file because ``ssl`` loads a key from the same
  file as its certificate, so a crash can never leave a certificate beside
  another one's key. A new one is loaded once before it replaces the old, so
  a key that does not match never displaces a good pair.
* **When it is due.** :class:`Certificates` asks again only once fewer than
  :data:`RENEW_BEFORE` remain, or when there is none, and not more often than
  :data:`RETRY_RENEW` (:data:`RETRY_MISSING` with none at all). Tailscale
  keeps its own copy and renews it, so asking returns its current one.
* **How it is swapped.** :class:`Served` is what a listener serves: a name
  and an ``SSLContext``, replaced together. Each new connection reads the
  current pair, so a renewal reaches the next connection with no restart and
  without touching a context a handshake may be using.

Reading a certificate's name and expiry needs only the standard library: a
small DER reader for the few fields used (:func:`cert_info`).

The suite never runs ``tailscale``. The CLI reaches :func:`issue` through the
``_oracle_web_certificates`` seam, which ``tests/conftest.py`` replaces with a
:class:`RecordingIssuer`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..core.errors import InvariantError, StateError
from .home import private_dir, write_private
from .web import Runner, tailscale_binaries

#: The certificate chain and its key, one file, under the oracle home.
CERT_FILE = "web/tls/oracle.pem"
#: Where a new one is written and loaded once before it replaces the old.
CANDIDATE_FILE = "web/tls/candidate.pem"

DAY = 86400.0

#: Ask for a new certificate once fewer than this many seconds remain. A
#: Let's Encrypt certificate lives 90 days; Tailscale renews its own copy at
#: about a third of that left.
RENEW_BEFORE = 30 * DAY

#: Once due, ask again at most this often while the answer is the same one.
RETRY_RENEW = 3600.0

#: With no certificate at all, ask again this often: the person may have just
#: turned HTTPS Certificates on in the admin console.
RETRY_MISSING = 600.0

#: ``doctor`` fails once fewer than this many seconds remain: renewal has
#: been failing for two weeks.
WARN_BEFORE = 14 * DAY

#: Seconds Tailscale's ``cert`` may take: an issuance talks to Let's Encrypt.
CERT_TIMEOUT = 120.0

_PEM = re.compile(r"-----BEGIN ([A-Z0-9 ]+)-----\r?\n.*?-----END \1-----", re.DOTALL)

# DER tags and the one object identifier read.
_SEQUENCE = 0x30
_UTC_TIME = 0x17
_GENERALIZED_TIME = 0x18
_VERSION = 0xA0
_EXTENSIONS = 0xA3
_OID = 0x06
_DNS_NAME = 0x82
#: subjectAltName, 2.5.29.17.
_SAN = bytes.fromhex("551d11")


class CertError(InvariantError):
    """No certificate could be had, or the one given cannot be used."""


@dataclass(frozen=True)
class Issued:
    """What Tailscale printed: the name asked for, the chain and the key, as PEM."""

    name: str
    certificate: str
    key: str


@dataclass(frozen=True)
class CertInfo:
    names: tuple[str, ...]
    not_after: float


@dataclass(frozen=True)
class Certificate:
    """The kept certificate: its file, the name it serves, when it expires."""

    path: Path
    name: str
    names: tuple[str, ...]
    not_after: float
    #: SHA-256 of the file, so a renewal is told from the same one again.
    fingerprint: str


# -- reading PEM and DER ----------------------------------------------------------------


def split_pem(text: str) -> tuple[str, str]:
    """The certificate chain, in order, and the one private key in ``text``.

    Anything around the blocks, and their order, is ignored: Tailscale prints
    the chain and then the key, and may say something before either.
    """
    chain, keys = [], []
    for match in _PEM.finditer(text):
        kind = match.group(1)
        if kind == "CERTIFICATE":
            chain.append(match.group(0))
        elif kind.endswith("PRIVATE KEY"):
            keys.append(match.group(0))
    if not chain:
        raise CertError("Tailscale printed no certificate")
    if not keys:
        raise CertError("Tailscale printed no private key")
    if len(keys) > 1:
        raise CertError("Tailscale printed more than one private key")
    return "\n".join(chain) + "\n", keys[0] + "\n"


def _tlv(data: bytes, pos: int, end: int) -> tuple[int, int, int]:
    """The DER element at ``pos``: its tag, and where its content starts and ends."""
    if pos + 2 > end:
        raise ValueError("truncated element")
    tag, length = data[pos], data[pos + 1]
    pos += 2
    if length & 0x80:
        count = length & 0x7F
        if not 1 <= count <= 4 or pos + count > end:
            raise ValueError("bad length")
        length = int.from_bytes(data[pos:pos + count], "big")
        pos += count
    if pos + length > end:
        raise ValueError("truncated content")
    return tag, pos, pos + length


def _items(data: bytes, start: int, end: int):
    pos = start
    while pos < end:
        tag, body, stop = _tlv(data, pos, end)
        yield tag, body, stop
        pos = stop


def _time(data: bytes, tag: int, start: int, end: int) -> float:
    text = data[start:end].decode("ascii")
    if tag == _UTC_TIME:
        text = ("19" if int(text[:2]) >= 50 else "20") + text
    elif tag != _GENERALIZED_TIME:
        raise ValueError("not a time")
    if len(text) != 15 or not text.endswith("Z"):
        raise ValueError(f"an unexpected time {text!r}")
    moment = datetime.strptime(text, "%Y%m%d%H%M%SZ").replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _names(data: bytes, start: int, end: int) -> list[str]:
    """The DNS names of the subjectAltName extension, from ``[3] Extensions``."""
    _, start, end = _tlv(data, start, end)
    for _, ext_start, ext_end in _items(data, start, end):
        parts = list(_items(data, ext_start, ext_end))
        tag, oid_start, oid_end = parts[0]
        if tag != _OID or data[oid_start:oid_end] != _SAN:
            continue
        _, value_start, value_end = parts[-1]
        _, names_start, names_end = _tlv(data, value_start, value_end)
        return [data[s:e].decode("ascii").lower()
                for tag, s, e in _items(data, names_start, names_end) if tag == _DNS_NAME]
    return []


def _parse(der: bytes) -> CertInfo:
    tag, start, end = _tlv(der, 0, len(der))
    if tag != _SEQUENCE:
        raise ValueError("not a certificate")
    tag, start, end = _tlv(der, start, end)
    if tag != _SEQUENCE:
        raise ValueError("no to-be-signed part")
    fields = list(_items(der, start, end))
    if fields and fields[0][0] == _VERSION:
        fields = fields[1:]
    # serial, signature algorithm, issuer, validity, subject, key, then optionals
    if len(fields) < 6 or fields[3][0] != _SEQUENCE:
        raise ValueError("no validity")
    times = list(_items(der, fields[3][1], fields[3][2]))
    if len(times) != 2:
        raise ValueError("no validity")
    not_after = _time(der, *times[1])
    names: list[str] = []
    for tag, start, end in fields[6:]:
        if tag == _EXTENSIONS:
            names = _names(der, start, end)
    return CertInfo(tuple(names), not_after)


def cert_info(chain: str) -> CertInfo:
    """The DNS names and the expiry of the first certificate in ``chain``."""
    first = next((m.group(0) for m in _PEM.finditer(chain) if m.group(1) == "CERTIFICATE"),
                 chain)
    try:
        return _parse(ssl.PEM_cert_to_DER_cert(first.strip()))
    except (ValueError, IndexError, UnicodeDecodeError) as exc:
        raise CertError(f"cannot read the certificate: {exc}") from exc


def expires_text(not_after: float) -> str:
    return datetime.fromtimestamp(not_after, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- asking Tailscale -------------------------------------------------------------------


def cert_domain(status_json: str) -> tuple[str | None, bool]:
    """The name to ask a certificate for, and whether Tailscale lists it as one.

    ``CertDomains`` is set only when HTTPS Certificates are on for the
    tailnet. Without it the MagicDNS name is tried anyway, so Tailscale's own
    refusal is what the person reads.
    """
    try:
        status = json.loads(status_json)
    except ValueError as exc:
        # The app's CLI without TERM prints its GUI's failure and exits 0
        # (BUG_084): what it said is the cause, not the parse error.
        raise CertError("tailscale status --json did not print JSON; it said: "
                        f"{_first_line(status_json)}") from exc
    if not isinstance(status, dict):
        raise CertError("tailscale status --json did not print an object")
    domains = status.get("CertDomains")
    if isinstance(domains, list) and domains and isinstance(domains[0], str):
        return domains[0].rstrip(".").lower(), True
    own = status.get("Self")
    name = own.get("DNSName") if isinstance(own, dict) else None
    if isinstance(name, str) and name.rstrip("."):
        return name.rstrip(".").lower(), False
    return None, False


def _first_line(text: str) -> str:
    return (text.strip().splitlines() or ["no output"])[0]


def issue(run: Runner, binaries: list[str] | None = None) -> Issued:
    """Ask Tailscale for this Mac's certificate, through stdout."""
    found = tailscale_binaries() if binaries is None else binaries
    if not found:
        raise CertError("no Tailscale CLI: neither 'tailscale' on PATH nor the macOS app's")
    failures = []
    for binary in found:
        status, text = run([binary, "status", "--json"])
        if status != 0:
            failures.append(_first_line(text))
            continue
        name, listed = cert_domain(text)
        if name is None:
            raise CertError("Tailscale reports no MagicDNS name for this Mac; turn MagicDNS "
                            "on for the tailnet")
        status, text = run([binary, "cert", "--cert-file", "-", "--key-file", "-", name])
        if status != 0:
            hint = "" if listed else (
                "; HTTPS Certificates look off for the tailnet (status lists no CertDomains):"
                " turn them on on the DNS page of the Tailscale admin console")
            raise CertError(f"tailscale cert {name} failed: {_first_line(text)}{hint}")
        chain, key = split_pem(text)
        return Issued(name, chain, key)
    raise CertError(f"cannot ask Tailscale for this Mac's name: {'; '.join(failures)}")


# -- keeping it -------------------------------------------------------------------------


def context_for(path: Path) -> ssl.SSLContext:
    """A server context serving the chain and key in ``path``."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_cert_chain(path)
    except (ssl.SSLError, OSError) as exc:
        raise CertError(f"cannot load {path}: {exc}") from exc
    return context


def read_certificate(home: Path) -> Certificate | None:
    """The kept certificate, or ``None`` when there is none that can be read."""
    path = Path(home) / CERT_FILE
    try:
        data = path.read_bytes()
    except OSError:
        return None
    text = data.decode("ascii", "replace")
    try:
        chain, _ = split_pem(text)
        info = cert_info(chain)
    except CertError:
        return None
    if not info.names:
        return None
    return Certificate(path, info.names[0], info.names, info.not_after,
                       hashlib.sha256(data).hexdigest())


class Certificates:
    """The kept certificate, and when to ask Tailscale for another.

    ``issue`` is the seam: ``() -> Issued``, raising :class:`CertError`.
    ``error`` is why the last attempt failed, until one succeeds.
    """

    def __init__(self, home: Path, issue: Callable[[], Issued],
                 clock: Callable[[], float] = time.time) -> None:
        self.home = Path(home)
        self.issue = issue
        self.clock = clock
        self.error: str | None = None
        self._next_try = 0.0

    def ensure(self) -> Certificate | None:
        """A certificate valid now, asked for first when one is due; else ``None``."""
        now = self.clock()
        current = read_certificate(self.home)
        if current is not None and current.not_after <= now:
            current = None
        due = current is None or current.not_after - now <= RENEW_BEFORE
        if not due or now < self._next_try:
            return current
        try:
            fresh = self._fetch(now)
        except (CertError, StateError) as exc:
            self.error = str(exc)
            self._next_try = now + (RETRY_MISSING if current is None else RETRY_RENEW)
            return current
        self.error = None
        if fresh.not_after - now <= RENEW_BEFORE:
            self._next_try = now + RETRY_RENEW
        return fresh

    def context(self, certificate: Certificate) -> ssl.SSLContext:
        """A context for ``certificate``; a failure is recorded as the error too."""
        try:
            return context_for(certificate.path)
        except CertError as exc:
            self.error = str(exc)
            raise

    def _fetch(self, now: float) -> Certificate:
        issued = self.issue()
        info = cert_info(issued.certificate)
        if issued.name not in info.names:
            raise CertError(f"the certificate names {', '.join(info.names) or 'nothing'}, "
                            f"not {issued.name}")
        if info.not_after <= now:
            raise CertError(f"Tailscale handed back a certificate for {issued.name} that "
                            f"expired at {expires_text(info.not_after)}")
        private_dir(self.home / "web")
        candidate = self.home / CANDIDATE_FILE
        write_private(candidate, (issued.certificate + issued.key).encode("ascii"))
        try:
            context_for(candidate)
            os.replace(candidate, self.home / CERT_FILE)
        except (CertError, OSError) as exc:
            candidate.unlink(missing_ok=True)
            raise CertError(str(exc)) from exc
        kept = read_certificate(self.home)
        if kept is None:  # pragma: no cover - just written and loaded
            raise CertError(f"cannot read back {CERT_FILE}")
        return kept


class Served:
    """The certificate a listener serves and its context, swapped together.

    A connection reads :attr:`context` once, when it arrives, so a renewal
    reaches the next connection, and a handshake under way keeps the context
    it began with.
    """

    def __init__(self, certificate: Certificate, context: ssl.SSLContext) -> None:
        self._current = (certificate, context)

    @property
    def certificate(self) -> Certificate:
        return self._current[0]

    @property
    def context(self) -> ssl.SSLContext:
        return self._current[1]

    @property
    def name(self) -> str:
        return self._current[0].name

    @property
    def not_after(self) -> float:
        return self._current[0].not_after

    def renew(self, certificate: Certificate, context: ssl.SSLContext) -> None:
        self._current = (certificate, context)


class RecordingIssuer:
    """The certificate seam for tests: ``issued`` when set, else a refusal."""

    def __init__(self, issued: Issued | None = None,
                 error: str = "no certificate in the test suite") -> None:
        self.issued = issued
        self.error = error
        self.calls = 0

    def __call__(self) -> Issued:
        self.calls += 1
        if self.issued is None:
            raise CertError(self.error)
        return self.issued
