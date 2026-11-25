const SCRIPT_PREFIX = "script:";

export function isScriptJob(kind: string): boolean {
  return kind.startsWith(SCRIPT_PREFIX);
}

/** 脚本任务的完整路径，供 title 显示；其他任务没有路径。 */
export function scriptJobPath(kind: string): string | undefined {
  return isScriptJob(kind) ? kind.slice(SCRIPT_PREFIX.length) : undefined;
}

export function kindLabel(kind: string): string {
  if (isScriptJob(kind)) {
    const path = kind.slice(SCRIPT_PREFIX.length);
    return `调试脚本 · ${path.slice(path.lastIndexOf("/") + 1)}`;
  }
  if (kind.startsWith("experiment:")) return "实验运行";
  if (kind.startsWith("session:")) return "分步会话";
  if (kind.startsWith("analysis:")) return "离线分析";
  if (kind.startsWith("comparison:")) return "对比分析";
  return ({ "clock-sync": "时钟同步", "load-init": "输出负载初始化", "phase-calibration": "相位校准", "lockin-capture": "锁相采集", "z-arbitrary-control": "Z 任意波控制" } as Record<string, string>)[kind] ?? (kind.startsWith("update:") ? "软件更新" : kind);
}