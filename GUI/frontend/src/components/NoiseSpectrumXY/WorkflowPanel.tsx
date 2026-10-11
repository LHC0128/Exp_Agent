import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { api } from "../../api";
import type { Job, ParameterValues } from "../../types/api";
import type { components } from "../../types/openapi";
import "./workflow.css";

export type XYSession = components["schemas"]["XYSession"];
type Flow = components["schemas"]["XYFlow"];
type Stage = components["schemas"]["XYStage"];
type StageState = components["schemas"]["XYStageState"];
type AnalysisState = components["schemas"]["XYAnalysisState"];
const BASE = "/api/experiments/noise-spectrum-xy";
const stateLabels: Record<string, string> = {
  pending: "未运行", stale: "需重跑", running: "执行中", completed: "已完成",
  failed: "失败", quality_failed: "质量未通过", cancelled: "已取消",
};

function Nodes({ stages, states, selected, onSelect, disabled, runId }: {
  stages: Stage[]; states?: Record<string, StageState>; selected: string[];
  onSelect: (value: string[]) => void; disabled: boolean; runId?: string | null;
}) {
  const groups = [...new Set(stages.map((stage) => stage.group))];
  return <div className="xy-flow-nodes">{groups.map((group) => <section className="xy-flow-group" key={group}>
    <h3>{group}</h3>
    <ol>{stages.filter((stage) => stage.group === group).map((stage) => {
      const state = states?.[stage.id];
      const artifacts = state?.artifacts?.length ? state.artifacts : stage.artifacts;
      const missing = stage.dependencies.filter((id) => states?.[id]?.status !== "completed" && !selected.includes(id));
      return <li className={`xy-flow-node ${state?.status ?? "pending"}`} key={stage.id}>
        <label><input type="checkbox" checked={selected.includes(stage.id)} disabled={disabled}
          onChange={(event) => onSelect(event.target.checked ? [...selected, stage.id] : selected.filter((id) => id !== stage.id))} />
          <strong>{stage.id} {stage.title}</strong></label>
        <span className="xy-step-status">{stateLabels[state?.status ?? "pending"] ?? state?.status}</span>
        <small>前置：{stage.dependencies.map((id) => `${id} ${stages.find((item) => item.id === id)?.title ?? ""}`).join("、") || "无"}</small>
        {missing.length > 0 && selected.includes(stage.id) && <p className="xy-missing">缺少前置：{missing.join("、")}</p>}
        {state?.error && <p className="xy-step-error">{state.error}</p>}
        {state?.status === "completed" && artifacts.length > 0 && <details><summary>产物</summary>
          {artifacts.map((name) => <div key={name}>{runId && !name.includes("*")
            ? <a href={stage.id.startsWith("A")
              ? `${BASE}/runs/${encodeURIComponent(runId)}/files/${name}`
              : `/api/runs/noise-spectrum-xy/${encodeURIComponent(runId)}/artifacts/${name}`} download>{name}</a>
            : <code>{name}</code>}</div>)}
        </details>}
      </li>;
    })}</ol>
  </section>)}</div>;
}

export function WorkflowPanel({ enabled, values, setValues, session, setSession, job, setJob,
  analysisJob, setAnalysisJob, hardwareBusy, runs, setError, onRefresh }: {
  enabled: boolean; values: ParameterValues; setValues: Dispatch<SetStateAction<ParameterValues>>;
  session?: XYSession; setSession: Dispatch<SetStateAction<XYSession | undefined>>;
  job?: Job; setJob: Dispatch<SetStateAction<Job | undefined>>;
  analysisJob?: Job; setAnalysisJob: Dispatch<SetStateAction<Job | undefined>>;
  hardwareBusy: boolean; runs: { run_id: string; completion_status: string }[];
  setError: Dispatch<SetStateAction<string>>; onRefresh: Dispatch<SetStateAction<number>>;
}) {
  const [flow, setFlow] = useState<Flow>();
  const [selectedA, setSelectedA] = useState<string[]>([]);
  const [selectedB, setSelectedB] = useState<string[]>([]);
  const [runId, setRunId] = useState("");
  const [analysis, setAnalysis] = useState<AnalysisState>();
  const [options, setOptions] = useState<Record<string, number>>({});
  const [sending, setSending] = useState(false);
  const live = Boolean(session && session.status !== "closed");
  const busy = sending || (live && (session?.status !== "waiting" || Boolean(session?.pending_request)));
  const analyzing = Boolean(analysisJob && ["queued", "running"].includes(analysisJob.status));
  const dirty = Boolean(session && Object.keys(values).some((key) => JSON.stringify(values[key]) !== JSON.stringify(session.parameters[key])));

  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    void Promise.all([api<Flow>(`${BASE}/workflow`), api<XYSession | null>(`${BASE}/sessions/active`)]).then(async ([definition, active]) => {
      if (disposed) return;
      setFlow(definition);
      if (active) {
        setSession(active);
        setValues(active.parameters as ParameterValues);
        const recovered = await api<Job>(`/api/jobs/${active.job_id}`);
        if (!disposed) setJob(recovered);
      }
    }).catch((reason) => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [enabled, setSession, setValues, setJob, setError]);

  useEffect(() => {
    if (!session || !job || session.job_id !== job.id) return;
    const event = [...job.events].reverse().find((item) => item.data?.workflow_session);
    if (event) {
      const snapshot = event.data.workflow_session as XYSession;
      setSession((current) => current && snapshot.revision >= current.revision
        ? { ...current, ...snapshot, pending_request: event.data.command_result ? null : current.pending_request,
          error: event.data.command_result ? (event.data.command_result as { error: string | null }).error : current.error }
        : current);
    }
    if (!["queued", "running"].includes(job.status)) {
      void api<XYSession>(`${BASE}/sessions/${session.session_id}`).then(setSession).catch((reason) => setError(String(reason)));
      onRefresh((current) => current + 1);
    }
  }, [job, session?.session_id, setSession, setError, onRefresh]);

  useEffect(() => {
    if (session?.run_id) setRunId(session.run_id);
  }, [session?.run_id]);

  useEffect(() => {
    if (!runId) { setAnalysis(undefined); return; }
    let disposed = false;
    setAnalysis(undefined);
    void api<AnalysisState>(`${BASE}/runs/${encodeURIComponent(runId)}/analysis`).then((state) => {
      if (!disposed) { setAnalysis(state); setOptions(state.options); }
    }).catch((reason) => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [runId, analysisJob?.status, setError]);

  useEffect(() => {
    const event = [...(analysisJob?.events ?? [])].reverse().find((item) => item.data?.analysis_workflow);
    const snapshot = event?.data.analysis_workflow as AnalysisState | undefined;
    if (snapshot?.run_id === runId) setAnalysis(snapshot);
  }, [analysisJob, runId]);

  const act = async (path: string, method: string, payload: object) => {
    setSending(true); setError("");
    try { setSession(await api<XYSession>(`${BASE}${path}`, { method, body: JSON.stringify(payload) })); }
    catch (reason) { setError(String(reason)); }
    finally { setSending(false); }
  };
  const create = async () => {
    setSending(true); setError("");
    try {
      const created = await api<XYSession>(`${BASE}/sessions`, { method: "POST", body: JSON.stringify({ parameters: values }) });
      setSession(created);
      setJob(await api<Job>(`/api/jobs/${created.job_id}`));
    } catch (reason) { setError(String(reason)); }
    finally { setSending(false); }
  };
  const analyze = async () => {
    if (!analysis) return;
    setSending(true); setError("");
    try {
      setAnalysisJob(await api<Job>(`${BASE}/runs/${encodeURIComponent(runId)}/analysis`, { method: "POST", body: JSON.stringify({
        revision: analysis.revision, step_ids: selectedB,
        analysis_parameters: Object.fromEntries(Object.entries(options).map(([name, value]) => [`ANALYSIS_${name.toUpperCase()}`, value])),
      }) }));
    } catch (reason) { setError(String(reason)); }
    finally { setSending(false); }
  };
  const chooseAllA = () => setSelectedA(flow?.acquisition.filter((stage) => stage.id !== "A14").map((stage) => stage.id) ?? []);
  const runChoices = [...new Set([session?.run_id, ...runs.filter((item) => item.completion_status !== "running").map((item) => item.run_id)].filter((id): id is string => Boolean(id)))];

  return <section className="xy-workflow" aria-label="交互实验流程图">
    <div className="xy-workflow-head"><h2>实验流程图</h2>{flow && <a href={flow.diagram_url} download>下载可编辑流程图</a>}</div>
    <p>显式勾选需要执行的模块及尚未完成的前置模块。重跑会使后续步骤失效，每轮扫描保存到独立目录。</p>
    {session && <div aria-live="polite">
      <p>会话 {session.session_id.slice(0, 8)} · 修订 {session.revision} · {live ? busy ? "执行或等待命令完成" : "等待下一步" : "已结束"} · 当前模块 {session.current_step ?? "—"}</p>
      {live && <p className="alert">分步等待期间保持输出状态并持续占用硬件锁；检查完成后请结束会话。</p>}
      {session.run_id && <p>本轮目录：{session.run_id}</p>}
      <p>输出：{Object.entries(session.outputs ?? {}).map(([name, state]) => `${name} ${state}`).join(" · ")}</p>
      <small>{session.output_source}</small>
      {session.error && <p role="alert">{session.error}</p>}
      {session.xy_phase_calibration && <details><summary>XY 校相诊断与测量历史</summary><pre>{JSON.stringify(session.xy_phase_calibration, null, 2)}</pre></details>}
      {Object.keys(session.clock_sources ?? {}).length > 0 && <details><summary>参考时钟回读与 DAQ 设置</summary>
        <pre>{JSON.stringify({ clock_sources: session.clock_sources, demod_phase_deg: session.demod_phase_deg, daq_settings: session.daq_settings }, null, 2)}</pre>
      </details>}
    </div>}
    <div className="xy-flow-columns">
      <section><h2>采集流程</h2>
        <div className="xy-flow-actions">
          <button disabled={!enabled || !flow || sending || live || hardwareBusy} onClick={() => void create()}>创建分步会话</button>
          <button className="secondary" disabled={!flow || busy} onClick={chooseAllA}>选择全部采集步骤</button>
          <button disabled={!live || busy || !selectedA.length || dirty} onClick={() => void act(`/sessions/${session!.session_id}/steps`, "POST", { revision: session!.revision, step_ids: selectedA })}>运行所选 / 重跑</button>
          <button disabled={!live || busy || !dirty} onClick={() => void act(`/sessions/${session!.session_id}/parameters`, "PATCH", { revision: session!.revision, parameters: values })}>应用参数到会话</button>
          <button className="danger" disabled={!live || busy} onClick={() => void act(`/sessions/${session!.session_id}/finish`, "POST", { revision: session!.revision })}>结束会话</button>
        </div>
        {live && dirty && <p role="status">参数草稿尚未应用，请先应用参数再执行模块。</p>}
        {flow && <Nodes stages={flow.acquisition} states={session?.steps} selected={selectedA} onSelect={setSelectedA} disabled={busy} runId={session?.run_id} />}
      </section>
      <section><h2>分析流程</h2>
        <label>分析运行目录<select value={runId} disabled={analyzing || sending} onChange={(event) => setRunId(event.target.value)}>
          <option value="">选择已结束扫描或历史运行</option>{runChoices.map((id) => <option key={id} value={id}>{id}</option>)}
        </select></label>
        <div className="xy-analysis-options">{Object.entries(options).map(([name, value]) => <label key={name}>
          {({ bin_width_hz: "频率格间距", frequency_min_hz: "分析频率下限", frequency_max_hz: "分析频率上限", fit_half_width_hz: "峰定位半窗口" } as Record<string, string>)[name] ?? name} (Hz)
          <input type="number" value={value} disabled={analyzing || sending} onChange={(event) => setOptions((current) => ({ ...current, [name]: Number(event.target.value) }))} />
        </label>)}</div>
        <div className="xy-flow-actions">
          <button className="secondary" disabled={!flow || analyzing || sending} onClick={() => setSelectedB(flow?.analysis.map((stage) => stage.id) ?? [])}>选择全部分析步骤</button>
          <button disabled={!analysis || !selectedB.length || analyzing || sending} onClick={() => void analyze()}>运行所选分析 / 重算</button>
        </div>
        <p>分析绑定所选目录，与硬件会话分开执行；旧数据缺少阶段记录时从 B01 开始。</p>
        {flow && <Nodes stages={flow.analysis} states={analysis?.steps} selected={selectedB} onSelect={setSelectedB} disabled={analyzing || sending} runId={runId} />}
      </section>
    </div>
  </section>;
}
