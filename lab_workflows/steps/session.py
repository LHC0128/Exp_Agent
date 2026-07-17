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

    def bind(self, semantic_name: str, device: Any) -> Any:
        self._semantic[semantic_name] = device
        return device

    def get(self, semantic_name: str) -> Any:
        return self._semantic[semantic_name]

    def unique_devices(self) -> tuple[Any, ...]:
        return tuple(self._connection_order)

    def cleanup_connection_failure(self) -> None:
        disconnect_device_mapping({
            str(index): device
            for index, device in enumerate(reversed(self._connection_order))
        })
        self._connection_order.clear()
        self._by_resource.clear()
        self._semantic.clear()

    def disconnect_tec_only(self) -> None:
        tec = self._semantic.get("tec")
        errors = disconnect_devices((DisconnectTarget("TEC", tec),))
        if errors:
            raise RuntimeError("；".join(errors))

    def __contains__(self, semantic_name: str) -> bool:
        return semantic_name in self._semantic
