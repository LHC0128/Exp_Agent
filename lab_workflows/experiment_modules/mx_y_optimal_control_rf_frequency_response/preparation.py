"""来源资格、仪器准备和恢复；不在导入或预检中连接设备。"""
from ...common import load_mapping
from ...control_sources import applied_control_from_corrected
from ...current_feedback import load_corrected_control_waveform
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...experiments.requirements import OPTIMAL_CONTROL
from ...steps import save_corrected_control_source_snapshot, validate_z_trigger_mapping
from ..mx_z_optimal_control_rf_sensitivity.workflow import (
    _connect_control_devices, _configure_common_outputs, safe_shutdown as _safe_shutdown,
)
from .comparison import build_constant_control_plan
from .run_format import EXPERIMENT_ID


def load_sources(params, root):
    corrected = load_corrected_control_waveform(root, params.corrected_control_source_run)
    applied = applied_control_from_corrected(corrected)
    plan = build_constant_control_plan(root,
        calibration_run=params.constant_control_calibration_source_run,
        target_larmor_frequency_hz=params.constant_control_larmor_frequency_hz) if params.comparison_enabled else None
    return corrected, applied, plan


def preflight(params, root):
    validate_mapping_usage(OPTIMAL_CONTROL, root)
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    validate_z_trigger_mapping(mapping)
    require_lockin_compatibility(EXPERIMENT_ID, params.to_external(), mapping=mapping)
    load_sources(params, root)
    return params, mapping


def connect(mapping, session):
    try:
        return _connect_control_devices(mapping, session)
    except BaseException:
        session.cleanup_connection_failure()
        raise


def configure(params, devices, channels, mapping, corrected, applied):
    rate, temperature, clocks, snapshot = _configure_common_outputs(
        params, devices, channels, mapping, corrected, applied)
    return dict(actual_rates={"response_sa_s": rate}, clock_sources=clocks,
        hf2_configuration=snapshot, temperature_control={"target_temperature_c": params.temperature_c,
        "actual_temperature_c": temperature, "controlled_by_experiment": devices.get("tec") is not None,
        "control_source": "tec103" if devices.get("tec") is not None else "external_software"})


def snapshot_source(directory, corrected, applied):
    return save_corrected_control_source_snapshot(directory.raw, corrected, applied)


def restore_optimal(params, devices, channels, corrected, applied):
    from ...z_arbitrary_control import configure_z_optimal_control_output
    configure_z_optimal_control_output(params, devices["z_field"], channels["z_field"], corrected, applied)


def safe_shutdown(devices, channels, params, *, disconnect_tec=True):
    # 分步本轮封存后还可重跑，TEC通信保留到会话结束。
    return _safe_shutdown(devices if disconnect_tec else {**devices, "tec": None}, channels, params)
