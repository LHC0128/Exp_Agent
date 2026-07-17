"""多个实验共用的声明式安全关闭与设备断开步骤。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..common import validate_safety_limit
from .temperature import set_temperature_switch


STANDARD_PRESERVED_OUTPUTS = (
    "main_magnetic_field",
    "Pump_laser_power",
    "Probe_laser_power",
    "Pump_modulation",
    "HF2_configuration",
)


@dataclass(frozen=True, slots=True)
class DGChannelShutdown:
    """一个需要归零并关闭的信号发生器通道。"""

    device: Any
    channel: int
    safety_key: str
    label: str
    disable_burst: bool = True
    disable_modulation: bool = True
    disable_sync: bool = False
    dc_value: float = 0.0


@dataclass(frozen=True, slots=True)
class TemperatureSwitchRestore:
    """需要恢复为开启状态的温度开关。"""

    device: Any
    channel: int
    label: str = "温度开关"


@dataclass(frozen=True, slots=True)
class ShutdownAction:
    """不属于 DG 通道的额外安全动作。"""

    label: str
    action: Callable[[], None]


@dataclass(frozen=True, slots=True)
class DisconnectTarget:
    """结束时需要断开的设备。"""

    label: str
    device: Any


@dataclass(frozen=True, slots=True)
class SafetyShutdownReport:
    """安全关闭结果，可直接写入 experiment_config.yaml。"""

    action_errors: tuple[str, ...]
    disconnect_errors: tuple[str, ...]
    preserved_outputs: tuple[str, ...]

    @property
    def errors(self) -> tuple[str, ...]:
        return self.action_errors + self.disconnect_errors

    @property
    def completed(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "completed": self.completed,
            "errors": list(self.errors),
            "action_errors": list(self.action_errors),
            "disconnect_errors": list(self.disconnect_errors),
            "preserved_outputs": list(self.preserved_outputs),
        }


def _attempt(errors: list[str], label: str, action: Callable[[], None]) -> None:
    try:
        action()
    except Exception as exc:
        errors.append(f"{label}: {exc}")


def _shutdown_dg_channel(errors: list[str], spec: DGChannelShutdown) -> None:
    """逐项执行，单个命令失败时仍继续尝试后续安全动作。"""

    def checked(action: Callable[[], None]) -> None:
        validate_safety_limit(spec.safety_key, spec.dc_value)
        action()

    if spec.disable_burst:
        _attempt(
            errors,
            f"关闭 {spec.label} Burst 失败",
            lambda: checked(lambda: spec.device.set_burst_state(False, channel=spec.channel)),
        )
    if spec.disable_sync:
        _attempt(
            errors,
            f"关闭 {spec.label} 同步输出失败",
            lambda: checked(lambda: spec.device.set_sync_state(False, channel=spec.channel)),
        )
    if spec.disable_modulation:
        _attempt(
            errors,
            f"关闭 {spec.label} 调制失败",
            lambda: checked(lambda: spec.device.set_mod_state(False, channel=spec.channel)),
        )
    _attempt(
        errors,
        f"归零 {spec.label} 失败",
        lambda: checked(lambda: spec.device.setup_dc(spec.dc_value, channel=spec.channel)),
    )
    _attempt(
        errors,
        f"关闭 {spec.label} 输出失败",
        lambda: checked(lambda: spec.device.set_output(False, channel=spec.channel)),
    )


def disconnect_devices(targets: Iterable[DisconnectTarget]) -> tuple[str, ...]:
    """按对象去重后断开设备，并返回全部错误。"""
    errors: list[str] = []
    seen: set[int] = set()
    for target in targets:
        device = target.device
        if device is None or id(device) in seen:
            continue
        seen.add(id(device))
        if not hasattr(device, "disconnect"):
            continue
        _attempt(errors, f"断开 {target.label} 失败", device.disconnect)
    return tuple(errors)


def disconnect_device_mapping(devices: Mapping[str, Any]) -> tuple[str, ...]:
    """连接阶段失败时断开映射中所有已连接设备。"""
    return disconnect_devices(
        DisconnectTarget(label=name, device=device)
        for name, device in devices.items()
    )


def run_safety_shutdown(
    *,
    dg_channels: Iterable[DGChannelShutdown] = (),
    temperature_switch: TemperatureSwitchRestore | None = None,
    extra_actions: Iterable[ShutdownAction] = (),
    disconnect_targets: Iterable[DisconnectTarget] = (),
    preserved_outputs: Iterable[str] = STANDARD_PRESERVED_OUTPUTS,
) -> SafetyShutdownReport:
    """执行统一安全关闭；具体通道与保留输出由各工作流显式声明。"""
    action_errors: list[str] = []
    for spec in dg_channels:
        if spec.device is not None:
            _shutdown_dg_channel(action_errors, spec)
    for action in extra_actions:
        _attempt(action_errors, action.label, action.action)
    if temperature_switch is not None and temperature_switch.device is not None:
        _attempt(
            action_errors,
            f"恢复 {temperature_switch.label}失败",
            lambda: set_temperature_switch(
                temperature_switch.device,
                True,
                channel=temperature_switch.channel,
                settle_time=0.0,
            ),
        )
    disconnect_errors = disconnect_devices(disconnect_targets)
    return SafetyShutdownReport(
        action_errors=tuple(action_errors),
        disconnect_errors=disconnect_errors,
        preserved_outputs=tuple(preserved_outputs),
    )
