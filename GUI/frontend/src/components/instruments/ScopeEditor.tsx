import { useState } from "react";

import { api } from "../../api";
import type { ControlRoute, ControlTargetResponse, ScopeChannelSnapshot, ScopeSettings, ScopeSnapshot, ScopeTriggerSnapshot } from "../../types/api";
import { Field } from "../FormFields";
import { Status } from "../Status";

type ScopeEditorProps = {
  snapshot: ScopeSnapshot;
  route: ControlRoute;
  onSaved: (value: ControlTargetResponse) => void;
  onError: (reason: unknown) => void;
};

export function ScopeEditor({ snapshot, route, onSaved, onError }: ScopeEditorProps) {
  const [state, setState] = useState<ScopeSnapshot>(snapshot);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const set = <Key extends keyof ScopeSnapshot>(key: Key, value: ScopeSnapshot[Key]) => {
    setState((old) => ({ ...old, [key]: value }));
  };
  const triggerSet = <Key extends keyof ScopeTriggerSnapshot>(key: Key, value: ScopeTriggerSnapshot[Key]) => {
    setState((old) => ({ ...old, trigger: { ...old.trigger, [key]: value } }));
  };
  const channelSet = <Key extends keyof ScopeChannelSnapshot>(
    index: number,
    key: Key,
    value: ScopeChannelSnapshot[Key],
  ) => {
    setState((old) => ({
      ...old,
      channels: old.channels.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: value } : item),
    }));
  };
  const save = async () => {
    const settings: ScopeSettings = {
      sampling_rate: state.sampling_rate,
      memory_depth: state.memory_depth,
      acquire_type: state.acquire_type,
      acquire_type_param: state.acquire_type_param,
      timebase_scale: state.timebase_scale,
      timebase_delay: state.timebase_delay,
      channels: state.channels,
      trigger: state.trigger,
    };
    setSaving(true);
    setError("");
    try {
      const saved = await api<ControlTargetResponse>(`/api/control-targets/${encodeURIComponent(route.mappingKey)}/scope`, {
        method: "PUT",
        body: JSON.stringify({ device_library_revision: route.deviceLibraryRevision, physical_mapping_revision: route.physicalMappingRevision, settings }),
      });
      onSaved(saved);
    } catch (reason) {
      setError(String(reason));
      onError(reason);
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <div className="workspace-head">
        <div><Status>CONNECTED</Status><h2>{snapshot.label}</h2><p>{snapshot.idn}</p></div>
        <button disabled={saving} onClick={() => void save()}>{saving ? "应用中…" : "应用并回读"}</button>
      </div>
      {error && <div className="alert error">{error}</div>}
      <section className="panel">
        <div className="panel-head"><h3>采集与时基</h3></div>
        <div className="form-grid">
          <Field label="采样率 (Sa/s)" value={state.sampling_rate} onChange={(value) => set("sampling_rate", value)} />
          <Field label="存储深度" value={state.memory_depth} type="text" onChange={(value) => set("memory_depth", value)} />
          <label><span>采集模式</span><select value={state.acquire_type} onChange={(event) => set("acquire_type", event.target.value)}><option value="NORMal">Normal</option><option value="PEAK">Peak</option><option value="AVERage">Average</option><option value="ERES">ERES</option></select></label>
          <Field label="时基 (s/div)" value={state.timebase_scale} onChange={(value) => set("timebase_scale", value)} />
          <Field label="时基延迟 (s)" value={state.timebase_delay} onChange={(value) => set("timebase_delay", value)} />
        </div>
      </section>
      <section className="panel">
        <div className="panel-head"><h3>边沿触发</h3></div>
        <div className="form-grid">
          <label><span>模式</span><select value={state.trigger.mode} onChange={(event) => triggerSet("mode", event.target.value)}><option value="AUTO">Auto</option><option value="NORMal">Normal</option><option value="SINGle">Single</option></select></label>
          <label><span>来源</span><select value={state.trigger.source} onChange={(event) => triggerSet("source", event.target.value)}><option value="C1">C1</option><option value="C2">C2</option><option value="C3">C3</option><option value="C4">C4</option><option value="EXT">EXT</option></select></label>
          <label><span>斜率</span><select value={state.trigger.slope} onChange={(event) => triggerSet("slope", event.target.value)}><option value="RISing">Rising</option><option value="FALLing">Falling</option><option value="ALTernate">Alternate</option></select></label>
          <Field label="触发电平 (V)" value={state.trigger.level} onChange={(value) => triggerSet("level", value)} />
        </div>
      </section>
      <section className="scope-channels">
        {state.channels.map((channel, index) => (
          <div className="panel" key={channel.number}>
            <div className="panel-head">
              <h3>CH{channel.number}</h3>
              <label className="switch"><input type="checkbox" checked={channel.enabled} onChange={(event) => channelSet(index, "enabled", event.target.checked)} /><span /></label>
            </div>
            <Field label="量程 (V/div)" value={channel.scale} onChange={(value) => channelSet(index, "scale", value)} />
            <Field label="偏置 (V)" value={channel.offset} onChange={(value) => channelSet(index, "offset", value)} />
            <label><span>耦合</span><select value={channel.coupling} onChange={(event) => channelSet(index, "coupling", event.target.value)}><option value="DC">DC</option><option value="AC">AC</option><option value="GND">GND</option></select></label>
            <label><span>阻抗</span><select value={channel.impedance} onChange={(event) => channelSet(index, "impedance", event.target.value)}><option value="ONEMeg">1 MΩ</option><option value="FIFTy">50 Ω</option></select></label>
            <Field label="探头倍率" value={channel.probe} onChange={(value) => channelSet(index, "probe", value)} />
          </div>
        ))}
      </section>
    </>
  );
}
