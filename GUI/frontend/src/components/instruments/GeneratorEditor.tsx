import { useEffect, useState, type ReactNode } from "react";

import { api } from "../../api";
import type {
  BurstSnapshot,
  ControlRoute,
  ControlTargetResponse,
  GeneratorChannelSnapshot,
  GeneratorChannelSettings,
  GeneratorSnapshot,
  ModulationSnapshot,
  ModulationType,
} from "../../types/api";
import { Field, SelectField } from "../FormFields";
import { Status } from "../Status";

type GeneratorEditorProps = {
  snapshot: GeneratorSnapshot;
  route: ControlRoute;
  onSaved: (value: ControlTargetResponse) => void;
  onError: (reason: unknown) => void;
  /** 安全范围文字（如「-20 ~ 20 V」），显示在通道状态条。 */
  limitText?: string;
  /** 只渲染这些通道号；不传时渲染全部通道。 */
  channelNumbers?: number[];
};

type NestedSettings = { mod: ModulationSnapshot; burst: BurstSnapshot };
type RunMode = "continuous" | "mod" | "burst";

const WAVEFORMS: Array<[key: string, value: string, label: string]> = [
  ["SIN", "SINusoid", "Sine"], ["SQU", "SQUare", "Square"], ["RAMP", "RAMP", "Ramp"],
  ["PULS", "PULSe", "Pulse"], ["DC", "DC", "DC"],
];
const UNIT_LABELS: Record<string, string> = { VPP: "Vpp", VRMS: "Vrms", DBM: "dBm" };

function editableLoad(value: GeneratorChannelSnapshot["load"]) {
  return Number(value) >= 9e37 ? "INF" : value;
}

const isHighImpedance = (value: GeneratorChannelSnapshot["load"]) =>
  value === null ? false : String(value).trim().toUpperCase().startsWith("INF") || !Number.isFinite(Number(value)) || Number(value) <= 0 || Number(value) >= 9e37;

/** 仪器回读的波形名有长短两种写法（SIN / SINusoid），统一成按键键名。 */
function shapeKey(shape: string) {
  const upper = shape.toUpperCase();
  if (upper === "USER" || upper.startsWith("ARB")) return "ARB";
  return WAVEFORMS.find(([key]) => upper.startsWith(key))?.[0] ?? upper;
}

const runModeOf = (channel: GeneratorChannelSnapshot): RunMode =>
  channel.burst.enabled ? "burst" : channel.mod.enabled ? "mod" : "continuous";

function sameValue(a: unknown, b: unknown) {
  if (typeof a === "number" || typeof b === "number") {
    const x = Number(a); const y = Number(b);
    if (Number.isFinite(x) && Number.isFinite(y)) return Math.abs(x - y) <= Math.max(1e-12, 1e-9 * Math.abs(y));
  }
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

/** 与仪器回读不同的设置项；负载按高阻语义比较，波形按键名比较。 */
function changedKeys(edited: GeneratorChannelSnapshot, original: GeneratorChannelSnapshot) {
  const keys = ["output", "frequency", "amplitude", "offset", "phase", "voltage_unit", "square_duty",
    "ramp_symmetry", "pulse_width", "pulse_delay", "mod", "burst"] as const;
  const changed: string[] = keys.filter(key => !sameValue(edited[key], original[key]));
  if (shapeKey(edited.shape) !== shapeKey(original.shape)) changed.push("shape");
  if (!sameValue(editableLoad(edited.load), editableLoad(original.load))) changed.push("load");
  return changed;
}

const display = (value: unknown) => value === null || value === undefined || value === "" ? "—" : String(value);

/** 主参数格：输入框内显示单位，改动后高亮并在下方给出仪器回读值。 */
function ParamCell({ dirty, readback, unit, children }: { dirty: boolean; readback: unknown; unit: ReactNode; children: ReactNode }) {
  return <div className={`generator-param${dirty ? " dirty" : ""}`}>
    <div className="generator-param-input">{children}<span className="generator-unit">{unit}</span></div>
    {dirty && <small className="generator-readback">仪器 {display(readback)}</small>}
  </div>;
}

export function GeneratorEditor({ snapshot, route, onSaved, onError, limitText, channelNumbers }: GeneratorEditorProps) {
  const [channels, setChannels] = useState<GeneratorChannelSnapshot[]>(snapshot.channels);
  const [saving, setSaving] = useState<number>();
  const [error, setError] = useState("");

  useEffect(() => {
    setChannels(snapshot.channels);
  }, [snapshot]);

  const update = <Key extends keyof GeneratorChannelSnapshot>(
    index: number,
    key: Key,
    value: GeneratorChannelSnapshot[Key],
  ) => setChannels((items) => items.map((item, itemIndex) => (
    itemIndex === index ? { ...item, [key]: value } : item
  )));

  const updateNested = <Group extends keyof NestedSettings, Key extends keyof NestedSettings[Group]>(
    index: number,
    group: Group,
    key: Key,
    value: NestedSettings[Group][Key],
  ) => setChannels((items) => items.map((item, itemIndex) => (
    itemIndex === index
      ? { ...item, [group]: { ...item[group], [key]: value } }
      : item
  )));

  const setMode = (index: number, mode: "mod" | "burst", enabled: boolean) => {
    setChannels((items) => items.map((item, itemIndex) => {
      if (itemIndex !== index) return item;
      if (mode === "mod") {
        return {
          ...item,
          target_mode: "mod",
          mod: { ...item.mod, enabled, type: item.mod.type || "AM" },
          burst: { ...item.burst, enabled: enabled ? false : item.burst.enabled },
        };
      }
      return {
        ...item,
        target_mode: "burst",
        burst: { ...item.burst, enabled },
        mod: { ...item.mod, enabled: enabled ? false : item.mod.enabled },
      };
    }));
  };

  /** 三选一运行模式：连续 = 关闭调制与 Burst；调制与 Burst 互斥沿用 setMode。 */
  const setRunMode = (index: number, mode: RunMode) => {
    if (mode === "continuous") {
      setChannels((items) => items.map((item, itemIndex) => itemIndex !== index ? item : {
        ...item, mod: { ...item.mod, enabled: false }, burst: { ...item.burst, enabled: false },
      }));
    } else setMode(index, mode, true);
  };

  const revert = (index: number) => setChannels((items) => items.map((item, itemIndex) => (
    itemIndex === index ? snapshot.channels[index] : item
  )));

  const save = async (index: number) => {
    const channel = channels[index];
    let settings: GeneratorChannelSettings = {
      output: channel.output,
      shape: channel.shape,
      frequency: channel.frequency,
      amplitude: channel.amplitude,
      offset: channel.offset,
      phase: channel.phase,
      voltage_unit: channel.shape.toUpperCase() === "DC" ? "VPP" : channel.voltage_unit,
      load: editableLoad(channel.load),
      square_duty: channel.square_duty,
      ramp_symmetry: channel.ramp_symmetry,
      pulse_width: channel.pulse_width,
      pulse_delay: channel.pulse_delay,
      target_mode: channel.target_mode,
      mod: channel.mod,
      burst: channel.burst,
    };
    const original = snapshot.channels[index];
    if (route.mappingKey === "Z_magnetic_field" && ["USER", "ARBITRARY"].includes(channel.shape.toUpperCase())) {
      // 冻结任意波由功能模块配置；此处只允许关闭，避免重写幅度和 Burst。
      settings = { output: false };
    }
    if (original.output && !channel.output) settings = { output: false };
    setSaving(channel.number);
    setError("");
    try {
      const saved = await api<ControlTargetResponse>(
        `/api/control-targets/${encodeURIComponent(route.mappingKey)}/generator`,
        { method: "PUT", body: JSON.stringify({
          device_library_revision: route.deviceLibraryRevision,
          physical_mapping_revision: route.physicalMappingRevision,
          settings,
        }) },
      );
      onSaved(saved);
    } catch (reason) {
      setError(String(reason));
      onError(reason);
    } finally {
      setSaving(undefined);
    }
  };

  const switchToVpp = async () => {
    setSaving(snapshot.channels[0].number); setError("");
    try {
      onSaved(await api<ControlTargetResponse>(
        `/api/control-targets/${encodeURIComponent(route.mappingKey)}/generator`,
        { method: "PUT", body: JSON.stringify({
          device_library_revision: route.deviceLibraryRevision,
          physical_mapping_revision: route.physicalMappingRevision,
          settings: { voltage_unit: "VPP" },
        }) },
      ));
    } catch (reason) { setError(String(reason)); onError(reason); } finally { setSaving(undefined); }
  };

  // 过滤只影响渲染：索引始终对齐 snapshot.channels，写入、撤销与回读比较仍按原通道定位。
  const visible = channels.map((_, index) => index)
    .filter(index => !channelNumbers || channelNumbers.includes(channels[index].number));

  return (
    <>
      <div className="workspace-head">
        <div><Status>{snapshot.reference_clock}</Status><h2>{snapshot.label}</h2><p>{snapshot.idn}</p></div>
      </div>
      {error && <div className="alert error">{error}</div>}
      {visible.map(index => {
        const channel = channels[index];
        const original = snapshot.channels[index];
        const unknownUnit = !["VPP", "VRMS", "DBM"].includes((original.voltage_unit || "").toUpperCase());
        const key = shapeKey(channel.shape);
        const isDc = key === "DC";
        const isArbitrary = key === "ARB";
        const rmsSupported = ["SIN", "SQU", "RAMP"].includes(key);
        const highImpedance = isHighImpedance(channel.load);
        const frozenArbitrary = route.mappingKey === "Z_magnetic_field" && ["USER", "ARBITRARY"].includes(channel.shape.toUpperCase());
        const disabled = channel.read_only || unknownUnit || frozenArbitrary;
        const outputDisabled = channel.read_only || frozenArbitrary || (unknownUnit && !channel.output);
        const mod = channel.mod;
        const burst = channel.burst;
        const modType = mod.type || "AM";
        const runMode = runModeOf(channel);
        const changed = changedKeys(channel, original);
        const dirty = (name: string) => changed.includes(name);
        const unitLabel = UNIT_LABELS[(channel.voltage_unit || "").toUpperCase()] ?? (channel.voltage_unit || "");
        const readbackUnit = UNIT_LABELS[(original.voltage_unit || "").toUpperCase()] ?? (original.voltage_unit || "");
        const modeLabel = runMode === "burst" ? "Burst" : runMode === "mod" ? `调制 ${modType}` : "连续";
        return (
          <section className="panel generator-panel" key={channel.number} aria-label={`CH${channel.number} ${channel.label}`}>
            <div className="generator-head">
              <div className="generator-chips">
                <span className="generator-chip">CH{channel.number}</span>
                {limitText && <span className="generator-chip">安全范围 {limitText}</span>}
                <span className="generator-chip">负载 {highImpedance ? "高阻" : `${display(editableLoad(channel.load))} Ω`}</span>
                <span className={`generator-chip${runMode !== "continuous" ? " mode-active" : ""}`}>{modeLabel}</span>
              </div>
              <label className={`generator-output${channel.output ? " on" : ""}${dirty("output") ? " dirty" : ""}`}>
                <input type="checkbox" aria-label="输出" checked={channel.output} disabled={outputDisabled}
                  onChange={(event) => update(index, "output", event.target.checked)} />
                <span>输出 {channel.output ? "ON" : "OFF"}</span>
              </label>
            </div>
            {channel.read_only && <div className="alert">该通道按仓库约定只读，以下参数仅展示设备回读值。</div>}
            {frozenArbitrary && <div className="alert">当前为任意波。预览、加载、相位及触发设置请使用 Z 任意波控制；此处保存只关闭 Z 输出。选择其他波形后可重新配置。</div>}
            {unknownUnit && <div className="alert error">当前幅度单位未知。可以回读或关闭输出；其他写入前请恢复为 Vpp。 <button type="button" onClick={() => void switchToVpp()} disabled={channel.read_only || saving !== undefined}>切换为 Vpp 并重新读取</button></div>}
            {channel.readback_errors.length > 0 && (
              <div className="alert error">
                部分参数不可用：{channel.readback_errors.map((item) => item.field).join("、")}
              </div>
            )}

            <div className="generator-section-label">波形</div>
            <div className={`generator-segment generator-waveforms${dirty("shape") ? " dirty" : ""}`} role="radiogroup" aria-label="波形">
              {[...WAVEFORMS, ...(isArbitrary || shapeKey(original.shape) === "ARB" ? [["ARB", shapeKey(original.shape) === "ARB" ? original.shape : channel.shape, "Arb"] as [string, string, string]] : [])].map(([optionKey, value, label]) => (
                <button key={optionKey} type="button" role="radio" aria-checked={key === optionKey} className={key === optionKey ? "active" : ""}
                  disabled={channel.read_only || unknownUnit || (optionKey === "ARB" && !isArbitrary && shapeKey(original.shape) !== "ARB")}
                  onClick={() => {
                    if (key === optionKey) return;
                    update(index, "shape", value);
                    if (!["SIN", "SQU", "RAMP"].includes(optionKey)) update(index, "voltage_unit", "VPP");
                  }}>{label}</button>
              ))}
            </div>

            <div className="generator-params">
              {isDc ? (
                <ParamCell dirty={dirty("offset")} readback={`${display(original.offset)} V`} unit="V">
                  <Field label="电平" value={channel.offset} disabled={disabled} onChange={(value) => update(index, "offset", value)} />
                </ParamCell>
              ) : <>
                <ParamCell dirty={dirty("frequency")} readback={`${display(original.frequency)} Hz`} unit="Hz">
                  <Field label="频率" value={channel.frequency} disabled={disabled} onChange={(value) => update(index, "frequency", value)} />
                </ParamCell>
                <ParamCell dirty={dirty("amplitude") || dirty("voltage_unit")} readback={`${display(original.amplitude)} ${readbackUnit}`}
                  unit={<select aria-label="单位" value={channel.voltage_unit ?? ""} disabled={disabled} onChange={(event) => update(index, "voltage_unit", event.target.value)}>
                    {unknownUnit && <option value={channel.voltage_unit ?? ""}>{unitLabel || "?"}</option>}
                    <option value="VPP">Vpp</option>
                    <option value="VRMS" disabled={!rmsSupported}>Vrms</option>
                    <option value="DBM" disabled={!rmsSupported || highImpedance}>dBm</option>
                  </select>}>
                  <Field label="幅度" value={channel.amplitude} disabled={disabled} onChange={(value) => update(index, "amplitude", value)} />
                </ParamCell>
                <ParamCell dirty={dirty("offset")} readback={`${display(original.offset)} V`} unit="V">
                  <Field label="偏置" value={channel.offset} disabled={disabled} onChange={(value) => update(index, "offset", value)} />
                </ParamCell>
                <ParamCell dirty={dirty("phase")} readback={`${display(original.phase)}°`} unit="°">
                  <Field label="相位" value={channel.phase} disabled={disabled} onChange={(value) => update(index, "phase", value)} />
                </ParamCell>
              </>}
            </div>

            {!isDc && <>
              <div className="generator-section-label">运行模式</div>
              <div className={`generator-segment${dirty("mod") || dirty("burst") ? " dirty" : ""}`} role="radiogroup" aria-label="运行模式">
                {([["continuous", "连续"], ["mod", "调制"], ["burst", "Burst"]] as Array<[RunMode, string]>).map(([mode, label]) => (
                  <button key={mode} type="button" role="radio" aria-checked={runMode === mode} className={runMode === mode ? "active" : ""}
                    disabled={disabled} onClick={() => setRunMode(index, mode)}>{label}</button>
                ))}
              </div>
            </>}

            {!isDc && runMode === "mod" && (
              <div className="form-grid generator-mode-fields" aria-label="调制参数">
                <SelectField label="调制类型" value={modType} disabled={disabled} onChange={(value) => updateNested(index, "mod", "type", value as ModulationType)}>
                  <option value="AM">AM</option><option value="FM">FM</option><option value="PM">PM</option><option value="FSKey">FSK</option><option value="PWM">PWM</option>
                </SelectField>
                <SelectField label="调制源" value={mod.source || "INTernal"} disabled={disabled} onChange={(value) => updateNested(index, "mod", "source", value)}>
                  <option value="INTernal">Internal</option><option value="EXTernal">External</option>
                </SelectField>
                {modType === "AM" && <Field label="调制深度 (%)" value={mod.am_depth} disabled={disabled} onChange={(value) => updateNested(index, "mod", "am_depth", value)} />}
                {modType === "FM" && <Field label="频率偏差 (Hz)" value={mod.fm_deviation} disabled={disabled} onChange={(value) => updateNested(index, "mod", "fm_deviation", value)} />}
                {modType === "PM" && <Field label="相位偏差 (deg)" value={mod.pm_deviation} disabled={disabled} onChange={(value) => updateNested(index, "mod", "pm_deviation", value)} />}
                {modType === "FSKey" && (
                  <>
                    <Field label="跳跃频率 (Hz)" value={mod.fsk_frequency} disabled={disabled} onChange={(value) => updateNested(index, "mod", "fsk_frequency", value)} />
                    <Field label="内部速率 (Hz)" value={mod.fsk_rate} disabled={disabled || mod.source === "EXTernal"} onChange={(value) => updateNested(index, "mod", "fsk_rate", value)} />
                    <SelectField label="极性" value={mod.fsk_polarity || (snapshot.type === "DG900" ? "POSitive" : "NORMal")} disabled={disabled} onChange={(value) => updateNested(index, "mod", "fsk_polarity", value)}>
                      {snapshot.type === "DG900" ? <><option value="POSitive">Positive</option><option value="NEGative">Negative</option></> : <><option value="NORMal">Normal</option><option value="INVerted">Inverted</option></>}
                    </SelectField>
                  </>
                )}
                {modType === "PWM" && <Field label="占空比偏差 (%)" value={mod.pwm_duty_deviation} disabled={disabled} onChange={(value) => updateNested(index, "mod", "pwm_duty_deviation", value)} />}
                {mod.source !== "EXTernal" && modType !== "FSKey" && (
                  <>
                    <Field label="内部调制频率 (Hz)" value={mod.internal_frequency} disabled={disabled} onChange={(value) => updateNested(index, "mod", "internal_frequency", value)} />
                    <SelectField label="内部调制波形" value={mod.internal_function || "SINusoid"} disabled={disabled} onChange={(value) => updateNested(index, "mod", "internal_function", value)}>
                      <option value="SINusoid">Sine</option><option value="SQUare">Square</option><option value="TRIangle">Triangle</option>
                      <option value="RAMP">Ramp Up</option><option value="NRAMp">Ramp Down</option><option value="NOISe">Noise</option>
                    </SelectField>
                  </>
                )}
              </div>
            )}

            {!isDc && runMode === "burst" && (
              <div className="form-grid generator-mode-fields" aria-label="Burst 参数">
                <SelectField label="Burst 模式" value={burst.mode || "TRIGgered"} disabled={disabled} onChange={(value) => updateNested(index, "burst", "mode", value)}>
                  <option value="TRIGgered">Triggered</option><option value="GATed">Gated</option><option value="INFinity">Infinity</option>
                </SelectField>
                {burst.mode !== "GATed" && (
                  <>
                    <Field label="起始相位 (deg)" value={burst.phase} disabled={disabled} onChange={(value) => updateNested(index, "burst", "phase", value)} />
                    <Field label="循环数 / INF" value={burst.ncycles} type="text" disabled={disabled} onChange={(value) => updateNested(index, "burst", "ncycles", value)} />
                    <Field label="Burst 周期 (s)" value={burst.period} disabled={disabled} onChange={(value) => updateNested(index, "burst", "period", value)} />
                    <Field label="触发延迟 (s)" value={burst.delay} disabled={disabled} onChange={(value) => updateNested(index, "burst", "delay", value)} />
                  </>
                )}
                <SelectField label="触发源" value={burst.trigger_source || (snapshot.type === "DG900" ? "IMMediate" : "INTernal")} disabled={disabled} onChange={(value) => updateNested(index, "burst", "trigger_source", value)}>
                  {snapshot.type === "DG900" ? <><option value="IMMediate">Internal</option><option value="EXTernal">External</option><option value="BUS">Manual/Bus</option><option value="TIMer">Timer</option></> : <><option value="INTernal">Internal</option><option value="EXTernal">External</option><option value="MANual">Manual</option></>}
                </SelectField>
                {burst.trigger_source === "EXTernal" && (
                  <SelectField label="触发边沿" value={burst.trigger_slope || "POSitive"} disabled={disabled} onChange={(value) => updateNested(index, "burst", "trigger_slope", value)}>
                    <option value="POSitive">Rising</option><option value="NEGative">Falling</option>
                  </SelectField>
                )}
              </div>
            )}

            <details className="generator-advanced">
              <summary>更多：负载与波形细节</summary>
              <div className="form-grid">
                <Field label="负载 (Ω / INF)" value={editableLoad(channel.load)} type="text" disabled={disabled} onChange={(value) => update(index, "load", value)} />
                {key === "SQU" && <Field label="占空比 (%)" value={channel.square_duty} disabled={disabled} onChange={(value) => update(index, "square_duty", value)} />}
                {key === "RAMP" && <Field label="对称度 (%)" value={channel.ramp_symmetry} disabled={disabled} onChange={(value) => update(index, "ramp_symmetry", value)} />}
                {key === "PULS" && (
                  <>
                    <Field label="脉宽 (s)" value={channel.pulse_width} disabled={disabled} onChange={(value) => update(index, "pulse_width", value)} />
                    {snapshot.type === "DG4000" && <Field label="脉冲延迟 (s)" value={channel.pulse_delay} disabled={disabled} onChange={(value) => update(index, "pulse_delay", value)} />}
                  </>
                )}
              </div>
            </details>

            <div className="generator-footer">
              <span className={changed.length ? "generator-pending" : "generator-synced"} role="status">
                {changed.length ? `${changed.length} 项未应用` : "与仪器一致"}
              </span>
              <div className="generator-footer-actions">
                <button type="button" className="secondary" disabled={!changed.length || saving !== undefined} onClick={() => revert(index)}>撤销</button>
                <button disabled={channel.read_only || saving !== undefined || (unknownUnit && !(original.output && !channel.output))} onClick={() => save(index)}>
                  {saving === channel.number ? "应用中…" : frozenArbitrary ? "关闭 Z 输出" : "应用并回读"}
                </button>
              </div>
            </div>
          </section>
        );
      })}
    </>
  );
}
