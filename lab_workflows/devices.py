"""根据 mapping.yaml 发现物理设备并复用连接信息。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .common import load_mapping


@dataclass(slots=True)
class ChannelRecord:
    number: int
    mapping_key: str
    label: str
    read_only: bool = False


@dataclass(slots=True)
class DeviceRecord:
    id: str
    type: str
    label: str
    resource: str
    short_resource: str
    channels: list[ChannelRecord] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _short_resource(resource: str) -> str:
    fields = resource.split("::")
    return fields[3] if len(fields) > 3 else resource


def discover_devices() -> list[DeviceRecord]:
    """返回 GUI 可管理设备；共享 CH2 仅按 Y_magnetic_field 展示。"""
    mapping = load_mapping()
    records: dict[str, DeviceRecord] = {}
    channel_keys: set[tuple[str, int]] = set()

    for key, cfg in mapping.items():
        kind = cfg.get("instrument")
        resource = cfg.get("resource")
        channel = cfg.get("channel")
        if kind == "signal_generator" and resource and channel is not None:
            if key == "rf_coil":
                continue
            device_type = "DG4000" if "::0x0641::" in resource else "DG900"
            short = _short_resource(resource)
            record = records.setdefault(
                resource,
                DeviceRecord(
                    id=short,
                    type=device_type,
                    label=cfg.get("device_label", cfg.get("label", key)),
                    resource=resource,
                    short_resource=short,
                ),
            )
            marker = (resource, int(channel))
            if marker not in channel_keys:
                record.channels.append(
                    ChannelRecord(
                        int(channel),
                        key,
                        cfg.get("label", key),
                    )
                )
                channel_keys.add(marker)

    scope = mapping.get("scope_waveform", {})
    if scope.get("resource"):
        resource = scope["resource"]
        short = _short_resource(resource)
        records[resource] = DeviceRecord(
            id=short,
            type="SDS",
            label=scope.get("label", "示波器"),
            resource=resource,
            short_resource=short,
            channels=[
                ChannelRecord(index, "scope_waveform", f"CH{index}")
                for index in range(1, 5)
            ],
        )
    return list(records.values())


def find_device(device_id: str) -> DeviceRecord:
    for record in discover_devices():
        if record.id == device_id:
            return record
    raise KeyError(f"未知设备: {device_id}")
