import type { components } from "../../types/openapi";

type WiringNode = components["schemas"]["WiringNode"];
type DevicePortProfile = components["schemas"]["DevicePortProfile"];
type PhysicalPort = components["schemas"]["PhysicalPort"];
type PortMode = components["schemas"]["PortMode"];

export type WiringSyncBadge = "missing" | "model" | "ports";

export const wiringSyncBadgeLabel: Record<WiringSyncBadge, string> = {
  missing: "档案中不存在",
  model: "型号不一致",
  ports: "端口或模式不一致",
};

export type WiringProfileComparison = {
  profile: DevicePortProfile | null;
  badges: WiringSyncBadge[];
};

export type WiringSyncChange = { from: string; to: string };

export type WiringSyncSummary = {
  name: WiringSyncChange | null;
  model: WiringSyncChange | null;
  addedPorts: string[];
  updatedPorts: string[];
  graphOnlyPorts: string[];
  addedModes: string[];
  removedModes: string[];
};

export type WiringSyncResult =
  | { ok: true; node: WiringNode; summary: WiringSyncSummary; changed: boolean }
  | { ok: false; reason: string };

const portKey = (port: PhysicalPort) => JSON.stringify([port.group, port.name, port.signal_type]);
const modeKey = (mode: PortMode) => JSON.stringify([mode.name, mode.direction]);
const portLabel = (port: PhysicalPort) => [port.group, port.name].filter(Boolean).join(" / ") || port.id;
const defaultCreateId = (prefix: string) => `${prefix}-${crypto.randomUUID()}`;

function groupPorts(ports: PhysicalPort[]) {
  const index = new Map<string, PhysicalPort[]>();
  for (const port of ports) {
    const key = portKey(port);
    const matches = index.get(key);
    if (matches) matches.push(port);
    else index.set(key, [port]);
  }
  return index;
}

function uniqueModes(modes: PortMode[]) {
  const index = new Map<string, PortMode>();
  for (const mode of modes) {
    const key = modeKey(mode);
    if (index.has(key)) return null;
    index.set(key, mode);
  }
  return index;
}

function sameModeSet(left: PortMode[], right: PortMode[]) {
  if (left.length !== right.length) return false;
  const leftIndex = uniqueModes(left);
  const rightIndex = uniqueModes(right);
  if (!leftIndex || !rightIndex || leftIndex.size !== rightIndex.size) return false;
  for (const key of leftIndex.keys()) if (!rightIndex.has(key)) return false;
  return true;
}

function samePortDefinition(left: PhysicalPort, right: PhysicalPort) {
  return left.direction === right.direction
    && left.connector === right.connector
    && left.purpose === right.purpose
    && sameModeSet(left.modes, right.modes);
}

function samePortsAndModes(device: DevicePortProfile, profile: DevicePortProfile) {
  if (device.ports.length !== profile.ports.length) return false;
  const left = groupPorts(device.ports);
  const right = groupPorts(profile.ports);
  if (left.size !== right.size) return false;
  for (const [key, matches] of left) {
    const peers = right.get(key);
    if (!peers || matches.length !== 1 || peers.length !== 1 || !samePortDefinition(matches[0], peers[0])) return false;
  }
  return true;
}

/** 只读比较图内快照与同库档案；没有设备库身份的手工节点不参与档案比较。 */
export function compareNodeProfile(node: WiringNode, profiles: DevicePortProfile[]): WiringProfileComparison {
  const libraryId = node.device.device_library_id;
  if (!libraryId) return { profile: null, badges: [] };
  const profile = profiles.find(item => item.device_library_id === libraryId) ?? null;
  if (!profile) return { profile: null, badges: ["missing"] };
  const badges: WiringSyncBadge[] = [];
  if (node.device.model.trim().toLowerCase() !== profile.model.trim().toLowerCase()) badges.push("model");
  if (!samePortsAndModes(node.device, profile)) badges.push("ports");
  return { profile, badges };
}

type PortSync = {
  port: PhysicalPort;
  addedModes: PortMode[];
  removedModes: PortMode[];
  selectedModeCleared: boolean;
};

function syncPort(oldPort: PhysicalPort, profilePort: PhysicalPort, makeId: (prefix: string) => string): PortSync | { reason: string } {
  const label = portLabel(profilePort);
  const oldModes = uniqueModes(oldPort.modes);
  if (!oldModes) return { reason: `端口“${label}”的原图模式存在重名或同向歧义，无法唯一匹配` };
  const profileModes = uniqueModes(profilePort.modes);
  if (!profileModes) return { reason: `档案端口“${label}”的模式存在重名或同向歧义，无法唯一匹配` };
  if (profilePort.direction === "switchable" && !profilePort.modes.length) return { reason: `档案端口“${label}”是可切换端口但没有模式定义` };
  const modes = profilePort.direction === "switchable"
    ? profilePort.modes.map(mode => ({ ...mode, id: oldModes.get(modeKey(mode))?.id ?? makeId("mode") }))
    : [];
  if (new Set(modes.map(mode => mode.id)).size !== modes.length) return { reason: `端口“${label}”的模式 ID 无法唯一生成` };
  let selectedMode: string | null = null;
  if (profilePort.direction === "switchable" && oldPort.selected_mode) {
    const oldSelected = oldPort.modes.find(mode => mode.id === oldPort.selected_mode);
    if (oldSelected && profileModes.has(modeKey(oldSelected))) selectedMode = oldSelected.id;
  }
  return {
    port: { ...profilePort, id: oldPort.id, modes, selected_mode: selectedMode },
    addedModes: profilePort.modes.filter(mode => !oldModes.has(modeKey(mode))),
    removedModes: oldPort.modes.filter(mode => !profileModes.has(modeKey(mode))),
    selectedModeCleared: oldPort.selected_mode !== selectedMode,
  };
}

/**
 * 生成同步后的当前图器件快照；不读取或写入档案、连线、节点坐标和运行配置。
 * 端口按 (group,name,signal_type) 唯一匹配，模式按 (name,direction) 唯一匹配；
 * 任一匹配不唯一时整体拒绝，绝不部分写入。
 */
export function syncNodeFromProfile(node: WiringNode, profile: DevicePortProfile, createId: (prefix: string) => string = defaultCreateId): WiringSyncResult {
  const oldPorts = node.device.ports;
  const profilePorts = profile.ports;
  const oldIndex = groupPorts(oldPorts);
  const profileIndex = groupPorts(profilePorts);

  for (const [key, matches] of oldIndex) {
    const targets = profileIndex.get(key);
    if (!targets) continue;
    if (matches.length !== 1 || targets.length !== 1) {
      return { ok: false, reason: `端口“${portLabel(matches[0])}”在图中或档案中存在重复定义，无法唯一匹配；未修改任何内容。` };
    }
  }
  for (const matches of profileIndex.values()) {
    if (matches.length !== 1) return { ok: false, reason: `档案端口“${portLabel(matches[0])}”存在重复定义，无法唯一匹配；未修改任何内容。` };
  }

  const usedIds = new Set<string>();
  for (const port of oldPorts) usedIds.add(port.id);
  for (const port of node.internal_ports ?? []) usedIds.add(port.id);
  for (const port of oldPorts) for (const mode of port.modes) usedIds.add(mode.id);
  const makeId = (prefix: string) => {
    const base = createId(prefix) || prefix;
    let id = base;
    let suffix = 1;
    while (usedIds.has(id)) id = `${base}-${suffix++}`;
    usedIds.add(id);
    return id;
  };

  const ports: PhysicalPort[] = [];
  const newPortIds: string[] = [];
  const addedPorts: string[] = [];
  const updatedPorts = new Set<string>();
  const graphOnlyPorts: string[] = [];
  const addedModes: string[] = [];
  const removedModes: string[] = [];

  for (const oldPort of oldPorts) {
    const targets = profileIndex.get(portKey(oldPort));
    if (!targets) {
      ports.push(oldPort);
      graphOnlyPorts.push(portLabel(oldPort));
      continue;
    }
    const synced = syncPort(oldPort, targets[0], makeId);
    if ("reason" in synced) return { ok: false, reason: `${synced.reason}；未修改任何内容。` };
    ports.push(synced.port);
    const label = portLabel(synced.port);
    // 连线、工作方向和用途状态留在节点上；这里只更新从档案带来的端口与模式定义。
    if (!samePortDefinition(oldPort, targets[0]) || synced.selectedModeCleared) updatedPorts.add(label);
    for (const mode of synced.addedModes) addedModes.push(`${label} / ${mode.name}`);
    for (const mode of synced.removedModes) removedModes.push(`${label} / ${mode.name}`);
  }

  for (const profilePort of profilePorts) {
    if (oldIndex.has(portKey(profilePort))) continue;
    const id = makeId("port");
    const modes = profilePort.direction === "switchable"
      ? profilePort.modes.map(mode => ({ ...mode, id: makeId("mode") }))
      : [];
    ports.push({ ...profilePort, id, modes, selected_mode: null });
    newPortIds.push(id);
    const label = portLabel(profilePort);
    addedPorts.push(label);
    for (const mode of modes) addedModes.push(`${label} / ${mode.name}`);
  }

  const summary: WiringSyncSummary = {
    name: node.device.name !== profile.name ? { from: node.device.name, to: profile.name } : null,
    model: node.device.model !== profile.model ? { from: node.device.model, to: profile.model } : null,
    addedPorts,
    updatedPorts: [...updatedPorts],
    graphOnlyPorts,
    addedModes,
    removedModes,
  };
  const changed = summary.name !== null || summary.model !== null || addedPorts.length > 0 || updatedPorts.size > 0;

  const usage = { ...(node.port_usage ?? {}) };
  for (const id of newPortIds) if (usage[id] === undefined) usage[id] = "unconfirmed";
  const next: WiringNode = { ...node, device: { ...profile, id: node.device.id, ports } };
  if (node.port_usage !== undefined || newPortIds.length > 0) next.port_usage = usage;
  if (node.port_directions !== undefined) {
    const byId = new Map(ports.map(port => [port.id, port]));
    const directions: Record<string, "in" | "out"> = {};
    for (const [id, direction] of Object.entries(node.port_directions)) {
      if (byId.get(id)?.direction === "inout") directions[id] = direction;
    }
    next.port_directions = directions;
  }
  return { ok: true, node: next, summary, changed };
}

/** 供确认面板逐条展示的差异摘要；不包含任何隐式写入动作。 */
export function wiringSyncSummaryLines(summary: WiringSyncSummary): string[] {
  const lines: string[] = [];
  if (summary.name) lines.push(`器件名称：${summary.name.from || "未命名"} → ${summary.name.to || "未命名"}`);
  if (summary.model) lines.push(`型号：${summary.model.from || "型号待确认"} → ${summary.model.to || "型号待确认"}`);
  if (summary.addedPorts.length) lines.push(`档案新增端口：${summary.addedPorts.join("、")}`);
  if (summary.updatedPorts.length) lines.push(`端口或模式更新：${summary.updatedPorts.join("、")}`);
  if (summary.graphOnlyPorts.length) lines.push(`仅图中保留（不删除）：${summary.graphOnlyPorts.join("、")}`);
  if (summary.addedModes.length) lines.push(`新增模式：${summary.addedModes.join("、")}`);
  if (summary.removedModes.length) lines.push(`移除模式：${summary.removedModes.join("、")}`);
  if (!lines.length) lines.push("档案与当前图快照一致，无需同步。");
  return lines;
}
