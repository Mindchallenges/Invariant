"""The HTML the oracle's web server sends: the control panel and three small pages.

The panel (IDEA_027, ADR-009) is one page with its screens drawn in it, the
overview, a session's detail, the leftovers, a project's chain and a
session's conversation, and bottom sheets for a confirm and its result. Everything is inline, so the page needs nothing but
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

**It shows each project's chain** (ADR-027 increment 3, ruling 15). A
project's row under "Projects" carries its chain line and opens its Chain
screen, a view inside the page like the detail: what the chain is doing, with
Pause or Resume; the chained start it follows, with Continue, Retry and Close
when it is stalled; the proposals with Start; and the queue, each entry with
move up, move down and Remove. The chain's cards under Needs you carry their
actions. Every one of them opens the same confirm sheet as a row's step, by
the action's key (ruling 14).

**It lets this device opt in to web push** (ADR-027 increment 4, ruling
17). Where the Mac offers push, the overview ends with a Notifications row:
what this device gets, and one pill, "Turn on" or "Turn off". The script
registers the service worker (:func:`service_worker`, served at
:data:`WORKER_PATH`) and, on the tap, subscribes with the key the view
carries, so the browser's own permission prompt is the confirm; the
subscription goes to the Mac and nothing else. A browser without the push
API, an iPhone outside the home-screen app and a blocked permission are
each said in words from ``WORDS``.

**It shows a live window's last message** (ADR-029 increment 1, ADR-009
D17). A card carries Claude's last text in an inset block under "Claude",
three lines at most on a full card and one on a compact card or a leftover
window's row; the detail's "Last message" shows the prompt it answers under
"You", then the text whole, folded behind "Show all" when the view says it is
long. Every word of it is set with ``textContent``, so markup in it is shown
as its characters; a row with none draws nothing in its place.

**It shows a live window's conversation** (ADR-029 increment 2, ADR-009
D17). "Open conversation" in a session's detail opens its Conversation
screen, a view inside the page like the detail: the kicker, the title and the
chips, then the turns from ``/api/session``, oldest first, newest at the
bottom: the person's under "You" on a quiet card, Claude's under "Claude" on
a card, a tool call as one monospace line, a cut text ending "Cut here: the
rest is on the Mac", all set with ``textContent``. While it is open and the
page visible it asks again every :data:`CONVERSATION_INTERVAL` seconds with
the last cursor, backing off on a failure. It opens at the bottom and stays
there as turns arrive, unless the person scrolled up; then "New messages"
floats at the foot and scrolls down. A read that stopped at a shape the host
does not know shows "Can't read this session here — use the Mac." above the
newer turns; a gone window says so in place of them.

**It drops every session's text in the background** (ADR-009 D17). Once the
page has been hidden for :data:`BACKGROUND_DROP` seconds, the read state (the
view, with its excerpts) and the open conversation are cleared from the
screen and from memory, keeping only how fresh the last read was; coming
back shows "Reading again…" until the next read. A drop the timer missed,
because the browser suspended it, is made on return.

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

#: How often an open conversation asks again, in seconds (ADR-029 D11).
CONVERSATION_INTERVAL = 2.0

#: How long the page may be hidden before it drops every session's text, in
#: seconds (ADR-009 D17).
BACKGROUND_DROP = 60.0

#: The tokens of docs/architecture/oracle-panel.rst, "Tokens", declared once.
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
button.row-text { text-align: left; align-items: flex-start; flex: 1; min-height: 44px;
                  padding: 0; }
.row-brief { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
             overflow: hidden; }
.inset.excerpt { gap: 4px; }
.excerpt-label { font-size: 13px; font-weight: 600; color: var(--text-tertiary); }
.excerpt-text { margin: 0; font-size: 15px; line-height: 22px; color: var(--text-secondary);
                white-space: pre-wrap; overflow-wrap: break-word; }
.excerpt-text.clamp { display: -webkit-box; -webkit-line-clamp: 3;
                      -webkit-box-orient: vertical; overflow: hidden; }
.excerpt-line { font-size: 13px; line-height: 18px; color: var(--text-tertiary);
                white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.show-all { align-self: flex-start; padding: 0; }
.talk-open { flex: none; width: 100%; }
.talk-title { font-size: 22px; line-height: 28px; font-weight: 700; }
.turns { display: flex; flex-direction: column; gap: 12px; }
.turn { gap: 4px; }
.turn-text { margin: 0; font-size: 15px; line-height: 22px; white-space: pre-wrap;
             overflow-wrap: break-word; }
.cut-line { font-size: 13px; line-height: 18px; color: var(--text-tertiary); }
.tool-line { color: var(--text-tertiary); line-height: 18px; }
.new-pill { position: fixed; left: 50%; transform: translateX(-50%);
            bottom: calc(24px + env(safe-area-inset-bottom)); height: 44px; border-radius: 22px;
            padding: 0 16px; background: var(--surface-button); color: var(--link);
            font-size: 15px; font-weight: 600; white-space: nowrap; }
#composer { position: fixed; left: 0; right: 0; bottom: 0; background: var(--surface-sheet);
             border-radius: 18px 18px 0 0; display: flex; flex-direction: column; gap: 6px;
             padding: 10px calc(16px + env(safe-area-inset-right))
                      calc(10px + env(safe-area-inset-bottom))
                      calc(16px + env(safe-area-inset-left)); }
main.talking { padding-bottom: 96px; }
main.talking .new-pill { bottom: calc(96px + env(safe-area-inset-bottom)); }
.composer-row { display: flex; gap: 8px; align-items: flex-end; }
.composer-input { flex: 1; font: 16px/22px var(--font); color: var(--text);
                  background: var(--surface-inset); border: 1px solid var(--border);
                  border-radius: 22px; padding: 10px 14px; min-height: 44px;
                  max-height: 132px; resize: none; overflow-y: auto; }
.send-btn { flex: 0 0 44px; width: 44px; height: 44px; border-radius: 22px;
            background: var(--accent-primary); color: #FFFFFF; display: flex;
            align-items: center; justify-content: center; padding: 0; }
.send-btn:disabled { opacity: 0.4; }
.stop-btn { flex: 0 0 44px; width: 44px; height: 44px; border-radius: 22px;
            background: var(--surface-button); color: var(--text); display: flex;
            align-items: center; justify-content: center; padding: 0; }
.stop-btn:disabled { opacity: 0.4; }
.composer-error { margin: 0; font-size: 13px; line-height: 18px; color: var(--accent-problem); }
.send { gap: 4px; }
.send-line { display: flex; flex-wrap: wrap; align-items: center; gap: 0 4px;
             font-size: 13px; line-height: 18px; }
.send-state { color: var(--text-secondary); }
.send-state.tone-working { background: none; color: var(--accent-working); }
.send-state.tone-problem { background: none; color: var(--accent-problem); }
.send-state.tone-idle { background: none; color: var(--text-secondary); }
.send-act { font-size: 13px; padding: 0 4px; }
.talk-off { margin: 0; font-size: 14px; line-height: 20px; color: var(--text-secondary); }
.asking { background: var(--tile-needs); gap: 16px; }
.question { display: flex; flex-direction: column; gap: 8px; }
.question-text { margin: 0; font-size: 17px; line-height: 22px; font-weight: 600;
                 white-space: pre-wrap; overflow-wrap: break-word; }
.choice { height: auto; min-height: 48px; flex: none; width: 100%; flex-direction: column;
          align-items: flex-start; justify-content: center; gap: 2px; padding: 10px 14px;
          text-align: left; }
.choice.picked { background: var(--accent-primary); color: #FFFFFF; }
.choice-label { overflow-wrap: break-word; }
.choice-note { font-size: 13px; line-height: 18px; font-weight: 400;
               color: var(--text-secondary); overflow-wrap: break-word; }
.choice.picked .choice-note { color: #FFFFFF; }
.other-open, .other-send, .send-answer { flex: none; width: 100%; }
.answer-other { font: 16px/22px var(--font); color: var(--text); width: 100%;
                background: var(--surface-inset); border: 1px solid var(--border);
                border-radius: 12px; padding: 10px 12px; min-height: 44px; }
.answer-error { margin: 0; font-size: 13px; line-height: 18px; color: var(--accent-problem); }
.entry-row { flex-wrap: wrap; }
.entry-actions { display: flex; gap: 8px; align-items: center; margin-left: auto; }
.buttons.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.buttons.grid .btn { flex: none; }
.now-actions { flex-wrap: wrap; }
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
const TALK_INTERVAL = __TALK_INTERVAL__;
const DROP_AFTER = __DROP_AFTER__;
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
      "&label=" + encodeURIComponent(form.label) + "&prompt=" + encodeURIComponent(form.prompt) +
      (form.model ? "&model=" + encodeURIComponent(form.model) : "");
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
    up: [["path", {d: "M6 15l6-6 6 6"}]],
    down: [["path", {d: "M6 9l6 6 6-6"}]],
    copy: [["rect", {x: "9", y: "9", width: "11", height: "11", rx: "2"}],
           ["path", {d: "M5 15V6a2 2 0 0 1 2-2h9"}]],
    cross: [["path", {d: "M18 6L6 18"}], ["path", {d: "M6 6l12 12"}]],
    tick: [["path", {d: "M5 12l5 5 9-10"}]],
    send: [["path", {d: "M12 19V5"}], ["path", {d: "M6 11l6-6 6 6"}]],
    stop: [["rect", {x: "7", y: "7", width: "10", height: "10", rx: "2",
                     fill: "currentColor"}]],
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
  // A card of a row, or of the chain (ADR-027 increment 3): a chain card's
  // Details opens its project's Chain screen, and its buttons name the
  // chain action they confirm.
  function fullCard(h, on, card) {
    const open = () => card.chain ? on.chain(card.chain) : on.open(card.row);
    const parts = [
      h("div", {cls: "meta"}, [h("span", {cls: "project", text: card.project}),
                               h("span", {cls: "age", text: card.age})]),
      h("div", {cls: "card-title", text: card.title}),
    ];
    if (card.chips.length) parts.push(chips(h, card.chips));
    if (card.excerpt) parts.push(excerpt(h, WORDS.claude, card.excerpt, "excerpt-text clamp"));
    if (card.note) parts.push(h("div", {cls: "note", text: card.note}));
    if (card.tap) {
      return h("div", {cls: "card tap", attrs: {role: "button", tabindex: "0"},
                       on: {click: open}}, parts);
    }
    const buttons = [];
    const actions = card.actions || [];
    if (card.reply) {
      buttons.push(h("button", {cls: "btn primary", text: card.reply.text,
                                on: {click: () => on.reply(card.row)}}));
    }
    if (card.answer) {
      buttons.push(h("button", {cls: "btn primary", text: card.answer.text,
                                on: {click: () => on.answer(card.row)}}));
    }
    if (card.button) {
      const target = card.button.row || card.row;
      buttons.push(h("button", {cls: "btn primary", text: card.button.text,
                                on: {click: () => on.confirm(target)}}));
    }
    actions.forEach((action, n) => buttons.push(h("button", {
      cls: "btn " + (n === 0 ? "primary" : "secondary"), text: action.text,
      on: {click: () => on.confirm(action.row)}})));
    buttons.push(h("button", {
      cls: "btn secondary" + (card.button && !actions.length ? " narrow" : ""),
      text: WORDS.details, on: {click: open}}));
    parts.push(h("div", {cls: actions.length ? "buttons grid" : "buttons"}, buttons));
    return h("div", {cls: card.kind === "chain" ? "card chain-card" : "card"}, parts);
  }
  function compactCard(h, on, card) {
    return h("button", {cls: "card compact", on: {click: () => on.open(card.row)}}, [
      h("span", {cls: "compact-text"}, [
        h("span", {cls: "project", text: card.project}),
        h("span", {cls: "card-title", text: card.title}),
        card.line ? h("span", {cls: "note", text: card.line}) : null,
        card.excerpt ? h("span", {cls: "excerpt-line", text: card.excerpt}) : null]),
      icon(h, "right", 18, "2", "chevron")]);
  }
  // A session's own text (ADR-029 increment 1): an inset block under its
  // label, set as text and never as markup (ADR-009 D17).
  function excerpt(h, label, text, cls) {
    return h("div", {cls: "inset excerpt"}, [h("span", {cls: "excerpt-label", text: label}),
                                             h("p", {cls: cls, text: text})]);
  }
  // The detail's "Last message": the prompt, then Claude's text, folded
  // behind "Show all" past the panel's length until it is opened.
  function lastMessage(h, ui, on, d) {
    const id = "last:" + d.row;
    const folded = d.last.head && !ui.more[id];
    const nodes = [h("span", {cls: "section-label", text: WORDS.last_message})];
    if (d.last.you) nodes.push(excerpt(h, WORDS.you, d.last.you, "excerpt-text"));
    const block = excerpt(h, WORDS.claude, folded ? d.last.head : d.last.claude,
                          "excerpt-text");
    if (folded) {
      block.appendChild(h("button", {cls: "link show-all", text: WORDS.show_all,
                                     attrs: {"aria-expanded": "false"},
                                     on: {click: () => on.more(id)}}));
    }
    nodes.push(block);
    return nodes;
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
  // A project's row: its sessions, its chain line, which opens its Chain
  // screen, and New session… when the page may start one.
  function projects(h, on, view) {
    const rows = view.projects.map((project) => {
      const lines = [h("span", {cls: "row-title", text: project.name}),
                     h("span", {cls: "row-line", text: project.line})];
      if (project.chain) lines.push(h("span", {cls: "row-line chain-line", text: project.chain}));
      const chain = view.chain && view.chain.projects[project.id];
      const text = chain
        ? h("button", {cls: "row-text chain-open", on: {click: () => on.chain(project.id)}}, lines)
        : h("span", {cls: "row-text"}, lines);
      const pill = view.start ? h("button", {cls: "pill row-pill",
        attrs: {"aria-label": WORDS.new_session + " " + project.name},
        on: {click: () => on.newSession(project.id)}}, [h("span", {text: WORDS.new_session})])
        : null;
      return h("div", {cls: "list-row"}, [text, pill]);
    });
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
    if ((view.start || view.chain) && view.projects && view.projects.length) {
      nodes.push(projects(h, on, view));
    }
    if (view.push) nodes.push(pushSection(h, on, view.push, ui.push || {}));
    return nodes;
  }
  // This device's web push (ADR-027 increment 4): what the Mac offers, what
  // this browser can do (``local``, which only the browser knows), and the one
  // pill that changes it.
  function pushSection(h, on, push, local) {
    let line = push.line;
    let pill = null;
    if (push.available) {
      const here = push.on && local.subscribed;
      if (!local.supported) {
        line = local.homeScreen ? WORDS.push_home_screen : WORDS.push_unsupported;
      } else if (local.busy) {
        line = WORDS.push_busy;
      } else if (local.blocked) {
        line = WORDS.push_blocked;
      } else {
        line = local.error ? fill(WORDS.push_failed, {error: local.error})
                           : here ? push.line_on : WORDS.push_off;
        pill = here ? {text: WORDS.push_turn_off, act: on.pushOff}
                    : {text: WORDS.push_turn_on, act: on.pushOn};
      }
    }
    const row = h("div", {cls: "list-row"}, [
      h("span", {cls: "row-text"}, [h("span", {cls: "row-title", text: WORDS.push_title}),
                                    h("span", {cls: "row-line", text: line})]),
      pill ? h("button", {cls: "pill row-pill", on: {click: () => pill.act()}},
               [h("span", {text: pill.text})]) : null]);
    return h("div", {cls: "section push"}, [
      h("div", {cls: "section-head"},
        [h("span", {cls: "section-label", text: WORDS.notifications})]),
      h("div", {cls: "list"}, [row])]);
  }
  // A base64url key as the bytes PushManager.subscribe wants; no atob, so it
  // runs in every engine the suite drives.
  function keyBytes(text) {
    const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    const out = [];
    let value = 0;
    let bits = 0;
    for (const ch of String(text)) {
      const digit = alphabet.indexOf(ch);
      if (digit < 0) continue;
      value = ((value << 6) | digit) & 0xffffff;
      bits += 6;
      if (bits >= 8) {
        bits -= 8;
        out.push((value >> bits) & 255);
      }
    }
    return new Uint8Array(out);
  }
  function back(h, on, label) {
    return h("button", {cls: "back", on: {click: () => on.back()}},
             [icon(h, "left", 20, "2.2"), h("span", {text: label || WORDS.back})]);
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
    if (d.last) nodes.push(...lastMessage(h, ui, on, d));
    if (d.conversation) {
      nodes.push(h("button", {cls: "btn secondary tall talk-open", text: WORDS.open_conversation,
                              on: {click: () => on.conversation(d.row)}}));
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
          h("span", {cls: "row-line", text: row.line}),
          row.excerpt ? h("span", {cls: "excerpt-line", text: row.excerpt}) : null]),
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
  function listSection(h, label, rows) {
    return h("div", {cls: "section"}, [
      h("div", {cls: "section-head"}, [h("span", {cls: "section-label", text: label})]),
      h("div", {cls: "list"}, rows)]);
  }
  function confirmButton(h, on, cls, button) {
    return h("button", {cls: cls, text: button.text, on: {click: () => on.confirm(button.row)}});
  }
  // A project's Chain screen (ADR-027 increment 3): what the chain does, the
  // start it follows, the proposals and the queue, every button a confirm.
  function chainScreen(view, ui, h, on, now) {
    const c = view.chain.projects[ui.key];
    const nodes = [back(h, on), h("div", {cls: "detail-head"}, [
      h("span", {cls: "kicker", text: c.kicker}), h("h1", {cls: "detail-title", text: c.title}),
      chips(h, c.chips)])];
    const going = [h("span", {cls: "label", text: WORDS.going_on}),
                   h("p", {cls: "body", text: c.going_on.join(" ")})];
    if (c.control) {
      going.push(h("div", {cls: "buttons"}, [confirmButton(
        h, on, "btn chain-control " + (c.control.primary ? "primary" : "secondary"),
        c.control)]));
    }
    nodes.push(h("div", {cls: "card"}, going));
    if (c.now) {
      const parts = [h("span", {cls: "label", text: WORDS.chain_now}),
                     h("div", {cls: "step"}, [h("span", {cls: "step-name now-title",
                                                         text: c.now.title}),
                                              h("span", {cls: "note", text: c.now.line})])];
      if (c.now.actions.length) {
        parts.push(h("div", {cls: "buttons now-actions"}, c.now.actions.map((action, n) =>
          confirmButton(h, on, "btn " + (n === 0 ? "primary" : "secondary"), action))));
      }
      nodes.push(h("div", {cls: "card"}, parts));
    }
    if (c.proposed.length) {
      nodes.push(listSection(h, WORDS.chain_proposed, c.proposed.map((p) =>
        h("div", {cls: "list-row"}, [
          h("span", {cls: "row-text"}, [h("span", {cls: "row-title", text: p.title}),
                                        h("span", {cls: "row-line", text: p.line})]),
          p.button ? h("button", {cls: "pill row-pill", on: {click: () => on.confirm(p.button.row)}},
                       [h("span", {text: p.button.text})]) : null]))));
    }
    const rows = c.queue.map((row) => {
      const text = [h("span", {cls: "row-title", text: row.title}),
                    h("span", {cls: "row-line", text: row.line})];
      if (row.brief) text.push(h("span", {cls: "row-line row-brief", text: row.brief}));
      const controls = [];
      for (const [name, move] of [["up", row.up], ["down", row.down]]) {
        if (!move) continue;
        controls.push(h("button", {cls: "iconbtn entry-" + name, attrs: {"aria-label": move.label},
                                   on: {click: () => on.confirm(move.row)}},
                        [icon(h, name, 20)]));
      }
      if (row.remove) {
        controls.push(h("button", {cls: "pill row-pill", on: {click: () => on.confirm(row.remove.row)}},
                        [h("span", {text: row.remove.text})]));
      }
      return h("div", {cls: "list-row entry-row"}, [h("span", {cls: "row-text"}, text),
        controls.length ? h("div", {cls: "entry-actions"}, controls) : null]);
    });
    if (!rows.length && c.empty) {
      rows.push(h("div", {cls: "list-row"}, [h("span", {cls: "quiet", text: c.empty})]));
    }
    if (rows.length) nodes.push(listSection(h, WORDS.chain_queue, rows));
    if (c.recent.length) {
      nodes.push(h("span", {cls: "section-label", text: WORDS.recent}));
      nodes.push(h("div", {cls: "card"}, c.recent.map((event) => h("div", {cls: "recent-row"}, [
        h("span", {text: event.text}), h("span", {cls: "when", text: clock(event.at, now)})]))));
    }
    if (c.technical.lines.length) {
      nodes.push(disclosure(h, ui, on, "technical:chain:" + ui.key, WORDS.technical,
                            c.technical.lines.map((line) => h("div", {text: line}))));
    }
    return nodes;
  }
  // The Conversation screen (ADR-029 increment 2): a live window's turns,
  // oldest first, each the session's own text set as text, never markup.
  function turn(h, t) {
    if (t.role === "tool") return h("div", {cls: "mono scroll tool-line", text: t.text});
    const you = t.role === "user";
    const parts = [h("span", {cls: "excerpt-label", text: you ? WORDS.you : WORDS.claude}),
                   h("p", {cls: "turn-text", text: t.text})];
    if (t.cut) parts.push(h("span", {cls: "cut-line", text: WORDS.cut_here}));
    return h("div", {cls: you ? "card quiet-card turn" : "card turn"}, parts);
  }
  function notice(h, text) {
    return h("div", {cls: "card quiet-card"}, [h("p", {cls: "body", text: text})]);
  }
  // A send from this device (ADR-029 increment 3): its line, worded by its
  // state, and the one thing it lets the person do.
  function reasonText(reason, what) {
    const said = WORDS["talk_why_" + reason] || reason || "";
    const more = what ? WORDS["changed_" + what] : "";
    return more ? said + " " + more : said;
  }
  function sendLine(s, restarted) {
    if (restarted) return {text: WORDS.talk_dropped_boot, tone: "problem"};
    switch (s.state) {
      case "held": return {text: WORDS.talk_held, tone: "idle", action: WORDS.undo};
      case "due": return {text: WORDS.talk_due, tone: "idle"};
      case "queued": return {text: WORDS.talk_queued, tone: "working", action: WORDS.cancel};
      case "typed": return {text: WORDS.talk_typed, tone: "idle"};
      case "delivered": return {text: WORDS.talk_delivered, tone: "idle"};
      case "not_confirmed": return {text: WORDS.talk_not_confirmed, tone: "problem"};
      case "dropped":
        return {text: s.reason === "ring" ? WORDS.talk_dropped_ring
                      : s.reason === "revoked" ? fill(WORDS.talk_refused,
                                                      {reason: reasonText(s.reason)})
                      : WORDS.talk_dropped, tone: "problem"};
      default:
        return {text: fill(WORDS.talk_refused, {reason: reasonText(s.reason, s.what)}),
                tone: "problem"};
    }
  }
  function sendCard(h, on, s, text, restarted) {
    const line = sendLine(s, restarted);
    if (s.kind === "interrupt") text = WORDS.stop;
    const parts = [h("span", {cls: "send-state tone-" + line.tone, text: line.text})];
    if (line.action) {
      parts.push(h("span", {cls: "send-state", text: "·", attrs: {"aria-hidden": "true"}}));
      parts.push(h("button", {cls: "link send-act", text: line.action,
                              on: {click: () => on.undo(s.id)}}));
    }
    const row = h("div", {cls: "send-line"}, parts);
    if (!text || s.state === "delivered") return h("div", {cls: "send"}, [row]);
    return h("div", {cls: "card quiet-card turn send"}, [
      h("span", {cls: "excerpt-label", text: WORDS.you}),
      h("p", {cls: "turn-text", text: text}), row]);
  }
  function offText(data) {
    const offered = data.offered || {};
    if (!data.live || data.unreadable || offered.send || !offered.why) return null;
    return WORDS["talk_why_" + offered.why] && offered.why !== "reading_only"
      && offered.why !== "unreadable" && offered.why !== "gone"
      ? WORDS["talk_why_" + offered.why] : null;
  }
  function conversation(view, ui, h, on) {
    const talk = ui.talk;
    const data = talk.data;
    const head = data && data.head;
    const known = view.details[ui.key];
    const nodes = [back(h, on, head ? head.title : known ? known.title : null)];
    if (head) {
      const parts = [h("span", {cls: "kicker", text: head.kicker}),
                     h("h1", {cls: "talk-title", text: head.title})];
      if (head.chips.length) parts.push(chips(h, head.chips));
      nodes.push(h("div", {cls: "detail-head"}, parts));
    }
    if (!data) {
      if (talk.dropped) nodes.push(h("p", {cls: "quiet", text: WORDS.reading_again}));
      return nodes;
    }
    const list = [];
    if (!data.live) {
      list.push(notice(h, WORDS.window_gone));
    } else {
      if (data.unreadable) list.push(notice(h, WORDS.cant_read_session));
      for (const t of data.turns) list.push(turn(h, t));
    }
    for (const s of talk.sends || []) {
      list.push(sendCard(h, on, s, (talk.texts || {})[s.id], s.restarted));
    }
    const pending = data.live ? data.pending : null;
    if (pending && pending.unreadable) {
      list.push(notice(h, WORDS.cant_read_question));
    } else if (pending && pending.questions && (data.offered || {}).answer
               && !answering(talk)) {
      list.push(answerBlock(h, on, pending, askingFor(ui, pending)));
    }
    const off = offText(data);
    if (off) list.push(h("p", {cls: "talk-off", text: off}));
    nodes.push(h("div", {cls: "turns"}, list));
    nodes.push(h("button", {cls: "new-pill", text: WORDS.new_messages, hidden: !talk.unseen,
                            attrs: {id: "newpill"}, on: {click: () => on.toBottom()}}));
    return nodes;
  }
  // What Claude waits on (ADR-029 increment 5): a question's block, the last
  // of the screen. A lone single-select question sends on one tap; otherwise
  // each choice is a toggle and one "Send answer" sends them all. "Other…"
  // opens a one-line field. Its state lives in ui.asking, so a read every
  // two seconds keeps what the person chose; a new question starts afresh.
  function askingFor(ui, pending) {
    if (!ui.asking || ui.asking.id !== pending.id) {
      ui.asking = {id: pending.id, picks: pending.questions.map(() => []),
                   other: pending.questions.map(() => ""),
                   open: pending.questions.map(() => false), inputs: [], error: "",
                   busy: false, typing: false, stale: false};
    }
    return ui.asking;
  }
  function lone(pending) {
    return pending.questions.length === 1 && !pending.questions[0].multi;
  }
  function answerComplete(pending, state) {
    return pending.questions.every((q, n) => state.picks[n].length > 0
      || (state.open[n] && !!state.other[n].trim()));
  }
  function answering(talk) {
    return (talk.sends || []).some((s) => s.kind === "question" && !s.restarted
      && ["held", "due", "typed"].includes(s.state));
  }
  function answerSummary(pending, answers) {
    return answers.map((a, n) => {
      const q = pending.questions[n];
      const parts = a.choices.map((c) => q.choices[c].label);
      if (a.other !== null) parts.push(a.other);
      return parts.join(", ");
    }).join("\n");
  }
  function answerBlock(h, on, pending, state) {
    const one = lone(pending);
    const parts = [];
    state.inputs = [];
    pending.questions.forEach((q, qi) => {
      const items = [h("p", {cls: "question-text", text: q.text})];
      q.choices.forEach((c, ci) => {
        const picked = state.picks[qi].includes(ci);
        const kids = [h("span", {cls: "choice-label", text: c.label})];
        if (c.description) kids.push(h("span", {cls: "choice-note", text: c.description}));
        items.push(h("button", {
          cls: "btn secondary tall choice" + (picked ? " picked" : ""),
          attrs: one ? {} : {"aria-pressed": picked ? "true" : "false"},
          on: {click: () => on.pick(qi, ci)}}, kids));
      });
      if (q.other && state.open[qi]) {
        const input = h("input", {cls: "answer-other", value: state.other[qi], attrs: {
          type: "text", maxlength: "8000", "aria-label": WORDS.your_answer,
          placeholder: WORDS.other_placeholder},
          on: {input: (event, node) => on.otherInput(qi, node.value),
               focus: () => on.typing(true), blur: () => on.typing(false)}});
        state.inputs[qi] = input;
        items.push(input);
        if (one) {
          items.push(h("button", {cls: "btn primary tall other-send", text: WORDS.send,
                                  on: {click: () => on.sendOther()}}));
        }
      } else if (q.other) {
        items.push(h("button", {cls: "btn secondary tall other-open", text: WORDS.other,
                                on: {click: () => on.otherOpen(qi)}}));
      }
      parts.push(h("div", {cls: "question"}, items));
    });
    if (!one) {
      const send = h("button", {cls: "btn primary tall send-answer", text: WORDS.send_answer,
                                on: {click: () => on.sendAnswer()}});
      send.disabled = state.busy || !answerComplete(pending, state);
      parts.push(send);
    }
    if (state.error) parts.push(h("p", {cls: "answer-error", text: state.error}));
    return h("div", {cls: "card asking"}, parts);
  }
  // The screen ui says, or the overview when its session or project has gone.
  function screen(view, ui, h, on, now) {
    if (ui.screen === "conversation" && ui.talk) return conversation(view, ui, h, on);
    if (ui.screen === "detail" && view.details[ui.key]) return detail(view, ui, h, on, now);
    if (ui.screen === "leftovers") return leftovers(view, ui, h, on);
    if (ui.screen === "chain" && view.chain && view.chain.projects[ui.key]) {
      return chainScreen(view, ui, h, on, now);
    }
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
    const model = h("input", {attrs: {type: "text", autocomplete: "off", autocapitalize: "none",
                                      spellcheck: "false", "aria-label": WORDS.model_field,
                                      placeholder: WORDS.model_hint},
                              value: form.model || "", on: {input: edit("model")}});
    const built = {
      picker: picker, label: label, prompt: prompt, model: model,
      modelLine: h("div", {cls: "note start-model"}),
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
      field(h, WORDS.model_field, [model, built.modelLine]),
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
    built.modelLine.textContent = w.model_line || "";
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
          startBody: startBody, startSheet: startSheet, fillStart: fillStart,
          keyBytes: keyBytes, icon: icon, sendLine: sendLine, reasonText: reasonText,
          lone: lone, answerComplete: answerComplete, answerSummary: answerSummary};
})();

if (typeof document !== "undefined") {
  const byId = (id) => document.getElementById(id);
  const SVG = "http://www.w3.org/2000/svg";
  const SVG_TAGS = {svg: true, path: true, rect: true};
  // The open conversation (ADR-029 increment 2): its row, its last read, and
  // whether turns arrived below the person's view.
  const talk = {key: null, data: null, unseen: false, dropped: false, failures: 0,
                timer: null, reading: false, pending: null, sends: [], texts: {}};
  // This device's sends (ADR-029 increment 3): each one's text, in memory only
  // until it is delivered or gone, and the boot it was sent under.
  const sent = {};
  let serverBoot = null;
  const compose = {box: null, input: null, button: null, stop: null, error: null,
                   busy: false, focus: false};
  const ui = {screen: "overview", key: null, open: {}, more: {}, scroll: {}, talk: talk,
              push: {supported: false, homeScreen: false, subscribed: false, busy: false,
                     error: "", blocked: false}};
  let current = null;
  // Every session's text dropped after a while in the background (ADR-009 D17).
  let dropped = false;
  let hiddenAt = null;
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
    if (ui.asking && ui.asking.typing) {
      // The person is typing an "Other" answer: a redraw would take the field
      // from under them. It is drawn when they leave it.
      ui.asking.stale = true;
      return;
    }
    if (dropped) {
      byId("screen").replaceChildren(h("p", {cls: "quiet", text: WORDS.reading_again}));
      return;
    }
    if (!current) return;
    const y = window.scrollY;
    const nodes = ORACLE.screen(current.view, ui, h, on, Date.now());
    byId("screen").replaceChildren(...nodes);
    const composing = showComposer();
    byId("screen").className = (ui.screen === "overview" ? "" : "sub")
      + (composing ? " talking" : "");
    byId("top").className = ui.screen === "overview" ? "" : "sub";
    window.scrollTo(0, y);
  }

  // -- the composer (ADR-029 increment 3): built once, outside the redrawn
  // screen, so a read every two seconds never takes the person's draft.
  function buildComposer() {
    if (compose.box) return;
    compose.input = h("textarea", {cls: "composer-input", value: "", attrs: {
      rows: "1", maxlength: "8000", "aria-label": WORDS.message_claude,
      placeholder: WORDS.message_claude}});
    compose.button = h("button", {cls: "send-btn", attrs: {"aria-label": WORDS.send},
                                  on: {click: () => sendMessage()}},
                       [ORACLE.icon(h, "send", 22, "2.2")]);
    // Stop (ADR-029 increment 4): a secondary round button beside Send while a
    // turn runs; it stays when send is off, as the one way to stop the turn.
    compose.stop = h("button", {cls: "stop-btn", attrs: {"aria-label": WORDS.stop},
                                on: {click: () => stopTurn()}},
                     [ORACLE.icon(h, "stop", 22, "2")]);
    compose.error = h("p", {cls: "composer-error", hidden: true});
    compose.box = byId("composer");
    compose.box.replaceChildren(compose.error, h("div", {cls: "composer-row"},
                                                 [compose.input, compose.stop, compose.button]));
  }
  function showComposer() {
    const box = byId("composer");
    const data = talk.data;
    const offered = (data && data.offered) || {};
    const shown = !dropped && ui.screen === "conversation" && !!data && !!data.live
      && (!!offered.send || !!offered.interrupt);
    box.hidden = !shown;
    if (!shown) return false;
    buildComposer();
    compose.input.hidden = !offered.send;
    compose.button.hidden = !offered.send;
    compose.stop.hidden = !offered.interrupt;
    compose.stop.disabled = compose.busy;
    compose.input.setAttribute("placeholder", data.offered.running ? WORDS.message_turn_end
                                                                   : WORDS.message_claude);
    compose.button.disabled = compose.busy;
    if (compose.focus) {
      compose.focus = false;
      compose.input.focus();
    }
    return true;
  }
  function composeError(text) {
    if (!compose.error) return;
    compose.error.textContent = text || "";
    compose.error.hidden = !text;
  }
  // The sends shown: the server's list for this row, with the text this page
  // still holds; one from an older boot of the server is dropped (D7).
  function mergeSends() {
    const listed = (talk.data && talk.data.sends) || [];
    const seen = {};
    const shown = [];
    for (const s of listed) {
      seen[s.id] = true;
      shown.push(s);
    }
    for (const id of Object.keys(sent)) {
      const mine = sent[id];
      if (seen[id] || mine.row !== talk.key || mine.boot === serverBoot) continue;
      if (!mine.live && !mine.restarted) continue;
      mine.restarted = true;
      shown.push({id: id, state: "dropped", reason: "restart", restarted: true});
    }
    talk.sends = shown;
    talk.texts = {};
    for (const s of shown) {
      const mine = sent[s.id];
      if (!mine) continue;
      mine.live = !s.restarted && ["held", "queued", "due", "typed"].includes(s.state);
      if (mine.text) talk.texts[s.id] = mine.text;
    }
  }
  function noteBoot(boot) {
    if (boot) serverBoot = boot;
  }
  async function talkPost(url, body) {
    const response = await fetch(url, {method: "POST", credentials: "same-origin",
                                       redirect: "manual",
                                       headers: {"Content-Type": "application/json"},
                                       body: JSON.stringify(body)});
    if (ORACLE.moved(response)) {
      location.reload();
      throw new Error(WORDS.moved);
    }
    let answer = {};
    try { answer = await response.json(); } catch (e) {}
    return {status: response.status, body: answer};
  }
  async function sendMessage() {
    if (!compose.input || compose.busy || talk.key === null) return;
    const text = compose.input.value;
    if (!text.trim()) return;
    compose.busy = true;
    composeError("");
    draw();
    if (talk.pending) await talk.pending;
    const key = talk.key;
    try {
      const nonce = (talk.data && talk.data.nonce) || "";
      const answer = await talkPost("/api/talk", {row: key, nonce: nonce, kind: "message",
                                                  text: text});
      if (answer.status === 202 && answer.body.send) {
        noteBoot(answer.body.boot);
        sent[answer.body.send.id] = {text: text, boot: answer.body.boot, row: key,
                                     live: true};
        compose.input.value = "";
        if (talk.data) {
          talk.data.nonce = null;
          talk.data.sends = [...(talk.data.sends || []), answer.body.send];
        }
      } else {
        const why = answer.body.reason && WORDS["talk_why_" + answer.body.reason]
          ? ORACLE.reasonText(answer.body.reason) : (answer.body.error || "HTTP " + answer.status);
        composeError(ORACLE.fill(WORDS.talk_refused, {reason: why}));
      }
    } catch (err) {
      composeError(ORACLE.fill(WORDS.talk_unreached, {error: err.message}));
    } finally {
      compose.busy = false;
      mergeSends();
      draw();
      wakeTalk();
    }
  }
  // Stop (ADR-029 increment 4): an interrupt, one tap with the same Undo; the
  // draft in the composer is the person's and stays as it is.
  async function stopTurn() {
    if (compose.busy || talk.key === null) return;
    compose.busy = true;
    composeError("");
    draw();
    if (talk.pending) await talk.pending;
    const key = talk.key;
    try {
      const nonce = (talk.data && talk.data.nonce) || "";
      const answer = await talkPost("/api/talk", {row: key, nonce: nonce, kind: "interrupt"});
      if (answer.status === 202 && answer.body.send) {
        noteBoot(answer.body.boot);
        sent[answer.body.send.id] = {text: "", boot: answer.body.boot, row: key, live: true};
        if (talk.data) {
          talk.data.nonce = null;
          talk.data.sends = [...(talk.data.sends || []), answer.body.send];
        }
      } else {
        const why = answer.body.reason && WORDS["talk_why_" + answer.body.reason]
          ? ORACLE.reasonText(answer.body.reason) : (answer.body.error || "HTTP " + answer.status);
        composeError(ORACLE.fill(WORDS.talk_refused, {reason: why}));
      }
    } catch (err) {
      composeError(ORACLE.fill(WORDS.talk_unreached, {error: err.message}));
    } finally {
      compose.busy = false;
      mergeSends();
      draw();
      wakeTalk();
    }
  }
  async function undoSend(id) {
    try {
      const answer = await talkPost("/api/talk/cancel", {id: id});
      if (answer.status === 200) {
        const mine = sent[id];
        if (mine && mine.kind !== "question" && compose.input && !compose.input.value) {
          compose.input.value = mine.text || "";
        }
        delete sent[id];
        if (talk.data) talk.data.sends = (talk.data.sends || []).filter((s) => s.id !== id);
        composeError("");
      } else {
        composeError(ORACLE.fill(WORDS.talk_refused, {reason: answer.body.error || ""}));
      }
    } catch (err) {
      composeError(ORACLE.fill(WORDS.talk_unreached, {error: err.message}));
    }
    mergeSends();
    draw();
    wakeTalk();
  }

  // A question's answer (ADR-029 increment 5): one tap with the same Undo;
  // the page keeps what was chosen until the answer is delivered or gone.
  async function postAnswer(answers) {
    const state = ui.asking;
    const pending = talk.data && talk.data.pending;
    if (!state || !pending || state.busy || talk.key === null) return;
    state.busy = true;
    state.error = "";
    draw();
    if (talk.pending) await talk.pending;
    const key = talk.key;
    try {
      const nonce = (talk.data && talk.data.nonce) || "";
      const answer = await talkPost("/api/talk", {row: key, nonce: nonce, kind: "question",
                                                  answers: answers});
      if (answer.status === 202 && answer.body.send) {
        noteBoot(answer.body.boot);
        sent[answer.body.send.id] = {text: ORACLE.answerSummary(pending, answers),
                                     boot: answer.body.boot, row: key, live: true,
                                     kind: "question"};
        if (talk.data) {
          talk.data.nonce = null;
          talk.data.sends = [...(talk.data.sends || []), answer.body.send];
        }
      } else {
        const why = answer.body.reason && WORDS["talk_why_" + answer.body.reason]
          ? ORACLE.reasonText(answer.body.reason) : (answer.body.error || "HTTP " + answer.status);
        state.error = ORACLE.fill(WORDS.talk_refused, {reason: why});
      }
    } catch (err) {
      state.error = ORACLE.fill(WORDS.talk_unreached, {error: err.message});
    } finally {
      state.busy = false;
      mergeSends();
      draw();
      wakeTalk();
    }
  }
  function pickAnswer(qi, ci) {
    const pending = talk.data && talk.data.pending;
    const state = ui.asking;
    if (!pending || !state) return;
    if (ORACLE.lone(pending)) {
      postAnswer([{choices: [ci], other: null}]);
      return;
    }
    const picks = state.picks[qi];
    if (pending.questions[qi].multi) {
      state.picks[qi] = picks.includes(ci) ? picks.filter((c) => c !== ci) : [...picks, ci];
    } else {
      state.picks[qi] = [ci];
      state.open[qi] = false;
    }
    draw();
  }
  function openOther(qi) {
    const pending = talk.data && talk.data.pending;
    const state = ui.asking;
    if (!pending || !state) return;
    state.open[qi] = true;
    if (!pending.questions[qi].multi) state.picks[qi] = [];
    draw();
    if (state.inputs[qi]) state.inputs[qi].focus();
  }
  function sendAnswers() {
    const pending = talk.data && talk.data.pending;
    const state = ui.asking;
    if (!pending || !state || !ORACLE.answerComplete(pending, state)) return;
    postAnswer(pending.questions.map((q, n) => ({
      choices: [...state.picks[n]].sort((a, b) => a - b),
      other: state.open[n] && state.other[n].trim() ? state.other[n] : null})));
  }

  function go(screen, key, push) {
    ui.scroll[ui.screen] = window.scrollY;
    ui.screen = screen;
    ui.key = key || null;
    ui.more = {};
    if (push) history.pushState({screen: screen, key: ui.key}, "");
    if (screen !== "conversation") stopTalk();
    else if (talk.key !== ui.key) openTalk(ui.key);
    draw();
    window.scrollTo(0, screen === "overview" ? (ui.scroll.overview || 0) : 0);
    if (screen === "conversation" && talk.timer === null && !talk.reading) readTalk();
  }

  // -- the conversation: read on demand, every 2 s while open and seen ------------------
  function stopTalk() {
    if (talk.timer !== null) clearTimeout(talk.timer);
    talk.timer = null;
    talk.key = null;
    talk.data = null;
    talk.sends = [];
    talk.texts = {};
    composeError("");
    talk.unseen = false;
    talk.dropped = false;
    talk.failures = 0;
  }
  function openTalk(key) {
    stopTalk();
    ui.asking = null;
    talk.key = key;
  }
  function atBottom() {
    return window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 48;
  }
  function toBottom() {
    talk.unseen = false;
    const pill = byId("newpill");
    if (pill) pill.hidden = true;
    window.scrollTo(0, document.documentElement.scrollHeight);
  }
  function showTalk(data) {
    const changed = !talk.data || data.cursor !== talk.data.cursor;
    const pinned = !talk.data || atBottom();
    talk.data = data;
    talk.dropped = false;
    mergeSends();
    if (changed && !pinned) talk.unseen = true;
    draw();
    if (changed && pinned) toBottom();
  }
  function readTalk() {
    const reading = readTalkNow();
    talk.pending = reading;
    return reading;
  }
  async function readTalkNow() {
    talk.timer = null;
    const key = talk.key;
    if (ui.screen !== "conversation" || key === null || talk.reading) return;
    if (document.visibilityState !== "visible") return;
    talk.reading = true;
    let url = "/api/session?row=" + encodeURIComponent(key);
    if (talk.data && talk.data.cursor) url += "&after=" + encodeURIComponent(talk.data.cursor);
    try {
      const response = await fetch(url, {cache: "no-store", credentials: "same-origin",
                                         redirect: "manual"});
      if (ORACLE.moved(response)) {
        location.reload();
        return;
      }
      if (response.status === 404) {
        if (key === talk.key) {
          showTalk({live: false, turns: [], unreadable: false, cursor: "gone",
                    head: talk.data ? talk.data.head : null});
        }
      } else if (!response.ok) {
        throw new Error("HTTP " + response.status);
      } else {
        const data = await response.json();
        noteBoot(data.boot);
        if (key === talk.key) {
          showTalk(data.unchanged ? Object.assign({}, talk.data, data) : data);
        }
      }
      talk.failures = 0;
    } catch (err) {
      talk.failures += 1;
    } finally {
      talk.reading = false;
      if (key === talk.key && ui.screen === "conversation") {
        talk.timer = setTimeout(readTalk,
                                ORACLE.retryDelay(TALK_INTERVAL, talk.failures) * 1000);
      }
    }
  }
  function wakeTalk() {
    if (ui.screen !== "conversation" || talk.key === null) return;
    if (talk.timer !== null) clearTimeout(talk.timer);
    talk.timer = null;
    readTalk();
  }
  // Hidden past DROP_AFTER: the state read and the conversation go, with
  // every session's text in them; only how fresh the last read was stays.
  function dropText() {
    current = null;
    const state = loop.state;
    state.data = state.data ? {freshness: state.data.freshness} : null;
    talk.data = null;
    talk.unseen = false;
    talk.dropped = true;
    talk.sends = [];
    talk.texts = {};
    for (const id of Object.keys(sent)) sent[id].text = null;
    dropped = true;
    draw();
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
    startForm = {project: project || start.projects[0].id, label: "", prompt: "", model: ""};
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
    startForm.model = built.model.value;
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

  // Web push (ADR-027 increment 4). The worker is registered once the Mac
  // offers push, and this browser's own subscription is read again on every
  // refresh, since only the browser knows it.
  let pushReg = null;
  let pushAsked = false;

  function pushPrepare(data) {
    const offered = data.view.push;
    if (!offered || !offered.available) return;
    const local = ui.push;
    local.supported = !!(window.isSecureContext && navigator.serviceWorker && window.PushManager);
    local.homeScreen = !local.supported && navigator.standalone === false;
    if (!local.supported) return;
    if (pushReg) {
      pushReg.pushManager.getSubscription().then((sub) => {
        if (!!sub !== local.subscribed) { local.subscribed = !!sub; draw(); }
      }, () => {});
      return;
    }
    if (pushAsked) return;
    pushAsked = true;
    (async () => {
      try {
        pushReg = await navigator.serviceWorker.register("/sw.js");
        local.subscribed = !!(await pushReg.pushManager.getSubscription());
        if (typeof Notification !== "undefined" && Notification.permission === "denied") {
          local.blocked = true;
        }
      } catch (err) {
        local.error = err && err.message ? err.message : String(err);
      }
      draw();
    })();
  }

  async function pushPost(url, body) {
    const response = await fetch(url, {
      method: "POST", cache: "no-store", credentials: "same-origin",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
    });
    if (response.ok) return;
    let error = "HTTP " + response.status;
    try { error = (await response.json()).error || error; } catch (e) {}
    throw new Error(error);
  }

  // The tap: subscribe first, while the tap still counts as the person's, so
  // the browser asks its permission; then hand the subscription to the Mac.
  async function pushOn() {
    const offered = current && current.view.push;
    if (!pushReg || !offered || !offered.key) return;
    const local = ui.push;
    const options = {userVisibleOnly: true, applicationServerKey: ORACLE.keyBytes(offered.key)};
    local.busy = true;
    local.error = "";
    local.blocked = false;
    draw();
    try {
      let sub;
      try {
        sub = await pushReg.pushManager.subscribe(options);
      } catch (err) {
        if (!err || err.name !== "InvalidStateError") throw err;
        const old = await pushReg.pushManager.getSubscription();
        if (old) await old.unsubscribe();
        sub = await pushReg.pushManager.subscribe(options);
      }
      await pushPost("/api/push/subscribe", sub);
      local.subscribed = true;
    } catch (err) {
      if (err && err.name === "NotAllowedError") local.blocked = true;
      else local.error = err && err.message ? err.message : String(err);
    }
    local.busy = false;
    draw();
    loop.wake();
  }

  async function pushOff() {
    const local = ui.push;
    local.busy = true;
    local.error = "";
    draw();
    try {
      const sub = pushReg ? await pushReg.pushManager.getSubscription() : null;
      const endpoint = sub ? sub.endpoint : null;
      if (sub) await sub.unsubscribe();
      await pushPost("/api/push/unsubscribe", {endpoint: endpoint});
      local.subscribed = false;
    } catch (err) {
      local.error = err && err.message ? err.message : String(err);
    }
    local.busy = false;
    draw();
    loop.wake();
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
    chain: (key) => go("chain", key, true),
    conversation: (key) => go("conversation", key, true),
    reply: (key) => { compose.focus = true; go("conversation", key, true); },
    answer: (key) => go("conversation", key, true),
    pick: pickAnswer,
    otherOpen: openOther,
    otherInput: (qi, value) => { if (ui.asking) ui.asking.other[qi] = value; },
    sendOther: () => {
      const state = ui.asking;
      if (state && state.other[0].trim()) postAnswer([{choices: [], other: state.other[0]}]);
    },
    sendAnswer: sendAnswers,
    typing: (on) => {
      if (!ui.asking) return;
      ui.asking.typing = on;
      if (!on && ui.asking.stale) {
        ui.asking.stale = false;
        draw();
      }
    },
    undo: (id) => undoSend(id),
    toBottom: toBottom,
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
    pushOn: pushOn,
    pushOff: pushOff,
  };

  function render(data) {
    current = data;
    dropped = false;
    noteBoot(data.boot);
    pushPrepare(data);
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
  let dropTimer = null;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      if (dropTimer !== null) clearTimeout(dropTimer);
      dropTimer = null;
      if (hiddenAt !== null && Date.now() - hiddenAt >= DROP_AFTER * 1000) dropText();
      hiddenAt = null;
      loop.wake();
      wakeTalk();
      return;
    }
    hiddenAt = Date.now();
    dropTimer = setTimeout(() => { dropTimer = null; dropText(); }, DROP_AFTER * 1000);
  });
  window.addEventListener("scroll", () => {
    if (ui.screen === "conversation" && talk.unseen && atBottom()) toBottom();
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


#: Where the service worker is served: the root, so its scope is the whole page.
WORKER_PATH = "/sw.js"

#: The service worker (ADR-027 increment 4). It reads nothing and fetches
#: nothing: a push carries the banner's own text, sealed for this browser,
#: and the worker shows it; a tap focuses the open panel, else opens it.
_WORKER = """// The oracle's service worker: shows a push, opens the panel on a tap.
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = (event.data && event.data.json()) || {};
  } catch (err) {
    data = {};
  }
  const body = [data.subtitle, data.message].filter((part) => part).join("\\n");
  event.waitUntil(self.registration.showNotification(data.title || "invariant", {
    body: body, silent: data.silent === true, icon: "/icon-192.png", data: {url: "/"}}));
});
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true})
    .then((windows) => {
      for (const client of windows) if ("focus" in client) return client.focus();
      return self.clients.openWindow("/");
    }));
});
"""


def service_worker() -> str:
    """The service worker's script, served at :data:`WORKER_PATH` to a paired device."""
    return _WORKER


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
        '<div id="composer" hidden></div>'
        '<div id="scrim" hidden></div>'
        '<div id="sheet" role="dialog" aria-modal="true" hidden></div>'
    )
    script = (
        _SCRIPT.replace("__MAX_BACKOFF__", f"{MAX_BACKOFF:g}")
        .replace("__STALE_INTERVALS__", str(STALE_INTERVALS))
        .replace("__INTERVAL__", f"{interval:g}")
        .replace("__TALK_INTERVAL__", f"{CONVERSATION_INTERVAL:g}")
        .replace("__DROP_AFTER__", f"{BACKGROUND_DROP:g}")
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
