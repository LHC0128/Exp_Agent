import type { ReactNode } from "react";

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
};

export function Field(props: NumberFieldProps | TextFieldProps) {
  const { label, value, disabled = false } = props;
  const type = props.type ?? "number";
  return (
    <label>
      <span>{label}</span>
      <input
        type={type}
        value={value ?? ""}
        disabled={disabled}
        min={props.type === "text" ? undefined : props.min}
        max={props.type === "text" ? undefined : props.max}
        step={props.type === "text" ? undefined : props.step}
        onChange={(event) => {
          if (props.type === "text") props.onChange(event.target.value);
          else props.onChange(Number(event.target.value));
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
