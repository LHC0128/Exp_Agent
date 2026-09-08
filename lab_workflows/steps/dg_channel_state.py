"""DG 信号发生器通道的扫描前状态快照与扫描后恢复步骤。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DGChannelState:
    """恢复 DG 通道工作模式所需的完整状态。"""

    shape: str
    frequency_hz: float | None
    amplitude_vpp: float | None
    offset_v: float
    phase_deg: float | None
    burst_state: bool
    burst_mode: str | None
    burst_ncycles: float | str | None
    burst_phase_deg: float | None
    burst_period_s: float | None
    burst_delay_s: float | None
    burst_trigger_source: str | None
    burst_trigger_slope: str | None
    mod_state: bool
    output_on: bool


def snapshot_dg_channel(device: Any, channel: int) -> DGChannelState:
    """在改为 DC 前读取足以恢复通道工作模式的状态。

    DC 电平使用型号驱动的 ``get_dc_voltage()`` 查询。DG4162 的内部
    查询可能不随物理输出同步，因此这里保存的是仪器报告值，不是独立
    的物理电压测量。
    """
    shape = str(device.get_shape(channel=channel))
    is_dc = shape.strip().upper() == "DC"
    if is_dc:
        dc_getter = getattr(device, "get_dc_voltage", None)
        offset_v = (
            float(dc_getter(channel=channel))
            if dc_getter is not None
            else float(device.get_offset(channel=channel))
        )
    else:
        offset_v = float(device.get_offset(channel=channel))
    burst_state = bool(device.get_burst_state(channel=channel))
    return DGChannelState(
        shape=shape,
        frequency_hz=(
            None if is_dc else float(device.get_frequency(channel=channel))
        ),
        amplitude_vpp=(
            None if is_dc else float(device.get_amplitude(channel=channel))
        ),
        offset_v=offset_v,
        phase_deg=(
            None if is_dc else float(device.get_phase_adjust(channel=channel))
        ),
        burst_state=burst_state,
        burst_mode=(
            str(device.get_burst_mode(channel=channel))
            if burst_state
            else None
        ),
        burst_ncycles=(
            device.get_burst_ncycles(channel=channel)
            if burst_state
            else None
        ),
        burst_phase_deg=(
            float(device.get_burst_phase(channel=channel))
            if burst_state
            else None
        ),
        burst_period_s=(
            float(device.get_burst_period(channel=channel))
            if burst_state
            else None
        ),
        burst_delay_s=(
            float(device.get_burst_delay(channel=channel))
            if burst_state
            else None
        ),
        burst_trigger_source=(
            str(device.get_burst_trigger_source(channel=channel))
            if burst_state
            else None
        ),
        burst_trigger_slope=(
            str(device.get_burst_trigger_slope(channel=channel))
            if burst_state
            else None
        ),
        mod_state=bool(device.get_mod_state(channel=channel)),
        output_on=bool(device.get_output(channel=channel)),
    )


def restore_dg_channel(
    device: Any,
    channel: int,
    state: DGChannelState,
) -> None:
    """恢复扫描前的波形、Burst/调制开关和输出状态。

    DC 波形按「切 DC 形状 → 型号专用 DC 设置 → 最后恢复输出」的顺序
    执行。DG4000 使用 ``VOLTage:OFFSet``，DG900 使用 ``APPLy:DC``；
    输出状态始终最后恢复。
    """
    device.set_output(False, channel=channel)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    if str(state.shape).upper() == "DC":
        device.set_shape("DC", channel=channel)
        device.set_dc_voltage(float(state.offset_v), channel=channel)
    else:
        device.set_shape(state.shape, channel=channel)
        if state.frequency_hz is not None:
            device.set_frequency(state.frequency_hz, channel=channel)
        if state.amplitude_vpp is not None:
            device.set_amplitude(state.amplitude_vpp, channel=channel)
        device.set_offset(state.offset_v, channel=channel)
        if state.phase_deg is not None:
            device.set_phase_adjust(state.phase_deg, channel=channel)
    if state.burst_state:
        assert state.burst_mode is not None
        assert state.burst_ncycles is not None
        assert state.burst_phase_deg is not None
        assert state.burst_period_s is not None
        assert state.burst_delay_s is not None
        assert state.burst_trigger_source is not None
        assert state.burst_trigger_slope is not None
        device.set_burst_mode(state.burst_mode, channel=channel)
        device.set_burst_ncycles(state.burst_ncycles, channel=channel)
        device.set_burst_phase(state.burst_phase_deg, channel=channel)
        device.set_burst_period(state.burst_period_s, channel=channel)
        device.set_burst_delay(state.burst_delay_s, channel=channel)
        device.set_burst_trigger_source(
            state.burst_trigger_source,
            channel=channel,
        )
        device.set_burst_trigger_slope(
            state.burst_trigger_slope,
            channel=channel,
        )
    device.set_burst_state(state.burst_state, channel=channel)
    device.set_mod_state(state.mod_state, channel=channel)
    # 输出状态最后恢复，避免恢复过程中短暂输出中间电平。
    device.set_output(state.output_on, channel=channel)
