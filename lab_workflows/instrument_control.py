"""GUI 使用的信号发生器与示波器通用读写服务。"""

from __future__ import annotations

import math
from typing import Any

from sds_acquisition import SDSInstrument

from .common import load_safety_limits, validate_safety_limit
from .devices import (
    CURRENT_SOURCE_DRIVERS,
    DeviceRecord,
    LASER_DRIVERS,
    SIGNAL_GENERATOR_DRIVERS,
    find_device,
)


def _connect(record: DeviceRecord):
    if record.type in SIGNAL_GENERATOR_DRIVERS:
        instrument = SIGNAL_GENERATOR_DRIVERS[record.type](record.resource)
    elif record.type in CURRENT_SOURCE_DRIVERS:
        instrument = CURRENT_SOURCE_DRIVERS[record.type](record.resource)
    elif record.type in LASER_DRIVERS:
        instrument = LASER_DRIVERS[record.type](
            record.resource,
            laser_channel=int(record.options.get("laser_channel", 1)),
            command_port=int(record.options.get("command_port", 1998)),
            monitoring_port=int(
                record.options.get("monitoring_port", 1999)
            ),
            timeout=float(record.options.get("timeout", 5.0)),
        )
    elif record.type == "SDS":
        instrument = SDSInstrument(record.resource)
    else:
        raise ValueError(f"不支持的设备类型: {record.type}")
    instrument.connect()
    return instrument


def _optional_query(errors: list[dict[str, str]], field: str, query, default=None):
    try:
        return query()
    except Exception as exc:
        errors.append({"field": field, "message": str(exc)})
        return default


def _canonical_shape(value: str) -> str:
    normalized = value.strip().strip('"').upper()
    for prefix, name in (
        ("SIN", "SINusoid"),
        ("SQU", "SQUare"),
        ("RAMP", "RAMP"),
        ("PULS", "PULSe"),
        ("NOIS", "NOISe"),
        ("DC", "DC"),
        ("USER", "USER"),
        ("ARB", "ARBitrary"),
    ):
        if normalized.startswith(prefix):
            return name
    return value.strip().strip('"')


def _read_modulation(instrument, device_type: str, channel: int,
                     errors: list[dict[str, str]]) -> dict[str, Any]:
    state = {
        "enabled": False,
        "type": None,
        "source": None,
        "internal_frequency": None,
        "internal_function": None,
        "am_depth": None,
        "fm_deviation": None,
        "pm_deviation": None,
        "fsk_frequency": None,
        "fsk_rate": None,
        "fsk_polarity": None,
        "pwm_duty_deviation": None,
    }
    if device_type == "DG4000":
        state["enabled"] = _optional_query(
            errors, "mod.enabled", lambda: instrument.get_mod_state(channel), False
        )
        state["type"] = _optional_query(
            errors, "mod.type", lambda: instrument.get_mod_type(channel)
        )
    else:
        for mod_type in instrument.MOD_TYPES:
            enabled = _optional_query(
                errors,
                f"mod.{mod_type}.enabled",
                lambda current=mod_type: instrument.get_mod_type_state(current, channel),
                False,
            )
            if enabled and state["type"] is None:
                state["enabled"] = True
                state["type"] = mod_type

    mod_type = state["type"]
    if not mod_type:
        return state

    if device_type == "DG4000":
        source_getters = {
            "AM": instrument.get_mod_am_source,
            "FM": instrument.get_mod_fm_source,
            "PM": instrument.get_mod_pm_source,
            "FSKey": instrument.get_mod_fsk_source,
            "PWM": instrument.get_mod_pwm_source,
        }
        frequency_getters = {
            "AM": instrument.get_mod_am_internal_freq,
            "FM": instrument.get_mod_fm_internal_freq,
            "PM": instrument.get_mod_pm_internal_freq,
            "FSKey": instrument.get_mod_fsk_rate,
            "PWM": instrument.get_mod_pwm_internal_freq,
        }
        function_getters = {
            "AM": instrument.get_mod_am_internal_func,
            "FM": instrument.get_mod_fm_internal_func,
            "PM": instrument.get_mod_pm_internal_func,
            "PWM": instrument.get_mod_pwm_internal_func,
        }
        source_getter = source_getters.get(mod_type)
        frequency_getter = frequency_getters.get(mod_type)
        function_getter = function_getters.get(mod_type)
        if source_getter:
            state["source"] = _optional_query(
                errors, "mod.source", lambda: source_getter(channel)
            )
        if frequency_getter and state["source"] != "EXTernal":
            state["internal_frequency"] = _optional_query(
                errors, "mod.internal_frequency", lambda: frequency_getter(channel)
            )
        if function_getter and state["source"] != "EXTernal":
            state["internal_function"] = _optional_query(
                errors, "mod.internal_function", lambda: function_getter(channel)
            )
    else:
        state["source"] = _optional_query(
            errors,
            "mod.source",
            lambda: instrument.get_mod_source(mod_type, channel),
        )
        if state["source"] != "EXTernal":
            state["internal_frequency"] = _optional_query(
                errors,
                "mod.internal_frequency",
                lambda: instrument.get_mod_internal_frequency(mod_type, channel),
            )
        if mod_type != "FSKey" and state["source"] != "EXTernal":
            state["internal_function"] = _optional_query(
                errors,
                "mod.internal_function",
                lambda: instrument.get_mod_internal_function(mod_type, channel),
            )

    value_getters = {
        "AM": ("am_depth", instrument.get_mod_am_depth),
        "FM": ("fm_deviation", instrument.get_mod_fm_deviation),
        "PM": ("pm_deviation", instrument.get_mod_pm_deviation),
        "FSKey": ("fsk_frequency", instrument.get_mod_fsk_frequency),
        "PWM": ("pwm_duty_deviation", instrument.get_mod_pwm_deviation_dcycle),
    }
    field_getter = value_getters.get(mod_type)
    if field_getter:
        field, getter = field_getter
        state[field] = _optional_query(
            errors, f"mod.{field}", lambda: getter(channel)
        )
    if mod_type == "FSKey":
        state["fsk_rate"] = state["internal_frequency"]
        state["fsk_polarity"] = _optional_query(
            errors,
            "mod.fsk_polarity",
            lambda: instrument.get_mod_fsk_polarity(channel),
        )
    return state


def _read_burst(instrument, channel: int,
                errors: list[dict[str, str]]) -> dict[str, Any]:
    getters = {
        "enabled": instrument.get_burst_state,
        "mode": instrument.get_burst_mode,
        "ncycles": instrument.get_burst_ncycles,
        "phase": instrument.get_burst_phase,
        "period": instrument.get_burst_period,
        "delay": instrument.get_burst_delay,
        "trigger_source": instrument.get_burst_trigger_source,
        "trigger_slope": instrument.get_burst_trigger_slope,
    }
    return {
        field: _optional_query(
            errors,
            f"burst.{field}",
            lambda current=getter: current(channel),
            False if field == "enabled" else None,
        )
        for field, getter in getters.items()
    }


def _read_generator_channel(instrument, device_type: str, channel) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    if device_type == "DG900":
        waveform = instrument.get_wave_parameters(channel.number)
        shape = _canonical_shape(str(waveform["shape"]))
        state = {
            **waveform,
            "shape": shape,
            "voltage_unit": None,
            "load": None,
        }
    else:
        shape = _canonical_shape(instrument.get_shape(channel.number))
        state = {
            "shape": shape,
            "frequency": instrument.get_frequency(channel.number),
            "amplitude": instrument.get_amplitude(channel.number),
            "offset": instrument.get_offset(channel.number),
            "phase": instrument.get_phase_adjust(channel.number),
            "voltage_unit": instrument.get_voltage_unit(channel.number),
            "load": instrument.get_output_load(channel.number),
        }
    state.update({
        "number": channel.number,
        "mapping_key": channel.mapping_key,
        "label": channel.label,
        "read_only": channel.read_only,
        "output": instrument.get_output(channel.number),
        "square_duty": None,
        "ramp_symmetry": None,
        "pulse_width": None,
        "pulse_delay": None,
    })
    if shape.startswith("SQU"):
        state["square_duty"] = _optional_query(
            errors, "square_duty",
            lambda: instrument.get_square_dcycle(channel.number),
        )
    elif shape.startswith("RAMP"):
        state["ramp_symmetry"] = _optional_query(
            errors, "ramp_symmetry",
            lambda: instrument.get_ramp_symmetry(channel.number),
        )
    elif shape.startswith("PULS"):
        state["pulse_width"] = _optional_query(
            errors, "pulse_width",
            lambda: instrument.get_pulse_width(channel.number),
        )
        if device_type == "DG4000":
            state["pulse_delay"] = _optional_query(
                errors, "pulse_delay",
                lambda: instrument.get_pulse_delay(channel.number),
            )
    state["mod"] = _read_modulation(
        instrument, device_type, channel.number, errors
    )
    state["burst"] = _read_burst(instrument, channel.number, errors)
    state["readback_errors"] = errors
    return state


def _gs200_mapping_key(record: DeviceRecord) -> str:
    mapping_key = str(record.options.get("mapping_key", "")).strip()
    if not mapping_key:
        raise ValueError(f"GS200 设备 {record.id} 缺少 mapping_key")
    return mapping_key


def _gs200_safety_rule(mapping_key: str) -> dict[str, Any]:
    rule = load_safety_limits().get(mapping_key)
    if not rule or rule.get("min") is None or rule.get("max") is None:
        raise ValueError(f"{mapping_key} 缺少完整的安全上下限")
    low = float(rule["min"])
    high = float(rule["max"])
    if not math.isfinite(low) or not math.isfinite(high) or low > high:
        raise ValueError(f"{mapping_key} 的安全上下限无效")
    return rule


def _finite_float(field: str, value: Any, *, scale: float = 1.0) -> float:
    converted = float(value) * scale
    if not math.isfinite(converted):
        raise ValueError(f"GS200 {field} 不是有限数值")
    return converted


def _read_gs200_state(record: DeviceRecord, instrument) -> dict[str, Any]:
    """读取 GS200 状态；非电流模式不把源电平误标为电流。"""
    mapping_key = _gs200_mapping_key(record)
    rule = _gs200_safety_rule(mapping_key)
    device = record.to_dict()
    device.pop("channels", None)
    source_function = str(instrument.get_source_function()).strip()
    is_current_mode = source_function.upper().startswith("CURR")
    current_ma = (
        _finite_float("电流设定值", instrument.get_current(), scale=1000.0)
        if is_current_mode
        else None
    )
    current_range_ma = (
        _finite_float("电流量程", instrument.get_current_range(), scale=1000.0)
        if is_current_mode
        else None
    )
    if current_ma is not None:
        validate_safety_limit(mapping_key, current_ma, {mapping_key: rule})
    return {
        **device,
        "idn": instrument.idn(),
        "mapping_key": mapping_key,
        "source_function": source_function,
        "output": bool(instrument.get_output()),
        "current_ma": current_ma,
        "current_range_ma": current_range_ma,
        "voltage_limit_v": _finite_float("限压", instrument.get_voltage_limit()),
        "current_limit_ma": _finite_float(
            "限流",
            instrument.get_current_limit(),
            scale=1000.0,
        ),
        "min_current_ma": float(rule["min"]),
        "max_current_ma": float(rule["max"]),
    }


def _raise_after_gs200_error(
    instrument,
    mapping_key: str,
    error: Exception,
) -> None:
    """按安全配置尽力关闭输出，并保留原始异常上下文。"""
    try:
        rule = load_safety_limits().get(mapping_key, {})
        should_shutdown = bool(rule.get("output_off_on_error", True))
    except Exception:
        should_shutdown = True
    if should_shutdown:
        try:
            instrument.set_output(False)
        except Exception as shutdown_error:
            raise RuntimeError(
                f"{error}；GS200 输出关断失败: {shutdown_error}"
            ) from error
    raise error


_LASER_SAFETY_OPTIONS = {
    "current": "current_safety_key",
    "temperature": "temperature_safety_key",
    "pzt": "pzt_safety_key",
    "scan_amplitude": "scan_amplitude_safety_key",
}


def _laser_safety_rules(
    record: DeviceRecord,
) -> dict[str, tuple[str, dict[str, Any]]]:
    """读取完整且有效的 DLC pro 安全限值；缺项时禁止控制。"""
    all_rules = load_safety_limits()
    result: dict[str, tuple[str, dict[str, Any]]] = {}
    for name, option_key in _LASER_SAFETY_OPTIONS.items():
        safety_key = str(record.options.get(option_key, "")).strip()
        if not safety_key:
            raise ValueError(f"DLC pro 缺少 {option_key} 配置")
        rule = all_rules.get(safety_key)
        if not rule or rule.get("min") is None or rule.get("max") is None:
            raise ValueError(f"{safety_key} 缺少完整的安全上下限")
        low = _finite_laser_float(f"{safety_key} 下限", rule["min"])
        high = _finite_laser_float(f"{safety_key} 上限", rule["max"])
        if low > high:
            raise ValueError(f"{safety_key} 的安全上下限无效")
        result[name] = (safety_key, rule)
    return result


def _finite_laser_float(field: str, value: Any) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"DLC pro {field} 必须是数值") from exc
    if not math.isfinite(converted):
        raise ValueError(f"DLC pro {field} 不是有限数值")
    return converted


def _validate_laser_envelope(
    pzt_voltage_v: Any,
    scan_amplitude_vpp: Any,
    rules: dict[str, tuple[str, dict[str, Any]]],
) -> tuple[float, float]:
    pzt = _finite_laser_float("PZT 电压", pzt_voltage_v)
    amplitude = _finite_laser_float("扫描幅度", scan_amplitude_vpp)
    pzt_key, pzt_rule = rules["pzt"]
    amplitude_key, amplitude_rule = rules["scan_amplitude"]
    validate_safety_limit(pzt_key, pzt, {pzt_key: pzt_rule})
    validate_safety_limit(
        amplitude_key,
        amplitude,
        {amplitude_key: amplitude_rule},
    )
    low = float(pzt_rule["min"])
    high = float(pzt_rule["max"])
    envelope_low = pzt - amplitude / 2.0
    envelope_high = pzt + amplitude / 2.0
    if envelope_low < low or envelope_high > high:
        raise ValueError(
            "DLC pro 扫描包络越界："
            f"{pzt:g} V ± {amplitude:g} Vpp / 2 = "
            f"{envelope_low:g}～{envelope_high:g} V，"
            f"允许范围为 {low:g}～{high:g} V"
        )
    return pzt, amplitude


def _laser_identity(record: DeviceRecord, instrument) -> dict[str, str]:
    controller_serial = instrument.get_controller_serial()
    laser_head_serial = instrument.get_laser_head_serial()
    expected_controller = str(
        record.options.get("controller_serial", "")
    ).strip()
    expected_head = str(record.options.get("laser_head_serial", "")).strip()
    if expected_controller and controller_serial != expected_controller:
        raise RuntimeError(
            "DLC pro 控制器身份不匹配："
            f"期望 {expected_controller!r}，实际 {controller_serial!r}"
        )
    if expected_head and laser_head_serial != expected_head:
        raise RuntimeError(
            "DLC pro 激光头身份不匹配："
            f"期望 {expected_head!r}，实际 {laser_head_serial!r}"
        )
    return {
        "controller_serial": controller_serial,
        "laser_head_serial": laser_head_serial,
    }


def _read_dlc_pro_state(
    record: DeviceRecord,
    instrument,
) -> dict[str, Any]:
    rules = _laser_safety_rules(record)
    identity = _laser_identity(record, instrument)
    device = record.to_dict()
    device.pop("channels", None)
    current_key, current_rule = rules["current"]
    temperature_key, temperature_rule = rules["temperature"]
    pzt_key, pzt_rule = rules["pzt"]
    amplitude_key, amplitude_rule = rules["scan_amplitude"]
    return {
        **device,
        **identity,
        "system_type": instrument.get_system_type(),
        "system_label": instrument.get_system_label(),
        "firmware_version": instrument.get_firmware_version(),
        "system_health_code": instrument.get_system_health_code(),
        "system_health": instrument.get_system_health(),
        "interlock_open": instrument.get_interlock_open(),
        "front_key_locked": instrument.get_front_key_locked(),
        "emission": instrument.get_emission(),
        "laser_type": instrument.get_laser_type(),
        "laser_product_name": instrument.get_laser_product_name(),
        "laser_enabled": instrument.get_laser_enabled(),
        "laser_health_code": instrument.get_laser_health_code(),
        "laser_health": instrument.get_laser_health(),
        "laser_emission": instrument.get_laser_emission(),
        "laser_head_model": instrument.get_laser_head_model(),
        "current_set_ma": instrument.get_laser_current_set_ma(),
        "current_actual_ma": instrument.get_laser_current_actual_ma(),
        "current_clip_ma": instrument.get_laser_current_clip_ma(),
        "current_clip_limit_ma": (
            instrument.get_laser_current_clip_limit_ma()
        ),
        "min_current_ma": float(current_rule["min"]),
        "max_current_ma": float(current_rule["max"]),
        "temperature_set_c": instrument.get_laser_temperature_set_c(),
        "temperature_actual_c": instrument.get_laser_temperature_actual_c(),
        "min_temperature_c": float(temperature_rule["min"]),
        "max_temperature_c": float(temperature_rule["max"]),
        "pzt_voltage_v": instrument.get_pzt_voltage_v(),
        "pzt_actual_v": instrument.get_pzt_voltage_actual_v(),
        "min_pzt_voltage_v": float(pzt_rule["min"]),
        "max_pzt_voltage_v": float(pzt_rule["max"]),
        "scan_amplitude_vpp": instrument.get_scan_amplitude_vpp(),
        "min_scan_amplitude_vpp": float(amplitude_rule["min"]),
        "max_scan_amplitude_vpp": float(amplitude_rule["max"]),
        "scan_frequency_hz": instrument.get_scan_frequency_hz(),
        "scan_enabled": instrument.get_scan_enabled(),
        "scan_unit": instrument.get_scan_unit(),
        "scan_output_channel": instrument.get_scan_output_channel(),
        "remote_emission_control_enabled": bool(
            record.options.get(
                "remote_emission_control_enabled",
                False,
            )
        ),
        "safety_keys": {
            "current": current_key,
            "temperature": temperature_key,
            "pzt": pzt_key,
            "scan_amplitude": amplitude_key,
        },
        "state_known": True,
    }


def _raise_laser_state_unknown(error: Exception) -> None:
    raise RuntimeError(
        f"{error}；设备状态可能未知，请检查 DLC pro/TOPAS"
    ) from error


def read_device(device_id: str) -> dict[str, Any]:
    """连接设备并返回页面需要的完整状态。"""
    record = find_device(device_id)
    instrument = _connect(record)
    try:
        if record.type == "DLC_PRO":
            try:
                return _read_dlc_pro_state(record, instrument)
            except Exception as exc:
                _raise_laser_state_unknown(exc)

        if record.type == "GS200":
            try:
                return _read_gs200_state(record, instrument)
            except Exception as exc:
                _raise_after_gs200_error(
                    instrument,
                    _gs200_mapping_key(record),
                    exc,
                )

        if record.type in {"DG4000", "DG900"}:
            channels = [
                _read_generator_channel(instrument, record.type, channel)
                for channel in record.channels
            ]
            return {
                **record.to_dict(),
                "idn": instrument.idn(),
                "reference_clock": instrument.get_ref_clock_source(),
                "channels": channels,
            }

        channels = [
            {
                "number": number,
                "label": f"CH{number}",
                "enabled": instrument.get_channel_state(number),
                "scale": instrument.get_channel_scale(number),
                "offset": instrument.get_channel_offset(number),
                "coupling": instrument.get_channel_coupling(number),
                "impedance": instrument.get_channel_impedance(number),
                "probe": instrument.get_channel_probe(number),
            }
            for number in range(1, 5)
        ]
        return {
            **record.to_dict(),
            "idn": instrument.idn(),
            "sampling_rate": instrument.get_sampling_rate(),
            "memory_depth": instrument.get_memory_depth(),
            "acquire_type": instrument.get_acquire_type(),
            "timebase_scale": instrument.get_timebase_scale(),
            "timebase_delay": instrument.get_timebase_delay(),
            "channels": channels,
            "trigger": {
                "mode": instrument.get_trigger_mode(),
                "type": instrument.get_trigger_type(),
                "source": instrument.get_trigger_source(),
                "slope": instrument.get_trigger_slope(),
                "level": instrument.get_trigger_level(),
            },
        }
    finally:
        instrument.disconnect()


def _validate_generator_output(mapping_key: str, settings: dict[str, Any]) -> None:
    limits = load_safety_limits()
    if "dc_voltage" in settings:
        validate_safety_limit(mapping_key, float(settings["dc_voltage"]), limits)
        return
    offset = float(settings.get("offset") or 0.0)
    amplitude = abs(float(settings.get("amplitude") or 0.0))
    validate_safety_limit(mapping_key, offset, limits)
    validate_safety_limit(mapping_key, offset - amplitude / 2, limits)
    validate_safety_limit(mapping_key, offset + amplitude / 2, limits)


def _apply_basic_waveform(instrument, device_type: str, channel: int,
                          settings: dict[str, Any]) -> None:
    shape = settings.get("shape")
    is_dc = str(shape).upper() == "DC"
    if shape:
        if device_type == "DG900" and is_dc:
            instrument.setup_dc(float(settings.get("offset", 0.0)), channel)
        else:
            instrument.set_shape(str(shape), channel)
    if not is_dc:
        setters = (
            ("frequency", instrument.set_frequency),
            ("amplitude", instrument.set_amplitude),
            ("offset", instrument.set_offset),
        )
        for key, setter in setters:
            if settings.get(key) is not None:
                setter(float(settings[key]), channel)
        if settings.get("phase") is not None:
            phase_setter = (
                instrument.set_phase
                if device_type == "DG900"
                else instrument.set_phase_adjust
            )
            phase_setter(float(settings["phase"]), channel)
    waveform_specific = [
        ("square_duty", instrument.set_square_dcycle),
        ("ramp_symmetry", instrument.set_ramp_symmetry),
        ("pulse_width", instrument.set_pulse_width),
    ]
    if device_type == "DG4000":
        waveform_specific.append(("pulse_delay", instrument.set_pulse_delay))
    for key, setter in waveform_specific:
        if settings.get(key) is not None:
            setter(float(settings[key]), channel)
    if device_type == "DG4000":
        if settings.get("voltage_unit"):
            instrument.set_voltage_unit(settings["voltage_unit"], channel)
        if settings.get("load") is not None:
            load = settings["load"]
            instrument.set_output_load(
                "INF" if str(load).upper().startswith("INF") else float(load),
                channel,
            )


def _apply_modulation(instrument, device_type: str, channel: int,
                      mod: dict[str, Any]) -> None:
    mod_type = mod.get("type") or "AM"
    if str(mod_type).upper() == "FSK":
        mod_type = "FSKey"
    source = mod.get("source") or "INTernal"

    if device_type == "DG4000":
        instrument.set_mod_type(mod_type, channel)
        source_setters = {
            "AM": instrument.set_mod_am_source,
            "FM": instrument.set_mod_fm_source,
            "PM": instrument.set_mod_pm_source,
            "FSKey": instrument.set_mod_fsk_source,
            "PWM": instrument.set_mod_pwm_source,
        }
        source_setters[mod_type](source, channel)
    else:
        instrument.set_mod_source(mod_type, source, channel)

    values = {
        "AM": ("am_depth", instrument.set_mod_am_depth),
        "FM": ("fm_deviation", instrument.set_mod_fm_deviation),
        "PM": ("pm_deviation", instrument.set_mod_pm_deviation),
        "FSKey": ("fsk_frequency", instrument.set_mod_fsk_frequency),
        "PWM": ("pwm_duty_deviation", instrument.set_mod_pwm_deviation_dcycle),
    }
    key, setter = values[mod_type]
    if mod.get(key) is not None:
        setter(float(mod[key]), channel)

    internal_frequency = mod.get("internal_frequency")
    if mod_type == "FSKey" and mod.get("fsk_rate") is not None:
        internal_frequency = mod["fsk_rate"]
    if source != "EXTernal" and internal_frequency is not None:
        if device_type == "DG900":
            instrument.set_mod_internal_frequency(
                mod_type, float(internal_frequency), channel
            )
        else:
            frequency_setters = {
                "AM": instrument.set_mod_am_internal_freq,
                "FM": instrument.set_mod_fm_internal_freq,
                "PM": instrument.set_mod_pm_internal_freq,
                "FSKey": instrument.set_mod_fsk_rate,
                "PWM": instrument.set_mod_pwm_internal_freq,
            }
            frequency_setters[mod_type](float(internal_frequency), channel)

    internal_function = mod.get("internal_function")
    if (source != "EXTernal" and internal_function
            and mod_type != "FSKey"):
        if device_type == "DG900":
            instrument.set_mod_internal_function(
                mod_type, internal_function, channel
            )
        else:
            function_setters = {
                "AM": instrument.set_mod_am_internal_func,
                "FM": instrument.set_mod_fm_internal_func,
                "PM": instrument.set_mod_pm_internal_func,
                "PWM": instrument.set_mod_pwm_internal_func,
            }
            function_setters[mod_type](internal_function, channel)
    if mod_type == "FSKey" and mod.get("fsk_polarity"):
        instrument.set_mod_fsk_polarity(mod["fsk_polarity"], channel)


def _apply_burst(instrument, channel: int, burst: dict[str, Any]) -> None:
    values = (
        ("mode", instrument.set_burst_mode, str),
        ("ncycles", instrument.set_burst_ncycles, lambda value: value),
        ("phase", instrument.set_burst_phase, float),
        ("period", instrument.set_burst_period, float),
        ("delay", instrument.set_burst_delay, float),
        ("trigger_source", instrument.set_burst_trigger_source, str),
        ("trigger_slope", instrument.set_burst_trigger_slope, str),
    )
    for key, setter, convert in values:
        if burst.get(key) is not None:
            setter(convert(burst[key]), channel)


def apply_generator_channel(
    device_id: str,
    channel_number: int,
    settings: dict[str, Any],
) -> dict[str, Any]:
    record = find_device(device_id)
    channel = next((item for item in record.channels if item.number == channel_number), None)
    if not channel:
        raise KeyError(f"设备 {device_id} 没有 CH{channel_number}")
    if channel.read_only:
        raise PermissionError(f"{channel.label} 在通用设备页只读")
    if record.type not in {"DG4000", "DG900"}:
        raise TypeError("该接口仅用于信号发生器")
    _validate_generator_output(channel.mapping_key, settings)

    instrument = _connect(record)
    try:
        try:
            if record.type == "DG900":
                instrument.clear_status()

            _apply_basic_waveform(instrument, record.type, channel_number, settings)

            mod = dict(settings.get("mod") or {})
            burst = dict(settings.get("burst") or {})
            if mod and burst and mod.get("enabled") and burst.get("enabled"):
                target = settings.get("target_mode")
                if target == "mod":
                    burst["enabled"] = False
                elif target == "burst":
                    mod["enabled"] = False
                else:
                    raise ValueError("Mod 与 Burst 不能同时启用")

            if mod or burst:
                if record.type == "DG900":
                    if burst.get("enabled"):
                        instrument.disable_all_mod(channel_number)
                        _apply_burst(instrument, channel_number, burst)
                        instrument.set_burst_state(True, channel_number)
                    elif mod.get("enabled"):
                        instrument.set_burst_state(False, channel_number)
                        instrument.disable_all_mod(channel_number)
                        if (mod.get("type") or "AM") == "PWM" and not str(
                                settings.get("shape", "")).upper().startswith("PULS"):
                            raise ValueError("PWM 调制要求基础波形为 PULSe")
                        _apply_modulation(
                            instrument, record.type, channel_number, mod
                        )
                        instrument.set_mod_type_state(
                            mod.get("type") or "AM", True, channel_number
                        )
                    else:
                        instrument.set_burst_state(False, channel_number)
                        instrument.disable_all_mod(channel_number)
                else:
                    if burst.get("enabled"):
                        instrument.set_mod_state(False, channel_number)
                        _apply_burst(instrument, channel_number, burst)
                        instrument.set_burst_state(True, channel_number)
                    elif mod.get("enabled"):
                        instrument.set_burst_state(False, channel_number)
                        if (mod.get("type") or "AM") == "PWM" and not str(
                                settings.get("shape", "")).upper().startswith("PULS"):
                            raise ValueError("PWM 调制要求基础波形为 PULSe")
                        _apply_modulation(
                            instrument, record.type, channel_number, mod
                        )
                        instrument.set_mod_state(True, channel_number)
                    else:
                        instrument.set_burst_state(False, channel_number)
                        instrument.set_mod_state(False, channel_number)

            if "output" in settings:
                instrument.set_output(bool(settings["output"]), channel_number)

            if record.type == "DG900":
                instrument.wait_for_operation_complete()
                instrument.raise_for_errors()
        except Exception:
            rule = load_safety_limits().get(channel.mapping_key, {})
            if rule.get("output_off_on_error"):
                try:
                    instrument.set_output(False, channel_number)
                except Exception:
                    pass
            raise
    finally:
        instrument.disconnect()
    return read_device(device_id)


def apply_current_source(
    device_id: str,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """安全设置 GS200 主磁场电流和输出状态，并返回实际回读值。"""
    if not settings or not ({"current_ma", "output"} & settings.keys()):
        raise ValueError("电流源设置至少需要 current_ma 或 output")

    record = find_device(device_id)
    if record.type != "GS200":
        raise TypeError("该接口仅用于 GS200 电流源")
    mapping_key = _gs200_mapping_key(record)
    rule = _gs200_safety_rule(mapping_key)
    current_ma = settings.get("current_ma")
    if current_ma is not None:
        current_ma = _finite_float("目标电流", current_ma)
        validate_safety_limit(mapping_key, current_ma, {mapping_key: rule})

    instrument = _connect(record)
    try:
        try:
            output_before = bool(instrument.get_output())
            requested_output = settings.get("output")
            if requested_output is True and not output_before:
                if current_ma is None:
                    raise ValueError("开启 GS200 输出时必须同时提供 current_ma")
                if settings.get("confirm_output_enable") is not True:
                    raise ValueError("开启 GS200 输出需要二次确认")

            if requested_output is False:
                instrument.set_output(False)

            if current_ma is not None:
                instrument.set_current(current_ma / 1000.0)
                source_function = str(instrument.get_source_function()).strip()
                if not source_function.upper().startswith("CURR"):
                    raise RuntimeError(
                        f"GS200 写入后仍处于 {source_function!r} 模式"
                    )
                actual_current_ma = _finite_float(
                    "电流回读值",
                    instrument.get_current(),
                    scale=1000.0,
                )
                validate_safety_limit(
                    mapping_key,
                    actual_current_ma,
                    {mapping_key: rule},
                )

            if requested_output is True:
                instrument.set_output(True)

            return _read_gs200_state(record, instrument)
        except Exception as exc:
            _raise_after_gs200_error(instrument, mapping_key, exc)
    finally:
        instrument.disconnect()


def _normalize_laser_settings(
    record: DeviceRecord,
    settings: dict[str, Any],
    rules: dict[str, tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    allowed = {
        "current_set_ma",
        "temperature_set_c",
        "pzt_voltage_v",
        "scan_amplitude_vpp",
        "scan_enabled",
    }
    unknown = set(settings) - allowed
    if unknown:
        raise ValueError(
            "DLC pro 设置包含未知字段: " + ", ".join(sorted(unknown))
        )
    if not settings:
        raise ValueError("DLC pro 设置至少需要一个控制量")

    normalized: dict[str, Any] = {}
    numeric_fields = {
        "current_set_ma": ("激光电流", "current"),
        "temperature_set_c": ("激光温度", "temperature"),
        "pzt_voltage_v": ("PZT 电压", "pzt"),
        "scan_amplitude_vpp": ("扫描幅度", "scan_amplitude"),
    }
    for field, (label, rule_name) in numeric_fields.items():
        if field not in settings or settings[field] is None:
            continue
        value = _finite_laser_float(label, settings[field])
        safety_key, rule = rules[rule_name]
        validate_safety_limit(safety_key, value, {safety_key: rule})
        normalized[field] = value

    if "scan_enabled" in settings:
        if not isinstance(settings["scan_enabled"], bool):
            raise TypeError("DLC pro 扫描启停值必须为布尔值")
        normalized["scan_enabled"] = settings["scan_enabled"]

    if not normalized:
        raise ValueError("DLC pro 设置至少需要一个非空控制量")
    if (
        "pzt_voltage_v" in normalized
        and "scan_amplitude_vpp" in normalized
    ):
        _validate_laser_envelope(
            normalized["pzt_voltage_v"],
            normalized["scan_amplitude_vpp"],
            rules,
        )
    return normalized


def _validate_laser_targets(
    normalized: dict[str, Any],
    snapshot: dict[str, Any],
    rules: dict[str, tuple[str, dict[str, Any]]],
) -> tuple[float, float]:
    target_current = normalized.get("current_set_ma")
    if target_current is not None:
        current_clip = _finite_laser_float(
            "实时 current-clip",
            snapshot["current_clip_ma"],
        )
        if target_current > current_clip:
            raise ValueError(
                f"DLC pro 激光电流 {target_current:g} mA 超过设备实时 "
                f"current-clip {current_clip:g} mA"
            )
    target_pzt = normalized.get(
        "pzt_voltage_v",
        snapshot["pzt_voltage_v"],
    )
    target_amplitude = normalized.get(
        "scan_amplitude_vpp",
        snapshot["scan_amplitude_vpp"],
    )
    return _validate_laser_envelope(
        target_pzt,
        target_amplitude,
        rules,
    )


def _laser_envelope_is_valid(
    pzt_voltage_v: float,
    scan_amplitude_vpp: float,
    rules: dict[str, tuple[str, dict[str, Any]]],
) -> bool:
    try:
        _validate_laser_envelope(pzt_voltage_v, scan_amplitude_vpp, rules)
    except ValueError:
        return False
    return True


def _apply_laser_scan_targets(
    instrument,
    normalized: dict[str, Any],
    snapshot: dict[str, Any],
    rules: dict[str, tuple[str, dict[str, Any]]],
) -> None:
    change_pzt = "pzt_voltage_v" in normalized
    change_amplitude = "scan_amplitude_vpp" in normalized
    if not change_pzt and not change_amplitude:
        return
    target_pzt = float(
        normalized.get("pzt_voltage_v", snapshot["pzt_voltage_v"])
    )
    target_amplitude = float(
        normalized.get(
            "scan_amplitude_vpp",
            snapshot["scan_amplitude_vpp"],
        )
    )
    current_pzt = float(snapshot["pzt_voltage_v"])
    current_amplitude = float(snapshot["scan_amplitude_vpp"])

    if change_pzt and not change_amplitude:
        instrument.set_pzt_voltage_v(target_pzt)
        return
    if change_amplitude and not change_pzt:
        instrument.set_scan_amplitude_vpp(target_amplitude)
        return
    if _laser_envelope_is_valid(target_pzt, current_amplitude, rules):
        instrument.set_pzt_voltage_v(target_pzt)
        instrument.set_scan_amplitude_vpp(target_amplitude)
    elif _laser_envelope_is_valid(current_pzt, target_amplitude, rules):
        instrument.set_scan_amplitude_vpp(target_amplitude)
        instrument.set_pzt_voltage_v(target_pzt)
    else:
        # 两个端点都无法一步安全到达时，先将扫描幅度收至 0。
        instrument.set_scan_amplitude_vpp(0.0)
        instrument.set_pzt_voltage_v(target_pzt)
        instrument.set_scan_amplitude_vpp(target_amplitude)


def apply_laser_settings(
    device_id: str,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """安全设置 DLC pro 电流、温度、PZT 和扫描状态。"""
    record = find_device(device_id)
    if record.type != "DLC_PRO":
        raise TypeError("该接口仅用于 TOPTICA DLC pro 激光器")
    rules = _laser_safety_rules(record)
    normalized = _normalize_laser_settings(record, settings, rules)

    instrument = _connect(record)
    try:
        try:
            before = _read_dlc_pro_state(record, instrument)
        except Exception as exc:
            _raise_laser_state_unknown(exc)
        _validate_laser_targets(normalized, before, rules)

        try:
            if "current_set_ma" in normalized:
                instrument.set_laser_current_ma(
                    normalized["current_set_ma"]
                )
            if "temperature_set_c" in normalized:
                instrument.set_laser_temperature_c(
                    normalized["temperature_set_c"]
                )
            _apply_laser_scan_targets(
                instrument,
                normalized,
                before,
                rules,
            )
            if "scan_enabled" in normalized:
                instrument.set_scan_enabled(normalized["scan_enabled"])
            return _read_dlc_pro_state(record, instrument)
        except Exception as exc:
            _raise_laser_state_unknown(exc)
    finally:
        instrument.disconnect()


def _validate_emission_preconditions(
    snapshot: dict[str, Any],
    rules: dict[str, tuple[str, dict[str, Any]]],
) -> None:
    if int(snapshot["system_health_code"]) != 0:
        raise RuntimeError("DLC pro 系统健康状态异常，拒绝开启 Emission")
    if int(snapshot["laser_health_code"]) != 0:
        raise RuntimeError("DLC pro 激光头健康状态异常，拒绝开启 Emission")
    if bool(snapshot["interlock_open"]):
        raise RuntimeError("DLC pro 联锁回路断开，拒绝开启 Emission")
    if not bool(snapshot["laser_enabled"]):
        raise RuntimeError("DLC pro Laser Enabled 为 OFF，拒绝开启 Emission")

    values = (
        ("current", "current_set_ma"),
        ("current", "current_actual_ma"),
        ("temperature", "temperature_set_c"),
        ("temperature", "temperature_actual_c"),
        ("pzt", "pzt_voltage_v"),
        ("pzt", "pzt_actual_v"),
        ("scan_amplitude", "scan_amplitude_vpp"),
    )
    for rule_name, field in values:
        safety_key, rule = rules[rule_name]
        value = _finite_laser_float(field, snapshot[field])
        validate_safety_limit(safety_key, value, {safety_key: rule})
    current_clip = _finite_laser_float(
        "current-clip",
        snapshot["current_clip_ma"],
    )
    if float(snapshot["current_set_ma"]) > current_clip:
        raise RuntimeError(
            "DLC pro 电流设定值超过实时 current-clip，拒绝开启 Emission"
        )
    _validate_laser_envelope(
        snapshot["pzt_voltage_v"],
        snapshot["scan_amplitude_vpp"],
        rules,
    )


def apply_laser_emission(
    device_id: str,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """通过独立高风险接口控制 Emission，并强制回读。"""
    allowed = {
        "enabled",
        "safety_acknowledged",
        "confirm_emission_enable",
        "confirmation_text",
    }
    unknown = set(settings) - allowed
    if unknown:
        raise ValueError(
            "Emission 设置包含未知字段: " + ", ".join(sorted(unknown))
        )
    if not isinstance(settings.get("enabled"), bool):
        raise TypeError("Emission 设置必须包含布尔值 enabled")

    record = find_device(device_id)
    if record.type != "DLC_PRO":
        raise TypeError("该接口仅用于 TOPTICA DLC pro 激光器")
    enabled = settings["enabled"]
    remote_allowed = bool(
        record.options.get("remote_emission_control_enabled", False)
    )
    if enabled:
        if not remote_allowed:
            raise PermissionError(
                "远程 Emission ON 默认禁用；确认 SI 模块、激光等级、"
                "联锁和警示回路后方可修改 mapping.yaml 开放"
            )
        if settings.get("safety_acknowledged") is not True:
            raise ValueError("开启 Emission 前必须勾选安全确认")
        if settings.get("confirm_emission_enable") is not True:
            raise ValueError("开启 Emission 需要再次确认")
        expected_text = str(
            record.options.get("controller_serial", record.id)
        )
        if settings.get("confirmation_text") != expected_text:
            raise ValueError(
                f"开启 Emission 必须输入 {expected_text}"
            )

    rules = _laser_safety_rules(record)
    instrument = _connect(record)
    try:
        try:
            before = _read_dlc_pro_state(record, instrument)
        except Exception as exc:
            _raise_laser_state_unknown(exc)
        if enabled:
            _validate_emission_preconditions(before, rules)
        try:
            instrument.set_emission(
                enabled,
                remote_enable_allowed=remote_allowed,
            )
            after = _read_dlc_pro_state(record, instrument)
            if (
                bool(after["emission"]) is not enabled
                or bool(after["laser_emission"]) is not enabled
            ):
                raise RuntimeError(
                    "Emission 最终回读与请求状态不一致"
                )
            return after
        except Exception as exc:
            _raise_laser_state_unknown(exc)
    finally:
        instrument.disconnect()


def apply_scope(device_id: str, settings: dict[str, Any]) -> dict[str, Any]:
    record = find_device(device_id)
    if record.type != "SDS":
        raise TypeError("该接口仅用于 SDS 示波器")
    instrument = _connect(record)
    try:
        if settings.get("sampling_rate") is not None:
            instrument.set_sampling_rate(float(settings["sampling_rate"]))
        if settings.get("memory_depth"):
            instrument.set_memory_depth(str(settings["memory_depth"]))
        if settings.get("acquire_type"):
            instrument.set_acquire_type(
                str(settings["acquire_type"]), settings.get("acquire_type_param")
            )
        if settings.get("timebase_scale") is not None:
            instrument.set_timebase_scale(float(settings["timebase_scale"]))
        if settings.get("timebase_delay") is not None:
            instrument.set_timebase_delay(float(settings["timebase_delay"]))
        for channel in settings.get("channels", []):
            number = int(channel["number"])
            for key, setter in (
                ("enabled", instrument.set_channel_state),
                ("scale", instrument.set_channel_scale),
                ("offset", instrument.set_channel_offset),
                ("coupling", instrument.set_channel_coupling),
                ("impedance", instrument.set_channel_impedance),
                ("probe", instrument.set_channel_probe),
            ):
                if key in channel:
                    setter(number, channel[key])
        trigger = settings.get("trigger", {})
        for key, setter in (
            ("mode", instrument.set_trigger_mode),
            ("type", instrument.set_trigger_type),
            ("source", instrument.set_trigger_source),
            ("slope", instrument.set_trigger_slope),
            ("level", instrument.set_trigger_level),
        ):
            if key in trigger:
                setter(trigger[key])
    finally:
        instrument.disconnect()
    return read_device(device_id)
