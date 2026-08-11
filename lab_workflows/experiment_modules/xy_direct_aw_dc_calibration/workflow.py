# %% [markdown] Cell 0
# # XY DirectAW 恒定包络电压标定
#
# 本实验复用 `RF_Field_Sensitivity_AW_FreqSweep_DirectAW.py` 的 DirectAW 链路，
# 直接由 `dg_comp` CH1/CH2 输出正交任意波，不经过外部 AM 调制：
#
# ```text
# X(t) = V_env cos(2π f_L t + φ)
# Y(t) = V_env cos(2π f_L t + φ + 90°)
# ```
#
# `V_env` 在 -2 V 到 +2 V 扫描；DG 任意波输出始终固定为 4 Vpp、0 V offset，
# 仅改变上传波形的归一化数组。每个 `V_env` 下扫描 Z 射频频率，得到响应峰位，
# 离线拟合 DirectAW 电压与实际响应中心频率之间的映射。
#
# 数据目录：`data/XY_DirectAW_DC_Calibration/<MMDD_HHMM_direct_aw_dc_cal>/`

# %% Cell 1
from pathlib import Path
import sys

project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import json
import math
import time
from datetime import datetime

import numpy as np
import yaml
from tqdm import tqdm

from gs200 import GS200Instrument
from lab_workflows.common import load_mapping
from lab_workflows.instrument_config import instrument_config_snapshot
from lab_workflows.devices import (
    signal_generator_max_arb_points,
)
from tec_controller import TECInstrument
from lab_workflows.experiment_runtime import (
    apply_runtime_params,
    check_cancelled,
    load_runtime_params,
)
from lab_workflows.experiment_modules.xy_direct_aw_dc_calibration.models import (
    XYDirectAWDCCalibrationParams,
)
from lab_workflows.steps import (
    DGChannelShutdown,
    DirectAWPhaseCalibrationConfig,
    DeviceSession,
    DisconnectTarget,
    STANDARD_PRESERVED_OUTPUTS,
    PhaseCalibrationConfig,
    TemperatureSwitchRestore,
    calibrate_demod_phase,
    calibrate_direct_aw_phase,
    configure_temperature_control,
    connect_signal_generator_routes,
    run_safety_shutdown,
    synchronize_connected_clocks,
)
from lockin_amplifier import (
    HF2Instrument,
    DAQConfig,
    SignalInputConfig,
    OscillatorConfig,
    DemodulatorConfig,
    demod,
    daq,
)

print("实验库导入完成")

# %% Cell 2
# ========== 配置与安全限值 ==========
MAPPING = load_mapping(project_root)
MAX_ARB_POINTS = signal_generator_max_arb_points(MAPPING["X_magnetic_field"])
with open(project_root / "params" / "safety_limits.yaml", encoding="utf-8") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]

def validate_safety_limit(name, value):
    """检查单个输出值是否满足仓库安全限值。"""
    lim = LIMITS.get(name)
    if lim is None:
        return float(value)
    lo = lim.get("min")
    hi = lim.get("max")
    value = float(value)
    if lo is not None and value < lo:
        raise ValueError(f"[安全拦截] {name}={value} 低于下限 {lo}")
    if hi is not None and value > hi:
        raise ValueError(f"[安全拦截] {name}={value} 高于上限 {hi}")
    return value


EXPERIMENT_TYPE = "XY_DirectAW_DC_Calibration"
RUN_TAG = "direct_aw_dc_cal"

# ---- DirectAW 恒定包络扫描 ----
# 17 点覆盖完整固定输出范围；负值等效于旋转场整体翻转 180°。
XY_ENV_VOLTAGE_START_V = -2.0
XY_ENV_VOLTAGE_STOP_V = 2.0
XY_ENV_VOLTAGE_POINTS = 17
# 兼容旧配置键；新配置统一使用起始值、终止值和点数。
XY_ENV_VOLTAGE_LIST_V = None
if XY_ENV_VOLTAGE_LIST_V is None:
    XY_ENV_VOLTAGE_LIST_V = np.linspace(
        XY_ENV_VOLTAGE_START_V,
        XY_ENV_VOLTAGE_STOP_V,
        XY_ENV_VOLTAGE_POINTS,
    )
else:
    XY_ENV_VOLTAGE_LIST_V = np.array(XY_ENV_VOLTAGE_LIST_V, dtype=float)
XY_AW_OUTPUT_VPP = 4.0
XY_AW_OUTPUT_OFFSET_V = 0.0
XY_AW_REPEAT_FREQ_Hz = 500.0
XY_AW_POINTS = 10000
XY_CARRIER_FREQ_Hz = 10000.0
XY_CTRL_PHASE_INITIAL_deg = 0.0
XY_CTRL_QUAD_deg = 90.0
XY_ENV_SETTLE_TIME_s = 1.0

# ---- DirectAW 外部触发 ----
XY_TRIGGER_FREQ_Hz = 100.0
XY_TRIGGER_AMPLITUDE_Vpp = 5.0
XY_TRIGGER_OFFSET_V = 2.5
XY_TRIGGER_DUTY_pct = 50.0
XY_TRIGGER_PHASE_deg = 0.0

# ---- DirectAW 整体相位校准 ----
ENABLE_XY_PHASE_CAL = True
XY_PHASE_CAL_ENVELOPE_V = 0.5
XY_PHASE_CAL_TOL_deg = 0.5
XY_PHASE_CAL_MAX_ITER = 8
XY_PHASE_CAL_MIN_R_V = 1e-12
XY_PHASE_CAL_MIN_R_RATIO = 0.1
XY_PHASE_CAL_SETTLE_s = 0.8

# ---- Z 射频频率扫描 ----
Z_RF_FREQ_START_Hz = 500.0
Z_RF_FREQ_STOP_Hz = 35000.0
Z_RF_FREQ_STEP_Hz = 500.0
Z_RF_FREQ_LIST_Hz = np.arange(
    Z_RF_FREQ_START_Hz,
    Z_RF_FREQ_STOP_Hz + 0.5 * Z_RF_FREQ_STEP_Hz,
    Z_RF_FREQ_STEP_Hz,
)
Z_RF_AMPLITUDE_Vpp = 0.01
FREQ_SETTLE_TIME_s = 0.5

# ---- 采集模式 ----
# both: 同时保存 Demod 0 sample.y 时域数据和 Demod 3 硬件解调结果。
ACQUISITION_MODE = "both"  # daq_y, demod_rxy, both
POINT_DURATION_s = 1.0

# ---- Pump / HF2 ----
PUMP_MOD_FREQ_Hz = 10000.0
PUMP_MOD_DUTY_pct = 5.0
PUMP_MOD_AMPLITUDE_Vpp = 0.18

DEMOD0_IDX = 0
DEMOD0_OSC_IDX = 0
DEMOD0_SIGNAL_RANGE_V = 2.0
DEMOD0_ORDER = 4
DEMOD0_TC_CALIB_s = 1e-3
DEMOD0_TC_MEAS_s = 1e-6
DEMOD0_RATE_Sa_s = 100000.0

DEMOD3_IDX = 3
DEMOD3_OSC_IDX = 1
DEMOD3_ADC_SELECT = 1
DEMOD3_ORDER = 8
DEMOD3_RATE_Sa_s = 4800.0
DEMOD3_TC_MIN_s = 0.01
DEMOD3_TC_PERIOD_FRACTION = 0.05
DEMOD3_READ_SETTLE_TIME_s = 0.2
DEMOD3_N_AVG = 50
DEMOD3_AVG_INTERVAL_s = 0.01

# ---- Demod 0 Y 物理回环到 Demod 3 ----
AUXOUT_INDEX = 1
AUXOUT_SOURCE_DEMOD_IDX = 0
AUXOUT_SOURCE_SELECT = 1  # Y
AUXOUT_SCALE = 1.0
AUXOUT_OFFSET_V = 0.0
SIGIN2_INDEX = 1
SIGIN2_RANGE_V = 1.0
SIGIN2_AC_COUPLING = False
SIGIN2_IMPEDANCE_OHM = 50

# ---- 温控与固定参数，沿用 DirectAW 频响实验 ----
TEMP_SWITCH_OFF_LEAD_s = 0.1
TEMP_SWITCH_ON_LAG_s = 1.0
FIXED_PARAMS = {
    "Pump_laser_power": 0.5,
    "Probe_laser_power": 0.3,
    "main_magnetic_field": 1.03,
    "temperature": 100.0,
    "Temp_Switch": 5.0,
}

# 强类型模型是唯一 GUI/默认配置入口；大写名称仅作为旧实验主体的局部兼容别名。
PARAMS = load_runtime_params(XYDirectAWDCCalibrationParams)
apply_runtime_params(globals(), PARAMS)
# Pump 调制、Demod0 参考与 DirectAW 载波共用前端声明的同一频率。
XY_CARRIER_FREQ_Hz = PUMP_MOD_FREQ_Hz
XY_ENV_VOLTAGE_LIST_V = np.linspace(
    XY_ENV_VOLTAGE_START_V,
    XY_ENV_VOLTAGE_STOP_V,
    XY_ENV_VOLTAGE_POINTS,
)
Z_RF_FREQ_LIST_Hz = np.arange(
    Z_RF_FREQ_START_Hz,
    Z_RF_FREQ_STOP_Hz + 0.5 * Z_RF_FREQ_STEP_Hz,
    Z_RF_FREQ_STEP_Hz,
)

# ---- 安全校验 ----
if XY_ENV_VOLTAGE_POINTS < 2:
    raise ValueError("DirectAW 扫描点数必须至少为 2")
if XY_ENV_VOLTAGE_START_V >= XY_ENV_VOLTAGE_STOP_V:
    raise ValueError("DirectAW 扫描起始电压必须小于终止电压")
if ACQUISITION_MODE not in ("daq_y", "demod_rxy", "both"):
    raise ValueError(
        "ACQUISITION_MODE 必须是 daq_y、demod_rxy 或 both"
    )
for key, value in FIXED_PARAMS.items():
    validate_safety_limit(key, value)
validate_safety_limit("Pump_modulation", PUMP_MOD_AMPLITUDE_Vpp)
validate_safety_limit("Time_sequence", 5.0)
validate_safety_limit("Z_magnetic_field", Z_RF_AMPLITUDE_Vpp)
validate_safety_limit("rf_coil", 0.0)

aw_low = XY_AW_OUTPUT_OFFSET_V - XY_AW_OUTPUT_VPP / 2.0
aw_high = XY_AW_OUTPUT_OFFSET_V + XY_AW_OUTPUT_VPP / 2.0
for key in ("X_magnetic_field", "Y_magnetic_field"):
    validate_safety_limit(key, aw_low)
    validate_safety_limit(key, aw_high)
if np.min(XY_ENV_VOLTAGE_LIST_V) < aw_low or np.max(XY_ENV_VOLTAGE_LIST_V) > aw_high:
    raise ValueError(
        f"包络扫描范围 [{np.min(XY_ENV_VOLTAGE_LIST_V)}, "
        f"{np.max(XY_ENV_VOLTAGE_LIST_V)}] V 超出固定 AW 输出范围 "
        f"[{aw_low}, {aw_high}] V"
    )
carrier_cycles = XY_CARRIER_FREQ_Hz / XY_AW_REPEAT_FREQ_Hz
if not np.isclose(carrier_cycles, round(carrier_cycles)):
    raise ValueError("单个 AW 周期必须包含整数个 DirectAW 载波周期")
trigger_ratio = XY_AW_REPEAT_FREQ_Hz / XY_TRIGGER_FREQ_Hz
if not np.isclose(trigger_ratio, round(trigger_ratio)):
    raise ValueError("AW 重复频率必须是外部触发频率的整数倍")
if XY_AW_POINTS > MAX_ARB_POINTS:
    raise ValueError("XY_AW_POINTS 超出当前信号源单波形点数上限")

print(f"DirectAW 包络扫描: {XY_ENV_VOLTAGE_LIST_V.tolist()} V")
print(f"固定输出: {XY_AW_OUTPUT_VPP:.1f} Vpp, offset={XY_AW_OUTPUT_OFFSET_V:.1f} V")
print(
    f"Z 频率扫描: {Z_RF_FREQ_LIST_Hz[0]:.0f} ~ "
    f"{Z_RF_FREQ_LIST_Hz[-1]:.0f} Hz, step={Z_RF_FREQ_STEP_Hz:.0f} Hz"
)

# %% Cell 3
# ========== 设备连接 ==========
devices = {}
session = DeviceSession()
clock_verification = {}


def reset_dg_channels(inst):
    """把 DG4000 双通道复位到安全关闭状态。"""
    for ch in (1, 2):
        inst.set_burst_state(False, channel=ch)
        inst.set_mod_state(False, channel=ch)
        inst.setup_dc(0.0, channel=ch)
        inst.set_output(False, channel=ch)


try:
    gs_cfg = MAPPING["main_magnetic_field"]
    gs = session.connect(
        "gs200", gs_cfg["resource"], lambda: GS200Instrument(gs_cfg["resource"])
    )
    gs.set_source_function(gs_cfg["source_function"])
    gs.set_current_limit(LIMITS["main_magnetic_field"]["max"] / 1000.0)
    devices["gs200"] = gs

    comp_cfg = MAPPING["X_magnetic_field"]
    dg_comp, _ = connect_signal_generator_routes(
        session,
        "dg_comp",
        {
            "x": ("X_magnetic_field", comp_cfg),
            "y": ("Y_magnetic_field", MAPPING["Y_magnetic_field"]),
        },
        logical_channels={"x": 1, "y": 2},
    )
    devices["dg_comp"] = dg_comp
    reset_dg_channels(dg_comp)

    trigger_cfg = MAPPING["X_magnetic_field_AM"]
    dg_trigger, _ = connect_signal_generator_routes(
        session,
        "dg_trigger",
        {
            "x_am": ("X_magnetic_field_AM", trigger_cfg),
            "y_am": ("Y_magnetic_field_AM", MAPPING["Y_magnetic_field_AM"]),
        },
        logical_channels={"x_am": 1, "y_am": 2},
    )
    devices["dg_trigger"] = dg_trigger
    reset_dg_channels(dg_trigger)

    sweep_cfg = MAPPING["Z_magnetic_field"]
    dg_sweep, _ = connect_signal_generator_routes(
        session,
        "dg_sweep",
        {
            "z": ("Z_magnetic_field", sweep_cfg),
            "sequence_2": ("Time_sequence_2", MAPPING["Time_sequence_2"]),
        },
        logical_channels={"z": 1, "sequence_2": 2},
    )
    devices["dg_sweep"] = dg_sweep
    reset_dg_channels(dg_sweep)

    mod_cfg = MAPPING["Pump_modulation"]
    dg_mod, _ = connect_signal_generator_routes(
        session,
        "dg_mod",
        {
            "carrier": ("Pump_modulation", mod_cfg),
            "gate": ("Time_sequence", MAPPING["Time_sequence"]),
        },
        logical_channels={"carrier": 1, "gate": 2},
    )
    devices["dg_mod"] = dg_mod
    reset_dg_channels(dg_mod)

    laser_cfg = MAPPING["Pump_laser_power"]
    dg_laser, _ = connect_signal_generator_routes(
        session,
        "dg_laser",
        {
            "pump": ("Pump_laser_power", laser_cfg),
            "probe": ("Probe_laser_power", MAPPING["Probe_laser_power"]),
        },
        logical_channels={"pump": 1, "probe": 2},
    )
    devices["dg_laser"] = dg_laser

    temp_cfg = MAPPING["Temp_Switch"]
    dg_temp, _ = connect_signal_generator_routes(
        session,
        "dg_temp",
        {"temp": ("Temp_Switch", temp_cfg)},
        logical_channels={"temp": 2},
    )
    devices["dg_temp"] = dg_temp

    tec_cfg = MAPPING["temperature"]
    tec = session.connect_optional(
        "tec",
        tec_cfg["resource"],
        lambda: TECInstrument(port=tec_cfg["resource"]),
        device_label="TEC103",
    )
    devices["tec"] = tec

    hf2_cfg = MAPPING["lockin_r"]
    hfi = session.connect(
        "hf2",
        f"hf2://{hf2_cfg.get('host', '127.0.0.1')}/{hf2_cfg['device_id']}",
        lambda: HF2Instrument(
            host=hf2_cfg.get("host", "127.0.0.1"),
            port=hf2_cfg.get("port", 8005),
            api_level=1,
            device_id=hf2_cfg["device_id"],
        ),
    )
    devices["hf2"] = hfi
    clock_verification = synchronize_connected_clocks(
        devices,
        MAPPING,
        {
            "dg_comp": "X_magnetic_field",
            "dg_trigger": "X_magnetic_field_AM",
            "dg_sweep": "Z_magnetic_field",
            "dg_mod": "Pump_modulation",
            "dg_laser": "Pump_laser_power",
            "dg_temp": "Temp_Switch",
            "hf2": "lockin_r",
        },
    )
except Exception:
    session.cleanup_connection_failure()
    raise

print(f"设备连接完成，共 {len(devices)} 个设备对象")

# %% Cell 4
# ========== DirectAW 与数据工具函数 ==========
def wrap_deg(angle_deg):
    return (float(angle_deg) + 180.0) % 360.0 - 180.0


def build_direct_aw(envelope_v, phase_deg):
    """生成恒定包络的 X/Y 正交 DirectAW 波形。"""
    envelope_v = float(envelope_v)
    phase_rad = math.radians(float(phase_deg))
    quad_rad = math.radians(XY_CTRL_QUAD_deg)
    t_s = np.arange(XY_AW_POINTS, dtype=float) / (
        XY_AW_POINTS * XY_AW_REPEAT_FREQ_Hz
    )
    carrier_phase = 2.0 * np.pi * XY_CARRIER_FREQ_Hz * t_s + phase_rad
    x_v = envelope_v * np.cos(carrier_phase)
    y_v = envelope_v * np.cos(carrier_phase + quad_rad)
    half_range = XY_AW_OUTPUT_VPP / 2.0
    x_norm = (x_v - XY_AW_OUTPUT_OFFSET_V) / half_range
    y_norm = (y_v - XY_AW_OUTPUT_OFFSET_V) / half_range
    max_abs_norm = max(float(np.max(np.abs(x_norm))), float(np.max(np.abs(y_norm))))
    if max_abs_norm > 1.0 + 1e-9:
        raise ValueError(
            f"envelope={envelope_v} V 生成的归一化波形峰值 {max_abs_norm:.6f} 超出 1"
        )
    return {
        "time_s": t_s,
        "envelope_V": envelope_v,
        "phase_deg": float(phase_deg),
        "x_waveform_V": x_v,
        "y_waveform_V": y_v,
        "x_normalized": np.clip(x_norm, -1.0, 1.0),
        "y_normalized": np.clip(y_norm, -1.0, 1.0),
    }


def configure_trigger_source():
    """打开并同相初始化 dg_trigger，之后在 DirectAW 迭代中保持连续输出。"""
    low = XY_TRIGGER_OFFSET_V - XY_TRIGGER_AMPLITUDE_Vpp / 2.0
    high = XY_TRIGGER_OFFSET_V + XY_TRIGGER_AMPLITUDE_Vpp / 2.0
    for key in ("X_magnetic_field_AM", "Y_magnetic_field_AM"):
        validate_safety_limit(key, low)
        validate_safety_limit(key, high)
    for ch in (1, 2):
        dg_trigger.set_burst_state(False, channel=ch)
        dg_trigger.set_mod_state(False, channel=ch)
        dg_trigger.setup_square(
            freq=XY_TRIGGER_FREQ_Hz,
            amplitude=XY_TRIGGER_AMPLITUDE_Vpp,
            offset=XY_TRIGGER_OFFSET_V,
            dcycle=XY_TRIGGER_DUTY_pct,
            phase=XY_TRIGGER_PHASE_deg,
            channel=ch,
        )
        dg_trigger.set_output(True, channel=ch)
    for ch in (1, 2):
        dg_trigger.phase_init(channel=ch)


def upload_direct_aw(envelope_v, phase_deg, output=True):
    """保持 dg_trigger 连续运行，重传两路 AW 后依次打开 Burst 和输出。"""
    wave = build_direct_aw(envelope_v, phase_deg)

    for ch in (1, 2):
        dg_comp.set_burst_state(False, channel=ch)
        dg_comp.set_mod_state(False, channel=ch)
        dg_comp.set_output(False, channel=ch)
    for ch, values in ((1, wave["x_normalized"]), (2, wave["y_normalized"])):
        dg_comp.setup_arbitrary(
            values.copy(),
            freq=XY_AW_REPEAT_FREQ_Hz,
            amplitude=XY_AW_OUTPUT_VPP,
            offset=XY_AW_OUTPUT_OFFSET_V,
            phase=0.0,
            channel=ch,
            output=False,
        )

    for ch in (1, 2):
        dg_comp.set_burst_state(True, channel=ch)
        dg_comp.set_burst_mode("INFinity", channel=ch)
        dg_comp.set_burst_trigger_source("EXTernal", channel=ch)
        dg_comp.set_burst_trigger_slope("POSitive", channel=ch)
        dg_comp.set_burst_phase(0.0, channel=ch)
        dg_comp.set_output(bool(output), channel=ch)
    return wave


def save_waveform_snapshot(path, wave):
    np.savez(
        path,
        time_s=wave["time_s"],
        envelope_V=np.float64(wave["envelope_V"]),
        phase_deg=np.float64(wave["phase_deg"]),
        x_waveform_V=wave["x_waveform_V"],
        y_waveform_V=wave["y_waveform_V"],
        x_normalized=wave["x_normalized"],
        y_normalized=wave["y_normalized"],
        fixed_output_vpp_V=np.float64(XY_AW_OUTPUT_VPP),
        fixed_output_offset_V=np.float64(XY_AW_OUTPUT_OFFSET_V),
        repeat_freq_Hz=np.float64(XY_AW_REPEAT_FREQ_Hz),
        carrier_freq_Hz=np.float64(XY_CARRIER_FREQ_Hz),
        quadrature_deg=np.float64(XY_CTRL_QUAD_deg),
    )


def update_json_index(path, records):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


def safe_outputs_off():
    """声明本实验需要关闭和保留的输出，交给共享安全步骤执行。"""
    return run_safety_shutdown(
        dg_channels=(
            DGChannelShutdown(dg_sweep, 1, "Z_magnetic_field", "Z 场"),
            DGChannelShutdown(dg_sweep, 2, "Time_sequence_2", "时序通道 2"),
            DGChannelShutdown(dg_comp, 1, "X_magnetic_field", "X DirectAW"),
            DGChannelShutdown(dg_comp, 2, "Y_magnetic_field", "Y DirectAW"),
            DGChannelShutdown(dg_trigger, 1, "X_magnetic_field_AM", "X 触发"),
            DGChannelShutdown(dg_trigger, 2, "Y_magnetic_field_AM", "Y 触发"),
            DGChannelShutdown(dg_mod, 2, "Time_sequence", "Pump 门控"),
        ),
        temperature_switch=TemperatureSwitchRestore(dg_temp, 2),
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),),
        preserved_outputs=STANDARD_PRESERVED_OUTPUTS,
    )


# %% Cell 5
# ========== 初始化实验状态与 HF2 ==========
dg_laser.setup_dc(FIXED_PARAMS["Pump_laser_power"], channel=1)
dg_laser.setup_dc(FIXED_PARAMS["Probe_laser_power"], channel=2)

gs.set_current(FIXED_PARAMS["main_magnetic_field"] / 1000.0)
gs.set_output(True)

dg_temp.setup_dc(FIXED_PARAMS["Temp_Switch"], channel=2)
dg_temp.set_output(True, channel=2)

temperature_status = configure_temperature_control(
    tec,
    FIXED_PARAMS["temperature"],
    channel=1,
    tolerance_c=1.0,
    stable_reads=1,
    poll_interval_s=5.0,
    timeout_s=1200.0,
)

for ch in (1, 2):
    dg_mod.set_burst_state(False, channel=ch)
    dg_mod.set_mod_state(False, channel=ch)
dg_mod.setup_sine(
    freq=100e6,
    amplitude=PUMP_MOD_AMPLITUDE_Vpp,
    offset=0.0,
    phase=0.0,
    channel=1,
)
dg_mod.setup_pulse(
    freq=PUMP_MOD_FREQ_Hz,
    amplitude=5.0,
    offset=2.5,
    channel=2,
)
dg_mod.set_pulse_dcycle(PUMP_MOD_DUTY_pct, channel=2)
dg_mod.set_sync_state(False, channel=2)

# 校相阶段关闭 DirectAW、触发、Z 场和温控磁场干扰。
for ch in (1, 2):
    dg_comp.set_output(False, channel=ch)
    dg_trigger.set_output(False, channel=ch)
dg_sweep.set_output(False, channel=1)
dg_temp.set_output(False, channel=2)
time.sleep(0.5)

sig0_cfg = SignalInputConfig(
    input_index=0,
    range=DEMOD0_SIGNAL_RANGE_V,
    ac_coupling=True,
    diff=False,
    impedance=50,
)
demod.configure_signal_input(hfi, sig0_cfg)
demod.configure_oscillator(
    hfi,
    OscillatorConfig(osc_index=DEMOD0_OSC_IDX, frequency=PUMP_MOD_FREQ_Hz),
)
actual_rate_d0_cal = demod.configure_demodulator(
    hfi,
    DemodulatorConfig(
        demod_index=DEMOD0_IDX,
        enable=True,
        rate=DEMOD0_RATE_Sa_s,
        input_channel=0,
        osc_select=DEMOD0_OSC_IDX,
        harmonic=1,
        time_constant=DEMOD0_TC_CALIB_s,
        order=DEMOD0_ORDER,
        phase=0.0,
    ),
)

sig1_cfg = SignalInputConfig(
    input_index=SIGIN2_INDEX,
    range=SIGIN2_RANGE_V,
    ac_coupling=SIGIN2_AC_COUPLING,
    diff=False,
    impedance=SIGIN2_IMPEDANCE_OHM,
)
demod.configure_signal_input(hfi, sig1_cfg)

auxout_path = hfi.aux_out_path(AUXOUT_INDEX)
auxout_outputselect = AUXOUT_SOURCE_DEMOD_IDX * 4 + AUXOUT_SOURCE_SELECT
hfi.set_int(f"{auxout_path}/outputselect", auxout_outputselect)
hfi.set_double(f"{auxout_path}/scale", AUXOUT_SCALE)
hfi.set_double(f"{auxout_path}/offset", AUXOUT_OFFSET_V)
hfi.sync()

initial_z_freq = float(Z_RF_FREQ_LIST_Hz[0])
demod.configure_oscillator(
    hfi,
    OscillatorConfig(osc_index=DEMOD3_OSC_IDX, frequency=initial_z_freq),
)
actual_rate_d3_initial = demod.configure_demodulator(
    hfi,
    DemodulatorConfig(
        demod_index=DEMOD3_IDX,
        enable=True,
        rate=DEMOD3_RATE_Sa_s,
        input_channel=DEMOD3_ADC_SELECT,
        osc_select=DEMOD3_OSC_IDX,
        harmonic=1,
        time_constant=max(
            DEMOD3_TC_MIN_s,
            DEMOD3_TC_PERIOD_FRACTION / initial_z_freq,
        ),
        order=DEMOD3_ORDER,
        phase=0.0,
    ),
)

print("执行 Demod 0 自动校相...")
demod0_phase_result = calibrate_demod_phase(
    hfi,
    PhaseCalibrationConfig(
        demod_idx=DEMOD0_IDX,
        tolerance_deg=1.0,
        max_attempts=5,
        settle_time=0.2,
    ),
)
calibrated_phase_0_deg = demod0_phase_result.phase_shift_deg

dg_temp.set_output(True, channel=2)
time.sleep(1.0)

# ---- DirectAW 整体相位校准 ----
configure_trigger_source()
xy_phase_deg = float(XY_CTRL_PHASE_INITIAL_deg)
phase_history = []
if ENABLE_XY_PHASE_CAL:
    dg_temp.set_output(False, channel=2)
    time.sleep(TEMP_SWITCH_OFF_LEAD_s)
    try:
        def apply_xy_phase_and_measure(phase_deg):
            upload_direct_aw(XY_PHASE_CAL_ENVELOPE_V, phase_deg, output=True)
            time.sleep(XY_PHASE_CAL_SETTLE_s)
            sample = demod.read_demod_sample(hfi, demod_idx=DEMOD0_IDX)
            theta_deg = wrap_deg(math.degrees(sample["theta"]))
            print(
                f"DirectAW 校相: phase={phase_deg:.3f}°, "
                f"theta={theta_deg:+.3f}°, R={sample['r']:.4e} V"
            )
            return sample

        direct_aw_phase_result = calibrate_direct_aw_phase(
            xy_phase_deg,
            apply_xy_phase_and_measure,
            DirectAWPhaseCalibrationConfig(
                tolerance_deg=XY_PHASE_CAL_TOL_deg,
                max_measurements=XY_PHASE_CAL_MAX_ITER + 1,
                minimum_r_v=XY_PHASE_CAL_MIN_R_V,
                minimum_r_ratio=XY_PHASE_CAL_MIN_R_RATIO,
            ),
            cancellation_check=check_cancelled,
        )
        xy_phase_deg = direct_aw_phase_result.final_phase_deg
        phase_history = [
            {
                "iteration": item.iteration,
                "xy_phase_deg": item.phase_deg,
                "theta_deg": item.theta_deg,
                "r_V": item.r_v,
                "minimum_r_V": item.minimum_r_v,
                "accepted": item.accepted,
                "mode": item.mode,
            }
            for item in direct_aw_phase_result.history
        ]
    finally:
        dg_temp.set_output(True, channel=2)
        time.sleep(1.0)
phase_cal_success = bool(
    direct_aw_phase_result.converged if ENABLE_XY_PHASE_CAL else False
)

actual_rate_d0_meas = demod.configure_demodulator(
    hfi,
    DemodulatorConfig(
        demod_index=DEMOD0_IDX,
        enable=True,
        rate=DEMOD0_RATE_Sa_s,
        input_channel=0,
        osc_select=DEMOD0_OSC_IDX,
        harmonic=1,
        time_constant=DEMOD0_TC_MEAS_s,
        order=DEMOD0_ORDER,
        phase=calibrated_phase_0_deg,
    ),
)

# ---- 创建运行目录并保存配置 ----
timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
raw_dir = run_dir / "raw"
results_dir = run_dir / "results"
raw_dir.mkdir(parents=True, exist_ok=True)
results_dir.mkdir(parents=True, exist_ok=True)

instrument_snapshot = instrument_config_snapshot(project_root)
config = {
    "experiment_id": "xy-direct-aw-dc-calibration",
    "schema_version": PARAMS.schema_version,
    "execution_mode": "typed_workflow",
    "parameters": PARAMS.to_external(),
    "experiment_type": EXPERIMENT_TYPE,
    "run_tag": RUN_TAG,
    "timestamp": timestamp,
    "purpose": (
        "使用 DirectAW 固定 4 Vpp/0 V offset 输出，标定恒定包络电压与实际响应峰位的映射"
    ),
    "acquisition_mode": ACQUISITION_MODE,
    "xy_direct_aw_scan": {
        "XY_ENV_VOLTAGE_LIST_V": XY_ENV_VOLTAGE_LIST_V.tolist(),
        "fixed_output_vpp_V": XY_AW_OUTPUT_VPP,
        "fixed_output_offset_V": XY_AW_OUTPUT_OFFSET_V,
        "output_range_V": [aw_low, aw_high],
        "repeat_freq_Hz": XY_AW_REPEAT_FREQ_Hz,
        "waveform_points": XY_AW_POINTS,
        "carrier_freq_Hz": XY_CARRIER_FREQ_Hz,
        "quadrature_deg": XY_CTRL_QUAD_deg,
        "calibrated_phase_deg": float(xy_phase_deg),
        "negative_voltage_note": "negative envelope is equivalent to a 180-degree rotating-field phase flip",
    },
    "xy_trigger": {
        "freq_Hz": XY_TRIGGER_FREQ_Hz,
        "amplitude_Vpp": XY_TRIGGER_AMPLITUDE_Vpp,
        "offset_V": XY_TRIGGER_OFFSET_V,
        "duty_pct": XY_TRIGGER_DUTY_pct,
        "restart_policy": (
            "trigger outputs ON and phase init once -> "
            "dg_comp CH1/CH2 upload -> burst ON -> outputs ON"
        ),
    },
    "z_frequency_scan": {
        "freq_list_Hz": Z_RF_FREQ_LIST_Hz.tolist(),
        "amplitude_Vpp": Z_RF_AMPLITUDE_Vpp,
        "settle_time_s": FREQ_SETTLE_TIME_s,
        "point_duration_s": POINT_DURATION_s,
    },
    "phase_calibration": {
        "enabled": ENABLE_XY_PHASE_CAL,
        "envelope_V": XY_PHASE_CAL_ENVELOPE_V,
        "success": phase_cal_success,
        "minimum_r_V": XY_PHASE_CAL_MIN_R_V,
        "minimum_r_ratio": XY_PHASE_CAL_MIN_R_RATIO,
        "best_r_V": (
            direct_aw_phase_result.best_r_v if ENABLE_XY_PHASE_CAL else None
        ),
        "history": phase_history,
        "demod0_calibrated_phase_deg": float(calibrated_phase_0_deg),
    },
    "hf2": {
        "demod0_actual_rate_cal_Sa_s": float(actual_rate_d0_cal),
        "demod0_actual_rate_meas_Sa_s": float(actual_rate_d0_meas),
        "demod3_initial_actual_rate_Sa_s": float(actual_rate_d3_initial),
        "physical_loopback": "Demod0 Y -> AuxOut2 -> SignalInput2 DC -> Demod3",
    },
    "pump_modulation": {
        "freq_Hz": PUMP_MOD_FREQ_Hz,
        "duty_pct": PUMP_MOD_DUTY_pct,
        "amplitude_Vpp": PUMP_MOD_AMPLITUDE_Vpp,
    },
    "fixed_params": FIXED_PARAMS,
    "clock_verification": clock_verification,
    "device_library_revision": instrument_snapshot["device_library_revision"],
    "physical_mapping_revision": instrument_snapshot["physical_mapping_revision"],
    "device_library_snapshot": instrument_snapshot["device_library"],
    "physical_mapping_snapshot": instrument_snapshot["physical_mappings"],
    "mapping_snapshot": instrument_snapshot["resolved_mapping"],
    "safety_limits_snapshot": LIMITS,
    "data_files": [],
}
config_path = run_dir / "experiment_config.yaml"
with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
print(f"运行目录: {run_dir}")

# %% Cell 6
# ========== DirectAW 包络电压 × Z 频率双层扫描 ==========
global_index_records = []
index_path = raw_dir / "frequency_index.json"

try:
    for v_idx, envelope_v in enumerate(
        tqdm(XY_ENV_VOLTAGE_LIST_V, desc="DirectAW envelope scan")
    ):
        check_cancelled()
        wave = upload_direct_aw(float(envelope_v), xy_phase_deg, output=True)
        time.sleep(XY_ENV_SETTLE_TIME_s)

        sign_tag = "m" if envelope_v < 0 else "p"
        voltage_tag = f"env_v{v_idx:02d}_{sign_tag}{abs(envelope_v):.4f}V"
        voltage_dir = raw_dir / voltage_tag
        voltage_dir.mkdir(exist_ok=True)
        save_waveform_snapshot(voltage_dir / "direct_aw_waveform.npz", wave)
        voltage_records = []

        print(
            f"\n[{v_idx + 1}/{len(XY_ENV_VOLTAGE_LIST_V)}] "
            f"DirectAW envelope={envelope_v:+.4f} V"
        )

        for z_idx, z_freq in enumerate(Z_RF_FREQ_LIST_Hz):
            check_cancelled()
            dg_sweep.set_burst_state(False, channel=1)
            dg_sweep.set_mod_state(False, channel=1)
            dg_sweep.setup_sine(
                freq=float(z_freq),
                amplitude=Z_RF_AMPLITUDE_Vpp,
                offset=0.0,
                phase=0.0,
                channel=1,
            )
            dg_sweep.set_phase_adjust(0.0, channel=1)
            dg_sweep.set_output(True, channel=1)

            point_rate = np.nan
            point_tc = np.nan
            point_adcselect = -1
            if ACQUISITION_MODE in ("demod_rxy", "both"):
                demod.configure_oscillator(
                    hfi,
                    OscillatorConfig(
                        osc_index=DEMOD3_OSC_IDX,
                        frequency=float(z_freq),
                    ),
                )
                demod.configure_demodulator(
                    hfi,
                    DemodulatorConfig(
                        demod_index=DEMOD3_IDX,
                        enable=True,
                        rate=max(
                            100.0,
                            min(DEMOD3_RATE_Sa_s, float(z_freq) * 50.0),
                        ),
                        input_channel=DEMOD3_ADC_SELECT,
                        osc_select=DEMOD3_OSC_IDX,
                        harmonic=1,
                        time_constant=max(
                            DEMOD3_TC_MIN_s,
                            DEMOD3_TC_PERIOD_FRACTION / float(z_freq),
                        ),
                        order=DEMOD3_ORDER,
                        phase=0.0,
                    ),
                )
                demod3_path = hfi.demod_path(DEMOD3_IDX)
                point_rate = hfi.get_double(f"{demod3_path}/rate")
                point_tc = hfi.get_double(f"{demod3_path}/timeconstant")
                point_adcselect = hfi.get_int(f"{demod3_path}/adcselect")

            time.sleep(FREQ_SETTLE_TIME_s)
            dg_temp.set_output(False, channel=2)
            time.sleep(TEMP_SWITCH_OFF_LEAD_s)

            point = {}
            try:
                if ACQUISITION_MODE in ("daq_y", "both"):
                    daq_cfg = DAQConfig(
                        device=MAPPING["lockin_r"]["device_id"],
                        trigger_type=0,
                        duration=POINT_DURATION_s,
                        grid_cols=int(actual_rate_d0_meas * POINT_DURATION_s),
                        grid_rows=1,
                        grid_mode=2,
                        signal_paths=["sample.y"],
                    )
                    daq_results = daq.acquire_data(
                        hfi,
                        config=daq_cfg,
                        demod_idx=DEMOD0_IDX,
                        actual_rate=actual_rate_d0_meas,
                        timeout=POINT_DURATION_s + 5.0,
                    )
                    y_signal = next(
                        (res.values for res in daq_results if res.signal_name == "sample.y"),
                        None,
                    )
                    if y_signal is not None:
                        point["y_V"] = np.asarray(y_signal, dtype=float)
                        point["time_s"] = np.asarray(daq_results[0].time, dtype=float)

                if ACQUISITION_MODE in ("demod_rxy", "both"):
                    time.sleep(DEMOD3_READ_SETTLE_TIME_s)
                    samples = []
                    for _ in range(DEMOD3_N_AVG):
                        samples.append(
                            demod.read_demod_sample(hfi, demod_idx=DEMOD3_IDX)
                        )
                        time.sleep(DEMOD3_AVG_INTERVAL_s)
                    x_arr = np.asarray([sample["x"] for sample in samples], dtype=float)
                    y_arr = np.asarray([sample["y"] for sample in samples], dtype=float)
                    r_arr = np.asarray([sample["r"] for sample in samples], dtype=float)
                    theta_arr = np.asarray(
                        [sample["theta"] for sample in samples], dtype=float
                    )
                    x_mean = float(np.mean(x_arr))
                    y_mean = float(np.mean(y_arr))
                    point.update(
                        {
                            "demod3_x_values_V": x_arr,
                            "demod3_y_values_V": y_arr,
                            "demod3_r_values_V": r_arr,
                            "demod3_theta_values_rad": theta_arr,
                            "demod3_x_mean_V": np.float64(x_mean),
                            "demod3_y_mean_V": np.float64(y_mean),
                            "demod3_r_vector_mean_V": np.float64(
                                math.hypot(x_mean, y_mean)
                            ),
                            "demod3_r_scalar_mean_V": np.float64(np.mean(r_arr)),
                            "demod3_r_scalar_std_V": np.float64(np.std(r_arr)),
                            "demod3_phase_vector_deg": np.float64(
                                math.degrees(math.atan2(y_mean, x_mean))
                            ),
                        }
                    )
            finally:
                dg_temp.set_output(True, channel=2)
                time.sleep(TEMP_SWITCH_ON_LAG_s)

            filename = f"zfreq_{z_idx:04d}_{z_freq:.3f}Hz.npz"
            save_dict = {
                "xy_envelope_voltage_V": np.float64(envelope_v),
                "z_freq_Hz": np.float64(z_freq),
                "z_drive_amplitude_Vpp": np.float64(Z_RF_AMPLITUDE_Vpp),
                "acquisition_mode": np.array(ACQUISITION_MODE),
                "actual_rate_Sa_s": np.float64(actual_rate_d0_meas),
                "demod3_actual_rate_Sa_s": np.float64(point_rate),
                "demod3_actual_tc_s": np.float64(point_tc),
                "demod3_adcselect_actual": np.int32(point_adcselect),
                **point,
            }
            np.savez(voltage_dir / filename, **save_dict)

            record = {
                "v_idx": int(v_idx),
                "xy_envelope_voltage_V": float(envelope_v),
                "z_idx": int(z_idx),
                "z_freq_Hz": float(z_freq),
                "subdir": voltage_tag,
                "file": filename,
            }
            voltage_records.append(record)
            global_index_records.append(record)
            update_json_index(voltage_dir / "frequency_index.json", voltage_records)
            update_json_index(index_path, global_index_records)

            if z_idx == 0 or (z_idx + 1) % 10 == 0 or z_idx == len(Z_RF_FREQ_LIST_Hz) - 1:
                hw_r = point.get("demod3_r_vector_mean_V", np.nan)
                print(
                    f"  [{z_idx + 1}/{len(Z_RF_FREQ_LIST_Hz)}] "
                    f"f={z_freq:.0f} Hz, HW R={float(hw_r):.4e} V"
                )

        np.savez(
            voltage_dir / "frequency_index.npz",
            records=np.array(voltage_records, dtype=object),
        )

    np.savez(
        raw_dir / "frequency_index.npz",
        records=np.array(global_index_records, dtype=object),
    )
    config["data_files"] = [
        "raw/frequency_index.json",
        "raw/frequency_index.npz",
    ]
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    print("DirectAW DC 标定采集完成")
finally:
    shutdown_report = safe_outputs_off()
    if shutdown_report.errors:
        print("安全关闭警告: " + "；".join(shutdown_report.errors))
print("其他设备保持连接")
