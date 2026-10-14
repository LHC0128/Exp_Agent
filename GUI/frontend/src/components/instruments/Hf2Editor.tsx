import { useEffect, useState } from "react";
import { api } from "../../api";
import type { ControlRoute, ControlTargetResponse, Hf2Snapshot, Job } from "../../types/api";
import { Field, SelectField } from "../FormFields";
import { useJobActivity } from "../JobActivity";
import { JobView } from "../JobView";
import { ScopePlot } from "../ScopePlot";
import { Status } from "../Status";

type Props = {
  snapshot: Hf2Snapshot; route: ControlRoute;
  onSaved: (value: ControlTargetResponse) => void; onError: (reason: unknown) => void;
};
const demodValues = (snapshot: Hf2Snapshot) => ({
  phase_deg: snapshot.phase_shift, frequency_hz: snapshot.frequency,
  rate: snapshot.sample_rate, time_constant: snapshot.time_constant,
  order: snapshot.order, harmonic: snapshot.harmonic, enabled: snapshot.enabled,
});

export function Hf2Editor({ snapshot, route, onSaved, onError }: Props) {
  const [values, setValues] = useState(demodValues(snapshot));
  const [outputs, setOutputs] = useState(snapshot.signal_outputs);
  const [auxiliary, setAuxiliary] = useState(snapshot.aux_outputs);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState("demod");
  const [duration, setDuration] = useState(1);
  const [points, setPoints] = useState(1000);
  const [source, setSource] = useState("voltage0");
  const [secondSource, setSecondSource] = useState("");
  const [divider, setDivider] = useState(4);
  const [length, setLength] = useState(2048);
  const [records, setRecords] = useState(1);
  const [job, setJob] = useState<Job>();
  const { hardwareBusy } = useJobActivity();
  const active = Boolean(job && ["queued", "running"].includes(job.status));
  const disabled = busy || hardwareBusy || active;
  useEffect(() => {
    setValues(demodValues(snapshot)); setOutputs(snapshot.signal_outputs); setAuxiliary(snapshot.aux_outputs);
  }, [snapshot]);
  const revision = { device_library_revision: route.deviceLibraryRevision, physical_mapping_revision: route.physicalMappingRevision };
  const apply = async (settings: object) => {
    setBusy(true);
    try {
      onSaved(await api<ControlTargetResponse>(`/api/control-targets/${encodeURIComponent(route.mappingKey)}/lockin`, {
        method: "PUT", body: JSON.stringify({ ...revision, settings }),
      }));
    } catch (reason) { onError(reason); } finally { setBusy(false); }
  };
  const acquire = async () => {
    setBusy(true);
    try {
      setJob(await api<Job>(`/api/control-targets/${encodeURIComponent(route.mappingKey)}/lockin-capture`, {
        method: "POST", body: JSON.stringify({ ...revision, settings: mode === "demod"
          ? { mode, duration, points, timeout: Math.max(30, duration + 10) }
          : { mode, sources: secondSource ? [source, secondSource] : [source], length, records,
            sample_rate_hz: snapshot.capabilities.scope_max_rate / 2 ** divider } }),
      }));
    } catch (reason) { onError(reason); } finally { setBusy(false); }
  };
  const initial = demodValues(snapshot);
  const changed = Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== initial[key as keyof typeof initial]));
  const result = job?.result as { run_dir?: string; traces?: Array<{ id: string; x: number[]; y: number[] }> } | undefined;
  const sources = snapshot.type === "HF2" ? ["voltage0", "voltage1"] : ["voltage0", "aux_input0", "aux_input1"];
  const sourceLabels: Record<string, string> = { voltage0: "主电压输入 1", voltage1: "主电压输入 2", aux_input0: "Aux Input 1", aux_input1: "Aux Input 2" };
  return <div className="lockin-editor">
    <div className="workspace-head"><div><Status>{snapshot.enabled ? "ENABLED" : "DISABLED"}</Status><h2>{snapshot.label}</h2><p>{snapshot.type} · Demod {snapshot.demod_idx} · {snapshot.capabilities.max_frequency_hz / 1e6} MHz</p></div></div>
    <section className="panel">
      <h3>解调设置</h3>
      <p>振荡器 {snapshot.oscillator} 由 Demod {snapshot.shared_demodulators.join("、")} 共用；改频也影响这些解调器和关联输出。</p>
      <div className="form-grid">
        <Field label="相移 (deg)" value={values.phase_deg} disabled={disabled} onChange={phase_deg => setValues({ ...values, phase_deg })} />
        <Field label="解调频率 (Hz)" value={values.frequency_hz} min={0} max={snapshot.capabilities.max_frequency_hz} disabled={disabled} onChange={frequency_hz => setValues({ ...values, frequency_hz })} />
        <Field label="请求采样率 (Sa/s)" value={values.rate} min={1} disabled={disabled} onChange={rate => setValues({ ...values, rate })} />
        <Field label="时间常数 (s)" value={values.time_constant} min={1e-9} disabled={disabled} onChange={time_constant => setValues({ ...values, time_constant })} />
        <Field label="滤波阶数" value={values.order} min={1} max={8} step={1} disabled={disabled} onChange={order => setValues({ ...values, order })} />
        <Field label="解调谐波" value={values.harmonic} min={1} step={1} disabled={disabled} onChange={harmonic => setValues({ ...values, harmonic })} />
        <label><span>向电脑传输解调数据</span><input type="checkbox" checked={values.enabled} disabled={disabled} onChange={event => setValues({ ...values, enabled: event.target.checked })} /></label>
      </div>
      <button disabled={disabled || !Object.keys(changed).length} onClick={() => void apply({ demod: changed })}>应用并回读</button>
      <p>X = {snapshot.x.toPrecision(5)} · Y = {snapshot.y.toPrecision(5)} · R = {snapshot.r.toPrecision(5)} · 相位 {snapshot.phase.toPrecision(5)}° · ADC 来源 {snapshot.input_select}</p>
    </section>
    <section className="panel"><h3>Signal Output</h3>
      {outputs.map((output, index) => {
        const mixer = snapshot.demod_idx;
        const update = (patch: Partial<typeof output>) => setOutputs(outputs.map((item, position) => position === index ? { ...item, ...patch } : item));
        return <div key={output.index} className="lockin-output">
          <h4>输出 {output.index + 1} · {output.load} · {output.differential ? "差分电压" : "单端电压"}</h4>
          <div className="form-grid">
            <Field label={`Demod ${mixer} 峰值幅度 (Vpeak)`} value={output.amplitudes_peak_v[mixer]} min={0} disabled={disabled || !output.writable} onChange={value => update({ amplitudes_peak_v: output.amplitudes_peak_v.map((amplitude, position) => position === mixer ? value : amplitude) })} />
            <Field label="直流偏置 (V)" value={output.offset_v} disabled={disabled || !output.writable} onChange={offset_v => update({ offset_v })} />
            <label><span>启用当前混频分量</span><input type="checkbox" disabled={disabled || !output.writable} checked={output.mixer_enabled[mixer]} onChange={event => update({ mixer_enabled: output.mixer_enabled.map((enabled, position) => position === mixer ? event.target.checked : enabled) })} /></label>
            <label><span>开启输出</span><input type="checkbox" disabled={disabled || !output.writable} checked={output.enabled} onChange={event => update({ enabled: event.target.checked })} /></label>
          </div>
          <button disabled={disabled || !output.writable} onClick={() => void apply({ signal_output: { index: output.index, amplitude_index: mixer, amplitude_peak_v: output.amplitudes_peak_v[mixer], offset_v: output.offset_v, mixer_enabled: output.mixer_enabled[mixer], enabled: output.enabled } })}>应用输出</button>
          <button className="secondary" disabled={disabled} onClick={() => void apply({ signal_output: { index: output.index, enabled: false } })}>关闭输出</button>
          {!output.writable && <p>安全范围未确认，设置已禁用：{output.safety_key}</p>}
        </div>;
      })}
    </section>
    <section className="panel"><h3>Aux Output</h3>
      {auxiliary.map((output, index) => {
        const update = (patch: Partial<typeof output>) => setAuxiliary(auxiliary.map((item, position) => position === index ? { ...item, ...patch } : item));
        return <div key={output.index} className="lockin-output"><h4>Aux {output.index + 1} · {output.value_v.toPrecision(5)} V</h4><div className="form-grid">
          <SelectField label="来源解调器" value={output.demod_index} disabled={disabled || !output.writable} onChange={value => update({ demod_index: Number(value) })}>{Array.from({ length: snapshot.capabilities.demodulators }, (_, idx) => <option key={idx} value={idx}>Demod {idx}</option>)}</SelectField>
          <SelectField label="来源信号" value={output.output_select} disabled={disabled || !output.writable} onChange={value => update({ output_select: Number(value) })}>{["X", "Y", "R", "相位"].map((label, idx) => <option key={idx} value={idx}>{label}</option>)}{output.output_select < 0 || output.output_select > 3 ? <option value={output.output_select}>其他模式 ({output.output_select})</option> : null}</SelectField>
          <Field label="缩放" value={output.scale} disabled={disabled || !output.writable} onChange={scale => update({ scale })} />
          <Field label="偏置 (V)" value={output.offset_v} disabled={disabled || !output.writable} onChange={offset_v => update({ offset_v })} />
        </div><button disabled={disabled || !output.writable || output.output_select < 0 || output.output_select > 3} onClick={() => void apply({ aux_output: { index: output.index, demod_index: output.demod_index, output_select: output.output_select, scale: output.scale, offset_v: output.offset_v } })}>应用 Aux 路由</button>
          <p>{!output.writable ? `安全范围未确认，设置已禁用：${output.safety_key}` : output.hardware_limits ? "应用时按安全范围设置硬件电压限幅。" : "HF2 无相同的硬件限幅节点，应用时按输入量程检查输出电压边界。"}</p>
        </div>;
      })}
    </section>
    <section className="panel"><h3>采集</h3><div className="form-grid">
      <SelectField label="数据类型" value={mode} disabled={disabled} onChange={setMode}><option value="demod">解调 X / Y / R 时序</option><option value="scope">内置 Scope 原始电压波形</option></SelectField>
      {mode === "demod" ? <>
        <Field label="采集时长 (s)" value={duration} min={0.001} max={60} disabled={disabled} onChange={setDuration} />
        <Field label="网格点数" value={points} min={2} max={1000000} disabled={disabled} onChange={setPoints} />
      </> : <>
        <SelectField label="Scope 电压来源" value={source} disabled={disabled} onChange={setSource}>{sources.map(item => <option key={item} value={item}>{sourceLabels[item]}</option>)}</SelectField>
        {snapshot.capabilities.scope_channels > 1 && <SelectField label="第二记录通道" value={secondSource} disabled={disabled} onChange={setSecondSource}><option value="">不启用</option>{sources.map(item => <option key={item} value={item}>{sourceLabels[item]}</option>)}</SelectField>}
        <SelectField label="Scope 采样率 (Sa/s)" value={divider} disabled={disabled} onChange={value => setDivider(Number(value))}>{Array.from({ length: 16 }, (_, idx) => <option key={idx} value={idx}>{snapshot.capabilities.scope_max_rate / 2 ** idx}</option>)}</SelectField>
        <Field label="每条记录点数" value={length} min={16} max={snapshot.capabilities.scope_max_samples} disabled={disabled || snapshot.type === "HF2"} onChange={setLength} />
        <Field label="独立记录数" value={records} min={1} max={10} disabled={disabled} onChange={setRecords} />
      </>}
    </div>
      <p>{mode === "demod" ? "本次请求速率为网格点数 ÷ 采集时长；按设备库模式选择 DAQ / Scope。Scope 按最近档位调整实际点数，保存真实时间轴及最终采集速率。" : "Scope 记录通道数与独立物理输入数不同；MFLI 只有一路主电压输入。"}</p>
      <button disabled={disabled} onClick={() => void acquire()}>开始采集</button>
      <JobView job={job} onUpdate={setJob} />
      {result?.run_dir && <p>完整数据已保存：{result.run_dir}</p>}
      {result?.traces && <ScopePlot mode="time" reset={0} traces={result.traces.map((trace, idx) => ({ ...trace, color: ["#147d74", "#cd6c2d", "#6261b5"][idx % 3] }))} />}
    </section>
  </div>;
}
