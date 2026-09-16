// Mx Y 最优控制 RF 频率响应实验页的“架构”Tab：内嵌本实验的 Archify 采集流程架构图。
const ARCHITECTURE_URL = "/architecture/mx-y-optimal-control-rf-frequency-response.html";

export function ArchitectureView() {
  return (
    <section className="mx-arch">
      <div className="mx-arch-head">
        <div>
          <small>EXPERIMENT ARCHITECTURE</small>
          <h2>采集流程架构</h2>
          <p>
            本实验的设备级采集流程：GUI 参数经强类型定义进入最优控制采集工作流，
            逐频率扫描 Y RF Burst 相位并采集 Demod0 R 有效点；含可选常数 Z 对照、
            Z 波形恢复与安全收尾，以及离线分析与结果产出。
          </p>
        </div>
        <a className="architecture-open" href={ARCHITECTURE_URL} target="_blank" rel="noreferrer">
          在新页面打开
        </a>
      </div>
      <div className="architecture-frame">
        <iframe
          src={ARCHITECTURE_URL}
          title="Mx Y 最优控制频率响应实验采集流程架构图"
          loading="lazy"
          sandbox="allow-scripts allow-same-origin allow-downloads allow-popups"
          allow="clipboard-write"
        />
      </div>
    </section>
  );
}
