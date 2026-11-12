import type { components } from "../../types/openapi";
import type { WiringGraph } from "./wiringTypes";

type WiringWarning = components["schemas"]["WiringWarning"];
type WiringFollowStatus = components["schemas"]["WiringFollowStatus"];

export type PreflightSeverity = "blocking" | "warning" | "info";
export type PreflightSource = "validation" | "requirement" | "follow" | "diagnostic" | "route";
export type PreflightFocus = { kind: "device"; deviceId: string } | { kind: "edge"; edgeId: string };
export type PreflightItem = {
  id: string;
  severity: PreflightSeverity;
  source: PreflightSource;
  message: string;
  focus?: PreflightFocus;
};

export type WiringPreflightRequirements = {
  required_devices: string[];
  required_mapping_keys: string[];
};

export type WiringPreflightInput = {
  validation: string[];
  requirements: WiringPreflightRequirements | null;
  requirementsError?: string;
  targetGraph: WiringGraph;
  mappingGroupsAvailable: boolean;
  followStatuses: WiringFollowStatus[];
  diagnostics: WiringWarning[];
  diagnosticsStale: boolean;
  blockedEdgeIds: string[];
  deviceName: (deviceId: string) => string;
};

export const wiringPreflightEmptyText = "未发现可自动识别的问题。";
export const wiringPreflightStaleSuffix = "（基于最近读取/保存，草稿尚未保存）";

// 需求条目可能写成“标签 (型号)”；只取型号做存在性比较，不推断连线。
const requiredDeviceModel = (value: string) => {
  const matched = /\(([^()]*)\)\s*$/.exec(value);
  return (matched ? matched[1] : value).trim().toLocaleLowerCase();
};

export const requiredDevicePresent = (graph: WiringGraph, value: string) => {
  const model = requiredDeviceModel(value);
  return model.length > 0 && graph.nodes.some(node => node.device.model.trim().toLocaleLowerCase() === model);
};

const severityRank: Record<PreflightSeverity, number> = { blocking: 0, warning: 1, info: 2 };
const sourceRank: Record<PreflightSource, number> = { validation: 0, requirement: 1, follow: 2, diagnostic: 3, route: 4 };

const focusKey = (focus: PreflightFocus | undefined) => focus
  ? focus.kind === "device" ? `device:${focus.deviceId}` : `edge:${focus.edgeId}`
  : "";

/** 只汇总现有提示与画布上报；不读取服务端、不修改图，也不改变保存或运行阻塞规则。 */
export function buildWiringPreflight(input: WiringPreflightInput): PreflightItem[] {
  const { validation, requirements, requirementsError = "", targetGraph, mappingGroupsAvailable, followStatuses,
    diagnostics, diagnosticsStale, blockedEdgeIds, deviceName } = input;
  const nameOf = (deviceId: string) => deviceName(deviceId) || deviceId;
  const items: Array<Omit<PreflightItem, "id">> = [];
  const push = (severity: PreflightSeverity, source: PreflightSource, message: string, focus?: PreflightFocus) => {
    items.push({ severity, source, message, ...(focus ? { focus } : {}) });
  };

  for (const problem of validation) push("blocking", "validation", problem);

  if (requirements) {
    for (const device of requirements.required_devices) {
      if (!requiredDevicePresent(targetGraph, device)) push("warning", "requirement", `${device}：图中未找到此型号器件。`);
    }
    for (const key of requirements.required_mapping_keys) {
      if (!mappingGroupsAvailable) push("warning", "requirement", `用途 ${key}：当前接线图未提供用途组信息，无法确认绑定，请人工核对。`);
      else if (!targetGraph.nodes.some(node => (node.follow_mapping ?? []).includes(key))) {
        push("warning", "requirement", `用途 ${key}：图中无器件绑定此用途组。`);
      }
    }
  }
  if (requirementsError.trim()) push("warning", "requirement", `实验接线要求读取失败，未核对型号 / 用途要求：${requirementsError}`);

  for (const status of followStatuses) {
    if (status.state !== "pending" && status.state !== "blocked") continue;
    const keys = status.mapping_keys.length ? `（${status.mapping_keys.join(" / ")}）` : "";
    push("warning", "follow", `${nameOf(status.device_id)}：${status.message}${keys}${diagnosticsStale ? wiringPreflightStaleSuffix : ""}`,
      { kind: "device", deviceId: status.device_id });
  }

  for (const warning of diagnostics) {
    const edge = targetGraph.edges.find(item => item.id === warning.edge_id);
    const prefix = edge ? `${nameOf(edge.source.device_id)} ↔ ${nameOf(edge.target.device_id)}：` : "连线提示：";
    push("warning", "diagnostic", `${prefix}${warning.message}${diagnosticsStale ? wiringPreflightStaleSuffix : ""}`, { kind: "edge", edgeId: warning.edge_id });
  }

  for (const edgeId of blockedEdgeIds) {
    const edge = targetGraph.edges.find(item => item.id === edgeId);
    const prefix = edge ? `${nameOf(edge.source.device_id)} ↔ ${nameOf(edge.target.device_id)}：` : `连线 ${edgeId}：`;
    push("warning", "route", `${prefix}因器件重叠或端口外侧被挡，暂时无法绕行；记录与端口未删除，请移开器件。`, { kind: "edge", edgeId });
  }

  const seen = new Set<string>();
  return items
    .filter(item => {
      const key = `${item.source}|${item.message}|${focusKey(item.focus)}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .sort((left, right) => severityRank[left.severity] - severityRank[right.severity]
      || sourceRank[left.source] - sourceRank[right.source]
      || (left.message < right.message ? -1 : left.message > right.message ? 1 : 0))
    .map((item, index) => ({ ...item, id: `preflight-${index}-${item.source}` }));
}
