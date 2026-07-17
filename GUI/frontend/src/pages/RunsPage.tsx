import { useEffect, useState } from "react";

import { api } from "../api";
import { JobView } from "../components/JobView";
import { PageHead } from "../components/PageHead";
import type { Job, RunSummary } from "../types/api";

export function RunsPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [job, setJob] = useState<Job>();
  useEffect(() => { api<RunSummary[]>("/api/runs").then(setRuns); }, []);
  const analyze = (run: RunSummary) => api<Job>(`/api/runs/${encodeURIComponent(run.id)}/analyze`, {
    method: "POST",
    body: JSON.stringify({ experiment_id: run.experiment_id }),
  }).then(setJob);

  return (
    <>
      <PageHead
        eyebrow="RUN HISTORY"
        title="运行记录"
        description="浏览全部实验的数据目录，并按指定运行目录重新分析。"
      />
      <div className="runs">
        {runs.length === 0 ? (
          <div className="empty"><h3>暂无运行记录</h3><p>完成一次实验后，结果会自动显示在这里。</p></div>
        ) : runs.map((run) => (
          <article key={`${run.experiment_id}:${run.id}`}>
            <div>
              <small>{run.experiment_title}</small><h3>{run.id}</h3><p>{run.path}</p>
              {run.can_analyze && <button className="secondary" onClick={() => analyze(run)}>重新分析</button>}
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
      <JobView job={job} onUpdate={setJob} />
    </>
  );
}
