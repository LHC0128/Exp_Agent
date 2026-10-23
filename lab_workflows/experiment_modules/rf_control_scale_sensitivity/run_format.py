"""区分历史标称相位（v1）与逐点校相／RF 相位设置（v2），不改写历史数据。"""
import math
from ..rf_sensitivity_direct_aw.run_format import manual_rf_phase

EXPERIMENT_ID = "rf-control-scale-sensitivity"
DATA_TYPE = "RF_Control_Scale_Sensitivity"
ACQUISITION_METHOD = "direct_aw_control_scale"
RESPONSE_COMPONENT = "X"
RAW_FORMAT_VERSION = 2
PHASE_CALIBRATION_SCOPE = "per_control_scale"
POINT_CALIBRATION_STAGES = ("A08", "A09", "A10", "A11", "A12")


def calibration_scope(config):
    return "nominal_control" if config.get("raw_format_version") == 1 else PHASE_CALIBRATION_SCOPE


def format_block_reason(config):
    if (not isinstance(config, dict) or config.get("experiment_id") != EXPERIMENT_ID
            or config.get("raw_format_version") not in (1, RAW_FORMAT_VERSION)
            or config.get("execution_mode") != "typed_workflow"
            or config.get("acquisition_method") != ACQUISITION_METHOD
            or not isinstance(config.get("parameters"), dict)):
        return "不是本实验已知的原始数据格式"
    if config["raw_format_version"] == 2 and config.get("phase_calibration_scope") != PHASE_CALIBRATION_SCOPE:
        return "v2 原始数据缺少逐控制 × Scale 校相标记"
    return ""


def analysis_block_reason(config):
    if reason := format_block_reason(config):
        return reason
    if config.get("completion_status") != "completed":
        return "采集尚未完整封存，仅可查看诊断"
    if config.get("phase_calibration_status") != "completed":
        return "标称倍率校相未通过" if config["raw_format_version"] == 1 else "逐控制 × Scale 校相未完成"
    return ""


def point_calibration_block_reason(config):
    expected = 1.0 if config["raw_format_version"] == 1 else config.get("control_scale")
    if expected is None or config.get("calibration_scale") != expected:
        return "子运行倍率与实际校相倍率不一致"
    if config["raw_format_version"] == 1:
        return ""
    if config.get("phase_calibration_status") != "completed":
        return "当前点校相未完成或 RF 相位设置未完成"
    steps = config.get("phase_calibration_steps", {})
    if not isinstance(steps, dict) or any(not isinstance(steps.get(key), dict) or steps[key].get("status") != "completed" for key in POINT_CALIBRATION_STAGES):
        return "当前控制 × Scale 的校相与 RF 相位设置记录未通过"
    try:
        manual_rf_phase(config)
        values = [config["calibration"][name]["phase_shift_deg"] for name in ("demod0", "demod3")]
        values += [config["xy_phase_calibration"]["final_phase_deg"], config["rf_phase_calibration"]["selected_phase_deg"]]
        valid = all(not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) for value in values)
        valid = valid and config["xy_phase_calibration"]["converged"] is True and config.get("calibration_source") == "."
    except (KeyError, TypeError, ValueError):
        valid = False
    return "" if valid else "每点独立校相结果缺失、非有限或来自其他倍率"
