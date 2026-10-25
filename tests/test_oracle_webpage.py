"""The control panel page, its script run whole against a fake DOM (IDEA_027 inc 3).

``test_oracle_web`` drives the refresh loop's logic; this drives the page: the
real script, on a document that records what is built, fed the real view of
``oracle/panel``. The fake document refuses ``innerHTML``, so a page that builds
from markup fails here. Each case scripts the server's answers and steps the
timers by hand, so nothing sleeps:

* the overview: the strip, the groups in order, a waiting card with no button;
* the refresh keeps the open detail, an open disclosure and the scroll
  position, and falls back to the overview when the session has gone;
* the confirm sheet: Cancel focused, the deed named, the countdown that
  disables it, and an action that posts only the row and the nonce;
* copy over HTTPS and over plain HTTP; the leftovers screen; the stale line.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from test_oracle_web import ENGINE, _script

from invariant.oracle import panel

pytestmark = pytest.mark.skipif(ENGINE is None, reason="no JavaScript engine runs here")

NOW = 1_792_640_000.0

#: A document with one session waiting on a prompt, one failing its tests and
#: one idle, plus four orphan hosts and a leaked worktree.
_ROWS = [
    {"kind": "session", "project": "K", "project_name": "Heap", "root": "/r",
     "session_id": "w1", "name": "mobile-panel-w1", "state": "isolated", "stage": "working",
     "worktree": "/r/wt/w1", "branch": "session/w1", "origin": "/r",
     "hosts": [{"host_session_id": "hw", "current": True, "pid": 1, "alive": True,
                "attention": {"waiting": {"kind": "permission",
                                          "since": "2026-10-21T10:00:00+00:00"},
                              "turn": "running"}}],
     "signals": [{"kind": "waiting", "cause": "permission", "alert": True,
                  "severity": "high"}],
     "recommendation": {"label": "answer it", "kind": "info", "argv": [], "cwd": None,
                        "env": {}}},
    {"kind": "session", "project": "K", "project_name": "Heap", "root": "/r",
     "session_id": "t1", "name": "band-height-t1", "state": "isolated", "stage": "working",
     "worktree": "/r/wt/t1", "branch": "session/t1", "origin": "/r", "hosts": [],
     "pipeline": {"methodology": "bugfix", "state": "fixing", "terminal": False,
                  "last_test": {"passed": False, "failed": 52}},
     "signals": [{"kind": "error", "cause": "tests", "alert": True, "severity": "high"}],
     "recommendation": {"label": "re-run", "kind": "run",
                        "argv": ["invariant", "pipeline", "test"], "cwd": "/r/wt/t1",
                        "env": {"INVARIANT_SESSION_ID": "t1"}}},
    {"kind": "session", "project": "K", "project_name": "Heap", "root": "/r",
     "session_id": "i1", "name": "quiet-i1", "state": "isolated", "stage": "working",
     "hosts": [], "signals": [], "recommendation": None},
    *[{"kind": "host", "project": "K", "hosts": [{"host_session_id": f"h{n}0000000",
                                                  "alive": True}],
       "signals": [], "recommendation": None} for n in range(4)],
    {"kind": "worktree", "project": "K", "path": "/r/wt/leak", "branch": "session/leak",
     "hosts": [], "signals": [], "recommendation": None},
]


def _state(rows=None, age=1.0, acting=True, jobs=()):
    document = {"now": NOW, "projects": [{"project": {"key": "K", "name": "Heap",
                                                      "root": "/r"},
                                          "rows": list(_ROWS if rows is None else rows)}]}
    return {"document": {}, "view": panel.view(document, acting=acting, jobs=jobs),
            "freshness": {"age": age, "interval": 5, "stale_after": 15, "banner": ""},
            "jobs": list(jobs)}


_CONFIRM = {
    "row": "K|t1", "nonce": "N1", "refused": None, "kind": "run", "lines": [],
    "subject": "", "expires_in": 120,
    "words": panel.confirm_words(_ROWS[1], "Heap", ("invariant", "pipeline", "test"), "run",
                                 "/r/wt/t1", {"INVARIANT_SESSION_ID": "t1"}, launch=None),
}

_JOB = {"id": "j1", "row": "K|t1", "argv": ["invariant", "pipeline", "test"], "kind": "run",
        "state": "done", "ok": False, "status": 2, "elapsed": 3.0, "output": "boom\n",
        "remote_control": None, "audit_error": ""}
_JOB["words"] = {**panel.result_words(_JOB), "why": "Failed, exit 2. boom"}

#: A document that records what is built. Enough DOM for the page and no more.
_PRELUDE = r"""
const registry = {};
const posted = [];
const fetched = [];
const timers = [];
const intervals = [];
const copied = [];
const selected = [];
let focused = null;
let fakeNow = 1792640000000;
let secure = true;
let answers = {};
class FakeNode {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.attrs = {}; this.listeners = {};
    this.own = ""; this.hidden = false; this.disabled = false; this.open = false;
    this.className = "";
  }
  setAttribute(name, value) {
    this.attrs[name] = String(value);
    if (name === "class") this.className = String(value);
    if (name === "id") registry[value] = this;
  }
  getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
  set textContent(value) { this.children = []; this.own = String(value); }
  get textContent() { return this.own + this.children.map((c) => c.textContent).join(""); }
  set innerHTML(value) { throw new Error("innerHTML was used"); }
  get firstChild() { return this.children[0] || null; }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; this.own = ""; }
  addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); }
  fire(name, event) {
    for (const fn of this.listeners[name] || []) fn(event || {preventDefault() {}});
  }
  focus() { focused = this; }
}
function element(id) {
  if (!registry[id]) { registry[id] = new FakeNode("div"); registry[id].attrs.id = id; }
  return registry[id];
}
const docListeners = {};
const winListeners = {};
globalThis.document = {
  createElement: (tag) => new FakeNode(tag),
  createElementNS: (ns, tag) => new FakeNode(tag),
  getElementById: element,
  addEventListener: (name, fn) => { (docListeners[name] = docListeners[name] || []).push(fn); },
  createRange: () => ({selectNodeContents: (node) => selected.push(node.textContent)}),
  visibilityState: "visible",
  title: "",
};
globalThis.window = {
  scrollY: 0,
  scrollTo: (x, y) => { window.scrollY = y; },
  addEventListener: (name, fn) => { (winListeners[name] = winListeners[name] || []).push(fn); },
  getSelection: () => ({removeAllRanges() {}, addRange() {}}),
  get isSecureContext() { return secure; },
};
globalThis.history = {
  state: null, stack: [],
  pushState(state) { this.stack.push(this.state); this.state = state; },
  back() {
    this.state = this.stack.pop() || null;
    for (const fn of winListeners.popstate || []) fn({state: this.state});
  },
};
globalThis.navigator = {clipboard: {writeText: (text) => { copied.push(text); return Promise.resolve(); }}};
globalThis.location = {reloads: 0, reload() { this.reloads += 1; }};
Date.now = () => fakeNow;
globalThis.setTimeout = (fn, ms) => { timers.push({fn: fn, ms: ms}); return timers.length; };
globalThis.clearTimeout = () => {};
globalThis.setInterval = (fn, ms) => { intervals.push(fn); return intervals.length; };
globalThis.clearInterval = (handle) => { intervals[handle - 1] = null; };
globalThis.fetch = async (url, options) => {
  fetched.push(url);
  if (options && options.method === "POST") posted.push(JSON.parse(options.body));
  const key = Object.keys(answers).find((prefix) => url.startsWith(prefix));
  const [status, body] = answers[key];
  return {status: status, ok: status >= 200 && status < 300, type: "basic",
          json: async () => body};
};
"""

#: Helpers for the cases: walk what was built, settle the promises, step timers.
_HELPERS = r"""
function all(node, out) {
  out = out || [];
  out.push(node);
  for (const child of node.children) all(child, out);
  return out;
}
function find(root, test) { return all(root).find(test) || null; }
function byText(root, text) { return find(root, (n) => n.own === text); }
function withClass(root, cls) {
  return all(root).filter((n) => (" " + n.className + " ").includes(" " + cls + " "));
}
function texts(root, cls) { return withClass(root, cls).map((n) => n.textContent); }
async function settle() { for (let i = 0; i < 60; i++) await null; }
async function refresh() {
  const timer = timers.filter((t) => t.ms >= 1000 && t.ms % 1000 === 0).pop();
  timers.length = 0;
  timer.fn();
  await settle();
}
function tickIntervals() { for (const fn of intervals) if (fn) fn(); }
const screen = () => element("screen");
const sheet = () => element("sheet");
const say = (value) => (typeof print === "function" ? print : console.log)(JSON.stringify(value));
"""


def _run(answers: dict, case: str) -> dict:
    harness = (
        _PRELUDE
        + f"answers = {json.dumps(answers)};\n"
        + _script()
        + _HELPERS
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


class TestTheOverview:
    def test_the_strip_the_groups_and_a_waiting_card_without_a_button(self):
        result = _run({"/api/state": [200, _state()]}, r"""
  const waiting = find(screen(), (n) => n.className === "card tap");
  say({
    tiles: texts(screen(), "tile"),
    headings: texts(screen(), "heading"),
    hrefs: withClass(screen(), "tile").map((n) => n.attrs.href),
    waitingButtons: withClass(waiting, "btn").length,
    waitingRole: waiting.attrs.role,
    primary: texts(screen(), "primary"),
    leftovers: texts(screen(), "leftovers-card"),
    title: document.title,
    fresh: element("freshtext").textContent,
    freshClass: element("fresh").className,
  });""")
        assert result["tiles"] == ["1Need you", "1Problems", "0Working", "5Leftovers"]
        assert result["headings"] == ["Needs you", "Problems", "Idle"]
        assert result["hrefs"] == ["#needs", "#problems", "#working", "#leftovers"]
        assert result["waitingButtons"] == 0 and result["waitingRole"] == "button"
        assert result["primary"] == ["Re-run tests…"]
        assert result["leftovers"] == [
            "5 leftovers to tidy4 Claude windows with no session, 1 left-over folder"]
        assert result["title"] == "(2) Sessions"
        assert result["fresh"] == "Live · updated 1 s ago"
        assert result["freshClass"] == "fresh"

    def test_a_read_only_page_has_no_primary_button(self):
        result = _run({"/api/state": [200, _state(acting=False)]}, r"""
  say({primary: texts(screen(), "primary"), details: texts(screen(), "secondary")});""")
        assert result == {"primary": [], "details": ["Details"]}


class TestTheRefreshKeepsThePlace:
    def test_the_open_detail_disclosure_and_scroll_survive_a_refresh(self):
        result = _run({"/api/state": [200, _state()]}, r"""
  byText(screen(), "Details").fire("click");
  const opened = {title: texts(screen(), "detail-title"), pushed: history.state};
  const tech = find(screen(), (n) => n.tagName === "DETAILS");
  tech.open = true;
  tech.fire("toggle");
  window.scrollY = 300;
  await refresh();
  const after = find(screen(), (n) => n.tagName === "DETAILS");
  say({
    opened: opened,
    title: texts(screen(), "detail-title"),
    going: texts(screen(), "body"),
    techOpen: after.open,
    techText: after.textContent,
    scroll: window.scrollY,
    fetches: fetched.filter((u) => u === "/api/state").length,
    header: element("top").className,
  });""")
        assert result["opened"] == {"title": ["Band height"],
                                    "pushed": {"screen": "detail", "key": "K|t1"}}
        assert result["title"] == ["Band height"]
        assert result["going"] == ["The last test run had 52 failures."]
        assert result["techOpen"] is True
        assert "Session t1 · state isolated · lane bugfix:fixing" in result["techText"]
        assert result["scroll"] == 300
        assert result["fetches"] == 2
        assert result["header"] == "sub"

    def test_a_session_that_has_gone_falls_back_to_the_overview(self):
        gone = _state(rows=[r for r in _ROWS if r.get("session_id") != "t1"])
        result = _run({"/api/state": [200, _state()]}, r"""
  byText(screen(), "Details").fire("click");
  answers["/api/state"] = [200, GONE];
  await refresh();
  say({headings: texts(screen(), "heading"), detail: texts(screen(), "detail-title")});
""".replace("GONE", json.dumps(gone)))
        assert result == {"headings": ["Needs you", "Idle"], "detail": []}

    def test_back_returns_to_the_overview_where_it_was(self):
        result = _run({"/api/state": [200, _state()]}, r"""
  window.scrollY = 120;
  byText(screen(), "Details").fire("click");
  const top = window.scrollY;
  find(screen(), (n) => n.className === "back").fire("click");
  say({top: top, scroll: window.scrollY, headings: texts(screen(), "heading"),
       state: history.state});""")
        assert result == {"top": 0, "scroll": 120,
                          "headings": ["Needs you", "Problems", "Idle"], "state": None}

    def test_a_waiting_card_opens_its_detail_and_a_stale_read_says_so(self):
        result = _run({"/api/state": [200, _state(age=40.0)]}, r"""
  find(screen(), (n) => n.className === "card tap").fire("click");
  say({title: texts(screen(), "detail-title"), step: texts(screen(), "step-name"),
       fresh: element("freshtext").textContent, cls: element("fresh").className});""")
        assert result == {"title": ["Mobile panel"], "step": [],
                          "fresh": "Stale · updated 40 s ago", "cls": "fresh stale"}

    def test_a_failed_refresh_says_so_and_backs_off(self):
        result = _run({"/api/state": [503, {"error": "the index is broken"}]}, r"""
  say({error: element("error").textContent, hidden: element("error").hidden,
       cls: element("fresh").className, fresh: element("freshtext").textContent,
       delay: timers[timers.length - 1].ms});""")
        assert result == {
            "error": "Refresh failed 1 times in a row (the index is broken). Retrying.",
            "hidden": False, "cls": "fresh stale", "fresh": "Stale · not updated yet",
            "delay": 10000}


class TestTheConfirmSheet:
    def test_it_leads_with_the_question_focuses_cancel_and_expires(self):
        result = _run({"/api/state": [200, _state()], "/api/confirm": [200, _CONFIRM]}, r"""
  byText(screen(), "Re-run tests…").fire("click");
  await settle();
  const shown = {
    hidden: sheet().hidden, title: texts(sheet(), "sheet-title"),
    buttons: withClass(sheet(), "sheet").map((n) => n.textContent),
    focused: focused.textContent, command: texts(sheet(), "command-text"),
    place: texts(sheet(), "inset")[0], expiry: texts(sheet(), "expiry"),
  };
  await refresh();
  const kept = {hidden: sheet().hidden, title: texts(sheet(), "sheet-title")};
  fakeNow += 121000;
  tickIntervals();
  const run = withClass(sheet(), "primary")[0];
  say({shown: shown, kept: kept, expired: texts(sheet(), "expiry"), disabled: run.disabled,
       posted: posted});""")
        shown = result["shown"]
        assert shown["hidden"] is False
        assert shown["title"] == ["Re-run the tests?"]
        assert shown["buttons"] == ["Run tests", "Cancel"]
        assert shown["focused"] == "Cancel"
        assert shown["command"] == ["INVARIANT_SESSION_ID=t1 invariant pipeline test"]
        assert "In Heap › session folder t1" in shown["place"]
        assert shown["expiry"] == ["This confirm expires in 2:00"]
        assert result["kept"] == {"hidden": False, "title": ["Re-run the tests?"]}
        assert result["expired"] == ["This confirm expired.Look again"]
        assert result["disabled"] is True
        assert result["posted"] == []

    def test_the_deed_posts_only_the_row_and_the_nonce_and_shows_the_result(self):
        result = _run({"/api/state": [200, _state()], "/api/confirm": [200, _CONFIRM],
                       "/api/act": [202, {"job": _JOB}]}, r"""
  byText(screen(), "Re-run tests…").fire("click");
  await settle();
  withClass(sheet(), "primary")[0].fire("click");
  await settle();
  const output = find(sheet(), (n) => n.tagName === "DETAILS");
  say({posted: posted, headline: texts(sheet(), "result-title"),
       line: texts(sheet(), "step").map((t) => t), outputOpen: output.open,
       outputText: output.textContent, focused: focused.textContent});""")
        assert result["posted"] == [{"row": "K|t1", "nonce": "N1"}]
        assert result["headline"] == ["Failed, exit 2"]
        assert result["outputOpen"] is False
        assert result["outputText"] == "Show full outputboom\n"
        assert result["focused"] == "Done"

    def test_a_failed_result_says_why_above_the_closed_output(self):
        result = _run({"/api/state": [200, _state()], "/api/confirm": [200, _CONFIRM],
                       "/api/act": [202, {"job": _JOB}]}, r"""
  byText(screen(), "Re-run tests…").fire("click");
  await settle();
  withClass(sheet(), "primary")[0].fire("click");
  await settle();
  const order = sheet().children.map((n) => n.className || n.tagName);
  say({why: texts(sheet(), "why"), order: order});""")
        assert result["why"] == ["WhyFailed, exit 2. boom"]
        order = result["order"]
        assert order.index("inset why") < order.index("disclosure inset")

    def test_a_successful_result_has_no_why(self):
        done = {**_JOB, "ok": True, "status": 0, "output": "ok\n"}
        done["words"] = {**panel.result_words(done), "why": ""}
        result = _run({"/api/state": [200, _state()], "/api/confirm": [200, _CONFIRM],
                       "/api/act": [202, {"job": done}]}, r"""
  byText(screen(), "Re-run tests…").fire("click");
  await settle();
  withClass(sheet(), "primary")[0].fire("click");
  await settle();
  say({why: texts(sheet(), "why"), headline: texts(sheet(), "result-title")});""")
        assert result == {"why": [], "headline": ["Done in 3 s"]}

    def test_a_refused_confirm_says_why_and_offers_no_deed(self):
        refused = {**_CONFIRM, "nonce": None, "words": None, "refused": "its host is waiting"}
        result = _run({"/api/state": [200, _state()], "/api/confirm": [200, refused]}, r"""
  byText(screen(), "Re-run tests…").fire("click");
  await settle();
  say({said: texts(sheet(), "sentence"), primary: withClass(sheet(), "primary").length});""")
        assert result == {"said": ["Not run: its host is waiting"], "primary": 0}

    def test_cancel_closes_the_sheet(self):
        result = _run({"/api/state": [200, _state()], "/api/confirm": [200, _CONFIRM]}, r"""
  byText(screen(), "Re-run tests…").fire("click");
  await settle();
  byText(sheet(), "Cancel").fire("click");
  say({hidden: sheet().hidden, scrim: element("scrim").hidden, empty: sheet().children.length});""")
        assert result == {"hidden": True, "scrim": True, "empty": 0}


class TestCopy:
    def test_over_https_the_clipboard_gets_the_command(self):
        result = _run({"/api/state": [200, _state()]}, r"""
  byText(screen(), "Details").fire("click");
  find(screen(), (n) => n.attrs["aria-label"] === "Copy command").fire("click");
  await settle();
  say({copied: copied, selected: selected,
       label: find(screen(), (n) => n.className === "btn secondary square").attrs["aria-label"]});""")
        assert result == {"copied": ["INVARIANT_SESSION_ID=t1 invariant pipeline test"],
                          "selected": [], "label": "Copied"}

    def test_over_plain_http_the_command_is_shown_and_selected(self):
        result = _run({"/api/state": [200, _state()]}, r"""
  secure = false;
  byText(screen(), "Details").fire("click");
  const target = find(screen(), (n) => n.className === "mono scroll copy-text");
  const before = target.hidden;
  find(screen(), (n) => n.attrs["aria-label"] === "Copy command").fire("click");
  say({copied: copied, selected: selected, before: before, after: target.hidden});""")
        assert result == {"copied": [],
                          "selected": ["INVARIANT_SESSION_ID=t1 invariant pipeline test"],
                          "before": True, "after": False}


class TestTheLeftovers:
    def test_the_tile_opens_them_and_show_more_lists_the_rest(self):
        result = _run({"/api/state": [200, _state()]}, r"""
  let prevented = false;
  withClass(screen(), "tile-leftovers")[0].fire("click", {preventDefault() { prevented = true; }});
  const first = {title: texts(screen(), "detail-title"), rows: texts(screen(), "row-title").length,
                 more: texts(screen(), "more")};
  find(screen(), (n) => n.className === "more").fire("click");
  say({prevented: prevented, first: first, rows: texts(screen(), "row-title").length,
       sections: texts(screen(), "section-label"), more: texts(screen(), "more")});""")
        assert result["prevented"] is True
        assert result["first"] == {"title": ["Leftovers"], "rows": 4, "more": ["Show 1 more"]}
        assert result["rows"] == 5
        assert result["sections"] == ["Claude windows with no session", "Left-over folders"]
        assert result["more"] == []


class TestTheMarkup:
    def test_the_header_is_worded_from_the_table_and_the_words_cannot_end_the_script(self):
        from invariant.oracle import webpage

        words = {**panel.WORDS, "title": "</script><b>x</b>"}
        page = webpage.page("n", words)
        script = page[page.index('<script nonce="n">'):]
        assert "</script><b>" not in script[:-len("</script></body></html>\n")]
        assert "&lt;/script&gt;&lt;b&gt;x&lt;/b&gt;" in page
        assert 'aria-label="Refresh now"' in page
        assert "color-scheme: dark" in page
        assert "word-break: normal" in page and "overflow-wrap: normal" in page

    def test_every_control_is_at_least_44_px(self):
        from invariant.oracle import webpage

        style = webpage._STYLE
        for rule in (".iconbtn { width: 44px; height: 44px;", ".btn { height: 44px;",
                     ".back { display: flex; align-items: center; gap: 4px; height: 44px;",
                     ".pill { min-height: 44px; min-width: 44px;",
                     ".tile { border-radius: 14px; padding: 10px 8px; min-height: 44px;"):
            assert rule in style, rule


# -- close host (IDEA_027 increment 6, ADR-009 D15) ----------------------------------------

_HOST = "27885f4e-1111-2222-3333-444455556666"
_CLOSE_ARGV = ["invariant", "oracle", "close-host", _HOST, "--pid", "41001",
               "--start", "2026-10-20T09:00:00", "--tty", "ttys004"]
_LEFTOVER = {
    "kind": "host", "project": "K", "project_name": "Heap", "root": "/r",
    "last_activity": NOW - 2 * 86400, "previous_session": "s0",
    "hosts": [{"host_session_id": _HOST, "current": True, "alive": True, "pid": 41001,
               "pid_source": "record", "tty": "ttys004"}],
    "signals": [{"kind": "orphan_host", "cause": "leftover", "alert": False,
                 "severity": "info"}],
    "recommendation": {"label": "close it", "kind": "run", "argv": _CLOSE_ARGV, "cwd": "/r",
                       "env": {}},
}
_CLOSE_CONFIRM = {
    "row": f"K|host:{_HOST}", "nonce": "N2", "refused": None, "kind": "run", "lines": [],
    "subject": "", "expires_in": 120,
    "words": panel.confirm_words(_LEFTOVER, "Heap", _CLOSE_ARGV, "run", "/r", {}, launch=None,
                                 now=NOW),
}
_CLOSE_JOB = {"id": "j2", "row": f"K|host:{_HOST}", "argv": _CLOSE_ARGV, "kind": "run",
              "state": "done", "ok": True, "status": 0, "elapsed": 4.0,
              "output": "Claude ended, and its Terminal tab closed.\n",
              "remote_control": None, "audit_error": ""}
_CLOSE_JOB["words"] = {**panel.result_words(_CLOSE_JOB), "why": "",
                       "outcome": "Claude ended, and its Terminal tab closed."}


class TestCloseFromTheLeftovers:
    def test_the_pill_opens_the_destructive_sheet_and_the_result_says_what_happened(self):
        result = _run({"/api/state": [200, _state([_LEFTOVER])],
                       "/api/confirm": [200, _CLOSE_CONFIRM],
                       "/api/act": [202, {"job": _CLOSE_JOB}]}, r"""
  withClass(screen(), "tile-leftovers")[0].fire("click", {preventDefault() {}});
  const pill = withClass(screen(), "pill")[0];
  const pillText = pill.textContent;
  pill.fire("click");
  await settle();
  const sheetShown = {
    title: texts(sheet(), "sheet-title"),
    ends: texts(sheet(), "deletes"),
    checked: texts(sheet(), "checked"),
    buttons: withClass(sheet(), "sheet").map((n) => n.textContent),
    focused: focused.textContent,
  };
  withClass(sheet(), "destructive")[0].fire("click");
  await settle();
  say({pill: pillText, sheet: sheetShown, outcome: texts(sheet(), "outcome"),
       why: texts(sheet(), "why"), posted: posted});""")
        assert result["pill"] == "Close…"
        shown = result["sheet"]
        assert shown["title"] == ["Close this Claude window?"]
        assert shown["ends"] == [("This endsClaude in Heap · window 27885f4eProcess 41001 · "
                                  "idle 2 days")]
        assert shown["checked"] == [("Checked just nowNo invariant session is using this "
                                     "Claude windowChecked again the moment you press Close "
                                     "window")]
        assert shown["buttons"] == ["Cancel", "Close window"]
        assert shown["focused"] == "Cancel"
        assert result["outcome"] == ["What happenedClaude ended, and its Terminal tab closed."]
        assert result["why"] == []
        assert result["posted"] == [{"row": f"K|host:{_HOST}", "nonce": "N2"}]

    def test_a_read_only_page_draws_no_pill(self):
        result = _run({"/api/state": [200, _state([_LEFTOVER], acting=False)]}, r"""
  withClass(screen(), "tile-leftovers")[0].fire("click", {preventDefault() {}});
  say({pills: withClass(screen(), "pill").length});""")
        assert result == {"pills": 0}
