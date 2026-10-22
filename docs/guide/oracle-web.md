# The oracle on your phone

The Session Oracle can show its dashboard on your phone: which session needs
you, what each one recommends, and a button for each step it can run, which
asks you to confirm first. The page is served by your Mac and reached over
your own Tailscale network, so session data never leaves your devices and
nothing on the public internet can reach it.

This page sets it up end to end. The decisions behind it are in ADR-008, and
the design is in [the oracle's architecture page](../architecture/oracle.md).

## What you need

- A Mac with invariant installed (see [Installation](installation.md)).
- Tailscale on the Mac and on the phone, both signed in to the same tailnet.
- Optionally, the watcher agent (`invariant oracle install`). Without it the
  page still works: it scans on each refresh and says "watcher not running".
  With it, the page reads the watcher's snapshot and you also get macOS
  notifications.

## 1. Install Tailscale

Install Tailscale on the Mac from tailscale.com or the Mac App Store, and
sign in. Install the Tailscale app on the phone and sign in to the same
account. Then check that the Mac has a tailnet address:

```bash
tailscale ip -4      # or, with only the app: /Applications/Tailscale.app/Contents/MacOS/Tailscale ip -4
```

It prints an address in the `100.x.y.z` range. If it prints nothing,
Tailscale is not connected yet.

## 2. Install the page

```bash
invariant oracle install            # the watcher, if you do not have it yet
invariant oracle install --web      # the page, always on: a second LaunchAgent
invariant oracle status             # both agents, and where the page serves
```

`install --web` writes `~/Library/LaunchAgents/dev.invariant.oracle.web.plist`
and loads it. The agent starts at login and is restarted if it stops. It
serves on port 7341. Pass `--port N` to choose another, and run `install
--web` again to change it later; the new install replaces the old one.

The page binds only your Mac's Tailscale address, never every interface. If
Tailscale is not up when you log in, the agent waits and binds as soon as
Tailscale connects. If the address changes, or Tailscale goes away and comes
back, the agent follows it. You do not need to restart anything.

`invariant oracle status` should end with lines like these:

```text
  web agent    installed, loaded, port 7341 (~/Library/LaunchAgents/dev.invariant.oracle.web.plist)
  web python   ok  ~/.local/share/uv/tools/invariant/bin/python
  web page     serving http://<tailnet address>:7341/ (pid 4242, since <time>)
  devices      0 paired devices
  last action  none yet
```

`web page` says `waiting for a Tailscale address` until Tailscale connects.

To serve the page by hand instead, in a terminal, run `invariant oracle web`.
It stops with Ctrl-C, and it refuses to start when Tailscale is not up.

## 3. Pair your phone

Every device must be paired once. Nothing else gets past the "not paired"
page.

```bash
invariant oracle web pair --name phone
```

It prints a code and a link per tailnet address:

```text
pairing code for phone: ABCDE-FGHJK
  it works once, for 5 minutes
  open on the device, while the page is served ('invariant oracle install --web', or 'invariant oracle web'):
    http://<tailnet address>:7341/pair?code=ABCDEFGHJK
```

Open the link on the phone, with Tailscale connected there, and press "Pair
this device". The phone keeps a cookie from then on, and the page opens. The
code works once and expires after five minutes (`--ttl` changes that).
Opening the link alone pairs nothing, so a chat app's link preview cannot use
it up.

You can type the address by hand instead: open `http://<tailnet
address>:7341/pair` and enter the code. If MagicDNS is on, the Mac's name
works too: `http://<mac name>.<tailnet>.ts.net:7341/pair`. Add the page to
the phone's home screen to open it like an app.

## 4. List and revoke devices

```bash
invariant oracle web devices          # each device's id, name and when it paired
invariant oracle web revoke <id>      # forget one device
```

A revoked device's next request gets only the "not paired" page, and the
other devices keep working. To pair it again, run `pair` again. Revoke a
device you have lost before anything else. Without Tailscale it cannot reach
the page anyway, but a found phone may still be signed in to your tailnet.

## The Local Network permission prompt

The first time the page listens on the tailnet address, macOS may ask
whether Python may "find devices on your local network". The prompt names the
Python interpreter that serves the page, not invariant and not Tailscale.
macOS asks this of any program that uses the network beyond the Mac itself,
and a listening socket on the Tailscale interface counts.

Allow it, or the phone cannot connect: the page shows as serving on the Mac,
but the phone's browser times out. You can change the answer later in System
Settings > Privacy & Security > Local Network, where the entry is the Python
interpreter (it may be listed as `python3` or by its version). The agent and
`invariant oracle web` use the same interpreter, so one answer covers both.

## Using the page

The top line says `updated N s ago`. It turns red, with STALE, once the rows
are older than three refresh intervals, which means the watcher stopped or
the page cannot reach the Mac. The page refreshes itself every few seconds
and keeps trying after a failure. When the Mac comes back (after a restart of
the agent, a reboot or a Tailscale reconnect), the page recovers by itself.
Switching back to the tab, or unlocking the phone with it open, makes it read
again at once.

A row with something to run has a button. Pressing it shows exactly what
would run, where, and with which variables. Nothing runs until you press Run
it. A step that resumes a session opens a Terminal window on the Mac, whether
or not anyone is at it, and you can pick that session up from Claude Code's
Remote Control on the phone. The page never answers a permission prompt.
Every action is logged on the Mac in `~/.invariant/web/actions.log`, and
`oracle status` shows the last one.

## Troubleshooting

Start with `invariant oracle status` and `invariant doctor`. When the page's
agent is installed, `doctor` has two rows for it: `oracle web interpreter`
and `oracle web page`. The agent's log is `~/.invariant/oracle/web.log`.

| What you see | Why, and what to do |
|---|---|
| `web page  waiting for a Tailscale address` | Tailscale is not connected on the Mac. Connect it, and the page binds within about 10 seconds. |
| `web page  waiting: cannot listen on … port 7341: …` | Another program holds the port, or a hand-run `invariant oracle web` does. Stop it, or reinstall on another port: `invariant oracle install --web --port 7342`. The agent retries every 10 seconds. |
| `web page  NOT running` | The process that served the page is gone. If `web agent` says `NOT loaded`, run `invariant oracle install --web` again. Otherwise read the log. |
| `web python  MISSING` | The interpreter in the plist was moved or reinstalled, for example by `uv tool upgrade`. Run `invariant oracle install --web` to point the agent at the current one. |
| The phone times out, but the Mac says serving | Allow Local Network for Python (see above). Check that Tailscale is connected on the phone, and that the address on the phone matches the one `status` prints. |
| The phone shows "not paired" | The device was revoked, its cookie was cleared, or it is a different browser. Pair it again. |
| "not this server" (421) | The address in the browser is not the Mac's tailnet address or its `*.ts.net` name. Use the link `pair` prints. |
| A step shows "The server restarted and no longer knows this action's result" | The agent restarted while the step ran. What ran, and how it ended, if it got that far, is in `~/.invariant/web/actions.log`. |
| The pairing link names the wrong port | `pair` uses the port the page reports, else the installed one. Pass `--port` to override it. |

## Remove it

```bash
invariant oracle uninstall --web      # the page only; the watcher stays
invariant oracle uninstall            # both agents
```

Both remove the plist and the page's status file. Paired devices and the
action log are records, so they are kept: re-installing brings the same
devices back. To forget them as well, delete `~/.invariant/web/`.
