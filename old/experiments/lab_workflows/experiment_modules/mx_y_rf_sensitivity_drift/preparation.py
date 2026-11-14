"""长飘准备复用标准单轮的设备、严格时钟、初态与安全策略。"""
from ...common import load_mapping
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ..mx_y_rf_sensitivity.preparation import (
    _connect_devices, _initial_state_snapshot, _configure_outputs, _configure_reference_clocks,
    configure_initial_outputs, configure_temperature_control, safe_shutdown,
)
from ..mx_y_rf_sensitivity.excitation import configure_pump, configure_response, _restore_response_acquisition_state
from .run_format import EXPERIMENT_ID, REQUIRED_MAPPING_KEYS


def preflight(params, root):
    validate_mapping_usage(REQUIRED_MAPPING_KEYS, root)
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    require_lockin_compatibility(EXPERIMENT_ID, params.to_external(), mapping=mapping)
    return params, mapping
