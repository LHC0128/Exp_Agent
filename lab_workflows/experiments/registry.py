"""正式 Python 实验的统一注册表。"""

from __future__ import annotations

from ..experiment_modules.bb_power_scan_burst.definition import DEFINITION as BB_POWER_SCAN_BURST_DEFINITION
from ..experiment_modules.rf_sensitivity_constxy_frequency.definition import DEFINITION as RF_CONSTXY_FREQUENCY_DEFINITION
from ..experiment_modules.bb_duty_scan_burst.definition import DEFINITION as BB_DUTY_SCAN_BURST_DEFINITION
from ..experiment_modules.coil_drive_characterization.definition import DEFINITION as COIL_DRIVE_CHARACTERIZATION_DEFINITION
from ..experiment_modules.z_field_bandwidth.definition import DEFINITION as Z_FIELD_BANDWIDTH_DEFINITION
from ..experiment_modules.rf_sensitivity_direct_aw.definition import (
    DEFINITION as RF_SENSITIVITY_DIRECT_AW_DEFINITION,
)
from ..experiment_modules.rf_sensitivity_aw_known_noise.definition import DEFINITION as RF_KNOWN_NOISE_DEFINITION
from ..experiment_modules.rf_control_scale_sensitivity.definition import DEFINITION as RF_CONTROL_SCALE_DEFINITION
from ..experiment_modules.scope_capture.definition import DEFINITION as SCOPE_CAPTURE_DEFINITION
from ..experiment_modules.noise_spectrum_xy.definition import (
    DEFINITION as NOISE_SPECTRUM_XY_DEFINITION,
)
from ..experiment_modules.noise_spectrum_xy_known_noise.definition import (
    DEFINITION as NOISE_SPECTRUM_XY_KNOWN_NOISE_DEFINITION,
)
from ..experiment_modules.noise_spectrum_xy_probe_am.definition import (
    DEFINITION as NOISE_SPECTRUM_XY_PROBE_AM_DEFINITION,
)
from ..experiment_modules.noise_spectrum_xy_demod3_r.definition import (
    DEFINITION as NOISE_SPECTRUM_XY_DEMOD3_R_DEFINITION,
)
from ..experiment_modules.photon_shot_noise.definition import DEFINITION as PHOTON_SHOT_NOISE_DEFINITION
from ..experiment_modules.projection_noise.definition import (
    DEFINITION as PROJECTION_NOISE_DEFINITION,
)
from ..experiment_modules.mx_y_rf_sensitivity.definition import (
    DEFINITION as MX_Y_RF_SENSITIVITY_DEFINITION,
)
from ..experiment_modules.mx_y_rf_frequency_response.definition import (
    DEFINITION as MX_Y_RF_FREQUENCY_RESPONSE_DEFINITION,
)
from ..experiment_modules.mx_y_optimal_control_rf_frequency_response.definition import (
    DEFINITION as MX_Y_OPTIMAL_CONTROL_RF_FREQUENCY_RESPONSE_DEFINITION,
)
from ..experiment_modules.mx_z_optimal_control_rf_sensitivity.definition import (
    DEFINITION as MX_Z_OPTIMAL_CONTROL_RF_SENSITIVITY_DEFINITION,
)
from ..experiment_modules.mx_y_rf_power_optimization.definition import (
    DEFINITION as MX_Y_RF_POWER_OPTIMIZATION_DEFINITION,
)
from ..experiment_modules.mx_y_rf_probe_detuning_optimization.definition import (
    DEFINITION as MX_Y_RF_PROBE_DETUNING_OPTIMIZATION_DEFINITION,
)
from ..experiment_modules.mx_main_field_calibration.definition import (
    DEFINITION as MX_MAIN_FIELD_CALIBRATION_DEFINITION,
)
from ..experiment_modules.mx_main_field_scope_noise_spectrum.definition import (
    DEFINITION as MX_MAIN_FIELD_SCOPE_NOISE_SPECTRUM_DEFINITION,
)
from ..experiment_modules.mx_z_field_calibration.definition import (
    DEFINITION as MX_Z_FIELD_CALIBRATION_DEFINITION,
)
from ..experiment_modules.rf_sensitivity_direct_aw_frequency.definition import (
    DEFINITION as RF_DIRECT_AW_FREQUENCY_DEFINITION,
)
from ..experiment_modules.t1_calibration.definition import DEFINITION as T1_CALIBRATION_DEFINITION
from ..experiment_modules.t2_calibration.definition import (
    DEFINITION as T2_CALIBRATION_DEFINITION,
)
from ..experiment_modules.xy_direct_aw_dc_calibration.definition import (
    DEFINITION as XY_DIRECT_AW_DC_DEFINITION,
)
from ..experiment_modules.static_sensitivity.definition import (
    DEFINITION as STATIC_SENSITIVITY_DEFINITION,
)
from .contracts import ExperimentDefinition
from .archived import ARCHIVED_EXPERIMENTS, load_archived_experiment
from ..experiment_modules.bell_bloom_z_field_calibration.definition import DEFINITION as BELL_BLOOM_Z_FIELD_CALIBRATION_DEFINITION
from ..experiment_modules.detection_chain_frequency_response.definition import (
    DEFINITION as DETECTION_CHAIN_FREQUENCY_RESPONSE_DEFINITION,
)


_DEFINITIONS = [
    BELL_BLOOM_Z_FIELD_CALIBRATION_DEFINITION,
    DETECTION_CHAIN_FREQUENCY_RESPONSE_DEFINITION,
    SCOPE_CAPTURE_DEFINITION,
    STATIC_SENSITIVITY_DEFINITION,
    MX_Y_RF_SENSITIVITY_DEFINITION,
    MX_Y_RF_FREQUENCY_RESPONSE_DEFINITION,
    MX_Y_OPTIMAL_CONTROL_RF_FREQUENCY_RESPONSE_DEFINITION,
    MX_Z_OPTIMAL_CONTROL_RF_SENSITIVITY_DEFINITION,
    COIL_DRIVE_CHARACTERIZATION_DEFINITION,
    MX_Y_RF_POWER_OPTIMIZATION_DEFINITION,
    MX_Y_RF_PROBE_DETUNING_OPTIMIZATION_DEFINITION,
    MX_MAIN_FIELD_CALIBRATION_DEFINITION,
    MX_MAIN_FIELD_SCOPE_NOISE_SPECTRUM_DEFINITION,
    MX_Z_FIELD_CALIBRATION_DEFINITION,
    NOISE_SPECTRUM_XY_DEFINITION,
    NOISE_SPECTRUM_XY_KNOWN_NOISE_DEFINITION,
    NOISE_SPECTRUM_XY_PROBE_AM_DEFINITION,
    NOISE_SPECTRUM_XY_DEMOD3_R_DEFINITION,
    PHOTON_SHOT_NOISE_DEFINITION,
    PROJECTION_NOISE_DEFINITION,
    RF_SENSITIVITY_DIRECT_AW_DEFINITION,
    RF_KNOWN_NOISE_DEFINITION,
    RF_CONTROL_SCALE_DEFINITION,
    RF_DIRECT_AW_FREQUENCY_DEFINITION,
    RF_CONSTXY_FREQUENCY_DEFINITION,
    Z_FIELD_BANDWIDTH_DEFINITION,
    BB_DUTY_SCAN_BURST_DEFINITION,
    BB_POWER_SCAN_BURST_DEFINITION,
    T1_CALIBRATION_DEFINITION,
    T2_CALIBRATION_DEFINITION,
    XY_DIRECT_AW_DC_DEFINITION,
]

_REGISTRY = {definition.id: definition for definition in _DEFINITIONS}
if len(_REGISTRY) != len(_DEFINITIONS):
    raise RuntimeError("实验注册表中存在重复 ID")


def list_experiments(*, include_archived: bool = False) -> list[ExperimentDefinition]:
    """默认返回在维护的正式实验；目录可附加不加载实现的归档卡片。"""
    return [*_DEFINITIONS, *ARCHIVED_EXPERIMENTS.values()] if include_archived else list(_DEFINITIONS)


def get_experiment(experiment_id: str) -> ExperimentDefinition:
    """按稳定 ID 获取实验定义。"""
    try:
        return _REGISTRY[experiment_id]
    except KeyError as exc:
        if experiment_id in ARCHIVED_EXPERIMENTS:
            return load_archived_experiment(experiment_id)
        raise KeyError(f"未知实验: {experiment_id}") from exc
