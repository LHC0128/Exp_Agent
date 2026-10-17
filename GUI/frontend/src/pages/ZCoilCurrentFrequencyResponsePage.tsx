import { useEffect, useState } from "react";
import { api } from "../api";
import { WorkflowExperimentPage, type WorkflowRunSummary } from "./WorkflowExperimentPage";

type CurrentReport = { capture_count?: number; frequency_count?: number; phase_reference?: string;
  reliable_point_count?: number; amplitude_reliable_point_count?: number; phase_reliable_point_count?: number;
  reliable_band_hz?: { minimum: number | null; maximum: number | null }; excluded_frequency_hz?: number[];
  closed_loop_usable?: boolean; valid_frame_count_rule?: string;
  sense_resistor_ohm?: number };
const statusLabel = (value: string) => ({ completed: "完成", failed: "失败", cancelled: "已取消",
  running: "运行中", not_started: "未分析", quality_failed: "质量未通过", stale: "需重算",
} as Record<string, string>)[value] ?? value;

function CurrentResult({ summary }: { summary: WorkflowRunSummary }) {
  const artifacts = summary.artifacts ?? {};
  const reportUrl = artifacts["results/frequency_response.json"];
  const [report, setReport] = useState<CurrentReport>();
  const [error, setError] = useState("");
  useEffect(() => {
    let disposed = false;
    setReport(undefined); setError("");
    if (reportUrl) void api<CurrentReport>(reportUrl).then(value => {
      if (!disposed) setReport(value);
    }).catch(reason => { if (!disposed) setError(`电流频响诊断加载失败：${String(reason)}`); });
    return () => { disposed = true; };
  }, [reportUrl]);
  return <div className="zaw-latest">
    <p>{summary.run_id} · {summary.timestamp} · 采集：{statusLabel(summary.completion_status)} · 分析：{statusLabel(summary.analysis_status)}</p>
    {summary.analysis_error && <p role="alert">{summary.analysis_error}</p>}
    {error && <p role="alert">{error}</p>}
    <p>CH3 低端采样电阻电压换算为电流。闭环相位要求可靠同帧驱动参考、每频至少两次有效重复及圆周相位标准差 &lt; 15°；单次测量保留幅值与相位诊断。</p>
    {report && <>
      <p>频率点：{report.frequency_count ?? "未记录"} · 采集帧：{report.capture_count ?? "未记录"} · 实测采样电阻：{report.sense_resistor_ohm ?? "未记录"} Ω</p>
      <p>幅值可靠点：{report.amplitude_reliable_point_count ?? report.reliable_point_count ?? "未记录"} · 相位可靠点：{report.phase_reliable_point_count ?? "未记录"}
        {report.reliable_band_hz && <> · 可靠覆盖：{report.reliable_band_hz.minimum == null || report.reliable_band_hz.maximum == null
          ? "无" : `${report.reliable_band_hz.minimum}–${report.reliable_band_hz.maximum} Hz`}</>}</p>
      <p>{!report.phase_reference ? "历史报告未记录相位参考，不能确认闭环相位资格。"
        : report.phase_reference !== "measured_drive_voltage" ? "采用 DG 命令幅值参考，相位不用于闭环标定。"
        : report.closed_loop_usable === undefined || report.valid_frame_count_rule !== "exact_frequency_v1"
          ? "历史报告未记录有效重复资格，请从原始帧重新分析后用于闭环。"
        : report.closed_loop_usable ? "实测参考与有效重复资格已通过；闭环仍需校验目标谐波覆盖。"
        : "当前报告不具备闭环资格；幅值与相位诊断已保留。"}</p>
      {!!report.excluded_frequency_hz?.length && <p>排除频率（Hz）：{report.excluded_frequency_hz.join("、")}</p>}
    </>}
    {artifacts["results/transfer_function.png"] && <figure className="zaw-figure">
      <figcaption>实际电流 I/V 幅值、相位与可靠性诊断</figcaption>
      <img src={artifacts["results/transfer_function.png"]} alt="results/transfer_function.png" loading="lazy" />
    </figure>}
    {!Object.keys(artifacts).length && <p>本次运行暂无结果文件。</p>}
    <p className="xy-artifact-links">{Object.entries(artifacts).map(([name, url]) =>
      <a key={name} href={url} download>{name}</a>)}</p>
  </div>;
}

export function ZCoilCurrentFrequencyResponsePage() {
  return <WorkflowExperimentPage experimentId="z-coil-current-frequency-response" finishStepId="A07" runStepId="A06"
    renderResult={summary => <CurrentResult summary={summary} />} />;
}
