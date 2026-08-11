import { useEffect, useState } from "react";

import { api } from "../../api";
import type {
  ControlRoute,
  ControlTargetResponse,
  CurrentSourceSettings,
  CurrentSourceSnapshot,
  GS200Snapshot,
  Keithley6221Snapshot,
  Keithley6221WaveformSettings,
} from "../../types/api";
import { Field, SelectField } from "../FormFields";
import { Status } from "../Status";

type CurrentSourceEditorProps = {
  snapshot: CurrentSourceSnapshot;
  route: ControlRoute;
  onSaved: (value: ControlTargetResponse) => void;
  onError: (reason: unknown) => void;
};

type EditorCallbacks = Omit<CurrentSourceEditorProps, "snapshot">;

async function updateCurrentSource(
  route: ControlRoute,
  settings: CurrentSourceSettings,
): Promise<ControlTargetResponse> {
  return api<ControlTargetResponse>(
    `/api/control-targets/${encodeURIComponent(route.mappingKey)}/current-source`,
    {
      method: "PUT",
      body: JSON.stringify({
        device_library_revision: route.deviceLibraryRevision,
        physical_mapping_revision: route.physicalMappingRevision,
        settings,
      }),
    },
  );
}

function GS200Editor({
  snapshot,
  route,
  onSaved,
  onError,
}: EditorCallbacks & { snapshot: GS200Snapshot }) {
  const [currentMa, setCurrentMa] = useState<number | null>(snapshot.current_ma);
  const [output, setOutput] = useState(snapshot.output);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setCurrentMa(snapshot.current_ma);
    setOutput(snapshot.output);
  }, [snapshot]);

  const save = async () => {
    setError("");
    const enablingOutput = !snapshot.output && output;
    if (output && currentMa === null) {
      setError("开启输出前必须输入有效的电流设定值。");
      return;
    }
    if (enablingOutput && !window.confirm(
      `确认开启主磁场输出？\n即将输出 ${currentMa} mA。`,
    )) return;

    const settings: CurrentSourceSettings = {
      output,
      confirm_output_enable: enablingOutput,
    };
    if (currentMa !== null) settings.current_ma = currentMa;

    setSaving(true);
    try {
      onSaved(await updateCurrentSource(route, settings));
    } catch (reason) {
      setError(String(reason));
      onError(reason);
    } finally {
      setSaving(false);
    }
  };

  const currentMode = snapshot.source_function.toUpperCase().startsWith("CURR");
  return (
    <>
      <div className="workspace-head">
        <div>
          <Status>{snapshot.output ? "OUTPUT ON" : "OUTPUT OFF"}</Status>
          <h2>{snapshot.label}</h2>
          <p>{snapshot.idn}</p>
        </div>
      </div>
      {error && <div className="alert error">{error}</div>}
      {!currentMode && (
        <div className="alert error">
          当前设备不是电流模式。输入电流并应用后将切换到电流模式；开启输出仍需二次确认。
        </div>
      )}
      <section className="panel current-source-panel">
        <div className="panel-head">
          <div><small>MAIN MAGNETIC FIELD</small><h3>主磁场电流输出</h3></div>
          <label className="switch">
            <input
              type="checkbox"
              checked={output}
              disabled={saving}
              onChange={(event) => setOutput(event.target.checked)}
            />
            <span />
          </label>
        </div>
        <div className="form-grid">
          <Field
            label="电流设定值 (mA)"
            value={currentMa}
            min={snapshot.min_current_ma}
            max={snapshot.max_current_ma}
            step="any"
            disabled={saving}
            onChange={setCurrentMa}
          />
          <label><span>源模式（只读）</span><input value={snapshot.source_function} disabled readOnly /></label>
          <Field label="电流量程 (mA，只读)" value={snapshot.current_range_ma} disabled onChange={() => undefined} />
          <Field label="限压 (V，只读)" value={snapshot.voltage_limit_v} disabled onChange={() => undefined} />
          <Field label="限流 (mA，只读)" value={snapshot.current_limit_ma} disabled onChange={() => undefined} />
        </div>
        <div className="alert">
          允许范围：{snapshot.min_current_ma}～{snapshot.max_current_ma} mA。界面显示的是设备设定值，不是独立测量值。
        </div>
        <div className="panel-actions">
          <button disabled={saving} onClick={() => void save()}>
            {saving ? "应用中…" : "应用并回读"}
          </button>
        </div>
      </section>
    </>
  );
}

type SourceView = "dc" | "waveform";
type DurationMode = "TIME" | "CYCLES" | "INFINITE";

function numberLabel(value: number): string {
  return value.toLocaleString(undefined, { maximumSignificantDigits: 10 });
}

function pointRange(points: number[]): [number, number] {
  let low = points[0];
  let high = points[0];
  for (const value of points) {
    if (value < low) low = value;
    if (value > high) high = value;
  }
  return [low, high];
}

function Keithley6221Editor({
  snapshot,
  route,
  onSaved,
  onError,
}: EditorCallbacks & { snapshot: Keithley6221Snapshot }) {
  const [view, setView] = useState<SourceView>("dc");
  const [currentMa, setCurrentMa] = useState(snapshot.current_ma);
  const [output, setOutput] = useState(snapshot.output);
  const [autorange, setAutorange] = useState(snapshot.autorange);
  const [currentRangeMa, setCurrentRangeMa] = useState(snapshot.current_range_ma);
  const [complianceV, setComplianceV] = useState(snapshot.compliance_v);
  const [analogFilter, setAnalogFilter] = useState(snapshot.analog_filter);
  const [outputResponse, setOutputResponse] = useState<"FAST" | "SLOW">(snapshot.output_response);
  const [shape, setShape] = useState(snapshot.waveform.shape);
  const [frequencyHz, setFrequencyHz] = useState(snapshot.waveform.frequency_hz);
  const [amplitudeMa, setAmplitudeMa] = useState(snapshot.waveform.amplitude_peak_ma);
  const [offsetMa, setOffsetMa] = useState(snapshot.waveform.offset_ma);
  const [dutyCycle, setDutyCycle] = useState(snapshot.waveform.duty_cycle_percent);
  const [ranging, setRanging] = useState<"BEST" | "FIXED">(snapshot.waveform.ranging);
  const initialDurationMode: DurationMode = snapshot.waveform.duration_mode === "MIXED"
    ? "INFINITE"
    : snapshot.waveform.duration_mode;
  const [durationMode, setDurationMode] = useState<DurationMode>(initialDurationMode);
  const [durationValue, setDurationValue] = useState(snapshot.waveform.duration_value ?? 1);
  const [arbitraryPoints, setArbitraryPoints] = useState<number[] | null>(null);
  const [arbitraryFile, setArbitraryFile] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setCurrentMa(snapshot.current_ma);
    setOutput(snapshot.output);
    setAutorange(snapshot.autorange);
    setCurrentRangeMa(snapshot.current_range_ma);
    setComplianceV(snapshot.compliance_v);
    setAnalogFilter(snapshot.analog_filter);
    setOutputResponse(snapshot.output_response);
    setShape(snapshot.waveform.shape);
    setFrequencyHz(snapshot.waveform.frequency_hz);
    setAmplitudeMa(snapshot.waveform.amplitude_peak_ma);
    setOffsetMa(snapshot.waveform.offset_ma);
    setDutyCycle(snapshot.waveform.duty_cycle_percent);
    setRanging(snapshot.waveform.ranging);
    setDurationMode(
      snapshot.waveform.duration_mode === "MIXED"
        ? "INFINITE"
        : snapshot.waveform.duration_mode,
    );
    setDurationValue(snapshot.waveform.duration_value ?? 1);
  }, [snapshot]);

  const send = async (settings: CurrentSourceSettings) => {
    setSaving(true);
    setError("");
    try {
      onSaved(await updateCurrentSource(route, settings));
    } catch (reason) {
      setError(String(reason));
      onError(reason);
    } finally {
      setSaving(false);
    }
  };

  const saveDc = async () => {
    const rangeChanged = !autorange && currentRangeMa !== snapshot.current_range_ma;
    const disruptive = autorange !== snapshot.autorange
      || rangeChanged
      || outputResponse !== snapshot.output_response;
    const requiresConfirmation = output && (!snapshot.output || disruptive);
    if (requiresConfirmation && !window.confirm(
      `确认开启 Keithley 6221 直流输出？\n即将输出 ${numberLabel(currentMa)} mA。`,
    )) return;

    const settings: CurrentSourceSettings = {
      current_ma: currentMa,
      output,
      confirm_output_enable: requiresConfirmation,
    };
    if (autorange !== snapshot.autorange) settings.autorange = autorange;
    if (rangeChanged) settings.current_range_ma = currentRangeMa;
    if (complianceV !== snapshot.compliance_v) settings.compliance_v = complianceV;
    if (analogFilter !== snapshot.analog_filter) settings.analog_filter = analogFilter;
    if (outputResponse !== snapshot.output_response) settings.output_response = outputResponse;
    await send(settings);
  };

  const parseArbitraryFile = async (file: File | undefined) => {
    if (!file) return;
    setError("");
    try {
      if (!/\.(csv|txt)$/i.test(file.name)) {
        throw new Error("任意波文件必须是 .csv 或 .txt");
      }
      const lines = (await file.text())
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);
      if (lines[0]?.toLowerCase() === "value") lines.shift();
      const values = lines.map((line, index) => {
        if (/[,;\t]/.test(line)) {
          throw new Error(`第 ${index + 1} 行不是单列数据`);
        }
        const value = Number(line);
        if (!Number.isFinite(value)) {
          throw new Error(`第 ${index + 1} 行不是有限数值`);
        }
        if (value < -1 || value > 1) {
          throw new Error(`第 ${index + 1} 行超出 [-1, 1]`);
        }
        return value;
      });
      if (values.length < 2 || values.length > 65535) {
        throw new Error("任意波点数必须在 2 到 65,535 之间");
      }
      setArbitraryPoints(values);
      setArbitraryFile(file.name);
    } catch (reason) {
      setArbitraryPoints(null);
      setArbitraryFile("");
      setError(String(reason));
    }
  };

  const waveformEnvelope = () => {
    const [lowFactor, highFactor] = shape === "ARB" && arbitraryPoints
      ? pointRange(arbitraryPoints)
      : [-1, 1];
    return [
      offsetMa + amplitudeMa * lowFactor,
      offsetMa + amplitudeMa * highFactor,
    ];
  };

  const submitWaveform = async (
    action: Keithley6221WaveformSettings["action"],
  ) => {
    if (action === "abort") {
      await send({ waveform: { action: "abort" } });
      return;
    }
    if (shape === "ARB" && !arbitraryPoints) {
      setError("配置 ARB 前必须导入有效的任意波文件");
      return;
    }
    const [low, high] = waveformEnvelope();
    const starting = action === "configure_and_start";
    if (starting && !window.confirm(
      `确认启动 Keithley 6221 波形？\n实际电流包络：${numberLabel(low)} ～ ${numberLabel(high)} mA。`,
    )) return;

    const waveform: Keithley6221WaveformSettings = {
      action,
      shape,
      frequency_hz: frequencyHz,
      amplitude_peak_ma: amplitudeMa,
      offset_ma: offsetMa,
      duty_cycle_percent: dutyCycle,
      ranging,
      duration_mode: durationMode,
      confirm_start: starting,
    };
    if (durationMode !== "INFINITE") waveform.duration_value = durationValue;
    if (shape === "ARB" && arbitraryPoints) waveform.arbitrary_points = arbitraryPoints;
    await send({ waveform });
  };

  const [arbMin, arbMax] = arbitraryPoints
    ? pointRange(arbitraryPoints)
    : [null, null];
  return (
    <>
      <div className="workspace-head">
        <div>
          <Status>{snapshot.output ? "OUTPUT ON" : "OUTPUT OFF"}</Status>
          <h2>{snapshot.label}</h2>
          <p>{snapshot.idn}</p>
        </div>
      </div>
      {error && <div className="alert error">{error}</div>}
      <div className="mode-tabs current-source-tabs" role="tablist">
        <button className={view === "dc" ? "active" : ""} onClick={() => setView("dc")}>DC</button>
        <button className={view === "waveform" ? "active" : ""} onClick={() => setView("waveform")}>波形</button>
      </div>

      {view === "dc" ? (
        <section className="panel current-source-panel">
          <div className="panel-head">
            <div><small>DIRECT CURRENT</small><h3>直流输出</h3></div>
            <label className="switch" title="输出">
              <input type="checkbox" checked={output} disabled={saving} onChange={(event) => setOutput(event.target.checked)} />
              <span />
            </label>
          </div>
          <div className="form-grid">
            <Field label="电流 (mA)" value={currentMa} min={snapshot.min_current_ma} max={snapshot.max_current_ma} step="any" disabled={saving} onChange={setCurrentMa} />
            <div className="binary-field"><span>自动量程</span><label className="switch"><input type="checkbox" checked={autorange} disabled={saving} onChange={(event) => setAutorange(event.target.checked)} /><span /></label></div>
            <Field label="固定量程 (mA)" value={currentRangeMa} min={2e-9} max={105} step="any" disabled={saving || autorange} onChange={setCurrentRangeMa} />
            <Field label="Compliance (V)" value={complianceV} min={0.1} max={105} step="any" disabled={saving} onChange={setComplianceV} />
            <div className="binary-field"><span>模拟滤波</span><label className="switch"><input type="checkbox" checked={analogFilter} disabled={saving} onChange={(event) => setAnalogFilter(event.target.checked)} /><span /></label></div>
            <SelectField label="输出响应" value={outputResponse} disabled={saving} onChange={(value) => setOutputResponse(value as "FAST" | "SLOW")}><option value="FAST">FAST</option><option value="SLOW">SLOW</option></SelectField>
          </div>
          <div className="panel-actions"><button disabled={saving} onClick={() => void saveDc()}>{saving ? "应用中…" : "应用并回读"}</button></div>
        </section>
      ) : (
        <section className="panel current-source-panel waveform-panel">
          <div className="panel-head"><div><small>INTERNAL WAVEFORM</small><h3>内部波形发生器</h3></div></div>
          <div className="form-grid">
            <SelectField label="波形" value={shape} disabled={saving} onChange={(value) => setShape(value as typeof shape)}><option value="SIN">正弦</option><option value="SQU">方波</option><option value="RAMP">Ramp</option><option value="ARB">任意波</option></SelectField>
            <Field label="峰值幅度 (mA)" value={amplitudeMa} min={2e-9} max={105} step="any" disabled={saving} onChange={setAmplitudeMa} />
            <Field label="频率 (Hz)" value={frequencyHz} min={0.001} max={100000} step="any" disabled={saving} onChange={setFrequencyHz} />
            <Field label="偏置 (mA)" value={offsetMa} min={-105} max={105} step="any" disabled={saving} onChange={setOffsetMa} />
            {shape === "SQU" && <Field label="占空比 (%)" value={dutyCycle} min={0} max={100} step="any" disabled={saving} onChange={setDutyCycle} />}
            <SelectField label="量程方式" value={ranging} disabled={saving} onChange={(value) => setRanging(value as "BEST" | "FIXED")}><option value="BEST">BEST</option><option value="FIXED">FIXED</option></SelectField>
            <SelectField label="时长" value={durationMode} disabled={saving} onChange={(value) => setDurationMode(value as DurationMode)}><option value="INFINITE">无限</option><option value="TIME">时间</option><option value="CYCLES">周期数</option></SelectField>
            {durationMode !== "INFINITE" && <Field label={durationMode === "TIME" ? "时间 (s)" : "周期数"} value={durationValue} min={durationMode === "TIME" ? 1e-7 : 0.001} max={durationMode === "TIME" ? 999999.999 : 99999999900} step="any" disabled={saving} onChange={setDurationValue} />}
          </div>
          {shape === "ARB" && (
            <div className="arb-file-row">
              <label><span>任意波文件</span><input type="file" accept=".csv,.txt,text/csv,text/plain" disabled={saving} onChange={(event) => void parseArbitraryFile(event.target.files?.[0])} /></label>
              <div className="arb-stats">
                <strong>{arbitraryFile || "未导入"}</strong>
                <span>点数 {arbitraryPoints?.length ?? snapshot.waveform.arbitrary_point_count}</span>
                <span>Min {arbMin === null ? "—" : numberLabel(arbMin)}</span>
                <span>Max {arbMax === null ? "—" : numberLabel(arbMax)}</span>
              </div>
            </div>
          )}
          <div className="panel-actions waveform-actions">
            <button className="secondary" disabled={saving} onClick={() => void submitWaveform("abort")}>停止</button>
            <button className="secondary" disabled={saving} onClick={() => void submitWaveform("configure")}>配置</button>
            <button disabled={saving} onClick={() => void submitWaveform("configure_and_start")}>{saving ? "执行中…" : "启动"}</button>
          </div>
        </section>
      )}
    </>
  );
}

export function CurrentSourceEditor(props: CurrentSourceEditorProps) {
  if (props.snapshot.type === "GS200") {
    return <GS200Editor {...props} snapshot={props.snapshot} />;
  }
  return <Keithley6221Editor {...props} snapshot={props.snapshot} />;
}
