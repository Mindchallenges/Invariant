# ADR-008 — The oracle listens on the tailnet only, to paired devices

- **Status:** Accepted. D1–D7 are implemented by IDEA_023 increment 1 (the
  read-only page). D8, the remote confirm, is decided in principle here and
  implemented by increment 2; increment 3 runs the server under the launchd
  agent.
- **Date:** 2026-10-22
- **Supersedes:** nothing. It amends ADR-007 in two places: the oracle gains a
  network listener, and D18's confirm-first actions will gain a second surface
  that is not the Mac's own terminal.

## Context

The Session Oracle (IDEA_012, ADR-007) tells you which session needs you, but
only on the Mac: `oracle tui` is a terminal and the notifications are macOS
banners. Sessions are often driven remotely, so a session that needs you
while you are away from the Mac goes unseen until you get back. IDEA_023 asks
for the dashboard on the phone.

The user settled three things on 2026-10-22:

- **Hosting.** The page is served from the Mac and reached over the user's
  private Tailscale network. Session data never leaves the user's devices,
  and nothing on the public internet can reach it. A hosted site (IDEA_015)
  and a Claude artifact (IDEA_016) were not chosen.
- **Remote power.** View, plus the same confirm-first actions as the TUI,
  confirmed on the page. It never answers a Claude Code permission prompt.
- **Auth.** The network is the gate, plus a token per device, stored on its
  first visit.

That makes `oracle web` the first network listener this library runs. ADR-007
calls the oracle read-only, and its D18 assumes the person confirming an
action sits at the Mac. This record holds the decisions the listener, the
token and the remote confirm need.

## Decisions recorded

### D1 — Bind only the Mac's Tailscale addresses; never the wildcard

<!-- invariant: allow-privacy -->
The server listens on each address the Mac holds in Tailscale's ranges, the
IPv4 block `100.64.0.0/10` and the IPv6 prefix `fd7a:115c:a1e0::/48`, and on
nothing else. One `ThreadingHTTPServer` is created per address. The addresses
are found by asking `tailscale ip` (on `PATH`, else the macOS app's own CLI).
If that answers nothing, `ifconfig`'s tunnel interfaces (`utun*`,
`tailscale*`) are read instead. Both answers are filtered to the two ranges,
so neither can hand the server an address on another network.

<!-- invariant: allow-privacy -->
With no Tailscale address, `oracle web` refuses to start. The one exception is
`--allow-loopback`, which serves on `127.0.0.1` alone, for a test on the Mac.
It applies only when no tailnet address exists, so it can never widen a real
deployment.

Why not the wildcard plus the firewall and the token, as the idea's risks
listed? The wildcard makes the page reachable from the café Wi-Fi the laptop
joins and from every other network the Mac is on, and then the token is the
only gate. Binding the tailnet address makes the network the first gate. A
device that is not on the tailnet has no route to the socket, which is the
user's "nothing on the public internet can reach it" in mechanism rather
than in policy. The cost is that an interface that comes up after the server
starts is not served. In the foreground that is a restart. For the launchd
agent (increment 3) it means the agent waits and retries rather than falls
back to the wildcard.

The IPv4 range is shared CGNAT space, so an address in it does not prove
Tailscale. That is why discovery asks `tailscale ip` first and reads only
tunnel interfaces from `ifconfig`, never an Ethernet or Wi-Fi interface.

### D2 — The `Host` header must name this server

A request is answered only if its `Host` is a bound address, a MagicDNS name
(`*.ts.net`), or, with loopback bound, `localhost`. Anything else gets a 421
and nothing more. This stops a DNS-rebinding page, opened in the phone's
browser on the tailnet, from addressing the server under its own name. The
token would stop it anyway, because the cookie belongs to the server's name
and not the attacker's. This check is the second line, and it is cheap.

### D3 — A device pairs once, with a one-time code, and keeps a hashed token

`invariant oracle web pair [--name phone] [--ttl 300]` issues a code: 10
characters from a 32-letter alphabet with no `0`/`O`/`1`/`I`, so 50 bits that
can be typed off a screen. It lives 5 minutes and is spent by the first good
redeem. The command prints it with a link per tailnet address,
`http://<address>:<port>/pair?code=…`.

- **Opening the link spends nothing.** `GET /pair` shows a form with the code
  filled in, and only its `POST` redeems the code. Link previews and
  prefetchers therefore cannot pair a device, or burn a code, on their own.
- **Redeeming mints the token.** That is 256 random bits, set as the cookie
  `invariant_oracle`: `HttpOnly; SameSite=Strict; Path=/; Max-Age=400 days`.
  `Authorization: Bearer <token>` is accepted too, for `curl`.
- **The store keeps hashes.** `<oracle home>/web/devices.json` holds each
  device's `{id, name, hash, paired}` and each pending code's `{hash, name,
  expires}`, with SHA-256 for both. A 256-bit token needs no slow hash. A code
  is weak by design, so it is single-use, expires, and ten wrong codes in a
  row cancel every pending code. The file is `0600` and its directory `0700`.
- **Revoking is per device.** `oracle web devices` lists `{id, name,
  paired}`, and `oracle web revoke <id>` removes one hash. That device's next
  request gets nothing, and the others are untouched.

**Not `Secure`.** The page is plain HTTP over WireGuard, and a browser will not
send a `Secure` cookie over HTTP to any host but `localhost`. The tailnet
already encrypts and authenticates the transport. `Secure` comes with HTTPS
if the page is ever served through `tailscale serve` and its certificate,
which would be a later decision.

**A store that cannot be read verifies nobody.** `verify` fails closed. Every
other operation refuses a corrupt store, a newer version or a malformed shape
instead of rewriting it, as the project index does (ADR-007 D3), because this
file is a source of truth: losing it unpairs every device. That is also why it
lives in `web/`, outside the watcher's `oracle/` directory, which is derived
and safe to delete. The pairing `expires` and the device's `paired` are clock
times under the home, the D14 exception again: user-level, never committed.

### D4 — Without a valid token, nothing

An unpaired request to any path but `/pair` gets the same minimal "not
paired" page with a 401. It carries no row, no project name and no count, and
the handler checks the token before it reads anything. The suite asserts the
loader is never called for one.

### D5 — Read-only in this increment; the page reads the same document

`GET /` is the page and `GET /api/state` returns `{document, view,
freshness}`. `document` is exactly `oracle list --json`'s, read through
`dashboard.load`: the watcher's snapshot while it is fresh, else an in-process
scan, which is the TUI's own rule. So the phone, the TUI and `oracle list`
cannot disagree about a row. `view` is that document arranged for the page,
using `dashboard.lines`, the TUI's tones and `row_label`. Each project's quiet
orphan hosts and leaked worktrees are folded into one summary line, because a
project that tore down many sessions has many of them and they buried the
rows that matter. A host or worktree that raises an alert is never folded.

No `POST` exists but the pairing form, so nothing on the page acts.

The page is one self-contained response: inline HTML, CSS and script, with no
CDN, font or image. It carries a `Content-Security-Policy` of `default-src
'none'` that admits only its own nonce-tagged script and style and
`connect-src 'self'`. Every response is `no-store`, `nosniff` and
`no-referrer`. The script builds the rows with `textContent` and never
`innerHTML`, so a session name cannot become markup.

### D6 — Every dashboard refreshes itself and says how old it is

The user's rule is that a dashboard always auto-refreshes. The page's script
fetches `/api/state` on a timer, at the snapshot's interval. It does not use a
meta refresh, which would lose the scroll position and the opened summaries.
A failed fetch doubles the wait, up to 60 seconds, and the loop never stops. A
line at the top says "updated N s ago": the rows' age when served, plus the
time since. It turns red once that is older than three intervals, which is
`watcher.snapshot_status`'s own rule (`dashboard.freshness`). A stalled
refresh, or a watcher that stopped, therefore shows by itself. The loop's
logic is a page-independent object, and the suite drives it under a
JavaScript engine (`node`, else macOS's `jsc`).

The TUI follows the same rule. Its timer now starts at mount and not after
the first successful read, so a first read that fails is retried. It shows
the same line every second, from the same function.

### D7 — The server, its discovery and the network are seams; the suite stays on loopback

The CLI reaches discovery through `_oracle_web_addresses` and the server
through `_oracle_web_server`. The suite replaces both for every test: no
tailnet is found until a test names one, and the server binds nothing. This is
D13's and D17's shape. `conftest.py`'s program guard also refuses `tailscale`.

A second guard came from running the suite. macOS asks whether Python may
"find devices on your local network" the first time a process reaches past
loopback, and a run of these tests put that prompt on the developer's
screen. The standard library's `HTTPServer.server_bind` calls
`socket.getfqdn` on the bound address, a reverse lookup nothing here reads, so
the server's own bind no longer does that. The suite's backstop now refuses
any `bind`, `connect`, `connect_ex` or `sendto` that is not loopback (the
wildcards included) and any reverse lookup. It raises the `BaseException`
the program guard raises, so no broad `except` can swallow it. The one test
that opens a socket binds loopback on an ephemeral port.

**Local Network permission.** A real `oracle web` on the tailnet address may
trigger the same prompt for the Python interpreter that serves it, on its
first serve. It must be allowed, or the phone cannot connect. It is granted
or revoked in System Settings > Privacy & Security > Local Network. Loopback
needs no permission. The setup guide says so.

### D8 — The remote confirm (increment 2, decided in principle)

The page will act through `oracle/actions`, the TUI's own `refusal` and
`Actions.perform`, and add a transport and no new rules. A `GET` that shows
the confirm returns the exact `argv`, `cwd` and added environment, as the TUI
modal does, plus a nonce bound to that step and that device. It is single
use, short-lived and kept in the server's memory. The confirm is a `POST`
carrying it, same-origin, with the `SameSite=Strict` cookie. A replayed or
forged confirm finds no nonce and runs nothing, and a confirm sent twice runs
once. Nothing on the page can answer a permission prompt. Increment 2 records
the details.

## Alternatives rejected

- **Wildcard plus firewall plus token.** See D1. The token would be the only
  gate on every network the Mac joins.
- **`tailscale serve` in front of a loopback server.** It brings HTTPS and
  tailnet identity headers, but it reconfigures the user's Tailscale node,
  depends on that feature being enabled for the tailnet, and leaves a proxy
  rule behind when invariant is gone. It remains open as an option for later,
  and would allow `Secure`.
- **A web framework (Flask, FastAPI, aiohttp).** Not needed: one page, one
  JSON route and one form. `http.server.ThreadingHTTPServer` does it, and the
  core install stays PyYAML-only. The page needs no extra either, unlike the
  TUI (ADR-007 D19).
- **A token in the URL.** It would end up in history, in screenshots and in
  any `Referer`. A cookie the script cannot read (`HttpOnly`) is the smaller
  exposure.
- **Long-lived codes or reusable links.** A code copied once into a chat would
  pair any device later. Single use and 5 minutes bound that exposure.
