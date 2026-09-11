import { useEffect, useState } from "react";
import { api } from "../../api";
import type { RunSummary } from "../../types/api";
import { ZAW_CLOSED_LOOP_EXPERIMENT_ID } from "./images";

const PAGE_SIZE = 50;

type RunListProps = {
  selectedRunId: string | null;
  onSelect: (run: RunSummary) => void;
};

export function RunList({ selectedRunId, onSelect }: RunListProps) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [hasMore, setHasMore] = useState(false);

  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setError("");
    const params = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: "0",
      experiment_id: ZAW_CLOSED_LOOP_EXPERIMENT_ID,
    });
    api<{ total: number; runs: RunSummary[] }>(`/api/runs?${params}`)
      .then((data) => {
        if (disposed) return;
        setRuns(data.runs);
        setTotal(data.total);
        setHasMore(data.runs.length < data.total);
      })
      .catch((reason) => !disposed && setError(String(reason)))
      .finally(() => !disposed && setLoading(false));
    return () => {
      disposed = true;
    };
  }, []);

  const loadMore = async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(runs.length),
        experiment_id: ZAW_CLOSED_LOOP_EXPERIMENT_ID,
      });
      const data = await api<{ total: number; runs: RunSummary[] }>(`/api/runs?${params}`);
      setRuns((old) => [...old, ...data.runs]);
      setTotal(data.total);
      setHasMore(runs.length + data.runs.length < data.total);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="zaw-run-list">
      <div className="zaw-run-list-head">
        <small>HISTORY</small>
        <h3>历史运行</h3>
        <span>{runs.length} / {total}</span>
      </div>
      {error && <div className="alert error">{error}</div>}
      {runs.length === 0 && !loading && (
        <p className="zaw-empty">尚无历史运行</p>
      )}
      <ul>
        {runs.map((run) => {
          const tone = run.id === selectedRunId ? "selected" : "";
          return (
            <li key={run.id}>
              <button
                className={`zaw-run-card ${tone}`}
                onClick={() => onSelect(run)}
                title={run.path}
              >
                <strong>{run.id}</strong>
                <span>{new Date(run.modified_at * 1000).toLocaleString()}</span>
                <span className="zaw-run-card-artifacts">{run.artifacts.length} 个文件</span>
              </button>
            </li>
          );
        })}
      </ul>
      {hasMore && (
        <button className="zaw-secondary" disabled={loading} onClick={() => void loadMore()}>
          {loading ? "载入中…" : "载入更多"}
        </button>
      )}
    </div>
  );
}
