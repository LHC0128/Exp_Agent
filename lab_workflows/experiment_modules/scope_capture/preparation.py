"""示波器映射预检、连接与安全收尾；完整运行和分步会话共用。"""
from pathlib import Path

from ...common import load_mapping
from ...experiment_runtime import check_cancelled
from ...instrument_config import load_device_definitions
from ...steps import (DisconnectTarget, TemperatureSwitchRestore, run_safety_shutdown,
                      set_temperature_switch, synchronize_connected_clocks)
from ...steps.clock import clock_device_id
from ...steps.safety_shutdown import ShutdownAction
from .acquisition import MAXIMUM_POINTS
from .models import ScopeCaptureParams


def preflight(params: ScopeCaptureParams, root: Path):
    errors = params.validate(root)
    if errors:
        raise ValueError("；".join(errors))
    mappings = load_mapping(root)
    mapping = mappings["scope_waveform"]
    if mapping.get("instrument") != "sds_acquisition" or not mapping.get("resource"):
        raise ValueError("scope_waveform 必须解析为 SDS 设备及有效 resource")
    temp_mapping = mappings.get("Temp_Switch")
    if params.disable_temperature_control and (
        not temp_mapping or temp_mapping.get("instrument") != "signal_generator"
        or not temp_mapping.get("resource") or temp_mapping.get("channel") is None
    ):
        raise ValueError("启用关闭温控采集时，Temp_Switch 必须解析为有效信号发生器通道")
    return params, mapping, temp_mapping


def connect(ctx, signal_generator_factory):
    check_cancelled()
    try:
        if ctx.scope is None:
            ctx.scope = ctx.connection.connect("scope", str(ctx.mapping["resource"]),
                lambda: ctx.instrument_factory(str(ctx.mapping["resource"])))
        if ctx.params.disable_temperature_control and ctx.temp_switch is None:
            ctx.temp_switch = ctx.connection.connect("temp_switch", str(ctx.temp_mapping["resource"]),
                lambda: signal_generator_factory(ctx.temp_mapping))
            ctx.temp_channel = int(ctx.temp_mapping["channel"])
        if ctx.params.disable_temperature_control:
            device = load_device_definitions(ctx.root)[ctx.temp_mapping["device_library_id"]]
            ctx.clock_sources = synchronize_connected_clocks(
                {"temp_switch": ctx.temp_switch}, {"Temp_Switch": ctx.temp_mapping},
                {"temp_switch": "Temp_Switch"},
                profile={"__default__": "EXT", clock_device_id(ctx.temp_mapping): device.reference_clock or "EXT"})
            set_temperature_switch(ctx.temp_switch, True, channel=ctx.temp_channel)
            ctx.outputs["温控开关"] = "5 V ON"
        ctx.acquirer = ctx.acquirer_factory(ctx.scope, check_cancelled=check_cancelled,
                                           maximum_points=MAXIMUM_POINTS)
    except BaseException:
        try:
            ctx.shutdown = shutdown(ctx).to_dict()
        except Exception as error:
            print(f"连接失败后的安全恢复失败：{error}", flush=True)
        ctx.scope = ctx.temp_switch = ctx.acquirer = None
        ctx.temp_channel = None
        raise
    ctx.outputs["SDS"] = "已连接"


def shutdown(ctx):
    """使用已连接设备收尾，不应用尚未重跑的参数草稿。"""
    return run_safety_shutdown(
        extra_actions=(ShutdownAction("停止 SDS", ctx.scope.trigger_stop),) if ctx.scope else (),
        temperature_switch=(TemperatureSwitchRestore(ctx.temp_switch, int(ctx.temp_channel))
            if ctx.temp_switch is not None and ctx.temp_channel is not None else None),
        disconnect_targets=tuple(target for target in (
            DisconnectTarget("SDS", ctx.scope) if ctx.scope else None,
            DisconnectTarget("温度开关", ctx.temp_switch) if ctx.temp_switch else None,
        ) if target is not None),
    )
