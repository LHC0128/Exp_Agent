import { describe, expect, it } from "vitest";
import type { components } from "../../types/openapi";
import { compareNodeProfile, syncNodeFromProfile, wiringSyncSummaryLines } from "./wiringSync";

type Port = components["schemas"]["PhysicalPort"];
type Profile = components["schemas"]["DevicePortProfile"];
type Node = components["schemas"]["WiringNode"];

const port = (overrides: Partial<Port> = {}): Port => ({
  id: "port-1", name: "P1", group: "", signal_type: null, connector: "", purpose: "",
  direction: null, modes: [], selected_mode: null, ...overrides,
});
const device = (overrides: Partial<Profile> = {}): Profile => ({
  id: "device-1", name: "档案器件", model: "M1", device_library_id: "lib-1", ports: [], ...overrides,
});
const node = (value: Profile, extra: Partial<Node> = {}): Node => ({ device: structuredClone(value), x: 60, y: 60, ...extra });
const sequence = () => { let index = 0; return (prefix: string) => `${prefix}-new-${++index}`; };

describe("syncNodeFromProfile", () => {
  it("按 (group,name,signal_type) 与模式 (name,direction) 唯一匹配，保留稳定 ID 和工作状态", () => {
    const switchPort = port({
      id: "port-sw", name: "SW", group: "RF", signal_type: "electrical", direction: "switchable",
      modes: [{ id: "mode-rx", name: "接收", direction: "in" }, { id: "mode-tx", name: "发送", direction: "out" }],
      selected_mode: "mode-tx",
    });
    const ioPort = port({ id: "port-io", name: "IO", group: "RF", signal_type: "electrical", direction: "inout", purpose: "旧用途" });
    const manualPort = port({ id: "port-manual", name: "人工", group: "光路", signal_type: "optical", direction: "in" });
    const oldDevice = device({ name: "旧图名称", model: "旧型号", ports: [switchPort, ioPort, manualPort] });
    const profile = device({
      id: "profile-other", name: "新档案名称", model: "新型号",
      ports: [
        { ...switchPort, id: "profile-sw", modes: [
          { id: "profile-rx", name: "接收", direction: "in" },
          { id: "profile-tx", name: "发送", direction: "out" },
          { id: "profile-bypass", name: "旁路", direction: "out" },
        ], selected_mode: "profile-tx" },
        { ...ioPort, id: "profile-io", purpose: "新用途", connector: "SMA" },
        port({ id: "profile-mon", name: "MON", group: "RF", signal_type: "electrical", direction: "out" }),
      ],
    });
    const result = syncNodeFromProfile(node(oldDevice, {
      follow_mapping: ["Temp_Switch"],
      port_directions: { "port-io": "out" },
      port_usage: { "port-sw": "used", "port-io": "unused" },
      settings: [{ id: "setting-1", name: "档位", options: [{ value: "1", label: "低" }], value: "1" }],
    }), profile, sequence());
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.node.device).toMatchObject({ id: "device-1", name: "新档案名称", model: "新型号", device_library_id: "lib-1" });
    expect(result.node.device.ports.map(item => item.id)).toEqual(["port-sw", "port-io", "port-manual", "port-new-2"]);
    const syncedSwitch = result.node.device.ports[0];
    expect(syncedSwitch.modes.map(mode => mode.id)).toEqual(["mode-rx", "mode-tx", "mode-new-1"]);
    expect(syncedSwitch.selected_mode).toBe("mode-tx");
    expect(result.node.device.ports[1]).toMatchObject({ id: "port-io", purpose: "新用途", connector: "SMA" });
    expect(result.node.device.ports[2]).toEqual(manualPort);
    expect(result.node.device.ports[3]).toMatchObject({ name: "MON", selected_mode: null, modes: [] });
    expect(result.node.port_usage).toEqual({ "port-sw": "used", "port-io": "unused", "port-new-2": "unconfirmed" });
    expect(result.node.port_directions).toEqual({ "port-io": "out" });
    expect(result.node.follow_mapping).toEqual(["Temp_Switch"]);
    expect(result.node.settings?.map(setting => setting.id)).toEqual(["setting-1"]);
    expect(result.node).toMatchObject({ x: 60, y: 60 });
    expect(result.changed).toBe(true);
    expect(result.summary).toMatchObject({
      name: { from: "旧图名称", to: "新档案名称" },
      model: { from: "旧型号", to: "新型号" },
      addedPorts: ["RF / MON"],
      graphOnlyPorts: ["光路 / 人工"],
    });
    expect(result.summary.updatedPorts).toEqual(expect.arrayContaining(["RF / SW", "RF / IO"]));
    expect(result.summary.addedModes).toContain("RF / SW / 旁路");
    const lines = wiringSyncSummaryLines(result.summary);
    expect(lines).toEqual(expect.arrayContaining([
      "器件名称：旧图名称 → 新档案名称",
      "型号：旧型号 → 新型号",
      "档案新增端口：RF / MON",
      "仅图中保留（不删除）：光路 / 人工",
    ]));
  });

  it("模式匹配不唯一或端口匹配不唯一时整体拒绝，不修改输入", () => {
    const profile = device({
      ports: [port({
        id: "profile-port", name: "P", group: "G", signal_type: "electrical", direction: "switchable",
        modes: [{ id: "profile-mode", name: "模式", direction: "in" }],
      })],
    });
    const duplicateOldModes = device({
      ports: [port({
        id: "old-port", name: "P", group: "G", signal_type: "electrical", direction: "switchable",
        modes: [{ id: "mode-a", name: "模式", direction: "in" }, { id: "mode-b", name: "模式", direction: "in" }],
      })],
    });
    const duplicateProfileModes = device({
      ports: [port({
        id: "profile-port", name: "P", group: "G", signal_type: "electrical", direction: "switchable",
        modes: [{ id: "profile-a", name: "模式", direction: "in" }, { id: "profile-b", name: "模式", direction: "in" }],
      })],
    });
    const duplicatePorts = device({
      ports: [
        port({ id: "old-a", name: "P", group: "G", signal_type: "electrical", direction: "in" }),
        port({ id: "old-b", name: "P", group: "G", signal_type: "electrical", direction: "in" }),
      ],
    });
    for (const [value, expected] of [
      [duplicateOldModes, "模式"],
      [duplicateProfileModes, "模式"],
      [duplicatePorts, "端口"],
    ] as const) {
      const result = syncNodeFromProfile(node(value), profile, sequence());
      expect(result.ok).toBe(false);
      if (result.ok) continue;
      expect(result.reason).toContain(expected);
      expect(result.reason).toContain("未修改任何内容");
    }
    expect(duplicateOldModes.ports[0].modes).toHaveLength(2);
    expect(duplicatePorts.ports).toHaveLength(2);
  });

  it("档案新增端口追加新 ID 并默认待确认，图中独有端口保留不删", () => {
    const manual = port({ id: "manual-port", name: "人工", group: "光路", signal_type: "optical", direction: "in" });
    const oldDevice = device({ ports: [manual] });
    const profile = device({
      ports: [
        port({ id: "profile-new", name: "NEW", group: "RF", signal_type: "electrical", direction: "out" }),
        port({ id: "profile-switch", name: "SW", group: "RF", signal_type: "electrical", direction: "switchable",
          modes: [{ id: "profile-mode", name: "输出", direction: "out" }] }),
      ],
    });
    const result = syncNodeFromProfile(node(oldDevice), profile, sequence());
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.node.device.ports.map(item => item.id)).toEqual(["manual-port", "port-new-1", "port-new-2"]);
    expect(result.node.device.ports[0]).toEqual(manual);
    expect(result.node.device.ports[1]).toMatchObject({ name: "NEW", selected_mode: null, modes: [] });
    expect(result.node.device.ports[2].modes.map(mode => mode.id)).toEqual(["mode-new-3"]);
    expect(result.node.port_usage).toEqual({ "port-new-1": "unconfirmed", "port-new-2": "unconfirmed" });
    expect(result.summary.addedPorts).toEqual(["RF / NEW", "RF / SW"]);
    expect(result.summary.graphOnlyPorts).toEqual(["光路 / 人工"]);
    expect(result.changed).toBe(true);
  });

  it("被档案移除的选定模式清为待确认，同时保留仍唯一匹配的模式 ID", () => {
    const oldDevice = device({
      ports: [port({
        id: "old-port", name: "SW", group: "RF", signal_type: "electrical", direction: "switchable",
        modes: [{ id: "old-selected", name: "旧模式", direction: "in" }, { id: "kept-mode", name: "保留模式", direction: "out" }],
        selected_mode: "old-selected",
      })],
    });
    const profile = device({
      ports: [port({
        id: "profile-port", name: "SW", group: "RF", signal_type: "electrical", direction: "switchable",
        modes: [{ id: "profile-kept", name: "保留模式", direction: "out" }, { id: "profile-added", name: "新模式", direction: "in" }],
        selected_mode: "profile-added",
      })],
    });
    const result = syncNodeFromProfile(node(oldDevice), profile, sequence());
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const synced = result.node.device.ports[0];
    expect(synced.id).toBe("old-port");
    expect(synced.modes.map(mode => mode.id)).toEqual(["kept-mode", "mode-new-1"]);
    expect(synced.selected_mode).toBeNull();
    expect(result.summary.removedModes).toEqual(["RF / SW / 旧模式"]);
    expect(result.summary.addedModes).toEqual(["RF / SW / 新模式"]);
  });
});

describe("compareNodeProfile", () => {
  it("档案缺失、型号和端口或模式差异分别给出只读徽标", () => {
    const base = device({
      ports: [port({
        id: "p", name: "P", group: "G", signal_type: "electrical", direction: "switchable",
        modes: [{ id: "m", name: "输入", direction: "in" }],
      })],
    });
    expect(compareNodeProfile(node(base), []).badges).toEqual(["missing"]);
    expect(compareNodeProfile(node(base), [device({ ...base, model: "M2" })]).badges).toEqual(["model"]);
    expect(compareNodeProfile(node(base), [device({
      ...base,
      ports: [base.ports[0], port({ id: "extra", name: "EXTRA", group: "G", signal_type: "electrical", direction: "out" })],
    })]).badges).toEqual(["ports"]);
    expect(compareNodeProfile(node(base), [device({
      ...base,
      ports: [{ ...base.ports[0], modes: [{ id: "m2", name: "输出", direction: "out" }] }],
    })]).badges).toEqual(["ports"]);
  });

  it("手工节点不参与档案比较，名称和工作状态差异不产生徽标", () => {
    const profile = device({
      ports: [port({
        id: "p", name: "P", group: "G", signal_type: "electrical", direction: "switchable",
        modes: [{ id: "m", name: "输入", direction: "in" }],
      })],
    });
    const manual = { ...node(profile), device: { ...profile, device_library_id: null } };
    expect(compareNodeProfile(manual, [profile])).toEqual({ profile: null, badges: [] });
    const local = node({
      ...profile,
      name: "现场独立命名",
      ports: [{ ...profile.ports[0], selected_mode: "m" }],
    });
    expect(compareNodeProfile(local, [profile])).toEqual({ profile, badges: [] });
  });
});
