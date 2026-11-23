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


def _state(rows=None, age=1.0, acting=True, jobs=(), excerpts=None, talking=False):
    document = {"now": NOW, "projects": [{"project": {"key": "K", "name": "Heap",
                                                      "root": "/r"},
                                          "rows": list(_ROWS if rows is None else rows)}]}
    return {"document": {}, "view": panel.view(document, acting=acting, jobs=jobs,
                                               excerpts=excerpts, talking=talking),
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
  documentElement: {scrollHeight: 2000},
};
globalThis.window = {
  scrollY: 0,
  innerHeight: 800,
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
// Not a plain assignment: under Node, globalThis.navigator is a getter with no
// setter, so assigning it is silently dropped (BUG_112).
Object.defineProperty(globalThis, "navigator", {
  value: {clipboard: {writeText: (text) => { copied.push(text); return Promise.resolve(); }}},
  configurable: true, writable: true,
});
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


# -- IDEA_030 increment 2: start a new session (ADR-009 D16) --------------------------------

_NAME = "heap-1026-0049"
_TIMEOUT = ("The window did not appear on the Mac within a minute; check the Mac (a trust "
            "prompt or a login may be waiting).")


def _start_state(rows=None, jobs=(), acting=True, projects=None):
    document = {"now": NOW, "projects": projects or [
        {"project": {"key": "K", "name": "Heap", "root": "/r"},
         "rows": list(_ROWS if rows is None else rows)},
        {"project": {"key": "L", "name": "Lark", "root": "/l"}, "rows": []}]}
    return {"document": {}, "view": panel.view(document, acting=acting, jobs=list(jobs),
                                                starting=True),
            "freshness": {"age": 1.0, "interval": 5, "stale_after": 15, "banner": ""},
            "jobs": list(jobs)}


def _prepared(name=_NAME, label="1026-0049", prompt=None, windows=2, changes=3, model=None):
    argv = ["/opt/bin/claude", "--name", name, "--remote-control", name,
            *(["--model", model] if model else []), *([prompt] if prompt else [])]
    return {"name": name, "label": label, "argv": argv, "cwd": "/r", "prompt": prompt,
            "model": model,
            "facts": {"open_windows": windows, "uncommitted": changes, "dirty": bool(changes)}}


def _start_confirm(nonce="S1", project="K", **prepared):
    shaped = _prepared(**prepared)
    return {"project": project, "nonce": nonce, "refused": None, "expires_in": 120,
            "name": shaped["name"], "label": shaped["label"],
            "words": panel.start_words(shaped, "Heap" if project == "K" else "Lark")}


def _start_job(state="done", ok=True, started=NOW - 5):
    job = {"id": "sj1", "row": "K|start", "subject": "Heap", "kind": "start",
           "argv": ["/opt/bin/claude", "--name", _NAME, "--remote-control", _NAME],
           "cwd": "/r", "started": started, "remote_control": _NAME, "state": state,
           "ok": ok if state == "done" else None, "status": None,
           "output": "claude is running\n", "elapsed": 4.0, "audit_error": "",
           "project": "K", "appeared": False}
    job["words"] = {**panel.result_words(job), "why": "", "outcome": ""}
    return job


class TestStartASession:
    def test_the_top_bar_button_opens_the_sheet_with_the_first_project(self):
        result = _run({"/api/state": [200, _start_state()],
                       "/api/start/confirm": [200, _start_confirm()]}, r"""
  const button = element("newsession");
  const before = {hidden: button.hidden};
  button.fire("click");
  await settle();
  const picker = find(sheet(), (n) => n.tagName === "SELECT");
  const label = find(sheet(), (n) => n.tagName === "INPUT");
  const prompt = find(sheet(), (n) => n.tagName === "TEXTAREA");
  const exact = find(sheet(), (n) => n.tagName === "DETAILS");
  say({before: before, fetched: fetched.filter((u) => u.startsWith("/api/start")),
       title: texts(sheet(), "sheet-title"), sentence: texts(sheet(), "sentence"),
       options: picker.children.map((o) => [o.attrs.value, o.textContent]),
       project: picker.value, label: label.value, prompt: prompt.value,
       name: texts(sheet(), "start-name"), facts: texts(sheet(), "checked"),
       exactOpen: exact.open, exact: exact.textContent,
       buttons: withClass(sheet(), "sheet").map((n) => n.textContent),
       disabled: withClass(sheet(), "primary")[0].disabled,
       focused: focused.textContent, expiry: texts(sheet(), "expiry"), posted: posted});""")
        assert result["before"] == {"hidden": False}
        assert result["fetched"] == ["/api/start/confirm?project=K&label=&prompt="]
        assert result["title"] == ["Start a new session?"]
        assert result["sentence"] == [("A new Terminal window on the Mac runs Claude with "
                                       "Remote Control in Heap's main folder.")]
        assert result["options"] == [["K", "Heap"], ["L", "Lark"]]
        assert result["project"] == "K"
        assert result["label"] == "1026-0049" and result["prompt"] == ""
        assert result["name"] == [f"Remote Control name: {_NAME}"]
        assert result["facts"] == [("Checked just now2 Claude windows already open hereMain "
                                    "folder has uncommitted changes (3 files)")]
        assert result["exactOpen"] is False
        assert f"/opt/bin/claude --name {_NAME} --remote-control {_NAME}" in result["exact"]
        assert "In Heap › main checkout" in result["exact"] and "/r" in result["exact"]
        assert result["buttons"] == ["Start session", "Cancel"]
        assert result["disabled"] is False
        assert result["focused"] == "Cancel"
        assert result["expiry"] == ["This confirm expires in 2:00"]
        assert result["posted"] == []

    def test_a_projects_shortcut_opens_the_sheet_with_it_chosen(self):
        result = _run({"/api/state": [200, _start_state()],
                       "/api/start/confirm": [200, _start_confirm(project="L")]}, r"""
  const section = find(screen(), (n) => n.className === "section projects");
  const rows = texts(section, "row-title");
  withClass(section, "pill")[1].fire("click");
  await settle();
  say({heading: texts(section, "section-label"), rows: rows,
       lines: texts(section, "row-line"), pills: texts(section, "pill"),
       fetched: fetched.filter((u) => u.startsWith("/api/start")),
       project: find(sheet(), (n) => n.tagName === "SELECT").value});""")
        assert result["heading"] == ["Projects"]
        assert result["rows"] == ["Heap", "Lark"]
        assert result["lines"] == ["3 open sessions", "No open session"]
        assert result["pills"] == ["New session…", "New session…"]
        assert result["fetched"] == ["/api/start/confirm?project=L&label=&prompt="]
        assert result["project"] == "L"

    def test_editing_asks_the_mac_again_and_the_deed_waits_for_its_answer(self):
        updated = _start_confirm(nonce="S2", name="heap-flaky", label="flaky", prompt="go on")
        result = _run({"/api/state": [200, _start_state()],
                       "/api/start/confirm": [200, _start_confirm()],
                       "/api/start": [202, {"job": _start_job(state="running")}]}, r"""
  element("newsession").fire("click");
  await settle();
  const label = find(sheet(), (n) => n.tagName === "INPUT");
  const prompt = find(sheet(), (n) => n.tagName === "TEXTAREA");
  const run = withClass(sheet(), "primary")[0];
  label.value = "flaky";
  label.fire("input");
  prompt.value = "go on";
  prompt.fire("input");
  const waiting = run.disabled;
  answers["/api/start/confirm"] = [200, UPDATED];
  const debounce = timers.filter((t) => t.ms === 400);
  timers.length = 0;
  debounce[debounce.length - 1].fn();
  await settle();
  const after = {disabled: run.disabled, name: texts(sheet(), "start-name"),
                 command: texts(sheet(), "command-text"), label: label.value};
  run.fire("click");
  await settle();
  say({waiting: waiting, debounces: debounce.length, after: after,
       fetched: fetched.filter((u) => u.startsWith("/api/start/confirm")),
       posted: posted, headline: texts(sheet(), "result-title")});""".replace(
            "UPDATED", json.dumps(updated)))
        assert result["waiting"] is True
        assert result["debounces"] == 2
        assert result["fetched"][-1] == ("/api/start/confirm?project=K&label=flaky"
                                         "&prompt=go%20on")
        assert len(result["fetched"]) == 2
        assert result["after"] == {
            "disabled": False, "name": ["Remote Control name: heap-flaky"],
            "command": ["/opt/bin/claude --name heap-flaky --remote-control heap-flaky 'go on'"],
            "label": "flaky"}
        assert result["posted"] == [{"project": "K", "nonce": "S2"}]
        assert result["headline"] == ["Running…"]

    def test_the_model_field_is_sent_only_when_typed_and_its_answer_shown(self):
        """DEC_ADR_030_D6: blank asks for the configured model, so the confirm
        URL is the one it always was; a typed model is sent and shown."""
        updated = _start_confirm(nonce="S2", model="chosen-model")
        result = _run({"/api/state": [200, _start_state()],
                       "/api/start/confirm": [200, _start_confirm(model="configured-model")]},
                      r"""
  element("newsession").fire("click");
  await settle();
  const before = texts(sheet(), "start-model");
  const model = all(sheet()).filter((n) => n.tagName === "INPUT")[1];
  model.value = "chosen-model";
  model.fire("input");
  answers["/api/start/confirm"] = [200, UPDATED];
  const debounce = timers.filter((t) => t.ms === 400);
  timers.length = 0;
  debounce[debounce.length - 1].fn();
  await settle();
  say({before: before, after: texts(sheet(), "start-model"),
       placeholder: model.attrs.placeholder,
       fetched: fetched.filter((u) => u.startsWith("/api/start/confirm"))});""".replace(
            "UPDATED", json.dumps(updated)))
        assert result["before"] == ["Runs on configured-model"]
        assert result["after"] == ["Runs on chosen-model"]
        assert result["placeholder"] == "Leave it empty for the default."
        assert result["fetched"] == [
            "/api/start/confirm?project=K&label=&prompt=",
            "/api/start/confirm?project=K&label=1026-0049&prompt=&model=chosen-model"]

    def test_a_refusal_is_said_in_the_sheet_and_the_fields_stay(self):
        refused = {**_start_confirm(), "nonce": None, "words": None,
                   "refused": "a session name must contain at least one alphanumeric character"}
        result = _run({"/api/state": [200, _start_state()],
                       "/api/start/confirm": [200, _start_confirm()]}, r"""
  element("newsession").fire("click");
  await settle();
  const label = find(sheet(), (n) => n.tagName === "INPUT");
  label.value = "!!!";
  label.fire("input");
  answers["/api/start/confirm"] = [200, REFUSED];
  timers.filter((t) => t.ms === 400).pop().fn();
  await settle();
  const refusal = withClass(sheet(), "start-refused")[0];
  say({refusal: refusal.textContent, hidden: refusal.hidden, label: label.value,
       disabled: withClass(sheet(), "primary")[0].disabled,
       still: find(sheet(), (n) => n.tagName === "INPUT") === label});""".replace(
            "REFUSED", json.dumps(refused)))
        assert result == {
            "refusal": "Not run: a session name must contain at least one alphanumeric character",
            "hidden": False, "label": "!!!", "disabled": True, "still": True}

    def test_the_picker_changes_the_project_and_asks_again(self):
        result = _run({"/api/state": [200, _start_state()],
                       "/api/start/confirm": [200, _start_confirm()]}, r"""
  element("newsession").fire("click");
  await settle();
  const picker = find(sheet(), (n) => n.tagName === "SELECT");
  picker.value = "L";
  picker.fire("change");
  timers.filter((t) => t.ms === 400).pop().fn();
  await settle();
  say({fetched: fetched.filter((u) => u.startsWith("/api/start/confirm"))});""")
        assert result["fetched"][-1].startswith("/api/start/confirm?project=L&label=1026-0049")

    def test_the_confirm_expires_and_looking_again_keeps_the_fields(self):
        result = _run({"/api/state": [200, _start_state()],
                       "/api/start/confirm": [200, _start_confirm()]}, r"""
  element("newsession").fire("click");
  await settle();
  find(sheet(), (n) => n.tagName === "TEXTAREA").value = "keep me";
  fakeNow += 121000;
  tickIntervals();
  const expired = {text: texts(sheet(), "expiry"),
                   disabled: withClass(sheet(), "primary")[0].disabled};
  byText(sheet(), "Look again").fire("click");
  await settle();
  say({expired: expired, fetched: fetched.filter((u) => u.startsWith("/api/start/confirm")),
       prompt: find(sheet(), (n) => n.tagName === "TEXTAREA").value,
       disabled: withClass(sheet(), "primary")[0].disabled});""")
        assert result["expired"] == {"text": ["This confirm expired.Look again"],
                                     "disabled": True}
        assert result["fetched"][-1] == ("/api/start/confirm?project=K&label=1026-0049"
                                         "&prompt=keep%20me")
        assert result["prompt"] == "keep me"
        assert result["disabled"] is False

    def test_cancel_closes_it_and_a_confirm_that_did_not_load_says_so(self):
        result = _run({"/api/state": [200, _start_state()],
                       "/api/start/confirm": [200, _start_confirm()]}, r"""
  element("newsession").fire("click");
  await settle();
  byText(sheet(), "Cancel").fire("click");
  const closed = sheet().hidden;
  answers["/api/start/confirm"] = [503, {refused: "the machine could not be read"}];
  element("newsession").fire("click");
  await settle();
  say({closed: closed, said: texts(sheet(), "sentence")});""")
        assert result["closed"] is True
        assert result["said"] == ["Not run: the machine could not be read"]

    def test_a_refused_start_is_said(self):
        result = _run({"/api/state": [200, _start_state()],
                       "/api/start/confirm": [200, _start_confirm()],
                       "/api/start": [409, {"error": "the name changed; nothing ran"}]}, r"""
  element("newsession").fire("click");
  await settle();
  withClass(sheet(), "primary")[0].fire("click");
  await settle();
  say({said: texts(sheet(), "sentence")});""")
        assert result["said"] == ["Not run: the name changed; nothing ran"]

    def test_a_read_only_page_offers_no_start(self):
        result = _run({"/api/state": [200, _state(acting=False)]}, r"""
  say({hidden: element("newsession").hidden,
       projects: withClass(screen(), "projects").length});""")
        assert result == {"hidden": True, "projects": 0}


class TestTheStartingCard:
    def test_it_shows_under_working_and_opens_the_starts_result(self):
        result = _run({"/api/state": [200, _start_state(rows=[],
                                                        jobs=[_start_job(state="running")])],
                       "/api/job": [200, _start_job()]}, r"""
  const card = withClass(screen(), "start-card")[0];
  const shown = {text: card.textContent, cls: card.className,
                 headings: texts(screen(), "heading"), tiles: texts(screen(), "tile")};
  card.fire("click");
  await settle();
  const running = texts(sheet(), "result-title");
  timers.filter((t) => t.ms === 1000).pop().fn();
  await settle();
  say({shown: shown, running: running, done: texts(sheet(), "result-title"),
       rc: texts(sheet(), "sentence")});""")
        shown = result["shown"]
        assert shown["text"] == f"Heap{_NAME}Starting…"
        assert "start-starting" in shown["cls"]
        assert shown["headings"] == ["Working"]
        assert shown["tiles"][2] == "1Working"
        assert result["running"] == ["Running…"]
        assert result["done"] == ["Opened in a new window"]
        assert result["rc"] == [f"Pick it up from Remote Control on your phone as {_NAME}."]

    def test_a_start_not_seen_in_a_minute_is_a_failure_with_its_reason(self):
        result = _run({"/api/state": [200, _start_state(rows=[],
                                                        jobs=[_start_job(started=NOW - 61)])]},
                      r"""
  const card = withClass(screen(), "start-card")[0];
  const shown = {cls: card.className, chips: texts(card, "chip"), note: texts(card, "note")};
  card.fire("click");
  await settle();
  say({shown: shown, headline: texts(sheet(), "result-title"), why: texts(sheet(), "why"),
       rc: texts(sheet(), "sentence"), fetched: fetched.filter((u) => u.startsWith("/api/job"))});""")
        assert result["shown"] == {"cls": "card compact start-card start-failed",
                                   "chips": ["Did not start"], "note": [_TIMEOUT]}
        assert result["headline"] == ["Failed"]
        assert result["why"] == ["Why" + _TIMEOUT]
        assert result["rc"] == [_TIMEOUT]
        assert result["fetched"] == []

    def test_the_card_updates_on_the_next_refresh_without_a_reload(self):
        later = _start_state(rows=[], jobs=[_start_job(started=NOW - 61)])
        result = _run({"/api/state": [200, _start_state(rows=[],
                                                        jobs=[_start_job(state="running")])]},
                      r"""
  const before = withClass(screen(), "start-card")[0].className;
  answers["/api/state"] = [200, LATER];
  await refresh();
  say({before: before, after: withClass(screen(), "start-card")[0].className,
       reloads: location.reloads});""".replace("LATER", json.dumps(later)))
        assert result == {"before": "card compact start-card start-starting",
                          "after": "card compact start-card start-failed", "reloads": 0}


class TestTheStartMarkup:
    def test_the_fields_are_16_px_and_44_px_and_never_scroll_sideways(self):
        from invariant.oracle import webpage

        style = webpage._STYLE
        assert (".field input, .field select, .field textarea { font: 16px/22px var(--font);"
                in style)
        assert "min-height: 44px; width: 100%;" in style
        assert ".newbtn { height: 44px;" in style
        assert "#top.sub #newsession" in style
        # The pairing form's code field keeps its own look; the sheet's fields do not
        # inherit its capitals.
        assert "form input {" in style
        page = webpage.page("n", panel.WORDS)
        assert 'id="newsession"' in page and "hidden" in page.split('id="newsession"')[1][:40]


# -- the last-message excerpt (ADR-029 increment 1, ADR-009 D17) ---------------------------

_MARKUP = '<script>alert(1)</script> and [a link](http://x.example)'
_LONG = "Line one of a long answer.\n" + "word " * 300


def _excerpts(claude=_MARKUP, you="<b>Ship it</b>"):
    return {"K|w1": {"claude": claude, "you": you}, "K|i1": {"claude": "All done.", "you": None},
            "K|host:h00000000": {"claude": "Should I delete the branch?", "you": None}}


class TestTheExcerpt:
    def test_a_full_card_shows_claudes_text_under_its_label_as_characters(self):
        result = _run({"/api/state": [200, _state(excerpts=_excerpts())]}, r"""
  const card = find(screen(), (n) => n.className === "card tap");
  const block = withClass(card, "excerpt")[0];
  const kids = card.children.map((n) => n.className);
  say({label: texts(block, "excerpt-label"), text: withClass(block, "excerpt-text")[0].own,
       cls: withClass(block, "excerpt-text")[0].className, order: kids});""")
        assert result["label"] == ["Claude"]
        assert result["text"] == _MARKUP
        assert result["cls"] == "excerpt-text clamp"
        assert result["order"] == ["meta", "card-title", "chips", "inset excerpt", "note"]

    def test_a_compact_card_and_a_leftover_row_show_one_line(self):
        result = _run({"/api/state": [200, _state(excerpts=_excerpts())]}, r"""
  const idle = texts(screen(), "excerpt-line");
  withClass(screen(), "tile-leftovers")[0].fire("click", {preventDefault() {}});
  say({idle: idle, leftovers: texts(screen(), "excerpt-line")});""")
        assert result == {"idle": ["All done."], "leftovers": ["Should I delete the branch?"]}

    def test_no_text_draws_no_block(self):
        result = _run({"/api/state": [200, _state()]}, r"""
  const cards = withClass(screen(), "excerpt").length + withClass(screen(), "excerpt-line").length;
  find(screen(), (n) => n.className === "card tap").fire("click");
  say({cards: cards, last: texts(screen(), "section-label").includes("Last message"),
       blocks: withClass(screen(), "excerpt").length});""")
        assert result == {"cards": 0, "last": False, "blocks": 0}

    def test_the_detail_shows_the_prompt_then_claudes_text(self):
        result = _run({"/api/state": [200, _state(excerpts=_excerpts())]}, r"""
  find(screen(), (n) => n.className === "card tap").fire("click");
  const blocks = withClass(screen(), "excerpt");
  say({section: texts(screen(), "section-label"),
       labels: blocks.map((b) => texts(b, "excerpt-label")[0]),
       texts: blocks.map((b) => withClass(b, "excerpt-text")[0].own),
       clamp: blocks.map((b) => withClass(b, "excerpt-text")[0].className),
       showAll: texts(screen(), "show-all")});""")
        assert result == {"section": ["Last message", "Recent"], "labels": ["You", "Claude"],
                          "texts": ["<b>Ship it</b>", _MARKUP],
                          "clamp": ["excerpt-text", "excerpt-text"], "showAll": []}

    def test_a_long_text_folds_behind_show_all_and_stays_open_on_refresh(self):
        state = _state(excerpts=_excerpts(claude=_LONG, you=None))
        result = _run({"/api/state": [200, state]}, r"""
  find(screen(), (n) => n.className === "card tap").fire("click");
  const text = () => withClass(screen(), "excerpt-text")[0].own;
  const folded = {text: text(), labels: texts(screen(), "excerpt-label"),
                  button: texts(screen(), "show-all")};
  byText(screen(), "Show all").fire("click");
  const opened = {text: text(), button: texts(screen(), "show-all")};
  await refresh();
  say({folded: folded, opened: opened, after: text()});""")
        head = state["view"]["details"]["K|w1"]["last"]["head"]
        assert result["folded"] == {"text": head, "labels": ["Claude"], "button": ["Show all"]}
        assert result["opened"] == {"text": _LONG, "button": []}
        assert result["after"] == _LONG

    def test_the_text_keeps_its_lines_and_wraps_a_long_word(self):
        from invariant.oracle import webpage

        style = webpage._STYLE
        assert "white-space: pre-wrap" in style and "overflow-wrap: break-word" in style
        assert "-webkit-line-clamp: 3" in style


# -- the Conversation screen (ADR-029 increment 2, ADR-009 D17) ---------------------------

_TURNS = [{"role": "user", "text": "<b>Ship it</b>", "at": NOW - 30, "cut": False},
          {"role": "tool", "text": "Ran git status", "at": NOW - 20, "cut": False},
          {"role": "assistant", "text": _MARKUP, "at": NOW - 10, "cut": True}]


def _talk(turns=_TURNS, cursor="c1", live=True, unreadable=False, **extra):
    return {"row": "K|w1", "live": live, "unreadable": unreadable, "cursor": cursor,
            "head": {"kicker": "Heap · Conversation", "title": "Mobile panel",
                     "chips": [{"text": "Needs permission", "tone": "needs"}]},
            "turns": list(turns),
            "offered": {"send": False, "answer": False, "interrupt": False,
                        "why": "reading_only"}, **extra}


#: Open the waiting card's detail, then its conversation.
_OPEN = r"""
  find(screen(), (n) => n.className === "card tap").fire("click");
  byText(screen(), "Open conversation").fire("click");
  await settle();
"""

_TIMERS = r"""
async function fire(ms) {
  const timer = timers.filter((t) => t.ms === ms).pop();
  if (!timer) return false;
  timers.splice(timers.indexOf(timer), 1);
  timer.fn();
  await settle();
  return true;
}
const sessions = () => fetched.filter((url) => url.startsWith("/api/session"));
const hide = () => {
  document.visibilityState = "hidden";
  for (const fn of docListeners.visibilitychange || []) fn();
};
const show = async () => {
  document.visibilityState = "visible";
  for (const fn of docListeners.visibilitychange || []) fn();
  await settle();
};
const everything = () => all(screen()).map((n) => n.own).filter(Boolean).join("|");
"""


def _talking(talk=None, excerpts=None):
    return {"/api/state": [200, _state(excerpts=excerpts or _excerpts(), talking=True)],
            "/api/session": [200, talk or _talk()]}


class TestTheConversation:
    def test_the_detail_ends_its_last_message_with_open_conversation(self):
        result = _run(_talking(), r"""
  find(screen(), (n) => n.className === "card tap").fire("click");
  const kids = screen().children.map((n) => n.className || n.own);
  const at = kids.indexOf("btn secondary tall talk-open");
  say({before: kids[at - 1], label: screen().children[at].own});""")
        assert result == {"before": "inset excerpt", "label": "Open conversation"}

    def test_without_a_last_message_it_stands_alone_and_without_a_reader_is_absent(self):
        alone = _run({"/api/state": [200, _state(talking=True)]}, r"""
  find(screen(), (n) => n.className === "card tap").fire("click");
  say({button: texts(screen(), "talk-open"), last: withClass(screen(), "excerpt").length});""")
        assert alone == {"button": ["Open conversation"], "last": 0}
        absent = _run({"/api/state": [200, _state(excerpts=_excerpts())]}, r"""
  find(screen(), (n) => n.className === "card tap").fire("click");
  say({button: texts(screen(), "talk-open")});""")
        assert absent == {"button": []}

    def test_it_shows_the_turns_oldest_first_as_characters(self):
        result = _run(_talking(), _TIMERS + _OPEN + r"""
  const turns = withClass(screen(), "turns")[0].children;
  say({url: sessions()[0], back: texts(screen(), "back"), kicker: texts(screen(), "kicker"),
       title: texts(screen(), "talk-title"), chips: texts(screen(), "chip"),
       classes: turns.map((n) => n.className),
       labels: turns.map((n) => texts(n, "excerpt-label")),
       texts: turns.map((n) => n.className.includes("tool-line") ? n.own
                                 : withClass(n, "turn-text")[0].own),
       cut: texts(screen(), "cut-line")});""")
        assert result == {
            "url": "/api/session?row=K%7Cw1", "back": ["Mobile panel"],
            "kicker": ["Heap · Conversation"], "title": ["Mobile panel"],
            "chips": ["Needs permission"],
            "classes": ["card quiet-card turn", "mono scroll tool-line", "card turn"],
            "labels": [["You"], [], ["Claude"]],
            "texts": ["<b>Ship it</b>", "Ran git status", _MARKUP],
            "cut": ["Cut here: the rest is on the Mac"]}

    def test_it_opens_at_the_bottom(self):
        result = _run(_talking(), _OPEN + "say({y: window.scrollY});")
        assert result == {"y": 2000}

    def test_it_asks_again_every_two_seconds_from_its_cursor(self):
        result = _run(_talking(), _TIMERS + _OPEN + r"""
  answers["/api/session"] = [200, {row: "K|w1", unchanged: true, cursor: "c1",
                                   head: {kicker: "Heap · Conversation", title: "Mobile panel",
                                          chips: []},
                                   offered: {send: false, answer: false, interrupt: false,
                                             why: "reading_only"}}];
  await fire(2000);
  const kept = withClass(screen(), "turn").length + withClass(screen(), "tool-line").length;
  say({urls: sessions(), kept: kept});""")
        assert result == {"urls": ["/api/session?row=K%7Cw1",
                                   "/api/session?row=K%7Cw1&after=c1"], "kept": 3}

    def test_a_new_turn_keeps_the_bottom_in_view(self):
        newer = _talk(turns=[*_TURNS, {"role": "assistant", "text": "Pushed.", "at": NOW,
                                       "cut": False}], cursor="c2")
        result = _run(_talking(), _TIMERS + _OPEN + f"""
  answers["/api/session"] = [200, {json.dumps(newer)}];
  window.scrollY = 1600;
  document.documentElement.scrollHeight = 2400;
  await fire(2000);
  say({{y: window.scrollY, pill: withClass(screen(), "new-pill")[0].hidden,
       last: texts(screen(), "turn-text").pop()}});""")
        assert result == {"y": 2400, "pill": True, "last": "Pushed."}

    def test_scrolled_up_a_new_turn_shows_the_pill_which_scrolls_down(self):
        newer = _talk(turns=[*_TURNS, {"role": "user", "text": "And?", "at": NOW,
                                       "cut": False}], cursor="c2")
        result = _run(_talking(), _TIMERS + _OPEN + f"""
  answers["/api/session"] = [200, {json.dumps(newer)}];
  window.scrollY = 100;
  await fire(2000);
  const pill = withClass(screen(), "new-pill")[0];
  const shown = {{hidden: pill.hidden, text: pill.own, y: window.scrollY}};
  pill.fire("click");
  say({{shown: shown, after: {{hidden: element("newpill").hidden, y: window.scrollY}}}});""")
        assert result == {"shown": {"hidden": False, "text": "New messages", "y": 100},
                          "after": {"hidden": True, "y": 2000}}

    def test_scrolling_to_the_bottom_puts_the_pill_away(self):
        newer = _talk(cursor="c2")
        result = _run(_talking(), _TIMERS + _OPEN + f"""
  answers["/api/session"] = [200, {json.dumps(newer)}];
  window.scrollY = 100;
  await fire(2000);
  const before = element("newpill").hidden;
  window.scrollY = 1200;
  for (const fn of winListeners.scroll || []) fn();
  say({{before: before, after: element("newpill").hidden}});""")
        assert result == {"before": False, "after": True}

    def test_a_hidden_page_does_not_ask_and_asks_at_once_when_seen_again(self):
        result = _run(_talking(), _TIMERS + _OPEN + r"""
  hide();
  await fire(2000);
  const whileHidden = sessions().length;
  await show();
  say({whileHidden: whileHidden, after: sessions().length});""")
        assert result == {"whileHidden": 1, "after": 2}

    def test_leaving_stops_the_asking(self):
        result = _run(_talking(), _TIMERS + _OPEN + r"""
  texts(screen(), "back");
  find(screen(), (n) => n.className === "back").fire("click");
  await settle();
  await fire(2000);
  say({screen: texts(screen(), "detail-title"), asked: sessions().length});""")
        assert result["asked"] == 1 and result["screen"]

    def test_after_a_minute_in_the_background_every_text_is_dropped(self):
        result = _run(_talking(), _TIMERS + _OPEN + r"""
  hide();
  fakeNow += 61000;
  await fire(60000);
  const dropped = everything();
  answers["/api/session"] = [200, Object.assign({}, answers["/api/session"][1],
                                                {cursor: "c9"})];
  await show();
  say({dropped: dropped, urls: sessions(), back: everything().includes("Ship it")});""")
        assert result["dropped"] == "Reading again…"
        assert result["urls"][-1] == "/api/session?row=K%7Cw1"
        assert result["back"] is True

    def test_a_drop_the_timer_missed_happens_on_return(self):
        result = _run(_talking(), _TIMERS + _OPEN + r"""
  hide();
  fakeNow += 61000;
  timers.length = 0;
  answers["/api/session"] = [500, {}];
  answers["/api/state"] = [500, {error: "down"}];
  await show();
  say({shown: everything()});""")
        assert result["shown"] == "Reading again…"

    def test_a_short_absence_keeps_the_text(self):
        result = _run(_talking(), _TIMERS + _OPEN + r"""
  hide();
  fakeNow += 5000;
  answers["/api/session"] = [500, {}];
  await show();
  say({kept: everything().includes("Ship it")});""")
        assert result == {"kept": True}

    def test_the_overview_drops_its_excerpts_too(self):
        result = _run(_talking(), _TIMERS + r"""
  hide();
  await fire(60000);
  say({shown: everything(), hasText: everything().includes("alert(1)")});""")
        assert result == {"shown": "Reading again…", "hasText": False}

    def test_an_unreadable_stop_shows_the_notice_above_the_newer_turns(self):
        talk = _talk(turns=_TURNS[2:], unreadable=True)
        result = _run(_talking(talk), _OPEN + r"""
  const turns = withClass(screen(), "turns")[0].children;
  say({first: turns[0].className, notice: turns[0].textContent,
       rest: turns.slice(1).map((n) => n.className)});""")
        assert result == {"first": "card quiet-card",
                          "notice": "Can't read this session here — use the Mac.",
                          "rest": ["card turn"]}

    @pytest.mark.parametrize("answer", [[200, _talk(turns=[], live=False)],
                                        [404, {"error": "no such session"}]])
    def test_a_gone_window_says_so(self, answer):
        result = _run({**_talking(), "/api/session": answer}, _OPEN + r"""
  say({turns: withClass(screen(), "turns")[0].textContent});""")
        assert result == {"turns": "The Claude window is gone."}

    def test_a_failed_read_keeps_what_was_shown_and_backs_off(self):
        result = _run(_talking(), _TIMERS + _OPEN + r"""
  answers["/api/session"] = [503, {error: "busy"}];
  await fire(2000);
  say({kept: everything().includes("Ship it"), next: timers.some((t) => t.ms === 4000)});""")
        assert result == {"kept": True, "next": True}

    def test_its_style_is_the_panels(self):
        from invariant.oracle import webpage

        style = webpage._STYLE
        for rule in (".turns {", ".tool-line {", ".new-pill {", ".talk-title {",
                     ".turn-text {", ".cut-line {", ".talk-open {"):
            assert rule in style, rule
        assert webpage.CONVERSATION_INTERVAL == 2.0 and webpage.BACKGROUND_DROP == 60.0


# -- sending (ADR-029 increment 3) ---------------------------------------------------

_OFFERED = {"send": True, "answer": False, "interrupt": False, "why": None, "running": False}


def _sendable(**extra):
    fields = {"nonce": "N1", "sends": [], "boot": "B1", **extra}
    fields["offered"] = dict(_OFFERED, **extra.get("offered", {}))
    return _talk(**fields)


def _held(send_id="S1", state="held", **extra):
    return {"id": send_id, "row": "K|w1", "kind": "message", "state": state,
            "reason": extra.get("reason"), "what": extra.get("what"), "at": NOW, "length": 5}


def _sending(talk=None, **answers):
    return {"/api/talk/cancel": answers.get("cancel", [200, {"send": _held(state="cancelled")}]),
            "/api/talk": answers.get("post", [202, {"send": _held(), "boot": "B1"}]),
            **_talking(talk or _sendable())}


_TYPE = r"""
  const box = element("composer");
  const input = find(box, (n) => n.tagName === "TEXTAREA");
  const send = withClass(box, "send-btn")[0];
  input.value = "Ship it <now>";
"""


class TestSending:
    def test_the_composer_shows_where_send_is_offered(self):
        result = _run(_sending(), _OPEN + r"""
  const box = element("composer");
  const input = find(box, (n) => n.tagName === "TEXTAREA");
  say({hidden: box.hidden, placeholder: input.attrs.placeholder, value: input.value,
       label: withClass(box, "send-btn")[0].attrs["aria-label"],
       talking: screen().className});""")
        assert result == {"hidden": False, "placeholder": "Message Claude", "value": "",
                          "label": "Send", "talking": "sub talking"}

    def test_a_running_turn_says_it_is_sent_when_the_turn_ends(self):
        result = _run(_sending(_sendable(offered={"running": True})), _OPEN + r"""
  const input = find(element("composer"), (n) => n.tagName === "TEXTAREA");
  say({placeholder: input.attrs.placeholder});""")
        assert result == {"placeholder": "Message Claude — sent when this turn ends"}

    def test_reading_only_has_no_composer(self):
        result = _run(_talking(), _OPEN + "say({hidden: element('composer').hidden});")
        assert result == {"hidden": True}

    @pytest.mark.parametrize(("why", "said"), [
        ("no_tab", "Send from here works for Terminal windows only."),
        ("no_claude", "Claude is not running in this window."),
        ("unreadable", None), ("gone", None)])
    def test_send_off_says_why_in_its_place(self, why, said):
        talk = _talk(offered={"send": False, "answer": False, "interrupt": False, "why": why},
                     nonce=None, sends=[], boot="B1")
        result = _run(_sending(talk), _OPEN + r"""
  say({hidden: element("composer").hidden, off: texts(screen(), "talk-off")});""")
        assert result == {"hidden": True, "off": [said] if said else []}

    def test_send_posts_the_row_nonce_and_text_and_shows_it_held(self):
        result = _run(_sending(), _TIMERS + _OPEN + _TYPE + r"""
  send.fire("click");
  await settle();
  const card = withClass(screen(), "send")[0];
  say({posted: posted, input: input.value, text: texts(card, "turn-text"),
       line: texts(card, "send-state"), act: texts(card, "send-act"),
       again: sessions().length});""")
        assert result == {
            "posted": [{"row": "K|w1", "nonce": "N1", "kind": "message",
                        "text": "Ship it <now>"}],
            "input": "", "text": ["Ship it <now>"], "line": ["Sending…", "·"],
            "act": ["Undo"], "again": 2}

    def test_an_empty_message_is_not_sent(self):
        result = _run(_sending(), _OPEN + _TYPE + r"""
  input.value = "   ";
  send.fire("click");
  await settle();
  say({posted: posted.length});""")
        assert result == {"posted": 0}

    def test_undo_cancels_it_and_gives_the_text_back(self):
        result = _run(_sending(), _OPEN + _TYPE + r"""
  send.fire("click");
  await settle();
  byText(screen(), "Undo").fire("click");
  await settle();
  say({cancel: posted[1], input: input.value, sends: withClass(screen(), "send").length});""")
        assert result == {"cancel": {"id": "S1"}, "input": "Ship it <now>", "sends": 0}

    def test_an_undo_too_late_says_so(self):
        answers = _sending(cancel=[409, {"error": "it is already being sent"}])
        result = _run(answers, _OPEN + _TYPE + r"""
  send.fire("click");
  await settle();
  byText(screen(), "Undo").fire("click");
  await settle();
  say({error: texts(element("composer"), "composer-error")});""")
        assert result == {"error": ["Not sent: it is already being sent"]}

    def test_a_refusal_says_why_and_keeps_the_text(self):
        answers = _sending(post=[409, {"error": "not sent: menu_command",
                                       "reason": "menu_command"}])
        result = _run(answers, _OPEN + _TYPE + r"""
  send.fire("click");
  await settle();
  say({error: texts(element("composer"), "composer-error"), input: input.value});""")
        assert result == {"error": ["Not sent: This command opens a menu — use it on the Mac."],
                          "input": "Ship it <now>"}

    def test_a_post_that_fails_says_it_may_not_have_reached_the_mac(self):
        result = _run(_sending(), _OPEN + _TYPE + r"""
  const real = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    if (url === "/api/talk") throw new Error("offline");
    return real(url, options);
  };
  send.fire("click");
  await settle();
  say({error: texts(element("composer"), "composer-error")});""")
        assert result == {"error": ["The message may not have reached the Mac: offline"]}

    @pytest.mark.parametrize(("send", "line", "text"), [
        (_held(state="queued"), ["Queued: sent when Claude's turn ends", "·"], True),
        (_held(state="typed"), ["Sent · checking"], True),
        (_held(state="delivered"), ["Delivered"], False),
        (_held(state="not_confirmed"), ["Not confirmed — check the session on the Mac"],
         True),
        (_held(state="refused", reason="changed", what="turn"),
         ["Not sent: The session changed since you looked. A new turn began."], True),
        (_held(state="refused", reason="typing"), ["Not sent: Someone is typing on the Mac."],
         True),
        (_held(state="dropped", reason="ring"),
         ["Dropped, not delivered: too many were queued"], True),
        (_held(state="dropped", reason="revoked"),
         ["Not sent: This device is not paired any more."], True),
        (_held(state="dropped"), ["Dropped, not delivered"], True),
    ])
    def test_each_state_is_one_line_under_its_turn(self, send, line, text):
        listed = _sendable(sends=[send], cursor="c2")
        answers = _sending()
        result = _run(answers, _TIMERS + _OPEN + _TYPE + f"""
  send.fire("click");
  await settle();
  answers["/api/session"] = [200, {json.dumps(listed)}];
  await fire(2000);
  const card = withClass(screen(), "send")[0];
  say({{line: texts(card, "send-state"), text: texts(card, "turn-text").length}});""")
        assert result == {"line": line, "text": 1 if text else 0}

    def test_a_send_from_an_older_boot_is_dropped_the_oracle_restarted(self):
        restarted = _sendable(boot="B2", cursor="c2")
        result = _run(_sending(), _TIMERS + _OPEN + _TYPE + f"""
  send.fire("click");
  await settle();
  answers["/api/session"] = [200, {json.dumps(restarted)}];
  await fire(2000);
  const first = texts(screen(), "send-state");
  await fire(2000);
  say({{first: first, again: texts(screen(), "send-state")}});""")
        assert result == {"first": ["Dropped, not delivered: the oracle restarted"],
                          "again": ["Dropped, not delivered: the oracle restarted"]}

    def test_a_minute_in_the_background_drops_the_held_text(self):
        result = _run(_sending(), _TIMERS + _OPEN + _TYPE + r"""
  send.fire("click");
  await settle();
  hide();
  fakeNow += 61000;
  await fire(60000);
  answers["/api/session"] = [200, Object.assign({}, answers["/api/session"][1],
                                                {cursor: "c9", sends: [
    {id: "S1", row: "K|w1", kind: "message", state: "held", at: 0, length: 5}]})];
  await show();
  say({text: everything().includes("Ship it <now>"), box: element("composer").hidden});""")
        assert result == {"text": False, "box": False}

    def test_reply_on_a_card_waiting_for_input_opens_the_composer(self):
        rows = [dict(_ROWS[0], signals=[{"kind": "waiting", "cause": "input", "alert": True,
                                         "severity": "high"}])]
        state = _state(rows=rows, excerpts=_excerpts(), talking=True)
        state["view"] = panel.view(
            {"now": NOW, "projects": [{"project": {"key": "K", "name": "Heap", "root": "/r"},
                                       "rows": rows}]},
            acting=True, excerpts=_excerpts(), talking=True, sending=True)
        answers = {"/api/state": [200, state], "/api/session": [200, _sendable()]}
        result = _run(answers, r"""
  const reply = byText(screen(), "Reply…");
  reply.fire("click");
  await settle();
  const input = find(element("composer"), (n) => n.tagName === "TEXTAREA");
  say({classes: reply.className, url: fetched.filter((u) => u.startsWith("/api/session")),
       focused: focused === input});""")
        assert result == {"classes": "btn primary", "url": ["/api/session?row=K%7Cw1"],
                          "focused": True}

    def test_its_style_is_the_panels(self):
        from invariant.oracle import webpage

        for rule in ("#composer {", ".composer-input {", ".send-btn {", ".send-line {",
                     ".talk-off {", "main.talking {"):
            assert rule in webpage._STYLE, rule
        assert '<div id="composer" hidden></div>' in webpage.page("n", panel.WORDS)


# -- Stop (ADR-029 increment 4) ---------------------------------------------------------


def _stoppable(**offered):
    return _sendable(offered={"running": True, "interrupt": True, **offered})


def _stopped(state="held", **extra):
    return dict(_held(state=state, **extra), kind="interrupt", length=0)


class TestStop:
    def test_stop_stands_beside_send_while_a_turn_runs(self):
        result = _run(_sending(_stoppable()), _OPEN + r"""
  const box = element("composer");
  const row = withClass(box, "composer-row")[0];
  say({buttons: row.children.map((n) => n.className),
       label: withClass(box, "stop-btn")[0].attrs["aria-label"]});""")
        assert result == {"buttons": ["composer-input", "stop-btn", "send-btn"],
                          "label": "Stop"}

    def test_no_stop_while_claude_waits_for_input(self):
        result = _run(_sending(), _OPEN + r"""
  say({stop: withClass(element("composer"), "stop-btn").map((n) => n.hidden)});""")
        assert result == {"stop": [True]}

    def test_stop_stays_alone_when_send_is_off(self):
        talk = _stoppable(send=False, why="unreadable")
        result = _run(_sending(talk), _OPEN + r"""
  const box = element("composer");
  say({hidden: box.hidden,
       input: find(box, (n) => n.tagName === "TEXTAREA").hidden,
       send: withClass(box, "send-btn")[0].hidden,
       stop: withClass(box, "stop-btn")[0].hidden});""")
        assert result == {"hidden": False, "input": True, "send": True, "stop": False}

    def test_stop_posts_an_interrupt_with_no_text_and_shows_it_held(self):
        answers = _sending(_stoppable(), post=[202, {"send": _stopped(), "boot": "B1"}])
        result = _run(answers, _TIMERS + _OPEN + r"""
  const input = find(element("composer"), (n) => n.tagName === "TEXTAREA");
  input.value = "a draft that stays";
  withClass(element("composer"), "stop-btn")[0].fire("click");
  await settle();
  const card = withClass(screen(), "send")[0];
  say({posted: posted, input: input.value, text: texts(card, "turn-text"),
       line: texts(card, "send-state"), act: texts(card, "send-act")});""")
        assert result == {"posted": [{"row": "K|w1", "nonce": "N1", "kind": "interrupt"}],
                          "input": "a draft that stays", "text": ["Stop"],
                          "line": ["Sending…", "·"], "act": ["Undo"]}

    def test_undoing_a_stop_leaves_the_draft_alone(self):
        answers = _sending(_stoppable(), post=[202, {"send": _stopped(), "boot": "B1"}])
        result = _run(answers, _OPEN + r"""
  const input = find(element("composer"), (n) => n.tagName === "TEXTAREA");
  withClass(element("composer"), "stop-btn")[0].fire("click");
  await settle();
  byText(screen(), "Undo").fire("click");
  await settle();
  say({input: input.value, sends: withClass(screen(), "send").length});""")
        assert result == {"input": "", "sends": 0}

    def test_a_refused_stop_says_why(self):
        answers = _sending(_stoppable(), post=[409, {"error": "x", "reason": "ended"}])
        result = _run(answers, _OPEN + r"""
  withClass(element("composer"), "stop-btn")[0].fire("click");
  await settle();
  say({error: texts(element("composer"), "composer-error")});""")
        assert result == {"error": ["Not sent: Claude's turn had already ended."]}

    def test_a_stop_that_cannot_reach_the_mac_says_so(self):
        result = _run(_sending(_stoppable()), _OPEN + r"""
  const real = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    if (url === "/api/talk") throw new Error("offline");
    return real(url, options);
  };
  withClass(element("composer"), "stop-btn")[0].fire("click");
  await settle();
  say({error: texts(element("composer"), "composer-error")});""")
        assert result == {"error": ["The message may not have reached the Mac: offline"]}

    @pytest.mark.parametrize(("send", "line"), [
        (_stopped(state="refused", reason="asking"),
         ["Not sent: Claude is waiting on a prompt — answer it on the Mac."]),
        (_stopped(state="refused", reason="ended"),
         ["Not sent: Claude's turn had already ended."]),
        (_stopped(state="delivered"), ["Delivered"]),
    ])
    def test_a_listed_stop_reads_as_stop(self, send, line):
        listed = _stoppable()
        listed["sends"] = [send]
        result = _run(_sending(listed), _OPEN + r"""
  const card = withClass(screen(), "send")[0];
  say({line: texts(card, "send-state"), text: texts(card, "turn-text")});""")
        assert result == {"line": line, "text": [] if send["state"] == "delivered" else ["Stop"]}


# -- answering a question (ADR-029 increment 5) -----------------------------------------

_HUE = {"text": "Pick one hue", "header": "Hue", "multi": False, "other": True,
        "choices": [{"label": "Crimson", "description": "<b>red</b>"},
                    {"label": "Teal", "description": ""}]}
_TOPPINGS = {"text": "Pick toppings", "header": "Toppings", "multi": True, "other": True,
             "choices": [{"label": "Cheese", "description": ""},
                         {"label": "Basil", "description": ""}]}


def _asking(*questions, **extra):
    pending = {"kind": "question", "id": "toolu_q1", "unreadable": False,
               "questions": list(questions or (_HUE,))}
    return _sendable(pending=pending, offered={"answer": True, "running": True}, **extra)


def _answer_sent(state="held", **extra):
    return dict(_held(state=state, **extra), kind="question", length=0)


def _answering(talk=None, **answers):
    return _sending(talk or _asking(),
                    post=answers.get("post", [202, {"send": _answer_sent(), "boot": "B1"}]),
                    **{k: v for k, v in answers.items() if k != "post"})


_BLOCK = r"""
  const block = () => withClass(screen(), "asking")[0] || null;
  const choices = () => withClass(block(), "choice");
"""


class TestAnswering:
    def test_answer_on_a_card_waiting_on_a_question_opens_its_conversation(self):
        rows = [dict(_ROWS[0], signals=[{"kind": "waiting", "cause": "question",
                                         "alert": True, "severity": "high"}])]
        rows[0]["hosts"] = [dict(rows[0]["hosts"][0], attention={
            "waiting": {"kind": "question", "since": "2026-10-21T10:00:00+00:00"},
            "turn": "running"})]
        document = {"now": NOW, "projects": [{"project": {"key": "K", "name": "Heap",
                                                          "root": "/r"}, "rows": rows}]}
        state = _state(rows=rows, excerpts=_excerpts(), talking=True)
        state["view"] = panel.view(document, acting=True, excerpts=_excerpts(), talking=True,
                                   sending=True, prompts={"K|w1": {"text": "Pick one hue",
                                                                  "unreadable": False}})
        answers = {"/api/state": [200, state], "/api/session": [200, _asking()]}
        result = _run(answers, _BLOCK + r"""
  const card = withClass(screen(), "card")[0];
  const note = texts(card, "note");
  byText(screen(), "Answer…").fire("click");
  await settle();
  say({note: note, url: fetched.filter((u) => u.startsWith("/api/session")),
       block: !!block(), focused: focused === null});""")
        assert result == {"note": ["Pick one hue"], "url": ["/api/session?row=K%7Cw1"],
                          "block": True, "focused": True}

    def test_a_lone_choice_is_sent_on_one_tap(self):
        result = _run(_answering(), _BLOCK + _OPEN + r"""
  const shown = {text: texts(block(), "question-text"),
                 labels: texts(block(), "choice-label"),
                 notes: texts(block(), "choice-note"),
                 other: texts(block(), "other-open"),
                 send: texts(block(), "send-answer")};
  choices()[1].fire("click");
  await settle();
  const card = withClass(screen(), "send")[0];
  say({shown: shown, posted: posted, you: texts(card, "turn-text"),
       line: texts(card, "send-state"), block: !!block()});""")
        assert result == {
            "shown": {"text": ["Pick one hue"], "labels": ["Crimson", "Teal"],
                      "notes": ["<b>red</b>"], "other": ["Other…"], "send": []},
            "posted": [{"row": "K|w1", "nonce": "N1", "kind": "question",
                        "answers": [{"choices": [1], "other": None}]}],
            "you": ["Teal"], "line": ["Sending…", "·"], "block": False}

    def test_other_opens_a_field_and_sends_its_text(self):
        result = _run(_answering(), _BLOCK + _OPEN + r"""
  byText(block(), "Other…").fire("click");
  const input = find(block(), (n) => n.tagName === "INPUT");
  input.value = "my <own> hue";
  input.fire("input");
  byText(block(), "Send").fire("click");
  await settle();
  say({posted: posted[0].answers, you: texts(withClass(screen(), "send")[0], "turn-text"),
       focused: focused === input});""")
        assert result == {"posted": [{"choices": [], "other": "my <own> hue"}],
                          "you": ["my <own> hue"], "focused": True}

    def test_an_empty_other_is_not_sent(self):
        result = _run(_answering(), _BLOCK + _OPEN + r"""
  byText(block(), "Other…").fire("click");
  byText(block(), "Send").fire("click");
  await settle();
  say({posted: posted.length});""")
        assert result == {"posted": 0}

    def test_several_questions_are_chosen_then_sent_together(self):
        result = _run(_answering(_asking(_HUE, _TOPPINGS)), _BLOCK + _OPEN + r"""
  const send = () => withClass(block(), "send-answer")[0];
  const before = send().disabled;
  choices()[0].fire("click");
  choices()[1].fire("click");
  const half = send().disabled;
  choices()[2].fire("click");
  choices()[3].fire("click");
  choices()[3].fire("click");
  withClass(block(), "other-open")[1].fire("click");
  const input = find(block(), (n) => n.tagName === "INPUT");
  input.value = "olive oil";
  input.fire("input");
  const pressed = choices().map((n) => n.attrs["aria-pressed"]);
  send().fire("click");
  await settle();
  say({before: before, half: half, pressed: pressed, posted: posted[0].answers,
       you: texts(withClass(screen(), "send")[0], "turn-text")});""")
        assert result == {
            "before": True, "half": True, "pressed": ["false", "true", "true", "false"],
            "posted": [{"choices": [1], "other": None},
                       {"choices": [0], "other": "olive oil"}],
            "you": ["Teal\nCheese, olive oil"]}

    def test_a_question_it_cannot_read_says_so(self):
        talk = _sendable(pending={"kind": "question", "unreadable": True})
        result = _run(_answering(talk), _TIMERS + _BLOCK + _OPEN + r"""
  say({block: !!block(), said: everything().includes("Can't read this question here")});""")
        assert result == {"block": False, "said": True}

    def test_no_block_where_answer_is_not_offered(self):
        talk = _asking()
        talk["offered"]["answer"] = False
        result = _run(_answering(talk), _BLOCK + _OPEN + "say({block: !!block()});")
        assert result == {"block": False}

    def test_a_refusal_says_why_in_the_block(self):
        answers = _answering(post=[409, {"error": "not sent: answered",
                                          "reason": "answered"}])
        result = _run(answers, _BLOCK + _OPEN + r"""
  choices()[0].fire("click");
  await settle();
  say({error: texts(block(), "answer-error")});""")
        assert result == {"error": ["Not sent: Claude is no longer waiting on that question."]}

    def test_a_post_that_fails_says_it_may_not_have_reached_the_mac(self):
        result = _run(_answering(), _BLOCK + _OPEN + r"""
  globalThis.fetch = async () => { throw new Error("offline"); };
  choices()[0].fire("click");
  await settle();
  say({error: texts(block(), "answer-error")});""")
        assert result == {"error": ["The message may not have reached the Mac: offline"]}

    def test_undo_brings_the_question_back(self):
        result = _run(_answering(_asking(_HUE, _TOPPINGS)), _BLOCK + _OPEN + r"""
  choices()[1].fire("click");
  choices()[2].fire("click");
  withClass(block(), "send-answer")[0].fire("click");
  await settle();
  const hidden = !block();
  byText(screen(), "Undo").fire("click");
  await settle();
  say({hidden: hidden, back: !!block(), cancel: posted[1],
       pressed: choices().map((n) => n.attrs["aria-pressed"])});""")
        assert result == {"hidden": True, "back": True, "cancel": {"id": "S1"},
                          "pressed": ["false", "true", "true", "false"]}

    def test_a_new_question_starts_afresh(self):
        other = _asking(dict(_HUE, text="Another"), _TOPPINGS)
        other["pending"]["id"] = "toolu_q2"
        other["cursor"] = "c2"
        result = _run(_answering(_asking(_HUE, _TOPPINGS)), _TIMERS + _BLOCK + _OPEN + f"""
  choices()[1].fire("click");
  answers["/api/session"] = [200, {json.dumps(other)}];
  await fire(2000);
  say({{text: texts(block(), "question-text"),
       pressed: choices().map((n) => n.attrs["aria-pressed"])}});""")
        assert result == {"text": ["Another", "Pick toppings"],
                          "pressed": ["false", "false", "false", "false"]}

    def test_its_style_is_the_panels(self):
        from invariant.oracle import webpage

        for rule in (".asking {", ".choice {", ".choice.picked {", ".question-text {",
                     ".answer-other {"):
            assert rule in webpage._STYLE, rule
        assert "var(--tile-needs)" in webpage._STYLE.split(".asking {")[1].split("}")[0]
