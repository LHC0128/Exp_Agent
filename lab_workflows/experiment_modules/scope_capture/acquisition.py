"""SDS 单通道采集配置与完整帧捕获；不调用仪器 FFT，不控制其他实验设备。"""

from copy import deepcopy
import json
import time

import numpy as np
from sds_acquisition import AcquisitionConfig, ChannelConfig, TriggerConfig

from ...experiment_runtime import check_cancelled
from ...steps import create_run_directory, set_temperature_switch
from ...steps.acquisition_window import DeferredAcquisitionWrites, acquisition_window
from ...steps.dispersion import save_scan
from .analysis_steps import validate_waveform
from .models import ScopeCaptureParams
from .run_format import RAW_FORMAT_VERSION

MAXIMUM_POINTS = 50_000_000


def sleep_cancellable(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def configure(scope, params: ScopeCaptureParams, acquirer) -> tuple[AcquisitionConfig, dict]:
    """设置采集/触发通道、垂直参数及触发条件。"""
    channel = params.channel
    inputs = {
        "scale": float(params.vertical_scale_v_div),
        "offset": float(params.vertical_offset_v),
        "coupling": str(scope.get_channel_coupling(channel)),
        "impedance": str(scope.get_channel_impedance(channel)),
        "probe": float(scope.get_channel_probe(channel)),
    }
    channels = []
    for number in range(1, 5):
        if number == channel:
            channels.append(ChannelConfig(number=number, **inputs))
        elif number == params.trigger_channel:
            channels.append(ChannelConfig(
                number=number, scale=float(scope.get_channel_scale(number)),
                offset=float(scope.get_channel_offset(number)),
                coupling=str(scope.get_channel_coupling(number)),
                impedance=str(scope.get_channel_impedance(number)),
                probe=float(scope.get_channel_probe(number)),
            ))
        else:
            channels.append(ChannelConfig(number=number, enabled=False))
    config = AcquisitionConfig(
        sampling_rate=params.sampling_rate_sa_s, sampling_time=params.duration_s,
        memory_management="FSRate", acquire_type="NORMal", acquire_delay=params.duration_s,
        channels=channels,
        trigger=TriggerConfig(mode=params.trigger_mode, source=f"C{params.trigger_channel}",
                              slope=params.trigger_slope, level=params.trigger_level_v),
    )
    acquirer.apply_config(config)
    memory_mode = str(scope.get_memory_management())
    rate = float(scope.get_sampling_rate())
    points = int(scope.get_actual_points())
    if memory_mode.upper() != "FSRATE" or not np.isfinite(rate) or rate <= 0:
        raise ValueError("示波器未进入有效固定采样率模式")
    if not 2 <= points <= MAXIMUM_POINTS:
        raise ValueError(f"示波器实际点数 {points} 超出第一版范围 2--{MAXIMUM_POINTS}")
    actual_scale = float(scope.get_channel_scale(channel))
    actual_offset = float(scope.get_channel_offset(channel))
    if not np.isfinite(actual_scale) or actual_scale <= 0 or not np.isfinite(actual_offset):
        raise ValueError("示波器垂直档位或偏置读回值无效")
    return config, {"requested": config.to_dict(), "input_settings": inputs,
                    "acquisition_channel": channel,
                    "actual_vertical_scale_v_div": actual_scale,
                    "actual_vertical_offset_v": actual_offset,
                    "vertical_divisions": 8,
                    "capture_strategy": "auto_force_frame" if params.trigger_mode.upper() == "AUTO" else "edge_trigger",
                    "actual_trigger": {
                        "mode": str(scope.get_trigger_mode()),
                        "source": str(scope.get_trigger_source()),
                        "slope": str(scope.get_trigger_slope()),
                        "level_v": float(scope.get_trigger_level()),
                    },
                    "readback_rate_sa_s": rate, "readback_points": points,
                    "readback_timebase_s_div": float(scope.get_timebase_scale()),
                    "memory_management": memory_mode, "memory_depth": str(scope.get_memory_depth())}


def capture(scope, acquirer, config, snapshot, *, check=check_cancelled,
            sleep=sleep_cancellable, monotonic=time.monotonic):
    """按配置触发并只接受完整帧；空帧重试，不复位整台仪器。"""
    last_problem = "未获得数据"
    attempts = snapshot.setdefault("capture_attempts", [])
    for attempt in range(3):
        check()
        diagnostic = {"attempt": attempt + 1, "trigger_mode": config.trigger.mode}
        attempts.append(diagnostic)
        scope.trigger_stop()
        scope.trigger_run()
        requested_mode = config.trigger.mode
        scope.set_trigger_mode(requested_mode)
        record_duration = max(config.sampling_time, snapshot["readback_points"] / snapshot["readback_rate_sa_s"])
        deadline = monotonic() + record_duration + 2.5
        if requested_mode.upper() in {"NORMAL", "SINGLE"}:
            # 每次重新布防，SINGLE 完成会自动停止；NORMAL 需确认触发后等满记录窗口。
            while True:
                check()
                status = str(scope.trigger_status()).upper().replace("'", "").strip()
                if status in {"STOP", "STOPPED"} and requested_mode.upper() == "SINGLE":
                    break
                if status in {"TRIGD", "TRIGGERED"}:
                    sleep(record_duration)
                    break
                if monotonic() >= deadline:
                    raise TimeoutError("示波器等待触发超时")
                sleep(0.05)
        elif requested_mode.upper() == "AUTO":
            # SDS1204X HD 在 AUTO 无有效边沿（尤其关闭温控时）可能不提交
            # 深存储帧。FTRIG 是一次性完整帧采集动作，完成后自动回到 AUTO。
            # 不能以固定 sleep 或 STOP 响应代替本次动作的完成确认。
            scope.set_trigger_mode("FTRIG")
            while str(scope.get_trigger_mode()).upper().strip() == "FTRIG":
                check()
                if monotonic() >= deadline:
                    diagnostic["failure"] = "强制触发超时，未读取波形"
                    raise TimeoutError("示波器 AUTO 完整帧强制触发未在记录窗口内完成，未读取旧帧")
                sleep(0.05)
            diagnostic["force_trigger_completed"] = True
        sleep(0.2)
        check()
        scope.trigger_stop()
        deadline = monotonic() + 2.0
        while str(scope.trigger_status()).upper().strip() not in {"STOP", "STOPPED"}:
            check()
            if monotonic() >= deadline:
                raise TimeoutError("示波器停止状态确认超时")
            sleep(0.05)
        result = acquirer.acquire_channel(snapshot["acquisition_channel"],
                                         config.timebase_scale, config.horizontal_divisions, trim_points=0)
        expected = int(result.preamble_dict.get("point_num", 0))
        diagnostic.update(exported_points=len(result.voltage), preamble_points=expected)
        if expected < 2 or len(result.voltage) != expected:
            last_problem = f"第 {attempt + 1} 次导出 {len(result.voltage)} 点，preamble 声明 {expected} 点"
            diagnostic["failure"] = last_problem
            print(last_problem, flush=True)
            continue
        t, v, rate = validate_waveform(result.time, result.voltage)
        if t[-1] - t[0] < config.sampling_time - 2.0 / rate:
            last_problem = "实际记录时长少于请求时长"
            diagnostic["failure"] = last_problem
            continue
        return result, t, v, rate
    raise RuntimeError("示波器连续三次未获得完整帧：" + last_problem)


def acquire_run(ctx):
    """单帧为一个采集组；完整帧先冻结，取消及恢复失败仍先恢复再抢救。"""
    check_cancelled()
    params = ctx.params
    ctx.run_directory = create_run_directory(ctx.data_type, params.run_tag, params.to_external(),
        schema_version=params.schema_version, project_root=ctx.root)
    ctx.run_dir = ctx.run_directory.root
    snapshot = deepcopy(ctx.scope_snapshot)
    snapshot.pop("capture_attempts", None)
    ctx.run_directory.update_config(experiment_id=ctx.experiment_id, execution_mode="typed_workflow",
        completion_status="running", raw_format_version=RAW_FORMAT_VERSION, scope_configuration=snapshot,
        clock_sources=ctx.clock_sources)
    if params.disable_temperature_control:
        ctx.run_directory.update_config(temperature_switch={
            "enabled": True, "mapping_key": "Temp_Switch", "channel": ctx.temp_channel,
            "off_voltage_v": 0.0, "on_voltage_v": 5.0, "off_settle_s": params.system_settle_s,
            "on_settle_s": params.temperature_switch_on_settle_s})
    ctx.publish("初始化完成，进入温控门控采集")

    def set_enabled(enabled):
        ctx.outputs["温控开关"] = "恢复中" if enabled else "关闭中"
        set_temperature_switch(ctx.temp_switch, enabled, channel=ctx.temp_channel,
            settle_time=params.temperature_switch_on_settle_s if enabled else 0.0, cancellation=None)
        ctx.outputs["温控开关"] = "5 V ON（已恢复）" if enabled else "0 V ON"

    def collect(pending):
        result, t, v, rate = capture(ctx.scope, ctx.acquirer, ctx.scope_config, snapshot)
        pending.add(save_scan, ctx.run_dir / "raw/waveform.npz", dict(
            time_s=t, voltage_v=v, raw_adc=result.raw_data, channel=params.channel,
            timestamp=result.timestamp, actual_rate_sa_s=rate,
            requested_rate_sa_s=params.sampling_rate_sa_s, requested_duration_s=params.duration_s,
            preamble_json=np.array(json.dumps(result.preamble_dict, ensure_ascii=False)),
            configuration_json=np.array(json.dumps(snapshot, ensure_ascii=False))))
        # 完整帧已冻结后响应取消，避免刚读到的数据在取消时丢失。
        check_cancelled()
        return t, v, rate

    try:
        with DeferredAcquisitionWrites() as pending:
            if params.disable_temperature_control:
                if ctx.temp_switch is None or ctx.temp_channel is None:
                    raise ValueError("温控门控尚未准备，请重新执行 A02")
                with acquisition_window(set_enabled, sleep_cancellable, params.system_settle_s):
                    t, v, rate = collect(pending)
            else:
                sleep_cancellable(params.system_settle_s)
                t, v, rate = collect(pending)
    except BaseException:
        try:
            ctx.run_directory.update_config(scope_configuration=snapshot)
        except Exception as error:
            print(f"保存采集诊断失败：{error}", flush=True)
        raise
    check_cancelled()
    ctx.scope_snapshot = snapshot
    ctx.run_directory.update_config(scope_configuration=snapshot, actual_rates={"scope": rate},
        sample_count=int(v.size), actual_duration_s=float(t[-1] - t[0]), data_files=["raw/waveform.npz"],
        completion_status="completed", failure_reason=None)
    ctx.outputs["SDS"] = "STOP；本轮波形已保存"
    print(f"已保存 {v.size} 点，实际 {rate:.9g} Sa/s：{ctx.run_dir}", flush=True)
