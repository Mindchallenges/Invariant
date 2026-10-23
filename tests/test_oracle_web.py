"""The oracle's web dashboard over Tailscale (IDEA_023 increment 1, ADR-008).

Four layers, each tested where it lives:

* ``oracle/devices`` — the paired-device store: one-time pairing codes, the
  long-lived per-device tokens kept only as hashes, list and revoke;
* ``oracle/web`` — which address the server may bind (only a Tailscale
  address, else loopback when allowed, else a refusal), the view the page
  draws, and :class:`WebApp`, the request handling, tested without a socket;
* the page's own script, its refresh loop driven under ``node`` when the
  machine has one;
* the CLI, ``invariant oracle web`` and its ``pair``, ``devices`` and
  ``revoke``.

**Nothing here binds a Tailscale address or asks the real ``tailscale``.**
The CLI reaches the address discovery through ``_oracle_web_addresses`` and
the server through ``_oracle_web_server``; ``conftest.py`` replaces both for
every test, and its program guard refuses ``tailscale``. The one test that
opens a socket binds loopback on an ephemeral port.
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

import pytest
import test_oracle_scan

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core.errors import InvariantError, StateError, UsageError
from invariant.oracle import dashboard, panel, web, webpage
from invariant.oracle.devices import (
    CODE_LENGTH,
    COOKIE_NAME,
    DEVICES_FILE,
    MAX_FAILED_REDEEMS,
    DeviceStore,
    PairingError,
    display_code,
    normalise_code,
)
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectIndex
from invariant.oracle.recommend import assess
from invariant.oracle.scan import ScanCache, scan_all
from invariant.oracle.watcher import SNAPSHOT_FILE, STALE_INTERVALS, Watcher
from invariant.oracle.web import (
    LOOPBACK,
    NoTailnetError,
    Request,
    WebApp,
    WebServer,
    choose_bind,
    discover_addresses,
    host_allowed,
    is_tailnet,
    parse_ifconfig,
    retry_delay,
    tailnet_addresses,
    url_for,
    view,
)

machine_world = test_oracle_scan.machine_world
tree_fingerprint = test_oracle_scan.tree_fingerprint

TS4 = "100.101.102.103"
TS6 = "fd7a:115c:a1e0::1234:5678"


class Clock:
    def __init__(self, now: float = 1_792_640_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return DeviceStore(tmp_path / "home", clock=clock)


# -- devices: pairing codes and tokens -------------------------------------


class TestPairing:
    def test_a_code_is_issued_short_and_unambiguous(self, store, clock):
        pairing = store.issue("phone", ttl=300)
        assert len(pairing.code) == CODE_LENGTH
        assert not set(pairing.code) & set("01IO")
        assert pairing.expires == clock.now + 300
        assert pairing.name == "phone"
        assert store.pending() == 1

    def test_the_code_is_stored_only_as_a_hash(self, store):
        pairing = store.issue("phone", ttl=300)
        text = (store.home / DEVICES_FILE).read_text()
        assert pairing.code not in text
        assert "sha256:" in text

    def test_redeeming_stores_a_device_and_returns_a_token_once(self, store):
        pairing = store.issue("phone", ttl=300)
        device, token = store.redeem(pairing.code)
        assert device.name == "phone"
        assert len(token) >= 40
        assert store.verify(token) == device
        assert store.pending() == 0
        text = (store.home / DEVICES_FILE).read_text()
        assert token not in text

    def test_a_code_is_single_use(self, store):
        pairing = store.issue("phone", ttl=300)
        store.redeem(pairing.code)
        with pytest.raises(PairingError):
            store.redeem(pairing.code)

    def test_a_code_expires(self, store, clock):
        pairing = store.issue("phone", ttl=300)
        clock.now += 301
        with pytest.raises(PairingError, match="not valid"):
            store.redeem(pairing.code)
        assert store.pending() == 0

    def test_a_code_is_read_forgivingly(self, store):
        pairing = store.issue("phone", ttl=300)
        spaced = f" {pairing.code[:5].lower()}-{pairing.code[5:]} "
        device, _ = store.redeem(spaced)
        assert device.name == "phone"

    def test_a_code_is_shown_in_two_groups_that_redeem_as_one(self, store):
        assert display_code("AVR7YTBRDE") == "AVR7Y-TBRDE"
        pairing = store.issue("phone", ttl=300)
        assert normalise_code(display_code(pairing.code)) == pairing.code
        device, _ = store.redeem(display_code(pairing.code))
        assert device.name == "phone"

    def test_too_many_wrong_codes_cancel_every_pending_code(self, store):
        pairing = store.issue("phone", ttl=300)
        for _ in range(MAX_FAILED_REDEEMS):
            with pytest.raises(PairingError):
                store.redeem("WRONGCODE2")
        assert store.pending() == 0
        with pytest.raises(PairingError):
            store.redeem(pairing.code)

    def test_a_good_redeem_resets_the_failure_count(self, store):
        for _ in range(MAX_FAILED_REDEEMS - 1):
            with pytest.raises(PairingError):
                store.redeem("WRONGCODE2")
        store.redeem(store.issue("phone", ttl=300).code)
        pairing = store.issue("tablet", ttl=300)
        with pytest.raises(PairingError):
            store.redeem("WRONGCODE2")
        assert store.redeem(pairing.code)[0].name == "tablet"

    def test_an_empty_code_is_refused(self, store):
        with pytest.raises(PairingError):
            store.redeem("   ")

    def test_a_non_positive_ttl_is_refused(self, store):
        with pytest.raises(UsageError):
            store.issue("phone", ttl=0)

    def test_a_blank_name_is_refused(self, store):
        with pytest.raises(UsageError):
            store.issue("  ", ttl=60)


class TestTokens:
    def test_a_wrong_or_missing_token_verifies_nothing(self, store):
        store.redeem(store.issue("phone", ttl=300).code)
        assert store.verify("not-the-token") is None
        assert store.verify("") is None
        assert store.verify(None) is None
        assert store.verify("x" * 1000) is None

    def test_no_store_verifies_nothing_and_writes_nothing(self, store):
        assert store.verify("anything") is None
        assert store.devices() == []
        assert not store.home.exists()

    def test_revoke_drops_one_device_and_keeps_the_rest(self, store):
        phone, phone_token = store.redeem(store.issue("phone", ttl=300).code)
        tablet, tablet_token = store.redeem(store.issue("tablet", ttl=300).code)
        assert phone.id != tablet.id
        assert store.revoke(phone.id) == phone
        assert store.verify(phone_token) is None
        assert store.verify(tablet_token) == tablet
        assert [d.id for d in store.devices()] == [tablet.id]

    def test_revoking_an_unknown_device_is_refused(self, store):
        with pytest.raises(UsageError, match="no paired device"):
            store.revoke("nope")

    def test_the_device_list_says_when_but_not_the_hash(self, store):
        device, _ = store.redeem(store.issue("phone", ttl=300).code)
        listed = device.to_dict()
        assert set(listed) == {"id", "name", "paired"}
        assert listed["paired"].endswith("+00:00")

    def test_the_store_is_private_to_the_user(self, store):
        store.redeem(store.issue("phone", ttl=300).code)
        assert (store.home / DEVICES_FILE).stat().st_mode & 0o077 == 0

    def test_a_corrupt_store_fails_closed_on_verify_and_loud_elsewhere(self, store):
        path = store.home / DEVICES_FILE
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        assert store.verify("anything") is None
        with pytest.raises(StateError):
            store.devices()

    def test_a_newer_version_is_refused_not_rewritten(self, store):
        path = store.home / DEVICES_FILE
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"version": 99, "devices": []}))
        with pytest.raises(StateError, match="version 99"):
            store.issue("phone", ttl=60)
        assert json.loads(path.read_text())["version"] == 99

    def test_a_malformed_shape_is_refused(self, store):
        path = store.home / DEVICES_FILE
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"version": 1, "devices": "nope"}))
        with pytest.raises(StateError, match="malformed"):
            store.devices()
        path.write_text(json.dumps([1, 2]))
        with pytest.raises(StateError, match="not a JSON object"):
            store.devices()

    def test_unknown_keys_survive_a_write(self, store):
        store.redeem(store.issue("phone", ttl=300).code)
        path = store.home / DEVICES_FILE
        document = json.loads(path.read_text())
        document["later"] = "kept"
        document["devices"][0]["later"] = "kept too"
        path.write_text(json.dumps(document))
        store.issue("tablet", ttl=60)
        after = json.loads(path.read_text())
        assert after["later"] == "kept"
        assert after["devices"][0]["later"] == "kept too"


# -- where the server may listen ---------------------------------------------


class TestAddresses:
    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            (TS4, True),
            ("100.64.0.1", True),
            ("100.127.255.254", True),
            ("100.63.255.255", False),
            ("100.128.0.1", False),
            (TS6, True),
            ("fd7a:115c:a1e1::1", False),
            ("192.168.1.10", False),
            ("127.0.0.1", False),
            ("0.0.0.0", False),
            ("::", False),
            ("not an address", False),
        ],
    )
    def test_the_tailnet_ranges(self, address, expected):
        assert is_tailnet(address) is expected

    def test_only_tailnet_addresses_are_kept_v4_first_without_repeats(self):
        found = tailnet_addresses([TS6, "192.168.1.2", TS4, TS4, "fe80::1%en0", f"{TS6}%utun3"])
        assert found == [TS4, TS6]

    def test_ifconfig_is_read_only_on_tunnel_interfaces(self):
        text = (
            "en0: flags=8863<UP,BROADCAST> mtu 1500\n"
            "\tinet 100.70.1.1 netmask 0xffc00000 broadcast 100.127.255.255\n"
            "utun4: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1280\n"
            f"\tinet {TS4} --> {TS4} netmask 0xffffffff\n"
            f"\tinet6 {TS6} prefixlen 48\n"
            "\tinet6 fe80::1%utun4 prefixlen 64 scopeid 0x13\n"
            "tailscale0: flags=4305<UP,POINTOPOINT,RUNNING,NOARP,MULTICAST>  mtu 1280\n"
            "        inet 100.90.0.2  netmask 255.255.255.255  destination 100.90.0.2\n"
        )
        assert parse_ifconfig(text) == [TS4, TS6, "fe80::1%utun4", "100.90.0.2"]

    def test_discovery_asks_tailscale_first(self):
        calls = []

        def run(argv):
            calls.append(argv)
            return 0, f"{TS4}\n{TS6}\n"

        assert discover_addresses(run, binaries=["/opt/ts"]) == [TS4, TS6]
        assert calls == [["/opt/ts", "ip"]]

    def test_discovery_falls_back_to_ifconfig(self):
        calls = []

        def run(argv):
            calls.append(argv)
            if argv[0] == "/opt/ts":
                return 1, "Tailscale is stopped."
            return 0, f"utun2: flags=1\n\tinet {TS4} --> {TS4}\n"

        assert discover_addresses(run, binaries=["/opt/ts"]) == [TS4]
        assert calls == [["/opt/ts", "ip"], ["ifconfig"]]

    def test_discovery_without_tailscale_or_ifconfig_finds_nothing(self):
        assert discover_addresses(lambda argv: (127, "not found"), binaries=[]) == []

    def test_a_tailscale_answer_outside_the_ranges_is_not_trusted(self):
        def run(argv):
            if argv[0] == "/opt/ts":
                return 0, "192.168.1.5\n"
            return 0, ""

        assert discover_addresses(run, binaries=["/opt/ts"]) == []

    def test_the_tailscale_binaries_are_looked_for(self, monkeypatch, tmp_path):
        app = tmp_path / "Tailscale"
        app.write_text("")
        monkeypatch.setattr(web, "MACOS_APP_BINARY", str(app))
        found = web.tailscale_binaries(which=lambda name: "/usr/local/bin/tailscale")
        assert found == ["/usr/local/bin/tailscale", str(app)]
        assert web.tailscale_binaries(which=lambda name: None) == [str(app)]

    def test_the_real_runner_reports_a_missing_program(self, tmp_path):
        status, text = web.run_program([str(tmp_path / "missing")])
        assert status == 127
        assert text

    def test_the_real_runner_runs_a_program(self):
        status, text = web.run_program(["/bin/echo", TS4])
        assert (status, text.strip()) == (0, TS4)

    def test_the_runner_gives_a_program_a_terminal_type_when_it_has_none(self, monkeypatch):
        # The Tailscale app's CLI starts its GUI when TERM is missing, and
        # launchd sets none (BUG_084).
        seen = {}

        def fake_run(argv, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(argv, 0, "out", "")

        monkeypatch.setattr(web.subprocess, "run", fake_run)
        monkeypatch.delenv("TERM", raising=False)
        monkeypatch.setenv("INVARIANT_TEST_MARK", "kept")
        web.run_program(["true"])
        assert seen["env"]["TERM"] == web.PROGRAM_TERM == "dumb"
        assert seen["env"]["INVARIANT_TEST_MARK"] == "kept"
        assert "TERM" not in os.environ
        monkeypatch.setenv("TERM", "")
        web.run_program(["true"])
        assert seen["env"]["TERM"] == "dumb"

    def test_the_runner_keeps_a_terminal_type_it_was_given(self, monkeypatch):
        seen = {}

        def fake_run(argv, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(argv, 0, "out", "")

        monkeypatch.setattr(web.subprocess, "run", fake_run)
        monkeypatch.setenv("TERM", "xterm-256color")
        web.run_program(["true"])
        assert seen["env"]["TERM"] == "xterm-256color"


class TestChooseBind:
    def test_the_tailscale_addresses_are_bound(self):
        assert choose_bind([TS4, TS6], allow_loopback=False) == [TS4, TS6]

    def test_they_win_over_loopback_even_when_it_is_allowed(self):
        assert choose_bind([TS4], allow_loopback=True) == [TS4]

    def test_no_tailscale_is_a_refusal(self):
        with pytest.raises(NoTailnetError, match="no Tailscale address") as caught:
            choose_bind([], allow_loopback=False)
        assert "--allow-loopback" in str(caught.value)

    def test_no_tailscale_with_loopback_allowed_binds_loopback_only(self):
        assert choose_bind([], allow_loopback=True) == [LOOPBACK]

    def test_never_the_wildcard(self):
        # Even a discovery that somehow returned the wildcard binds nothing.
        with pytest.raises(NoTailnetError):
            choose_bind(tailnet_addresses(["0.0.0.0", "::"]), allow_loopback=False)

    def test_urls(self):
        assert url_for(TS4, 7341) == f"http://{TS4}:7341/"
        assert url_for(TS6, 7341, "/pair?code=X") == f"http://[{TS6}]:7341/pair?code=X"


class TestHostHeader:
    def test_a_bound_address_or_a_magicdns_name_is_accepted(self):
        bound = [TS4, TS6]
        assert host_allowed(f"{TS4}:7341", bound)
        assert host_allowed(f"[{TS6}]:7341", bound)
        assert host_allowed("my-mac.tail1234.ts.net:7341", bound)
        assert host_allowed("MY-MAC.TAIL1234.TS.NET", bound)

    def test_anything_else_is_refused(self):
        bound = [TS4]
        assert not host_allowed("evil.example:7341", bound)
        assert not host_allowed("100.101.102.104:7341", bound)
        assert not host_allowed("localhost:7341", bound)
        assert not host_allowed("", bound)
        assert not host_allowed(None, bound)
        assert not host_allowed("[::1", bound)

    def test_loopback_names_only_when_loopback_is_bound(self):
        assert host_allowed("localhost:7341", [LOOPBACK])
        assert host_allowed("127.0.0.1:7341", [LOOPBACK])
        assert not host_allowed("my-mac.tail1234.ts.net", [LOOPBACK])


# -- the view the page draws -------------------------------------------------


def _loaded(document, source=dashboard.SOURCE_SNAPSHOT, age=2.0, interval=5.0, banner=""):
    return dashboard.Loaded(document, source, interval, banner=banner, age=age)


def _document(world) -> dict:
    """The machine assessed at the fixture's own clock, which every age in it is from."""
    now = test_oracle_scan.NOW
    snapshots = scan_all(ProjectIndex(oracle_home()), clock=lambda: now,
                         cache=ScanCache(probe=world["probe"]))
    return assess(snapshots, now)


class TestView:
    """``web.view`` is the panel's view; ``test_oracle_panel`` pins the panel itself."""

    def test_the_machine_is_grouped_by_attention(self, machine_world):
        shown = view(_loaded(_document(machine_world)))
        assert [g["id"] for g in shown["groups"]][:2] == ["needs", "problems"]
        assert [t["id"] for t in shown["tiles"]] == ["needs", "problems", "working",
                                                      "leftovers"]
        needs = shown["groups"][0]["cards"]
        assert any(c["row"].endswith("|a-live") for c in needs)
        assert any(c["row"].endswith("|g-rev") for c in needs)

    def test_a_waiting_session_needs_you_and_has_no_button(self, machine_world):
        shown = view(_loaded(_document(machine_world)), acting=True)
        waiting = next(c for c in shown["groups"][0]["cards"] if c["row"].endswith("|a-live"))
        assert waiting["button"] is None and waiting["tap"] is True
        assert waiting["chips"][0]["text"] == "Waiting for your permission"

    def test_orphan_hosts_and_leaked_worktrees_are_leftovers(self, machine_world):
        shown = view(_loaded(_document(machine_world)))
        assert shown["summary"]["leftovers"] >= 2
        hosts, folders = shown["leftovers"]["sections"]
        assert hosts["count"] >= 1 and folders["count"] == 1
        assert "1 left-over folder" in shown["leftovers_card"]["line"]
        shown_rows = [c["row"] for g in shown["groups"] for c in g["cards"]]
        assert not [r for r in shown_rows if "|worktree:" in r or "|host:" in r]

    def test_the_recommendation_is_a_step_with_its_command_to_copy(self, machine_world):
        shown = view(_loaded(_document(machine_world)), acting=True)
        rev = next(d for k, d in shown["details"].items() if k.endswith("|g-rev"))
        assert rev["step"]["copy"] == "invariant session merge --id g-rev"
        assert rev["step"]["button"] == {"text": "Merge…"}

    def test_an_orphan_host_with_an_alert_is_not_a_leftover(self):
        document = {"projects": [{"project": {"key": "k", "name": "p", "root": "/p"},
                                  "elapsed": 0.0,
                                  "rows": [{"kind": "host", "project": "k",
                                            "hosts": [{"host_session_id": "h"}],
                                            "signals": [{"kind": "waiting", "detail": "x",
                                                         "cause": "question",
                                                         "alert": True, "severity": "high"}],
                                            "recommendation": None}]}]}
        shown = view(_loaded(document))
        assert shown["summary"]["needs"] == 1 and shown["summary"]["leftovers"] == 0
        assert shown["leftovers_card"] is None

    def test_freshness_follows_the_snapshot_rule(self):
        fresh = web.freshness_fields(_loaded({"projects": []}, age=2.0), served_at=10.0)
        assert fresh == {
            "source": "snapshot", "age": 2.0, "interval": 5.0,
            "stale_after": STALE_INTERVALS * 5.0, "stale": False,
            "text": "updated 2s ago", "served_at": 10.0, "banner": "",
        }
        stale = web.freshness_fields(_loaded({"projects": []}, age=16.0), served_at=10.0)
        assert stale["stale"] is True
        assert "STALE" in stale["text"]

    def test_retry_delay_backs_off_and_caps(self):
        assert retry_delay(5.0, 0) == 5.0
        assert retry_delay(5.0, 1) == 10.0
        assert retry_delay(5.0, 2) == 20.0
        assert retry_delay(5.0, 10) == web.MAX_BACKOFF


class TestFreshnessText:
    def test_the_shared_wording(self):
        assert dashboard.freshness(3.4, 5.0) == ("updated 3s ago", False)
        text, stale = dashboard.freshness(15.1, 5.0)
        assert stale and "STALE" in text
        assert dashboard.freshness(15.0, 5.0)[1] is False
        text, stale = dashboard.freshness(None, 5.0)
        assert stale and "never" in text

    def test_load_carries_the_snapshots_age(self, machine_world):
        watcher = Watcher(ProjectIndex(oracle_home()), None,
                          cache=ScanCache(probe=machine_world["probe"]))
        watcher.tick()
        path = oracle_home() / SNAPSHOT_FILE
        old = time.time() - 4
        import os
        os.utime(path, (old, old))
        loaded = dashboard.load(oracle_home(), time.time(), lambda: {"projects": []})
        assert loaded.source == dashboard.SOURCE_SNAPSHOT
        assert 3.5 < loaded.age < 10
        scanned = dashboard.load(oracle_home() / "nowhere", time.time(), lambda: {"projects": []})
        assert scanned.age == 0.0


# -- the request handling ------------------------------------------------------


def _app(store, document=None, bound=(TS4,), clock=None, loads=None):
    document = document if document is not None else {"projects": []}

    def load():
        if loads is not None:
            loads.append(1)
        return _loaded(document)

    return WebApp(store, load, list(bound), clock=clock or Clock(), nonce=lambda: "N0NCE")


def _get(app, path, token=None, host=f"{TS4}:7341", headers=None):
    sent = {"host": host}
    if token:
        sent["cookie"] = f"{COOKIE_NAME}={token}"
    sent.update(headers or {})
    return app.handle(Request("GET", path, sent, b""))


def _post(app, path, body: bytes, host=f"{TS4}:7341", headers=None):
    sent = {"host": host, "content-type": "application/x-www-form-urlencoded"}
    sent.update(headers or {})
    return app.handle(Request("POST", path, sent, body))


def _paired(store):
    return store.redeem(store.issue("phone", ttl=300).code)[1]


class TestUnpaired:
    @pytest.mark.parametrize("path", ["/", "/api/state", "/anything", "/?x=1"])
    def test_an_unpaired_request_gets_nothing(self, store, machine_world, path):
        loads = []
        app = _app(store, _document(machine_world), loads=loads)
        response = _get(app, path)
        assert response.status == 401
        body = response.body.decode()
        assert "not paired" in body
        assert "a-live" not in body and "alpha" not in body
        assert loads == []

    def test_a_wrong_token_gets_nothing(self, store):
        _paired(store)
        response = _get(_app(store), "/api/state", token="forged")
        assert response.status == 401

    def test_a_revoked_token_gets_nothing(self, store):
        token = _paired(store)
        store.revoke(store.devices()[0].id)
        assert _get(_app(store), "/", token=token).status == 401

    def test_an_unknown_host_header_is_refused_before_anything(self, store):
        token = _paired(store)
        response = _get(_app(store), "/", token=token, host="evil.example")
        assert response.status == 421
        assert b"a-live" not in response.body

    def test_other_methods_are_refused(self, store):
        token = _paired(store)
        app = _app(store)
        for method in ("PUT", "DELETE", "PATCH"):
            response = app.handle(Request(method, "/", {"host": TS4, "cookie": f"{COOKIE_NAME}={token}"}, b""))
            assert response.status == 405

    def test_no_post_but_pairing(self, store):
        token = _paired(store)
        response = _post(_app(store), "/api/state", b"",
                         headers={"cookie": f"{COOKIE_NAME}={token}"})
        assert response.status == 405


class TestPairingOverHttp:
    def test_the_link_shows_a_form_and_redeems_nothing(self, store):
        pairing = store.issue("phone", ttl=300)
        response = _get(_app(store), f"/pair?code={pairing.code}")
        assert response.status == 200
        body = response.body.decode()
        assert f'value="{pairing.code}"' in body
        assert 'method="post"' in body
        assert store.pending() == 1

    def test_the_form_escapes_what_it_echoes(self, store):
        response = _get(_app(store), '/pair?code=%22%3E%3Cscript%3E')
        assert b"<script>" not in response.body

    def test_posting_the_code_sets_a_strict_http_only_cookie(self, store):
        pairing = store.issue("phone", ttl=300)
        response = _post(_app(store), "/pair", f"code={pairing.code}".encode())
        assert response.status == 303
        assert response.header("location") == "/"
        cookie = response.header("set-cookie")
        assert cookie.startswith(f"{COOKIE_NAME}=")
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
        token = cookie.split(";")[0].split("=", 1)[1]
        assert store.verify(token).name == "phone"

    def test_the_cookie_then_opens_the_page(self, store):
        pairing = store.issue("phone", ttl=300)
        cookie = _post(_app(store), "/pair", f"code={pairing.code}".encode()).header("set-cookie")
        token = cookie.split(";")[0].split("=", 1)[1]
        assert _get(_app(store), "/", token=token).status == 200

    def test_a_wrong_or_spent_code_is_refused(self, store):
        pairing = store.issue("phone", ttl=300)
        app = _app(store)
        assert _post(app, "/pair", b"code=WRONGWRONG").status == 403
        assert _post(app, "/pair", f"code={pairing.code}".encode()).status == 303
        response = _post(app, "/pair", f"code={pairing.code}".encode())
        assert response.status == 403
        assert response.header("set-cookie") is None

    def test_an_expired_code_is_refused(self, store, clock):
        pairing = store.issue("phone", ttl=60)
        clock.now += 61
        assert _post(_app(store, clock=clock), "/pair", f"code={pairing.code}".encode()).status == 403

    # "null" is what a browser sends for a cross-site form whose page runs a
    # strict referrer policy, as this page's own same-origin one does (BUG_079).
    @pytest.mark.parametrize("origin", ["http://evil.example", "null"])
    def test_a_cross_site_post_is_refused(self, store, origin):
        pairing = store.issue("phone", ttl=300)
        response = _post(_app(store), "/pair", f"code={pairing.code}".encode(),
                         headers={"origin": origin})
        assert response.status == 403
        assert store.pending() == 1

    def test_a_same_origin_post_is_accepted(self, store):
        pairing = store.issue("phone", ttl=300)
        response = _post(_app(store), "/pair", f"code={pairing.code}".encode(),
                         headers={"origin": f"http://{TS4}:7341"})
        assert response.status == 303

    def test_a_post_that_is_not_a_form_is_refused(self, store):
        pairing = store.issue("phone", ttl=300)
        response = _post(_app(store), "/pair", f"code={pairing.code}".encode(),
                         headers={"content-type": "application/json"})
        assert response.status == 415
        assert store.pending() == 1

    def test_a_bearer_header_works_as_well_as_the_cookie(self, store):
        token = _paired(store)
        response = _get(_app(store), "/api/state", headers={"authorization": f"Bearer {token}"})
        assert response.status == 200


class TestPaired:
    def test_the_page_is_self_contained(self, store):
        token = _paired(store)
        response = _get(_app(store), "/", token=token)
        assert response.status == 200
        assert response.header("content-type").startswith("text/html")
        body = response.body.decode()
        assert "<script" in body and 'nonce="N0NCE"' in body
        assert "cdn" not in body.lower()
        assert 'src="' not in body and 'href="http' not in body
        assert 'name="viewport"' in body
        csp = response.header("content-security-policy")
        assert "default-src 'none'" in csp and "'nonce-N0NCE'" in csp
        assert "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp

    def test_every_response_is_uncached_and_unsniffed(self, store):
        token = _paired(store)
        for response in (_get(_app(store), "/", token=token), _get(_app(store), "/")):
            assert response.header("cache-control") == "no-store"
            assert response.header("x-content-type-options") == "nosniff"
            assert response.header("referrer-policy") == "same-origin"

    def test_the_json_is_the_oracle_list_document_with_the_view(self, store, machine_world):
        token = _paired(store)
        document = _document(machine_world)
        response = _get(_app(store, document), "/api/state", token=token)
        assert response.status == 200
        assert response.header("content-type") == "application/json"
        body = json.loads(response.body)
        assert body["document"] == json.loads(json.dumps(document))
        assert body["view"]["groups"][0]["id"] == "needs"
        assert body["freshness"]["source"] == "snapshot"
        assert body["freshness"]["interval"] == 5.0
        assert body["freshness"]["stale"] is False

    def test_the_json_flags_a_stale_read(self, store):
        token = _paired(store)
        app = WebApp(store, lambda: _loaded({"projects": []}, age=60.0), [TS4],
                     clock=Clock(), nonce=lambda: "n")
        body = json.loads(_get(app, "/api/state", token=token).body)
        assert body["freshness"]["stale"] is True

    def test_a_read_that_fails_is_a_503_and_the_next_one_runs(self, store):
        token = _paired(store)
        calls = []

        def load():
            calls.append(1)
            if len(calls) == 1:
                raise InvariantError("the index is broken")
            return _loaded({"projects": []})

        app = WebApp(store, load, [TS4], clock=Clock(), nonce=lambda: "n")
        failed = _get(app, "/api/state", token=token)
        assert failed.status == 503
        assert "the index is broken" in json.loads(failed.body)["error"]
        assert _get(app, "/api/state", token=token).status == 200

    def test_an_unknown_path_is_404_for_a_paired_device(self, store):
        token = _paired(store)
        assert _get(_app(store), "/nope", token=token).status == 404

    def test_the_handling_writes_nothing_under_any_state_root(self, store, machine_world):
        token = _paired(store)
        roots = [machine_world[name].state_root for name in ("alpha", "beta", "gamma")]
        home = oracle_home()
        index = ProjectIndex(home)
        cache = ScanCache(probe=machine_world["probe"])

        def load():
            return dashboard.load(home, time.time(),
                                  lambda: assess(scan_all(index, cache=cache), time.time()))

        before = tree_fingerprint(*roots, home)
        app = WebApp(store, load, [TS4], clock=Clock(), nonce=lambda: "n")
        for path in ("/", "/api/state", "/api/state", "/pair"):
            _get(app, path, token=token)
            _get(app, path)
        assert tree_fingerprint(*roots, home) == before


# -- the page's refresh loop ----------------------------------------------------


#: JavaScriptCore's shell, on every Mac.
JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"


def _javascript() -> list[str] | None:
    """A JavaScript engine that runs here: ``node``, else macOS's ``jsc``."""
    for argv in ([shutil.which("node"), "-e"], [JSC, "-e"]):
        if not argv[0] or not Path(argv[0]).exists():
            continue
        try:
            probe = subprocess.run([*argv, "0"], capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return argv
    return None


ENGINE = _javascript()


def _script() -> str:
    page = webpage.page("n", panel.WORDS, interval=5.0)
    start = page.index('<script nonce="n">') + len('<script nonce="n">')
    return page[start:page.index("</script>", start)]


class TestThePageScript:
    def test_the_loop_is_on_a_timer_not_a_meta_refresh(self):
        page = webpage.page("n", panel.WORDS, interval=5.0)
        assert "http-equiv" not in page
        script = _script()
        assert "setTimeout" in script and "fetch(" in script
        assert f"MAX_BACKOFF = {web.MAX_BACKOFF:g}" in script
        assert "textContent" in script and "innerHTML" not in script

    @pytest.mark.skipif(ENGINE is None, reason="no JavaScript engine runs here")
    def test_the_loop_survives_failures_with_backoff_and_ages(self):
        harness = _script() + r"""
const delays = [];
const seen = [];
let now = 1000;
let calls = 0;
const loop = ORACLE.makeLoop({
  interval: 5,
  now: () => now,
  fetchState: async () => {
    calls += 1;
    if (calls <= 2) throw new Error('offline');
    return {freshness: {age: 1, interval: 4, stale_after: 12}};
  },
  schedule: (fn, ms) => delays.push(ms),
  onData: (d) => seen.push('data'),
  onError: (e, n) => seen.push('error ' + n),
});
(async () => {
  await loop.tick(); await loop.tick(); await loop.tick(); await loop.tick();
  now = 1000 + 2500;
  const age = ORACLE.ageOf(loop.state, now);
  now = 1000 + 20000;
  const later = ORACLE.ageOf(loop.state, now);
  (typeof print === "function" ? print : console.log)(JSON.stringify({
    delays, seen, failures: loop.state.failures, age, later,
    fresh: ORACLE.freshnessText(age, 12), stale: ORACLE.freshnessText(later, 12),
    never: ORACLE.freshnessText(null, 12),
    cap: ORACLE.retryDelay(5, 30),
  }));
})();
"""
        out = subprocess.run([*ENGINE, harness], capture_output=True, text=True, timeout=30,
                             check=True)
        result = json.loads(out.stdout)
        assert result["delays"] == [10000, 20000, 4000, 4000]
        assert result["seen"] == ["error 1", "error 2", "data", "data"]
        assert result["failures"] == 0
        assert result["age"] == pytest.approx(3.5)
        assert result["later"] == pytest.approx(21.0)
        assert result["fresh"] == {"text": "Live · updated 4 s ago", "stale": False}
        assert result["stale"] == {"text": "Stale · updated 21 s ago", "stale": True}
        assert result["never"]["stale"] is True
        assert result["cap"] == web.MAX_BACKOFF

    def test_the_page_wakes_the_loop_when_it_is_looked_at_again(self):
        script = _script()
        for event in ('"visibilitychange"', '"online"', '"pageshow"', '"focus"'):
            assert event in script
        assert "loop.wake()" in script

    @pytest.mark.skipif(ENGINE is None, reason="no JavaScript engine runs here")
    def test_wake_cuts_a_backoff_short_after_a_server_restart(self):
        # The server is down for two reads, so the loop backs off; it comes
        # back, and a wake (the phone looked at the page again) reads at
        # once instead of sitting out the rest of the backoff.
        harness = _script() + r"""
const scheduled = [];
const cancelled = [];
let up = false;
let calls = 0;
let release = null;
const loop = ORACLE.makeLoop({
  interval: 5,
  now: () => 1000,
  fetchState: () => {
    calls += 1;
    if (calls === 4) return new Promise((resolve) => { release = resolve; });
    if (!up) return Promise.reject(new Error('connection refused'));
    return Promise.resolve({freshness: {age: 0, interval: 5, stale_after: 15}});
  },
  schedule: (fn, ms) => { scheduled.push(ms); return 'timer' + scheduled.length; },
  cancel: (handle) => cancelled.push(handle),
  onData: () => {},
  onError: () => {},
});
(async () => {
  await loop.tick(); await loop.tick();
  const backedOff = loop.state.failures;
  up = true;
  await loop.wake();
  const afterWake = {failures: loop.state.failures, data: loop.state.data !== null};
  const pending = loop.wake();
  const second = loop.wake();
  const inFlightCalls = calls;
  release({freshness: {age: 0, interval: 5, stale_after: 15}});
  await pending; await second;
  (typeof print === "function" ? print : console.log)(JSON.stringify({
    backedOff, afterWake, cancelled, scheduled, inFlightCalls, calls,
    lost: ORACLE.jobPoll(404), done: ORACLE.jobPoll(200), down: ORACLE.jobPoll(0),
    other: ORACLE.jobPoll(503),
  }));
})();
"""
        out = subprocess.run([*ENGINE, harness], capture_output=True, text=True, timeout=30,
                             check=True)
        result = json.loads(out.stdout)
        assert result["backedOff"] == 2
        assert result["afterWake"] == {"failures": 0, "data": True}
        assert result["cancelled"][0] == "timer2"
        assert result["scheduled"] == [10000, 20000, 5000, 5000]
        assert result["inFlightCalls"] == 4 and result["calls"] == 4
        assert result["lost"] == "lost"
        assert result["done"] == "show"
        assert result["down"] == "retry" and result["other"] == "retry"

    def test_a_job_the_server_forgot_is_said_to_be_lost(self):
        script = _script()
        assert "ORACLE.jobPoll(response.status)" in script
        assert "actions.log" in script


# -- a real socket, on loopback only ------------------------------------------------


def _http(url: str, method: str = "GET", body: bytes | None = None,
          headers: dict | None = None) -> tuple[int, bytes, dict]:
    """One request with ``http.client``: no proxy lookup, no redirect followed."""
    import http.client

    target = urllib.parse.urlsplit(url)
    connection = http.client.HTTPConnection(target.hostname, target.port, timeout=5)
    try:
        connection.request(method, target.path or "/", body=body, headers=headers or {})
        answer = connection.getresponse()
        return answer.status, answer.read(), dict(answer.getheaders())
    finally:
        connection.close()


class TestTheServerSeam:
    """The suite's recorder stands where ``WebServer`` does, so they take one signature."""

    def test_the_recorder_takes_the_servers_own_arguments(self):
        from invariant.oracle.web import RecordingWeb

        real = inspect.signature(WebServer).parameters
        recorded = inspect.signature(RecordingWeb()._make).parameters
        assert [(p.name, p.kind, p.default) for p in recorded.values()] == [
            (p.name, p.kind, p.default) for p in real.values()]

    def test_neither_takes_a_certificate_positionally(self, store):
        from invariant.oracle.web import RecordingWeb

        app = WebApp(store, lambda: _loaded({"projects": []}), [LOOPBACK])
        for make in (WebServer, RecordingWeb()._make):
            with pytest.raises(TypeError):
                make(app, [LOOPBACK], 0, object())

    def test_the_recorder_keeps_what_it_was_asked_to_serve(self, store):
        from invariant.oracle.web import RecordingWeb

        app = WebApp(store, lambda: _loaded({"projects": []}), [LOOPBACK])
        recorder = RecordingWeb()
        server = recorder.factory(app, [LOOPBACK], 0, request_timeout=3.0, max_connections=2)
        assert (server.request_timeout, server.max_connections) == (3.0, 2)
        assert recorder.servers == [server] and server.served is None


class TestTheServer:
    def test_it_serves_on_loopback_and_an_unpaired_request_gets_nothing(self, store):
        token = _paired(store)
        app = WebApp(store, lambda: _loaded({"projects": []}), [LOOPBACK],
                     nonce=lambda: "n")
        server = WebServer(app, [LOOPBACK], 0)
        try:
            urls = server.start()
            assert len(urls) == 1 and urls[0].startswith("http://127.0.0.1:")
            server.serve_in_background()
            url = urls[0] + "api/state"
            status, body, _ = _http(url)
            assert status == 401 and b"not paired" in body
            status, body, _ = _http(url, headers={"Cookie": f"{COOKIE_NAME}={token}"})
            assert status == 200
            assert json.loads(body)["freshness"]["source"] == "snapshot"
            form = {"Content-Type": "application/x-www-form-urlencoded"}
            status, _, _ = _http(urls[0] + "pair", "POST", b"x" * (web.MAX_BODY + 1), form)
            assert status == 413
            pairing = store.issue("tablet", ttl=60)
            status, _, headers = _http(urls[0] + "pair", "POST",
                                       f"code={pairing.code}".encode(), form)
            assert status == 303 and headers["Location"] == "/"
            status, body, _ = _http(urls[0], "HEAD",
                                    headers={"Cookie": f"{COOKIE_NAME}={token}"})
            assert status == 200 and body == b""
        finally:
            server.close()

    def test_a_port_in_use_is_an_error_and_nothing_is_left_open(self, store):
        app = WebApp(store, lambda: _loaded({"projects": []}), [LOOPBACK])
        first = WebServer(app, [LOOPBACK], 0)
        try:
            port = int(first.start()[0].rsplit(":", 1)[1].rstrip("/"))
            second = WebServer(app, [LOOPBACK, LOOPBACK], port)
            with pytest.raises(web.WebError, match="cannot listen"):
                second.start()
            assert second.servers == []
        finally:
            first.close()

    def test_serve_stops_on_interrupt(self, store, monkeypatch):
        app = WebApp(store, lambda: _loaded({"projects": []}), [LOOPBACK])
        server = WebServer(app, [LOOPBACK], 0)
        server.start()

        def interrupt(timeout=None):
            raise KeyboardInterrupt

        monkeypatch.setattr(server.stopped, "wait", interrupt)
        server.serve()
        assert server.servers == []

    def test_binding_asks_no_name_of_the_address(self, store):
        # The stock HTTPServer.server_bind would call socket.getfqdn, a
        # reverse lookup the conftest guard refuses.
        server = WebServer(WebApp(store, lambda: _loaded({"projects": []}), [LOOPBACK]),
                           [LOOPBACK], 0)
        try:
            server.start()
            assert server.servers[0].server_name == LOOPBACK
        finally:
            server.close()


class TestTheNetworkGuard:
    """The conftest backstop: nothing in the suite reaches past loopback.

    Each refused call is stopped before the real socket call runs, so these
    tests bind and send nothing.
    """

    @pytest.mark.parametrize("address", [("0.0.0.0", 0), ("", 0), (TS4, 0), ("192.168.1.2", 0)])
    def test_a_bind_beyond_loopback_is_refused(self, address):
        import socket

        from conftest import RefusedProgram

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock, \
                pytest.raises(RefusedProgram, match="only loopback"):
            sock.bind(address)

    @pytest.mark.parametrize("address", [("::", 0, 0, 0), (TS6, 0, 0, 0)])
    def test_an_ipv6_wildcard_or_tailnet_bind_is_refused(self, address):
        import socket

        from conftest import RefusedProgram

        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock, \
                pytest.raises(RefusedProgram):
            sock.bind(address)

    def test_a_connect_or_send_beyond_loopback_is_refused(self):
        import socket

        from conftest import RefusedProgram

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            with pytest.raises(RefusedProgram):
                sock.connect((TS4, 7341))
            with pytest.raises(RefusedProgram):
                sock.connect_ex(("192.168.1.1", 80))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock, \
                pytest.raises(RefusedProgram):
            sock.sendto(b"x", ("224.0.0.251", 5353))

    def test_a_reverse_lookup_is_refused(self):
        import socket

        from conftest import RefusedProgram

        with pytest.raises(RefusedProgram):
            socket.getfqdn("127.0.0.1")
        with pytest.raises(RefusedProgram):
            socket.gethostbyaddr(TS4)

    def test_the_stock_http_server_bind_is_caught(self):
        from http.server import HTTPServer

        from conftest import RefusedProgram

        with pytest.raises(RefusedProgram):
            HTTPServer((LOOPBACK, 0), object)

    def test_loopback_is_allowed(self):
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.sendto(b"x", sock.getsockname())
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
            sock.bind(("::1", 0))


# -- the CLI ---------------------------------------------------------------------------


def run_cli(capsys, *argv) -> tuple[int, str]:
    code = main(list(argv))
    return code, capsys.readouterr().out


class TestTheCommands:
    def test_without_tailscale_web_refuses_to_start(self, capsys, oracle_web):
        code = main(["oracle", "web"])
        err = capsys.readouterr().err
        assert code == NoTailnetError.exit_code
        assert "no Tailscale address" in err
        assert oracle_web.servers == []

    def test_it_binds_the_tailscale_address_it_found(self, capsys, oracle_web):
        oracle_web.addresses = [TS4, TS6]
        code, out = run_cli(capsys, "oracle", "web", "--port", "7400")
        assert code == 0
        (server,) = oracle_web.servers
        assert server.addresses == [TS4, TS6] and server.port == 7400
        assert server.foreground
        assert f"http://{TS4}:7400/" in out and f"http://[{TS6}]:7400/" in out
        assert "invariant oracle web pair" in out

    def test_allow_loopback_binds_loopback_when_no_tailscale(self, capsys, oracle_web):
        code, out = run_cli(capsys, "oracle", "web", "--allow-loopback")
        assert code == 0
        assert oracle_web.servers[0].addresses == [LOOPBACK]
        assert oracle_web.servers[0].port == web.DEFAULT_PORT
        assert "loopback only" in out

    def test_the_app_it_serves_reads_the_machine(self, capsys, oracle_web, machine_world,
                                                 monkeypatch):
        monkeypatch.setattr(cli_main, "_oracle_cache",
                            lambda: ScanCache(probe=machine_world["probe"]))
        oracle_web.addresses = [TS4]
        run_cli(capsys, "oracle", "web")
        app = oracle_web.servers[0].app
        store = DeviceStore(oracle_home())
        token = _paired(store)
        body = json.loads(_get(app, "/api/state", token=token).body)
        rows = [c["row"] for g in body["view"]["groups"] for c in g["cards"]]
        assert {r.split("|")[1] for r in rows} == {
            "a-live", "a-parked", "b-strand", "b-dead", "g-rev"}
        assert body["freshness"]["source"] == "scan"

    @pytest.mark.parametrize("port", ["0", "70000", "-1"])
    def test_a_bad_port_is_a_usage_error(self, capsys, oracle_web, port):
        oracle_web.addresses = [TS4]
        assert main(["oracle", "web", "--port", port]) == 2
        assert oracle_web.servers == []

    def test_pair_prints_a_code_and_a_link_per_address(self, capsys, oracle_web):
        oracle_web.addresses = [TS4]
        code, out = run_cli(capsys, "oracle", "web", "pair", "--name", "phone", "--json")
        assert code == 0
        body = json.loads(out)
        assert len(body["code"]) == CODE_LENGTH
        assert body["name"] == "phone"
        shown = display_code(body["code"])
        assert body["links"] == [f"http://{TS4}:{web.DEFAULT_PORT}/pair?code={shown}"]
        assert DeviceStore(oracle_home()).pending() == 1

    def test_pair_shows_the_code_as_its_link_carries_it(self, capsys, oracle_web):
        oracle_web.addresses = [TS4]
        code, out = run_cli(capsys, "oracle", "web", "pair")
        assert code == 0
        shown = out.split("pairing code for ", 1)[1].split(": ", 1)[1].split()[0]
        assert "-" in shown and f"/pair?code={shown}" in out

    def test_pair_text_says_how_long_it_lasts(self, capsys, oracle_web):
        oracle_web.addresses = [TS4]
        code, out = run_cli(capsys, "oracle", "web", "pair", "--ttl", "120")
        assert code == 0
        assert "2 minutes" in out and "once" in out

    def test_pair_without_tailscale_still_gives_the_code(self, capsys, oracle_web):
        code, out = run_cli(capsys, "oracle", "web", "pair")
        assert code == 0
        assert "no Tailscale address" in out
        assert DeviceStore(oracle_home()).pending() == 1

    def test_pair_with_loopback_allowed_links_loopback(self, capsys, oracle_web):
        _, out = run_cli(capsys, "oracle", "web", "pair", "--allow-loopback", "--port",
                            "7401", "--json")
        assert json.loads(out)["links"][0].startswith(f"http://{LOOPBACK}:7401/pair?code=")

    def test_a_bad_ttl_is_a_usage_error(self, capsys, oracle_web):
        assert main(["oracle", "web", "pair", "--ttl", "0"]) == 2

    def test_devices_and_revoke(self, capsys, oracle_web):
        store = DeviceStore(oracle_home())
        device, _ = store.redeem(store.issue("phone", ttl=300).code)
        code, out = run_cli(capsys, "oracle", "web", "devices")
        assert code == 0 and device.id in out and "phone" in out
        code, out = run_cli(capsys, "oracle", "web", "devices", "--json")
        assert json.loads(out) == {"devices": [device.to_dict()], "pending": 0}
        code, out = run_cli(capsys, "oracle", "web", "revoke", device.id)
        assert code == 0 and "revoked" in out
        code, out = run_cli(capsys, "oracle", "web", "devices")
        assert "no paired devices" in out
        assert main(["oracle", "web", "revoke", device.id]) == 2

    def test_the_seams_are_inert_by_default(self, oracle_web):
        assert cli_main._oracle_web_addresses()() == []
        assert cli_main._oracle_web_server() is oracle_web.factory

    def test_the_real_seams(self, monkeypatch):
        monkeypatch.setattr(web, "discover_addresses", lambda run: [TS4])
        assert cli_main._real_oracle_web_addresses()() == [TS4]
        assert cli_main._real_oracle_web_server() is WebServer

    def test_the_web_writes_nothing_under_a_state_root(self, capsys, oracle_web, machine_world,
                                                       monkeypatch):
        monkeypatch.setattr(cli_main, "_oracle_cache",
                            lambda: ScanCache(probe=machine_world["probe"]))
        roots = [machine_world[name].state_root for name in ("alpha", "beta", "gamma")]
        before = tree_fingerprint(*roots)
        oracle_web.addresses = [TS4]
        run_cli(capsys, "oracle", "web")
        run_cli(capsys, "oracle", "web", "pair")
        token = _paired(DeviceStore(oracle_home()))
        app = oracle_web.servers[0].app
        for path in ("/", "/api/state"):
            _get(app, path, token=token)
        assert tree_fingerprint(*roots) == before


def test_the_guard_refuses_the_real_tailscale():
    from conftest import RefusedProgram

    with pytest.raises(RefusedProgram):
        subprocess.run(["tailscale", "ip"], check=False)
    with pytest.raises(RefusedProgram):
        subprocess.run(["/Applications/Tailscale.app/Contents/MacOS/Tailscale", "ip"],
                       check=False)


def test_the_web_modules_import_no_host_and_no_toolkit():
    import re

    for name in ("web.py", "webpage.py", "devices.py", "panel.py"):
        source = (Path(web.__file__).parent / name).read_text()
        imports = re.findall(r"^\s*(?:from|import)\s+(\S+)", source, re.MULTILINE)
        assert not [i for i in imports if "hosts" in i or "textual" in i or "tui" in i], name


# -- IDEA_026: the page over HTTPS, with Tailscale's certificate ---------------------

TLS_FIXTURES = Path(__file__).parent / "fixtures" / "tls"
TS_NAME = "mac.example.ts.net"


def _served(tmp_path, which: str = "a"):
    """A ``Served`` over a fixture certificate, kept as the oracle would keep it."""
    from invariant.oracle.tls import Certificates, Issued, RecordingIssuer, Served

    pem = (TLS_FIXTURES / f"{which}.crt").read_text()
    key = (TLS_FIXTURES / f"{which}.key").read_text()
    certs = Certificates(tmp_path / f"tls-{which}", RecordingIssuer(Issued(TS_NAME, pem, key)),
                         clock=lambda: 1_800_000_000.0)
    cert = certs.ensure()
    return Served(cert, certs.context(cert)), certs


def _tls_app(store, served, bound=(TS4,)):
    return WebApp(store, lambda: _loaded({"projects": []}), list(bound), clock=Clock(),
                  nonce=lambda: "N0NCE", served=served)


class TestTheHostUnderTls:
    def test_only_the_certificates_name_is_this_server(self):
        assert host_allowed(f"{TS_NAME}:7341", [TS4], name=TS_NAME)
        assert host_allowed(f"{TS_NAME.upper()}.:7341", [TS4], name=TS_NAME)
        assert host_allowed(TS_NAME, [TS4], name=TS_NAME)
        assert not host_allowed(f"{TS4}:7341", [TS4], name=TS_NAME)
        assert not host_allowed("other-mac.example.ts.net:7341", [TS4], name=TS_NAME)
        assert not host_allowed(None, [TS4], name=TS_NAME)

    def test_a_bare_address_is_told_the_name_and_given_nothing_else(self, store, tmp_path):
        served, _ = _served(tmp_path)
        token = _paired(store)
        response = _get(_tls_app(store, served), "/", token=token, host=f"{TS4}:7341")
        assert response.status == 421
        assert f"https://{TS_NAME}:7341/".encode() in response.body
        response = _get(_tls_app(store, served), "/", token=token, host=f"[{TS6}]")
        assert response.status == 421 and f"https://{TS_NAME}/".encode() in response.body

    def test_the_name_answers(self, store, tmp_path):
        served, _ = _served(tmp_path)
        token = _paired(store)
        response = _get(_tls_app(store, served), "/", token=token, host=f"{TS_NAME}:7341")
        assert response.status == 200


class TestCookieAndOriginOverHttps:
    def test_the_pairing_cookie_is_secure(self, store, tmp_path):
        served, _ = _served(tmp_path)
        pairing = store.issue("phone", ttl=300)
        response = _post(_tls_app(store, served), "/pair", f"code={pairing.code}".encode(),
                         host=f"{TS_NAME}:7341",
                         headers={"origin": f"https://{TS_NAME}:7341"})
        assert response.status == 303
        cookie = response.header("set-cookie")
        assert "; Secure" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie

    def test_over_plain_http_it_is_not(self, store):
        pairing = store.issue("phone", ttl=300)
        cookie = _post(_app(store), "/pair", f"code={pairing.code}".encode()).header("set-cookie")
        assert "Secure" not in cookie

    def test_the_http_origin_is_refused_once_the_page_is_https(self, store, tmp_path):
        served, _ = _served(tmp_path)
        pairing = store.issue("phone", ttl=300)
        response = _post(_tls_app(store, served), "/pair", f"code={pairing.code}".encode(),
                         host=f"{TS_NAME}:7341",
                         headers={"origin": f"http://{TS_NAME}:7341"})
        assert response.status == 403

    def test_an_action_needs_the_https_origin(self, store, tmp_path):
        from invariant.oracle.web import _origin_required

        headers = {"host": f"{TS_NAME}:7341", "origin": f"https://{TS_NAME}:7341"}
        assert _origin_required(headers, "https")
        assert not _origin_required(headers, "http")
        assert not _origin_required({**headers, "origin": f"http://{TS_NAME}:7341"}, "https")


class TestThePlainHttpWarning:
    def test_the_page_and_the_pair_form_carry_it(self, store):
        token = _paired(store)
        app = WebApp(store, lambda: _loaded({"projects": []}), [TS4], clock=Clock(),
                     nonce=lambda: "n", insecure="no certificate: <HTTPS off>")
        page = _get(app, "/", token=token).body.decode()
        assert 'id="insecure"' in page and "no certificate: &lt;HTTPS off&gt;" in page
        form = _get(app, "/pair?code=X").body.decode()
        assert 'id="insecure"' in form and "&lt;HTTPS off&gt;" in form

    def test_without_one_neither_does(self, store):
        token = _paired(store)
        assert 'id="insecure"' not in _get(_app(store), "/", token=token).body.decode()
        assert 'id="insecure"' not in _get(_app(store), "/pair").body.decode()


class TestThePageFollowsAMove:
    def test_the_state_fetch_does_not_follow_a_redirect_and_reloads_on_one(self):
        script = _script()
        assert 'redirect: "manual"' in script
        assert "ORACLE.moved(response)" in script and "location.reload()" in script

    @pytest.mark.skipif(ENGINE is None, reason="no JavaScript engine runs here")
    def test_moved_is_an_opaque_redirect_only(self):
        harness = _script() + r"""
(typeof print === "function" ? print : console.log)(JSON.stringify([
  ORACLE.moved({type: "opaqueredirect", status: 0}),
  ORACLE.moved({type: "basic", status: 200}),
  ORACLE.moved({type: "basic", status: 401}),
]));
"""
        out = subprocess.run([*ENGINE, harness], capture_output=True, text=True, timeout=30,
                             check=True)
        assert json.loads(out.stdout) == [True, False, False]


def _tls_get(port: int, path: str = "/", host: str = TS_NAME, headers: dict | None = None):
    """One request over TLS to loopback, naming the certificate's host; nothing verified."""
    import http.client
    import ssl

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    connection = http.client.HTTPSConnection(LOOPBACK, port, timeout=5, context=context)
    try:
        connection.connect()
        peer = connection.sock.getpeercert(binary_form=True)
        connection.request("GET", path, headers={"Host": f"{host}:{port}", **(headers or {})})
        answer = connection.getresponse()
        return answer.status, answer.read(), dict(answer.getheaders()), peer
    finally:
        connection.close()


def _der(which: str) -> bytes:
    import ssl

    return ssl.PEM_cert_to_DER_cert((TLS_FIXTURES / f"{which}.crt").read_text())


class TestTheServerOverTls:
    def test_it_serves_https_on_its_name_and_renews_without_a_restart(self, store, tmp_path):
        served, _ = _served(tmp_path, "a")
        token = _paired(store)
        server = WebServer(_tls_app(store, served, bound=[LOOPBACK]), [LOOPBACK], 0,
                           served=served)
        try:
            urls = server.start()
            port = server.port
            assert urls == [f"https://{TS_NAME}:{port}/"]
            server.serve_in_background()
            status, body, _, peer = _tls_get(port, "/api/state",
                                             headers={"Cookie": f"{COOKIE_NAME}={token}"})
            assert status == 200 and json.loads(body)["view"]["groups"] == []
            assert peer == _der("a")
            renewed, _ = _served(tmp_path, "b")
            served.renew(renewed.certificate, renewed.context)
            status, _, _, peer = _tls_get(port, "/api/state",
                                          headers={"Cookie": f"{COOKIE_NAME}={token}"})
            assert status == 200 and peer == _der("b")
        finally:
            server.close()

    def test_plain_http_gets_only_a_redirect_to_the_https_name(self, store, tmp_path):
        served, _ = _served(tmp_path)
        token = _paired(store)
        server = WebServer(_tls_app(store, served, bound=[LOOPBACK]), [LOOPBACK], 0,
                           served=served)
        try:
            server.start()
            server.serve_in_background()
            status, body, headers = _http(f"http://{LOOPBACK}:{server.port}/api/state?x=1",
                                          headers={"Cookie": f"{COOKIE_NAME}={token}"})
            assert status == 307
            assert headers["Location"] == f"https://{TS_NAME}:{server.port}/"
            assert body == b"" and headers["Cache-Control"] == "no-store"
        finally:
            server.close()

    def test_a_failed_handshake_or_a_silent_client_is_closed_quietly(self, store, tmp_path):
        import socket

        served, _ = _served(tmp_path)
        server = WebServer(_tls_app(store, served, bound=[LOOPBACK]), [LOOPBACK], 0,
                           served=served, request_timeout=0.5)
        try:
            server.start()
            server.serve_in_background()
            with socket.create_connection((LOOPBACK, server.port), timeout=5) as raw:
                raw.sendall(b"\x16\x03\x01\x00\x05junk!")
                try:
                    answer = raw.recv(64)
                except ConnectionResetError:
                    answer = b""
                assert b"HTTP" not in answer
            with socket.create_connection((LOOPBACK, server.port), timeout=5) as raw:
                assert raw.recv(64) == b""
            with socket.create_connection((LOOPBACK, server.port), timeout=5) as raw:
                raw.shutdown(socket.SHUT_WR)
                assert raw.recv(64) == b""
            status, _, _, _ = _tls_get(server.port, "/")
            assert status == 401
        finally:
            server.close()


class TestTheCommandsOverTls:
    def issue(self, oracle_certificates, which: str = "a") -> None:
        from invariant.oracle.tls import Issued

        oracle_certificates.issued = Issued(
            TS_NAME, (TLS_FIXTURES / f"{which}.crt").read_text(),
            (TLS_FIXTURES / f"{which}.key").read_text())

    def test_web_serves_https_when_tailscale_gives_a_certificate(
            self, capsys, oracle_web, oracle_certificates):
        self.issue(oracle_certificates)
        oracle_web.addresses = [TS4]
        code, out = run_cli(capsys, "oracle", "web", "--port", "7400")
        assert code == 0
        (server,) = oracle_web.servers
        assert server.served is not None and server.served.name == TS_NAME
        assert server.app.served is server.served and server.app.insecure is None
        assert f"https://{TS_NAME}:7400/" in out and "http://" not in out
        assert "expires" in out

    def test_without_a_certificate_it_serves_http_and_says_why(
            self, capsys, oracle_web, oracle_certificates):
        oracle_web.addresses = [TS4]
        code, out = run_cli(capsys, "oracle", "web")
        assert code == 0
        (server,) = oracle_web.servers
        assert server.served is None
        assert "no certificate in the test suite" in server.app.insecure
        assert f"http://{TS4}:7341/" in out
        assert "PLAIN HTTP" in out and "no certificate in the test suite" in out

    def test_loopback_is_plain_http_with_no_warning_and_asks_for_nothing(
            self, capsys, oracle_web, oracle_certificates):
        code, _ = run_cli(capsys, "oracle", "web", "--allow-loopback")
        assert code == 0
        assert oracle_web.servers[0].served is None
        assert oracle_web.servers[0].app.insecure is None
        assert oracle_certificates.calls == 0

    def test_a_certificate_that_will_not_load_falls_back_to_http(
            self, capsys, oracle_web, oracle_certificates, monkeypatch):
        from invariant.oracle import tls

        self.issue(oracle_certificates)
        oracle_web.addresses = [TS4]

        def broken(path):
            raise tls.CertError(f"cannot load {path}: broken")

        monkeypatch.setattr(tls, "context_for", broken)
        code, out = run_cli(capsys, "oracle", "web")
        assert code == 0 and oracle_web.servers[0].served is None
        assert "broken" in out

    def test_pair_links_the_https_name_once_a_certificate_is_kept(
            self, capsys, oracle_web, oracle_certificates):
        self.issue(oracle_certificates)
        oracle_web.addresses = [TS4]
        run_cli(capsys, "oracle", "web")
        code, out = run_cli(capsys, "oracle", "web", "pair", "--json")
        assert code == 0
        body = json.loads(out)
        assert body["links"] == [f"https://{TS_NAME}:7341/pair?code={display_code(body['code'])}"]

    def test_pair_ignores_an_expired_certificate(
            self, capsys, oracle_web, oracle_certificates, monkeypatch):
        from invariant.oracle import tls

        self.issue(oracle_certificates)
        oracle_web.addresses = [TS4]
        run_cli(capsys, "oracle", "web")
        monkeypatch.setattr(cli_main.time, "time", lambda: 5_000_000_000.0)
        body = json.loads(run_cli(capsys, "oracle", "web", "pair", "--json")[1])
        assert body["links"][0].startswith(f"http://{TS4}:")
        assert tls.read_certificate(oracle_home()) is not None

    def test_the_command_hands_the_listener_its_certificate_by_keyword(
            self, capsys, oracle_web, oracle_certificates):
        # BUG_083: passed fourth and positionally, the holder became
        # WebServer's request_timeout, and every connection raised.
        self.issue(oracle_certificates)
        oracle_web.addresses = [TS4]
        calls = []

        def factory(*args, **keywords):
            calls.append((args, keywords))
            return oracle_web._make(*args, **keywords)

        oracle_web.factory = factory
        assert run_cli(capsys, "oracle", "web")[0] == 0
        ((args, keywords),) = calls
        bound = inspect.signature(WebServer).bind(*args, **keywords)
        assert len(args) == 3 and bound.arguments["served"] is oracle_web.servers[0].served
        assert "request_timeout" not in bound.arguments

    def test_the_certificate_seam_is_the_recorder_and_the_real_one_asks_tailscale(
            self, oracle_certificates, monkeypatch):
        from invariant.oracle import tls

        assert cli_main._oracle_web_certificates() is oracle_certificates
        asked = []
        monkeypatch.setattr(tls, "issue", lambda run: asked.append(run) or "issued")
        assert cli_main._real_oracle_web_certificates()() == "issued"
        assert asked[0].keywords == {"timeout": tls.CERT_TIMEOUT}
        assert asked[0].func is web.run_program


def test_run_program_takes_a_timeout(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "out", "")

    monkeypatch.setattr(web.subprocess, "run", fake_run)
    assert web.run_program(["true"], timeout=99) == (0, "out")
    assert seen["timeout"] == 99
    web.run_program(["true"])
    assert seen["timeout"] == web.PROGRAM_TIMEOUT
