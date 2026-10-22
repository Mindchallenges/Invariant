---
id: IDEA_026
type: idea
title: Session Oracle web page over HTTPS with a Let's Encrypt certificate from Tailscale
status: done
residue: []
history:
- from: proposed
  to: accepted
  reason: Designed in ADR-008 D19-D24
  forced: false
- from: accepted
  to: in_progress
  reason: null
  forced: false
- from: in_progress
  to: done
  reason: Shipped in the IDEA_026 commit
  forced: false
priority: high
effort: M
tier: '2'
area: oracle/web
---

**The want.** The Session Oracle's web page (IDEA_023) is served over HTTPS,
with a Let's Encrypt certificate that Tailscale issues for the Mac's MagicDNS
name (`<host>.<tailnet>.ts.net`). Today the page is plain HTTP over the
tailnet. WireGuard already encrypts the transport, but the phone's browser
marks the page as not secure, and the pairing cookie `invariant_oracle` is
`HttpOnly` and `SameSite=Strict` but not `Secure`. ADR-008 D3 deferred HTTPS
to a later decision; this is that decision. The user asked for it on
2026-10-22: "You should also set it up an SSL key using Let's encrypt."

**Settled with the user (2026-10-22):**

* **The certificate comes from Tailscale.** Tailscale's cert command issues a
  Let's Encrypt certificate for the machine's MagicDNS name. MagicDNS is
  already on. The user must enable "HTTPS Certificates" on the DNS page of the
  Tailscale admin console. That is a manual step, and it goes in the guide
  ([The oracle on your phone](../guide/oracle-web.md)).
* **The oracle's own listener serves TLS.** It keeps binding only the Mac's
  tailnet addresses (ADR-008 D1, D2).
* **Rejected: `tailscale serve` as a TLS front.** It would proxy from
  loopback, which weakens the tailnet-only bind rule and the `Host` and
  `Origin` checks. (ADR-008 already lists it under "Alternatives rejected" as
  open for later; this closes it.)
* **Renewal.** Certificates last 90 days. The page's LaunchAgent
  (`dev.invariant.oracle.web`) renews ahead of expiry and loads the new
  certificate without a restart.
* **HTTPS only.** Once HTTPS works, plain HTTP is no longer served, and the
  cookie gains `Secure`.
* **Checks.** The `Host` and `Origin` checks accept the `https` scheme and the
  ts.net name. The pairing link printed by `oracle web pair` uses the https
  name.
* **Visibility.** `oracle status` and `doctor` show the certificate's expiry.
* **Missing certificate.** If no certificate can be had (HTTPS Certificates
  not enabled in the console), the page either refuses clearly or keeps HTTP
  with a visible warning. The architect decides which and records it in the
  ADR.
* **Trade-off, told to the user.** Certificate Transparency logs publicly
  record the machine's ts.net name. The name is unreachable off the tailnet,
  and no session data is exposed. The ADR records this.
* **Tests never run tailscale.** Certificate issuance goes through a seam,
  like discovery and the server today (ADR-008 D7; the `conftest.py` program
  guard already refuses `tailscale`).

**Acceptance:**

* With HTTPS Certificates enabled for the tailnet, the phone opens the page at its
  https address on the ts.net name with no certificate warning, and
  the browser reports the connection as secure.
* A plain-HTTP request to the listener gets no page once HTTPS is serving.
* After pairing, the `invariant_oracle` cookie carries `Secure`, `HttpOnly`
  and `SameSite=Strict`, and the pairing form, confirms and actions all work
  over https (the `Origin` check accepts the https origin of the ts.net name).
* `oracle web pair` prints an https link on the ts.net name.
* `oracle status` and `doctor` show when the certificate expires. A
  certificate within its renewal window is renewed by the running agent, and
  the next connection is served the new certificate with no restart.
* With no certificate available, the outcome the ADR chose (a clear refusal,
  or HTTP with a visible warning) happens, and `status` and `doctor` say why.
* The suite issues no certificate and runs no `tailscale` command.

**Architecture sketch:**

* A certificate seam next to `_oracle_web_addresses` and
  `_oracle_web_server`: issue or fetch the certificate and key for the
  MagicDNS name into a private file under the oracle home (born owner-only,
  ADR-008 D14). The suite replaces it with a recorder and a test certificate.
* `web.WebServer` wraps each tailnet listener in an `ssl.SSLContext`. Reload
  without a restart means swapping the context's certificate chain (or the
  context) for new connections.
* `webservice.WebService`'s loop already wakes every 30 s while serving. It
  is the natural place to check expiry and renew ahead of it, and to record
  the expiry in `oracle/web.json` for `web_status`.
* `web` `Host` and `Origin` rules: add the https scheme and the ts.net name;
  the cookie gains `Secure`.
* A new ADR-008 decision (or a successor ADR) replaces D3's "Not `Secure`"
  note: the certificate's source, the HTTPS-only rule, renewal, the
  missing-certificate behaviour and the Certificate Transparency trade-off.
* Guide: the admin-console step, and what the expiry line in `status` means.

**Depends on:** BUG_079 (the page refuses its own pairing form and actions
because browsers send `Origin: null`). It is being fixed first, and this
idea's `Origin` change builds on that fix.

**Related:** IDEA_023 (the web page this hardens), ADR-008 (D1, D2, D3, D7,
D14, D15, D16, D17).

**Risks / open questions:**

* Missing certificate: refuse, or HTTP with a warning? The architect decides
  and records it.
* Whether the agent can run Tailscale's cert command under launchd: the CLI
  may need privileges or the GUI app's helper on macOS, and the issued files'
  location and permissions differ by install. The seam must report a failure
  clearly rather than serve nothing silently.
* Hot reload: `ssl.SSLContext.load_cert_chain` on a live context affects new
  handshakes only; confirm that is enough, or swap the context per listener.
* The certificate names the ts.net host, not the 100.x address, so the page
  and pairing link must use the name; a phone reaching the Mac by address
  would get a name mismatch.
* Certificate Transparency publishes the machine's ts.net name (recorded,
  accepted by the user).

**Why now / why this tier:**

It makes the phone page read as secure and puts `Secure` on the pairing
cookie, closing the gap ADR-008 D3 left open, right after IDEA_023 shipped and
BUG_079 makes the page usable. Value medium-high for every phone use of the
page; effort M, within the oracle's web layer plus one seam. Tier 2.
