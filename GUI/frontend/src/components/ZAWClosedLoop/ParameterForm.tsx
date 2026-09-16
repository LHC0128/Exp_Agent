import { useMemo, useState, type KeyboardEvent } from "react";
import { Field, SelectField } from "../FormFields";
import type {
  ParameterGroup,
  ParameterLayout,
  ParameterValues,
  SchemaField,
} from "../../types/api";

type ParameterFormProps = {
  fields: SchemaField[];
  layout: ParameterLayout;
  values: ParameterValues;
  onChange: (field: SchemaField, value: ParameterValues[string]) => void;
  /** 参数在基础/高级之间移动后回传完整布局；只改分类，不改参数值。 */
  onLayoutChange: (next: ParameterLayout) => void;
  disabled: boolean;
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

export function ParameterForm({ fields, layout, values, onChange, onLayoutChange, disabled }: ParameterFormProps) {
  const [activeGroup, setActiveGroup] = useState<ParameterGroup>("basic");
  const [arrayDrafts, setArrayDrafts] = useState<Record<string, string>>({});

  const renderInput = (field: SchemaField) => {
    const value = values[field.name] ?? field.default;
    const label = field.unit ? `${field.label} (${field.unit})` : field.label;
    const isDisabled = disabled || Boolean(field.read_only);
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
        value={typeof value === "number" ? value : Number(value ?? 0)}
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
  const groupedFields = useMemo(() => ({
    basic: layout.basic.flatMap((name) => {
      const field = fieldsByName.get(name);
      return field ? [field] : [];
    }),
    advanced: layout.advanced.flatMap((name) => {
      const field = fieldsByName.get(name);
      return field ? [field] : [];
    }),
  }), [fieldsByName, layout]);

  /** 把参数移动到目标分组末尾；参数键与取值都不变，只更新 parameter_layout。 */
  const moveField = (name: string, target: ParameterGroup) => {
    const source: ParameterGroup = target === "basic" ? "advanced" : "basic";
    if (!layout[source].includes(name)) return;
    const next: ParameterLayout = {
      basic: [...layout.basic],
      advanced: [...layout.advanced],
    };
    next[source] = next[source].filter((item) => item !== name);
    next[target] = [...next[target], name];
    onLayoutChange(next);
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
      ?.querySelector<HTMLButtonElement>(`#zaw-parameter-tab-${next}`)
      ?.focus();
  };

  const activeFields = groupedFields[activeGroup];

  return (
    <div className="zaw-parameter-form">
      <div className="zaw-parameter-tabs" role="tablist" aria-label="参数分组">
        {parameterGroups.map((group) => {
          const selected = activeGroup === group;
          const label = group === "basic" ? "基础参数" : "高级参数";
          return (
            <button
              key={group}
              id={`zaw-parameter-tab-${group}`}
              type="button"
              role="tab"
              aria-label={`${label}，${groupedFields[group].length} 项`}
              aria-selected={selected}
              aria-controls={`zaw-parameter-panel-${group}`}
              tabIndex={selected ? 0 : -1}
              className={selected ? "active" : ""}
              onClick={() => setActiveGroup(group)}
              onKeyDown={handleTabKeyDown}
            >
              <span>{label}</span>
              <strong>{groupedFields[group].length}</strong>
            </button>
          );
        })}
      </div>
      <div
        id={`zaw-parameter-panel-${activeGroup}`}
        role="tabpanel"
        aria-labelledby={`zaw-parameter-tab-${activeGroup}`}
        className="zaw-parameter-panel"
      >
        {activeFields.length > 0
          ? (
            <div className="zaw-parameter-list">
              {activeFields.map((field) => {
                const target: ParameterGroup = activeGroup === "basic" ? "advanced" : "basic";
                const targetLabel = target === "basic" ? "基础" : "高级";
                return (
                  <div className="zaw-parameter-item" key={field.name}>
                    <button
                      type="button"
                      className="zaw-parameter-move"
                      aria-label={`${field.label} 移至${targetLabel}参数`}
                      title={`移至${targetLabel}参数`}
                      disabled={disabled}
                      onClick={() => moveField(field.name, target)}
                    >
                      移至{targetLabel}
                    </button>
                    {renderInput(field)}
                  </div>
                );
              })}
            </div>
          )
          : <p className="zaw-empty">当前分组暂无可编辑参数。</p>}
      </div>
      {fields.length === 0 && <p className="zaw-empty">暂无可编辑参数</p>}
    </div>
  );
}
