"""DHO900 采集器；复用 SDS 的配置/结果结构，不复用其通信或波形解码。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import math
import time
from typing import Callable

import numpy as np
from sds_acquisition.config import AcquisitionConfig, AcquisitionResult

from .instrument import (
    DHO900Instrument, MEMORY_DEPTHS, RATE_REL_TOLERANCE,
    _channel, _finite, _integer, _positive, _source,
)


@dataclass(frozen=True)
class _Preamble:
    format: int
    mode: int
    points: int
    count: int
    xincrement: float
    xorigin: float
    xreference: float
    yincrement: float
    yorigin: float
    yreference: float

    @classmethod
    def parse(cls, response: str) -> _Preamble:
        fields = response.split(",")
        if len(fields) != 10:
            raise ValueError("DHO900 preamble 必须包含十个字段")
        values = [_finite(float(value), "preamble") for value in fields]
        return cls(
            _integer(values[0], "波形格式", 0, 2), _integer(values[1], "读取模式", 0, 2),
            _integer(values[2], "帧点数", 1, 50_000_000), _integer(values[3], "平均次数", 1, 65536),
            _positive(values[4], "采样间隔"), *values[5:],
        )


class DHO900Acquisition:
    """同名 apply_config/acquire_channel/acquire_all；严格率、停止后整帧读取。"""

    def __init__(self, instrument: DHO900Instrument, *,
                 check_cancelled: Callable[[], None] | None = None,
                 maximum_points: int | None = None):
        self._inst = instrument
        self._check_cancelled = check_cancelled or (lambda: None)
        self._maximum_points = (50_000_000 if maximum_points is None else
                                _integer(maximum_points, "采集点数预算", 1, 50_000_000))
        self._snapshot: dict = {}
        self._configured = False

    def _wait(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while True:
            self._check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.05, remaining))

    def _arm_single(self) -> None:
        self._inst.set_trigger_mode("SINGle")
        self._inst.check_errors()
        if self._inst.get_trigger_mode() != "SINGle":
            raise ValueError("DHO900 未接受单次布防，拒绝读取旧帧")

    def _wait_stopped(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while self._inst.trigger_status() != "Stop":
            self._check_cancelled()
            if time.monotonic() >= deadline:
                raise TimeoutError("DHO900 等待完整帧停止超时；未读取旧帧")
            self._wait(0.05)
        self._check_cancelled()

    def _require_live_acquisition(self) -> None:
        recording = self._inst.query(":RECord:WRECord:ENABle?")
        replay = self._inst.query(":RECord:WREPlay:OPERate?").upper()
        if recording != "0" or replay != "STOP":
            raise ValueError("DHO900 录制或回放状态不允许单次采集；请在仪器上关闭，未自动覆盖用户设置")

    def _validate(self, config: AcquisitionConfig) -> int:
        rate = _positive(config.sampling_rate, "目标采样率")
        duration = _positive(config.sampling_time, "采集时间")
        _positive(config.horizontal_divisions, "水平格数")
        if config.horizontal_divisions != 10:
            raise ValueError("DHO900 基础采集只支持十格主时基")
        if config.memory_management is not None:
            raise NotImplementedError("DHO900 不支持 SDS 的独立存储管理模式；memory_management 须为 None，采集器严格校验实际率")
        if config.interpolation != "ON":
            raise NotImplementedError("基础接口不改变仪器插值设置，interpolation 只接受已有默认 ON")
        requested_points = rate * duration
        if not math.isfinite(requested_points):
            raise ValueError("目标记录点数必须为有限整数")
        rounded_points = round(requested_points)
        if not math.isclose(requested_points, rounded_points, rel_tol=1e-12, abs_tol=0):
            raise ValueError("目标采样率与采集时间的乘积须为整数点数")
        points = _integer(rounded_points, "目标记录点数", 2, self._maximum_points)
        if points not in MEMORY_DEPTHS:
            raise ValueError(f"严格采样请求需要 {points} 点，DHO900 可选存储深度为 {MEMORY_DEPTHS}；未自动改采样率")
        enabled = [channel for channel in config.channels if channel.enabled]
        numbers = [_channel(channel.number) for channel in config.channels]
        if len(numbers) != len(set(numbers)) or not enabled:
            raise ValueError("通道不能重复且至少启用一个模拟通道")
        maximum = 50_000_000 if len(enabled) == 1 else 25_000_000 if len(enabled) == 2 else 10_000_000
        if points > maximum:
            raise ValueError(f"启用 {len(enabled)} 个通道时 DHO900 最大深度为 {maximum} 点")
        for channel in config.channels:
            if not isinstance(channel.enabled, bool):
                raise ValueError("通道开关须为 bool")
            if not channel.enabled:
                continue
            _positive(channel.scale, "垂直档位")
            _finite(channel.offset, "通道偏移")
            if channel.coupling.upper() not in {"AC", "DC", "GND"}:
                raise ValueError("通道耦合须为 AC/DC/GND")
            self._inst.set_channel_impedance(channel.number, channel.impedance)
            probe = _positive(channel.probe, "探头比")
            if probe not in {factor * 10 ** power for factor in (1, 2, 5) for power in range(-3, 5)}:
                raise ValueError("探头比不在 DHO900 的可选范围")
        trigger = config.trigger
        self._inst.validate_trigger_mode(trigger.mode)
        self._inst.validate_trigger_slope(trigger.slope)
        _source(trigger.source)
        if trigger.type.upper() != "EDGE":
            raise NotImplementedError("基础接口仅支持 EDGE 触发")
        _finite(trigger.level, "触发电平")
        _finite(config.timebase_delay, "时基偏移")
        if _finite(config.acquire_delay, "采集等待") < 0:
            raise ValueError("采集等待不能为负数")
        self._inst.validate_acquire_type(config.acquire_type, config.acquire_type_param)
        return points

    def _stop_after_failure(self) -> None:
        # 原始异常（包括取消）优先；停止失败不能掩盖它。
        try:
            self._inst.trigger_stop()
        except Exception:
            pass

    def apply_config(self, config: AcquisitionConfig) -> None:
        """配置只在全部静态校验通过后写入，成功后停止，不触发正式采集。"""
        points = self._validate(config)
        self._configured = False
        self._check_cancelled()
        self._require_live_acquisition()
        self._inst.check_errors()
        try:
            self._inst.trigger_run()
            self._wait(0.1)
            enabled = {channel.number for channel in config.channels if channel.enabled}
            # 显式关闭未请求通道，确保通道数/深度约束不依赖前面板残留状态。
            for number in range(1, 5):
                self._inst.set_channel_state(number, number in enabled)
            for channel in config.channels:
                if not channel.enabled:
                    continue
                self._check_cancelled()
                self._inst.set_channel_probe(channel.number, channel.probe)
                self._inst.set_channel_coupling(channel.number, channel.coupling)
                self._inst.set_channel_scale(channel.number, channel.scale)
                self._inst.set_channel_offset(channel.number, channel.offset)
                if self._inst.get_channel_units(channel.number).upper() != "VOLT":
                    raise ValueError("DHO900 通道幅度单位不是 VOLT；未自动修改单位")
            self._inst.set_acquire_type(config.acquire_type, config.acquire_type_param)
            self._inst.write(":TIMebase:ROLL 0")
            self._inst.write(":TIMebase:MODE MAIN")
            self._inst.write(":TIMebase:DELay:ENABle OFF")
            self._inst.set_timebase_scale(config.timebase_scale)
            self._inst.set_timebase_delay(config.timebase_delay)
            self._inst.set_memory_depth(points)
            self._wait(0.1)
            if self._inst.query(":TIMebase:MODE?").upper() != "MAIN" or self._inst.query(":TIMebase:ROLL?") != "0":
                raise ValueError("DHO900 未进入主时基非 ROLL 模式，不能执行完整帧触发")
            self._inst.set_sampling_rate(config.sampling_rate)
            actual_points = self._inst.get_actual_points()
            actual_scale = self._inst.get_timebase_scale()
            if actual_points != points or not math.isclose(actual_scale, config.timebase_scale,
                                                          rel_tol=RATE_REL_TOLERANCE, abs_tol=0):
                raise ValueError(f"DHO900 记录窗口不匹配：目标 {points} 点/{config.sampling_time:g} s，"
                                 f"实际 {actual_points} 点/{actual_scale * 10:g} s")
            trigger = config.trigger
            self._inst.set_trigger_type(trigger.type)
            self._inst.set_trigger_source(trigger.source)
            self._inst.set_trigger_slope(trigger.slope)
            self._inst.set_trigger_level(trigger.level)
            self._inst.set_trigger_mode(trigger.mode)
            self._inst.check_errors()
            self._inst.trigger_stop()
            self._wait_stopped(2.0)
            self._snapshot = {"requested": config.to_dict(), "model": "DHO900",
                              "actual_sample_rate_sa_s": self._inst.get_sampling_rate(),
                              "actual_points": actual_points, "actual_timebase_s_div": actual_scale,
                              "sample_rate_policy": "strict", "waveform_format": "ASCii",
                              "frame_trigger_mode": "SINGle",
                              "input_impedance_source": "型号固定 1 MΩ；非查询回读"}
            self._configured = True
        except BaseException:
            self._stop_after_failure()
            raise

    def start_acquisition(self, config: AcquisitionConfig) -> None:
        if not self._configured or self._snapshot.get("requested") != config.to_dict():
            raise ValueError("请先对当前配置成功调用 apply_config")
        self._check_cancelled()
        mode = self._inst.validate_trigger_mode(config.trigger.mode)
        self._require_live_acquisition()
        self._inst.check_errors()
        try:
            self._inst.trigger_stop()
            if mode == "AUTO":
                # 普通/单次模式的 TFORce 是手册明确支持的强制触发；等待 STOP 确认本帧完成。
                self._arm_single()
                self._inst.write(":TFORce")
                self._inst.check_errors()
                self._wait_stopped(config.sampling_time + 5.0)
            else:
                # 采集器读取单个冻结记录；NORMal 的边沿条件用单次布防完成，避免连续重触发后 STOP 截帧。
                self._arm_single()
                self._wait_stopped(config.sampling_time + 10.0)
            # 保持本次完成后的 SINGLE/STOP，不恢复 AUTO/NORM 重启采集；请求模式保存在快照。
            self._wait(config.acquire_delay)
        except BaseException:
            self._stop_after_failure()
            raise

    def stop_acquisition(self) -> None:
        self._inst.trigger_stop()
        self._wait_stopped(2.0)

    def acquire_channel(self, channel: int, timebase_scale: float, horiz_divisions: int = 10,
                        trim_points: int = 0) -> AcquisitionResult:
        """读取已经冻结的 RAW 整帧；ASCII 已是电压，不作第二次 ADC 缩放。"""
        channel = _channel(channel)
        _positive(timebase_scale, "请求时基")
        _positive(horiz_divisions, "水平格数")
        trim_points = _integer(trim_points, "裁剪点数", 0, self._maximum_points)
        self._check_cancelled()
        if self._inst.trigger_status() != "Stop":
            raise ValueError("DHO900 RAW 波形必须在 STOP 状态读取；未自动停止调用方的采集")
        if not self._inst.get_channel_state(channel):
            raise ValueError(f"DHO900 C{channel} 未启用")
        if self._inst.get_channel_units(channel).upper() != "VOLT":
            raise ValueError("通道幅度单位不是 VOLT，拒绝当作电压数据")
        self._inst.set_waveform_source(f"C{channel}")
        self._inst.set_waveform_mode("RAW")
        self._inst.set_waveform_width("ASCii")
        frame_points = self._inst.get_actual_points()
        if frame_points > self._maximum_points:
            raise ValueError("波形点数超出本次允许范围")
        self._inst.set_waveform_start(0)
        # POINts 与起止窗口会跨查询保留；先恢复整帧，再核对 preamble，不能把旧分块当整帧。
        self._inst.write(f":WAVeform:POINts {frame_points}")
        self._inst.set_waveform_points(frame_points)
        preamble = _Preamble.parse(self._inst.get_waveform_preamble())
        if preamble.format != 2 or preamble.mode != 2:
            raise ValueError("DHO900 未进入 ASCII/RAW 波形读取模式")
        if preamble.points != frame_points:
            raise ValueError("DHO900 preamble 点数与整帧存储深度不符；拒绝把局部窗口当完整记录")
        if self._configured:
            rate = 1.0 / preamble.xincrement
            if preamble.points != self._snapshot["actual_points"] or not math.isclose(
                    rate, self._snapshot["requested"]["sampling_rate"], rel_tol=RATE_REL_TOLERANCE, abs_tol=0):
                raise ValueError("DHO900 帧参数与已配置的严格采样请求不符；拒绝旧帧或改变后的配置")
        values = np.empty(preamble.points, dtype=np.float64)
        budget = self._inst.get_waveform_max_points()
        for start in range(0, preamble.points, budget):
            self._check_cancelled()
            count = min(budget, preamble.points - start)
            self._inst.set_waveform_start(0)
            self._inst.set_waveform_points(start + count)
            self._inst.set_waveform_start(start)
            text = self._inst.get_waveform_data().decode("ascii").strip()
            fields = text.split(",")
            if len(fields) != count:
                raise ValueError(f"DHO900 波形分块点数不符：期望 {count}，实际 {len(fields)}")
            chunk = np.array([float(value) for value in fields], dtype=np.float64)
            if not np.isfinite(chunk).all():
                raise ValueError("DHO900 波形包含非有限电压值")
            values[start:start + count] = chunk
        self._check_cancelled()
        self._inst.check_errors()
        if self._inst.trigger_status() != "Stop":
            raise ValueError("读取期间 DHO900 离开 STOP，整帧一致性无法确认")
        if trim_points:
            if trim_points > len(values):
                raise ValueError("DHO900 帧点数不足，不补点或插值")
            values = values[:trim_points].copy()
        axis = preamble.xorigin + (np.arange(len(values), dtype=np.float64) - preamble.xreference) * preamble.xincrement
        metadata = {**asdict(preamble), "model": "DHO900", "point_num": preamble.points,
                    "horiz_interval": preamble.xincrement, "raw_encoding": "unavailable_ascii_voltage"}
        return AcquisitionResult(channel=channel, raw_data=np.array([], dtype=np.int16), voltage=values,
                                 time=axis, preamble_dict=metadata, config_snapshot=deepcopy(self._snapshot),
                                 timestamp=time.time())

    def acquire_all(self, config: AcquisitionConfig) -> list[AcquisitionResult]:
        self.apply_config(config)
        completed = False
        try:
            self.start_acquisition(config)
            self.stop_acquisition()
            results = [self.acquire_channel(channel.number, config.timebase_scale, config.horizontal_divisions,
                                            trim_points=self._snapshot["actual_points"])
                       for channel in config.channels if channel.enabled]
            completed = True
            return results
        finally:
            if completed:
                self._inst.trigger_stop()  # 正常路径的通信失败须报告，不能伪称收尾成功。
            else:
                self._stop_after_failure()
