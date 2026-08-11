import { useEffect, useState } from "react";

import { api } from "../../api";
import type {
  DeviceConfig,
  DeviceLibraryDocument,
  PhysicalMappingsDocument,
  VisaDiscovery,
} from "../../types/api";

const defaultDevice = (): DeviceConfig => ({
  instrument: "signal_generator", model: "DG900", label: "新设备", resource: "",
  reference_clock: "EXT", connection: {}, capabilities: { channels: [1, 2] },
});
const slug = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");

export function DeviceLibraryView() {
  const [document, setDocument] = useState<DeviceLibraryDocument>();
  const [mappings, setMappings] = useState<PhysicalMappingsDocument>();
  const [discovery, setDiscovery] = useState<VisaDiscovery>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const [devices, physicalMappings] = await Promise.all([
        api<DeviceLibraryDocument>("/api/device-library"),
        api<PhysicalMappingsDocument>("/api/physical-mappings"),
      ]);
      setDocument(devices);
      setMappings(physicalMappings);
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
    const devices = { ...document.devices }; delete devices[id];
    setDocument({ ...document, devices });
  };
  const save = async () => {
    if (!document) return;
    setBusy(true); setError("");
    try {
      setDocument(await api<DeviceLibraryDocument>("/api/device-library", {
        method: "PUT", body: JSON.stringify({ base_revision: document.revision, devices: document.devices }),
      }));
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  };
  const scan = async () => {
    setBusy(true); setError("");
    try { setDiscovery(await api<VisaDiscovery>("/api/device-library/discover-visa", { method: "POST" })); }
    catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  };

  if (!document) return <div className="empty"><p>正在读取设备库…</p></div>;
  const referencedDeviceIds = new Set(
    Object.values(mappings?.mapping || {}).map((mapping) => mapping.device_id),
  );
  return (
    <div className="config-view">
      <div className="config-toolbar"><div><h2>设备库</h2><p>修订 {document.revision.slice(0, 10)}</p></div><div><button className="secondary" onClick={() => add()}>新增设备</button><button className="secondary" onClick={() => void scan()} disabled={busy}>扫描 VISA</button><button onClick={() => void save()} disabled={busy}>保存设备库</button></div></div>
      {error && <div className="alert error">{error}</div>}
      {discovery && <section className="discovery-list"><h3>扫描结果</h3>{discovery.devices.map((item) => <div key={item.resource}><span><strong>{item.model}</strong><small>{item.idn}</small></span><button className="secondary" disabled={item.instrument === "unknown"} onClick={() => add({ ...defaultDevice(), instrument: item.instrument, model: item.model, label: item.model, resource: item.resource }, slug(item.resource.split("::")[3] || item.model))}>加入设备库</button></div>)}</section>}
      <div className="device-config-grid">
        {Object.entries(document.devices).map(([id, device]) => (
          <section className="device-config" key={id}>
            <div className="panel-head"><div><small>{id}</small><h3>{device.label}</h3></div><button className="icon-button" title={referencedDeviceIds.has(id) ? "设备仍被物理量映射引用" : "删除设备"} disabled={referencedDeviceIds.has(id)} onClick={() => remove(id)}>×</button></div>
            <label>标签<input value={device.label} onChange={(event) => update(id, { label: event.target.value })} /></label>
            <label>设备类型<select value={device.instrument} onChange={(event) => update(id, { instrument: event.target.value })}><option value="signal_generator">信号发生器</option><option value="gs200">Yokogawa GS200</option><option value="keithley_6221">Keithley 6221</option><option value="sds_acquisition">示波器</option><option value="lockin_amplifier">HF2</option><option value="tec_controller">TEC</option><option value="toptica_dlc_pro">DLC pro</option></select></label>
            <label>型号<input value={device.model} onChange={(event) => update(id, { model: event.target.value })} /></label>
            <label>Resource<input value={device.resource || ""} onChange={(event) => update(id, { resource: event.target.value || null })} /></label>
            <label>参考时钟<select value={device.reference_clock || ""} onChange={(event) => update(id, { reference_clock: (event.target.value || null) as DeviceConfig["reference_clock"] })}><option value="">不适用</option><option value="INT">INT</option><option value="EXT">EXT</option></select></label>
          </section>
        ))}
      </div>
    </div>
  );
}
