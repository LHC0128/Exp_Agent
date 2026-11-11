"""装置（Setup）：每套磁力仪持有一份物理量映射。

``params/setups.yaml`` 惰性创建：文件不存在时等价于只有一个「默认装置」，
映射文件就是原有的 ``params/mapping.yaml``，所有路径、修订与快照保持原样。

生效装置的解析顺序：
1. 当前上下文或环境变量绑定的装置（任务接纳时捕获，任务及子进程内不受切换影响）；
2. ``setups.yaml`` 的 ``active``；
3. 文件不存在时为 ``default``。

本模块只管装置登记与映射文件定位，不连接或写入任何仪器。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
import os
import re
import secrets
from typing import Any, Iterator, Mapping

import yaml

from .common import find_project_root

DEFAULT_SETUP_ID = "default"
DEFAULT_SETUP_LABEL = "默认装置"
DEFAULT_MAPPING_FILE = "params/mapping.yaml"
SETUP_ENV_VAR = "EXP_AGENT_SETUP_ID"
_SCHEMA_VERSION = 1
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_bound: ContextVar[str | None] = ContextVar("exp_agent_bound_setup", default=None)


class SetupConfigError(ValueError):
    """装置登记文件缺失引用、结构非法或请求的装置不可用。"""


@dataclass(frozen=True)
class Setup:
    id: str
    label: str
    mapping_file: str
    archived: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "mapping_file": self.mapping_file, "archived": self.archived}


@dataclass(frozen=True)
class SetupRegistry:
    active: str
    setups: dict[str, Setup]
    stored: bool  # False 表示 setups.yaml 不存在，使用隐式默认装置

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "active": self.active,
            "setups": {key: setup.as_dict() for key, setup in self.setups.items()},
        }

    @property
    def revision(self) -> str:
        content = yaml.safe_dump(self.payload(), allow_unicode=True, sort_keys=True).encode("utf-8")
        return sha256(content).hexdigest()

    def public(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "active": self.active,
            "stored": self.stored,
            "setups": [{"id": key, "label": setup.label, "archived": setup.archived}
                       for key, setup in self.setups.items()],
        }


def _root(root: Path | None) -> Path:
    return Path(root) if root is not None else find_project_root()


def setups_path(root: Path | None = None) -> Path:
    return _root(root) / "params" / "setups.yaml"


def _implicit_registry() -> SetupRegistry:
    return SetupRegistry(
        active=DEFAULT_SETUP_ID,
        setups={DEFAULT_SETUP_ID: Setup(DEFAULT_SETUP_ID, DEFAULT_SETUP_LABEL, DEFAULT_MAPPING_FILE)},
        stored=False,
    )


def _validate_id(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    if not _ID_PATTERN.fullmatch(text):
        raise SetupConfigError(f"装置 ID 非法：{value!r}（仅字母、数字、-、_，以字母或数字开头）")
    return text


def _validate_mapping_file(value: Any, setup_id: str) -> str:
    text = str(value).strip().replace("\\", "/") if value is not None else ""
    path = PurePosixPath(text)
    # 映射文件只能落在 params/ 内的相对路径，拒绝绝对路径与 .. 越界。
    if (not text or path.is_absolute() or ".." in path.parts or path.parts[0] != "params"
            or path.suffix not in (".yaml", ".yml") or ":" in text):
        raise SetupConfigError(f"装置 {setup_id} 的 mapping_file 必须是 params/ 下的相对 YAML 路径：{value!r}")
    return path.as_posix()


def _parse_registry(payload: Any) -> SetupRegistry:
    if not isinstance(payload, Mapping):
        raise SetupConfigError("setups.yaml 必须是映射")
    if payload.get("schema_version") != _SCHEMA_VERSION:
        raise SetupConfigError(f"setups.yaml 的 schema_version 必须为 {_SCHEMA_VERSION}")
    raw_setups = payload.get("setups")
    if not isinstance(raw_setups, Mapping) or not raw_setups:
        raise SetupConfigError("setups.yaml 的 setups 必须是非空映射")
    setups: dict[str, Setup] = {}
    files: dict[str, str] = {}
    for raw_id, raw in raw_setups.items():
        setup_id = _validate_id(raw_id)
        if not isinstance(raw, Mapping):
            raise SetupConfigError(f"装置 {setup_id} 定义必须是映射")
        label = str(raw.get("label") or "").strip()
        if not label:
            raise SetupConfigError(f"装置 {setup_id} 缺少名称")
        mapping_file = _validate_mapping_file(raw.get("mapping_file"), setup_id)
        if mapping_file.casefold() in files:
            raise SetupConfigError(f"装置 {setup_id} 与 {files[mapping_file.casefold()]} 共用映射文件 {mapping_file}")
        archived = raw.get("archived", False)
        if not isinstance(archived, bool):
            raise SetupConfigError(f"装置 {setup_id} 的 archived 必须是布尔值")
        files[mapping_file.casefold()] = setup_id
        setups[setup_id] = Setup(setup_id, label, mapping_file, archived)
    active = _validate_id(payload.get("active"))
    if active not in setups:
        raise SetupConfigError(f"当前装置 {active} 未登记")
    if setups[active].archived:
        raise SetupConfigError(f"当前装置 {active} 已归档，不能作为当前装置")
    return SetupRegistry(active=active, setups=setups, stored=True)


def load_setup_registry(root: Path | None = None) -> SetupRegistry:
    """读取装置登记；文件不存在时返回隐式默认装置，损坏时明确报错而不退回默认。"""
    path = setups_path(root)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return _implicit_registry()
    try:
        payload = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeError, yaml.YAMLError) as exc:
        raise SetupConfigError(f"setups.yaml 无法解析：{exc}") from exc
    return _parse_registry(payload)


def bound_setup_id() -> str | None:
    setup_id = _bound.get()
    if setup_id is not None:
        return setup_id
    return os.environ.get(SETUP_ENV_VAR, "").strip() or None


def subprocess_environment(base: Mapping[str, str]) -> dict[str, str]:
    """复制子进程环境并传递任务绑定；无绑定时原样保留已有环境项。"""
    environment = dict(base)
    if setup_id := bound_setup_id():
        environment[SETUP_ENV_VAR] = setup_id
    return environment


@contextmanager
def bound_setup(setup_id: str) -> Iterator[str]:
    """在当前上下文内固定生效装置，供任务在整个运行期间解析同一份映射。"""
    token = _bound.set(_validate_id(setup_id))
    try:
        yield setup_id
    finally:
        _bound.reset(token)


def effective_setup(root: Path | None = None) -> Setup:
    registry = load_setup_registry(root)
    setup_id = bound_setup_id() or registry.active
    setup = registry.setups.get(setup_id)
    if setup is None:
        # 绑定的装置被移除时停止并报告，不退回其他装置的映射。
        raise SetupConfigError(f"任务绑定的装置 {setup_id} 未登记")
    return setup


def capture_setup(root: Path | None = None) -> Setup:
    """在任务接纳时捕获生效装置，解析错误直接交由调用方处理。"""
    return effective_setup(root)


def mapping_path_for(setup: Setup, root: Path | None = None) -> Path:
    return _root(root) / Path(*PurePosixPath(setup.mapping_file).parts)


def effective_mapping_path(root: Path | None = None) -> Path:
    return mapping_path_for(effective_setup(root), root)


def all_setup_mapping_paths(root: Path | None = None) -> dict[str, Path]:
    """全部已登记装置（含归档）的映射文件，供设备库变更做跨装置引用校验。"""
    registry = load_setup_registry(root)
    return {key: mapping_path_for(setup, root) for key, setup in registry.setups.items()}


# ---- 写入：与设备库/映射保存共用同一进程锁和跨进程文件锁 ----

def _write_registry(root: Path | None, registry: SetupRegistry) -> None:
    from .instrument_config import _atomic_write

    path = setups_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, registry.payload())


@contextmanager
def _locked(root: Path | None, base_revision: str) -> Iterator[SetupRegistry]:
    from .instrument_config import _CONFIG_LOCK, _process_config_lock, RevisionConflict

    with _CONFIG_LOCK, _process_config_lock(root):
        registry = load_setup_registry(root)
        if base_revision != registry.revision:
            raise RevisionConflict("装置登记已被其他操作修改，请刷新后重试")
        yield registry


def create_setup(label: str, *, copy_from: str, base_revision: str,
                 setup_id: str | None = None, root: Path | None = None) -> SetupRegistry:
    """复制映射并登记新装置后复制现场图；不改变当前装置，不触碰仪器。"""
    label = str(label or "").strip()
    if not label:
        raise SetupConfigError("请填写装置名称")
    with _locked(root, base_revision) as registry:
        source = registry.setups.get(copy_from)
        if source is None:
            raise SetupConfigError(f"复制来源装置 {copy_from} 未登记")
        if setup_id is None:
            setup_id = f"setup-{secrets.token_hex(4)}"
            while setup_id in registry.setups:
                setup_id = f"setup-{secrets.token_hex(4)}"
        setup_id = _validate_id(setup_id)
        if setup_id in registry.setups:
            raise SetupConfigError(f"装置 {setup_id} 已存在")
        if any(item.label == label for item in registry.setups.values()):
            raise SetupConfigError(f"装置名称“{label}”已被使用")
        mapping_file = f"params/setups/{setup_id}/mapping.yaml"
        target = mapping_path_for(Setup(setup_id, label, mapping_file), root)
        if target.exists():
            raise SetupConfigError(f"映射文件 {mapping_file} 已存在，拒绝覆盖")
        from .instrument_config import _atomic_write

        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, mapping_path_for(source, root).read_bytes())
        updated = SetupRegistry(
            active=registry.active,
            setups={**registry.setups, setup_id: Setup(setup_id, label, mapping_file)},
            stored=True,
        )
        try:
            _write_registry(root, updated)
        except Exception:
            # 登记写入失败时移除刚复制的映射，避免留下未登记的孤立文件。
            target.unlink(missing_ok=True)
            raise
        # 登记与映射已一致；现场图复制失败保留新装置并明确报告，避免静默丢失现场图。
        from .wiring import copy_site_for_setup

        try:
            copy_site_for_setup(_root(root), copy_from, setup_id)
        except Exception as exc:
            raise SetupConfigError(f"装置已建立但现场图未复制（{setup_id}）：{exc}") from exc
        return load_setup_registry(root)


def set_active_setup(setup_id: str, *, base_revision: str, root: Path | None = None) -> SetupRegistry:
    """只改当前装置指针。硬件空闲检查由调用方（GUI 后端）在同一请求内完成。"""
    with _locked(root, base_revision) as registry:
        setup = registry.setups.get(setup_id)
        if setup is None:
            raise SetupConfigError(f"装置 {setup_id} 未登记")
        if setup.archived:
            raise SetupConfigError(f"装置 {setup_id} 已归档，不能设为当前装置")
        if setup_id == registry.active and registry.stored:
            return registry
        _write_registry(root, SetupRegistry(active=setup_id, setups=registry.setups, stored=True))
        return load_setup_registry(root)


def update_setup(setup_id: str, *, base_revision: str, label: str | None = None,
                 archived: bool | None = None, root: Path | None = None) -> SetupRegistry:
    """改名或归档；不提供删除，归档装置的映射文件保留。"""
    with _locked(root, base_revision) as registry:
        setup = registry.setups.get(setup_id)
        if setup is None:
            raise SetupConfigError(f"装置 {setup_id} 未登记")
        new_label = setup.label if label is None else str(label).strip()
        if not new_label:
            raise SetupConfigError("装置名称不能为空")
        if any(key != setup_id and item.label == new_label for key, item in registry.setups.items()):
            raise SetupConfigError(f"装置名称“{new_label}”已被使用")
        new_archived = setup.archived if archived is None else bool(archived)
        if new_archived and setup_id == registry.active:
            raise SetupConfigError("当前装置不能归档，请先切换到其他装置")
        setups = dict(registry.setups)
        setups[setup_id] = Setup(setup_id, new_label, setup.mapping_file, new_archived)
        _write_registry(root, SetupRegistry(active=registry.active, setups=setups, stored=True))
        return load_setup_registry(root)
