import { useId, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { ArrowLeftRight } from "lucide-react";
import { Field, SelectField } from "../FormFields";
import { useUnsavedChanges } from "../NavigationGuard";
import type {
  ParameterGroup,
  ParameterLayout,
  ParameterValues,
  SchemaField,
} from "../../types/api";
import {
  COMPARISON_FIELD_NAMES,
  COMPARISON_TOGGLE,
} from "./images";

type ParameterFormProps = {
  fields: SchemaField[];
  layout: ParameterLayout;
  values: ParameterValues;
  onChange: (field: SchemaField, value: ParameterValues[string]) => void;
  /** 参数在基础/高级之间移动后回传完整布局；只改分类，不改变参数值。 */
  onLayoutChange: (next: ParameterLayout) => void;
  /** 参数移动成功后的提示，例如「已移至基础参数」。 */
  onMoveNotice: (message: string) => void;
  disabled: boolean;
  presentation?: "tabs" | "workflow";
  renderParameterNote?: (field: SchemaField) => ReactNode;
};

const numberToText = (value: unknown): string => {
  if (Array.isArray(value)) return value.join(", ");
  if (value === null || value === undefined) return "";
  return String(value);
};

const parseArray = (text: string): number[] | null => {
  const pieces = text.split(",").map((item) => item.trim()).filter((item) => item.length > 0);
  if (!pieces.length) return [];
  const parsed: number[] = [];
  for (const piece of pieces) {
    const number = Number(piece);
    if (!Number.isFinite(number)) return null;
    parsed.push(number);
  }
  return parsed;
};

const parameterGroups: ParameterGroup[] = ["basic", "advanced"];

export function ParameterForm({
  fields,
  layout,
  values,
  onChange,
  onLayoutChange,
  onMoveNotice,
  disabled,
  presentation = "tabs",
  renderParameterNote,
}: ParameterFormProps) {
  const [activeGroup, setActiveGroup] = useState<ParameterGroup>("basic");
  const [arrayDrafts, setArrayDrafts] = useState<Record<string, string>>({});
  useUnsavedChanges(Object.entries(arrayDrafts).some(([name, draft]) => draft !== numberToText(values[name])));

  const comparisonEnabled = Boolean(values[COMPARISON_TOGGLE]);
  const fieldId = useId();
  const compensationField = fields.find(field => field.name === "CONTROL_SCALE_FACTORS");
  const controlField = compensationField && fields.find(field =>
    ["ARB_WAVEFORM_FILE", "CONTROL_WAVEFORM_FILES"].includes(field.name) && field.options !== undefined);

  const renderInput = (field: SchemaField) => {
    const value = values[field.name] ?? field.default;
    const label = field.unit ? `${field.label} (${field.unit})` : field.label;
    const isDisabled = disabled || Boolean(field.read_only);
    if (field === controlField && compensationField) {
      const selected = Array.isArray(value) ? value.map(String) : [String(value ?? "")];
      const factors = values[compensationField.name] ?? compensationField.default;
      const mapping = factors && typeof factors === "object" && !Array.isArray(factors) ? factors : {};
      return <fieldset className="zaw-multi-options zaw-control-options">
        <legend>{label}</legend>
        <div className="zaw-control-option-head"><span>控制波形</span><span>选择</span><span>{compensationField.label}</span></div>
        {field.options?.filter(option => option.value !== "").map((option, index) => {
          const relative = String(option.value), name = relative.split("/")[0];
          const checkboxId = `${fieldId}-${index}`;
          return <div className="zaw-control-option" key={relative}>
            <label htmlFor={checkboxId}>{option.label}</label>
            <input id={checkboxId} type="checkbox" checked={selected.includes(relative)} disabled={isDisabled}
              onChange={event => onChange(field, field.type === "array"
                ? event.target.checked ? [...selected, relative] : selected.filter(item => item !== relative)
                : event.target.checked ? relative : "")} />
            <div className="zaw-control-coefficient"><Field label={`${compensationField.label} · ${name}`}
              value={Object.hasOwn(mapping, name) ? mapping[name] : 1} step="any"
              disabled={isDisabled || Boolean(compensationField.read_only)}
              onChange={next => onChange(compensationField, { ...mapping, [name]: next })} /></div>
          </div>;
        })}
        {field.type !== "array" && <small>仅可选择一个控制波形。</small>}
      </fieldset>;
    }
    if (field.type === "array" && field.options?.length) {
      // 原生 multiple select 的选中反馈不明显且需 Ctrl 多选；
      // 改用勾选列表，勾选状态即选中状态。
      const selected = Array.isArray(value) ? value.map(String) : [];
      return <fieldset className="zaw-multi-options" key={field.name}>
        <legend>{label}</legend>
        {field.options.map(option => {
          const optionValue = String(option.value);
          return <label key={optionValue} className="zaw-multi-option">
            <input type="checkbox" checked={selected.includes(optionValue)} disabled={isDisabled}
              onChange={event => onChange(field, event.target.checked
                ? [...selected, optionValue]
                : selected.filter(item => item !== optionValue))} />
            <span>{option.label}</span>
          </label>;
        })}
      </fieldset>;
    }
    if (field.options && field.options.length > 0) {
      const stringValue = typeof value === "string" || typeof value === "number" ? String(value) : "";
      return (
        <SelectField
          key={field.name}
          label={label}
          value={stringValue}
          disabled={isDisabled}
          onChange={(selected) => {
            const match = field.options?.find((option) => String(option.value) === selected);
            onChange(field, match?.value ?? selected);
          }}
        >
          {field.options.map((option) => (
            <option key={String(option.value)} value={String(option.value)}>{option.label}</option>
          ))}
        </SelectField>
      );
    }
    if (field.type === "object") {
      const mapping = value && typeof value === "object" && !Array.isArray(value) ? value : {};
      return <fieldset className="zaw-multi-options"><legend>{label}</legend>{Object.entries(mapping).map(([name, factor]) =>
        <Field key={name} label={`${label} · ${name}`} value={factor} disabled={isDisabled}
          onChange={next => onChange(field, { ...mapping, [name]: next })} />)}
        {!Object.keys(mapping).length && <small>请先选择控制。</small>}
      </fieldset>;
    }
    if (field.type === "boolean") {
      return (
        <SelectField
          key={field.name}
          label={label}
          value={String(Boolean(value))}
          disabled={isDisabled}
          onChange={(selected) => onChange(field, selected === "true")}
        >
          <option value="true">是</option>
          <option value="false">否</option>
        </SelectField>
      );
    }
    if (field.type === "string") {
      return (
        <Field
          key={field.name}
          label={label}
          value={typeof value === "string" ? value : numberToText(value)}
          type="text"
          disabled={isDisabled}
          onChange={(next) => onChange(field, next)}
        />
      );
    }
    if (field.type === "array") {
      const draft = arrayDrafts[field.name] ?? (Array.isArray(value) ? value.join(", ") : "");
      return (
        <Field
          key={field.name}
          label={label}
          value={draft}
          type="text"
          disabled={isDisabled}
          onChange={(next) => setArrayDrafts((old) => ({ ...old, [field.name]: next }))}
          onBlur={() => {
            const parsed = parseArray(draft);
            if (parsed) {
              onChange(field, parsed);
              setArrayDrafts((old) => ({ ...old, [field.name]: parsed.join(", ") }));
            } else {
              const fallback = Array.isArray(value) ? value.join(", ") : "";
              setArrayDrafts((old) => ({ ...old, [field.name]: fallback }));
            }
          }}
        />
      );
    }
    return (
      <Field
        key={field.name}
        label={label}
        value={value === null ? null : typeof value === "number" ? value : Number(value ?? 0)}
        disabled={isDisabled}
        min={field.minimum}
        max={field.maximum}
        onChange={(next) => onChange(field, field.type === "integer" ? Math.trunc(next) : next)}
      />
    );
  };

  const fieldsByName = useMemo(
    () => new Map(fields.map((field) => [field.name, field])),
    [fields],
  );

  // 关闭对照时隐藏两个对照输入，但保留它们在 values 中的取值。
  const visibleNames = useMemo(() => {
    const hidden = new Set<string>(
      comparisonEnabled ? [] : [...COMPARISON_FIELD_NAMES],
    );
    if (controlField && compensationField) hidden.add(compensationField.name);
    return (group: ParameterGroup) =>
      layout[group].filter((name) => fieldsByName.has(name) && !hidden.has(name));
  }, [comparisonEnabled, fieldsByName, layout, controlField, compensationField]);

  /** 把参数移动到目标分组末尾；参数键与取值都不变，只更新 parameter_layout。 */
  const moveField = (name: string, target: ParameterGroup) => {
    const source: ParameterGroup = target === "basic" ? "advanced" : "basic";
    if (!layout[source].includes(name)) return;
    const next: ParameterLayout = {
      basic: [...layout.basic],
      advanced: [...layout.advanced],
    };
    const moving = name === controlField?.name && compensationField ? [name, compensationField.name] : [name];
    for (const group of parameterGroups) next[group] = next[group].filter(item => !moving.includes(item));
    next[target].push(...moving);
    onLayoutChange(next);
    // 视图停留在当前分组：参数从本组移出后即从列表中消失，不自动切换 Tab。
    onMoveNotice(target === "basic" ? "已移至基础参数" : "已移至高级参数");
  };

  const handleTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const current = parameterGroups.indexOf(activeGroup);
    const nextIndex = event.key === "Home"
      ? 0
      : event.key === "End"
        ? parameterGroups.length - 1
        : (current + (event.key === "ArrowRight" ? 1 : -1) + parameterGroups.length)
          % parameterGroups.length;
    const next = parameterGroups[nextIndex];
    setActiveGroup(next);
    event.currentTarget.parentElement
      ?.querySelector<HTMLButtonElement>(`#mxy-parameter-tab-${next}`)
      ?.focus();
  };

  const groupLabel = (group: ParameterGroup) => (group === "basic" ? "基础参数" : "高级参数");

  const renderGroup = (group: ParameterGroup) => (
    visibleNames(group).length > 0
      ? <div className="zaw-parameter-list">{visibleNames(group).map((name) => {
        const field = fieldsByName.get(name)!;
        const target: ParameterGroup = group === "basic" ? "advanced" : "basic";
        const targetLabel = target === "basic" ? "基础" : "高级";
        return <div className="zaw-parameter-item" key={field.name}>
          <button type="button" className="zaw-parameter-move"
            aria-label={`${field.label} 移至${targetLabel}参数`} title={`移至${targetLabel}参数`}
            disabled={disabled} onClick={() => moveField(field.name, target)}>
            <ArrowLeftRight size={12} aria-hidden="true" />移至{targetLabel}
          </button>
          {renderInput(field)}
          {field.default === null && ["number", "integer"].includes(field.type) && <button type="button"
            aria-label={`${field.label} 恢复自动`} disabled={disabled || field.read_only || values[field.name] === null}
            onClick={() => onChange(field, null)}>恢复自动</button>}
          {field.description && <small>{field.description}</small>}
          {renderParameterNote?.(field)}
          {field === controlField && compensationField && <>
            <small>{compensationField.description}</small>{renderParameterNote?.(compensationField)}
          </>}
        </div>;
      })}</div>
      : <p className="zaw-empty">当前分组暂无可编辑参数。</p>
  );

  if (presentation === "workflow") return <div className="zaw-parameter-form">
    <h4>基础参数（{visibleNames("basic").length}）</h4>
    {renderGroup("basic")}
    {visibleNames("advanced").length > 0 && <details className="zaw-parameter-advanced">
      <summary>高级参数（{visibleNames("advanced").length}）</summary>
      {renderGroup("advanced")}
    </details>}
  </div>;

  return (
    <div className="zaw-parameter-form">
      <div className="zaw-parameter-tabs" role="tablist" aria-label="参数分组">
        {parameterGroups.map((group) => {
          const selected = activeGroup === group;
          const label = groupLabel(group);
          return (
            <button
              key={group}
              id={`mxy-parameter-tab-${group}`}
              type="button"
              role="tab"
              aria-label={`${label}，${visibleNames(group).length} 项`}
              aria-selected={selected}
              aria-controls={`mxy-parameter-panel-${group}`}
              tabIndex={selected ? 0 : -1}
              className={selected ? "active" : ""}
              onClick={() => setActiveGroup(group)}
              onKeyDown={handleTabKeyDown}
            >
              <span>{label}</span>
              <strong>{visibleNames(group).length}</strong>
            </button>
          );
        })}
      </div>
      <div
        id={`mxy-parameter-panel-${activeGroup}`}
        role="tabpanel"
        aria-labelledby={`mxy-parameter-tab-${activeGroup}`}
        className="zaw-parameter-panel"
      >
        {renderGroup(activeGroup)}
      </div>
      {fields.length === 0 && <p className="zaw-empty">暂无可编辑参数</p>}
    </div>
  );
}
