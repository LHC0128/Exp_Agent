import { describe, expect, it } from "vitest";
import { buildWiringPreflight, requiredDevicePresent, wiringPreflightEmptyText, type WiringPreflightInput } from "./wiringPreflight";
import type { WiringGraph } from "./wiringTypes";

const node = (id: string, name: string, model = "人工器件", follow_mapping?: string[] | null): WiringGraph["nodes"][number] => ({
  device: { id, name, model, device_library_id: null, ports: [] },
  x: 60,
  y: 60,
  ...(follow_mapping === undefined ? {} : { follow_mapping }),
});

const graph = (nodes: WiringGraph["nodes"] = [], edges: WiringGraph["edges"] = []): WiringGraph => ({ nodes, edges });
const edge = (id: string): WiringGraph["edges"][number] => ({
  id,
  source: { device_id: "device-a", port_id: "out" },
  target: { device_id: "device-b", port_id: "in" },
  signal_type: "electrical",
  label: "",
});

const base = (overrides: Partial<WiringPreflightInput> = {}): WiringPreflightInput => ({
  validation: [],
  requirements: null,
  requirementsError: "",
  targetGraph: graph(),
  mappingGroupsAvailable: true,
  followStatuses: [],
  diagnostics: [],
  diagnosticsStale: false,
  blockedEdgeIds: [],
  profileBadges: {},
  deviceName: id => (({ "device-a": "信号源", "device-b": "锁相" }) as Record<string, string>)[id] ?? id,
  ...overrides,
});

describe("buildWiringPreflight", () => {
  it("把校验问题列为阻断并排在警告之前", () => {
    const items = buildWiringPreflight(base({
      validation: ["现场：器件名称不能为空。"],
      requirements: { required_devices: ["HF2"], required_mapping_keys: [] },
    }));
    expect(items.map(item => [item.severity, item.source, item.message])).toEqual([
      ["blocking", "validation", "现场：器件名称不能为空。"],
      ["warning", "requirement", "HF2：图中未找到此型号器件。"],
    ]);
  });

  it("按现有型号判定提示缺失设备，不因名称相似而认为已满足", () => {
    const targetGraph = graph([node("device-drive", "驱动源", "DG4000")]);
    expect(requiredDevicePresent(targetGraph, "驱动 (DG4000)")).toBe(true);
    const items = buildWiringPreflight(base({ targetGraph, requirements: { required_devices: ["驱动 (DG4000)", "HF2"], required_mapping_keys: [] } }));
    expect(items).toHaveLength(1);
    expect(items[0].message).toContain("HF2");
  });

  it("用途未绑定给警告，缺少用途组时提示无法确认绑定", () => {
    const targetGraph = graph([node("device-a", "信号源", "DG4000", ["drive"])]);
    const unbound = buildWiringPreflight(base({ targetGraph, requirements: { required_devices: [], required_mapping_keys: ["drive", "read"] } }));
    expect(unbound.filter(item => item.source === "requirement").map(item => item.message)).toEqual([
      "用途 read：图中无器件绑定此用途组。",
    ]);
    const unavailable = buildWiringPreflight(base({ targetGraph, mappingGroupsAvailable: false, requirements: { required_devices: [], required_mapping_keys: ["read"] } }));
    expect(unavailable[0].message).toContain("无法确认绑定，请人工核对");
  });

  it("要求读取失败只提示未核对，不新增阻断或 focus", () => {
    const items = buildWiringPreflight(base({ requirementsError: "实验定义读取失败" }));
    expect(items).toEqual([expect.objectContaining({ severity: "warning", source: "requirement", message: expect.stringContaining("实验定义读取失败") })]);
    expect(items[0].focus).toBeUndefined();
  });

  it("只有 pending / blocked 的跟随即条目，且草稿过期时标注基于最近读取", () => {
    const statuses: WiringPreflightInput["followStatuses"] = [
      { device_id: "device-a", state: "pending", mapping_keys: ["drive"], message: "等待确认用途" },
      { device_id: "device-b", state: "blocked", mapping_keys: [], message: "端口缺少对应关系" },
      { device_id: "device-a", state: "independent", mapping_keys: [], message: "独立快照" },
      { device_id: "device-b", state: "updated", mapping_keys: [], message: "已跟随" },
    ];
    const items = buildWiringPreflight(base({ followStatuses: statuses, diagnosticsStale: true }));
    expect(items.map(item => item.focus)).toEqual([
      { kind: "device", deviceId: "device-a" },
      { kind: "device", deviceId: "device-b" },
    ]);
    expect(items.every(item => item.message.includes("（基于最近读取/保存，草稿尚未保存）"))).toBe(true);
    expect(items[0].message).toContain("信号源：等待确认用途（drive）");
  });

  it("诊断与阻塞路由指向连线，草稿过期时诊断使用最近读取文案", () => {
    const targetGraph = graph([node("device-a", "信号源"), node("device-b", "锁相")], [edge("e1"), edge("e2")]);
    const items = buildWiringPreflight(base({
      targetGraph,
      diagnostics: [{ edge_id: "e1", code: "direction", message: "方向待确认" }],
      diagnosticsStale: true,
      blockedEdgeIds: ["e2"],
    }));
    expect(items.map(item => [item.source, item.focus])).toEqual([
      ["diagnostic", { kind: "edge", edgeId: "e1" }],
      ["route", { kind: "edge", edgeId: "e2" }],
    ]);
    expect(items[0].message).toBe("信号源 ↔ 锁相：方向待确认（基于最近读取/保存，草稿尚未保存）");
    expect(items[1].message).toContain("暂时无法绕行");
  });

  it("档案差异按设备汇总并指向器件；重复条目只保留一条", () => {
    const status = { device_id: "device-a", state: "pending" as const, mapping_keys: [], message: "等待确认用途" };
    const items = buildWiringPreflight(base({
      followStatuses: [status, status],
      profileBadges: { "device-a": ["model", "ports"] },
    }));
    expect(items).toHaveLength(2);
    expect(items[0].message).toContain("信号源：等待确认用途");
    expect(items[1].message).toContain("型号不一致、端口或模式不一致");
    expect(items[1].focus).toEqual({ kind: "device", deviceId: "device-a" });
  });

  it("零条目返回空清单，文案不声称通过或正确", () => {
    expect(buildWiringPreflight(base())).toEqual([]);
    expect(wiringPreflightEmptyText).not.toMatch(/通过|正确/);
  });
});
