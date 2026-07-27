import { useEffect, useState } from "react";

import { api } from "../../api";
import type {
  LaserEmissionSettings,
  LaserSettings,
  LaserSnapshot,
} from "../../types/api";
import { Field } from "../FormFields";
import { Status } from "../Status";

type LaserEditorProps = {
  snapshot: LaserSnapshot;
  onSaved: (value: LaserSnapshot) => void;
};

export function LaserEditor({ snapshot, onSaved }: LaserEditorProps) {
  const [currentMa, setCurrentMa] = useState(snapshot.current_set_ma);
  const [temperatureC, setTemperatureC] = useState(snapshot.temperature_set_c);
  const [pztVoltageV, setPztVoltageV] = useState(snapshot.pzt_voltage_v);
  const [scanAmplitudeVpp, setScanAmplitudeVpp] = useState(snapshot.scan_amplitude_vpp);
  const [scanEnabled, setScanEnabled] = useState(snapshot.scan_enabled);
  const [safetyAcknowledged, setSafetyAcknowledged] = useState(false);
  const [confirmationText, setConfirmationText] = useState("");
  const [saving, setSaving] = useState(false);
  const [emissionSaving, setEmissionSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setCurrentMa(snapshot.current_set_ma);
    setTemperatureC(snapshot.temperature_set_c);
    setPztVoltageV(snapshot.pzt_voltage_v);
    setScanAmplitudeVpp(snapshot.scan_amplitude_vpp);
    setScanEnabled(snapshot.scan_enabled);
    setSafetyAcknowledged(false);
    setConfirmationText("");
  }, [snapshot]);

  const saveLaser = async () => {
    setError("");
    const values = [currentMa, temperatureC, pztVoltageV, scanAmplitudeVpp];
    if (values.some((value) => !Number.isFinite(value))) {
      setError("电流、温度、PZT 电压和扫描幅度必须是有限数值。");
      return;
    }
    const envelopeLow = pztVoltageV - scanAmplitudeVpp / 2;
    const envelopeHigh = pztVoltageV + scanAmplitudeVpp / 2;
    if (
      envelopeLow < snapshot.min_pzt_voltage_v
      || envelopeHigh > snapshot.max_pzt_voltage_v
    ) {
      setError(
        `扫描包络 ${envelopeLow}～${envelopeHigh} V 超出 `
        + `${snapshot.min_pzt_voltage_v}～${snapshot.max_pzt_voltage_v} V。`,
      );
      return;
    }

    const settings: LaserSettings = {
      current_set_ma: currentMa,
      temperature_set_c: temperatureC,
      pzt_voltage_v: pztVoltageV,
      scan_amplitude_vpp: scanAmplitudeVpp,
      scan_enabled: scanEnabled,
    };
    setSaving(true);
    try {
      const saved = await api<LaserSnapshot>(
        `/api/devices/${snapshot.id}/laser`,
        { method: "PUT", body: JSON.stringify({ settings }) },
      );
      onSaved(saved);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setSaving(false);
    }
  };

  const setEmission = async (enabled: boolean) => {
    setError("");
    const settings: LaserEmissionSettings = { enabled };
    if (enabled) {
      if (!safetyAcknowledged) {
        setError("请先勾选 SI 模块、激光等级、联锁和警示回路安全确认。");
        return;
      }
      if (confirmationText !== snapshot.controller_serial) {
        setError(`请输入 ${snapshot.controller_serial} 以确认目标控制器。`);
        return;
      }
      if (!window.confirm(
        `高风险操作：确认远程开启 ${snapshot.controller_serial} 的 Emission？`,
      )) {
        return;
      }
      settings.safety_acknowledged = true;
      settings.confirm_emission_enable = true;
      settings.confirmation_text = confirmationText;
    }

    setEmissionSaving(true);
    try {
      const saved = await api<LaserSnapshot>(
        `/api/devices/${snapshot.id}/emission`,
        { method: "PUT", body: JSON.stringify({ settings }) },
      );
      onSaved(saved);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setEmissionSaving(false);
    }
  };

  const healthy = snapshot.system_health_code === 0
    && snapshot.laser_health_code === 0
    && !snapshot.interlock_open;
  return (
    <>
      <div className="workspace-head">
        <div>
          <Status tone={snapshot.emission ? "bad" : healthy ? "ok" : "work"}>
            {snapshot.emission ? "EMISSION ON" : "EMISSION OFF"}
          </Status>
          <h2>{snapshot.label}</h2>
          <p>
            {snapshot.system_type} · {snapshot.controller_serial}
            {" · "}Laser {snapshot.laser_head_serial}
            {" · "}FW {snapshot.firmware_version}
          </p>
        </div>
      </div>
      {error && <div className="alert error">{error}</div>}

      <section className="panel laser-panel">
        <div className="panel-head">
          <div><small>LASER 1 · PROBE</small><h3>激光与 PZT 参数</h3></div>
          <div className="scan-control">
            <small>{scanEnabled ? "SCAN ON" : "SCAN OFF"}</small>
            <label className="switch" title="扫描启停">
              <input
                type="checkbox"
                checked={scanEnabled}
                disabled={saving}
                onChange={(event) => setScanEnabled(event.target.checked)}
              />
              <span />
            </label>
          </div>
        </div>
        <div className="form-grid">
          <Field
            label="激光电流设定值 (mA)"
            value={currentMa}
            min={snapshot.min_current_ma}
            max={Math.min(snapshot.max_current_ma, snapshot.current_clip_ma)}
            step="any"
            disabled={saving}
            onChange={setCurrentMa}
          />
          <Field
            label="激光电流实际值 (mA，只读)"
            value={snapshot.current_actual_ma}
            disabled
            onChange={() => undefined}
          />
          <Field
            label="Current Clip (mA，只读)"
            value={snapshot.current_clip_ma}
            disabled
            onChange={() => undefined}
          />
          <Field
            label="激光温度设定值 (°C)"
            value={temperatureC}
            min={snapshot.min_temperature_c}
            max={snapshot.max_temperature_c}
            step="any"
            disabled={saving}
            onChange={setTemperatureC}
          />
          <Field
            label="激光温度实际值 (°C，只读)"
            value={snapshot.temperature_actual_c}
            disabled
            onChange={() => undefined}
          />
          <Field
            label="PZT 电压（Scan Offset，V）"
            value={pztVoltageV}
            min={snapshot.min_pzt_voltage_v}
            max={snapshot.max_pzt_voltage_v}
            step="any"
            disabled={saving}
            onChange={setPztVoltageV}
          />
          <Field
            label="PZT 实际电压 (V，只读)"
            value={snapshot.pzt_actual_v}
            disabled
            onChange={() => undefined}
          />
          <Field
            label="扫描幅度 (Vpp)"
            value={scanAmplitudeVpp}
            min={snapshot.min_scan_amplitude_vpp}
            max={snapshot.max_scan_amplitude_vpp}
            step="any"
            disabled={saving}
            onChange={setScanAmplitudeVpp}
          />
          <Field
            label="扫描频率 (Hz，只读)"
            value={snapshot.scan_frequency_hz}
            disabled
            onChange={() => undefined}
          />
        </div>
        <div className="alert">
          电流 {snapshot.min_current_ma}～{snapshot.max_current_ma} mA，
          温度 {snapshot.min_temperature_c}～{snapshot.max_temperature_c} °C，
          PZT {snapshot.min_pzt_voltage_v}～{snapshot.max_pzt_voltage_v} V，
          扫描幅度不超过 {snapshot.max_scan_amplitude_vpp} Vpp。
          扫描包络必须完整处于 PZT 安全范围内。
        </div>
        <div className="panel-actions">
          <button disabled={saving || emissionSaving} onClick={saveLaser}>
            {saving ? "应用中…" : "应用参数并回读"}
          </button>
        </div>
      </section>

      <section className="panel emission-panel danger-zone">
        <div className="panel-head">
          <div><small>HIGH RISK CONTROL</small><h3>Emission 控制</h3></div>
          <Status tone={healthy ? "ok" : "bad"}>
            {snapshot.system_health} · {snapshot.laser_health}
          </Status>
        </div>
        <div className="form-grid">
          <label>
            <span>Laser Enabled（只读）</span>
            <input value={snapshot.laser_enabled ? "ON" : "OFF"} disabled readOnly />
          </label>
          <label>
            <span>Interlock（只读）</span>
            <input value={snapshot.interlock_open ? "OPEN" : "CLOSED"} disabled readOnly />
          </label>
          <label>
            <span>远程 ON 门禁（只读）</span>
            <input
              value={snapshot.remote_emission_control_enabled ? "ENABLED" : "DISABLED"}
              disabled
              readOnly
            />
          </label>
        </div>

        {snapshot.emission ? (
          <div className="emission-actions">
            <div className="alert error">
              Emission 当前为 ON。远程 OFF 无需确认，但执行后仍会强制回读。
            </div>
            <button
              className="danger"
              disabled={emissionSaving || saving}
              onClick={() => void setEmission(false)}
            >
              {emissionSaving ? "关闭并回读中…" : "关闭 Emission"}
            </button>
          </div>
        ) : (
          <>
            {!snapshot.remote_emission_control_enabled && (
              <div className="alert">
                远程 Emission ON 默认被配置禁止。确认 SI 模块、激光等级、联锁和警示回路后，
                才能在 mapping.yaml 中开放。
              </div>
            )}
            <label className="safety-confirmation">
              <input
                type="checkbox"
                checked={safetyAcknowledged}
                disabled={!snapshot.remote_emission_control_enabled || emissionSaving}
                onChange={(event) => setSafetyAcknowledged(event.target.checked)}
              />
              <span>已现场确认 SI 模块、激光等级、联锁和警示回路满足远程开启条件</span>
            </label>
            <label>
              <span>输入 {snapshot.controller_serial} 确认目标控制器</span>
              <input
                value={confirmationText}
                disabled={!snapshot.remote_emission_control_enabled || emissionSaving}
                onChange={(event) => setConfirmationText(event.target.value)}
              />
            </label>
            <div className="panel-actions">
              <button
                className="danger"
                disabled={
                  !snapshot.remote_emission_control_enabled
                  || !safetyAcknowledged
                  || confirmationText !== snapshot.controller_serial
                  || emissionSaving
                  || saving
                }
                onClick={() => void setEmission(true)}
              >
                {emissionSaving ? "开启并回读中…" : "开启 Emission"}
              </button>
            </div>
          </>
        )}
      </section>
    </>
  );
}
