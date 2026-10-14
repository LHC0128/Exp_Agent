"""复用 DirectAW 参数，两个 RF 频率独立保存。"""
from dataclasses import dataclass
from typing import ClassVar, Literal
from ...experiment_params import parameter
from ..rf_sensitivity_direct_aw.models import RFSensitivityDirectAWParams


@dataclass(slots=True)
class RFSensitivityDirectAWRParams(RFSensitivityDirectAWParams):
    require_rf_integer_cycles: ClassVar[bool] = False
    scan_rf_phase: ClassVar[bool] = False

    run_tag: str = parameter(default="rf_sens_aw_r", external_name="RUN_TAG", label="运行标签", group="basic")
    z_rf_freq: float = parameter(default=3020.0, external_name="Z_RF_FREQ", label="R 响应扫描频率", unit="Hz", group="basic", minimum=1e-6)
    z_rf_noise_freq: float = parameter(default=3000.0, external_name="Z_RF_NOISE_FREQ", label="RF 解调器校相 / X 噪声频率", unit="Hz", group="basic", minimum=1e-6)
    z_burst_phase_init: float = parameter(default=0.0, external_name="Z_BURST_PHASE_INIT", label="正 RF Burst 相位", unit="°", description="固定正 RF 相位基准；负幅度加 180°，本实验跳过 A11 相位扫描。")
    z_phase_scan_start_deg: float = parameter(default=0.0, external_name="Z_PHASE_SCAN_START_DEG", label="RF 校相起始相位", visible=False)
    z_phase_scan_stop_deg: float = parameter(default=340.0, external_name="Z_PHASE_SCAN_STOP_DEG", label="RF 校相终止相位", visible=False)
    z_phase_scan_step_deg: float = parameter(default=20.0, external_name="Z_PHASE_SCAN_STEP_DEG", label="RF 校相相位步进", visible=False)
    # R 变体无 A13B 带宽扫频节点，线宽模式相关参数隐藏且固定色散等效。
    linewidth_mode: Literal["frequency_sweep", "dispersive_equivalent"] = parameter(
        default="dispersive_equivalent", external_name="LINEWIDTH_MODE", label="线宽模式", visible=False)
    bw_freq_start: float = parameter(default=2000.0, external_name="BW_FREQ_START", label="带宽扫频起点", visible=False)
    bw_freq_stop: float = parameter(default=4000.0, external_name="BW_FREQ_STOP", label="带宽扫频终点", visible=False)
    bw_freq_points: int = parameter(default=21, external_name="BW_FREQ_POINTS", label="带宽扫频点数", visible=False)

    def validate_model(self) -> list[str]:
        errors = super(RFSensitivityDirectAWRParams, self).validate_model()
        if self.linewidth_mode == "frequency_sweep":
            errors.append("R 变体无带宽扫频节点，LINEWIDTH_MODE 必须为色散等效线宽")
        return errors
