"""采集、分析和历史接口共同使用的运行资格。"""

from pathlib import Path
import re

EXPERIMENT_ID = "rf-sensitivity-aw"
DATA_TYPE = "RF_Field_Sensitivity_AW"
SIGNAL_CHAIN = "Demod0 Y -> AuxOut2 -> Signal Input2 -> Demod3 X"
COMPARISON_JOB_KIND = f"comparison:{EXPERIMENT_ID}"
COMPARISON_FILES = ("full_analysis_comparison.png", "full_analysis_comparison.pdf")


def is_new_run(config: object) -> bool:
    return (
        isinstance(config, dict)
        and config.get("experiment_id") == EXPERIMENT_ID
        and config.get("schema_version") == 2
        and config.get("execution_mode") == "typed_workflow"
        and config.get("acquisition_method") == "direct_aw_x"
        and isinstance(config.get("parameters"), dict)
    )


def analysis_block_reason(config: object) -> str:
    if not is_new_run(config):
        return "仅支持 schema version 2 的 DirectAW 新格式运行，旧数据不支持分析"
    if config.get("completion_status") != "completed":
        return "采集尚未完整完成，仅可查看诊断"
    xy_phase = config.get("xy_phase_calibration")
    if config.get("phase_calibration_status") != "completed" or not isinstance(xy_phase, dict) or xy_phase.get("converged") is not True:
        return "校相未通过，仅可查看诊断"
    return ""


def comparison_block_reason(run_dir: Path, config: object) -> str:
    reason = analysis_block_reason(config)
    if reason:
        return reason
    if config.get("analysis_status") != "completed":
        return "分析尚未完成，请先重新分析"
    directory = Path(run_dir).resolve()
    try:
        for relative in ("experiment_config.yaml", "results/analysis.yaml", "results/response_fit.npz", "results/sensitivity.npz"):
            path = directory / relative
            if directory not in path.resolve().parents:
                return "对比输入必须位于本次运行目录内"
            if not path.is_file():
                return f"缺少 {relative}，请先重新分析"
    except OSError:
        return "对比结果无法读取，请刷新后重试"
    return ""


def normalize_comparison_runs(runs: list[dict]) -> list[dict]:
    """GUI 与独立比较入口共用选择、路径标识和单行英文图例校验。"""
    if len(runs) < 2:
        raise ValueError("至少选择两次运行进行对比")
    seen = set()
    normalized = []
    for item in runs:
        run_id = item.get("run_id")
        if not isinstance(run_id, str) or not run_id or run_id in {".", ".."} or run_id.endswith((".", " ")) or any(c in run_id for c in "/\\:\x00"):
            raise ValueError("运行 ID 必须是单个目录名")
        if run_id in seen:
            raise ValueError(f"重复选择运行：{run_id}")
        seen.add(run_id)
        legend = item.get("legend", "")
        if not isinstance(legend, str) or any(ord(c) < 32 or ord(c) == 127 for c in legend) or re.search(r"[\u3400-\u9fff\uf900-\ufaff]", legend):
            raise ValueError("图例须使用单行英文文字、数字或符号")
        normalized.append({**item, "legend": legend.strip() or run_id})
    return normalized
