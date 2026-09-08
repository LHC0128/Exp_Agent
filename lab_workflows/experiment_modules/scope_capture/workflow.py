"""SDS 单通道采集；不调用仪器 FFT，不控制其他实验设备。"""

from pathlib import Path
from typing import Callable
import json
import time

import numpy as np
from sds_acquisition import AcquisitionConfig, ChannelConfig, SDSAcquisition, SDSInstrument, TriggerConfig

from ...common import WorkflowCancelled, find_project_root, load_mapping
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...devices import create_signal_generator
from ...steps import (
    DeviceSession,
    DisconnectTarget,
    TemperatureSwitchRestore,
    create_run_directory,
    run_safety_shutdown,
    set_temperature_switch,
    temperature_gated_acquire,
)
from ...steps.safety_shutdown import ShutdownAction
from .analysis import validate_waveform
from .models import ScopeCaptureParams

EXPERIMENT_ID = "scope-capture"
DATA_TYPE = "Scope_Capture"
EXECUTION_MODE = "typed_workflow"
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
        raise RuntimeError("示波器未进入有效固定采样率模式")
    if not 2 <= points <= MAXIMUM_POINTS:
        raise RuntimeError(f"示波器实际点数 {points} 超出第一版范围 2--{MAXIMUM_POINTS}")
    actual_scale = float(scope.get_channel_scale(channel))
    actual_offset = float(scope.get_channel_offset(channel))
    if not np.isfinite(actual_scale) or actual_scale <= 0 or not np.isfinite(actual_offset):
        raise RuntimeError("示波器垂直档位或偏置读回值无效")
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
        check()
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


def run(params: ScopeCaptureParams, *, project_root: Path | None = None,
        instrument_factory: Callable = SDSInstrument, acquirer_factory: Callable = SDSAcquisition) -> Path:
    root = project_root or find_project_root()
    errors = params.validate(root)
    if errors:
        raise ValueError("；".join(errors))
    mappings = load_mapping(root)
    mapping = mappings["scope_waveform"]
    if mapping.get("instrument") != "sds_acquisition" or not mapping.get("resource"):
        raise ValueError("scope_waveform 必须解析为 SDS 设备及有效 resource")
    temp_mapping = mappings.get("Temp_Switch")
    if params.disable_temperature_control and (
        not temp_mapping
        or temp_mapping.get("instrument") != "signal_generator"
        or not temp_mapping.get("resource")
        or temp_mapping.get("channel") is None
    ):
        raise ValueError("启用关闭温控采集时，Temp_Switch 必须解析为有效信号发生器通道")
    directory = create_run_directory(DATA_TYPE, params.run_tag, params.to_external(),
                                     schema_version=params.schema_version, project_root=root)
    directory.update_config(experiment_id=EXPERIMENT_ID, execution_mode=EXECUTION_MODE,
                            completion_status="running")
    scope = instrument_factory(str(mapping["resource"]))
    session = DeviceSession()
    connected = False
    temp_switch = None
    temp_channel = None
    status = "failed"
    failure = None
    snapshot = None
    try:
        check_cancelled()
        print("连接示波器", flush=True)
        session.connect("scope", str(mapping["resource"]), lambda: scope)
        connected = True
        if params.disable_temperature_control:
            assert temp_mapping is not None
            temp_switch = session.connect(
                "temp_switch",
                str(temp_mapping["resource"]),
                lambda: create_signal_generator(temp_mapping),
            )
            temp_channel = int(temp_mapping["channel"])
            set_temperature_switch(temp_switch, True, channel=temp_channel)
            directory.update_config(
                temperature_switch={
                    "enabled": True,
                    "mapping_key": "Temp_Switch",
                    "channel": temp_channel,
                    "off_voltage_v": 0.0,
                    "on_voltage_v": 5.0,
                    "off_settle_s": params.temperature_switch_off_settle_s,
                    "on_settle_s": params.temperature_switch_on_settle_s,
                }
            )
        acquirer = acquirer_factory(scope, check_cancelled=check_cancelled, maximum_points=MAXIMUM_POINTS)
        config, snapshot = configure(scope, params, acquirer)
        directory.update_config(scope_configuration=snapshot)
        print(f"采集 C{params.channel}，请求 {params.duration_s:g} s；等待完整帧", flush=True)
        def acquire_waveform():
            return capture(scope, acquirer, config, snapshot)

        if params.disable_temperature_control:
            assert temp_switch is not None and temp_channel is not None
            result, t, v, rate = temperature_gated_acquire(
                temp_switch=temp_switch,
                temp_channel=temp_channel,
                off_settle_s=params.temperature_switch_off_settle_s,
                on_settle_s=params.temperature_switch_on_settle_s,
                acquire=acquire_waveform,
                check_cancelled=check_cancelled,
                sleep=sleep_cancellable,
                set_temperature_switch=set_temperature_switch,
                cancellation=None,
            )
        else:
            result, t, v, rate = acquire_waveform()
        check_cancelled()
        np.savez(directory.raw / "waveform.npz", time_s=t, voltage_v=v, raw_adc=result.raw_data,
                 channel=params.channel, timestamp=result.timestamp, actual_rate_sa_s=rate,
                 requested_rate_sa_s=params.sampling_rate_sa_s, requested_duration_s=params.duration_s,
                 preamble_json=np.array(json.dumps(result.preamble_dict, ensure_ascii=False)),
                 configuration_json=np.array(json.dumps(snapshot, ensure_ascii=False)))
        directory.update_config(actual_rates={"scope": rate}, sample_count=int(v.size),
                                actual_duration_s=float(t[-1] - t[0]), data_files=["raw/waveform.npz"])
        status = "completed"
        print(f"已保存 {v.size} 点，实际 {rate:.9g} Sa/s：{directory.root}", flush=True)
        return directory.root
    except WorkflowCancelled as exc:
        status, failure = "cancelled", str(exc)
        raise
    except Exception as exc:
        failure = str(exc)
        raise
    finally:
        report = run_safety_shutdown(
            extra_actions=(ShutdownAction("停止 SDS", scope.trigger_stop),) if connected else (),
            temperature_switch=(
                TemperatureSwitchRestore(temp_switch, int(temp_channel))
                if temp_switch is not None and temp_channel is not None
                else None
            ),
            disconnect_targets=(
                DisconnectTarget("SDS", scope),
                DisconnectTarget("温度开关", temp_switch),
            ),
        )
        directory.update_config(completion_status="failed" if report.errors else status,
                                failure_reason=failure, safety_shutdown=report.to_dict(),
                                **({"scope_configuration": snapshot} if snapshot is not None else {}))
        if report.errors and failure is None:
            raise RuntimeError("示波器结束清理失败：" + "；".join(report.errors))


if __name__ == "__main__":
    try:
        run(load_runtime_params(ScopeCaptureParams))
    except WorkflowCancelled as exc:
        print(str(exc))
        raise SystemExit(130)
