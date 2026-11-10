"""人工接线图的完整快照、差异比较与独立持久化。

现场图和各实验目标图独立投影用途映射；只读配置和端口档案，不连接仪器。
任一用途不能完整唯一迁移时保留整张原图，提示不代表硬件状态。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
import threading
from typing import Annotated, Callable, Literal, Mapping, TypedDict

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_serializer, model_validator
import yaml

from lab_workflows.device_ports import (
    DevicePortProfile, DevicePortsStorageError, NonEmptyString, PhysicalPort,
    effective_port_direction, read_device_ports,
)
from lab_workflows.instrument_config import (
    RevisionConflict, load_device_document as load_device_library, load_mapping_document,
)


GridCoordinate = Annotated[int, Field(strict=True, ge=0, le=20000, multiple_of=20)]


class _WiringModel(BaseModel):
    """接线图的每层边界均拒绝额外字段和隐式类型转换。"""

    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")


class WiringEndpoint(_WiringModel):
    device_id: NonEmptyString
    port_id: NonEmptyString


class WiringInternalPort(_WiringModel):
    id: NonEmptyString
    name: NonEmptyString
    direction: Literal["in", "out"]


class WiringSettingOption(_WiringModel):
    value: NonEmptyString
    label: NonEmptyString


class WiringSetting(_WiringModel):
    id: NonEmptyString
    name: NonEmptyString
    options: list[WiringSettingOption] = Field(min_length=1)
    value: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_setting_choices(self) -> WiringSetting:
        values = [option.value for option in self.options]
        if len(values) != len(set(values)):
            raise ValueError("图内设置的选项 value 不得重复")
        if self.value is not None and self.value not in values:
            raise ValueError("图内设置的选定值必须引用已有选项 value")
        return self


class WiringNode(_WiringModel):
    device: DevicePortProfile
    x: GridCoordinate
    y: GridCoordinate
    follow_mapping: list[NonEmptyString] | None = None
    port_directions: dict[NonEmptyString, Literal["in", "out"]] = Field(default_factory=dict)
    port_usage: dict[NonEmptyString, Literal["used", "unused", "unconfirmed"]] = Field(default_factory=dict)
    internal_ports: list[WiringInternalPort] = Field(default_factory=list)
    settings: list[WiringSetting] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_node_choices(self) -> WiringNode:
        ports = {port.id: port for port in self.device.ports}
        internal_ids = [port.id for port in self.internal_ports]
        if len(internal_ids) != len(set(internal_ids)):
            raise ValueError("同一节点内的内部端口 ID 不得重复")
        if set(internal_ids).intersection(ports):
            raise ValueError("内部端口 ID 不得与同一节点的物理端口 ID 重名")
        setting_ids = [setting.id for setting in self.settings]
        if len(setting_ids) != len(set(setting_ids)):
            raise ValueError("同一节点内的图内设置 ID 不得重复")
        if any(key not in ports or ports[key].direction != "inout" for key in self.port_directions):
            raise ValueError("工作方向仅允许引用节点已有的 inout 物理端口")
        if any(key not in ports for key in self.port_usage):
            raise ValueError("使用状态仅允许引用节点已有的物理端口")
        if self.follow_mapping is not None and len(self.follow_mapping) != len(set(self.follow_mapping)):
            raise ValueError("跟随用途键不得重复")
        return self


class _WiringEdgeRequired(TypedDict):
    id: str
    source: WiringEndpoint
    target: WiringEndpoint
    signal_type: Literal["optical", "electrical", "internal"]
    label: str


class _WiringEdgeSerialized(_WiringEdgeRequired, total=False):
    connector: str
    cable_id: str
    note: str


class WiringEdge(_WiringModel):
    id: NonEmptyString
    source: WiringEndpoint
    target: WiringEndpoint
    signal_type: Literal["optical", "electrical", "internal"]
    label: str
    connector: NonEmptyString | None = None
    cable_id: NonEmptyString | None = None
    note: NonEmptyString | None = None

    @field_validator("connector", "cable_id", "note", mode="before")
    @classmethod
    def normalize_cable_text(cls, value: object) -> object:
        """空串与纯空白视为未填写，避免旧图产生无意义差异。"""
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def validate_cable_fields(self) -> WiringEdge:
        if self.signal_type == "internal" and (
            self.connector is not None or self.cable_id is not None or self.note is not None
        ):
            raise ValueError("内部信号不是线缆，不得填写接头、线缆编号或备注")
        return self

    @model_serializer(mode="wrap")
    def omit_unset_cable_fields(self, handler: Callable[[WiringEdge], dict[str, object]]) -> _WiringEdgeSerialized:
        """未填写的线缆字段不写入 YAML 或 API，保持旧接线图结构不变。"""
        data = handler(self)
        # 保留端点模型，让序列化 schema 仍引用 WiringEndpoint 而不是退化为任意映射。
        data["source"] = self.source
        data["target"] = self.target
        return {key: value for key, value in data.items() if value is not None}


def _endpoint_pair(edge: WiringEdge) -> tuple[tuple[str, str], tuple[str, str]]:
    """无向物理端口对；接线方向只用于人工提示，不改变同一根线身份。"""
    source = (edge.source.device_id, edge.source.port_id)
    target = (edge.target.device_id, edge.target.port_id)
    return (source, target) if source <= target else (target, source)


class WiringGraph(_WiringModel):
    nodes: list[WiringNode]
    edges: list[WiringEdge]

    @model_validator(mode="after")
    def validate_graph(self) -> WiringGraph:
        device_ids = [node.device.id for node in self.nodes]
        if len(device_ids) != len(set(device_ids)):
            raise ValueError("同一接线图内的设备 ID 不得重复")
        library_ids = [node.device.device_library_id for node in self.nodes if node.device.device_library_id is not None]
        if len(library_ids) != len(set(library_ids)):
            raise ValueError("同一接线图内的设备库 ID 不得关联多个设备")
        endpoints = {(node.device.id, port.id) for node in self.nodes for port in node.device.ports}
        internal_ports = {(node.device.id, port.id): port for node in self.nodes for port in node.internal_ports}
        internal_pairs: set[tuple[tuple[str, str], tuple[str, str]]] = set()
        unused_endpoints = {(node.device.id, port_id) for node in self.nodes
                            for port_id, usage in node.port_usage.items() if usage == "unused"}
        edge_ids: set[str] = set()
        pairs: set[tuple[tuple[str, str], tuple[str, str]]] = set()
        for edge in self.edges:
            if edge.id in edge_ids:
                raise ValueError("同一接线图内的连线 ID 不得重复")
            edge_ids.add(edge.id)
            if edge.signal_type == "internal":
                source = (edge.source.device_id, edge.source.port_id)
                target = (edge.target.device_id, edge.target.port_id)
                if source[0] != target[0]:
                    raise ValueError("内部信号仅允许连接同一设备内的内部端口")
                if source not in internal_ports or target not in internal_ports:
                    raise ValueError("内部信号端点必须引用该节点已有的内部端口，不得混用物理端口")
                if source == target:
                    raise ValueError("同一内部端口不可连接自身")
                if internal_ports[source].direction != "out" or internal_ports[target].direction != "in":
                    raise ValueError("内部信号必须从 out 内部端口连接到 in 内部端口")
                if (source, target) in internal_pairs:
                    raise ValueError("同一对内部端口不可重复连线")
                internal_pairs.add((source, target))
                continue
            pair = _endpoint_pair(edge)
            if pair[0] not in endpoints or pair[1] not in endpoints:
                raise ValueError("连线端点必须引用图内已有设备和物理端口")
            if pair[0] in unused_endpoints or pair[1] in unused_endpoints:
                raise ValueError("已有连线的物理端口不能标记为未使用")
            if pair[0] == pair[1]:
                raise ValueError("同一物理端口不可连接自身")
            if pair in pairs:
                raise ValueError("同一对物理端口不可重复连线（包含反向和不同光电类型）")
            pairs.add(pair)
        return self


class WiringWarning(_WiringModel):
    edge_id: NonEmptyString
    code: NonEmptyString
    message: str


class WiringDiagnostics(_WiringModel):
    site: list[WiringWarning]
    targets: dict[NonEmptyString, list[WiringWarning]]


class _WiringGraphs(_WiringModel):
    site: WiringGraph
    targets: dict[NonEmptyString, WiringGraph]


class WiringBody(_WiringGraphs):
    base_revision: NonEmptyString


class _WiringDocument(_WiringGraphs):
    schema_version: Literal[1]

    @model_validator(mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> object:
        if isinstance(value, Mapping) and type(value.get("schema_version")) is not int:
            raise ValueError("schema_version 必须是整数 1")
        return value


class WiringMappingGroup(_WiringModel):
    keys: list[str]
    label: str
    device_library_id: str | None


class WiringFollowStatus(_WiringModel):
    device_id: NonEmptyString
    state: Literal["independent", "bound", "updated", "pending", "blocked"]
    mapping_keys: list[str]
    message: str


class WiringFollowStatuses(_WiringModel):
    site: list[WiringFollowStatus] = Field(default_factory=list)
    targets: dict[NonEmptyString, list[WiringFollowStatus]] = Field(default_factory=dict)


class WiringResponse(_WiringDocument):
    revision: NonEmptyString
    diagnostics: WiringDiagnostics
    mapping_groups: list[WiringMappingGroup] = Field(default_factory=list)
    follow_status: WiringFollowStatuses = Field(default_factory=WiringFollowStatuses)


class WiringRevision(_WiringModel):
    revision: NonEmptyString


class WiringSnapshot(_WiringModel):
    """运行记录用的只读接线快照；图与组合修订来自同一次加锁读取。"""

    schema_version: Literal[1] = 1
    revision: NonEmptyString
    site: WiringGraph
    targets: dict[NonEmptyString, WiringGraph]


class WiringCompareBody(_WiringModel):
    site: WiringGraph
    target: WiringGraph


class WiringModeChange(_WiringModel):
    device_id: NonEmptyString
    device_name: str
    port_id: NonEmptyString
    port_name: str
    site_mode: str | None
    target_mode: str | None


class WiringUsageChange(_WiringModel):
    device_id: NonEmptyString
    device_name: str
    port_id: NonEmptyString
    port_name: str
    site_usage: Literal["used", "unused", "unconfirmed"]
    target_usage: Literal["used", "unused", "unconfirmed"]


class WiringSettingChange(_WiringModel):
    device_id: NonEmptyString
    device_name: str
    setting_id: NonEmptyString
    setting_name: str
    site_value: str | None
    target_value: str | None


class WiringCableChange(_WiringModel):
    """已保留连线的线缆记录差异；匹配时忽略，不改变同一根线身份。"""

    edge_id: NonEmptyString
    site_connector: str | None
    target_connector: str | None
    site_cable_id: str | None
    target_cable_id: str | None
    site_note: str | None
    target_note: str | None


class WiringComparison(_WiringModel):
    retained: list[WiringEdge]
    removed: list[WiringEdge]
    added: list[WiringEdge]
    internal_retained: list[WiringEdge] = Field(default_factory=list)
    internal_removed: list[WiringEdge] = Field(default_factory=list)
    internal_added: list[WiringEdge] = Field(default_factory=list)
    mode_changes: list[WiringModeChange]
    usage_changes: list[WiringUsageChange] = Field(default_factory=list)
    setting_changes: list[WiringSettingChange] = Field(default_factory=list)
    cable_changes: list[WiringCableChange] = Field(default_factory=list)
    site_warnings: list[WiringWarning]
    target_warnings: list[WiringWarning]


def _graph_warnings(graph: WiringGraph) -> list[WiringWarning]:
    ports = {(node.device.id, port.id): (node, port) for node in graph.nodes for port in node.device.ports}
    warnings: list[WiringWarning] = []
    for edge in graph.edges:
        if edge.signal_type == "internal":
            # 内部端口的所属与方向已由图校验器保证，不套用物理口提示。
            continue
        for role, endpoint, allowed, role_name in (
            ("source", edge.source, {"out", "inout"}, "起点"),
            ("target", edge.target, {"in", "inout"}, "终点"),
        ):
            node, port = ports[(endpoint.device_id, endpoint.port_id)]
            port_name = f"{port.group} / {port.name}" if port.group else port.name
            name = f"{role_name}「{node.device.name} / {port_name}」"

            def warn(code: str, message: str) -> None:
                warnings.append(WiringWarning(edge_id=edge.id, code=f"{role}_{code}", message=f"{name}{message}"))

            direction = _working_direction(node, port)
            if direction is None:
                if port.direction == "switchable":
                    warn("mode_unconfirmed", "尚未人工选择端口模式，请确认方向")
                else:
                    warn("direction_unknown", "方向尚未人工确认")
            elif direction not in allowed:
                expected = "输出或双向" if role == "source" else "输入或双向"
                warn("direction_conflict", f"记录方向与连线方向不符；此处应为{expected}")
            if port.signal_type is None:
                warn("signal_type_unknown", "光电类型尚未人工确认")
            elif port.signal_type != edge.signal_type:
                warn("signal_type_conflict", "记录的光电类型与连线类型不符")
        if edge.connector is not None:
            mismatches = []
            for endpoint, role_name in ((edge.source, "起点"), (edge.target, "终点")):
                node, port = ports[(endpoint.device_id, endpoint.port_id)]
                recorded = port.connector.strip()
                if recorded and recorded.casefold() != edge.connector.casefold():
                    port_name = f"{port.group} / {port.name}" if port.group else port.name
                    mismatches.append(f"{role_name}「{node.device.name} / {port_name}」记录为“{recorded}”")
            if mismatches:
                warnings.append(WiringWarning(
                    edge_id=edge.id, code="connector_conflict",
                    message=f"线缆记录的接头“{edge.connector}”与{'、'.join(mismatches)}不符，请现场核对",
                ))
    return warnings


_DIRECTION_NAMES = {"in": "输入", "out": "输出", "inout": "双向"}


def _working_direction(node: WiringNode, port: PhysicalPort) -> str | None:
    return node.port_directions.get(port.id, effective_port_direction(port))


def _mode_identity(node: WiringNode, port: PhysicalPort) -> tuple[str | None, str | None, str | None]:
    # 比较模式 ID 和图内工作方向，不改变物理口的 inout 能力。
    return port.direction, port.selected_mode, _working_direction(node, port)


def _mode_name(node: WiringNode, port: PhysicalPort) -> str | None:
    direction = _working_direction(node, port)
    if direction is None:
        return None
    direction_name = _DIRECTION_NAMES[direction]
    if port.direction == "switchable":
        mode = next(mode for mode in port.modes if mode.id == port.selected_mode)
        return f"{mode.name}（{direction_name}）"
    return direction_name


def compare_wiring(
    site: WiringGraph | Mapping[str, object], target: WiringGraph | Mapping[str, object],
) -> WiringComparison:
    """只比较传入快照；不读写磁盘，不依赖设备档案或实验注册状态。"""
    site_graph = WiringGraph.model_validate(site)
    target_graph = WiringGraph.model_validate(target)
    site_edges = {(_endpoint_pair(edge), edge.signal_type): edge for edge in site_graph.edges if edge.signal_type != "internal"}
    target_edges = {(_endpoint_pair(edge), edge.signal_type): edge for edge in target_graph.edges if edge.signal_type != "internal"}
    site_internal = {((edge.source.device_id, edge.source.port_id), (edge.target.device_id, edge.target.port_id), "internal"): edge
                     for edge in site_graph.edges if edge.signal_type == "internal"}
    target_internal = {((edge.source.device_id, edge.source.port_id), (edge.target.device_id, edge.target.port_id), "internal"): edge
                       for edge in target_graph.edges if edge.signal_type == "internal"}
    site_connected = {endpoint for pair, _ in site_edges for endpoint in pair}
    target_connected = {endpoint for pair, _ in target_edges for endpoint in pair}
    site_ports = {(node.device.id, port.id): (node, port) for node in site_graph.nodes for port in node.device.ports}
    changes: list[WiringModeChange] = []
    usage_changes: list[WiringUsageChange] = []
    setting_changes: list[WiringSettingChange] = []
    cable_changes: list[WiringCableChange] = []
    site_nodes = {node.device.id: node for node in site_graph.nodes}
    for node in target_graph.nodes:
        site_node = site_nodes.get(node.device.id)
        if site_node is not None:
            site_settings = {setting.id: setting for setting in site_node.settings}
            target_settings = {setting.id: setting for setting in node.settings}
            # 目标顺序优先，已移除设置随后；缺失与尚未选择均按 None 比较。
            for setting_id in dict.fromkeys([*target_settings, *site_settings]):
                site_setting = site_settings.get(setting_id)
                target_setting = target_settings.get(setting_id)
                site_value = site_setting.value if site_setting is not None else None
                target_value = target_setting.value if target_setting is not None else None
                if site_value != target_value:
                    setting = target_setting if target_setting is not None else site_setting
                    setting_changes.append(WiringSettingChange(
                        device_id=node.device.id, device_name=node.device.name,
                        setting_id=setting_id, setting_name=setting.name,
                        site_value=site_value, target_value=target_value,
                    ))
        for port in node.device.ports:
            site_entry = site_ports.get((node.device.id, port.id))
            if site_entry is None:
                continue
            if _mode_identity(*site_entry) != _mode_identity(node, port):
                changes.append(WiringModeChange(
                    device_id=node.device.id, device_name=node.device.name,
                    port_id=port.id, port_name=port.name,
                    site_mode=_mode_name(*site_entry), target_mode=_mode_name(node, port),
                ))
            # 仅比较有效二态；旧快照的缺省/待确认不补写，已有连线始终视为使用。
            endpoint = (node.device.id, port.id)
            site_usage = "used" if endpoint in site_connected or site_entry[0].port_usage.get(port.id) == "used" else "unused"
            target_usage = "used" if endpoint in target_connected or node.port_usage.get(port.id) == "used" else "unused"
            if site_usage != target_usage:
                usage_changes.append(WiringUsageChange(
                    device_id=node.device.id, device_name=node.device.name,
                    port_id=port.id, port_name=port.name,
                    site_usage=site_usage, target_usage=target_usage,
                ))
    # 端口对和光电类型决定同一根线；线缆记录不同只另列差异，不拆成删除加新增。
    for edge in target_graph.edges:
        if edge.signal_type == "internal":
            continue
        site_edge = site_edges.get((_endpoint_pair(edge), edge.signal_type))
        if site_edge is None:
            continue
        if (site_edge.connector, site_edge.cable_id, site_edge.note) != (edge.connector, edge.cable_id, edge.note):
            cable_changes.append(WiringCableChange(
                edge_id=edge.id,
                site_connector=site_edge.connector, target_connector=edge.connector,
                site_cable_id=site_edge.cable_id, target_cable_id=edge.cable_id,
                site_note=site_edge.note, target_note=edge.note,
            ))
    return WiringComparison(
        retained=[edge for key, edge in target_edges.items() if key in site_edges],
        removed=[edge for key, edge in site_edges.items() if key not in target_edges],
        added=[edge for key, edge in target_edges.items() if key not in site_edges],
        internal_retained=[edge for key, edge in target_internal.items() if key in site_internal],
        internal_removed=[edge for key, edge in site_internal.items() if key not in target_internal],
        internal_added=[edge for key, edge in target_internal.items() if key not in site_internal],
        mode_changes=changes, usage_changes=usage_changes, setting_changes=setting_changes,
        cable_changes=cable_changes,
        site_warnings=_graph_warnings(site_graph), target_warnings=_graph_warnings(target_graph),
    )


class WiringStorageError(RuntimeError):
    """接线图读取、版本或写入失败；不能解释为空图或覆盖坏文件。"""


_WIRING_LOCK = threading.Lock()


def _read_document(path: Path) -> tuple[_WiringDocument, str]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return _WiringDocument(schema_version=1, site=WiringGraph(nodes=[], edges=[]), targets={}), hashlib.sha256(b"").hexdigest()
    except OSError as exc:
        raise WiringStorageError(f"接线图读取失败：{exc}") from exc
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
        if isinstance(data, dict) and "schema_version" in data and data["schema_version"] != 1:
            raise WiringStorageError(f"接线图版本不受支持：{data['schema_version']!r}")
        document = _WiringDocument.model_validate(data)
    except (UnicodeError, yaml.YAMLError, ValidationError) as exc:
        raise WiringStorageError(f"接线图损坏或结构非法：{exc}") from exc
    return document, hashlib.sha256(raw).hexdigest()


_DEPENDENCY_NAMES = ("mapping.yaml", "devices.yaml", "device_ports.yaml")


@dataclass(frozen=True)
class _FollowSnapshot:
    raw_files: tuple[bytes | None, ...]
    configured: bool
    library: dict
    mapping: dict
    groups: list[WiringMappingGroup]
    profiles: list[DevicePortProfile]
    error: str | None = None


def _has_binding(document: _WiringGraphs) -> bool:
    return any(node.follow_mapping for graph in (document.site, *document.targets.values()) for node in graph.nodes)


def _dependency_files(root: Path, *, follow_required: bool = False) -> tuple[bytes | None, ...]:
    result: list[bytes | None] = []
    for name in _DEPENDENCY_NAMES:
        if name == "device_ports.yaml" and not follow_required and not any(raw is not None for raw in result):
            # 无联动配置/绑定的历史快照连档案读取错误也不受影响。
            result.append(None)
            continue
        try:
            result.append((root / "params" / name).read_bytes())
        except FileNotFoundError:
            result.append(None)
        except OSError as exc:
            raise WiringStorageError(f"接线图联动配置读取失败（{name}）：{exc}") from exc
    return tuple(result)


def _config_payload(raw: bytes, name: str) -> dict:
    value = yaml.safe_load(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} 必须是非空 YAML 映射，不能当作空配置")
    version = value.get("schema_version")
    supported = (1, 2) if name == "mapping.yaml" else (1,)
    if "schema_version" in value and (type(version) is not int or version not in supported):
        raise ValueError(f"{name} 版本不受支持：{version!r}")
    field = "mapping" if name == "mapping.yaml" else "devices"
    if field not in value or not isinstance(value[field], dict):
        raise ValueError(f"{name} 的 {field} 必须是显式 YAML 映射，不能把坏配置当空")
    if field == "devices" and any(
        not isinstance(key, str) or not key.strip() or not isinstance(config, dict)
        for key, config in value[field].items()
    ):
        raise ValueError("devices.yaml 的设备 ID 必须是非空字符串，且设备定义必须是映射")
    return value


def _mapping_groups(document: dict, library: dict) -> list[WiringMappingGroup]:
    """只有用途约束产生联动关系；同设备 ID 本身不构成用途组。"""
    mappings = document.get("mapping", {})
    if not isinstance(mappings, dict):
        raise ValueError("mapping.yaml 的 mapping 必须是映射")
    parents = {}
    for key, config in mappings.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(config, dict):
            raise ValueError("用途键必须是非空字符串，且用途定义必须是映射")
        if "device_id" in config and (not isinstance(config["device_id"], str) or not config["device_id"].strip()):
            raise ValueError(f"用途 {key} 的 device_id 必须是非空字符串")
        if "label" in config and not isinstance(config["label"], str):
            raise ValueError(f"用途 {key} 的 label 必须是字符串")
        parents[key] = key

    def find(key: str) -> str:
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    constraints = document.get("constraints", {})
    if not isinstance(constraints, dict):
        raise ValueError("mapping.yaml 的 constraints 必须是映射")
    for category in ("shared_channel_groups", "colocation_groups"):
        groups = constraints.get(category, {})
        if not isinstance(groups, dict):
            raise ValueError(f"{category} 必须是用途组映射")
        for name, members in groups.items():
            if (not isinstance(name, str) or not isinstance(members, list)
                    or len(members) < 2 or any(not isinstance(key, str) or key not in parents for key in members)
                    or len(set(members)) != len(members)):
                raise ValueError(f"用途组 {name} 必须包含至少两个不重复的已有用途键")
            for key in members[1:]:
                parents[find(key)] = find(members[0])
    components: dict[str, list[str]] = {}
    for key in mappings:
        components.setdefault(find(key), []).append(key)
    result = []
    for keys in components.values():
        ids = {mappings[key].get("device_id") for key in keys}
        destination = next(iter(ids)) if len(ids) == 1 else None
        if destination not in library:
            destination = None
        result.append(WiringMappingGroup(
            keys=keys, label=" / ".join(mappings[key].get("label") or key for key in keys),
            device_library_id=destination,
        ))
    return result


def _read_follow_snapshot(root: Path, *documents: _WiringGraphs) -> _FollowSnapshot:
    """公共只读加载器前后核对原始字节，不获取配置写锁或创建 .lock。"""
    follow_required = any(_has_binding(document) for document in documents)
    for _ in range(3):
        raw = _dependency_files(root, follow_required=follow_required)
        configured = raw[0] is not None or raw[1] is not None
        library, mapping, groups, profiles, error = {}, {}, [], [], None
        if configured or any(_has_binding(document) for document in documents):
            try:
                if raw[0] is None or raw[1] is None:
                    raise ValueError("用途跟随需要 mapping.yaml 和 devices.yaml，当前配置缺失")
                raw_mapping = _config_payload(raw[0], "mapping.yaml")
                _config_payload(raw[1], "devices.yaml")
                library = load_device_library(root)["devices"]
                # 原始结构先校验，避免公共兼容加载器把坏约束转换为空组或字符串列表。
                groups = _mapping_groups(raw_mapping, library)
                mapping = load_mapping_document(root)["mapping"]
                profiles = read_device_ports(root).devices
            except (OSError, UnicodeError, yaml.YAMLError, ValueError, TypeError,
                    AttributeError, KeyError, DevicePortsStorageError) as exc:
                error = f"接线图用途配置不可用：{exc}"
        if _dependency_files(root, follow_required=follow_required) == raw:
            return _FollowSnapshot(raw, configured, library, mapping, groups, profiles, error)
    raise WiringStorageError("接线图联动配置在读取期间持续变化，请重新加载")


def _combined_revision(raw_revision: str, document: _WiringGraphs, snapshot: _FollowSnapshot) -> str:
    # 无联动配置且无显式绑定时维持旧客户端的 raw SHA256，不让独立端口档案改变旧语义。
    if not snapshot.configured and not _has_binding(document):
        return raw_revision
    digest = hashlib.sha256(b"wiring-follow-v1\0" + raw_revision.encode("ascii"))
    for name, raw in zip(_DEPENDENCY_NAMES, snapshot.raw_files):
        digest.update(name.encode("ascii") + b"\0")
        digest.update(b"missing\0" if raw is None else b"present\0" + len(raw).to_bytes(8, "big") + raw)
    return digest.hexdigest()


def _migrate_device(node: WiringNode, destination: str, snapshot: _FollowSnapshot) -> WiringNode:
    profiles = [profile for profile in snapshot.profiles if profile.device_library_id == destination]
    if len(profiles) != 1:
        raise ValueError(f"设备 {destination} 没有唯一端口档案")
    profile = profiles[0]
    models = {model.strip().casefold() for model in (node.device.model, profile.model, snapshot.library[destination]["model"])}
    if len(models) != 1 or not next(iter(models)):
        raise ValueError("设备型号变化或未确认，不能自动迁移")

    def unique(items: list, key, label: str) -> dict:
        indexed = {key(item): item for item in items}
        if len(indexed) != len(items):
            raise ValueError(f"{label}存在重名歧义，不能唯一迁移")
        return indexed

    port_key = lambda port: (port.group, port.name, port.signal_type)
    old_ports = unique(node.device.ports, port_key, "原图物理口")
    new_ports = unique(profile.ports, port_key, "目标物理口")
    if old_ports.keys() != new_ports.keys():
        raise ValueError("物理口不完整对应（group/name/signal_type），不能迁移")
    ports = []
    for old in node.device.ports:
        new = new_ports[port_key(old)]
        if old.direction != new.direction:
            raise ValueError(f"端口 {old.name} 的方向能力变化，不能保留原图工作方式")
        mode_key = lambda mode: (mode.name, mode.direction)
        old_modes = unique(old.modes, mode_key, "原图模式")
        new_modes = unique(new.modes, mode_key, "目标模式")
        if old_modes.keys() != new_modes.keys():
            raise ValueError(f"端口 {old.name} 的模式不能完整唯一对应")
        data = new.model_dump()
        data.update(id=old.id, selected_mode=old.selected_mode, modes=[
            {**new_modes[mode_key(mode)].model_dump(), "id": mode.id} for mode in old.modes
        ])
        ports.append(data)
    data = node.model_dump()
    data["device"] = {**profile.model_dump(), "id": node.device.id, "ports": ports}
    return WiringNode.model_validate(data)


def _project_graph(graph: WiringGraph, snapshot: _FollowSnapshot) -> tuple[WiringGraph, list[WiringFollowStatus]]:
    planned = []
    statuses = []
    for node in graph.nodes:
        keys = list(node.follow_mapping or [])
        state, message, projected = "independent", "独立设备，不跟随用途映射", node
        if node.follow_mapping != [] and (snapshot.configured or node.follow_mapping):
            if snapshot.error:
                state, message = "blocked", snapshot.error
            else:
                if node.follow_mapping is None and node.device.device_library_id is None:
                    # 未关联实库的手工节点不是待选用途，不阻断同图中的仪器跟随。
                    planned.append(node)
                    statuses.append(WiringFollowStatus(
                        device_id=node.device.id, state="independent", mapping_keys=[],
                        message="未关联设备库的手工节点，保持独立",
                    ))
                    continue
                if node.follow_mapping is None:
                    candidates = [group for group in snapshot.groups if node.device.device_library_id is not None
                                  and any(snapshot.mapping[key].get("device_id") == node.device.device_library_id for key in group.keys)]
                    if len(candidates) == 1:
                        keys = list(candidates[0].keys)
                    else:
                        state = "pending"
                        message = ("当前设备承担多个独立用途，请人工选择用途组" if candidates
                                   else "未能识别用途；旧图换机后不可反推绑定，请人工选择用途组")
                if state != "pending":
                    try:
                        missing = [key for key in keys if key not in snapshot.mapping]
                        if missing:
                            raise ValueError(f"已绑定用途键不存在：{', '.join(missing)}")
                        # 已存 keys 保持权威；约束闭包仅补足一致性校验，不写回绑定字段。
                        related = {key for group in snapshot.groups if set(group.keys).intersection(keys) for key in group.keys}
                        destinations = {snapshot.mapping[key].get("device_id") for key in related}
                        if len(destinations) != 1:
                            raise ValueError("绑定用途未指向同一台设备")
                        destination = next(iter(destinations))
                        if destination not in snapshot.library:
                            raise ValueError("绑定用途未指向已有设备库设备")
                        if node.device.model.strip().casefold() != snapshot.library[destination]["model"].strip().casefold():
                            raise ValueError("设备型号变化，不能自动迁移")
                        # 仍是同台设备时无需重取档案或刷新图内人工记录；换机才严格迁移。
                        projected = node if destination == node.device.device_library_id else _migrate_device(node, destination, snapshot)
                        state = "bound" if projected == node else "updated"
                        message = "用途已绑定，图中设备与当前映射一致" if state == "bound" else "已按用途映射同步设备，图内逻辑 ID 和接线保持不变"
                    except (ValueError, KeyError) as exc:
                        state, message = "blocked", str(exc)
        planned.append(projected)
        statuses.append(WiringFollowStatus(device_id=node.device.id, state=state, mapping_keys=keys, message=message))
    failure = any(status.state in {"pending", "blocked"} for status in statuses)
    try:
        projected_graph = WiringGraph(nodes=planned, edges=graph.edges)
    except ValidationError as exc:
        failure = True
        for status in statuses:
            if status.state in {"bound", "updated"}:
                status.state = "blocked"
                status.message = f"迁移后的设备库关联冲突：{exc}"
    if failure:
        for status in statuses:
            if status.state == "updated":
                status.state = "blocked"
            status.message += "；本图未同步，已完整保留原图" if status.state != "independent" else "；本图其余用途未同步，原图保留"
        return graph, statuses
    return projected_graph, statuses


def _project_document(document: _WiringDocument, snapshot: _FollowSnapshot) -> tuple[_WiringDocument, WiringFollowStatuses]:
    if snapshot.error and not any(
        node.follow_mapping != [] for graph in (document.site, *document.targets.values()) for node in graph.nodes
    ):
        raise WiringStorageError(snapshot.error)
    site, site_status = _project_graph(document.site, snapshot)
    targets, target_status = {}, {}
    for key, graph in document.targets.items():
        targets[key], target_status[key] = _project_graph(graph, snapshot)
    return (_WiringDocument(schema_version=1, site=site, targets=targets),
            WiringFollowStatuses(site=site_status, targets=target_status))


def _response(
    document: _WiringDocument, revision: str, snapshot: _FollowSnapshot,
    statuses: WiringFollowStatuses | None = None,
) -> WiringResponse:
    projected = document
    if statuses is None:
        projected, statuses = _project_document(document, snapshot)
    return WiringResponse(
        schema_version=1, revision=revision, site=projected.site, targets=projected.targets,
        diagnostics=WiringDiagnostics(
            site=_graph_warnings(projected.site),
            targets={key: _graph_warnings(graph) for key, graph in projected.targets.items()},
        ), mapping_groups=snapshot.groups, follow_status=statuses,
    )


def read_wiring(root: str | Path) -> WiringResponse:
    """只读投影共享现场和全部目标；任何失败均逐图保原，不创建文件或锁文件。"""
    root = Path(root)
    with _WIRING_LOCK:
        document, raw_revision = _read_document(root / "params" / "wiring.yaml")
        snapshot = _read_follow_snapshot(root, document)
        return _response(document, _combined_revision(raw_revision, document, snapshot), snapshot)


def read_wiring_revision(root: str | Path) -> WiringRevision:
    """只读组合修订；不投影图、不计算提示，不创建文件或锁文件。"""
    root = Path(root)
    with _WIRING_LOCK:
        document, raw_revision = _read_document(root / "params" / "wiring.yaml")
        snapshot = _read_follow_snapshot(root, document)
        return WiringRevision(revision=_combined_revision(raw_revision, document, snapshot))


def read_wiring_snapshot(root: str | Path) -> WiringSnapshot:
    """只读记录快照：图与组合修订在同一次加锁读取中产生。

    不投影用途跟随、不返回提示；缺省 wiring.yaml 沿用空图语义。
    """
    root = Path(root)
    with _WIRING_LOCK:
        document, raw_revision = _read_document(root / "params" / "wiring.yaml")
        snapshot = _read_follow_snapshot(root, document)
        revision = _combined_revision(raw_revision, document, snapshot)
        return WiringSnapshot(
            schema_version=1, revision=revision, site=document.site, targets=document.targets,
        )


def save_wiring(root: str | Path, body: WiringBody | Mapping[str, object]) -> WiringResponse:
    """同一依赖快照用于版本和投影；落盘前重核原始文件指纹，再原子替换。"""
    request = WiringBody.model_validate(body)
    root = Path(root)
    path = root / "params" / "wiring.yaml"
    with _WIRING_LOCK:
        current, raw_revision = _read_document(path)
        snapshot = _read_follow_snapshot(root, current, request)
        if _combined_revision(raw_revision, current, snapshot) != request.base_revision:
            raise RevisionConflict("接线图或用途配置已被其他编辑更新，请重新加载后保存")
        document, statuses = _project_document(_WiringDocument(schema_version=1, site=request.site, targets=request.targets), snapshot)
        temporary: Path | None = None
        try:
            content = yaml.safe_dump(document.model_dump(mode="json"), allow_unicode=True, sort_keys=False).encode("utf-8")
            result = _response(document, _combined_revision(hashlib.sha256(content).hexdigest(), document, snapshot), snapshot, statuses)
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                latest = path.read_bytes()
            except FileNotFoundError:
                latest = b""
            follow_required = _has_binding(current) or _has_binding(request)
            if _dependency_files(root, follow_required=follow_required) != snapshot.raw_files or hashlib.sha256(latest).hexdigest() != raw_revision:
                raise RevisionConflict("接线图或用途配置在保存期间变化，请重新加载后保存")
            os.replace(temporary, path)
        except (RevisionConflict, WiringStorageError, OSError, UnicodeError, yaml.YAMLError) as exc:
            message = str(exc) if isinstance(exc, (RevisionConflict, WiringStorageError)) else f"接线图写入失败：{exc}"
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    message += f"；本次临时文件清理失败：{cleanup_error}"
            error = RevisionConflict if isinstance(exc, RevisionConflict) else WiringStorageError
            raise error(message) from exc
        # 成功替换后本次临时路径已被移走，不再清理其他写入者的临时文件。
        return result
