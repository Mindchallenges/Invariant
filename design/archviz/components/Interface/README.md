# Interface

Interfaces are elements, drawn as a 16px lollipop (`iface`) or a square port (`iface` with `kind: "port"`), labelled underneath in mono.

**Consumer provides:** `label` (the interface name), optional `tech` (the protocol: `gRPC/TLS`, `CAN-FD`), position.

- Wire the provider with `provides` (plain solid line, no head) and the consumer with `requires` (dashed line ending in a tee, the socket).
- Use the square port for hardware connectors and AUTOSAR ports; the circle for software interfaces.
- Interfaces take the `accent` role so they stand out from components (`secondary`) in the same view.
- Don't: draw an interface as a box. Don't label the provides/requires lines; the notation says it.
