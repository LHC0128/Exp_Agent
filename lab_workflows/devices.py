"""根据 mapping.yaml 发现物理设备并复用连接信息。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from gs200 import GS200Instrument
from keithley_6221 import Keithley6221Instrument
from signal_generator import DG4000Instrument, DG900Instrument
from toptica_laser import DLCProInstrument

from .common import load_mapping
from .instrument_config import load_device_definitions


SIGNAL_GENERATOR_DRIVERS = {
    "DG4000": DG4000Instrument,
    "DG900": DG900Instrument,
}

CURRENT_SOURCE_DRIVERS = {
    "GS200": GS200Instrument,
    "6221": Keithley6221Instrument,
}

LASER_DRIVERS = {
    "DLC_PRO": DLCProInstrument,
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


def create_dlc_pro(config: Mapping[str, Any]) -> DLCProInstrument:
    """按 mapping.yaml 创建 DLC pro，不在此处建立网络连接。"""
    if config.get("instrument") != "toptica_dlc_pro":
        raise ValueError("配置不是 toptica_dlc_pro 映射")
    resource = str(config.get("resource", "")).strip()
    if not resource:
        raise ValueError("DLC pro 映射缺少 resource")
    return DLCProInstrument(
        resource,
        laser_channel=int(config.get("laser_channel", 1)),
        command_port=int(config.get("command_port", 1998)),
        monitoring_port=int(config.get("monitoring_port", 1999)),
        timeout=float(config.get("timeout", 5.0)),
    )


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
    mapping_keys: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.mapping_keys and self.mapping_key:
            self.mapping_keys = [self.mapping_key]


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
    """从设备库返回 GUI 可管理设备，并附加物理量绑定别名。"""
    mapping = load_mapping()
    library = load_device_definitions()
    records: dict[str, DeviceRecord] = {}
    device_records: dict[str, DeviceRecord] = {}

    for device_id, device in library.items():
        resource = device.resource
        if device.instrument == "signal_generator" and resource:
            short = _short_resource(resource)
            record = DeviceRecord(
                id=short,
                type=signal_generator_model(
                    {"instrument": "signal_generator", "model": device.model}
                ),
                label=device.label,
                resource=resource,
                short_resource=short,
                options={"device_id": device_id},
            )
            records[resource] = record
            device_records[device_id] = record
        elif device.instrument == "gs200" and resource:
            short = _short_resource(resource)
            record = DeviceRecord(
                id=short,
                type="GS200",
                label=device.label,
                resource=resource,
                short_resource=short,
                options={"device_id": device_id},
            )
            records[resource] = record
            device_records[device_id] = record
        elif device.instrument == "keithley_6221" and resource:
            short = _short_resource(resource)
            record = DeviceRecord(
                id=short,
                type="6221",
                label=device.label,
                resource=resource,
                short_resource=short,
                options={"device_id": device_id},
            )
            records[resource] = record
            device_records[device_id] = record
        elif device.instrument == "toptica_dlc_pro" and resource:
            control_id = str(device.connection.get("device_id", device_id))
            record = DeviceRecord(
                id=control_id,
                type="DLC_PRO",
                label=device.label,
                resource=resource,
                short_resource=str(
                    device.connection.get("controller_serial", control_id)
                ),
                options={"device_id": device_id, **device.connection},
            )
            records[f"toptica:{resource}"] = record
            device_records[device_id] = record
        elif device.instrument == "sds_acquisition" and resource:
            short = _short_resource(resource)
            record = DeviceRecord(
                id=short,
                type="SDS",
                label=device.label,
                resource=resource,
                short_resource=short,
                options={"device_id": device_id},
                channels=[
                    ChannelRecord(index, "scope_waveform", f"CH{index}")
                    for index in range(1, 5)
                ],
            )
            records[resource] = record
            device_records[device_id] = record

    for key, cfg in mapping.items():
        kind = cfg.get("instrument")
        resource = cfg.get("resource")
        channel = cfg.get("channel")
        device_id = str(cfg.get("device_id", ""))
        record = device_records.get(device_id)
        if not record:
            continue
        if kind == "toptica_dlc_pro":
            record.options.update({
                "mapping_key": key,
                "laser_channel": int(cfg.get("laser_channel", 1)),
                "current_safety_key": str(cfg.get("current_safety_key", "")),
                "temperature_safety_key": str(cfg.get("temperature_safety_key", "")),
                "pzt_safety_key": str(cfg.get("pzt_safety_key", "")),
                "scan_amplitude_safety_key": str(
                    cfg.get("scan_amplitude_safety_key", "")
                ),
            })
        elif kind in {"gs200", "keithley_6221"}:
            record.options.update({
                "mapping_key": key,
                "source_function": cfg.get("source_function", "CURRent"),
            })
        elif kind == "signal_generator" and resource and channel is not None:
            existing = next(
                (item for item in record.channels if item.number == int(channel)),
                None,
            )
            if existing:
                if key not in existing.mapping_keys:
                    existing.mapping_keys.append(key)
                    existing.label = " / ".join(
                        mapping[item].get("label", item)
                        for item in existing.mapping_keys
                    )
            else:
                record.channels.append(
                    ChannelRecord(
                        int(channel),
                        key,
                        cfg.get("label", key),
                        mapping_keys=[key],
                    )
                )
    return list(records.values())


def find_device(device_id: str) -> DeviceRecord:
    for record in discover_devices():
        if record.id == device_id:
            return record
    raise KeyError(f"未知设备: {device_id}")
