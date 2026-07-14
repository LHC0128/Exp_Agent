import { useEffect, useMemo, useState } from "react";
import { Navigate, NavLink, Route, Routes, useParams } from "react-router-dom";
import { api, type Device, type Job } from "./api";

const nav = [
  ["/", "总览", "⌂"],
  ["/instruments", "仪器控制", "◫"],
  ["/tools", "功能模块", "◇"],
  ["/experiments", "实验中心", "∿"],
  ["/runs", "历史数据", "≡"],
];

function Status({ tone = "ok", children }: { tone?: string; children: React.ReactNode }) {
  return <span className={`status ${tone}`}><i />{children}</span>;
}

function Layout() {
  return (
    <div className="shell">
      <aside>
        <div className="brand"><span>LHC</span><div>原子磁力仪<small>实验控制台</small></div></div>
        <nav>{nav.map(([path, label, icon]) => <NavLink key={path} to={path} end={path === "/"}><b>{icon}</b>{label}</NavLink>)}</nav>
        <div className="local-note"><Status>本机模式</Status><p>硬件操作仅监听 127.0.0.1</p></div>
      </aside>
      <main><Routes><Route path="/" element={<Overview />} /><Route path="/instruments" element={<Instruments />} /><Route path="/tools" element={<Tools />} /><Route path="/experiments" element={<ExperimentCatalog />} /><Route path="/experiments/:experimentId" element={<Experiment />} /><Route path="/experiment" element={<Navigate to="/experiments/static-sensitivity" replace />} /><Route path="/runs" element={<Runs />} /></Routes></main>
    </div>
  );
}

function PageHead({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: React.ReactNode }) {
  return <header className="page-head"><div><p>{eyebrow}</p><h1>{title}</h1><span>{description}</span></div>{action}</header>;
}

function Overview() {
  const [health, setHealth] = useState<{ status: string; hardware_busy: boolean }>();
  const [devices, setDevices] = useState<Device[]>([]);
  useEffect(() => { api<typeof health>("/api/health").then(setHealth); api<Device[]>("/api/devices").then(setDevices); }, []);
  return <><PageHead eyebrow="SYSTEM OVERVIEW" title="实验系统总览" description="集中管理仪器状态、校准任务与实验测量。" /><section className="hero-grid"><div className="hero-card"><p>硬件工作区</p><h2>{health?.hardware_busy ? "设备任务运行中" : "系统已就绪"}</h2><span>{health?.hardware_busy ? "当前锁定其他硬件写入" : "可以开始读取设备或执行实验"}</span><div className="signal-line"><i /><i /><i /><i /><i /></div></div><div className="metric"><span>已映射设备</span><strong>{devices.length}</strong><small>信号发生器与示波器</small></div></section><section className="section"><div className="section-title"><div><small>QUICK START</small><h2>常用入口</h2></div></div><div className="quick-grid"><NavLink to="/instruments"><b>01</b><h3>读取仪器状态</h3><p>从物理面板同步信号源与示波器参数。</p></NavLink><NavLink to="/tools"><b>02</b><h3>系统准备</h3><p>同步参考时钟并执行 Demod0 安全校相。</p></NavLink><NavLink to="/experiments"><b>03</b><h3>进入实验中心</h3><p>选择测量、标定、优化或硬件验证流程。</p></NavLink></div></section></>;
}

function Instruments() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [selected, setSelected] = useState<Device>();
  const [snapshots, setSnapshots] = useState<Record<string, any>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const cacheSnapshot = (value: any) => setSnapshots((current) => ({ ...current, [value.id]: value }));
  const refresh = async () => { if (!selected) return; setBusy(true); setError(""); try { cacheSnapshot(await api(`/api/devices/${selected.id}/refresh`, { method: "POST" })); } catch (e) { setError(String(e)); } finally { setBusy(false); } };
  useEffect(() => { api<Device[]>("/api/devices").then((items) => { setDevices(items); setSelected(items[0]); }); }, []);
  useEffect(() => { if (selected) refresh(); }, [selected?.id]);
  const snapshot = selected ? snapshots[selected.id] : undefined;
  return <><PageHead eyebrow="INSTRUMENTS" title="仪器控制" description="参数写入后自动回读，以仪器实际状态为准。" action={<button onClick={refresh} disabled={!selected || busy}>{busy ? "正在读取…" : "读取设备参数"}</button>} /><div className="instrument-layout"><div className="device-list">{devices.map((device) => <button className={selected?.id === device.id ? "selected" : ""} onClick={() => setSelected(device)} key={device.id}><span className={`device-icon ${device.type.toLowerCase()}`}>{device.type === "SDS" ? "OSC" : "GEN"}</span><div><strong>{device.label}</strong><small>{device.type} · {device.short_resource}</small></div><i /></button>)}</div><div className="workspace">{error && <div className="alert error">{error}</div>}{!snapshot ? <div className="empty"><span>↻</span><h3>读取当前设备参数</h3><p>选择设备后点击右上角按钮，将仪器面板上的最新设置同步到这里。</p></div> : snapshot.type === "SDS" ? <ScopeEditor key={snapshot.id} snapshot={snapshot} onSaved={cacheSnapshot} /> : <GeneratorEditor key={snapshot.id} snapshot={snapshot} onSaved={cacheSnapshot} />}</div></div></>;
}

function Field({ label, value, onChange, type = "number", disabled = false }: { label: string; value: any; onChange: (value: any) => void; type?: string; disabled?: boolean }) {
  return <label><span>{label}</span><input type={type} value={value ?? ""} disabled={disabled} onChange={(event) => onChange(type === "number" ? Number(event.target.value) : event.target.value)} /></label>;
}

function SelectField({ label, value, onChange, disabled = false, children }: { label: string; value: any; onChange: (value: string) => void; disabled?: boolean; children: React.ReactNode }) {
  return <label><span>{label}</span><select value={value ?? ""} disabled={disabled} onChange={(event) => onChange(event.target.value)}>{children}</select></label>;
}

function GeneratorEditor({ snapshot, onSaved }: { snapshot: any; onSaved: (value: any) => void }) {
  const [channels, setChannels] = useState<any[]>(snapshot.channels);
  const [saving, setSaving] = useState<number>();
  const [tabs, setTabs] = useState<Record<number, "wave" | "mod" | "burst">>({});
  const [error, setError] = useState("");
  useEffect(() => { setChannels(snapshot.channels); }, [snapshot]);
  const update = (index: number, key: string, value: any) => setChannels((items) => items.map((item, i) => i === index ? { ...item, [key]: value } : item));
  const updateNested = (index: number, group: "mod" | "burst", key: string, value: any) => setChannels((items) => items.map((item, i) => i === index ? { ...item, [group]: { ...item[group], [key]: value } } : item));
  const setMode = (index: number, mode: "mod" | "burst", enabled: boolean) => setChannels((items) => items.map((item, i) => {
    if (i !== index) return item;
    if (mode === "mod") return { ...item, target_mode: "mod", mod: { ...item.mod, enabled, type: item.mod.type || "AM" }, burst: { ...item.burst, enabled: enabled ? false : item.burst.enabled } };
    return { ...item, target_mode: "burst", burst: { ...item.burst, enabled }, mod: { ...item.mod, enabled: enabled ? false : item.mod.enabled } };
  }));
  const save = async (index: number) => { const channel = channels[index]; setSaving(channel.number); setError(""); try { onSaved(await api(`/api/devices/${snapshot.id}/channels/${channel.number}`, { method: "PUT", body: JSON.stringify({ settings: channel }) })); } catch (e) { setError(String(e)); } finally { setSaving(undefined); } };
  return <>
    <div className="workspace-head"><div><Status>{snapshot.reference_clock}</Status><h2>{snapshot.label}</h2><p>{snapshot.idn}</p></div></div>
    {error && <div className="alert error">{error}</div>}
    {channels.map((channel, index) => {
      const tab = tabs[channel.number] || "wave";
      const disabled = channel.read_only;
      const mod = channel.mod || {};
      const burst = channel.burst || {};
      const modType = mod.type || "AM";
      return <section className="panel generator-panel" key={channel.number}>
        <div className="panel-head"><div><small>CHANNEL {channel.number}</small><h3>{channel.label}</h3></div><label className="switch"><input type="checkbox" checked={channel.output} disabled={disabled} onChange={(e) => update(index, "output", e.target.checked)} /><span /></label></div>
        {disabled && <div className="alert">该通道按仓库约定只读，以下参数仅展示设备回读值。</div>}
        {channel.readback_errors?.length > 0 && <div className="alert error">部分参数不可用：{channel.readback_errors.map((item: any) => item.field).join("、")}</div>}
        <div className="mode-tabs" role="tablist">
          <button className={tab === "wave" ? "active" : ""} onClick={() => setTabs((old) => ({ ...old, [channel.number]: "wave" }))}>基础波形</button>
          <button className={tab === "mod" ? "active" : ""} onClick={() => setTabs((old) => ({ ...old, [channel.number]: "mod" }))}>调制</button>
          <button className={tab === "burst" ? "active" : ""} onClick={() => setTabs((old) => ({ ...old, [channel.number]: "burst" }))}>Burst</button>
        </div>
        {tab === "wave" && <div className="form-grid">
          <SelectField label="波形" value={channel.shape} disabled={disabled} onChange={(v) => update(index, "shape", v)}><option value="SINusoid">Sine</option><option value="SQUare">Square</option><option value="RAMP">Ramp</option><option value="PULSe">Pulse</option><option value="DC">DC</option></SelectField>
          {!String(channel.shape).startsWith("DC") && <><Field label="频率 (Hz)" value={channel.frequency} disabled={disabled} onChange={(v) => update(index, "frequency", v)} /><Field label="幅度 (Vpp)" value={channel.amplitude} disabled={disabled} onChange={(v) => update(index, "amplitude", v)} /></>}
          <Field label="偏置 (V)" value={channel.offset} disabled={disabled} onChange={(v) => update(index, "offset", v)} />
          {!String(channel.shape).startsWith("DC") && <Field label="相位 (deg)" value={channel.phase} disabled={disabled} onChange={(v) => update(index, "phase", v)} />}
          {snapshot.type === "DG4000" && <><SelectField label="电压单位" value={channel.voltage_unit} disabled={disabled} onChange={(v) => update(index, "voltage_unit", v)}><option value="VPP">Vpp</option><option value="VRMS">Vrms</option><option value="DBM">dBm</option></SelectField><Field label="负载 (Ω / INF)" value={channel.load} type="text" disabled={disabled} onChange={(v) => update(index, "load", v)} /></>}
          {String(channel.shape).startsWith("SQU") && <Field label="占空比 (%)" value={channel.square_duty} disabled={disabled} onChange={(v) => update(index, "square_duty", v)} />}
          {String(channel.shape).startsWith("RAMP") && <Field label="对称度 (%)" value={channel.ramp_symmetry} disabled={disabled} onChange={(v) => update(index, "ramp_symmetry", v)} />}
          {String(channel.shape).startsWith("PUL") && <><Field label="脉宽 (s)" value={channel.pulse_width} disabled={disabled} onChange={(v) => update(index, "pulse_width", v)} />{snapshot.type === "DG4000" && <Field label="脉冲延迟 (s)" value={channel.pulse_delay} disabled={disabled} onChange={(v) => update(index, "pulse_delay", v)} />}</>}
        </div>}
        {tab === "mod" && <>
          <div className="mode-enable"><div><strong>调制输出</strong><span>启用时自动关闭 Burst</span></div><label className="switch"><input type="checkbox" checked={Boolean(mod.enabled)} disabled={disabled} onChange={(e) => setMode(index, "mod", e.target.checked)} /><span /></label></div>
          <div className="form-grid">
            <SelectField label="调制类型" value={modType} disabled={disabled} onChange={(v) => updateNested(index, "mod", "type", v)}><option value="AM">AM</option><option value="FM">FM</option><option value="PM">PM</option><option value="FSKey">FSK</option><option value="PWM">PWM</option></SelectField>
            <SelectField label="调制源" value={mod.source || "INTernal"} disabled={disabled} onChange={(v) => updateNested(index, "mod", "source", v)}><option value="INTernal">Internal</option><option value="EXTernal">External</option></SelectField>
            {modType === "AM" && <Field label="调制深度 (%)" value={mod.am_depth} disabled={disabled} onChange={(v) => updateNested(index, "mod", "am_depth", v)} />}
            {modType === "FM" && <Field label="频率偏差 (Hz)" value={mod.fm_deviation} disabled={disabled} onChange={(v) => updateNested(index, "mod", "fm_deviation", v)} />}
            {modType === "PM" && <Field label="相位偏差 (deg)" value={mod.pm_deviation} disabled={disabled} onChange={(v) => updateNested(index, "mod", "pm_deviation", v)} />}
            {modType === "FSKey" && <><Field label="跳跃频率 (Hz)" value={mod.fsk_frequency} disabled={disabled} onChange={(v) => updateNested(index, "mod", "fsk_frequency", v)} /><Field label="内部速率 (Hz)" value={mod.fsk_rate} disabled={disabled || mod.source === "EXTernal"} onChange={(v) => updateNested(index, "mod", "fsk_rate", v)} /><SelectField label="极性" value={mod.fsk_polarity || (snapshot.type === "DG900" ? "POSitive" : "NORMal")} disabled={disabled} onChange={(v) => updateNested(index, "mod", "fsk_polarity", v)}>{snapshot.type === "DG900" ? <><option value="POSitive">Positive</option><option value="NEGative">Negative</option></> : <><option value="NORMal">Normal</option><option value="INVerted">Inverted</option></>}</SelectField></>}
            {modType === "PWM" && <Field label="占空比偏差 (%)" value={mod.pwm_duty_deviation} disabled={disabled} onChange={(v) => updateNested(index, "mod", "pwm_duty_deviation", v)} />}
            {mod.source !== "EXTernal" && modType !== "FSKey" && <><Field label="内部调制频率 (Hz)" value={mod.internal_frequency} disabled={disabled} onChange={(v) => updateNested(index, "mod", "internal_frequency", v)} /><SelectField label="内部调制波形" value={mod.internal_function || "SINusoid"} disabled={disabled} onChange={(v) => updateNested(index, "mod", "internal_function", v)}><option value="SINusoid">Sine</option><option value="SQUare">Square</option><option value="TRIangle">Triangle</option><option value="RAMP">Ramp Up</option><option value="NRAMp">Ramp Down</option><option value="NOISe">Noise</option></SelectField></>}
          </div>
        </>}
        {tab === "burst" && <>
          <div className="mode-enable"><div><strong>Burst 输出</strong><span>启用时自动关闭调制</span></div><label className="switch"><input type="checkbox" checked={Boolean(burst.enabled)} disabled={disabled} onChange={(e) => setMode(index, "burst", e.target.checked)} /><span /></label></div>
          <div className="form-grid">
            <SelectField label="Burst 模式" value={burst.mode || "TRIGgered"} disabled={disabled} onChange={(v) => updateNested(index, "burst", "mode", v)}><option value="TRIGgered">Triggered</option><option value="GATed">Gated</option>{snapshot.type === "DG4000" && <option value="INFinity">Infinity</option>}</SelectField>
            {burst.mode !== "GATed" && <><Field label="循环数 / INF" value={burst.ncycles} type="text" disabled={disabled} onChange={(v) => updateNested(index, "burst", "ncycles", v)} /><Field label="起始相位 (deg)" value={burst.phase} disabled={disabled} onChange={(v) => updateNested(index, "burst", "phase", v)} /><Field label="Burst 周期 (s)" value={burst.period} disabled={disabled} onChange={(v) => updateNested(index, "burst", "period", v)} /><Field label="触发延迟 (s)" value={burst.delay} disabled={disabled} onChange={(v) => updateNested(index, "burst", "delay", v)} /></>}
            <SelectField label="触发源" value={burst.trigger_source || (snapshot.type === "DG900" ? "IMMediate" : "INTernal")} disabled={disabled} onChange={(v) => updateNested(index, "burst", "trigger_source", v)}>{snapshot.type === "DG900" ? <><option value="IMMediate">Internal</option><option value="EXTernal">External</option><option value="BUS">Manual/Bus</option><option value="TIMer">Timer</option></> : <><option value="INTernal">Internal</option><option value="EXTernal">External</option><option value="MANual">Manual</option></>}</SelectField>
            {burst.trigger_source === "EXTernal" && <SelectField label="触发边沿" value={burst.trigger_slope || "POSitive"} disabled={disabled} onChange={(v) => updateNested(index, "burst", "trigger_slope", v)}><option value="POSitive">Rising</option><option value="NEGative">Falling</option></SelectField>}
          </div>
        </>}
        <div className="panel-actions"><button disabled={disabled || saving === channel.number} onClick={() => save(index)}>{saving === channel.number ? "应用中…" : "应用并回读"}</button></div>
      </section>;
    })}
  </>;
}

function ScopeEditor({ snapshot, onSaved }: { snapshot: any; onSaved: (value: any) => void }) {
  const [state, setState] = useState(snapshot);
  const set = (key: string, value: any) => setState((old: any) => ({ ...old, [key]: value }));
  const triggerSet = (key: string, value: any) => setState((old: any) => ({ ...old, trigger: { ...old.trigger, [key]: value } }));
  const channelSet = (index: number, key: string, value: any) => setState((old: any) => ({ ...old, channels: old.channels.map((item: any, i: number) => i === index ? { ...item, [key]: value } : item) }));
  const save = async () => onSaved(await api(`/api/devices/${snapshot.id}/scope`, { method: "PUT", body: JSON.stringify({ settings: state }) }));
  return <><div className="workspace-head"><div><Status>CONNECTED</Status><h2>{snapshot.label}</h2><p>{snapshot.idn}</p></div><button onClick={save}>应用并回读</button></div><section className="panel"><div className="panel-head"><h3>采集与时基</h3></div><div className="form-grid"><Field label="采样率 (Sa/s)" value={state.sampling_rate} onChange={(v) => set("sampling_rate", v)} /><Field label="存储深度" value={state.memory_depth} type="text" onChange={(v) => set("memory_depth", v)} /><label><span>采集模式</span><select value={state.acquire_type} onChange={(e) => set("acquire_type", e.target.value)}><option value="NORMal">Normal</option><option value="PEAK">Peak</option><option value="AVERage">Average</option><option value="ERES">ERES</option></select></label><Field label="时基 (s/div)" value={state.timebase_scale} onChange={(v) => set("timebase_scale", v)} /><Field label="时基延迟 (s)" value={state.timebase_delay} onChange={(v) => set("timebase_delay", v)} /></div></section><section className="panel"><div className="panel-head"><h3>边沿触发</h3></div><div className="form-grid"><label><span>模式</span><select value={state.trigger.mode} onChange={(e) => triggerSet("mode", e.target.value)}><option value="AUTO">Auto</option><option value="NORMal">Normal</option><option value="SINGle">Single</option></select></label><label><span>来源</span><select value={state.trigger.source} onChange={(e) => triggerSet("source", e.target.value)}><option value="C1">C1</option><option value="C2">C2</option><option value="C3">C3</option><option value="C4">C4</option><option value="EXT">EXT</option></select></label><label><span>斜率</span><select value={state.trigger.slope} onChange={(e) => triggerSet("slope", e.target.value)}><option value="RISing">Rising</option><option value="FALLing">Falling</option><option value="ALTernate">Alternate</option></select></label><Field label="触发电平 (V)" value={state.trigger.level} onChange={(v) => triggerSet("level", v)} /></div></section><section className="scope-channels">{state.channels.map((channel: any, index: number) => <div className="panel" key={channel.number}><div className="panel-head"><h3>CH{channel.number}</h3><label className="switch"><input type="checkbox" checked={channel.enabled} onChange={(e) => channelSet(index, "enabled", e.target.checked)} /><span /></label></div><Field label="量程 (V/div)" value={channel.scale} onChange={(v) => channelSet(index, "scale", v)} /><Field label="偏置 (V)" value={channel.offset} onChange={(v) => channelSet(index, "offset", v)} /><label><span>耦合</span><select value={channel.coupling} onChange={(e) => channelSet(index, "coupling", e.target.value)}><option value="DC">DC</option><option value="AC">AC</option><option value="GND">GND</option></select></label><label><span>阻抗</span><select value={channel.impedance} onChange={(e) => channelSet(index, "impedance", e.target.value)}><option value="ONEMeg">1 MΩ</option><option value="FIFTy">50 Ω</option></select></label><Field label="探头倍率" value={channel.probe} onChange={(v) => channelSet(index, "probe", v)} /></div>)}</section></>;
}

function JobView({ job, onUpdate }: { job?: Job; onUpdate: (job: Job) => void }) {
  useEffect(() => { if (!job || !["queued", "running"].includes(job.status)) return; const timer = window.setInterval(() => api<Job>(`/api/jobs/${job.id}`).then(onUpdate), 700); return () => window.clearInterval(timer); }, [job?.id, job?.status]);
  if (!job) return null;
  const result = job.result as { run_dir?: string } | undefined;
  return <div className="job"><div className="job-top"><Status tone={job.status === "failed" ? "bad" : job.status === "completed" ? "ok" : "work"}>{job.status}</Status><strong>{job.message}</strong><span>{Math.round(job.percent || 0)}%</span></div><div className="progress"><i style={{ width: `${job.percent || 0}%` }} /></div>{job.status === "completed" && result?.run_dir && <div className="alert success job-result">结果已保存：{result.run_dir}</div>}<div className="log">{job.events.slice(-12).map((event, index) => <p key={index}><time>{event.timestamp?.split("T")[1]}</time>{event.message}</p>)}</div>{job.status === "running" && job.kind.startsWith("experiment:") && <button className="danger" onClick={() => api<Job>(`/api/jobs/${job.id}/cancel`, { method: "POST" }).then(onUpdate)}>安全停止</button>}</div>;
}

function Tools() {
  const [job, setJob] = useState<Job>();
  const startClock = () => api<Job>("/api/clocks/sync", { method: "POST" }).then(setJob);
  const startPhase = () => api<Job>("/api/tools/demod0-phase-calibration", { method: "POST", body: JSON.stringify({ tolerance_deg: 1, max_attempts: 5, settle_time: 0.2 }) }).then(setJob);
  return <><PageHead eyebrow="SYSTEM TOOLS" title="功能模块" description="执行跨设备准备流程；同一时间只允许运行一个硬件任务。" /><div className="tool-grid"><article><small>REFERENCE CLOCK</small><h2>参考时钟同步</h2><p>按共享时钟配置逐台设置并回读信号发生器与 HF2。</p><button onClick={startClock}>开始同步</button></article><article><small>DEMODULATOR 0</small><h2>相位自动校准</h2><p>自动关闭 Z 场和温控干扰，完成后恢复原始状态。</p><button onClick={startPhase}>开始安全校相</button></article></div><JobView job={job} onUpdate={setJob} /></>;
}

type SchemaOption = { value: string; label: string };
type SchemaField = { name: string; label: string; unit: string; group: string; default: any; minimum?: number; maximum?: number; description?: string; options?: SchemaOption[] };
type ParameterGroup = "basic" | "advanced";
type ParameterLayout = Record<ParameterGroup, string[]>;
type ExperimentDefinition = { id: string; title: string; category: string; category_label: string; family: string; variant: string; description: string; required_devices: string[]; acquisition_program: string; analysis_program: string | null; wiring_notes: string[]; safety_notes: string[]; supports_cancel: boolean; can_analyze: boolean };
type ExperimentTag = { id: string; label: string };
type ExperimentCatalogConfig = { schema_version: number; tags: ExperimentTag[]; assignments: Record<string, string>; experiment_descriptions: Record<string, string>; experiment_titles: Record<string, string> };

function ExperimentCatalog() {
  const [experiments, setExperiments] = useState<ExperimentDefinition[]>([]);
  const [tags, setTags] = useState<ExperimentTag[]>([]);
  const [category, setCategory] = useState("all");
  const [error, setError] = useState("");
  const [manageTags, setManageTags] = useState(false);
  const [newTag, setNewTag] = useState("");
  const [tagNames, setTagNames] = useState<Record<string, string>>({});
  const [experimentTitles, setExperimentTitles] = useState<Record<string, string>>({});
  const [experimentDescriptions, setExperimentDescriptions] = useState<Record<string, string>>({});
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState("");
  const loadCatalog = () => Promise.all([
    api<ExperimentDefinition[]>("/api/experiments"),
    api<ExperimentCatalogConfig>("/api/experiment-tags"),
  ]).then(([items, config]) => {
    setExperiments(items);
    setTags(config.tags);
    setTagNames(Object.fromEntries(config.tags.map((tag) => [tag.id, tag.label])));
    setExperimentTitles(Object.fromEntries(items.map((item) => [item.id, item.title])));
    setExperimentDescriptions(Object.fromEntries(items.map((item) => [item.id, item.description])));
    setError("");
  });
  useEffect(() => {
    loadCatalog()
      .catch((reason) => setError(String(reason)));
  }, []);
  const visible = category === "all" ? experiments : experiments.filter((item) => item.category === category);
  const add = async () => {
    if (!newTag.trim()) return;
    setSaving("new"); setStatus("");
    try {
      await api("/api/experiment-tags", { method: "POST", body: JSON.stringify({ label: newTag }) });
      setNewTag(""); await loadCatalog(); setStatus("标签已添加");
    } catch (reason) { setStatus(String(reason)); } finally { setSaving(""); }
  };
  const rename = async (tag: ExperimentTag) => {
    setSaving(tag.id); setStatus("");
    try {
      await api(`/api/experiment-tags/${tag.id}`, { method: "PUT", body: JSON.stringify({ label: tagNames[tag.id] }) });
      await loadCatalog(); setStatus("标签名称已保存");
    } catch (reason) { setStatus(String(reason)); } finally { setSaving(""); }
  };
  const move = async (experimentId: string, tagId: string) => {
    setSaving(experimentId); setStatus("");
    try {
      const result = await api<{ experiment: ExperimentDefinition }>(`/api/experiments/${experimentId}/tag`, { method: "PUT", body: JSON.stringify({ tag_id: tagId }) });
      setExperiments((items) => items.map((item) => item.id === experimentId ? result.experiment : item));
      setStatus(`“${result.experiment.title}”已移动到“${result.experiment.category_label}”`);
    } catch (reason) { setStatus(String(reason)); } finally { setSaving(""); }
  };
  const saveMetadata = async (item: ExperimentDefinition) => {
    setSaving(`metadata:${item.id}`); setStatus("");
    try {
      const result = await api<{ experiment: ExperimentDefinition }>(`/api/experiments/${item.id}/metadata`, { method: "PUT", body: JSON.stringify({ title: experimentTitles[item.id] || "", description: experimentDescriptions[item.id] || "" }) });
      setExperiments((items) => items.map((current) => current.id === item.id ? result.experiment : current));
      setExperimentTitles((current) => ({ ...current, [item.id]: result.experiment.title }));
      setExperimentDescriptions((current) => ({ ...current, [item.id]: result.experiment.description }));
      setStatus(`“${result.experiment.title}”的名称和介绍已保存`);
    } catch (reason) { setStatus(String(reason)); } finally { setSaving(""); }
  };
  return <><PageHead eyebrow="EXPERIMENT CENTER" title="实验中心" description="所有正式 Python 实验使用统一参数、预检、运行与分析入口。" action={<button className="secondary" onClick={() => setManageTags((value) => !value)}>{manageTags ? "完成编辑" : "管理分类与实验信息"}</button>} />{error && <div className="alert error">实验目录加载失败：{error}<button className="secondary" onClick={() => window.location.reload()}>刷新页面</button></div>}{manageTags && <section className="tag-manager"><div className="tag-manager-head"><div><small>TAG MANAGEMENT</small><h2>添加或重命名标签</h2><p>在下方每张实验卡片中修改该实验自己的名称、介绍和所属分类。</p></div><div className="tag-add"><input value={newTag} placeholder="新标签名称" maxLength={30} onChange={(event) => setNewTag(event.target.value)} /><button disabled={saving === "new" || !newTag.trim()} onClick={add}>添加标签</button></div></div><div className="tag-editor-list">{tags.map((tag) => <div key={tag.id}><span>{experiments.filter((item) => item.category === tag.id).length} 个实验</span><input value={tagNames[tag.id] ?? tag.label} maxLength={30} aria-label={`${tag.label}名称`} onChange={(event) => setTagNames((current) => ({ ...current, [tag.id]: event.target.value }))} /><button className="secondary" disabled={saving === tag.id || tagNames[tag.id]?.trim() === tag.label} onClick={() => rename(tag)}>保存名称</button></div>)}</div>{status && <div className={`alert ${status.startsWith("Error") ? "error" : "success"}`}>{status}</div>}</section>}<div className="catalog-filters"><button className={category === "all" ? "active" : ""} onClick={() => setCategory("all")}>全部 <span>{experiments.length}</span></button>{tags.map((tag) => <button className={category === tag.id ? "active" : ""} key={tag.id} onClick={() => setCategory(tag.id)}>{tag.label} <span>{experiments.filter((item) => item.category === tag.id).length}</span></button>)}</div>{status && !manageTags && <div className="alert success">{status}</div>}<div className="experiment-catalog">{visible.map((item) => <article key={item.id}><div className="catalog-meta"><span>{item.category_label}</span></div>{manageTags ? <div className="experiment-metadata-editor"><label><span>实验名称</span><input value={experimentTitles[item.id] ?? item.title} maxLength={80} aria-label={`${item.title}名称`} onChange={(event) => setExperimentTitles((current) => ({ ...current, [item.id]: event.target.value }))} /></label><label><span>实验介绍</span><textarea value={experimentDescriptions[item.id] ?? item.description} maxLength={500} aria-label={`${item.title}介绍`} placeholder="填写这个实验的简要介绍" onChange={(event) => setExperimentDescriptions((current) => ({ ...current, [item.id]: event.target.value }))} /></label><button className="secondary" disabled={saving === `metadata:${item.id}` || ((experimentTitles[item.id] ?? "").trim() === item.title && (experimentDescriptions[item.id] ?? "").trim() === item.description)} onClick={() => saveMetadata(item)}>保存名称和介绍</button></div> : <><h2>{item.title}</h2><p>{item.description || "暂未填写实验介绍。"}</p></>}<div className="catalog-programs"><div><span>采集</span><code>{item.acquisition_program}</code></div><div><span>分析</span><code>{item.analysis_program || "无独立分析程序"}</code></div></div><div className="catalog-card-actions"><NavLink to={`/experiments/${item.id}`}>配置实验 →</NavLink>{manageTags && <label><span>所属分类</span><select value={item.category} disabled={saving === item.id} onChange={(event) => move(item.id, event.target.value)}>{tags.map((tag) => <option key={tag.id} value={tag.id}>{tag.label}</option>)}</select></label>}</div></article>)}</div></>;
}

function defaultParameterLayout(fields: SchemaField[]): ParameterLayout {
  return {
    basic: fields.filter((field) => field.group === "basic").map((field) => field.name),
    advanced: fields.filter((field) => field.group !== "basic").map((field) => field.name),
  };
}

function loadParameterLayout(fields: SchemaField[], storageKey: string): ParameterLayout {
  const fallback = defaultParameterLayout(fields);
  try {
    const saved = JSON.parse(window.localStorage.getItem(storageKey) || "null") as Partial<ParameterLayout> | null;
    if (!saved) return fallback;
    const validNames = new Set(fields.map((field) => field.name));
    const used = new Set<string>();
    const clean = (items: unknown) => Array.isArray(items) ? items.filter((name): name is string => typeof name === "string" && validNames.has(name) && !used.has(name) && Boolean(used.add(name))) : [];
    const layout: ParameterLayout = { basic: clean(saved.basic), advanced: clean(saved.advanced) };
    fields.forEach((field) => {
      if (!used.has(field.name)) layout[field.group === "basic" ? "basic" : "advanced"].push(field.name);
    });
    return layout;
  } catch {
    return fallback;
  }
}

function Experiment() {
  const { experimentId = "static-sensitivity" } = useParams();
  const layoutKey = `exp-agent:${experimentId}:parameter-layout`;
  const collapsedKey = `exp-agent:${experimentId}:advanced-collapsed`;
  const [definition, setDefinition] = useState<ExperimentDefinition>();
  const [fields, setFields] = useState<SchemaField[]>([]);
  const [values, setValues] = useState<Record<string, any>>({});
  const [layout, setLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [advancedCollapsed, setAdvancedCollapsed] = useState(() => window.localStorage.getItem(collapsedKey) !== "false");
  const [dragging, setDragging] = useState<string>();
  const [dropTarget, setDropTarget] = useState<string>();
  const [preflight, setPreflight] = useState<{ ok: boolean; errors: string[] }>();
  const [defaultsStatus, setDefaultsStatus] = useState<{ ok: boolean; message: string }>();
  const [savingDefaults, setSavingDefaults] = useState(false);
  const [starting, setStarting] = useState(false);
  const [job, setJob] = useState<Job>();
  useEffect(() => {
    setDefinition(undefined); setFields([]); setJob(undefined); setPreflight(undefined); setDefaultsStatus(undefined);
    api<ExperimentDefinition>(`/api/experiments/${experimentId}`).then(setDefinition);
    api<{ fields: SchemaField[] }>(`/api/experiments/${experimentId}/schema`).then((schema) => { setFields(schema.fields); setValues(Object.fromEntries(schema.fields.map((item) => [item.name, item.default]))); setLayout(loadParameterLayout(schema.fields, layoutKey)); });
  }, [experimentId]);
  useEffect(() => { api<Job[]>("/api/jobs").then((jobs) => {
    const matching = jobs.filter((item) => item.kind === `experiment:${experimentId}`);
    const recovered = matching.find((item) => ["queued", "running"].includes(item.status)) || matching.find((item) => Boolean(item.started_at));
    if (recovered) setJob(recovered);
  }); }, [experimentId]);
  useEffect(() => { if (fields.length) window.localStorage.setItem(layoutKey, JSON.stringify(layout)); }, [fields.length, layout, layoutKey]);
  useEffect(() => { window.localStorage.setItem(collapsedKey, String(advancedCollapsed)); }, [advancedCollapsed, collapsedKey]);
  const fieldsByName = useMemo(() => new Map(fields.map((field) => [field.name, field])), [fields]);
  const moveParameter = (name: string, group: ParameterGroup, before?: string) => setLayout((current) => {
    const next: ParameterLayout = { basic: current.basic.filter((item) => item !== name), advanced: current.advanced.filter((item) => item !== name) };
    const index = before ? next[group].indexOf(before) : -1;
    next[group].splice(index >= 0 ? index : next[group].length, 0, name);
    return next;
  });
  const reorderParameter = (name: string, direction: -1 | 1) => setLayout((current) => {
    const group: ParameterGroup = current.basic.includes(name) ? "basic" : "advanced";
    const index = current[group].indexOf(name);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= current[group].length) return current;
    const next = { basic: [...current.basic], advanced: [...current.advanced] };
    [next[group][index], next[group][target]] = [next[group][target], next[group][index]];
    return next;
  });
  const resetLayout = () => setLayout(defaultParameterLayout(fields));
  const finishDrop = (group: ParameterGroup, before?: string) => {
    if (dragging && dragging !== before) moveParameter(dragging, group, before);
    setDragging(undefined);
    setDropTarget(undefined);
  };
  const check = () => api<{ ok: boolean; errors: string[] }>(`/api/experiments/${experimentId}/preflight`, { method: "POST", body: JSON.stringify({ parameters: values }) }).then(setPreflight);
  const saveDefaults = async () => {
    setSavingDefaults(true);
    setDefaultsStatus(undefined);
    try {
      const result = await api<{ ok: boolean; message: string; schema: { fields: SchemaField[] } }>(`/api/experiments/${experimentId}/defaults`, { method: "PUT", body: JSON.stringify({ parameters: values }) });
      setFields(result.schema.fields);
      setDefaultsStatus({ ok: true, message: result.message });
    } catch (error) {
      setDefaultsStatus({ ok: false, message: String(error) });
    } finally {
      setSavingDefaults(false);
    }
  };
  const jobActive = Boolean(job && ["queued", "running"].includes(job.status));
  const run = async () => {
    if (starting || jobActive) return;
    setStarting(true);
    try {
      const result = await api<{ ok: boolean; errors: string[] }>(`/api/experiments/${experimentId}/preflight`, { method: "POST", body: JSON.stringify({ parameters: values }) });
      setPreflight(result);
      if (result.ok) setJob(await api<Job>(`/api/experiments/${experimentId}/runs`, { method: "POST", body: JSON.stringify({ parameters: values }) }));
    } finally {
      setStarting(false);
    }
  };
  const renderGroup = (group: ParameterGroup, title: string) => {
    const collapsed = group === "advanced" && advancedCollapsed;
    return <section className={`parameter-group ${collapsed ? "collapsed" : ""} ${dragging ? "drag-active" : ""}`} onDragOver={(event) => { event.preventDefault(); setDropTarget(`${group}:end`); }} onDrop={(event) => { event.preventDefault(); finishDrop(group); }}>
    <div className="parameter-group-head"><div><h3>{title}</h3><span>{layout[group].length}</span></div><div className="parameter-group-actions">{group === "basic" && <button className="icon-button" title="恢复默认分类和顺序" aria-label="恢复默认分类和顺序" onClick={resetLayout}>↺</button>}{group === "advanced" && <button className="icon-button collapse-button" title={collapsed ? "展开高级参数" : "收起高级参数"} aria-label={collapsed ? "展开高级参数" : "收起高级参数"} aria-expanded={!collapsed} onClick={() => setAdvancedCollapsed((current) => !current)}>{collapsed ? "▸" : "▾"}</button>}</div></div>
    {!collapsed && <div className="parameter-list">
      {layout[group].map((name, index) => {
        const field = fieldsByName.get(name);
        if (!field) return null;
        const targetKey = `${group}:${name}`;
        return <div className={`parameter-item ${dragging === name ? "is-dragging" : ""} ${dropTarget === targetKey ? "drop-before" : ""}`} key={name} onDragOver={(event) => { event.preventDefault(); event.stopPropagation(); setDropTarget(targetKey); }} onDrop={(event) => { event.preventDefault(); event.stopPropagation(); finishDrop(group, name); }}>
          <div className="parameter-tools">
            <span className="drag-handle" draggable title="拖动参数" aria-label={`拖动${field.label}`} onDragStart={(event) => { setDragging(name); event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", name); }} onDragEnd={() => { setDragging(undefined); setDropTarget(undefined); }}>⋮⋮</span>
            <button className="icon-button" title="上移" aria-label={`${field.label}上移`} disabled={index === 0} onClick={() => reorderParameter(name, -1)}>↑</button>
            <button className="icon-button" title="下移" aria-label={`${field.label}下移`} disabled={index === layout[group].length - 1} onClick={() => reorderParameter(name, 1)}>↓</button>
            <button className="icon-button" title={group === "basic" ? "移至高级参数" : "移至基础参数"} aria-label={group === "basic" ? `${field.label}移至高级参数` : `${field.label}移至基础参数`} onClick={() => moveParameter(name, group === "basic" ? "advanced" : "basic")}>⇄</button>
          </div>
          {field.options ? <SelectField label={`${field.label}${field.unit ? ` (${field.unit})` : ""}`} value={values[field.name]} onChange={(value) => setValues((old) => ({ ...old, [field.name]: value }))}>{field.options.length ? field.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>) : <option value="">未找到可选文件</option>}</SelectField> : <Field label={`${field.label}${field.unit ? ` (${field.unit})` : ""}`} value={Array.isArray(values[field.name]) ? values[field.name].join(", ") : values[field.name]} type={typeof field.default === "string" || Array.isArray(field.default) ? "text" : "number"} onChange={(value) => setValues((old) => ({ ...old, [field.name]: Array.isArray(field.default) ? String(value).split(",").map(Number) : value }))} />}
        </div>;
      })}
      {layout[group].length === 0 && <div className="parameter-empty">暂无参数</div>}
      <div className={`parameter-drop-end ${dropTarget === `${group}:end` ? "active" : ""}`} />
    </div>}
  </section>;
  };
  return <><PageHead eyebrow={`${definition?.category_label || "EXPERIMENT"} · ${definition?.variant || ""}`} title={definition?.title || "正在载入实验"} description={definition?.description || "读取实验定义与默认参数。"} /><section className="experiment-card">{definition && <div className="experiment-notes"><span>所需设备：{definition.required_devices.join(" · ")}</span>{definition.wiring_notes.map((note) => <p key={note}>{note}</p>)}</div>}<div className="experiment-strip"><div><Status>CONFIGURATION</Status><h2>实验参数</h2></div><button className="secondary" disabled={savingDefaults || !fields.length} onClick={saveDefaults}>{savingDefaults ? "保存中…" : "存为默认参数"}</button></div><div className={`parameter-layout ${advancedCollapsed ? "advanced-collapsed" : ""}`}>{renderGroup("basic", "基础参数")}{renderGroup("advanced", "高级参数")}</div><div className="experiment-actions"><button className="secondary" disabled={starting || jobActive || !fields.length} onClick={check}>仅预检</button><button disabled={starting || jobActive || !fields.length} onClick={run}>{jobActive ? "实验运行中" : starting ? "正在启动…" : "预检并运行实验"}</button></div>{defaultsStatus && <div className={`alert ${defaultsStatus.ok ? "success" : "error"}`}>{defaultsStatus.message}</div>}{preflight && <div className={`alert ${preflight.ok ? "success" : "error"}`}>{preflight.ok ? "参数预检通过，可以启动实验。" : preflight.errors.join("；")}</div>}</section><JobView job={job} onUpdate={setJob} /></>;
}

function Runs() {
  const [runs, setRuns] = useState<Array<{ id: string; experiment_id: string; experiment_title: string; path: string; artifacts: string[]; can_analyze: boolean }>>([]);
  const [job, setJob] = useState<Job>();
  useEffect(() => { api<typeof runs>("/api/runs").then(setRuns); }, []);
  const analyze = (run: typeof runs[number]) => api<Job>(`/api/runs/${encodeURIComponent(run.id)}/analyze`, { method: "POST", body: JSON.stringify({ experiment_id: run.experiment_id }) }).then(setJob);
  return <><PageHead eyebrow="RUN HISTORY" title="运行记录" description="浏览全部实验的数据目录，并按指定运行目录重新分析。" /><div className="runs">{runs.length === 0 ? <div className="empty"><h3>暂无运行记录</h3><p>完成一次实验后，结果会自动显示在这里。</p></div> : runs.map((run) => <article key={`${run.experiment_id}:${run.id}`}><div><small>{run.experiment_title}</small><h3>{run.id}</h3><p>{run.path}</p>{run.can_analyze && <button className="secondary" onClick={() => analyze(run)}>重新分析</button>}</div><div className="artifacts">{run.artifacts.map((name) => <a target="_blank" key={name} href={`/api/runs/${run.experiment_id}/${encodeURIComponent(run.id)}/artifacts/${encodeURIComponent(name)}`}>{name}</a>)}</div></article>)}</div><JobView job={job} onUpdate={setJob} /></>;
}

export default Layout;
