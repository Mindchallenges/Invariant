# The oracle on your phone

The Session Oracle can show its dashboard on your phone: which session needs
you, what each one recommends, and a button for each step it can run, which
asks you to confirm first. The page is served by your Mac and reached over
your own Tailscale network, so session data never leaves your devices and
nothing on the public internet can reach it. It is served over HTTPS at your
Mac's Tailscale name, `https://<mac name>.<tailnet>.ts.net:7341/`, with a
Let's Encrypt certificate Tailscale issues, so the phone's browser shows it as
secure.

This page sets it up end to end. The decisions behind it are in ADR-008, and
the design is in [the oracle's architecture page](../architecture/oracle.md).

## What is changing

The page is being redesigned as a mobile control panel (IDEA_027, ADR-009).
The setup on this page stays the same: Tailscale, the LaunchAgent, pairing,
and confirm-first actions.

Shipped so far, the plain-language panel ("Using the page" below):

- The first screen leads with a strip of counts (need you, problems,
  working, leftovers), and each count jumps to its group. Sessions that need
  you come first.
- Cards use plain words, such as "Waiting for your permission" or "52 tests
  failing", instead of engine state names, paths and process ids. Those
  stay one tap away, under "Technical details" in a session's own view.
- A session has a detail view, and any command can be copied.
- A confirm opens from the bottom of the screen. It says in one line what
  will happen, shows the command, and hides the exact folder and environment
  behind a toggle.
- The page can be added to the home screen as an app, with its own icon, and
  opens full screen without the browser's bars ("6. Add it to your home
  screen" below).

Still to come:

- A failed action says why in one sentence, with the full output behind
  "Show full output". Until then the result says "Failed, exit N", with the
  output behind that toggle.
- Later, after two fixes it depends on: closing a leftover Claude window and
  cleaning up stranded sessions and folders, each confirmed first. Each
  lists what it deletes and checks again that nothing is using it when you
  press the button.

The page still never answers a permission prompt. The design is in
[the control panel's design page](../architecture/oracle-panel.md). This
guide is updated as each part ships.

## What you need

- A Mac with invariant installed (see [Installation](installation.md)).
- Tailscale on the Mac and on the phone, both signed in to the same tailnet,
  with MagicDNS on (it is by default) and HTTPS Certificates turned on (step 2).
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

## 2. Turn on HTTPS certificates

Open the Tailscale admin console in a browser, go to the DNS page, and under
HTTPS Certificates choose Enable. This is once per tailnet. It lets each
machine get a Let's Encrypt certificate for its MagicDNS name; invariant asks
Tailscale for the Mac's when the page starts, and renews it by itself before
it expires (every 90 days). Nothing else is configured.

One thing to know before you enable it: every Let's Encrypt certificate is
recorded in public Certificate Transparency logs, so your Mac's Tailscale
name, `<mac name>.<tailnet>.ts.net`, becomes publicly visible. The name
reaches nothing off your tailnet, and no session data, address or path is
published. If you would rather not publish the name, rename the Mac in
Tailscale first, or leave HTTPS off: the page then works over plain HTTP, with
a red warning on it.

## 3. Install the page

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
back, the agent follows it. When the certificate is due, the agent renews it
and the next connection gets the new one. You do not need to restart
anything.

`invariant oracle status` should end with lines like these:

```text
  web agent    installed, loaded, port 7341 (~/Library/LaunchAgents/dev.invariant.oracle.web.plist)
  web python   ok  ~/.local/share/uv/tools/invariant/bin/python
  web page     serving https://<mac name>.<tailnet>.ts.net:7341/ (pid 4242, since <time>)
  certificate  <mac name>.<tailnet>.ts.net expires <date> (in 89 days)
  devices      0 paired devices
  last action  none yet
```

`web page` says `waiting for a Tailscale address` until Tailscale connects.
`certificate` says when the certificate expires. Within its last 30 days it
adds `renewing`: the agent is asking Tailscale for the next one, and you do
nothing. If it says `NONE: serving plain http`, the page has no certificate
and the rest of the line says why; see Troubleshooting.

To serve the page by hand instead, in a terminal, run `invariant oracle web`.
It stops with Ctrl-C, and it refuses to start when Tailscale is not up. It
asks Tailscale for the certificate once when it starts.

A page served by hand is not a test of the agent. `invariant oracle web` run
in a terminal inherits that terminal's environment: your `PATH`, your
`INVARIANT_HOME`, and a `TERM`. The agent gets only what its plist sets, and
launchd sets no `TERM`. The Tailscale app's command-line tool starts its GUI
instead of answering when `TERM` is missing, so invariant gives every program
it asks for addresses or a certificate `TERM=dumb` when there is none. When
the hand-run page works and the agent does not, the difference is in that
environment: read `invariant oracle status` and the agent's log, not the
terminal's output.

## 4. Pair your phone

Every device must be paired once. Nothing else gets past the "not paired"
page.

```bash
invariant oracle web pair --name phone
```

It prints a code and the link to open:

```text
pairing code for phone: ABCDE-FGHJK
  it works once, for 5 minutes
  open on the device, while the page is served ('invariant oracle install --web', or 'invariant oracle web'):
    https://<mac name>.<tailnet>.ts.net:7341/pair?code=ABCDE-FGHJK
```

Before the Mac has a certificate, the link is plain HTTP, one per tailnet
address: `http://<tailnet address>:7341/pair?code=…`.

Open the link on the phone, with Tailscale connected there, and press "Pair
this device". The phone keeps a cookie from then on, and the page opens. The
code works once and expires after five minutes (`--ttl` changes that).
Opening the link alone pairs nothing, so a chat app's link preview cannot use
it up.

You can type the address by hand instead: open
`https://<mac name>.<tailnet>.ts.net:7341/pair` and enter the code. Use the
name, not the `100.x.y.z` address: the certificate names the Mac's name, so
the address gets a certificate warning and then only a "not this server"
page. To open the page like an app, add it to the home screen (step 6).

**A phone paired before HTTPS.** A cookie belongs to the name or address it
was set on. A phone paired at the `100.x.y.z` address has no cookie for the
Mac's name, so it shows "not paired" there once: run `pair` again and open
the new link. A page you left open over plain HTTP moves itself to the https
name on its next refresh.

## 5. List and revoke devices

```bash
invariant oracle web devices          # each device's id, name and when it paired
invariant oracle web revoke <id>      # forget one device
```

A revoked device's next request gets only the "not paired" page, and the
other devices keep working. To pair it again, run `pair` again. Revoke a
device you have lost before anything else. Without Tailscale it cannot reach
the page anyway, but a found phone may still be signed in to your tailnet.

## 6. Add it to your home screen

The page installs as an app: its own icon (four coloured tiles, the colours
of the strip's counts, on black), full screen without the browser's bars,
dark to the edges, with nothing hidden under the notch or the home bar. It is
the same page from the same Mac, under the same pairing, so it shows and
does exactly what the browser tab does.

On an iPhone:

1. Pair the phone in Safari first (step 4), so the page opens.
2. In Safari, on the page, tap Share, then Add to Home Screen. The name
   offered is "Oracle"; change it if you like, and tap Add.
3. Open it from the home screen.

On Android, Chrome's menu offers Install app or Add to Home screen on the
same page.

**If the app opens on "not paired".** On iOS an app added to the home screen
may keep its own cookies, apart from Safari's, so it may not know the
pairing Safari has. It then shows the "not paired" page, which pairs it from
inside the app:

1. On the Mac, run `invariant oracle web pair --name phone-app` (any name;
   it is a second device in `oracle web devices`).
2. In the app, tap "Enter a pairing code", type the code it printed, and tap
   "Pair this device". Do not tap the pairing link in a message: a link
   opens in Safari, not in the app.

The app then opens on the panel, and stays paired as Safari does. Revoking
it is the same as any device (step 5).

**What has been checked, and what has not.** The manifest, the icons, the
full-screen tags, the safe-area padding and pairing from the "not paired"
page are built and tested in invariant's suite. Two iOS behaviours have not
yet been checked on an iPhone: whether an app added to the home screen keeps
Safari's pairing, and whether iOS sends the pairing cookie when it fetches
the icon. The icon and the app's manifest are served only to a paired
device, like the page. If iOS fetches the icon without the cookie, the home
screen shows a plain fallback icon (a picture of the page, or a letter)
instead of the four tiles; the app still works. Serving the icon to unpaired
devices would loosen the rule that an unpaired device gets nothing, so it is
not done without the user's decision (ADR-009 D10).

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

**Sessions** is the first screen. Under its title, "Live · updated N s ago"
says how fresh the rows are. It turns red and reads "Stale" once they are
older than three refresh intervals, which means the watcher stopped or the
page cannot reach the Mac. The page refreshes itself every few seconds and
keeps trying after a failure, saying so under the title. When the Mac comes
back (after a restart of the agent, a reboot or a Tailscale reconnect), the
page recovers by itself. Switching back to the tab, or unlocking the phone
with it open, makes it read again at once, and so does the round Refresh
button.

A red line under the title saying the page is served over plain HTTP means
the Mac has no HTTPS certificate yet; it says why. The page still works.
Once the Mac gets a certificate, a page left open over plain HTTP moves
itself to the https name on its next refresh.

**The strip** counts what needs you, the problems, what is working and the
leftovers. Tap a count to jump to its group. A count of 0 still shows.

**The groups**, in order:

| Group | What is in it |
|---|---|
| Needs you | A Claude window waiting for your permission or your answer, and finished sessions ready for review or to merge |
| Problems | Failing tests, a check that refused a step, no progress for a while, a broken hook, a Claude window that closed without saying so, a session whose folder is gone, a project that cannot be read |
| Working | Claude is working on it, and nothing above holds |
| Idle | Every other live session: waiting for your next message, paused, merged and ready to clean up |

A card in Needs you or Problems shows its project, how long the condition
has lasted, the session's title, its state in plain words and, when there is
one, a button for its next step ("Re-run tests…", "Merge…", "Resume…") next
to Details. A card waiting for your permission or answer has no button: tap
it for its details, and answer the prompt in Claude on your Mac or phone. The
page never answers a prompt. Working and idle sessions are one-line cards;
tap one for its details.

**A session's details** say what is going on in a sentence or two, the
suggested next step with its button and a Copy button, "Recent" (the
session's dated events and your own actions on it, newest first), and
"Technical details", closed until you open it: the session id, its state and
lane, its folder, and each Claude window's id and process id. Back, or the
phone's back gesture, returns to where you were in the list.

**Leftovers** are the Claude windows with no session and the left-over
folders that need nothing from you. The tile or the card at the bottom of
the list opens them. The first three of each kind show, then "Show N more".
For now they are listed only; closing and removing them comes later.

**Running a step.** The button opens a sheet from the bottom. It asks the
question ("Re-run the tests?"), says in one sentence what happens and what
it does not touch, and shows the command, with Copy, and where it runs in
plain words. "Exact folder and environment" opens to show the exact folder
and each variable it adds. Nothing runs until you press the button named for
the deed ("Run tests", "Merge", "Open window"); Cancel is selected when the
sheet opens. The confirm lasts two minutes and counts down; once it expires,
"Look again" asks the Mac afresh. The result appears in the same sheet:
"Done in N s", or "Failed, exit N", with the command's output behind "Show
full output". A step that resumes a session opens a Terminal window on the
Mac, whether or not anyone is at it, and you can pick that session up from
Claude Code's Remote Control on the phone. Every action is logged on the Mac
in `~/.invariant/web/actions.log`, and `oracle status` shows the last one.

**Copy** puts the exact command on the clipboard, with any variable it needs
in front, so you can run it in a terminal of your own. Copying runs nothing.
Over plain HTTP the phone does not allow the clipboard, so the command is
shown and selected for you to copy.

## Troubleshooting

Start with `invariant oracle status` and `invariant doctor`. When the page's
agent is installed, `doctor` has rows for it: `oracle web interpreter`,
`oracle web page`, and `oracle web certificate` once the agent has reported
on its certificate. The agent's log is `~/.invariant/oracle/web.log`.

| What you see | Why, and what to do |
|---|---|
| `web page  waiting for a Tailscale address` | Tailscale is not connected on the Mac. Connect it, and the page binds within about 10 seconds. |
| `web page  waiting: cannot listen on … port 7341: …` | Another program holds the port, or a hand-run `invariant oracle web` does. Stop it, or reinstall on another port: `invariant oracle install --web --port 7342`. The agent retries every 10 seconds. |
| `web page  NOT running` | The process that served the page is gone. If `web agent` says `NOT loaded`, run `invariant oracle install --web` again. Otherwise read the log. |
| `certificate  NONE: serving plain http; … HTTPS Certificates look off …` | HTTPS Certificates are not enabled for your tailnet. Enable them on the DNS page of the Tailscale admin console (step 2). The agent asks again every 10 minutes, so the page moves to https by itself; `invariant oracle install --web` restarts it at once. |
| `certificate  NONE: … did not print JSON; it said: The Tailscale GUI failed to start …` | Tailscale's command-line tool started its GUI instead of answering, which it does when `TERM` is missing. invariant gives it `TERM=dumb` whenever there is none, so you should not see this. If you do, either the agent is still running an invariant from before that was added (upgrade it, then run `invariant oracle install --web` to restart the agent), or the tool does not accept `TERM=dumb` either: file a bug with the line from `invariant oracle status`. |
| `certificate  NONE: serving plain http; …` with another reason | Tailscale did not answer, or refused. The reason is Tailscale's own. Check that Tailscale is connected, then wait 10 minutes or reinstall the agent as above. Meanwhile the page works over plain HTTP, with a red warning at its top. |
| `certificate  … last renewal failed: …` | The certificate is still valid and still served; the agent keeps asking every hour. `doctor` fails once fewer than 14 days remain. Fix what the reason says. |
| The phone warns that the certificate does not match | You opened the `100.x.y.z` address. Open the `*.ts.net` name instead, or the link `pair` prints. |
| `web python  MISSING` | The interpreter in the plist was moved or reinstalled, for example by `uv tool upgrade`. Run `invariant oracle install --web` to point the agent at the current one. |
| The phone times out, but the Mac says serving | Allow Local Network for Python (see above). Check that Tailscale is connected on the phone, and that the address on the phone matches the one `status` prints. |
| The phone shows "not paired" | The device was revoked, its cookie was cleared, or it is a different browser. Pair it again. |
| The home-screen app shows "not paired" | It may not share Safari's pairing. Pair it from inside the app: run `pair` on the Mac and tap "Enter a pairing code" (step 6). If it was revoked while open, swipe it away in the app switcher and open it again to reach that page. |
| The home-screen icon is a picture of the page or a letter, not four tiles | iOS fetched the icon without the pairing cookie, so it got nothing. The app works the same. See "What has been checked" in step 6. |
| "not this server" (421) | Over HTTPS, the browser named something other than the Mac's `*.ts.net` name, such as its address; the page names the right URL. Over plain HTTP, it was not the Mac's tailnet address or its name. Use the link `pair` prints. |
| A step shows "The server restarted and no longer knows this action's result" | The agent restarted while the step ran. What ran, and how it ended, if it got that far, is in `~/.invariant/web/actions.log`. |
| The pairing link names the wrong port | `pair` uses the port the page reports, else the installed one. Pass `--port` to override it. |

## Remove it

```bash
invariant oracle uninstall --web      # the page only; the watcher stays
invariant oracle uninstall            # both agents
```

Both remove the plist and the page's status file. Paired devices and the
action log are records, so they are kept: re-installing brings the same
devices back. The certificate is kept too, in `~/.invariant/web/tls/`. To
forget all of them as well, delete `~/.invariant/web/`.
