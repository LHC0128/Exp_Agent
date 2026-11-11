import { useEffect, useState } from "react";
import { api } from "../../api";
import { useJobActivity } from "../JobActivity";
import type { ControlTarget, ControlTargetCatalog, ControlTargetResponse, DeviceSnapshot, GeneratorChannelSnapshot, ParameterValue, ParameterValues, SchemaField } from "../../types/api";
import type { ParameterDraft } from "../ExperimentViewTabs";
import { WiringParameterPanel } from "./WiringParameterPanel";

type Reading = { snapshot: DeviceSnapshot; readAt: string };
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const number = (value: number | null, unit: string) => value === null ? "—" : `${value} ${unit}`.trim();
// 与后端 normalize_output_load 一致：INF/INFINITY/INFINITE 与 ≥9e37 的查询标记都表示高阻。
const loadText = (value: GeneratorChannelSnapshot["load"]) => value === null ? "—"
  : Number(value) >= 9e37 || ["INF", "INFINITY", "INFINITE"].includes(String(value).trim().toUpperCase()) ? "高阻" : `${value} Ω`;
const isGenerator = (target: ControlTarget) => target.kind === "generator";

function readOnlyReason(target: ControlTarget, channel: GeneratorChannelSnapshot | null, hardwareBusy: boolean) {
  if (!channel) return "请先读取仪器通道";
  if (hardwareBusy) return "其他硬件任务运行中";
  if (target.read_only || channel.read_only) return "该目标或通道只读，请在仪器控制页操作";
  if (channel.shape.toUpperCase() === "DC") return "DC 通道第一版只读";
  if (/^(USER|ARB)/i.test(channel.shape)) return "任意波通道只读";
  if ((channel.voltage_unit || "").toUpperCase() !== "VPP") return "幅度单位不是 Vpp，只读";
  if (channel.burst.enabled || channel.mod.enabled) return "Burst/调制通道第一版只读，请在仪器控制页操作";
  return "";
}

/** 画布节点调试徽标：已回读、有未写入的输入、回读与实验参数不同。 */
export type DebugBadge = "read" | "pending" | "diff";
export const debugBadgeLabel: Record<DebugBadge, string> = { read: "已回读", pending: "有未写入值", diff: "≠ 实验参数" };

type SettingField = "frequency" | "amplitude" | "offset" | "phase" | "square_duty";
type PendingSettings = Partial<Record<SettingField, string>>;
type NumericSettings = Partial<Record<SettingField, number>>;
const settingLabels: Record<SettingField, string> = { frequency: "频率", amplitude: "幅度", offset: "偏置", phase: "相位", square_duty: "占空比" };

function differs(readback: number | null, saved: ParameterValue | undefined, field: SettingField) {
  if (readback === null || typeof saved !== "number" || !Number.isFinite(readback) || !Number.isFinite(saved)) return false;
  const normalize = (value: number) => field === "phase" ? ((value % 360) + 360) % 360 : value;
  const a = normalize(readback); const b = normalize(saved);
  return Math.abs(a - b) > Math.max(1e-9, 1e-6 * Math.abs(b));
}

function GeneratorReadback({ target, channel, fields, valueOf, pending, disabled, onChange }: {
  target: ControlTarget;
  channel: GeneratorChannelSnapshot;
  fields: SchemaField[] | null;
  valueOf: (field: SchemaField) => ParameterValue | undefined;
  pending: PendingSettings;
  disabled: boolean;
  onChange: (field: SettingField, value: string) => void;
}) {
  const unit = (channel.voltage_unit || "").toUpperCase() === "VPP" ? "Vpp" : channel.voltage_unit || "";
  const isDc = channel.shape.toUpperCase() === "DC";
  // DC 与「仪器控制」页一致：只有偏置是有效电平，频率/幅度/相位不显示。
  const rows: Array<[string, string, SettingField?]> = isDc ? [
    ["输出", channel.output ? "开启" : "关闭"],
    ["波形", "DC"],
    ["电平（偏置）", number(channel.offset, "V")],
    ["负载", loadText(channel.load)],
  ] : [
    ["输出", channel.output ? "开启" : "关闭"],
    ["波形", channel.shape || "—"],
    ["频率", number(channel.frequency, "Hz"), "frequency"],
    ["幅度", number(channel.amplitude, unit), "amplitude"],
    ["偏置", number(channel.offset, "V"), "offset"],
    ["相位", number(channel.phase, "°"), "phase"],
    ...(/^SQU/i.test(channel.shape) ? [["占空比", number(channel.square_duty, "%"), "square_duty"] as [string, string, SettingField]] : []),
    ...(/^PULS/i.test(channel.shape) ? [["脉宽", number(channel.pulse_width, "s")] as [string, string]] : []),
    ["负载", loadText(channel.load)],
    ...(channel.burst.enabled ? [["Burst", "开启"] as [string, string]] : []),
    ...(channel.mod.enabled ? [["调制", channel.mod.type || "开启"] as [string, string]] : []),
  ];
  return <>
    <div className="wiring-debug-table-wrap"><table className="wiring-debug-table" aria-label={`${target.label}仪器回读与实验参数`}>
      <thead><tr><th scope="col">设置项</th><th scope="col">仪器回读</th><th scope="col">实验参数</th>{!isDc && <th scope="col">待写入</th>}</tr></thead>
      <tbody>{rows.map(([label, value, key]) => {
        const matches = key ? (fields ?? []).filter(field => field.setting?.role === target.mapping_key && field.setting.field === key) : [];
        const different = key !== undefined && matches.some(field => differs(channel[key], valueOf(field), key));
        return <tr key={label} className={different ? "wiring-debug-diff" : undefined}>
          <th scope="row">{label}</th><td>{value}</td>
          <td>{different && <span aria-label="与仪器回读不同">≠</span>}{matches.length ? matches.map(field => <div key={field.name}>
            {field.label}：{(value => value === null || value === undefined ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value))(valueOf(field))} {field.unit}
          </div>) : "—"}</td>
          {!isDc && <td>{key ? <input type="text" inputMode="decimal" aria-label={`待写入：${target.label} ${label}`}
            placeholder={channel[key] === null ? "—" : String(channel[key])} value={pending[key] ?? ""} disabled={disabled}
            onChange={event => onChange(key, event.target.value)} /> : "—"}</td>}
        </tr>;
      })}</tbody>
    </table></div>
    {channel.readback_errors.length > 0 && <p className="wiring-help">部分参数未回读：{channel.readback_errors.map(item => item.field).join("、")}</p>}
  </>;
}

/** 目标接线图「调试」页：按器件用途键回读仪器，并对比本实验已保存参数。 */
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
  /** 只回存设置项级绑定、且回读与当前草稿不同的有限数值字段。 */
  const saveCandidates = (target: ControlTarget, channel: GeneratorChannelSnapshot) => (fields ?? []).flatMap(field => {
    const setting = field.setting;
    if (!setting || setting.role !== target.mapping_key || !(setting.field in settingLabels)) return [];
    const key = setting.field as SettingField;
    if (key === "square_duty" && !/^SQU/i.test(channel.shape)) return [];
    const readback = channel[key];
    return readback !== null && Number.isFinite(readback) && differs(readback, valueOf(field), key) ? [{ field, value: readback }] : [];
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
  const [writing, setWriting] = useState("");
  const [pending, setPending] = useState<Record<string, PendingSettings>>({});

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

  // 状态覆盖所有已回读或有输入的用途（不止当前选中器件），切换器件后画布徽标仍保留。
  useEffect(() => {
    if (!onStatusChange) return;
    const status: Record<string, DebugBadge[]> = {};
    for (const target of catalog?.targets ?? []) {
      const result = readings[target.mapping_key];
      const channel = result ? channelOf(target, result.snapshot) : null;
      const badges: DebugBadge[] = [];
      if (channel) badges.push("read");
      if (Object.values(pending[target.mapping_key] ?? {}).some(value => (value ?? "").trim())) badges.push("pending");
      if (channel && saveCandidates(target, channel).length) badges.push("diff");
      if (badges.length) status[target.mapping_key] = badges;
    }
    onStatusChange(status);
    // saveCandidates/channelOf 只依赖下列状态；把这两个每次渲染新建的函数列入依赖会重复汇报。
  },[catalog, readings, pending, fields, parameterDraft?.values, onStatusChange]);

  function handleError(target: ControlTarget, error: unknown) {
    setErrors(current => ({ ...current, [target.mapping_key]: message(error) }));
    // 映射或设备库已变化：丢弃旧回读与输入并重读目标清单，避免写入旧通道。
    if ((error as { code?: string }).code === "revision_conflict") {
      setReadings({}); setPending({}); setCatalog(null); setCatalogEpoch(value => value + 1);
    }
  }

  async function read(target: ControlTarget) {
    if (!catalog) return;
    setReading(target.mapping_key); setErrors(current => ({ ...current, [target.mapping_key]: "" }));
    try {
      const response = await api<ControlTargetResponse>(`/api/control-targets/${encodeURIComponent(target.mapping_key)}/refresh`, {
        method: "POST", body: JSON.stringify({ device_library_revision: catalog.device_library_revision, physical_mapping_revision: catalog.physical_mapping_revision }),
      });
      setReadings(current => ({ ...current, [target.mapping_key]: { snapshot: response.snapshot, readAt: response.read_at } }));
      // 重新回读替换输入基准，清空草稿，避免提交已隐藏的设置项。
      setPending(current => ({ ...current, [target.mapping_key]: {} }));
    } catch (error) {
      handleError(target, error);
    } finally { setReading(""); }
  }

  async function writeSettings(target: ControlTarget, settings: NumericSettings | { output: boolean }) {
    if (!catalog) return;
    setWriting(target.mapping_key); setErrors(current => ({ ...current, [target.mapping_key]: "" }));
    try {
      const response = await api<ControlTargetResponse>(`/api/control-targets/${encodeURIComponent(target.mapping_key)}/generator`, {
        method: "PUT", body: JSON.stringify({ device_library_revision: catalog.device_library_revision, physical_mapping_revision: catalog.physical_mapping_revision, settings }),
      });
      setReadings(current => ({ ...current, [target.mapping_key]: { snapshot: response.snapshot, readAt: response.read_at } }));
      setPending(current => ({ ...current, [target.mapping_key]: {} }));
    } catch (error) {
      // 写入失败时仪器可能已部分改变（后端按规则可能已关闭输出）：作废旧回读，要求重新读取后再操作。
      setReadings(current => { const next = { ...current }; delete next[target.mapping_key]; return next; });
      handleError(target, error);
    } finally { setWriting(""); }
  }

  function write(target: ControlTarget) {
    const settings: NumericSettings = {};
    // 输入框是数值校验边界；后端负责各用途的仪器安全限值校验。
    for (const field of Object.keys(settingLabels) as SettingField[]) {
      const text = (pending[target.mapping_key]?.[field] ?? "").trim();
      if (!text) continue;
      const value = Number(text);
      if (!Number.isFinite(value)) {
        setErrors(current => ({ ...current, [target.mapping_key]: `${settingLabels[field]}请输入有限数值。` }));
        return;
      }
      settings[field] = value;
    }
    if (Object.keys(settings).length) void writeSettings(target, settings);
  }

  function switchOutput(target: ControlTarget, output: boolean) {
    if (window.confirm(`确认${output ? "打开" : "关闭"}「${target.label}」输出？`)) void writeSettings(target, { output });
  }

  const channelOf = (target: ControlTarget, snapshot: DeviceSnapshot) => snapshot.type === "DG4000" || snapshot.type === "DG900"
    ? snapshot.channels.find(channel => channel.mapping_keys.includes(target.mapping_key) || channel.mapping_key === target.mapping_key) ?? null
    : null;

  return <>
    <section className="wiring-panel wiring-debug" aria-label="器件调试">
      <h2>仪器回读</h2>
      {!deviceName ? <p className="wiring-help">在画布中选中器件后，可读取它在当前装置映射中的仪器当前值。</p>
        : !mappingKeys.length ? <p className="wiring-help">“{deviceName}”未关联用途键，无法定位到仪器通道。</p>
          : catalogError ? <p className="wiring-help">物理量目标读取失败：{catalogError}</p>
            : !catalog ? <p className="wiring-help">正在读取物理量目标…</p>
              : !targets.length ? <p className="wiring-help">当前装置的物理量映射中没有 {mappingKeys.join(" / ")}。</p>
                : <>
                  <p className="wiring-help">读取与写入都会短暂占用硬件锁；写入只改填写的项，不改变输出开关、波形、Burst 与调制。</p>
                  {targets.map(target => {
                    const result = readings[target.mapping_key];
                    const channel = result ? channelOf(target, result.snapshot) : null;
                    const reason = readOnlyReason(target, channel, hardwareBusy);
                    const disabled = reason !== "" || reading !== "" || writing !== "";
                    const outputDisabled = !channel || hardwareBusy || reading !== "" || writing !== "" || target.read_only || channel.read_only || (!channel.output && disabled);
                    return <article className="wiring-debug-target" key={target.mapping_key} aria-label={`调试目标：${target.label}`}>
                      <div className="wiring-section-head">
                        <h3>{target.label} <small>{target.mapping_key}</small></h3>
                        {isGenerator(target) && <button className="secondary" disabled={hardwareBusy || reading !== "" || writing !== ""} onClick={() => void read(target)}>
                          {reading === target.mapping_key ? "读取中…" : result ? "重新读取" : "读取"}</button>}
                      </div>
                      <p className="wiring-help">{target.device_label} · {target.short_resource}{target.endpoint ? ` · CH${target.endpoint.index}` : ""}</p>
                      {!isGenerator(target) ? <p className="wiring-help">第一版只支持信号源通道，此类仪器请在「仪器控制」页查看。</p> : <>
                        {errors[target.mapping_key] && <p className="alert error" role="alert">{errors[target.mapping_key]}</p>}
                        {result ? channel ? <><GeneratorReadback target={target} channel={channel} fields={fields} valueOf={valueOf}
                          pending={pending[target.mapping_key] ?? {}} disabled={disabled}
                          onChange={(field, value) => setPending(current => ({ ...current, [target.mapping_key]: { ...current[target.mapping_key], [field]: value } }))} />
                          <p className="wiring-help">读取于 {new Date(result.readAt).toLocaleTimeString()}</p></>
                          : <p className="wiring-help">回读中未找到该用途对应的通道。</p> : <p className="wiring-help">尚未读取。</p>}
                        {reason && <p className="wiring-help">{reason}</p>}
                        <div className="wiring-debug-actions">
                          <button disabled={disabled} onClick={() => write(target)}>{writing === target.mapping_key ? "写入中…" : "写入仪器"}</button>
                          <button className="secondary" disabled={outputDisabled} onClick={() => switchOutput(target, !channel?.output)}>{channel?.output ? "关闭输出" : "打开输出"}</button>
                          {parameterDraft && channel && <button className="secondary" disabled={!saveCandidates(target, channel).length || saveChoices[target.mapping_key] !== undefined}
                            onClick={() => { setNotices(current => ({ ...current, [target.mapping_key]: "" })); setSaveChoices(current => ({ ...current, [target.mapping_key]: Object.fromEntries(saveCandidates(target, channel).map(item => [item.field.name, true])) })); }}>存为实验参数…</button>}
                        </div>
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
