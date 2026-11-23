"""Web push on the page and in the CLI (ADR-027 increment 4, ruling 17).

``test_oracle_push`` proves the channel itself. This proves how a phone opts
in and out, and how the CLI wires the channel behind the one notifier seam:

* the routes: ``/sw.js``, ``POST /api/push/subscribe`` and ``unsubscribe``,
  behind the token, from the page's own origin, over HTTPS only;
* the view: the Notifications row says what this device can do, and why not;
* the page's script and the service worker, run whole under a JavaScript
  engine against fakes of the browser's push API;
* the CLI: ``oracle web`` hands the page the push book, ``revoke`` drops a
  device's subscriptions, ``devices`` says who has push on, ``notify-test``
  reports every channel, and the real notifier fans out to the Mac and push.

Nothing reaches the network: the POST is the ``_oracle_push_post`` seam, which
``conftest.py`` replaces for every test.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys

import pytest
import test_oracle_webpage as page_tests
from test_oracle_web import (
    ENGINE,
    TS4,
    TS_NAME,
    Clock,
    _get,
    _loaded,
    _paired,
    _script,
    _served,
    run_cli,
)

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.oracle import panel, push, webpage
from invariant.oracle.devices import COOKIE_NAME, DeviceStore
from invariant.oracle.home import oracle_home
from invariant.oracle.notify import FanOutNotifier, OsascriptNotifier, Outcome
from invariant.oracle.web import Request, WebApp

HOST = f"{TS_NAME}:7341"
ORIGIN = f"https://{HOST}"
APPLE = "https://web.push.apple.com/QGuQyavXutnMIthj6CZ6ENO2gWKKZ8"


def _subscription(endpoint: str = APPLE) -> dict:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    point = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    b64 = lambda data: base64.urlsafe_b64encode(data).rstrip(b"=").decode()
    return {"endpoint": endpoint, "expirationTime": None,
            "keys": {"p256dh": b64(point), "auth": b64(os.urandom(16))}}


@pytest.fixture
def store(tmp_path):
    return DeviceStore(tmp_path / "home", clock=Clock())


@pytest.fixture
def book(tmp_path):
    return push.PushBook(tmp_path / "home", clock=Clock())


def _app(store, served=None, book=None, why=None):
    return WebApp(store, lambda: _loaded({"projects": []}), [TS4], clock=Clock(),
                  nonce=lambda: "N0NCE", served=served, push=book, push_why=why)


def _post_json(app, path, body, token, origin=ORIGIN, host=HOST):
    headers = {"host": host, "content-type": "application/json",
               "cookie": f"{COOKIE_NAME}={token}"}
    if origin is not None:
        headers["origin"] = origin
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    return app.handle(Request("POST", path, headers, data))


def _state(app, token, host=HOST):
    return json.loads(_get(app, "/api/state", token=token, host=host).body)


needs_crypto = pytest.mark.skipif(push.unavailable() is not None,
                                  reason="cryptography is not installed")


@needs_crypto
class TestTheRoutes:
    def test_a_device_subscribes_and_the_view_says_it_is_on(self, store, book, tmp_path):
        served, _ = _served(tmp_path)
        token = _paired(store)
        app = _app(store, served, book)
        before = _state(app, token)["view"]["push"]
        assert before["available"] is True and before["on"] is False
        assert before["key"] == book.key()["public"]
        response = _post_json(app, "/api/push/subscribe", _subscription(), token)
        assert response.status == 200 and json.loads(response.body) == {"on": True}
        (row,) = book.subscriptions()
        device = store.devices()[0]
        assert row["device"] == device.id and row["origin"] == ORIGIN
        after = _state(app, token)["view"]["push"]
        assert after["on"] is True
        assert after["line_on"] == panel.WORDS["push_on"]

    def test_unsubscribe_takes_the_endpoint_or_all_of_the_device(self, store, book, tmp_path):
        served, _ = _served(tmp_path)
        token = _paired(store)
        app = _app(store, served, book)
        _post_json(app, "/api/push/subscribe", _subscription(APPLE + "1"), token)
        _post_json(app, "/api/push/subscribe", _subscription(APPLE + "2"), token)
        response = _post_json(app, "/api/push/unsubscribe", {"endpoint": APPLE + "1"}, token)
        assert json.loads(response.body) == {"removed": 1}
        response = _post_json(app, "/api/push/unsubscribe", {"endpoint": None}, token)
        assert json.loads(response.body) == {"removed": 1}
        assert book.subscriptions() == []

    def test_a_foreign_or_missing_origin_is_refused(self, store, book, tmp_path):
        served, _ = _served(tmp_path)
        token = _paired(store)
        app = _app(store, served, book)
        for origin in (None, "https://evil.example", f"http://{HOST}"):
            for path in ("/api/push/subscribe", "/api/push/unsubscribe"):
                response = _post_json(app, path, _subscription(), token, origin=origin)
                assert response.status == 403
        assert book.subscriptions() == []

    def test_a_subscription_the_oracle_will_not_keep_is_a_400(self, store, book, tmp_path):
        served, _ = _served(tmp_path)
        token = _paired(store)
        app = _app(store, served, book)
        response = _post_json(app, "/api/push/subscribe",
                              _subscription("https://evil.example/x"), token)
        assert response.status == 400
        assert "push service" in json.loads(response.body)["error"]
        for body in (b"not json", b"[1]"):
            assert _post_json(app, "/api/push/subscribe", body, token).status == 400
        assert _post_json(app, "/api/push/subscribe", b"x" * 9000, token).status == 413
        assert _post_json(app, "/api/push/unsubscribe", b"[]", token).status == 400

    def test_a_get_to_a_push_route_is_not_allowed(self, store, book, tmp_path):
        served, _ = _served(tmp_path)
        token = _paired(store)
        response = _get(_app(store, served, book), "/api/push/subscribe", token=token,
                        host=HOST)
        assert response.status == 405

    def test_an_unpaired_device_gets_nothing(self, store, book, tmp_path):
        served, _ = _served(tmp_path)
        app = _app(store, served, book)
        for path in ("/sw.js", "/api/push/subscribe"):
            response = app.handle(Request("POST" if "api" in path else "GET", path,
                                          {"host": HOST, "origin": ORIGIN}, b"{}"))
            assert response.status == 401
        assert not (tmp_path / "home" / push.PUSH_FILE).exists()

    def test_the_service_worker_is_served_with_its_own_tight_policy(self, store, book,
                                                                     tmp_path):
        served, _ = _served(tmp_path)
        token = _paired(store)
        response = _get(_app(store, served, book), "/sw.js", token=token, host=HOST)
        assert response.status == 200
        assert response.header("content-type") == "text/javascript; charset=utf-8"
        assert response.header("content-security-policy") == "default-src 'none'; img-src 'self'"
        assert response.header("cache-control") == "no-store"
        assert response.body.decode() == webpage.service_worker()

    def test_the_page_may_register_its_own_worker(self, store, book, tmp_path):
        served, _ = _served(tmp_path)
        token = _paired(store)
        csp = _get(_app(store, served, book), "/", token=token, host=HOST).header(
            "content-security-policy")
        assert "worker-src 'self'" in csp

    def test_a_delivery_error_is_worded_on_the_row(self, store, book, tmp_path):
        served, _ = _served(tmp_path)
        token = _paired(store)
        app = _app(store, served, book)
        _post_json(app, "/api/push/subscribe", _subscription(), token)
        book.record([push.Delivery(APPLE, ok=False, error="the push service answered 503")])
        view = _state(app, token)["view"]["push"]
        assert view["line_on"] == panel.WORDS["push_on_error"].format(
            error="the push service answered 503")

    def test_an_unreadable_book_turns_push_off_and_says_so(self, store, book, tmp_path):
        served, _ = _served(tmp_path)
        token = _paired(store)
        path = tmp_path / "home" / push.PUSH_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"version": 9}')
        app = _app(store, served, book)
        view = _state(app, token)["view"]["push"]
        assert view == {"available": False, "line": panel.WORDS["push_unreadable"],
                        "key": None, "on": False, "line_on": None}
        assert _post_json(app, "/api/push/subscribe", _subscription(), token).status == 409
        assert _get(app, "/sw.js", token=token, host=HOST).status == 404


@needs_crypto
class TestABookThatFailsUnderneath:
    def test_a_device_read_that_fails_turns_the_row_off(self, store, book, tmp_path,
                                                         monkeypatch):
        from invariant.core.errors import StateError

        served, _ = _served(tmp_path)
        token = _paired(store)

        def broken(device_id):
            raise StateError("unreadable")

        monkeypatch.setattr(book, "for_device", broken)
        view = _state(_app(store, served, book), token)["view"]["push"]
        assert view["available"] is False and view["line"] == panel.WORDS["push_unreadable"]

    def test_a_write_that_fails_is_a_503(self, store, book, tmp_path, monkeypatch):
        from invariant.core.errors import StateError

        served, _ = _served(tmp_path)
        token = _paired(store)

        def broken(*args):
            raise StateError("the disk is full")

        monkeypatch.setattr(book, "subscribe", broken)
        response = _post_json(_app(store, served, book), "/api/push/subscribe",
                              _subscription(), token)
        assert response.status == 503 and "disk is full" in json.loads(response.body)["error"]


class TestWhenPushIsNotOffered:
    def test_over_plain_http_the_row_says_it_needs_https(self, store, book):
        token = _paired(store)
        app = _app(store, None, book)
        view = _state(app, token, host=f"{TS4}:7341")["view"]["push"]
        assert view["available"] is False
        assert view["line"] == panel.WORDS["push_needs_https"]
        response = app.handle(Request("POST", "/api/push/subscribe", {
            "host": f"{TS4}:7341", "origin": f"http://{TS4}:7341",
            "content-type": "application/json", "cookie": f"{COOKIE_NAME}={token}"}, b"{}"))
        assert response.status == 409
        assert _get(app, "/sw.js", token=token).status == 404

    def test_without_the_extra_the_row_says_why(self, store, tmp_path):
        served, _ = _served(tmp_path)
        token = _paired(store)
        view = _state(_app(store, served, None, why="extra"), token)["view"]["push"]
        assert view["line"] == panel.WORDS["push_no_extra"]
        assert view["available"] is False

    def test_a_server_built_without_push_shows_no_row(self, store):
        token = _paired(store)
        app = WebApp(store, lambda: _loaded({"projects": []}), [TS4], clock=Clock(),
                     nonce=lambda: "n")
        assert _state(app, token, host=f"{TS4}:7341")["view"]["push"] is None
        assert _get(app, "/api/push/subscribe", token=token).status == 404

    def test_unsubscribe_works_over_plain_http_so_a_device_can_always_leave(self, store,
                                                                            book):
        token = _paired(store)
        device = store.devices()[0]
        book.subscriptions()  # an empty book reads fine
        book.subscribe(device.id, {"endpoint": APPLE, "p256dh": "x", "auth": "y"}, ORIGIN)
        app = _app(store, None, book)
        response = app.handle(Request("POST", "/api/push/unsubscribe", {
            "host": f"{TS4}:7341", "origin": f"http://{TS4}:7341",
            "content-type": "application/json", "cookie": f"{COOKIE_NAME}={token}"},
            b'{"endpoint": null}'))
        assert response.status == 200 and book.subscriptions() == []


class TestThePanelWords:
    def test_each_reason_has_its_sentence(self):
        for why, word in (("extra", "push_no_extra"), ("http", "push_needs_https"),
                          ("read", "push_unreadable")):
            assert panel.push_view({"available": False, "why": why}) == {
                "available": False, "line": panel.WORDS[word], "key": None, "on": False,
                "line_on": None}

    def test_an_available_row_carries_the_key_and_its_on_line(self):
        assert panel.push_view({"available": True, "key": "K", "on": True, "error": None}) == {
            "available": True, "line": None, "key": "K", "on": True,
            "line_on": panel.WORDS["push_on"]}
        assert panel.push_view(None) is None

    def test_no_word_names_a_path_or_an_engine_name(self):
        for name in ("notifications", "push_title", "push_on", "push_off", "push_turn_on",
                     "push_turn_off", "push_busy", "push_no_extra", "push_needs_https",
                     "push_unreadable", "push_home_screen", "push_unsupported",
                     "push_blocked", "push_failed", "push_on_error"):
            word = panel.WORDS[name]
            assert "/" not in word and ".json" not in word and word.strip() == word


# -- the page's script and the service worker -------------------------------------------


_KEY = base64.urlsafe_b64encode(b"\x04" + bytes(range(64))).rstrip(b"=").decode()


def _push_state(push_view, rows=None):
    state = page_tests._state(rows=rows)
    state["view"]["push"] = push_view
    return state


#: A browser with the push API: a registration whose push manager remembers
#: its subscription, and a permission the case can set.
_BROWSER = r"""
let subscribed = null;
let registered = [];
let permission = "default";
let refuse = null;
const pushManager = {
  getSubscription: async () => subscribed,
  subscribe: async (options) => {
    if (refuse) { const err = new Error(refuse.message); err.name = refuse.name; throw err; }
    subscribed = {endpoint: "https://web.push.apple.com/E1", options: options,
                  unsubscribe: async () => { subscribed = null; return true; },
                  toJSON: () => ({endpoint: "https://web.push.apple.com/E1",
                                  keys: {p256dh: "P", auth: "A"}})};
    return subscribed;
  },
};
navigator.serviceWorker = {
  register: async (url) => { registered.push(url); return {pushManager: pushManager}; },
};
window.PushManager = function () {};
globalThis.Notification = {get permission() { return permission; }};
"""

#: The pill's button, found by its label (the label is a span inside it).
_CLICK = r"""
function button(label) {
  return find(element("screen"), (n) => n.tagName === "BUTTON" && n.textContent === label);
}
"""


def _run(answers: dict, case: str, browser: str = _BROWSER) -> dict:
    harness = (
        page_tests._PRELUDE
        + browser
        + f"answers = {json.dumps(answers)};\n"
        + _script()
        + page_tests._HELPERS
        + _CLICK
        + "(async () => {\n  await settle();\n"
        + case
        + "\n})().catch((err) => say({harness_error: String(err), stack: String(err.stack)}));\n"
    )
    out = subprocess.run([*ENGINE, harness], capture_output=True, text=True, timeout=60,
                         check=False)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert "harness_error" not in result, result
    return result


def _view(available=True, on=False, line=None):
    if not available:
        return panel.push_view({"available": False, "why": line or "http"})
    return panel.push_view({"available": True, "key": _KEY, "on": on, "error": None})


@pytest.mark.skipif(ENGINE is None, reason="no JavaScript engine runs here")
class TestTheNotificationsRow:
    def test_off_it_offers_turn_on_and_registers_the_worker(self):
        result = _run({"/api/state": [200, _push_state(_view())]}, r"""
  await settle();
  const row = withClass(screen(), "push")[0];
  say({label: texts(screen(), "section-label").pop(), row: texts(row, "row-line"),
       pill: texts(row, "pill"), registered: registered});""")
        assert result == {"label": "Notifications", "row": [panel.WORDS["push_off"]],
                          "pill": ["Turn on"], "registered": ["/sw.js"]}

    def test_turn_on_subscribes_with_the_key_and_posts_the_subscription(self):
        result = _run({"/api/state": [200, _push_state(_view())],
                       "/api/push/subscribe": [200, {"on": True}]}, r"""
  await settle();
  button("Turn on").fire("click");
  await settle();
  const options = subscribed.options;
  say({posted: posted, visible: options.userVisibleOnly,
       key: Array.from(options.applicationServerKey).slice(0, 3),
       keyLength: options.applicationServerKey.length,
       fetched: fetched.filter((u) => u.startsWith("/api/push"))});""")
        assert result["posted"] == [{"endpoint": "https://web.push.apple.com/E1",
                                     "keys": {"p256dh": "P", "auth": "A"}}]
        assert result["visible"] is True
        assert result["key"] == [4, 0, 1] and result["keyLength"] == 65
        assert result["fetched"] == ["/api/push/subscribe"]

    def test_on_here_it_says_so_and_turn_off_unsubscribes(self):
        result = _run({"/api/state": [200, _push_state(_view(on=True))],
                       "/api/push/unsubscribe": [200, {"removed": 1}]}, r"""
  await pushManager.subscribe({});
  await refresh();
  const row = withClass(screen(), "push")[0];
  const shown = {line: texts(row, "row-line"), pill: texts(row, "pill")};
  button("Turn off").fire("click");
  await settle();
  say({shown: shown, posted: posted, subscribed: subscribed});""")
        assert result == {"shown": {"line": [panel.WORDS["push_on"]], "pill": ["Turn off"]},
                          "posted": [{"endpoint": "https://web.push.apple.com/E1"}],
                          "subscribed": None}

    def test_on_for_the_device_but_not_in_this_browser_is_off_here(self):
        result = _run({"/api/state": [200, _push_state(_view(on=True))]}, r"""
  await settle();
  const row = withClass(screen(), "push")[0];
  say({line: texts(row, "row-line"), pill: texts(row, "pill")});""")
        assert result == {"line": [panel.WORDS["push_off"]], "pill": ["Turn on"]}

    def test_a_refused_permission_says_the_browser_blocks_them(self):
        result = _run({"/api/state": [200, _push_state(_view())]}, r"""
  await settle();
  refuse = {name: "NotAllowedError", message: "denied"};
  button("Turn on").fire("click");
  await settle();
  const row = withClass(screen(), "push")[0];
  say({line: texts(row, "row-line"), pill: texts(row, "pill"), posted: posted});""")
        assert result == {"line": [panel.WORDS["push_blocked"]], "pill": [], "posted": []}

    def test_a_refusal_from_the_mac_is_shown(self):
        result = _run({"/api/state": [200, _push_state(_view())],
                       "/api/push/subscribe": [400, {"error": "not a known push service"}]},
                      r"""
  await settle();
  button("Turn on").fire("click");
  await settle();
  say({line: texts(withClass(screen(), "push")[0], "row-line")});""")
        assert result == {"line": [panel.WORDS["push_failed"].format(
            error="not a known push service")]}

    def test_a_key_from_an_older_subscription_is_replaced(self):
        result = _run({"/api/state": [200, _push_state(_view())],
                       "/api/push/subscribe": [200, {"on": True}]}, r"""
  await settle();
  let first = true;
  const real = pushManager.subscribe;
  subscribed = {endpoint: "old", unsubscribe: async () => { subscribed = null; return true; }};
  pushManager.subscribe = async (options) => {
    if (first) { first = false; const e = new Error("other key"); e.name = "InvalidStateError";
                 throw e; }
    return real(options);
  };
  button("Turn on").fire("click");
  await settle();
  say({endpoint: subscribed.endpoint, posted: posted.length});""")
        assert result == {"endpoint": "https://web.push.apple.com/E1", "posted": 1}

    def test_a_mac_that_cannot_send_says_why_and_offers_nothing(self):
        result = _run({"/api/state": [200, _push_state(_view(available=False,
                                                             line="extra"))]}, r"""
  await settle();
  const row = withClass(screen(), "push")[0];
  say({line: texts(row, "row-line"), pill: texts(row, "pill"), registered: registered});""")
        assert result == {"line": [panel.WORDS["push_no_extra"]], "pill": [],
                          "registered": []}

    def test_an_iphone_outside_the_home_screen_app_is_told_to_add_it(self):
        result = _run({"/api/state": [200, _push_state(_view())]}, r"""
  await settle();
  const row = withClass(screen(), "push")[0];
  say({line: texts(row, "row-line"), pill: texts(row, "pill")});""",
                      browser="navigator.standalone = false;\n")
        assert result == {"line": [panel.WORDS["push_home_screen"]], "pill": []}

    def test_a_browser_without_push_says_it_cannot(self):
        result = _run({"/api/state": [200, _push_state(_view())]}, r"""
  await settle();
  say({line: texts(withClass(screen(), "push")[0], "row-line")});""", browser="")
        assert result == {"line": [panel.WORDS["push_unsupported"]]}

    def test_a_worker_that_will_not_register_is_shown(self):
        result = _run({"/api/state": [200, _push_state(_view())]}, r"""
  await settle();
  say({line: texts(withClass(screen(), "push")[0], "row-line")});""",
                      browser=_BROWSER + r"""
navigator.serviceWorker.register = async () => { throw new Error("no worker here"); };
""")
        assert result == {"line": [panel.WORDS["push_failed"].format(error="no worker here")]}

    def test_no_row_without_a_push_view(self):
        result = _run({"/api/state": [200, _push_state(None)]}, r"""
  say({rows: withClass(screen(), "push").length, registered: registered});""")
        assert result == {"rows": 0, "registered": []}


_WORKER = r"""
const shown = [];
const focused = [];
const opened = [];
const handlers = {};
let windows = [];
globalThis.self = {
  addEventListener: (name, fn) => { handlers[name] = fn; },
  registration: {showNotification: async (title, options) => { shown.push({title, options}); }},
  clients: {
    matchAll: async () => windows,
    openWindow: async (url) => { opened.push(url); },
  },
};
const say = (value) => (typeof print === "function" ? print : console.log)(JSON.stringify(value));
function event(data) {
  const waits = [];
  return {data: data, waitUntil: (p) => waits.push(p), notification: {close() {}}, waits};
}
"""


def _worker(case: str) -> dict:
    harness = (_WORKER + webpage.service_worker() + "\n(async () => {\n" + case
               + "\n})().catch((err) => say({harness_error: String(err)}));\n")
    out = subprocess.run([*ENGINE, harness], capture_output=True, text=True, timeout=60,
                         check=False)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert "harness_error" not in result, result
    return result


@pytest.mark.skipif(ENGINE is None, reason="no JavaScript engine runs here")
class TestTheServiceWorker:
    def test_a_push_shows_the_banners_text(self):
        result = _worker(r"""
  const e = event({json: () => ({title: "invariant: Heap", subtitle: "band height",
                                  message: "Claude asked for permission", silent: false})});
  handlers.push(e);
  await Promise.all(e.waits);
  say(shown);""")
        assert result == [{"title": "invariant: Heap", "options": {
            "body": "band height\nClaude asked for permission", "silent": False,
            "icon": "/icon-192.png", "data": {"url": "/"}}}]

    def test_a_push_without_a_readable_payload_still_shows_something(self):
        result = _worker(r"""
  const e = event({json: () => { throw new Error("not json"); }});
  handlers.push(e);
  const empty = event(null);
  handlers.push(empty);
  await Promise.all([...e.waits, ...empty.waits]);
  say(shown.map((s) => [s.title, s.options.body, s.options.silent]));""")
        assert result == [["invariant", "", False], ["invariant", "", False]]

    def test_a_tap_focuses_the_open_panel_or_opens_it(self):
        result = _worker(r"""
  const first = event(null);
  handlers.notificationclick(first);
  await Promise.all(first.waits);
  windows = [{focus: async () => { focused.push(1); }}];
  const second = event(null);
  handlers.notificationclick(second);
  await Promise.all(second.waits);
  say({opened: opened, focused: focused});""")
        assert result == {"opened": ["/"], "focused": [1]}


# -- the CLI ----------------------------------------------------------------------------


@needs_crypto
class TestTheCommands:
    def test_web_hands_the_page_the_push_book(self, capsys, oracle_web):
        oracle_web.addresses = [TS4]
        code, _ = run_cli(capsys, "oracle", "web")
        assert code == 0
        app = oracle_web.servers[0].app
        assert app.push is not None and app.push.home == oracle_home()
        assert app.push_why is None

    def test_web_without_the_extra_says_why(self, capsys, oracle_web, monkeypatch):
        monkeypatch.setitem(sys.modules, "cryptography", None)
        oracle_web.addresses = [TS4]
        code, _ = run_cli(capsys, "oracle", "web")
        assert code == 0
        app = oracle_web.servers[0].app
        assert app.push is None and app.push_why == "extra"

    def test_revoke_drops_the_devices_subscriptions(self, capsys):
        home = oracle_home()
        store = DeviceStore(home)
        _paired(store)
        device = store.devices()[0]
        book = push.PushBook(home)
        book.subscribe(device.id, push.check_subscription(_subscription()), ORIGIN)
        code, out = run_cli(capsys, "oracle", "web", "revoke", device.id)
        assert code == 0 and "its notifications stop" in out
        assert book.subscriptions() == []

    def test_devices_says_who_has_notifications_on(self, capsys):
        home = oracle_home()
        store = DeviceStore(home)
        _paired(store)
        device = store.devices()[0]
        push.PushBook(home).subscribe(device.id, push.check_subscription(_subscription()),
                                      ORIGIN)
        code, out = run_cli(capsys, "oracle", "web", "devices")
        assert code == 0 and "notifications on" in out
        code, out = run_cli(capsys, "oracle", "web", "devices", "--json")
        assert json.loads(out)["devices"][0]["push"] == 1

    def test_devices_with_an_unreadable_book_still_lists_them(self, capsys):
        home = oracle_home()
        _paired(DeviceStore(home))
        (home / push.PUSH_FILE).write_text("[]")
        code, out = run_cli(capsys, "oracle", "web", "devices", "--json")
        assert code == 0 and json.loads(out)["devices"][0]["push"] is None

    def test_the_real_notifier_fans_out_to_the_mac_and_push(self, monkeypatch):
        notifier = cli_main._real_oracle_notifier()
        assert isinstance(notifier, FanOutNotifier)
        names = [name for name, _ in notifier.channels]
        assert names == ["mac", "push"]
        assert isinstance(notifier.channels[0][1], OsascriptNotifier)
        channel = notifier.channels[1][1]
        assert isinstance(channel, push.PushNotifier)
        assert channel.book.home == oracle_home()

    def test_without_the_extra_the_real_notifier_is_the_mac_alone(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "cryptography", None)
        notifier = cli_main._real_oracle_notifier()
        assert [name for name, _ in notifier.channels] == ["mac"]

    def test_revoke_with_an_unreadable_book_still_revokes(self, capsys):
        home = oracle_home()
        store = DeviceStore(home)
        _paired(store)
        device = store.devices()[0]
        (home / push.PUSH_FILE).write_text("[]")
        code, out = run_cli(capsys, "oracle", "web", "revoke", device.id, "--json")
        assert code == 0 and json.loads(out)["push_dropped"] == 0
        assert store.devices() == []

    def test_the_real_push_post_is_urllib(self):
        assert cli_main._real_oracle_push_post() is push.urllib_post

    def test_the_push_post_seam_is_replaced_for_every_test(self):
        with pytest.raises(AssertionError, match="network"):
            cli_main._oracle_push_post()("https://web.push.apple.com/x", b"", {}, 1.0)


class _Fan:
    """A fan-out the CLI's notify-test can be handed: scripted outcomes."""

    def __init__(self, outcomes, fail=None):
        self.last = []
        self.outcomes = outcomes
        self.fail = fail
        self.log = None
        self.channels = []

    def notify(self, notification):
        self.last = list(self.outcomes)
        if self.fail:
            from invariant.oracle.notify import NotifyError

            raise NotifyError(self.fail)


class TestNotifyTest:
    def test_it_reports_every_channel(self, capsys, monkeypatch):
        fan = _Fan([Outcome("mac", "", True, "sent"), Outcome("push", "iPhone", True, "sent")])
        monkeypatch.setattr(cli_main, "_oracle_notifier", lambda: fan)
        code, out = run_cli(capsys, "oracle", "notify-test")
        assert code == 0
        assert "Mac banner: sent" in out and "push to iPhone: sent" in out
        code, out = run_cli(capsys, "oracle", "notify-test", "--json")
        assert json.loads(out) == {"sent": True, "channels": [
            {"channel": "mac", "target": "", "ok": True, "detail": "sent"},
            {"channel": "push", "target": "iPhone", "ok": True, "detail": "sent"}]}

    def test_one_channel_failing_exits_1_naming_it(self, capsys, monkeypatch):
        fan = _Fan([Outcome("mac", "", True, "sent"),
                    Outcome("push", "iPhone", False, "the push service answered 503")])
        monkeypatch.setattr(cli_main, "_oracle_notifier", lambda: fan)
        code = main(["oracle", "notify-test"])
        captured = capsys.readouterr()
        assert code == 1
        assert "Mac banner: sent" in captured.out
        assert "push to iPhone: FAILED: the push service answered 503" in captured.out
        assert "not every channel delivered" in captured.err

    def test_nobody_on_push_is_said_and_is_not_a_failure(self, capsys, monkeypatch):
        fan = _Fan([Outcome("mac", "", True, "sent"),
                    Outcome("push", "", None, "no device has turned push on")])
        monkeypatch.setattr(cli_main, "_oracle_notifier", lambda: fan)
        code, out = run_cli(capsys, "oracle", "notify-test")
        assert code == 0 and "push: no device has turned push on" in out

    def test_every_channel_failing_is_the_old_error(self, capsys, monkeypatch):
        fan = _Fan([Outcome("mac", "", False, "osascript refused")], fail="mac: osascript")
        monkeypatch.setattr(cli_main, "_oracle_notifier", lambda: fan)
        code = main(["oracle", "notify-test"])
        assert code == 1
        assert "the test notification was not delivered" in capsys.readouterr().err

    def test_push_unavailable_is_said(self, capsys, monkeypatch):
        fan = _Fan([Outcome("mac", "", True, "sent")])
        monkeypatch.setattr(cli_main, "_oracle_notifier", lambda: fan)
        monkeypatch.setitem(sys.modules, "cryptography", None)
        code, out = run_cli(capsys, "oracle", "notify-test")
        assert code == 0 and "push: not available: web push needs the cryptography" in out

    def test_the_suites_recorder_still_works(self, capsys, _no_real_notifier):
        code, out = run_cli(capsys, "oracle", "notify-test")
        assert code == 0 and "sent one notification" in out
        assert _no_real_notifier.sent[0].title == "invariant oracle"


class TestWatchLogsAFailedChannel:
    def test_watch_hands_the_fan_out_its_log(self, capsys, monkeypatch):
        fan = FanOutNotifier([])
        monkeypatch.setattr(cli_main, "_oracle_notifier", lambda: fan)
        code = main(["oracle", "watch", "--once", "--no-chain"])
        assert code == 0
        assert fan.log is not None
        fan.log("push to iPhone could not notify: 503")
        assert "oracle watch: push to iPhone could not notify: 503" in capsys.readouterr().err
