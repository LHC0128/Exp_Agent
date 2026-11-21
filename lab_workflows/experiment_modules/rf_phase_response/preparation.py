"""射频相位响应的预检与公共硬件编排；导入不连接设备。

硬件连接、激励与收尾复用 ``z_chain_common.prep``；本模块只保留本实验的参数预检
（单脉宽、相位网格、拖尾功耗与锁相能力）。
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
                                   configure_chain, configure_lockin, configure_main_field,
                                   configure_rf, configure_trigger, configure_x_bias,
                                   configure_z_idle, disable_main_field, enable_temperature_switch,
                                   channel_bias_v, channel_key, configure_dc_side, phase_drift_deg, plan,
                                   pulse_wave, rf_levels, rf_role, set_rf_phase, shutdown)
from .models import RfPhaseResponseParams
from .run_format import EXPERIMENT_ID

__all__ = [
    "ARB_POINTS", "ARB_SAMPLE_RATE_CHECK", "BURST_NCYCLES", "SafetyAbort", "TEMP_SWITCH_ON_V",
    "TRIGGER_DUTY", "TRIGGER_OFFSET_V", "TRIGGER_VPP", "amplifier_safety_check", "clocks",
    "configure_chain", "configure_dc_side", "configure_lockin", "configure_rf", "configure_trigger",
    "configure_x_bias",
    "configure_z_idle", "connect", "phase_drift_deg", "current_limit", "disable_main_field",
    "enable_temperature_switch", "configure_main_field",
    "plan", "preflight", "pulse_wave", "rf_levels", "set_amp_output", "set_rf_phase", "shutdown", "sleep",
]


def preflight(params: RfPhaseResponseParams, root):
    """连接前预检：参数、映射、安全限值、脉宽约束与锁相能力；不连接任何设备。"""
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
    for role in ("x", "y"):
        validate_safety_limit(channel_key(role), 0.0, limits)
        validate_safety_limit(channel_key(role), channel_bias_v(params, role), limits)
    if params.main_field_current_ma:
        source_function = str(mapping[MAIN_FIELD_KEY].get("source_function", "")).upper()
        if source_function not in ("CURR", "CURRENT"):
            raise ValueError(f"{MAIN_FIELD_KEY} 必须配置为电流模式才能加主磁场")
        validate_safety_limit(MAIN_FIELD_KEY, params.main_field_current_ma, limits)
    for value in rf_levels(params):
        validate_safety_limit(channel_key(rf_role(params)), value, limits)
    for value in (0.0, TRIGGER_OFFSET_V - TRIGGER_VPP / 2.0, TRIGGER_OFFSET_V + TRIGGER_VPP / 2.0):
        validate_safety_limit(TRIGGER_KEY, value, limits)
    validate_safety_limit(TEMP_SWITCH_KEY, TEMP_SWITCH_ON_V, limits)
    for current_a in (levels["pulse_peak_current_a"], -levels["pulse_peak_current_a"]):
        validate_safety_limit(CURRENT_LIMIT_KEY, current_a, limits)

    width = float(params.z_pulse_width_us)
    if width <= 2.0 * params.z_pulse_edge_us:
        raise ValueError("Z_PULSE_WIDTH_US 必须明显大于两倍升余弦沿宽")
    if width > params.pulse_period_us / 2.0:
        raise ValueError("Z_PULSE_WIDTH_US 不能超过半个射频周期")
    if not params.phase_angles_deg():
        raise ValueError("射频相位网格为空，请检查起点、终点与步进")

    resistor = max(params.sense_resistor_ohm, params.damping_resistor_ohm)
    if params.z_tail_comp_enabled:
        # 带尾波形不再是矩形占空比：按实际写入波形的 mean(v²) 计算平均功耗。
        mean_square = pulse_wave(params, width)[1]["mean_square"]
        power = levels["pulse_peak_current_a"] ** 2 * mean_square * resistor
    else:
        duty = 2.0 * width / params.pulse_period_us
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
