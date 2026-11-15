# Sequence

Lays out a UML sequence diagram on a fixed grid: participants as heads, lifelines, ordered messages and loop/alt fragments.

**Consumer provides:** `participants[]` (`id`, `label`, `type` — the type picks the head colour) and `messages[]` (`from`, `to`, `label`, `kind`: `sync` | `async` | `return`, optional `fragment`). Returns `{svg, w, h}`; wrap it with `Archviz.svg`.

- Spacing comes from `seq-col` (lifelines) and `seq-row` (messages).
- Consecutive messages with the same `fragment` text share one frame; write it `loop [condition]`, `alt [condition]`, `opt [condition]`.
- A self-message loops right of its lifeline with its label beside it.
- Don't: number messages in the label; order comes from the model.
