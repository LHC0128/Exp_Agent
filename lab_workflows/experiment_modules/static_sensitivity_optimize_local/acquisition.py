"""逐候选复用标准静磁场工作流；父清单只在子测量恢复温控后写盘。"""
from copy import deepcopy
import time

from ...experiment_runtime import check_cancelled, report_runtime_progress
from ..noise_spectrum_xy.state_io import write_state
from ..static_sensitivity.analysis import analyze as analyze_point
from ..static_sensitivity.stages import ACQUISITION as POINT_STAGES, FINISH_ID as POINT_FINISH_ID
from . import preparation, run_format

POINT_STEPS = [stage.id for stage in POINT_STAGES if stage.id not in ("A01", "A02", "A03", POINT_FINISH_ID)]


def save_manifest(ctx):
    write_state(ctx.directory.root / run_format.MANIFEST, dict(experiment_id=run_format.EXPERIMENT_ID,
        raw_format_version=run_format.RAW_FORMAT_VERSION, run_id=ctx.directory.root.name,
        baseline_params=ctx.run_parameters["BASELINE_PARAMS"],
        optimize_sequence=ctx.params.sequence(), records=deepcopy(ctx.records)))


def run_points(ctx):
    started = time.monotonic()
    for record in ctx.records:
        check_cancelled()
        params = preparation.point_parameters(ctx, record)
        record.update(status="running", error="")
        save_manifest(ctx)
        try:
            ctx.point.update_parameters(params.to_external())
            ctx.point.execute(POINT_STEPS)
            point = ctx.point.snapshot()
            record["run_id"] = point["run_id"]
            if not (point.get("safety_shutdown") or {}).get("completed"):
                raise RuntimeError("子运行安全恢复未完成，停止局部优化")
            try:
                summary = analyze_point(ctx.root / "data" / run_format.DATA_TYPE / record["run_id"])
            except ValueError as exc:
                # 此处硬件采集及恢复已经成功；只允许离线质量错误成为坏点。
                record.update(status="quality_failed", error=str(exc), fit_rejection_reasons=str(exc))
            else:
                record.update(status="completed", success=bool(summary.get("success")),
                    fit_is_valid=bool(summary.get("fit_valid")),
                    sens_flat_fT_per_sqrt_Hz=summary.get("sensitivity_flat_fT_per_sqrt_Hz"),
                    fit_r2=summary.get("fit_r_squared"), gamma_nT=summary.get("fit_gamma_fT", 0.) / 1e6,
                    slope_V_per_fT=summary.get("fit_slope_V_per_fT"),
                    fit_rejection_reasons="; ".join(summary.get("fit_rejection_reasons", [])))
            save_manifest(ctx)
            count = record["iteration"]
            report_runtime_progress("optimization", f"候选 {count}/{len(ctx.records)}：{record['parameter']}={record['value']}，{record['status']}",
                100 * count / len(ctx.records), estimated_remaining_seconds=(time.monotonic() - started) / count * (len(ctx.records) - count))
        except BaseException as exc:
            record.update(status="failed", error=str(exc), run_id=ctx.point.snapshot().get("run_id"))
            # 不在这里写盘：让父 finally 先完成恢复，再抢救清单，避免掩盖原始错误。
            raise
