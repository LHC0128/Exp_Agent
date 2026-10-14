"""锁相控制与采集；GUI 和普通脚本共用，输出写入先检查实验安全限值。"""

import math
from pathlib import Path
from uuid import uuid4

import numpy as np

from lockin_amplifier import AuxOutConfig, DAQConfig, ScopeConfig, auxout, demod, acquire_scope, acquisition_module
from lockin_amplifier.daq import acquire_data
from .common import WorkflowCancelled, load_safety_limits, validate_safety_limit
from .steps.run_directory import create_run_directory


def output_rule(device_id: str, kind: str, index: int, *, project_root=None) -> tuple[str, dict]:
    key = f"{device_id}_{kind}_{index}"
    return key, dict(load_safety_limits(project_root).get(key) or {})


def _bounded_rule(device_id: str, kind: str, index: int, *, project_root=None) -> tuple[str, dict]:
    key, rule = output_rule(device_id, kind, index, project_root=project_root)
    if any(rule.get(name) is None for name in ("min", "max")):
        raise PermissionError(f"尚未确认安全电压范围：{key}；该输出写入已禁用")
    if not all(math.isfinite(float(rule[name])) for name in ("min", "max")) or rule["min"] >= rule["max"]:
        raise ValueError(f"输出安全限值无效: {key}")
    return key, rule


def read_outputs(instr, device_id: str, demod_idx: int) -> dict:
    signal_outputs, auxiliary_outputs = [], []
    count = instr.capabilities["demodulators"]
    for index in range(instr.capabilities["signal_outputs"]):
        path = instr.sig_out_path(index)
        output_range = instr.get_double(f"{path}/range")
        factor = demod._output_voltage_factor(instr, index, output_range)
        key, rule = output_rule(device_id, "signal_output", index)
        signal_outputs.append({
            "index": index, "enabled": bool(instr.get_int(f"{path}/on")),
            "range_v": output_range, "offset_v": instr.get_double(f"{path}/offset") * factor,
            "amplitudes_peak_v": [instr.get_double(f"{path}/amplitudes/{idx}") * factor for idx in range(count)],
            "mixer_enabled": [bool(instr.get_int(f"{path}/enables/{idx}")) for idx in range(count)],
            "load": ("50 Ohm" if instr.get_int(f"{path}/imp50") else "HiZ") if instr.model == "MFLI" else "HiZ reference",
            "differential": bool(instr.get_int(f"{path}/diff")) if instr.model == "MFLI" else False,
            "safety_key": key, "writable": rule.get("min") is not None and rule.get("max") is not None,
        })
    for index in range(4):
        path = instr.aux_out_path(index)
        key, rule = output_rule(device_id, "aux_output", index)
        auxiliary_outputs.append({
            "index": index, "demod_index": instr.get_int(f"{path}/demodselect"),
            "output_select": instr.get_int(f"{path}/outputselect"),
            "scale": instr.get_double(f"{path}/scale"), "offset_v": instr.get_double(f"{path}/offset"),
            "value_v": instr.get_double(f"{path}/value"),
            "safety_key": key, "writable": rule.get("min") is not None and rule.get("max") is not None,
            "hardware_limits": instr.model == "MFLI",
        })
    return {"signal_outputs": signal_outputs, "aux_outputs": auxiliary_outputs}


def apply_settings(instr, device_id: str, demod_idx: int, settings: dict, *, project_root=None) -> None:
    """一次请求处理一类设置；主输出失败后关闭本通道。"""
    if "demod" in settings:
        config = settings["demod"]
        actions = {"phase_deg": demod.set_demod_phase, "rate": demod.set_demod_rate,
                   "time_constant": demod.set_demod_time_constant, "order": demod.set_demod_order,
                   "harmonic": demod.set_demod_harmonic, "enabled": demod.set_demod_enable}
        if "frequency_hz" in config:
            demod.set_demod_frequency(instr, demod_idx, config["frequency_hz"], harmonic=config.get("harmonic"))
        for key, setter in actions.items():
            if key in config and not (key == "harmonic" and "frequency_hz" in config):
                setter(instr, demod_idx, config[key])
        return
    if "signal_output" in settings:
        config = settings["signal_output"]
        index, mixer = config["index"], config.get("amplitude_index", demod_idx)
        path = instr.sig_out_path(index)
        instr.demod_path(mixer)
        original_enabled = bool(instr.get_int(f"{path}/on"))
        if config.get("enabled") is False and set(config) <= {"index", "enabled"}:
            demod.set_output_enable(instr, index, False)
            return
        key, rule = _bounded_rule(device_id, "signal_output", index, project_root=project_root)
        if instr.get_int(f"{path}/add"):
            raise ValueError("Signal Output 正在叠加外部输入，无法检查总电压；请先在 LabOne 核对叠加模式")
        count = instr.capabilities["demodulators"]
        output_range = instr.get_double(f"{path}/range")
        factor = demod._output_voltage_factor(instr, index, output_range)
        amplitudes = [instr.get_double(f"{path}/amplitudes/{idx}") * factor for idx in range(count)]
        enabled = [bool(instr.get_int(f"{path}/enables/{idx}")) for idx in range(count)]
        amplitudes[mixer] = config.get("amplitude_peak_v", amplitudes[mixer])
        enabled[mixer] = config.get("mixer_enabled", enabled[mixer])
        offset = config.get("offset_v", instr.get_double(f"{path}/offset") * factor)
        swing = sum(abs(amplitude) for amplitude, on in zip(amplitudes, enabled) if on)
        validate_safety_limit(key, offset - swing, {key: rule})
        validate_safety_limit(key, offset + swing, {key: rule})
        if abs(offset) + swing > output_range:
            raise ValueError("总输出电压超出当前量程；请先在 LabOne 核对输出量程")
        try:
            demod.set_output_enable(instr, index, False)
            if "amplitude_peak_v" in config:
                demod.set_output_amplitude(instr, index, config["amplitude_peak_v"], mixer)
            if "offset_v" in config:
                demod.set_output_offset(instr, index, config["offset_v"])
            if "mixer_enabled" in config:
                instr.set_int(f"{path}/enables/{mixer}", int(config["mixer_enabled"]))
            instr.sync()
            if config.get("enabled", original_enabled):
                demod.set_output_enable(instr, index, True)
        except Exception as exc:
            try:
                demod.set_output_enable(instr, index, False)
            except Exception as cleanup:
                raise RuntimeError(f"{exc}；Signal Output 关闭失败，状态未知: {cleanup}") from exc
            raise
        return
    config = settings["aux_output"]
    index = config["index"]
    path = instr.aux_out_path(index)
    source_demod = config["demod_index"]
    instr.demod_path(source_demod)
    key, rule = _bounded_rule(device_id, "aux_output", index, project_root=project_root)
    validate_safety_limit(key, config["offset_v"], {key: rule})
    if instr.model == "HF2":
        adc = instr.get_int(f"{instr.demod_path(source_demod)}/adcselect")
        if config["output_select"] == 3:
            bound = 180.0
        elif adc in (0, 1):
            bound = math.sqrt(2) * instr.get_double(f"{instr.sig_in_path(adc)}/range")
        else:
            raise ValueError("HF2 Aux 路由来源不能确定安全电压边界，请先核对主输入")
        swing = abs(config["scale"]) * bound
        validate_safety_limit(key, config["offset_v"] - swing, {key: rule})
        validate_safety_limit(key, config["offset_v"] + swing, {key: rule})
    else:
        if instr.get_int(f"{path}/tipprotect/enable"):
            raise ValueError("MFLI Aux Output 启用了 Tip Protect，可能覆盖电压限制，请先核对")
    try:
        if instr.model == "MFLI":
            instr.set_double(f"{path}/limitlower", float(rule["min"]))
            instr.set_double(f"{path}/limitupper", float(rule["max"]))
            instr.set_double(f"{path}/preoffset", 0.0)
        auxout.configure_aux_output(instr, AuxOutConfig(
            aux_index=index, demod_index=source_demod, output_select=config["output_select"],
            scale=config["scale"], offset=config["offset_v"],
        ))
    except Exception as exc:
        try:
            safe = min(max(0.0, float(rule["min"])), float(rule["max"]))
            validate_safety_limit(key, safe, {key: rule})
            instr.set_int(f"{path}/outputselect", -1)
            instr.set_double(f"{path}/offset", safe)
            instr.sync()
        except Exception as cleanup:
            raise RuntimeError(f"{exc}；Aux Output 恢复失败，状态未知: {cleanup}") from exc
        raise


def capture(instr, device_id: str, demod_idx: int, settings: dict, *, cancellation_check=None) -> dict:
    mode = settings["mode"]
    directory = create_run_directory("LockinAcquisition", f"{instr.model.lower()}_{uuid4().hex[:6]}", settings)
    directory.update_config(lockin={"device_library_id": device_id, "device_id": instr.device_id,
                                    "model": instr.model, "capabilities": instr.capabilities}, completion_status="running")
    try:
        if mode == "scope":
            config = ScopeConfig(sources=settings.get("sources", ["voltage0"]),
                                 sample_rate_hz=settings.get("sample_rate_hz"),
                                 length=settings.get("length", 2048), records=settings.get("records", 1))
            results = acquire_scope(instr, config, timeout=settings.get("timeout", 30), cancellation_check=cancellation_check)
        else:
            duration, points = settings.get("duration", 1.0), settings.get("points", 1000)
            base = instr.demod_path(demod_idx)
            rate = settings.get("requested_rate", points / duration)
            if acquisition_module(instr.model, rate, instr.acquisition_mode) == "daq" and not instr.get_int(f"{base}/enable"):
                raise ValueError("请先启用当前解调器的数据传输")
            results = acquire_data(instr, DAQConfig(device=instr.device_id, duration=duration, grid_cols=points,
                                                   signal_paths=["sample.x", "sample.y", "sample.r"], record_flags=4),
                                   demod_idx, timeout=settings.get("timeout", 30), cancellation_check=cancellation_check,
                                   requested_rate=rate, strict_cleanup=True)
        arrays, traces, metadata = {}, [], []
        for idx, result in enumerate(results):
            if not len(result.values):
                raise RuntimeError("锁相放大器返回空记录")
            arrays[f"values_{idx}"] = result.values
            arrays[f"time_{idx}"] = result.time
            boundaries = getattr(result, "record_slices", []) or [(0, len(result.values))]
            arrays[f"record_slices_{idx}"] = np.asarray(boundaries)
            metadata.append({"signal_name": result.signal_name, "record_index": getattr(result, "record_index", 0),
                             "config_snapshot": result.config_snapshot})
            for segment, (start, end) in enumerate(boundaries):
                selected = np.linspace(start, end - 1, min(end - start, 2000), dtype=int)
                traces.append({"id": f"{result.signal_name} / {getattr(result, 'record_index', 0)}:{segment}",
                               "x": result.time[selected].tolist(), "y": result.values[selected].tolist()})
        target = directory.raw / "acquisition.npz"
        np.savez_compressed(target, **arrays)
        directory.update_config(completion_status="completed", records=metadata, data_files=["raw/acquisition.npz"])
        return {"run_dir": str(directory.root), "artifacts": [str(target)], "model": instr.model, "traces": traces}
    except Exception as exc:
        directory.update_config(completion_status="cancelled" if isinstance(exc, WorkflowCancelled) else "failed", error=str(exc))
        raise
