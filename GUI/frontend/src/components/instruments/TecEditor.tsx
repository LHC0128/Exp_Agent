import { useEffect, useState } from "react";

import { api } from "../../api";
import type { ControlRoute, ControlTargetResponse, TecSnapshot } from "../../types/api";
import { Field } from "../FormFields";
import { Status } from "../Status";

export function TecEditor({ snapshot, route, onSaved, onError }: {
  snapshot: TecSnapshot;
  route: ControlRoute;
  onSaved: (value: ControlTargetResponse) => void;
  onError: (reason: unknown) => void;
}) {
  const [target, setTarget] = useState(snapshot.target_temperature_c);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => setTarget(snapshot.target_temperature_c), [snapshot]);

  const save = async () => {
    setSaving(true); setError("");
    try {
      onSaved(await api<ControlTargetResponse>(
        `/api/control-targets/${encodeURIComponent(route.mappingKey)}/tec`,
        { method: "PUT", body: JSON.stringify({
          device_library_revision: route.deviceLibraryRevision,
          physical_mapping_revision: route.physicalMappingRevision,
          settings: { target_temperature_c: target },
        }) },
      ));
    } catch (reason) { setError(String(reason)); onError(reason); } finally { setSaving(false); }
  };

  return <>
    <div className="workspace-head"><div><Status>{snapshot.enabled ? "ENABLED" : "DISABLED"}</Status><h2>{snapshot.label}</h2><p>TEC103 CH{snapshot.channel}</p></div></div>
    {error && <div className="alert error">{error}</div>}
    <section className="panel"><div className="panel-head"><div><small>TEMPERATURE</small><h3>Cell 温度</h3></div></div>
      <div className="form-grid">
        <Field label="目标温度 (°C)" value={target} min={snapshot.min_temperature_c} max={snapshot.max_temperature_c} disabled={saving} onChange={setTarget} />
        <Field label="实际温度 (°C，只读)" value={snapshot.actual_temperature_c} disabled onChange={() => undefined} />
        <Field label="输出模式（只读）" value={snapshot.output_mode} disabled onChange={() => undefined} />
        <Field label="传感器电阻 (kΩ，只读)" value={snapshot.resistance_kohm} disabled onChange={() => undefined} />
      </div>
      <div className="alert">允许范围：{snapshot.min_temperature_c}～{snapshot.max_temperature_c} °C。使能、输出模式和 PID 仅展示，不提供写入。</div>
      <div className="panel-actions"><button disabled={saving} onClick={save}>{saving ? "应用中…" : "应用并回读"}</button></div>
    </section>
  </>;
}
