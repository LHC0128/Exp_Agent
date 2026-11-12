import { useEffect, useState } from "react";

import { api } from "../api";

const TITLE_PREFIX = "[开发] ";

/** 开发目录后端（无运行目录标记）时常驻提示：仪器已禁用，采集类操作会被后端拒绝。 */
export function DevModeBanner() {
  const [dev, setDev] = useState(false);
  useEffect(() => {
    let disposed = false;
    api<{ runtime_mode?: string }>("/api/health")
      .then((health) => { if (!disposed) setDev(health.runtime_mode === "dev"); })
      .catch(() => { /* 后端不可达时由其他页面报告，不在此猜测模式。 */ });
    return () => { disposed = true; };
  }, []);
  useEffect(() => {
    if (!dev || document.title.startsWith(TITLE_PREFIX)) return;
    document.title = TITLE_PREFIX + document.title;
  }, [dev]);
  if (!dev) return null;
  return (
    <p className="dev-mode-banner" role="status">
      开发目录：仪器已禁用。可修改参数、预检、查看界面与离线分析；采集和仪器操作会被拒绝，请到运行目录 GUI 执行。
    </p>
  );
}
