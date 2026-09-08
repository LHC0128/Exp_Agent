import { useState } from "react";

import { api } from "../api";
import { Field } from "../components/FormFields";
import { useJobActivity } from "../components/JobActivity";
import { JobView } from "../components/JobView";
import { PageHead } from "../components/PageHead";
import { ZArbitraryControl } from "../components/ZArbitraryControl";
import type { Job } from "../types/api";

export function ToolsPage() {
  const [job, setJob] = useState<Job>();
  const [error, setError] = useState("");
  const [toleranceDeg, setToleranceDeg] = useState(1);
  const [maxAttempts, setMaxAttempts] = useState(5);
  const [settleTime, setSettleTime] = useState(0.2);
  const { hardwareBusy } = useJobActivity();
  const localActive = Boolean(job && ["queued", "running"].includes(job.status));
  const blocked = hardwareBusy || localActive;

  const start = async (action: () => Promise<Job>) => {
    setError("");
    try {
      setJob(await action());
    } catch (reason) {
      setError(String(reason));
    }
  };
  const startClock = () => void start(() => api<Job>("/api/clocks/sync", { method: "POST" }));
  const startPhase = () => void start(() => api<Job>("/api/tools/demod0-phase-calibration", {
    method: "POST",
    body: JSON.stringify({
      tolerance_deg: toleranceDeg,
      max_attempts: maxAttempts,
      settle_time: settleTime,
    }),
  }));

  return (
    <>
      <PageHead
        eyebrow="SYSTEM TOOLS"
        title="功能模块"
        description="执行跨设备准备流程；同一时间只允许运行一个硬件任务。"
      />
      {error && <div className="alert error">{error}</div>}
      <div className="tool-grid">
        <article>
          <small>REFERENCE CLOCK</small><h2>参考时钟同步</h2>
          <p>按共享时钟配置逐台设置并回读信号发生器与 HF2。</p>
          <button disabled={blocked} onClick={startClock}>{blocked ? "其他硬件任务运行中" : "开始同步"}</button>
        </article>
        <article>
          <small>DEMODULATOR 0</small><h2>相位自动校准</h2>
          <p>自动关闭 Z 场和温控干扰，完成后恢复原始状态。</p>
          <div className="form-grid tool-phase-form">
            <Field label="容差 (deg)" value={toleranceDeg} min={0.01} max={5} step="any" disabled={blocked} onChange={setToleranceDeg} />
            <Field label="最大尝试次数" value={maxAttempts} min={1} max={20} disabled={blocked} onChange={setMaxAttempts} />
            <Field label="稳定等待 (s)" value={settleTime} min={0.05} max={5} step="any" disabled={blocked} onChange={setSettleTime} />
          </div>
          <button disabled={blocked} onClick={startPhase}>{blocked ? "其他硬件任务运行中" : "开始安全校相"}</button>
        </article>
      </div>
      <JobView job={job} onUpdate={setJob} />
      <ZArbitraryControl />
    </>
  );
}
