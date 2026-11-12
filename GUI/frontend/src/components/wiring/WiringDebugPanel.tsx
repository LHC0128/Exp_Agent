import { useEffect, useState } from "react";
import { api } from "../../api";
import { GeneratorEditor } from "../instruments/GeneratorEditor";
import { useJobActivity } from "../JobActivity";
import type { ControlTarget, ControlTargetCatalog, ControlTargetResponse, DeviceSnapshot, GeneratorChannelSnapshot, GeneratorSnapshot, ParameterSetting, ParameterValue, ParameterValues, SchemaField } from "../../types/api";
import type { ParameterDraft } from "../ExperimentViewTabs";
import { WiringParameterPanel } from "./WiringParameterPanel";

type Reading = { snapshot: DeviceSnapshot; readAt: string };
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const number = (value: number | null, unit: string) => value === null ? "—" : `${value} ${unit}`.trim();
const isGenerator = (target: ControlTarget) => target.kind === "generator";
const limitText = (target: ControlTarget) => target.safety.min != null && target.safety.max != null
  ? `${target.safety.min} ～ ${target.safety.max} V` : undefined;
const parameterText = (value: ParameterValue | undefined) => value === null || value === undefined ? "—"
  : typeof value === "object" ? JSON.stringify(value) : String(value);
const generatorOf = (snapshot: DeviceSnapshot): GeneratorSnapshot | null => snapshot.type === "DG4000" || snapshot.type === "DG900" ? snapshot : null;
const channelOf = (target: ControlTarget, snapshot: GeneratorSnapshot) => snapshot.channels
  .find(channel => channel.mapping_keys.includes(target.mapping_key) || channel.mapping_key === target.mapping_key) ?? null;

/** 画布节点调试徽标：已回读、回读与实验参数不同。 */
export type DebugBadge = "read" | "diff";
export const debugBadgeLabel: Record<DebugBadge, string> = { read: "已回读", diff: "≠ 实验参数" };

type SettingField = "frequency" | "amplitude" | "offset" | "phase" | "square_duty" | "pulse_width" | "burst_phase";
const settingLabels: Record<SettingField, string> = {
  frequency: "频率", amplitude: "幅度", offset: "偏置", phase: "相位", square_duty: "占空比", pulse_width: "脉宽", burst_phase: "Burst 相位",
};
const settingFields = Object.keys(settingLabels) as SettingField[];
/** Burst 相位不是通道主参数，回读取 burst.phase。 */
const readbackOf = (channel: GeneratorChannelSnapshot, field: SettingField) => field === "burst_phase" ? channel.burst.phase : channel[field];
/** Vpp / Vrms 波峰比：正弦 2√2，方波 2，锯齿 2√3；其他波形无确定换算。 */
const VPP_PER_VRMS: Array<[RegExp, number]> = [[/^SIN/i, 2 * Math.SQRT2], [/^SQU/i, 2], [/^RAMP/i, 2 * Math.sqrt(3)]];
/**
 * 回读换算到实验参数单位后再比较与回存：幅度参数按 Vpp 登记，Vrms/dBm 通道按波形换算，
 * dBm 还需数值负载；无法换算时返回 null，不标差异也不回存。
 */
function comparableOf(channel: GeneratorChannelSnapshot, field: SettingField): number | null {
  const value = readbackOf(channel, field);
  if (field !== "amplitude" || value === null) return value;
  const unit = (channel.voltage_unit || "").toUpperCase();
  if (unit === "VPP") return value;
  const factor = VPP_PER_VRMS.find(([pattern]) => pattern.test(channel.shape))?.[1];
  if (!factor) return null;
  if (unit === "VRMS") return value * factor;
  const load = Number(channel.load);
  if (unit !== "DBM" || !Number.isFinite(load) || load <= 0 || load >= 9e37) return null;
  return Math.sqrt(10 ** (value / 10) / 1000 * load) * factor;
}

function differs(readback: number | null, saved: ParameterValue | undefined, field: SettingField) {
  if (readback === null || typeof saved !== "number" || !Number.isFinite(readback) || !Number.isFinite(saved)) return false;
  const normalize = (value: number) => field === "phase" || field === "burst_phase" ? ((value % 360) + 360) % 360 : value;
  const a = normalize(readback); const b = normalize(saved);
  return Math.abs(a - b) > Math.max(1e-9, 1e-6 * Math.abs(b));
}

type ComparisonRow = { field: SettingField; entries: Array<{ parameter: SchemaField; binding: ParameterSetting }> };

/** 只列出本用途已登记设置项的行；非方波时占空比回读无意义，不列出。 */
function comparisonRows(target: ControlTarget, channel: GeneratorChannelSnapshot, fields: SchemaField[] | null): ComparisonRow[] {
  const entries = (fields ?? []).flatMap(parameter => (parameter.settings ?? [])
    .filter(binding => binding.role === target.mapping_key && binding.field in settingLabels)
    .map(binding => ({ parameter, binding })));
  return settingFields
    .filter(field => entries.some(item => item.binding.field === field) && (field !== "square_duty" || /^SQU/i.test(channel.shape)))
    .map(field => ({ field, entries: entries.filter(item => item.binding.field === field) }));
}

/** 只读对比：一列仪器回读，一列实验参数；derived 只展示关联参数与换算依据。 */
function GeneratorComparison({ target, channel, rows, valueOf }: {
  target: ControlTarget;
  channel: GeneratorChannelSnapshot;
  rows: ComparisonRow[];
  valueOf: (field: SchemaField) => ParameterValue | undefined;
}) {
  const amplitudeUnit = (channel.voltage_unit || "").toUpperCase() === "VPP" ? "Vpp" : channel.voltage_unit || "";
  const unitOf = (field: SettingField) => field === "frequency" ? "Hz" : field === "amplitude" ? amplitudeUnit
    : field === "offset" ? "V" : field === "square_duty" ? "%" : field === "pulse_width" ? "s" : "°";
  return <div className="wiring-debug-table-wrap"><table className="wiring-debug-table" aria-label={`${target.label}仪器回读与实验参数`}>
    <thead><tr><th scope="col">设置项</th><th scope="col">仪器回读</th><th scope="col">实验参数</th></tr></thead>
    <tbody>{rows.map(row => {
      const readback = readbackOf(channel, row.field);
      const comparable = comparableOf(channel, row.field);
      const different = row.entries.some(({ parameter, binding }) => binding.relation === "direct" && differs(comparable, valueOf(parameter), row.field));
      return <tr key={row.field} className={different ? "wiring-debug-diff" : undefined}>
        <th scope="row">{settingLabels[row.field]}</th>
        <td>{number(readback, unitOf(row.field))}{row.field === "amplitude" && amplitudeUnit !== "Vpp" && <small className="wiring-debug-derived">
          {comparable === null ? "该波形/负载无法换算为 Vpp，不比较" : `≈ ${Number(comparable.toPrecision(6))} Vpp`}</small>}</td>
        <td>{different && <span aria-label="与仪器回读不同">≠</span>}{row.entries.map(({ parameter, binding }) => <div key={parameter.name}>
          {parameter.label}：{parameterText(valueOf(parameter))} {parameter.unit}
          {binding.relation === "derived" && <small className="wiring-debug-derived">{binding.note}</small>}
        </div>)}</td>
      </tr>;
    })}</tbody>
  </table></div>;
}

/** 目标接线图「调试」页：按器件用途键远程操作信号源，并对比本实验已保存参数。 */
export function WiringDebugPanel({ active = true, deviceName, mappingKeys, fields, fieldsLoading, fieldsError, parameterDraft, onStatusChange }: {
  /** 调试页可见；首次可见前不请求物理量目标，避免只看接线时产生仪器相关请求。 */
  active?: boolean;
  deviceName: string | null;
  mappingKeys: string[];
  fields: SchemaField[] | null;
  fieldsLoading: boolean;
  fieldsError: string;
  parameterDraft?: ParameterDraft;
  /** 按用途键汇报调试状态，供画布节点显示徽标。 */
  onStatusChange?: (status: Record<string, DebugBadge[]>) => void;
}) {
  // 有运行页草稿时对比草稿值（含未保存修改），否则对比已保存值。
  const valueOf = (field: SchemaField) => parameterDraft ? parameterDraft.values[field.name] ?? field.default : field.default;
  const [saveChoices, setSaveChoices] = useState<Record<string, Record<string, boolean>>>({});
  const [notices, setNotices] = useState<Record<string, string>>({});
  /** 只回存 direct 绑定、且回读与当前草稿不同的有限数值字段。 */
  const saveCandidates = (target: ControlTarget, channel: GeneratorChannelSnapshot) => comparisonRows(target, channel, fields).flatMap(row => {
    const readback = comparableOf(channel, row.field);
    if (readback === null || !Number.isFinite(readback)) return [];
    return row.entries
      .filter(({ parameter, binding }) => binding.relation === "direct" && differs(readback, valueOf(parameter), row.field))
      .map(({ parameter }) => ({ field: parameter, value: readback }));
  });
  function confirmSave(target: ControlTarget, channel: GeneratorChannelSnapshot) {
    if (!parameterDraft) return;
    const choice = saveChoices[target.mapping_key] ?? {};
    const patch: ParameterValues = Object.fromEntries(saveCandidates(target, channel).filter(item => choice[item.field.name]).map(item => [item.field.name, item.value]));
    if (Object.keys(patch).length) parameterDraft.apply(patch);
    setSaveChoices(current => { const next = { ...current }; delete next[target.mapping_key]; return next; });
    setNotices(current => ({ ...current, [target.mapping_key]: Object.keys(patch).length
      ? `已把 ${Object.keys(patch).length} 项写入运行页参数草稿，尚未保存；请到「运行」页保存参数。` : "未选择任何参数。" }));
  }
  const { hardwareBusy } = useJobActivity();
  const [catalog, setCatalog] = useState<ControlTargetCatalog | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [catalogEpoch, setCatalogEpoch] = useState(0);
  const [readings, setReadings] = useState<Record<string, Reading>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [reading, setReading] = useState("");

  const [activated, setActivated] = useState(active);
  if (active && !activated) setActivated(true);
  useEffect(() => {
    if (!activated) return;
    const controller = new AbortController();
    setCatalogError("");
    void api<ControlTargetCatalog>("/api/control-targets", { signal: controller.signal }).then(value => {
      if (!Array.isArray(value?.targets)) throw new Error("物理量目标格式不受支持");
      if (!controller.signal.aborted) setCatalog(value);
    }).catch(error => { if (!controller.signal.aborted) { setCatalog(null); setCatalogError(message(error)); } });
    return () => controller.abort();
  }, [activated, catalogEpoch]);

  const keys = new Set(mappingKeys);
  const targets = (catalog?.targets ?? []).filter(target => keys.has(target.mapping_key));

  // 状态覆盖所有已回读的用途（不止当前选中器件），切换器件后画布徽标仍保留。
  useEffect(() => {
    if (!onStatusChange) return;
    const status: Record<string, DebugBadge[]> = {};
    for (const target of catalog?.targets ?? []) {
      const result = readings[target.mapping_key];
      const snapshot = result ? generatorOf(result.snapshot) : null;
      const channel = snapshot ? channelOf(target, snapshot) : null;
      const badges: DebugBadge[] = [];
      if (channel) badges.push("read");
      if (channel && saveCandidates(target, channel).length) badges.push("diff");
      if (badges.length) status[target.mapping_key] = badges;
    }
    onStatusChange(status);
    // saveCandidates 只依赖下列状态；把这个每次渲染新建的函数列入依赖会重复汇报。
  },[catalog, readings, fields, parameterDraft?.values, onStatusChange]);

  function handleError(target: ControlTarget, error: unknown) {
    setErrors(current => ({ ...current, [target.mapping_key]: message(error) }));
    // 映射或设备库已变化：丢弃旧回读并重读目标清单，避免写入旧通道。
    if ((error as { code?: string }).code === "revision_conflict") {
      setReadings({}); setCatalog(null); setCatalogEpoch(value => value + 1);
    }
  }

  function handleWriteError(target: ControlTarget, error: unknown) {
    // 写入失败时仪器可能已部分改变（后端按规则可能已关闭输出）：作废旧回读，要求重新读取后再操作。
    setReadings(current => { const next = { ...current }; delete next[target.mapping_key]; return next; });
    handleError(target, error);
  }

  function handleSaved(response: ControlTargetResponse) {
    const key = response.target.mapping_key;
    setReadings(current => ({ ...current, [key]: { snapshot: response.snapshot, readAt: response.read_at } }));
    setErrors(current => ({ ...current, [key]: "" }));
  }

  async function read(target: ControlTarget) {
    if (!catalog) return;
    setReading(target.mapping_key); setErrors(current => ({ ...current, [target.mapping_key]: "" }));
    try {
      const response = await api<ControlTargetResponse>(`/api/control-targets/${encodeURIComponent(target.mapping_key)}/refresh`, {
        method: "POST", body: JSON.stringify({ device_library_revision: catalog.device_library_revision, physical_mapping_revision: catalog.physical_mapping_revision }),
      });
      setReadings(current => ({ ...current, [target.mapping_key]: { snapshot: response.snapshot, readAt: response.read_at } }));
    } catch (error) {
      handleError(target, error);
    } finally { setReading(""); }
  }

  return <>
    <section className="wiring-panel wiring-debug" aria-label="器件调试">
      <h2>仪器回读与写入</h2>
      {!deviceName ? <p className="wiring-help">在画布中选中器件后，可读取它在当前装置映射中的仪器当前值。</p>
        : !mappingKeys.length ? <p className="wiring-help">“{deviceName}”未关联用途键，无法定位到仪器通道。</p>
          : catalogError ? <p className="wiring-help">物理量目标读取失败：{catalogError}</p>
            : !catalog ? <p className="wiring-help">正在读取物理量目标…</p>
              : !targets.length ? <p className="wiring-help">当前装置的物理量映射中没有 {mappingKeys.join(" / ")}。</p>
                : <>
                  <p className="wiring-help">读取与写入都会短暂占用硬件锁；写入提交面板当前值，超出安全范围会被拒绝。</p>
                  {targets.map(target => {
                    const result = readings[target.mapping_key];
                    const snapshot = result ? generatorOf(result.snapshot) : null;
                    const channel = snapshot ? channelOf(target, snapshot) : null;
                    const rows = channel ? comparisonRows(target, channel, fields) : [];
                    return <article className="wiring-debug-target" key={target.mapping_key} aria-label={`调试目标：${target.label}`}>
                      <div className="wiring-section-head">
                        <h3>{target.label} <small>{target.mapping_key}</small></h3>
                        {isGenerator(target) && <button className="secondary" disabled={hardwareBusy || reading !== ""} onClick={() => void read(target)}>
                          {reading === target.mapping_key ? "读取中…" : result ? "重新读取" : "读取"}</button>}
                      </div>
                      <p className="wiring-help">{target.device_label} · {target.short_resource}{target.endpoint ? ` · CH${target.endpoint.index}` : ""}</p>
                      {!isGenerator(target) ? <p className="wiring-help">第一版只支持信号源通道，此类仪器请在「仪器控制」页查看。</p> : <>
                        {errors[target.mapping_key] && <p className="alert error" role="alert">{errors[target.mapping_key]}</p>}
                        {hardwareBusy && <p className="wiring-help">其他硬件任务运行中</p>}
                        {result && snapshot && channel ? <>
                          <fieldset className="wiring-debug-generator" disabled={hardwareBusy || reading !== ""}>
                            <GeneratorEditor snapshot={snapshot} channelNumbers={[channel.number]}
                              route={{ mappingKey: target.mapping_key, deviceLibraryRevision: catalog.device_library_revision, physicalMappingRevision: catalog.physical_mapping_revision }}
                              limitText={limitText(target)} onSaved={handleSaved} onError={error => handleWriteError(target, error)} />
                          </fieldset>
                          {rows.length > 0 && <GeneratorComparison target={target} channel={channel} rows={rows} valueOf={valueOf} />}
                          <p className="wiring-help">读取于 {new Date(result.readAt).toLocaleTimeString()}</p>
                        </> : result ? <p className="wiring-help">回读中未找到该用途对应的通道。</p> : <p className="wiring-help">尚未读取。</p>}
                        {parameterDraft && channel && <div className="wiring-debug-actions">
                          <button className="secondary" disabled={!saveCandidates(target, channel).length || saveChoices[target.mapping_key] !== undefined}
                            onClick={() => { setNotices(current => ({ ...current, [target.mapping_key]: "" })); setSaveChoices(current => ({ ...current, [target.mapping_key]: Object.fromEntries(saveCandidates(target, channel).map(item => [item.field.name, true])) })); }}>存为实验参数…</button>
                        </div>}
                        {parameterDraft && channel && saveChoices[target.mapping_key] && <fieldset className="wiring-debug-save" aria-label={`存为实验参数：${target.label}`}>
                          <legend>勾选要写入运行页草稿的参数（回读值 → 草稿）</legend>
                          {saveCandidates(target, channel).map(item => <label key={item.field.name}>
                            <input type="checkbox" checked={saveChoices[target.mapping_key][item.field.name] ?? false}
                              onChange={event => setSaveChoices(current => ({ ...current, [target.mapping_key]: { ...current[target.mapping_key], [item.field.name]: event.target.checked } }))} />
                            {item.field.label}：{String(valueOf(item.field))} → {item.value} {item.field.unit}
                          </label>)}
                          <div className="wiring-debug-actions"><button onClick={() => confirmSave(target, channel)}>写入草稿</button>
                            <button className="secondary" onClick={() => setSaveChoices(current => { const next = { ...current }; delete next[target.mapping_key]; return next; })}>取消</button></div>
                        </fieldset>}
                        {notices[target.mapping_key] && <p className="wiring-help" role="status">{notices[target.mapping_key]}</p>}
                      </>}
                    </article>;
                  })}
                </>}
    </section>
    <WiringParameterPanel deviceName={deviceName} mappingKeys={mappingKeys} fields={fields} loading={fieldsLoading} error={fieldsError} />
  </>;
}
