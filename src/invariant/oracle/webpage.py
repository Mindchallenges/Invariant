"""The HTML the oracle's web server sends: one page and three small ones.

Everything is inline, so the page needs nothing but the Mac it came from: no
CDN, no font, no image. The script and the style carry the response's nonce,
which the Content-Security-Policy ``oracle/web`` sends is the only thing it
lets run. The script builds every element with ``textContent``, never
``innerHTML``, so a row's text is never read as markup.

**It always refreshes** (ADR-008 D6). The script fetches ``/api/state`` on a
timer, not with a meta refresh, so the scroll position and an opened summary
survive. A failed fetch backs off — the interval doubled per failure, capped
at :data:`MAX_BACKOFF` — and the loop never stops. A line at the top says
``updated N s ago`` and turns red once the rows are older than three
intervals, the watcher's own staleness rule, which a stalled refresh reaches
by itself.

The loop's logic is the ``ORACLE`` object, which touches no page, so the
suite drives it under ``node``.
"""

from __future__ import annotations

from html import escape

from .watcher import DEFAULT_INTERVAL, STALE_INTERVALS

#: The longest wait between two refreshes after failures, in seconds.
MAX_BACKOFF = 60.0

_STYLE = """
:root { color-scheme: light dark; --red: #d1242f; --amber: #bf6c00; --dim: #8b8b8b;
        --line: rgba(127,127,127,.25); }
body { font: 15px/1.4 -apple-system, system-ui, sans-serif; margin: 0; padding: 0 12px 32px; }
header { position: sticky; top: 0; padding: 10px 0 8px; backdrop-filter: blur(8px);
         background: Canvas; border-bottom: 1px solid var(--line); }
h1 { font-size: 17px; margin: 0; }
h2 { font-size: 15px; margin: 18px 0 6px; }
#count { font-weight: 600; }
#fresh { font-size: 13px; color: var(--dim); }
#fresh.stale { color: var(--red); font-weight: 600; }
#banner { background: rgba(191,108,0,.15); padding: 6px 8px; border-radius: 6px; margin-top: 6px;
          font-size: 13px; }
.line { border-left: 4px solid var(--line); padding: 6px 8px; margin: 6px 0;
        border-radius: 4px; background: rgba(127,127,127,.06); overflow-wrap: anywhere; }
.line.nested { margin-left: 16px; font-size: 13px; color: var(--dim); }
.head { display: flex; justify-content: space-between; gap: 8px; }
.subject { font-weight: 600; }
.flag { font-size: 12px; }
.stage { font-size: 13px; color: var(--dim); }
.signal { font-size: 13px; }
.next { margin-top: 4px; font-size: 13px; }
.next code { display: block; white-space: pre-wrap; font-size: 12px; }
.where, .detail { color: var(--dim); font-size: 12px; }
.tone-red { border-left-color: var(--red); }
.tone-red .subject, .tone-red .flag, .signal.tone-red { color: var(--red); }
.tone-amber { border-left-color: var(--amber); }
.signal.tone-amber, .tone-amber .flag { color: var(--amber); }
.signal.tone-dim { color: var(--dim); }
details.noise summary { color: var(--dim); font-size: 13px; padding: 4px 0; cursor: pointer; }
.quiet { color: var(--dim); font-size: 13px; }
form { display: grid; gap: 12px; max-width: 360px; margin-top: 16px; }
input, button { font: inherit; padding: 10px; }
input { text-transform: uppercase; letter-spacing: .1em; }
"""

_SCRIPT = """
"use strict";
const MAX_BACKOFF = {max_backoff};
const STALE_INTERVALS = {stale_intervals};
const DEFAULT_INTERVAL = {interval};

const ORACLE = (function () {{
  function retryDelay(interval, failures) {{
    if (failures <= 0) return interval;
    return Math.min(interval * Math.pow(2, failures), MAX_BACKOFF);
  }}
  function ageOf(state, now) {{
    if (!state.data || state.fetchedAt === null) return null;
    return state.data.freshness.age + Math.max(0, (now - state.fetchedAt) / 1000);
  }}
  function freshnessText(age, staleAfter) {{
    if (age === null) return {{text: "never updated: no read has succeeded yet", stale: true}};
    const stale = age > staleAfter;
    let text = "updated " + Math.round(age) + "s ago";
    if (stale) text += "; STALE: older than " + STALE_INTERVALS + " intervals";
    return {{text: text, stale: stale}};
  }}
  function makeLoop(opts) {{
    const state = {{failures: 0, data: null, fetchedAt: null}};
    async function tick() {{
      try {{
        const data = await opts.fetchState();
        state.data = data;
        state.fetchedAt = opts.now();
        state.failures = 0;
        opts.onData(data);
      }} catch (err) {{
        state.failures += 1;
        opts.onError(err, state.failures);
      }} finally {{
        const interval = state.data ? state.data.freshness.interval : opts.interval;
        opts.schedule(tick, retryDelay(interval, state.failures) * 1000);
      }}
    }}
    return {{tick: tick, state: state}};
  }}
  return {{retryDelay: retryDelay, ageOf: ageOf, freshnessText: freshnessText,
          makeLoop: makeLoop}};
}})();

if (typeof document !== "undefined") {{
  const byId = (id) => document.getElementById(id);
  const openNoise = new Set();
  let lastError = "";

  function el(tag, cls, text) {{
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }}

  function renderLine(line) {{
    const card = el("div", "line tone-" + (line.tone || "plain") + (line.nested ? " nested" : ""));
    const head = el("div", "head");
    head.appendChild(el("span", "subject", line.subject));
    head.appendChild(el("span", "flag", line.status));
    card.appendChild(head);
    if (line.stage) card.appendChild(el("div", "stage", line.stage));
    for (const signal of line.signals) {{
      card.appendChild(el("div", "signal tone-" + (signal.tone || "plain"), signal.text));
    }}
    if (line.label || line.next) {{
      const next = el("div", "next");
      if (line.label) next.appendChild(el("div", "label", "next: " + line.label));
      if (line.next && line.next !== line.label) next.appendChild(el("code", null, line.next));
      if (line.where) next.appendChild(el("div", "where", "in " + line.where));
      if (line.detail) next.appendChild(el("div", "detail", line.detail));
      card.appendChild(next);
    }}
    return card;
  }}

  function render(data) {{
    const view = data.view;
    const fragment = document.createDocumentFragment();
    for (const project of view.projects) {{
      const section = el("section", "project");
      section.appendChild(el("h2", null, project.name));
      if (!project.lines.length && !project.noise.lines.length) {{
        section.appendChild(el("div", "quiet", "nothing live"));
      }}
      for (const line of project.lines) section.appendChild(renderLine(line));
      if (project.noise.lines.length) {{
        const details = el("details", "noise");
        details.open = openNoise.has(project.name);
        details.addEventListener("toggle", () => {{
          if (details.open) openNoise.add(project.name); else openNoise.delete(project.name);
        }});
        details.appendChild(el("summary", null, project.noise.summary + ", no alert"));
        for (const line of project.noise.lines) details.appendChild(renderLine(line));
        section.appendChild(details);
      }}
      fragment.appendChild(section);
    }}
    if (!view.projects.length) fragment.appendChild(el("div", "quiet", "no projects registered"));
    byId("projects").replaceChildren(fragment);
    byId("count").textContent = view.alerts ? view.alerts + " need you" : "nothing needs you";
    const banner = byId("banner");
    banner.textContent = data.freshness.banner || "";
    banner.hidden = !data.freshness.banner;
    document.title = (view.alerts ? "(" + view.alerts + ") " : "") + "invariant oracle";
  }}

  const loop = ORACLE.makeLoop({{
    interval: DEFAULT_INTERVAL,
    now: () => Date.now(),
    fetchState: async () => {{
      const response = await fetch("/api/state", {{cache: "no-store", credentials: "same-origin"}});
      if (response.status === 401) throw new Error("this device is not paired any more");
      if (!response.ok) {{
        let message = "HTTP " + response.status;
        try {{ message = (await response.json()).error || message; }} catch (e) {{}}
        throw new Error(message);
      }}
      return response.json();
    }},
    schedule: (fn, ms) => setTimeout(fn, ms),
    onData: (data) => {{ lastError = ""; render(data); showAge(); }},
    onError: (err, failures) => {{
      lastError = "refresh failed (" + failures + " in a row: " + err.message + "); retrying";
      showAge();
    }},
  }});

  function showAge() {{
    const data = loop.state.data;
    const staleAfter = data ? data.freshness.stale_after : STALE_INTERVALS * DEFAULT_INTERVAL;
    const said = ORACLE.freshnessText(ORACLE.ageOf(loop.state, Date.now()), staleAfter);
    const fresh = byId("fresh");
    fresh.textContent = said.text + (lastError ? " · " + lastError : "");
    fresh.className = said.stale || lastError ? "stale" : "";
  }}

  setInterval(showAge, 1000);
  loop.tick();
}}
"""


def _document(nonce: str, title: str, body: str, script: str = "") -> str:
    tail = f'<script nonce="{nonce}">{script}</script>' if script else ""
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title>"
        f'<style nonce="{nonce}">{_STYLE}</style></head>'
        f"<body>{body}{tail}</body></html>\n"
    )


def page(nonce: str, interval: float = DEFAULT_INTERVAL) -> str:
    """The dashboard: a header with its freshness line, then the projects."""
    body = (
        "<header><h1>invariant oracle · <span id=\"count\">loading</span></h1>"
        "<div id=\"fresh\">never updated: no read has succeeded yet</div>"
        "<div id=\"banner\" hidden></div></header>"
        "<main id=\"projects\"></main>"
    )
    script = _SCRIPT.format(
        max_backoff=f"{MAX_BACKOFF:g}",
        stale_intervals=STALE_INTERVALS,
        interval=f"{interval:g}",
    )
    return _document(nonce, "invariant oracle", body, script)


def message(nonce: str, title: str, text: str) -> str:
    return _document(nonce, title, f"<h1>{escape(title)}</h1><p>{escape(text)}</p>")


def not_paired(nonce: str) -> str:
    """All an unpaired device ever sees: no rows, no project, no name."""
    return message(
        nonce,
        "not paired",
        "This device is not paired with the oracle. On the Mac, run "
        "'invariant oracle web pair' and open the link it prints here.",
    )


def pair_form(nonce: str, code: str) -> str:
    """The form a pairing link opens. Opening it spends nothing; the button does."""
    body = (
        "<h1>Pair this device</h1>"
        "<p>The code was printed by 'invariant oracle web pair' on the Mac. "
        "It works once, for a few minutes.</p>"
        '<form method="post" action="/pair">'
        '<label>Pairing code <input name="code" required autocomplete="one-time-code" '
        f'autocapitalize="characters" value="{escape(code, quote=True)}"></label>'
        '<button type="submit">Pair this device</button></form>'
    )
    return _document(nonce, "pair this device", body)
