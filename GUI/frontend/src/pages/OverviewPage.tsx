import { useEffect, useState } from "react";

import { api } from "../api";
import { PageHead } from "../components/PageHead";
import type { Device } from "../types/api";

type Health = { status: string; hardware_busy: boolean };

/** 实验系统四阶段流程带：参数配置 → 仪器采集 → 原始数据 → 离线分析。 */
const PIPELINE_STAGES = [
  { index: "01", label: "参数配置", detail: "GUI 提交强类型参数" },
  { index: "02", label: "仪器采集", detail: "驱动层执行扫描" },
  { index: "03", label: "原始数据", detail: "raw/ 落盘原始记录" },
  { index: "04", label: "离线分析", detail: "results/ 产出图表" },
];

/** 架构图中的两类语义连线，先在图外说明，减少读图成本。 */
const DIAGRAM_SEMANTICS = [
  {
    key: "safety",
    title: "安全边界",
    detail: "safety_limits.yaml 在输出前校验仪器量值。",
  },
  {
    key: "compat",
    title: "兼容路径",
    detail: "LegacyScriptAdapter 只读接入 experiments/ 旧脚本。",
  },
];

const ARCHITECTURE_URL = "/architecture/system-architecture.html";

export function OverviewPage() {
  const [health, setHealth] = useState<Health>();
  const [devices, setDevices] = useState<Device[]>([]);

  useEffect(() => {
    let disposed = false;
    const refreshHealth = () => {
      api<Health>("/api/health").then((value) => {
        if (!disposed) setHealth(value);
      }).catch(() => {
        // 后端不可用时保持上次状态。
      });
    };
    refreshHealth();
    api<Device[]>("/api/devices").then(setDevices).catch(() => {
      // 设备列表失败时保持空列表，不影响其他区域。
    });
    const timer = window.setInterval(refreshHealth, 3000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <>
      <PageHead
        eyebrow="SYSTEM OVERVIEW"
        title="实验系统总览"
        description="集中管理仪器状态、校准任务与实验测量。"
      />
      <section className="hero-grid">
        <div className="hero-card">
          <p>硬件工作区</p>
          <h2>{health?.hardware_busy ? "设备任务运行中" : "系统已就绪"}</h2>
          <span>{health?.hardware_busy ? "当前锁定其他硬件写入" : "可以开始读取设备或执行实验"}</span>
          <div className="signal-line"><i /><i /><i /><i /><i /></div>
        </div>
        <div className="metric">
          <span>已映射设备</span>
          <strong>{devices.length}</strong>
          <small>设备库中的仪器</small>
        </div>
      </section>
      <section className="section architecture-workbench">
        <div className="architecture-workbench-head">
          <div>
            <small>SYSTEM ARCHITECTURE</small>
            <h2>实验系统架构</h2>
            <p>从参数配置到结果产出的完整链路，含公共 lab_workflows 边界与 experiments/ 脚本边界。</p>
          </div>
          <a className="architecture-open" href={ARCHITECTURE_URL} target="_blank" rel="noreferrer">
            在新页面打开
          </a>
        </div>
        <div className="architecture-body">
          <ol className="architecture-pipeline" aria-label="实验流程四阶段">
            {PIPELINE_STAGES.map((stage) => (
              <li key={stage.index} className="architecture-stage">
                <b>{stage.index}</b>
                <strong>{stage.label}</strong>
                <small>{stage.detail}</small>
              </li>
            ))}
          </ol>
          <ul className="architecture-semantics" aria-label="架构图语义标识">
            {DIAGRAM_SEMANTICS.map((item) => (
              <li key={item.key} className={`architecture-semantic architecture-semantic-${item.key}`}>
                <span className="architecture-semantic-line" aria-hidden="true" />
                <strong>{item.title}</strong>
                <small>{item.detail}</small>
              </li>
            ))}
          </ul>
        </div>
        <div className="architecture-frame">
          <iframe
            src={ARCHITECTURE_URL}
            title="实验系统架构交互图"
            loading="lazy"
            sandbox="allow-scripts allow-same-origin allow-downloads allow-popups"
            allow="clipboard-write"
          />
        </div>
      </section>
    </>
  );
}
