import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { PageHead } from "../components/PageHead";
import { Dashboard } from "../components/ZAWClosedLoop/Dashboard";
import { HistoryView } from "../components/ZAWClosedLoop/HistoryView";
import type { ZAWClosedLoopRunSummary } from "../components/ZAWClosedLoop/types";
import { ZAW_CLOSED_LOOP_EXPERIMENT_ID } from "../components/ZAWClosedLoop/images";
import type {
  ExperimentDefinition,
  ExperimentSchema,
  ParameterValues,
} from "../types/api";
import "../pages/zawClosedLoop.css";

type Tab = "run" | "history";

export function ZAWClosedLoopExperimentPage() {
  const [tab, setTab] = useState<Tab>("run");
  const [definition, setDefinition] = useState<ExperimentDefinition | undefined>();
  const [fields, setFields] = useState<ExperimentSchema["fields"]>([]);
  const [values, setValues] = useState<ParameterValues>({});
  const [loadError, setLoadError] = useState("");
  const [prefill, setPrefill] = useState<ParameterValues | null>(null);
  const [latestRun, setLatestRun] = useState<ZAWClosedLoopRunSummary | undefined>();

  useEffect(() => {
    let disposed = false;
    setDefinition(undefined);
    setFields([]);
    setValues({});
    setLoadError("");
    Promise.all([
      api<ExperimentDefinition>(`/api/experiments/${ZAW_CLOSED_LOOP_EXPERIMENT_ID}`),
      api<ExperimentSchema>(`/api/experiments/${ZAW_CLOSED_LOOP_EXPERIMENT_ID}/schema`),
    ])
      .then(([def, schema]) => {
        if (disposed) return;
        setDefinition(def);
        setFields(schema.fields);
        const initial = Object.fromEntries(
          schema.fields.map((field) => [field.name, field.default]),
        ) as ParameterValues;
        setValues(initial);
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
        eyebrow="EXPERIMENT · Z_AW_CLOSED_LOOP"
        title={definition?.title ?? "正在载入实验"}
        description={description}
      />
      {loadError && <div className="alert error">实验参数加载失败：{loadError}。请确认 GUI 后端已重启后刷新页面。</div>}
      {definition && fields.length > 0 && (
        <>
          <div className="zaw-tab-strip" role="tablist" aria-label="Z 闭环实验视图" onKeyDown={handleKeyDown}>
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
          </div>
          {tab === "run" && (
            <Dashboard
              experimentId={ZAW_CLOSED_LOOP_EXPERIMENT_ID}
              definition={definition}
              fields={fields}
              values={values}
              setValues={setValues}
              prefill={prefill}
              clearPrefill={() => setPrefill(null)}
              onLatestRun={setLatestRun}
            />
          )}
          {tab === "history" && (
            <HistoryView
              experimentId={ZAW_CLOSED_LOOP_EXPERIMENT_ID}
              fields={fields}
              onFillBack={(parameters) => {
                setPrefill(parameters as ParameterValues);
              }}
              onJumpToRun={() => setTab("run")}
            />
          )}
        </>
      )}
      {definition && !fields.length && !loadError && (
        <p className="zaw-empty">实验无可编辑参数。</p>
      )}
    </>
  );
}
