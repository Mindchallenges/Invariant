# ADR-008 — The oracle listens on the tailnet only, to paired devices

- **Status:** Accepted. D1–D7 are implemented by IDEA_023 increment 1 (the
  read-only page). D8, the remote confirm, and D9–D14, its details and the
  listener's hardening, are implemented by increment 2. D15–D18, the page
  served always by a LaunchAgent, are implemented by increment 3. D19–D24,
  the page over HTTPS with Tailscale's certificate, are implemented by
  IDEA_026; D22 replaces D3's "Not `Secure`" note.
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

**`Secure` once served over HTTPS (amended by D22).** As first shipped, the
page was plain HTTP over WireGuard and the cookie was not `Secure`, because a
browser will not send a `Secure` cookie over HTTP to any host but `localhost`.
IDEA_026 serves the page over HTTPS, and the cookie it sets there is
`Secure`. Over the plain-HTTP fallback (D21) it is not, for the same reason
as before.

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
`Referrer-Policy: same-origin`. The script builds the rows with `textContent`
and never `innerHTML`, so a session name cannot become markup.

The referrer policy was `no-referrer` at first. Under it a browser sends
`Origin: null` on the page's own form `POST`, a navigation, so the pairing
form was refused by the `Origin` check meant for forgeries, and no real
browser could pair (BUG_079). `same-origin` sends a referrer, and with it a
`/pair?code=` query, only to this origin and never to another, which was what
`no-referrer` was there for. A same-origin request then carries the page's
real `Origin`, and a cross-origin one still carries `null`, which both
`Origin` checks refuse (D10, D11).

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

### D8 — The remote confirm adds a transport, not a rule

The page acts through `oracle/actions`: the TUI's own `step_for`, `refusal`,
`Actions.describe` and `Actions.perform`. A `GET` that shows the confirm
returns the exact `argv`, `cwd` and added environment, the lines the TUI modal
prints, plus a nonce bound to that step and that device. It is single use,
short-lived and kept in the server's memory. The confirm is a `POST` carrying
it, same-origin, with the `SameSite=Strict` cookie. A replayed or forged
confirm finds no nonce and runs nothing, and a confirm sent twice runs once.
Nothing on the page can answer a permission prompt: there is no route to one,
and `refusal` refuses any runnable step on a row that waits on one. The
transport is `oracle/webactions`. D9–D14 are its details.

### D9 — The server derives the step; the client names only a row

An action names its row by a key that survives a rescan, `project|subject`
(`scan.row_key`, the subject a signal's key already uses), not by its position
on the page. The server reads the machine afresh, an in-process scan through
the same path as `oracle list` and not the snapshot, which can be 15 seconds
old. It finds the one row with that key and derives the step from it, for the
confirm and again for the action. Two rows with one key, or none, is "not
there". The action's body is `{row, nonce}`. Any `argv`, `cwd`, `env` or
`kind` a client adds is never read.

The confirm remembers what it agreed to run: the kind, `argv`, `cwd`, added
environment and, for a terminal, the session name (`actions.command_of`). When
the fresh step at action time differs, nothing runs and the page is told to
look again. The label and the gate detail are left out of that comparison,
because they describe the step and move while the command stays the same.
Every refusal is checked again at action time too. A row that started
waiting on a prompt between the confirm and the press is refused then.

### D10 — The nonce, the origin and one action at a time

- **Nonce.** 24 random bytes, issued only with a confirm that may run (never
  for an `info` step or a refused one), bound to the device's id and the row
  key, good for 120 seconds, and kept in memory (at most 64; the oldest is
  dropped first). The first action that presents it spends it, whatever
  happens next. A nonce shown by the wrong device is spent by that attempt
  too, so a leaked one is spent when somebody tries it, not kept for later. A server
  restart forgets every nonce, which only means opening the confirm again.
- **Origin is required.** An action `POST` must carry an `Origin` naming
  this server, scheme `http`, host and port exactly. A missing `Origin` is
  refused here, unlike on `/pair` (D11). A browser sends `Origin` with every
  `POST` a page makes, its own origin's included, so a request without one
  did not come from the page. Where a browser sends `Sec-Fetch-Site`,
  anything but `same-origin` is refused too. The paired token is checked
  before all of this, so an unpaired request still learns nothing.
- **The body** is JSON or a form, at most 4096 bytes, with string `row` and
  `nonce`. Anything else is a 400, 413 or 415, and nothing runs.
- **One at a time per row.** An action on a row whose last action is still
  running is refused, and at most four run at once across rows. That holds
  across devices too. A double tap therefore runs once: the second press
  finds its nonce spent, or the row busy.
- **Results are polled.** The action answers 202 at once with a job. The
  command runs in its own thread, so a long run blocks no request, and `GET
  /api/job?id=…` returns its state, exit status, output (its last 64 KiB) and
  elapsed time, to the device that started it and no other. The device's
  last 20 actions come back with every `/api/state`, so the page keeps
  refreshing while an action runs or after it has finished, and a reload
  still shows the result.

### D11 — `/pair` keeps its no-`Origin` allowance; an action does not

`POST /pair` refuses a foreign `Origin` but lets a missing one through. A
cross-site forgery of the pairing form could at most make the victim's
browser redeem a code, and only the Mac's `oracle web pair` issues codes. An
attacker who does not have a live code can do nothing with the form. One who
does could pair their own device directly, so a forged form gains them
nothing. The code is the credential, and it is single-use and expires. Older
browsers, `curl` and some privacy modes send no `Origin` on a form `POST`, and
pairing should not fail for them. An action is different: its credential
is the ambient cookie, which a forged request would carry. So there the
missing `Origin` is refused (D10), together with the nonce, which is the real
proof that the request came from the confirm.

### D12 — A terminal step is offered from the page

The Mac may have nobody at it, and a `terminal` step opens a window there. It
is offered anyway, under the same confirm, because that is its value from a
phone. The window runs `claude --name <session> --remote-control <session>`,
and the session it starts can be picked up from the phone through Claude
Code's Remote Control. The confirm says so: the exact launch line, that the
window opens on the Mac whether or not anyone is at it, and the name to look
for in Remote Control. The launch goes through `_handoff_launcher` and counts
only once the successor watch sees the new `claude` alive (BUG_075). The
watch's verdict is the job's result on the page: "claude (pid N) is running
<name>", or the failure with the line to run by hand. Not offering it would
leave a parked session unrecoverable from the phone, which is the case
IDEA_023 exists for.

### D13 — An audit line per action, as a record

Each action from the page appends two JSON lines to `<oracle home>/web/actions.log`,
one at its start and one at its end. Each line holds `at` (UTC, to the
second), `event`, `job`, `device` and `device_name`, `row`, `kind`, `argv` and
`cwd`, and the end adds `exit` (`null` for a terminal), `ok` and `elapsed`.
It holds no token, nonce, environment or output.

- **A record, not derived state.** Nothing else can say later what was run from
  the phone, so the log lives in `web/`, beside the paired devices, and not
  in the watcher's `oracle/`, which is safe to delete. Nothing reads it back,
  and it is appended to and never rewritten or rotated. A line is about 300
  bytes.
- **No record, no action.** If the start line cannot be written, the action
  is refused before it runs (503). An end line that cannot be written is
  reported on the job instead.
- **Two lines rather than one.** A server killed mid-run still leaves the
  start line.
- **Clock times, machine-local.** This is ADR-007's D14 exception again: the log lives under the home and is never committed.

### D14 — The listener resists slow and greedy clients; private files are born private

- **A socket timeout per request**, 10 seconds between reads, so a client
  that sends a request a byte at a time ties up a thread for no longer than
  that.
- **At most 40 headers.** The standard library's cap is 100. A browser sends
  about a dozen, so more is a 431.
- **At most 32 connections at once.** One more is closed without an answer,
  so many slow clients cannot pile up threads.
- **A malformed `Content-Length`** is a 400 rather than an exception.
- **Owner-only from creation.** `web/` is made `0700` with `mkdir`'s mode,
  and narrowed first if an older build made it wider. Each `devices.json`
  write creates its temporary file `0600` (`JsonStore(mode=…)`, with
  `O_EXCL`, after removing a stale one) before a byte goes in. The audit log
  is opened `O_APPEND | O_CREAT | O_NOFOLLOW` with mode `0600`. Nothing is
  created with the process default and narrowed by `chmod` after the write,
  which would leave it readable in between.

### D15 — A second LaunchAgent serves the page, and only on request

`invariant oracle install --web [--port 7341]` installs
`dev.invariant.oracle.web`, beside the watcher's `dev.invariant.oracle`. Its
plist runs `<python> -m invariant oracle web --port N --wait`, at load and
kept alive, with both streams in `<oracle home>/oracle/web.log`. Plain
`oracle install` is unchanged and never touches it; `oracle uninstall --web`
removes the page alone, and plain `oracle uninstall` removes both.

Both agents go through one `LaunchdInstaller`, keyed by label, so the second
one is written, loaded, replaced, removed and checked by the code that
already does it for the first (ADR-007 D17). The plist follows the watcher's
rules: the absolute interpreter, unresolved; `-m invariant`; an explicit
`PATH`; `INVARIANT_HOME` only if it was set. Its `PATH` finds more, because
the page does more: `invariant`, which an action from the page runs, then
git, osascript and `tailscale`, then the interpreter's own directory, which
holds the console script of the installation serving the page.

**Why not the watcher's agent.** One process serving both would be one fewer
plist, but it would couple two things that fail differently. A port in use, a
bind refused or a wedged listener would then take the notifications down with
it, and a watcher restarted for its own reasons would drop every phone's
connection. The page is also opt-in. Most people who want notifications do
not want a network listener, so making `oracle watch` listen, even behind a
flag, would make the one agent everybody installs the first network listener
on their Mac. Two agents keep the listener as separate as the decision to run
one, with a log and an uninstall of its own. They share the interpreter, so
macOS's Local Network permission (D7) covers both.

### D16 — Under launchd, no tailnet is a wait, never an exit and never a fallback

A login usually comes before Tailscale is up. A server that refused to start
(D1) would exit, and launchd would restart it every ten seconds for as long
as Tailscale stayed down, which is a crash loop with a log line each time.
`oracle web --wait`, the agent's mode, therefore waits instead
(`oracle/webservice`):

- **No Tailscale address, no socket.** It asks for the addresses every 10 s,
  logs once that it is waiting, and binds nothing. The answer is filtered to
  Tailscale's ranges once more inside the loop, so no discovery result can
  make it bind another network. `--wait` and `--allow-loopback` together
  are a usage error, and the plist never carries the second.
- **It follows the tailnet.** While serving, it asks again every 30 s. When
  the addresses change, it closes the listener and binds the new ones. When
  they are gone, it closes and waits. This is D1's cost ("an interface that
  comes up after the server starts is not served") paid by the agent rather
  than by the person.
- **A failed bind waits too.** A port in use is logged once and tried again
  every 10 s, so nothing here exits for a condition time can mend.
- **Actions survive a rebind.** One `RemoteActions` serves every listener the
  process makes, so a running job, its result and the device's recent jobs
  outlive an address change. Only a process restart forgets them (D10).

In the foreground, without `--wait`, a missing tailnet is still a refusal
(exit 1), because the person who typed the command is there to read it.

### D17 — The loop reports what it is doing; status and doctor read that report

The loop writes `<oracle home>/oracle/web.json`, `{version, pid, state,
addresses, port, urls, since, error}`, when its state changes: `waiting`
(with the bind error, if one is why) or `serving`. It is derived, so it sits
in the watcher's `oracle/` and is safe to delete. The loop removes it when it
stops, and `oracle uninstall` removes it after the `bootout`, because
launchd's SIGTERM ends the process before it can.

`oracle status` reports the web agent's half: whether it is installed and
loaded, its port, whether its interpreter still exists, the report (where it
serves, or that it waits, and whether its pid is alive), the paired device
count, and the audit log's last line. `doctor` adds two rows when the web
plist exists: `oracle web interpreter`, as the watcher's, and `oracle web
page`, which passes while the reporting process is alive and no bind error
stands, and carries the address, the device count and the last action in its
detail. Waiting for Tailscale passes: the agent is doing what it should, and
nothing in invariant can fix it. Like the watcher's row, both read files and
ask launchd nothing.

This amends D13's "nothing reads it back". `AuditLog.last` reads the log's
last 8 KiB, never the whole file, and skips a line cut short by a killed
write.

`oracle web pair` names the port the page serves on, taken from the report,
else from the installed plist, else 7341, so the link matches an agent
installed with `--port`.

### D18 — The page recovers when the server comes back

D6's loop never stops, so a page left open across a listener restart, an
agent restart or a reboot recovers by itself, within its backoff (60 s at
most). Two additions make that prompt and honest:

- **A look cuts the backoff short.** When the page becomes visible again,
  regains focus, is restored from the back-forward cache, or the browser
  says the network is back, the pending wait is cancelled and the page
  reads at once. A read already in flight is not doubled. On a phone, that
  is the moment anyone is looking.
- **A forgotten job is said to be lost.** A restarted server knows no job
  from before it (D10), so `/api/job` answers 404. The panel stops polling
  and says the result is in the Mac's audit log, rather than showing
  "running" forever.

The device's cookie survives every restart, because `devices.json` is a
record (D3). An open confirm's nonce does not, which only means opening the
confirm again.

### D19 — The certificate is Tailscale's, fetched through stdout and kept owner-only

The page is served with the Let's Encrypt certificate Tailscale issues for the
Mac's MagicDNS name, `<host>.<tailnet>.ts.net`. The person turns on "HTTPS
Certificates" on the DNS page of the Tailscale admin console once; nothing
else is configured. `oracle/tls.issue` asks the Tailscale CLI two things:
`status --json`, for the name (its `CertDomains`, else `Self.DNSName`, the
trailing dot stripped; never a name written into invariant), then `cert
--cert-file - --key-file - <name>`, which prints the chain and then the key to
stdout.

- **Stdout, not a path.** The Mac App Store and standalone apps' CLI is
  sandboxed: given a path outside its container, `cert` fails with "operation
  not permitted" on its temporary file. Given `-`, it prints. invariant then
  writes the file itself.
- **One lookup for the CLI.** `web.tailscale_binaries`, the address
  discovery's own, which tries `tailscale` on `PATH` and then the app bundle's
  absolute path. The LaunchAgent's minimal `PATH` therefore cannot hide it.
- **One file, owner-only from creation.** `<oracle home>/web/tls/oracle.pem`
  holds the chain followed by the key, `0600` in `0700` directories, written
  through a temporary file created `0600` and renamed over it
  (`home.write_private`, D14's rule). One file, because `ssl` loads the key
  from the certificate's own file, so no crash can leave a certificate beside
  another one's key. A new pair is loaded once from a candidate file before it
  replaces the kept one, so a key that does not match, a certificate for
  another name or an expired one never displaces a good pair.
- **It lives in `web/`**, beside the paired devices, because the key is a
  secret and `web/` is the home's private directory. It is not a record:
  deleting it costs one request to Tailscale.
- **A seam.** The CLI reaches `issue` through `_oracle_web_certificates`,
  beside `_oracle_web_addresses` and `_oracle_web_server` (D7). The suite
  replaces it with a recorder that hands back a self-signed fixture or
  refuses, and the program guard still refuses `tailscale`.

Reading a certificate's name and expiry uses a small DER reader over the
standard library, so the core install stays PyYAML-only.

### D20 — Once there is a certificate, HTTPS only

With a certificate, every listener speaks TLS on the same port, 7341, and
plain HTTP is not served. A connection is read in its own thread: if its
first byte is not a TLS handshake, it gets `307` with `Location:
https://<name>:<port>/`, `Cache-Control: no-store`, no body, and is closed. No
page, no JSON and no cookie is read or sent over it. The location is built
from the certificate's name and the listening port, never from the request's
`Host`, so it cannot be steered elsewhere. A `307`, not a `308`: a browser
caches a permanent redirect, and the fallback in D21 may need plain HTTP on
that port again.

`oracle web` in the foreground follows the same rule: it asks for the
certificate once at start and serves HTTPS with it. `--allow-loopback`
serves plain HTTP on loopback as before, because the certificate does not
name loopback.

### D21 — No certificate: plain HTTP, said loudly, never a locked-out phone

When no usable certificate can be had (HTTPS Certificates off for the
tailnet, Tailscale not answering, the kept one expired and renewal failing),
the page is served over plain HTTP, exactly as before IDEA_026, and says so:

- the page and the pairing form carry a red line at the top naming why;
- `oracle web` prints `PLAIN HTTP: …` with the reason, and the agent's log
  says it once;
- `oracle/web.json` records `scheme: http` and the error, `oracle status`
  prints `certificate  NONE: serving plain http; <why>`, and `doctor`'s
  `oracle web certificate` row fails.

Refusing to serve was the alternative. It would make a console setting, or
a Tailscale hiccup at login, lock the phone out of the page entirely, and
plain HTTP over WireGuard is the transport ADR-008 shipped with and judged
sufficient (D3 as first written). A warning keeps the page usable and the
gap visible. The loop asks Tailscale again every 10 minutes while it has no
certificate, so turning HTTPS Certificates on reaches the page within that,
without a restart.

A kept certificate that is still valid is always served, even while renewing
it fails: the fallback is for no certificate, not for a late one.

### D22 — The name is the page: `Host`, `Origin`, the pairing link and the cookie

The certificate names the MagicDNS name, not the `100.x` or `fd7a:` address,
so over HTTPS the page is that name and nothing else:

- **`Host`.** Only the certificate's name, any port, case and a trailing dot
  ignored. A bare address cannot reach this check without the person
  clicking through the browser's name-mismatch warning first, so it is not
  rewarded with a redirect: it gets a 421 whose text names the https URL.
  Another `*.ts.net` name, which D2 admitted over HTTP, is refused too: the
  certificate does not cover it. Over plain HTTP, D2's rule stands.
- **`Origin`.** The pairing form's and the actions' checks (D10, D11) compare
  the scheme the page is served over, `https`, as well as the host and port.
  An `http://` origin is refused by an HTTPS page.
- **The pairing link.** `oracle web pair` prints one link,
  `https://<name>:<port>/pair?code=…`, whenever a valid certificate is kept.
  It reads the kept file and asks Tailscale nothing. Without one it prints a
  plain-HTTP link per address, as before.
- **The cookie** gains `Secure` when it is set over HTTPS.

A device paired over plain HTTP at an address keeps a cookie for that
address, which the name does not receive. It pairs once more at the name.
One paired at the name over HTTP keeps its cookie: a cookie is the host's,
whatever the scheme.

### D23 — Renewal runs inside the loop and reaches the next connection

`WebService`'s loop (D16) also keeps the certificate. On each look at a
tailnet that is up, `Certificates.ensure` reads the kept file and asks
Tailscale for a new one only once fewer than 30 days remain (a 90-day
Let's Encrypt certificate; Tailscale renews its own copy at about that
point), and then at most once an hour until a newer one comes back.

A renewal for the same name is swapped into the running listener with no
restart. What a listener serves is a `Served`: the certificate and a fresh
`SSLContext` built from it, replaced together. Each connection reads that
pair once, when it arrives, and wraps itself with that context, so the next
connection gets the new certificate and a handshake under way keeps the one
it began with.

- **Not `load_cert_chain` on the live context.** That mutates a context
  other threads are handshaking with, and a key that fails to match would
  leave it half-replaced. A fresh context is built and loaded first, and only
  a context that loaded is swapped in.
- **Not a rebuilt listener.** Closing and binding again drops the
  connections in flight and races the port. A listener is rebuilt only when
  what it is changes: the first certificate (plain HTTP to HTTPS), its loss
  (HTTPS to plain HTTP), or a certificate for a new name (a renamed Mac).

The report, `oracle/web.json`, gains `scheme` and `certificate: {name,
not_after, expires, error}`, rewritten whenever the certificate served
changes. `oracle status` prints its name and expiry, `renewing` inside the
30 days, and the last renewal's failure. `doctor`'s `oracle web certificate`
row fails over plain HTTP or once fewer than 14 days remain, by which point
renewal has failed for two weeks.

**The open page follows the move.** A page left open over plain HTTP when the
listener switches to HTTPS gets the D20 redirect on its next state read. The
read does not follow redirects (`redirect: "manual"`), sees an opaque
redirect, and reloads the page, which the browser then follows to the https
name. D6's loop is otherwise unchanged: a failed read backs off and shows
`updated N s ago`, STALE once three intervals old.

### D24 — The machine's name is published in Certificate Transparency logs

Every certificate Let's Encrypt issues is logged publicly, so the Mac's
MagicDNS name, `<host>.<tailnet>.ts.net`, becomes public. The user accepted
this on 2026-10-22. The name reaches nothing off the tailnet: it resolves
only through MagicDNS, and the listener binds only tailnet addresses (D1).
No session data, port or path is published, only the name. Someone who
wants to keep a machine's name out of the logs can rename the Mac in
Tailscale before turning HTTPS on, or leave HTTPS Certificates off and keep
the D21 fallback.

## Alternatives rejected

- **Wildcard plus firewall plus token.** See D1. The token would be the only
  gate on every network the Mac joins.
- **`tailscale serve` in front of a loopback server.** It brings HTTPS and
  tailnet identity headers, but it reconfigures the user's Tailscale node,
  depends on that feature being enabled for the tailnet, and leaves a proxy
  rule behind when invariant is gone. It would also proxy from loopback, which
  weakens D1's tailnet-only bind and D2's `Host` check. IDEA_026 closed it:
  the oracle's own listener serves TLS (D19–D23).
- **Refusing to serve without a certificate.** See D21: a console setting
  would lock the phone out of the page.
- **Writing the certificate through `tailscale cert`'s own file paths.** The
  sandboxed app's CLI cannot write outside its container (D19).
- **`load_cert_chain` on the live context, or a listener rebuilt per
  renewal.** See D23.
- **Redirecting a bare tailnet address to the name.** See D22: the request
  arrives only after a certificate warning was clicked through.
- **A web framework (Flask, FastAPI, aiohttp).** Not needed: one page, one
  JSON route and one form. `http.server.ThreadingHTTPServer` does it, and the
  core install stays PyYAML-only. The page needs no extra either, unlike the
  TUI (ADR-007 D19).
- **A token in the URL.** It would end up in history, in screenshots and in
  any `Referer`. A cookie the script cannot read (`HttpOnly`) is the smaller
  exposure.
- **Trusting the snapshot for an action.** It is a file anybody with the
  user's rights can write, and it can be 15 seconds old. The confirm and the
  action read the machine afresh (D9).
- **Running the argv the page sent.** It would make the page's script, and
  anything that can forge a request, the authority on what runs. The page
  names a row, and the server decides (D9).
- **A synchronous action.** A merge or a test run can take minutes, and the
  phone's request would time out first. The 202-and-poll design (D10) keeps
  every request short.
- **The watcher's agent also serving the page.** See D15: one process
  would make a listener failure a notification failure, and the agent
  everybody installs a network listener.
- **Exiting when there is no tailnet, and letting launchd restart it.** See
  D16: a crash loop, throttled to one restart every ten seconds, for as long
  as Tailscale is down.
- **Falling back to the wildcard or loopback under the agent.** D1 stands:
  the network is the first gate, and an unattended process has nobody to
  notice that it widened.
- **Long-lived codes or reusable links.** A code copied once into a chat would
  pair any device later. Single use and 5 minutes bound that exposure.
