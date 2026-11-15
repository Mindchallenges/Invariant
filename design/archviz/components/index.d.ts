/** Archviz — diagram primitives. All functions return markup strings (SVG or HTML). */
type Role = "primary" | "secondary" | "tertiary" | "accent" | "neutral" | "critical";
type NodeType = "actor" | "system" | "extsys" | "usecase" | "hw" | "bus" | "cloud" | "node" | "net" | "platform" |
  "app" | "store" | "queue" | "comp" | "iface" | "data" | "cls" | "state" | "step" | "scenario" | "req" | "adr";
type LinkType = "part_of" | "deployed_on" | "runs_on" | "connects" | "uses" | "flows_to" | "provides" | "requires" |
  "realizes" | "inherits" | "composes" | "aggregates" | "associates" | "next" | "satisfies" | "decides" |
  "msg.sync" | "msg.async" | "msg.return" | "lifeline";

export interface ElementNodeProps {
  /** Element type; sets shape and role. */ type: NodeType;
  /** Element name. */ label?: string;
  /** Technology line, shown as [tech] in mono. */ tech?: string;
  /** UML stereotype, shown as «stereotype». */ stereotype?: string;
  /** Sub-kind that changes the shape, e.g. "decision", "initial", "soc", "port". */ kind?: string;
  /** Status overlay. */ status?: "proposed" | "draft" | "risk" | "open" | "deprecated";
  /** Override the type's role. */ role?: Role;
  /** Neighbour pulled in by expansion. */ ghost?: boolean;
  /** Element under focus. */ selected?: boolean;
  x?: number; y?: number; w?: number; h?: number;
}
export type InterfaceProps = ElementNodeProps;
export type StateFlowProps = ElementNodeProps;
export type StatusProps = ElementNodeProps;
export interface RelationProps {
  link: LinkType; points?: [number, number][]; d?: string;
  label?: string; labelAt?: [number, number]; anchor?: "start" | "middle" | "end"; highlight?: boolean;
}
export interface ContainerProps {
  kind: "boundary" | "zone" | "group" | "band" | "lane" | "fragment"; role?: Role;
  title: string; tech?: string; x: number; y: number; w: number; h: number;
}
export interface UmlClassProps {
  label: string; stereotype?: string; abstract?: boolean;
  attributes?: string[]; operations?: string[]; role?: Role; x?: number; y?: number; w?: number;
}
export interface SequenceProps {
  participants: { id: string; label: string; type: NodeType }[];
  messages: { from: string; to: string; label: string; kind?: "sync" | "async" | "return"; fragment?: string }[];
  col?: number; row?: number;
}
export interface LegendProps { types?: NodeType[]; links?: LinkType[]; }
export interface DiagramFrameProps { title?: string; caption?: string; body: string; tip?: string; legend?: string; toolbar?: boolean; }
