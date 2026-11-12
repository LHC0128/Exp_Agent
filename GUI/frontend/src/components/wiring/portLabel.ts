import type { components } from "../../types/openapi";

type PhysicalPort = components["schemas"]["PhysicalPort"];

/** 端口只显示名称；同一器件内重名时回退带分组，避免图上出现两个同名端口。 */
export function portLabeler(ports: PhysicalPort[]) {
  const seen = new Set<string>();
  const duplicated = new Set<string>();
  for (const port of ports) {
    if (seen.has(port.name)) duplicated.add(port.name);
    seen.add(port.name);
  }
  return (port: PhysicalPort) => [duplicated.has(port.name) ? port.group : "", port.name || "未命名端口"].filter(Boolean).join(" / ");
}