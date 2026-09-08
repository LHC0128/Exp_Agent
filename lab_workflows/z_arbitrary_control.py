"""Z 冻结任意波的只读预览、共享配置步骤与独立 GUI 控制服务。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
import math

import numpy as np

from .common import CancellationToken, emit, find_project_root, validate_safety_limit
from .current_feedback import load_corrected_control_waveform, sha256_file
from . import instrument_control as control


OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE = "NEGative"
SOURCE_DIRECTORY = "Z_AW_Closed_Loop_Waveform_Correction"


@dataclass(frozen=True, slots=True)
class ZArbitrarySettings:
    """冻结波形之外允许编辑的控制参数。"""

    control_burst_phase_deg: float = 0.0
    output_amplitude_vpp: float | None = None
    link_trigger: bool = False
    trigger_frequency_hz: float = 100.0
    trigger_amplitude_vpp: float = 5.0
    trigger_offset_v: float = 2.5
    trigger_duty_percent: float = 50.0

    def validate(self) -> None:
        if not math.isfinite(self.control_burst_phase_deg):
            raise ValueError("Burst 起始相位必须为有限值")
        if self.output_amplitude_vpp is not None and (
            not math.isfinite(self.output_amplitude_vpp) or self.output_amplitude_vpp <= 0
        ):
            raise ValueError("Z 任意波输出 Vpp 必须为正有限值")
        if not self.link_trigger:
            return
        values = (self.trigger_frequency_hz, self.trigger_amplitude_vpp,
                  self.trigger_offset_v, self.trigger_duty_percent)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("触发参数必须为有限值")
        if self.trigger_frequency_hz <= 0 or self.trigger_amplitude_vpp <= 0:
            raise ValueError("触发频率和幅度必须为正")
        # DG4000 方波的硬件占空比范围；DG900 也采用这一共同有效范围。
        if not 20 <= self.trigger_duty_percent <= 80:
            raise ValueError("共同触发占空比必须在 20% 到 80% 之间")
        for value in (self.trigger_offset_v,
                      self.trigger_offset_v - self.trigger_amplitude_vpp / 2,
                      self.trigger_offset_v + self.trigger_amplitude_vpp / 2):
            validate_safety_limit("Time_sequence_2", value)


def configure_optimal_control_trigger(params: Any, device: Any, channel: int,
                                      *, output: bool) -> None:
    """配置共同触发方波；调用方负责控制配置前的输出状态。"""
    for value in (params.trigger_offset_v - params.trigger_amplitude_vpp / 2,
                  params.trigger_offset_v + params.trigger_amplitude_vpp / 2):
        validate_safety_limit("Time_sequence_2", value)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.setup_square(freq=params.trigger_frequency_hz,
                        amplitude=params.trigger_amplitude_vpp,
                        offset=params.trigger_offset_v,
                        dcycle=params.trigger_duty_percent, phase=0.0, channel=channel)
    device.set_output_load("INFinity", channel=channel)
    device.phase_init(channel=channel)
    device.set_output(bool(output), channel=channel)


def configure_z_optimal_control_output(params: Any, device: Any, channel: int,
                                       theory: Any, applied: Any,
                                       *, output: bool = True) -> None:
    """共享的外部下降沿无限 Burst 配置，默认保持既有实验输出行为。"""
    from .steps.arbitrary import ArbitraryWaveformSpec, upload_arbitrary

    for value in (applied.minimum_v, applied.maximum_v,
                  applied.output_minimum_v, applied.output_maximum_v):
        validate_safety_limit("Z_magnetic_field", value)
    upload_arbitrary(device, ArbitraryWaveformSpec(
        values=applied.normalized, frequency=theory.repeat_frequency_hz,
        amplitude=applied.amplitude_vpp, offset=applied.offset_v,
        phase=0.0, channel=channel, output=False,
    ))
    # DG4162 的 APPLy:USER 参数可能被忽略，上传后必须显式重写。
    device.set_frequency(theory.repeat_frequency_hz, channel=channel)
    device.set_amplitude(applied.amplitude_vpp, channel=channel)
    device.set_offset(applied.offset_v, channel=channel)
    device.set_burst_state(True, channel=channel)
    device.set_burst_mode("INFinity", channel=channel)
    device.set_burst_trigger_source("EXTernal", channel=channel)
    device.set_burst_trigger_slope(OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE, channel=channel)
    device.set_burst_phase(params.control_burst_phase_deg % 360.0, channel=channel)
    device.set_output(bool(output), channel=channel)


def start_outputs(device: Any, z_channel: int, trigger_channel: int | None) -> None:
    """只用于已完成校验和配置的输出，Z 先就绪，再启动共同触发。"""
    device.set_output(True, channel=z_channel)
    if trigger_channel is not None:
        device.set_output(True, channel=trigger_channel)


def stop_outputs(device: Any, z_channel: int, trigger_channel: int | None) -> None:
    """逐路尝试关闭，即使一路失败也继续关闭另一输出。"""
    errors = []
    for channel in (z_channel, trigger_channel):
        if channel is None:
            continue
        try:
            device.set_output(False, channel=channel)
        except Exception as exc:
            errors.append(f"CH{channel}: {exc}")
    if errors:
        raise RuntimeError("输出关闭失败，设备状态未知：" + "；".join(errors))


def _load_source(root: Path, run_name: str):
    source_root = (root / "data" / SOURCE_DIRECTORY).resolve()
    path = (source_root / run_name / "results" / "corrected_control_waveform.npz").resolve()
    if source_root not in path.parents:
        raise ValueError("波形路径超出闭环结果目录")
    before = sha256_file(path)
    waveform = load_corrected_control_waveform(root, run_name)
    if waveform.waveform_sha256 != before:
        raise ValueError("读取期间波形文件发生变化，请重新预览")
    return waveform


def _summary(waveform: Any) -> dict[str, Any]:
    return {
        "run_name": waveform.run_name, "waveform_sha256": waveform.waveform_sha256,
        "points": int(waveform.time_s.size),
        "repeat_frequency_hz": float(waveform.repeat_frequency_hz),
        "period_s": 1.0 / waveform.repeat_frequency_hz,
        "amplitude_vpp": float(waveform.amplitude_vpp), "offset_v": float(waveform.offset_v),
        "minimum_v": float(np.min(waveform.voltage_v)),
        "maximum_v": float(np.max(waveform.voltage_v)),
    }


def _reproject_amplitude(waveform: Any, applied: Any, amplitude_vpp: float | None) -> Any:
    """保持闭环建议 voltage_v 不变，只改变仪器 Vpp 到归一化点的映射。"""
    amplitude = float(waveform.amplitude_vpp if amplitude_vpp is None else amplitude_vpp)
    if not math.isfinite(amplitude) or amplitude <= 0:
        raise ValueError("Z 任意波输出 Vpp 必须为正有限值")
    offset = float(waveform.offset_v)
    voltage = np.asarray(waveform.voltage_v, dtype=float)
    normalized = (voltage - offset) / (amplitude / 2.0)
    max_abs = float(np.max(np.abs(normalized)))
    if max_abs > 1.0 + 1e-9:
        required = 2.0 * float(np.max(np.abs(voltage - offset)))
        raise ValueError(
            f"输出 Vpp={amplitude:.6g} 太小，无法容纳闭环建议电压；至少需要 {required:.6g} Vpp"
        )
    output_minimum = offset - amplitude / 2.0
    output_maximum = offset + amplitude / 2.0
    validate_safety_limit("Z_magnetic_field", output_minimum)
    validate_safety_limit("Z_magnetic_field", output_maximum)
    return replace(applied, normalized=np.clip(normalized, -1.0, 1.0),
                   amplitude_vpp=amplitude, offset_v=offset,
                   output_minimum_v=output_minimum,
                   output_maximum_v=output_maximum,
                   max_abs_normalized=max_abs)


def preview_source(root: Path, run_name: str) -> dict[str, Any]:
    waveform = _load_source(root, run_name)
    return {**_summary(waveform), "time_s": waveform.time_s.tolist(),
            "voltage_v": waveform.voltage_v.tolist()}


def list_sources(root: Path) -> list[dict[str, Any]]:
    """只枚举存在冻结文件的运行；单个损坏文件不影响其他选项。"""
    directory = root / "data" / SOURCE_DIRECTORY
    results = []
    for path in sorted(directory.glob("*/results/corrected_control_waveform.npz")):
        name = path.parent.parent.name
        try:
            results.append({"run_name": name, "summary": _summary(_load_source(root, name)),
                            "error": None})
        except Exception as exc:
            results.append({"run_name": name, "summary": None, "error": str(exc)})
    return results


class ZArbitraryControlService:
    """独立工具的会话状态；执行调用必须持有 GUI 全局硬件锁。"""

    def __init__(self, root: Path | None = None):
        self.root = root or find_project_root()
        self._lock = RLock()
        self._last: dict[str, Any] | None = None

    def status(self) -> dict[str, Any]:
        catalog = control.list_control_targets()
        with self._lock:
            last = deepcopy(self._last)
        revisions = (catalog["device_library_revision"], catalog["physical_mapping_revision"])
        known = bool(last and tuple(last["revisions"]) == revisions)
        return {
            "device_library_revision": revisions[0], "physical_mapping_revision": revisions[1],
            "targets": [t for t in catalog["targets"]
                        if t["mapping_key"] in {"Z_magnetic_field", "Time_sequence_2"}],
            "source_known": bool(known and last.get("source") and last["state"] != "unknown"),
            "last_applied": last if known else None,
        }

    def _resolve(self, device_revision: str, mapping_revision: str, link: bool):
        contexts = []
        for key in (["Z_magnetic_field", "Time_sequence_2"] if link else ["Z_magnetic_field"]):
            target, device, endpoint = control._resolve_control_target(key, device_revision, mapping_revision)
            if target["kind"] != "generator" or endpoint is None or endpoint.kind != "channel":
                raise ValueError(f"{key} 必须映射到信号发生器通道")
            record = control._target_record(target, device, endpoint)
            if record.type not in {"DG4000", "DG900"} or record.channels[0].read_only:
                raise ValueError(f"{key} 不支持任意波输出")
            contexts.append((target, record, endpoint))
        if link and (contexts[0][1].resource != contexts[1][1].resource
                     or contexts[0][2].index == contexts[1][2].index):
            raise ValueError("Z 与时序信号2必须位于同一仪器的不同通道")
        return contexts

    def preflight(self, *, action: str, device_library_revision: str,
                  physical_mapping_revision: str, run_name: str | None = None,
                  waveform_sha256: str | None = None,
                  settings: ZArbitrarySettings | None = None) -> dict[str, Any]:
        """无硬件预检；停止采用最近已应用的联动状态。"""
        if action not in {"configure", "configure_and_start", "stop"}:
            raise ValueError("任意波动作无效")
        settings = settings or ZArbitrarySettings()
        revisions = (device_library_revision, physical_mapping_revision)
        with self._lock:
            previous = deepcopy(self._last)
        if previous and tuple(previous["revisions"]) != revisions:
            previous = None
        link = bool(previous and previous["link_trigger"]) if action == "stop" else settings.link_trigger
        contexts = self._resolve(*revisions, link)
        waveform = None
        if action != "stop":
            settings.validate()
            if not run_name or not waveform_sha256:
                raise ValueError("请先选择并预览冻结波形")
            waveform = _load_source(self.root, run_name)
            if waveform.waveform_sha256 != waveform_sha256:
                raise ValueError("波形文件已变化，请重新预览后应用")
            driver = control.SIGNAL_GENERATOR_DRIVERS[contexts[0][1].type]
            if not getattr(driver, "MIN_ARB_POINTS", 2) <= waveform.time_s.size <= driver.MAX_ARB_POINTS:
                raise ValueError("波形点数超出当前信号发生器能力")
            for value in (float(np.min(waveform.voltage_v)), float(np.max(waveform.voltage_v)),
                          waveform.offset_v):
                validate_safety_limit("Z_magnetic_field", value)
            from .experiment_modules.mx_z_optimal_control_rf_sensitivity.sources import corrected_control_contract
            _, applied = corrected_control_contract(waveform)
            _reproject_amplitude(waveform, applied, settings.output_amplitude_vpp)
        return {"contexts": contexts, "waveform": waveform, "settings": settings,
                "link_trigger": link, "previous": previous, "revisions": revisions}

    def execute(self, *, cancellation: CancellationToken | None = None,
                progress=None, **request) -> dict[str, Any]:
        cancellation = cancellation or CancellationToken()
        emit(progress, "preflight", "检查波形、映射及输出限值", 5)
        prepared = self.preflight(**request)
        contexts, settings = prepared["contexts"], prepared["settings"]
        waveform, link = prepared["waveform"], prepared["link_trigger"]
        z_channel = contexts[0][2].index
        trigger_channel = contexts[1][2].index if link else None
        cancellation.raise_if_cancelled()
        instrument = control._connect(contexts[0][1])
        mutated = False
        try:
            if waveform is not None:
                for _, _, endpoint in contexts:
                    if str(instrument.get_voltage_unit(endpoint.index)).upper() != "VPP":
                        raise ValueError("当前单位不是 Vpp，请先在仪器控制中切换为 Vpp 并重新读取")
            cancellation.raise_if_cancelled()
            mutated = True
            stop_outputs(instrument, z_channel, trigger_channel)
            if waveform is not None:
                emit(progress, "configure", "输出已关闭，配置触发与任意波", 25)
                if link:
                    configure_optimal_control_trigger(settings, instrument, trigger_channel, output=False)
                cancellation.raise_if_cancelled()
                from .experiment_modules.mx_z_optimal_control_rf_sensitivity.sources import corrected_control_contract
                theory, applied = corrected_control_contract(waveform)
                applied = _reproject_amplitude(waveform, applied, settings.output_amplitude_vpp)
                configure_z_optimal_control_output(settings, instrument, z_channel, theory, applied, output=False)
                cancellation.raise_if_cancelled()
                instrument.wait_for_operation_complete()
                instrument.raise_for_errors()
                if request["action"] == "configure_and_start":
                    emit(progress, "start_output", "启用 Z 输出及选定的共同触发", 75)
                    start_outputs(instrument, z_channel, trigger_channel)
            cancellation.raise_if_cancelled()
            emit(progress, "readback", "回读涉及的通道配置", 90)
            instrument.wait_for_operation_complete()
            instrument.raise_for_errors()
            snapshots = [control._control_response(target,
                         control._read_target_connected(target, record, endpoint, instrument))
                         for target, record, endpoint in contexts]
            cancellation.raise_if_cancelled()
            from dataclasses import asdict
            last = {
                "revisions": list(prepared["revisions"]), "link_trigger": link,
                "source": _summary(waveform) if waveform is not None else
                          (prepared["previous"] or {}).get("source"),
                "settings": asdict(settings) if waveform is not None else
                            (prepared["previous"] or {}).get("settings"),
                "state": "enabled" if request["action"] == "configure_and_start" else "off",
                "updated_at": datetime.now(timezone.utc).isoformat(), "snapshots": snapshots,
            }
            with self._lock:
                self._last = last
            return self.status()
        except Exception as exc:
            if mutated:
                cleanup_error = None
                try:
                    stop_outputs(instrument, z_channel, trigger_channel)
                except Exception as error:
                    cleanup_error = error
                with self._lock:
                    self._last = {"revisions": list(prepared["revisions"]),
                                  "link_trigger": link, "source": None, "settings": None,
                                  "state": "unknown" if cleanup_error else "off", "snapshots": [],
                                  "updated_at": datetime.now(timezone.utc).isoformat()}
                if cleanup_error:
                    raise RuntimeError(f"{exc}；{cleanup_error}") from exc
            raise
        finally:
            instrument.disconnect()
