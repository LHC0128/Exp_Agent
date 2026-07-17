import { useEffect, useState } from "react";

import { api } from "../api";
import { GeneratorEditor } from "../components/instruments/GeneratorEditor";
import { ScopeEditor } from "../components/instruments/ScopeEditor";
import { PageHead } from "../components/PageHead";
import type { Device, DeviceSnapshot } from "../types/api";

export function InstrumentsPage() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [selected, setSelected] = useState<Device>();
  const [snapshots, setSnapshots] = useState<Record<string, DeviceSnapshot>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const cacheSnapshot = (value: DeviceSnapshot) => {
    setSnapshots((current) => ({ ...current, [value.id]: value }));
  };
  const readSnapshot = async (device: Device) => {
    setBusy(true);
    setError("");
    try {
      cacheSnapshot(await api<DeviceSnapshot>(`/api/devices/${device.id}/refresh`, { method: "POST" }));
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  };
  const refresh = () => { if (selected) void readSnapshot(selected); };

  useEffect(() => {
    api<Device[]>("/api/devices").then((items) => {
      setDevices(items);
      setSelected(items[0]);
    });
  }, []);
  useEffect(() => {
    if (selected) void readSnapshot(selected);
  }, [selected?.id]);

  const snapshot = selected ? snapshots[selected.id] : undefined;
  return (
    <>
      <PageHead
        eyebrow="INSTRUMENTS"
        title="仪器控制"
        description="参数写入后自动回读，以仪器实际状态为准。"
        action={<button onClick={refresh} disabled={!selected || busy}>{busy ? "正在读取…" : "读取设备参数"}</button>}
      />
      <div className="instrument-layout">
        <div className="device-list">
          {devices.map((device) => (
            <button className={selected?.id === device.id ? "selected" : ""} onClick={() => setSelected(device)} key={device.id}>
              <span className={`device-icon ${device.type.toLowerCase()}`}>{device.type === "SDS" ? "OSC" : "GEN"}</span>
              <div><strong>{device.label}</strong><small>{device.type} · {device.short_resource}</small></div><i />
            </button>
          ))}
        </div>
        <div className="workspace">
          {error && <div className="alert error">{error}</div>}
          {!snapshot ? (
            <div className="empty"><span>↻</span><h3>读取当前设备参数</h3><p>选择设备后点击右上角按钮，将仪器面板上的最新设置同步到这里。</p></div>
          ) : snapshot.type === "SDS" ? (
            <ScopeEditor key={snapshot.id} snapshot={snapshot} onSaved={cacheSnapshot} />
          ) : (
            <GeneratorEditor key={snapshot.id} snapshot={snapshot} onSaved={cacheSnapshot} />
          )}
        </div>
      </div>
    </>
  );
}
