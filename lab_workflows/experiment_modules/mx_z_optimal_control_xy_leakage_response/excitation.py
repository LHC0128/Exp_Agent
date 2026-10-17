"""同形 XY 任意波、带符号幅度与共同触发配置。"""
from typing import Any
import numpy as np
from ...common import validate_safety_limit
from ...steps import ArbitraryWaveformSpec, upload_arbitrary, OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE
from .scan import signed_control_hardware


def _configure_xy_idle(devices, channels):
    """共同触发开启前把 X/Y 安全置为 0 V 且关闭。"""
    for name, safety_key in (("x_field", "X_magnetic_field"), ("y_rf", "Y_magnetic_field")):
        validate_safety_limit(safety_key, 0.)
        device, channel = devices["xy_field"], channels[name]
        device.set_output(False, channel=channel)
        device.set_burst_state(False, channel=channel)
        device.set_mod_state(False, channel=channel)
        device.setup_dc(0., channel=channel)
        device.set_output(False, channel=channel)


class _XYWaveformController:
    """保留 USER 缓存；零点明确切到 DC，负幅度增加 180°。"""

    def __init__(self, device, trigger_device, channels, theory, applied, params):
        self.device, self.trigger_device, self.channels = device, trigger_device, channels
        self.theory, self.applied, self.params = theory, applied, params
        # 重扫时物理通道可能仍是上轮任意波，不能假定初始模式为 DC。
        self.mode = {"x_field": "unknown", "y_rf": "unknown"}

    def _configure_channel(self, channel_name, safety_key, signed_amplitude_vpp):
        channel = self.channels[channel_name]
        amplitude, phase, output_on = signed_control_hardware(signed_amplitude_vpp, self.params.control_burst_phase_deg)
        self.device.set_output(False, channel=channel)
        if not output_on:
            validate_safety_limit(safety_key, 0.)
            if self.mode[channel_name] != "dc":
                self.device.set_burst_state(False, channel=channel)
                self.device.set_mod_state(False, channel=channel)
                self.device.setup_dc(0., channel=channel)
                self.mode[channel_name] = "dc"
            self.device.set_output(False, channel=channel)
            return dict(hardware_amplitude_vpp=0., hardware_phase_deg=phase, output_on=False)
        half_range = amplitude / 2.
        for voltage in (-half_range, half_range, np.min(self.applied.normalized) * half_range,
                        np.max(self.applied.normalized) * half_range):
            validate_safety_limit(safety_key, float(voltage))
        if self.mode[channel_name] != "aw":
            upload_arbitrary(self.device, ArbitraryWaveformSpec(values=self.applied.normalized,
                frequency=self.theory.repeat_frequency_hz, amplitude=amplitude, offset=0., phase=0.,
                channel=channel, output=False))
            self.device.set_frequency(self.theory.repeat_frequency_hz, channel=channel)
            self.device.set_offset(0., channel=channel)
            self.device.set_burst_state(True, channel=channel)
            self.device.set_burst_mode("INFinity", channel=channel)
            self.device.set_burst_trigger_source("EXTernal", channel=channel)
            self.device.set_burst_trigger_slope(OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE, channel=channel)
            self.mode[channel_name] = "aw"
        self.device.set_amplitude(amplitude, channel=channel)
        self.device.set_burst_phase(phase, channel=channel)
        return dict(hardware_amplitude_vpp=amplitude, hardware_phase_deg=phase, output_on=True)

    def apply(self, x_signed_vpp, y_signed_vpp) -> dict[str, Any]:
        """关闭触发，配置并同时 arm XY，重新开启共同触发；不重 arm Z。"""
        self.trigger_device.set_output(False, channel=self.channels["trigger"])
        self.device.set_output(False, channel=self.channels["x_field"])
        self.device.set_output(False, channel=self.channels["y_rf"])
        hardware = {}
        for axis, name, key, value in (("x", "x_field", "X_magnetic_field", x_signed_vpp),
                                      ("y", "y_rf", "Y_magnetic_field", y_signed_vpp)):
            result = self._configure_channel(name, key, value)
            hardware.update({f"{axis}_{key}": value for key, value in result.items()})
        for axis, name in (("x", "x_field"), ("y", "y_rf")):
            if hardware[f"{axis}_output_on"]:
                self.device.set_output(True, channel=self.channels[name])
        self.trigger_device.set_output(True, channel=self.channels["trigger"])
        return hardware
