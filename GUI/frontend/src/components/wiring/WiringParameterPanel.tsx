import type { ParameterValue, SchemaField } from "../../types/api";

const formatValue = (field: SchemaField, value: ParameterValue): string => {
  if (value === null || value === "") return "未设置";
  const option = field.options?.find(item => item.value === value);
  if (option) return option.label;
  if (typeof value === "boolean") return value ? "开启" : "关闭";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "未设置";
  if (typeof value === "object") {
    const entries = Object.entries(value);
    return entries.length ? entries.map(([key, item]) => `${key}: ${item}`).join("; ") : "未设置";
  }
  return field.unit ? `${value} ${field.unit}` : String(value);
};

/** 目标接线图选中器件时，只读列出本实验写入该器件用途键的已保存参数；编辑仍在运行页参数表单。 */
export function WiringParameterPanel({ deviceName, mappingKeys, fields, loading, error }: {
  deviceName: string | null;
  mappingKeys: string[];
  fields: SchemaField[] | null;
  loading: boolean;
  error: string;
}) {
  const keys = new Set(mappingKeys);
  const linked = (fields ?? []).filter(field => field.roles?.some(role => keys.has(role)));
  return <section className="wiring-panel wiring-parameters" aria-label="器件关联实验参数">
    <h2>实验参数（只读）</h2>
    {!deviceName ? <p className="wiring-help">在画布中选中器件后，显示本实验写入该器件的参数。</p>
      : loading ? <p className="wiring-help">正在读取实验参数…</p>
        : error ? <p className="wiring-help">实验参数读取失败：{error}</p>
          : !mappingKeys.length ? <p className="wiring-help">“{deviceName}”未关联用途键，没有可显示的实验参数。</p>
            : !linked.length ? <p className="wiring-help">本实验没有写入“{deviceName}”（{mappingKeys.join(" / ")}）的参数。</p>
              : <>
                <p className="wiring-help">用途：{mappingKeys.join(" / ")}。显示已保存值，修改请在「运行」页参数表单中进行。</p>
                <dl className="wiring-parameter-list">
                  {linked.map(field => {
                    const shared = field.roles!.filter(role => !keys.has(role));
                    return <div key={field.name}>
                      <dt title={field.name}>{field.label}</dt>
                      <dd>{formatValue(field, field.default)}</dd>
                      {shared.length > 0 && <dd className="wiring-parameter-shared">同时作用于 {shared.join(" / ")}</dd>}
                    </div>;
                  })}
                </dl>
              </>}
  </section>;
}
