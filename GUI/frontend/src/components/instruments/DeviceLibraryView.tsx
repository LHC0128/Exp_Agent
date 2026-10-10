import { useEffect, useState } from "react";

import { api } from "../../api";
import { useJobActivity } from "../JobActivity";
import { deviceOptionLabel, endpointLabel, linkedMappingKeys, switchMappingDevice } from "./mappingDevices";
import type {
  DeviceConfig,
  DeviceLibraryDocument,
  DeviceLibrarySaveResult,
  PhysicalMappingsDocument,
  VisaDiscovery,
} from "../../types/api";

const defaultDevice = (): DeviceConfig => ({
  instrument: "signal_generator", model: "DG900", label: "新设备", resource: "",
  reference_clock: "EXT", connection: {}, capabilities: { channels: [1, 2] },
});
const slug = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
const serialNumber = (device: DeviceConfig) => String(
  device.connection.device_id && device.instrument === "lockin_amplifier"
    ? device.connection.device_id
    : device.connection.serial_number || device.connection.controller_serial
      || (device.resource?.toUpperCase().startsWith("USB") ? device.resource.split("::")[3] : "") || "",
).trim();
const findDiscoveryMatch = (devices: Record<string, DeviceConfig>, item: VisaDiscovery["devices"][number]) => {
  const serial = String(item.serial_number || item.connection?.device_id || item.idn.split(",")[2] || "").trim().toLowerCase();
  const hasSerial = !["", "0", "n/a", "na", "unknown", "none"].includes(serial);
  return Object.entries(devices).find(([, device]) =>
    Boolean(item.resource && device.resource?.toLowerCase() === item.resource.toLowerCase())
    || (device.instrument === item.instrument && device.model.trim().toUpperCase() === item.model.trim().toUpperCase() && hasSerial
      && serialNumber(device).toLowerCase() === serial),
  );
};

export function DeviceLibraryView() {
  const [document, setDocument] = useState<DeviceLibraryDocument>();
  const [savedDevices, setSavedDevices] = useState<Record<string, DeviceConfig>>({});
  const [networkText, setNetworkText] = useState("");
  const [mappings, setMappings] = useState<PhysicalMappingsDocument>();
  const [savedMappings, setSavedMappings] = useState<PhysicalMappingsDocument>();
  const [discovery, setDiscovery] = useState<VisaDiscovery>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { hardwareBusy } = useJobActivity();

  const load = async () => {
    try {
      const [devices, physicalMappings] = await Promise.all([
        api<DeviceLibraryDocument>("/api/device-library"),
        api<PhysicalMappingsDocument>("/api/physical-mappings"),
      ]);
      setDocument(devices);
      setSavedDevices(devices.devices);
      setNetworkText((devices.network_resources || []).join("\n"));
      setMappings(physicalMappings);
      setSavedMappings(physicalMappings);
    }
    catch (reason) { setError(String(reason)); }
  };
  useEffect(() => { void load(); }, []);

  const update = (id: string, values: Partial<DeviceConfig>) => {
    if (!document) return;
    setDocument({ ...document, devices: { ...document.devices, [id]: { ...document.devices[id], ...values } } });
  };
  const add = (device = defaultDevice(), requestedId?: string) => {
    if (!document) return;
    let id = requestedId || `device_${Object.keys(document.devices).length + 1}`;
    let suffix = 2;
    while (document.devices[id]) id = `${requestedId || "device"}_${suffix++}`;
    setDocument({ ...document, devices: { ...document.devices, [id]: device } });
  };
  const remove = (id: string) => {
    if (!document) return;
    const label = document.devices[id]?.label || id;
    if (!window.confirm(`确认从设备库删除“${label}”（${id}）？`)) return;
    const devices = { ...document.devices }; delete devices[id];
    setDocument({ ...document, devices });
  };
  const addDiscovered = (item: VisaDiscovery["devices"][number]) => {
    if (!document || findDiscoveryMatch(document.devices, item)) return;
    add({
      instrument: item.instrument, model: item.model, label: item.model, resource: item.resource,
      reference_clock: ["signal_generator", "lockin_amplifier"].includes(item.instrument) ? "EXT" : null,
      connection: { ...item.connection, ...(item.serial_number ? { serial_number: item.serial_number } : {}) },
      capabilities: item.instrument === "lockin_amplifier" ? { channels: item.model === "MFLI" ? [0, 1, 2, 3] : [0, 1, 2, 3, 4, 5] }
        : item.instrument === "signal_generator" ? { channels: [1, 2] }
          : item.instrument === "sds_acquisition" ? { channels: [1, 2, 3, 4] } : {},
    }, slug(item.serial_number || item.resource || item.model));
  };
  const save = async () => {
    if (!document || !mappings) return;
    setBusy(true); setError("");
    try {
      const saved = await api<DeviceLibrarySaveResult>("/api/device-library", {
        method: "PUT", body: JSON.stringify({ base_revision: document.revision, devices: document.devices,
          mapping: mappings.mapping, physical_mapping_revision: mappings.revision,
          network_resources: networkText.split(/\r?\n/).map(value => value.trim()).filter(Boolean) }),
      });
      setDocument(saved);
      setSavedDevices(saved.devices);
      setNetworkText((saved.network_resources || []).join("\n"));
      setMappings(saved.physical_mappings);
      setSavedMappings(saved.physical_mappings);
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  };
  const scan = async () => {
    setBusy(true); setError("");
    try { setDiscovery(await api<VisaDiscovery>("/api/device-library/discover-visa", { method: "POST" })); }
    catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  };

  const selectDevice = (keys: string[], deviceId: string) => {
    if (!document || !mappings) return;
    try {
      setMappings(switchMappingDevice(mappings, document.devices, keys, deviceId));
      setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };

  if (!document) return <div className="empty"><p>正在读取设备库…</p></div>;
  const lockins = Object.entries(document.devices).filter(([, device]) => device.instrument === "lockin_amplifier");
  const lockinKeys = Object.entries(mappings?.mapping || {}).filter(([, binding]) =>
    binding.instrument === "lockin_amplifier").map(([key]) => key);
  const selectedLockin = mappings?.mapping[lockinKeys[0]]?.device_id || "";
  const displayedLockin = selectedLockin || lockins[0]?.[0];
  const networkDirty = networkText !== (document.network_resources || []).join("\n");
  const dirty = JSON.stringify(document.devices) !== JSON.stringify(savedDevices) || networkDirty
    || JSON.stringify(mappings?.mapping) !== JSON.stringify(savedMappings?.mapping);
  const referencedDeviceIds = new Set(
    [...Object.values(mappings?.mapping || {}), ...Object.values(savedMappings?.mapping || {})].map(mapping => mapping.device_id),
  );
  const cards: { key: string; id: string; title: string; mappingKeys: string[] }[] = [];
  const groupedKeys = new Set<string>();
  for (const [key, binding] of Object.entries(mappings?.mapping || {})) {
    if (binding.instrument !== "signal_generator" || groupedKeys.has(key) || !mappings) continue;
    const keys = linkedMappingKeys(mappings, key);
    keys.forEach(member => groupedKeys.add(member));
    cards.push({ key: `mapping:${key}`, id: binding.device_id,
      title: keys.map(member => mappings.mapping[member]?.label || member).join(" / "), mappingKeys: keys });
  }
  const groupedDeviceIds = new Set(cards.map(card => card.id));
  for (const [id, device] of Object.entries(document.devices)) {
    if (groupedDeviceIds.has(id) || (device.instrument === "lockin_amplifier" && id !== displayedLockin)) continue;
    cards.push({ key: `device:${id}`, id, title: device.instrument === "lockin_amplifier" ? "锁相放大器" : device.label, mappingKeys: [] });
  }
  return (
    <div className="config-view">
      <div className="config-toolbar"><div><h2>设备库</h2><p>修订 {document.revision.slice(0, 10)}{dirty && " · 有未保存更改"}</p></div><div><button className="secondary" onClick={() => add()} disabled={busy}>新增设备</button><button className="secondary" onClick={() => void scan()} disabled={busy || hardwareBusy || networkDirty} title={networkDirty ? "请先保存网络扫描地址" : undefined}>{busy ? "处理中…" : "扫描 USB / 网络 / 锁相放大器"}</button><button onClick={() => void save()} disabled={busy || hardwareBusy || !mappings}>保存设备库</button></div></div>
      {error && <div className="alert error">{error}</div>}
      <p className="muted">信号源 Resource 和锁相放大器选择均在点击“保存设备库”后生效。信号源可在不同实验中共用通道；启动实验前核对接线。</p>
      <section className="device-config">
        <label className="device-config-wide">网络扫描地址（每行一个 VISA 资源）<textarea rows={8} value={networkText} disabled={busy}
          placeholder="TCPIP0::192.168.124.31::INSTR" onChange={event => setNetworkText(event.target.value)} /></label>
        <p className="device-config-wide muted">先保存地址，再主动扫描确认型号和序列号；加入设备后还需保存。清空此列表会停止扫描候选地址，已入库设备仍会扫描。</p>
      </section>
      {discovery && <section className="discovery-list">
        <h3>扫描结果</h3><p className="muted">扫描只读取身份信息；加入后需保存设备库。按型号和序列号识别 USB 与网络连接中的同一台设备。</p>
        {discovery.devices.map((item) => {
          const draft = findDiscoveryMatch(document.devices, item);
          const saved = findDiscoveryMatch(savedDevices, item);
          const match = draft || saved;
          const status = saved ? (draft ? "已入库" : "已移除，待保存") : draft ? "已加入，待保存" : "未入库";
          return <div key={item.resource || `${item.connection?.host}:${item.connection?.port}/${item.connection?.device_id}`}>
            <span><strong>{item.model}</strong><small>{item.idn}</small>
              {item.serial_number && <small>序列号：{item.serial_number}</small>}
              <small>{item.resource || `USB · 数据服务器 ${item.connection?.host}:${item.connection?.port}`}</small>
              <small className="discovery-status">{status}{match && ` · ${match[1].label}（${match[0]}）`}</small>
            </span>
            <button className="secondary" disabled={busy || Boolean(match) || item.instrument === "unknown"} onClick={() => addDiscovered(item)}>
              {match ? status : item.instrument === "unknown" ? "暂不支持" : "加入设备库"}
            </button>
          </div>;
        })}
        {!discovery.devices.length && <p>未发现可读取身份信息的设备。</p>}
        {discovery.errors.length > 0 && <details className="discovery-errors"><summary>{discovery.errors.length} 个连接未能完成扫描</summary><ul>{discovery.errors.map((item, index) => <li key={`${item.resource}-${index}`}><strong>{item.resource}</strong>：{item.message}</li>)}</ul></details>}
      </section>}
      <div className="device-config-grid">
        {cards.map(card => {
          const { id } = card;
          const device = document.devices[id];
          if (!device) return null;
          return <section className="device-config" key={card.key}>
            <div className="panel-head"><div><small>{id}</small><h3>{card.title}</h3></div><button className="icon-button" title={referencedDeviceIds.has(id) ? "设备仍被物理量映射引用" : "删除设备"} disabled={busy || referencedDeviceIds.has(id)} onClick={() => remove(id)}>×</button></div>
            {card.mappingKeys.length > 0 && <div className="device-config-wide">
              <label>Resource<select aria-label={`${card.mappingKeys[0]} Resource`} value={id} disabled={busy || !mappings}
                onChange={event => selectDevice(card.mappingKeys, event.target.value)}>
                {Object.entries(document.devices).filter(([, entry]) => entry.instrument === "signal_generator")
                  .map(([deviceId, entry]) => <option key={deviceId} value={deviceId}>{deviceOptionLabel(entry)}</option>)}
              </select></label>
              <p className="muted">{card.mappingKeys.map(key => {
                const binding = mappings!.mapping[key];
                return `${key}${binding?.endpoint ? ` · ${endpointLabel(binding.endpoint.kind, binding.endpoint.index)}` : ""}`;
              }).join("；")}。保存后整组切换实际设备。</p>
            </div>}
            {device.instrument === "lockin_amplifier" && <div className="device-config-wide lockin-selection">
              <label>当前锁相放大器（全系统）<select value={selectedLockin} disabled={busy || !mappings || !lockinKeys.length} onChange={event => selectDevice(lockinKeys, event.target.value)}>
                {!selectedLockin && <option value="">尚未选择</option>}
                {lockins.map(([deviceId, entry]) => <option key={deviceId} value={deviceId}>{entry.model} · {String(entry.connection.device_id || deviceId)}</option>)}
              </select></label>
              <p className="muted">保存设备库后用于全部实验；启动前检查输入通道和频率是否兼容。{hardwareBusy && "硬件任务结束后才可保存。"}</p>
            </div>}
            <label>标签<input value={device.label} disabled={busy} onChange={(event) => update(id, { label: event.target.value })} /></label>
            <label>设备类型<select value={device.instrument} disabled={busy} onChange={(event) => update(id, { instrument: event.target.value })}><option value="signal_generator">信号发生器</option><option value="gs200">Yokogawa GS200</option><option value="keithley_6221">Keithley 6221</option><option value="sds_acquisition">示波器</option><option value="lockin_amplifier">锁相放大器</option><option value="tec_controller">TEC</option><option value="toptica_dlc_pro">DLC pro</option></select></label>
            <label>型号<input value={device.model} disabled={busy} onChange={(event) => update(id, { model: event.target.value })} /></label>
            {device.instrument !== "lockin_amplifier" && <label>{card.mappingKeys.length ? "设备连接地址" : "Resource"}<input value={device.resource || ""} disabled={busy} onChange={(event) => update(id, { resource: event.target.value || null })} /></label>}
            {serialNumber(device) && <label>{device.instrument === "lockin_amplifier" ? "设备 ID / 序列号" : "序列号"}<input value={serialNumber(device)} readOnly /></label>}
            {device.instrument === "lockin_amplifier" && <>
              <label>数据服务器地址<input value={`${device.connection.host || "127.0.0.1"}:${device.connection.port || (device.model === "MFLI" ? 8004 : 8005)}`} readOnly /></label>
              <p className="device-config-wide muted">{device.model === "MFLI" ? "MFLI 通过网口连接，此处显示 LabOne 数据服务器地址。" : "HF2 通过 USB 接入数据服务器，此处显示服务器地址。"}</p>
            </>}
            <label>参考时钟<select value={device.reference_clock || ""} disabled={busy} onChange={(event) => update(id, { reference_clock: (event.target.value || null) as DeviceConfig["reference_clock"] })}><option value="">不适用</option><option value="INT">INT</option><option value="EXT">EXT</option></select></label>
            <label className="device-config-wide">通道号列表（逗号分隔）<input value={((device.capabilities.channels as number[] | undefined) || []).join(", ")} disabled={busy} onChange={(event) => update(id, {
              capabilities: {
                ...device.capabilities,
                channels: event.target.value.split(",").map((item) => item.trim()).filter(Boolean).map(Number)
                  .filter((item) => Number.isInteger(item) && (device.instrument === "lockin_amplifier" ? item >= 0 : item > 0)),
              },
            })} /></label>
          </section>;
        })}
      </div>
    </div>
  );
}
