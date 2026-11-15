/* @ds-bundle: {"format":4,"namespace":"Archviz","components":[{"name":"ElementNode"},{"name":"Interface"},{"name":"Relation"},{"name":"Container"},{"name":"UmlClass"},{"name":"Sequence"},{"name":"StateFlow"},{"name":"Status"},{"name":"Legend"},{"name":"DiagramFrame"}]} */
(function () {
  "use strict";

  // ---------------------------------------------------------------- metamodel
  var LAYERS = {
    context: "primary", application: "primary", component: "secondary", design: "tertiary",
    behaviour: "primary", platform: "neutral", infrastructure: "secondary", hardware: "tertiary", trace: "neutral"
  };
  var LAYER_TITLES = {
    context: "Context", application: "Applications", component: "Components", design: "Detailed design",
    behaviour: "Behaviour", platform: "Platform", infrastructure: "Cloud & infrastructure", hardware: "Hardware",
    trace: "Requirements & decisions"
  };
  // directive: [title, prefix, layer, shape, roleOverride, border]
  var TYPE_TABLE = [
    ["actor", "Actor", "ACT_", "context", "actor"],
    ["system", "System", "SYS_", "context", "round"],
    ["extsys", "External system", "EXT_", "context", "round", "neutral", "dashed"],
    ["usecase", "Use case", "UC_", "context", "ellipse"],
    ["hw", "Hardware", "HW_", "hardware", "cut"],
    ["bus", "Bus / link", "BUS_", "hardware", "bus"],
    ["cloud", "Cloud / zone", "CLD_", "infrastructure", "zone"],
    ["node", "Compute node", "NODE_", "infrastructure", "rect"],
    ["net", "Network", "NET_", "infrastructure", "bus"],
    ["platform", "Platform", "PLT_", "platform", "rect"],
    ["app", "Application", "APP_", "application", "round"],
    ["store", "Data store", "DB_", "application", "barrel"],
    ["queue", "Queue / topic", "Q_", "application", "rhomboid"],
    ["comp", "Component", "CMP_", "component", "component"],
    ["iface", "Interface", "IF_", "component", "interface", "accent"],
    ["data", "Data object", "DAT_", "component", "tag"],
    ["cls", "Class", "CLS_", "design", "class"],
    ["state", "State", "ST_", "behaviour", "round"],
    ["step", "Flow step", "STP_", "behaviour", "round"],
    ["scenario", "Scenario", "SEQ_", "behaviour", "rect"],
    ["req", "Requirement", "REQ_", "trace", "rect"],
    ["adr", "Decision", "ADR_", "trace", "tag", "accent"]
  ];
  var TYPES = {};
  TYPE_TABLE.forEach(function (t) {
    TYPES[t[0]] = { title: t[1], prefix: t[2], layer: t[3], shape: t[4], role: t[5] || LAYERS[t[3]], border: t[6] || "solid" };
  });
  var KIND_SHAPES = {
    "step.start": "ellipse", "step.end": "ellipse", "step.decision": "diamond", "step.merge": "diamond",
    "step.fork": "bar", "step.join": "bar", "step.io": "rhomboid", "state.initial": "initial", "state.final": "final",
    "state.choice": "diamond", "hw.sensor": "ellipse", "hw.actuator": "hexagon", "hw.soc": "octagon",
    "app.ui": "bottomround", "app.gateway": "hexagon", "iface.port": "port"
  };
  // link: [dash, colourKey, endMarker, startMarker, label]
  var LINK_TABLE = {
    part_of: ["dashed", "line", "none", "none", ""],
    deployed_on: ["dashed", "line", "vee", "none", "deployed on"],
    runs_on: ["dashed", "line", "vee", "none", "runs on"],
    connects: ["strong", "tertiary", "none", "none", ""],
    uses: ["solid", "line", "vee", "none", ""],
    flows_to: ["solid", "primary", "tri", "none", ""],
    provides: ["solid", "line", "none", "none", ""],
    requires: ["dashed", "line", "tee", "none", ""],
    realizes: ["dashed", "line", "trio", "none", ""],
    inherits: ["solid", "line", "trio", "none", ""],
    composes: ["solid", "line", "none", "dia", ""],
    aggregates: ["solid", "line", "none", "diao", ""],
    associates: ["solid", "line", "none", "none", ""],
    next: ["solid", "line", "tri", "none", ""],
    satisfies: ["dotted", "neutral", "vee", "none", "satisfies"],
    decides: ["dotted", "accent", "vee", "none", ""],
    "msg.sync": ["solid", "ink", "tri", "none", ""],
    "msg.async": ["solid", "ink", "vee", "none", ""],
    "msg.return": ["dashed", "ink", "vee", "none", ""],
    lifeline: ["hairdash", "line", "none", "none", ""]
  };
  var LINKS = {};
  Object.keys(LINK_TABLE).forEach(function (k) {
    var l = LINK_TABLE[k];
    LINKS[k] = { dash: l[0], color: l[1], end: l[2], start: l[3], label: l[4] };
  });

  // ---------------------------------------------------------------- helpers
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  var CHAR = 6.6, META_CHAR = 6.1, LINE = 15, PAD = 12, MINW = 110, MINH = 42;

  function measure(lines) {
    var w = 0;
    lines.forEach(function (l) { w = Math.max(w, l.text.length * (l.meta ? META_CHAR : CHAR)); });
    return { w: Math.max(MINW, Math.round(w + 2 * PAD)), h: Math.max(MINH, lines.length * LINE + 2 * 9) };
  }

  function linesOf(o) {
    var out = [];
    if (o.stereotype) out.push({ text: "«" + o.stereotype + "»", meta: true });
    out.push({ text: o.label || "", meta: false });
    if (o.tech) out.push({ text: "[" + o.tech + "]", meta: true });
    return out;
  }

  function textBlock(lines, cx, cy, cls) {
    var y0 = cy - (lines.length * LINE) / 2 + LINE * 0.72;
    return lines.map(function (l, i) {
      return '<text class="' + (l.meta ? "av-meta" : "av-label") + (cls ? " " + cls : "") + '" x="' + cx +
        '" y="' + (y0 + i * LINE).toFixed(1) + '" text-anchor="middle">' + esc(l.text) + "</text>";
    }).join("");
  }

  function shapeSvg(shape, w, h) {
    switch (shape) {
      case "round": return '<rect class="av-shape av-rr" width="' + w + '" height="' + h + '"/>';
      case "rect": return '<rect class="av-shape" rx="1.5" width="' + w + '" height="' + h + '"/>';
      case "bottomround": return '<path class="av-shape" d="M0 0H' + w + "V" + (h - 10) + "Q" + w + " " + h + " " + (w - 10) + " " + h + "H10Q0 " + h + " 0 " + (h - 10) + 'Z"/>';
      case "cut": return '<polygon class="av-shape" points="8,0 ' + (w - 8) + ",0 " + w + ",8 " + w + "," + (h - 8) + " " + (w - 8) + "," + h + " 8," + h + " 0," + (h - 8) + ' 0,8"/>';
      case "barrel": return '<path class="av-shape" d="M0 7C0 -2 ' + w + " -2 " + w + " 7V" + (h - 7) + "C" + w + " " + (h + 2) + " 0 " + (h + 2) + " 0 " + (h - 7) + 'Z"/><path class="av-shape-line" d="M0 7C0 16 ' + w + " 16 " + w + ' 7"/>';
      case "rhomboid": return '<polygon class="av-shape" points="12,0 ' + w + ",0 " + (w - 12) + "," + h + " 0," + h + '"/>';
      case "ellipse": return '<ellipse class="av-shape" cx="' + w / 2 + '" cy="' + h / 2 + '" rx="' + w / 2 + '" ry="' + h / 2 + '"/>';
      case "diamond": return '<polygon class="av-shape" points="' + w / 2 + ",0 " + w + "," + h / 2 + " " + w / 2 + "," + h + " 0," + h / 2 + '"/>';
      case "hexagon": return '<polygon class="av-shape" points="12,0 ' + (w - 12) + ",0 " + w + "," + h / 2 + " " + (w - 12) + "," + h + " 12," + h + " 0," + h / 2 + '"/>';
      case "octagon": return '<polygon class="av-shape" points="10,0 ' + (w - 10) + ",0 " + w + ",10 " + w + "," + (h - 10) + " " + (w - 10) + "," + h + " 10," + h + " 0," + (h - 10) + ' 0,10"/>';
      case "tag": return '<polygon class="av-shape" points="0,0 ' + (w - 12) + ",0 " + w + "," + h / 2 + " " + (w - 12) + "," + h + " 0," + h + '"/>';
      case "component":
        return '<rect class="av-shape av-rr" width="' + w + '" height="' + h + '"/>' +
          '<g class="av-comp-icon" transform="translate(' + (w - 21) + ',6)"><rect x="4" width="12" height="13" rx="1"/><rect y="2.5" width="7" height="3"/><rect y="7.5" width="7" height="3"/></g>';
      default: return '<rect class="av-shape av-rr" width="' + w + '" height="' + h + '"/>';
    }
  }

  // ---------------------------------------------------------------- ElementNode
  /** node({type, label, tech, stereotype, kind, status, x, y, w?, h?, ghost?}) → SVG <g>; x/y = top-left. */
  function node(o) {
    var t = TYPES[o.type] || TYPES.app;
    var shape = KIND_SHAPES[o.type + "." + (o.kind || "")] || t.shape;
    var role = o.role || t.role;
    var cls = "av-node av-r-" + role + " av-t-" + o.type + (t.border === "dashed" ? " av-dashed" : "") +
      (o.status ? " av-s-" + o.status : "") + (o.ghost ? " av-ghost" : "") + (o.selected ? " av-selected" : "");
    var x = o.x || 0, y = o.y || 0, inner;
    if (shape === "actor") {
      inner = '<g class="av-actor" transform="translate(' + ((o.w || 30) / 2 - 15) + ',0)"><circle cx="15" cy="7" r="6"/><path d="M15 13v15M4 19h22M15 28l-9 13M15 28l9 13"/></g>' +
        '<text class="av-label" x="' + (o.w || 30) / 2 + '" y="58" text-anchor="middle">' + esc(o.label) + "</text>";
      return '<g class="' + cls + '" transform="translate(' + x + "," + y + ')">' + inner + "</g>";
    }
    if (shape === "interface" || shape === "port") {
      var s = 16;
      inner = (shape === "port" ? '<rect class="av-iface" width="' + s + '" height="' + s + '"/>' : '<circle class="av-iface" cx="' + s / 2 + '" cy="' + s / 2 + '" r="' + (s / 2 - 1) + '"/>') +
        '<text class="av-meta av-strong" x="' + s / 2 + '" y="' + (s + 14) + '" text-anchor="middle">' + esc(o.label) + "</text>" +
        (o.tech ? '<text class="av-meta" x="' + s / 2 + '" y="' + (s + 27) + '" text-anchor="middle">' + esc(o.tech) + "</text>" : "");
      return '<g class="' + cls + '" transform="translate(' + x + "," + y + ')">' + inner + "</g>";
    }
    if (shape === "initial") return '<g class="' + cls + '" transform="translate(' + x + "," + y + ')"><circle class="av-solid" cx="9" cy="9" r="9"/></g>';
    if (shape === "final") return '<g class="' + cls + '" transform="translate(' + x + "," + y + ')"><circle class="av-ring" cx="10" cy="10" r="9.5"/><circle class="av-solid" cx="10" cy="10" r="5.5"/></g>';
    if (shape === "bar") return '<g class="' + cls + '" transform="translate(' + x + "," + y + ')"><rect class="av-solid" width="' + (o.w || 140) + '" height="6" rx="1"/></g>';
    var lines = linesOf(o);
    var m = measure(lines);
    var w = o.w || m.w, h = o.h || m.h;
    if (shape === "diamond") { w = o.w || Math.round(m.w * 1.35); h = o.h || Math.max(56, Math.round(m.h * 1.4)); }
    if (shape === "ellipse") { w = o.w || Math.round(m.w * 1.15); }
    if (shape === "barrel") { h = o.h || m.h + 8; }
    if (shape === "bus") {
      w = o.w || Math.max(220, m.w * 1.5); h = 18;
      inner = '<rect class="av-bus" width="' + w + '" height="' + h + '" rx="3"/>' +
        '<text class="av-bus-label" x="' + w / 2 + '" y="12.5" text-anchor="middle">' + esc(o.label) + (o.tech ? " · " + esc(o.tech) : "") + "</text>";
      return '<g class="' + cls + '" transform="translate(' + x + "," + y + ')">' + inner + "</g>";
    }
    inner = shapeSvg(shape, w, h) + textBlock(lines, (shape === "component" ? w / 2 - 6 : w / 2), h / 2 + (shape === "barrel" ? 5 : 0));
    return '<g class="' + cls + '" transform="translate(' + x + "," + y + ')" data-w="' + w + '" data-h="' + h + '">' + inner + "</g>";
  }

  /** size(o) → {w, h} that node(o) will use (for layout by hand). */
  function size(o) {
    var t = TYPES[o.type] || TYPES.app;
    var shape = KIND_SHAPES[o.type + "." + (o.kind || "")] || t.shape;
    if (shape === "actor") return { w: 30, h: 62 };
    if (shape === "interface" || shape === "port") return { w: 16, h: 16 };
    var m = measure(linesOf(o));
    if (shape === "diamond") return { w: Math.round(m.w * 1.35), h: Math.max(56, Math.round(m.h * 1.4)) };
    if (shape === "ellipse") return { w: Math.round(m.w * 1.15), h: m.h };
    if (shape === "barrel") return { w: m.w, h: m.h + 8 };
    if (shape === "bus") return { w: Math.max(220, m.w * 1.5), h: 18 };
    return m;
  }

  // ---------------------------------------------------------------- UmlClass
  /** umlClass({label, stereotype, attributes:[], operations:[], x, y, w?}) */
  function umlClass(o) {
    var attrs = o.attributes || [], ops = o.operations || [];
    var head = (o.stereotype ? 2 : 1);
    var longest = Math.max((o.label || "").length * 7.2, Math.max.apply(null, [0].concat(attrs.concat(ops).map(function (s) { return s.length * 6.2; }))));
    var w = o.w || Math.max(150, Math.round(longest + 24));
    var hh = head * 15 + 14, ha = Math.max(1, attrs.length) * 14 + 10, ho = Math.max(1, ops.length) * 14 + 10;
    var s = '<g class="av-node av-r-' + (o.role || "tertiary") + ' av-t-cls" transform="translate(' + (o.x || 0) + "," + (o.y || 0) + ')">' +
      '<rect class="av-shape" rx="2" width="' + w + '" height="' + (hh + ha + ho) + '"/>' +
      '<line class="av-shape-line" x1="0" x2="' + w + '" y1="' + hh + '" y2="' + hh + '"/>' +
      '<line class="av-shape-line" x1="0" x2="' + w + '" y1="' + (hh + ha) + '" y2="' + (hh + ha) + '"/>';
    var y = 18;
    if (o.stereotype) { s += '<text class="av-meta" x="' + w / 2 + '" y="' + y + '" text-anchor="middle">«' + esc(o.stereotype) + "»</text>"; y += 15; }
    s += '<text class="av-label av-strong' + (o.abstract ? " av-italic" : "") + '" x="' + w / 2 + '" y="' + y + '" text-anchor="middle">' + esc(o.label) + "</text>";
    attrs.forEach(function (a, i) { s += '<text class="av-code" x="10" y="' + (hh + 17 + i * 14) + '">' + esc(a) + "</text>"; });
    ops.forEach(function (a, i) { s += '<text class="av-code" x="10" y="' + (hh + ha + 17 + i * 14) + '">' + esc(a) + "</text>"; });
    return s + "</g>";
  }

  // ---------------------------------------------------------------- Container
  /** container({kind:'boundary'|'group'|'zone'|'band'|'lane'|'fragment', role, title, tech, x, y, w, h}) */
  function container(o) {
    var kind = o.kind || "group", role = o.role || "neutral";
    var x = o.x || 0, y = o.y || 0, w = o.w, h = o.h;
    var s = '<g class="av-container av-c-' + kind + " av-r-" + role + '" transform="translate(' + x + "," + y + ')">';
    if (kind === "fragment") {
      var tw = Math.max(70, (o.title || "").length * 6.4 + 16);
      return s + '<rect class="av-frame" width="' + w + '" height="' + h + '"/><path class="av-frag-tag" d="M0 0H' + tw + "V12L" + (tw - 8) + ' 20H0Z"/>' +
        '<text class="av-frag-label" x="7" y="14">' + esc(o.title) + "</text></g>";
    }
    s += '<rect class="av-frame" width="' + w + '" height="' + h + '"/>';
    if (kind === "lane") {
      s += '<text class="av-group-label av-lane-label" x="' + w / 2 + '" y="20" text-anchor="middle">' + esc(o.title) + "</text>";
    } else if (kind === "band") {
      s += '<text class="av-group-label" x="14" y="21">' + esc(o.title).toUpperCase() + "</text>";
    } else {
      s += '<text class="av-group-label" x="12" y="20">' + esc(o.title) + "</text>";
      if (o.tech) s += '<text class="av-meta" x="12" y="33">[' + esc(o.tech) + "]</text>";
    }
    return s + "</g>";
  }

  // ---------------------------------------------------------------- Relation
  var MARKERS = ["vee", "tri", "trio", "tee", "dia", "diao"];
  var COLORS = ["line", "ink", "primary", "tertiary", "neutral", "accent"];
  /** defs() → <defs> with every arrowhead in every relation colour. Put once in each <svg>. */
  function defs() {
    var d = "<defs>";
    COLORS.forEach(function (c) {
      d += '<marker id="av-vee-' + c + '" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="av-mk-stroke av-c-' + c + '" d="M1 1L9 5L1 9"/></marker>';
      d += '<marker id="av-tri-' + c + '" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="av-mk-fill av-c-' + c + '" d="M0 0L10 5L0 10Z"/></marker>';
      d += '<marker id="av-trio-' + c + '" viewBox="0 0 12 12" refX="11" refY="6" markerWidth="10" markerHeight="10" orient="auto-start-reverse"><path class="av-mk-hollow av-c-' + c + '" d="M1 1L11 6L1 11Z"/></marker>';
      d += '<marker id="av-tee-' + c + '" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path class="av-mk-stroke av-c-' + c + '" d="M9 0V10"/></marker>';
      d += '<marker id="av-dia-' + c + '" viewBox="0 0 14 8" refX="1" refY="4" markerWidth="12" markerHeight="8" orient="auto-start-reverse"><path class="av-mk-fill av-c-' + c + '" d="M1 4L7 1L13 4L7 7Z"/></marker>';
      d += '<marker id="av-diao-' + c + '" viewBox="0 0 14 8" refX="1" refY="4" markerWidth="12" markerHeight="8" orient="auto-start-reverse"><path class="av-mk-hollow av-c-' + c + '" d="M1 4L7 1L13 4L7 7Z"/></marker>';
    });
    return d + "</defs>";
  }

  /** relation({link, points:[[x,y],…] | d, label?, labelAt:[x,y]?, highlight?}) → SVG path (+ label). */
  function relation(o) {
    var l = LINKS[o.link] || LINKS.uses;
    var color = o.highlight ? "accent" : l.color;
    var d = o.d || ("M" + o.points.map(function (p) { return p[0] + " " + p[1]; }).join("L"));
    var s = '<path class="av-edge av-d-' + l.dash + " av-c-" + color + '" d="' + d + '"' +
      (l.end !== "none" ? ' marker-end="url(#av-' + l.end + "-" + color + ')"' : "") +
      (l.start !== "none" ? ' marker-start="url(#av-' + l.start + "-" + color + ')"' : "") + "/>";
    var label = o.label != null ? o.label : l.label;
    if (label && o.labelAt) {
      s += '<text class="av-edge-label" x="' + o.labelAt[0] + '" y="' + o.labelAt[1] + '" text-anchor="' + (o.anchor || "middle") + '">' + esc(label) + "</text>";
    }
    return s;
  }

  // ---------------------------------------------------------------- Sequence
  /** sequence({participants:[{id,label,type}], messages:[{from,to,label,kind,fragment}], col?, row?}) → {svg, w, h} */
  function sequence(o) {
    var col = o.col || 170, row = o.row || 44, head = 44, x0 = 20;
    var parts = o.participants, msgs = o.messages;
    var xs = {};
    parts.forEach(function (p, i) { xs[p.id] = x0 + 70 + i * col; });
    var bottom = head + 26 + (msgs.length + 0.6) * row;
    var s = "";
    // fragments first (behind)
    var frags = [];
    msgs.forEach(function (m, i) {
      var last = frags[frags.length - 1];
      if (m.fragment && last && last.title === m.fragment && last.end === i - 1) last.end = i;
      else if (m.fragment) frags.push({ title: m.fragment, start: i, end: i });
    });
    frags.forEach(function (f) {
      var ms = msgs.slice(f.start, f.end + 1);
      var fx = Math.min.apply(null, ms.map(function (m) { return Math.min(xs[m.from], xs[m.to]); })) - 28;
      var fx2 = Math.max.apply(null, ms.map(function (m) { return Math.max(xs[m.from], xs[m.to]) + (m.from === m.to ? 120 : 0); })) + 28;
      var fy = head + 26 + (f.start + 0.6) * row - 38, fy2 = head + 26 + (f.end + 0.6) * row + (ms[ms.length - 1].from === ms[ms.length - 1].to ? 30 : 14);
      s += container({ kind: "fragment", title: f.title, x: fx, y: fy, w: fx2 - fx, h: fy2 - fy });
    });
    parts.forEach(function (p) {
      var x = xs[p.id];
      s += relation({ link: "lifeline", points: [[x, head], [x, bottom]] });
      var t = TYPES[p.type] || TYPES.app, w = Math.min(col - 18, Math.max(110, p.label.length * 6.8 + 22));
      s += '<g class="av-node av-r-' + t.role + (t.border === "dashed" ? " av-dashed" : "") + '" transform="translate(' + (x - w / 2) + ',8)"><rect class="av-shape av-rr" width="' + w + '" height="36"/>' +
        '<text class="av-label" x="' + w / 2 + '" y="22.5" text-anchor="middle">' + esc(p.label) + "</text></g>";
    });
    msgs.forEach(function (m, i) {
      var y = head + 26 + (i + 0.6) * row, a = xs[m.from], b = xs[m.to];
      var link = "msg." + (m.kind || "sync");
      if (m.from === m.to) {
        s += relation({ link: link, points: [[a, y - 8], [a + 40, y - 8], [a + 40, y + 10], [a + 3, y + 10]] });
        s += '<text class="av-edge-label av-ink" x="' + (a + 48) + '" y="' + (y + 5) + '">' + esc(m.label) + "</text>";
      } else {
        var dir = b > a ? 1 : -1;
        s += relation({ link: link, points: [[a, y], [b - dir * 1, y]] });
        s += '<text class="av-edge-label av-ink" x="' + (a + b) / 2 + '" y="' + (y - 6) + '" text-anchor="middle">' + esc(m.label) + "</text>";
      }
    });
    var w = x0 + 70 + (parts.length - 1) * col + 90;
    return { svg: s, w: w, h: bottom + 12 };
  }

  // ---------------------------------------------------------------- Legend / frame
  var LINK_NAMES = {
    part_of: "part of", deployed_on: "deployed on", runs_on: "runs on", connects: "connects", uses: "uses",
    flows_to: "flows to", provides: "provides", requires: "requires", realizes: "realizes", inherits: "inherits",
    composes: "composes", aggregates: "aggregates", associates: "associates", next: "next / transition",
    satisfies: "satisfies", decides: "decides", "msg.sync": "sync call", "msg.async": "async message", "msg.return": "return"
  };
  /** legend({types:[…], links:[…]}) → HTML string */
  function legend(o) {
    var html = '<div class="av-legend">';
    (o.types || []).forEach(function (t) {
      var tt = TYPES[t];
      html += '<span class="av-leg-item"><span class="av-leg-sw av-r-' + tt.role + (tt.border === "dashed" ? " av-dashed" : "") + '"></span>' + esc(tt.title) + "</span>";
    });
    (o.links || []).forEach(function (k) {
      html += '<span class="av-leg-item"><svg width="34" height="10" viewBox="0 0 34 10">' + defs() +
        relation({ link: k, points: [[3, 5], [30, 5]] }) + "</svg>" + esc(LINK_NAMES[k] || k) + "</span>";
    });
    return html + "</div>";
  }

  /** svg(w, h, inner, title?) → a complete <svg> with markers. */
  function svg(w, h, inner, title) {
    return '<svg class="av-svg" viewBox="0 0 ' + w + " " + h + '" width="100%" role="img"' + (title ? ' aria-label="' + esc(title) + '"' : "") + ">" + defs() + inner + "</svg>";
  }

  /** frame({title, caption?, body(svg string), legend?(html), toolbar?:bool}) → HTML string */
  function frame(o) {
    var tb = o.toolbar === false ? "" : '<div class="av-toolbar"><input class="av-search" placeholder="Search…" aria-label="Search elements">' +
      ["Fit", "Re-layout", "Legend", "PNG", "⤢"].map(function (b) { return '<button class="av-btn" type="button">' + b + "</button>"; }).join("") + "</div>";
    return '<figure class="av-figure"><div class="av-frame-box">' + tb + '<div class="av-stage">' +
      (o.title ? '<div class="av-title">' + esc(o.title) + "</div>" : "") + o.body + (o.tip || "") + "</div>" + (o.legend || "") + "</div>" +
      (o.caption ? '<figcaption class="av-caption">' + esc(o.caption) + "</figcaption>" : "") + "</figure>";
  }

  /** tooltip({title, id, type, layer, tech, summary}) → HTML string (place inside .av-stage) */
  function tooltip(o) {
    var t = TYPES[o.type] || {};
    return '<div class="av-tip"><div class="av-tip-title">' + esc(o.title) + '</div><div class="av-tip-meta"><code>' + esc(o.id) + "</code> · " +
      esc(t.title || o.type) + " · " + esc(LAYER_TITLES[o.layer || t.layer] || "") + (o.tech ? " · " + esc(o.tech) : "") + "</div>" +
      (o.summary ? '<div class="av-tip-body">' + esc(o.summary) + "</div>" : "") + '<a class="av-tip-link" href="#">Open in docs →</a></div>';
  }

  window.Archviz = {
    TYPES: TYPES, LAYERS: LAYERS, LAYER_TITLES: LAYER_TITLES, KIND_SHAPES: KIND_SHAPES, LINKS: LINKS,
    node: node, size: size, umlClass: umlClass, container: container, relation: relation, defs: defs,
    sequence: sequence, legend: legend, svg: svg, frame: frame, tooltip: tooltip,
    // component names, for the catalogue
    ElementNode: node, Interface: node, Relation: relation, Container: container, UmlClass: umlClass,
    Sequence: sequence, StateFlow: node, Status: node, Legend: legend, DiagramFrame: frame
  };
})();
