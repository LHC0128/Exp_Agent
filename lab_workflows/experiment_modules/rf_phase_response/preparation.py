"""射频相位响应的设备连接、公共 Z 链配置、MFLI MF-MOD 射频准备与安全收尾。

链路与「射频相位灵敏度 A03」同源：射频由 MFLI Signal Output 1 的 MF-MOD 无调制载波
（Osc 2）经串联大电阻接到 Y 射频线圈，解调读出固定 Demod 4（索引 3）、参考 Osc 2；
Z 通道仍由 DG4000 直驱 ±π 脉冲（1200B 已物理旁路），X 静态偏置由 DG X 通道给出，
``Time_sequence_2`` 5 kHz 方波作为共同外触发。设备操作复用 ``lab_workflows.steps`` 与
``z_chain_common`` 的公共步骤；本模块只保留本实验的参数预检、编排与收尾清单。
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict

from gs200 import GS200Instrument
from hardware_gate import HardwareAccessDenied
from lockin_amplifier import (DemodulatorConfig, OscillatorConfig, SignalInputConfig,
                              acquisition_module, demod, lockin_capabilities)
from pyvisa.errors import VisaIOError
from signal_generator.types import normalize_output_load
from tec_controller import TECInstrument

from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...devices import create_lockin, signal_generator_max_arb_points
from ...experiment_runtime import check_cancelled
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...steps import (STANDARD_PRESERVED_OUTPUTS, ShutdownAction, configure_temperature_control,
                      connect_signal_generator_routes, initialize_lockin_inputs, run_safety_shutdown,
                      set_temperature_switch, synchronize_connected_clocks)
from ...steps.clock import normalize_clock_source
from ...steps.coil_drive_chain import SafetyAbort, current_limit, sleep
from ..z_chain_common import prep
from ..z_chain_common import rf_phase as shared_rf_phase
from ..z_chain_common.keys import (CURRENT_LIMIT_KEY, LOCKIN_KEY, MAIN_FIELD_KEY, SOURCE_KEY,
                                   TEC_KEY, TEMP_SWITCH_KEY, TRIGGER_KEY, X_KEY)
from ..z_chain_common.params import DG_HIGH_Z_MAX_VPP, amplifier_enabled
from ..z_chain_common.rf_phase import (MOD_CARRIER_OSC_IDX, mfli_output_levels,
                                       mfli_sigout_safety_key, select_mfli_output_range)
from . import run_format
from .models import READOUT_DEMOD_IDX, RfPhaseResponseParams

__all__ = [
    "ARB_POINTS", "ARB_SAMPLE_RATE_CHECK", "BURST_NCYCLES", "SafetyAbort", "TEMP_SWITCH_ON_V",
    "TRIGGER_DUTY", "TRIGGER_OFFSET_V", "TRIGGER_VPP", "arm_z", "clocks", "configure_chain",
    "configure_main_field", "configure_trigger", "configure_x_bias", "connect",
    "connect_and_synchronize", "current_limit", "disable_main_field", "enable_temperature_switch",
    "phase_drift_deg", "plan", "preflight", "prepare_measurement", "park", "pulse_wave",
    "write_temperature", "sleep",
]
ARB_POINTS = prep.ARB_POINTS
ARB_SAMPLE_RATE_CHECK = prep.ARB_SAMPLE_RATE_CHECK
BURST_NCYCLES = prep.BURST_NCYCLES
TEMP_SWITCH_ON_V = prep.TEMP_SWITCH_ON_V
TRIGGER_DUTY = prep.TRIGGER_DUTY
TRIGGER_OFFSET_V = prep.TRIGGER_OFFSET_V
TRIGGER_VPP = prep.TRIGGER_VPP
plan = prep.plan
pulse_wave = prep.pulse_wave
configure_chain = prep.configure_chain
configure_x_bias = prep.configure_x_bias
configure_trigger = prep.configure_trigger
configure_main_field = prep.configure_main_field
disable_main_field = prep.disable_main_field
enable_temperature_switch = prep.enable_temperature_switch
phase_drift_deg = prep.phase_drift_deg

_EXPECTED = {
    SOURCE_KEY: ("signal_generator", {"DG4000"}),
    X_KEY: ("signal_generator", {"DG4000"}),
    TRIGGER_KEY: ("signal_generator", {"DG4000"}),
    TEMP_SWITCH_KEY: ("signal_generator", {"DG4000", "DG900"}),
    LOCKIN_KEY: ("lockin_amplifier", {"MFLI"}),
    MAIN_FIELD_KEY: ("gs200", {"GS200"}),
    TEC_KEY: ("tec_controller", {"TEC103"}),
}
_DG_ROLES = (SOURCE_KEY, X_KEY, TRIGGER_KEY, TEMP_SWITCH_KEY)
_SIGNAL_ROUTE_SPECS = (
    ("z", SOURCE_KEY),
    ("x", X_KEY),
    ("trigger", TRIGGER_KEY),
    ("temp", TEMP_SWITCH_KEY),
)


def installed_lockin_options(capabilities: dict | None) -> set[str]:
    """规范化锁相选件名称集合（移除 MF- 前缀）。"""
    options = (capabilities or {}).get("options") or ()
    return {str(item).strip().upper().removeprefix("MF-") for item in options if str(item).strip()}


def _require_mfli_capabilities(capabilities: dict | None, *, require_mod_option: bool) -> None:
    """确认 MFLI 具备四通道（MF-MD）及实机相干调制器（MF-MOD）选件。"""
    installed = installed_lockin_options(capabilities)
    if "MD" not in installed:
        raise ValueError("MFLI 缺少 MF-MD 选件，无法同时分配 MOD 载波/调制振荡器与 Demod 4 独立读出")
    if require_mod_option and "MOD" not in installed:
        raise ValueError("MFLI 缺少必需的 MF-MOD 选件，无法由 Signal Output 1 输出相干载波")


def _require_finite_limit_bounds(limits: dict, key: str) -> None:
    rule = limits.get(key, {})
    if any(type(rule.get(bound)) not in (int, float) or not math.isfinite(rule[bound])
           for bound in ("min", "max")):
        raise ValueError(f"{key} 缺少有限安全上下限")


def _require_5khz_carrier(params: RfPhaseResponseParams) -> None:
    """本方案只支持 5 kHz 载波：Z 双脉冲周期、共同触发与 MF-MOD 载波共用该频率。"""
    if not math.isclose(params.y_rf_freq_hz, 5000.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("本方案只支持 Y 向 5 kHz 载波")


def preflight(params: RfPhaseResponseParams, root):
    """连接前预检：参数、映射、限值、MFLI 载波包络与 DG 直驱电压/估算电流；不连硬件。"""
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    _require_5khz_carrier(params)
    if params.demod0_idx != READOUT_DEMOD_IDX:
        raise ValueError("启用 MF-MOD 后 Demod 1–3 被调制器占用，原子读出须使用 Demod 4（索引 3）")
    mapping = load_mapping(root)
    for key in run_format.REQUIRED_MAPPING_KEYS:
        if key not in mapping:
            raise ValueError(f"缺少设备映射：{key}")
    validate_mapping_usage(run_format.REQUIRED_MAPPING_KEYS, root)
    for key, (kind, models) in _EXPECTED.items():
        device = mapping.get(key, {})
        if device.get("instrument") != kind or str(device.get("model", "")).upper() not in models:
            raise ValueError(f"{key} 必须映射到 {'/'.join(sorted(models))}")
    endpoints = {(mapping[key].get("resource"), mapping[key].get("channel")) for key in _DG_ROLES}
    if len(endpoints) != len(_DG_ROLES):
        raise ValueError("独立激励与温控量不能共用同一信号源端点")
    for key in (TRIGGER_KEY, SOURCE_KEY, LOCKIN_KEY):
        if str(mapping[key].get("reference_clock", "")).upper() not in {"EXT", "EXTERNAL"}:
            raise ValueError(f"{key} 须按设备库使用共同外部 10 MHz")
    if params.main_field_current_ma and str(mapping[MAIN_FIELD_KEY].get("source_function", "")).upper() \
            not in {"CURR", "CURRENT"}:
        raise ValueError("GS200 必须映射电流模式")

    lockin_cfg = mapping[LOCKIN_KEY]
    declared_caps = lockin_cfg.get("capabilities") or {}
    caps = lockin_capabilities(str(lockin_cfg.get("model", "MFLI")), declared_caps.get("options", []))
    if caps["demodulators"] < 4 or caps["oscillators"] < 3:
        raise ValueError("MFLI 必须具备至少 4 个解调器与 3 个振荡器（需要 MF-MD 选件）")
    _require_mfli_capabilities(declared_caps, require_mod_option=False)
    if acquisition_module(str(lockin_cfg.get("model", "MFLI")), params.demod0_rate_sa_s,
                          str(lockin_cfg.get("acquisition_mode", "auto"))) != "daq":
        raise ValueError("本实验需要 MFLI DAQ 的同步 X/Y 与真实仪器时间戳")
    require_lockin_compatibility(run_format.EXPERIMENT_ID, params.to_external(), mapping=mapping)

    limits = load_safety_limits(root)
    sigout_key = mfli_sigout_safety_key(mapping)
    for key in (*_DG_ROLES, sigout_key, MAIN_FIELD_KEY, CURRENT_LIMIT_KEY,
                *((TEC_KEY,) if params.set_tec_target else ())):
        _require_finite_limit_bounds(limits, key)
    select_mfli_output_range(params)
    levels = plan(params)
    for key, values in (
        (SOURCE_KEY, (0.0, levels["pulse_min_v"], levels["pulse_max_v"])),
        (X_KEY, (0.0, params.x_bias_v)),
        (sigout_key, mfli_output_levels(params)),
        (TRIGGER_KEY, (0.0, TRIGGER_OFFSET_V - TRIGGER_VPP / 2.0,
                       TRIGGER_OFFSET_V + TRIGGER_VPP / 2.0)),
        (TEMP_SWITCH_KEY, (0.0, TEMP_SWITCH_ON_V)),
        (MAIN_FIELD_KEY, (params.main_field_current_ma,)),
        (CURRENT_LIMIT_KEY, (-levels["pulse_peak_current_a"], levels["pulse_peak_current_a"])),
        (TEC_KEY, (params.target_temperature_c,) if params.set_tec_target else ()),
    ):
        for value in values:
            validate_safety_limit(key, value, limits)
    if not amplifier_enabled(params):
        # DG 直驱：幅度设定受高阻档上限约束，线圈电流按已知回路电阻估算并单独过电流限值。
        if params.z_pulse_vpp > DG_HIGH_Z_MAX_VPP:
            raise ValueError(f"无放大器时 Z_PULSE_VPP={params.z_pulse_vpp:g} Vpp 超过 DG4000 高阻档上限 "
                             f"{DG_HIGH_Z_MAX_VPP:g} Vpp")
        for value in (-levels["estimated_peak_current_ma"] * 1e-3, levels["estimated_peak_current_ma"] * 1e-3):
            validate_safety_limit(CURRENT_LIMIT_KEY, value, limits)
    if levels["arb_sample_rate_sa_s"] > ARB_SAMPLE_RATE_CHECK:
        raise ValueError("Z 任意波播放采样率超限")
    _, pulse = pulse_wave(params, params.z_pulse_width_us)
    resistor = max(params.sense_resistor_ohm, params.damping_resistor_ohm)
    power_w = levels["pulse_peak_current_a"] ** 2 * pulse["mean_square"] * resistor
    if power_w > params.resistor_rated_w / 2.0:
        raise ValueError(f"Z 链电阻平均功耗 {power_w:g} W 超过额定功率的一半")
    source = mapping[SOURCE_KEY]
    capabilities = source.get("capabilities") or {}
    if (signal_generator_max_arb_points(source) < ARB_POINTS
            or not capabilities.get("supports_arbitrary")
            or not capabilities.get("supports_infinite_burst")):
        raise ValueError("现用 Z 信号源缺少必需的任意波点数或无限 Burst 能力")
    return params, mapping


def _connect_devices(ctx):
    """连接 DG 的 Z/X/trigger/temp、MFLI、GS200 与可选 TEC；不连接 Y 通道、功放与示波器。"""
    mapping, devices, session = ctx.mapping, ctx.devices, ctx.device_session
    try:
        signals, channels = connect_signal_generator_routes(
            session, "signals", {name: (key, mapping[key]) for name, key in _SIGNAL_ROUTE_SPECS}
        )
        devices["signals"], devices["channels"] = signals, channels
        lockin_cfg = mapping[LOCKIN_KEY]
        devices["hf2"] = session.connect(
            "hf2",
            f"mfli://{lockin_cfg.get('host')}:{lockin_cfg.get('port')}/{lockin_cfg['device_id']}",
            lambda: create_lockin(lockin_cfg),
        )
        mfli = devices["hf2"]
        if str(mfli.model).upper() != str(lockin_cfg.get("model", "MFLI")).upper():
            raise ValueError("已连接锁相型号与设备库不一致，禁止启动输出")
        _require_mfli_capabilities(mfli.capabilities, require_mod_option=True)
        actual_mapping = mapping | {LOCKIN_KEY: lockin_cfg | {"model": mfli.model,
                                                             "capabilities": mfli.capabilities}}
        require_lockin_compatibility(run_format.EXPERIMENT_ID, ctx.params.to_external(),
                                     mapping=actual_mapping)
        initialize_lockin_inputs(mfli)
        main_field = mapping[MAIN_FIELD_KEY]
        devices["gs200"] = session.connect(
            "gs200", str(main_field["resource"]), lambda: GS200Instrument(main_field["resource"])
        )
        tec_cfg = mapping[TEC_KEY]
        devices["tec"] = session.connect_optional(
            "tec", tec_cfg["resource"], lambda: TECInstrument(tec_cfg["resource"]), device_label="TEC"
        )
        devices["optional_connection_errors"] = session.optional_connection_errors()
    except BaseException:
        session.cleanup_connection_failure()
        raise


def clocks(ctx):
    """DG4000/DG900 与 MFLI 的公共 10 MHz 参考时钟严格回读。"""
    return synchronize_connected_clocks(
        {"signals": ctx.devices["signals"], "hf2": ctx.devices["hf2"]},
        ctx.mapping,
        {"signals": SOURCE_KEY, "hf2": LOCKIN_KEY},
        settle_wait=sleep,
    )


def connect(ctx):
    """连接全部设备；失败时断开已连接设备，不留半开会话。"""
    return _connect_devices(ctx)


def connect_and_synchronize(ctx):
    """首次调用时连接设备；随后严格回读公共参考时钟。"""
    if not ctx.device_session.unique_devices():
        _connect_devices(ctx)
    ctx.state["clock_sources"] = clocks(ctx)
    ctx.state["optional_connection_errors"] = ctx.device_session.optional_connection_errors()


def _route(ctx, name):
    route = ctx.devices["signals"].routes[ctx.devices["channels"][name]]
    return route.device, route.actual_channel


def _dg_state(device, command):
    """仅接受明确的 DG4000 开关回读，不把未知响应解释为 OFF。"""
    value = device.query(command).strip().upper()
    if value not in {"0", "1", "OFF", "ON"}:
        raise SafetyAbort(f"DG4000 {command} 状态不可判定：{value!r}")
    return value in {"1", "ON"}


def dg_output(device, channel):
    return _dg_state(device, f":OUTPut{channel}:STATe?")


def _verify_generators(ctx):
    """确认所用 DG 通道电平单位、极性与同机通道独立性，再写激励。"""
    seen = set()
    for role in ("x", "z", "trigger"):
        source, channel = _route(ctx, role)
        if id(source) not in seen:
            coupling = source.query(":COUPling:STATe?").split(",")
            if len(coupling) != 3 or any(part.rsplit(":", 1)[-1].strip().upper() not in {"OFF", "0"}
                                         for part in coupling):
                raise SafetyAbort(f"{role} 所在 DG4000 未确认关闭同机通道耦合")
            seen.add(id(source))
        if (normalize_clock_source(source.get_ref_clock_source()) != "EXT"
                or source.get_voltage_unit(channel=channel).upper() != "VPP"
                or source.query(f":OUTPut{channel}:POLarity?").upper() not in {"NORM", "NORMAL"}
                or (role == "z" and not math.isclose(source.get_burst_delay(channel=channel), 0.,
                                                     abs_tol=1e-9))
                or (role == "trigger"
                    and normalize_output_load(source.get_output_load(channel=channel)) != "INF")):
            raise SafetyAbort(f"{role} 的时钟、负载、单位、极性或 Burst 延迟未通过回读")
        source.raise_for_errors()


def _write_temperature_once(device, channel, enabled, volts):
    set_temperature_switch(device, enabled, channel=channel)
    if callable(getattr(device, "wait_for_operation_complete", None)):
        device.wait_for_operation_complete()
    readback = device.get_dc_voltage(channel=channel)
    if (not device.get_output(channel=channel) or device.get_shape(channel=channel).upper() != "DC"
            or not math.isclose(readback, volts, abs_tol=1e-4)):
        raise SafetyAbort(f"温控开关未回读为 DC {volts:g} V + 输出 ON")
    return dict(output=True, dc_setting_v=readback)


def write_temperature(ctx, enabled):
    """写 ``Temp_Switch`` 5 V ON / 0 V OFF 并回读；DG900 VXI-11 瞬时超时时重连重试。

    供共享整周扫描在本实验的回调位置逐点调用；失败抛 ``SafetyAbort``。
    """
    device, channel = _route(ctx, "temp")
    volts = TEMP_SWITCH_ON_V if enabled else 0.0
    validate_safety_limit(TEMP_SWITCH_KEY, volts, load_safety_limits(ctx.root))
    can_reconnect = callable(getattr(device, "disconnect", None)) and callable(
        getattr(device, "connect", None))
    last_exc: VisaIOError | None = None
    for attempt in range(3):
        if attempt > 0:
            print(f"[警告] Temp_Switch CH{channel} VISA 通信超时/异常，断开重连后重试（{attempt}/2）："
                  f"{last_exc}", flush=True)
            try:
                device.disconnect()
            except Exception:
                pass
            time.sleep(0.3)
        try:
            if attempt > 0:
                device.connect()
            return _write_temperature_once(device, channel, enabled, volts)
        except VisaIOError as exc:
            last_exc = exc
            if attempt == 2 or not can_reconnect:
                raise SafetyAbort(f"Temp_Switch CH{channel} 通信三次失败，温控开关状态未确认：{exc}") from exc
    raise SafetyAbort("Temp_Switch 写入未完成")


def _dc_level(device, channel, value):
    """写 DC 电平；DG4162 在 DC 波形下会忽略 OFFSET/APPLy 写入，先切正弦再写。"""
    if device.get_shape(channel=channel).strip().upper() == "DC":
        device.set_shape("SINusoid", channel=channel)
    device.set_offset(float(value), channel=channel)
    device.set_shape("DC", channel=channel)
    shape = device.get_shape(channel=channel).strip().upper()
    actual = device.get_dc_voltage(channel=channel)
    if shape != "DC" or not math.isclose(actual, float(value), abs_tol=1e-4):
        raise SafetyAbort(f"DC 电平 {value:g} V 未生效：波形 {shape}、回读 {actual:g} V")


def configure_readout_demodulator(ctx):
    """配置并严格回读 MFLI Signal Input 1 / Osc 2 与独立读出解调器 Demod 4。"""
    p = ctx.params
    mfli = ctx.devices["hf2"]
    demod.configure_signal_input(mfli, SignalInputConfig(input_index=0, range=p.demod0_signal_range_v,
                                                         diff=False))
    demod.configure_oscillator(mfli, OscillatorConfig(osc_index=MOD_CARRIER_OSC_IDX,
                                                      frequency=p.y_rf_freq_hz))
    config = DemodulatorConfig(
        demod_index=p.demod0_idx,
        rate=p.demod0_rate_sa_s,
        input_channel=0,
        osc_select=MOD_CARRIER_OSC_IDX,
        time_constant=p.demod0_tc_s,
        order=p.demod0_order,
        phase=0.0,
    )
    actual_rate = float(demod.configure_demodulator(mfli, config, for_acquisition=True))
    if not math.isfinite(actual_rate) or actual_rate <= 0:
        raise RuntimeError(f"MFLI Demod{p.demod0_idx + 1} 实际采样率无效")
    ctx.state["actual_rates"]["demod0_Sa_s"] = actual_rate
    readback_phase = float(mfli.get_double(f"{mfli.demod_path(p.demod0_idx)}/phaseshift"))
    if phase_drift_deg(readback_phase, 0.0) > prep.PHASE_READBACK_TOLERANCE_DEG:
        raise RuntimeError(f"MFLI Demod{p.demod0_idx + 1} 解调相位回读 {readback_phase:g}° 与初始 0° 不符")
    record = dict(
        model=mfli.model,
        device_id=mfli.device_id,
        demod_idx=p.demod0_idx,
        osc_select=MOD_CARRIER_OSC_IDX,
        requested_rate_Sa_s=p.demod0_rate_sa_s,
        actual_rate_Sa_s=actual_rate,
        time_constant_s=p.demod0_tc_s,
        order=p.demod0_order,
        signal_range_v=p.demod0_signal_range_v,
        rf_frequency_Hz=p.y_rf_freq_hz,
        readback={node: float(mfli.get_double(f"{mfli.demod_path(p.demod0_idx)}/{node}"))
                  for node in ("rate", "timeconstant", "phaseshift")},
        phase_deg=readback_phase,
        phase_requested_deg=0.0,
        capabilities=dict(mfli.capabilities),
    )
    ctx.state["lockin"] = record
    ctx.outputs["MFLI"] = (
        f"Demod{p.demod0_idx + 1}（Osc{MOD_CARRIER_OSC_IDX + 1} 参考）请求 {p.demod0_rate_sa_s:g} Sa/s、"
        f"TC {p.demod0_tc_s:g} s、参考 {p.y_rf_freq_hz:g} Hz、初始相位 {readback_phase:g}°"
        f"（传输率回读 {actual_rate:g} Sa/s）"
    )
    return record


def _prepare_temperature(ctx):
    write_temperature(ctx, True)
    ctx.outputs["温控"] = "ON（5 V，已回读）"
    if not ctx.params.set_tec_target:
        return
    channel = int(ctx.mapping[TEC_KEY].get("channel", 1))
    try:
        status = configure_temperature_control(ctx.devices["tec"], ctx.params.target_temperature_c,
                                               channel=channel)
    except HardwareAccessDenied:
        raise
    except Exception as exc:
        ctx.state["temperature"]["setting_error"] = str(exc)
    else:
        ctx.state["temperature"] = asdict(status)


def prepare_measurement(ctx):
    """A02：连接后配链路、X 偏置、Demod 4、共同触发与 MF-MOD 载波，最后设主磁场与温控。"""
    _verify_generators(ctx)
    check_cancelled()
    ctx.active = True
    configure_chain(ctx)
    configure_x_bias(ctx)
    configure_readout_demodulator(ctx)
    configure_trigger(ctx)
    carrier = shared_rf_phase.Carrier(ctx)
    carrier.begin_carrier()
    check_cancelled()
    configure_main_field(ctx, ctx.params.main_field_current_ma)
    _prepare_temperature(ctx)
    return carrier


def arm_z(ctx):
    """按固定脉宽上传双脉冲任意波并 arm 无限 burst；DG 直驱，无 1200B 动作。"""
    p = ctx.params
    source, channel = _route(ctx, "z")
    z = plan(p)
    limits = load_safety_limits(ctx.root)
    for value in (0.0, z["pulse_min_v"], z["pulse_max_v"]):
        validate_safety_limit(SOURCE_KEY, value, limits)
    wave, record = pulse_wave(p, p.z_pulse_width_us)
    source.set_output(False, channel=channel)
    if dg_output(source, channel):
        raise SafetyAbort("Z 驱动未回读为 OFF，拒绝上传波形")
    source.set_burst_state(False, channel=channel)
    source.set_mod_state(False, channel=channel)
    source.setup_arbitrary(wave, p.y_rf_freq_hz, p.pulse_vpp, 0.0, output=False, channel=channel)
    source.set_burst_mode("INFinity", channel=channel)
    source.set_burst_ncycles(BURST_NCYCLES, channel=channel)
    source.set_burst_trigger_source("EXTernal", channel=channel)
    source.set_burst_trigger_slope("POSitive", channel=channel)
    source.set_burst_phase(0.0, channel=channel)
    source.set_burst_state(True, channel=channel)
    source.wait_for_operation_complete()
    source.raise_for_errors()
    actual = dict(
        shape=source.get_shape(channel=channel),
        load=normalize_output_load(source.get_output_load(channel=channel)),
        points=source.get_arb_points(channel=channel),
        vpp=source.get_amplitude(channel=channel),
        frequency_hz=source.get_frequency(channel=channel),
        offset_v=source.get_offset(channel=channel),
        burst_mode=source.get_burst_mode(channel=channel),
        burst=source.get_burst_state(channel=channel),
        modulation=source.get_mod_state(channel=channel),
        burst_phase_deg=source.get_burst_phase(channel=channel),
        trigger=source.get_burst_trigger_source(channel=channel),
        slope=source.get_burst_trigger_slope(channel=channel),
    )
    if actual["load"] != "INF":
        actual["load"] = float(actual["load"])
    # DG 直驱固定用高阻档：写入幅度即开路电动势，电流由回路电阻估算。
    if (actual["shape"].upper() not in {"USER", "ARB", "ARBITRARY", "CUSTOM"} or actual["load"] != "INF"
            or actual["points"] != wave.size
            or actual["burst_mode"].upper() not in {"INF", "INFINITY"}
            or not actual["burst"] or actual["modulation"] or dg_output(source, channel)
            or actual["trigger"].upper() not in {"EXT", "EXTERNAL"}
            or actual["slope"].upper() not in {"POS", "POSITIVE"}
            or not math.isclose(actual["vpp"], p.pulse_vpp, abs_tol=1e-3)
            or not math.isclose(actual["frequency_hz"], p.y_rf_freq_hz, rel_tol=1e-6)
            or not math.isclose(actual["offset_v"], 0.0, abs_tol=1e-4)
            or not math.isclose(actual["burst_phase_deg"], 0.0, abs_tol=0.02)):
        raise SafetyAbort(f"Z 双脉冲写入回读不符，拒绝打开输出：{actual}")
    record["generator_readback"] = actual
    source.set_output(True, channel=channel)
    if not dg_output(source, channel):
        raise SafetyAbort("Z 无法确认信号源输出 ON，拒绝开始采集")
    check_cancelled()
    ctx.state.setdefault("z_waveform", dict(
        tail_comp=bool(p.z_tail_comp_enabled), points=ARB_POINTS,
        **(dict(s_f=p.z_tail_s_f, tau_e_us=p.z_tail_tau_e_us, tau_c_us=p.z_tail_tau_c_us)
           if p.z_tail_comp_enabled else {}),
        widths={}))["widths"][f"{p.z_pulse_width_us:g}"] = record
    ctx.outputs["Z 通道"] = (
        f"外触发 ±π 双脉冲连续输出（DG 直驱，无放大器），周期 {p.pulse_period_us:g} µs、"
        f"宽 {p.z_pulse_width_us:g} µs、幅度 {p.pulse_vpp:g} Vpp、"
        f"估算峰值电流 {z['estimated_peak_current_ma']:.1f} mA、"
        f"外触发 {p.y_rf_freq_hz:g} Hz（电流按已知回路电阻估算，非直接测量）"
    )


def _off_and_zero(ctx, role, key):
    """仅在回读 OFF 时归零；命令失败也再次关输出并分别保存回读。"""
    device, channel = _route(ctx, role)
    label = {"x": "X 偏置", "z": "Z 通道"}[role]
    record = ctx.state.setdefault("shutdown_readbacks", {})[key] = {"channel": channel}
    errors = []

    def shape():
        return device.get_shape(channel=channel).strip().upper()

    try:
        device.set_output(False, channel=channel)
        if dg_output(device, channel):
            raise SafetyAbort("未回读为 OFF，禁止改变波形")
        device.set_burst_state(False, channel=channel)
        device.set_mod_state(False, channel=channel)
        if role == "z":
            device.set_sync_state(False, channel=channel)
        validate_safety_limit(key, 0.0, load_safety_limits(ctx.root))
        # 即使已处于 DC，也要先离开 DC 才能改写电平；否则 DG4162 忽略写入、旧偏置残留。
        _dc_level(device, channel, 0.0)
        record["dc_commanded_v"] = 0.0
    except Exception as exc:
        errors.append(f"归零命令: {exc}")
    finally:
        try:
            device.set_output(False, channel=channel)
            device.wait_for_operation_complete()
        except Exception as exc:
            errors.append(f"最终关闭输出或等待完成: {exc}")

    checks = {
        "output": (lambda: dg_output(device, channel), lambda value: value is False),
        "shape": (shape, lambda value: value == "DC"),
        "dc_setting_v": (lambda: device.get_dc_voltage(channel=channel),
                         lambda value: math.isclose(value, 0.0, abs_tol=1e-4)),
        "burst": (lambda: _dg_state(device, f":SOURce{channel}:BURSt:STATe?"), lambda value: value is False),
        "modulation": (lambda: _dg_state(device, f":SOURce{channel}:MOD:STATe?"), lambda value: value is False),
    }
    if role == "z":
        checks["sync"] = (lambda: _dg_state(device, f":OUTPut{channel}:SYNC:STATe?"),
                          lambda value: value is False)
    for field, (query, expected) in checks.items():
        try:
            value = record[field] = query()
            if not expected(value):
                errors.append(f"{field} 回读不符: {value!r}")
        except Exception as exc:
            errors.append(f"{field} 回读异常: {exc}")
    try:
        device.raise_for_errors()
    except Exception as exc:
        errors.append(f"SCPI 错误队列: {exc}")
    if errors:
        record["errors"] = errors
        output = record.get("output")
        ctx.outputs[label] = ("OFF（已回读），其余收尾未确认" if output is False else
                              "回读为 ON，需现场检查" if output is True else "输出状态未知，需现场检查")
        raise SafetyAbort(f"{key} 收尾未通过: {'；'.join(errors)}")
    ctx.outputs[label] = "OFF，仪器报告 DC 0 V"


def _stop_mfli_rf(ctx):
    """安全关闭并清零 MFLI Signal Output 1 与 MF-MOD，全部节点逐项回读验证。"""
    lockin = ctx.devices["hf2"]
    dev = lockin.device_id
    sigout_key = mfli_sigout_safety_key(ctx.mapping)
    record = ctx.state.setdefault("shutdown_readbacks", {})[sigout_key] = {"channel": 0}
    errors = []

    try:
        lockin.set_int(f"/{dev}/sigouts/0/on", 0)
        lockin.sync()
        if int(lockin.get_int(f"/{dev}/sigouts/0/on")) != 0:
            raise SafetyAbort("MFLI Out1 未回读为 OFF，禁止归零")
        lockin.set_double(f"/{dev}/mods/0/carrier/amplitude", 0.0)
        lockin.set_int(f"/{dev}/mods/0/sidebands/0/enable", 0)
        lockin.set_int(f"/{dev}/mods/0/sidebands/1/enable", 0)
        lockin.set_int(f"/{dev}/mods/0/output", 0)
        lockin.set_int(f"/{dev}/mods/0/enable", 0)
        validate_safety_limit(sigout_key, 0.0, load_safety_limits(ctx.root))
        lockin.set_double(f"/{dev}/sigouts/0/offset", 0.0)
        record["offset_commanded_v"] = 0.0
    except Exception as exc:
        errors.append(f"Y 射频收尾命令: {exc}")
    finally:
        for path, val in ((f"/{dev}/sigouts/0/on", 0), (f"/{dev}/mods/0/enable", 0)):
            try:
                lockin.set_int(path, val)
            except Exception as exc:
                errors.append(f"最终关闭 {path}: {exc}")
        try:
            lockin.sync()
        except Exception as exc:
            errors.append(f"MFLI sync 异常: {exc}")

    checks = {
        "output": (lambda: int(lockin.get_int(f"/{dev}/sigouts/0/on")) == 1, lambda v: v is False),
        "mod_enable": (lambda: int(lockin.get_int(f"/{dev}/mods/0/enable")), lambda v: v == 0),
        "mod_output": (lambda: int(lockin.get_int(f"/{dev}/mods/0/output")), lambda v: v == 0),
        "sideband0_enable": (lambda: int(lockin.get_int(f"/{dev}/mods/0/sidebands/0/enable")),
                             lambda v: v == 0),
        "sideband1_enable": (lambda: int(lockin.get_int(f"/{dev}/mods/0/sidebands/1/enable")),
                             lambda v: v == 0),
        "offset_v": (lambda: float(lockin.get_double(f"/{dev}/sigouts/0/offset")),
                     lambda v: math.isclose(v, 0.0, abs_tol=1e-4)),
        "carrier_amplitude_vpk": (
            lambda: float(lockin.get_double(f"/{dev}/mods/0/carrier/amplitude")),
            lambda v: math.isclose(v, 0.0, abs_tol=1e-5),
        ),
    }
    for field, (query, expected) in checks.items():
        try:
            value = record[field] = query()
            if not expected(value):
                errors.append(f"{field} 回读不符: {value!r}")
        except Exception as exc:
            errors.append(f"{field} 回读异常: {exc}")

    if errors:
        record["errors"] = errors
        output = record.get("output")
        ctx.outputs["Y 射频"] = (
            "OFF（已回读），其余收尾未确认" if output is False else
            "回读为 ON，需现场检查" if output is True else "输出状态未知，需现场检查"
        )
        raise SafetyAbort(f"{sigout_key} 收尾未通过: {'；'.join(errors)}")
    ctx.outputs["Y 射频"] = "OFF，MFLI Out1 偏置与载波幅值已归零"


def park(ctx):
    """以公共安全编排逐项恢复；单通道失败不跳过其他通道、温控或主场。"""
    if not ctx.active:
        return []
    devices = ctx.devices
    actions = [
        ShutdownAction("Z_magnetic_field 关闭并归零", lambda: _off_and_zero(ctx, "z", SOURCE_KEY)),
        ShutdownAction("MFLI Y 射频关闭并归零", lambda: _stop_mfli_rf(ctx)),
        ShutdownAction("X_magnetic_field 关闭并归零", lambda: _off_and_zero(ctx, "x", X_KEY)),
    ]

    def restore_temperature():
        ctx.state.setdefault("shutdown_readbacks", {})[TEMP_SWITCH_KEY] = write_temperature(ctx, True)
        ctx.outputs["温控"] = "ON（5 V，已回读）"

    def restore_main_field():
        source, before = devices["gs200"], ctx.main_field_state
        prep.restore_main_field_state(source, before)
        actual = prep.snapshot_main_field_state(source)
        if (actual.output_on != before.output_on
                or not math.isclose(actual.current_a, before.current_a, abs_tol=1e-6)
                or not math.isclose(actual.current_range_a, before.current_range_a, rel_tol=1e-6)
                or not math.isclose(actual.current_limit_a, before.current_limit_a, rel_tol=1e-6)):
            raise SafetyAbort("GS200 主磁场未回读为运行前状态")
        ctx.state.setdefault("shutdown_readbacks", {})[MAIN_FIELD_KEY] = asdict(actual)
        ctx.outputs["主磁场"] = "已回读恢复原状态"

    def stop_mfli():
        lockin = devices["hf2"]
        demod.set_demod_enable(lockin, ctx.params.demod0_idx, False)
        if lockin.get_int(lockin.demod_path(ctx.params.demod0_idx) + "/enable") != 0:
            raise SafetyAbort("MFLI 未回读为停止解调传输")
        ctx.outputs["MFLI"] = "已回读停止解调传输"

    for key in ("Z 通道", "Y 射频", "X 偏置", "温控", "MFLI", "主磁场"):
        ctx.outputs[key] = "收尾待回读"
    actions.extend((ShutdownAction("恢复温控并回读", restore_temperature),
                    ShutdownAction("停 MFLI 解调传输", stop_mfli)))
    if ctx.main_field_state is not None:
        actions.append(ShutdownAction("恢复 GS200 主磁场", restore_main_field))
    report = run_safety_shutdown(extra_actions=actions,
                                 preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, TRIGGER_KEY))
    ctx.state["safety_shutdown"] = report.to_dict()
    if report.completed:
        ctx.active = False
        ctx.main_field_state = None
    return list(report.errors)
