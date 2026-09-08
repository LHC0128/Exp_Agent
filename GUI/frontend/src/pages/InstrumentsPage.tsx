import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { CurrentSourceEditor } from "../components/instruments/CurrentSourceEditor";
import { DeviceLibraryView } from "../components/instruments/DeviceLibraryView";
import { GeneratorEditor } from "../components/instruments/GeneratorEditor";
import { Hf2Editor } from "../components/instruments/Hf2Editor";
import { LaserEditor } from "../components/instruments/LaserEditor";
import { PhysicalMappingsView } from "../components/instruments/PhysicalMappingsView";
import { ScopeEditor } from "../components/instruments/ScopeEditor";
import { TecEditor } from "../components/instruments/TecEditor";
import { useJobActivity } from "../components/JobActivity";
import { PageHead } from "../components/PageHead";
import type { ControlRoute, ControlTarget, ControlTargetBulkResponse, ControlTargetCatalog, ControlTargetResponse, DeviceSnapshot } from "../types/api";

function DeviceControlView() {
  const [catalog, setCatalog] = useState<ControlTargetCatalog>();
  const [snapshots, setSnapshots] = useState<Record<string, DeviceSnapshot>>({});
  const [readAt, setReadAt] = useState<Record<string, string>>({});
  const [targetErrors, setTargetErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busyKey, setBusyKey] = useState("");
  const [readingAll, setReadingAll] = useState(false);
  const { hardwareBusy } = useJobActivity();

  const loadCatalog = async () => {
    const value = await api<ControlTargetCatalog>("/api/control-targets");
    setCatalog(value);
  };
  const handleResponse = (value: ControlTargetResponse) => {
    const key = value.target.mapping_key;
    setSnapshots((current) => ({ ...current, [key]: value.snapshot }));
    setReadAt((current) => ({ ...current, [key]: value.read_at }));
    setTargetErrors((current) => ({ ...current, [key]: "" }));
  };
  const handleFailure = (reason: unknown) => {
    const message = String(reason);
    setError(message);
    const code = (reason as { code?: string }).code;
    if (code === "revision_conflict" || message.includes("已变化") || message.includes("409")) {
      setSnapshots({}); setReadAt({}); setTargetErrors({}); void loadCatalog();
    }
  };
  const revisionBody = () => ({
    device_library_revision: catalog?.device_library_revision,
    physical_mapping_revision: catalog?.physical_mapping_revision,
  });
  const readSnapshot = async (target: ControlTarget) => {
    if (!catalog) return;
    setBusyKey(target.mapping_key);
    setError("");
    try {
      handleResponse(await api<ControlTargetResponse>(
        `/api/control-targets/${encodeURIComponent(target.mapping_key)}/refresh`,
        { method: "POST", body: JSON.stringify(revisionBody()) },
      ));
    } catch (reason) {
      setTargetErrors((current) => ({ ...current, [target.mapping_key]: String(reason) }));
      handleFailure(reason);
    } finally {
      setBusyKey("");
    }
  };
  const readAll = async () => {
    if (!catalog) return;
    setReadingAll(true); setError(""); setTargetErrors({});
    try {
      const response = await api<ControlTargetBulkResponse>("/api/control-targets/refresh-all", {
        method: "POST", body: JSON.stringify(revisionBody()),
      });
      const nextSnapshots: Record<string, DeviceSnapshot> = {};
      const nextTimes: Record<string, string> = {};
      const nextErrors: Record<string, string> = {};
      response.results.forEach((item) => {
        const key = item.target.mapping_key;
        if (item.snapshot && item.read_at) {
          nextSnapshots[key] = item.snapshot; nextTimes[key] = item.read_at;
        } else if (item.error) nextErrors[key] = item.error;
      });
      setSnapshots(nextSnapshots); setReadAt(nextTimes); setTargetErrors(nextErrors);
    } catch (reason) { handleFailure(reason); } finally { setReadingAll(false); }
  };
  useEffect(() => {
    void loadCatalog().catch(handleFailure);
  }, []);
  const routeFor = (target: ControlTarget): ControlRoute => ({
    mappingKey: target.mapping_key,
    deviceLibraryRevision: catalog!.device_library_revision,
    physicalMappingRevision: catalog!.physical_mapping_revision,
  });
  const editorFor = (target: ControlTarget, snapshot: DeviceSnapshot) => {
    const route = routeFor(target);
    if (snapshot.type === "SDS") return <ScopeEditor snapshot={snapshot} route={route} onSaved={handleResponse} onError={handleFailure} />;
    if (snapshot.type === "GS200" || snapshot.type === "6221") return <CurrentSourceEditor snapshot={snapshot} route={route} onSaved={handleResponse} onError={handleFailure} />;
    if (snapshot.type === "DLC_PRO") return <LaserEditor snapshot={snapshot} route={route} onSaved={handleResponse} onError={handleFailure} />;
    if (snapshot.type === "TEC103") return <TecEditor snapshot={snapshot} route={route} onSaved={handleResponse} onError={handleFailure} />;
    if (snapshot.type === "HF2") return <Hf2Editor snapshot={snapshot} />;
    return <GeneratorEditor snapshot={snapshot} route={route} onSaved={handleResponse} onError={handleFailure} />;
  };
  return (
    <>
      <div className="config-toolbar control-toolbar"><div><h2>设备控制</h2><p>物理量设置总览</p></div><button onClick={() => void readAll()} disabled={!catalog || readingAll || !!busyKey || hardwareBusy}>{readingAll ? "正在读取全部…" : hardwareBusy ? "其他硬件任务运行中" : "连接并读取全部"}</button></div>
      {error && <div className="alert error">{error}</div>}
      {hardwareBusy && <div className="alert">其他硬件任务正在运行，设备读取与写入暂不可用。</div>}
      <div className="control-target-grid">
        {catalog?.targets.map((target) => {
          const snapshot = snapshots[target.mapping_key];
          const endpoint = target.endpoint ? `${target.endpoint.kind}${target.endpoint.index}` : "设备级";
          return <article className={`control-target-card kind-${target.kind}${snapshot ? " has-snapshot" : ""}`} key={target.mapping_key}>
            <header><div><h3>{target.label}</h3><small>{target.model} · {endpoint}</small></div><button onClick={() => void readSnapshot(target)} disabled={readingAll || !!busyKey || hardwareBusy}>{busyKey === target.mapping_key ? "读取中…" : "读取"}</button></header>
            {target.safety.min != null && target.safety.max != null && <div className="target-limit">{target.safety.min} ～ {target.safety.max} {target.kind === "current_source" ? "mA" : target.kind === "tec" ? "°C" : "V"}</div>}
            {targetErrors[target.mapping_key] && <div className="target-read-error">{targetErrors[target.mapping_key]}</div>}
            {target.mapping_key === "Z_magnetic_field" && <Link to="/tools#z-arbitrary-control">Z 任意波控制：预览、加载与触发 →</Link>}
            {readAt[target.mapping_key] && <small className="read-time">{new Date(readAt[target.mapping_key]).toLocaleTimeString()}</small>}
            {snapshot ? <div className="target-editor">{editorFor(target, snapshot)}</div> : <div className="target-unread">未读取</div>}
          </article>;
        })}
      </div>
    </>
  );
}

type InstrumentView = "mapping" | "library" | "control";

export function InstrumentsPage() {
  const [view, setView] = useState<InstrumentView>("control");
  return (
    <>
      <PageHead eyebrow="INSTRUMENTS" title="仪器控制" description="管理物理量绑定、设备资源和实时参数。" />
      <div className="instrument-tabs" role="tablist">
        <button className={view === "mapping" ? "active" : ""} onClick={() => setView("mapping")}>物理量映射</button>
        <button className={view === "library" ? "active" : ""} onClick={() => setView("library")}>设备库</button>
        <button className={view === "control" ? "active" : ""} onClick={() => setView("control")}>设备控制</button>
      </div>
      {view === "mapping" ? <PhysicalMappingsView /> : view === "library" ? <DeviceLibraryView /> : <DeviceControlView />}
    </>
  );
}
