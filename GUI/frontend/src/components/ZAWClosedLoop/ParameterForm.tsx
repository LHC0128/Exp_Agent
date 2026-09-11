import { useState } from "react";
import { Field, SelectField } from "../FormFields";
import type { SchemaField, ParameterValues } from "../../types/api";

type ParameterFormProps = {
  fields: SchemaField[];
  values: ParameterValues;
  onChange: (field: SchemaField, value: ParameterValues[string]) => void;
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

export function ParameterForm({ fields, values, onChange, disabled }: ParameterFormProps) {
  const [advancedOpen, setAdvancedOpen] = useState(true);
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

  const basic = fields.filter((field) => field.group !== "advanced");
  const advanced = fields.filter((field) => field.group === "advanced");

  return (
    <div className="zaw-parameter-form">
      <div className="zaw-parameter-section">
        <div className="zaw-parameter-section-head"><h3>基础参数</h3><span>{basic.length}</span></div>
        <div className="zaw-parameter-list">{basic.map((field) => renderInput(field))}</div>
      </div>
      {advanced.length > 0 && (
        <div className={`zaw-parameter-section ${advancedOpen ? "" : "collapsed"}`}>
          <div className="zaw-parameter-section-head">
            <h3>高级参数</h3>
            <div className="zaw-parameter-section-actions">
              <span>{advanced.length}</span>
              <button
                className="zaw-icon-button"
                aria-label={advancedOpen ? "收起高级参数" : "展开高级参数"}
                aria-expanded={advancedOpen}
                onClick={() => setAdvancedOpen((current) => !current)}
              >
                {advancedOpen ? "▾" : "▸"}
              </button>
            </div>
          </div>
          {advancedOpen && <div className="zaw-parameter-list">{advanced.map((field) => renderInput(field))}</div>}
        </div>
      )}
      {fields.length === 0 && <p className="zaw-empty">暂无可编辑参数</p>}
    </div>
  );
}
