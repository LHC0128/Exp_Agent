import type { components } from "../../types/openapi";

type Schemas = components["schemas"];

export type WiringEndpoint = Schemas["WiringEndpoint"];

// 后端对连线使用自定义序列化（省略未填写的线缆字段），生成的输出模型会退化为自由字典；
// 前端统一使用字段明确的图模型，不依赖生成器的 Input/Output 拆分结果。
export type WiringEdge = {
  id: string;
  source: WiringEndpoint;
  target: WiringEndpoint;
  signal_type: "optical" | "electrical" | "internal";
  label: string;
  connector?: string | null;
  cable_id?: string | null;
  note?: string | null;
};

export type WiringGraph = { nodes: Schemas["WiringNode"][]; edges: WiringEdge[] };

export type WiringDocument = Omit<Schemas["WiringResponse"], "site" | "targets"> & {
  site: WiringGraph;
  targets: Record<string, WiringGraph>;
};

export type WiringComparison = Omit<Schemas["WiringComparison"],
  "retained" | "removed" | "added" | "internal_retained" | "internal_removed" | "internal_added"> & {
  retained: WiringEdge[];
  removed: WiringEdge[];
  added: WiringEdge[];
  internal_retained?: WiringEdge[];
  internal_removed?: WiringEdge[];
  internal_added?: WiringEdge[];
};
