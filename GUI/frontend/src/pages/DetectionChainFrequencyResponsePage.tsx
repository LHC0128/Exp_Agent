import { runStatusLabel } from "../components/workflow/RunHistory";
import { WorkflowExperimentPage, type WorkflowRunSummary } from "./WorkflowExperimentPage";

const ID = "detection-chain-frequency-response";

type Summary = WorkflowRunSummary & {
  failure_reason?: string;
  temperature_control?: { control_source?: string; actual_temperature_c?: number | null } | null;
};

function Result({ value }: { value: Summary }) {
  const artifacts = value.artifacts ?? {};
  return <div className="zaw-latest">
    <p><strong>{value.run_id}</strong> · {value.timestamp}</p>
    <p>采集：{runStatusLabel(value.completion_status)} · 分析：{runStatusLabel(value.analysis_status)}</p>
    {value.temperature_control?.control_source === "external_software" &&
      <p className="alert">外部温控：程序未设置或检测温度，请确认外部软件维持目标温度。</p>
    }
    {value.failure_reason && <div className="alert error">{value.failure_reason}</div>}
    {value.analysis_error && <div className="alert error">{value.analysis_error}</div>}
    {artifacts["frequency_response.png"] &&
      <figure className="zaw-figure">
        <figcaption>探测链路频率响应</figcaption>
        <img src={artifacts["frequency_response.png"]} alt="频率响应曲线" />
      </figure>
    }
    {!artifacts["frequency_response.png"] && <p className="zaw-empty">本次运行暂无结果图。</p>}
    <p className="xy-artifact-links">
      {["analysis_results.npz"].filter(name => artifacts[name]).map(name =>
        <a key={name} href={artifacts[name]} download>{name}</a>
      )}
    </p>
  </div>;
}

export function DetectionChainFrequencyResponsePage() {
  return <WorkflowExperimentPage experimentId={ID} eyebrow="CALIBRATION · FREQUENCY"
    renderResult={summary => <Result value={summary as Summary} />} />;
}
