"""无硬件预检、设备连接、时钟、示波器帧配置与安全收尾；导入不连接设备。"""
from __future__ import annotations

import math
import time

from aigtek_1200b import Aigtek1200BInstrument
from oscilloscope import AcquisitionConfig, ChannelConfig, TriggerConfig, create_oscilloscope, create_scope_acquisition

from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...devices import create_signal_generator
from ...experiment_runtime import check_cancelled
from ...instrument_config import validate_mapping_usage
from ...steps import (DGChannelShutdown, DisconnectTarget, ShutdownAction, run_safety_shutdown,
                      synchronize_connected_clocks)
from .run_format import AMPLIFIER_KEY, CURRENT_LIMIT_KEY, REQUIRED_MAPPING_KEYS, SCOPE_KEY, SOURCE_KEY

VERTICAL_HALF_DIVISIONS = 4.0


class SafetyAbort(RuntimeError):
    """实测量越过安全判据；立即中止并执行安全收尾。"""


def sleep(seconds, *, cancellable=True):
    deadline = time.monotonic() + seconds
    while (remaining := deadline - time.monotonic()) > 0:
        if cancellable:
            check_cancelled()
        time.sleep(min(0.05, remaining))


def current_limit(limits):
    """线圈电流规则必须存在且上下界均为有限数（单位 A），否则拒绝开启功放。"""
    rule = limits.get(CURRENT_LIMIT_KEY) or {}
    low, high = rule.get("min"), rule.get("max")
    if low is None or high is None or not (math.isfinite(low) and math.isfinite(high)):
        raise ValueError(f"params/safety_limits.yaml 缺少 {CURRENT_LIMIT_KEY} 的有限上下界（单位 A），拒绝开启功放")
    return float(low), float(high)


def nice_up(value):
    """不小于 value 的 1-2-5 序列值。"""
    exponent = math.floor(math.log10(value))
    for mantissa in (1.0, 2.0, 5.0, 10.0):
        candidate = mantissa * 10.0 ** exponent
        if candidate >= value * (1 - 1e-12):
            return candidate
    raise AssertionError("unreachable")


def scale_for(peak_v):
    return min(10.0, max(0.0005, nice_up(max(peak_v, 1e-4) * 1.25 / VERTICAL_HALF_DIVISIONS)))


def capture_config(p, window_s, current_scale, *, trigger_mode="NORMal"):
    """十格主时基取 1-2-5 序列；存储深度 AUTO，由示波器给出采样率，点数不超过 max_memory_depth。"""
    duration = nice_up(window_s / 10.0) * 10.0
    return AcquisitionConfig(
        sampling_rate=p.max_memory_depth / duration,  # AUTO 深度下仅作占位，实际采样率由示波器回读
        sampling_time=duration, memory_management=None, acquire_delay=p.scope_acquire_delay_s,
        channels=[ChannelConfig(p.scope_current_channel, enabled=True, scale=current_scale, coupling="DC",
                                impedance="ONEMeg", probe=1),
                  ChannelConfig(p.scope_sync_channel, enabled=True, scale=p.sync_scale_v_div, coupling="DC",
                                impedance="ONEMeg", probe=1)],
        trigger=TriggerConfig(mode=trigger_mode, source=f"C{p.scope_sync_channel}", slope="RISing",
                              level=p.sync_trigger_level_v))


def plan(p):
    """计划中的全部信号源电平、峰值电流与示波器窗口；预检和执行共用。"""
    k = p.amps_per_drive_volt
    return dict(sine_drive_peak_v=p.sine_peak_current_ma * 1e-3 / k, pulse_drive_v=p.pulse_current_ma * 1e-3 / k,
                gain_check_drive_v=p.gain_check_current_ma * 1e-3 / k)


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
    for value in (0.0, levels["sine_drive_peak_v"], -levels["sine_drive_peak_v"], levels["pulse_drive_v"],
                  levels["gain_check_drive_v"]):
        validate_safety_limit(SOURCE_KEY, value, limits)
    for current_ma in (params.sine_peak_current_ma, -params.sine_peak_current_ma, params.pulse_current_ma,
                       params.gain_check_current_ma):
        validate_safety_limit(CURRENT_LIMIT_KEY, current_ma * 1e-3, limits)
    resistor = max(params.sense_resistor_ohm, params.damping_resistor_ohm)
    power = max((params.sine_peak_current_ma * 1e-3) ** 2 / 2 * resistor,
                (params.gain_check_current_ma * 1e-3) ** 2 * resistor,
                (params.pulse_current_ma * 1e-3) ** 2 * max(params.pulse_widths_s) * params.pulse_repetition_hz * resistor)
    if power > params.resistor_rated_w / 2:
        raise ValueError(f"电阻平均功耗 {power:.3g} W 超过额定功率 {params.resistor_rated_w:g} W 的一半")
    # 记录窗在连接前就能判定，避免测到一半才拒绝。
    capture_config(params, params.zero_window_s, 1.0, trigger_mode="AUTO")
    for frequency in params.frequencies():
        capture_config(params, params.sine_cycles / frequency, 1.0)
    for width in params.pulse_widths_s:
        capture_config(params, params.pulse_window_multiple * width, 1.0)
    return params, mapping


def amplifier_safety_check(ctx):
    """1200B safety_check 回调：放行关闭输出与本次参数里的增益/阻抗写入，开启输出前重做整次预检。"""
    def check(operation, value):
        if operation == "output":
            if value is not False:
                preflight(ctx.params, ctx.root)
            return
        if operation not in {"gain_db", "input_impedance_ohms", "output_impedance_ohms"}:
            raise ValueError(f"本实验不修改功放 {operation}")
        if ctx.state.get("amp_output_on"):
            raise ValueError("功放输出开启时不允许修改增益或阻抗；先关闭输出并确认面板")
        preflight(ctx.params, ctx.root)
    return check


def configure_amplifier(ctx):
    """按参数写入 1200B 的增益与输入/输出阻抗，并把信号源输出负载配成与输入阻抗一致。

    1200B 没有增益与阻抗回读，写入值就是预测电流的依据；顺序是上游停输出 → 功放停输出 →
    写输入/输出阻抗 → 写增益 → 配信号源负载。写入值连同回路电阻与预测 I/V 记入运行状态。
    AMPLIFIER_WRITE_SETTINGS 为 false 时不写硬件，只按声明值预测。
    """
    p, sg, amp = ctx.params, ctx.devices["sg"], ctx.devices["amp"]
    sg.set_output(False)
    amp.set_output(False)
    ctx.state["amp_output_on"] = False
    if p.amplifier_write_settings:
        amp.set_input_impedance(p.amplifier_input_impedance_ohm)
        amp.set_output_impedance(p.amplifier_output_impedance_ohm)
        amp.set_gain_db(p.amplifier_gain_db)
    load = "INF" if p.amplifier_input_impedance_ohm >= 1e6 else 50
    sg.set_output_load(load)
    written = "已写入，未回读" if p.amplifier_write_settings else "未写入，需面板一致"
    ctx.state["amplifier"] = dict(gain_db=p.amplifier_gain_db,
                                  input_impedance_ohm=p.amplifier_input_impedance_ohm,
                                  output_impedance_ohm=p.amplifier_output_impedance_ohm,
                                  source_output_load=load,
                                  written=bool(p.amplifier_write_settings),
                                  loop_resistance_ohm=p.loop_resistance_ohm,
                                  predicted_amps_per_volt=p.amps_per_drive_volt)
    ctx.outputs["功放"] = (f"{p.amplifier_gain_db:g} dB、输入 {p.amplifier_input_impedance_ohm:g} Ω、"
                           f"输出 {p.amplifier_output_impedance_ohm:g} Ω（{written}）")
    ctx.outputs["Z 通道"] = f"输出 OFF，负载 {load}"


def connect(ctx):
    m, d, session = ctx.mapping, ctx.devices, ctx.device_session
    d["sg"] = session.connect("sg", f"{m[SOURCE_KEY]['resource']}#{m[SOURCE_KEY]['channel']}",
                              lambda: create_signal_generator(m[SOURCE_KEY]))
    d["scope"] = session.connect("scope", str(m[SCOPE_KEY]["resource"]), lambda: create_oscilloscope(m[SCOPE_KEY]))
    acquirer = create_scope_acquisition(d["scope"], check_cancelled=check_cancelled)
    acquirer.use_auto_depth(ctx.params.max_memory_depth)  # 存储深度 AUTO，点数上限由本实验参数给定
    d["acquirer"] = session.bind("acquirer", acquirer)
    amp = m[AMPLIFIER_KEY]
    d["amp"] = session.connect("amp", str(amp["resource"]), lambda: Aigtek1200BInstrument(
        amp["resource"], serial_number=str((amp.get("connection") or {}).get("serial_number", "0001")),
        safety_check=amplifier_safety_check(ctx)))
    configure_amplifier(ctx)


def set_amp_output(ctx, enabled):
    """开/关 1200B 输出并登记状态；安全回调据此拒绝带载改增益或阻抗。"""
    ctx.devices["amp"].set_output(enabled)
    ctx.state["amp_output_on"] = enabled
    ctx.outputs["功放"] = "ON（已发送 AON，未回读）" if enabled else "OFF（已发送 AOF，未回读）"


def clocks(ctx):
    return synchronize_connected_clocks({SOURCE_KEY: ctx.devices["sg"]}, ctx.mapping, {SOURCE_KEY: SOURCE_KEY},
                                        settle_wait=sleep)


def set_dc(ctx, volts):
    sg = ctx.devices["sg"]
    validate_safety_limit(SOURCE_KEY, volts, load_safety_limits(ctx.root))
    sg.set_burst_state(False)
    sg.set_mod_state(False)
    sg.setup_dc(volts)


def shutdown(ctx):
    """先关功放去掉线圈电流，再把 Z 通道归零并关闭同步/输出、停止示波器，最后断开全部设备。"""
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
