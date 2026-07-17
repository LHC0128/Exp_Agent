"""GUI 使用的信号发生器与示波器通用读写服务。"""

from __future__ import annotations

from typing import Any

from sds_acquisition import SDSInstrument

from .common import load_safety_limits, validate_safety_limit
from .devices import DeviceRecord, SIGNAL_GENERATOR_DRIVERS, find_device


def _connect(record: DeviceRecord):
    if record.type in SIGNAL_GENERATOR_DRIVERS:
        instrument = SIGNAL_GENERATOR_DRIVERS[record.type](record.resource)
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


def read_device(device_id: str) -> dict[str, Any]:
    """连接设备并返回页面需要的完整状态。"""
    record = find_device(device_id)
    instrument = _connect(record)
    try:
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
    finally:
        instrument.disconnect()
    return read_device(device_id)


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
