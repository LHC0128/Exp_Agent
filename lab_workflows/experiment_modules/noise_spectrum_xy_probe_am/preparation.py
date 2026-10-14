"""复用 XY 准备步骤，增加 Probe AM 信号源与噪声预检。"""
from gs200 import GS200Instrument
from lab_workflows.steps import initialize_lockin_inputs
from tec_controller import TECInstrument
from lab_workflows.devices import create_lockin
from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...experiments.requirements import PROBE_AM_NOISE_SPECTRUM_XY
from ...experiment_runtime import check_cancelled
from ...steps import DeviceSession, connect_signal_generator_routes, synchronize_connected_clocks
from ..noise_spectrum_xy.models import NoiseSpectrumXYParams
from ..noise_spectrum_xy.preparation import build_target_scan_axes
from ..noise_spectrum_xy.phase import validate_sine_peak
from ..noise_spectrum_xy.excitation import validate_pump_levels, validate_trigger_square_levels
from .models import NoiseSpectrumXYProbeAMParams
from .excitation import generate_probe_am_waveforms
from .safety import safe_connection_outputs_off


def validate_noise_output_levels(params, limits):
    """噪声注入输出极值全部先过安全限值。"""
    for value in (params.noise_amplitude_vpp / 2.0, -params.noise_amplitude_vpp / 2.0):
        validate_safety_limit("Probe_AOM_AM", value, limits)
    if params.inject_controlled_noise:
        for value in (params.z_noise_amplitude_vpp, params.z_noise_amplitude_vpp / 2,
                      -params.z_noise_amplitude_vpp / 2):
            validate_safety_limit("Z_magnetic_field", value, limits)


def preflight(params, root):
    params = NoiseSpectrumXYProbeAMParams.from_external(params.to_external())
    errors = params.validate(root)
    if errors:
        raise ValueError("；".join(errors))
    validate_mapping_usage(PROBE_AM_NOISE_SPECTRUM_XY, root)
    mapping, limits = load_mapping(root), load_safety_limits(root)
    require_lockin_compatibility("noise-spectrum-xy-uncontrolled-probe-am", params.to_external(), mapping=mapping)
    axes = build_target_scan_axes(params, limits)
    validate_sine_peak(params.xy_calib_envelope_v, limits)
    validate_trigger_square_levels(params, limits)
    validate_pump_levels(params, limits)
    validate_noise_output_levels(params, limits)
    generate_probe_am_waveforms(params)
    check_cancelled()
    return params, mapping, limits, axes


def synchronize_devices(devices, mapping):
    """连接与同步分开；失败由会话先安全关断再释放连接。"""
    return synchronize_connected_clocks(
        devices,
        mapping,
        {
            "dg_comp": "X_magnetic_field",
            "dg_sweep": "Z_magnetic_field",
            "dg_mod": "Pump_modulation",
            "dg_temp": "Temp_Switch",
            "dg_laser": "Pump_laser_power",
            "hf2": "lockin_r",
            "dg_probe_am": "Probe_AOM_Carrier",
        },
    )


def connect_instruments(ctx):
    """按既有路由连接设备；失败时先收尾已连接输出再释放连接。"""

    def shutdown_partial(session):
        report = safe_connection_outputs_off(session, ctx.mapping)
        ctx.shutdown = report.to_dict()
        if report.errors:
            print("连接安全收尾失败：" + "；".join(report.errors), flush=True)

    mapping = ctx.mapping
    devices = {}
    session = DeviceSession()
    try:
        # ---- GS200: 主磁场 ----
        gs_cfg = mapping["main_magnetic_field"]
        gs = session.connect("gs200", gs_cfg["resource"], lambda: GS200Instrument(gs_cfg["resource"]))
        print(f"GS200 已连接: {gs.idn()}")
        gs.set_source_function(gs_cfg["source_function"])
        # [经验] GS200 需硬件级电流保护
        gs.set_current_limit(ctx.limits["main_magnetic_field"]["max"] / 1000.0)
        devices["gs200"] = gs

        # ---- DG4000: X/Y 补偿磁场（Burst 载波输出） ----
        dg_comp_cfg = mapping["X_magnetic_field"]
        dg_comp, _ = connect_signal_generator_routes(
            session, "dg_comp",
            {"x": ("X_magnetic_field", dg_comp_cfg), "y": ("Y_magnetic_field", mapping["Y_magnetic_field"])},
            logical_channels={"x": 1, "y": 2},
        )
        print(f"补偿场 DG4000 已连接: {dg_comp.idn()}")
        devices["dg_comp"] = dg_comp

        # ---- DG4000: Z 可控噪声与 Time_sequence_2 共用外触发 ----
        sequence_cfg = mapping["Time_sequence_2"]
        dg_sweep_cfg = mapping["Z_magnetic_field"]
        dg_sweep, _ = connect_signal_generator_routes(
            session, "dg_sweep",
            {"z": ("Z_magnetic_field", dg_sweep_cfg), "sequence_2": ("Time_sequence_2", sequence_cfg)},
            logical_channels={"z": 1, "sequence_2": 2},
        )
        z_channel = int(dg_sweep_cfg["channel"])
        dg_sweep.set_output(False, channel=z_channel)
        dg_sweep.set_voltage_unit("VPP", channel=z_channel)
        print(f"XY 外触发 DG4000 已连接: {dg_sweep.idn()}")
        devices["dg_sweep"] = dg_sweep

        # ---- DG4000: Probe AOM 载波与外部 AM 噪声（独立信号源） ----
        dg_probe_cfg = mapping["Probe_AOM_Carrier"]
        dg_probe_noise_cfg = mapping["Probe_AOM_AM"]
        dg_probe_am, _ = connect_signal_generator_routes(
            session, "dg_probe_am",
            {"carrier": ("Probe_AOM_Carrier", dg_probe_cfg), "noise": ("Probe_AOM_AM", dg_probe_noise_cfg)},
            logical_channels={"carrier": 1, "noise": 2},
        )
        print(f"Probe AOM 信号源已连接: {dg_probe_am.idn()}")
        devices["dg_probe_am"] = dg_probe_am

        # ---- DG4000: Pump 调制 ----
        dg_mod_cfg = mapping["Pump_modulation"]
        dg_mod, _ = connect_signal_generator_routes(
            session, "dg_mod",
            {"carrier": ("Pump_modulation", dg_mod_cfg), "gate": ("Time_sequence", mapping["Time_sequence"])},
            logical_channels={"carrier": 1, "gate": 2},
        )
        print(f"调制 DG4000 已连接: {dg_mod.idn()}")
        devices["dg_mod"] = dg_mod

        # ---- DG900: 温度开关 ----
        dg_temp_cfg = mapping["Temp_Switch"]
        dg_temp, _ = connect_signal_generator_routes(
            session, "dg_temp", {"temp": ("Temp_Switch", dg_temp_cfg)}, logical_channels={"temp": 2},
        )
        print(f'温控设备已连接: {dg_temp.idn()}')
        devices["dg_temp"] = dg_temp

        # ---- TEC103: 温度控制器 ----
        tec_cfg = mapping["temperature"]
        tec = session.connect_optional("tec", tec_cfg["resource"],
            lambda: TECInstrument(port=tec_cfg["resource"]), device_label="TEC103")
        devices["tec"] = tec

        # ---- DG900: Pump/Probe 光功率 ----
        dg_laser_cfg = mapping["Pump_laser_power"]
        dg_laser, _ = connect_signal_generator_routes(
            session, "dg_laser",
            {"pump": ("Pump_laser_power", dg_laser_cfg), "probe": ("Probe_laser_power", mapping["Probe_laser_power"])},
            logical_channels={"pump": 1, "probe": 2},
        )
        print(f'光功率 {dg_laser_cfg["model"]} 已连接: {dg_laser.idn()}')
        devices["dg_laser"] = dg_laser

        # ---- HF2: 锁相放大器 ----
        hf2_cfg = mapping["lockin_r"]
        hfi = session.connect("hf2", f"hf2://{hf2_cfg.get('host', '127.0.0.1')}/{hf2_cfg['device_id']}",
            lambda: create_lockin(hf2_cfg))
        initialize_lockin_inputs(hfi)
        print(f"HF2 已连接: {hfi.idn}")
        devices["hf2"] = hfi
    except BaseException as e:
        print(f"设备连接失败: {e}")
        try:
            shutdown_partial(session)
        except Exception as cleanup_error:
            print(f"连接安全收尾警告: {cleanup_error}")
        try:
            session.cleanup_connection_failure()
        except Exception as cleanup_error:
            print(f"连接清理警告: {cleanup_error}")
        raise

    print(f"\n所有设备连接完成，共 {len(devices)} 个设备")
    return devices, session
