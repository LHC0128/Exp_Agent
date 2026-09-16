// Mx Y 最优控制 RF 频率响应专属页面的实验 ID、artifact 名与 URL 助手。
export const MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID =
  "mx-y-optimal-control-rf-frequency-response";

export const COMPARISON_PNG = "rf_frequency_response_comparison.png";
export const PHASE_FREQUENCY_PNG = "r_phase_frequency.png";

/** 历史 Tab 的运行列表端点：后端只返回新格式运行。 */
export const RUNS_PATH = `/api/experiments/${MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID}/runs`;

export const SUMMARY_PATH = (runId: string) =>
  `/api/experiments/${MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID}/runs/${encodeURIComponent(runId)}/summary`;

export function artifactUrl(
  experimentId: string,
  runId: string,
  name: string,
): string {
  return `/api/runs/${encodeURIComponent(experimentId)}/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(name)}`;
}

/** 对照相关参数：这些字段只在 COMPARISON_ENABLED 为真时渲染。 */
export const COMPARISON_TOGGLE = "COMPARISON_ENABLED";
export const COMPARISON_FIELD_NAMES = [
  "CONSTANT_CONTROL_LARMOR_FREQUENCY_HZ",
  "CONSTANT_CONTROL_CALIBRATION_SOURCE_RUN",
] as const;
