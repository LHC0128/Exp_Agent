import { useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { api } from "../../api";
import { JobView } from "../JobView";
import type { ExperimentSchema, Job, ParameterLayout, ParameterValues } from "../../types/api";
import {
  preflightMessage, sameParameterLayout, validatedParameterLayout, type PreflightResult,
} from "../../pages/experimentHelpers";

const identity = (values: ParameterValues) => values;

/** 运行区共用的保存默认值、预检与启动逻辑；请求体与各实验原有接口一致。 */
export function useRunControls({ experimentId, values, layout, normalize = identity, onSaved, onStarted, setError, setJob }: {
  experimentId: string;
  values: ParameterValues;
  layout: ParameterLayout;
  /** 发送前的参数联动；默认原样发送。 */
  normalize?: (values: ParameterValues) => ParameterValues;
  /** 后端确认保存后回传最新 schema 与已确认布局，页面据此更新字段和基线。 */
  onSaved: (schema: ExperimentSchema, layout: ParameterLayout) => void;
  /** 运行任务创建后回调，例如刷新运行列表。 */
  onStarted?: (job: Job) => void;
  setError: Dispatch<SetStateAction<string>>;
  setJob: Dispatch<SetStateAction<Job | undefined>>;
}) {
  const BASE = `/api/experiments/${experimentId}`;
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);
  const [notice, setNotice] = useState("");
  const [preflight, setPreflight] = useState<PreflightResult>();

  const check = async (start: boolean) => {
    setStarting(true); setError("");
    try {
      const parameters = normalize(values);
      const checked = await api<PreflightResult>(`${BASE}/preflight`, {
        method: "POST", body: JSON.stringify({ parameters }),
      });
      setPreflight(checked);
      if (start && checked.ok) {
        const created = await api<Job>(`${BASE}/runs`, { method: "POST", body: JSON.stringify({ parameters }) });
        setJob(created); onStarted?.(created);
      }
    } catch (reason) { setError(String(reason)); }
    finally { setStarting(false); }
  };

  const save = async () => {
    setSaving(true); setError("");
    try {
      const saved = await api<{ message?: string; schema: ExperimentSchema }>(`${BASE}/defaults`, {
        method: "PUT", body: JSON.stringify({ parameters: normalize(values), parameter_layout: layout }),
      });
      // 只有后端明确回读到与当前一致的布局才算保存成功，避免静默丢失分类。
      const confirmed = saved.schema.parameter_layout_saved
        ? validatedParameterLayout(saved.schema.fields, saved.schema.parameter_layout) : undefined;
      if (!confirmed || !sameParameterLayout(confirmed, layout)) throw new Error("后端未确认参数布局已保存");
      onSaved(saved.schema, confirmed);
      setNotice(saved.message || "参数与布局已保存");
    } catch (reason) { setError(String(reason)); }
    finally { setSaving(false); }
  };

  return { saving, starting, setStarting, notice, setNotice, preflight, check, save };
}

export type RunControlsState = ReturnType<typeof useRunControls>;

/** 一键运行区：保存 / 仅预检 / 预检并运行，及其状态提示、预览插槽和任务进度。 */
export function RunControls({ controls, canEdit, canStart, layoutDirty, hardwareBusyNote, job, onJobUpdate,
  preview, startLabel = "预检并运行实验" }: {
  controls: RunControlsState;
  /** 参数可编辑（无占用硬件的运行、字段已载入）；保存与仅预检共用。 */
  canEdit: boolean;
  /** 允许启动新运行；页面按自身占用条件计算。 */
  canStart: boolean;
  layoutDirty: boolean;
  /** 其他硬件任务占用时显示提示。 */
  hardwareBusyNote: boolean;
  job?: Job;
  onJobUpdate: Dispatch<SetStateAction<Job | undefined>>;
  /** 实验专属的只读预览。 */
  preview?: ReactNode;
  startLabel?: string;
}) {
  const { saving, starting, notice, preflight, check, save } = controls;
  return <div className="xy-workflow-controls">
    <div className="xy-flow-actions">
      <button disabled={!canEdit || saving || starting} onClick={() => void save()}>{saving ? "保存中…" : "保存参数与布局"}</button>
      <button className="secondary" disabled={!canEdit || starting} onClick={() => void check(false)}>仅预检</button>
      <button disabled={!canStart || starting} onClick={() => void check(true)}>{starting ? "处理中…" : startLabel}</button>
    </div>
    {layoutDirty && <p role="status">布局有未保存修改</p>}
    {notice && <p role="status">{notice}</p>}
    {preflight && <div className={`alert ${preflight.ok ? "success" : "error"}`}>{preflightMessage(preflight, "参数预检通过")}</div>}
    {hardwareBusyNote && <p>其他硬件任务正在运行。</p>}
    {preview}
    <JobView job={job} onUpdate={onJobUpdate} />
  </div>;
}

/** 运行页底部的最近一次结果；结果内容由实验提供。 */
export function LatestResultPanel<T extends { run_id: string }>({ latest, render, onOpen, note }: {
  latest?: T;
  render: (summary: T) => ReactNode;
  /** 提供时显示“查看本次详情”，通常切到历史页并选中该运行。 */
  onOpen?: (runId: string) => void;
  note?: ReactNode;
}) {
  return <section className="zaw-panel zaw-panel-result">
    <div className="zaw-panel-head"><small>LATEST</small><h2>最近一次结果</h2></div>
    {note}
    {latest ? <>{render(latest)}{onOpen && <button className="zaw-secondary" onClick={() => onOpen(latest.run_id)}>查看本次详情</button>}</>
      : <p className="zaw-empty">暂无运行记录。</p>}
  </section>;
}
