"""设备库、物理量绑定及其持久化校验。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

import yaml

from .common import find_project_root


class RevisionConflict(ValueError):
    """保存时客户端基于的配置修订已经过期。"""


@dataclass(frozen=True, slots=True)
class SignalGeneratorCapabilities:
    channels: tuple[int, ...] = (1, 2)
    min_arb_points: int = 2
    max_arb_points: int = 16384
    supports_arbitrary: bool = True
    supports_infinite_burst: bool = True
    max_frequency_hz: float | None = None


@dataclass(frozen=True, slots=True)
class Endpoint:
    kind: str
    index: int

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> Endpoint | None:
        if not payload:
            return None
        return cls(str(payload["kind"]), int(payload["index"]))


@dataclass(frozen=True, slots=True)
class DeviceDefinition:
    device_id: str
    instrument: str
    model: str
    label: str
    resource: str | None
    reference_clock: str | None = None
    connection: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_item(cls, device_id: str, payload: Mapping[str, Any]) -> DeviceDefinition:
        return cls(
            device_id=str(device_id),
            instrument=str(payload.get("instrument", "")).strip(),
            model=str(payload.get("model", "")).strip(),
            label=str(payload.get("label", device_id)).strip(),
            resource=(
                str(payload["resource"]).strip()
                if payload.get("resource") is not None
                else None
            ),
            reference_clock=(
                str(payload["reference_clock"]).strip().upper()
                if payload.get("reference_clock") is not None
                else None
            ),
            connection=dict(payload.get("connection") or {}),
            capabilities=dict(payload.get("capabilities") or {}),
        )

    def to_config(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("device_id")
        return result


@dataclass(frozen=True, slots=True)
class PhysicalBinding:
    mapping_key: str
    device_id: str
    instrument: str
    label: str
    endpoint: Endpoint | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MappingConstraint:
    name: str
    members: tuple[str, ...]


_CONFIG_LOCK = RLock()
_DEVICE_SCHEMA_VERSION = 1
_MAPPING_SCHEMA_VERSION = 2


def _root(root: Path | None = None) -> Path:
    return root or find_project_root()


def device_library_path(root: Path | None = None) -> Path:
    return _root(root) / "params" / "devices.yaml"


def physical_mapping_path(root: Path | None = None) -> Path:
    return _root(root) / "params" / "mapping.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def _revision(payload: Mapping[str, Any]) -> str:
    content = yaml.safe_dump(
        dict(payload), allow_unicode=True, sort_keys=True
    ).encode("utf-8")
    return sha256(content).hexdigest()


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    content = yaml.safe_dump(
        dict(payload), allow_unicode=True, sort_keys=False
    )
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


@contextmanager
def _process_config_lock(root: Path | None = None):
    """用同一锁文件串行化跨进程的设备库和映射保存。"""
    lock_path = _root(root) / "params" / ".instrument_config.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            unlock = lambda: msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        except ImportError:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            unlock = lambda: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        try:
            yield
        finally:
            stream.seek(0)
            unlock()


def _normalize_device_document(payload: Mapping[str, Any]) -> dict[str, Any]:
    devices = payload.get("devices") or {}
    if not isinstance(devices, Mapping):
        raise ValueError("devices.yaml 的 devices 必须是映射")
    normalized: dict[str, Any] = {
        "schema_version": _DEVICE_SCHEMA_VERSION,
        "devices": {},
    }
    resources: dict[str, str] = {}
    for raw_id, raw_device in devices.items():
        device_id = str(raw_id).strip()
        if not device_id or not isinstance(raw_device, Mapping):
            raise ValueError("设备 ID 和设备定义不能为空")
        device = DeviceDefinition.from_item(device_id, raw_device)
        if not device.instrument or not device.model or not device.label:
            raise ValueError(f"设备 {device_id} 缺少 instrument/model/label")
        if device.reference_clock not in {None, "INT", "EXT"}:
            raise ValueError(f"设备 {device_id} 的 reference_clock 只能为 INT/EXT")
        if device.resource:
            previous = resources.get(device.resource)
            if previous and previous != device_id:
                raise ValueError(
                    f"设备资源 {device.resource} 被 {previous} 与 {device_id} 重复定义"
                )
            resources[device.resource] = device_id
        normalized["devices"][device_id] = device.to_config()
    return normalized


def load_device_document(root: Path | None = None) -> dict[str, Any]:
    path = device_library_path(root)
    if not path.exists():
        return {"schema_version": _DEVICE_SCHEMA_VERSION, "devices": {}}
    return _normalize_device_document(_load_yaml(path))


def load_device_definitions(root: Path | None = None) -> dict[str, DeviceDefinition]:
    document = load_device_document(root)
    return {
        device_id: DeviceDefinition.from_item(device_id, config)
        for device_id, config in document["devices"].items()
    }


def _normalize_endpoint(config: Mapping[str, Any]) -> dict[str, Any] | None:
    endpoint = config.get("endpoint")
    if endpoint is not None:
        parsed = Endpoint.from_dict(endpoint)
        return {"kind": parsed.kind, "index": parsed.index} if parsed else None
    for field_name, kind in (
        ("channel", "channel"),
        ("demod_idx", "demod"),
        ("laser_channel", "laser_channel"),
    ):
        if config.get(field_name) is not None:
            return {"kind": kind, "index": int(config[field_name])}
    return None


def _normalize_mapping_document(payload: Mapping[str, Any]) -> dict[str, Any]:
    mappings = payload.get("mapping") or {}
    if not isinstance(mappings, Mapping):
        raise ValueError("mapping.yaml 的 mapping 必须是映射")
    normalized_mappings: dict[str, Any] = {}
    for raw_key, raw_config in mappings.items():
        key = str(raw_key)
        if not isinstance(raw_config, Mapping):
            raise ValueError(f"映射 {key} 定义无效")
        config = dict(raw_config)
        endpoint = _normalize_endpoint(config)
        for legacy_key in ("channel", "demod_idx", "laser_channel"):
            config.pop(legacy_key, None)
        if endpoint is not None:
            config["endpoint"] = endpoint
        normalized_mappings[key] = config
    constraints = payload.get("constraints") or {}
    normalized_constraints = {
        "shared_channel_groups": {
            str(name): [str(item) for item in members]
            for name, members in (constraints.get("shared_channel_groups") or {}).items()
        },
        "colocation_groups": {
            str(name): [str(item) for item in members]
            for name, members in (constraints.get("colocation_groups") or {}).items()
        },
    }
    return {
        "schema_version": _MAPPING_SCHEMA_VERSION,
        "mapping": normalized_mappings,
        "constraints": normalized_constraints,
    }


def load_mapping_document(root: Path | None = None) -> dict[str, Any]:
    return _normalize_mapping_document(_load_yaml(physical_mapping_path(root)))


def _endpoint_fields(endpoint: Endpoint | None) -> dict[str, Any]:
    if endpoint is None:
        return {}
    field_name = {
        "channel": "channel",
        "demod": "demod_idx",
        "laser_channel": "laser_channel",
    }.get(endpoint.kind)
    if field_name is None:
        raise ValueError(f"未知端点类型: {endpoint.kind}")
    return {field_name: endpoint.index}


def _device_channels(device: DeviceDefinition) -> set[int] | None:
    raw = device.capabilities.get("channels")
    return {int(value) for value in raw} if raw is not None else None


def _validate_configuration(
    devices_document: Mapping[str, Any],
    mappings_document: Mapping[str, Any],
) -> None:
    devices = {
        key: DeviceDefinition.from_item(key, value)
        for key, value in devices_document["devices"].items()
    }
    mappings = mappings_document["mapping"]
    constraints = mappings_document["constraints"]
    shared_membership: dict[str, str] = {}

    for group_name, members in constraints["shared_channel_groups"].items():
        if len(members) < 2 or len(set(members)) != len(members):
            raise ValueError(f"共享通道组 {group_name} 至少需要两个不重复物理量")
        for key in members:
            if key not in mappings:
                raise ValueError(f"共享通道组 {group_name} 引用了未知映射 {key}")
            if key in shared_membership:
                raise ValueError(f"映射 {key} 不能属于多个共享通道组")
            shared_membership[key] = group_name

    endpoints: dict[tuple[str, str, int], str] = {}
    for key, config in mappings.items():
        device_id = str(config.get("device_id", "")).strip()
        if not device_id:
            # 旧内联配置由兼容解析器处理，不允许通过新 API 再保存。
            if config.get("instrument") and (
                config.get("resource") is not None
                or config.get("instrument") == "lockin_amplifier"
            ):
                continue
            raise ValueError(f"映射 {key} 缺少 device_id")
        if device_id not in devices:
            raise ValueError(f"映射 {key} 引用了未知设备 {device_id}")
        device = devices[device_id]
        expected = str(config.get("instrument", "")).strip()
        if expected and expected != device.instrument:
            raise ValueError(
                f"映射 {key} 需要 {expected}，设备 {device_id} 是 {device.instrument}"
            )
        endpoint = Endpoint.from_dict(config.get("endpoint"))
        if endpoint is None:
            continue
        allowed_channels = _device_channels(device)
        if allowed_channels is not None and endpoint.index not in allowed_channels:
            raise ValueError(
                f"映射 {key} 的端点 {endpoint.index} 超出设备 {device_id} 能力"
            )
        marker = (device_id, endpoint.kind, endpoint.index)
        previous = endpoints.get(marker)
        if previous:
            first_group = shared_membership.get(previous)
            current_group = shared_membership.get(key)
            if not first_group or first_group != current_group:
                raise ValueError(
                    f"{previous} 与 {key} 冲突占用 {device_id} {endpoint.kind}{endpoint.index}"
                )
        endpoints[marker] = previous or key

    for group_name, members in constraints["shared_channel_groups"].items():
        markers = set()
        for key in members:
            config = mappings[key]
            endpoint = Endpoint.from_dict(config.get("endpoint"))
            markers.add(
                (
                    str(config.get("device_id", "")),
                    endpoint.kind if endpoint else None,
                    endpoint.index if endpoint else None,
                )
            )
        if len(markers) != 1:
            raise ValueError(f"共享通道组 {group_name} 的成员必须绑定同一设备端点")

    for group_name, members in constraints["colocation_groups"].items():
        if len(members) < 2 or len(set(members)) != len(members):
            raise ValueError(f"同机组 {group_name} 至少需要两个不重复物理量")
        unknown = [key for key in members if key not in mappings]
        if unknown:
            raise ValueError(f"同机组 {group_name} 引用了未知映射 {unknown}")
        device_ids = {str(mappings[key].get("device_id", "")) for key in members}
        if len(device_ids) != 1:
            raise ValueError(f"同机组 {group_name} 的成员必须绑定同一设备")


def resolve_mapping(root: Path | None = None) -> dict[str, dict[str, Any]]:
    devices_document = load_device_document(root)
    mappings_document = load_mapping_document(root)
    _validate_configuration(devices_document, mappings_document)
    devices = load_device_definitions(root)
    result: dict[str, dict[str, Any]] = {}
    for key, raw_config in mappings_document["mapping"].items():
        config = dict(raw_config)
        device_library_id = str(config.pop("device_id", "")).strip()
        if not device_library_id:
            # 兼容旧 mapping.yaml。
            endpoint = Endpoint.from_dict(config.pop("endpoint", None))
            result[key] = {**config, **_endpoint_fields(endpoint)}
            continue
        device = devices[device_library_id]
        endpoint = Endpoint.from_dict(config.pop("endpoint", None))
        connection = dict(device.connection)
        connection_device_id = str(
            connection.get("device_id") or device_library_id
        ).strip()
        result[key] = {
            "instrument": device.instrument,
            "model": device.model,
            "label": config.pop("label", device.label),
            "resource": device.resource,
            **connection,
            **config,
            "device_library_id": device_library_id,
            "device_id": connection_device_id,
            "reference_clock": device.reference_clock,
            "capabilities": dict(device.capabilities),
            **_endpoint_fields(endpoint),
        }
    return result


def public_device_library(root: Path | None = None) -> dict[str, Any]:
    document = load_device_document(root)
    return {**document, "revision": _revision(document)}


def public_physical_mappings(root: Path | None = None) -> dict[str, Any]:
    document = load_mapping_document(root)
    return {**document, "revision": _revision(document)}


def save_device_library(
    devices: Mapping[str, Any],
    *,
    base_revision: str,
    root: Path | None = None,
) -> dict[str, Any]:
    with _CONFIG_LOCK, _process_config_lock(root):
        current = load_device_document(root)
        if base_revision != _revision(current):
            raise RevisionConflict("设备库已被其他操作修改，请刷新后重试")
        proposed = _normalize_device_document(
            {"schema_version": _DEVICE_SCHEMA_VERSION, "devices": devices}
        )
        mappings = load_mapping_document(root)
        _validate_configuration(proposed, mappings)
        _atomic_write(device_library_path(root), proposed)
        return public_device_library(root)


def save_physical_mappings(
    mapping: Mapping[str, Any],
    constraints: Mapping[str, Any],
    *,
    base_revision: str,
    root: Path | None = None,
) -> dict[str, Any]:
    with _CONFIG_LOCK, _process_config_lock(root):
        current = load_mapping_document(root)
        if base_revision != _revision(current):
            raise RevisionConflict("物理量映射已被其他操作修改，请刷新后重试")
        proposed = _normalize_mapping_document(
            {
                "schema_version": _MAPPING_SCHEMA_VERSION,
                "mapping": mapping,
                "constraints": constraints,
            }
        )
        _validate_configuration(load_device_document(root), proposed)
        _atomic_write(physical_mapping_path(root), proposed)
        return public_physical_mappings(root)


def instrument_config_snapshot(root: Path | None = None) -> dict[str, Any]:
    devices = load_device_document(root)
    mappings = load_mapping_document(root)
    return {
        "device_library_revision": _revision(devices),
        "physical_mapping_revision": _revision(mappings),
        "device_library": devices,
        "physical_mappings": mappings,
        "resolved_mapping": resolve_mapping(root),
    }


def discover_visa_devices() -> dict[str, Any]:
    """只通过资源枚举和 ``*IDN?`` 发现 VISA 设备，不修改设备状态。"""
    import pyvisa

    manager = pyvisa.ResourceManager()
    discovered: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    try:
        resources = manager.list_resources()
        for resource in resources:
            instrument = None
            try:
                instrument = manager.open_resource(resource)
                instrument.timeout = 2000
                idn = str(instrument.query("*IDN?")).strip()
                upper = idn.upper()
                kind = model = "unknown"
                if "RIGOL" in upper and ("DG8" in upper or "DG9" in upper):
                    kind, model = "signal_generator", "DG900"
                elif "RIGOL" in upper and "DG4" in upper:
                    kind, model = "signal_generator", "DG4000"
                elif "YOKOGAWA" in upper and "GS" in upper:
                    kind, model = "gs200", "GS200"
                elif "KEITHLEY" in upper and "6221" in upper:
                    kind, model = "keithley_6221", "6221"
                elif "SIGLENT" in upper and "SDS" in upper:
                    kind, model = "sds_acquisition", "SDS"
                discovered.append(
                    {
                        "resource": str(resource),
                        "idn": idn,
                        "instrument": kind,
                        "model": model,
                    }
                )
            except Exception as exc:
                errors.append({"resource": str(resource), "message": str(exc)})
            finally:
                if instrument is not None:
                    instrument.close()
    finally:
        manager.close()
    return {"devices": discovered, "errors": errors}
