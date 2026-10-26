"""The HTML the oracle's web server sends: the control panel and three small pages.

The panel (IDEA_027, ADR-009) is one page with three screens drawn in it, the
overview, a session's detail and the leftovers, and bottom sheets for a
confirm and its result. Everything is inline, so the page needs nothing but
the Mac it came from: no CDN, no font, no image; the few icons are inline SVG.
The script and the style carry the response's nonce, which the
Content-Security-Policy ``oracle/web`` sends is the only thing it lets run.
The script builds every element with ``textContent``, never ``innerHTML``, so
a row's text is never read as markup.

**It holds no vocabulary** (ADR-009 D3). Every word it shows comes from
``oracle/panel``: the fixed strings are embedded here as ``WORDS``, and the
rest arrives with every read of ``/api/state``. The script groups, counts and
words nothing; it draws what the view says.

**It always refreshes** (ADR-008 D6). The script fetches ``/api/state`` on a
timer, not with a meta refresh. A failed fetch backs off, the interval
doubled per failure, capped at :data:`MAX_BACKOFF`, and the loop never stops.
When the page is looked at again (it becomes visible, regains focus, is
restored, or the network comes back) a backoff is cut short and it reads at
once. The freshness line says ``Live · updated N s ago`` and turns red,
``Stale``, once the rows are older than three intervals. A refresh redraws the
screen the person is on and keeps the open detail, the open disclosures and
the scroll position; an open sheet is not redrawn at all.

**It acts only on a second, explicit confirm** (ADR-008 D8–D12, ADR-009 D6).
A card's primary button fetches ``/api/confirm`` and shows the step as a
bottom sheet: the question, one sentence, the command with Copy, the exact
folder and environment behind a toggle, and the deed button over Cancel,
which holds focus. The sheet counts the nonce down and disables the deed when
it expires. Only the deed posts ``/api/act``, carrying the row key and the
nonce and nothing else; the result is polled from ``/api/job``.

**It starts a new session only on the same kind of confirm** (IDEA_030,
ADR-009 D16). "New session…" in the top bar, and each project's shortcut
under "Projects", open a sheet with the project picker, the label, the first
message, the two facts and the exact command, all from
``/api/start/confirm``; every edit asks the Mac again, after a pause, and the
deed waits for its answer, so what is posted to ``/api/start`` (the project
and the nonce) is exactly what the sheet last showed. A start is then a card
under Working until its window appears.

**It follows the page to HTTPS** (ADR-008 D23): a state fetch answered with a
redirect it did not follow reloads the page.

**It installs as a home-screen app** (ADR-009 D10). The panel links a web app
manifest (:func:`manifest`) with ``crossorigin="use-credentials"``, so the
browser fetches it with the device's cookie, and a 180-pixel touch icon; the
manifest names 192- and 512-pixel ones. The icons are drawn here
(:func:`icon`), PNGs written with ``zlib`` from the tokens, never fetched or
shipped as files. ``oracle/web`` serves both, only to a paired device. Every
page covers the whole screen (``viewport-fit=cover``) and pads by
``env(safe-area-inset-*)``, and so does the bottom sheet.

The loop, the small helpers and every screen's drawing are the ``ORACLE``
object, which touches no page: each draw function takes the view and an
element builder, so the suite drives all of it under ``node`` or ``jsc``.
"""

from __future__ import annotations

import json
import re
import struct
import zlib
from collections.abc import Mapping
from functools import cache
from html import escape

from .watcher import DEFAULT_INTERVAL, STALE_INTERVALS

#: The longest wait between two refreshes after failures, in seconds.
MAX_BACKOFF = 60.0

#: The tokens of docs/architecture/oracle-panel.md, "Tokens", declared once.
_TOKENS = """
:root {
  color-scheme: dark;
  --bg: #0B0C0E; --bg-under-sheet: #050506;
  --surface-card: #16181B; --surface-quiet: #121417; --surface-sheet: #17191C;
  --surface-inset: #0F1113; --surface-button: #1F2226; --surface-pill: #23262A;
  --tile-idle: #1A1C1F; --border: #24272B; --grabber: #3A3E44;
  --text: #F2F3F5; --text-secondary: #A4AAB0; --text-tertiary: #C4C9CE;
  --chevron: #8A9097; --link: #8FC1FF; --link-pressed: #B9D8FF; --live: #4ADE80;
  --accent-needs: #FFC266; --tile-needs: #2B2114; --chip-needs: #3A2A12;
  --accent-problem: #FF8F85; --tile-problem: #2A1716; --chip-problem: #3A1A18;
  --accent-working: #8FC1FF; --tile-working: #14202D; --chip-working: #142436;
  --accent-idle: #C4C9CE; --chip-idle: #23262A;
  --accent-primary: #2563EB; --accent-destructive: #B91C1C; --tick-ground: #13261A;
  --font: -apple-system, system-ui, "Segoe UI", sans-serif;
  --mono: ui-monospace, Menlo, monospace;
}
"""


def token(name: str) -> str:
    """A colour token's value, as :data:`_TOKENS` declares it: the one source."""
    found = re.search(rf"--{re.escape(name)}:\s*(#[0-9A-F]{{6}});", _TOKENS)
    if found is None:
        raise KeyError(name)
    return found.group(1)


_STYLE = _TOKENS + """
* { box-sizing: border-box; overflow-wrap: normal; word-break: normal; }
[hidden] { display: none !important; }
body { margin: 0; background: var(--bg); color: var(--text); font: 15px/22px var(--font);
       padding: calc(16px + env(safe-area-inset-top)) calc(16px + env(safe-area-inset-right))
                calc(34px + env(safe-area-inset-bottom)) calc(16px + env(safe-area-inset-left)); }
button { font: inherit; color: inherit; border: 0; background: none; cursor: pointer;
         -webkit-tap-highlight-color: transparent; }
a { color: var(--link); text-decoration: none; }
a:active, .link:active { color: var(--link-pressed); }
h1, h2, p { margin: 0; }
#top { display: flex; flex-direction: column; gap: 8px; margin-bottom: 20px; }
.headrow { display: flex; justify-content: space-between; align-items: flex-end; gap: 12px;
           flex-wrap: wrap; }
.headtext { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
#title { font-size: 32px; line-height: 38px; font-weight: 700; letter-spacing: -0.4px; }
#top.sub #title, #top.sub #refresh, #top.sub #newsession { display: none; }
.headbtns { display: flex; gap: 8px; align-items: center; flex-shrink: 0; }
.newbtn { height: 44px; border-radius: 22px; background: var(--surface-button); padding: 0 16px;
          font-size: 15px; font-weight: 600; white-space: nowrap; }
.fresh { display: flex; align-items: center; gap: 6px; font-size: 13px;
         color: var(--text-secondary); }
.fresh .dot { width: 7px; height: 7px; border-radius: 4px; background: var(--live);
              flex-shrink: 0; }
.fresh.stale { color: var(--accent-problem); }
.fresh.stale .dot { background: var(--accent-problem); }
.iconbtn { width: 44px; height: 44px; border-radius: 22px; background: var(--surface-button);
           display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.notice { border-radius: 14px; padding: 10px 14px; font-size: 14px; line-height: 20px; }
#insecure { background: var(--tile-problem); color: var(--accent-problem); font-weight: 600; }
#banner { background: var(--tile-needs); color: var(--accent-needs); }
#error { background: var(--tile-problem); color: var(--accent-problem); }
#screen { display: flex; flex-direction: column; gap: 20px; }
#screen.sub { gap: 18px; }
.strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
.tile { border-radius: 14px; padding: 10px 8px; min-height: 44px; display: flex;
        flex-direction: column; gap: 2px; }
.tile .count { font-size: 24px; line-height: 28px; font-weight: 700; }
.tile .label { font-size: 12px; font-weight: 600; }
.tile-needs { background: var(--tile-needs); color: var(--accent-needs); }
.tile-problems { background: var(--tile-problem); color: var(--accent-problem); }
.tile-working { background: var(--tile-working); color: var(--accent-working); }
.tile-leftovers { background: var(--tile-idle); color: var(--accent-idle); }
.group { display: flex; flex-direction: column; gap: 10px; scroll-margin-top: 16px; }
.heading, .section-label { font-size: 13px; font-weight: 600; letter-spacing: 0.6px;
                           text-transform: uppercase; color: var(--text-secondary); }
.group-needs .heading { color: var(--accent-needs); }
.group-problems .heading { color: var(--accent-problem); }
.group-working .heading { color: var(--accent-working); }
.group-idle .heading { color: var(--accent-idle); }
.card { background: var(--surface-card); border-radius: 18px; padding: 14px 16px;
        display: flex; flex-direction: column; gap: 8px; text-align: left; width: 100%; }
.card.tap { cursor: pointer; }
.quiet-card { background: var(--surface-quiet); border: 1px solid var(--border); }
.compact, .leftovers-card { flex-direction: row; justify-content: space-between;
                            align-items: center; gap: 12px; min-height: 44px; }
.compact-text { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.meta { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.project, .kicker, .label { font-size: 12px; font-weight: 600; color: var(--text-secondary);
                            text-transform: uppercase; letter-spacing: 0.4px; }
.age { font-size: 12px; color: var(--text-secondary); flex-shrink: 0; }
.meta .project, .compact-text .project { min-width: 0; overflow: hidden;
                                         text-overflow: ellipsis; white-space: nowrap; }
.card-title { font-size: 17px; line-height: 22px; font-weight: 600; }
.note { font-size: 14px; line-height: 20px; color: var(--text-secondary); }
.body { font-size: 15px; line-height: 22px; }
.chips { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.chip { font-size: 13px; font-weight: 600; padding: 4px 10px; border-radius: 10px; }
.tone-needs { background: var(--chip-needs); color: var(--accent-needs); }
.tone-problem { background: var(--chip-problem); color: var(--accent-problem); }
.tone-working { background: var(--chip-working); color: var(--accent-working); }
.tone-idle { background: var(--chip-idle); color: var(--accent-idle); }
.buttons { display: flex; gap: 8px; margin-top: 4px; }
.btn { height: 44px; border-radius: 12px; font-size: 16px; font-weight: 600; display: flex;
       align-items: center; justify-content: center; padding: 0 12px; flex: 1; }
.btn.narrow { flex: 0 0 92px; }
.btn.tall { height: 48px; font-size: 17px; }
.btn.square { flex: 0 0 48px; height: 48px; padding: 0; }
.btn.primary { background: var(--accent-primary); color: #FFFFFF; }
.btn.secondary { background: var(--surface-button); }
.btn.destructive { background: var(--accent-destructive); color: #FFFFFF; }
.btn.sheet { height: 52px; border-radius: 14px; font-size: 17px; flex: none; width: 100%; }
.btn.sheet.secondary { background: var(--surface-pill); }
.btn:disabled { opacity: 0.4; }
.chevron { color: var(--chevron); flex-shrink: 0; }
.back { display: flex; align-items: center; gap: 4px; height: 44px; font-size: 17px;
        color: var(--link); align-self: flex-start; padding: 0; }
.detail-head { display: flex; flex-direction: column; gap: 6px; }
.detail-title { font-size: 28px; line-height: 34px; font-weight: 700; letter-spacing: -0.3px; }
.step { display: flex; flex-direction: column; gap: 4px; }
.step-name { font-size: 17px; font-weight: 600; }
.list { background: var(--surface-card); border-radius: 18px; display: flex;
        flex-direction: column; }
.list-row { min-height: 64px; padding: 10px 16px; display: flex; align-items: center;
            justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--border); }
.list-row:last-child { border-bottom: 0; }
.row-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.row-title { font-size: 16px; }
.row-line { font-size: 13px; color: var(--text-secondary); }
.more { min-height: 52px; padding: 0 16px; display: flex; align-items: center;
        justify-content: space-between; color: var(--link); font-size: 15px; width: 100%; }
.section-head { display: flex; justify-content: space-between; align-items: baseline;
                padding: 0 4px; }
.recent-row { display: flex; justify-content: space-between; gap: 12px; font-size: 14px; }
.recent-row .when { color: var(--text-secondary); flex-shrink: 0; }
details.disclosure { background: var(--surface-quiet); border: 1px solid var(--border);
                     border-radius: 18px; padding: 0 16px; }
details summary { min-height: 52px; display: flex; align-items: center; font-size: 15px;
                  font-weight: 600; color: var(--text-tertiary); cursor: pointer;
                  list-style: none; }
details summary::-webkit-details-marker { display: none; }
.disclosure-body { display: flex; flex-direction: column; gap: 10px; padding-bottom: 16px;
                   font-size: 13px; color: var(--text-secondary); }
.mono { font-family: var(--mono); font-size: 13px; }
.scroll { overflow-x: auto; white-space: nowrap; }
.quiet { color: var(--text-secondary); font-size: 14px; }
#scrim { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.72); }
#sheet { position: fixed; left: 0; right: 0; bottom: 0; max-height: 92vh; overflow-y: auto;
         background: var(--surface-sheet); border-radius: 24px 24px 0 0;
         padding: 10px calc(16px + env(safe-area-inset-right))
                  calc(34px + env(safe-area-inset-bottom)) calc(16px + env(safe-area-inset-left));
         display: flex; flex-direction: column; gap: 16px; }
.grabber { width: 36px; height: 5px; border-radius: 3px; background: var(--grabber);
           align-self: center; flex-shrink: 0; }
.sheet-head { display: flex; flex-direction: column; gap: 6px; }
.sheet-title { font-size: 22px; line-height: 28px; font-weight: 700; }
.sentence { font-size: 15px; line-height: 22px; color: var(--text-tertiary); }
.inset { background: var(--surface-inset); border-radius: 14px; padding: 12px 14px;
         display: flex; flex-direction: column; gap: 10px; }
.inset.deletes { background: var(--tile-problem); color: var(--accent-problem); }
.inset.checked, .inset.outcome { color: var(--text-tertiary); }
.inset-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.command-text { font-size: 15px; }
.pill { min-height: 44px; min-width: 44px; display: flex; align-items: center;
        justify-content: center; }
.pill span { height: 32px; padding: 0 10px; border-radius: 8px; background: var(--surface-pill);
             font-size: 13px; font-weight: 600; display: flex; align-items: center; }
.pill.row-pill { flex-shrink: 0; margin-left: auto; }
.pill.row-pill span { height: 36px; padding: 0 14px; border-radius: 10px; font-size: 15px;
                      color: var(--text); }
.sheet-toggle { border-top: 1px solid var(--border); padding-top: 4px; }
.sheet-toggle summary { min-height: 44px; font-size: 14px; font-weight: 400; }
.sheet-buttons { display: flex; flex-direction: column; gap: 10px; }
.expiry { font-size: 13px; color: var(--text-secondary); text-align: center; }
.link { color: var(--link); min-height: 44px; padding: 0 8px; display: inline-flex;
        align-items: center; }
.result-head { display: flex; align-items: center; gap: 12px; }
.mark { width: 40px; height: 40px; border-radius: 20px; display: flex; align-items: center;
        justify-content: center; flex-shrink: 0; background: var(--surface-pill); }
.mark-failed { background: var(--chip-problem); color: var(--accent-problem); }
.mark-ok { background: var(--tick-ground); color: var(--live); }
.result-title { font-size: 20px; line-height: 26px; font-weight: 700; }
pre.output { margin: 0; font: 12px/18px var(--mono); color: var(--text-secondary);
             max-height: 160px; overflow: auto; white-space: pre; }
.refused { color: var(--accent-problem); }
.field { display: flex; flex-direction: column; gap: 6px; }
.field input, .field select, .field textarea { font: 16px/22px var(--font); color: var(--text);
  background: var(--surface-inset); border: 1px solid var(--border); border-radius: 12px;
  padding: 10px 12px; min-height: 44px; width: 100%; max-width: 100%; }
.field textarea { min-height: 88px; resize: vertical; }
.start-name { font-family: var(--mono); font-size: 13px; }
form { display: grid; gap: 12px; max-width: 360px; margin-top: 16px; }
form input { font: inherit; padding: 10px; text-transform: uppercase; letter-spacing: .1em;
             min-height: 44px; }
form button { min-height: 44px; background: var(--accent-primary); color: #FFFFFF;
              border-radius: 12px; font-weight: 600; }
"""

_SCRIPT = r"""
"use strict";
const MAX_BACKOFF = __MAX_BACKOFF__;
const STALE_INTERVALS = __STALE_INTERVALS__;
const DEFAULT_INTERVAL = __INTERVAL__;
const WORDS = __WORDS__;

const ORACLE = (function () {
  function retryDelay(interval, failures) {
    if (failures <= 0) return interval;
    return Math.min(interval * Math.pow(2, failures), MAX_BACKOFF);
  }
  function ageOf(state, now) {
    if (!state.data || state.fetchedAt === null) return null;
    return state.data.freshness.age + Math.max(0, (now - state.fetchedAt) / 1000);
  }
  function fill(template, values) {
    return String(template).replace(/\{(\w+)\}/g, (all, name) =>
      Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : all);
  }
  function freshnessText(age, staleAfter) {
    if (age === null) return {text: WORDS.never, stale: true};
    const stale = age > staleAfter;
    return {text: fill(stale ? WORDS.stale : WORDS.live, {age: Math.round(age)}), stale: stale};
  }
  function countdown(left) {
    if (left <= 0) return {text: WORDS.expired, expired: true};
    const whole = Math.ceil(left);
    const time = Math.floor(whole / 60) + ":" + String(whole % 60).padStart(2, "0");
    return {text: fill(WORDS.expires, {time: time}), expired: false};
  }
  function makeLoop(opts) {
    const state = {failures: 0, data: null, fetchedAt: null};
    let timer = null;
    let inFlight = null;
    async function read() {
      try {
        const data = await opts.fetchState();
        state.data = data;
        state.fetchedAt = opts.now();
        state.failures = 0;
        opts.onData(data);
      } catch (err) {
        state.failures += 1;
        opts.onError(err, state.failures);
      } finally {
        const interval = state.data ? state.data.freshness.interval : opts.interval;
        timer = opts.schedule(tick, retryDelay(interval, state.failures) * 1000);
      }
    }
    function tick() {
      if (inFlight) return inFlight;
      inFlight = read().finally(() => { inFlight = null; });
      return inFlight;
    }
    // Read now, not at the end of a backoff: the page was looked at again,
    // or the network came back, and the server may be back with it.
    function wake() {
      if (inFlight) return inFlight;
      if (timer !== null && opts.cancel) opts.cancel(timer);
      timer = null;
      return tick();
    }
    return {tick: tick, wake: wake, state: state};
  }
  // A job poll's answer: show it, keep polling, or the server forgot it
  // (it restarted; the Mac's audit log still has the action).
  function jobPoll(status) {
    if (status === 200) return "show";
    if (status === 404) return "lost";
    return "retry";
  }
  function actionBody(row, nonce) {
    return JSON.stringify({row: row, nonce: nonce});
  }
  // A start names a registered project by its key and nothing else; the label
  // and the first message travel only to be confirmed, and the start itself
  // carries the project and the nonce the Mac answered with.
  function startUrl(form) {
    return "/api/start/confirm?project=" + encodeURIComponent(form.project) +
      "&label=" + encodeURIComponent(form.label) + "&prompt=" + encodeURIComponent(form.prompt);
  }
  function startBody(project, nonce) {
    return JSON.stringify({project: project, nonce: nonce});
  }
  // The Mac moved the page to HTTPS: the fetch saw a redirect it did not follow.
  function moved(response) {
    return response.type === "opaqueredirect";
  }
  function clock(at, now) {
    const when = new Date(at * 1000);
    if (when.toDateString() === new Date(now).toDateString()) {
      return when.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
    }
    return when.toLocaleDateString([], {day: "numeric", month: "short"});
  }

  // -- drawing: h(tag, props, children) builds an element; on.* are the actions.
  const ICONS = {
    refresh: [["path", {d: "M21 12a9 9 0 1 1-2.6-6.4"}], ["path", {d: "M21 4v5h-5"}]],
    right: [["path", {d: "M9 6l6 6-6 6"}]],
    left: [["path", {d: "M15 6l-6 6 6 6"}]],
    down: [["path", {d: "M6 9l6 6 6-6"}]],
    copy: [["rect", {x: "9", y: "9", width: "11", height: "11", rx: "2"}],
           ["path", {d: "M5 15V6a2 2 0 0 1 2-2h9"}]],
    cross: [["path", {d: "M18 6L6 18"}], ["path", {d: "M6 6l12 12"}]],
    tick: [["path", {d: "M5 12l5 5 9-10"}]],
  };
  function icon(h, name, size, stroke, cls) {
    return h("svg", {cls: cls || "icon", attrs: {
      width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
      "stroke-width": stroke || "2", "stroke-linecap": "round", "stroke-linejoin": "round",
      "aria-hidden": "true"}}, ICONS[name].map((part) => h(part[0], {attrs: part[1]})));
  }
  function chips(h, list) {
    return h("div", {cls: "chips"},
             list.map((chip) => h("span", {cls: "chip tone-" + chip.tone, text: chip.text})));
  }
  function disclosure(h, ui, on, id, summary, body, cls) {
    return h("details", {cls: cls || "disclosure", open: !!ui.open[id],
                         on: {toggle: (event, node) => on.toggle(id, node.open)}},
             [h("summary", {text: summary}), h("div", {cls: "disclosure-body"}, body)]);
  }
  function fullCard(h, on, card) {
    const open = () => on.open(card.row);
    const parts = [
      h("div", {cls: "meta"}, [h("span", {cls: "project", text: card.project}),
                               h("span", {cls: "age", text: card.age})]),
      h("div", {cls: "card-title", text: card.title}),
    ];
    if (card.chips.length) parts.push(chips(h, card.chips));
    if (card.note) parts.push(h("div", {cls: "note", text: card.note}));
    if (card.tap) {
      return h("div", {cls: "card tap", attrs: {role: "button", tabindex: "0"},
                       on: {click: open}}, parts);
    }
    const buttons = [];
    if (card.button) {
      buttons.push(h("button", {cls: "btn primary", text: card.button.text,
                                on: {click: () => on.confirm(card.row)}}));
    }
    buttons.push(h("button", {cls: "btn secondary" + (card.button ? " narrow" : ""),
                              text: WORDS.details, on: {click: open}}));
    parts.push(h("div", {cls: "buttons"}, buttons));
    return h("div", {cls: "card"}, parts);
  }
  function compactCard(h, on, card) {
    return h("button", {cls: "card compact", on: {click: () => on.open(card.row)}}, [
      h("span", {cls: "compact-text"}, [
        h("span", {cls: "project", text: card.project}),
        h("span", {cls: "card-title", text: card.title}),
        card.line ? h("span", {cls: "note", text: card.line}) : null]),
      icon(h, "right", 18, "2", "chevron")]);
  }
  // A start of this device: "Starting…" until its window appears, or why it did not.
  function startCard(h, on, card) {
    const parts = [h("span", {cls: "project", text: card.project}),
                   h("span", {cls: "card-title", text: card.title})];
    if (card.chips.length) parts.push(chips(h, card.chips));
    if (card.line) parts.push(h("span", {cls: "note", text: card.line}));
    return h("button", {cls: "card compact start-card start-" + card.state,
                        on: {click: () => on.startJob(card)}},
             [h("span", {cls: "compact-text"}, parts), icon(h, "right", 18, "2", "chevron")]);
  }
  function projects(h, on, start) {
    const rows = start.projects.map((project) => h("div", {cls: "list-row"}, [
      h("span", {cls: "row-text"}, [h("span", {cls: "row-title", text: project.name}),
                                    h("span", {cls: "row-line", text: project.line})]),
      h("button", {cls: "pill row-pill",
                   attrs: {"aria-label": WORDS.new_session + " " + project.name},
                   on: {click: () => on.newSession(project.id)}},
        [h("span", {text: WORDS.new_session})])]));
    return h("div", {cls: "section projects"}, [
      h("div", {cls: "section-head"}, [h("span", {cls: "section-label", text: WORDS.projects})]),
      h("div", {cls: "list"}, rows)]);
  }
  function overview(view, ui, h, on) {
    const nodes = [h("nav", {cls: "strip"}, view.tiles.map((tile) => h("a", {
      cls: "tile tile-" + tile.id, attrs: {href: tile.href},
      on: tile.id === "leftovers" ? {click: (event) => on.leftovers(event)} : null,
    }, [h("span", {cls: "count", text: tile.count}), h("span", {cls: "label", text: tile.label})])))];
    for (const group of view.groups) {
      nodes.push(h("section", {cls: "group group-" + group.id, attrs: {id: group.id}}, [
        h("h2", {cls: "heading", text: group.heading}),
        ...group.cards.map((card) => card.start ? startCard(h, on, card)
                                   : group.compact ? compactCard(h, on, card)
                                   : fullCard(h, on, card))]));
    }
    if (view.empty) nodes.push(h("p", {cls: "quiet", text: view.empty}));
    const left = view.leftovers_card;
    if (left) {
      nodes.push(h("button", {cls: "card quiet-card leftovers-card",
                              on: {click: (event) => on.leftovers(event)}}, [
        h("span", {cls: "compact-text"}, [h("span", {cls: "row-title", text: left.title}),
                                          h("span", {cls: "note", text: left.line})]),
        icon(h, "right", 18, "2", "chevron")]));
    }
    if (view.start && view.start.projects.length) nodes.push(projects(h, on, view.start));
    return nodes;
  }
  function back(h, on) {
    return h("button", {cls: "back", on: {click: () => on.back()}},
             [icon(h, "left", 20, "2.2"), h("span", {text: WORDS.back})]);
  }
  function detail(view, ui, h, on, now) {
    const d = view.details[ui.key];
    const nodes = [back(h, on)];
    const head = [h("span", {cls: "kicker", text: d.kicker}),
                  h("h1", {cls: "detail-title", text: d.title})];
    if (d.chips.length) head.push(chips(h, d.chips));
    nodes.push(h("div", {cls: "detail-head"}, head));
    if (d.going_on.length) {
      nodes.push(h("div", {cls: "card"}, [h("span", {cls: "label", text: WORDS.going_on}),
                                          h("p", {cls: "body", text: d.going_on.join(" ")})]));
    }
    if (d.step) {
      const target = h("div", {cls: "mono scroll copy-text", text: d.step.copy, hidden: true});
      const buttons = [];
      if (d.step.button) {
        buttons.push(h("button", {cls: "btn primary tall", text: d.step.button.text,
                                  on: {click: () => on.confirm(d.row)}}));
      }
      buttons.push(h("button", {cls: "btn secondary square",
                                attrs: {"aria-label": WORDS.copy_command},
                                on: {click: (event, node) => on.copy(d.step.copy, node, target)}},
                     [icon(h, "copy", 20)]));
      nodes.push(h("div", {cls: "card"}, [
        h("span", {cls: "label", text: WORDS.next_step}),
        h("div", {cls: "step"}, [h("span", {cls: "step-name", text: d.step.name}),
                                 h("span", {cls: "note", text: d.step.sentence})]),
        h("div", {cls: "buttons"}, buttons), target]));
    }
    if (d.recent.length) {
      nodes.push(h("span", {cls: "section-label", text: WORDS.recent}));
      nodes.push(h("div", {cls: "card"}, d.recent.map((event) => h("div", {cls: "recent-row"}, [
        h("span", {text: event.text}), h("span", {cls: "when", text: clock(event.at, now)})]))));
    }
    const tech = d.technical.lines.map((line) => h("div", {text: line}));
    if (d.technical.path) tech.push(h("div", {cls: "mono scroll", text: d.technical.path}));
    nodes.push(disclosure(h, ui, on, "technical:" + d.row, WORDS.technical, tech));
    return nodes;
  }
  function leftovers(view, ui, h, on) {
    const screen = view.leftovers;
    const nodes = [back(h, on), h("div", {cls: "detail-head"}, [
      h("h1", {cls: "detail-title", text: screen.title}),
      h("p", {cls: "note", text: screen.intro})])];
    for (const section of screen.sections) {
      if (!section.count) continue;
      const all = ui.more[section.id];
      const rows = (all ? section.rows : section.rows.slice(0, section.shown)).map((row) =>
        h("div", {cls: "list-row"}, [h("span", {cls: "row-text"}, [
          h("span", {cls: "row-title", text: row.title}),
          h("span", {cls: "row-line", text: row.line})]),
          row.button ? h("button", {cls: "pill row-pill",
                                    on: {click: () => on.confirm(row.row)}},
                         [h("span", {text: row.button.text})]) : null]));
      if (section.more && !all) {
        rows.push(h("button", {cls: "more", on: {click: () => on.more(section.id)}},
                    [h("span", {text: section.more}), icon(h, "down", 18)]));
      }
      nodes.push(h("div", {cls: "section"}, [
        h("div", {cls: "section-head"}, [h("span", {cls: "section-label", text: section.heading}),
                                         h("span", {cls: "quiet", text: section.count})]),
        h("div", {cls: "list"}, rows)]));
    }
    return nodes;
  }
  // The screen ui says, or the overview when its session has gone.
  function screen(view, ui, h, on, now) {
    if (ui.screen === "detail" && view.details[ui.key]) return detail(view, ui, h, on, now);
    if (ui.screen === "leftovers") return leftovers(view, ui, h, on);
    ui.screen = "overview";
    ui.key = null;
    return overview(view, ui, h, on);
  }

  function grabber(h) {
    return h("div", {cls: "grabber", attrs: {"aria-hidden": "true"}});
  }
  function messageSheet(h, on, text, closable) {
    const nodes = [grabber(h), h("p", {cls: "sentence", text: text})];
    let close = null;
    if (closable) {
      close = h("button", {cls: "btn sheet secondary", text: WORDS.close,
                           on: {click: () => on.close()}});
      nodes.push(close);
    }
    return {nodes: nodes, focus: close};
  }
  function confirmSheet(h, on, data) {
    if (data.refused || !data.nonce || !data.words) {
      const text = data.refused ? fill(WORDS.not_run, {reason: data.refused})
                                : WORDS.nothing_to_run;
      return messageSheet(h, on, text, true);
    }
    const w = data.words;
    const command = h("div", {cls: "mono scroll command-text", text: w.command});
    const copy = h("button", {cls: "pill copy",
                              on: {click: (event, node) => on.copy(w.command, node, command)}},
                   [h("span", {text: WORDS.copy})]);
    const block = h("div", {cls: "inset"}, [
      h("div", {cls: "inset-head"}, [h("span", {cls: "label", text: WORDS.command}), copy]),
      command, h("div", {cls: "note", text: w.place})]);
    const exact = [];
    if (w.cwd) exact.push(h("div", {cls: "mono scroll", text: w.cwd}));
    for (const line of w.env) exact.push(h("div", {cls: "mono scroll", text: line}));
    if (w.launch) exact.push(h("div", {cls: "mono scroll", text: w.launch}));
    exact.push(h("div", {text: WORDS.runs_as}));
    const nodes = [grabber(h), h("div", {cls: "sheet-head"}, [
      h("h2", {cls: "sheet-title", text: w.title}), h("p", {cls: "sentence", text: w.sentence})])];
    if (w.destructive) {
      if (w.deletes.length) {
        nodes.push(h("div", {cls: "inset deletes"}, [
          h("span", {cls: "label", text: w.deletes_heading || WORDS.this_deletes}),
          ...w.deletes.map((line) => h("div", {text: line}))]));
      }
      if (w.checked && w.checked.length) {
        nodes.push(h("div", {cls: "inset checked"}, [
          h("span", {cls: "label", text: WORDS.checked_now}),
          ...w.checked.map((line) => h("div", {text: line}))]));
      }
      nodes.push(h("details", {cls: "sheet-toggle"}, [
        h("summary", {text: WORDS.exact_command}),
        h("div", {cls: "disclosure-body"}, [block, ...exact])]));
    } else {
      nodes.push(block, h("details", {cls: "sheet-toggle"}, [
        h("summary", {text: WORDS.exact}), h("div", {cls: "disclosure-body"}, exact)]));
    }
    const run = h("button", {cls: "btn sheet " + (w.destructive ? "destructive" : "primary"),
                             text: w.button,
                             on: {click: (event, node) => {
                               node.disabled = true;
                               on.act(data.row, data.nonce);
                             }}});
    const cancel = h("button", {cls: "btn sheet secondary", text: WORDS.cancel,
                                on: {click: () => on.close()}});
    const expiry = h("div", {cls: "expiry", text: countdown(data.expires_in).text});
    nodes.push(h("div", {cls: "sheet-buttons"}, w.destructive ? [cancel, run] : [run, cancel]),
               expiry);
    return {nodes: nodes, focus: cancel, run: run, expiry: expiry};
  }
  function field(h, label, children) {
    return h("label", {cls: "field"}, [h("span", {cls: "label", text: label}), ...children]);
  }
  // The "New session" sheet. Its fields keep what the person typed; fillStart
  // puts the Mac's answer for them in place without rebuilding the sheet, so a
  // field never loses its focus or its caret to an answer.
  function startSheet(h, on, projectList, form, data) {
    const edit = (name) => (event, node) => on.startEdit(name, node.value);
    const picker = h("select", {attrs: {"aria-label": WORDS.project_field},
                                value: form.project, on: {change: edit("project")}},
                     projectList.map((p) => h("option", {attrs: {value: p.id}, text: p.name})));
    const label = h("input", {attrs: {type: "text", autocomplete: "off", autocapitalize: "none",
                                      spellcheck: "false", "aria-label": WORDS.label_field},
                              value: form.label, on: {input: edit("label")}});
    const prompt = h("textarea", {attrs: {rows: "3", "aria-label": WORDS.prompt_field},
                                  value: form.prompt, on: {input: edit("prompt")}});
    const built = {
      picker: picker, label: label, prompt: prompt,
      title: h("h2", {cls: "sheet-title"}), sentence: h("p", {cls: "sentence"}),
      name: h("div", {cls: "note start-name"}), facts: h("div", {cls: "inset checked"}),
      refused: h("p", {cls: "refused start-refused", hidden: true}),
      command: h("div", {cls: "mono scroll command-text"}), place: h("div", {cls: "note"}),
      cwd: h("div", {cls: "mono scroll"}), expiry: h("div", {cls: "expiry"}),
      project: data.project, nonce: null,
    };
    const copy = h("button", {cls: "pill copy", on: {click: (event, node) =>
      on.copy(built.command.textContent, node, built.command)}}, [h("span", {text: WORDS.copy})]);
    built.run = h("button", {cls: "btn sheet primary", text: data.words.button,
                             on: {click: (event, node) => {
                               node.disabled = true;
                               on.startAct();
                             }}});
    built.focus = h("button", {cls: "btn sheet secondary", text: WORDS.cancel,
                               on: {click: () => on.close()}});
    built.nodes = [
      grabber(h), h("div", {cls: "sheet-head"}, [built.title, built.sentence]),
      field(h, WORDS.project_field, [picker]),
      field(h, WORDS.label_field, [label, built.name]),
      field(h, WORDS.prompt_field, [prompt, h("span", {cls: "note", text: WORDS.prompt_hint})]),
      built.facts, built.refused,
      h("details", {cls: "sheet-toggle"}, [
        h("summary", {text: WORDS.exact_start}),
        h("div", {cls: "disclosure-body"}, [
          h("div", {cls: "inset"}, [
            h("div", {cls: "inset-head"}, [h("span", {cls: "label", text: WORDS.command}), copy]),
            built.command, built.place]),
          built.cwd])]),
      h("div", {cls: "sheet-buttons"}, [built.run, built.focus]), built.expiry];
    fillStart(h, built, data);
    return built;
  }
  function fillStart(h, built, data) {
    const w = data.words;
    built.refused.hidden = !data.refused;
    built.refused.textContent = data.refused ? fill(WORDS.not_run, {reason: data.refused}) : "";
    built.nonce = data.nonce || null;
    if (data.project) built.project = data.project;
    built.run.disabled = !built.nonce;
    if (!w) return;
    built.title.textContent = w.title;
    built.sentence.textContent = w.sentence;
    built.name.textContent = w.name_line;
    built.facts.replaceChildren(h("span", {cls: "label", text: WORDS.checked_now}),
                                ...w.facts.map((line) => h("div", {text: line})));
    built.command.textContent = w.command;
    built.place.textContent = w.place;
    built.cwd.textContent = w.cwd || "";
  }
  function resultSheet(h, on, ui, job) {
    const w = job.words;
    const mark = h("span", {cls: "mark mark-" + w.tone},
                   w.tone === "running" ? [] : [icon(h, w.tone === "ok" ? "tick" : "cross", 20,
                                                     "2.4")]);
    const nodes = [grabber(h), h("div", {cls: "result-head"}, [mark, h("div", {cls: "step"}, [
      h("h2", {cls: "result-title", text: w.headline}),
      h("span", {cls: "note", text: w.line})])])];
    if (job.remote_control && job.state === "done" && job.ok) {
      nodes.push(h("p", {cls: "sentence",
                         text: fill(WORDS.remote_control, {name: job.remote_control})}));
    }
    if (w.why) {
      nodes.push(h("div", {cls: "inset why"}, [h("span", {cls: "label", text: WORDS.why}),
                                                h("p", {cls: "sentence", text: w.why})]));
    }
    if (w.outcome) {
      nodes.push(h("div", {cls: "inset outcome"}, [
        h("span", {cls: "label", text: WORDS.outcome}),
        h("p", {cls: "sentence", text: w.outcome})]));
    }
    if (job.output) {
      nodes.push(disclosure(h, ui, on, "output:" + job.id, WORDS.show_output,
                            [h("pre", {cls: "output", text: job.output})], "disclosure inset"));
    }
    if (job.audit_error) nodes.push(h("p", {cls: "refused", text: job.audit_error}));
    const done = h("button", {cls: "btn sheet secondary", text: WORDS.done,
                              on: {click: () => on.close()}});
    nodes.push(done);
    return {nodes: nodes, focus: done};
  }
  return {retryDelay: retryDelay, ageOf: ageOf, fill: fill, freshnessText: freshnessText,
          countdown: countdown, makeLoop: makeLoop, actionBody: actionBody, jobPoll: jobPoll,
          moved: moved, clock: clock, screen: screen, messageSheet: messageSheet,
          confirmSheet: confirmSheet, resultSheet: resultSheet, startUrl: startUrl,
          startBody: startBody, startSheet: startSheet, fillStart: fillStart};
})();

if (typeof document !== "undefined") {
  const byId = (id) => document.getElementById(id);
  const SVG = "http://www.w3.org/2000/svg";
  const SVG_TAGS = {svg: true, path: true, rect: true};
  const ui = {screen: "overview", key: null, open: {}, more: {}, scroll: {}};
  let current = null;
  let lastError = "";
  let panelJob = null;
  let countdownTimer = null;
  // The open "New session" sheet: what it asks for, its handles, its pause.
  let startForm = null;
  let startBuilt = null;
  let startPause = null;
  let startAsked = 0;

  function h(tag, props, children) {
    const p = props || {};
    const node = SVG_TAGS[tag] ? document.createElementNS(SVG, tag) : document.createElement(tag);
    if (p.cls) node.setAttribute("class", p.cls);
    if (p.text !== undefined && p.text !== null) node.textContent = String(p.text);
    const attrs = p.attrs || {};
    for (const name of Object.keys(attrs)) node.setAttribute(name, String(attrs[name]));
    if (p.open) node.open = true;
    if (p.hidden) node.hidden = true;
    const handlers = p.on || {};
    for (const name of Object.keys(handlers)) {
      node.addEventListener(name, (event) => handlers[name](event, node));
    }
    for (const child of children || []) if (child) node.appendChild(child);
    // After the children: a select takes a value only once its options are there.
    if (p.value !== undefined && p.value !== null) node.value = String(p.value);
    return node;
  }

  function draw() {
    if (!current) return;
    const y = window.scrollY;
    const nodes = ORACLE.screen(current.view, ui, h, on, Date.now());
    byId("screen").replaceChildren(...nodes);
    byId("screen").className = ui.screen === "overview" ? "" : "sub";
    byId("top").className = ui.screen === "overview" ? "" : "sub";
    window.scrollTo(0, y);
  }

  function go(screen, key, push) {
    ui.scroll[ui.screen] = window.scrollY;
    ui.screen = screen;
    ui.key = key || null;
    ui.more = {};
    if (push) history.pushState({screen: screen, key: ui.key}, "");
    draw();
    window.scrollTo(0, screen === "overview" ? (ui.scroll.overview || 0) : 0);
  }

  function showSheet(built) {
    const sheet = byId("sheet");
    sheet.replaceChildren(...built.nodes);
    sheet.hidden = false;
    byId("scrim").hidden = false;
    if (built.focus) built.focus.focus();
  }

  function stopCountdown() {
    if (countdownTimer !== null) clearInterval(countdownTimer);
    countdownTimer = null;
  }

  function closeSheet() {
    panelJob = null;
    stopCountdown();
    if (startPause !== null) clearTimeout(startPause);
    startPause = null;
    startForm = null;
    startBuilt = null;
    const sheet = byId("sheet");
    sheet.hidden = true;
    sheet.replaceChildren();
    byId("scrim").hidden = true;
  }

  function message(text) {
    showSheet(ORACLE.messageSheet(h, on, text, true));
  }

  function select(target) {
    if (!target) return;
    target.hidden = false;
    const range = document.createRange();
    range.selectNodeContents(target);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

  // Over HTTPS, the clipboard; over plain HTTP it is not there, so the text is
  // selected instead (ADR-009 D5). Copying runs nothing and spends no nonce.
  function copy(text, button, target) {
    const said = () => {
      const label = button.firstChild && button.firstChild.tagName === "SPAN"
        ? button.firstChild : null;
      if (label) label.textContent = WORDS.copied;
      button.setAttribute("aria-label", WORDS.copied);
    };
    if (window.isSecureContext && navigator.clipboard) {
      navigator.clipboard.writeText(text).then(said, () => select(target));
    } else {
      select(target);
    }
  }

  async function openConfirm(row) {
    closeSheet();
    showSheet(ORACLE.messageSheet(h, on, WORDS.loading, false));
    let data;
    try {
      const response = await fetch("/api/confirm?row=" + encodeURIComponent(row),
                                   {cache: "no-store", credentials: "same-origin"});
      data = await response.json();
    } catch (err) {
      message(ORACLE.fill(WORDS.confirm_failed, {error: err.message}));
      return;
    }
    const built = ORACLE.confirmSheet(h, on, data);
    showSheet(built);
    if (!built.run) return;
    expireIn(built, data.expires_in, () => openConfirm(row));
  }

  // Count the nonce down on a sheet; at zero the deed is off and "Look again" asks anew.
  function expireIn(built, seconds, again) {
    stopCountdown();
    const opened = Date.now();
    built.expiry.textContent = ORACLE.countdown(seconds).text;
    countdownTimer = setInterval(() => {
      const said = ORACLE.countdown(seconds - (Date.now() - opened) / 1000);
      built.expiry.textContent = said.text;
      if (!said.expired) return;
      stopCountdown();
      built.run.disabled = true;
      built.expiry.appendChild(h("button", {cls: "link", text: WORDS.look_again,
                                            on: {click: again}}));
    }, 1000);
  }

  // Ask the Mac what the sheet's fields would start. {data}, {error}, or null
  // when a later question has been asked since, whose answer is the one shown.
  async function askStart() {
    const asked = ++startAsked;
    try {
      const response = await fetch(ORACLE.startUrl(startForm),
                                   {cache: "no-store", credentials: "same-origin"});
      const data = await response.json();
      return asked === startAsked ? {data: data} : null;
    } catch (err) {
      return asked === startAsked ? {error: err.message} : null;
    }
  }

  async function openStart(project) {
    const start = current && current.view.start;
    if (!start || !start.projects.length) return;
    closeSheet();
    startForm = {project: project || start.projects[0].id, label: "", prompt: ""};
    showSheet(ORACLE.messageSheet(h, on, WORDS.loading, false));
    const answer = await askStart();
    if (!answer || !startForm) return;
    if (answer.error) {
      message(ORACLE.fill(WORDS.confirm_failed, {error: answer.error}));
      return;
    }
    const data = answer.data;
    if (!data.words) {
      message(data.refused ? ORACLE.fill(WORDS.not_run, {reason: data.refused})
                           : WORDS.nothing_to_run);
      return;
    }
    startForm.label = data.label || "";
    startBuilt = ORACLE.startSheet(h, on, start.projects, startForm, data);
    showSheet(startBuilt);
    expireIn(startBuilt, data.expires_in, askAgain);
  }

  // An edit: the deed waits, and the Mac is asked once the typing pauses.
  function startEdit(name, value) {
    if (!startForm || !startBuilt) return;
    startForm[name] = value;
    startBuilt.run.disabled = true;
    if (startPause !== null) clearTimeout(startPause);
    startPause = setTimeout(askAgain, 400);
  }

  async function askAgain() {
    startPause = null;
    if (!startBuilt) return;
    const built = startBuilt;
    startForm.project = built.picker.value;
    startForm.label = built.label.value;
    startForm.prompt = built.prompt.value;
    built.run.disabled = true;
    const answer = await askStart();
    if (!answer || startBuilt !== built) return;
    const data = answer.error ? {refused: ORACLE.fill(WORDS.confirm_failed,
                                                      {error: answer.error})} : answer.data;
    ORACLE.fillStart(h, built, data);
    if (built.nonce) expireIn(built, data.expires_in, askAgain);
  }

  function startAct() {
    if (!startBuilt || !startBuilt.nonce) return;
    return postAction("/api/start", ORACLE.startBody(startBuilt.project, startBuilt.nonce));
  }

  function confirmAct(row, nonce) {
    return postAction("/api/act", ORACLE.actionBody(row, nonce));
  }

  async function postAction(url, body) {
    stopCountdown();
    let response, data;
    try {
      response = await fetch(url, {
        method: "POST", cache: "no-store", credentials: "same-origin",
        headers: {"Content-Type": "application/json"}, body: body,
      });
      data = await response.json();
    } catch (err) {
      message(ORACLE.fill(WORDS.unreached, {error: err.message}));
      return;
    }
    if (response.status !== 202) {
      message(ORACLE.fill(WORDS.not_run, {reason: data.error || "HTTP " + response.status}));
      return;
    }
    showSheet(ORACLE.resultSheet(h, on, ui, data.job));
    if (data.job.state !== "done") pollJob(data.job.id);
  }

  function pollJob(id) {
    panelJob = id;
    const poll = async () => {
      if (panelJob !== id) return;
      try {
        const response = await fetch("/api/job?id=" + encodeURIComponent(id),
                                     {cache: "no-store", credentials: "same-origin"});
        const verdict = ORACLE.jobPoll(response.status);
        if (verdict === "lost") {
          if (panelJob === id) message(WORDS.lost);
          return;
        }
        if (verdict === "show") {
          const job = await response.json();
          if (panelJob !== id) return;
          showSheet(ORACLE.resultSheet(h, on, ui, job));
          if (job.state === "done") return;
        }
      } catch (err) {}
      setTimeout(poll, 1000);
    };
    setTimeout(poll, 1000);
  }

  const on = {
    open: (key) => go("detail", key, true),
    back: () => {
      if (history.state && history.state.screen) history.back();
      else go("overview", null, false);
    },
    leftovers: (event) => {
      if (event && event.preventDefault) event.preventDefault();
      go("leftovers", null, true);
    },
    toggle: (id, open) => { ui.open[id] = open; },
    more: (id) => { ui.more[id] = true; draw(); },
    confirm: (row) => openConfirm(row),
    act: (row, nonce) => confirmAct(row, nonce),
    newSession: (project) => openStart(project),
    startEdit: startEdit,
    startAct: startAct,
    // A start's card: its result, live while it runs; a window that never
    // came is shown as the failure the card says it is.
    startJob: (card) => {
      const job = ((current && current.jobs) || []).find((j) => j.id === card.start);
      if (!job) return;
      closeSheet();
      const shown = card.result ? Object.assign({}, job, {ok: false, words: card.result}) : job;
      showSheet(ORACLE.resultSheet(h, on, ui, shown));
      if (!card.result && job.state !== "done") pollJob(job.id);
    },
    copy: copy,
    close: closeSheet,
  };

  function render(data) {
    current = data;
    draw();
    const banner = byId("banner");
    banner.textContent = data.freshness.banner || "";
    banner.hidden = !data.freshness.banner;
    byId("newsession").hidden = !data.view.start;
    document.title = data.view.doc_title;
  }

  const loop = ORACLE.makeLoop({
    interval: DEFAULT_INTERVAL,
    now: () => Date.now(),
    fetchState: async () => {
      const response = await fetch("/api/state", {cache: "no-store", credentials: "same-origin",
                                                  redirect: "manual"});
      if (ORACLE.moved(response)) {
        location.reload();
        throw new Error(WORDS.moved);
      }
      if (response.status === 401) throw new Error(WORDS.not_paired_any_more);
      if (!response.ok) {
        let error = "HTTP " + response.status;
        try { error = (await response.json()).error || error; } catch (e) {}
        throw new Error(error);
      }
      return response.json();
    },
    schedule: (fn, ms) => setTimeout(fn, ms),
    cancel: (handle) => clearTimeout(handle),
    onData: (data) => { lastError = ""; render(data); showAge(); },
    onError: (err, failures) => {
      lastError = ORACLE.fill(WORDS.refresh_failed, {count: failures, error: err.message});
      showAge();
    },
  });

  function showAge() {
    const data = loop.state.data;
    const staleAfter = data ? data.freshness.stale_after : STALE_INTERVALS * DEFAULT_INTERVAL;
    const said = ORACLE.freshnessText(ORACLE.ageOf(loop.state, Date.now()), staleAfter);
    byId("freshtext").textContent = said.text;
    byId("fresh").className = "fresh" + (said.stale || lastError ? " stale" : "");
    const error = byId("error");
    error.textContent = lastError;
    error.hidden = !lastError;
  }

  window.addEventListener("popstate", (event) => {
    const state = event.state || {screen: "overview", key: null};
    if (state.screen !== ui.screen || (state.key || null) !== ui.key) {
      go(state.screen, state.key, false);
    }
  });
  byId("refresh").addEventListener("click", () => loop.wake());
  byId("newsession").addEventListener("click", () => openStart(null));
  byId("scrim").addEventListener("click", closeSheet);
  setInterval(showAge, 1000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") loop.wake();
  });
  for (const name of ["online", "pageshow", "focus"]) {
    window.addEventListener(name, () => loop.wake());
  }
  loop.tick();
}
"""

_REFRESH_ICON = (
    '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M21 12a9 9 0 1 1-2.6-6.4"></path><path d="M21 4v5h-5"></path></svg>'
)


#: Where the manifest and the icons are served, and each icon's size in pixels.
MANIFEST_PATH = "/manifest.webmanifest"
APPLE_TOUCH_ICON = "/icon-180.png"
ICONS = {APPLE_TOUCH_ICON: 180, "/icon-192.png": 192, "/icon-512.png": 512}
_MANIFEST_ICONS = ("/icon-192.png", "/icon-512.png")


def manifest(words: Mapping[str, str]) -> str:
    """The web app manifest: the panel, standalone, on its own dark ground."""
    return json.dumps({
        "name": words["app_name"],
        "short_name": words["app_short_name"],
        "id": "/",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": token("bg"),
        "theme_color": token("bg"),
        "icons": [{"src": path, "sizes": f"{ICONS[path]}x{ICONS[path]}", "type": "image/png"}
                  for path in _MANIFEST_ICONS],
    }, ensure_ascii=False, indent=2)


#: The glyph, in fractions of the icon's side: the summary strip's four
#: accents as a two-by-two grid of rounded tiles, inside the central circle a
#: maskable icon keeps (radius 0.4), on the page's ground.
_GLYPH_MARGIN, _GLYPH_GAP, _GLYPH_RADIUS = 0.25, 0.05, 0.05
_GLYPH_ACCENTS = (("accent-needs", "accent-problem"), ("accent-working", "accent-idle"))


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def _tile_cover(x: float, y: float, box: tuple[float, float, float, float],
                radius: float) -> float:
    """How much of the pixel centred on ``(x, y)`` a rounded box covers, 0 to 1.

    The box's signed distance, read as a coverage over one pixel's width, so
    the edges are smooth without supersampling.
    """
    left, top, right, bottom = box
    cx, cy = (left + right) / 2, (top + bottom) / 2
    qx = abs(x - cx) - ((right - left) / 2 - radius)
    qy = abs(y - cy) - ((bottom - top) / 2 - radius)
    outside = (max(qx, 0.0) ** 2 + max(qy, 0.0) ** 2) ** 0.5
    distance = outside + min(max(qx, qy), 0.0) - radius
    return min(max(0.5 - distance, 0.0), 1.0)


@cache
def icon(size: int) -> bytes:
    """The home-screen icon as a PNG, ``size`` pixels square: one of :data:`ICONS`."""
    if size not in ICONS.values():
        raise ValueError(f"no {size}-pixel icon; the sizes are {sorted(ICONS.values())}")
    ground = _rgb(token("bg"))
    side = (1 - 2 * _GLYPH_MARGIN - _GLYPH_GAP) / 2
    tiles = []
    for row, names in enumerate(_GLYPH_ACCENTS):
        for column, name in enumerate(names):
            left = (_GLYPH_MARGIN + column * (side + _GLYPH_GAP)) * size
            top = (_GLYPH_MARGIN + row * (side + _GLYPH_GAP)) * size
            tiles.append(((left, top, left + side * size, top + side * size), _rgb(token(name))))
    radius = _GLYPH_RADIUS * size
    blank = bytes(ground) * size
    raw = bytearray()
    for y in range(size):
        line = bytearray(blank)
        for (left, top, right, bottom), colour in tiles:
            if not top - 1 <= y <= bottom:
                continue
            for x in range(max(int(left) - 1, 0), min(int(right) + 2, size)):
                cover = _tile_cover(x + 0.5, y + 0.5, (left, top, right, bottom), radius)
                if cover:
                    line[3 * x:3 * x + 3] = bytes(
                        round(g + (c - g) * cover) for g, c in zip(ground, colour))
        raw += b"\x00" + line
    return _png(size, size, bytes(raw))


def _png(width: int, height: int, raw: bytes) -> bytes:
    """An 8-bit RGB PNG of ``raw``: each row its filter byte, then its pixels."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body)))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _app_head(words: Mapping[str, str]) -> str:
    """What makes the panel a home-screen app: its manifest, icon and iOS tags."""
    return (
        f'<link rel="manifest" href="{MANIFEST_PATH}" crossorigin="use-credentials">'
        f'<link rel="apple-touch-icon" sizes="180x180" href="{APPLE_TOUCH_ICON}">'
        '<meta name="apple-mobile-web-app-capable" content="yes">'
        '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
        f'<meta name="apple-mobile-web-app-title" '
        f'content="{escape(words["app_short_name"], quote=True)}">'
        f'<meta name="theme-color" content="{token("bg")}">'
    )


def _document(nonce: str, title: str, body: str, script: str = "", head: str = "") -> str:
    tail = f'<script nonce="{nonce}">{script}</script>' if script else ""
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1, '
        'viewport-fit=cover">'
        '<meta name="color-scheme" content="dark">'
        f"{head}<title>{escape(title)}</title>"
        f'<style nonce="{nonce}">{_STYLE}</style></head>'
        f"<body>{body}{tail}</body></html>\n"
    )


def _warning(warning: str | None) -> str:
    return f'<div id="insecure" class="notice">{escape(warning)}</div>' if warning else ""


def _script_json(value: object) -> str:
    """JSON that cannot end the ``<script>`` it sits in."""
    return (json.dumps(value, ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def page(nonce: str, words: Mapping[str, str], interval: float = DEFAULT_INTERVAL,
         warning: str | None = None) -> str:
    """The panel: a header with its freshness line, then the screen the script draws.

    ``words`` are ``oracle/panel``'s fixed strings, embedded for the script.
    """
    body = (
        '<header id="top"><div class="headrow"><div class="headtext">'
        f'<h1 id="title">{escape(words["title"])}</h1>'
        f'<div id="fresh" class="fresh stale"><span class="dot" aria-hidden="true"></span>'
        f'<span id="freshtext">{escape(words["never"])}</span></div></div>'
        '<div class="headbtns">'
        f'<button id="newsession" class="newbtn" hidden>{escape(words["new_session"])}</button>'
        f'<button id="refresh" class="iconbtn" aria-label="{escape(words["refresh"])}">'
        f"{_REFRESH_ICON}</button></div></div>"
        f"{_warning(warning)}"
        '<div id="banner" class="notice" hidden></div>'
        '<div id="error" class="notice" hidden></div></header>'
        '<main id="screen"></main>'
        '<div id="scrim" hidden></div>'
        '<div id="sheet" role="dialog" aria-modal="true" hidden></div>'
    )
    script = (
        _SCRIPT.replace("__MAX_BACKOFF__", f"{MAX_BACKOFF:g}")
        .replace("__STALE_INTERVALS__", str(STALE_INTERVALS))
        .replace("__INTERVAL__", f"{interval:g}")
        .replace("__WORDS__", _script_json(dict(words)))
    )
    return _document(nonce, words["title"], body, script, head=_app_head(words))


def message(nonce: str, title: str, text: str) -> str:
    return _document(nonce, title, f"<h1>{escape(title)}</h1><p>{escape(text)}</p>")


def not_paired(nonce: str) -> str:
    """All an unpaired device ever sees: no rows, no project, no name.

    It links the pairing form, so a home-screen app, which may not share the
    browser's cookie (ADR-009 D10), can be paired from inside itself.
    """
    body = (
        "<h1>not paired</h1>"
        "<p>This device is not paired with the oracle. On the Mac, run "
        "'invariant oracle web pair' and open the link it prints here, or "
        "type the code it prints into the pairing form.</p>"
        '<p><a class="link" href="/pair">Enter a pairing code</a></p>'
    )
    return _document(nonce, "not paired", body)


def pair_form(nonce: str, code: str, warning: str | None = None) -> str:
    """The form a pairing link opens. Opening it spends nothing; the button does."""
    body = (
        "<h1>Pair this device</h1>"
        f"{_warning(warning)}"
        "<p>The code was printed by 'invariant oracle web pair' on the Mac. "
        "It works once, for a few minutes.</p>"
        '<form method="post" action="/pair">'
        '<label>Pairing code <input name="code" required autocomplete="one-time-code" '
        f'autocapitalize="characters" value="{escape(code, quote=True)}"></label>'
        '<button type="submit">Pair this device</button></form>'
    )
    return _document(nonce, "pair this device", body)
