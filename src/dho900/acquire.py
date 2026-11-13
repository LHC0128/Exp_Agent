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


class FrameMismatch(ValueError):
    """读回的整帧与配置回读不一致；该帧须整帧丢弃，不得当有效数据使用。"""


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
        self._auto_depth_cap: int | None = None

    def use_auto_depth(self, max_points: int | None) -> None:
        """由调用方（实验）选择：存储深度取 AUTO，超过 max_points 时改用该上限深度；None 恢复严格采样率。

        AUTO 下 config.sampling_rate 不参与配置，实际采样率与点数以回读为准并写入快照；
        max_points 是上限而不是精确深度，仪器在部分时基下只给得出更小的深度。
        """
        self._auto_depth_cap = None if max_points is None else _integer(max_points, "最大存储深度", 2, self._maximum_points)
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

    def _stable_depth_state(self, attempts: int = 16, interval_s: float = 0.25) -> tuple[str, float]:
        """连续两次回读的「存储深度 + 采样率」一致才认定仪器已按新时基重算完深度。

        固件改时基后先按旧深度给出采样率，随后才重算 AUTO 深度（真机实测 1 s 窗先回读
        625 kSa/s、再变 500 kSa/s），只比较采样率会在换挡瞬间误判为已稳定。
        """
        previous = (self._inst.get_memory_depth(), self._inst.get_sampling_rate())
        for _ in range(attempts):
            self._wait(interval_s)
            current = (self._inst.get_memory_depth(), self._inst.get_sampling_rate())
            if current == previous:
                return current
            previous = current
        raise TimeoutError("DHO900 深度/采样率回读持续变化，无法确定实际深度")

    def _points_from_rate(self, rate: float, duration: float) -> int:
        product = rate * duration
        points = round(product)
        if points < 2 or not math.isclose(product, points, rel_tol=RATE_REL_TOLERANCE, abs_tol=0):
            raise ValueError(f"DHO900 回读采样率 {rate:g} Sa/s × 窗口 {duration:g} s = {product:g} 点，不是整数点数")
        return points

    def _resolve_auto_depth(self, config: AcquisitionConfig, cap: int) -> tuple[int, str]:
        """深度由仪器按时基选定，cap 只是上限；实际深度以深度与采样率一起稳定的回读为准。

        固件在部分时基下给不出等于上限的深度（真机实测 2 ms/div 请求 100000 点得到 50000 点、
        100 µs/div 请求 10000 点得到 6250 点、1 s 窗请求 100000 点得到 100000 点），只要回读的
        实际深度不超过上限就按它采帧，不把上限当精确请求。
        """
        points = self._points_from_rate(self._stable_depth_state()[1], config.sampling_time)
        if points <= cap:
            return points, "AUTO"
        self._inst.set_memory_depth(cap)
        self._wait(0.1)
        points = self._points_from_rate(self._stable_depth_state()[1], config.sampling_time)
        if points > cap:
            raise ValueError(f"DHO900 实际深度 {points} 点超过上限 {cap} 点")
        return points, "capped"

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
        auto = self._auto_depth_cap is not None
        duration = _positive(config.sampling_time, "采集时间")
        _positive(config.horizontal_divisions, "水平格数")
        if config.horizontal_divisions != 10:
            raise ValueError("DHO900 基础采集只支持十格主时基")
        if config.memory_management is not None:
            raise NotImplementedError("DHO900 不支持 SDS 的独立存储管理模式；memory_management 须为 None，采集器严格校验实际率")
        if config.interpolation != "ON":
            raise NotImplementedError("基础接口不改变仪器插值设置，interpolation 只接受已有默认 ON")
        if auto:
            points = _integer(self._auto_depth_cap, "最大存储深度", 2, self._maximum_points)
            if points not in MEMORY_DEPTHS:
                raise ValueError(f"最大存储深度 {points} 不在 DHO900 可选存储深度 {MEMORY_DEPTHS} 中")
        else:
            requested_points = _positive(config.sampling_rate, "目标采样率") * duration
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
            auto = self._auto_depth_cap is not None
            if auto:
                self._inst.write(":ACQuire:MDEPth AUTO")
            else:
                self._inst.set_memory_depth(points)
            self._wait(0.1)
            if self._inst.query(":TIMebase:MODE?").upper() != "MAIN" or self._inst.query(":TIMebase:ROLL?") != "0":
                raise ValueError("DHO900 未进入主时基非 ROLL 模式，不能执行完整帧触发")
            if auto:
                actual_points, depth_mode = self._resolve_auto_depth(config, points)
            else:
                self._inst.set_sampling_rate(config.sampling_rate)
                actual_points, depth_mode = self._inst.get_actual_points(), "fixed"
            actual_scale = self._inst.get_timebase_scale()
            if (actual_points != points and not auto) or actual_points > points or not math.isclose(
                    actual_scale, config.timebase_scale, rel_tol=RATE_REL_TOLERANCE, abs_tol=0):
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
                              # 采样率由已核对的整帧点数与主时基换算，不用 :ACQuire:SRATe?：
                              # 固件重算深度期间该回读会短暂给出另一档采样率，写进快照就会误拒好帧。
                              "actual_sample_rate_sa_s": actual_points / (actual_scale * 10),
                              "actual_points": actual_points, "actual_timebase_s_div": actual_scale,
                              "sample_rate_policy": "auto_depth" if auto else "strict", "depth_mode": depth_mode,
                              "waveform_format": "ASCii",
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
        # DHO900 固件：已处于 RAW 时重写 MODE RAW 不刷新内部点数状态，POINts 会锁死在
        # 屏幕默认点数（实测 5000），深帧 preamble 点数因此与配置深度不符。先切 NORMal
        # 再切 RAW 强制重置波形读取状态机，POINts 才能正确设为整帧点数。
        self._inst.set_waveform_mode("NORMal")
        self._inst.set_waveform_mode("RAW")
        self._inst.set_waveform_width("ASCii")
        auto = self._configured and self._snapshot["depth_mode"] != "fixed"
        # AUTO 深度没有数字回读，点数取自 apply_config 的回读换算；下面的 preamble 仍须与之一致。
        frame_points = self._snapshot["actual_points"] if auto else self._inst.get_actual_points()
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
            raise FrameMismatch("DHO900 preamble 点数与整帧存储深度不符；拒绝把局部窗口当完整记录")
        if self._configured:
            rate = 1.0 / preamble.xincrement
            expected_rate = (self._snapshot["actual_sample_rate_sa_s"] if auto
                             else self._snapshot["requested"]["sampling_rate"])
            if preamble.points != self._snapshot["actual_points"] or not math.isclose(
                    rate, expected_rate, rel_tol=RATE_REL_TOLERANCE, abs_tol=0):
                raise FrameMismatch("DHO900 帧参数与已配置的严格采样请求不符；拒绝旧帧或改变后的配置")
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
        """配置、布防、停止并读取整帧；整帧参数与配置回读不符时丢帧重做一次。

        固件在配置回读之后仍可能重算 AUTO 深度（真机实测回读 625 kSa/s、实际 500 kSa/s），
        首帧因此被拒。重做按同一配置重新配置与布防，不复用被拒的旧帧。
        """
        try:
            return self._acquire_frame(config)
        except FrameMismatch:
            self._configured = False
            return self._acquire_frame(config)

    def _acquire_frame(self, config: AcquisitionConfig) -> list[AcquisitionResult]:
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
