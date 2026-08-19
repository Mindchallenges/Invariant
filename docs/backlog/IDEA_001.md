---
id: IDEA_001
type: idea
title: The links pattern's TLD-like suffix rule drops a punycode IDN top-level domain
status: proposed
residue: []
history: []
area: core/privacy
severity: low
priority: low
---

Found in review of BUG_044, not covered by its tests.

The scheme arm's host test ends in `\.[A-Za-z]{2,}` followed by `(?![A-Za-z0-9_-])`. A punycode internationalised top-level domain is alphanumeric and ends in a digit or a digit-bearing label, so the suffix rule cannot consume it and the end-of-host lookahead then rejects the partial match. A genuine web address in that form is therefore no longer a finding, where the pre-BUG_044 pattern did flag it.

Measured: an address whose authority is a two-label name under a punycode top-level domain is not flagged by the scheme arm. The schemeless `www.` arm still flags the `www.`-prefixed spelling of the same address, because that arm has no end-of-host lookahead, so the gap is confined to the scheme arm.

Narrow, and strictly smaller than the false-positive flood BUG_044 removed, so it was not treated as a blocker on that change. A fix is a suffix alternative for the punycode form, plus a corpus member in the host-authority table of TestLinksNameANetworkHost so the class stays crossed against every scheme.
