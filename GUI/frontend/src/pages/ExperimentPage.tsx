import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { api } from "../api";
import { ScopeCapturePage } from "./ScopeCapturePage";
import { ZAWClosedLoopExperimentPage } from "./ZAWClosedLoopExperimentPage";
import { Field, SelectField } from "../components/FormFields";
import { useJobActivity } from "../components/JobActivity";
import { JobView } from "../components/JobView";
import { PageHead } from "../components/PageHead";
import { Status } from "../components/Status";
import {
  defaultParameterLayout,
  loadLocalParameterLayout,
  parseArrayValue,
  validatedParameterLayout,
} from "./experimentHelpers";
import type {
  ExperimentDefinition,
  ExperimentSchema,
  Job,
  JobSummary,
  ParameterGroup,
  ParameterLayout,
  ParameterValue,
  ParameterValues,
  SchemaField,
} from "../types/api";

export function ExperimentPage() {
  const { experimentId } = useParams();
  if (experimentId === "scope-capture") return <ScopeCapturePage />;
  if (experimentId === "z-aw-closed-loop-waveform-correction") return <ZAWClosedLoopExperimentPage />;
  return <GenericExperimentPage />;
}

function GenericExperimentPage() {
  const { experimentId = "static-sensitivity" } = useParams();
  const layoutKey = `exp-agent:${experimentId}:parameter-layout`;
  const collapsedKey = `exp-agent:${experimentId}:advanced-collapsed`;
  const [definition, setDefinition] = useState<ExperimentDefinition>();
  const [fields, setFields] = useState<SchemaField[]>([]);
  const [values, setValues] = useState<ParameterValues>({});
  const [layout, setLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [advancedCollapsed, setAdvancedCollapsed] = useState(() => window.localStorage.getItem(collapsedKey) !== "false");
  const [dragging, setDragging] = useState<string>();
  const [dropTarget, setDropTarget] = useState<string>();
  const [preflight, setPreflight] = useState<{ ok: boolean; errors: string[] }>();
  const [defaultsStatus, setDefaultsStatus] = useState<{ ok: boolean; message: string }>();
  const [schemaError, setSchemaError] = useState("");
  const [arrayDrafts, setArrayDrafts] = useState<Record<string, string>>({});
  const [savingDefaults, setSavingDefaults] = useState(false);
  const [starting, setStarting] = useState(false);
  const [actionError, setActionError] = useState("");
  const [job, setJob] = useState<Job>();
  const { hardwareBusy, activeJobs } = useJobActivity();

  useEffect(() => {
    let disposed = false;
    setDefinition(undefined); setFields([]); setJob(undefined); setPreflight(undefined);
    setDefaultsStatus(undefined); setSchemaError(""); setActionError(""); setArrayDrafts({});
    Promise.all([
      api<ExperimentDefinition>(`/api/experiments/${experimentId}`),
      api<ExperimentSchema>(`/api/experiments/${experimentId}/schema`),
    ]).then(([definitionValue, schema]) => {
      if (disposed) return;
      const savedLayout = schema.parameter_layout_saved
        ? validatedParameterLayout(schema.fields, schema.parameter_layout)
        : undefined;
      setDefinition(definitionValue);
      setFields(schema.fields);
      const initial = Object.fromEntries(schema.fields.map((item) => [item.name, item.default]));
      setValues(initial);
      setLayout(savedLayout || (
        schema.parameter_layout_saved
          ? defaultParameterLayout(schema.fields)
          : loadLocalParameterLayout(schema.fields, layoutKey)
      ));
      // 页面加载时仅刷新只读派生字段显示（如共同触发频率），不覆盖可编辑参数。
      api<{ ok: boolean; values?: ParameterValues }>(`/api/experiments/${experimentId}/derive`, {
        method: "POST",
        body: JSON.stringify({ parameters: initial }),
      }).then((result) => {
        if (disposed || !result.ok || !result.values) return;
        const readOnlyNames = new Set(
          schema.fields.filter((item) => item.read_only).map((item) => item.name),
        );
        setValues((old) => {
          const next = { ...old };
          for (const [name, value] of Object.entries(result.values || {})) {
            if (readOnlyNames.has(name)) next[name] = value;
          }
          return next;
        });
      }).catch(() => {
        // 派生计算失败时保持默认显示，预检会给出具体错误。
      });
    }).catch((reason) => {
      if (!disposed) setSchemaError(String(reason));
    });
    return () => { disposed = true; };
  }, [experimentId]);

  useEffect(() => {
    let disposed = false;
    api<JobSummary[]>("/api/jobs").then(async (jobs) => {
      if (disposed) return;
      const matching = jobs.filter((item) => item.kind === `experiment:${experimentId}`);
      const recovered = matching.find((item) => ["queued", "running"].includes(item.status))
        || matching.find((item) => Boolean(item.started_at));
      if (!recovered) return;
      try {
        const full = await api<Job>(`/api/jobs/${recovered.id}`);
        if (!disposed) setJob(full);
      } catch {
        if (!disposed) setJob({ ...recovered, events: [] });
      }
    }).catch(() => {
      // 任务列表读取失败时保持无任务状态，不影响参数编辑。
    });
    return () => { disposed = true; };
  }, [experimentId]);
  useEffect(() => {
    if (fields.length) window.localStorage.setItem(layoutKey, JSON.stringify(layout));
  }, [fields.length, layout, layoutKey]);
  useEffect(() => {
    window.localStorage.setItem(collapsedKey, String(advancedCollapsed));
  }, [advancedCollapsed, collapsedKey]);

  const fieldsByName = useMemo(() => new Map(fields.map((field) => [field.name, field])), [fields]);
  const moveParameter = (name: string, group: ParameterGroup, before?: string) => setLayout((current) => {
    const next: ParameterLayout = {
      basic: current.basic.filter((item) => item !== name),
      advanced: current.advanced.filter((item) => item !== name),
    };
    const index = before ? next[group].indexOf(before) : -1;
    next[group].splice(index >= 0 ? index : next[group].length, 0, name);
    return next;
  });
  const reorderParameter = (name: string, direction: -1 | 1) => setLayout((current) => {
    const group: ParameterGroup = current.basic.includes(name) ? "basic" : "advanced";
    const index = current[group].indexOf(name);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= current[group].length) return current;
    const next = { basic: [...current.basic], advanced: [...current.advanced] };
    [next[group][index], next[group][target]] = [next[group][target], next[group][index]];
    return next;
  });
  const resetLayout = () => setLayout(defaultParameterLayout(fields));
  const finishDrop = (group: ParameterGroup, before?: string) => {
    if (dragging && dragging !== before) moveParameter(dragging, group, before);
    setDragging(undefined); setDropTarget(undefined);
  };
  const check = async () => {
    setActionError("");
    try {
      setPreflight(await api<{ ok: boolean; errors: string[] }>(`/api/experiments/${experimentId}/preflight`, {
        method: "POST",
        body: JSON.stringify({ parameters: values }),
      }));
    } catch (reason) {
      setActionError(String(reason));
    }
  };
  const saveDefaults = async () => {
    setSavingDefaults(true); setDefaultsStatus(undefined);
    try {
      const result = await api<{ ok: boolean; message: string; schema: ExperimentSchema }>(
        `/api/experiments/${experimentId}/defaults`,
        { method: "PUT", body: JSON.stringify({ parameters: values, parameter_layout: layout }) },
      );
      const savedLayout = result.schema.parameter_layout_saved
        ? validatedParameterLayout(result.schema.fields, result.schema.parameter_layout)
        : undefined;
      if (!savedLayout) throw new Error("后端未确认参数分类已保存，请重启 GUI 后端后重试");
      if (JSON.stringify(savedLayout) !== JSON.stringify(layout)) throw new Error("后端回传的参数分类与当前布局不一致，未刷新页面布局");
      setFields(result.schema.fields); setLayout(savedLayout);
      setDefaultsStatus({ ok: true, message: result.message });
    } catch (error) {
      setDefaultsStatus({ ok: false, message: String(error) });
    } finally { setSavingDefaults(false); }
  };
  const jobActive = Boolean(job && ["queued", "running"].includes(job.status));
  const ownActive = Boolean(job && activeJobs.some((item) => item.id === job.id));
  const blockedByOthers = hardwareBusy && !ownActive;
  const run = async () => {
    if (starting || jobActive) return;
    setStarting(true);
    setActionError("");
    try {
      const result = await api<{ ok: boolean; errors: string[] }>(`/api/experiments/${experimentId}/preflight`, {
        method: "POST",
        body: JSON.stringify({ parameters: values }),
      });
      setPreflight(result);
      if (result.ok) {
        setJob(await api<Job>(`/api/experiments/${experimentId}/runs`, {
          method: "POST",
          body: JSON.stringify({ parameters: values }),
        }));
      }
    } catch (reason) {
      setActionError(String(reason));
    } finally { setStarting(false); }
  };

  const updateValue = (field: SchemaField, value: ParameterValue) => {
    setValues((old) => ({ ...old, [field.name]: value }));
    if (field.name === "CONTROL_VERSION" || field.name === "CONTROL_RESULTS_ROOT") {
      // 控制版本或结果目录变化时，刷新全部派生显示值（只读触发频率与默认 Y RF 频率）。
      const next = { ...values, [field.name]: value };
      api<{ ok: boolean; values?: ParameterValues }>(`/api/experiments/${experimentId}/derive`, {
        method: "POST",
        body: JSON.stringify({ parameters: next }),
      }).then((result) => {
        if (!result.ok || !result.values) return;
        setValues((old) => ({ ...old, ...result.values }));
      }).catch(() => {
        // 派生计算失败时保持当前输入，预检会给出具体错误。
      });
    }
  };
  const renderInput = (field: SchemaField) => {
    const value = values[field.name] ?? field.default;
    const label = `${field.label}${field.unit ? ` (${field.unit})` : ""}`;
    const disabled = Boolean(field.read_only);
    if (field.options) {
      return (
        <SelectField label={label} value={typeof value === "string" || typeof value === "number" ? value : ""} disabled={disabled} onChange={(selected) => {
          const matched = field.options?.find((option) => String(option.value) === selected);
          updateValue(field, matched?.value ?? selected);
        }}>
          {field.options.length
            ? field.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)
            : <option value="">未找到可选文件</option>}
        </SelectField>
      );
    }
    if (field.type === "boolean") {
      return (
        <SelectField label={label} value={String(Boolean(value))} disabled={disabled} onChange={(selected) => updateValue(field, selected === "true")}>
          <option value="true">是</option><option value="false">否</option>
        </SelectField>
      );
    }
    if (field.type === "string") {
      return (
        <Field
          label={label}
          value={typeof value === "string" ? value : ""}
          type="text"
          disabled={disabled}
          onChange={(next) => updateValue(field, next)}
        />
      );
    }
    if (field.type === "array") {
      const draft = arrayDrafts[field.name] ?? (Array.isArray(value) ? value.join(", ") : "");
      return (
        <Field
          label={label}
          value={draft}
          type="text"
          disabled={disabled}
          onChange={(next) => setArrayDrafts((old) => ({ ...old, [field.name]: next }))}
          onBlur={() => {
            const parsed = parseArrayValue(draft, field.default);
            const committed = Array.isArray(value) ? value.join(", ") : "";
            setArrayDrafts((old) => ({ ...old, [field.name]: parsed ? parsed.value.join(", ") : committed }));
            if (parsed) updateValue(field, parsed.value);
          }}
        />
      );
    }
    return (
      <Field
        label={label}
        value={typeof value === "number" ? value : Number(value)}
        disabled={disabled}
        min={field.minimum}
        max={field.maximum}
        onChange={(next) => updateValue(field, field.type === "integer" ? Math.trunc(next) : next)}
      />
    );
  };

  const renderGroup = (group: ParameterGroup, title: string) => {
    const collapsed = group === "advanced" && advancedCollapsed;
    return (
      <section
        className={`parameter-group ${collapsed ? "collapsed" : ""} ${dragging ? "drag-active" : ""}`}
        onDragOver={(event) => { event.preventDefault(); setDropTarget(`${group}:end`); }}
        onDrop={(event) => { event.preventDefault(); finishDrop(group); }}
      >
        <div className="parameter-group-head">
          <div><h3>{title}</h3><span>{layout[group].length}</span></div>
          <div className="parameter-group-actions">
            {group === "basic" && <button className="icon-button" title="恢复默认分类和顺序" aria-label="恢复默认分类和顺序" onClick={resetLayout}>↺</button>}
            {group === "advanced" && <button className="icon-button collapse-button" title={collapsed ? "展开高级参数" : "收起高级参数"} aria-label={collapsed ? "展开高级参数" : "收起高级参数"} aria-expanded={!collapsed} onClick={() => setAdvancedCollapsed((current) => !current)}>{collapsed ? "▸" : "▾"}</button>}
          </div>
        </div>
        {!collapsed && (
          <div className="parameter-list">
            {layout[group].map((name, index) => {
              const field = fieldsByName.get(name);
              if (!field) return null;
              const targetKey = `${group}:${name}`;
              return (
                <div
                  className={`parameter-item ${dragging === name ? "is-dragging" : ""} ${dropTarget === targetKey ? "drop-before" : ""}`}
                  key={name}
                  onDragOver={(event) => { event.preventDefault(); event.stopPropagation(); setDropTarget(targetKey); }}
                  onDrop={(event) => { event.preventDefault(); event.stopPropagation(); finishDrop(group, name); }}
                >
                  <div className="parameter-tools">
                    <span className="drag-handle" draggable title="拖动参数" aria-label={`拖动${field.label}`} onDragStart={(event) => { setDragging(name); event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", name); }} onDragEnd={() => { setDragging(undefined); setDropTarget(undefined); }}>⋮⋮</span>
                    <button className="icon-button" title="上移" aria-label={`${field.label}上移`} disabled={index === 0} onClick={() => reorderParameter(name, -1)}>↑</button>
                    <button className="icon-button" title="下移" aria-label={`${field.label}下移`} disabled={index === layout[group].length - 1} onClick={() => reorderParameter(name, 1)}>↓</button>
                    <button className="icon-button" title={group === "basic" ? "移至高级参数" : "移至基础参数"} aria-label={group === "basic" ? `${field.label}移至高级参数` : `${field.label}移至基础参数`} onClick={() => moveParameter(name, group === "basic" ? "advanced" : "basic")}>⇄</button>
                  </div>
                  {renderInput(field)}
                </div>
              );
            })}
            {layout[group].length === 0 && <div className="parameter-empty">暂无参数</div>}
            <div className={`parameter-drop-end ${dropTarget === `${group}:end` ? "active" : ""}`} />
          </div>
        )}
      </section>
    );
  };

  return (
    <>
      <PageHead
        eyebrow={`${definition?.category_label || "EXPERIMENT"} · ${definition?.variant || ""}`}
        title={definition?.title || "正在载入实验"}
        description={definition?.description || "读取实验定义与默认参数。"}
      />
      <section className="experiment-card">
        {definition && (
          <div className="experiment-notes">
            <span>所需设备：{definition.required_devices.join(" · ")} · {definition.execution_mode === "typed_workflow" ? "新模式" : "旧模式"}</span>
            {definition.wiring_notes.map((note) => <p key={note}>{note}</p>)}
          </div>
        )}
        <div className="experiment-strip">
          <div><Status>CONFIGURATION</Status><h2>实验参数</h2></div>
          <button className="secondary" disabled={savingDefaults || !fields.length} onClick={saveDefaults}>{savingDefaults ? "保存中…" : "存为默认参数"}</button>
        </div>
        {schemaError && <div className="alert error">实验参数加载失败：{schemaError}。请确认 GUI 后端已重启后刷新页面。</div>}
        <div className={`parameter-layout ${advancedCollapsed ? "advanced-collapsed" : ""}`}>{renderGroup("basic", "基础参数")}{renderGroup("advanced", "高级参数")}</div>
        {blockedByOthers && <div className="alert">其他硬件任务正在运行，等待其完成后才能启动本实验。</div>}
        <div className="experiment-actions">
          <button className="secondary" disabled={starting || jobActive || !fields.length} onClick={check}>仅预检</button>
          <button disabled={starting || jobActive || !fields.length || blockedByOthers} onClick={run}>{jobActive ? "实验运行中" : starting ? "正在启动…" : "预检并运行实验"}</button>
        </div>
        {defaultsStatus && <div className={`alert ${defaultsStatus.ok ? "success" : "error"}`}>{defaultsStatus.message}</div>}
        {preflight && <div className={`alert ${preflight.ok ? "success" : "error"}`}>{preflight.ok ? "参数预检通过，可以启动实验。" : preflight.errors.join("；")}</div>}
        {actionError && <div className="alert error">{actionError}</div>}
      </section>
      <JobView job={job} onUpdate={setJob} />
    </>
  );
}
