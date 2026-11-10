import { useCallback, useEffect, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { api } from "../../api";
import type { SchemaField } from "../../types/api";

const runStatusLabels: Record<string, string> = {
  completed: "完成",
  failed: "失败",
  cancelled: "已取消",
  running: "运行中",
  not_started: "未分析",
  quality_failed: "质量未通过",
  unknown: "未记录",
  acquired: "已采集，待安全收尾",
  partial: "部分分析完成",
  waiting_calibration: "待选择标定曲线",
};

/** 共享运行状态中文标签；未知值原样返回。合并各实验页原有 statusLabels，不丢已有键。 */
export function runStatusLabel(value: string): string {
  return runStatusLabels[value] ?? value;
}

/** 历史卡片所需的运行字段；卡片显示 run_id、时间与状态行。 */
export type RunCardFields = { run_id: string; timestamp?: string; completion_status: string };
type RunListShape<TRun> = { runs: TRun[]; total: number };

/** 历史数据读取：列表分页、选中摘要、最近一次；任一依赖变化时重新读取。 */
export function useRunHistory<TRun extends { run_id: string }, TSummary extends { run_id: string }>({
  experimentId, reloadKeys, setError, pageSize = 50, listQuery = "", autoSelectLatest = true,
}: {
  experimentId: string;
  /** 触发重新读取的外部状态，如 job?.status、analysisJob?.status；同一调用点长度固定。 */
  reloadKeys: unknown[];
  setError: Dispatch<SetStateAction<string>>;
  pageSize?: number;
  /** 追加到列表查询串，如 "&starred_only=true"；变化时重新读取列表。 */
  listQuery?: string;
  /** 没有选中时自动选中最近一次；默认 true。 */
  autoSelectLatest?: boolean;
}) {
  const BASE = `/api/experiments/${experimentId}`;
  const [list, setList] = useState<RunListShape<TRun>>({ runs: [], total: 0 });
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const [selected, setSelected] = useState<TSummary>();
  const [latest, setLatest] = useState<TSummary>();
  const summaryUrl = (id: string) => `${BASE}/runs/${encodeURIComponent(id)}/summary`;

  useEffect(() => {
    let disposed = false;
    void api<RunListShape<TRun>>(`${BASE}/runs?limit=${pageSize}&offset=${offset}${listQuery}`).then(data => {
      if (disposed) return;
      // 删除或切换筛选后当前页越界时回退到最后一页，本次不渲染列表。
      if (offset && offset >= data.total) {
        setOffset(Math.max(0, Math.ceil(data.total / pageSize) - 1) * pageSize);
        return;
      }
      setList(data);
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [BASE, pageSize, offset, listQuery, refresh, ...reloadKeys]);

  useEffect(() => {
    let disposed = false;
    void api<RunListShape<TRun>>(`${BASE}/runs?limit=1&offset=0`).then(async data => {
      const value = data.runs[0] ? await api<TSummary>(summaryUrl(data.runs[0].run_id)) : undefined;
      if (!disposed) {
        setLatest(value);
        if (autoSelectLatest) setSelectedId(current => current || value?.run_id || "");
      }
    }).catch(reason => {
      if (!disposed) { setLatest(undefined); setError(String(reason)); }
    });
    return () => { disposed = true; };
  }, [BASE, autoSelectLatest, refresh, ...reloadKeys]);

  useEffect(() => {
    if (!selectedId) { setSelected(undefined); return; }
    let disposed = false;
    setSelected(undefined);
    void api<TSummary>(summaryUrl(selectedId)).then(value => {
      if (!disposed) setSelected(value);
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [BASE, selectedId, refresh, ...reloadKeys]);

  // 引用保持稳定：WorkflowPanel 等消费方把 onRefresh 列入 effect 依赖，不稳定会引发刷新循环。
  const reload = useCallback(() => setRefresh(current => current + 1), []);

  return {
    list, setList,
    offset, setOffset, pageSize,
    /** 等价于各页原有 setRefresh(v => v + 1)。 */
    reload,
    /** 每次 reload 递增；页面自有的读取可把它列入依赖，与历史刷新保持同步。 */
    refreshKey: refresh,
    selectedId, setSelectedId, selected, latest,
  };
}

export type RunHistoryState<TRun extends { run_id: string }, TSummary extends { run_id: string }> =
  ReturnType<typeof useRunHistory<TRun, TSummary>>;

/** 历史 Tab 主体：左侧列表 + 右侧详情；填回、重新分析与实验专属功能经回调/插槽传入。 */
export function RunHistoryView<TRun extends RunCardFields, TSummary extends { run_id: string; parameters: Record<string, unknown> }>({
  history, fields, renderResult, fillBack, reanalyze, listTools, refreshDisabled = false,
  renderRunControls, renderRunStatus, renderRunNote, runRowClassName = "", onSelectRun, emptyListText = "暂无运行记录。",
  detailHeader, detailTools, detailHidden, detailExtra,
}: {
  history: RunHistoryState<TRun, TSummary>;
  /** 本次参数表用字段 label。 */
  fields: SchemaField[];
  renderResult: (summary: TSummary) => ReactNode;
  fillBack: { disabled: boolean; onClick: (summary: TSummary) => void };
  reanalyze?: { disabled: (summary: TSummary) => boolean; onClick: (summary: TSummary) => void };
  /** 刷新按钮旁的列表工具，如“仅显示星标”。 */
  listTools?: ReactNode;
  /** 刷新按钮禁用条件，如 DirectAW 的目录操作进行中。 */
  refreshDisabled?: boolean;
  /** 卡片左侧控件，如对比勾选框、星标按钮。 */
  renderRunControls?: (run: TRun) => ReactNode;
  /** 卡片状态行；默认只显示采集状态的中文标签。 */
  renderRunStatus?: (run: TRun) => ReactNode;
  /** 卡片下方说明，如“不可对比：…”。 */
  renderRunNote?: (run: TRun) => ReactNode;
  /** 提供 renderRunControls 时行容器（控件 + 卡片）的类名，布局样式由实验自带。 */
  runRowClassName?: string;
  /** 点击卡片选中运行后的附加动作，如切回单次详情。 */
  onSelectRun?: (run: TRun) => void;
  emptyListText?: string;
  /** 详情区顶部，如单次/对比视图切换。 */
  detailHeader?: ReactNode;
  /** 填回/重新分析之后的详情工具，如重命名、删除。 */
  detailTools?: (summary: TSummary) => ReactNode;
  /** 提供时详情包在可隐藏容器内并保持挂载；用于与 detailExtra 做不卸载的视图切换。 */
  detailHidden?: boolean;
  /** 详情之后始终挂载的附加面板（自行控制 hidden），如含任务进度订阅的对比面板。 */
  detailExtra?: ReactNode;
}) {
  const { list, offset, setOffset, pageSize, reload, selectedId, setSelectedId, selected } = history;
  const detail = selected ? <>
    <div className="zaw-run-details-actions">
      <button className="zaw-secondary" disabled={fillBack.disabled} onClick={() => fillBack.onClick(selected)}>填回参数</button>
      {reanalyze && <button className="zaw-secondary" disabled={reanalyze.disabled(selected)} onClick={() => reanalyze.onClick(selected)}>重新分析</button>}
    </div>
    {detailTools?.(selected)}
    {renderResult(selected)}
    <details className="zaw-parameter-table"><summary>本次参数</summary><table><tbody>
      {Object.entries(selected.parameters).map(([name, value]) =>
        <tr key={name}><th>{fields.find(field => field.name === name)?.label ?? name}</th><td>{value !== null && typeof value === "object" ? JSON.stringify(value) : String(value)}</td></tr>)}
    </tbody></table></details>
  </> : <p className="zaw-empty">{selectedId ? "正在读取运行详情…" : "请选择一条运行记录。"}</p>;
  return <div className="zaw-history">
    <section className="zaw-run-list">
      <div className="zaw-run-list-head"><small>RUNS</small><h3>历史运行（{list.total}）</h3></div>
      <div className="zaw-control-buttons">
        <button className="zaw-secondary" disabled={refreshDisabled} onClick={reload}>刷新记录</button>
        {listTools}
      </div>
      <ul>{list.runs.map(run => {
        const card = <button aria-label={run.run_id} className={`zaw-run-card ${selectedId === run.run_id ? "selected" : ""}`}
          onClick={() => { setSelectedId(run.run_id); onSelectRun?.(run); }}>
          <strong>{run.run_id}</strong>
          {run.timestamp && <span>{run.timestamp}</span>}
          <span>{renderRunStatus ? renderRunStatus(run) : runStatusLabel(run.completion_status)}</span>
        </button>;
        return <li key={run.run_id}>
          {renderRunControls ? <div className={runRowClassName || undefined}>{renderRunControls(run)}{card}</div> : card}
          {renderRunNote?.(run)}
        </li>;
      })}</ul>
      {!list.runs.length && <p className="zaw-empty">{emptyListText}</p>}
      <div className="zaw-control-buttons">
        <button className="zaw-secondary" disabled={!offset} onClick={() => setOffset(current => Math.max(0, current - pageSize))}>上一页</button>
        <button className="zaw-secondary" disabled={offset + pageSize >= list.total} onClick={() => setOffset(current => current + pageSize)}>下一页</button>
      </div>
    </section>
    <section className="zaw-run-details">
      {detailHeader}
      {detailHidden === undefined ? detail : <div hidden={detailHidden}>{detail}</div>}
      {detailExtra}
    </section>
  </div>;
}
