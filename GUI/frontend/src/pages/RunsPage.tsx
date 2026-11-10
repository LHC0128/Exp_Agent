import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import { JobView } from "../components/JobView";
import { PageHead } from "../components/PageHead";
import type {
  ExperimentDefinition, Job, RunsResponse, RunSummary, RunWiringResponse, WiringGraph,
} from "../types/api";

const PAGE_SIZE = 50;
const WIRING_NOTE = "记录快照不代表当时硬件实际状态或安全校验";

type WiringView =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; data: RunWiringResponse };

function runKey(run: RunSummary) {
  return `${run.experiment_id}:${run.id}`;
}

function shortRevision(revision: string | null | undefined) {
  return revision ? revision.slice(0, 12) : "未记录";
}

function graphCounts(graph: WiringGraph | null | undefined) {
  return graph ? `${graph.nodes.length} 台器件 / ${graph.edges.length} 条连线` : "未记录";
}

function RunWiringPanel({ run, view }: { run: RunSummary; view: WiringView }) {
  if (view.state === "loading") {
    return <div className="run-wiring"><p>读取记录接线中…</p></div>;
  }
  if (view.state === "error") {
    return <div className="run-wiring"><div className="alert error">{view.message}</div></div>;
  }
  const data = view.data;
  const target = data.targets?.[run.experiment_id];
  return (
    <div className="run-wiring">
      <p className="run-wiring-meta">
        记录修订：<code>{shortRevision(data.recorded_revision)}</code>
        {" · "}记录时间：{data.recorded_at ?? "未记录"}
      </p>
      {!data.available ? (
        <p>{data.reason ?? "该运行没有记录接线快照"}</p>
      ) : (
        <>
          <p className="run-wiring-status">
            {data.matches_current === true && "与当前接线一致"}
            {data.matches_current === false && "当前接线已变更"}
            {data.matches_current === null && "当前接线不可读"}
          </p>
          {data.current_error && (
            <p>无法读取当前接线，以下仅为运行记录快照。{data.current_error}</p>
          )}
          <p>现场图：{graphCounts(data.site)}</p>
          <p>目标图（{run.experiment_id}）：{graphCounts(target)}</p>
          {target && (
            <>
              <details>
                <summary>目标图器件（{target.nodes.length}）</summary>
                <ul>
                  {target.nodes.map((node) => (
                    <li key={node.device.id}>{node.device.name}（{node.device.id}）</li>
                  ))}
                </ul>
              </details>
              <details>
                <summary>目标图连线（{target.edges.length}）</summary>
                <ul>
                  {target.edges.map((edge) => (
                    <li key={edge.id}>
                      {edge.label}：{edge.source.device_id}/{edge.source.port_id}
                      {" → "}
                      {edge.target.device_id}/{edge.target.port_id}
                    </li>
                  ))}
                </ul>
              </details>
            </>
          )}
        </>
      )}
      <p className="run-wiring-note">{data.note || WIRING_NOTE}</p>
    </div>
  );
}

export function RunsPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [experiments, setExperiments] = useState<ExperimentDefinition[]>([]);
  const [filterId, setFilterId] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [job, setJob] = useState<Job>();
  const [error, setError] = useState("");
  const [analyzing, setAnalyzing] = useState("");
  const [wiringFor, setWiringFor] = useState("");
  const [wiring, setWiring] = useState<WiringView>();
  const wiringRequest = useRef<AbortController | null>(null);

  const load = async (reset: boolean, filter: string, disposed: () => boolean) => {
    setLoading(true);
    setError("");
    const params = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: String(reset ? 0 : runs.length),
    });
    if (filter) params.set("experiment_id", filter);
    try {
      const data = await api<RunsResponse>(`/api/runs?${params}`);
      if (disposed()) return;
      setRuns((old) => (reset ? data.runs : [...old, ...data.runs]));
      setTotal(data.total);
    } catch (reason) {
      if (!disposed()) setError(String(reason));
    } finally {
      if (!disposed()) setLoading(false);
    }
  };

  useEffect(() => {
    let live = true;
    void load(true, filterId, () => !live);
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterId]);

  useEffect(() => {
    api<ExperimentDefinition[]>("/api/experiments")
      .then(setExperiments)
      .catch((reason) => setError(String(reason)));
  }, []);

  useEffect(() => () => wiringRequest.current?.abort(), []);

  const visible = search.trim()
    ? runs.filter((run) => (
      `${run.id} ${run.experiment_title}`.toLowerCase().includes(search.trim().toLowerCase())
    ))
    : runs;
  const loadMore = () => void load(false, filterId, () => false);

  const closeWiring = () => {
    wiringRequest.current?.abort();
    wiringRequest.current = null;
    setWiringFor("");
    setWiring(undefined);
  };

  const showWiring = async (run: RunSummary) => {
    const key = runKey(run);
    if (wiringFor === key) {
      closeWiring();
      return;
    }
    wiringRequest.current?.abort();
    const controller = new AbortController();
    wiringRequest.current = controller;
    setWiringFor(key);
    setWiring({ state: "loading" });
    try {
      const data = await api<RunWiringResponse>(
        `/api/runs/${encodeURIComponent(run.experiment_id)}/${encodeURIComponent(run.id)}/wiring`,
        { signal: controller.signal },
      );
      if (controller.signal.aborted || wiringRequest.current !== controller) return;
      setWiring({ state: "ready", data });
    } catch (reason) {
      if (controller.signal.aborted || wiringRequest.current !== controller) return;
      setWiring({ state: "error", message: String(reason) });
    }
  };

  const analyze = async (run: RunSummary) => {
    setError("");
    setAnalyzing(run.id);
    try {
      setJob(await api<Job>(`/api/runs/${encodeURIComponent(run.id)}/analyze`, {
        method: "POST",
        body: JSON.stringify({ experiment_id: run.experiment_id }),
      }));
    } catch (reason) {
      setError(String(reason));
    } finally {
      setAnalyzing("");
    }
  };

  return (
    <>
      <PageHead
        eyebrow="RUN HISTORY"
        title="运行记录"
        description="浏览全部实验的数据目录，并按指定运行目录重新分析。"
      />
      {error && <div className="alert error">{error}</div>}
      <div className="runs-toolbar">
        <select value={filterId} aria-label="按实验筛选" onChange={(event) => setFilterId(event.target.value)}>
          <option value="">全部实验</option>
          {experiments.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
        </select>
        <input
          type="text"
          value={search}
          placeholder="按运行目录或实验名称搜索"
          aria-label="按运行目录或实验名称搜索"
          onChange={(event) => setSearch(event.target.value)}
        />
        <span className="runs-total">已显示 {visible.length} / 共 {total} 条</span>
      </div>
      <p className="runs-wiring-note">{WIRING_NOTE}</p>
      <div className="runs">
        {visible.length === 0 ? (
          <div className="empty"><h3>{search || filterId ? "没有匹配的运行记录" : "暂无运行记录"}</h3><p>完成一次实验后，结果会自动显示在这里。</p></div>
        ) : visible.map((run) => {
          const key = runKey(run);
          const expanded = wiringFor === key && wiring !== undefined;
          return (
            <article key={key}>
              <div className="run-summary">
                <div>
                  <small>{run.experiment_title}</small><h3>{run.id}</h3><p>{run.path}</p>
                  {run.can_analyze && <button className="secondary" disabled={analyzing === run.id} onClick={() => void analyze(run)}>{analyzing === run.id ? "分析中…" : "重新分析"}</button>}
                  <button className="secondary" onClick={() => void showWiring(run)}>
                    {expanded ? "收起" : "查看记录接线"}
                  </button>
                </div>
                <div className="artifacts">
                  {run.artifacts.map((name) => (
                    <a
                      target="_blank"
                      rel="noreferrer"
                      key={name}
                      href={`/api/runs/${run.experiment_id}/${encodeURIComponent(run.id)}/artifacts/${encodeURIComponent(name)}`}
                    >
                      {name}
                    </a>
                  ))}
                </div>
              </div>
              {expanded && <RunWiringPanel run={run} view={wiring} />}
            </article>
          );
        })}
      </div>
      {runs.length < total && !search.trim() && (
        <div className="runs-more">
          <button className="secondary" disabled={loading} onClick={loadMore}>{loading ? "加载中…" : "加载更多"}</button>
        </div>
      )}
      <JobView job={job} onUpdate={setJob} />
    </>
  );
}
