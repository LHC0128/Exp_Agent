"""SDS 固定采样率波形采集与自动量程共享步骤。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
from sds_acquisition import (
    AcquisitionConfig,
    ChannelConfig,
    SDSAcquisition,
    SDSInstrument,
    TriggerConfig,
)

logger = logging.getLogger(__name__)

_SCOPE_FORCE_TRIGGER_TIMEOUT_GUARD_S = 2.5
_SCOPE_FORCE_TRIGGER_POLL_S = 0.05
_SCOPE_FORCE_TRIGGER_SETTLE_S = 0.2
_SCOPE_SOFT_RESET_SETTLE_S = 4.0


@dataclass(frozen=True, slots=True)
class ScopeCaptureSettings:
    """SDS 单通道固定采样率采集设置。"""

    sample_rate_sa_s: float
    duration_s: float
    pd_channel: int
    trigger_mode: str
    initial_scale_v_div: float
    offset_v: float
    vertical_divisions: int
    scale_min_v_div: float
    scale_max_v_div: float
    auto_range_low_fraction: float
    auto_range_high_fraction: float
    auto_offset_tolerance_fraction: float
    auto_range_max_attempts: int
    welch_nperseg: int
    maximum_frequency_hz: float
    coupling: str = "DC"
    record_max_attempts: int = 3
    memory_management: str = "FSRate"
    auto_offset_enabled: bool = True

    @property
    def requested_points(self) -> int:
        return int(self.sample_rate_sa_s * self.duration_s)

    @property
    def capture_wait_s(self) -> float:
        """返回 SDS 强制触发帧完成的最长等待时间。"""
        return float(self.duration_s) + _SCOPE_FORCE_TRIGGER_TIMEOUT_GUARD_S

    def vertical_half_span_v(self, scale_v_div: float) -> float:
        """返回显示中心到上/下边缘的电压范围。"""
        return float(scale_v_div) * self.vertical_divisions / 2.0


@dataclass(slots=True)
class ScopeAutoRangeState:
    """跨采集帧保持的 SDS 垂直量程和偏置。"""

    scale_v_div: float
    offset_v: float = 0.0
    allow_shrink: bool = True


def next_auto_range_scale(
    settings: ScopeCaptureSettings,
    current_scale_v_div: float,
    peak_deviation_v: float,
    *,
    allow_shrink: bool = True,
) -> float:
    """根据波形相对中心的峰值偏差返回下一次量程。"""
    half_span_v = settings.vertical_half_span_v(current_scale_v_div)
    if allow_shrink and (
        peak_deviation_v < settings.auto_range_low_fraction * half_span_v
        and current_scale_v_div > settings.scale_min_v_div * 2.0
    ):
        return current_scale_v_div / 2.0
    if (
        peak_deviation_v > settings.auto_range_high_fraction * half_span_v
        and current_scale_v_div < settings.scale_max_v_div / 2.0
    ):
        return current_scale_v_div * 2.0
    return current_scale_v_div


def next_auto_offset(
    settings: ScopeCaptureSettings,
    current_scale_v_div: float,
    current_offset_v: float,
    waveform_min_v: float,
    waveform_max_v: float,
) -> float:
    """按 SDS offset 符号约定返回使波形居中的偏置。"""
    waveform_center_v = 0.5 * (waveform_min_v + waveform_max_v)
    target_offset_v = -waveform_center_v
    half_span_v = settings.vertical_half_span_v(current_scale_v_div)
    deadband_v = settings.auto_offset_tolerance_fraction * half_span_v
    if abs(target_offset_v - current_offset_v) <= deadband_v:
        return current_offset_v
    return target_offset_v


def configure_fixed_rate_scope(
    settings: ScopeCaptureSettings,
    devices: dict[str, Any],
    *,
    sleep: Callable[[float], None],
) -> tuple[AcquisitionConfig, dict[str, Any]]:
    """配置 SDS 单通道 AUTO 采集并回读关键实际参数。"""
    channels = [
        ChannelConfig(
            number=channel,
            enabled=channel == settings.pd_channel,
            scale=settings.initial_scale_v_div,
            offset=settings.offset_v,
            coupling=settings.coupling,
            impedance="ONEMeg",
            probe=1.0,
        )
        for channel in range(1, 5)
    ]
    config = AcquisitionConfig(
        sampling_rate=settings.sample_rate_sa_s,
        sampling_time=settings.duration_s,
        acquire_type="NORMal",
        memory_management=settings.memory_management,
        acquire_delay=settings.duration_s,
        channels=channels,
        trigger=TriggerConfig(
            mode=settings.trigger_mode,
            source=f"C{settings.pd_channel}",
            type="EDGE",
            slope="RISing",
            level=0.0,
        ),
    )
    acquirer: SDSAcquisition = devices["acquirer"]
    scope: SDSInstrument = devices["scope"]
    acquirer.apply_config(config)
    sleep(0.1)
    actual_memory_management = str(scope.get_memory_management()).strip()
    if actual_memory_management.upper() != settings.memory_management.upper():
        raise ValueError(
            "示波器未进入固定采样率存储模式："
            f"期望 {settings.memory_management}，实际 {actual_memory_management}"
        )
    actual_rate_sa_s = float(scope.get_sampling_rate())
    if actual_rate_sa_s <= 2.0 * settings.maximum_frequency_hz:
        raise ValueError(
            "示波器真实采样率不足："
            f"{actual_rate_sa_s:.9g} Sa/s 的 Nyquist 不能覆盖 "
            f"{settings.maximum_frequency_hz:.9g} Hz"
        )
    actual_points = int(scope.get_actual_points())
    if actual_points < settings.welch_nperseg:
        raise ValueError(
            f"示波器实际点数 {actual_points} 少于 "
            f"WELCH_NPERSEG={settings.welch_nperseg}"
        )
    actual_scale = float(scope.get_channel_scale(settings.pd_channel))
    actual_offset = float(scope.get_channel_offset(settings.pd_channel))
    if not np.isfinite(actual_scale) or actual_scale <= 0.0:
        raise ValueError("示波器回读的初始通道量程无效")
    if not np.isfinite(actual_offset):
        raise ValueError("示波器回读的初始通道偏置不是有限值")
    return config, {
        "requested_sample_rate_sa_s": settings.sample_rate_sa_s,
        "actual_sample_rate_sa_s": actual_rate_sa_s,
        "requested_duration_s": settings.duration_s,
        "requested_points": settings.requested_points,
        "actual_points": actual_points,
        "requested_memory_management": settings.memory_management,
        "actual_memory_management": actual_memory_management,
        "memory_depth": str(scope.get_memory_depth()),
        "pd_channel": settings.pd_channel,
        "trigger_mode": settings.trigger_mode,
        "frame_trigger_action": (
            "FTRIG"
            if settings.trigger_mode.strip().upper() == "AUTO"
            else settings.trigger_mode
        ),
        "requires_input_trigger_edge": (
            settings.trigger_mode.strip().upper() != "AUTO"
        ),
        "trigger_source": f"C{settings.pd_channel}",
        "coupling": settings.coupling,
        "impedance": "1 MOhm",
        "probe": "1x",
        "offset_v": settings.offset_v,
        "auto_offset_enabled": settings.auto_offset_enabled,
        "initial_scale_v_div": settings.initial_scale_v_div,
        "actual_initial_offset_v": actual_offset,
        "actual_initial_scale_v_div": actual_scale,
    }


def temperature_gated_acquire(
    *,
    temp_switch: Any,
    temp_channel: int,
    off_settle_s: float,
    on_settle_s: float,
    acquire: Callable[[], Any],
    check_cancelled: Callable[[], None],
    sleep: Callable[[float], None],
    set_temperature_switch: Callable[..., None],
    cancellation: Any,
) -> Any:
    """每次采集前关闭温控，并在任何退出路径恢复 5 V ON。"""
    check_cancelled()
    set_temperature_switch(temp_switch, False, channel=temp_channel)
    try:
        sleep(off_settle_s)
        return acquire()
    finally:
        set_temperature_switch(
            temp_switch,
            True,
            channel=temp_channel,
            settle_time=on_settle_s,
            cancellation=cancellation,
        )


def read_complete_scope_record(
    settings: ScopeCaptureSettings,
    devices: dict[str, Any],
    scope_config: AcquisitionConfig,
    *,
    check_cancelled: Callable[[], None],
    sleep: Callable[[float], None],
) -> Any:
    """强制提交完整帧后读取；零长度传输时软复位一次并恢复配置。"""
    scope: SDSInstrument = devices["scope"]
    acquirer: SDSAcquisition = devices["acquirer"]
    last_problem = "未开始采集"
    soft_reset_performed = False
    for attempt in range(1, settings.record_max_attempts + 1):
        check_cancelled()
        scope.trigger_run()
        scope.set_trigger_mode(settings.trigger_mode)
        # 噪声采集不依赖输入边沿的位置。AUTO 自由运行在无边沿时可能迟迟
        # 不提交深存储帧，因此显式发送 FTRIG，等待该一次性动作完成并恢复
        # 到 AUTO。SDS1204X HD 实测温控关闭时可连续返回完整 500k 点。
        if settings.trigger_mode.strip().upper() == "AUTO":
            scope.set_trigger_mode("FTRIG")
            force_deadline = time.monotonic() + settings.capture_wait_s
            while (
                str(scope.get_trigger_mode()).strip().upper() == "FTRIG"
            ):
                if time.monotonic() >= force_deadline:
                    last_problem = (
                        f"第 {attempt} 次强制触发在 "
                        f"{settings.capture_wait_s:.3g} s 内未完成"
                    )
                    logger.warning(last_problem)
                    break
                check_cancelled()
                sleep(_SCOPE_FORCE_TRIGGER_POLL_S)
            sleep(_SCOPE_FORCE_TRIGGER_SETTLE_S)
        else:
            sleep(settings.capture_wait_s)
        check_cancelled()

        scope.trigger_stop()
        stop_deadline = time.monotonic() + 2.0
        while str(scope.trigger_status()).strip().upper() != "STOP":
            if time.monotonic() >= stop_deadline:
                raise RuntimeError("示波器 STOP 状态确认超时")
            sleep(0.05)

        result = acquirer.acquire_channel(
            settings.pd_channel,
            scope_config.timebase_scale,
            scope_config.horizontal_divisions,
            trim_points=0,
        )
        voltage = np.asarray(result.voltage, dtype=float).reshape(-1)
        time_s = np.asarray(result.time, dtype=float).reshape(-1)
        preamble_points = int(result.preamble_dict.get("point_num", 0))
        minimum_points = max(settings.welch_nperseg, preamble_points)
        if voltage.size < minimum_points:
            last_problem = (
                f"第 {attempt} 次读取仅得到 {voltage.size} 点，"
                f"preamble={preamble_points} 点，最低要求 {minimum_points} 点"
            )
            if voltage.size == 0 and not soft_reset_performed:
                # SDS1204X HD V1.1.3.8 存在波形导出状态卡死：preamble
                # 仍声明完整记录，但 DATA? 持续返回 #9000000000。软复位可
                # 恢复导出通道；随后重放实验配置及当前自动量程状态。
                current_scale = float(
                    scope.get_channel_scale(settings.pd_channel)
                )
                current_offset = float(
                    scope.get_channel_offset(settings.pd_channel)
                )
                logger.warning(
                    "%s；执行一次示波器软复位并恢复采集配置",
                    last_problem,
                )
                try:
                    scope.reset()
                    sleep(_SCOPE_SOFT_RESET_SETTLE_S)
                    check_cancelled()
                    acquirer.apply_config(scope_config)
                    scope.set_channel_scale(settings.pd_channel, current_scale)
                    scope.set_channel_offset(settings.pd_channel, current_offset)
                    sleep(0.2)
                except Exception as exc:
                    raise RuntimeError(
                        f"{last_problem}；示波器软复位恢复失败：{exc}"
                    ) from exc
                soft_reset_performed = True
            continue
        if time_s.size != voltage.size or time_s.size < 2:
            last_problem = (
                f"第 {attempt} 次读取的时间轴 {time_s.size} 点与波形 "
                f"{voltage.size} 点不一致"
            )
            continue
        sample_interval_s = float(np.median(np.diff(time_s)))
        actual_duration_s = sample_interval_s * (time_s.size - 1)
        minimum_duration_s = max(0.0, settings.duration_s - 2.0 * sample_interval_s)
        if (
            not np.isfinite(sample_interval_s)
            or sample_interval_s <= 0.0
            or actual_duration_s < minimum_duration_s
        ):
            last_problem = (
                f"第 {attempt} 次读取时长仅 {actual_duration_s:.9g} s，"
                f"请求 {settings.duration_s:.9g} s"
            )
            continue
        return result
    raise RuntimeError(
        f"示波器连续 {settings.record_max_attempts} 次未获得完整记录：{last_problem}"
    )


def acquire_autoranged_waveform(
    settings: ScopeCaptureSettings,
    scope_config: AcquisitionConfig,
    scope: SDSInstrument,
    auto_range: ScopeAutoRangeState,
    *,
    capture: Callable[[], Any],
    check_cancelled: Callable[[], None],
) -> dict[str, Any]:
    """联合调整量程和 offset，返回最终接受波形及诊断信息。"""
    attempt_scales: list[float] = []
    attempt_offsets: list[float] = []
    attempt_abs_max: list[float] = []
    attempt_min: list[float] = []
    attempt_max: list[float] = []
    attempt_center: list[float] = []
    attempt_peak_deviation: list[float] = []
    attempt_display_edge_fraction: list[float] = []
    result: Any | None = None
    accepted_scale = float(auto_range.scale_v_div)
    accepted_offset = float(auto_range.offset_v)

    for attempt in range(settings.auto_range_max_attempts):
        check_cancelled()
        scale_used = float(auto_range.scale_v_div)
        offset_used = float(auto_range.offset_v)
        attempt_scales.append(scale_used)
        attempt_offsets.append(offset_used)
        result = capture()
        voltage = np.asarray(result.voltage, dtype=float).reshape(-1)
        if voltage.size < settings.welch_nperseg:
            raise RuntimeError(
                f"示波器有效样本数 {voltage.size} 少于 "
                f"WELCH_NPERSEG={settings.welch_nperseg}"
            )
        if not np.all(np.isfinite(voltage)):
            raise RuntimeError("示波器 PD 波形包含非有限值")
        waveform_min_v = float(np.min(voltage))
        waveform_max_v = float(np.max(voltage))
        waveform_center_v = 0.5 * (waveform_min_v + waveform_max_v)
        peak_deviation_v = 0.5 * (waveform_max_v - waveform_min_v)
        display_center_v = -offset_used
        peak_from_display_center_v = max(
            abs(waveform_min_v - display_center_v),
            abs(waveform_max_v - display_center_v),
        )
        half_span_v = settings.vertical_half_span_v(scale_used)
        attempt_abs_max.append(float(np.max(np.abs(voltage))))
        attempt_min.append(waveform_min_v)
        attempt_max.append(waveform_max_v)
        attempt_center.append(waveform_center_v)
        attempt_peak_deviation.append(peak_deviation_v)
        attempt_display_edge_fraction.append(peak_from_display_center_v / half_span_v)
        scale_metric_v = (
            peak_deviation_v
            if settings.auto_offset_enabled
            else peak_from_display_center_v
        )
        next_scale = next_auto_range_scale(
            settings,
            scale_used,
            scale_metric_v,
            allow_shrink=auto_range.allow_shrink,
        )
        next_offset = (
            next_auto_offset(
                settings,
                scale_used,
                offset_used,
                waveform_min_v,
                waveform_max_v,
            )
            if settings.auto_offset_enabled
            else offset_used
        )
        if (
            next_offset != offset_used
            and next_scale < scale_used
            and peak_from_display_center_v
            >= settings.auto_range_high_fraction * half_span_v
        ):
            next_scale = scale_used
        accepted_scale = scale_used
        accepted_offset = offset_used
        scale_changed = next_scale != scale_used
        offset_changed = next_offset != offset_used
        if not scale_changed and not offset_changed:
            break
        if scale_changed:
            scope.set_channel_scale(settings.pd_channel, next_scale)
            actual_scale = float(scope.get_channel_scale(settings.pd_channel))
            if not np.isfinite(actual_scale) or actual_scale <= 0.0:
                raise RuntimeError("示波器回读的通道量程无效")
        else:
            actual_scale = scale_used
        auto_range.scale_v_div = actual_scale
        if offset_changed:
            scope.set_channel_offset(settings.pd_channel, next_offset)
            actual_offset = float(scope.get_channel_offset(settings.pd_channel))
        else:
            actual_offset = offset_used
        if not np.isfinite(actual_offset):
            raise RuntimeError("示波器回读的通道偏置不是有限值")
        auto_range.offset_v = actual_offset
        for channel_config in scope_config.channels:
            if channel_config.number == settings.pd_channel:
                channel_config.scale = actual_scale
                channel_config.offset = actual_offset
                break
        if attempt + 1 < settings.auto_range_max_attempts:
            result = None
            continue
        break

    if result is None:
        raise RuntimeError("示波器自动量程未获得有效波形")
    time_s = np.asarray(result.time, dtype=float).reshape(-1)
    voltage_v = np.asarray(result.voltage, dtype=float).reshape(-1)
    if time_s.size != voltage_v.size or time_s.size < 2:
        raise RuntimeError("示波器时间轴与 PD 波形长度不一致")
    sample_interval_s = float(np.median(np.diff(time_s)))
    if not np.isfinite(sample_interval_s) or sample_interval_s <= 0.0:
        raise RuntimeError("示波器真实采样间隔无效")
    actual_rate_sa_s = 1.0 / sample_interval_s
    if actual_rate_sa_s <= 2.0 * settings.maximum_frequency_hz:
        raise RuntimeError(
            "示波器波形真实采样率不足："
            f"{actual_rate_sa_s:.9g} Sa/s 的 Nyquist 不能覆盖 "
            f"{settings.maximum_frequency_hz:.9g} Hz"
        )
    return {
        "result": result,
        "time_s": time_s,
        "voltage_v": voltage_v,
        "actual_rate_sa_s": actual_rate_sa_s,
        "actual_duration_s": sample_interval_s * max(0, time_s.size - 1),
        "scale_used_v_div": accepted_scale,
        "offset_used_v": accepted_offset,
        "next_scale_v_div": float(auto_range.scale_v_div),
        "next_offset_v": float(auto_range.offset_v),
        "attempt_count": len(attempt_scales),
        "attempt_scales_v_div": np.asarray(attempt_scales, dtype=float),
        "attempt_offsets_v": np.asarray(attempt_offsets, dtype=float),
        "attempt_abs_max_v": np.asarray(attempt_abs_max, dtype=float),
        "attempt_min_v": np.asarray(attempt_min, dtype=float),
        "attempt_max_v": np.asarray(attempt_max, dtype=float),
        "attempt_center_v": np.asarray(attempt_center, dtype=float),
        "attempt_peak_deviation_v": np.asarray(attempt_peak_deviation, dtype=float),
        "attempt_display_edge_fraction": np.asarray(
            attempt_display_edge_fraction, dtype=float
        ),
    }
