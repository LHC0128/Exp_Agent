import { WorkflowExperimentPage, type WorkflowRunSummary } from "./WorkflowExperimentPage";

type DriftRow = {
  cycle_index: number; status: string; valid: boolean; actual_start_utc?: string | null;
  actual_elapsed_s?: number | null; sensitivity_flat_median_ft_per_sqrt_hz?: number | null;
  zero_point_sensitivity_flat_median_ft_per_sqrt_hz?: number | null; linewidth_hwhm_hz?: number | null;
  response_fit_r_squared?: number | null; excluded_point_count?: number | null;
  warnings?: string; error?: string | null;
};
type DriftSummary = {
  rows?: DriftRow[]; valid_cycle_count?: number;
  invalid_cycles?: { cycle_index: number; reason: string }[];
};
type Summary = WorkflowRunSummary & { drift_summary?: DriftSummary };
const ANALYSIS_KEYS = {};
const labels: Record<string, string> = {
  completed: "完成", failed: "失败", cancelled: "已取消", running: "运行中", acquired: "等待安全收尾",
  not_started: "未开始分步分析", partial: "部分完成", unknown: "未记录", online: "在线趋势缓存",
  pending: "未执行", invalid_quality: "质量无效", analysis_failed: "子分析失败", acquisition_failed: "采集失败",
  quality_failed: "质量未通过", invalid: "科学无效",
};
const number = (value?: number | null) => value != null && Number.isFinite(value) ? value.toPrecision(5) : "—";

function DriftResult({ summary }: { summary: Summary }) {
  const artifacts = summary.artifacts ?? {};
  const drift = summary.drift_summary;
  const rows = drift?.rows ?? [];
  const valid = drift?.valid_cycle_count ?? rows.filter(row => row.valid).length;
  return <div className="zaw-latest">
    <p><strong>{summary.run_id}</strong> · {summary.timestamp} · 采集：{labels[summary.completion_status] ?? summary.completion_status}
      {" · 分析："}{labels[summary.analysis_status] ?? summary.analysis_status}</p>
    {summary.analysis_error && <p role="alert">{summary.analysis_error}</p>}
    {rows.length > 0 && <>
      <p>已有趋势缓存 {rows.length} 轮，其中科学有效 {valid} 轮。幅度等效 HWHM 不代表扫频共振线宽。</p>
      {valid === 0 && <p>当前全部轮次无效，仍可查看和导出诊断。</p>}
      <div className="xy-diagnostic" style={{ overflowX: "auto" }}>
        <table aria-label="长飘轮次趋势" style={{ minWidth: 1040, whiteSpace: "nowrap" }}><thead><tr><th>原轮次索引</th><th>开始时间 (UTC)</th><th>经过 (s)</th>
          <th>采集状态</th><th>科学有效</th><th>灵敏度 (fT/√Hz)</th><th>零点方法 (fT/√Hz)</th>
          <th>幅度等效 HWHM (Hz)</th><th>R²</th><th>排除点数</th><th>无效原因 / 警告</th></tr></thead>
          <tbody>{rows.map(row => <tr key={row.cycle_index}>
            <td>{row.cycle_index}</td><td>{row.actual_start_utc ?? "—"}</td><td>{number(row.actual_elapsed_s)}</td>
            <td>{labels[row.status] ?? row.status}</td><td>{row.valid ? "有效" : "无效"}</td>
            <td>{number(row.sensitivity_flat_median_ft_per_sqrt_hz)}</td>
            <td>{number(row.zero_point_sensitivity_flat_median_ft_per_sqrt_hz)}</td><td>{number(row.linewidth_hwhm_hz)}</td>
            <td>{number(row.response_fit_r_squared)}</td><td>{row.excluded_point_count ?? "—"}</td>
            <td style={{ whiteSpace: "normal", minWidth: 320, maxWidth: 480 }}>{[drift?.invalid_cycles?.find(item => item.cycle_index === row.cycle_index)?.reason,
              row.error, row.warnings].filter(Boolean).filter((value, index, all) => all.indexOf(value) === index).join("；") || (row.valid ? "—" : "旧缓存未记录原因，请查看子运行诊断")}</td>
          </tr>)}</tbody></table>
      </div>
    </>}
    {([ ["drift_trend.png", "长飘趋势（实际经过时间）"],
      ["drift_trend_clock_time.png", "长飘趋势（上海日期时间）"] ] as const)
      .filter(([name]) => artifacts[`results/${name}`]).map(([name, title]) => <figure className="zaw-figure" key={name}>
        <figcaption>{title}</figcaption><img src={artifacts[`results/${name}`]} alt={title} loading="lazy" />
      </figure>)}
    {!Object.keys(artifacts).length && <p>本次运行暂无结果文件。</p>}
    <p className="xy-artifact-links">{Object.entries(artifacts).map(([name, url]) =>
      <a key={name} href={url} download>{name}</a>)}</p>
  </div>;
}

const renderResult = (summary: WorkflowRunSummary) => <DriftResult summary={summary} />;

export function MxYRFSensitivityDriftPage() {
  return <WorkflowExperimentPage experimentId="mx-y-rf-sensitivity-drift"
    analysisParameterKeys={ANALYSIS_KEYS} renderResult={renderResult} />;
}
