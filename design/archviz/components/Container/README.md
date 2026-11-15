# Container

Draws a box that holds other elements: a system boundary, a cloud zone, a hardware group, a layer band, a swimlane or a sequence fragment.

**Consumer provides:** `kind` (`boundary`, `zone`, `group`, `band`, `lane`, `fragment`), `role`, `title`, optional `tech`, and `x`, `y`, `w`, `h` — size it around its children with `space-4` padding and 40px at the top for the title.

| Kind | Used for | Look |
| --- | --- | --- |
| boundary | system / application boundary (C4) | dashed role stroke, `-group` fill |
| zone | cloud region, VPC, cluster | dashed, `radius-zone` |
| group | board, node, platform holding children | solid, `radius-group` |
| band | one layer in the layered view | hairline dashed, upper-case title |
| lane | swimlane column in a flow | `grid` border, `neutral-group` fill, centred title |
| fragment | sequence loop / alt / opt | accent hairline, solid accent tag |

- Titles sit inside, top-left, in `group-label` and the role's `-ink` token (4.6:1 or better in both themes).
- Nest at most three containers deep; deeper hierarchies get their own view.
