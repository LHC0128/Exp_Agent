import { useEffect, useState } from "react";
import { api } from "../../api";
import type {
  MxYOptimalControlFrequencyRunItem,
  MxYOptimalControlFrequencyRunsResponse,
} from "./types";
import { RUNS_PATH } from "./images";

const PAGE_SIZE = 50;

type RunListProps = {
  selectedRunId: string | null;
  onSelect: (run: MxYOptimalControlFrequencyRunItem) => void;
};

export function RunList({ selectedRunId, onSelect }: RunListProps) {
  const [runs, setRuns] = useState<MxYOptimalControlFrequencyRunItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [hasMore, setHasMore] = useState(false);

  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setError("");
    // 端点本身已限定实验，无需再传 experiment_id。
    const params = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: "0",
    });
    api<MxYOptimalControlFrequencyRunsResponse>(`${RUNS_PATH}?${params}`)
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
      });
      const data = await api<MxYOptimalControlFrequencyRunsResponse>(`${RUNS_PATH}?${params}`);
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
          const tone = run.run_id === selectedRunId ? "selected" : "";
          return (
            <li key={run.run_id}>
              <button
                className={`zaw-run-card ${tone}`}
                onClick={() => onSelect(run)}
                title={run.run_id}
              >
                <strong>{run.run_id}</strong>
                <span>{run.timestamp}</span>
                <span>{run.completion_status}</span>
                <span className="zaw-run-card-artifacts">
                  {run.comparison_enabled ? "含常数对照" : "仅最优控制"}
                </span>
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
