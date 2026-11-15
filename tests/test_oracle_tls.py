"""The web page's certificate from Tailscale (IDEA_026, ADR-008 D19-D24).

``oracle/tls`` owns the certificate the page serves: asking Tailscale's CLI
for it through stdout, checking what came back, keeping it owner-only under
the oracle home, and deciding when it is due again.

**Nothing here runs ``tailscale``.** Every call goes through a runner a test
hands in, and the certificates are the self-signed fixtures under
``tests/fixtures/tls``, made once with ``openssl`` for the made-up name
``mac.example.ts.net``. No clock here is the wall clock: each test names the
time it runs at, against the fixtures' own dates.
"""

from __future__ import annotations

import json
import ssl
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from invariant.core.errors import StateError
from invariant.oracle import home as oracle_home_module
from invariant.oracle import tls, web
from invariant.oracle.tls import (
    CERT_FILE,
    RENEW_BEFORE,
    RETRY_MISSING,
    RETRY_RENEW,
    CertError,
    Certificates,
    Issued,
    RecordingIssuer,
    Served,
    cert_domain,
    cert_info,
    context_for,
    issue,
    read_certificate,
    split_pem,
)

FIXTURES = Path(__file__).parent / "fixtures" / "tls"
NAME = "mac.example.ts.net"
DAY = 86400.0


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def issued(which: str = "a", name: str = NAME, key: str | None = None) -> Issued:
    return Issued(name, fixture(f"{which}.crt"), fixture(key or f"{which}.key"))


def epoch(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


#: When each fixture expires, as ``openssl x509 -enddate`` printed it.
A_EXPIRES = epoch("2036-10-19T19:45:44")
B_EXPIRES = epoch("2126-09-28T19:45:44")

#: A moment well inside fixture a's life.
EARLY = epoch("2027-01-01T00:00:00")


class Clock:
    def __init__(self, now: float = EARLY) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Runner:
    """``tailscale`` as a script: each argv's answer, and what was asked."""

    def __init__(self, answers: dict[str, tuple[int, str]]) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(argv)
        return self.answers.get(argv[1], (1, "unexpected"))


def status_json(domains=(NAME,), dns: str | None = NAME + ".") -> str:
    body: dict = {"Self": {"DNSName": dns} if dns is not None else {}}
    if domains is not None:
        body["CertDomains"] = list(domains)
    return json.dumps(body)


# -- reading what Tailscale prints ----------------------------------------------------


class TestSplitPem:
    def test_the_certificate_then_the_key_as_tailscale_prints_both_to_stdout(self):
        chain, key = split_pem(fixture("a.crt") + fixture("a.key"))
        assert chain.startswith("-----BEGIN CERTIFICATE-----")
        assert "PRIVATE KEY" not in chain
        assert key.startswith("-----BEGIN PRIVATE KEY-----")

    def test_the_order_and_any_chatter_around_the_blocks_do_not_matter(self):
        text = "Wrote nothing\n" + fixture("a-ec.key") + "note\n" + fixture("a.crt")
        chain, key = split_pem(text)
        assert key.startswith("-----BEGIN EC PRIVATE KEY-----")
        assert chain.count("BEGIN CERTIFICATE") == 1

    def test_a_chain_keeps_every_certificate_in_order(self):
        chain, _ = split_pem(fixture("a.crt") + fixture("b.crt") + fixture("a.key"))
        assert chain.count("BEGIN CERTIFICATE") == 2
        assert chain.index(fixture("a.crt").strip()) < chain.index(fixture("b.crt").strip())

    def test_a_four_certificate_chain_with_the_key_last_as_tailscale_prints_it(self):
        """Leaf, intermediate, root, cross-signed root, then the key."""
        blocks = [fixture(n) for n in ("a.crt", "c.crt", "b.crt", "c.crt")]
        chain, key = split_pem("".join(blocks) + fixture("a.key"))
        assert chain.count("BEGIN CERTIFICATE") == 4 and "PRIVATE KEY" not in chain
        assert chain.startswith(blocks[0].strip())
        assert key.startswith("-----BEGIN PRIVATE KEY-----")
        # the leaf, not an issuer, is the one whose name and expiry are read
        info = cert_info(chain)
        assert info.names == (NAME,) and info.not_after == A_EXPIRES

    def test_a_four_certificate_chain_is_kept_whole_leaf_first_key_last(self, tmp_path):
        blocks = [fixture(n) for n in ("a.crt", "c.crt", "b.crt", "c.crt")]
        chain = "".join(blocks)
        text = chain + fixture("a.key")
        parts, key = split_pem(text)
        issuer = RecordingIssuer(Issued(NAME, parts, key))
        got = Certificates(tmp_path, issuer, Clock()).ensure()
        assert got.name == NAME and got.not_after == A_EXPIRES
        body = (tmp_path / CERT_FILE).read_text()
        assert body.count("BEGIN CERTIFICATE") == 4
        assert body.index("BEGIN CERTIFICATE") < body.index("PRIVATE KEY")
        assert body.rindex("END CERTIFICATE") < body.index("PRIVATE KEY")
        assert body.startswith(blocks[0].strip())

    def test_no_certificate_is_an_error(self):
        with pytest.raises(CertError, match="no certificate"):
            split_pem(fixture("a.key"))

    def test_no_key_is_an_error(self):
        with pytest.raises(CertError, match="no private key"):
            split_pem(fixture("a.crt"))

    def test_two_keys_are_an_error(self):
        with pytest.raises(CertError, match="more than one private key"):
            split_pem(fixture("a.crt") + fixture("a.key") + fixture("b.key"))


class TestCertInfo:
    def test_the_name_and_the_expiry_of_a_utc_time(self):
        info = cert_info(fixture("a.crt"))
        assert info.names == (NAME,)
        assert info.not_after == A_EXPIRES

    def test_a_generalized_time_past_2049(self):
        assert cert_info(fixture("b.crt")).not_after == B_EXPIRES

    def test_the_first_certificate_of_a_chain_is_the_one_read(self):
        info = cert_info(fixture("c.crt") + fixture("a.crt"))
        assert info.names == ("renamed.example.ts.net",)

    def test_garbage_is_an_error_not_a_crash(self):
        body = "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n"
        with pytest.raises(CertError, match="cannot read the certificate"):
            cert_info(body)

    def test_a_truncated_certificate_is_an_error(self):
        der = ssl.PEM_cert_to_DER_cert(fixture("a.crt"))
        with pytest.raises(CertError):
            cert_info(ssl.DER_cert_to_PEM_cert(der[:200]))

    def test_the_der_reader_refuses_bad_lengths(self):
        with pytest.raises(ValueError):
            tls._tlv(b"\x30\x85\x00\x00\x00\x00\x01", 0, 7)
        with pytest.raises(ValueError):
            tls._tlv(b"\x30", 0, 1)
        with pytest.raises(ValueError):
            tls._tlv(b"\x30\x05\x00", 0, 3)

    def test_a_time_that_is_not_a_time(self):
        with pytest.raises(ValueError):
            tls._time(b"\x02\x01\x00", 0x02, 2, 3)
        with pytest.raises(ValueError):
            tls._time(b"20260101", 0x18, 0, 8)

    def test_a_certificate_without_names(self, monkeypatch):
        monkeypatch.setattr(tls, "_SAN", b"\x00\x00\x00")
        assert cert_info(fixture("a.crt")).names == ()


class TestCertDomain:
    def test_the_cert_domain_without_its_trailing_dot(self):
        assert cert_domain(status_json(domains=[NAME + "."])) == (NAME, True)

    def test_else_the_magicdns_name(self):
        assert cert_domain(status_json(domains=None)) == (NAME, False)
        assert cert_domain(status_json(domains=[])) == (NAME, False)

    def test_neither_is_none(self):
        assert cert_domain(status_json(domains=None, dns=None)) == (None, False)
        assert cert_domain(status_json(domains=None, dns="")) == (None, False)

    def test_not_json_is_an_error(self):
        with pytest.raises(CertError, match="status --json"):
            cert_domain("{nope")
        with pytest.raises(CertError, match="status --json"):
            cert_domain("[1]")

    def test_an_answer_that_is_not_json_is_reported_by_its_first_line(self):
        with pytest.raises(CertError) as caught:
            cert_domain("The Tailscale GUI failed to start.\nsecond line\n")
        assert "said: The Tailscale GUI failed to start." in str(caught.value)
        assert "second line" not in str(caught.value)
        with pytest.raises(CertError, match="said: no output"):
            cert_domain("  ")


# -- asking Tailscale -----------------------------------------------------------------


BIN = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


class TestIssue:
    def test_one_status_and_one_cert_call_both_through_stdout(self):
        run = Runner({"status": (0, status_json()),
                      "cert": (0, fixture("a.crt") + fixture("a.key"))})
        got = issue(run, [BIN])
        assert got.name == NAME
        assert "BEGIN CERTIFICATE" in got.certificate and "PRIVATE KEY" in got.key
        assert run.calls == [
            [BIN, "status", "--json"],
            [BIN, "cert", "--cert-file", "-", "--key-file", "-", NAME],
        ]

    def test_it_reuses_the_discovery_lookup_with_the_app_bundle(self, monkeypatch):
        monkeypatch.setattr(web.shutil, "which", lambda name: None)
        monkeypatch.setattr(web.os.path, "isfile", lambda path: path == web.MACOS_APP_BINARY)
        run = Runner({"status": (0, status_json()),
                      "cert": (0, fixture("a.crt") + fixture("a.key"))})
        issue(run)
        assert run.calls[0][0] == web.MACOS_APP_BINARY

    def test_no_cli_at_all(self):
        with pytest.raises(CertError, match="no Tailscale CLI"):
            issue(Runner({}), [])

    def test_a_status_that_fails_tries_the_next_binary(self):
        class TwoBinaries(Runner):
            def __call__(self, argv):
                self.calls.append(argv)
                if argv[0] == "/first":
                    return 1, "failed to connect to local tailscaled\nmore"
                return {"status": (0, status_json()),
                        "cert": (0, fixture("a.crt") + fixture("a.key"))}[argv[1]]

        run = TwoBinaries({})
        assert issue(run, ["/first", BIN]).name == NAME

    def test_every_status_failing_names_why(self):
        run = Runner({"status": (1, "failed to connect to local tailscaled\nmore")})
        with pytest.raises(CertError, match="failed to connect to local tailscaled$"):
            issue(run, [BIN])

    def test_no_name_at_all(self):
        run = Runner({"status": (0, status_json(domains=None, dns=None))})
        with pytest.raises(CertError, match="no MagicDNS name"):
            issue(run, [BIN])
        assert len(run.calls) == 1

    def test_a_cert_refusal_with_https_off_says_where_to_turn_it_on(self):
        run = Runner({"status": (0, status_json(domains=None)),
                      "cert": (1, "your Tailscale account does not support getting TLS certs")})
        with pytest.raises(CertError) as caught:
            issue(run, [BIN])
        assert "does not support getting TLS certs" in str(caught.value)
        assert "HTTPS Certificates" in str(caught.value)
        assert "admin console" in str(caught.value)

    def test_a_cert_refusal_with_https_on_is_its_own_message(self):
        run = Runner({"status": (0, status_json()), "cert": (1, "acme: rate limited")})
        with pytest.raises(CertError) as caught:
            issue(run, [BIN])
        assert "acme: rate limited" in str(caught.value)
        assert "admin console" not in str(caught.value)

    def test_a_cert_call_that_printed_no_key(self):
        run = Runner({"status": (0, status_json()), "cert": (0, fixture("a.crt"))})
        with pytest.raises(CertError, match="no private key"):
            issue(run, [BIN])


# -- keeping it ----------------------------------------------------------------------


class TestWritePrivate:
    def test_it_is_born_owner_only_and_replaces_atomically(self, tmp_path):
        path = tmp_path / "web" / "tls" / "x.pem"
        oracle_home_module.write_private(path, b"one")
        oracle_home_module.write_private(path, b"two")
        assert path.read_bytes() == b"two"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert [p.name for p in path.parent.iterdir()] == ["x.pem"]

    def test_a_stale_temporary_file_is_replaced_not_reused(self, tmp_path):
        path = tmp_path / "x.pem"
        stale = tmp_path / ".x.pem.tmp"
        stale.write_text("old")
        stale.chmod(0o644)
        oracle_home_module.write_private(path, b"new")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert not stale.exists()

    def test_a_write_that_fails_is_a_state_error_and_leaves_no_temporary(self, tmp_path):
        target = tmp_path / "x.pem"
        target.mkdir()
        (target / "child").write_text("x")
        with pytest.raises(StateError, match="cannot write"):
            oracle_home_module.write_private(target, b"data")
        assert not (tmp_path / ".x.pem.tmp").exists()


class TestCertificates:
    def manager(self, home: Path, issuer=None, clock: Clock | None = None):
        issuer = issuer if issuer is not None else RecordingIssuer(issued("a"))
        return Certificates(home, issuer, clock or Clock()), issuer

    def test_with_none_stored_it_asks_and_keeps_one_file_owner_only(self, tmp_path):
        certs, issuer = self.manager(tmp_path)
        got = certs.ensure()
        assert issuer.calls == 1 and certs.error is None
        assert got.name == NAME and got.not_after == A_EXPIRES
        path = tmp_path / CERT_FILE
        assert got.path == path
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o700
        assert sorted(p.name for p in path.parent.iterdir()) == ["oracle.pem"]
        body = path.read_text()
        assert "BEGIN CERTIFICATE" in body and "PRIVATE KEY" in body

    def test_a_valid_one_outside_the_window_is_not_asked_for_again(self, tmp_path):
        certs, issuer = self.manager(tmp_path)
        first = certs.ensure()
        assert certs.ensure() == first
        assert issuer.calls == 1

    def test_inside_the_window_it_renews(self, tmp_path):
        clock = Clock()
        certs, issuer = self.manager(tmp_path, clock=clock)
        certs.ensure()
        clock.now = A_EXPIRES - RENEW_BEFORE + 1
        issuer.issued = issued("b")
        got = certs.ensure()
        assert issuer.calls == 2 and got.not_after == B_EXPIRES
        assert read_certificate(tmp_path).fingerprint == got.fingerprint

    def test_the_same_certificate_again_waits_an_hour_before_asking_again(self, tmp_path):
        clock = Clock(A_EXPIRES - 10 * DAY)
        certs, issuer = self.manager(tmp_path, clock=clock)
        certs.ensure()
        certs.ensure()
        assert issuer.calls == 1
        clock.now += RETRY_RENEW
        certs.ensure()
        assert issuer.calls == 2

    def test_a_failed_renewal_keeps_the_one_it_has_and_says_why(self, tmp_path):
        clock = Clock()
        certs, issuer = self.manager(tmp_path, clock=clock)
        kept = certs.ensure()
        clock.now = A_EXPIRES - 5 * DAY
        issuer.issued = None
        assert certs.ensure() == kept
        assert "no certificate in the test suite" in certs.error
        certs.ensure()
        assert issuer.calls == 2
        clock.now += RETRY_RENEW
        certs.ensure()
        assert issuer.calls == 3

    def test_none_at_all_retries_sooner(self, tmp_path):
        clock = Clock()
        certs, issuer = self.manager(tmp_path, RecordingIssuer(), clock)
        assert certs.ensure() is None and certs.error
        clock.now += RETRY_MISSING - 1
        certs.ensure()
        assert issuer.calls == 1
        clock.now += 1
        certs.ensure()
        assert issuer.calls == 2

    def test_a_success_clears_the_error(self, tmp_path):
        clock = Clock()
        certs, issuer = self.manager(tmp_path, RecordingIssuer(), clock)
        certs.ensure()
        issuer.issued = issued("a")
        clock.now += RETRY_MISSING
        assert certs.ensure() is not None and certs.error is None

    def test_an_expired_one_is_not_served(self, tmp_path):
        clock = Clock()
        certs, issuer = self.manager(tmp_path, clock=clock)
        certs.ensure()
        issuer.issued = None
        clock.now = A_EXPIRES + 1
        assert certs.ensure() is None
        assert read_certificate(tmp_path) is not None

    def test_a_key_that_does_not_match_is_refused_and_the_old_one_kept(self, tmp_path):
        clock = Clock()
        certs, issuer = self.manager(tmp_path, clock=clock)
        kept = certs.ensure()
        clock.now = A_EXPIRES - 5 * DAY
        issuer.issued = issued("b", key="a.key")
        assert certs.ensure() == kept
        assert "cannot load" in certs.error
        assert sorted(p.name for p in (tmp_path / CERT_FILE).parent.iterdir()) == ["oracle.pem"]

    def test_a_certificate_for_another_name_is_refused(self, tmp_path):
        certs, _ = self.manager(tmp_path, RecordingIssuer(issued("c", name=NAME)))
        assert certs.ensure() is None
        assert "renamed.example.ts.net" in certs.error and NAME in certs.error

    def test_an_expired_certificate_from_tailscale_is_refused(self, tmp_path):
        certs, _ = self.manager(tmp_path, clock=Clock(A_EXPIRES + 1))
        assert certs.ensure() is None and "expired" in certs.error

    def test_a_home_that_cannot_be_written_is_an_error_not_a_crash(self, tmp_path):
        (tmp_path / "web").write_text("not a directory")
        certs, _ = self.manager(tmp_path)
        assert certs.ensure() is None and "web" in certs.error

    def test_the_context_it_builds_serves_the_certificate(self, tmp_path):
        certs, _ = self.manager(tmp_path)
        context = certs.context(certs.ensure())
        assert isinstance(context, ssl.SSLContext)
        assert context.minimum_version >= ssl.TLSVersion.TLSv1_2

    def test_a_context_that_cannot_be_built_is_recorded(self, tmp_path):
        certs, _ = self.manager(tmp_path)
        got = certs.ensure()
        got.path.write_text("garbage")
        with pytest.raises(CertError):
            certs.context(got)
        assert "cannot load" in certs.error


class TestReadCertificate:
    def test_nothing_stored(self, tmp_path):
        assert read_certificate(tmp_path) is None

    def test_a_stored_file_that_is_not_a_certificate(self, tmp_path):
        path = tmp_path / CERT_FILE
        path.parent.mkdir(parents=True)
        path.write_text("garbage")
        assert read_certificate(tmp_path) is None

    def test_a_stored_certificate_naming_nothing(self, tmp_path, monkeypatch):
        path = tmp_path / CERT_FILE
        path.parent.mkdir(parents=True)
        path.write_text(fixture("a.crt") + fixture("a.key"))
        monkeypatch.setattr(tls, "_SAN", b"\x00\x00\x00")
        assert read_certificate(tmp_path) is None

    def test_an_unreadable_path(self, tmp_path):
        (tmp_path / CERT_FILE).mkdir(parents=True)
        assert read_certificate(tmp_path) is None


class TestServed:
    def test_a_renewal_swaps_the_certificate_and_the_context_in_place(self, tmp_path):
        clock = Clock()
        certs = Certificates(tmp_path, RecordingIssuer(issued("a")), clock)
        first = certs.ensure()
        served = Served(first, certs.context(first))
        assert served.name == NAME and served.not_after == A_EXPIRES
        old = served.context
        certs.issue.issued = issued("b")
        clock.now = A_EXPIRES - DAY
        second = certs.ensure()
        served.renew(second, certs.context(second))
        assert served.certificate == second and served.context is not old
        assert served.not_after == B_EXPIRES


def test_context_for_a_missing_file():
    with pytest.raises(CertError, match="cannot load"):
        context_for(Path("/nonexistent/oracle.pem"))


def test_the_recording_issuer():
    recorder = RecordingIssuer()
    with pytest.raises(CertError, match="test suite"):
        recorder()
    recorder.issued = issued("a")
    assert recorder().name == NAME and recorder.calls == 2


def test_expires_text():
    assert tls.expires_text(A_EXPIRES) == "2036-10-19T19:45:44Z"


def test_the_tls_module_imports_no_host_and_no_toolkit():
    import re

    source = Path(tls.__file__).read_text()
    imports = re.findall(r"^\s*(?:from|import)\s+(\S+)", source, re.MULTILINE)
    assert not [i for i in imports if "hosts" in i or "textual" in i or "tui" in i]
