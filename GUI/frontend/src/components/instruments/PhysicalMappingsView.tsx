import { useEffect, useState } from "react";

import { api } from "../../api";
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

function endpointLabel(kind: string, index: number) {
  if (kind === "demod") return `Demod ${index}`;
  if (kind === "laser_channel") return `Laser ${index}`;
  return `CH${index}`;
}

export function PhysicalMappingsView() {
  const [document, setDocument] = useState<PhysicalMappingsDocument>();
  const [library, setLibrary] = useState<DeviceLibraryDocument>();
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

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
  const save = async () => {
    if (!document) return;
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
      <div className="mapping-table">
        <div className="mapping-row mapping-header"><span>物理量</span><span>设备</span><span>端点</span><span>说明</span></div>
        {Object.entries(document.mapping).map(([key, config]) => {
          const candidates = Object.entries(library.devices).filter(([, device]) => device.instrument === config.instrument);
          const selected = library.devices[config.device_id];
          const channels = (selected?.capabilities.channels as number[] | undefined) || [];
          return (
            <div className="mapping-row" key={key}>
              <div><strong>{config.label || key}</strong><small>{key}</small></div>
              <select value={config.device_id} onChange={(event) => {
                const deviceId = event.target.value;
                const device = library.devices[deviceId];
                const available = (device.capabilities.channels as number[] | undefined) || [];
                updateMapping(key, {
                  device_id: deviceId,
                  endpoint: config.endpoint && available.length ? { kind: endpointKind(config.instrument), index: available[0] } : config.endpoint,
                });
              }}>
                {candidates.map(([id, device]) => <option value={id} key={id}>{device.label} · {device.model}</option>)}
              </select>
              {config.endpoint ? (
                <select value={config.endpoint.index} onChange={(event) => updateMapping(key, { endpoint: { ...config.endpoint!, index: Number(event.target.value) } })}>
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
