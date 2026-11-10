import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ApiError } from "../api";
import { useUnsavedChanges } from "../components/NavigationGuard";
import { PageHead } from "../components/PageHead";
import type { DeviceLibraryDocument } from "../types/api";
import type { components } from "../types/openapi";
import "./devicePorts.css";

type DevicePortProfile = components["schemas"]["DevicePortProfile"];
type DevicePortPreset = components["schemas"]["DevicePortPreset"];
type PhysicalPort = components["schemas"]["PhysicalPort"];
type PortMode = components["schemas"]["PortMode"];
type DevicePortsResponse = components["schemas"]["DevicePortsResponse"];

const newId = (kind: "device" | "port" | "mode") => `${kind}-${crypto.randomUUID()}`;
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const signalName = (value: PhysicalPort["signal_type"]) => value === "optical" ? "光学" : value === "electrical" ? "电学" : "光/电待确认";

/** 预览只投影已记录的单向定义，旧双向记录待确认，不改写原始档案。 */
function effectiveDirection(port: PhysicalPort) {
  const direction = port.direction === "switchable" ? port.modes.find(mode => mode.id === port.selected_mode)?.direction : port.direction;
  return direction === "in" || direction === "out" ? direction : null;
}

function presetFor(model: string, presets: DevicePortPreset[]) {
  const key = model.trim().toUpperCase();
  return presets.find(preset => preset.model.trim().toUpperCase() === key);
}

function copyPorts(preset?: DevicePortPreset): PhysicalPort[] {
  return (preset?.ports ?? []).map(port => ({
    ...structuredClone(port), id: newId("port"),
    modes: port.modes.map(mode => ({ ...mode, id: newId("mode") })),
    selected_mode: null,
  }));
}

function checkedDocument(value: DevicePortsResponse): DevicePortsResponse {
  if (value.schema_version !== 1 || typeof value.revision !== "string" || !Array.isArray(value.devices) || !Array.isArray(value.presets)) {
    throw new Error("端口档案格式不受支持，未载入；请检查服务端后重试。");
  }
  return value;
}

export function DevicePortsPage() {
  const [saved, setSaved] = useState<DevicePortsResponse | null>(null);
  const [draft, setDraft] = useState<DevicePortProfile[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const [library, setLibrary] = useState<DeviceLibraryDocument | null>(null);
  const [libraryLoading, setLibraryLoading] = useState(true);
  const [libraryError, setLibraryError] = useState("");
  const [candidates, setCandidates] = useState<string[]>([]);
  const [presetId, setPresetId] = useState("");
  const [focusedPortId, setFocusedPortId] = useState<string | null>(null);
  const portNames = useRef(new Map<string, HTMLInputElement>());
  const prefix = useId();
  const savedFingerprint = useMemo(() => saved ? JSON.stringify(saved.devices) : null, [saved]);
  const draftFingerprint = useMemo(() => JSON.stringify(draft), [draft]);
  const dirty = saved !== null && savedFingerprint !== draftFingerprint;
  const locked = loading || saving || saved === null;
  useUnsavedChanges(dirty);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setLoadError("");
    setLibraryLoading(true); setLibraryError("");
    void api<DevicePortsResponse>("/api/device-ports", { signal: controller.signal }).then(checkedDocument).then(document => {
      if (controller.signal.aborted) return;
      setSaved(document); setDraft(structuredClone(document.devices));
      setActiveId(current => document.devices.some(device => device.id === current) ? current : document.devices[0]?.id ?? null);
      setCandidates([]); setSaveError(""); setConflict(false); setNotice("");
    }).catch(error => {
      if (!controller.signal.aborted) setLoadError(message(error));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    // 设备库请求独立失败，不能阻止读取和维护已有端口档案。
    void api<DeviceLibraryDocument>("/api/device-library", { signal: controller.signal }).then(document => {
      if (!controller.signal.aborted) setLibrary(document);
    }).catch(error => {
      if (!controller.signal.aborted) { setLibrary(null); setLibraryError(message(error)); }
    }).finally(() => { if (!controller.signal.aborted) setLibraryLoading(false); });
    return () => controller.abort();
  }, [reload]);

  const active = draft.find(device => device.id === activeId);
  const validation = useMemo(() => {
    const problems: string[] = [];
    for (const device of draft) {
      if (!device.name.trim()) problems.push("设备名称不能为空。");
      for (const port of device.ports) {
        const label = `${device.name || "未命名设备"} / ${port.name || "未命名端口"}`;
        if (!port.name.trim()) problems.push(`${label}：端口名称不能为空。`);
        if (port.direction === "switchable") {
          if (port.modes.length === 0) problems.push(`${label}：可切换端口至少需要一个模式。`);
          if (port.modes.some(mode => !mode.name.trim())) problems.push(`${label}：模式名称不能为空。`);
          if (port.selected_mode !== null && !port.modes.some(mode => mode.id === port.selected_mode)) problems.push(`${label}：当前记录模式不存在，请重新选择。`);
        }
      }
    }
    return problems;
  }, [draft]);

  function keepDraft(action: string) {
    return !dirty || window.confirm(`当前有未保存的设备端口修改。${action}将保留全部未保存修改；保存或放弃草稿会作用于全部设备。继续？`);
  }
  function selectDevice(id: string) {
    if (locked || id === activeId || !keepDraft("切换设备")) return;
    setActiveId(id); setFocusedPortId(null);
  }
  function updateDevice(change: Partial<DevicePortProfile>) {
    if (locked || !active) return;
    setDraft(current => current.map(device => device.id === active.id ? { ...device, ...change } : device));
    setNotice("");
  }
  function updatePort(id: string, change: Partial<PhysicalPort>) {
    if (!active || locked) return;
    updateDevice({ ports: active.ports.map(port => port.id === id ? { ...port, ...change } : port) });
  }
  function updateMode(port: PhysicalPort, id: string, change: Partial<PortMode>) {
    updatePort(port.id, { modes: port.modes.map(mode => mode.id === id ? { ...mode, ...change } : mode) });
  }
  function createIndependent() {
    if (locked || !saved || !keepDraft("新建设备")) return;
    const preset = saved.presets.find(item => item.id === presetId);
    const device: DevicePortProfile = {
      id: newId("device"), name: preset ? `${preset.model} 设备` : "新设备", model: preset?.model ?? "",
      device_library_id: null, ports: copyPorts(preset),
    };
    setDraft(current => [...current, device]); setActiveId(device.id); setFocusedPortId(null);
    setNotice("已加入草稿；预置仅复制到这个新设备，未覆盖已有档案。请核实端口与记录模式。");
  }
  function createFromLibrary() {
    if (locked || !saved || libraryLoading || !library || libraryError || !candidates.length || !keepDraft("从设备库建档")) return;
    const devices: DevicePortProfile[] = candidates.filter(id => library.devices[id] && !draft.some(device => device.device_library_id === id)).map(id => {
      const source = library.devices[id];
      return { id: newId("device"), name: source.label || id, model: source.model, device_library_id: id, ports: copyPorts(presetFor(source.model, saved.presets)) };
    });
    if (!devices.length) return;
    setDraft(current => [...current, ...devices]); setActiveId(devices[0].id); setFocusedPortId(null); setCandidates([]);
    setNotice(`已为 ${devices.length} 台设备各自建立草稿；无预置型号保留空白端口待填。设备库未被修改。`);
  }
  function removeDevice() {
    if (locked || !active || !window.confirm(`从草稿删除设备“${active.name}”及其端口？其余设备的未保存修改仍保留，保存或放弃会作用于全部设备。`)) return;
    const next = draft.filter(device => device.id !== active.id);
    setDraft(next); setActiveId(next[0]?.id ?? null); setFocusedPortId(null); setNotice("设备已从草稿删除，保存全部档案后生效。");
  }
  function discard() {
    if (locked || !saved || !dirty || !window.confirm("放弃全部设备的未保存修改，恢复到上次读取或成功保存的端口档案？")) return;
    setDraft(structuredClone(saved.devices));
    setActiveId(current => saved.devices.some(device => device.id === current) ? current : saved.devices[0]?.id ?? null);
    setFocusedPortId(null); setSaveError(""); setNotice("已放弃全部设备草稿；未重新读取服务端。如需获取其他窗口的修改，请重新载入。");
  }
  function reloadDocument() {
    if (saving || loading || (dirty && !window.confirm("重新载入会丢弃全部设备的未保存修改，并读取服务端最新档案及设备库。确定重新载入？"))) return;
    setReload(value => value + 1);
  }
  async function save() {
    if (locked || !saved || !dirty || validation.length) return;
    if (loadError) { setSaveError("重新载入尚未成功，已暂停保存；现有草稿保留，请重新载入成功后再保存。"); return; }
    setSaving(true); setSaveError(""); setNotice("");
    try {
      const document = checkedDocument(await api<DevicePortsResponse>("/api/device-ports", {
        method: "PUT", body: JSON.stringify({ base_revision: saved.revision, devices: draft }),
      }));
      setSaved(document); setDraft(structuredClone(document.devices)); setConflict(false);
      setNotice("全部设备端口档案已保存；没有连接硬件或下发任何设置。");
    } catch (error) {
      setConflict((error as ApiError).code === "revision_conflict" || /409/.test(message(error)));
      setSaveError(message(error));
    } finally { setSaving(false); }
  }
  function locatePort(id: string) {
    setFocusedPortId(id);
    const input = portNames.current.get(id);
    input?.scrollIntoView?.({ behavior: "smooth", block: "center" });
    input?.focus({ preventScroll: true });
  }
  function sourceWarning(device: DevicePortProfile) {
    if (!device.device_library_id || libraryLoading || !library || libraryError) return null;
    const source = library.devices[device.device_library_id];
    if (!source) return "来源设备已不在设备库中；档案和端口仍保留，不会自动删除。";
    if (source.model !== device.model) return `来源设备库型号现为 ${source.model}，档案型号为 ${device.model || "未填写"}；未自动替换或生成端口，请人工核对。`;
    return null;
  }
  const groups = [
    { direction: "in", label: "输入端口", className: "ports-in" },
    { direction: "out", label: "输出端口", className: "ports-out" },
    { direction: null, label: "待确认端口", className: "ports-unknown" },
  ] as const;

  return <div className="device-ports-page">
    <PageHead eyebrow="DEVICE PORTS" title="设备端口建档" description="整理设备与物理端口，仅维护档案，不连接或控制硬件。"
      action={<div style={{ display: "flex", flexWrap: "wrap", gap: 16 }}><Link to="/instruments/wiring">实验接线图</Link><Link to="/instruments">返回仪器控制</Link></div>} />
    <div className="ports-safety-note" role="note"><strong>档案记录，非硬件实际状态。</strong>端口单次使用仅输入或输出；复用端口按功能模式记录单向定义，历史双向记录保留但待确认。方向和当前记录模式均由用户确认；选模式只更新档案，不读取设备、不下发设置。接线关系请到独立实验接线图页面手工维护；此处不验证连线或自动同步图内快照。</div>
    <div className="ports-toolbar">
      <div><strong>{saved === null ? "档案尚未载入" : dirty ? "有未保存修改" : "全部档案已保存"}</strong><span>保存 / 放弃草稿作用于全部设备{saved ? ` · 修订 ${saved.revision}` : " · 尚未读取档案"}</span></div>
      <div><button onClick={() => void save()} disabled={locked || !!loadError || !dirty || validation.length > 0}>{saving ? "正在保存…" : "保存全部档案"}</button>
        <button className="secondary" onClick={discard} disabled={locked || !dirty}>放弃全部草稿</button>
        <button className="secondary" onClick={reloadDocument} disabled={loading || saving}>重新载入</button></div>
    </div>
    {loading && <p className="alert" role="status">正在读取端口档案…</p>}
    {loadError && <div className="alert error" role="alert">{saved ? "重新载入失败，现有草稿未被覆盖；可继续查看和编辑草稿，但保存已暂停，需重新载入成功后才能保存：" : "端口档案读取失败，已禁止新建和保存，避免空档覆盖；尚无可用草稿，需重新载入成功后才能继续："}{loadError}<span>若新后端接口尚未加载，请在任务安全结束后由操作者重启现有后端；页面不会自动重启或连接硬件。</span><button className="secondary" disabled={loading || saving} onClick={reloadDocument}>重试读取档案</button></div>}
    {saveError && <div className="alert error" role="alert">{conflict ? "保存冲突（409），草稿与基础修订已保留。请先确认后重新载入最新档案；重新载入将丢弃全部草稿。" : "保存失败，草稿与基础修订已保留，可重试。"}<span>{saveError}</span>{conflict && <button className="secondary" onClick={reloadDocument} disabled={saving || loading}>确认重新载入最新档案</button>}</div>}
    {conflict && !saveError && <p className="alert">服务端曾报告修订冲突；放弃草稿不会读取最新档案，请显式重新载入。</p>}
    {notice && <p className="alert success" role="status">{notice}</p>}
    {validation.length > 0 && <div className="alert error" role="alert"><strong>请补全后保存全部档案：</strong><ul>{validation.map((problem, index) => <li key={index}>{problem}</li>)}</ul></div>}
    <fieldset className="ports-workspace" disabled={locked} aria-label="设备端口草稿">
      <section className="ports-panel ports-device-list" aria-labelledby={`${prefix}-devices`}>
        <header><span className="ports-eyebrow">01 / 设备</span><h2 id={`${prefix}-devices`}>设备列表 <small>{draft.length}</small></h2></header>
        <p className="ports-help">同页切换保留整份草稿；保存与放弃均包含所有设备。</p>
        <div className="ports-device-items" aria-label="已建档设备">{draft.map(device => <button key={device.id} className={device.id === activeId ? "ports-device active" : "ports-device"}
          aria-pressed={device.id === activeId} onClick={() => selectDevice(device.id)}>
          <strong>{device.name || "未命名设备"}</strong><span>{device.model || "型号待填"} · {device.ports.length} 个端口</span>
          <small>{device.device_library_id ? "来源：设备库" : "独立档案"}{sourceWarning(device) ? " · 来源需核对" : ""}</small>
        </button>)}</div>
        {!draft.length && <p className="ports-empty">还没有设备档案。可从设备库多选建档，或独立新建光学 / 电学设备。</p>}
        <div className="ports-create">
          <h3>从设备库建档</h3><p className="ports-help">复用设备库稳定 ID，不扫描、不写入设备库。已建档的来源不可重复添加。</p>
          {libraryLoading && <p role="status">正在读取设备库…</p>}
          {libraryError && <p className="ports-warning" role="alert">设备库读取失败：{libraryError}。仅禁用库建档，不影响已有档案或独立建档。可通过重新载入重试。</p>}
          <div className="ports-candidates">{library && Object.entries(library.devices).map(([id, device]) => {
            const exists = draft.some(profile => profile.device_library_id === id);
            return <label key={id} className="ports-candidate"><input type="checkbox" disabled={exists || libraryLoading || !!libraryError} checked={!exists && candidates.includes(id)}
              onChange={event => setCandidates(current => event.target.checked ? [...current, id] : current.filter(value => value !== id))} />
              <span>{device.label || id}<small>{device.model}{exists ? " · 已建档" : presetFor(device.model, saved?.presets ?? []) ? " · 使用端口预置" : " · 无预置，空白待填"}</small></span></label>;
          })}</div>
          {library && !Object.keys(library.devices).length && <p className="ports-empty">设备库暂无设备。</p>}
          <button className="secondary" onClick={createFromLibrary} disabled={libraryLoading || !library || !!libraryError || !candidates.length}>为所选设备建档</button>
        </div>
        <div className="ports-create"><h3>独立新建设备</h3>
          <label><span>新设备预置</span><select value={presetId} onChange={event => setPresetId(event.target.value)}><option value="">空白设备（含光学 / 被动设备）</option>{saved?.presets.map(preset => <option key={preset.id} value={preset.id}>{preset.name}</option>)}</select></label>
          <p className="ports-help">预置只复制给单个新设备，不重载或覆盖现有设备；模式初始待确认。</p>
          <button className="secondary" onClick={createIndependent}>新建独立设备</button>
        </div>
      </section>
      <section className="ports-panel ports-editor" aria-labelledby={`${prefix}-editor`}>
        <header><span className="ports-eyebrow">02 / 端口档案</span><h2 id={`${prefix}-editor`}>设备与端口编辑</h2></header>
        {active ? <>
          {sourceWarning(active) && <p className="ports-warning" role="alert">{sourceWarning(active)}</p>}
          <div className="ports-fields"><label><span>设备名称</span><input value={active.name} onChange={event => updateDevice({ name: event.target.value })} /></label>
            <label><span>设备型号</span><input value={active.model} placeholder="可留空待确认" onChange={event => updateDevice({ model: event.target.value })} /></label></div>
          <div className="ports-source"><span>{active.device_library_id ? `设备库来源：${active.device_library_id}` : "独立设备档案（不写入设备库）"}</span><button className="secondary ports-danger" onClick={removeDevice}>删除设备</button></div>
          <div className="ports-section-head"><h3>物理端口 <small>{active.ports.length}</small></h3><button className="secondary" onClick={() => {
            const id = newId("port");
            updateDevice({ ports: [...active.ports, { id, name: `端口 ${active.ports.length + 1}`, group: "", signal_type: null, connector: "", purpose: "", direction: null, modes: [], selected_mode: null }] });
            setFocusedPortId(id);
          }}>添加端口</button></div>
          {!active.ports.length && <p className="ports-empty">尚无物理端口。请按设备面板和说明书逐项建档，不自动猜测光电类型或方向。</p>}
          <div className="ports-edit-list">{active.ports.map((port, index) => <fieldset key={port.id} className={`ports-port-card${focusedPortId === port.id ? " focused" : ""}`} aria-label={`端口 ${index + 1} 编辑`} onFocusCapture={() => setFocusedPortId(port.id)}>
            <legend>端口 {index + 1}</legend>
            <div className="ports-fields"><label><span>端口名称</span><input ref={element => { if (element) portNames.current.set(port.id, element); else portNames.current.delete(port.id); }} value={port.name} onChange={event => updatePort(port.id, { name: event.target.value })} /></label>
              <label><span>端口分组</span><input value={port.group} placeholder="如前面板 / 光路" onChange={event => updatePort(port.id, { group: event.target.value })} /></label>
              <label><span>端口方向</span><select value={port.direction ?? ""} onChange={event => {
                const direction = (event.target.value || null) as PhysicalPort["direction"];
                updatePort(port.id, { direction, modes: direction === "switchable" ? port.modes.length ? port.modes : [{ id: newId("mode"), name: "模式 1", direction: "in" }] : [], selected_mode: null });
              }}><option value="">待确认</option><option value="in">输入</option><option value="out">输出</option>{port.direction === "inout" && <option value="inout" disabled>历史双向记录（请确认单向）</option>}<option value="switchable">可切换（按记录模式）</option></select></label>
              <label><span>光电类型</span><select value={port.signal_type ?? ""} onChange={event => updatePort(port.id, { signal_type: (event.target.value || null) as PhysicalPort["signal_type"] })}><option value="">待确认</option><option value="optical">光学</option><option value="electrical">电学</option></select></label>
              <label><span>物理接口</span><input value={port.connector} placeholder="如 BNC / SMA / 自由空间" onChange={event => updatePort(port.id, { connector: event.target.value })} /></label>
              <label><span>端口用途</span><input value={port.purpose} onChange={event => updatePort(port.id, { purpose: event.target.value })} /></label></div>
            {port.direction === "switchable" && <div className="ports-modes">
              <h4>可切换模式</h4><p className="ports-help">至少一个模式；名称记录功能（如同步、触发、门控、MOD 的 AM / FM），每种功能单次仅输入或输出，不按名称猜测方向。模式方向为档案定义，不代表设备当前状态；历史双向模式保留但待确认。改为固定方向会清空模式记录。</p>
              {port.modes.map((mode, modeIndex) => <div key={mode.id} className="ports-mode" role="group" aria-label={`模式 ${modeIndex + 1}`}>
                <label><span>模式名称</span><input value={mode.name} onChange={event => updateMode(port, mode.id, { name: event.target.value })} /></label>
                <label><span>模式方向</span><select value={mode.direction} onChange={event => updateMode(port, mode.id, { direction: event.target.value as PortMode["direction"] })}><option value="in">输入</option><option value="out">输出</option>{mode.direction === "inout" && <option value="inout" disabled>历史双向记录（请确认单向）</option>}</select></label>
                <button className="secondary ports-danger" disabled={port.modes.length <= 1} title={port.modes.length <= 1 ? "至少保留一个模式" : "删除此模式，若已选中则恢复待确认"} onClick={() => updatePort(port.id, { modes: port.modes.filter(item => item.id !== mode.id), selected_mode: port.selected_mode === mode.id ? null : port.selected_mode })}>删除模式</button>
              </div>)}
              <button className="secondary" onClick={() => updatePort(port.id, { modes: [...port.modes, { id: newId("mode"), name: `模式 ${port.modes.length + 1}`, direction: "in" }] })}>添加模式</button>
              <label className="ports-selected-mode"><span>当前记录模式（仅档案）</span><select value={port.selected_mode ?? ""} onChange={event => updatePort(port.id, { selected_mode: event.target.value || null })}><option value="">待确认（未记录）</option>{port.modes.map(mode => <option key={mode.id} value={mode.id}>{mode.name}</option>)}</select></label>
            </div>}
            <div className="ports-port-actions"><span>稳定 ID 自动保留，改名不会改变标识。</span><button className="secondary ports-danger" onClick={() => updateDevice({ ports: active.ports.filter(item => item.id !== port.id) })}>删除端口</button></div>
          </fieldset>)}</div>
        </> : <p className="ports-empty">从左侧选择或新建设备，开始维护物理端口。</p>}
      </section>
      <section className="ports-panel ports-preview" aria-labelledby={`${prefix}-preview`}>
        <header><span className="ports-eyebrow">03 / 实时预览</span><h2 id={`${prefix}-preview`}>实时黑盒图</h2></header>
        <p className="ports-help">仅按档案方向排列。点击端口定位并聚焦编辑项；光学 / 电学同时以文字和颜色区分。</p>
        {active ? <><div className="ports-blackbox">
          <div className="ports-blackbox-body"><strong>{active.name || "未命名设备"}</strong><span>{active.model || "型号待确认"}</span><small>设备黑盒 · 不表示物理连线</small></div>
          {groups.map(group => <section key={group.label} className={`ports-diagram-group ${group.className}`} aria-label={group.label}>
            <h3>{group.label}</h3>
            {active.ports.filter(port => effectiveDirection(port) === group.direction).map(port => <button key={port.id} className={`ports-diagram-port signal-${port.signal_type ?? "unknown"}${focusedPortId === port.id ? " focused" : ""}`} aria-label={`定位端口：${active.ports.filter(item => item.name === port.name).length > 1 && port.group ? `${port.group} / ` : ""}${port.name || "未命名端口"}`} onClick={() => locatePort(port.id)}>
              <strong>{port.name || "未命名端口"}</strong><span className="ports-signal">{signalName(port.signal_type)}</span>{port.group && <small>{port.group}</small>}
              {port.direction === "switchable" && <small>记录模式：{port.modes.find(mode => mode.id === port.selected_mode)?.name ?? "待确认"}</small>}
              {(port.direction === "inout" || (port.direction === "switchable" && port.modes.find(mode => mode.id === port.selected_mode)?.direction === "inout")) && <small>历史双向记录（请确认单向）</small>}
            </button>)}
            {!active.ports.some(port => effectiveDirection(port) === group.direction) && <span className="ports-diagram-empty">暂无</span>}
          </section>)}
        </div><p className="ports-preview-note">输入在左，输出在右；端口单次仅输入或输出。可切换端口按所选功能模式的单向定义排列，不按名称猜测；记录模式未选、方向未知或历史双向记录均归入待确认，原始记录不会自动改写。</p></> : <p className="ports-empty">选择设备后显示端口黑盒预览。</p>}
      </section>
    </fieldset>
  </div>;
}
