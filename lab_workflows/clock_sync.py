"""全系统参考时钟同步，供脚本与 GUI 共用。"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lockin_amplifier import HF2Instrument
from signal_generator import DG4000Instrument, DG900Instrument

from .common import ProgressCallback, emit, find_project_root, load_mapping, load_yaml


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


def _normalize(value: str) -> str:
    return "EXT" if str(value).upper().startswith("EXT") else "INT"


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
        short = resource.split("::")[3]
        device_type = "DG4000" if "::0x0641::" in resource else "DG900"
        entry = devices.setdefault(
            resource,
            {
                "type": device_type,
                "resource": resource,
                "short": short,
                "label": cfg.get("label", key),
                "channels": set(),
            },
        )
        entry["channels"].add(int(channel))

    hf2_cfg = mapping.get("lockin_r", {}) or mapping.get("lockin_xy", {})
    if hf2_cfg:
        devices["hf2"] = {
            "type": "HF2",
            "short": hf2_cfg.get("device_id", "dev18246"),
            "label": "HF2 锁相放大器",
            "config": hf2_cfg,
        }
    return list(devices.values())


def load_clock_profile(path: Path | None = None) -> dict[str, str]:
    root = find_project_root()
    data = load_yaml(path or root / "params" / "clock_sources.yaml")
    default = _normalize(data.get("defaults_to", "EXT"))
    profile = {str(key): _normalize(value) for key, value in data.get("devices", {}).items()}
    profile["__default__"] = default
    return profile


def synchronize_clocks(
    profile_path: Path | None = None,
    progress: ProgressCallback | None = None,
) -> list[ClockResult]:
    """应用参考时钟配置，单台失败不会阻止其他设备。"""
    profile = load_clock_profile(profile_path)
    results: list[ClockResult] = []
    devices = _discover_clock_devices()
    emit(progress, "clock", f"发现 {len(devices)} 台可同步设备", 0)

    for index, spec in enumerate(devices, start=1):
        target = profile.get(spec["short"], profile["__default__"])
        before = after = "?"
        instrument = None
        try:
            if spec["type"] in {"DG4000", "DG900"}:
                cls = DG4000Instrument if spec["type"] == "DG4000" else DG900Instrument
                instrument = cls(spec["resource"], channel=min(spec["channels"]))
                instrument.connect()
                before = _normalize(instrument.get_ref_clock_source())
                instrument.set_ref_clock_source(
                    "EXTernal" if target == "EXT" else "INTernal"
                )
                time.sleep(0.2)
                after = _normalize(instrument.get_ref_clock_source())
            else:
                cfg = spec["config"]
                instrument = HF2Instrument(
                    host=cfg.get("host", "127.0.0.1"),
                    port=cfg.get("port", 8005),
                    api_level=1,
                    device_id=cfg["device_id"],
                )
                instrument.connect()
                before = "EXT" if instrument.get_extclk() else "INT"
                instrument.set_extclk(target == "EXT")
                time.sleep(0.2)
                after = "EXT" if instrument.get_extclk() else "INT"
            ok = after == target
            error = "" if ok else f"期望 {target}，实际 {after}"
        except Exception as exc:
            ok, error = False, str(exc)
        finally:
            if instrument and hasattr(instrument, "disconnect"):
                try:
                    instrument.disconnect()
                except Exception:
                    pass

        result = ClockResult(
            spec["label"], spec["type"], spec["short"], before, after, target, ok, error
        )
        results.append(result)
        emit(
            progress,
            "clock",
            f"{spec['label']}: {before} → {after}",
            index / len(devices) * 100,
            "info" if ok else "error",
            result=result.to_dict(),
        )
    return results
