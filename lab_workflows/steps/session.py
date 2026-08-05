"""按物理 resource 复用连接的设备会话。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .safety_shutdown import DisconnectTarget, disconnect_device_mapping, disconnect_devices


class DeviceSession:
    """保存已连接设备，避免同一物理仪器被重复打开。"""

    def __init__(self) -> None:
        self._by_resource: dict[str, Any] = {}
        self._semantic: dict[str, Any] = {}
        self._connection_order: list[Any] = []
        self._optional_connection_errors: dict[str, str] = {}

    def connect(
        self,
        semantic_name: str,
        resource: str,
        factory: Callable[[], Any],
    ) -> Any:
        if resource not in self._by_resource:
            device = factory()
            device.connect()
            self._by_resource[resource] = device
            self._connection_order.append(device)
        device = self._by_resource[resource]
        self._semantic[semantic_name] = device
        return device

    def connect_optional(
        self,
        semantic_name: str,
        resource: str,
        factory: Callable[[], Any],
        *,
        device_label: str | None = None,
    ) -> Any | None:
        """连接可选设备；失败时记录原因并允许实验继续。"""
        try:
            return self.connect(semantic_name, resource, factory)
        except Exception as exc:
            label = device_label or semantic_name
            message = (
                f"{label} 连接失败（{resource}）：{exc}。"
                "已跳过该可选设备，实验继续运行。"
            )
            self._semantic[semantic_name] = None
            self._optional_connection_errors[semantic_name] = str(exc)
            print(f"[警告] {message}")
            return None

    def bind(self, semantic_name: str, device: Any) -> Any:
        self._semantic[semantic_name] = device
        return device

    def get(self, semantic_name: str) -> Any:
        return self._semantic[semantic_name]

    def unique_devices(self) -> tuple[Any, ...]:
        return tuple(self._connection_order)

    def optional_connection_errors(self) -> dict[str, str]:
        """返回可选设备的连接失败记录副本。"""
        return dict(self._optional_connection_errors)

    def cleanup_connection_failure(self) -> None:
        disconnect_device_mapping({
            str(index): device
            for index, device in enumerate(reversed(self._connection_order))
        })
        self._connection_order.clear()
        self._by_resource.clear()
        self._semantic.clear()
        self._optional_connection_errors.clear()

    def disconnect_tec_only(self) -> None:
        tec = self._semantic.get("tec")
        errors = disconnect_devices((DisconnectTarget("TEC", tec),))
        if errors:
            raise RuntimeError("；".join(errors))

    def __contains__(self, semantic_name: str) -> bool:
        return semantic_name in self._semantic
