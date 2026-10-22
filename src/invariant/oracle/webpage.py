"""The HTML the oracle's web server sends: one page and three small ones.

Everything is inline, so the page needs nothing but the Mac it came from: no
CDN, no font, no image. The script and the style carry the response's nonce,
which the Content-Security-Policy ``oracle/web`` sends is the only thing it
lets run. The script builds every element with ``textContent``, never
``innerHTML``, so a row's text is never read as markup.

**It always refreshes** (ADR-008 D6). The script fetches ``/api/state`` on a
timer, not with a meta refresh, so the scroll position and an opened summary
survive. A failed fetch backs off — the interval doubled per failure, capped
at :data:`MAX_BACKOFF` — and the loop never stops. When the page is looked
at again (it becomes visible, regains focus, is restored, or the network
comes back) a backoff is cut short and it reads at once, so a server that
restarted is seen as soon as anyone looks. A line at the top says
``updated N s ago`` and turns red once the rows are older than three
intervals, the watcher's own staleness rule, which a stalled refresh reaches
by itself.

**It acts only on a second, explicit confirm** (ADR-008 D8–D12). A row whose
step is ``run`` or ``terminal`` has a button. It fetches ``/api/confirm`` and
shows what the server says would run, word for word as the TUI's confirm, with
Cancel focused; only the second button posts ``/api/act``, and that carries
the row key and the confirm's nonce and nothing else. The result is polled
from ``/api/job`` while the refresh loop goes on, and the device's recent
actions come back with every refresh.

**It follows the page to HTTPS** (ADR-008 D23). The state fetch does not
follow a redirect. Once the Mac serves HTTPS, a page still open over plain
HTTP is answered with a redirect to the https name, which the fetch sees as an
opaque redirect; the page then reloads itself, and the browser follows the
redirect to the page at its https name. A tailnet page served over plain HTTP
says so at the top (``warning``), and so does the pairing form.

The loop's logic and the small action helpers are the ``ORACLE`` object, which
touches no page, so the suite drives it under ``node``.
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
#insecure { background: rgba(209,36,47,.15); color: var(--red); font-weight: 600;
            padding: 6px 8px; border-radius: 6px; margin-top: 6px; font-size: 13px; }
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
button.act { margin-top: 6px; font-size: 13px; padding: 6px 10px; }
#panel { position: fixed; left: 8px; right: 8px; bottom: 8px; max-height: 80vh; overflow: auto;
         background: Canvas; border: 1px solid var(--line); border-radius: 8px; padding: 12px;
         box-shadow: 0 4px 24px rgba(0,0,0,.3); }
#panel h3 { margin: 0 0 8px; font-size: 15px; }
pre.lines, pre.output { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px;
                        background: rgba(127,127,127,.1); padding: 8px; border-radius: 4px; }
pre.output { max-height: 40vh; overflow: auto; }
.buttons { display: flex; gap: 12px; margin: 8px 0; }
.refused, .failed, .failed summary { color: var(--red); }
details.job summary { font-size: 13px; padding: 4px 0; cursor: pointer; }
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
    let timer = null;
    let inFlight = null;
    async function read() {{
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
        timer = opts.schedule(tick, retryDelay(interval, state.failures) * 1000);
      }}
    }}
    function tick() {{
      if (inFlight) return inFlight;
      inFlight = read().finally(() => {{ inFlight = null; }});
      return inFlight;
    }}
    // Read now, not at the end of a backoff: the page was looked at again,
    // or the network came back, and the server may be back with it.
    function wake() {{
      if (inFlight) return inFlight;
      if (timer !== null && opts.cancel) opts.cancel(timer);
      timer = null;
      return tick();
    }}
    return {{tick: tick, wake: wake, state: state}};
  }}
  // A job poll's answer: show it, keep polling, or the server forgot it
  // (it restarted; the Mac's audit log still has the action).
  function jobPoll(status) {{
    if (status === 200) return "show";
    if (status === 404) return "lost";
    return "retry";
  }}
  function actionBody(row, nonce) {{
    return JSON.stringify({{row: row, nonce: nonce}});
  }}
  function actionLabel(kind) {{
    return kind === "terminal" ? "open a session\u2026" : "run\u2026";
  }}
  // The Mac moved the page to HTTPS: the fetch saw a redirect it did not follow.
  function moved(response) {{
    return response.type === "opaqueredirect";
  }}
  function jobText(job) {{
    if (job.state !== "done") return "running";
    const took = " in " + Number(job.elapsed || 0).toFixed(1) + "s";
    if (job.status === null || job.status === undefined) {{
      return (job.ok ? "launched" : "FAILED") + took;
    }}
    return (job.ok ? "" : "FAILED: ") + "exit " + job.status + took;
  }}
  return {{retryDelay: retryDelay, ageOf: ageOf, freshnessText: freshnessText,
          makeLoop: makeLoop, actionBody: actionBody, actionLabel: actionLabel,
          jobText: jobText, jobPoll: jobPoll, moved: moved}};
}})();

if (typeof document !== "undefined") {{
  const byId = (id) => document.getElementById(id);
  const openNoise = new Set();
  const openJobs = new Set();
  let lastError = "";
  let panelJob = null;

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
    if (line.action) {{
      const button = el("button", "act", ORACLE.actionLabel(line.action));
      button.addEventListener("click", () => openConfirm(line.row));
      card.appendChild(button);
    }}
    return card;
  }}

  function showPanel(children) {{
    const panel = byId("panel");
    panel.replaceChildren(...children);
    panel.hidden = false;
  }}

  function closePanel() {{
    panelJob = null;
    const panel = byId("panel");
    panel.hidden = true;
    panel.replaceChildren();
  }}

  function closeButton(text) {{
    const button = el("button", "cancel", text);
    button.addEventListener("click", closePanel);
    return button;
  }}

  async function openConfirm(row) {{
    panelJob = null;
    showPanel([el("div", "quiet", "asking the Mac what would run\u2026")]);
    let data;
    try {{
      const response = await fetch("/api/confirm?row=" + encodeURIComponent(row),
                                   {{cache: "no-store", credentials: "same-origin"}});
      data = await response.json();
    }} catch (err) {{
      showPanel([el("p", "refused", "could not load the confirm: " + err.message),
                 closeButton("close")]);
      return;
    }}
    const parts = [el("h3", null, data.subject || row)];
    if (data.refused) {{
      parts.push(el("p", "refused", "Not run: " + data.refused), closeButton("close"));
      showPanel(parts);
      return;
    }}
    parts.push(el("pre", "lines", data.lines.join("\\n")));
    if (!data.nonce) {{
      parts.push(closeButton("close"));
      showPanel(parts);
      return;
    }}
    const buttons = el("div", "buttons");
    const cancel = closeButton("Cancel");
    const go = el("button", "go", data.kind === "terminal" ? "Open it" : "Run it");
    go.addEventListener("click", () => {{
      go.disabled = true;
      cancel.disabled = true;
      confirmAct(row, data.nonce);
    }});
    buttons.appendChild(cancel);
    buttons.appendChild(go);
    parts.push(buttons, el("div", "quiet", "Nothing runs until you press " + go.textContent +
                              ". This confirm lasts " + Math.round(data.expires_in) + "s."));
    showPanel(parts);
    cancel.focus();
  }}

  async function confirmAct(row, nonce) {{
    let response, data;
    try {{
      response = await fetch("/api/act", {{
        method: "POST", cache: "no-store", credentials: "same-origin",
        headers: {{"Content-Type": "application/json"}}, body: ORACLE.actionBody(row, nonce),
      }});
      data = await response.json();
    }} catch (err) {{
      showResult({{error: "the action may not have reached the Mac: " + err.message}});
      return;
    }}
    if (response.status !== 202) {{
      showResult({{error: data.error || "HTTP " + response.status}});
      return;
    }}
    showResult({{job: data.job}});
    if (data.job.state !== "done") pollJob(data.job.id);
  }}

  function showResult(result) {{
    if (result.error) {{
      showPanel([el("p", "refused", result.error), closeButton("close")]);
      return;
    }}
    const job = result.job;
    const failed = job.state === "done" && !job.ok;
    const parts = [el("h3", null, job.subject),
                   el("pre", "lines", job.argv.join(" ") + "\\nin " + job.cwd),
                   el("div", failed ? "failed" : "", ORACLE.jobText(job))];
    if (job.remote_control && job.state === "done" && job.ok) {{
      parts.push(el("div", "quiet",
                    "pick it up from Claude Code's Remote Control as " + job.remote_control));
    }}
    if (job.output) parts.push(el("pre", "output", job.output));
    if (job.audit_error) parts.push(el("div", "refused", job.audit_error));
    parts.push(closeButton("close"));
    showPanel(parts);
  }}

  function pollJob(id) {{
    panelJob = id;
    const poll = async () => {{
      if (panelJob !== id) return;
      try {{
        const response = await fetch("/api/job?id=" + encodeURIComponent(id),
                                     {{cache: "no-store", credentials: "same-origin"}});
        const verdict = ORACLE.jobPoll(response.status);
        if (verdict === "lost") {{
          if (panelJob !== id) return;
          showPanel([el("div", "refused", "The server restarted and no longer knows this "
                        + "action's result. The Mac's audit log has it: web/actions.log "
                        + "in the oracle home."), closeButton("close")]);
          return;
        }}
        if (verdict === "show") {{
          const job = await response.json();
          if (panelJob !== id) return;
          showResult({{job: job}});
          if (job.state === "done") return;
        }}
      }} catch (err) {{}}
      setTimeout(poll, 1000);
    }};
    setTimeout(poll, 1000);
  }}

  function renderJobs(jobs) {{
    const fragment = document.createDocumentFragment();
    if (jobs.length) fragment.appendChild(el("h2", null, "your actions"));
    for (const job of jobs) {{
      const failed = job.state === "done" && !job.ok;
      const details = el("details", "job" + (failed ? " failed" : ""));
      details.open = openJobs.has(job.id);
      details.addEventListener("toggle", () => {{
        if (details.open) openJobs.add(job.id); else openJobs.delete(job.id);
      }});
      details.appendChild(el("summary", null, job.subject + ": " + ORACLE.jobText(job)));
      details.appendChild(el("pre", "lines", job.argv.join(" ") + "\\nin " + job.cwd));
      if (job.output) details.appendChild(el("pre", "output", job.output));
      fragment.appendChild(details);
    }}
    byId("jobs").replaceChildren(fragment);
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
    renderJobs(data.jobs || []);
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
      const response = await fetch("/api/state", {{cache: "no-store", credentials: "same-origin",
                                                   redirect: "manual"}});
      if (ORACLE.moved(response)) {{
        location.reload();
        throw new Error("the page moved to HTTPS; opening it there");
      }}
      if (response.status === 401) throw new Error("this device is not paired any more");
      if (!response.ok) {{
        let message = "HTTP " + response.status;
        try {{ message = (await response.json()).error || message; }} catch (e) {{}}
        throw new Error(message);
      }}
      return response.json();
    }},
    schedule: (fn, ms) => setTimeout(fn, ms),
    cancel: (handle) => clearTimeout(handle),
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
  document.addEventListener("visibilitychange", () => {{
    if (document.visibilityState === "visible") loop.wake();
  }});
  for (const name of ["online", "pageshow", "focus"]) {{
    window.addEventListener(name, () => loop.wake());
  }}
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


def _warning(warning: str | None) -> str:
    return f'<div id="insecure">{escape(warning)}</div>' if warning else ""


def page(nonce: str, interval: float = DEFAULT_INTERVAL, warning: str | None = None) -> str:
    """The dashboard: a header with its freshness line, then the projects."""
    body = (
        "<header><h1>invariant oracle · <span id=\"count\">loading</span></h1>"
        f"{_warning(warning)}"
        "<div id=\"fresh\">never updated: no read has succeeded yet</div>"
        "<div id=\"banner\" hidden></div></header>"
        "<section id=\"jobs\"></section>"
        "<main id=\"projects\"></main>"
        "<div id=\"panel\" role=\"dialog\" hidden></div>"
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
