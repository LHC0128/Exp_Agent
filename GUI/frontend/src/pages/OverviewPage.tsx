import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";

import { api } from "../api";
import { PageHead } from "../components/PageHead";
import type { Device } from "../types/api";

type Health = { status: string; hardware_busy: boolean };

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
      <section className="section">
        <div className="section-title"><div><small>QUICK START</small><h2>常用入口</h2></div></div>
        <div className="quick-grid">
          <NavLink to="/instruments"><b>01</b><h3>读取仪器状态</h3><p>从物理面板同步信号源与示波器参数。</p></NavLink>
          <NavLink to="/tools"><b>02</b><h3>系统准备</h3><p>同步参考时钟并执行 Demod0 安全校相。</p></NavLink>
          <NavLink to="/experiments"><b>03</b><h3>进入实验中心</h3><p>选择测量、标定、优化或硬件验证流程。</p></NavLink>
        </div>
      </section>
    </>
  );
}
