import { useEffect, useState } from "react";

import { api } from "../../api";
import { deviceOptionLabel, endpointLabel, linkedMappingKeys, switchMappingDevice } from "./mappingDevices";
import type {
  DeviceLibraryDocument,
  MappingConstraints,
  PhysicalMappingConfig,
  PhysicalMappingsDocument,
} from "../../types/api";

const clone = <T,>(value: T): T => structuredClone(value);

function endpointKind(instrument: string) {
  if (instrument === "lockin_amplifier") return "demod" as const;
  if (instrument === "toptica_dlc_pro") return "laser_channel" as const;
  return "channel" as const;
}

export function PhysicalMappingsView() {
  const [document, setDocument] = useState<PhysicalMappingsDocument>();
  const [library, setLibrary] = useState<DeviceLibraryDocument>();
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [newInstrument, setNewInstrument] = useState("signal_generator");
  const [newDeviceId, setNewDeviceId] = useState("");
  const [newEndpointIndex, setNewEndpointIndex] = useState("");
  const [newDescription, setNewDescription] = useState("");

  const load = async () => {
    setError("");
    try {
      const [mappings, devices] = await Promise.all([
        api<PhysicalMappingsDocument>("/api/physical-mappings"),
        api<DeviceLibraryDocument>("/api/device-library"),
      ]);
      setDocument(mappings);
      setLibrary(devices);
    } catch (reason) {
      setError(String(reason));
    }
  };
  useEffect(() => { void load(); }, []);

  const updateMapping = (key: string, update: Partial<PhysicalMappingConfig>) => {
    if (!document) return;
    const next = clone(document);
    next.mapping[key] = { ...next.mapping[key], ...update };
    setDocument(next);
  };
  const switchDevice = (key: string, deviceId: string) => {
    if (!document || !library) return;
    try {
      setDocument(switchMappingDevice(document, library.devices, linkedMappingKeys(document, key), deviceId));
      setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };
  const updateGroup = (kind: keyof MappingConstraints, name: string, value: string) => {
    if (!document) return;
    const next = clone(document);
    next.constraints[kind][name] = value.split(",").map((item) => item.trim()).filter(Boolean);
    setDocument(next);
  };
  const addGroup = (kind: keyof MappingConstraints) => {
    if (!document) return;
    const next = clone(document);
    let index = 1;
    while (next.constraints[kind][`group_${index}`]) index += 1;
    next.constraints[kind][`group_${index}`] = [];
    setDocument(next);
  };
  const removeGroup = (kind: keyof MappingConstraints, name: string) => {
    if (!document) return;
    const next = clone(document);
    delete next.constraints[kind][name];
    setDocument(next);
  };
  const addMapping = () => {
    if (!document || !library) return;
    const key = newKey.trim();
    if (!key) { setError("请填写物理量键名"); return; }
    if (document.mapping[key]) { setError(`物理量 ${key} 已存在`); return; }
    const device = library.devices[newDeviceId];
    if (!device) { setError("请选择设备"); return; }
    const endpointIndex = Number(newEndpointIndex);
    const endpoint = newEndpointIndex !== "" && Number.isFinite(endpointIndex) && endpointIndex >= 0
      ? { kind: endpointKind(newInstrument), index: endpointIndex }
      : undefined;
    const next = clone(document);
    next.mapping[key] = {
      instrument: newInstrument,
      device_id: newDeviceId,
      label: key,
      description: newDescription.trim(),
      ...(endpoint ? { endpoint } : {}),
    };
    setDocument(next);
    setNewKey(""); setNewDeviceId(""); setNewEndpointIndex(""); setNewDescription("");
    setError("");
  };
  const save = async () => {
    if (!document || !library) return;
    const mappingKeys = new Set(Object.keys(document.mapping));
    for (const member of Object.values(document.constraints.shared_channel_groups).flat()) {
      if (!mappingKeys.has(member)) {
        setError(`共享通道组成员“${member}”不是已存在的物理量键`);
        return;
      }
    }
    for (const member of Object.values(document.constraints.colocation_groups).flat()) {
      if (!mappingKeys.has(member)) {
        setError(`同机组成员“${member}”不是已存在的物理量键`);
        return;
      }
    }
    setSaving(true);
    setError("");
    try {
      setDocument(await api<PhysicalMappingsDocument>("/api/physical-mappings", {
        method: "PUT",
        body: JSON.stringify({
          base_revision: document.revision,
          mapping: document.mapping,
          constraints: document.constraints,
        }),
      }));
    } catch (reason) {
      setError(String(reason));
    } finally {
      setSaving(false);
    }
  };

  if (!document || !library) return <div className="empty"><p>正在读取物理量映射…</p></div>;
  return (
    <div className="config-view">
      <div className="config-toolbar">
        <div><h2>物理量映射</h2><p>修订 {document.revision.slice(0, 10)}</p></div>
        <div><button className="secondary" onClick={() => void load()}>重新加载</button><button onClick={() => void save()} disabled={saving}>{saving ? "正在保存…" : "保存全部映射"}</button></div>
      </div>
      {error && <div className="alert error">{error}</div>}
      <p className="muted">USB 与网络设备统一选择；切换设备会联动同机组和共享通道组，保留通道号，保存全部映射后生效。信号源通道可在不同实验中复用，同一次实验独立使用的物理量不能占用同一通道。</p>
      <section className="mapping-add">
        <div className="section-head"><h3>新增物理量</h3></div>
        <div className="mapping-add-form">
          <label><span>物理量键</span><input value={newKey} placeholder="例如 probe_detuning" onChange={(event) => setNewKey(event.target.value)} /></label>
          <label><span>设备类型</span><select value={newInstrument} onChange={(event) => { setNewInstrument(event.target.value); setNewDeviceId(""); setNewEndpointIndex(""); }}>
            <option value="signal_generator">信号发生器</option>
            <option value="gs200">Yokogawa GS200</option>
            <option value="keithley_6221">Keithley 6221</option>
            <option value="sds_acquisition">示波器</option>
            <option value="lockin_amplifier">锁相放大器（HF2 / MFLI）</option>
            <option value="tec_controller">TEC</option>
            <option value="toptica_dlc_pro">DLC pro</option>
          </select></label>
          <label><span>设备</span><select value={newDeviceId} onChange={(event) => { setNewDeviceId(event.target.value); setNewEndpointIndex(""); }}>
            <option value="">选择设备</option>
            {Object.entries(library.devices)
              .filter(([, device]) => device.instrument === newInstrument)
              .map(([id, device]) => <option key={id} value={id}>{deviceOptionLabel(device)}</option>)}
          </select></label>
          <label><span>端点（可选）</span><select value={newEndpointIndex} onChange={(event) => setNewEndpointIndex(event.target.value)}>
            <option value="">设备级</option>
            {(((library.devices[newDeviceId]?.capabilities.channels) as number[] | undefined) || []).map((channel) => (
              <option key={channel} value={channel}>{endpointLabel(endpointKind(newInstrument), channel)}</option>
            ))}
          </select></label>
          <label><span>说明</span><input value={newDescription} onChange={(event) => setNewDescription(event.target.value)} /></label>
          <button className="secondary" onClick={addMapping}>添加物理量</button>
        </div>
      </section>
      <div className="mapping-table">
        <div className="mapping-row mapping-header"><span>物理量</span><span>设备</span><span>端点</span><span>说明</span></div>
        {Object.entries(document.mapping).map(([key, config]) => {
          const candidates = Object.entries(library.devices).filter(([, device]) => device.instrument === config.instrument);
          const selected = library.devices[config.device_id];
          const channels = (selected?.capabilities.channels as number[] | undefined) || [];
          return (
            <div className="mapping-row" key={key}>
              <div><strong>{config.label || key}</strong><small>{key}</small></div>
              <select aria-label={`${key} 设备`} value={config.device_id} disabled={config.instrument === "lockin_amplifier"} title={config.instrument === "lockin_amplifier" ? "在设备库的锁相放大器卡片中统一切换" : undefined} onChange={event => switchDevice(key, event.target.value)}>
                {candidates.map(([id, device]) => <option value={id} key={id}>{deviceOptionLabel(device)}</option>)}
              </select>
              {config.endpoint ? (
                <select aria-label={`${key} 端点`} value={config.endpoint.index} onChange={(event) => updateMapping(key, { endpoint: { ...config.endpoint!, index: Number(event.target.value) } })}>
                  {channels.map((channel) => <option value={channel} key={channel}>{endpointLabel(config.endpoint!.kind, channel)}</option>)}
                </select>
              ) : <span className="muted">无端点</span>}
              <p>{config.description || ""}</p>
            </div>
          );
        })}
      </div>
      {(["shared_channel_groups", "colocation_groups"] as const).map((kind) => (
        <section className="constraint-section" key={kind}>
          <div className="section-head"><h3>{kind === "shared_channel_groups" ? "共享通道组" : "同机组"}</h3><button className="secondary" onClick={() => addGroup(kind)}>新增组</button></div>
          {Object.entries(document.constraints[kind]).map(([name, members]) => (
            <div className="constraint-row" key={name}><strong>{name}</strong><input value={members.join(", ")} onChange={(event) => updateGroup(kind, name, event.target.value)} /><button className="icon-button" title="删除组" onClick={() => removeGroup(kind, name)}>×</button></div>
          ))}
        </section>
      ))}
    </div>
  );
}
