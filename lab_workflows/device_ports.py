"""设备端口档案的共享模型、人工确认预置与独立持久化。

本模块不连接硬件；模式和关联设备库 ID 只记录人工建档信息。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import threading
from typing import Annotated, Literal, Mapping

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, model_validator
import yaml

from lab_workflows.instrument_config import RevisionConflict


NonEmptyString = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
PortDirection = Literal["in", "out", "inout"]


class _PortModel(BaseModel):
    """所有档案边界拒绝未知字段和隐式类型转换。"""

    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")


class PortMode(_PortModel):
    id: NonEmptyString
    name: NonEmptyString
    direction: PortDirection


class PhysicalPort(_PortModel):
    id: NonEmptyString
    name: NonEmptyString
    group: str
    signal_type: Literal["optical", "electrical"] | None
    connector: str
    purpose: str
    direction: Literal["in", "out", "inout", "switchable"] | None
    modes: list[PortMode]
    selected_mode: NonEmptyString | None

    @model_validator(mode="after")
    def validate_modes(self) -> PhysicalPort:
        mode_ids = [mode.id for mode in self.modes]
        if len(mode_ids) != len(set(mode_ids)):
            raise ValueError("同一端口内的模式 ID 不得重复")
        if self.direction == "switchable":
            if not self.modes:
                raise ValueError("可切换端口必须至少有一个模式")
            if self.selected_mode is not None and self.selected_mode not in mode_ids:
                raise ValueError("selected_mode 必须是该端口已有的模式 ID 或 null")
        elif self.modes or self.selected_mode is not None:
            raise ValueError("非可切换端口的 modes 必须为空且 selected_mode 必须为 null")
        return self


class DevicePortPreset(_PortModel):
    id: NonEmptyString
    name: NonEmptyString
    model: str
    ports: list[PhysicalPort]

    @model_validator(mode="after")
    def validate_port_ids(self) -> DevicePortPreset:
        port_ids = [port.id for port in self.ports]
        if len(port_ids) != len(set(port_ids)):
            raise ValueError("同一设备内的端口 ID 不得重复")
        return self


class DevicePortProfile(DevicePortPreset):
    device_library_id: NonEmptyString | None


class _DeviceProfiles(_PortModel):
    devices: list[DevicePortProfile]

    @model_validator(mode="after")
    def validate_device_ids(self) -> _DeviceProfiles:
        device_ids = [device.id for device in self.devices]
        if len(device_ids) != len(set(device_ids)):
            raise ValueError("设备 ID 不得重复")
        library_ids = [device.device_library_id for device in self.devices if device.device_library_id is not None]
        if len(library_ids) != len(set(library_ids)):
            raise ValueError("同一设备库 ID 不得关联多个设备档案")
        return self


class DevicePortsBody(_DeviceProfiles):
    base_revision: NonEmptyString


class _DevicePortsDocument(_DeviceProfiles):
    schema_version: Literal[1]

    @model_validator(mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> object:
        if isinstance(value, Mapping) and type(value.get("schema_version")) is not int:
            raise ValueError("schema_version 必须是整数 1")
        return value


class DevicePortsResponse(_DevicePortsDocument):
    revision: NonEmptyString
    presets: list[DevicePortPreset]


def effective_port_direction(port: PhysicalPort | Mapping[str, object]) -> str | None:
    """返回人工记录模式的有效方向；未确认或尚未选择模式时为 None。"""
    validated = port if isinstance(port, PhysicalPort) else PhysicalPort.model_validate(port)
    if validated.direction != "switchable":
        return validated.direction
    return next((mode.direction for mode in validated.modes if mode.id == validated.selected_mode), None)


class DevicePortsStorageError(RuntimeError):
    """档案读取、版本或写入失败；调用方不得将其解释为空档案。"""


_DEVICE_PORTS_LOCK = threading.Lock()


def _read_document(path: Path) -> tuple[list[DevicePortProfile], str]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return [], hashlib.sha256(b"").hexdigest()
    except OSError as exc:
        raise DevicePortsStorageError(f"设备端口档案读取失败：{exc}") from exc
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
        if isinstance(data, dict) and "schema_version" in data and data["schema_version"] != 1:
            raise DevicePortsStorageError(f"设备端口档案版本不受支持：{data['schema_version']!r}")
        document = _DevicePortsDocument.model_validate(data)
    except (UnicodeError, yaml.YAMLError, ValidationError) as exc:
        raise DevicePortsStorageError(f"设备端口档案损坏或结构非法：{exc}") from exc
    return document.devices, hashlib.sha256(raw).hexdigest()


def read_device_ports(root: str | Path) -> DevicePortsResponse:
    """读取独立档案；文件缺失时返回空档案，不创建目录或文件。"""
    with _DEVICE_PORTS_LOCK:
        devices, revision = _read_document(Path(root) / "params" / "device_ports.yaml")
        return DevicePortsResponse(schema_version=1, revision=revision, devices=devices, presets=device_port_presets())


def save_device_ports(root: str | Path, body: DevicePortsBody | Mapping[str, object]) -> DevicePortsResponse:
    """在本进程独立写锁内做修订检查，落盘前重核目标指纹，再原子替换。"""
    request = DevicePortsBody.model_validate(body)
    path = Path(root) / "params" / "device_ports.yaml"
    with _DEVICE_PORTS_LOCK:
        _, current_revision = _read_document(path)
        if current_revision != request.base_revision:
            raise RevisionConflict("设备端口档案已被其他编辑更新，请重新加载后保存")
        document = _DevicePortsDocument(schema_version=1, devices=request.devices)
        content = yaml.safe_dump(document.model_dump(mode="json"), allow_unicode=True, sort_keys=False).encode("utf-8")
        temporary: Path | None = None
        try:
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
            if hashlib.sha256(latest).hexdigest() != current_revision:
                raise RevisionConflict("设备端口档案在保存期间被外部修改，请重新载入")
            os.replace(temporary, path)
        except (RevisionConflict, OSError) as exc:
            message = str(exc) if isinstance(exc, RevisionConflict) else f"设备端口档案写入失败：{exc}"
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    message += f"；本次临时文件清理失败：{cleanup_error}"
            error = RevisionConflict if isinstance(exc, RevisionConflict) else DevicePortsStorageError
            raise error(message) from exc
        # 成功替换已经移走本次临时路径，不再执行可能掩盖写入结果的清理。
        return DevicePortsResponse(
            schema_version=1, revision=hashlib.sha256(content).hexdigest(),
            devices=document.devices, presets=device_port_presets(),
        )


def _port(
    port_id: str, name: str, group: str, direction: Literal["in", "out", "switchable"],
    purpose: str, modes: list[PortMode] | None = None, *, connector: str = "BNC",
) -> PhysicalPort:
    return PhysicalPort(
        id=port_id, name=name, group=group, signal_type="electrical", connector=connector,
        purpose=purpose, direction=direction, modes=modes or [], selected_mode=None,
    )


def _clock_port() -> PhysicalPort:
    return _port("clock_10mhz", "10 MHz", "参考时钟", "switchable", "共用 10 MHz 参考时钟接口", [
        PortMode(id="internal", name="内部参考时钟", direction="out"),
        PortMode(id="external", name="外部参考时钟", direction="in"),
    ])


def device_port_presets() -> list[DevicePortPreset]:
    """每次构造独立副本；仅包含已确认的 DG4000、DG900、HF2 和 MFLI 信号端口。"""
    dg4000: list[PhysicalPort] = []
    dg900: list[PhysicalPort] = []
    for channel in (1, 2):
        group = f"ch{channel}"
        dg4000.extend([
            _port(f"ch{channel}_out", f"CH{channel} Output", group, "out", "通道信号输出"),
            _port(f"ch{channel}_sync", f"CH{channel} Sync", group, "out", "同步信号输出"),
            _port(f"ch{channel}_external", f"CH{channel} External", group, "in", "外部触发 / 门控 / MOD（AM / FM）输入"),
        ])
        dg900.extend([
            _port(f"ch{channel}_out", f"CH{channel} Output", group, "out", "通道信号输出"),
            _port(f"ch{channel}_multi", f"CH{channel} Multi", group, "switchable", "同步输出或外部触发 / 门控 / MOD（AM / FM）输入", [
                PortMode(id="sync", name="同步", direction="out"),
                PortMode(id="trigger", name="触发", direction="in"),
                PortMode(id="gate", name="门控", direction="in"),
                PortMode(id="am", name="AM", direction="in"),
                PortMode(id="fm", name="FM", direction="in"),
            ]),
        ])
    dg4000.append(_clock_port())
    dg900.append(_clock_port())
    hf2 = [
        _port("clock_in", "10 MHz Clock In", "参考时钟", "in", "10 MHz 参考时钟输入"),
        _port("clock_out", "10 MHz Clock Out", "参考时钟", "out", "10 MHz 参考时钟输出"),
    ]
    hf2.extend(_port(f"aux_out_{channel}", f"Aux Out {channel}", "辅助输出", "out", "辅助信号输出") for channel in range(1, 5))
    for channel in (1, 2):
        output = f"signal_output_{channel}"
        hf2.extend([
            _port(f"{output}_add", "ADD", output, "in", "加到内部设置信号的外部输入"),
            _port(f"{output}_output", "Output", output, "out", "内部设置输出＋ADD输入"),
            _port(f"{output}_sync", "Sync", output, "out", "同步信号输出"),
        ])
        inputs = f"signal_input_{channel}"
        hf2.extend([
            _port(f"{inputs}_in", "IN", inputs, "in", "信号输入"),
            _port(f"{inputs}_diff", "Diff", inputs, "in", "差分信号输入"),
        ])
    mfli = [
        _port("clock_in", "10 MHz In", "参考时钟", "in", "10 MHz 参考时钟输入", connector=""),
        _port("clock_out", "10 MHz Out", "参考时钟", "out", "10 MHz 参考时钟输出", connector=""),
        _port("signal_input_i", "I in", "Signal Input", "in", "电流信号输入", connector=""),
        _port("signal_input_v", "V", "Signal Input", "in", "电压信号输入", connector=""),
        _port("signal_input_vdiff", "Vdiff", "Signal Input", "in", "差分电压信号输入", connector=""),
        _port("signal_output_v", "V", "Signal Output", "out", "电压信号输出", connector=""),
        _port("signal_output_vdiff", "Vdiff", "Signal Output", "out", "差分电压信号输出", connector=""),
    ]
    mfli.extend(
        _port(f"aux_input_{channel}", f"Aux Input {channel}", "辅助输入", "in", "辅助信号输入", connector="")
        for channel in (1, 2)
    )
    mfli.extend(
        _port(f"aux_out_{channel}", f"Aux Out {channel}", "辅助输出", "out", "辅助信号输出", connector="")
        for channel in range(1, 5)
    )
    return [
        DevicePortPreset(id="dg4000", name="DG4000", model="DG4000", ports=dg4000),
        DevicePortPreset(id="dg900", name="DG900", model="DG900", ports=dg900),
        DevicePortPreset(id="hf2", name="HF2", model="HF2", ports=hf2),
        DevicePortPreset(id="mfli", name="MFLI", model="MFLI", ports=mfli),
    ]
