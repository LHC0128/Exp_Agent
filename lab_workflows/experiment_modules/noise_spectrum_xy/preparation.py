"""预检、连接、时钟、固定输出与温度准备。"""
from lab_workflows.steps import initialize_lockin_inputs
import numpy as np
from gs200 import GS200Instrument
from tec_controller import TECInstrument
from lab_workflows.devices import create_lockin
from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...instrument_config import instrument_config_snapshot
from ...lockin_compatibility import require_lockin_compatibility
from ...experiment_runtime import check_cancelled
from ...steps import DeviceSession, connect_signal_generator_routes, synchronize_connected_clocks, configure_temperature_control
from ...steps.safety_shutdown import disconnect_device_mapping
from .models import NoiseSpectrumXYParams
from ._helpers import _RuntimeCancellation, set_temp_switch
EXPERIMENT_TYPE = "Noise_Spectrum_XY_Ctrl"
PURPOSE = "noise_spectroscopy"

def build_target_scan_axes(params: NoiseSpectrumXYParams, limits):
    """由目标 Omega_ctrl 轴反算正弦峰值电压轴。"""
    target_freq_hz = np.linspace(
        params.target_noise_freq_start_hz,
        params.target_noise_freq_stop_hz,
        params.target_noise_freq_points,
    )
    envelope_v = (target_freq_hz - params.xy_ctrl_b_hz) / params.xy_ctrl_k_hz_per_v
    envelope_max = float(np.max(envelope_v))

    for key in ("X_magnetic_field", "Y_magnetic_field"):
        validate_safety_limit(key, -envelope_max, limits)
        validate_safety_limit(key, envelope_max, limits)

    return target_freq_hz, envelope_v


def connect_instruments(mapping, *, on_failure=None):
    """按既有路由连接设备；失败释放已建立的连接。"""
    devices = {}
    session = DeviceSession()

    try:
        # ---- GS200: 主磁场 ----
        gs_cfg = mapping["main_magnetic_field"]
        gs = session.connect(
            "gs200", gs_cfg["resource"], lambda: GS200Instrument(gs_cfg["resource"])
        )
        print(f"GS200 已连接: {gs.idn()}")
        devices["gs200"] = gs

        # ---- DG4000: X/Y 补偿磁场（Burst 载波输出） ----
        dg_comp_cfg = mapping["X_magnetic_field"]
        dg_comp, _ = connect_signal_generator_routes(
            session,
            "dg_comp",
            {
                "x": ("X_magnetic_field", dg_comp_cfg),
                "y": ("Y_magnetic_field", mapping["Y_magnetic_field"]),
            },
            logical_channels={"x": 1, "y": 2},
        )
        print(f"补偿场 DG4000 已连接: {dg_comp.idn()}")
        # Y 通道: 通过同一设备 CH2 控制
        devices["dg_comp"] = dg_comp

        # ---- DG4000: Z 磁场关闭；Time_sequence_2 为 dg_comp 共用外触发 ----
        dg_sweep_cfg = mapping["Z_magnetic_field"]
        dg_sweep, _ = connect_signal_generator_routes(
            session,
            "dg_sweep",
            {
                "z": ("Z_magnetic_field", dg_sweep_cfg),
                "sequence_2": ("Time_sequence_2", mapping["Time_sequence_2"]),
            },
            logical_channels={"z": 1, "sequence_2": 2},
        )
        print(f"Z 场 DG4000 已连接: {dg_sweep.idn()}")
        devices["dg_sweep"] = dg_sweep

        # ---- DG4000: Pump 调制 ----
        dg_mod_cfg = mapping["Pump_modulation"]
        dg_mod, _ = connect_signal_generator_routes(
            session,
            "dg_mod",
            {
                "carrier": ("Pump_modulation", dg_mod_cfg),
                "gate": ("Time_sequence", mapping["Time_sequence"]),
            },
            logical_channels={"carrier": 1, "gate": 2},
        )
        print(f"调制 DG4000 已连接: {dg_mod.idn()}")
        devices["dg_mod"] = dg_mod

        # ---- DG900: 温度开关 ----
        dg_temp_cfg = mapping["Temp_Switch"]
        dg_temp, _ = connect_signal_generator_routes(
            session,
            "dg_temp",
            {"temp": ("Temp_Switch", dg_temp_cfg)},
            logical_channels={"temp": 2},
        )
        print(f'温控设备已连接: {dg_temp.idn()}')
        devices["dg_temp"] = dg_temp

        # ---- TEC103: 温度控制器 ----
        tec_cfg = mapping["temperature"]
        tec = session.connect_optional(
            "tec",
            tec_cfg["resource"],
            lambda: TECInstrument(port=tec_cfg["resource"]),
            device_label="TEC103",
        )
        devices["tec"] = tec

        # ---- DG900: Pump/Probe 光功率 ----
        dg_laser_cfg = mapping["Pump_laser_power"]
        dg_probe_cfg = mapping["Probe_laser_power"]
        dg_laser, _ = connect_signal_generator_routes(
            session,
            "dg_laser",
            {
                "pump": ("Pump_laser_power", dg_laser_cfg),
                "probe": ("Probe_laser_power", dg_probe_cfg),
            },
            logical_channels={"pump": 1, "probe": 2},
        )
        print(f'光功率 {dg_laser_cfg["model"]} 已连接: {dg_laser.idn()}')
        devices["dg_laser"] = dg_laser

        # ---- HF2: 锁相放大器 ----
        hf2_cfg = mapping["lockin_r"]
        hfi = session.connect(
            "hf2",
            f"hf2://{hf2_cfg.get('host', '127.0.0.1')}/{hf2_cfg['device_id']}",
            lambda: create_lockin(hf2_cfg),
        )
        initialize_lockin_inputs(hfi)
        print(f"HF2 已连接: {hfi.idn}")
        devices["hf2"] = hfi

    except BaseException as e:
        print(f"设备连接失败: {e}")
        if on_failure is not None:
            try:
                on_failure(session)
            except Exception as cleanup_error:
                print(f"连接安全收尾警告: {cleanup_error}")
        try:
            session.cleanup_connection_failure()
        except Exception as cleanup_error:
            print(f"连接清理警告: {cleanup_error}")
        raise

    print(f"\n所有设备连接完成，共 {len(devices)} 个设备")
    return devices, session


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
            },
        )


def connect_devices(mapping):
    devices, _ = connect_instruments(mapping)
    try:
        return devices, synchronize_devices(devices, mapping)
    except BaseException:
        disconnect_device_mapping(devices)
        raise

def configure_initial_outputs(devices, params: NoiseSpectrumXYParams, mapping, limits):
    """设置固定输出和温度；调用方负责统一安全收尾。"""
    tec = devices["tec"]
    gs = devices["gs200"]
    dg_laser = devices["dg_laser"]
    dg_sweep = devices["dg_sweep"]
    dg_temp = devices["dg_temp"]
    gs.set_source_function(mapping["main_magnetic_field"]["source_function"])
    # GS200 保留硬件级电流保护。
    gs.set_current_limit(limits["main_magnetic_field"]["max"] / 1000.0)
    validate_safety_limit("Z_magnetic_field", 0.0, limits)
    dg_sweep.setup_dc(0.0, channel=1)
    dg_sweep.set_output(False, channel=1)
    dg_sweep.set_output(False, channel=2)
    check_cancelled()
    # ---- 1. Pump 光功率 ----
    validate_safety_limit("Pump_laser_power", params.pump_laser_power, limits)
    dg_laser.setup_dc(
        params.pump_laser_power,
        channel=1,
    )
    print(f"Pump 光功率: {params.pump_laser_power} V DC")

    # ---- 2. Probe 光功率 ----
    validate_safety_limit("Probe_laser_power", params.probe_laser_power, limits)
    dg_laser.setup_dc(
        params.probe_laser_power,
        channel=2,
    )
    print(f"Probe 光功率: {params.probe_laser_power} V DC")

    # ---- 3. 主磁场 ----
    validate_safety_limit("main_magnetic_field", params.main_magnetic_field, limits)
    gs.set_current(params.main_magnetic_field / 1000.0)
    gs.set_output(True)
    print(f"主磁场: {params.main_magnetic_field} mA")

    # ---- 4. 温度开关 (ON) ----
    validate_safety_limit("Temp_Switch", params.temp_switch, limits)
    set_temp_switch(dg_temp, True, params, limits)
    print(f"温度开关: ON ({params.temp_switch} V)")

    # ---- 6. Z 磁场输出确认关闭 ----
    validate_safety_limit("Z_magnetic_field", 0.0, limits)
    dg_sweep.setup_dc(0.0, channel=1)
    dg_sweep.set_output(False, channel=1)
    dg_sweep.set_output(False, channel=2)
    print("Z 磁场: DC 0V, 输出 OFF (本实验不使用)")


def configure_temperature(devices, params):
    """只下发 UI 目标温度；不启停 TEC，也不等待温度稳定。"""
    tec = devices["tec"]
    # ---- 5. 温度控制 ----
    temperature_status = configure_temperature_control(
        tec,
        params.temperature,
        channel=1,
    )
    temp_now = temperature_status.actual_temperature_c
    if temp_now is not None:
        print(f"TEC 温度读数: {temp_now:.2f} °C")

    return temperature_status

def build_run_config(params: NoiseSpectrumXYParams, project_root, clock_sources, timestamp, target_frequencies, envelopes):
    """保存原参数、扫描轴和设备快照，保持历史字段不变。"""
    fixed_params = {
        "Pump_laser_power": params.pump_laser_power,
        "Probe_laser_power": params.probe_laser_power,
        "temperature": params.temperature,
        "Temp_Switch": params.temp_switch,
        "main_magnetic_field": params.main_magnetic_field,
    }
    instrument_snapshot = instrument_config_snapshot(project_root)
    config = {
        "experiment_id": "noise-spectrum-xy",
        "schema_version": params.schema_version,
        "execution_mode": "typed_workflow",
        "clock_sources": clock_sources,
        "parameters": params.to_external(),
        "experiment_type": EXPERIMENT_TYPE,
        "purpose": PURPOSE,
        "timestamp": timestamp,
        "device_library_revision": instrument_snapshot["device_library_revision"],
        "physical_mapping_revision": instrument_snapshot["physical_mapping_revision"],
        "device_library_snapshot": instrument_snapshot["device_library"],
        "physical_mapping_snapshot": instrument_snapshot["physical_mappings"],
        "mapping_snapshot": instrument_snapshot["resolved_mapping"],
        "scan_params": {
            "TARGET_NOISE_FREQ_START_HZ": params.target_noise_freq_start_hz,
            "TARGET_NOISE_FREQ_STOP_HZ": params.target_noise_freq_stop_hz,
            "TARGET_NOISE_FREQ_POINTS": params.target_noise_freq_points,
            "target_noise_frequency_Hz": target_frequencies.tolist(),
            "xy_envelope_voltage_V": envelopes.tolist(),
            "XY_SETTLE_TIME_s": params.system_settle_s,
            "TEMP_SWITCH_ON_SETTLE_S": params.temp_switch_on_settle_s,
            "temperature_switch_off_mode": "dc_zero_output_on",
        },
        "xy_ctrl": {
            "XY_CTRL_FREQ_Hz": params.xy_ctrl_freq,
            "XY_CTRL_PHASE_deg": params.xy_ctrl_phase,
            "XY_CTRL_QUAD_deg": params.xy_ctrl_quad,
            "XY_CALIB_ENVELOPE_V": params.xy_calib_envelope_v,
            "XY_PHASE_CAL_TOL_DEG": params.xy_phase_cal_tol_deg,
            "XY_PHASE_CAL_MAX_ITER": params.xy_phase_cal_max_iter,
            "XY_PHASE_CAL_MIN_R_V": params.xy_phase_cal_min_r_v,
            "XY_PHASE_CAL_MIN_R_RATIO": params.xy_phase_cal_min_r_ratio,
            "XY_CTRL_K_HZ_PER_V": params.xy_ctrl_k_hz_per_v,
            "XY_CTRL_B_HZ": params.xy_ctrl_b_hz,
            "calibration_envelope_range_V": [
                params.xy_calibration_min_envelope_v,
                params.xy_calibration_max_envelope_v,
            ],
            "waveform_mode": "burst_sine",
            "sine_offset_V": 0.0,
            "amplitude_semantics": "2 * envelope peak voltage (Vpp)",
            "XY_TRIGGER_FREQ_Hz": params.xy_trigger_freq,
            "XY_TRIGGER_AMPLITUDE_Vpp": params.xy_trigger_amplitude,
            "XY_TRIGGER_OFFSET_V": params.xy_trigger_offset,
            "XY_TRIGGER_DUTY_pct": params.xy_trigger_duty,
            "XY_TRIGGER_PHASE_deg": params.xy_trigger_phase,
            "trigger_source": (
                f"Time_sequence_2 CH2 {params.xy_trigger_freq:g} Hz square -> "
                "splitter -> dg_comp CH1/CH2 Ext Trig"
            ),
            "trigger_policy": (
                "Time_sequence_2 CH2 output ON and phase init once -> "
                "each scan point: XY outputs OFF -> set_amplitude -> "
                "calibrated burst phases -> outputs ON awaiting shared external trigger; "
                "zero peak keeps XY outputs OFF"
            ),
        },
        "fixed_params": fixed_params,
        "pump_modulation": {
            "pump_mod_freq_Hz": params.pump_mod_freq,
            "pump_mod_amplitude_V": params.pump_mod_amplitude,
            "pump_mod_duty_pct": params.pump_mod_duty,
        },
        "hf2_demod": {
            "demod_idx": params.hf2_demod_idx,
            "osc_freq_Hz": params.hf2_osc_freq,
            "signal_range_V": params.hf2_signal_range,
            "demod_rate_Sa_s": params.hf2_demod_rate,
            "demod_TC_s": params.hf2_demod_tc,
        },
        "hf2_daq": {
            "DAQ_duration_s": params.hf2_daq_duration,
            "DAQ_TC_s": params.hf2_daq_tc,
            "DAQ_rate_Sa_s": params.hf2_daq_rate,
        },
    }
    return config


def preflight(params, project_root):
    """每次执行前校验真实配置，不改变仪器。"""
    params = NoiseSpectrumXYParams.from_external(params.to_external())
    errors = params.validate(project_root)
    if errors:
        raise ValueError("；".join(errors))
    from ...instrument_config import validate_mapping_usage
    from ...experiments.requirements import NOISE_SPECTRUM_XY
    validate_mapping_usage(NOISE_SPECTRUM_XY, project_root)
    mapping = load_mapping(project_root)
    require_lockin_compatibility("noise-spectrum-xy", params.to_external(), mapping=mapping)
    limits = load_safety_limits(project_root)
    axes = build_target_scan_axes(params, limits)
    from .phase import validate_sine_peak
    from .excitation import validate_pump_levels, validate_trigger_square_levels
    validate_sine_peak(params.xy_calib_envelope_v, limits)
    validate_trigger_square_levels(params, limits)
    validate_pump_levels(params, limits)
    check_cancelled()
    return params, mapping, limits, axes
