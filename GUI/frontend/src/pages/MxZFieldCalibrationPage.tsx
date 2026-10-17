import { WorkflowExperimentPage, type WorkflowRunSummary } from "./WorkflowExperimentPage";

const labels: Record<string, string> = { completed: "完成", failed: "失败", cancelled: "已取消",
  running: "运行中", not_started: "未分析" };
const status = (value: string) => labels[value] ?? value;
const plots: Record<string, string> = { "z_frequency_calibration.png": "Z 电压与共振中心",
  "frequency_response_fits.png": "各 Z 电压的 R 频扫拟合", "z_frequency_residuals.png": "线性拟合残差" };

function Result({ value }: { value: WorkflowRunSummary }) {
  const artifacts = value.artifacts ?? {};
  return <div className="zaw-latest">
    <p>{value.run_id} · {value.timestamp} · 采集：{status(value.completion_status)} · 分析：{status(value.analysis_status)}</p>
    {value.analysis_error && <div className="alert error">{value.analysis_error}</div>}
    {!Object.keys(artifacts).length && <p>本次运行暂无分析结果。</p>}
    {Object.entries(artifacts).filter(([name]) => plots[name.split("/").at(-1)!]).map(([name, url]) =>
      <figure className="zaw-figure" key={name}><figcaption>{plots[name.split("/").at(-1)!]}</figcaption>
        <img src={url} alt={plots[name.split("/").at(-1)!]} loading="lazy" /></figure>)}
    <p className="xy-artifact-links">{Object.entries(artifacts).map(([name, url]) => <a key={name} href={url} download>{name}</a>)}</p>
  </div>;
}

export function MxZFieldCalibrationPage() {
  return <WorkflowExperimentPage experimentId="mx-z-field-calibration" finishStepId="A10" runStepId="A08"
    analysisParameterKeys={{ LINEAR_R_SQUARED_MIN: "LINEAR_R_SQUARED_MIN" }} renderResult={value => <Result value={value} />} />;
}
