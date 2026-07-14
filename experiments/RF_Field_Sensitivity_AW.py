# %% [markdown] Cell 0
# # 射频场灵敏度测量采集脚本（dg_comp 直接任意波）
#
# 从 RF_Field_Sensitivity_AW.ipynb 拆分出的采集入口。
# 本脚本只负责仪器连接、相位校准、Z RF 幅度响应扫描和 Demod3 噪声采集。
# 离线拟合、PSD 和灵敏度绘图请运行 RF_Field_Sensitivity_AW_plot.py。

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

import matplotlib
matplotlib.use("TkAgg")
import numpy as np
import yaml

from gs200 import GS200Instrument
from signal_generator import DG4000Instrument, DG900Instrument
from tec_controller import TECInstrument
from lockin_amplifier import (
    HF2Instrument,
    DAQConfig,
    SignalInputConfig,
    OscillatorConfig,
    DemodulatorConfig,
    demod,
    daq,
)

print("所有库导入成功")

# %% Cell 2
with open(project_root / "params" / "mapping.yaml", encoding="utf-8") as f:
    MAPPING = yaml.safe_load(f)["mapping"]

with open(project_root / "params" / "safety_limits.yaml", encoding="utf-8") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]

EXPERIMENT_TYPE = "RF_Field_Sensitivity_AW"
RUN_TAG = "rf_sens_aw"
PURPOSE = "RF field sensitivity with direct X/Y arbitrary waveforms"

# ---- 响应曲线扫描参数（Phase A）----
RF_AMP_START = -0.2
RF_AMP_STOP = 0.2
RF_AMP_POINTS = 81
RF_SETTLE_TIME = 0.3
RESPONSE_N_AVG = 5
RESPONSE_AVG_INTERVAL = 0.2

# ---- 噪声采集参数（Phase B）----
NOISE_N_AVG = 10
NOISE_DURATION = 1.0
NOISE_RATE = 100000
NOISE_NPERSEG = 10000

# ---- X/Y 直接任意波控制 ----
ARB_WAVEFORM_FILE = "control_waveform.csv"
AW_REPEAT_FREQ = 2000.0
# Omega_Ctrl = XY_CTRL_K_HZ_PER_V * V + XY_CTRL_B_HZ
XY_CTRL_K_HZ_PER_V = 7508.36
XY_CTRL_B_HZ = -58.77
XY_AW_OUTPUT_VPP = 4.0
XY_AW_OUTPUT_OFFSET = 0.0
XY_CTRL_PHASE = 0.0
XY_CTRL_QUAD = 90.0
XY_CTRL_PHASE_TOL_DEG = 1.0
XY_CTRL_PHASE_MAX_ITER = 8
XY_PHASE_SETTLE_TIME = 0.8

# dg_am 只作为 dg_comp 外触发源。
XY_TRIGGER_FREQ = 100.0
XY_TRIGGER_AMPLITUDE = 5.0
XY_TRIGGER_OFFSET = 2.5
XY_TRIGGER_DUTY = 50.0
XY_TRIGGER_PHASE = 0.0

# ---- Z 射频场参数（dg_sweep CH1 Burst）----
Z_RF_FREQ = 8000.0
Z_RF_AMPLITUDE_INIT = 0.1
Z_V_TO_NT = 3517 / 2
Z_V_TO_FT = Z_V_TO_NT * 1_000_000
Z_BURST_PHASE_INIT = 0.0

# dg_sweep CH2 作为 dg_sweep CH1 的外触发源。
Z_TRIGGER_FREQ = 100.0
Z_TRIGGER_AMPLITUDE = 5.0
Z_TRIGGER_OFFSET = 2.5
Z_TRIGGER_DUTY = 50.0
Z_TRIGGER_PHASE = 0.0

# ---- Pump 调制参数 ----
PUMP_MOD_FREQ = 10000.0
PUMP_MOD_DUTY = 5.0
PUMP_MOD_AMPLITUDE = 0.18
RF_GATE_AMPLITUDE = 5.0
RF_GATE_OFFSET = 2.5

# ---- HF2 解调器 0 配置（主信号解调）----
DEMOD0_IDX = 0
DEMOD0_OSC_IDX = 0
DEMOD0_OSC_FREQ = PUMP_MOD_FREQ
DEMOD0_SIGNAL_RANGE = 2.0
DEMOD0_ORDER = 4
DEMOD0_TC_CALIB = 0.001
DEMOD0_TC_MEAS = 1e-6
DEMOD0_RATE = 100000

# ---- HF2 解调器 3 配置（射频场解调）----
DEMOD3_IDX = 3
DEMOD3_OSC_IDX = 1
DEMOD3_ADC_SELECT = 2
DEMOD3_ORDER = 8
DEMOD3_TC = 0.009576194757015027
DEMOD3_RATE = 1000
DEMOD3_NOISE_TC = 1e-5
DEMOD3_NOISE_ORDER = 4
DEMOD3_NOISE_RATE = NOISE_RATE

# ---- 固定参数 ----
FIXED_PARAMS = {
    "Pump_laser_power": 0.5,
    "Probe_laser_power": 0.3,
    "main_magnetic_field": 1.03,
    "temperature": 100,
    "Temp_Switch": 5.0,
}


def validate_safety_limit(name, value):
    """检查数值是否在安全范围内，超出则报错。"""
    lim = LIMITS.get(name)
    if lim is None:
        return value
    lo = lim.get("min")
    hi = lim.get("max")
    if lo is not None and value < lo:
        raise ValueError(f"[安全拦截] {name}={value} 低于下限 {lo}")
    if hi is not None and value > hi:
        raise ValueError(f"[安全拦截] {name}={value} 高于上限 {hi}")
    return value

def wrap_deg(angle_deg):
    """把角度压到 [-180, 180) 区间。"""
    return (float(angle_deg) + 180.0) % 360.0 - 180.0


for key, value in FIXED_PARAMS.items():
    validate_safety_limit(key, value)
validate_safety_limit("Z_magnetic_field", abs(RF_AMP_START))
validate_safety_limit("Z_magnetic_field", abs(RF_AMP_STOP))
validate_safety_limit("rf_coil", 0.0)

print("配置已加载")

# %% Cell 3
devices = {}

try:
    gs_cfg = MAPPING["main_magnetic_field"]
    gs = GS200Instrument(gs_cfg["resource"])
    gs.connect()
    print(f"GS200 已连接: {gs.idn()}")
    gs.set_source_function(gs_cfg["source_function"])
    gs.set_current_limit(LIMITS["main_magnetic_field"]["max"] / 1000.0)
    devices["gs200"] = gs

    dg_comp_cfg = MAPPING["X_magnetic_field"]
    dg_comp = DG4000Instrument(dg_comp_cfg["resource"], channel=1)
    dg_comp.connect()
    print(f"补偿场 DG4000 已连接: {dg_comp.idn()}")
    dg_comp.set_ref_clock_source("EXTernal")
    for ch in (1, 2):
        dg_comp.set_burst_state(False, channel=ch)
        dg_comp.set_mod_state(False, channel=ch)
        dg_comp.setup_dc(0.0, channel=ch)
        dg_comp.set_output(False, channel=ch)
    devices["dg_comp"] = dg_comp

    dg_am_cfg = MAPPING["X_magnetic_field_AM"]
    dg_am = DG4000Instrument(dg_am_cfg["resource"], channel=1)
    dg_am.connect()
    print(f"dg_am 已连接: {dg_am.idn()}")
    dg_am.set_ref_clock_source("EXTernal")
    for ch in (1, 2):
        dg_am.set_burst_state(False, channel=ch)
        dg_am.set_mod_state(False, channel=ch)
        dg_am.setup_dc(0.0, channel=ch)
        dg_am.set_output(False, channel=ch)
    devices["dg_am"] = dg_am

    dg_sweep_cfg = MAPPING["Z_magnetic_field"]
    dg_sweep = DG4000Instrument(dg_sweep_cfg["resource"], channel=1)
    dg_sweep.connect()
    print(f"dg_sweep DG4000 已连接: {dg_sweep.idn()}")
    dg_sweep.set_ref_clock_source("EXTernal")
    for ch in (1, 2):
        dg_sweep.set_burst_state(False, channel=ch)
        dg_sweep.set_mod_state(False, channel=ch)
        dg_sweep.setup_dc(0.0, channel=ch)
        dg_sweep.set_output(False, channel=ch)
    devices["dg_sweep"] = dg_sweep

    dg_mod_cfg = MAPPING["Pump_modulation"]
    dg_mod = DG4000Instrument(dg_mod_cfg["resource"], channel=1)
    dg_mod.connect()
    print(f"调制 DG4000 已连接: {dg_mod.idn()}")
    devices["dg_mod"] = dg_mod

    dg_temp_cfg = MAPPING["Temp_Switch"]
    dg_temp = DG900Instrument(dg_temp_cfg["resource"], channel=2)
    dg_temp.connect()
    print(f"温控开关 DG900 已连接: {dg_temp.idn()}")
    devices["dg_temp"] = dg_temp

    tec_cfg = MAPPING["temperature"]
    tec = TECInstrument(port=tec_cfg["resource"])
    tec.connect()
    print("TEC103 已连接")
    devices["tec"] = tec

    dg_laser_cfg = MAPPING["Pump_laser_power"]
    dg_laser = DG900Instrument(dg_laser_cfg["resource"], channel=1)
    dg_laser.connect()
    print(f"光功率 DG900 已连接: {dg_laser.idn()}")
    devices["dg_laser"] = dg_laser

    hf2_cfg = MAPPING["lockin_r"]
    hfi = HF2Instrument(
        host=hf2_cfg.get("host", "127.0.0.1"),
        port=hf2_cfg.get("port", 8005),
        api_level=1,
        device_id=hf2_cfg["device_id"],
    )
    hfi.connect()
    print(f"HF2 已连接: {hfi.idn}")
    hfi.set_extclk(True)
    devices["hf2"] = hfi

except Exception as exc:
    print(f"设备连接失败: {exc}")
    for dev in set(d for d in devices.values() if d is not None):
        try:
            if hasattr(dev, "disconnect"):
                dev.disconnect()
        except Exception:
            pass
    raise

print(f"\n所有设备连接完成，共 {len(devices)} 个设备")

# %% Cell 4
hfi = devices["hf2"]
tec = devices["tec"]
gs = devices["gs200"]
dg_laser = devices["dg_laser"]
dg_comp = devices["dg_comp"]
dg_sweep = devices["dg_sweep"]
dg_am = devices["dg_am"]
dg_mod = devices["dg_mod"]
dg_temp = devices["dg_temp"]

validate_safety_limit("Pump_laser_power", FIXED_PARAMS["Pump_laser_power"])
dg_laser.setup_dc(FIXED_PARAMS["Pump_laser_power"], channel=1)
print(f"Pump 光功率: {FIXED_PARAMS['Pump_laser_power']} V DC")

validate_safety_limit("Probe_laser_power", FIXED_PARAMS["Probe_laser_power"])
dg_laser.setup_dc(FIXED_PARAMS["Probe_laser_power"], channel=2)
print(f"Probe 光功率: {FIXED_PARAMS['Probe_laser_power']} V DC")

validate_safety_limit("main_magnetic_field", FIXED_PARAMS["main_magnetic_field"])
gs.set_current(FIXED_PARAMS["main_magnetic_field"] / 1000.0)
gs.set_output(True)
print(f"主磁场: {FIXED_PARAMS['main_magnetic_field']} mA")

validate_safety_limit("Temp_Switch", FIXED_PARAMS["Temp_Switch"])
dg_temp.setup_dc(FIXED_PARAMS["Temp_Switch"], channel=2)
dg_temp.set_output(True, channel=2)
print(f"温度开关: ON ({FIXED_PARAMS['Temp_Switch']} V)")

validate_safety_limit("temperature", FIXED_PARAMS["temperature"])
tec.set_target_temperature(FIXED_PARAMS["temperature"], channel=1)
tec.set_enable(True, channel=1)
temp_now = tec.get_temperature(channel=1)
print(f"温度设定: {FIXED_PARAMS['temperature']} °C, 当前: {temp_now:.1f} °C")
print("等待温度稳定...")
while True:
    time.sleep(5)
    temp_now = tec.get_temperature(channel=1)
    print(f"  当前温度: {temp_now:.2f} °C")
    if abs(temp_now - FIXED_PARAMS["temperature"]) < 1:
        print(f"温度已稳定: {temp_now:.2f} °C")
        break

print("\n--- Pump 调制配置 ---")
for ch in (1, 2):
    dg_mod.set_burst_state(False, channel=ch)
    dg_mod.set_mod_state(False, channel=ch)

validate_safety_limit("Pump_modulation", PUMP_MOD_AMPLITUDE)
dg_mod.setup_sine(
    freq=100e6, amplitude=PUMP_MOD_AMPLITUDE,
    offset=0.0, phase=0.0, channel=1,
)
print(f"CH1: 100MHz 正弦, {PUMP_MOD_AMPLITUDE * 1000:.0f} mVpp")

validate_safety_limit("Time_sequence", RF_GATE_AMPLITUDE)
pulse_width = (PUMP_MOD_DUTY / 100.0) / PUMP_MOD_FREQ
dg_mod.setup_pulse(
    freq=PUMP_MOD_FREQ, amplitude=RF_GATE_AMPLITUDE,
    offset=RF_GATE_OFFSET, width=pulse_width, channel=2,
)
dg_mod.set_pulse_dcycle(PUMP_MOD_DUTY, channel=2)
dg_mod.set_output(True, channel=1)
dg_mod.set_output(True, channel=2)
dg_mod.set_sync_state(True, channel=2)
print(f"CH2: {PUMP_MOD_FREQ / 1000:.0f} kHz 脉冲, duty={PUMP_MOD_DUTY}%, SYNC ON")

# %% Cell 5
timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
raw_dir = run_dir / "raw"
results_dir = run_dir / "results"
raw_dir.mkdir(parents=True, exist_ok=True)
results_dir.mkdir(parents=True, exist_ok=True)

config = {
    "experiment_type": EXPERIMENT_TYPE,
    "purpose": PURPOSE,
    "timestamp": timestamp,
    "run_tag": RUN_TAG,
    "scan_params": {
        "RF_AMP_START_V": RF_AMP_START,
        "RF_AMP_STOP_V": RF_AMP_STOP,
        "RF_AMP_POINTS": RF_AMP_POINTS,
        "RF_SETTLE_TIME_s": RF_SETTLE_TIME,
        "RESPONSE_N_AVG": RESPONSE_N_AVG,
    },
    "noise_params": {
        "NOISE_N_AVG": NOISE_N_AVG,
        "NOISE_DURATION_s": NOISE_DURATION,
        "NOISE_RATE_Sa_s": NOISE_RATE,
        "NOISE_NPERSEG": NOISE_NPERSEG,
    },
    "xy_direct_aw": {
        "ARB_WAVEFORM_FILE": ARB_WAVEFORM_FILE,
        "AW_REPEAT_FREQ_Hz": AW_REPEAT_FREQ,
        "XY_CTRL_K_HZ_PER_V": XY_CTRL_K_HZ_PER_V,
        "XY_CTRL_B_HZ": XY_CTRL_B_HZ,
        "XY_AW_OUTPUT_VPP": XY_AW_OUTPUT_VPP,
        "XY_AW_OUTPUT_OFFSET_V": XY_AW_OUTPUT_OFFSET,
        "XY_CTRL_PHASE_initial_deg": XY_CTRL_PHASE,
        "XY_CTRL_QUAD_deg": XY_CTRL_QUAD,
        "PUMP_MOD_FREQ_Hz": PUMP_MOD_FREQ,
    },
    "xy_trigger": {
        "source": "dg_am CH1/CH2 fixed square -> dg_comp Ext Trig",
        "XY_TRIGGER_FREQ_Hz": XY_TRIGGER_FREQ,
        "XY_TRIGGER_AMPLITUDE_Vpp": XY_TRIGGER_AMPLITUDE,
        "XY_TRIGGER_OFFSET_V": XY_TRIGGER_OFFSET,
        "XY_TRIGGER_DUTY_pct": XY_TRIGGER_DUTY,
        "XY_TRIGGER_PHASE_deg": XY_TRIGGER_PHASE,
    },
    "z_rf_field": {
        "Z_RF_FREQ_Hz": Z_RF_FREQ,
        "Z_V_TO_NT": Z_V_TO_NT,
        "Z_RF_AMPLITUDE_INIT_Vpp": Z_RF_AMPLITUDE_INIT,
    },
    "z_trigger": {
        "source": "dg_sweep CH2 fixed square -> dg_sweep CH1 Ext Trig",
        "Z_TRIGGER_FREQ_Hz": Z_TRIGGER_FREQ,
        "Z_TRIGGER_AMPLITUDE_Vpp": Z_TRIGGER_AMPLITUDE,
        "Z_TRIGGER_OFFSET_V": Z_TRIGGER_OFFSET,
        "Z_TRIGGER_DUTY_pct": Z_TRIGGER_DUTY,
        "Z_TRIGGER_PHASE_deg": Z_TRIGGER_PHASE,
    },
    "pump_modulation": {
        "PUMP_MOD_FREQ_Hz": PUMP_MOD_FREQ,
        "PUMP_MOD_DUTY_pct": PUMP_MOD_DUTY,
        "PUMP_MOD_AMPLITUDE_Vpp": PUMP_MOD_AMPLITUDE,
        "RF_GATE_AMPLITUDE_Vpp": RF_GATE_AMPLITUDE,
        "RF_GATE_OFFSET_V": RF_GATE_OFFSET,
    },
    "hf2_demod0": {
        "demod_idx": DEMOD0_IDX,
        "osc_idx": DEMOD0_OSC_IDX,
        "osc_freq_Hz": DEMOD0_OSC_FREQ,
        "signal_range_V": DEMOD0_SIGNAL_RANGE,
        "rate_Sa_s": DEMOD0_RATE,
        "TC_calib_s": DEMOD0_TC_CALIB,
        "TC_meas_s": DEMOD0_TC_MEAS,
        "order": DEMOD0_ORDER,
    },
    "hf2_demod3": {
        "demod_idx": DEMOD3_IDX,
        "osc_idx": DEMOD3_OSC_IDX,
        "adcselect": DEMOD3_ADC_SELECT,
        "TC_response_s": DEMOD3_TC,
        "rate_response_Sa_s": DEMOD3_RATE,
        "TC_noise_s": DEMOD3_NOISE_TC,
        "rate_noise_Sa_s": DEMOD3_NOISE_RATE,
        "order": DEMOD3_ORDER,
    },
    "fixed_params": FIXED_PARAMS,
    "mapping_snapshot": MAPPING,
    "safety_limits_snapshot": LIMITS,
    "actual_rates": {},
    "data_files": [],
}

config_path = run_dir / "experiment_config.yaml"
with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

print(f"运行目录: {run_dir}")
print(f"实验配置已保存: {config_path}")

# %% Cell 6
def build_aw_params(v_waveform):
    """按固定 Vpp/offset 把物理电压波形转换为 DG arbitrary 参数。"""
    v_waveform = np.asarray(v_waveform, dtype=float)
    v_min = float(np.min(v_waveform))
    v_max = float(np.max(v_waveform))
    vpp = float(XY_AW_OUTPUT_VPP)
    offset = float(XY_AW_OUTPUT_OFFSET)
    half_range = 0.5 * vpp
    if half_range <= 0:
        raise ValueError("XY_AW_OUTPUT_VPP 必须大于 0")

    normalized = (v_waveform - offset) / half_range
    max_abs_normalized = float(np.max(np.abs(normalized)))
    if max_abs_normalized > 1.0 + 1e-9:
        raise ValueError(
            f"X/Y AW 目标物理电压 [{v_min:+.6f}, {v_max:+.6f}] V "
            f"超出固定输出范围 [{offset - half_range:+.6f}, {offset + half_range:+.6f}] V；"
            "请增大 XY_AW_OUTPUT_VPP 或降低 Omega_ctrl"
        )

    return {
        "waveform_V": v_waveform,
        "normalized": np.clip(normalized, -1.0, 1.0),
        "vpp": float(vpp),
        "offset": float(offset),
        "min": v_min,
        "max": v_max,
        "output_min": float(offset - half_range),
        "output_max": float(offset + half_range),
        "max_abs_normalized": max_abs_normalized,
    }


def load_control_envelope():
    """加载 Omega_ctrl(t)，并重采样到 AW 公共重复周期。"""
    waveform_path = project_root / "experiments" / ARB_WAVEFORM_FILE
    data = np.loadtxt(waveform_path, delimiter=",", skiprows=1)
    if data.ndim != 2 or data.shape[1] < 2:
        raise ValueError(f"{waveform_path} 需要至少两列: time_s, Omega_ctrl_Hz")
    time_s = data[:, 0].astype(float)
    omega_ctrl_hz = data[:, 1].astype(float)
    if len(time_s) < 2:
        raise ValueError("控制波形至少需要 2 个采样点")

    dt = float(np.median(np.diff(time_s)))
    source_period_s = float((time_s[-1] - time_s[0]) + dt)
    source_repeat_freq = 1.0 / source_period_s
    aw_period_s = 1.0 / AW_REPEAT_FREQ
    n_aw_points = int(round(aw_period_s / dt))
    if n_aw_points < 2 or n_aw_points > DG4000Instrument.MAX_ARB_POINTS:
        raise ValueError(
            f"AW 公共周期点数 {n_aw_points} 超出 DG4000 限制 "
            f"2~{DG4000Instrument.MAX_ARB_POINTS}；请调整 AW_REPEAT_FREQ 或 CSV 采样间隔"
        )

    actual_aw_period_s = n_aw_points * dt
    actual_aw_repeat_freq = 1.0 / actual_aw_period_s
    if abs(actual_aw_repeat_freq - AW_REPEAT_FREQ) / AW_REPEAT_FREQ > 1e-6:
        raise ValueError(
            f"AW_REPEAT_FREQ={AW_REPEAT_FREQ} Hz 与 CSV dt={dt:g} s 不能形成整数点周期；"
            f"最近可实现频率为 {actual_aw_repeat_freq:.9g} Hz"
        )

    control_cycles = source_repeat_freq / AW_REPEAT_FREQ
    larmor_cycles = PUMP_MOD_FREQ / AW_REPEAT_FREQ
    if abs(control_cycles - round(control_cycles)) > 1e-6:
        raise ValueError(
            f"控制包络频率 {source_repeat_freq:.9g} Hz 不是 AW_REPEAT_FREQ={AW_REPEAT_FREQ} Hz 的整数倍"
        )
    if abs(larmor_cycles - round(larmor_cycles)) > 1e-6:
        raise ValueError(
            f"Pump/Larmor 频率 {PUMP_MOD_FREQ:.9g} Hz 不是 AW_REPEAT_FREQ={AW_REPEAT_FREQ} Hz 的整数倍"
        )

    target_time_s = np.arange(n_aw_points, dtype=float) * dt
    source_t0 = float(time_s[0])
    source_rel_t = time_s - source_t0
    target_rel_t = target_time_s % source_period_s
    interp_t = np.r_[source_rel_t, source_period_s]
    interp_omega = np.r_[omega_ctrl_hz, omega_ctrl_hz[0]]
    target_omega_ctrl_hz = np.interp(target_rel_t, interp_t, interp_omega)
    envelope_v = (target_omega_ctrl_hz - XY_CTRL_B_HZ) / XY_CTRL_K_HZ_PER_V
    return {
        "path": waveform_path,
        "time_s": target_time_s,
        "omega_ctrl_hz": target_omega_ctrl_hz,
        "envelope_v": envelope_v,
        "dt_s": dt,
        "source_time_s": time_s,
        "source_omega_ctrl_hz": omega_ctrl_hz,
        "source_period_s": source_period_s,
        "source_repeat_freq_hz": source_repeat_freq,
        "period_s": actual_aw_period_s,
        "repeat_freq_hz": actual_aw_repeat_freq,
        "requested_repeat_freq_hz": AW_REPEAT_FREQ,
        "control_cycles_per_aw": int(round(control_cycles)),
        "larmor_cycles_per_aw": int(round(larmor_cycles)),
    }


def synthesize_xy_waveforms(phase_deg):
    """按固定 90° 正交关系生成 X/Y 直接任意波。"""
    aw = load_control_envelope()
    phase_rad = math.radians(float(phase_deg))
    quad_rad = math.radians(XY_CTRL_QUAD)
    carrier_phase = 2.0 * np.pi * PUMP_MOD_FREQ * aw["time_s"] + phase_rad
    x_wave = aw["envelope_v"] * np.cos(carrier_phase)
    y_wave = aw["envelope_v"] * np.cos(carrier_phase + quad_rad)
    x_aw = build_aw_params(x_wave)
    y_aw = build_aw_params(y_wave)
    return aw, x_aw, y_aw


def validate_xy_waveform_safety(x_aw, y_aw):
    """检查直接输出到 X/Y 线圈的 AW 电压范围。"""
    for value in (x_aw["min"], x_aw["max"], x_aw["offset"], x_aw["output_min"], x_aw["output_max"]):
        validate_safety_limit("X_magnetic_field", value)
    for value in (y_aw["min"], y_aw["max"], y_aw["offset"], y_aw["output_min"], y_aw["output_max"]):
        validate_safety_limit("Y_magnetic_field", value)


def upload_xy_aw(phase_deg, outputs_on=True):
    """重新合成并上传 X/Y AW，返回保存用的波形参数。"""
    aw, x_aw, y_aw = synthesize_xy_waveforms(phase_deg)
    validate_xy_waveform_safety(x_aw, y_aw)

    for ch in (1, 2):
        dg_comp.set_burst_state(False, channel=ch)
        dg_comp.set_mod_state(False, channel=ch)
        dg_comp.set_output(False, channel=ch)

    dg_comp.setup_arbitrary(
        x_aw["normalized"].copy(),
        freq=aw["repeat_freq_hz"],
        amplitude=x_aw["vpp"],
        offset=x_aw["offset"],
        phase=0.0,
        channel=1,
        output=False,
    )
    dg_comp.setup_arbitrary(
        y_aw["normalized"].copy(),
        freq=aw["repeat_freq_hz"],
        amplitude=y_aw["vpp"],
        offset=y_aw["offset"],
        phase=0.0,
        channel=2,
        output=False,
    )

    for ch in (1, 2):
        dg_comp.set_burst_state(True, channel=ch)
        dg_comp.set_burst_mode("INFinity", channel=ch)
        dg_comp.set_burst_ncycles(50000, channel=ch)
        dg_comp.set_burst_trigger_source("EXTernal", channel=ch)
        dg_comp.set_burst_trigger_slope("POSitive", channel=ch)
        dg_comp.set_burst_phase(0.0, channel=ch)
        dg_comp.set_output(bool(outputs_on), channel=ch)

    print(
        f"X/Y AW 已上传: repeat={aw['repeat_freq_hz']:.3f} Hz, "
        f"phase={phase_deg:.2f}°, X={x_aw['vpp']:.3f} Vpp, Y={y_aw['vpp']:.3f} Vpp"
    )
    return aw, x_aw, y_aw


def validate_trigger_square_levels():
    half_amp = XY_TRIGGER_AMPLITUDE / 2.0
    low_level = XY_TRIGGER_OFFSET - half_amp
    high_level = XY_TRIGGER_OFFSET + half_amp
    validate_safety_limit("X_magnetic_field_AM", low_level)
    validate_safety_limit("X_magnetic_field_AM", high_level)
    validate_safety_limit("Y_magnetic_field_AM", low_level)
    validate_safety_limit("Y_magnetic_field_AM", high_level)


def validate_z_trigger_square_levels():
    half_amp = Z_TRIGGER_AMPLITUDE / 2.0
    low_level = Z_TRIGGER_OFFSET - half_amp
    high_level = Z_TRIGGER_OFFSET + half_amp
    validate_safety_limit("Time_sequence_2", low_level)
    validate_safety_limit("Time_sequence_2", high_level)


def configure_dg_am_reference_trigger():
    """设置 dg_am CH1/CH2 为固定同相方波，作为 dg_comp 外触发。"""
    validate_trigger_square_levels()
    for ch in (1, 2):
        dg_am.set_burst_state(False, channel=ch)
        dg_am.set_mod_state(False, channel=ch)
        dg_am.setup_square(
            freq=XY_TRIGGER_FREQ,
            amplitude=XY_TRIGGER_AMPLITUDE,
            offset=XY_TRIGGER_OFFSET,
            dcycle=XY_TRIGGER_DUTY,
            phase=XY_TRIGGER_PHASE,
            channel=ch,
        )
        dg_am.set_output(True, channel=ch)
    for ch in (1, 2):
        dg_am.phase_init(channel=ch)
    print(
        f"dg_am CH1/CH2: fixed square {XY_TRIGGER_FREQ} Hz, "
        "outputs ON -> dg_comp Ext Trig"
    )


def configure_dg_sweep_reference_trigger():
    """设置 dg_sweep CH2 为固定方波，作为 dg_sweep CH1 外触发。"""
    validate_z_trigger_square_levels()
    dg_sweep.set_burst_state(False, channel=2)
    dg_sweep.set_mod_state(False, channel=2)
    dg_sweep.setup_square(
        freq=Z_TRIGGER_FREQ,
        amplitude=Z_TRIGGER_AMPLITUDE,
        offset=Z_TRIGGER_OFFSET,
        dcycle=Z_TRIGGER_DUTY,
        phase=Z_TRIGGER_PHASE,
        channel=2,
    )
    dg_sweep.set_output(True, channel=2)
    dg_sweep.phase_init(channel=2)
    print(
        f"dg_sweep CH2: fixed square {Z_TRIGGER_FREQ} Hz, "
        "output ON -> dg_sweep CH1 Ext Trig"
    )


def arm_z_rf_burst(amplitude_v, phase_deg):
    """重新 arm dg_sweep CH1 Burst，使 0/180° 相位对当前扫描点生效。"""
    validate_safety_limit("Z_magnetic_field", abs(float(amplitude_v)))
    dg_sweep.set_output(False, channel=1)
    dg_sweep.set_burst_state(False, channel=1)
    dg_sweep.set_amplitude(abs(float(amplitude_v)), channel=1)
    dg_sweep.set_burst_phase(float(phase_deg) % 360.0, channel=1)
    dg_sweep.set_burst_state(True, channel=1)
    dg_sweep.set_output(True, channel=1)
    # CH1 使用 EXT trigger；等待 CH2 固定方波给出新的上升沿。
    time.sleep(1.5 / Z_TRIGGER_FREQ)


def read_demod_theta_deg(demod_idx=DEMOD0_IDX):
    sample = demod.read_demod_sample(hfi, demod_idx=demod_idx)
    theta_deg = float(math.degrees(sample["theta"]))
    return wrap_deg(theta_deg), sample


configure_dg_am_reference_trigger()
aw, x_aw, y_aw = upload_xy_aw(XY_CTRL_PHASE, outputs_on=False)

np.savez(
    raw_dir / "xy_aw_waveforms_initial.npz",
    time_s=aw["time_s"],
    omega_ctrl_Hz=aw["omega_ctrl_hz"],
    envelope_V=aw["envelope_v"],
    x_waveform_V=x_aw["waveform_V"],
    y_waveform_V=y_aw["waveform_V"],
    x_normalized=x_aw["normalized"],
    y_normalized=y_aw["normalized"],
    x_vpp=np.float64(x_aw["vpp"]),
    y_vpp=np.float64(y_aw["vpp"]),
    x_offset=np.float64(x_aw["offset"]),
    y_offset=np.float64(y_aw["offset"]),
    x_output_min=np.float64(x_aw["output_min"]),
    x_output_max=np.float64(x_aw["output_max"]),
    y_output_min=np.float64(y_aw["output_min"]),
    y_output_max=np.float64(y_aw["output_max"]),
    x_max_abs_normalized=np.float64(x_aw["max_abs_normalized"]),
    y_max_abs_normalized=np.float64(y_aw["max_abs_normalized"]),
    repeat_freq_Hz=np.float64(aw["repeat_freq_hz"]),
    xy_ctrl_phase_deg=np.float64(XY_CTRL_PHASE),
    xy_ctrl_quad_deg=np.float64(XY_CTRL_QUAD),
    xy_ctrl_k_Hz_per_V=np.float64(XY_CTRL_K_HZ_PER_V),
    xy_ctrl_b_Hz=np.float64(XY_CTRL_B_HZ),
)

# %% Cell 7
print("=" * 60)
print("Phase 1: Demod 0 主信号相位校准")
print("=" * 60)

dg_temp.set_output(False, channel=2)
dg_comp.set_output(False, channel=1)
dg_comp.set_output(False, channel=2)
dg_sweep.set_output(False, channel=1)
time.sleep(0.5)

sig_cfg = SignalInputConfig(
    input_index=0,
    range=DEMOD0_SIGNAL_RANGE,
    ac_coupling=True,
    diff=False,
    impedance=50,
)
demod.configure_signal_input(hfi, sig_cfg)

osc0_cfg = OscillatorConfig(osc_index=DEMOD0_OSC_IDX, frequency=DEMOD0_OSC_FREQ)
demod.configure_oscillator(hfi, osc0_cfg)

demod0_cfg = DemodulatorConfig(
    demod_index=DEMOD0_IDX,
    enable=True,
    rate=DEMOD0_RATE,
    input_channel=0,
    osc_select=DEMOD0_OSC_IDX,
    harmonic=1,
    time_constant=DEMOD0_TC_CALIB,
    order=DEMOD0_ORDER,
    phase=0.0,
)
actual_rate_d0_calib = demod.configure_demodulator(hfi, demod0_cfg)
calibrated_phase_0 = demod.auto_calibrate_phase(
    hfi, demod_idx=DEMOD0_IDX, tolerance_deg=1.0, settle_time=0.2
)

dg_temp.set_output(True, channel=2)
time.sleep(0.5)
print(f"Demod 0 校相完成: phase={calibrated_phase_0:.2f}°, rate={actual_rate_d0_calib:.0f} Sa/s")
# %% Cell 8
print("=" * 60)
print("Phase 2: XY_CTRL_PHASE 校准（XY_CTRL_QUAD 固定 90°）")
print("=" * 60)


def apply_phase_and_measure(phase_deg):
    upload_xy_aw(phase_deg, outputs_on=True)
    time.sleep(XY_PHASE_SETTLE_TIME)
    theta_deg, sample = read_demod_theta_deg(DEMOD0_IDX)
    print(
        f"  phase={phase_deg:.2f}°, theta={theta_deg:+.2f}°, "
        f"R={sample['r']:.6e}"
    )
    return theta_deg, sample
# %% Cell 8
phase_history = []
phase_sign = None
dg_temp.set_output(False, channel=2)
time.sleep(0.5)

try:
    theta0, sample0 = apply_phase_and_measure(XY_CTRL_PHASE)
    phase_history.append({
        "iteration": 0,
        "xy_ctrl_phase_deg": float(XY_CTRL_PHASE),
        "theta_deg": float(theta0),
        "r": float(sample0["r"]),
        "mode": "initial",
    })

    if abs(theta0) >= XY_CTRL_PHASE_TOL_DEG:
        minus_phase = (XY_CTRL_PHASE - theta0) % 360.0
        theta_minus, sample_minus = apply_phase_and_measure(minus_phase)

        phase_history.extend([
            {
                "iteration": 1,
                "xy_ctrl_phase_deg": float(minus_phase),
                "theta_deg": float(theta_minus),
                "r": float(sample_minus["r"]),
                "mode": "fixed_minus",
            },
        ])

        phase_sign = -1.0
        XY_CTRL_PHASE = minus_phase
        theta_now = theta_minus
        print("  固定更新方向: phase -= theta")

        for iteration in range(2, XY_CTRL_PHASE_MAX_ITER + 1):
            if abs(theta_now) < XY_CTRL_PHASE_TOL_DEG:
                break
            XY_CTRL_PHASE = (XY_CTRL_PHASE + phase_sign * theta_now) % 360.0
            theta_now, sample_now = apply_phase_and_measure(XY_CTRL_PHASE)
            phase_history.append({
                "iteration": iteration,
                "xy_ctrl_phase_deg": float(XY_CTRL_PHASE),
                "theta_deg": float(theta_now),
                "r": float(sample_now["r"]),
                "mode": "iterate",
            })
    else:
        theta_now = theta0
        phase_sign = 0.0

finally:
    dg_temp.set_output(True, channel=2)
    time.sleep(0.5)

phase_cal_success = abs(theta_now) < XY_CTRL_PHASE_TOL_DEG
print(
    f"XY_CTRL_PHASE 校准完成: phase={XY_CTRL_PHASE:.2f}°, "
    f"theta={theta_now:+.2f}°, success={phase_cal_success}"
)

aw, x_aw, y_aw = upload_xy_aw(XY_CTRL_PHASE, outputs_on=True)
np.savez(
    raw_dir / "xy_aw_waveforms.npz",
    time_s=aw["time_s"],
    omega_ctrl_Hz=aw["omega_ctrl_hz"],
    envelope_V=aw["envelope_v"],
    x_waveform_V=x_aw["waveform_V"],
    y_waveform_V=y_aw["waveform_V"],
    x_normalized=x_aw["normalized"],
    y_normalized=y_aw["normalized"],
    x_vpp=np.float64(x_aw["vpp"]),
    y_vpp=np.float64(y_aw["vpp"]),
    x_offset=np.float64(x_aw["offset"]),
    y_offset=np.float64(y_aw["offset"]),
    x_output_min=np.float64(x_aw["output_min"]),
    x_output_max=np.float64(x_aw["output_max"]),
    y_output_min=np.float64(y_aw["output_min"]),
    y_output_max=np.float64(y_aw["output_max"]),
    x_max_abs_normalized=np.float64(x_aw["max_abs_normalized"]),
    y_max_abs_normalized=np.float64(y_aw["max_abs_normalized"]),
    repeat_freq_Hz=np.float64(aw["repeat_freq_hz"]),
    xy_ctrl_phase_deg=np.float64(XY_CTRL_PHASE),
    xy_ctrl_quad_deg=np.float64(XY_CTRL_QUAD),
    xy_ctrl_k_Hz_per_V=np.float64(XY_CTRL_K_HZ_PER_V),
    xy_ctrl_b_Hz=np.float64(XY_CTRL_B_HZ),
)

config["xy_direct_aw"]["XY_CTRL_PHASE_calibrated_deg"] = float(XY_CTRL_PHASE)
config["xy_direct_aw"]["XY_CTRL_QUAD_deg"] = float(XY_CTRL_QUAD)
config["xy_phase_calibration"] = {
    "success": bool(phase_cal_success),
    "final_theta_deg": float(theta_now),
    "phase_update_sign": float(phase_sign),
    "history": phase_history,
}
config["actual_rates"]["demod0_calib_Sa_s"] = float(actual_rate_d0_calib)
config["data_files"].extend([
    "raw/xy_aw_waveforms_initial.npz",
    "raw/xy_aw_waveforms.npz",
])
with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

# %% Cell 9
print("=" * 60)
print("Phase 3: Demod 3 射频场相位校准")
print("=" * 60)

configure_dg_sweep_reference_trigger()

validate_safety_limit("Z_magnetic_field", Z_RF_AMPLITUDE_INIT)
dg_sweep.setup_sine(
    freq=Z_RF_FREQ,
    amplitude=Z_RF_AMPLITUDE_INIT,
    offset=0.0,
    phase=0.0,
    channel=1,
)
dg_sweep.set_output(False, channel=1)
dg_sweep.set_burst_state(False, channel=1)
dg_sweep.set_burst_mode("INFinity", channel=1)
dg_sweep.set_burst_trigger_source("EXTernal", channel=1)
dg_sweep.set_burst_trigger_slope("POSitive", channel=1)
arm_z_rf_burst(Z_RF_AMPLITUDE_INIT, Z_BURST_PHASE_INIT)

osc1_cfg = OscillatorConfig(osc_index=DEMOD3_OSC_IDX, frequency=Z_RF_FREQ)
demod.configure_oscillator(hfi, osc1_cfg)

demod0_meas_cfg = DemodulatorConfig(
    demod_index=DEMOD0_IDX,
    enable=True,
    rate=DEMOD0_RATE,
    input_channel=0,
    osc_select=DEMOD0_OSC_IDX,
    harmonic=1,
    time_constant=DEMOD0_TC_MEAS,
    order=DEMOD0_ORDER,
    phase=calibrated_phase_0,
)
actual_rate_d0_meas = demod.configure_demodulator(hfi, demod0_meas_cfg)

demod3_response_cfg = DemodulatorConfig(
    demod_index=DEMOD3_IDX,
    enable=True,
    rate=DEMOD3_RATE,
    input_channel=DEMOD3_ADC_SELECT,
    osc_select=DEMOD3_OSC_IDX,
    harmonic=1,
    time_constant=DEMOD3_TC,
    order=DEMOD3_ORDER,
    phase=0.0,
)
actual_rate_d3_resp = demod.configure_demodulator(hfi, demod3_response_cfg)

dg_temp.set_output(False, channel=2)
time.sleep(0.5)
calibrated_phase_3 = demod.auto_calibrate_phase(
    hfi, demod_idx=DEMOD3_IDX, settle_time=0.2, tolerance_deg=1.0
)
dg_temp.set_output(True, channel=2)
time.sleep(0.5)

print(
    f"Demod 3 校相完成: phase={calibrated_phase_3:.2f}°, "
    f"rate={actual_rate_d3_resp:.0f} Sa/s"
)

config["calibration"] = {
    "calibrated_phase_0_deg": float(calibrated_phase_0),
    "calibrated_phase_3_deg": float(calibrated_phase_3),
    "z_burst_phase_deg": float(Z_BURST_PHASE_INIT),
    "z_negative_field_phase_deg": float(Z_BURST_PHASE_INIT),
    "z_positive_field_phase_deg": float((Z_BURST_PHASE_INIT + 180.0) % 360.0),
}
config["actual_rates"]["demod0_meas_Sa_s"] = float(actual_rate_d0_meas)
config["actual_rates"]["demod3_response_Sa_s"] = float(actual_rate_d3_resp)
with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

# %% Cell 10
print("=" * 60)
print("Phase A: Z RF 幅度响应曲线扫描")
print("=" * 60)

amplitudes = np.linspace(RF_AMP_START, RF_AMP_STOP, RF_AMP_POINTS)
recorded_x = []
recorded_y = []
recorded_r = []
recorded_theta = []

try:
    for i, amp in enumerate(amplitudes):
        if abs(amp) <= 1e-6:
            dg_sweep.set_output(False, channel=1)
            dg_sweep.set_burst_state(False, channel=1)
        else:
            phase = Z_BURST_PHASE_INIT if amp > 0 else (Z_BURST_PHASE_INIT + 180.0) % 360.0
            arm_z_rf_burst(amp, phase)

        time.sleep(RF_SETTLE_TIME)
        dg_temp.set_output(False, channel=2)
        time.sleep(0.5)

        x_values, y_values, r_values, theta_values = [], [], [], []
        for _ in range(RESPONSE_N_AVG):
            sample = demod.read_demod_sample(hfi, demod_idx=DEMOD3_IDX)
            x_values.append(sample["x"])
            y_values.append(sample["y"])
            r_values.append(sample["r"])
            theta_values.append(sample["theta"])
            time.sleep(RESPONSE_AVG_INTERVAL)

        recorded_x.append(float(np.mean(x_values)))
        recorded_y.append(float(np.mean(y_values)))
        recorded_r.append(float(np.mean(r_values)))
        recorded_theta.append(float(np.degrees(np.angle(np.mean(np.exp(1j * np.asarray(theta_values)))))))

        dg_temp.set_output(True, channel=2)
        time.sleep(1.0)

        if (i + 1) % 10 == 0 or i == 0 or i == len(amplitudes) - 1:
            print(
                f"  [{i + 1}/{len(amplitudes)}] amp={amp:+.4f} V, "
                f"X3={recorded_x[-1]:+.4e}, R3={recorded_r[-1]:.4e}"
            )

finally:
    dg_temp.set_output(True, channel=2)
    dg_sweep.set_output(False, channel=1)
    print("响应扫描结束，温度开关已恢复，Z RF 输出已关闭")

response_data = {
    "amplitudes_V": amplitudes[:len(recorded_x)],
    "amplitudes_nT": amplitudes[:len(recorded_x)] * Z_V_TO_NT,
    "demod3_x_V": np.asarray(recorded_x),
    "demod3_y_V": np.asarray(recorded_y),
    "demod3_r_V": np.asarray(recorded_r),
    "demod3_theta_deg": np.asarray(recorded_theta),
    "z_rf_freq_Hz": np.float64(Z_RF_FREQ),
    "z_v_to_nT": np.float64(Z_V_TO_NT),
    "xy_ctrl_phase_deg": np.float64(XY_CTRL_PHASE),
    "xy_ctrl_quad_deg": np.float64(XY_CTRL_QUAD),
}
np.savez(raw_dir / "response_data.npz", **response_data)
config["data_files"].append("raw/response_data.npz")
with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
print(f"响应曲线数据已保存: {raw_dir / 'response_data.npz'}")

# %% Cell 11
print("=" * 60)
print("Phase B: Demod 3 噪声 DAQ 采集")
print("=" * 60)

dg_sweep.set_output(False, channel=1)
noise_demod_cfg = DemodulatorConfig(
    demod_index=DEMOD3_IDX,
    enable=True,
    rate=DEMOD3_NOISE_RATE,
    input_channel=DEMOD3_ADC_SELECT,
    osc_select=DEMOD3_OSC_IDX,
    harmonic=1,
    time_constant=DEMOD3_NOISE_TC,
    order=DEMOD3_NOISE_ORDER,
    phase=calibrated_phase_3,
)
actual_noise_rate = demod.configure_demodulator(hfi, noise_demod_cfg)

daq_cfg = DAQConfig(
    device=MAPPING["lockin_r"]["device_id"],
    trigger_type=0,
    duration=NOISE_DURATION,
    grid_cols=int(actual_noise_rate * NOISE_DURATION),
    grid_rows=1,
    grid_mode=2,
    signal_paths=["sample.x"],
)

noise_files = []
try:
    for n in range(NOISE_N_AVG):
        dg_temp.set_output(False, channel=2)
        time.sleep(0.5)

        results = daq.acquire_data(
            hfi, daq_cfg, demod_idx=DEMOD3_IDX,
            actual_rate=actual_noise_rate, timeout=NOISE_DURATION + 10.0,
        )
        noise_x = results[0].values if results else np.array([])
        if len(noise_x) == 0:
            print(f"  第 {n + 1} 次采集未获取到有效数据，跳过")
        else:
            path = raw_dir / f"noise_D3_{n:04d}.npz"
            np.savez(
                path,
                noise_x_V=noise_x,
                actual_rate_Sa_s=np.float64(actual_noise_rate),
                demod_idx=np.int32(DEMOD3_IDX),
                signal_path="sample.x",
                noise_duration_s=np.float64(NOISE_DURATION),
            )
            noise_files.append(f"raw/{path.name}")
            print(
                f"  [{n + 1}/{NOISE_N_AVG}] {path.name}: "
                f"n={len(noise_x)}, std={np.std(noise_x):.4e}"
            )

        dg_temp.set_output(True, channel=2)
        time.sleep(2.0)

finally:
    dg_temp.set_output(True, channel=2)
    print("噪声采集结束，温度开关已恢复")

demod.configure_demodulator(hfi, demod3_response_cfg)
config["noise_params"]["actual_rate_Sa_s"] = float(actual_noise_rate)
config["data_files"].extend(noise_files)
with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
print(f"实际噪声采样率: {actual_noise_rate:.0f} Sa/s")

# %% Cell 12
print("正在断开 TEC...")
tec = devices.get("tec")
if tec and hasattr(tec, "disconnect"):
    try:
        tec.disconnect()
        print("  TEC 已断开")
    except Exception as exc:
        print(f"  TEC 断开失败: {exc}")
print("其他设备保持连接")
