"""基于仓库时钟配置同步已连接设备。"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from ..common import find_project_root, load_yaml


@dataclass(slots=True)
class ClockSyncRecord:
    """单台设备的参考时钟设置与回读结果。"""

    device_id: str
    device_type: str
    before: str
    target: str
    actual: str
    ok: bool
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_clock_source(value: Any) -> str:
    """把驱动返回值统一为时钟配置使用的 ``EXT``/``INT``。"""
    return "EXT" if str(value).upper().startswith("EXT") else "INT"


def load_clock_profile(path: Path | None = None) -> dict[str, str]:
    """读取 ``clock_sources.yaml`` 并标准化所有目标值。"""
    root = find_project_root()
    data = load_yaml(path or root / "params" / "clock_sources.yaml")
    default = normalize_clock_source(data.get("defaults_to", "EXT"))
    profile = {
        str(key): normalize_clock_source(value)
        for key, value in data.get("devices", {}).items()
    }
    profile["__default__"] = default
    return profile


def clock_device_id(config: Mapping[str, Any]) -> str:
    """从 mapping 条目中取得 ``clock_sources.yaml`` 使用的设备 ID。"""
    if config.get("instrument") == "lockin_amplifier":
        device_id = config.get("device_id")
        if not device_id:
            raise ValueError("锁相放大器 mapping 缺少 device_id")
        return str(device_id)

    resource = config.get("resource")
    if not resource:
        raise ValueError("信号源 mapping 缺少 resource")
    fields = str(resource).split("::")
    if len(fields) <= 3:
        raise ValueError(f"无法从 resource 提取设备 ID: {resource}")
    return fields[3]


def synchronize_clock_device(
    instrument: Any,
    config: Mapping[str, Any],
    *,
    profile: Mapping[str, str],
    settle_s: float = 0.2,
) -> ClockSyncRecord:
    """设置一台已连接设备的时钟源，并立即回读验证。"""
    device_id = clock_device_id(config)
    target = normalize_clock_source(profile.get(device_id, profile["__default__"]))
    kind = str(config.get("instrument", ""))
    device_type = (
        "HF2"
        if kind == "lockin_amplifier"
        else str(config.get("model", "signal_generator"))
    )
    before = actual = "?"
    try:
        if kind == "signal_generator":
            before = normalize_clock_source(instrument.get_ref_clock_source())
            instrument.set_ref_clock_source(
                "EXTernal" if target == "EXT" else "INTernal"
            )
            if settle_s > 0:
                time.sleep(settle_s)
            actual = normalize_clock_source(instrument.get_ref_clock_source())
        elif kind == "lockin_amplifier":
            before = "EXT" if instrument.get_extclk() else "INT"
            instrument.set_extclk(target == "EXT")
            if settle_s > 0:
                time.sleep(settle_s)
            actual = "EXT" if instrument.get_extclk() else "INT"
        else:
            raise ValueError(f"设备 {device_id} 不支持参考时钟同步: {kind or '未知类型'}")
        ok = actual == target
        error = "" if ok else f"期望 {target}，实际 {actual}"
    except Exception as exc:
        ok = False
        error = str(exc)
    return ClockSyncRecord(
        device_id=device_id,
        device_type=device_type,
        before=before,
        target=target,
        actual=actual,
        ok=ok,
        error=error,
    )


def synchronize_connected_clocks(
    devices: Mapping[str, Any],
    mapping: Mapping[str, Mapping[str, Any]],
    assignments: Mapping[str, str],
    *,
    profile_path: Path | None = None,
    profile: Mapping[str, str] | None = None,
    settle_s: float = 0.2,
    strict: bool = True,
) -> dict[str, dict[str, Any]]:
    """统一同步工作流中已连接的时钟设备。

    ``assignments`` 将工作流设备名映射到 ``mapping.yaml`` 的物理量键。工作流
    默认采用严格模式，任何设置异常或回读不一致都会在配置输出前终止实验。
    """
    targets = dict(profile) if profile is not None else load_clock_profile(profile_path)
    results: dict[str, dict[str, Any]] = {}
    for semantic_name, mapping_key in assignments.items():
        if semantic_name not in devices:
            raise KeyError(f"工作流缺少时钟设备: {semantic_name}")
        if mapping_key not in mapping:
            raise KeyError(f"mapping 缺少时钟设备配置: {mapping_key}")
        record = synchronize_clock_device(
            devices[semantic_name],
            mapping[mapping_key],
            profile=targets,
            settle_s=settle_s,
        )
        results[semantic_name] = record.to_dict()
        if record.ok:
            print(
                f"  {semantic_name} ({record.device_id}) 时钟源: "
                f"{record.before} → {record.actual}"
            )
        else:
            message = (
                f"{semantic_name} 时钟源设置失败：设备 {record.device_id}，"
                f"目标 {record.target}，实际 {record.actual}"
            )
            if record.error:
                message += f"，错误: {record.error}"
            if strict:
                raise RuntimeError(message)
            print(f"  ⚠ {message}")
    return results
