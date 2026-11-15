# StateFlow

State machines and flowcharts reuse ElementNode with behaviour kinds: `state` (`initial`, `final`, `choice`) and `step` (`start`, `end`, `decision`, `merge`, `io`, `fork`, `join`).

**Consumer provides:** the same props as ElementNode; transitions are `next` relations, labelled with `event [guard] / action` or a decision branch (`yes`, `no`).

- Initial and final pseudo-states are solid `ink` marks without labels.
- Decisions are diamonds; every outgoing branch carries a label.
- Swimlanes are `lane` containers placed side by side in lane order.
