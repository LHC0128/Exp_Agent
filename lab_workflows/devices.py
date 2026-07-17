"""根据 mapping.yaml 发现物理设备并复用连接信息。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from signal_generator import DG4000Instrument, DG900Instrument

from .common import load_mapping


SIGNAL_GENERATOR_DRIVERS = {
    "DG4000": DG4000Instrument,
    "DG900": DG900Instrument,
}


def signal_generator_model(config: Mapping[str, Any]) -> str:
    if config.get('instrument') != 'signal_generator':
        raise ValueError('Configuration is not a signal_generator mapping')
    model = str(config.get("model", "")).strip().upper()
    if not model:
        raise ValueError('Signal generator mapping is missing the model field')
    if model not in SIGNAL_GENERATOR_DRIVERS:
        raise ValueError(f'Unsupported signal generator model: {model}')
    return model


def _resolve_signal_generator_config(config: Any) -> Mapping[str, Any]:
    if not isinstance(config, str):
        return config
    matches = [
        item
        for item in load_mapping().values()
        if item.get('instrument') == 'signal_generator'
        and item.get('resource') == config
    ]
    if not matches:
        raise KeyError(f'No signal generator mapping for resource: {config}')
    models = {signal_generator_model(item) for item in matches}
    if len(models) != 1:
        raise ValueError(f'Conflicting models for resource: {config}')
    return matches[0]


def create_signal_generator(
    config: Any,
    *,
    channel: int | None = None,
) -> Any:
    config = _resolve_signal_generator_config(config)
    model = signal_generator_model(config)
    resource = config.get('resource')
    if not resource:
        raise ValueError('Signal generator mapping is missing the resource field')
    selected_channel = channel if channel is not None else config.get('channel')
    if selected_channel is None:
        raise ValueError('Signal generator mapping is missing the channel field')
    driver = SIGNAL_GENERATOR_DRIVERS[model]
    return driver(str(resource), channel=int(selected_channel))


def signal_generator_max_arb_points(config: Any) -> int:
    config = _resolve_signal_generator_config(config)
    driver = SIGNAL_GENERATOR_DRIVERS[signal_generator_model(config)]
    max_points = getattr(driver, 'MAX_ARB_POINTS', None)
    if max_points is None:
        raise ValueError('Configured signal generator does not support arbitrary waves')
    return int(max_points)


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
            device_type = signal_generator_model(cfg)
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
            if record.type != device_type:
                raise ValueError(
                    f'Conflicting models for resource {resource}: '
                    f'{record.type} / {device_type}'
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
