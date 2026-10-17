"""X/Y 空闲置零与三轴平衡场的实际输出控制。"""

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


class _XYZFieldController:
    """设置 X/Y DG4000 DC 与 GS200 Z 电流。"""

    def __init__(
        self,
        xy_device: Any,
        gs200: Any,
        channels: dict[str, int],
    ) -> None:
        self.xy_device = xy_device
        self.gs200 = gs200
        self.channels = channels
        self._last_z_ma: float | None = None

    def apply(self, x_v: float, y_v: float, z_ma: float) -> None:
        x_v = float(validate_safety_limit("X_magnetic_field", x_v))
        y_v = float(validate_safety_limit("Y_magnetic_field", y_v))
        z_ma = float(validate_safety_limit("main_magnetic_field", z_ma))
        if self._last_z_ma is None or z_ma != self._last_z_ma:
            self.gs200.set_current(z_ma / 1000.0)
            self.gs200.set_output(True)
            self._last_z_ma = z_ma
        for channel_name, value in (("x_field", x_v), ("y_rf", y_v)):
            channel = self.channels[channel_name]
            self.xy_device.set_burst_state(False, channel=channel)
            self.xy_device.set_mod_state(False, channel=channel)
            self.xy_device.setup_dc(value, channel=channel)
            # 扫描零点也保持 Output ON，避免零点引入输出状态跳变。
            self.xy_device.set_output(True, channel=channel)
