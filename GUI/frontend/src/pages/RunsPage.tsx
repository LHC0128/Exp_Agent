import { useEffect, useState } from "react";

import { api } from "../api";
import { JobView } from "../components/JobView";
import { PageHead } from "../components/PageHead";
import type { ExperimentDefinition, Job, RunsResponse, RunSummary } from "../types/api";

const PAGE_SIZE = 50;

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

  const visible = search.trim()
    ? runs.filter((run) => (
      `${run.id} ${run.experiment_title}`.toLowerCase().includes(search.trim().toLowerCase())
    ))
    : runs;
  const loadMore = () => void load(false, filterId, () => false);

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
      <div className="runs">
        {visible.length === 0 ? (
          <div className="empty"><h3>{search || filterId ? "没有匹配的运行记录" : "暂无运行记录"}</h3><p>完成一次实验后，结果会自动显示在这里。</p></div>
        ) : visible.map((run) => (
          <article key={`${run.experiment_id}:${run.id}`}>
            <div>
              <small>{run.experiment_title}</small><h3>{run.id}</h3><p>{run.path}</p>
              {run.can_analyze && <button className="secondary" disabled={analyzing === run.id} onClick={() => void analyze(run)}>{analyzing === run.id ? "分析中…" : "重新分析"}</button>}
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
          </article>
        ))}
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
