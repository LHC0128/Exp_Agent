"""采集、分析和历史接口共同使用的运行资格。"""

from pathlib import Path
import math
import re

EXPERIMENT_ID = "rf-sensitivity-aw"
DATA_TYPE = "RF_Field_Sensitivity_AW"
SIGNAL_CHAIN = "Demod0 Y -> AuxOut2 -> Signal Input2 -> Demod3 X"
RESPONSE_COMPONENT = "X"
ACQUISITION_METHOD = "direct_aw_x"
COMPARISON_JOB_KIND = f"comparison:{EXPERIMENT_ID}"
COMPARISON_FILES = ("full_analysis_comparison.png", "full_analysis_comparison.pdf")


def is_multi_control(config):
    return (isinstance(config, dict) and config.get("experiment_id") == EXPERIMENT_ID
            and config.get("raw_format_version") == 2 and config.get("control_layout") == "controls")


def _supported_layout(config):
    version = config.get("raw_format_version")
    files = config.get("parameters", {}).get("CONTROL_WAVEFORM_FILES")
    single = files is None or isinstance(files, list) and len(files) <= 1
    if version is None:
        return single and not config.get("control_layout") and not config.get("controls")
    return type(version) is int and (
        version == 1 and single and config.get("control_layout") in (None, "single")
        or version == 2 and is_multi_control(config))


def is_new_run(config: object, *, experiment_id=EXPERIMENT_ID, acquisition_method=ACQUISITION_METHOD,
               schema_versions=(2, 3, 4)) -> bool:
    return (
        isinstance(config, dict)
        and config.get("experiment_id") == experiment_id
        and config.get("schema_version") in schema_versions
        and config.get("execution_mode") == "typed_workflow"
        and config.get("acquisition_method") == acquisition_method
        and isinstance(config.get("parameters"), dict)
        and (experiment_id != EXPERIMENT_ID or _supported_layout(config))
    )


def _control_passed(record, relative):
    if not isinstance(record, dict) or record.get("source_file") != relative:
        return False
    phase, steps = record.get("xy_phase_calibration"), record.get("steps")
    return (isinstance(phase, dict) and phase.get("converged") is True
            and isinstance(steps, dict) and isinstance(steps.get("A12"), dict)
            and steps["A12"].get("status") == "completed")


def analysis_block_reason(config: object, *, experiment_id=EXPERIMENT_ID, acquisition_method=ACQUISITION_METHOD,
                          schema_versions=(2, 3, 4)) -> str:
    if not is_new_run(config, experiment_id=experiment_id, acquisition_method=acquisition_method,
                      schema_versions=schema_versions):
        versions = "、".join(map(str, schema_versions))
        return f"仅支持版本 {versions} 的 DirectAW 新格式运行，旧数据不支持分析"
    if config.get("completion_status") != "completed":
        return "采集尚未完整完成，仅可查看诊断"
    if config.get("controls_sealed") is False or is_multi_control(config) and config.get("controls_sealed") is not True:
        return "所选控制尚未完整封存，仅可查看诊断"
    xy_phase = config.get("xy_phase_calibration")
    if config.get("phase_calibration_status") != "completed" or not isinstance(xy_phase, dict) or xy_phase.get("converged") is not True:
        return "校相未通过，仅可查看诊断"
    if experiment_id == EXPERIMENT_ID and is_multi_control(config):
        files = config["parameters"].get("CONTROL_WAVEFORM_FILES")
        controls = config.get("controls", {})
        if not isinstance(files, list) or not files or not all(isinstance(item, str) for item in files) or len(set(files)) != len(files):
            return "多控制运行缺少有效控制清单"
        names = [Path(relative).parent.parent.name for relative in files]
        if (len(set(names)) != len(names) or not isinstance(controls, dict) or set(controls) != set(names)
                or any(not _control_passed(controls[name], relative) for name, relative in zip(names, files))):
            return "部分控制校相未通过或来源不一致，仅可查看诊断"
    return ""


def manual_rf_phase(config: dict) -> float | None:
    """校验显式手动相位来源；旧记录缺开关时仍按扫描处理。"""
    parameters = config.get("parameters", {})
    enabled = parameters.get("Z_PHASE_SCAN_ENABLED", True)
    calibration = config.get("rf_phase_calibration", {})
    if not isinstance(enabled, bool) or not isinstance(calibration, dict):
        raise ValueError("RF 相位模式记录无效")
    if enabled:
        if calibration.get("mode", "scan") != "scan" or calibration.get("scan_skipped", False) is not False:
            raise ValueError("RF 相位扫描开关与来源记录不一致")
        return None
    values = (parameters.get("Z_BURST_PHASE_INIT"), calibration.get("selected_phase_deg"))
    if not all(type(value) in (int, float) and math.isfinite(value) for value in values):
        raise ValueError("手动 RF 相位参数或记录缺失、非有限")
    phase, selected = values[0] % 360.0, values[1]
    if (calibration.get("mode") != "manual" or calibration.get("scan_skipped") is not True
            or type(calibration.get("points_completed")) is not int or calibration["points_completed"] != 0
            or not 0 <= selected < 360 or not math.isclose(selected, phase, rel_tol=0, abs_tol=1e-8)):
        raise ValueError("手动 RF 相位来源记录与参数不一致")
    return phase


def comparison_block_reason(run_dir: Path, config: object, *, experiment_id=EXPERIMENT_ID, acquisition_method=ACQUISITION_METHOD,
                            schema_versions=(2, 3, 4)) -> str:
    reason = analysis_block_reason(config, experiment_id=experiment_id, acquisition_method=acquisition_method, schema_versions=schema_versions)
    if reason:
        return reason
    if config.get("analysis_status") != "completed":
        return "分析尚未完成，请先重新分析"
    directory = Path(run_dir).resolve()
    try:
        multi = experiment_id == EXPERIMENT_ID and is_multi_control(config)
        response_file = "results/response_fits.json" if multi else "results/response_fit.npz"
        required = ["experiment_config.yaml", "results/analysis.yaml", response_file, "results/sensitivity.npz"]
        if multi:
            from .control_analysis import control_inputs
            for _, child, _ in control_inputs(directory, config):
                required.extend(str((child / "results" / name).relative_to(directory))
                                for name in ("analysis.yaml", "response_fit.npz", "sensitivity.npz"))
        for relative in required:
            path = directory / relative
            if directory not in path.resolve().parents:
                return "对比输入必须位于本次运行目录内"
            if not path.is_file():
                return f"缺少 {relative}，请先重新分析"
    except (OSError, ValueError, KeyError, TypeError):
        return "对比结果无法读取，请刷新后重试"
    return ""


def normalize_comparison_runs(runs: list[dict]) -> list[dict]:
    """GUI 与独立比较入口共用选择、路径标识和单行英文图例校验。"""
    if len(runs) < 2:
        raise ValueError("至少选择两次运行进行对比")
    seen = set()
    normalized = []
    for index, item in enumerate(runs):
        run_id = item.get("run_id")
        if not isinstance(run_id, str) or not run_id or run_id in {".", ".."} or run_id.endswith((".", " ")) or any(c in run_id for c in "/\\:\x00"):
            raise ValueError("运行 ID 必须是单个目录名")
        if run_id in seen:
            raise ValueError(f"重复选择运行：{run_id}")
        seen.add(run_id)
        legend = item.get("legend", "")
        if not isinstance(legend, str) or any(ord(c) < 32 or ord(c) == 127 for c in legend) or re.search(r"[\u3400-\u9fff\uf900-\ufaff]", legend):
            raise ValueError("图例须使用单行英文文字、数字或符号")
        default_legend = run_id
        if re.search(r"[\u3400-\u9fff\uf900-\ufaff]", run_id):
            prefix = re.match(r"^(\d{4}_\d{4}(?:\d{2})?)_", run_id)
            default_legend = f"Run {prefix[1] if prefix else index + 1}"
        normalized.append({**item, "legend": legend.strip() or default_legend})
    return normalized
