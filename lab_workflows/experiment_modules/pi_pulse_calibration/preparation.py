"""无硬件预检、设备连接、时钟与安全收尾；导入不连接设备。

链路的器件操作复用 ``lab_workflows.steps.coil_drive_chain``；本模块只保留本实验的
预检、计划电平与收尾清单。
"""
from __future__ import annotations

from aigtek_1200b import Aigtek1200BInstrument
from oscilloscope import create_oscilloscope, create_scope_acquisition

from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...devices import create_signal_generator
from ...experiment_runtime import check_cancelled
from ...instrument_config import validate_mapping_usage
from ...steps import (DGChannelShutdown, DisconnectTarget, ShutdownAction, run_safety_shutdown,
                      synchronize_connected_clocks)
from ...steps.coil_drive_chain import (
    VERTICAL_HALF_DIVISIONS,
    SafetyAbort,
    amplifier_safety_check as _chain_amplifier_safety_check,
    capture_config,
    configure_amplifier,
    current_limit,
    nice_up,
    scale_for,
    set_amp_output,
    set_dc as _chain_set_dc,
    sleep,
)
from .run_format import AMPLIFIER_KEY, CURRENT_LIMIT_KEY, REQUIRED_MAPPING_KEYS, SCOPE_KEY, SOURCE_KEY

__all__ = [
    "VERTICAL_HALF_DIVISIONS", "SafetyAbort", "amplifier_safety_check", "baseline_scale", "capture_config",
    "clocks", "configure_amplifier", "connect", "current_limit", "nice_up", "plan", "preflight",
    "pulse_scale", "scale_for", "set_amp_output", "set_dc", "shutdown", "sleep",
]


def amplifier_safety_check(ctx):
    return _chain_amplifier_safety_check(ctx, preflight)


def plan(p):
    """计划中的电平、峰值电流与示波器窗口；预检与执行共用。"""
    vpp = p.pulse_vpp
    offset = p.pulse_baseline_v
    k = p.conversion_amps_per_volt if hasattr(p, "conversion_amps_per_volt") else p.amps_per_drive_volt
    return dict(pulse_vpp=vpp,
                pulse_offset_v=offset,
                pulse_max_v=offset + vpp / 2.0,
                pulse_min_v=offset - vpp / 2.0,
                pulse_peak_current_a=p.pulse_peak_current_ma * 1e-3,
                peak_sense_v=p.pulse_peak_current_ma * 1e-3 * p.sense_resistor_ohm)


def pulse_scale(p):
    """脉冲帧的垂直档位：覆盖峰值电流对应的采样电阻电压。"""
    return scale_for(p.pulse_peak_current_ma * 1e-3 * p.sense_resistor_ohm)


def baseline_scale(p):
    """直流基线帧的垂直档位：按 1 mA 量级，与脉冲档位区分开。"""
    return scale_for(1e-3 * p.sense_resistor_ohm)


def set_dc(ctx, volts):
    return _chain_set_dc(ctx, volts, SOURCE_KEY)


def preflight(params, root):
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    validate_mapping_usage(REQUIRED_MAPPING_KEYS, root)
    for key in REQUIRED_MAPPING_KEYS:
        if key not in mapping:
            raise ValueError(f"缺少设备映射：{key}")
    expected = {SOURCE_KEY: ("signal_generator", None), AMPLIFIER_KEY: ("power_amplifier", {"1200B"}),
                SCOPE_KEY: ("oscilloscope", {"DHO900", "DHO914", "DHO914S", "DHO924", "DHO924S"})}
    for key, (kind, models) in expected.items():
        config = mapping[key]
        if config.get("instrument") != kind or (models and str(config.get("model", "")).upper() not in models):
            raise ValueError(f"{key} 必须映射到 {kind}{'（' + '/'.join(sorted(models)) + '）' if models else ''}")
    limits = load_safety_limits(root)
    current_limit(limits)
    rule = limits.get(SOURCE_KEY) or {}
    if rule.get("min") is None or rule.get("max") is None:
        raise ValueError(f"{SOURCE_KEY} 缺少明确安全上下限")
    levels = plan(params)
    for value in (0.0, levels["pulse_max_v"], levels["pulse_min_v"]):
        validate_safety_limit(SOURCE_KEY, value, limits)
    for current_a in (levels["pulse_peak_current_a"], -levels["pulse_peak_current_a"]):
        validate_safety_limit(CURRENT_LIMIT_KEY, current_a, limits)
    duty = 2.0 * max(params.pulse_widths_us) / params.pulse_period_us
    resistor = max(params.sense_resistor_ohm, params.damping_resistor_ohm)
    power = (levels["pulse_peak_current_a"]) ** 2 * duty * resistor
    if power > params.resistor_rated_w / 2:
        raise ValueError(f"电阻平均功耗 {power:.3g} W 超过额定功率 {params.resistor_rated_w:g} W 的一半")
    capture_config(params, params.capture_window_s, pulse_scale(params), sync=True)
    return params, mapping


def connect(ctx):
    m, d, session = ctx.mapping, ctx.devices, ctx.device_session
    d["sg"] = session.connect("sg", f"{m[SOURCE_KEY]['resource']}#{m[SOURCE_KEY]['channel']}",
                              lambda: create_signal_generator(m[SOURCE_KEY]))
    d["scope"] = session.connect("scope", str(m[SCOPE_KEY]["resource"]), lambda: create_oscilloscope(m[SCOPE_KEY]))
    acquirer = create_scope_acquisition(d["scope"], check_cancelled=check_cancelled)
    acquirer.use_auto_depth(ctx.params.max_memory_depth)
    d["acquirer"] = session.bind("acquirer", acquirer)
    amp = m[AMPLIFIER_KEY]
    d["amp"] = session.connect("amp", str(amp["resource"]), lambda: Aigtek1200BInstrument(
        amp["resource"], serial_number=str((amp.get("connection") or {}).get("serial_number", "0001")),
        safety_check=amplifier_safety_check(ctx)))
    configure_amplifier(ctx)


def clocks(ctx):
    return synchronize_connected_clocks({SOURCE_KEY: ctx.devices["sg"]}, ctx.mapping, {SOURCE_KEY: SOURCE_KEY},
                                        settle_wait=sleep)


def shutdown(ctx):
    """先关功放去掉线圈电流，再把 Z 通道归零、关闭同步/输出、停止示波器，最后断开。"""
    d = ctx.devices
    first = run_safety_shutdown(
        extra_actions=(ShutdownAction("发送 1200B 关闭命令", lambda: d["amp"].set_output(False)),) if d.get("amp") else (),
        preserved_outputs=())
    actions = (ShutdownAction("停止 DHO900", d["scope"].trigger_stop),) if d.get("scope") else ()
    second = run_safety_shutdown(
        dg_channels=[DGChannelShutdown(d["sg"], int(d["sg"].channel), SOURCE_KEY, "Z 线圈驱动", disable_sync=True)]
        if d.get("sg") else [],
        extra_actions=actions,
        disconnect_targets=[DisconnectTarget(name, device) for name, device in
                            (("1200B", d.get("amp")), ("DHO900", d.get("scope")), ("Z 信号源", d.get("sg"))) if device],
        preserved_outputs=())
    return first.action_errors + second.action_errors, second.disconnect_errors
