"""三轴 DC 配置；Z 偏置只改 offset，不重写最优控制波形。"""
from typing import Any
from ...common import validate_safety_limit

def _configure_xy_idle(
    devices: dict[str, Any],
    channels: dict[str, int],
) -> None:
    """启动最优控制前把 X/Y 安全置为 0 V 且关闭。"""
    xy = devices["xy_field"]
    for channel_name, safety_key in (
        ("x_field", "X_magnetic_field"),
        ("y_rf", "Y_magnetic_field"),
    ):
        validate_safety_limit(safety_key, 0.0)
        channel = channels[channel_name]
        xy.set_output(False, channel=channel)
        xy.set_burst_state(False, channel=channel)
        xy.set_mod_state(False, channel=channel)
        xy.setup_dc(0.0, channel=channel)
        xy.set_output(False, channel=channel)


def _validate_bias_envelope(
    applied: Any,
    bias_v: float,
    output_vpp: float,
) -> float:
    """校验偏置叠加后的完整 Z 任意波包络并返回总 offset。"""
    bias = float(bias_v)
    target_offset = float(applied.offset_v) + bias
    values = (
        float(applied.minimum_v) + bias,
        float(applied.maximum_v) + bias,
        target_offset - abs(float(output_vpp)) / 2.0,
        target_offset + abs(float(output_vpp)) / 2.0,
    )
    for value in values:
        validate_safety_limit("Z_magnetic_field", value)
    return target_offset


class _XYZBiasController:
    """设置 X/Y DC，并仅通过 DG4000 offset 叠加 Z 偏置。"""

    def __init__(
        self,
        xy_device: Any,
        z_device: Any,
        channels: dict[str, int],
        applied: Any,
        output_vpp: float,
    ) -> None:
        self.xy_device = xy_device
        self.z_device = z_device
        self.channels = channels
        self.applied = applied
        self.output_vpp = float(output_vpp)
        self._last_bias_v: float | None = None

    def apply(self, x_v: float, y_v: float, bias_v: float) -> None:
        x_v = float(validate_safety_limit("X_magnetic_field", x_v))
        y_v = float(validate_safety_limit("Y_magnetic_field", y_v))
        target_offset = _validate_bias_envelope(
            self.applied,
            bias_v,
            self.output_vpp,
        )
        if self._last_bias_v is None or float(bias_v) != self._last_bias_v:
            self.z_device.set_offset(
                target_offset,
                channel=self.channels["z_field"],
            )
            self._last_bias_v = float(bias_v)
        for channel_name, value in (("x_field", x_v), ("y_rf", y_v)):
            channel = self.channels[channel_name]
            self.xy_device.set_burst_state(False, channel=channel)
            self.xy_device.set_mod_state(False, channel=channel)
            self.xy_device.setup_dc(value, channel=channel)
            self.xy_device.set_output(True, channel=channel)
