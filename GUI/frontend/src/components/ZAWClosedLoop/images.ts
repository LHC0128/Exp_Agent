// 共享 URL 助手：历史 Tab 详情中的 artifact 路径已经由后端填好绝对 URL，
// 这里只保留控件内可复用的拼装函数，避免 magic string 散落。
export const CONVERGENCE_PNG = "convergence.png";
export const WAVEFORM_COMPARISON_PNG = "waveform_comparison.png";

export function artifactUrl(
  experimentId: string,
  runId: string,
  name: string,
): string {
  return `/api/runs/${encodeURIComponent(experimentId)}/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(name)}`;
}

export const ZAW_CLOSED_LOOP_EXPERIMENT_ID = "z-aw-closed-loop-waveform-correction";
export const SUMMARY_PATH = (runId: string) =>
  `/api/experiments/${ZAW_CLOSED_LOOP_EXPERIMENT_ID}/runs/${encodeURIComponent(runId)}/summary`;
