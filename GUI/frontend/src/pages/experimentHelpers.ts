import type { ParameterLayout, ParameterValue, SchemaField } from "../types/api";

export function defaultParameterLayout(fields: SchemaField[]): ParameterLayout {
  return {
    basic: fields.filter((field) => field.group === "basic").map((field) => field.name),
    advanced: fields.filter((field) => field.group !== "basic").map((field) => field.name),
  };
}

export function validatedParameterLayout(fields: SchemaField[], value: unknown): ParameterLayout | undefined {
  if (!value || typeof value !== "object") return undefined;
  const candidate = value as Partial<ParameterLayout>;
  if (!Array.isArray(candidate.basic) || !Array.isArray(candidate.advanced)) return undefined;
  const fieldNames = fields.map((field) => field.name);
  const validNames = new Set(fieldNames);
  const allNames = [...candidate.basic, ...candidate.advanced];
  if (allNames.some((name) => typeof name !== "string" || !validNames.has(name))) return undefined;
  if (new Set(allNames).size !== allNames.length || allNames.length !== fieldNames.length) return undefined;
  return { basic: [...candidate.basic], advanced: [...candidate.advanced] };
}

export function loadLocalParameterLayout(fields: SchemaField[], storageKey: string): ParameterLayout {
  const fallback = defaultParameterLayout(fields);
  try {
    const saved = JSON.parse(window.localStorage.getItem(storageKey) || "null") as Partial<ParameterLayout> | null;
    if (!saved) return fallback;
    const validNames = new Set(fields.map((field) => field.name));
    const used = new Set<string>();
    const clean = (items: unknown) => Array.isArray(items)
      ? items.filter((name): name is string => (
        typeof name === "string" && validNames.has(name) && !used.has(name) && Boolean(used.add(name))
      ))
      : [];
    const layout: ParameterLayout = { basic: clean(saved.basic), advanced: clean(saved.advanced) };
    fields.forEach((field) => {
      if (!used.has(field.name)) layout[field.group === "basic" ? "basic" : "advanced"].push(field.name);
    });
    return layout;
  } catch {
    return fallback;
  }
}

export function parseArrayValue(
  value: string,
  defaultValue: ParameterValue,
): { value: number[] | string[] } | null {
  const items = value.split(",").map((item) => item.trim()).filter(Boolean);
  if (items.length === 0) return null;
  if (Array.isArray(defaultValue) && defaultValue.some((item) => typeof item === "string")) {
    return { value: items };
  }
  const numbers = items.map(Number);
  if (numbers.some((item) => !Number.isFinite(item))) return null;
  return { value: numbers };
}
