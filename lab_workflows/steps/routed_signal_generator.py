"""把一组逻辑通道路由到一个或多个实际信号发生器。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from typing import Any, Mapping

from ..devices import create_signal_generator
from .session import DeviceSession


@dataclass(frozen=True, slots=True)
class SignalGeneratorRoute:
    """单个逻辑通道对应的实际设备、端点与物理量。"""

    device: Any
    actual_channel: int
    mapping_key: str


class RoutedSignalGenerator:
    """保持驱动调用形式不变，并按逻辑通道转发到实际设备。"""

    def __init__(self, routes: Mapping[int, SignalGeneratorRoute]) -> None:
        if not routes:
            raise ValueError("信号源路由不能为空")
        self._routes = dict(routes)
        self._primary = next(iter(self._routes.values())).device

    @property
    def routes(self) -> dict[int, SignalGeneratorRoute]:
        return dict(self._routes)

    def iter_physical_routes(self) -> tuple[SignalGeneratorRoute, ...]:
        """按物理设备去重，供参考时钟和状态快照使用。"""
        result: list[SignalGeneratorRoute] = []
        seen: set[int] = set()
        for route in self._routes.values():
            marker = id(route.device)
            if marker in seen:
                continue
            seen.add(marker)
            result.append(route)
        return tuple(result)

    def __getattr__(self, name: str) -> Any:
        primary_attr = getattr(self._primary, name)
        if not callable(primary_attr):
            return primary_attr

        @wraps(primary_attr)
        def routed_call(*args: Any, **kwargs: Any) -> Any:
            logical_channel = kwargs.get("channel")
            if logical_channel is None:
                return primary_attr(*args, **kwargs)
            try:
                route = self._routes[int(logical_channel)]
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"未知信号源逻辑通道: {logical_channel}") from exc
            forwarded = dict(kwargs)
            forwarded["channel"] = route.actual_channel
            return getattr(route.device, name)(*args, **forwarded)

        return routed_call


def connect_signal_generator_routes(
    session: DeviceSession,
    semantic_name: str,
    bindings: Mapping[str, tuple[str, Mapping[str, Any]]],
    *,
    logical_channels: Mapping[str, int] | None = None,
) -> tuple[RoutedSignalGenerator, dict[str, int]]:
    """连接并组合多个物理量绑定，返回代理及工作流逻辑通道。"""
    routes: dict[int, SignalGeneratorRoute] = {}
    channels: dict[str, int] = {}
    endpoint_tokens: dict[tuple[int, int], int] = {}
    next_synthetic = 1000

    for channel_name, (mapping_key, config) in bindings.items():
        resource = str(config.get("resource", "")).strip()
        if not resource:
            raise ValueError(f"{mapping_key} 缺少信号源 resource")
        actual_channel = int(config["channel"])
        device = session.connect(
            f"{semantic_name}:{mapping_key}",
            resource,
            lambda config=config: create_signal_generator(config),
        )
        endpoint = (id(device), actual_channel)
        preferred = (
            int(logical_channels[channel_name])
            if logical_channels and channel_name in logical_channels
            else None
        )
        if endpoint in endpoint_tokens:
            logical_channel = endpoint_tokens[endpoint]
        elif preferred is not None:
            if preferred in routes:
                raise ValueError(
                    f"{semantic_name} 逻辑通道 {preferred} 被多个实际端点占用"
                )
            logical_channel = preferred
        elif actual_channel not in routes:
            logical_channel = actual_channel
        else:
            while next_synthetic in routes:
                next_synthetic += 1
            logical_channel = next_synthetic
            next_synthetic += 1
        endpoint_tokens[endpoint] = logical_channel
        routes[logical_channel] = SignalGeneratorRoute(
            device=device,
            actual_channel=actual_channel,
            mapping_key=mapping_key,
        )
        channels[channel_name] = logical_channel

    routed = RoutedSignalGenerator(routes)
    session.bind(semantic_name, routed)
    return routed, channels
