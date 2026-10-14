import { useEffect, useState } from "react";

import { api } from "../api";
import { useJobActivity } from "../components/JobActivity";
import { PageHead } from "../components/PageHead";
import type { Device } from "../types/api";

type Health = { status: string; instance_id: string; hardware_busy: boolean };
type RestartState = "idle" | "waiting" | "success" | "error";

const RESTART_POLL_INTERVAL_MS = 500;
const RESTART_TIMEOUT_MS = 20_000;

const sleep = (milliseconds: number) => new Promise<void>((resolve) => {
  window.setTimeout(resolve, milliseconds);
});

async function waitForBackendRestart(previousInstanceId: string): Promise<Health> {
  const deadline = Date.now() + RESTART_TIMEOUT_MS;
  while (Date.now() < deadline) {
    await sleep(RESTART_POLL_INTERVAL_MS);
    try {
      const next = await api<Health>("/api/health");
      if (next.instance_id && next.instance_id !== previousInstanceId) return next;
    } catch {
      // 后端停机期间继续等待新进程监听端口。
    }
  }
  throw new Error("后端重启超时，请检查后端窗口中的错误并手动启动 GUI 后端。");
}

function isApiError(reason: unknown): reason is Error & { code?: string } {
  return reason instanceof Error && "code" in reason;
}

export function OverviewPage() {
  const { hardwareBusy, activeJobs } = useJobActivity();
  const [health, setHealth] = useState<Health>();
  const [devices, setDevices] = useState<Device[]>([]);
  const [restartState, setRestartState] = useState<RestartState>("idle");
  const [restartMessage, setRestartMessage] = useState("");

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
    api<Device[]>("/api/devices").then((value) => {
      if (!disposed) setDevices(value);
    }).catch(() => {
      // 设备列表失败时保持空列表，不影响其他区域。
    });
    const timer = window.setInterval(refreshHealth, 3000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, []);

  const restartBusy = restartState === "waiting";
  const anyActivity = Boolean(health?.hardware_busy || hardwareBusy || activeJobs.length);
  const restartDisabled = restartBusy || anyActivity || !health?.instance_id;
  const restartTitle = !health?.instance_id
    ? "等待后端状态"
    : anyActivity
      ? "有任务或硬件操作进行中，完成后才能重启后端"
      : "重启 FastAPI 后端并应用磁盘上的代码修改";

  const restartBackend = async () => {
    if (restartDisabled || !health?.instance_id) return;
    if (!window.confirm("后端将短暂断线，内存中的任务状态会清空。确认重启后端？")) return;

    const previousInstanceId = health.instance_id;
    setRestartState("waiting");
    setRestartMessage("正在重启后端，等待服务恢复…");
    try {
      try {
        await api<{ status: string }>("/api/restart", { method: "POST" });
      } catch (reason) {
        if (isApiError(reason)) throw reason;
        // 连接可能正好在后端退出时中断，继续通过 instance_id 确认结果。
      }
      const nextHealth = await waitForBackendRestart(previousInstanceId);
      setHealth(nextHealth);
      setRestartState("success");
      setRestartMessage("后端已重启，页面已恢复。");
      api<Device[]>("/api/devices").then(setDevices).catch(() => {
        // 设备列表刷新失败不影响后端重启结果。
      });
    } catch (reason) {
      setRestartState("error");
      setRestartMessage(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const hardwareActive = Boolean(health?.hardware_busy || hardwareBusy);
  const heroTitle = restartBusy
    ? "后端重启中"
    : hardwareActive
      ? "设备任务运行中"
      : health
        ? "系统已就绪"
        : "后端不可用";
  const heroDetail = restartBusy
    ? "正在等待 FastAPI 后端重新监听 8000 端口"
    : hardwareActive
      ? "当前锁定其他硬件写入"
      : health
        ? "可以开始读取设备或执行实验"
        : "请检查后端窗口或重新启动 GUI";
  const restartAlertClass = restartState === "error"
    ? "alert error"
    : restartState === "success"
      ? "alert success"
      : "alert";

  return (
    <>
      <PageHead
        eyebrow="SYSTEM OVERVIEW"
        title="实验系统总览"
        description="集中管理仪器状态、校准任务与实验测量。"
        action={(
          <button
            className="secondary"
            type="button"
            disabled={restartDisabled}
            title={restartTitle}
            aria-busy={restartBusy}
            onClick={() => void restartBackend()}
          >
            {restartBusy ? "重启中…" : "重启后端"}
          </button>
        )}
      />
      {restartMessage && <p className={restartAlertClass} role="status">{restartMessage}</p>}
      <section className="hero-grid">
        <div className="hero-card">
          <p>硬件工作区</p>
          <h2>{heroTitle}</h2>
          <span>{heroDetail}</span>
          <div className="signal-line"><i /><i /><i /><i /><i /></div>
        </div>
        <div className="metric">
          <span>已映射设备</span>
          <strong>{devices.length}</strong>
          <small>设备库中的仪器</small>
        </div>
      </section>
    </>
  );
}
