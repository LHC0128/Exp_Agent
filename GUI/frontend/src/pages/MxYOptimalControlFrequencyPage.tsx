import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { PageHead } from "../components/PageHead";
import { Dashboard } from "../components/MxYOptimalControlFrequency/Dashboard";
import { HistoryView } from "../components/MxYOptimalControlFrequency/HistoryView";
import { ArchitectureView } from "../components/MxYOptimalControlFrequency/ArchitectureView";
import { MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID } from "../components/MxYOptimalControlFrequency/images";
import type {
  ExperimentDefinition,
  ExperimentSchema,
  Job,
  JobSummary,
  ParameterLayout,
  ParameterValues,
} from "../types/api";
import {
  defaultParameterLayout,
  sameParameterLayout,
  validatedParameterLayout,
} from "./experimentHelpers";
import "../pages/zawClosedLoop.css";

type Tab = "run" | "history" | "architecture";

export function MxYOptimalControlFrequencyPage() {
  const [tab, setTab] = useState<Tab>("run");
  const [definition, setDefinition] = useState<ExperimentDefinition | undefined>();
  const [fields, setFields] = useState<ExperimentSchema["fields"]>([]);
  // 页面统一维护可变参数布局；savedLayout 是磁盘上最近一次确认的布局。
  const [layout, setLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [savedLayout, setSavedLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [values, setValues] = useState<ParameterValues>({});
  const [loadError, setLoadError] = useState("");
  const [prefill, setPrefill] = useState<ParameterValues | null>(null);
  // 任务状态保存在页面层：切换“运行/历史”Tab 或重新进入页面时不丢失。
  const [job, setJob] = useState<Job | undefined>();

  useEffect(() => {
    let disposed = false;
    // 恢复本实验最近的活动任务；没有活动任务时恢复最近一次已开始的任务。
    api<JobSummary[]>("/api/jobs")
      .then((summaries) => {
        const matching = summaries.filter(
          (item) => item.kind === `experiment:${MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID}`,
        );
        const pool = matching.some((item) => ["queued", "running"].includes(item.status))
          ? matching.filter((item) => ["queued", "running"].includes(item.status))
          : matching.filter((item) => item.started_at);
        if (disposed || pool.length === 0) return undefined;
        return api<Job>(`/api/jobs/${pool[0].id}`);
      })
      .then((detail) => {
        if (!disposed && detail) setJob(detail);
      })
      .catch(() => {
        // 后端不可用时保持无任务状态，页面自身会提示加载错误。
      });
    return () => {
      disposed = true;
    };
  }, []);

  useEffect(() => {
    let disposed = false;
    setDefinition(undefined);
    setFields([]);
    setLayout({ basic: [], advanced: [] });
    setSavedLayout({ basic: [], advanced: [] });
    setValues({});
    setLoadError("");
    Promise.all([
      api<ExperimentDefinition>(`/api/experiments/${MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID}`),
      api<ExperimentSchema>(`/api/experiments/${MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID}/schema`),
    ])
      .then(([def, schema]) => {
        if (disposed) return;
        setDefinition(def);
        setFields(schema.fields);
        const persisted = schema.parameter_layout_saved
          ? validatedParameterLayout(schema.fields, schema.parameter_layout)
          : undefined;
        const initialLayout = persisted ?? defaultParameterLayout(schema.fields);
        setLayout(initialLayout);
        setSavedLayout(initialLayout);
        setValues(Object.fromEntries(
          schema.fields.map((field) => [field.name, field.default]),
        ) as ParameterValues);
      })
      .catch((reason) => !disposed && setLoadError(String(reason)));
    return () => {
      disposed = true;
    };
  }, []);

  const description = useMemo(
    () => definition?.description ?? "读取实验定义与默认参数。",
    [definition],
  );

  // 参数在基础/高级之间移动后布局与磁盘不一致时，提示用户手动保存。
  const layoutDirty = fields.length > 0 && !sameParameterLayout(layout, savedLayout);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const target = event.currentTarget;
    const items = Array.from(target.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
    const currentIndex = items.findIndex((item) => item === document.activeElement);
    if (currentIndex === -1) return;
    const next = event.key === "ArrowRight"
      ? (currentIndex + 1) % items.length
      : (currentIndex - 1 + items.length) % items.length;
    items[next]?.focus();
    setTab(items[next]?.dataset.tab as Tab);
  };

  return (
    <>
      <PageHead
        eyebrow="EXPERIMENT · MX_Y_OPTIMAL_CONTROL_RF_FREQUENCY_RESPONSE"
        title={definition?.title ?? "正在载入实验"}
        description={description}
      />
      {loadError && <div className="alert error">实验参数加载失败：{loadError}。请确认 GUI 后端已重启后刷新页面。</div>}
      {definition && fields.length > 0 && (
        <>
          <div className="zaw-tab-strip" role="tablist" aria-label="Mx Y 最优控制频率响应实验视图" onKeyDown={handleKeyDown}>
            <button
              role="tab"
              data-tab="run"
              aria-selected={tab === "run"}
              className={tab === "run" ? "active" : ""}
              onClick={() => setTab("run")}
            >
              运行
            </button>
            <button
              role="tab"
              data-tab="history"
              aria-selected={tab === "history"}
              className={tab === "history" ? "active" : ""}
              onClick={() => setTab("history")}
            >
              历史
            </button>
            <button
              role="tab"
              data-tab="architecture"
              aria-selected={tab === "architecture"}
              className={tab === "architecture" ? "active" : ""}
              onClick={() => setTab("architecture")}
            >
              架构
            </button>
          </div>
          {tab === "run" && (
            <Dashboard
              experimentId={MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID}
              definition={definition}
              fields={fields}
              layout={layout}
              layoutDirty={layoutDirty}
              onLayoutChange={setLayout}
              onLayoutSaved={(persisted) => {
                setLayout(persisted);
                setSavedLayout(persisted);
              }}
              values={values}
              setValues={setValues}
              job={job}
              setJob={setJob}
              prefill={prefill}
              clearPrefill={() => setPrefill(null)}
            />
          )}
          {tab === "history" && (
            <HistoryView
              experimentId={MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID}
              fields={fields}
              onFillBack={(parameters) => {
                setPrefill(parameters as ParameterValues);
              }}
              onJumpToRun={() => setTab("run")}
            />
          )}
          {tab === "architecture" && <ArchitectureView />}
        </>
      )}
      {definition && !fields.length && !loadError && (
        <p className="zaw-empty">实验无可编辑参数。</p>
      )}
    </>
  );
}
