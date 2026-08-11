import type { Hf2Snapshot } from "../../types/api";
import { Field } from "../FormFields";
import { Status } from "../Status";

export function Hf2Editor({ snapshot }: { snapshot: Hf2Snapshot }) {
  return <>
    <div className="workspace-head"><div><Status>{snapshot.enabled ? "ENABLED" : "DISABLED"}</Status><h2>{snapshot.label}</h2><p>HF2 Demod {snapshot.demod_idx}</p></div></div>
    <section className="panel"><div className="panel-head"><div><small>READ ONLY</small><h3>Demod {snapshot.demod_idx}</h3></div></div>
      <div className="form-grid">
        {([[
          "X", snapshot.x], ["Y", snapshot.y], ["R", snapshot.r], ["相位 (deg)", snapshot.phase],
          ["频率 (Hz)", snapshot.frequency], ["采样率 (Sa/s)", snapshot.sample_rate],
          ["时间常数 (s)", snapshot.time_constant], ["阶数", snapshot.order],
          ["谐波", snapshot.harmonic], ["相移 (deg)", snapshot.phase_shift],
        ] as Array<[string, number]>).map(([label, value]) => <Field key={label} label={`${label}（只读）`} value={value} disabled onChange={() => undefined} />)}
      </div>
      <div className="alert">该物理量为只读。自动校相仍在功能模块中执行。</div>
    </section>
  </>;
}
