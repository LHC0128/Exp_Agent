"""π 脉冲宽度原子域定标的预检与兼容导出；硬件编排已移到 ``z_chain_common.prep``。

本模块保留实验专属预检（脉宽网格、相位网格、拖尾功耗、电学参考与锁相能力）以及
历史导入名，采集、编排与测试继续从这里引用公共步骤。
"""
from __future__ import annotations

from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...devices import signal_generator_max_arb_points
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...steps.coil_drive_chain import SafetyAbort, current_limit, set_amp_output, sleep
from ..z_chain_common import prep as _prep
from ..z_chain_common.keys import (AMPLIFIER_KEY, CURRENT_LIMIT_KEY, LOCKIN_KEY, MAIN_FIELD_KEY,
                                   REQUIRED_MAPPING_KEYS, RF_KEY, SCOPE_KEY, SOURCE_KEY, TEC_KEY,
                                   TEMP_SWITCH_KEY, TRIGGER_KEY, X_KEY)
from ..z_chain_common.params import ARB_POINTS
from ..z_chain_common.prep import (ARB_SAMPLE_RATE_CHECK, BURST_NCYCLES, TEMP_SWITCH_ON_V,
                                   TRIGGER_DUTY, TRIGGER_OFFSET_V, TRIGGER_VPP, clocks,
                                   configure_chain, configure_lockin, configure_rf,
                                   configure_trigger, configure_x_bias, configure_z_idle,
                                   disable_main_field, enable_temperature_switch, plan, pulse_wave,
                                   rf_levels, set_rf_phase, shutdown)
from .models import PiWidthScanParams
from .run_format import EXPERIMENT_ID

__all__ = [
    "ARB_POINTS", "ARB_SAMPLE_RATE_CHECK", "BURST_NCYCLES", "SafetyAbort", "TEMP_SWITCH_ON_V",
    "TRIGGER_DUTY", "TRIGGER_OFFSET_V", "TRIGGER_VPP", "amplifier_safety_check", "clocks",
    "configure_chain", "configure_lockin", "configure_rf", "configure_trigger", "configure_x_bias",
    "configure_z_idle", "connect", "current_limit", "disable_main_field", "enable_temperature_switch",
    "plan", "preflight", "pulse_wave", "rf_levels", "set_amp_output", "set_rf_phase", "shutdown", "sleep",
]


def preflight(params: PiWidthScanParams, root):
    """连接前预检：参数、映射、安全限值与锁相能力；不连接任何设备。"""
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    for key in REQUIRED_MAPPING_KEYS:
        if key not in mapping:
            raise ValueError(f"缺少设备映射：{key}")
    validate_mapping_usage(REQUIRED_MAPPING_KEYS, root)
    expected = {
        SOURCE_KEY: ("signal_generator", None),
        X_KEY: ("signal_generator", None),
        RF_KEY: ("signal_generator", None),
        TRIGGER_KEY: ("signal_generator", None),
        TEMP_SWITCH_KEY: ("signal_generator", None),
        LOCKIN_KEY: ("lockin_amplifier", {"MFLI"}),
        AMPLIFIER_KEY: ("power_amplifier", {"1200B"}),
        SCOPE_KEY: ("oscilloscope", {"DHO900", "DHO914", "DHO914S", "DHO924", "DHO924S"}),
        MAIN_FIELD_KEY: ("gs200", None),
        TEC_KEY: ("tec_controller", None),
    }
    for key, (kind, models) in expected.items():
        config = mapping[key]
        if config.get("instrument") != kind or (models and str(config.get("model", "")).upper() not in models):
            raise ValueError(f"{key} 必须映射到 {kind}{'（' + '/'.join(sorted(models)) + '）' if models else ''}")
    limits = load_safety_limits(root)
    current_limit(limits)
    for key in (SOURCE_KEY, X_KEY, RF_KEY, TRIGGER_KEY, TEMP_SWITCH_KEY, MAIN_FIELD_KEY):
        rule = limits.get(key) or {}
        if rule.get("min") is None or rule.get("max") is None:
            raise ValueError(f"{key} 缺少明确安全上下限")
    require_lockin_compatibility(EXPERIMENT_ID, params.to_external(), mapping=mapping)

    levels = plan(params)
    for value in (0.0, levels["pulse_max_v"], levels["pulse_min_v"]):
        validate_safety_limit(SOURCE_KEY, value, limits)
    validate_safety_limit(X_KEY, 0.0, limits)
    validate_safety_limit(X_KEY, params.x_bias_v, limits)
    for value in rf_levels(params):
        validate_safety_limit(RF_KEY, value, limits)
    for value in (0.0, TRIGGER_OFFSET_V - TRIGGER_VPP / 2.0, TRIGGER_OFFSET_V + TRIGGER_VPP / 2.0):
        validate_safety_limit(TRIGGER_KEY, value, limits)
    validate_safety_limit(TEMP_SWITCH_KEY, TEMP_SWITCH_ON_V, limits)
    for current_a in (levels["pulse_peak_current_a"], -levels["pulse_peak_current_a"]):
        validate_safety_limit(CURRENT_LIMIT_KEY, current_a, limits)

    widths = params.widths_us()
    resistor = max(params.sense_resistor_ohm, params.damping_resistor_ohm)
    if params.z_tail_comp_enabled:
        # 带尾波形不再是矩形占空比：按实际写入波形的 mean(v²) 取所有脉宽中的最大值。
        widths_checked = list(widths) + ([params.phase_check_width_us] if not params.multi_phase else [])
        mean_square = max(pulse_wave(params, width)[1]["mean_square"] for width in widths_checked)
        power = levels["pulse_peak_current_a"] ** 2 * mean_square * resistor
    else:
        duty = 2.0 * max(widths) / params.pulse_period_us
        power = levels["pulse_peak_current_a"] ** 2 * duty * resistor
    if power > params.resistor_rated_w / 2:
        raise ValueError(f"电阻平均功耗 {power:.3g} W 超过额定功率 {params.resistor_rated_w:g} W 的一半")
    if levels["arb_sample_rate_sa_s"] > ARB_SAMPLE_RATE_CHECK:
        raise ValueError(f"任意波采样率 {levels['arb_sample_rate_sa_s']:.3g} Sa/s 超过信号源上限")

    source = mapping[SOURCE_KEY]
    capabilities = source.get("capabilities") or {}
    if not capabilities.get("supports_arbitrary", False):
        raise ValueError(f"{SOURCE_KEY} 当前设备缺少任意波能力 supports_arbitrary")
    if not capabilities.get("supports_infinite_burst", False):
        raise ValueError(f"{SOURCE_KEY} 当前设备缺少 burst 能力 supports_infinite_burst")
    if signal_generator_max_arb_points(source) < ARB_POINTS:
        raise ValueError(f"{SOURCE_KEY} 的任意波点数上限小于 {ARB_POINTS} 点")
    return params, mapping


def connect(ctx):
    """连接所有设备；失败时断开已连接设备，不留半开会话。"""
    return _prep.connect(ctx, experiment_id=EXPERIMENT_ID, preflight_fn=preflight)


def amplifier_safety_check(ctx):
    """1200B 安全回调：只放行关闭输出与本次参数里的增益/阻抗写入，其余一律拒绝。"""
    return _prep.amplifier_safety_check(ctx, preflight)
