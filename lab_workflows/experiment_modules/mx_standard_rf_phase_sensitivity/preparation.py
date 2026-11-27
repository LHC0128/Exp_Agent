"""标准 Mx 的设备预检、连接、初始条件、载波控制与安全收尾。

链路：GS200 常量主场 + MFLI Signal Output 1 的 MF-MOD 连续载波（只开载波，PM 边带全程
关闭）+ Demod 4 / Osc 4 静态读出；Z 双脉冲链（1200B 功放与 Z 信号源）保持关闭并回读。
本模块只调用 lab_workflows.steps 与 z_chain_common 的共享步骤，不改写其他实验的实现。
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict

from gs200 import GS200Instrument
from hardware_gate import HardwareAccessDenied
from lockin_amplifier import DemodulatorConfig, OscillatorConfig, SignalInputConfig, demod, lockin_capabilities
from pyvisa.errors import VisaIOError
from tec_controller import TECInstrument

from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...devices import create_lockin
from ...experiment_runtime import check_cancelled
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...steps import (
    STANDARD_PRESERVED_OUTPUTS, DGChannelShutdown, ShutdownAction, connect_signal_generator_routes,
    configure_temperature_control, initialize_lockin_inputs, restore_main_field_state,
    run_safety_shutdown, set_temperature_switch, snapshot_main_field_state,
    synchronize_connected_clocks,
)
from ...steps.coil_drive_chain import SafetyAbort, set_amp_output
from ..z_chain_common import prep
from ..z_chain_common.keys import (
    LOCKIN_KEY, MAIN_FIELD_KEY, SOURCE_KEY, TEC_KEY, TEMP_SWITCH_KEY, TRIGGER_KEY, X_KEY,
)
from ..z_chain_common.params import amplifier_enabled
from ..z_chain_common.rf_phase import mfli_output_levels, mfli_sigout_safety_key, select_mfli_output_range
from . import run_format
from .models import MOD_CARRIER_OSC_IDX, MOD_SIDEBAND_OSC_IDXS, READOUT_DEMOD_IDX, READOUT_OSC_IDX

# 映射键必须落在这些设备类型上；型号取自设备库的实际配置。
_EXPECTED = {
    SOURCE_KEY: ("signal_generator", {"DG4000", "DG900"}),
    X_KEY: ("signal_generator", {"DG4000", "DG900"}),
    TRIGGER_KEY: ("signal_generator", {"DG4000", "DG900"}),
    TEMP_SWITCH_KEY: ("signal_generator", {"DG4000", "DG900"}),
    LOCKIN_KEY: ("lockin_amplifier", {"MFLI"}),
    MAIN_FIELD_KEY: ("gs200", {"GS200"}),
    TEC_KEY: ("tec_controller", {"TEC103"}),
}
_DG_ROLES = (SOURCE_KEY, X_KEY, TRIGGER_KEY, TEMP_SWITCH_KEY)
_DG_CHANNEL_NAMES = (("z", SOURCE_KEY), ("x", X_KEY), ("trigger", TRIGGER_KEY), ("temp", TEMP_SWITCH_KEY))
# 解调相位回读容差（度）：设备按 1e-6 分辨率写，超过该差别即视为设定未生效。
PHASE_READBACK_TOLERANCE_DEG = 0.5
# 温度开关（Temp_Switch）关/开电平，与共享 set_temperature_switch 的默认值一致。
TEMP_SWITCH_ON_V = 5.0


def installed_lockin_options(capabilities: dict | None) -> set[str]:
    """规范化锁相选件名称集合（移除 MF- 前缀）。"""
    options = (capabilities or {}).get("options") or ()
    return {str(item).strip().upper().removeprefix("MF-") for item in options if str(item).strip()}


def require_mfli_capabilities(capabilities: dict | None) -> None:
    """标准 Mx 需要四通道（MF-MD）与实机相干调制器（MF-MOD）：载波由 MF-MOD 产生、读出用 Demod 4。"""
    installed = installed_lockin_options(capabilities)
    if "MD" not in installed:
        raise ValueError("MFLI 缺少 MF-MD 选件，无法同时分配 MF-MOD 载波振荡器与 Demod 4 独立读出")
    if "MOD" not in installed:
        raise ValueError("MFLI 缺少必需的 MF-MOD 选件，无法由 Signal Output 1 输出相干载波")


def _require_finite_limit_bounds(limits: dict, key: str) -> None:
    rule = limits.get(key, {})
    if any(type(rule.get(bound)) not in (int, float) or not math.isfinite(rule[bound]) for bound in ("min", "max")):
        raise ValueError(f"{key} 缺少有限安全上下限")


def preflight(params, root):
    """无硬件预检：参数、映射、设备类型、MFLI 选件与全部输出电平。"""
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    if params.demod_idx != READOUT_DEMOD_IDX:
        raise ValueError("标准 Mx 载波占用 MF-MOD，原子读出须使用 Demod 4（索引 3）")
    mapping = load_mapping(root)
    validate_mapping_usage(run_format.REQUIRED_MAPPING_KEYS, root)
    for key, (kind, models) in _EXPECTED.items():
        device = mapping.get(key, {})
        if device.get("instrument") != kind or str(device.get("model", "")).upper() not in models:
            raise ValueError(f"{key} 必须映射到 {'/'.join(sorted(models))}")
    endpoints = {(mapping[key].get("resource"), mapping[key].get("channel")) for key in _DG_ROLES}
    if len(endpoints) != len(_DG_ROLES):
        raise ValueError("独立激励与温控量不能共用同一信号源端点")
    for key in (SOURCE_KEY, TRIGGER_KEY, LOCKIN_KEY):
        if str(mapping[key].get("reference_clock", "")).upper() not in {"EXT", "EXTERNAL"}:
            raise ValueError(f"{key} 须按设备库使用共同外部 10 MHz")
    if str(mapping[MAIN_FIELD_KEY].get("source_function", "")).upper() not in {"CURR", "CURRENT"}:
        raise ValueError("GS200 必须映射电流模式")

    lockin_cfg = mapping[LOCKIN_KEY]
    caps = lockin_capabilities(str(lockin_cfg.get("model", "MFLI")), (lockin_cfg.get("capabilities") or {}).get("options", []))
    if caps["demodulators"] < 4 or caps["oscillators"] < 4:
        raise ValueError("MFLI 必须具备至少 4 个解调器与 4 个振荡器（需要 MF-MD 选件）")

    limits = load_safety_limits(root)
    sigout_key = mfli_sigout_safety_key(mapping)
    checked = [*_DG_ROLES, sigout_key, MAIN_FIELD_KEY, *((TEC_KEY,) if params.set_tec_target else ())]
    for key in checked:
        _require_finite_limit_bounds(limits, key)
    select_mfli_output_range(params)
    for key, values in (
        (sigout_key, mfli_output_levels(params)),
        (X_KEY, (0.0, params.x_bias_v)),
        (SOURCE_KEY, (0.0,)),
        (TRIGGER_KEY, (0.0, prep.TRIGGER_OFFSET_V - prep.TRIGGER_VPP / 2.0,
                       prep.TRIGGER_OFFSET_V + prep.TRIGGER_VPP / 2.0)),
        (TEMP_SWITCH_KEY, (0.0, TEMP_SWITCH_ON_V)),
        (MAIN_FIELD_KEY, (params.main_field_current_ma,)),
        (TEC_KEY, (params.target_temperature_c,) if params.set_tec_target else ()),
    ):
        for value in values:
            try:
                validate_safety_limit(key, value, limits)
            except ValueError as exc:
                raise ValueError(f"{key} 预检未通过：{exc}") from exc
    return params, mapping


def _connect_devices(ctx):
    """连接本实验全部仪器；不连接 1200B（物理旁路）与示波器（本实验不使用）。"""
    mapping, devices, session = ctx.mapping, ctx.devices, ctx.device_session
    try:
        signals, channels = connect_signal_generator_routes(
            session, "signals", {name: (key, mapping[key]) for name, key in _DG_CHANNEL_NAMES})
        devices["signals"], devices["channels"] = signals, channels
        lockin_cfg = mapping[LOCKIN_KEY]
        devices["hf2"] = session.connect(
            "hf2", f"mfli://{lockin_cfg.get('host')}:{lockin_cfg.get('port')}/{lockin_cfg['device_id']}",
            lambda: create_lockin(lockin_cfg))
        mfli = devices["hf2"]
        if str(mfli.model).upper() != str(lockin_cfg.get("model", "MFLI")).upper():
            raise ValueError("已连接锁相型号与设备库不一致，禁止启动输出")
        require_mfli_capabilities(mfli.capabilities)
        actual_mapping = mapping | {LOCKIN_KEY: lockin_cfg | {"model": mfli.model, "capabilities": mfli.capabilities}}
        require_lockin_compatibility(run_format.EXPERIMENT_ID, ctx.params.to_external(), mapping=actual_mapping)
        initialize_lockin_inputs(mfli)
        main_field = mapping[MAIN_FIELD_KEY]
        devices["gs200"] = session.connect(
            "gs200", str(main_field["resource"]), lambda: GS200Instrument(main_field["resource"]))
        tec_cfg = mapping[TEC_KEY]
        devices["tec"] = session.connect_optional(
            "tec", tec_cfg["resource"], lambda: TECInstrument(tec_cfg["resource"]), device_label="TEC")
        devices["optional_connection_errors"] = session.optional_connection_errors()
    except BaseException:
        session.cleanup_connection_failure()
        raise


def clocks(ctx):
    """DG4000 / DG900 与 MFLI 的公共 10 MHz 参考时钟严格回读。"""
    return synchronize_connected_clocks(
        {"signals": ctx.devices["signals"], "hf2": ctx.devices["hf2"]}, ctx.mapping,
        {"signals": SOURCE_KEY, "hf2": LOCKIN_KEY}, settle_wait=prep.sleep)


def connect_and_synchronize(ctx):
    """A02 的第一步：连接（已连接则复用）并写入时钟同步回读结果。"""
    if not ctx.device_session.unique_devices():
        _connect_devices(ctx)
    ctx.state["clock_sources"] = clocks(ctx)
    ctx.state["optional_connection_errors"] = ctx.device_session.optional_connection_errors()


def _route(ctx, name):
    """逻辑通道到物理信号源与真实通道号；代理的 query 不转发 channel，读回必须用真实设备。"""
    route = ctx.devices["signals"].routes[ctx.devices["channels"][name]]
    return route.device, route.actual_channel


def _dg_state(device, command):
    """只接受明确的开关回读，不把未知响应解释为 OFF。"""
    value = device.query(command).strip().upper()
    if value not in {"0", "1", "OFF", "ON"}:
        raise SafetyAbort(f"信号源 {command} 状态不可判定：{value!r}")
    return value in {"1", "ON"}


def dg_output(device, channel):
    return _dg_state(device, f":OUTPut{channel}:STATe?")


def wait(seconds: float) -> None:
    """可取消的整段等待：切成 0.1 s 片段，收到取消立即抛出。"""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"等待时长必须是有限非负秒数：{seconds!r}")
    deadline = time.monotonic() + float(seconds)
    while True:
        check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def _write_temperature_once(device, channel, enabled, volts):
    set_temperature_switch(device, enabled, channel=channel)
    if callable(getattr(device, "wait_for_operation_complete", None)):
        device.wait_for_operation_complete()
    readback = device.get_dc_voltage(channel=channel)
    if (not device.get_output(channel=channel) or device.get_shape(channel=channel).upper() != "DC"
            or not math.isclose(readback, volts, abs_tol=1e-4)):
        raise SafetyAbort(f"温控开关未回读为 DC {volts:g} V + 输出 ON")
    return dict(output=True, dc_setting_v=readback)


def write_temperature(ctx, enabled: bool) -> dict:
    """关 0 V ON、开 5 V ON；写入与回读通过后才进入稳定等待或写盘。DG900 瞬时超时重连重试。"""
    device, channel = _route(ctx, "temp")
    volts = TEMP_SWITCH_ON_V if enabled else 0.0
    validate_safety_limit(TEMP_SWITCH_KEY, volts, load_safety_limits(ctx.root))
    can_reconnect = callable(getattr(device, "disconnect", None)) and callable(getattr(device, "connect", None))
    last_exc: Exception | None = None
    for attempt in range(3):
        if attempt > 0:
            print(f"[警告] Temp_Switch CH{channel} 通信超时，断开重连后重试（{attempt}/2）：{last_exc}", flush=True)
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
                raise
    raise SafetyAbort(f"温控开关写入失败：{last_exc}")


def configure_readout_demodulator(
    ctx, *, rate_sa_s: float, time_constant_s: float, phase_deg: float, state_key: str,
    actual_rate_key: str, configure_input: bool, stage_tag: str,
):
    """配置并严格回读 MFLI Signal Input 1 / Osc 4 与 Demod 4 解调器（窄带或宽带档）。"""
    p = ctx.params
    mfli = ctx.devices["hf2"]
    if configure_input:
        demod.configure_signal_input(mfli, SignalInputConfig(input_index=0, range=p.demod_signal_range_v, diff=False))
        demod.configure_oscillator(mfli, OscillatorConfig(osc_index=READOUT_OSC_IDX, frequency=p.y_rf_freq_hz))
    config = DemodulatorConfig(demod_index=p.demod_idx, rate=rate_sa_s, input_channel=0,
                               osc_select=READOUT_OSC_IDX, time_constant=time_constant_s, order=p.demod_order,
                               phase=phase_deg)
    actual_rate = float(demod.configure_demodulator(mfli, config, for_acquisition=True))
    if not math.isfinite(actual_rate) or actual_rate <= 0:
        raise RuntimeError("MFLI Demod 4 实际采样率无效")
    ctx.state["actual_rates"][actual_rate_key] = actual_rate
    readback_phase = float(mfli.get_double(f"{mfli.demod_path(p.demod_idx)}/phaseshift"))
    drift = prep.phase_drift_deg(readback_phase, phase_deg)
    if drift > PHASE_READBACK_TOLERANCE_DEG:
        raise RuntimeError(f"MFLI 解调相位回读 {readback_phase:g}° 与设定 {phase_deg:g}° 相差 {drift:.3g}°")
    record = dict(model=mfli.model, device_id=mfli.device_id, demod_idx=p.demod_idx,
                  osc_select=READOUT_OSC_IDX, requested_rate_Sa_s=rate_sa_s,
                  actual_rate_Sa_s=actual_rate, time_constant_s=time_constant_s,
                  order=p.demod_order, signal_range_v=p.demod_signal_range_v,
                  rf_frequency_Hz=p.y_rf_freq_hz,
                  readback={node: float(mfli.get_double(f"{mfli.demod_path(p.demod_idx)}/{node}"))
                            for node in ("rate", "timeconstant", "phaseshift")},
                  phase_deg=readback_phase, phase_requested_deg=phase_deg,
                  capabilities=dict(mfli.capabilities))
    ctx.state[state_key] = record
    ctx.outputs["MFLI"] = (f"Demod {p.demod_idx + 1}（{stage_tag}，Osc {READOUT_OSC_IDX + 1}）"
                           f"请求 {rate_sa_s:g} Sa/s、TC {time_constant_s:g} s、参考 {p.y_rf_freq_hz:g} Hz、"
                           f"相位 {readback_phase:g}°（传输率回读 {actual_rate:g} Sa/s）")
    return record


def configure_lockin(ctx):
    """A02–A04：按参数配置窄带静态读出（输入量程、参考频率、时间常数与传输率）。"""
    p = ctx.params
    return configure_readout_demodulator(
        ctx, rate_sa_s=p.demod_rate_sa_s, time_constant_s=p.demod_tc_s, phase_deg=0.0,
        state_key="lockin", actual_rate_key="demod4_Sa_s", configure_input=True, stage_tag="A02–A04")


def configure_dynamic_lockin(ctx, *, phase_deg: float):
    """仅 A05/A06：把 Demod 4 切到高带宽档并保持当前解调相位，不重写输入与振荡器。"""
    p = ctx.params
    return configure_readout_demodulator(
        ctx, rate_sa_s=p.demod_dynamic_rate_sa_s, time_constant_s=p.demod_dynamic_tc_s,
        phase_deg=float(phase_deg), state_key="dynamic_lockin", actual_rate_key="demod4_dynamic_Sa_s",
        configure_input=False, stage_tag="A05/A06")


def configure_x_bias(ctx):
    """按 X_BIAS_V 在 X 通道写 DC 偏置并回读；0 V 时明确关输出。"""
    p = ctx.params
    prep.configure_dc_bias(ctx, "x")
    device, channel = _route(ctx, "x")
    bias = float(p.x_bias_v)
    readback = float(device.get_dc_voltage(channel=channel))
    output_on = dg_output(device, channel)
    if not math.isclose(readback, bias, abs_tol=1e-4) or output_on is not (bias != 0.0):
        raise SafetyAbort(
            f"X 偏置未回读为设定值：设定 {bias:+g} V，回读 {readback:+g} V（输出 {output_on}）；"
            "DG4162 在 DC 波形下会忽略 OFFSET 写入，需先切正弦再写")


def configure_trigger(ctx):
    """共同触发：与载波同频方波，输出与 SYNC 打开；失败不启动线圈驱动。"""
    prep.configure_trigger(ctx)
    device, channel = _route(ctx, "trigger")
    device.wait_for_operation_complete()
    device.raise_for_errors()
    readings = dict(output=dg_output(device, channel), frequency_hz=device.get_frequency(channel=channel),
                    vpp=device.get_amplitude(channel=channel), offset_v=device.get_offset(channel=channel),
                    duty_cycle=device.get_square_dcycle(channel=channel), shape=device.get_shape(channel=channel),
                    burst=device.get_burst_state(channel=channel), modulation=device.get_mod_state(channel=channel),
                    sync_enabled=device.get_sync_state(channel=channel))
    if (not readings["output"] or readings["burst"] or readings["modulation"] or not readings["sync_enabled"]
            or readings["shape"].upper() not in {"SQU", "SQUARE"}
            or not math.isclose(readings["frequency_hz"], ctx.params.y_rf_freq_hz, rel_tol=1e-6)
            or not math.isclose(readings["vpp"], prep.TRIGGER_VPP, abs_tol=1e-3)
            or not math.isclose(readings["offset_v"], prep.TRIGGER_OFFSET_V, abs_tol=1e-3)
            or not math.isclose(readings["duty_cycle"], prep.TRIGGER_DUTY, abs_tol=0.1)):
        raise SafetyAbort(f"共同触发未通过仪器回读：{readings}")
    ctx.state["common_trigger"] = readings


def disable_z_chain(ctx):
    """关闭 Z 双脉冲链：1200B 功放（若在本链路中）与 Z 信号源输出逐项回读为 OFF。"""
    validate_safety_limit(SOURCE_KEY, 0.0, load_safety_limits(ctx.root))
    device, channel = _route(ctx, "z")
    record = {}
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.set_sync_state(False, channel=channel)
    device.set_output(False, channel=channel)
    device.wait_for_operation_complete()
    record.update(z_source_output=dg_output(device, channel))
    if record["z_source_output"]:
        raise SafetyAbort("Z 信号源输出未回读为 OFF")
    if amplifier_enabled(ctx.params):
        set_amp_output(ctx, False)
        record["amplifier_output"] = bool(ctx.state.get("amp_output_on"))
        if record["amplifier_output"]:
            raise SafetyAbort("1200B 功放输出未回读为 OFF")
    else:
        record["amplifier_output"] = None
        record["amplifier_bypassed"] = True
    ctx.state["z_pulse_chain"] = record
    ctx.outputs["Z 通道"] = "脉冲链关闭（标准 Mx：不打脉冲，输出已回读 OFF）"


def configure_temperature(ctx):
    """温控开关 ON 并回读；TEC 只下发目标温度，不启停不判稳。"""
    record = write_temperature(ctx, True)
    ctx.state["temperature_switch"] = record
    ctx.outputs["温控"] = "ON（5 V DC + 输出 ON，已回读）"
    if not ctx.params.set_tec_target:
        return
    channel = int(ctx.mapping[TEC_KEY].get("channel", 1))
    try:
        status = configure_temperature_control(ctx.devices["tec"], ctx.params.target_temperature_c, channel=channel)
    except HardwareAccessDenied:
        raise
    except Exception as exc:
        ctx.state["temperature"]["setting_error"] = str(exc)
    else:
        ctx.state["temperature"] = asdict(status)


def set_main_field(ctx) -> dict:
    """GS200 电流只由标定式反算：限值校验、写入与回读都由共享步骤完成。"""
    p = ctx.params
    current_ma = p.main_field_current_ma
    validate_safety_limit(MAIN_FIELD_KEY, current_ma, load_safety_limits(ctx.root))
    prep.configure_main_field(ctx, current_ma)
    record = dict(ctx.state.get("main_field") or {})
    record["formula"] = dict(k_hz_per_ma=p.cal_k_hz_per_ma, b_hz=p.cal_b_hz, rf_frequency_hz=p.y_rf_freq_hz)
    ctx.state["main_field"] = record
    ctx.outputs["主磁场"] = (f"GS200 {current_ma:+.4f} mA（由 f={p.y_rf_freq_hz:g} Hz 与标定式反算，已回读）")
    return record


def _sigout(device_id: str, node: str) -> str:
    return f"/{device_id}/sigouts/0/{node}"


def _mod(device_id: str, node: str) -> str:
    return f"/{device_id}/mods/0/{node}"


def _carrier_safety(ctx) -> str:
    """按 Signal Output 1 安全限值校验零、偏置与当前包络（含边带开启时的 2° 峰值），返回限值键名。"""
    check_cancelled()
    limits = load_safety_limits(ctx.root)
    key = mfli_sigout_safety_key(ctx.mapping)
    pm_enabled = bool((ctx.carrier or {}).get("pm_enabled"))
    for value in (0.0, float(ctx.params.y_bias_v), *ctx.params.y_output_envelope(pm_enabled=pm_enabled)):
        validate_safety_limit(key, value, limits)
    return key


def _sync_carrier_state(ctx) -> None:
    """把载波会话状态（含 PM 边带开关与当前调相频率）同步进运行配置快照。"""
    session = ctx.carrier or {}
    state = ctx.state.setdefault("carrier", {})
    state.update(pm_sidebands_enabled=bool(session.get("pm_enabled")),
                 pm_frequency_hz=float(session.get("fm_hz", 0.0)) if session.get("pm_enabled") else None)


def carrier_verify(ctx, *, output: bool) -> dict:
    """回读载波、外时钟、量程、偏置与读出参考，并与当前 PM 边带状态逐项比对。"""
    p = ctx.params
    device = ctx.devices["hf2"]
    dev = device.device_id
    device.sync()
    got = dict(
        extclk=bool(device.get_extclk()),
        output=bool(device.get_int(_sigout(dev, "on"))),
        range_v=float(device.get_double(_sigout(dev, "range"))),
        offset_v=float(device.get_double(_sigout(dev, "offset"))),
        add_enabled=bool(device.get_int(_sigout(dev, "add"))),
        mod_enable=bool(device.get_int(_mod(dev, "enable"))),
        mode=int(device.get_int(_mod(dev, "mode"))),
        freqdevenable=int(device.get_int(_mod(dev, "freqdevenable"))),
        mod_output=int(device.get_int(_mod(dev, "output"))),
        carrier_enable=bool(device.get_int(_mod(dev, "carrier/enable"))),
        carrier_osc=int(device.get_int(_mod(dev, "carrier/oscselect"))),
        carrier_hz=float(device.get_double(f"{device.osc_path(MOD_CARRIER_OSC_IDX)}/freq")),
        carrier_vpk=float(device.get_double(_mod(dev, "carrier/amplitude"))),
        command_deg=float(device.get_double(_mod(dev, "carrier/phaseshift"))),
        mod_index_rad=float(device.get_double(_mod(dev, "index"))),
        sideband0_enable=bool(device.get_int(_mod(dev, "sidebands/0/enable"))),
        sideband1_enable=bool(device.get_int(_mod(dev, "sidebands/1/enable"))),
        sideband0_osc=int(device.get_int(_mod(dev, "sidebands/0/oscselect"))),
        sideband1_osc=int(device.get_int(_mod(dev, "sidebands/1/oscselect"))),
        sideband0_hz=float(device.get_double(f"{device.osc_path(MOD_SIDEBAND_OSC_IDXS[0])}/freq")),
        sideband1_hz=float(device.get_double(f"{device.osc_path(MOD_SIDEBAND_OSC_IDXS[1])}/freq")),
        readout_osc_hz=float(device.get_double(f"{device.osc_path(READOUT_OSC_IDX)}/freq")),
    )
    session = ctx.carrier or {}
    pm_enabled = bool(session.get("pm_enabled"))
    envelope = p.y_output_envelope(pm_enabled=pm_enabled)
    expected_deg = 0.0 if ctx.carrier is None else float(session.get("command_deg", 0.0))
    valid = (
        got["extclk"]
        and got["output"] is output
        and not got["add_enabled"]
        and got["range_v"] >= max(abs(value) for value in envelope) - 1e-9
        and math.isclose(got["offset_v"], p.y_bias_v, abs_tol=1e-4)
        and got["mod_enable"]
        and got["mode"] == 1
        and got["freqdevenable"] == 0
        and got["mod_output"] == 1
        and got["carrier_enable"]
        and got["carrier_osc"] == MOD_CARRIER_OSC_IDX
        and math.isclose(got["carrier_hz"], p.y_rf_freq_hz, rel_tol=1e-6)
        and math.isclose(got["carrier_vpk"], p.y_carrier_vpk, abs_tol=1e-5)
        and prep.phase_drift_deg(got["command_deg"], expected_deg) <= 0.02
        and got["sideband0_osc"] == MOD_SIDEBAND_OSC_IDXS[0]
        and got["sideband1_osc"] == MOD_SIDEBAND_OSC_IDXS[1]
        and got["sideband0_enable"] is pm_enabled
        and got["sideband1_enable"] is pm_enabled
        and math.isclose(got["mod_index_rad"], p.y_pm_depth_rad, abs_tol=1e-4)
        and math.isclose(got["readout_osc_hz"], p.y_rf_freq_hz, rel_tol=1e-6)
    )
    if pm_enabled:
        fm_hz = float(session.get("fm_hz", 0.0))
        valid = (valid and math.isclose(got["sideband0_hz"], fm_hz, abs_tol=1e-3)
                 and math.isclose(got["sideband1_hz"], fm_hz, abs_tol=1e-3))
    if not valid:
        raise SafetyAbort(f"MFLI 标准 Mx 载波回读不符：{got}")
    return got


def carrier_begin(ctx) -> dict:
    """写 Out1 与 MF-MOD 载波节点（不启用 PM 边带），回读确认后返回载波会话状态。"""
    p = ctx.params
    device = ctx.devices["hf2"]
    dev = device.device_id
    safety_key = _carrier_safety(ctx)
    if int(device.get_int(_sigout(dev, "add"))) != 0:
        raise SafetyAbort("MFLI Signal Output 1 开启了外部 Add 叠加，无法确保总电压安全")
    device.set_int(_sigout(dev, "on"), 0)
    device.set_int(_mod(dev, "enable"), 0)
    device.set_double(_sigout(dev, "range"), select_mfli_output_range(p))
    device.set_double(_sigout(dev, "offset"), float(p.y_bias_v))
    device.set_int(_mod(dev, "mode"), 1)
    device.set_int(_mod(dev, "freqdevenable"), 0)
    device.set_int(_mod(dev, "carrier/oscselect"), MOD_CARRIER_OSC_IDX)
    # 上下边带共用同一振荡器（真机回读固定为该索引），调频只写这一路。
    device.set_int(_mod(dev, "sidebands/0/oscselect"), MOD_SIDEBAND_OSC_IDXS[0])
    device.set_int(_mod(dev, "sidebands/1/oscselect"), MOD_SIDEBAND_OSC_IDXS[1])
    device.set_double(f"{device.osc_path(MOD_CARRIER_OSC_IDX)}/freq", float(p.y_rf_freq_hz))
    for index in MOD_SIDEBAND_OSC_IDXS:
        device.set_double(f"{device.osc_path(index)}/freq", float(p.sweep_order_hz[0]))
    device.set_double(f"{device.osc_path(READOUT_OSC_IDX)}/freq", float(p.y_rf_freq_hz))
    device.set_double(_mod(dev, "index"), float(p.y_pm_depth_rad))
    device.set_double(_mod(dev, "carrier/amplitude"), float(p.y_carrier_vpk))
    device.set_double(_mod(dev, "carrier/phaseshift"), 0.0)
    device.set_int(_mod(dev, "carrier/enable"), 1)
    device.set_int(_mod(dev, "sidebands/0/enable"), 0)
    device.set_int(_mod(dev, "sidebands/1/enable"), 0)
    device.set_int(_mod(dev, "output"), 1)
    device.set_int(_mod(dev, "enable"), 1)
    device.set_int(_sigout(dev, "on"), 1)
    ctx.carrier = dict(command_deg=0.0, safety_key=safety_key,
                       carrier_osc_idx=MOD_CARRIER_OSC_IDX, readout_osc_idx=READOUT_OSC_IDX,
                       sideband_osc_idxs=MOD_SIDEBAND_OSC_IDXS,
                       pm_enabled=False, fm_hz=float(p.sweep_order_hz[0]))
    ctx.state["carrier"] = dict(ctx.state.get("carrier") or {}, **ctx.carrier,
                                frequency_hz=p.y_rf_freq_hz, vpp=p.y_rf_vpp, vpk=p.y_carrier_vpk,
                                bias_v=p.y_bias_v, pm_sidebands_enabled=False,
                                readback=carrier_verify(ctx, output=True))
    ctx.outputs["Y 射频"] = (f"MFLI Out1 连续载波（标准 Mx，无 PM 边带），{p.y_carrier_vpk:g} Vpk，"
                             f"偏置 {p.y_bias_v:+g} V，指令相位 0°")
    return ctx.carrier


def carrier_set_phase(ctx, phase_deg: float) -> float:
    """输出开启状态下连续改写 MF-MOD 载波相移并回读确认。"""
    if ctx.carrier is None:
        raise SafetyAbort("缺少本轮已准备的载波会话；请从 A02 开始整链执行")
    _carrier_safety(ctx)
    device = ctx.devices["hf2"]
    command = float(phase_deg) % 360.0
    device.set_double(_mod(device.device_id, "carrier/phaseshift"), command)
    ctx.carrier["command_deg"] = command
    carrier_verify(ctx, output=True)
    tag = f"PM {ctx.carrier.get('fm_hz', 0.0):g} Hz" if ctx.carrier.get("pm_enabled") else "无 PM 边带"
    ctx.outputs["Y 射频"] = f"MFLI Out1 载波（标准 Mx，{tag}），指令相位 {command:g}°"
    return command


def carrier_begin_pm(ctx) -> dict:
    """保持载波与相移不变，使能 2° 峰值相干边带 PM 并回读；先按含边带包络校验限值。"""
    if ctx.carrier is None:
        raise SafetyAbort("缺少本轮已准备的载波会话；请从 A02 开始整链执行")
    p = ctx.params
    device, dev = ctx.devices["hf2"], ctx.devices["hf2"].device_id
    ctx.carrier.update(pm_enabled=True, fm_hz=float(p.sweep_order_hz[0]))
    _carrier_safety(ctx)
    for index in MOD_SIDEBAND_OSC_IDXS:
        device.set_double(f"{device.osc_path(index)}/freq", ctx.carrier["fm_hz"])
    device.set_double(_mod(dev, "index"), float(p.y_pm_depth_rad))
    device.set_int(_mod(dev, "sidebands/0/enable"), 1)
    device.set_int(_mod(dev, "sidebands/1/enable"), 1)
    got = carrier_verify(ctx, output=True)
    _sync_carrier_state(ctx)
    ctx.outputs["Y 射频"] = (f"MFLI Out1 载波 + 2° 峰值相干边带 PM {ctx.carrier['fm_hz']:g} Hz，"
                             f"指令相位 {ctx.carrier['command_deg']:g}°")
    return got


def carrier_set_pm_frequency(ctx, frequency_hz: float) -> dict:
    """切换上下边带振荡器频率并回读，载波频率与相移保持不变。"""
    if ctx.carrier is None or not ctx.carrier.get("pm_enabled"):
        raise SafetyAbort("缺少本轮已开启的 PM 边带会话；请从 A05 开始整链执行")
    value = float(frequency_hz)
    if not math.isfinite(value) or value <= 0:
        raise SafetyAbort(f"调相频率无效：{frequency_hz!r}")
    _carrier_safety(ctx)
    device, dev = ctx.devices["hf2"], ctx.devices["hf2"].device_id
    ctx.carrier["fm_hz"] = value
    for index in MOD_SIDEBAND_OSC_IDXS:
        device.set_double(f"{device.osc_path(index)}/freq", value)
    got = carrier_verify(ctx, output=True)
    _sync_carrier_state(ctx)
    ctx.outputs["Y 射频"] = (f"MFLI Out1 载波 + 2° 峰值相干边带 PM {value:g} Hz，"
                             f"指令相位 {ctx.carrier['command_deg']:g}°")
    return got


def carrier_stop_pm(ctx) -> dict:
    """关闭上下边带回到无调制连续载波，载波幅值与相移保持不变。"""
    if ctx.carrier is None:
        raise SafetyAbort("缺少本轮已准备的载波会话；请从 A02 开始整链执行")
    ctx.carrier["pm_enabled"] = False
    _carrier_safety(ctx)
    device, dev = ctx.devices["hf2"], ctx.devices["hf2"].device_id
    device.set_int(_mod(dev, "sidebands/0/enable"), 0)
    device.set_int(_mod(dev, "sidebands/1/enable"), 0)
    got = carrier_verify(ctx, output=True)
    _sync_carrier_state(ctx)
    ctx.outputs["Y 射频"] = (f"MFLI Out1 连续载波（标准 Mx，无 PM 边带），"
                             f"指令相位 {ctx.carrier['command_deg']:g}°")
    return got


def stop_carrier(ctx) -> None:
    """安全关闭并清零 MFLI Signal Output 1 与 MF-MOD，全部节点逐项回读验证。"""
    lockin = ctx.devices["hf2"]
    dev = lockin.device_id
    safety_key = mfli_sigout_safety_key(ctx.mapping)
    record = ctx.state.setdefault("shutdown_readbacks", {})[safety_key] = {"channel": 0}
    errors = []
    try:
        lockin.set_int(_sigout(dev, "on"), 0)
        lockin.sync()
        if int(lockin.get_int(_sigout(dev, "on"))) != 0:
            raise SafetyAbort("MFLI Out1 未回读为 OFF，禁止归零")
        lockin.set_int(_mod(dev, "sidebands/0/enable"), 0)
        lockin.set_int(_mod(dev, "sidebands/1/enable"), 0)
        lockin.set_double(_mod(dev, "carrier/amplitude"), 0.0)
        lockin.set_int(_mod(dev, "output"), 0)
        lockin.set_int(_mod(dev, "enable"), 0)
        validate_safety_limit(safety_key, 0.0, load_safety_limits(ctx.root))
        lockin.set_double(_sigout(dev, "offset"), 0.0)
        record["offset_commanded_v"] = 0.0
    except Exception as exc:
        errors.append(f"Y 射频收尾命令: {exc}")
    finally:
        for path, value in ((_sigout(dev, "on"), 0), (_mod(dev, "enable"), 0)):
            try:
                lockin.set_int(path, value)
            except Exception as exc:
                errors.append(f"最终关闭 {path}: {exc}")
        try:
            lockin.sync()
        except Exception as exc:
            errors.append(f"MFLI sync 异常: {exc}")
    checks = {
        "output": (lambda: int(lockin.get_int(_sigout(dev, "on"))) == 1, lambda value: value is False),
        "mod_enable": (lambda: int(lockin.get_int(_mod(dev, "enable"))), lambda value: value == 0),
        "mod_output": (lambda: int(lockin.get_int(_mod(dev, "output"))), lambda value: value == 0),
        "sideband0_enable": (lambda: int(lockin.get_int(_mod(dev, "sidebands/0/enable"))), lambda value: value == 0),
        "sideband1_enable": (lambda: int(lockin.get_int(_mod(dev, "sidebands/1/enable"))), lambda value: value == 0),
        "offset_v": (lambda: float(lockin.get_double(_sigout(dev, "offset"))),
                     lambda value: math.isclose(value, 0.0, abs_tol=1e-4)),
        "carrier_amplitude_vpk": (lambda: float(lockin.get_double(_mod(dev, "carrier/amplitude"))),
                                  lambda value: math.isclose(value, 0.0, abs_tol=1e-5)),
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
        ctx.outputs["Y 射频"] = "回读为 ON，需现场检查" if record.get("output") is True else "收尾未确认，需现场检查"
        raise SafetyAbort(f"{safety_key} 收尾未通过: {'；'.join(errors)}")
    ctx.outputs["Y 射频"] = "OFF，MFLI Out1 偏置与载波幅值已归零"


def prepare(ctx):
    """A02：温控、X 偏置、共同触发、Z 链关闭、Demod 4 读出、载波与主场。"""
    check_cancelled()
    ctx.active = True
    configure_temperature(ctx)
    configure_x_bias(ctx)
    configure_trigger(ctx)
    disable_z_chain(ctx)
    configure_lockin(ctx)
    carrier_begin(ctx)
    set_main_field(ctx)
    ctx.state["main_field_current_ma"] = float(ctx.params.main_field_current_ma)
    ctx.save()
    check_cancelled()
    return ctx


def _restore_temperature(ctx):
    ctx.state.setdefault("shutdown_readbacks", {})[TEMP_SWITCH_KEY] = write_temperature(ctx, True)
    ctx.outputs["温控"] = "ON（5 V DC + 输出 ON，已回读）"


def _restore_main_field(ctx):
    source, before = ctx.devices["gs200"], ctx.main_field_state
    if before is None:
        return
    restore_main_field_state(source, before)
    actual = snapshot_main_field_state(source)
    if (actual.output_on != before.output_on
            or not math.isclose(actual.current_a, before.current_a, abs_tol=1e-6)
            or not math.isclose(actual.current_range_a, before.current_range_a, rel_tol=1e-6)
            or not math.isclose(actual.current_limit_a, before.current_limit_a, rel_tol=1e-6)):
        raise SafetyAbort("GS200 主磁场未回读为运行前状态")
    ctx.state.setdefault("shutdown_readbacks", {})[MAIN_FIELD_KEY] = asdict(actual)
    ctx.outputs["主磁场"] = "已回读恢复原状态"


def _stop_mfli_readout(ctx):
    lockin = ctx.devices["hf2"]
    demod.set_demod_enable(lockin, ctx.params.demod_idx, False)
    if lockin.get_int(f"{lockin.demod_path(ctx.params.demod_idx)}/enable") != 0:
        raise SafetyAbort("MFLI 未回读为停止解调传输")
    ctx.outputs["MFLI"] = "已回读停止解调传输"


def park(ctx) -> list[str]:
    """以共享安全编排逐项恢复：载波、Z/X 归零、温控与主场。单项失败不跳过其他项。

    设备断开由工作流在拿到本函数结果后统一执行，避免同一物理设备被断开两次。
    """
    if not ctx.devices:
        return []
    signals, channels = ctx.devices.get("signals"), ctx.devices.get("channels") or {}
    actions = [ShutdownAction("MFLI 载波关闭并归零", lambda: stop_carrier(ctx))]
    if ctx.devices.get("hf2"):
        actions.append(ShutdownAction("停 MFLI 解调传输", lambda: _stop_mfli_readout(ctx)))
    actions.append(ShutdownAction("恢复温控并回读", lambda: _restore_temperature(ctx)))
    if ctx.main_field_state is not None:
        actions.append(ShutdownAction("恢复 GS200 主磁场", lambda: _restore_main_field(ctx)))
    dg_channels = []
    if signals is not None and "z" in channels:
        dg_channels.append(DGChannelShutdown(signals, channels["z"], SOURCE_KEY, "Z 线圈驱动", disable_sync=True))
    if signals is not None and "x" in channels:
        dg_channels.append(DGChannelShutdown(signals, channels["x"], X_KEY, "X 静态偏置"))
    for key in ("Z 通道", "Y 射频", "X 偏置", "温控", "MFLI"):
        ctx.outputs[key] = "收尾待回读"
    report = run_safety_shutdown(
        dg_channels=dg_channels,
        extra_actions=actions,
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, TRIGGER_KEY),
    )
    ctx.state["safety_shutdown"] = report.to_dict()
    if report.completed:
        ctx.active = False
        ctx.main_field_state = None
    return list(report.errors)
