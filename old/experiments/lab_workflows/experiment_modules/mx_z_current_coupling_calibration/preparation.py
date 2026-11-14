"""电流耦合预检与 SDS 准备，复用相同接线的 Mx Z 设备职责。"""
from ...common import load_mapping
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ..mx_z_field_calibration.preparation import (
    _connect_devices, _initial_state_snapshot, _configure_reference_clocks,
    configure_initial_outputs, configure_temperature_control, safe_shutdown,
)
from ..mx_z_field_calibration.acquisition import _configure_sense_scope
from .run_format import EXPERIMENT_ID, REQUIRED_MAPPING_KEYS


def preflight(params, root):
    validate_mapping_usage(REQUIRED_MAPPING_KEYS, root)
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    require_lockin_compatibility(EXPERIMENT_ID, params.to_external(), mapping=mapping)
    return params, mapping


def connect_devices(mapping, session):
    return _connect_devices(mapping, session, include_scope=True)


def configure_sense_scope(params, devices):
    if "scope" not in devices:
        raise RuntimeError("电流耦合采集必须连接 SDS")
    return _configure_sense_scope(params, devices)


def require_rf_mode(devices, channels):
    shape = str(devices["xy_field"].get_shape(channel=channels["y_rf"])).strip().strip('"').upper()
    if shape not in ("SIN", "SINE", "SINUSOID"):
        raise ValueError("Y RF 模式已改变，请显式重跑 A07 正弦与解调准备")


def require_scope_mode(devices, snapshot):
    scope = devices["scope"]
    if (str(scope.get_memory_management()).strip().upper() != str(snapshot["actual_memory_management"]).upper()
            or str(scope.get_trigger_mode()).strip().upper() != str(snapshot["trigger_mode"]).upper()):
        raise ValueError("SDS 采集模式已改变，请显式重跑 A08 示波器准备")
