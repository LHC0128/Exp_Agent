"""复用 Mx Y 的设备步骤，集中光功率网格预检、快照与双路恢复。"""
from ...common import load_mapping
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...steps import synchronize_connected_clocks
from ..mx_y_rf_sensitivity.workflow import connect_mx_y_rf_devices, snapshot_mx_y_rf_state
from ..mx_y_rf_sensitivity.preparation import (
    configure_initial_outputs, configure_temperature_control, safe_shutdown)
from ..mx_y_rf_sensitivity.excitation import (
    configure_pump, configure_response, restore_mx_y_rf_laser_powers)
from .run_format import EXPERIMENT_ID, DATA_TYPE, RAW_FORMAT_VERSION, REQUIRED_MAPPING_KEYS


def preflight(params, root):
    validate_mapping_usage(REQUIRED_MAPPING_KEYS, root)
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    require_lockin_compatibility(EXPERIMENT_ID, params.to_external(), mapping=mapping)
    return params, mapping


def synchronize_clocks(devices, mapping):
    return synchronize_connected_clocks(devices, mapping, {
        "z_field": "Z_magnetic_field", "xy_field": "rf_coil", "laser": "Pump_laser_power",
        "pump_rf": "Pump_modulation", "temp_switch": "Temp_Switch", "hf2": "lockin_r"})


def initial_config(params):
    return dict(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, execution_mode="typed_workflow",
        raw_format_version=RAW_FORMAT_VERSION, completion_status="running",
        scan_mode="nested_scan", scan_order="pump_outer_probe_inner_serpentine",
        objective="minimize flat_median_ft_per_sqrt_hz", linewidth_mode="amplitude_equivalent",
        geometry={"main_field": "Z", "pump": "Z", "probe": "X", "rf_field": "Y", "x_field_output": "OFF"},
        compatibility={"reference_experiment_id": "mx-y-rf-sensitivity", "schema_version": params.schema_version},
        acquisition_signals=["R"], power_restore={"pump_power_v": params.pump_laser_power_v,
            "probe_power_v": params.probe_laser_power_v, "outputs_on": True})


def shutdown(devices, channels, pump_params, power_params, *, disconnect_tec=True):
    """任一恢复失败仍尝试其它安全动作，绝不把断开通信当作关闭输出。"""
    errors = []
    try:
        report = safe_shutdown(devices, channels, pump_params, disconnect_tec=disconnect_tec).to_dict()
        errors.extend(report["errors"])
    except Exception as exc:
        report = {"completed": False, "errors": [str(exc)]}
        errors.append(str(exc))
    restore = dict(pump_power_v=power_params.pump_laser_power_v,
        probe_power_v=power_params.probe_laser_power_v, outputs_on=True, success=False, error=None)
    try:
        restore_mx_y_rf_laser_powers(devices, channels,
            pump_power_v=power_params.pump_laser_power_v, probe_power_v=power_params.probe_laser_power_v)
        restore["success"] = True
    except Exception as exc:
        restore["error"] = str(exc)
        errors.append("光功率基准恢复失败：" + str(exc))
    report.update(completed=not errors, errors=errors)
    return {"safety_shutdown": report, "laser_power_restore": restore,
        "device_disconnect": {"tec_disconnected": disconnect_tec and not report.get("disconnect_errors"),
            "other_devices_preserved": True, "errors": report.get("disconnect_errors", [])}}
