import { useEffect, useState } from "react";

import { api } from "../../api";
import type { CurrentSourceSettings, CurrentSourceSnapshot } from "../../types/api";
import { Field } from "../FormFields";
import { Status } from "../Status";

type CurrentSourceEditorProps = {
  snapshot: CurrentSourceSnapshot;
  onSaved: (value: CurrentSourceSnapshot) => void;
};

export function CurrentSourceEditor({ snapshot, onSaved }: CurrentSourceEditorProps) {
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
    )) {
      return;
    }

    const settings: CurrentSourceSettings = {
      output,
      confirm_output_enable: enablingOutput,
    };
    if (currentMa !== null) settings.current_ma = currentMa;

    setSaving(true);
    try {
      const saved = await api<CurrentSourceSnapshot>(
        `/api/devices/${snapshot.id}/current-source`,
        { method: "PUT", body: JSON.stringify({ settings }) },
      );
      onSaved(saved);
    } catch (reason) {
      setError(String(reason));
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
          <button disabled={saving} onClick={save}>
            {saving ? "应用中…" : "应用并回读"}
          </button>
        </div>
      </section>
    </>
  );
}
