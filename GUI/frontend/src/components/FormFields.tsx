import { useEffect, useRef, type ReactNode } from "react";

type NumberFieldProps = {
  label: string;
  value: number | null | undefined;
  onChange: (value: number) => void;
  type?: "number";
  disabled?: boolean;
  min?: number;
  max?: number;
  step?: number | "any";
};

type TextFieldProps = {
  label: string;
  value: string | number | null | undefined;
  onChange: (value: string) => void;
  type: "text";
  disabled?: boolean;
  onBlur?: () => void;
};

/** 外部数值转输入框文本；空值与非数值显示为空。 */
function numberToText(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "";
  return String(value);
}

/** 解析有限数值；空串或非法输入返回 null。 */
function parseFiniteNumber(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const converted = Number(trimmed);
  return Number.isFinite(converted) ? converted : null;
}

export function Field(props: NumberFieldProps | TextFieldProps) {
  if (props.type === "text") {
    const { label, value, disabled = false, onChange, onBlur } = props;
    return (
      <label>
        <span>{label}</span>
        <input
          type="text"
          value={value ?? ""}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          onBlur={onBlur}
        />
      </label>
    );
  }
  return <NumberField {...props} />;
}

/**
 * 数字输入：非受控模式，编辑期间浏览器保留“-”“1.”等中间态，
 * 只有文本能解析为有限数值时才通过 onChange 回传；失焦时回退非法输入。
 */
function NumberField({
  label,
  value,
  onChange,
  disabled = false,
  min,
  max,
  step,
}: NumberFieldProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const focused = useRef(false);
  const lastCommitted = useRef(numberToText(value));

  useEffect(() => {
    // 外部快照刷新时同步显示值；用户正在输入时以输入内容为准。
    lastCommitted.current = numberToText(value);
    if (focused.current || !inputRef.current) return;
    if (inputRef.current.value !== numberToText(value)) {
      inputRef.current.value = numberToText(value);
    }
  }, [value]);

  return (
    <label>
      <span>{label}</span>
      <input
        ref={inputRef}
        type="number"
        defaultValue={numberToText(value)}
        disabled={disabled}
        min={min}
        max={max}
        step={step}
        onFocus={() => {
          focused.current = true;
        }}
        onChange={() => {
          const raw = inputRef.current?.value ?? "";
          const parsed = parseFiniteNumber(raw);
          if (parsed !== null) {
            lastCommitted.current = raw;
            onChange(parsed);
          }
        }}
        onBlur={() => {
          focused.current = false;
          const raw = inputRef.current?.value ?? "";
          const parsed = parseFiniteNumber(raw);
          if (parsed === null) {
            if (inputRef.current) inputRef.current.value = lastCommitted.current;
          } else if (parsed !== value) {
            lastCommitted.current = raw;
            onChange(parsed);
          }
        }}
      />
    </label>
  );
}

type SelectFieldProps = {
  label: string;
  value: string | number | null | undefined;
  onChange: (value: string) => void;
  disabled?: boolean;
  children: ReactNode;
};

export function SelectField({
  label,
  value,
  onChange,
  disabled = false,
  children,
}: SelectFieldProps) {
  return (
    <label>
      <span>{label}</span>
      <select
        value={value ?? ""}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        {children}
      </select>
    </label>
  );
}
