"""继承 DirectAW 参数，仅增加多控制、倍率及共同分析口径。"""
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal
import numpy as np
from ...experiment_params import parameter
from ..rf_sensitivity_direct_aw.models import RFSensitivityDirectAWSingleParams as RFSensitivityDirectAWParams
from ..rf_sensitivity_aw_known_noise.models import RFSensitivityKnownNoiseParams


@dataclass(slots=True)
class RFControlScaleParams(RFSensitivityDirectAWParams):
    schema_version: ClassVar[int] = 2
    # 加载时保留标称包络，逐点统一乘扫描倍率与补偿，不能应用两次。
    compensate_control_on_load: ClassVar[bool] = False
    scan_rf_phase: bool = parameter(default=True, external_name="Z_PHASE_SCAN_ENABLED", label="扫描 RF 触发相位", group="basic", description="开启时每个控制 × Scale 独立扫描 RF 相位；关闭时 A13 内为所有组合应用同一个手动 RF 相位，其余校相仍逐点执行。")
    run_tag: str = parameter(default="rf_control_scale", external_name="RUN_TAG", label="运行标签", group="basic")
    resume_run_id: str = parameter(default="", external_name="RESUME_RUN_ID", label="续测来源运行 ID", description="留空为完整新测；指定本实验运行 ID 时，新建目录保留已完整采集点，其余点按本轮扫描／手动相位设置重新校相并完整测量。")
    tec_external_control: bool = parameter(default=False, external_name="TEC_EXTERNAL_CONTROL", label="TEC 由外部软件控制", description="启用后完全不连接 TEC 串口、不下发温度；仍正常操作独立温控开关，实际温度未知记 null。")
    arb_waveform_file: str = parameter(default="", external_name="ARB_WAVEFORM_FILE", label="当前控制文件", visible=False)
    control_waveform_files: list[str] = parameter(default_factory=list, external_name="CONTROL_WAVEFORM_FILES", label="控制波形（可多选）", group="basic")
    control_scales: list[float] = parameter(default_factory=lambda: [round(0.9 + i * 0.025, 3) for i in range(9)], external_name="CONTROL_SCALES", label="实际扫描倍率（兼容）", visible=False)
    control_scale_start: float | None = parameter(default=None, external_name="CONTROL_SCALE_START", label="扫描倍率起点", group="basic", description="起点和终点均包含，允许降序、零和负数；不要求包含 1。旧参数未填写三个范围字段时继续使用原倍率列表。")
    control_scale_stop: float | None = parameter(default=None, external_name="CONTROL_SCALE_STOP", label="扫描倍率终点", group="basic")
    control_scale_points: int | None = parameter(default=None, external_name="CONTROL_SCALE_POINTS", label="扫描倍率点数", group="basic", minimum=1, description="正整数；1 点仅使用起点，多点要求起点与终点不同。")
    control_scale_factors: dict[str, float] = parameter(default_factory=dict, external_name="CONTROL_SCALE_FACTORS", label="各控制补偿倍率", group="basic", legacy_default={}, description="按控制名称保存，未设置时为 1；实际倍率 = 扫描倍率 × 补偿倍率。只缩放 XY 完整物理控制包络，图横轴仍为扫描倍率；未选控制的补偿设置保留但不生效。")
    rf_amp_points: int = parameter(default=81, external_name="RF_AMP_POINTS", label="RF 扫描点数", group="basic", minimum=5)
    include_noise: bool = parameter(default=True, external_name="INCLUDE_NOISE", label="采集噪声并计算灵敏度", group="basic")
    linewidth_mode: Literal["frequency_sweep", "dispersive_equivalent"] = parameter(default="frequency_sweep", external_name="LINEWIDTH_MODE", label="线宽模式", visible=False)
    bw_freq_start: float = parameter(default=2000.0, external_name="BW_FREQ_START", label="R 扫频起点", unit="Hz", group="basic", minimum=1e-6, description="A13 每个控制倍率的 R 扫频起点；与终点共同完整包含固定目标 Z_RF_FREQ。")
    slope_fit_points: int = parameter(default=13, external_name="SLOPE_FIT_POINTS", label="旧局部拟合点数（兼容）", visible=False, description="保留历史 GUI/YAML 键；全曲线色散拟合不读取该值。")
    noise_outlier_policy: Literal["mad_high", "none"] = parameter(default="mad_high", external_name="NOISE_OUTLIER_POLICY", label="噪声异常记录处理", options=(("mad_high", "保守剔除组内高值异常"), ("none", "保留全部噪声记录")), description="至少5条时，仅剔除同时超过组内中位数3倍和中位数加6个MAD稳健标准差的单条记录；保留原始数据和剔除清单。")
    sensitivity_band_min_hz: float = parameter(default=3.0, external_name="SENSITIVITY_BAND_MIN_HZ", label="公共统计频带下限", unit="Hz", minimum=1e-9)
    sensitivity_band_max_hz: float = parameter(default=150.0, external_name="SENSITIVITY_BAND_MAX_HZ", label="公共统计频带上限", unit="Hz", minimum=1e-9)

    @classmethod
    def migrate_external(cls, values, schema_version):
        migrated = super(RFControlScaleParams, cls).migrate_external(values, schema_version)
        if "CONTROL_SCALES" in migrated:
            for name in ("CONTROL_SCALE_START", "CONTROL_SCALE_STOP", "CONTROL_SCALE_POINTS"):
                migrated.setdefault(name, None)
        return migrated

    def __post_init__(self):
        super(RFControlScaleParams, self).__post_init__()
        bounds = (self.control_scale_start, self.control_scale_stop, self.control_scale_points)
        if all(value is not None for value in bounds):
            if (type(self.control_scale_points) is not int or self.control_scale_points < 1
                    or not np.isfinite([self.control_scale_start, self.control_scale_stop]).all()):
                raise ValueError("扫描倍率起终点必须有限，CONTROL_SCALE_POINTS 必须是正整数")
            with np.errstate(over="ignore", invalid="ignore"):
                self.control_scales = np.linspace(*bounds).tolist()

    def acquisition_parameters(self):
        """比较实际扫描点与有效补偿，不让旧范围写法或未选控制设置影响续测。"""
        values = self.to_external()
        for name in ("CONTROL_SCALE_START", "CONTROL_SCALE_STOP", "CONTROL_SCALE_POINTS"):
            values.pop(name)
        values["CONTROL_SCALE_FACTORS"] = {
            Path(relative).parent.parent.name: self.control_scale_factor(relative)
            for relative in self.control_waveform_files}
        return values

    @classmethod
    def derive_external(cls, values):
        """只读规范化，供历史填回复用模型的兼容规则。"""
        return {"parameters": cls.from_external(values).to_external()}

    @staticmethod
    def _options(item, root):
        return RFSensitivityKnownNoiseParams._options(item, root)

    def validate_model(self):
        self.arb_waveform_file = self.control_waveform_files[0] if self.control_waveform_files else ""
        errors = super(RFControlScaleParams, self).validate_model()
        if not self.control_waveform_files or len(set(self.control_waveform_files)) != len(self.control_waveform_files):
            errors.append("请勾选至少一个不重复的控制波形")
        bounds = (self.control_scale_start, self.control_scale_stop, self.control_scale_points)
        if any(value is not None for value in bounds) and any(value is None for value in bounds):
            errors.append("扫描倍率起点、终点和点数必须同时填写")
        scales = np.asarray(self.control_scales, dtype=float)
        if (scales.ndim != 1 or not scales.size or not np.isfinite(scales).all()
                or np.unique(scales).size != scales.size):
            errors.append("CONTROL_SCALES 必须是非空、有限且不重复的倍率列表；多点扫描的起终点不能相同")
        if self.linewidth_mode != "frequency_sweep":
            errors.append("本实验必须实测 R 扫频，不能使用色散等效线宽")
        if not self.rf_amp_start < 0 < self.rf_amp_stop:
            errors.append("RF 幅度扫描必须跨越零场")
        if not (0 < self.sensitivity_band_min_hz < self.sensitivity_band_max_hz):
            errors.append("公共统计频带须为递增的正数")
        if self.include_noise and self.sensitivity_band_max_hz >= self.noise_rate / 2:
            errors.append("公共统计频带必须低于噪声请求采样率的 Nyquist")
        return errors
