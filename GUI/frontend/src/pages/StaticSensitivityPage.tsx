import { useEffect, useState } from "react";
import { api } from "../api";
import { PageHead } from "../components/PageHead";
import { GenericExperimentPage } from "./ExperimentPage";

type RunItem = {
  run_id: string;
  timestamp: string;
  run_tag: string;
  completion_status: string;
};

type Summary = RunItem & {
  parameters: Record<string, unknown>;
  analysis: Record<string, unknown>;
  artifacts: Record<string, string>;
};

const ID = "static-sensitivity";

export function StaticSensitivityPage() {
  const [tab, setTab] = useState<"run" | "history" | "architecture">("run");
  return (
    <>
      {tab === "run" ? <GenericExperimentPage /> : <PageHead eyebrow="EXPERIMENT · STATIC_SENSITIVITY" title="静磁场灵敏度" description="色散、噪声与灵敏度运行记录。" />}
      <div className="zaw-tab-strip" role="tablist" aria-label="静磁场灵敏度实验视图">
        {(["run", "history", "architecture"] as const).map((item) => (
          <button key={item} role="tab" aria-selected={tab === item} onClick={() => setTab(item)}>
            {item === "run" ? "运行" : item === "history" ? "历史" : "架构"}
          </button>
        ))}
      </div>
      {tab === "history" && <HistoryView />}
      {tab === "architecture" && <ArchitectureView />}
    </>
  );
}

function HistoryView() {
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [selected, setSelected] = useState<Summary>();
  const [error, setError] = useState("");

  useEffect(() => {
    api<{ runs: RunItem[] }>(`/api/experiments/${ID}/runs`)
      .then((value) => setRuns(value.runs))
      .catch((reason) => setError(String(reason)));
  }, []);

  const select = (run: RunItem) => {
    api<Summary>(`/api/experiments/${ID}/runs/${encodeURIComponent(run.run_id)}/summary`)
      .then(setSelected)
      .catch((reason) => setError(String(reason)));
  };

  return (
    <div className="zaw-history">
      <section className="zaw-run-list">
        <div className="zaw-run-list-head"><small>HISTORY</small><h3>历史运行</h3><span>{runs.length}</span></div>
        {error && <div className="alert error">{error}</div>}
        <ul>{runs.map((run) => <li key={run.run_id}><button className="zaw-run-card" onClick={() => select(run)}><strong>{run.run_id}</strong><span>{run.timestamp}</span><span>{run.completion_status}</span></button></li>)}</ul>
      </section>
      <section className="zaw-run-details">
        {!selected ? <p className="zaw-empty">从左侧选择一条历史运行查看详情。</p> : <>
          <h3>{selected.run_id}</h3>
          <p>完成状态：{selected.completion_status}</p>
          <div className="zaw-parameter-table"><table><tbody>{Object.entries(selected.parameters).map(([key, value]) => <tr key={key}><th>{key}</th><td>{String(value)}</td></tr>)}</tbody></table></div>
          <div className="zaw-run-details-figures">{Object.entries(selected.artifacts).filter(([name]) => name.endsWith(".png")).map(([name, url]) => <figure key={name}><figcaption>{name}</figcaption><img src={url} alt={name} loading="lazy" /></figure>)}</div>
        </>}
      </section>
    </div>
  );
}

function ArchitectureView() {
  const url = "/architecture/static-sensitivity.html";
  return <section className="mx-arch"><div className="mx-arch-head"><div><small>EXPERIMENT ARCHITECTURE</small><h2>采集流程架构</h2><p>展示 Pump 门控、Z 扫场、HF2 采集和离线分析的数据链路。</p></div><a className="architecture-open" href={url} target="_blank" rel="noreferrer">在新页面打开</a></div><div className="architecture-frame"><iframe src={url} title="静磁场灵敏度实验采集流程架构图" loading="lazy" sandbox="allow-scripts allow-same-origin allow-downloads allow-popups" /></div></section>;
}
