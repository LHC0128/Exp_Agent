import logging
import math
import time
from typing import List

from sds_acquisition.config import (
    AcquisitionConfig,
    AcquisitionResult,
    ChannelConfig,
    TriggerConfig,
)
from sds_acquisition.instrument import SDSInstrument
from sds_acquisition.waveform import (
    WaveformPreamble,
    convert_raw_to_voltage,
    build_time_axis,
    extract_waveform_data,
)

logger = logging.getLogger(__name__)


class SDSAcquisition:
    """采集编排器：配置仪器 → 触发 → 读取波形 → 数据转换."""

    def __init__(self, instrument: SDSInstrument):
        self._inst = instrument

    # ------------------------------------------------------------------
    # 完整配置应用
    # ------------------------------------------------------------------

    def apply_config(self, config: AcquisitionConfig) -> None:
        # 示波器在 RUN 状态下才能设置采样率等参数
        self._inst.trigger_run()
        time.sleep(0.1)
        self._configure_acquisition(config)
        self._configure_timebase(config)
        for ch in config.channels:
            self._configure_channel(ch)
        self._configure_trigger(config.trigger)

    def _configure_acquisition(self, config: AcquisitionConfig) -> None:
        if config.memory_management is not None:
            self._inst.set_memory_management(config.memory_management)
        self._inst.set_sampling_rate(config.sampling_rate)
        self._inst.set_acquire_type(config.acquire_type, config.acquire_type_param)

    def _configure_timebase(self, config: AcquisitionConfig) -> None:
        self._inst.set_timebase_scale(config.timebase_scale)
        self._inst.set_timebase_delay(config.timebase_delay)

    def _configure_channel(self, ch: ChannelConfig) -> None:
        self._inst.set_channel_state(ch.number, ch.enabled)
        if ch.enabled:
            self._inst.set_channel_scale(ch.number, ch.scale)
            self._inst.set_channel_offset(ch.number, ch.offset)
            self._inst.set_channel_coupling(ch.number, ch.coupling)
            self._inst.set_channel_impedance(ch.number, ch.impedance)
            self._inst.set_channel_probe(ch.number, ch.probe)

    def _configure_trigger(self, trig: TriggerConfig) -> None:
        self._inst.set_trigger_mode(trig.mode)
        self._inst.set_trigger_type(trig.type)
        self._inst.set_trigger_source(trig.source)
        self._inst.set_trigger_slope(trig.slope)
        self._inst.set_trigger_level(trig.level)

    # ------------------------------------------------------------------
    # 触发控制
    # ------------------------------------------------------------------

    def start_acquisition(self, config: AcquisitionConfig) -> None:
        # apply_config 已使示波器处于 RUN 状态并配置了触发
        if config.trigger.mode in ("AUTO", "FTRIG"):
            time.sleep(max(config.acquire_delay, 0.1))
        elif config.trigger.mode == "SINGle":
            # SINGle 模式下触发可能已发生, 等待采集完成
            self._inst.wait_for_trigger(timeout=10.0)
            time.sleep(config.acquire_delay)
        elif config.trigger.mode == "NORMal":
            self._inst.wait_for_trigger(timeout=10.0)
            self._inst.trigger_stop()
            time.sleep(config.acquire_delay)

    def stop_acquisition(self) -> None:
        self._inst.trigger_stop()

    # ------------------------------------------------------------------
    # 单通道波形读取
    # ------------------------------------------------------------------

    def acquire_channel(
        self,
        channel: int,
        timebase_scale: float,
        horiz_divisions: int = 10,
        trim_points: int = 0,
    ) -> AcquisitionResult:
        source = f"C{channel}"
        # Siglent 官方分片读取顺序先复位起点，再选择源并读取 preamble。
        # 起点会跨查询保留；若沿用上一轮的非零起点，整帧 POINT 设置可能
        # 越界，部分固件随后会用零长度二进制块响应 DATA?。
        self._inst.set_waveform_start(0)
        self._inst.set_waveform_source(source)

        # 显式设置字节序（大端序），确保 WORD 模式解析正确
        self._inst.write(":WAVeform:BYTeorder MSB")

        # 读取并解析 preamble
        preamble_raw = self._inst.get_waveform_preamble()
        preamble = WaveformPreamble(preamble_raw)
        logger.debug("通道 %d preamble: %s", channel, preamble)

        # 根据 ADC 位数选择数据宽度
        width = "WORD" if preamble.adc_bit > 8 else "BYTE"
        self._inst.set_waveform_width(width)

        # 分片读取处理深度存储
        total_points = preamble.point_num
        max_slice = self._inst.get_waveform_max_points()

        if total_points > max_slice:
            raw_data = self._read_multi_slice(total_points, max_slice)
        else:
            self._inst.set_waveform_points(total_points)
            raw_data = self._inst.get_waveform_data()

        # 二进制 → ADC 码
        adc_codes = extract_waveform_data(raw_data, width)

        # 裁剪到期望的点数（示波器常多返回一个点）
        if trim_points > 0 and len(adc_codes) != trim_points:
            if len(adc_codes) > trim_points:
                logger.debug("通道 %d: 裁剪 %d → %d 点", channel, len(adc_codes), trim_points)
                adc_codes = adc_codes[:trim_points]
            else:
                logger.warning("通道 %d: 点数不足 %d < %d", channel, len(adc_codes), trim_points)

        # ADC 码 → 电压
        voltage = convert_raw_to_voltage(
            adc_codes,
            preamble.vertical_gain,
            preamble.vertical_offset,
            preamble.code_per_div,
        )

        # 时间轴 — build_time_axis 自动从 preamble 推算 scope 实际 timebase_scale
        # 这样即使 SDS 仲裁后实际 timebase 与我们请求的不同, 时间轴仍准确
        time_axis = build_time_axis(
            len(adc_codes),
            preamble.horiz_interval,
            preamble.horiz_offset,
            horiz_divisions,
        )

        return AcquisitionResult(
            channel=channel,
            raw_data=adc_codes,
            voltage=voltage,
            time=time_axis,
            preamble_dict=preamble.to_dict(),
            config_snapshot={},
            timestamp=time.time(),
        )

    def _read_multi_slice(self, total_points: int, max_slice: int) -> bytes:
        """将深度存储分多次读取并拼接。"""
        self._inst.set_waveform_points(int(max_slice))
        num_slices = math.ceil(total_points / max_slice)
        accumulated = b""
        for i in range(num_slices):
            start = int(i * max_slice)
            self._inst.set_waveform_start(start)
            chunk = self._inst.get_waveform_data()
            accumulated += chunk
            logger.debug("分片 %d/%d: start=%d, 读取 %d bytes",
                         i + 1, num_slices, start, len(chunk))
        return accumulated

    # ------------------------------------------------------------------
    # 多通道完整采集
    # ------------------------------------------------------------------

    def acquire_all(self, config: AcquisitionConfig) -> List[AcquisitionResult]:
        """
        完整采集流程：
        1. 应用配置
        2. 启动触发并等待
        3. 停止采集（冻结波形）
        4. 依次读取各启用通道
        """
        self.apply_config(config)
        self.start_acquisition(config)
        self.stop_acquisition()

        results: List[AcquisitionResult] = []
        config_snapshot = config.to_dict()

        for ch in config.channels:
            if not ch.enabled:
                continue
            try:
                result = self.acquire_channel(
                    ch.number,
                    config.timebase_scale,
                    config.horizontal_divisions,
                    trim_points=config.total_points,
                )
                result.config_snapshot = config_snapshot
                results.append(result)
                logger.info("通道 %d: 采集 %d 点", ch.number, len(result.voltage))
            except Exception as e:
                logger.error("通道 %d 采集失败: %s", ch.number, e)

        return results
