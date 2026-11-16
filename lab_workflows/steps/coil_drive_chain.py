"""线圈驱动链路的共享步骤：信号源 DC、1200B 设置与安全回调、DHO900 帧配置。

由 ``coil_drive_characterization`` 与 ``coil_constant_calibration`` 共用，只含无实验语义的
器件操作与波形配置；实验的预检、计划电平与收尾仍留在各实验模块。导入不连接仪器。
"""
from __future__ import annotations

import math
import time
from typing import Any

from oscilloscope import AcquisitionConfig, ChannelConfig, TriggerConfig

from ..common import load_safety_limits, validate_safety_limit
from ..experiment_runtime import check_cancelled

# 垂直方向半屏格数；1-2-5 档位按峰值留 25% 余量后覆盖该格数。
VERTICAL_HALF_DIVISIONS = 4.0


class SafetyAbort(RuntimeError):
    """实测量越过安全判据；立即中止并执行安全收尾。"""


def sleep(seconds, *, cancellable=True):
    """协作式等待；取消检查以 50 ms 为粒度。"""
    deadline = time.monotonic() + seconds
    while (remaining := deadline - time.monotonic()) > 0:
        if cancellable:
            check_cancelled()
        time.sleep(min(0.05, remaining))


def current_limit(limits):
    """线圈电流规则必须存在且上下界均为有限数（单位 A），否则拒绝开启功放。"""
    rule = limits.get("z_pulse_coil_current") or {}
    low, high = rule.get("min"), rule.get("max")
    if low is None or high is None or not (math.isfinite(low) and math.isfinite(high)):
        raise ValueError("params/safety_limits.yaml 缺少 z_pulse_coil_current 的有限上下界（单位 A），拒绝开启功放")
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


def capture_config(p, window_s, current_scale, *, sync=False):
    """十格主时基取 1-2-5 序列；存储深度 AUTO，由示波器给出采样率，点数不超过 max_memory_depth。

    直流帧 ``sync=False``：只开采样电阻通道、AUTO 触发，不需要同步通道；
    波形帧 ``sync=True``：再开同步通道并按同步沿 NORMal 触发。
    """
    duration = nice_up(window_s / 10.0) * 10.0
    channels = [ChannelConfig(p.scope_current_channel, enabled=True, scale=current_scale, coupling="DC",
                              impedance="ONEMeg", probe=1)]
    if sync:
        channels.append(ChannelConfig(p.scope_sync_channel, enabled=True, scale=p.sync_scale_v_div,
                                      coupling="DC", impedance="ONEMeg", probe=1))
        trigger = TriggerConfig(mode="NORMal", source=f"C{p.scope_sync_channel}", slope="RISing",
                                level=p.sync_trigger_level_v)
    else:
        trigger = TriggerConfig(mode="AUTO", source=f"C{p.scope_current_channel}", slope="RISing", level=0.0)
    return AcquisitionConfig(
        sampling_rate=p.max_memory_depth / duration,  # AUTO 深度下仅作占位，实际采样率由示波器回读
        sampling_time=duration, memory_management=None, acquire_delay=p.scope_acquire_delay_s,
        channels=channels, trigger=trigger)


def amplifier_safety_check(ctx, preflight):
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
    ``AMPLIFIER_WRITE_SETTINGS`` 为 false 时不写硬件，只按声明值预测。
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


def set_amp_output(ctx, enabled):
    """开/关 1200B 输出并登记状态；安全回调据此拒绝带载改增益或阻抗。"""
    ctx.devices["amp"].set_output(enabled)
    ctx.state["amp_output_on"] = enabled
    ctx.outputs["功放"] = "ON（已发送 AON，未回读）" if enabled else "OFF（已发送 AOF，未回读）"


def set_dc(ctx, volts, safety_key):
    """把信号源通道写成 DC 电平；先过安全限值再停 Burst/调制。"""
    sg = ctx.devices["sg"]
    validate_safety_limit(safety_key, volts, load_safety_limits(ctx.root))
    sg.set_burst_state(False)
    sg.set_mod_state(False)
    sg.setup_dc(volts)
