import {
  COMPARISON_PNG,
  MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID,
  PHASE_FREQUENCY_PNG,
} from "../components/MxYOptimalControlFrequency/images";
import {
  readCalibration,
  type MxYOptimalControlFrequencyRunSummary,
} from "../components/MxYOptimalControlFrequency/types";
import { WorkflowExperimentPage, type WorkflowRunSummary } from "./WorkflowExperimentPage";

const ANALYSIS_KEYS = {};

type Summary = WorkflowRunSummary & MxYOptimalControlFrequencyRunSummary;

const number = (value: unknown): string =>
  typeof value === "number" && Number.isFinite(value) ? String(value) : "—";

const text = (value: unknown): string => {
  if (typeof value === "number") return Number.isFinite(value) ? value.toString() : "—";
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "是" : "否";
  if (value === null || value === undefined) return "—";
  return String(value);
};

function Result({ summary }: { summary: Summary }) {
  const artifacts = summary.artifacts ?? {};
  const calibration = readCalibration(summary.calibration ?? {});
  const comparison = artifacts[COMPARISON_PNG];
  const heatmap = artifacts[PHASE_FREQUENCY_PNG];
  const optimal = summary.optimal_response_metadata ?? {};
  const constant = summary.constant_response_metadata ?? {};
  return (
    <div className="zaw-latest">
      <div className="zaw-metrics-grid">
        <div className="zaw-metric">
          <small>完成状态</small>
          <span className="zaw-metric-value">{summary.completion_status}</span>
        </div>
        <div className="zaw-metric">
          <small>常数对照</small>
          <span className="zaw-metric-value">{summary.comparison_enabled ? "已启用" : "未启用"}</span>
        </div>
        <div className="zaw-metric">
          <small>最优控制有效点</small>
          <span className="zaw-metric-value">{number(optimal.valid_points)}</span>
        </div>
        <div className="zaw-metric">
          <small>常数控制有效点</small>
          <span className="zaw-metric-value">{number(constant.valid_points)}</span>
        </div>
      </div>
      {summary.comparison_enabled && calibration.extrapolated && (
        <div className="alert error">
          目标电压超出标定支撑范围（{text(calibration.supportVoltageMinV)} – {text(calibration.supportVoltageMaxV)} V），
          结果依赖全局线性外推，请谨慎使用。
        </div>
      )}
      <p className="zaw-latest-meta">
        {summary.timestamp} · {summary.run_tag}
        {summary.comparison_enabled && calibration.calibrationRun
          ? ` · 标定：${calibration.calibrationRun}`
          : ""}
      </p>
      {summary.control_source && (
        <p>闭环冻结波形：{text(summary.control_source.run)} · 幅度 {text(summary.control_source.amplitude_vpp)} Vpp · 偏置 {text(summary.control_source.offset_v)} V</p>
      )}
      {summary.comparison_enabled && (
        <details className="zaw-calibration">
          <summary>标定信息</summary>
          <table>
            <tbody>
              <tr><th>标定运行</th><td>{text(calibration.calibrationRun)}</td></tr>
              <tr><th>目标 Larmor 频率 (Hz)</th><td>{text(calibration.targetLarmorFrequencyHz)}</td></tr>
              <tr><th>目标线圈电流 (A)</th><td>{text(calibration.targetCurrentA)}</td></tr>
              <tr><th>目标恒定 Z 电压 (V)</th><td>{text(calibration.targetVoltageV)}</td></tr>
              <tr><th>电压-电流拟合斜率 (A/V)</th><td>{text(calibration.gainAPerV)}</td></tr>
              <tr>
                <th>标定有效电压范围</th>
                <td>{text(calibration.supportVoltageMinV)} – {text(calibration.supportVoltageMaxV)} V</td>
              </tr>
              <tr><th>分析 SHA-256</th><td>{text(calibration.analysisSha256)}</td></tr>
            </tbody>
          </table>
        </details>
      )}
      {comparison && (
        <figure className="zaw-figure">
          <figcaption>
            {summary.comparison_enabled ? "最优控制中位数 vs 常数控制对照" : "最优控制中位数曲线"}
          </figcaption>
          <img src={comparison} alt="RF 频率响应曲线" loading="lazy" />
        </figure>
      )}
      {heatmap && (
        <figure className="zaw-figure">
          <figcaption>相位-频率热图</figcaption>
          <img src={heatmap} alt="相位-频率热图" loading="lazy" />
        </figure>
      )}
    </div>
  );
}

export function MxYOptimalControlFrequencyPage() {
  return <WorkflowExperimentPage experimentId={MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID}
    eyebrow="EXPERIMENT · MX_Y_OPTIMAL_CONTROL_RF_FREQUENCY_RESPONSE"
    analysisParameterKeys={ANALYSIS_KEYS}
    renderResult={summary => <Result summary={summary as Summary} />} />;
}

