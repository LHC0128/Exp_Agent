import { useEffect, useState, type ComponentProps, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { api } from "../../api";
import { ParameterForm } from "../MxYOptimalControlFrequency/ParameterForm";
import type { Job, ParameterLayout, ParameterValues, SchemaField } from "../../types/api";
import type { components } from "../../types/openapi";
import "./workflow.css";

export type XYSession = components["schemas"]["XYSession"];
type Flow = components["schemas"]["XYFlow"];
type Stage = components["schemas"]["XYStage"];
type StageState = components["schemas"]["XYStageState"];
type AnalysisState = components["schemas"]["XYAnalysisState"];
const identityParameters = (values: ParameterValues) => values;
const stateLabels: Record<string, string> = {
  pending: "未运行", stale: "需重跑", running: "执行中", completed: "已完成",
  failed: "失败", quality_failed: "质量未通过", cancelled: "已取消",
};

const externalOptions = (options: Record<string, number>, prefix: string, keys?: Record<string, string>): ParameterValues => Object.fromEntries(
  Object.entries(options).map(([name, value]) => [keys?.[name] ?? `${prefix}${name.toUpperCase()}`, value]),
);
const changedNames = (values: ParameterValues, applied: ParameterValues) => Object.keys(values).filter(
  (name) => JSON.stringify(values[name]) !== JSON.stringify(applied[name]),
);
const affectedIds = (changed: string[], impacts: Record<string, string[]> = {}) =>
  [...new Set(changed.flatMap((name) => impacts[name] ?? []))];

function Nodes({ stages, states, selected, onSelect, disabled, runId, editor, sharingStages,
  changed, appliedValues, impacts, experimentId }: {
  experimentId: string; stages: Stage[]; states?: Record<string, StageState>; selected: string[];
  onSelect: (value: string[]) => void; disabled: boolean; runId?: string | null;
  editor: Omit<ComponentProps<typeof ParameterForm>, "presentation" | "renderParameterNote">;
  sharingStages: Stage[]; changed: string[]; appliedValues?: ParameterValues; impacts: string[];
}) {
  const BASE = `/api/experiments/${experimentId}`;
  const groups = [...new Set(stages.map((stage) => stage.group))];
  return <div className="xy-flow-nodes">{groups.map((group) => <section className="xy-flow-group" key={group}>
    <h3>{group}</h3>
    <ol>{stages.filter((stage) => stage.group === group).map((stage) => {
      const state = states?.[stage.id];
      const artifacts = state?.artifacts?.length ? state.artifacts : stage.artifacts;
      const missing = stage.dependencies.filter((id) => states?.[id]?.status !== "completed" && !selected.includes(id));
      const nodeFields = editor.fields.filter((field) => stage.display_parameters.includes(field.name));
      const basicFields = editor.layout.basic.flatMap((name) => nodeFields.filter((field) => field.name === name));
      const advancedCount = nodeFields.filter((field) => editor.layout.advanced.includes(field.name)).length;
      const otherUsers = (name: string) => sharingStages.filter((item) => item.id !== stage.id && item.display_parameters.includes(name));
      const preview = state?.status === "completed" && impacts.includes(stage.id);
      return <li className={`xy-flow-node ${state?.status ?? "pending"}${preview ? " draft-affected" : ""}`} key={stage.id}
        data-connect-next={stages[stages.indexOf(stage) + 1]?.dependencies.includes(stage.id) ?? false}>
        <label className="xy-step-select"><input type="checkbox" checked={selected.includes(stage.id)} disabled={disabled}
          onChange={(event) => onSelect(event.target.checked ? [...selected, stage.id] : selected.filter((id) => id !== stage.id))} />
          <strong>{stage.id} {stage.title}</strong></label>
        <span className="xy-step-status">{stateLabels[state?.status ?? "pending"] ?? state?.status}</span>
        <small>前置：{stage.dependencies.map((id) => `${id} ${stages.find((item) => item.id === id)?.title ?? ""}`).join("、") || "无"}</small>
        {missing.length > 0 && selected.includes(stage.id) && <p className="xy-missing">缺少前置：{missing.join("、")}</p>}
        {state?.error && <p className="xy-step-error">{state.error}</p>}
        {preview && <p className="xy-draft-note">应用草稿后预计需重跑；当前执行记录尚未改变。</p>}
        {nodeFields.length > 0 && <details className="xy-node-parameters">
          <summary>参数：{basicFields.length ? basicFields.map((field) =>
            `${field.label}${otherUsers(field.name).length ? "（共享）" : ""}`).join("、") : "无基础参数"} · 高级 {advancedCount} 项</summary>
          <div role="region" aria-label={`${stage.id} ${stage.title} 参数`}>
            <ParameterForm {...editor} fields={nodeFields} presentation="workflow"
              renderParameterNote={(field) => <>
                {otherUsers(field.name).length > 0 && <small className="xy-shared-note">共用步骤：{otherUsers(field.name).map((item) => `${item.id} ${item.title}`).join("、")}</small>}
                {changed.includes(field.name) && <small className="xy-draft-note">草稿未应用：{String(appliedValues?.[field.name])} → {String(editor.values[field.name])}</small>}
              </>} />
          </div>
        </details>}
        {state?.status === "completed" && artifacts.length > 0 && <details><summary>产物</summary>
          {artifacts.map((name) => <div key={name}>{runId && !name.includes("*")
            ? <a href={stage.id.startsWith("A")
              ? `${BASE}/runs/${encodeURIComponent(runId)}/files/${name}`
              : `/api/runs/${experimentId}/${encodeURIComponent(runId)}/artifacts/${name}`} download>{name}</a>
            : <code>{name}</code>}</div>)}
        </details>}
      </li>;
    })}</ol>
  </section>)}</div>;
}

export function WorkflowPanel({ enabled, fields, layout, setLayout, setNotice, parametersDisabled,
  values, setValues, session, setSession, job, setJob,
  analysisJob, setAnalysisJob, hardwareBusy, runs, setError, onRefresh, children,
  experimentId = "noise-spectrum-xy", finishStepId = "A14", runStepId = "A12",
  analysisParameterPrefix = "ANALYSIS_", analysisParameterKeys, normalizeParameters = identityParameters }: {
  experimentId?: string; finishStepId?: string; runStepId?: string; analysisParameterPrefix?: string;
  analysisParameterKeys?: Record<string, string>;
  normalizeParameters?: (values: ParameterValues) => ParameterValues;
  enabled: boolean; values: ParameterValues; setValues: Dispatch<SetStateAction<ParameterValues>>;
  fields: SchemaField[]; layout: ParameterLayout; setLayout: Dispatch<SetStateAction<ParameterLayout>>;
  setNotice: Dispatch<SetStateAction<string>>; parametersDisabled: boolean;
  session?: XYSession; setSession: Dispatch<SetStateAction<XYSession | undefined>>;
  job?: Job; setJob: Dispatch<SetStateAction<Job | undefined>>;
  analysisJob?: Job; setAnalysisJob: Dispatch<SetStateAction<Job | undefined>>;
  hardwareBusy: boolean; runs: { run_id: string; completion_status: string }[];
  setError: Dispatch<SetStateAction<string>>; onRefresh: Dispatch<SetStateAction<number>>;
  children: ReactNode;
}) {
  const BASE = `/api/experiments/${experimentId}`;
  const [flow, setFlow] = useState<Flow>();
  const [selectedA, setSelectedA] = useState<string[]>([]);
  const [selectedB, setSelectedB] = useState<string[]>([]);
  const [runId, setRunId] = useState("");
  const [analysis, setAnalysis] = useState<AnalysisState>();
  const [analysisDrafts, setAnalysisDrafts] = useState<Record<string, ParameterValues>>({});
  const [sending, setSending] = useState(false);
  const live = Boolean(session && session.status !== "closed");
  const busy = sending || (live && (session?.status !== "waiting" || Boolean(session?.pending_request)));
  const analyzing = Boolean(analysisJob && ["queued", "running"].includes(analysisJob.status));
  const acquisitionChanged = live && session ? changedNames(values, session.parameters as ParameterValues) : [];
  const dirty = acquisitionChanged.length > 0;
  const analysisValues = runId ? analysisDrafts[runId] ?? {} : values;
  const appliedAnalysis = analysis ? externalOptions(analysis.options, analysisParameterPrefix, analysisParameterKeys) : {};
  const analysisChanged = runId ? (analysis ? changedNames(analysisValues, appliedAnalysis) : []) : acquisitionChanged;
  const acquisitionImpacts = affectedIds(acquisitionChanged, flow?.parameter_impacts.acquisition);
  const analysisImpacts = runId ? affectedIds(analysisChanged, flow?.parameter_impacts.analysis) : [];
  const sharingStages = flow ? [...flow.acquisition, ...flow.analysis] : [];
  const acquisitionEditor = { fields, layout, values, onChange: (field: SchemaField, value: ParameterValues[string]) =>
    setValues((current) => normalizeParameters({ ...current, [field.name]: value })), onLayoutChange: setLayout,
    onMoveNotice: setNotice, disabled: parametersDisabled || busy };
  const analysisEditor = { ...acquisitionEditor, values: analysisValues,
    fields: runId ? fields.filter((field) => Object.prototype.hasOwnProperty.call(appliedAnalysis, field.name)) : fields,
    disabled: runId ? analyzing || sending || !analysis : acquisitionEditor.disabled,
    onChange: (field: SchemaField, value: ParameterValues[string]) => {
      if (!runId) acquisitionEditor.onChange(field, value);
      else setAnalysisDrafts((current) => ({ ...current, [runId]: { ...current[runId], [field.name]: value } }));
    } };

  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    void Promise.all([api<Flow>(`${BASE}/workflow`), api<XYSession | null>(`${BASE}/sessions/active`)]).then(async ([definition, active]) => {
      if (disposed) return;
      if (active) {
        setSession(active);
        setValues(normalizeParameters(active.parameters as ParameterValues));
        const recovered = await api<Job>(`/api/jobs/${active.job_id}`);
        if (!disposed) setJob(recovered);
      }
      if (disposed) return;
      if (!definition.parameter_impacts?.acquisition || !definition.parameter_impacts?.analysis
        || [...definition.acquisition, ...definition.analysis].some((stage) => !Array.isArray(stage.display_parameters))) {
        throw new Error("流程接口版本过旧，请在任务结束后重启 GUI 后端并刷新页面。");
      }
      setFlow(definition);
    }).catch((reason) => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [enabled, setSession, setValues, setJob, setError, BASE, normalizeParameters]);

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
    if (!runId) { setAnalysis(undefined); return; }
    let disposed = false;
    setAnalysis(undefined);
    void api<AnalysisState>(`${BASE}/runs/${encodeURIComponent(runId)}/analysis`).then((state) => {
      if (!disposed) {
        setAnalysis(state);
        setAnalysisDrafts((current) => current[runId] ? current : { ...current, [runId]: externalOptions(state.options, analysisParameterPrefix, analysisParameterKeys) });
      }
    }).catch((reason) => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [runId, analysisJob?.status, setError, BASE, analysisParameterPrefix, analysisParameterKeys]);

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
        analysis_parameters: analysisValues,
      }) }));
    } catch (reason) { setError(String(reason)); }
    finally { setSending(false); }
  };
  const chooseAllA = () => setSelectedA(flow?.acquisition.filter((stage) => stage.id !== finishStepId).map((stage) => stage.id) ?? []);
  const runChoices = [...new Set([session?.steps[runStepId]?.status === "completed" ? session?.run_id : undefined,
    ...Object.keys(analysisDrafts),
    ...runs.filter((item) => item.completion_status !== "running").map((item) => item.run_id)].filter((id): id is string => Boolean(id)))];

  return <section className="xy-workflow" aria-label="交互实验流程图">
    <div className="xy-workflow-head"><h2>实验流程图</h2>{flow && <a href={flow.diagram_url} download>下载可编辑流程图</a>}</div>
    {children}
    <p>显式勾选需要执行的模块及尚未完成的前置模块。重跑会使后续步骤失效，每轮扫描保存到独立目录。</p>
    <p>基础参数名称常显，展开节点编辑；高级参数单独折叠。共享参数的值和基础／高级分类同步，初始化条件只在配置节点展示。</p>
    {session && <div aria-live="polite">
      <p>会话 {session.session_id.slice(0, 8)} · 修订 {session.revision} · {live ? busy ? "执行或等待命令完成" : "等待下一步" : "已结束"} · 当前模块 {session.current_step ?? "—"}</p>
      {live && <p className="alert">分步等待期间保持输出状态并持续占用硬件锁；检查完成后请结束会话。</p>}
      {!live && <p>下方状态为已结束会话的执行记录，参数编辑用于下一次实验。</p>}
      {session.run_id && <p>本轮目录：{session.run_id}</p>}
      <p>输出：{Object.entries(session.outputs ?? {}).map(([name, state]) => `${name} ${state}`).join(" · ")}</p>
      <small>{session.output_source}</small>
      {session.error && <p role="alert">{session.error}</p>}
      {session.rf_phase_calibration && <details><summary>Z RF 相位扫描诊断</summary><pre>{JSON.stringify(session.rf_phase_calibration, null, 2)}</pre></details>}
      {session.safety_shutdown && <details><summary>安全收尾结果</summary><pre>{JSON.stringify(session.safety_shutdown, null, 2)}</pre></details>}
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
        {live && dirty && <p role="status">预计需重跑的已完成步骤：{flow?.acquisition.filter((stage) => acquisitionImpacts.includes(stage.id) && session?.steps[stage.id]?.status === "completed")
          .map((stage) => `${stage.id} ${stage.title}`).join("、") || "暂无"}。实际状态以应用后的后端结果为准。</p>}
        {flow && <Nodes experimentId={experimentId} stages={flow.acquisition} states={session?.steps} selected={selectedA} onSelect={setSelectedA} disabled={busy}
          runId={session?.run_id} editor={acquisitionEditor} sharingStages={sharingStages}
          changed={acquisitionChanged} appliedValues={session?.parameters as ParameterValues | undefined} impacts={acquisitionImpacts} />}
      </section>
      <section><h2>分析流程</h2>
        <label>分析操作对象<select value={runId} disabled={analyzing || sending} onChange={(event) => setRunId(event.target.value)}>
          <option value="">下一次实验（自动分析设置）</option>{runChoices.map((id) => <option key={id} value={id}>{id}</option>)}
        </select></label>
        <p>{runId ? `正在编辑 ${runId} 的分析覆盖值，不改变下一次实验设置。`
          : "正在编辑下一次实验的自动分析设置；保存默认值也保存这些参数。完整运行自动执行全部分析步骤。"}</p>
        {runId && analysisChanged.length > 0 && <p role="status">分析参数草稿尚未应用，执行分析时生效。预计需重算的已完成步骤：{flow?.analysis
          .filter((stage) => analysisImpacts.includes(stage.id) && analysis?.steps[stage.id]?.status === "completed")
          .map((stage) => `${stage.id} ${stage.title}`).join("、") || "暂无"}。</p>}
        <div className="xy-flow-actions">
          <button className="secondary" disabled={!flow || !analysis || analyzing || sending} onClick={() => setSelectedB(flow?.analysis.map((stage) => stage.id) ?? [])}>选择全部分析步骤</button>
          <button disabled={!analysis || !selectedB.length || analyzing || sending} onClick={() => void analyze()}>运行所选分析 / 重算</button>
        </div>
        <p>独立分析绑定所选目录，与硬件会话分开执行；旧数据缺少阶段记录时从 B01 开始。</p>
        {runId && !analysis && <p role="status">正在读取该目录的分析设置…</p>}
        {flow && (!runId || analysis) && <Nodes experimentId={experimentId} stages={flow.analysis} states={runId ? analysis?.steps : undefined}
          selected={runId ? selectedB : []} onSelect={setSelectedB} disabled={!runId || analyzing || sending}
          runId={runId} editor={analysisEditor} sharingStages={runId ? flow.analysis : sharingStages}
          changed={analysisChanged} appliedValues={runId ? appliedAnalysis : session?.parameters as ParameterValues | undefined} impacts={analysisImpacts} />}
      </section>
    </div>
  </section>;
}
