import { useState } from "react";

import { api } from "../api";
import { JobView } from "../components/JobView";
import { PageHead } from "../components/PageHead";
import type { Job } from "../types/api";

export function ToolsPage() {
  const [job, setJob] = useState<Job>();
  const startClock = () => api<Job>("/api/clocks/sync", { method: "POST" }).then(setJob);
  const startPhase = () => api<Job>("/api/tools/demod0-phase-calibration", {
    method: "POST",
    body: JSON.stringify({ tolerance_deg: 1, max_attempts: 5, settle_time: 0.2 }),
  }).then(setJob);

  return (
    <>
      <PageHead
        eyebrow="SYSTEM TOOLS"
        title="功能模块"
        description="执行跨设备准备流程；同一时间只允许运行一个硬件任务。"
      />
      <div className="tool-grid">
        <article>
          <small>REFERENCE CLOCK</small><h2>参考时钟同步</h2>
          <p>按共享时钟配置逐台设置并回读信号发生器与 HF2。</p>
          <button onClick={startClock}>开始同步</button>
        </article>
        <article>
          <small>DEMODULATOR 0</small><h2>相位自动校准</h2>
          <p>自动关闭 Z 场和温控干扰，完成后恢复原始状态。</p>
          <button onClick={startPhase}>开始安全校相</button>
        </article>
      </div>
      <JobView job={job} onUpdate={setJob} />
    </>
  );
}
