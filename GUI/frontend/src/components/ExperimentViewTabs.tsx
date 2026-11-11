import { lazy, Suspense, useState, type KeyboardEvent, type ReactNode } from "react";
import type { ParameterValues } from "../types/api";

/** 运行页参数草稿：目标接线图「存为实验参数」只合并进草稿，保存仍走运行页原有流程。 */
export type ParameterDraft = { values: ParameterValues; apply: (patch: ParameterValues) => void };

const WiringEditor = lazy(() => import("./wiring/WiringEditor").then(module => ({ default: module.WiringEditor })));

export type ExperimentView = "run" | "history" | "target";
const defaultViews: readonly ExperimentView[] = ["run", "history", "target"];
const labels: Record<ExperimentView, string> = { run: "运行", history: "历史", target: "目标接线图" };

/** 首次访问才挂载，之后只隐藏；切换实验视图不丢草稿或中断任务监听。 */
export function ExperimentViewPanel({ active, id, labelledBy, children }: {
  active: boolean; id: string; labelledBy: string; children: ReactNode;
}) {
  const [visited, setVisited] = useState(active);
  if (active && !visited) setVisited(true);
  return <section role="tabpanel" id={id} aria-labelledby={labelledBy} hidden={!active}>
    {(active || visited) && children}
  </section>;
}

/** 各实验共享视图导航；目标编辑固定当前稳定实验 ID，不提供跨实验切换。 */
export function ExperimentViewTabs({ experimentId, title, value, onChange, idPrefix = experimentId, label = "实验视图", views = defaultViews, parameterDraft }: {
  experimentId: string; title?: string; value: ExperimentView; onChange: (view: ExperimentView) => void;
  idPrefix?: string; label?: string; views?: readonly ExperimentView[]; parameterDraft?: ParameterDraft;
}) {
  function navigate(event: KeyboardEvent<HTMLDivElement>) {
    const origin = event.target instanceof Element ? event.target.closest<HTMLButtonElement>('[role="tab"]') : null;
    const index = origin ? Array.from(event.currentTarget.querySelectorAll('[role="tab"]')).indexOf(origin) : views.indexOf(value);
    let next: ExperimentView | undefined;
    if (event.key === "ArrowRight") next = views[(index + 1) % views.length];
    if (event.key === "ArrowLeft") next = views[(index - 1 + views.length) % views.length];
    if (event.key === "Home") next = views[0];
    if (event.key === "End") next = views.at(-1);
    if (!next) return;
    event.preventDefault(); onChange(next);
    const buttons = event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    buttons?.[views.indexOf(next)]?.focus();
  }
  return <>
    <div className="tab-strip" role="tablist" aria-label={label} onKeyDown={navigate}>
      {views.map(view => <button key={view} id={`${idPrefix}-tab-${view}`} role="tab" type="button"
        aria-selected={value === view} aria-controls={`${idPrefix}-panel-${view}`} tabIndex={value === view ? 0 : -1}
        onClick={() => onChange(view)}>{labels[view]}</button>)}
    </div>
    {views.includes("target") && <ExperimentViewPanel active={value === "target"} id={`${idPrefix}-panel-target`} labelledBy={`${idPrefix}-tab-target`}>
      <Suspense fallback={<p className="alert" role="status">正在载入目标接线图编辑器…</p>}>
        <WiringEditor key={experimentId} experimentId={experimentId} experimentTitle={title} active={value === "target"} showParameters={views.includes("run")} parameterDraft={parameterDraft} />
      </Suspense>
    </ExperimentViewPanel>}
  </>;
}
