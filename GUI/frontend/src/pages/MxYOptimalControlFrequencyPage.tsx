import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { PageHead } from "../components/PageHead";
import { recoverableJob, useExperimentTask, useRequestedJobId } from "../components/experimentNavigation";
import { useUnsavedChanges } from "../components/NavigationGuard";
import { ExperimentViewPanel, ExperimentViewTabs, type ExperimentView } from "../components/ExperimentViewTabs";
import { Dashboard } from "../components/MxYOptimalControlFrequency/Dashboard";
import { HistoryView } from "../components/MxYOptimalControlFrequency/HistoryView";
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

export function MxYOptimalControlFrequencyPage() {
  const [tab, setTab] = useState<ExperimentView>("run");
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
  const requestedJobId = useRequestedJobId();

  useEffect(() => {
    let disposed = false;
    // 恢复本实验最近的活动任务；没有活动任务时恢复最近一次已开始的任务。
    api<JobSummary[]>("/api/jobs")
      .then((summaries) => {
        const selected = recoverableJob(summaries, MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID, requestedJobId);
        if (disposed || !selected) return undefined;
        return api<Job>(`/api/jobs/${selected.id}`);
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
  // 常驻运行页按活会话保护参数；页面层只补充尚未保存的布局。
  useUnsavedChanges(layoutDirty);
  useExperimentTask({ ready: fields.length > 0, setTab });

  return (
    <>
      <PageHead
        eyebrow="EXPERIMENT · MX_Y_OPTIMAL_CONTROL_RF_FREQUENCY_RESPONSE"
        title={definition?.title ?? "正在载入实验"}
        description={description}
        experiment={definition}
      />
      {loadError && <div className="alert error">实验参数加载失败：{loadError}。请确认 GUI 后端已重启后刷新页面。</div>}
      <ExperimentViewTabs experimentId={MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID} title={definition?.title} value={tab} onChange={setTab} label="Mx Y 最优控制频率响应实验视图" />
      {definition && fields.length > 0 && (
        <>
          <div id={`${MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID}-panel-run`} role="tabpanel" aria-labelledby={`${MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID}-tab-run`} hidden={tab !== "run"}>
            <Dashboard
              experimentId={MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID}
              definition={definition}
              fields={fields}
              layout={layout}
              layoutDirty={layoutDirty}
              onLayoutChange={setLayout}
              onLayoutSaved={(persisted, savedFields) => {
                setLayout(persisted);
                setSavedLayout(persisted);
                setFields(savedFields);
              }}
              values={values}
              setValues={setValues}
              job={job}
              setJob={setJob}
              prefill={prefill}
              clearPrefill={() => setPrefill(null)}
            />
          </div>
          <ExperimentViewPanel active={tab === "history"} id={`${MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID}-panel-history`} labelledBy={`${MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID}-tab-history`}>
            <HistoryView
              experimentId={MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID}
              fields={fields}
              onFillBack={(parameters) => {
                setPrefill(parameters as ParameterValues);
              }}
              onJumpToRun={() => setTab("run")}
            />
          </ExperimentViewPanel>
        </>
      )}
      {definition && !fields.length && !loadError && (
        <p className="zaw-empty">实验无可编辑参数。</p>
      )}
    </>
  );
}
