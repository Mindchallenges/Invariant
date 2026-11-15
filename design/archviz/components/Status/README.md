# Status

Status overlays the role colour without replacing it: proposed and draft dash the border, risk and open switch to `critical` with a strong border, deprecated fades to 45%.

**Consumer provides:** `status` on any node (`proposed`, `draft`, `risk`, `open`, `deprecated`), `ghost: true` for neighbours pulled in by expansion, `selected: true` for the element under focus (`focus-ring`, 2.5px).

- Status is never shown by colour alone: the border style or opacity changes too, and the tooltip names the status in words.
