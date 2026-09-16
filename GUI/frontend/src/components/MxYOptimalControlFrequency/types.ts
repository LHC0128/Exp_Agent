// Mx Y 最优控制 RF 频率响应专属摘要的本地类型。
// 后端 Pydantic model 总是为 parameters/calibration/artifacts 等字段设置
// default_factory，因此运行时不会出现 undefined，这里直接声明为必选。
export type MxYOptimalControlFrequencyRunSummary = {
  run_id: string;
  timestamp: string;
  run_tag: string;
  completion_status: string;
  comparison_enabled: boolean;
  parameters: Record<string, unknown>;
  calibration: Record<string, unknown>;
  optimal_response_metadata: Record<string, unknown>;
  constant_response_metadata: Record<string, unknown>;
  /** 后端填好的绝对 artifact URL，键为图片文件名。 */
  artifacts: Record<string, string>;
};

/** 新格式历史列表项；旧格式运行不会出现在该列表中。 */
export type MxYOptimalControlFrequencyRunItem = {
  run_id: string;
  timestamp: string;
  run_tag: string;
  completion_status: string;
  comparison_enabled: boolean;
};

export type MxYOptimalControlFrequencyRunsResponse = {
  total: number;
  offset: number;
  limit: number;
  runs: MxYOptimalControlFrequencyRunItem[];
};

export type CalibrationSummary = {
  calibrationRun?: string;
  targetLarmorFrequencyHz?: number;
  targetCurrentA?: number;
  targetVoltageV?: number;
  extrapolated?: boolean;
  supportVoltageMinV?: number;
  supportVoltageMaxV?: number;
  gainAPerV?: number;
  analysisSha256?: string;
};

const asNumber = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value)
    ? value
    : typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))
      ? Number(value)
      : undefined;

const asString = (value: unknown): string | undefined =>
  typeof value === "string" && value !== "" ? value : undefined;

/** 把后端 calibration 块收敛为界面需要的字段，缺失即为 undefined。 */
export function readCalibration(calibration: Record<string, unknown>): CalibrationSummary {
  const support = (calibration.calibration_support ?? {}) as Record<string, unknown>;
  const fit = (calibration.voltage_to_current_fit ?? {}) as Record<string, unknown>;
  return {
    calibrationRun: asString(calibration.calibration_run),
    targetLarmorFrequencyHz: asNumber(calibration.target_larmor_frequency_hz),
    targetCurrentA: asNumber(calibration.target_current_a),
    targetVoltageV: asNumber(calibration.target_voltage_v),
    extrapolated: calibration.extrapolated === true,
    supportVoltageMinV: asNumber(support.voltage_min_v),
    supportVoltageMaxV: asNumber(support.voltage_max_v),
    gainAPerV: asNumber(fit.gain_a_per_v),
    analysisSha256: asString(calibration.calibration_analysis_sha256),
  };
}
