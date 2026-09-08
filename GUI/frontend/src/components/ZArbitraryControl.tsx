import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { api } from "../api";
import type { Job, ZArbitraryPreview, ZArbitrarySettings, ZArbitrarySourceItem, ZArbitraryStatus } from "../types/api";
import { Field, SelectField } from "./FormFields";
import { useJobActivity } from "./JobActivity";
import { JobView } from "./JobView";
import { ScopePlot } from "./ScopePlot";
import "./zArbitraryControl.css";

const base = "/api/tools/z-arbitrary-control";
const defaults: ZArbitrarySettings = {
  control_burst_phase_deg: 0, output_amplitude_vpp: null, link_trigger: false, trigger_frequency_hz: 100,
  trigger_amplitude_vpp: 5, trigger_offset_v: 2.5, trigger_duty_percent: 50,
};
const number = (value: number) => Number(value.toPrecision(7)).toString();

export function ZArbitraryControl() {
  const [sources, setSources] = useState<ZArbitrarySourceItem[]>([]);
  const [state, setState] = useState<ZArbitraryStatus>();
  const [selected, setSelected] = useState("");
  const [preview, setPreview] = useState<ZArbitraryPreview>();
  const [settings, setSettings] = useState<ZArbitrarySettings>(defaults);
  const [job, setJob] = useState<Job>();
  const [error, setError] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [reset, setReset] = useState(0);
  const previewRequest = useRef(0);
  const host = useRef<HTMLElement>(null);
  const location = useLocation();
  const { hardwareBusy, activeJobs } = useJobActivity();
  const localActive = !!job && ["queued", "running"].includes(job.status);
  const blocked = hardwareBusy || localActive || submitting;

  const refreshState = useCallback(async () => {
    setState(await api<ZArbitraryStatus>(`${base}/state`));
  }, []);
  const refreshSources = async () => {
    try { setSources(await api<ZArbitrarySourceItem[]>(`${base}/sources`)); }
    catch (reason) { setError(String(reason)); }
  };
  useEffect(() => {
    void refreshSources();
    void refreshState().catch(reason => setError(String(reason)));
  }, [refreshState]);
  useEffect(() => {
    if (location.hash === "#z-arbitrary-control") host.current?.scrollIntoView?.({ block: "start" });
  }, [location.hash]);
  useEffect(() => {
    if (job && !localActive) void refreshState().catch(reason => setError(String(reason)));
  }, [job?.id, job?.status, localActive, refreshState]);
  useEffect(() => {
    const active = activeJobs.find(item => item.kind === "z-arbitrary-control");
    if (active && active.id !== job?.id) {
      void api<Job>(`/api/jobs/${active.id}`).then(setJob).catch(reason => setError(String(reason)));
    }
  }, [activeJobs, job?.id]);

  const selectSource = async (runName: string) => {
    const request = ++previewRequest.current;
    setSelected(runName); setPreview(undefined); setError("");
    setPreviewLoading(!!runName);
    if (!runName) return;
    try {
      const value = await api<ZArbitraryPreview>(`${base}/preview/${encodeURIComponent(runName)}`);
      if (request === previewRequest.current) {
        setPreview(value);
        setSettings(current => ({ ...current, output_amplitude_vpp: value.amplitude_vpp }));
        setReset(old => old + 1);
      }
    } catch (reason) {
      if (request === previewRequest.current) setError(String(reason));
    } finally {
      if (request === previewRequest.current) setPreviewLoading(false);
    }
  };
  const execute = async (action: "configure" | "configure_and_start" | "stop") => {
    if (!state) return;
    setSubmitting(true); setError("");
    try {
      const body = {
        action,
        device_library_revision: state.device_library_revision,
        physical_mapping_revision: state.physical_mapping_revision,
        ...(action === "stop" ? {} : {
          run_name: preview?.run_name, waveform_sha256: preview?.waveform_sha256, settings,
        }),
      };
      setJob(await api<Job>(`${base}/actions`, { method: "POST", body: JSON.stringify(body) }));
    } catch (reason) {
      setError(String(reason));
      await refreshState().catch(() => undefined);
    } finally { setSubmitting(false); }
  };
  const change = (key: keyof ZArbitrarySettings, value: number | boolean) =>
    setSettings(current => ({ ...current, [key]: value }));
  const last = state?.last_applied;
  const target = state?.targets.find(item => item.mapping_key === "Z_magnetic_field");
  const trigger = state?.targets.find(item => item.mapping_key === "Time_sequence_2");
  const valid = Number.isFinite(settings.control_burst_phase_deg) && (!settings.link_trigger || (
    [settings.trigger_frequency_hz, settings.trigger_amplitude_vpp, settings.trigger_offset_v,
      settings.trigger_duty_percent].every(value => Number.isFinite(value)) &&
    (settings.trigger_frequency_hz ?? 0) > 0 && (settings.trigger_amplitude_vpp ?? 0) > 0 &&
    (settings.trigger_duty_percent ?? 0) >= 20 && (settings.trigger_duty_percent ?? 0) <= 80
  ));

  return <section className="z-arbitrary-control panel" id="z-arbitrary-control" ref={host}>
    <div className="panel-head"><div><small>ARBITRARY WAVEFORM</small><h2>Z 任意波控制</h2>
      <p>选择闭环冻结波形，预览电压，再应用到 Z 通道。</p></div></div>
    {error && <div className="alert error" role="alert">{error}</div>}
    <div className="z-arbitrary-layout">
      <div>
        <SelectField label="闭环冻结结果" value={selected} disabled={blocked} onChange={value => void selectSource(value)}>
          <option value="">请选择波形</option>
          {sources.map(source => <option key={source.run_name} value={source.run_name} disabled={!!source.error}>
            {source.run_name}{source.error ? "（文件无效）" : ""}
          </option>)}
        </SelectField>
        <div className="z-arbitrary-buttons">
          <button disabled={blocked} onClick={() => void refreshSources()}>刷新来源列表</button>
          <button disabled={blocked || !selected} onClick={() => void selectSource(selected)}>重新预览</button>
        </div>
        {sources.filter(item => item.error).map(item => <div className="alert error" key={item.run_name}>{item.run_name}：{item.error}</div>)}
        {!sources.length && <p>未找到可用冻结文件，请先生成闭环校正结果。</p>}
        <p>Z 输出：{target ? `${target.device_label} · CH${target.endpoint?.index}` : "未映射"}</p>
        <p>外部下降沿触发 · 无限 Burst</p>
        <Field label="Burst 起始相位 (deg)" value={settings.control_burst_phase_deg} disabled={blocked}
          onChange={value => change("control_burst_phase_deg", value)} />
        <Field label="输出幅度 (Vpp)" value={settings.output_amplitude_vpp ?? preview?.amplitude_vpp}
          min={0.001} disabled={blocked || !preview} onChange={value => change("output_amplitude_vpp", value)} />
        <label className="z-arbitrary-link"><input type="checkbox" checked={settings.link_trigger} disabled={blocked}
          onChange={event => change("link_trigger", event.target.checked)} />联动时序信号2</label>
        {settings.link_trigger && <>
          <p>触发输出：{trigger ? `${trigger.device_label} · CH${trigger.endpoint?.index}` : "未映射"} · High-Z</p>
          <div className="form-grid">
            <Field label="触发频率 (Hz)" value={settings.trigger_frequency_hz} min={0.001} disabled={blocked} onChange={value => change("trigger_frequency_hz", value)} />
            <Field label="触发幅度 (Vpp)" value={settings.trigger_amplitude_vpp} min={0.001} disabled={blocked} onChange={value => change("trigger_amplitude_vpp", value)} />
            <Field label="触发偏置 (V)" value={settings.trigger_offset_v} disabled={blocked} onChange={value => change("trigger_offset_v", value)} />
            <Field label="触发占空比 (%)" value={settings.trigger_duty_percent} min={20} max={80} disabled={blocked} onChange={value => change("trigger_duty_percent", value)} />
          </div>
        </>}
        <p>{settings.link_trigger ? "将时序信号2接入 Z 信号发生器 Ext Trig；若分配给其他设备，同样使用下降沿。" : "使用已配置的外部触发；本次不设置时序信号2。"}</p>
        <p>软件回读只能确认配置，不能检测外部触发线是否连接。</p>
      </div>
      <div className="z-arbitrary-preview">
        <h3>文件电压预览 · V–t</h3>
        {previewLoading && <p>正在读取波形…</p>}
        {!preview && !previewLoading && <p className="z-arbitrary-empty">选择一个结果即可离线预览波形。</p>}
        {preview && <>
          <dl className="z-arbitrary-metadata">
            <div><dt>点数</dt><dd>{preview.points}</dd></div>
            <div><dt>频率</dt><dd>{number(preview.repeat_frequency_hz)} Hz</dd></div>
            <div><dt>周期</dt><dd>{number(preview.period_s * 1000)} ms</dd></div>
            <div><dt>文件幅度</dt><dd>{number(preview.amplitude_vpp)} Vpp</dd></div>
            <div><dt>冻结偏置</dt><dd>{number(preview.offset_v)} V</dd></div>
            <div><dt>电压范围</dt><dd>{number(preview.minimum_v)} ～ {number(preview.maximum_v)} V</dd></div>
          </dl>
          <ScopePlot mode="time" reset={reset} traces={[{ id: preview.run_name, color: "#147d74",
            x: preview.time_s, y: preview.voltage_v }]} />
          <button onClick={() => setReset(old => old + 1)}>恢复全图</button>
          <p>一个周期的文件电压波形；频率、建议电压和偏置来自闭环文件。应用时可调整输出 Vpp，后端会重新换算归一化数组。图中使用原始时间轴，Burst 起始相位单独设置。</p>
        </>}
      </div>
    </div>
    <div className="z-arbitrary-buttons">
      <button disabled={blocked || !preview || !state || !target || !valid} onClick={() => void execute("configure")}>仅加载，保持关闭</button>
      <button className="primary" disabled={blocked || !preview || !state || !target || !valid} onClick={() => void execute("configure_and_start")}>加载并启动</button>
      <button disabled={blocked || !state || !target} onClick={() => void execute("stop")}>停止{last?.link_trigger ? " Z 和时序信号2" : " Z"}</button>
      <button disabled={blocked} onClick={() => void refreshState().catch(reason => setError(String(reason)))}>刷新应用记录</button>
    </div>
    {blocked && <p>硬件任务执行中，写入暂不可用；本任务可在下方取消。</p>}
    <div className="z-arbitrary-applied">
      <h3>最近应用记录</h3>
      <p>{state?.source_known && last?.source ? `来源：${last.source.run_name}` : "波形来源未知，重新加载后建立应用记录。"}</p>
      {last && <>
        <p>{new Date(last.updated_at).toLocaleString()} · {last.state === "enabled" ? "输出已开启，等待外部触发（触发发生未检测）" : last.state === "off" ? "输出已关闭" : "设备状态未知，请检查仪器"}
          {last.link_trigger ? " · 已联动时序信号2" : " · 未联动触发"}</p>
        <p>这是最近一次模块操作的记录；后续仪器操作可能改变输出。选择其他预览不会更改此记录。</p>
        {last.settings && <p>已应用 Burst 相位：{last.settings.control_burst_phase_deg}°
          {last.link_trigger && `；触发：${last.settings.trigger_frequency_hz} Hz / ${last.settings.trigger_amplitude_vpp} Vpp / ${last.settings.trigger_offset_v} V / ${last.settings.trigger_duty_percent}%`}</p>}
        {last.snapshots.length > 0 && <div className="z-arbitrary-readback"><table>
          <thead><tr><th>通道</th><th>输出</th><th>波形</th><th>频率</th><th>Vpp / 偏置</th><th>Burst / 触发</th></tr></thead>
          <tbody>{last.snapshots.flatMap(item => item.snapshot.type === "DG4000" || item.snapshot.type === "DG900"
            ? item.snapshot.channels.map(channel => <tr key={item.target.mapping_key}>
              <td>{item.target.label} · CH{channel.number}</td><td>{channel.output ? "开启" : "关闭"}</td>
              <td>{channel.shape}</td><td>{channel.frequency} Hz</td><td>{channel.amplitude} / {channel.offset} V</td>
              <td>{channel.burst.enabled ? `${channel.burst.mode} / ${channel.burst.trigger_source} / ${channel.burst.trigger_slope}` : "关闭"}
                {channel.readback_errors.length > 0 && <span>（部分回读失败）</span>}</td>
            </tr>) : [])}</tbody>
        </table></div>}
      </>}
    </div>
    <JobView job={job} onUpdate={setJob} />
  </section>;
}
