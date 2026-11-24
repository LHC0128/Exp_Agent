"""DHO924 同帧电学监视与面板保护；不使用示波器作为锁相采集时间轴。"""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
from dho900 import DHO900Acquisition

from ...experiment_runtime import check_cancelled
from ...steps import ShutdownAction, run_safety_shutdown
from ...steps.coil_drive_chain import SafetyAbort, capture_config, nice_up

REF_CH, COIL_CH = 3, 4
_SCOPE_BUDGET = 1_000_000
_READOUT = (":WAVeform:SOURce", ":WAVeform:MODE", ":WAVeform:FORMat")
_TIMEBASE = (":TIMebase:MODE", ":TIMebase:ROLL", ":TIMebase:DELay:ENABle")
_COMMON = ("timebase_scale", "timebase_delay", "memory_depth", "acquire_type", "trigger_type",
           "trigger_source", "trigger_slope", "trigger_level", "trigger_mode")
_VERTICAL = ("probe", "coupling", "impedance", "scale", "offset")


def _state(scope):
    return dict(status=scope.trigger_status(),
                display={i: scope.get_channel_state(i) for i in range(1, 5)},
                vertical={i: {key: getattr(scope, "get_channel_" + key)(i) for key in _VERTICAL}
                          for i in (REF_CH, COIL_CH)},
                common={key: getattr(scope, "get_" + key)() for key in _COMMON},
                controls={name: scope.query(name + "?") for name in (*_TIMEBASE, *_READOUT)})


def snapshot_scope(scope):
    """拒绝覆盖冻结帧、录放、不同终端阻抗或未核对的特殊面板模式。"""
    state = _state(scope)
    mode, roll, delayed = [state["controls"][key].upper() for key in _TIMEBASE]
    if (state["status"].upper() == "STOP" or state["common"]["trigger_mode"].upper() in {"SING", "SINGLE"}
            or state["common"]["trigger_type"].upper() != "EDGE" or mode != "MAIN" or roll != "0"
            or delayed not in {"OFF", "0"} or state["common"]["acquire_type"].upper() not in {"NORM", "NORMAL"}
            or scope.query(":RECord:WRECord:ENABle?") != "0"
            or scope.query(":RECord:WREPlay:OPERate?").upper() != "STOP"):
        raise SafetyAbort("DHO924 须在主时基非滚动、普通采集、边沿连续触发，且没有录放或冻结帧")
    for ch, settings in state["vertical"].items():
        if (settings["probe"] != 1. or settings["impedance"].upper() not in {"ONEMEG", "1M"}
                or settings["coupling"].upper() != "DC" or scope.get_channel_units(ch).upper() != "VOLT"):
            raise SafetyAbort(f"DHO924 CH{ch} 须为 1×、高阻、DC 电压监视")
    return state


def _drain_errors(scope):
    """读空错误队列并返回；恢复前遗留错误与恢复动作新产生的错误分开记录。"""
    errors = []
    for _ in range(16):
        response = scope.query(":SYSTem:ERRor?").strip()
        if response.startswith("0"):
            break
        errors.append(response)
    return errors


def restore_scope(ctx):
    """逐条恢复本实验改变的面板状态，重新 RUN 后核对。"""
    import time

    scope, original = ctx.devices["scope"], ctx.scope_original
    # 采集超时或异常可能已在示波器里留下待读错误；先记下来，只把恢复动作新增的错误当失败。
    pending = _drain_errors(scope)
    actions = [ShutdownAction("DHO924 STOP", scope.trigger_stop)]
    actions += [ShutdownAction(f"DHO924 CH{ch} 显示",
                              lambda ch=ch, enabled=enabled: scope.set_channel_state(ch, enabled))
                for ch, enabled in original["display"].items()]
    for ch, settings in original["vertical"].items():
        # 高阻未变，不切换物理负载；其余数值由任务改过。
        actions += [ShutdownAction(f"DHO924 CH{ch} {key}",
                    lambda ch=ch, key=key, value=value: getattr(scope, "set_channel_" + key)(ch, value))
                    for key, value in settings.items() if key != "impedance"]
    for name in _TIMEBASE:
        actions.append(ShutdownAction(f"DHO924 {name}", lambda name=name: scope.write(f"{name} {original['controls'][name]}")))
    for name, value in original["common"].items():
        actions.append(ShutdownAction(f"DHO924 {name}", lambda name=name, value=value: getattr(scope, "set_" + name)(value)))
    for name in _READOUT:
        actions.append(ShutdownAction(f"DHO924 {name}", lambda name=name: scope.write(f"{name} {original['controls'][name]}")))
    actions.append(ShutdownAction("DHO924 RUN", scope.trigger_run))
    report = run_safety_shutdown(extra_actions=actions)
    deadline = time.monotonic() + 2.0
    while scope.trigger_status().upper() == "STOP" and time.monotonic() < deadline:
        time.sleep(0.1)  # 收尾不允许取消打断
    actual = _state(scope)
    ctx.state["scope_restore"] = dict(expected=original, actual=actual, pre_restore_errors=pending)
    changes = [key for key in original if key != "status" and actual[key] != original[key]]
    errors = [*report.errors]
    if changes or actual["status"].upper() == "STOP":
        errors.append(f"示波器面板回读不符：{changes or ['持续 STOP']}")
    fresh = _drain_errors(scope)
    if fresh:
        errors.append("DHO900 SCPI 错误：" + "；".join(fresh))
    if errors:
        raise SafetyAbort("；".join(errors))
    ctx.outputs["DHO924"] = "已回读恢复面板与连续触发"


def frame_config(fm_hz):
    # 20/40 Hz 用已验证的 0.5 s 时基；更低频延长到至少十个周期。
    requested = 0.5 if fm_hz is None else max(0.5, 10 / fm_hz)
    params = SimpleNamespace(scope_current_channel=COIL_CH, scope_sync_channel=REF_CH,
                             sync_scale_v_div=2., sync_trigger_level_v=2.,
                             scope_acquire_delay_s=1.5, max_memory_depth=_SCOPE_BUDGET)
    return capture_config(params, nice_up(requested / 10) * 10, 0.05, sync=True)


def acquire(ctx, f_hz):
    """按公开严格采集入口读 CH3/4 两路；驱动负责 STOP 延时及整帧一次重试。"""
    cfg = frame_config(f_hz)
    ctx.scope_touched = True
    acquirer = DHO900Acquisition(ctx.devices["scope"], check_cancelled=check_cancelled,
                                maximum_points=_SCOPE_BUDGET)
    acquirer.use_auto_depth(_SCOPE_BUDGET)
    results = acquirer.acquire_all(cfg)
    channels = {record.channel: record for record in results}
    if len(results) != 2 or set(channels) != {REF_CH, COIL_CH}:
        raise SafetyAbort("DHO924 未返回完整同帧 CH3/CH4")
    ref, coil = channels[REF_CH], channels[COIL_CH]
    t = np.asarray(ref.time)
    if t.size < 8 or not np.array_equal(t, coil.time) or ref.config_snapshot != coil.config_snapshot:
        raise SafetyAbort("DHO924 CH3/CH4 时间轴或配置快照不同")
    interval = float(ref.preamble_dict["xincrement"])
    span = float(t[-1] - t[0])
    if (not math.isfinite(interval) or interval <= 0 or interval != float(coil.preamble_dict["xincrement"])
            or interval > (1 + 1e-9) / (20 * ctx.params.y_rf_freq_hz) or span < 0.98 * cfg.sampling_time):
        raise SafetyAbort("DHO924 实测采样率或时间窗不足，未使用旧帧或补点")
    for channel in cfg.channels:
        voltage = channels[channel.number].voltage
        if not np.isfinite(voltage).all() or np.max(np.abs(voltage - channel.offset)) >= 0.98 * 4 * channel.scale:
            raise SafetyAbort(f"DHO924 CH{channel.number} 数据非有限或接近垂直限幅")
    return (dict(rate_sa_s=1 / interval, scope_record_s=span, scope_config=ref.config_snapshot,
                 ch3_preamble=ref.preamble_dict, ch4_preamble=coil.preamble_dict, timestamp=ref.timestamp),
            dict(time_s=t, reference_v=ref.voltage, coil_v=coil.voltage))
