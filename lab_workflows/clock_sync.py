"""全系统参考时钟同步，供脚本与 GUI 共用。"""

from __future__ import annotations

from lab_workflows.devices import create_lockin
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from .common import ProgressCallback, emit, load_mapping
from .devices import create_signal_generator, signal_generator_model
from .steps.clock import (
    clock_device_id,
    load_clock_profile,
    normalize_clock_source,
)


@dataclass(slots=True)
class LoadResult:
    """单台信号源的负载初始化结果。"""

    label: str
    device_type: str
    device_id: str
    policy: dict[int, str]
    ok: bool
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def initialize_output_loads(
    progress: ProgressCallback | None = None,
) -> list[LoadResult]:
    """按设备库与映射用途初始化所有信号源输出负载，不改动参考时钟。"""
    results: list[LoadResult] = []
    devices = _discover_clock_devices()
    generators = [spec for spec in devices if spec["type"] in {"DG4000", "DG900"}]
    emit(progress, "load", f"发现 {len(generators)} 台信号源", 0)
    for index, spec in enumerate(generators, start=1):
        ok, error, policy = False, "", {}
        instrument = None
        try:
            instrument = create_signal_generator(
                spec["config"], channel=min(spec["channels"])  # 连接时应用负载策略
            )
            instrument.connect()
            policy = dict(instrument.load_policy)
            ok = True
        except Exception as exc:
            error = str(exc)
        finally:
            if instrument is not None and hasattr(instrument, "disconnect"):
                try:
                    instrument.disconnect()
                except Exception:
                    pass
        result = LoadResult(
            spec["label"], spec["type"], spec["short"],
            {str(ch): str(load) for ch, load in policy.items()}, ok, error,
        )
        results.append(result)
        emit(
            progress,
            "load",
            f"{spec['label']}: 负载已设置" if ok else f"{spec['label']}: 负载设置失败 {error}",
            index / len(generators) * 100 if generators else 100,
            "info" if ok else "error",
            result=result.to_dict(),
        )
    return results


@dataclass(slots=True)
class ClockResult:
    label: str
    device_type: str
    device_id: str
    before: str
    after: str
    target: str
    ok: bool
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _discover_clock_devices() -> list[dict[str, Any]]:
    mapping = load_mapping()
    devices: dict[str, dict[str, Any]] = {}
    for key, cfg in mapping.items():
        if cfg.get("instrument") != "signal_generator":
            continue
        resource = cfg.get("resource")
        channel = cfg.get("channel")
        if not resource or channel is None:
            continue
        short = clock_device_id(cfg)
        device_type = signal_generator_model(cfg)
        entry = devices.setdefault(
            resource,
            {
                "type": device_type,
                "resource": resource,
                "short": short,
                "label": cfg.get("label", key),
                "channels": set(),
                "config": cfg,
            },
        )
        if entry['type'] != device_type:
            raise ValueError(f'Conflicting models for resource {resource}')
        entry["channels"].add(int(channel))

    hf2_cfg = mapping.get("lockin_r", {}) or mapping.get("lockin_xy", {})
    if hf2_cfg:
        devices["hf2"] = {
            "type": hf2_cfg["model"],
            "short": hf2_cfg.get("device_id", "dev18246"),
            "label": hf2_cfg.get("label", "锁相放大器"),
            "config": hf2_cfg,
        }
    return list(devices.values())


def synchronize_clocks(
    profile_path: Path | None = None,
    progress: ProgressCallback | None = None,
) -> list[ClockResult]:
    """两阶段同步：所有设备写入目标时钟源后，再统一回读校验。

    每台设备仅在阶段二回读时断开；不改动输出负载，单台失败不阻止其他设备。
    """
    profile = load_clock_profile(profile_path)
    results: list[ClockResult] = []
    devices = _discover_clock_devices()
    emit(progress, "clock", f"发现 {len(devices)} 台可同步设备", 0)

    pending: list[tuple[ClockResult, Any, str]] = []

    # 阶段一：逐台连接、读取原值并写入目标时钟源，保持连接等待统一回读。
    for spec in devices:
        target = profile.get(spec["short"], profile["__default__"])
        before = "?"
        instrument = None
        kind = ""
        try:
            if spec["type"] in {"DG4000", "DG900"}:
                instrument = create_signal_generator(
                    spec["config"], channel=min(spec["channels"]),
                    apply_load_policy=False,
                )
                kind = "signal_generator"
            else:
                instrument = create_lockin(spec["config"])
                kind = "lockin_amplifier"
            instrument.connect()
            if kind == "signal_generator":
                before = normalize_clock_source(instrument.get_ref_clock_source())
                instrument.set_ref_clock_source(
                    "EXTernal" if target == "EXT" else "INTernal"
                )
            else:
                before = "EXT" if instrument.get_extclk() else "INT"
                instrument.set_extclk(target == "EXT")
        except Exception as exc:
            if instrument is not None and hasattr(instrument, "disconnect"):
                try:
                    instrument.disconnect()
                except Exception:
                    pass
            failed = ClockResult(
                spec["label"], spec["type"], spec["short"], before, "?", target, False, str(exc)
            )
            results.append(failed)
            emit(progress, "clock", f"{spec['label']}: 设置失败 {exc}", 0, "error")
            continue
        pending.append((
            ClockResult(spec["label"], spec["type"], spec["short"], before, "?", target, False),
            instrument,
            kind,
        ))

    emit(progress, "clock", f"{len(pending)}/{len(devices)} 台已设置，统一回读校验", 50)

    # 阶段二：全部设置完成后统一回读校验；末台设备的回读天然留足时钟切换时间。
    for index, (result, instrument, kind) in enumerate(pending, start=1):
        try:
            if kind == "signal_generator":
                result.after = normalize_clock_source(instrument.get_ref_clock_source())
            else:
                result.after = "EXT" if instrument.get_extclk() else "INT"
            result.ok = result.after == result.target
            result.error = "" if result.ok else f"期望 {result.target}，实际 {result.after}"
        except Exception as exc:
            result.error = str(exc)
        finally:
            if hasattr(instrument, "disconnect"):
                try:
                    instrument.disconnect()
                except Exception:
                    pass
        results.append(result)
        total = len(pending)
        emit(
            progress,
            "clock",
            f"{result.label}: {result.before} → {result.after}",
            50 + index / total * 50 if total else 100,
            "info" if result.ok else "error",
            result=result.to_dict(),
        )
    return results
