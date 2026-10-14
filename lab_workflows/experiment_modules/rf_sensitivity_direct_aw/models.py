"""保留 GUI/YAML 外部键的 DirectAW 参数模型。"""

from __future__ import annotations

from lockin_amplifier import SignalInputConfig
from dataclasses import Field, dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal
import math
import re

from ...common import find_project_root, load_mapping, validate_safety_limit
from ...control_sources import resolve_control_results_root
from ...devices import signal_generator_max_arb_points
from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class RFSensitivityDirectAWParams(ExperimentParams):
    schema_version: ClassVar[int] = 3
    require_rf_integer_cycles: ClassVar[bool] = True
    scan_rf_phase: ClassVar[bool] = True

    system_settle_legacy_defaults: ClassVar[dict[str, float]] = {'RF_STEP_SETTLE_S': 0.3, 'TEMP_OFF_SETTLE_S': 0.5}
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ()
    system_settle_s: float = parameter(default=0.5, external_name="SYSTEM_SETTLE_S", label="系统稳定时间", unit="s", group="basic", minimum=0, description="配置和温控门控共同稳定的采集前等待；组内每次换点或换态后重新等待。")
    run_tag: str = parameter(default="rf_sens_aw", external_name="RUN_TAG", label="运行标签", group="basic")
    rf_amp_start: float = parameter(default=-0.2, external_name="RF_AMP_START", label="RF 起始幅度", unit="Vpp", group="basic", safety_key="Z_magnetic_field")
    rf_amp_stop: float = parameter(default=0.2, external_name="RF_AMP_STOP", label="RF 终止幅度", unit="Vpp", group="basic", safety_key="Z_magnetic_field")
    rf_amp_points: int = parameter(default=81, external_name="RF_AMP_POINTS", label="RF 扫描点数", group="basic", minimum=2)
    temp_group_size: int = parameter(default=5, external_name="TEMP_GROUP_SIZE", label="温控分组点数", group="basic", minimum=1, description="RF 相位/幅度扫描与带宽扫频中每关闭一次温控连续采集的扫描点数；组间恢复温控并按温控恢复后等待。")
    temp_switch_on_lag_s: float = parameter(default=0.5, external_name="TEMP_SWITCH_ON_LAG_S", label="温控恢复后等待", unit="s", group="basic", minimum=0, description="每个温控分组（末组不足分组点数也适用）或其他校相/采集阶段恢复温控后，等待的时间。")
    noise_n_avg: int = parameter(default=10, external_name="NOISE_N_AVG", label="噪声采集次数", group="basic", minimum=1)
    noise_duration: float = parameter(default=1.0, external_name="NOISE_DURATION", label="每次噪声时长", unit="s", group="basic", minimum=1e-6)
    noise_rate: float = parameter(default=100000, external_name="NOISE_RATE", label="请求噪声采样率", unit="Sa/s", minimum=1)
    noise_nperseg: int = parameter(default=10000, external_name="NOISE_NPERSEG", label="Welch 段长", minimum=2)
    arb_waveform_file: str = parameter(default="", external_name="ARB_WAVEFORM_FILE", label="最优控制波形版本", group="basic", description=r"从 D:\Code\theory_agent\simulate\results\oc_sens 选择 vN/waveforms/optimal_control_waveform.csv；时间单位为秒。")
    aw_repeat_freq: float = parameter(default=2000.0, external_name="AW_REPEAT_FREQ", label="AW 重复频率", unit="Hz", group="basic", minimum=1e-6)
    xy_ctrl_k_hz_per_v: float = parameter(default=7508.36, external_name="XY_CTRL_K_HZ_PER_V", label="XY 标定 K", unit="Hz/V")
    xy_ctrl_b_hz: float = parameter(default=-58.77, external_name="XY_CTRL_B_HZ", label="XY 标定 B", unit="Hz")

    xy_ctrl_phase: float = parameter(default=0.0, external_name="XY_CTRL_PHASE", label="XY 初始相位", unit="°")
    xy_ctrl_quad: float = parameter(default=90.0, external_name="XY_CTRL_QUAD", label="XY 正交相差", unit="°")
    xy_ctrl_phase_tol_deg: float = parameter(default=1.0, external_name="XY_CTRL_PHASE_TOL_DEG", label="XY 校相容差", unit="°", minimum=1e-9)
    xy_ctrl_phase_max_iter: int = parameter(default=8, external_name="XY_CTRL_PHASE_MAX_ITER", label="XY 最大相位更新次数", minimum=0)
    xy_ctrl_phase_min_r_v: float = parameter(default=1e-12, external_name="XY_CTRL_PHASE_MIN_R_V", label="XY 校相最低 R", unit="V", minimum=1e-15)
    xy_ctrl_phase_min_r_ratio: float = parameter(default=0.1, external_name="XY_CTRL_PHASE_MIN_R_RATIO", label="XY 最低 R / 最佳 R", minimum=0, maximum=1)

    # GUI 只呈现 XY 键；提交时把共同参数同步到隐藏的 Z 键。
    xy_trigger_freq: float = parameter(default=100.0, external_name="XY_TRIGGER_FREQ", label="XYZ 共同触发频率", unit="Hz", group="basic", minimum=1e-6)
    xy_trigger_amplitude: float = parameter(default=5.0, external_name="XY_TRIGGER_AMPLITUDE", label="共同触发幅度", unit="Vpp", minimum=1e-9)
    xy_trigger_offset: float = parameter(default=2.5, external_name="XY_TRIGGER_OFFSET", label="共同触发偏置", unit="V")
    xy_trigger_duty: float = parameter(default=50.0, external_name="XY_TRIGGER_DUTY", label="共同触发占空比", unit="%", minimum=0.01, maximum=99.99)
    xy_trigger_phase: float = parameter(default=0.0, external_name="XY_TRIGGER_PHASE", label="共同触发相位", unit="°")
    z_trigger_freq: float = parameter(default=100.0, external_name="Z_TRIGGER_FREQ", label="Z 触发频率", visible=False)
    z_trigger_amplitude: float = parameter(default=5.0, external_name="Z_TRIGGER_AMPLITUDE", label="Z 触发幅度", visible=False)
    z_trigger_offset: float = parameter(default=2.5, external_name="Z_TRIGGER_OFFSET", label="Z 触发偏置", visible=False)
    z_trigger_duty: float = parameter(default=50.0, external_name="Z_TRIGGER_DUTY", label="Z 触发占空比", visible=False)
    z_trigger_phase: float = parameter(default=0.0, external_name="Z_TRIGGER_PHASE", label="Z 触发相位", visible=False)
    z_rf_freq: float = parameter(default=8000.0, external_name="Z_RF_FREQ", label="Z RF 频率", unit="Hz", group="basic", minimum=1e-6)
    z_v_to_nt: float = parameter(default=1758.5, external_name="Z_V_TO_NT", label="Z 电压到 RF 场转换", unit="nT/V", group="basic", minimum=1e-12, description="Z 通道电压到射频磁场大小的转换系数，响应磁场轴与灵敏度换算使用该值。")
    z_rf_amplitude_init: float = parameter(default=0.1, external_name="Z_RF_AMPLITUDE_INIT", label="Z 校相幅度", unit="Vpp", minimum=1e-9, safety_key="Z_magnetic_field")
    z_burst_phase_init: float = parameter(default=0.0, external_name="Z_BURST_PHASE_INIT", label="正 RF Burst 相位", unit="°", description="RF 相位扫描前的初始正 RF 相位基准；扫描成功后由选定相位取代。")
    z_phase_scan_start_deg: float = parameter(default=0.0, external_name="Z_PHASE_SCAN_START_DEG", label="RF 校相起始相位", unit="°", minimum=0.0, maximum=360.0)
    z_phase_scan_stop_deg: float = parameter(default=340.0, external_name="Z_PHASE_SCAN_STOP_DEG", label="RF 校相终止相位", unit="°", minimum=0.0, maximum=360.0)
    z_phase_scan_step_deg: float = parameter(default=20.0, external_name="Z_PHASE_SCAN_STEP_DEG", label="RF 校相相位步进", unit="°", minimum=0.001, maximum=360.0)
    scan_point_duration_s: float = parameter(default=0.2, external_name="SCAN_POINT_DURATION_S", label="每点采样时长", unit="s", group="basic", minimum=1e-6, description="RF 相位扫描、幅度扫描与带宽扫频每个扫描点的 DAQ 采样时长；三段扫描共用 RF 解调器响应时间常数、阶数和采样率。")
    linewidth_mode: Literal["frequency_sweep", "dispersive_equivalent"] = parameter(
        default="dispersive_equivalent", external_name="LINEWIDTH_MODE", label="线宽模式", group="basic",
        options=(("frequency_sweep", "扫频测线宽"), ("dispersive_equivalent", "色散等效线宽")),
        description="扫频测线宽时在 A14 零 RF X 噪声采集后执行 A13B，以每点 R 均值做洛伦兹拟合并回补灵敏度谱；色散等效线宽沿用幅度扫描色散拟合的 gamma。")
    bw_freq_start: float = parameter(default=2000.0, external_name="BW_FREQ_START", label="带宽扫频起点", unit="Hz", group="basic", minimum=1e-6, description="A13B 带宽扫频起始频率，须与终点共同包含 Z_RF_FREQ。")
    bw_freq_stop: float = parameter(default=4000.0, external_name="BW_FREQ_STOP", label="带宽扫频终点", unit="Hz", group="basic", minimum=1e-6)
    bw_freq_points: int = parameter(default=21, external_name="BW_FREQ_POINTS", label="带宽扫频点数", group="basic", minimum=5)
    pump_mod_freq: float = parameter(default=10000.0, external_name="PUMP_MOD_FREQ", label="Pump / Larmor 频率", unit="Hz", group="basic", minimum=1e-6)
    pump_mod_duty: float = parameter(default=5.0, external_name="PUMP_MOD_DUTY", label="Pump 门控占空比", unit="%", safety_key="PUMP_MOD_DUTY")
    pump_mod_amplitude: float = parameter(default=0.18, external_name="PUMP_MOD_AMPLITUDE", label="Pump 载波幅度", unit="Vpp", minimum=0, safety_key="Pump_modulation")
    rf_gate_amplitude: float = parameter(default=5.0, external_name="RF_GATE_AMPLITUDE", label="Pump 门控幅度", unit="Vpp", minimum=1e-9)
    rf_gate_offset: float = parameter(default=2.5, external_name="RF_GATE_OFFSET", label="Pump 门控偏置", unit="V")
    demod0_idx: int = parameter(default=0, external_name="DEMOD0_IDX", label="主解调器索引", read_only=True)
    demod0_osc_idx: int = parameter(default=0, external_name="DEMOD0_OSC_IDX", label="主振荡器索引", read_only=True)
    demod0_signal_range: float = parameter(default=2.0, external_name="DEMOD0_SIGNAL_RANGE", label="Input1 量程", unit="V", minimum=1e-6)
    demod0_order: int = parameter(default=4, external_name="DEMOD0_ORDER", label="Demod0 阶数", minimum=1, maximum=8)
    demod0_tc_calib: float = parameter(default=0.001, external_name="DEMOD0_TC_CALIB", label="Demod0 校相时间常数", unit="s", minimum=1e-9)
    demod0_tc_meas: float = parameter(default=1e-6, external_name="DEMOD0_TC_MEAS", label="Demod0 测量时间常数", unit="s", minimum=1e-9)
    demod0_rate: float = parameter(default=100000, external_name="DEMOD0_RATE", label="Demod0 请求采样率", unit="Sa/s", minimum=1)
    demod3_idx: int = parameter(default=3, external_name="DEMOD3_IDX", label="RF 解调器索引", minimum=1, maximum=5, description="从 0 开始计数；不能占用主解调器 0，可用上限按锁相型号和选件检查。")
    demod3_osc_idx: int = parameter(default=1, external_name="DEMOD3_OSC_IDX", label="RF 振荡器索引", read_only=True)
    demod3_adc_select: int = parameter(default=1, external_name="DEMOD3_ADC_SELECT", label="RF 输入节点编码", visible=False, description="历史兼容键，由型号和 RF 输入线路派生。")
    rf_input_route: str = parameter(default="physical", external_name="RF_INPUT_ROUTE", label="RF 输入线路", group="basic", options=(("physical", "物理连接"), ("internal", "内部回采（仅 MFLI）")))
    rf_aux_output_idx: int = parameter(default=1, external_name="RF_AUX_OUTPUT_IDX", label="RF 辅助输出通道", group="basic", options=tuple((idx, f"AuxOut{idx + 1}") for idx in range(4)))
    rf_physical_input_source: str = parameter(default="aux_input1", external_name="RF_PHYSICAL_INPUT_SOURCE", label="RF 物理输入通道", group="basic", options=(("aux_input0", "Aux Input1"), ("aux_input1", "Aux Input2"), ("voltage1", "Signal Input2（仅 HF2）")), description="仅物理线路生效；用 BNC 将所选 AuxOut 接到此输入。内部线路直接读取所选 AuxOut。")
    demod3_order: int = parameter(default=8, external_name="DEMOD3_ORDER", label="RF 解调器响应阶数", minimum=1, maximum=8)
    demod3_tc: float = parameter(default=0.009576194757015027, external_name="DEMOD3_TC", label="RF 解调器响应时间常数", unit="s", minimum=1e-9)
    demod3_rate: float = parameter(default=1000, external_name="DEMOD3_RATE", label="RF 解调器响应请求采样率", unit="Sa/s", minimum=1)
    demod3_noise_tc: float = parameter(default=1e-5, external_name="DEMOD3_NOISE_TC", label="RF 解调器噪声时间常数", unit="s", minimum=1e-9)
    demod3_noise_order: int = parameter(default=4, external_name="DEMOD3_NOISE_ORDER", label="RF 解调器噪声阶数", minimum=1, maximum=8)
    loopback_aux_scale: float = parameter(default=2.2044316004030406, external_name="LOOPBACK_AUX_SCALE", label="辅助输出回环增益")
    loopback_aux_offset_v: float = parameter(default=0.0, external_name="LOOPBACK_AUX_OFFSET_V", label="辅助输出回环偏置", unit="V", minimum=-10, maximum=10)
    loopback_input_range_v: float = parameter(default=1.8160198402606689, external_name="LOOPBACK_INPUT_RANGE_V", label="Signal Input2 回环量程", unit="V", minimum=1e-6, description="仅 HF2 物理线路选择 Signal Input2 时生效。")
    loopback_input_ac: bool = parameter(default=False, external_name="LOOPBACK_INPUT_AC", label="Input2 AC 耦合（关闭为 DC）", visible=False)
    loopback_input_impedance_ohm: int = parameter(default=1000000, external_name="LOOPBACK_INPUT_IMPEDANCE_OHM", label="Input2 阻抗", unit="Ω", options=((1000000, "1 MΩ"), (50, "50 Ω")), visible=False)
    loopback_input_diff: bool = parameter(default=False, external_name="LOOPBACK_INPUT_DIFF", label="Signal Input2 差分（关闭为单端）", description="仅 HF2 物理线路选择 Signal Input2 时生效。")
    pump_laser_power: float = parameter(default=0.5, external_name="FIXED_PARAMS.Pump_laser_power", label="Pump 光功率", unit="V", group="basic", safety_key="Pump_laser_power")
    probe_laser_power: float = parameter(default=0.3, external_name="FIXED_PARAMS.Probe_laser_power", label="Probe 光功率", unit="V", group="basic", safety_key="Probe_laser_power")
    main_magnetic_field: float = parameter(default=1.03, external_name="FIXED_PARAMS.main_magnetic_field", label="主磁场电流", unit="mA", group="basic", safety_key="main_magnetic_field")
    temperature: float = parameter(default=100.0, external_name="FIXED_PARAMS.temperature", label="Cell 温度", unit="°C", group="basic", safety_key="temperature")
    temp_switch: float = parameter(default=5.0, external_name="FIXED_PARAMS.Temp_Switch", label="温控开启电平", unit="V", read_only=True, safety_key="Temp_Switch")

    def __post_init__(self):
        # 历史参数键仍可读取，输入模式统一使用公共关闭设置。
        defaults = SignalInputConfig()
        self.loopback_input_ac = defaults.ac_coupling
        self.loopback_input_impedance_ohm = defaults.impedance

    @property
    def rf_input_source(self) -> str:
        return f"aux_output{self.rf_aux_output_idx}" if self.rf_input_route == "internal" else self.rf_physical_input_source

    def signal_chain(self, component: str = "X") -> str:
        output = f"AuxOut{self.rf_aux_output_idx + 1}"
        destination = "internal" if self.rf_input_route == "internal" else {
            "voltage1": "Signal Input2", "aux_input0": "Aux Input1", "aux_input1": "Aux Input2",
        }[self.rf_physical_input_source]
        return f"Demod0 Y -> {output} -> {destination} -> RF demodulator (index {self.demod3_idx}) {component} calibration / X noise"

    @staticmethod
    def _options(item: Field, root: Path) -> list[tuple]:
        if item.name != "arb_waveform_file":
            return ExperimentParams._options(item, root)
        directory = resolve_control_results_root("oc_sens").resolve()
        versions = [path for path in directory.glob("v*") if re.fullmatch(r"v[1-9]\d*", path.name) and path.is_dir()]
        choices = [("", "请选择最优控制版本")]
        for version in sorted(versions, key=lambda path: int(path.name[1:])):
            waveform = version / "waveforms" / "optimal_control_waveform.csv"
            if waveform.is_file() and directory in waveform.resolve().parents:
                choices.append((waveform.relative_to(directory).as_posix(), version.name))
        return choices

    def validate_model(self) -> list[str]:
        errors = []
        if not self.arb_waveform_file:
            errors.append("请选择 oc_sens 中的最优控制波形版本")
        if not self.run_tag or any(char in self.run_tag for char in ("/", "\\", "\0")) or self.run_tag in (".", ".."):
            errors.append("RUN_TAG 必须是安全的单层目录标签")
        if self.rf_amp_start >= self.rf_amp_stop:
            errors.append("RF_AMP_START 必须小于 RF_AMP_STOP")
        if self.xy_ctrl_k_hz_per_v == 0 or self.loopback_aux_scale == 0:
            errors.append("XY 标定 K 和回环增益不能为 0")
        for suffix in ("freq", "amplitude", "offset", "duty", "phase"):
            if getattr(self, f"xy_trigger_{suffix}") != getattr(self, f"z_trigger_{suffix}"):
                errors.append(f"共同触发的 XY_TRIGGER_{suffix.upper()} 与 Z_TRIGGER_{suffix.upper()} 必须一致")
        if self.xy_trigger_freq > 0:
            frequencies = [("AW_REPEAT_FREQ", self.aw_repeat_freq)]
            if self.require_rf_integer_cycles:
                frequencies.append(("Z_RF_FREQ", self.z_rf_freq))
            for key, value in frequencies:
                cycles = value / self.xy_trigger_freq
                if not math.isfinite(cycles) or cycles < 1 or abs(cycles - round(cycles)) > 1e-6:
                    errors.append(f"{key} 必须是共同触发频率的正整数倍")
        for key, actual, fixed in (("DEMOD0_IDX", self.demod0_idx, 0), ("DEMOD0_OSC_IDX", self.demod0_osc_idx, 0), ("DEMOD3_OSC_IDX", self.demod3_osc_idx, 1), ("FIXED_PARAMS.Temp_Switch", self.temp_switch, 5)):
            if actual != fixed:
                errors.append(f"{key} 固定为 {fixed}")
        if not self.scan_rf_phase:
            pass
        elif self.z_phase_scan_start_deg >= self.z_phase_scan_stop_deg:
            errors.append("Z_PHASE_SCAN_START_DEG 必须小于 Z_PHASE_SCAN_STOP_DEG")
        else:
            intervals = (self.z_phase_scan_stop_deg - self.z_phase_scan_start_deg) / self.z_phase_scan_step_deg
            if not math.isclose(intervals, round(intervals), abs_tol=1e-10):
                errors.append("RF 校相扫描范围必须被相位步进整除")
            else:
                points = int(round(intervals)) + 1
                if points < 6 or points % 2 or not math.isclose(points * self.z_phase_scan_step_deg, 360.0):
                    errors.append("RF 相位扫描必须以至少 6 个偶数点恰好覆盖一个 360° 周期")
        if self.linewidth_mode == "frequency_sweep":
            if self.bw_freq_start >= self.bw_freq_stop:
                errors.append("BW_FREQ_START 必须小于 BW_FREQ_STOP")
            elif not self.bw_freq_start < self.z_rf_freq < self.bw_freq_stop:
                errors.append("带宽扫频窗口必须完整包含 Z_RF_FREQ")
            if self.xy_trigger_freq > 0 and self.require_rf_integer_cycles:
                for key, value in (("BW_FREQ_START", self.bw_freq_start), ("BW_FREQ_STOP", self.bw_freq_stop)):
                    cycles = value / self.xy_trigger_freq
                    if not math.isfinite(cycles) or cycles < 1 or abs(cycles - round(cycles)) > 1e-6:
                        errors.append(f"{key} 必须是共同触发频率的正整数倍")
        for key, amplitude, offset in (("Time_sequence_2", self.xy_trigger_amplitude, self.xy_trigger_offset), ("Time_sequence", self.rf_gate_amplitude, self.rf_gate_offset)):
            for level in (offset - amplitude / 2, offset + amplitude / 2):
                try:
                    validate_safety_limit(key, level)
                except ValueError as exc:
                    errors.append(str(exc))
        return errors

    @classmethod
    def migrate_external(cls, values: dict[str, Any], schema_version: int) -> dict[str, Any]:
        """AW 满量程自动选择；响应采集改为定长 DAQ，剥离/迁移旧稳定等待与平均键。"""
        migrated = super(RFSensitivityDirectAWParams, cls).migrate_external(values, schema_version)
        if not any(key in migrated for key in ("RF_INPUT_ROUTE", "RF_AUX_OUTPUT_IDX", "RF_PHYSICAL_INPUT_SOURCE")):
            # 旧参数保存的是 HF2 AuxOut2 到 Signal Input2，不能套用新默认接线。
            migrated.update(RF_INPUT_ROUTE="physical", RF_AUX_OUTPUT_IDX=1, RF_PHYSICAL_INPUT_SOURCE="voltage1")
        for key in ("XY_AW_OUTPUT_VPP", "XY_AW_OUTPUT_OFFSET", "RESPONSE_N_AVG", "RESPONSE_AVG_INTERVAL"):
            migrated.pop(key, None)
        for old, new in (("RF_SETTLE_TIME", "RF_STEP_SETTLE_S"), ("XY_PHASE_SETTLE_TIME", "TEMP_OFF_SETTLE_S"), ("Z_PHASE_SCAN_DURATION_S", "SCAN_POINT_DURATION_S")):
            if old in migrated:
                migrated[new] = migrated.pop(old)
        return migrated

    def validate(self, project_root: Path | None = None) -> list[str]:
        root = project_root or find_project_root()
        errors = super(RFSensitivityDirectAWParams, self).validate(root)
        if errors:
            return errors
        from .waveform import load_envelope, synthesize_xy
        try:
            mapping = load_mapping(root)
            max_points = min(signal_generator_max_arb_points(mapping[key]) for key in ("X_magnetic_field", "Y_magnetic_field"))
            envelope = load_envelope(self, max_points)
            # 满量程自动跟随包络峰值；下限与安全限值检查均在 synthesize_xy 内完成。
            synthesize_xy(self, envelope, self.xy_ctrl_phase)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            errors.append(str(exc))
        return errors
