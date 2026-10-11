"""保留 GUI/YAML 外部键的 DirectAW 参数模型。"""

from __future__ import annotations

from dataclasses import Field, dataclass
from pathlib import Path
from typing import Any, ClassVar
import math
import re

from ...common import find_project_root, load_mapping, validate_safety_limit
from ...control_sources import resolve_control_results_root
from ...devices import signal_generator_max_arb_points
from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class RFSensitivityDirectAWParams(ExperimentParams):
    schema_version: ClassVar[int] = 2

    run_tag: str = parameter(default="rf_sens_aw", external_name="RUN_TAG", label="运行标签", group="basic")
    rf_amp_start: float = parameter(default=-0.2, external_name="RF_AMP_START", label="RF 起始幅度", unit="Vpp", group="basic", safety_key="Z_magnetic_field")
    rf_amp_stop: float = parameter(default=0.2, external_name="RF_AMP_STOP", label="RF 终止幅度", unit="Vpp", group="basic", safety_key="Z_magnetic_field")
    rf_amp_points: int = parameter(default=81, external_name="RF_AMP_POINTS", label="RF 扫描点数", group="basic", minimum=2)
    rf_settle_time: float = parameter(default=0.3, external_name="RF_SETTLE_TIME", label="RF 稳定等待", unit="s", minimum=0)
    response_n_avg: int = parameter(default=5, external_name="RESPONSE_N_AVG", label="每点响应平均次数", minimum=1)
    response_avg_interval: float = parameter(default=0.2, external_name="RESPONSE_AVG_INTERVAL", label="响应读取间隔", unit="s", minimum=0)
    temp_switch_on_lag_s: float = parameter(default=0.5, external_name="TEMP_SWITCH_ON_LAG_S", label="温控恢复后等待", unit="s", group="basic", minimum=0, description="每次恢复温控（Temp_Switch 置 5 V）后到下一步骤的等待时间，适用于校相、RF 相位扫描、响应和噪声的全部恢复点。")
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
    xy_phase_settle_time: float = parameter(default=0.8, external_name="XY_PHASE_SETTLE_TIME", label="XY 校相稳定等待", unit="s", minimum=0)

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
    z_phase_scan_duration_s: float = parameter(default=0.2, external_name="Z_PHASE_SCAN_DURATION_S", label="RF 校相每点采样时长", unit="s", minimum=1e-6)
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
    demod3_idx: int = parameter(default=3, external_name="DEMOD3_IDX", label="RF 解调器索引", read_only=True)
    demod3_osc_idx: int = parameter(default=1, external_name="DEMOD3_OSC_IDX", label="RF 振荡器索引", read_only=True)
    demod3_adc_select: int = parameter(default=1, external_name="DEMOD3_ADC_SELECT", label="Demod3 输入（Signal Input2）", read_only=True)
    demod3_order: int = parameter(default=8, external_name="DEMOD3_ORDER", label="Demod3 响应阶数", minimum=1, maximum=8)
    demod3_tc: float = parameter(default=0.009576194757015027, external_name="DEMOD3_TC", label="Demod3 响应时间常数", unit="s", minimum=1e-9)
    demod3_rate: float = parameter(default=1000, external_name="DEMOD3_RATE", label="Demod3 响应请求采样率", unit="Sa/s", minimum=1)
    demod3_noise_tc: float = parameter(default=1e-5, external_name="DEMOD3_NOISE_TC", label="Demod3 噪声时间常数", unit="s", minimum=1e-9)
    demod3_noise_order: int = parameter(default=4, external_name="DEMOD3_NOISE_ORDER", label="Demod3 噪声阶数", minimum=1, maximum=8)
    loopback_aux_scale: float = parameter(default=2.2044316004030406, external_name="LOOPBACK_AUX_SCALE", label="AuxOut2 回环增益")
    loopback_aux_offset_v: float = parameter(default=0.0, external_name="LOOPBACK_AUX_OFFSET_V", label="AuxOut2 回环偏置", unit="V", minimum=-10, maximum=10)
    loopback_input_range_v: float = parameter(default=1.8160198402606689, external_name="LOOPBACK_INPUT_RANGE_V", label="Input2 回环量程", unit="V", minimum=1e-6)
    loopback_input_ac: bool = parameter(default=False, external_name="LOOPBACK_INPUT_AC", label="Input2 AC 耦合（关闭为 DC）")
    loopback_input_impedance_ohm: int = parameter(default=1000000, external_name="LOOPBACK_INPUT_IMPEDANCE_OHM", label="Input2 阻抗", unit="Ω", options=((1000000, "1 MΩ"), (50, "50 Ω")))
    loopback_input_diff: bool = parameter(default=False, external_name="LOOPBACK_INPUT_DIFF", label="Input2 差分（关闭为单端）")
    pump_laser_power: float = parameter(default=0.5, external_name="FIXED_PARAMS.Pump_laser_power", label="Pump 光功率", unit="V", group="basic", safety_key="Pump_laser_power")
    probe_laser_power: float = parameter(default=0.3, external_name="FIXED_PARAMS.Probe_laser_power", label="Probe 光功率", unit="V", group="basic", safety_key="Probe_laser_power")
    main_magnetic_field: float = parameter(default=1.03, external_name="FIXED_PARAMS.main_magnetic_field", label="主磁场电流", unit="mA", group="basic", safety_key="main_magnetic_field")
    temperature: float = parameter(default=100.0, external_name="FIXED_PARAMS.temperature", label="Cell 温度", unit="°C", group="basic", safety_key="temperature")
    temp_switch: float = parameter(default=5.0, external_name="FIXED_PARAMS.Temp_Switch", label="温控开启电平", unit="V", read_only=True, safety_key="Temp_Switch")

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
            for key, value in (("AW_REPEAT_FREQ", self.aw_repeat_freq), ("Z_RF_FREQ", self.z_rf_freq)):
                cycles = value / self.xy_trigger_freq
                if not math.isfinite(cycles) or cycles < 1 or abs(cycles - round(cycles)) > 1e-6:
                    errors.append(f"{key} 必须是共同触发频率的正整数倍")
        for key, actual, fixed in (("DEMOD0_IDX", self.demod0_idx, 0), ("DEMOD0_OSC_IDX", self.demod0_osc_idx, 0), ("DEMOD3_IDX", self.demod3_idx, 3), ("DEMOD3_OSC_IDX", self.demod3_osc_idx, 1), ("DEMOD3_ADC_SELECT", self.demod3_adc_select, 1), ("FIXED_PARAMS.Temp_Switch", self.temp_switch, 5)):
            if actual != fixed:
                errors.append(f"{key} 固定为 {fixed}")
        if self.z_phase_scan_start_deg >= self.z_phase_scan_stop_deg:
            errors.append("Z_PHASE_SCAN_START_DEG 必须小于 Z_PHASE_SCAN_STOP_DEG")
        else:
            intervals = (self.z_phase_scan_stop_deg - self.z_phase_scan_start_deg) / self.z_phase_scan_step_deg
            if not math.isclose(intervals, round(intervals), abs_tol=1e-10):
                errors.append("RF 校相扫描范围必须被相位步进整除")
            else:
                points = int(round(intervals)) + 1
                if points < 6 or points % 2 or not math.isclose(points * self.z_phase_scan_step_deg, 360.0):
                    errors.append("RF 相位扫描必须以至少 6 个偶数点恰好覆盖一个 360° 周期")
        for key, amplitude, offset in (("Time_sequence_2", self.xy_trigger_amplitude, self.xy_trigger_offset), ("Time_sequence", self.rf_gate_amplitude, self.rf_gate_offset)):
            for level in (offset - amplitude / 2, offset + amplitude / 2):
                try:
                    validate_safety_limit(key, level)
                except ValueError as exc:
                    errors.append(str(exc))
        return errors

    @classmethod
    def migrate_external(cls, values: dict[str, Any], schema_version: int) -> dict[str, Any]:
        """AW 满量程已改为随包络峰值自动选择，剥离旧固定幅度/偏置键。"""
        migrated = super(RFSensitivityDirectAWParams, cls).migrate_external(values, schema_version)
        for key in ("XY_AW_OUTPUT_VPP", "XY_AW_OUTPUT_OFFSET"):
            migrated.pop(key, None)
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
