"""复用 DirectAW 参数，两个 RF 频率独立保存。"""
from dataclasses import dataclass
from typing import ClassVar
from ...experiment_params import parameter
from ..rf_sensitivity_direct_aw.models import RFSensitivityDirectAWParams


@dataclass(slots=True)
class RFSensitivityDirectAWRParams(RFSensitivityDirectAWParams):
    require_rf_integer_cycles: ClassVar[bool] = False
    scan_rf_phase: ClassVar[bool] = False

    run_tag: str = parameter(default="rf_sens_aw_r", external_name="RUN_TAG", label="运行标签", group="basic")
    z_rf_freq: float = parameter(default=3020.0, external_name="Z_RF_FREQ", label="R 响应扫描频率", unit="Hz", group="basic", minimum=1e-6)
    z_rf_noise_freq: float = parameter(default=3000.0, external_name="Z_RF_NOISE_FREQ", label="Demod3 校相 / X 噪声频率", unit="Hz", group="basic", minimum=1e-6)
    z_burst_phase_init: float = parameter(default=0.0, external_name="Z_BURST_PHASE_INIT", label="正 RF Burst 相位", unit="°", description="固定正 RF 相位基准；负幅度加 180°，本实验跳过 A11 相位扫描。")
    z_phase_scan_start_deg: float = parameter(default=0.0, external_name="Z_PHASE_SCAN_START_DEG", label="RF 校相起始相位", visible=False)
    z_phase_scan_stop_deg: float = parameter(default=340.0, external_name="Z_PHASE_SCAN_STOP_DEG", label="RF 校相终止相位", visible=False)
    z_phase_scan_step_deg: float = parameter(default=20.0, external_name="Z_PHASE_SCAN_STEP_DEG", label="RF 校相相位步进", visible=False)
