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
- A failed action says why in one sentence, with the full output behind
  "Show full output" ("Running a step" below).
- A leftover Claude window can be closed from the Leftovers screen, with
  its Terminal tab, after a confirm that names it ("Closing a leftover Claude
  window" below).
- A new Claude session can be started on the Mac, with Remote Control on, in
  any registered project ("Start a new session" below).

Still to come:

- Cleaning up stranded sessions and left-over folders, each confirmed first.
  Each lists what it deletes and checks again that nothing is using it when
  you press the button.

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

**If the app opens on "not paired".** On an iPhone the app added to the home
screen kept Safari's pairing when this was checked, so it opens on the panel
straight away. If yours does open on "not paired" (another browser or
device, or a pairing that was revoked), pair it from inside the app:

1. On the Mac, run `invariant oracle web pair --name phone-app` (any name;
   it is a second device in `oracle web devices`).
2. In the app, tap "Enter a pairing code", type the code it printed, and tap
   "Pair this device". Do not tap the pairing link in a message: a link
   opens in Safari, not in the app.

The app then opens on the panel, and stays paired as Safari does. Revoking
it is the same as any device (step 5).

**What has been checked.** On an iPhone (2026-10-25) the panel added to the home
screen showed its four-tile icon and opened without asking to pair again.
The manifest, the icons, the full-screen tags, the safe-area padding and
pairing from the "not paired" page are also tested in invariant's suite. The
icon and the manifest are served only to a paired device, like the page.

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
| Needs you | A Claude window waiting for your permission or your answer ("Waiting for you" when only Claude's own status says it is waiting), and finished sessions ready for review or to merge |
| Problems | Failing tests, a check that refused a step, no progress for a while, a broken hook, a Claude window that closed without saying so, a session whose folder is gone, a project that cannot be read |
| Working | Claude is working on it, and nothing above holds. A turn that has gone quiet for an hour, or that Claude itself says is over, is not working |
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
Each Claude window says what it is: "Still open on the Mac" (its session
ended and it has been idle an hour or more), "Open, never had a session",
"Active, no session" (used in the last hour, or mid-turn by Claude's own
status), "Already closed ·
record only", "Process id not recorded", or "Its process ids disagree". A
window that can safely be closed has a "Close…" button. A window gets
"Attach…" instead only when it is the one open window the project's one
session without a window could take; when several windows could all take
that session, each gets "Close…". Removing left-over folders comes later.

**Closing a leftover Claude window.** "Close…" opens a red sheet: "Close this
Claude window?", and "Ends Claude in this window, then closes the Terminal
tab." Under "This ends" it names the project, the window (the first 8
characters of its Claude session id), the process id and how long it has
been idle. "Checked just now" says no invariant session uses the window, and
that this is checked again when you press the button. The exact command is
behind "Exact command". Cancel is first; the red "Close window" acts. You can
reopen the conversation later with `claude --resume` on the Mac.

When you press it, the Mac checks everything again: the process is still
that Claude window (same process, same start, same terminal) and no session
uses it. If anything changed, nothing is done and the sheet says why. Then
it asks Claude to end and waits up to 10 seconds. It never forces Claude: if
Claude does not exit, the sheet says "Claude did not exit; nothing else was
done", and the window stays. Once Claude has ended, the Terminal tab it ran
in is closed when only its shell is left there, with the window when that
was its only tab. Under "What happened" the sheet says how it ended:
"Claude ended, and its Terminal tab closed.", or "Claude ended; the window
was left open because …" (something else runs in the tab, no Terminal tab
matched, or the window is not Terminal's: Remote Control, VS Code, iTerm).
Only a window offered "Close…" can be closed: one whose process id is
unknown or disputed, one used in the last hour, and one another project's
session may be using are left alone.

**Running a step.** The button opens a sheet from the bottom. It asks the
question ("Re-run the tests?"), says in one sentence what happens and what
it does not touch, and shows the command, with Copy, and where it runs in
plain words. "Exact folder and environment" opens to show the exact folder
and each variable it adds. Nothing runs until you press the button named for
the deed ("Run tests", "Merge", "Open window"); Cancel is selected when the
sheet opens. The confirm lasts two minutes and counts down; once it expires,
"Look again" asks the Mac afresh. The result appears in the same sheet:
"Done in N s", or "Failed, exit N", with the command's output behind "Show
full output".

**Why it failed.** A failed step adds a "Why" line above the output. It names
a cause only when invariant itself wrote one, such as "invariant could not
find a value for destination_id." or "invariant said: the merge gates
refused this session — nothing has been merged". Otherwise it says "Failed,
exit N." and the output's last meaningful line, skipping blank lines,
traceback frames and progress bars: for a test run, usually its summary
("1 failed, 3 passed in 0.12s"); for a crash, the exception ("ValueError:
bad config"). A step that timed out or could not start reads "Failed." and
what the Mac said, such as "timed out after 600s". The line never guesses
a cause the output does not state, so when it only quotes a line, open
"Show full output" for the rest. A step that resumes a session opens a Terminal window on the
Mac, whether or not anyone is at it, and you can pick that session up from
Claude Code's Remote Control on the phone. Every action is logged on the Mac
in `~/.invariant/web/actions.log`, and `oracle status` shows the last one.

**Start a new session.** "New session…" at the top of the Sessions screen
opens a sheet for starting a new Claude window on the Mac, with Remote
Control on, so you can pick it up on the phone. Each project also has its own
"New session…" under **Projects** at the bottom of the list, which opens the
same sheet with that project already chosen. Only projects invariant knows
are listed: ones where you ran `invariant init`, started a session, or ran
`invariant oracle projects add`.

The sheet says what happens: "A new Terminal window on the Mac runs Claude
with Remote Control in <project>'s main folder." Under it:

- **Project**, to choose another.
- **Name**, the window's label, set to the time ("1026-0049") until you
  change it. The line under it shows the full name the window gets, the
  project and the label, such as `invariant-1026-0049`: that is the name
  Remote Control lists it by. If a window already uses that name, a `-2` is
  added.
- **First message**, optional. Claude starts with it, as if you had typed it.
- **Checked just now**: how many Claude windows are already open in that
  project, and whether its main folder has uncommitted changes. These are
  for your information; neither stops the start.
- **Exact command and folder**, closed: the `claude` line that will run, with
  Copy, and the folder it runs in.

Each time you change a field, the sheet asks the Mac again, and "Start
session" waits a moment for the answer, so what starts is always what the
sheet shows. If the Mac refuses (a name with no letter or digit, a first
message that starts with `-`, a project that is no longer registered), the
sheet says why in red and keeps what you typed. Nothing runs until you press
"Start session"; Cancel is selected when the sheet opens, and the confirm
lasts two minutes, like any other.

Claude starts in its normal permission mode. The page never offers to skip
permission prompts, and never answers one: each prompt of the new window
reaches you in Remote Control. No invariant session is created in advance;
Claude starts one when the work begins, as in any window.

After you press it, the start shows as a card at the top of **Working**: the
project, the name, and "Starting…". Once the Mac sees the new window (usually
within a few seconds), the card becomes that window's row, under its name;
tap it for its details. If the window has not appeared after a minute, the
card says "Did not start" and why. Every start is logged on the Mac in
`~/.invariant/web/actions.log`, with the line it ran.

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
| The home-screen icon is a picture of the page or a letter, not four tiles | The device was not paired in that browser when you added it, so the icon request got nothing. Pair in the browser first, then remove the icon and add it again. The app works the same either way. |
| "not this server" (421) | Over HTTPS, the browser named something other than the Mac's `*.ts.net` name, such as its address; the page names the right URL. Over plain HTTP, it was not the Mac's tailnet address or its name. Use the link `pair` prints. |
| A failed step's "Why" quotes a line instead of naming a cause | The output did not state a cause in a form invariant wrote, so the page quotes the output's last meaningful line rather than guess. Open "Show full output" and read upward from the end; if the step is one you can run yourself, Copy its command from the step and run it in a terminal on the Mac. |
| "Why" says "invariant could not find a value for …" | The project's manifest discovers that value with a command of its own, and the command printed nothing or failed. Run the step in a terminal on the Mac to see the command's own message, and fix the discovery command or the condition it checks (for example, more than one simulator booted). |
| A step shows "The server restarted and no longer knows this action's result" | The agent restarted while the step ran. What ran, and how it ended, if it got that far, is in `~/.invariant/web/actions.log`. |
| The pairing link names the wrong port | `pair` uses the port the page reports, else the installed one. Pass `--port` to override it. |
| Closing a window says "Claude ended; macOS has not allowed the oracle to control Terminal" | Claude has ended, but macOS did not let the page's agent control Terminal, so the tab stays open; close it by hand this once. The first close from the agent asks for the Automation permission on the Mac, and nobody may have been there to answer (the page waits 30 seconds), or it was refused (error -1743). On the Mac, open System Settings > Privacy & Security > Automation, find the app that runs the oracle (the Python the agent runs, or the terminal you started `oracle web` from), and turn on Terminal under it. If it is not listed, close a window from the page while you are at the Mac and answer the prompt. |
| Closing a window says "nothing was done: …" | Something changed between the confirm and the button: the window closed, its process is another one now, or a session started using it. Nothing was signalled. Refresh the page; the Leftovers screen shows what is there now. |
| "Claude did not exit; nothing else was done" | Claude was asked to end and was still running 10 seconds later. The page never forces it. Look at the window on the Mac: it may be busy with a turn. |
| A started session says "Did not start: The window did not appear on the Mac within a minute" | The Mac opened Terminal, but no Claude window by that name showed up. Look at the Mac: Claude may be waiting on a prompt before it starts, such as "Do you trust the files in this folder?" for a folder it has not opened before, or a login. Answer it there, and the window appears on the page by itself. If no Terminal window opened at all, see the next row. |
| A started session says "Did not start" and "invariant said: the launch did not start a successor" | Terminal did not run the line, or Claude exited at once. The full output has the exact line; run it in a terminal on the Mac to see Claude's own message. The first start from the page's agent may also need the Automation permission for Terminal (see the Automation row above). |
| "invariant said: no claude on PATH to start" | The page's agent cannot find `claude`. Its `PATH` is the one its plist sets, and `install --web` adds the folder `claude` was in when you ran it. Run `invariant oracle install --web` again from a terminal where `claude` works, so the agent gets that folder. |
| A project is not in the New session list | Only registered projects are offered. Run `invariant oracle projects add` in that project on the Mac (or `invariant init` if it has no manifest yet); `invariant oracle projects list` shows what is registered. A project whose folder lost its manifest is refused with a pointer to `invariant oracle projects prune`. |
| "the window would now be named …-2, not …" | Another Claude window took the name between the sheet and the button. Nothing was started; open the sheet again, and it shows the name the window would get now. |
| A leftover window has no "Close…" | It was used in the last hour, Claude says it is working or waiting for you, its process id is not known or two sources disagree on it, it was started outside this project, or another project's session lists it. These are left alone on purpose. A window waiting for you is in Needs you, not the Leftovers. |

## Remove it

```bash
invariant oracle uninstall --web      # the page only; the watcher stays
invariant oracle uninstall            # both agents
```

Both remove the plist and the page's status file. Paired devices and the
action log are records, so they are kept: re-installing brings the same
devices back. The certificate is kept too, in `~/.invariant/web/tls/`. To
forget all of them as well, delete `~/.invariant/web/`.
