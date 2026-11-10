import { type WiringGraph as Graph } from "./wiringTypes";

type Node = Graph["nodes"][number];
export type PortUsage = "used" | "unused";

/** 端口身份用一个不含歧义的分隔符拼接，避免 JSON 序列化在每次查找时分配。 */
export const endpointKey = (deviceId: string, portId: string) => `${deviceId}\u0000${portId}`;

/** 一次扫描生成连线端点和已用端口索引；有线优先于旧的缺省或矛盾记录。 */
export function portUsageSets(graph: Graph): { connected: Set<string>; used: Set<string> } {
  const connected = new Set<string>();
  const used = new Set<string>();
  for (const edge of graph.edges) for (const endpoint of [edge.source, edge.target]) {
    const key = endpointKey(endpoint.device_id, endpoint.port_id);
    connected.add(key); used.add(key);
  }
  for (const node of graph.nodes) for (const [portId, usage] of Object.entries(node.port_usage ?? {})) {
    if (usage === "used") used.add(endpointKey(node.device.id, portId));
  }
  return { connected, used };
}

/** 二态只作展示/比较投影，读取不改原图；有线优先于旧的缺省或矛盾记录。 */
export function portUsage(graph: Graph, node: Node, portId: string): PortUsage {
  return graph.edges.some(edge => [edge.source, edge.target].some(endpoint => endpoint.device_id === node.device.id && endpoint.port_id === portId))
    || node.port_usage?.[portId] === "used" ? "used" : "unused";
}
