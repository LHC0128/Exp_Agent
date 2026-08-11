import { useEffect, useState } from "react";

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
};

type ChannelTab = "wave" | "mod" | "burst";
type NestedSettings = { mod: ModulationSnapshot; burst: BurstSnapshot };

export function GeneratorEditor({ snapshot, route, onSaved, onError }: GeneratorEditorProps) {
  const [channels, setChannels] = useState<GeneratorChannelSnapshot[]>(snapshot.channels);
  const [saving, setSaving] = useState<number>();
  const [tabs, setTabs] = useState<Record<number, ChannelTab>>({});
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

  const save = async (index: number) => {
    const channel = channels[index];
    let settings: GeneratorChannelSettings = {
      output: channel.output,
      shape: channel.shape,
      frequency: channel.frequency,
      amplitude: channel.amplitude,
      offset: channel.offset,
      phase: channel.phase,
      voltage_unit: channel.voltage_unit,
      load: channel.load,
      square_duty: channel.square_duty,
      ramp_symmetry: channel.ramp_symmetry,
      pulse_width: channel.pulse_width,
      pulse_delay: channel.pulse_delay,
      target_mode: channel.target_mode,
      mod: channel.mod,
      burst: channel.burst,
    };
    const original = snapshot.channels[index];
    if ((original.voltage_unit || "").toUpperCase() !== "VPP") {
      if (original.output && !channel.output) settings = { output: false };
      else {
        setError("当前单位不是 Vpp。请先切换为 Vpp 并重新读取；当前仅允许关闭输出。");
        return;
      }
    }
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

  return (
    <>
      <div className="workspace-head">
        <div><Status>{snapshot.reference_clock}</Status><h2>{snapshot.label}</h2><p>{snapshot.idn}</p></div>
      </div>
      {error && <div className="alert error">{error}</div>}
      {channels.map((channel, index) => {
        const tab = tabs[channel.number] || "wave";
        const nonVpp = (channel.voltage_unit || "").toUpperCase() !== "VPP";
        const disabled = channel.read_only || nonVpp;
        const mod = channel.mod;
        const burst = channel.burst;
        const modType = mod.type || "AM";
        return (
          <section className="panel generator-panel" key={channel.number}>
            <div className="panel-head">
              <div><small>CHANNEL {channel.number}</small><h3>{channel.label}</h3></div>
              <label className="switch">
                <input
                  type="checkbox"
                  checked={channel.output}
                  disabled={channel.read_only || (nonVpp && !channel.output)}
                  onChange={(event) => update(index, "output", event.target.checked)}
                />
                <span />
              </label>
            </div>
            {channel.read_only && <div className="alert">该通道按仓库约定只读，以下参数仅展示设备回读值。</div>}
            {nonVpp && <div className="alert error">当前单位为 {channel.voltage_unit || "未知"}。可以回读或关闭输出；其他写入前必须切换为 Vpp。 <button type="button" onClick={() => void switchToVpp()} disabled={saving !== undefined}>切换为 Vpp 并重新读取</button></div>}
            {channel.readback_errors.length > 0 && (
              <div className="alert error">
                部分参数不可用：{channel.readback_errors.map((item) => item.field).join("、")}
              </div>
            )}
            <div className="form-grid generator-basic-grid">
              <SelectField label="波形" value={channel.shape} disabled={disabled} onChange={(value) => update(index, "shape", value)}>
                <option value="SINusoid">Sine</option><option value="SQUare">Square</option><option value="RAMP">Ramp</option><option value="PULSe">Pulse</option><option value="DC">DC</option>
              </SelectField>
              {!channel.shape.startsWith("DC") && <><Field label="频率 (Hz)" value={channel.frequency} disabled={disabled} onChange={(value) => update(index, "frequency", value)} /><Field label="幅度 (Vpp)" value={channel.amplitude} disabled={disabled} onChange={(value) => update(index, "amplitude", value)} /></>}
              <Field label="偏置 (V)" value={channel.offset} disabled={disabled} onChange={(value) => update(index, "offset", value)} />
              {!channel.shape.startsWith("DC") && <Field label="相位 (deg)" value={channel.phase} disabled={disabled} onChange={(value) => update(index, "phase", value)} />}
            </div>
            <details className="generator-advanced">
              <summary>高级设置</summary>
            <div className="mode-tabs" role="tablist">
              <button className={tab === "wave" ? "active" : ""} onClick={() => setTabs((old) => ({ ...old, [channel.number]: "wave" }))}>波形细节</button>
              <button className={tab === "mod" ? "active" : ""} onClick={() => setTabs((old) => ({ ...old, [channel.number]: "mod" }))}>调制</button>
              <button className={tab === "burst" ? "active" : ""} onClick={() => setTabs((old) => ({ ...old, [channel.number]: "burst" }))}>Burst</button>
            </div>

            {tab === "wave" && (
              <div className="form-grid">
                <SelectField label="电压单位" value={channel.voltage_unit} disabled={disabled} onChange={(value) => update(index, "voltage_unit", value)}>
                  <option value="VPP">Vpp</option><option value="VRMS">Vrms</option><option value="DBM">dBm</option>
                </SelectField>
                <Field label="负载 (Ω / INF)" value={channel.load} type="text" disabled={disabled} onChange={(value) => update(index, "load", value)} />
                {channel.shape.startsWith("SQU") && <Field label="占空比 (%)" value={channel.square_duty} disabled={disabled} onChange={(value) => update(index, "square_duty", value)} />}
                {channel.shape.startsWith("RAMP") && <Field label="对称度 (%)" value={channel.ramp_symmetry} disabled={disabled} onChange={(value) => update(index, "ramp_symmetry", value)} />}
                {channel.shape.startsWith("PUL") && (
                  <>
                    <Field label="脉宽 (s)" value={channel.pulse_width} disabled={disabled} onChange={(value) => update(index, "pulse_width", value)} />
                    {snapshot.type === "DG4000" && <Field label="脉冲延迟 (s)" value={channel.pulse_delay} disabled={disabled} onChange={(value) => update(index, "pulse_delay", value)} />}
                  </>
                )}
              </div>
            )}

            {tab === "mod" && (
              <>
                <div className="mode-enable">
                  <div><strong>调制输出</strong><span>启用时自动关闭 Burst</span></div>
                  <label className="switch"><input type="checkbox" checked={mod.enabled} disabled={disabled} onChange={(event) => setMode(index, "mod", event.target.checked)} /><span /></label>
                </div>
                <div className="form-grid">
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
              </>
            )}

            {tab === "burst" && (
              <>
                <div className="mode-enable">
                  <div><strong>Burst 输出</strong><span>启用时自动关闭调制</span></div>
                  <label className="switch"><input type="checkbox" checked={burst.enabled} disabled={disabled} onChange={(event) => setMode(index, "burst", event.target.checked)} /><span /></label>
                </div>
                <div className="form-grid">
                  <SelectField label="Burst 模式" value={burst.mode || "TRIGgered"} disabled={disabled} onChange={(value) => updateNested(index, "burst", "mode", value)}>
                    <option value="TRIGgered">Triggered</option><option value="GATed">Gated</option><option value="INFinity">Infinity</option>
                  </SelectField>
                  {burst.mode !== "GATed" && (
                    <>
                      <Field label="循环数 / INF" value={burst.ncycles} type="text" disabled={disabled} onChange={(value) => updateNested(index, "burst", "ncycles", value)} />
                      <Field label="起始相位 (deg)" value={burst.phase} disabled={disabled} onChange={(value) => updateNested(index, "burst", "phase", value)} />
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
              </>
            )}
            </details>
            <div className="panel-actions">
              <button disabled={channel.read_only || saving === channel.number || (nonVpp && !(snapshot.channels[index].output && !channel.output))} onClick={() => save(index)}>
                {saving === channel.number ? "应用中…" : "应用并回读"}
              </button>
            </div>
          </section>
        );
      })}
    </>
  );
}
