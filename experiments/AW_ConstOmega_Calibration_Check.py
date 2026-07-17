# %% [markdown] Cell 0
# # AW 常数 Omega_ctrl 标定 sanity check
#
# 用同一条 AW -> external AM -> XY 旋转场链路输出常数 Omega_ctrl，短扫 Z RF 频率，
# 比较推荐公式 `linear_voltage` 与旧混合公式 `(Omega-B)/K+MOD_OFFSET`。
#
# 本脚本只负责连接仪器、执行短扫并保存 `raw/` 原始数据；峰值、残差和图表由
# `experiments/AW_ConstOmega_Calibration_Check_plot.py` 离线完成。
#
# 重要前提：
# - 主磁场、Pump/Probe 光功率、气室温度沿用当前系统状态，本脚本不自动调节这些量。
# - 温度开关用 DG900 CH2 的 0 V / 5 V DC 切换，不使用 output OFF 模式。

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
from tqdm import tqdm

from lab_workflows.devices import create_signal_generator
from lockin_amplifier import (
    HF2Instrument,
    OscillatorConfig,
    DemodulatorConfig,
    demod,
)

print("库导入完成")

# %% Cell 2
with open(project_root / "params" / "mapping.yaml", encoding="utf-8") as f:
    MAPPING = yaml.safe_load(f)["mapping"]
with open(project_root / "params" / "safety_limits.yaml", encoding="utf-8") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]


def validate_safety_limit(name, value):
    """检查数值是否在安全范围内；未定义限值时只返回原值。"""
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


def validate_voltage_span(name, offset_v, vpp_v):
    """检查正弦/任意波输出的中心、上下峰值都在安全范围内。"""
    offset_v = float(offset_v)
    half_v = 0.5 * float(vpp_v)
    validate_safety_limit(name, offset_v)
    validate_safety_limit(name, offset_v + half_v)
    validate_safety_limit(name, offset_v - half_v)
    return offset_v, vpp_v


EXPERIMENT_TYPE = "AW_ConstOmega_Calibration_Check"
RUN_TAG = "const_aw_omega_check"

# 默认只跑推荐公式以节省时间；legacy_with_mod_offset 保留代码支持但不默认执行。
CONVERSION_MODES = ["linear_voltage"]
# OMEGA_CONST_LIST_Hz = [0.0, 500.0, 1000.0, 2000.0, 3000.0]
OMEGA_CONST_LIST_Hz = [5000.0, 10000.0, 15000.0, 20000.0, 25000.0]

# Omega_ctrl -> AM 物理电压换算参数，来自当前 AW 频率响应脚本。
A_ENV_FREQ = 500.0
A_ENV_K = 16319.281020056458
A_ENV_B = 1121.8563579769193
AM_ZERO_V_X = -0.1487540739959558
AM_ZERO_V_Y = -0.12523177770966548
MOD_OFFSET_X = AM_ZERO_V_X
MOD_OFFSET_Y = AM_ZERO_V_Y

# 用近似常数 arbitrary 输出，确保走 AW 路径。
CONST_AW_POINTS = 1024
CONST_AW_VPP = 0.002

# X/Y 载波：dg_comp CH1/CH2，外部 AM 模式。
XY_CARRIER_FREQ = 90e3
X_CARRIER_AMPLITUDE = 6.0
Y_CARRIER_AMPLITUDE = 6.0
X_CARRIER_PHASE = 0.0
Y_CARRIER_PHASE = 90.0
X_AM_DEPTH = 100.0
Y_AM_DEPTH = 100.0

# X/Y 载波相位校准参数，默认与 RF_Field_Sensitivity_AW_FreqSweep.py 一致。
ENABLE_XY_CARRIER_PHASE_CAL = True
XY_PHASE_CAL_DC_X = 1.0
XY_PHASE_CAL_DC_Y = 1.0
XY_PHASE_CAL_MAX_ITER_X = 5
XY_PHASE_CAL_MAX_ITER_Y = 15
XY_PHASE_CAL_TOL_DEG = 0.5
XY_PHASE_CAL_SETTLE_s = 0.5
# Z RF 短扫。
Z_RF_AMPLITUDE = 0.03
Z_SCAN_HALF_WIDTH_Hz = 5000.0
Z_SCAN_STEP_Hz = 500.0
Z_SCAN_MIN_Hz = 500.0

FREQ_SETTLE_TIME_s = 0.2
AW_SETTLE_TIME_s = 0.5
TEMP_SWITCH_OFF_LEAD_s = 0.1
TEMP_SWITCH_ON_LAG_s = 0.4

# HF2 Demod 0：主信号解调。
DEMOD0_IDX = 0
DEMOD0_OSC_IDX = 0
DEMOD0_OSC_FREQ = 90000.0
DEMOD0_ORDER = 4
DEMOD0_TC_CALIB = 0.001
DEMOD0_TC_MEAS = 1e-6
DEMOD0_RATE = 100000

# HF2 Demod 3：跟随 Z RF 频率，读取物理回环的 X/Y。
DEMOD3_IDX = 3
DEMOD3_OSC_IDX = 1
DEMOD3_TC_MIN_s = 0.01
DEMOD3_TC_PERIOD_FRACTION = 0.05
DEMOD3_ORDER = 8
DEMOD3_RATE = 4800
DEMOD3_READ_SETTLE_TIME_s = 0.2
DEMOD3_N_AVG = 30
DEMOD3_AVG_INTERVAL_s = 0.01
DEMOD3_ADC_SELECT = 1

# 仅作为本次运行环境记录；本脚本不自动设置这些输出。
FIXED_PARAMS = {
    "Pump_laser_power": 0.2,
    "Probe_laser_power": 0.1,
    "main_magnetic_field": 9.31,
    "temperature": 100.0,
    "Temp_Switch": 5.0,
}

for key, value in FIXED_PARAMS.items():
    validate_safety_limit(key, float(value))
validate_voltage_span("X_magnetic_field", 0.0, X_CARRIER_AMPLITUDE)
validate_voltage_span("Y_magnetic_field", 0.0, Y_CARRIER_AMPLITUDE)
validate_voltage_span("Z_magnetic_field", 0.0, Z_RF_AMPLITUDE)
validate_safety_limit("Temp_Switch", 0.0)
validate_safety_limit("Temp_Switch", FIXED_PARAMS["Temp_Switch"])
validate_safety_limit("X_magnetic_field_AM", XY_PHASE_CAL_DC_X)
validate_safety_limit("Y_magnetic_field_AM", XY_PHASE_CAL_DC_Y)

print("参数和安全限值检查完成")
print("运行前请确认: dg_am CH1/CH2 已接入 dg_comp CH1/CH2 external AM，Z RF 接线正确。")

# %% Cell 3
def omega_to_am_voltage(omega_hz, mode, channel):
    """把常数 Omega_ctrl 换算为对应 X/Y AM 物理电压。"""
    if mode == "linear_voltage":
        return (float(omega_hz) - A_ENV_B) / A_ENV_K
    if mode == "zero_centered":
        v_zero = AM_ZERO_V_X if channel == "x" else AM_ZERO_V_Y
        return v_zero + float(omega_hz) / A_ENV_K
    if mode == "legacy_with_mod_offset":
        mod = MOD_OFFSET_X if channel == "x" else MOD_OFFSET_Y
        return (float(omega_hz) - A_ENV_B) / A_ENV_K + mod
    raise ValueError(f"未知转换模式: {mode}")


def z_freq_list_for(omega_hz):
    """按每个 Omega_const 生成局部 Z RF 频率短扫列表。"""
    center = max(float(omega_hz), Z_SCAN_MIN_Hz + Z_SCAN_HALF_WIDTH_Hz)
    lo = max(Z_SCAN_MIN_Hz, center - Z_SCAN_HALF_WIDTH_Hz)
    hi = center + Z_SCAN_HALF_WIDTH_Hz
    return np.arange(lo, hi + 0.5 * Z_SCAN_STEP_Hz, Z_SCAN_STEP_Hz)


def configure_xy_carrier(dg_comp):
    """配置 dg_comp CH1/CH2 为 90 kHz external AM 载波。"""
    for ch in (1, 2):
        dg_comp.set_burst_state(False, channel=ch)
        dg_comp.set_mod_state(False, channel=ch)

    validate_voltage_span("X_magnetic_field", 0.0, X_CARRIER_AMPLITUDE)
    dg_comp.setup_sine(
        XY_CARRIER_FREQ,
        X_CARRIER_AMPLITUDE,
        offset=0.0,
        phase=X_CARRIER_PHASE,
        channel=1,
    )
    dg_comp.set_mod_type("AM", channel=1)
    dg_comp.set_mod_am_source("EXT", channel=1)
    dg_comp.set_mod_am_depth(X_AM_DEPTH, channel=1)
    dg_comp.set_mod_state(True, channel=1)

    validate_voltage_span("Y_magnetic_field", 0.0, Y_CARRIER_AMPLITUDE)
    dg_comp.setup_sine(
        XY_CARRIER_FREQ,
        Y_CARRIER_AMPLITUDE,
        offset=0.0,
        phase=Y_CARRIER_PHASE,
        channel=2,
    )
    dg_comp.set_mod_type("AM", channel=2)
    dg_comp.set_mod_am_source("EXT", channel=2)
    dg_comp.set_mod_am_depth(Y_AM_DEPTH, channel=2)
    dg_comp.set_mod_state(True, channel=2)


def configure_const_aw(dg_am, vx, vy):
    """把 dg_am CH1/CH2 配成近似常数 AW 输出。"""
    validate_voltage_span("X_magnetic_field_AM", vx, CONST_AW_VPP)
    validate_voltage_span("Y_magnetic_field_AM", vy, CONST_AW_VPP)

    y = np.zeros(CONST_AW_POINTS, dtype=float)
    for ch in (1, 2):
        dg_am.set_burst_state(False, channel=ch)
        dg_am.set_mod_state(False, channel=ch)
    dg_am.setup_arbitrary(y.copy(), freq=A_ENV_FREQ, amplitude=CONST_AW_VPP, offset=vx, channel=1)
    dg_am.setup_arbitrary(y.copy(), freq=A_ENV_FREQ, amplitude=CONST_AW_VPP, offset=vy, channel=2)


def configure_z_rf(dg_sweep, z_freq_hz):
    """设置 Z RF 正弦输出，频率扫描允许重绘正弦波。"""
    validate_voltage_span("Z_magnetic_field", 0.0, Z_RF_AMPLITUDE)
    dg_sweep.set_burst_state(False, channel=1)
    dg_sweep.set_mod_state(False, channel=1)
    dg_sweep.setup_sine(float(z_freq_hz), Z_RF_AMPLITUDE, offset=0.0, phase=0.0, channel=1)


def set_temp_switch(dg_temp, enabled):
    """温度开关保持 output ON，通过 5 V/0 V DC 表示开/关。"""
    voltage = FIXED_PARAMS["Temp_Switch"] if enabled else 0.0
    validate_safety_limit("Temp_Switch", float(voltage))
    dg_temp.setup_dc(float(voltage), channel=2)
    dg_temp.set_output(True, channel=2)


def configure_xy_carrier_for_phase_cal(dg_comp_inst, x_phase_deg, y_phase_deg):
    """把 dg_comp CH1/CH2 配成 90 kHz sine + AM EXT，准备相位校准。"""
    for ch in (1, 2):
        dg_comp_inst.set_burst_state(False, channel=ch)
        dg_comp_inst.set_mod_state(False, channel=ch)

    validate_voltage_span("X_magnetic_field", 0.0, X_CARRIER_AMPLITUDE)
    dg_comp_inst.setup_sine(
        XY_CARRIER_FREQ,
        X_CARRIER_AMPLITUDE,
        offset=0.0,
        phase=float(x_phase_deg),
        channel=1,
    )
    dg_comp_inst.set_mod_type("AM", channel=1)
    dg_comp_inst.set_mod_am_source("EXT", channel=1)
    dg_comp_inst.set_mod_am_depth(X_AM_DEPTH, channel=1)
    dg_comp_inst.set_mod_state(True, channel=1)

    validate_voltage_span("Y_magnetic_field", 0.0, Y_CARRIER_AMPLITUDE)
    dg_comp_inst.setup_sine(
        XY_CARRIER_FREQ,
        Y_CARRIER_AMPLITUDE,
        offset=0.0,
        phase=float(y_phase_deg),
        channel=2,
    )
    dg_comp_inst.set_mod_type("AM", channel=2)
    dg_comp_inst.set_mod_am_source("EXT", channel=2)
    dg_comp_inst.set_mod_am_depth(Y_AM_DEPTH, channel=2)
    dg_comp_inst.set_mod_state(True, channel=2)


def set_xy_phase_cal_am_dc(dg_am_inst, v_dc_x, v_dc_y):
    """设置 dg_am CH1/CH2 的物理 DC AM 电压；不叠加 MOD_OFFSET。"""
    validate_safety_limit("X_magnetic_field_AM", float(v_dc_x))
    validate_safety_limit("Y_magnetic_field_AM", float(v_dc_y))
    for ch in (1, 2):
        dg_am_inst.set_burst_state(False, channel=ch)
        dg_am_inst.set_mod_state(False, channel=ch)
    dg_am_inst.setup_dc(float(v_dc_x), channel=1)
    dg_am_inst.setup_dc(float(v_dc_y), channel=2)
    print(f"  dg_am CH1(X): phase-cal DC = {float(v_dc_x):.4f} V")
    print(f"  dg_am CH2(Y): phase-cal DC = {float(v_dc_y):.4f} V")


def calibrate_xy_carrier_phase(
    dg_comp_inst,
    dg_am_inst,
    hfi_inst,
    initial_x_phase,
    initial_y_phase,
    calib_dc_x,
    calib_dc_y,
    max_iter_x=5,
    max_iter_y=15,
    tol_deg=0.5,
    settle_s=0.5,
    calibrated_demod0_phase_deg=0.0,
):
    """分通道迭代校准 X/Y carrier phase，返回与配置文件一致的结果字段。"""
    result = {
        "calibrated_phase_0_deg": float(calibrated_demod0_phase_deg),
        "X_CARRIER_PHASE_deg": float(initial_x_phase) % 360,
        "Y_CARRIER_PHASE_deg": float(initial_y_phase) % 360,
        "xy_phase_diff_deg": (float(initial_y_phase) - float(initial_x_phase)) % 360,
        "phase_cal_success_x": False,
        "phase_cal_success_y": False,
        "n_iter_x": 0,
        "n_iter_y": 0,
        "calib_dc_x_V": float(calib_dc_x),
        "calib_dc_y_V": float(calib_dc_y),
    }

    x_phase = float(initial_x_phase) % 360
    y_phase = float(initial_y_phase) % 360

    demod0_phase_cal_cfg = DemodulatorConfig(
        DEMOD0_IDX,
        True,
        DEMOD0_RATE,
        0,
        DEMOD0_OSC_IDX,
        1,
        DEMOD0_TC_CALIB,
        DEMOD0_ORDER,
        float(calibrated_demod0_phase_deg),
    )
    demod.configure_demodulator(hfi_inst, demod0_phase_cal_cfg)
    print(f"  Demod 0: TC={DEMOD0_TC_CALIB:.0e} s (校相 TC)")

    set_xy_phase_cal_am_dc(dg_am_inst, calib_dc_x, calib_dc_y)
    configure_xy_carrier_for_phase_cal(dg_comp_inst, x_phase, y_phase)

    print("\n  校准 CH1 相位 (仅开 CH1)...")
    dg_comp_inst.set_output(False, channel=2)
    dg_am_inst.set_output(False, channel=2)
    dg_comp_inst.set_output(True, channel=1)
    dg_am_inst.set_output(True, channel=1)
    time.sleep(settle_s)

    for i in range(max_iter_x):
        sample = demod.read_demod_sample(hfi_inst, demod_idx=DEMOD0_IDX)
        theta_deg = math.degrees(sample["theta"])
        result["n_iter_x"] = i + 1
        print(f"    CH1 迭代 {i + 1}: theta = {theta_deg:.2f} deg")
        if abs(theta_deg) < tol_deg:
            result["phase_cal_success_x"] = True
            print(f"    CH1 相位已收敛 (< {tol_deg} deg)")
            break
        x_phase = (x_phase - theta_deg) % 360
        dg_comp_inst.set_phase_adjust(x_phase, channel=1)
        time.sleep(settle_s)

    result["X_CARRIER_PHASE_deg"] = float(x_phase)
    print(f"    CH1 最终载波相位 = {x_phase:.2f} deg")

    print("\n  校准 CH2 相位 (CH1 + CH2 都开)...")
    dg_comp_inst.set_output(True, channel=2)
    dg_am_inst.set_output(True, channel=2)
    dg_comp_inst.set_phase_adjust(y_phase, channel=2)
    time.sleep(settle_s * 0.6)

    for i in range(max_iter_y):
        sample = demod.read_demod_sample(hfi_inst, demod_idx=DEMOD0_IDX)
        theta_deg = math.degrees(sample["theta"])
        result["n_iter_y"] = i + 1
        print(f"    CH2 迭代 {i + 1}: theta = {theta_deg:.2f} deg")
        if abs(theta_deg) < tol_deg:
            result["phase_cal_success_y"] = True
            print(f"    CH2 相位已收敛 (< {tol_deg} deg)")
            break
        y_phase = (y_phase - theta_deg) % 360
        dg_comp_inst.set_phase_adjust(y_phase, channel=2)
        time.sleep(settle_s)

    result["Y_CARRIER_PHASE_deg"] = float(y_phase)
    result["xy_phase_diff_deg"] = float((y_phase - x_phase) % 360)
    print(f"    CH2 最终载波相位 = {y_phase:.2f} deg")
    print(f"    实际相位差 = {result['xy_phase_diff_deg']:.2f} deg")
    return result

def restore_experiment_outputs(devices):
    """恢复本脚本负责的危险输出，但保持设备连接。"""
    dg_temp = devices.get("dg_temp")
    if dg_temp is not None:
        try:
            set_temp_switch(dg_temp, True)
            print("  温度开关: 已恢复 ON")
        except Exception as exc:
            print(f"  温度开关恢复失败: {exc}")

    for name in ("dg_sweep", "dg_am", "dg_comp"):
        dev = devices.get(name)
        if dev is None:
            continue
        for ch in (1, 2):
            try:
                if hasattr(dev, "set_burst_state"):
                    dev.set_burst_state(False, channel=ch)
                if hasattr(dev, "set_mod_state"):
                    dev.set_mod_state(False, channel=ch)
                if hasattr(dev, "setup_dc"):
                    dev.setup_dc(0.0, channel=ch)
                dev.set_output(False, channel=ch)
            except Exception as exc:
                print(f"  {name} CH{ch} 安全恢复失败: {exc}")


def save_config(config_path, experiment_config):
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(experiment_config, f, allow_unicode=True, sort_keys=False)


# %% Cell 4
devices = {}
resource_cache = {}


def connect_dg4000(mapping_key, device_key):
    """按 resource 复用 DG4000 物理设备实例。"""
    cfg = MAPPING[mapping_key]
    resource = cfg["resource"]
    if resource not in resource_cache:
        dev = create_signal_generator(resource, channel=cfg.get("channel") or 1)
        dev.connect()
        dev.set_ref_clock_source("EXTernal")
        resource_cache[resource] = dev
        print(f"{device_key} 已连接: {dev.idn()}")
    devices[device_key] = resource_cache[resource]
    return devices[device_key]


try:
    dg_comp = connect_dg4000("X_magnetic_field", "dg_comp")
    dg_am = connect_dg4000("X_magnetic_field_AM", "dg_am")
    dg_sweep = connect_dg4000("Z_magnetic_field", "dg_sweep")

    dg_temp_cfg = MAPPING["Temp_Switch"]
    dg_temp = create_signal_generator(dg_temp_cfg["resource"], channel=dg_temp_cfg["channel"])
    dg_temp.connect()
    devices["dg_temp"] = dg_temp
    print(f"dg_temp 已连接: {dg_temp.idn()}")

    hf2_cfg = MAPPING["lockin_r"]
    hfi = HF2Instrument(
        host=hf2_cfg.get("host", "127.0.0.1"),
        port=hf2_cfg.get("port", 8005),
        api_level=1,
        device_id=hf2_cfg["device_id"],
    )
    hfi.connect()
    hfi.set_extclk(True)
    devices["hf2"] = hfi
    print(f"HF2 已连接: {hfi.idn}")

    restore_experiment_outputs(devices)

except Exception:
    print("设备连接失败，清理已连接设备")
    restore_experiment_outputs(devices)
    for dev in set(devices.values()):
        try:
            if hasattr(dev, "disconnect"):
                dev.disconnect()
        except Exception:
            pass
    raise

print("设备连接完成")

# %% Cell 5
hfi = devices["hf2"]
dg_comp = devices["dg_comp"]
dg_am = devices["dg_am"]
dg_sweep = devices["dg_sweep"]
dg_temp = devices["dg_temp"]

set_temp_switch(dg_temp, True)
configure_xy_carrier(dg_comp)

demod.configure_oscillator(hfi, OscillatorConfig(DEMOD0_OSC_IDX, DEMOD0_OSC_FREQ))
actual_rate_d0_initial = demod.configure_demodulator(
    hfi,
    DemodulatorConfig(
        DEMOD0_IDX,
        True,
        DEMOD0_RATE,
        0,
        DEMOD0_OSC_IDX,
        1,
        DEMOD0_TC_CALIB,
        DEMOD0_ORDER,
        0.0,
    ),
)

print(f"dg_comp 载波已配置: {XY_CARRIER_FREQ:.1f} Hz, X={X_CARRIER_PHASE:.1f} deg, Y={Y_CARRIER_PHASE:.1f} deg")
print(f"HF2 Demod 0 校相初始配置: actual_rate={actual_rate_d0_initial:.1f} Sa/s")

print("\n正在进行 Demod 0 相位校准...")
set_temp_switch(dg_temp, False)
time.sleep(TEMP_SWITCH_OFF_LEAD_s)
try:
    time.sleep(0.5)
    calibrated_phase_0 = demod.auto_calibrate_phase(
        hfi,
        demod_idx=DEMOD0_IDX,
        tolerance_deg=1.0,
        max_attempts=5,
        settle_time=0.2,
    )
    sample = demod.read_demod_sample(hfi, demod_idx=DEMOD0_IDX)
    print(f"Demod 0 校准完成: phaseshift = {calibrated_phase_0:.2f} deg")
    print(f"  校准后样本: R={sample['r']:.6f}, X={sample['x']:.6f}, Y={sample['y']:.6f}")

    if ENABLE_XY_CARRIER_PHASE_CAL:
        print("\n" + "=" * 60)
        print("X/Y carrier phase calibration")
        print("=" * 60)
        phase_calibration = calibrate_xy_carrier_phase(
            dg_comp,
            dg_am,
            hfi,
            initial_x_phase=X_CARRIER_PHASE,
            initial_y_phase=Y_CARRIER_PHASE,
            calib_dc_x=XY_PHASE_CAL_DC_X,
            calib_dc_y=XY_PHASE_CAL_DC_Y,
            max_iter_x=XY_PHASE_CAL_MAX_ITER_X,
            max_iter_y=XY_PHASE_CAL_MAX_ITER_Y,
            tol_deg=XY_PHASE_CAL_TOL_DEG,
            settle_s=XY_PHASE_CAL_SETTLE_s,
            calibrated_demod0_phase_deg=calibrated_phase_0,
        )
        X_CARRIER_PHASE = float(phase_calibration["X_CARRIER_PHASE_deg"])
        Y_CARRIER_PHASE = float(phase_calibration["Y_CARRIER_PHASE_deg"])
    else:
        print("X/Y carrier phase calibration 已禁用 (ENABLE_XY_CARRIER_PHASE_CAL=False)")
        phase_calibration = {
            "calibrated_phase_0_deg": float(calibrated_phase_0),
            "X_CARRIER_PHASE_deg": float(X_CARRIER_PHASE) % 360,
            "Y_CARRIER_PHASE_deg": float(Y_CARRIER_PHASE) % 360,
            "xy_phase_diff_deg": float((Y_CARRIER_PHASE - X_CARRIER_PHASE) % 360),
            "phase_cal_success_x": False,
            "phase_cal_success_y": False,
            "n_iter_x": 0,
            "n_iter_y": 0,
            "calib_dc_x_V": float(XY_PHASE_CAL_DC_X),
            "calib_dc_y_V": float(XY_PHASE_CAL_DC_Y),
        }
except Exception:
    restore_experiment_outputs(devices)
    raise
finally:
    set_temp_switch(dg_temp, True)
    time.sleep(TEMP_SWITCH_ON_LAG_s)

# 校相后恢复最终 X/Y carrier phase；dg_am 会在正式扫描时切回 constant arbitrary waveform。
try:
    configure_xy_carrier(dg_comp)
    for ch, phase_deg in ((1, X_CARRIER_PHASE), (2, Y_CARRIER_PHASE)):
        dg_comp.set_phase_adjust(float(phase_deg), channel=ch)
except Exception:
    restore_experiment_outputs(devices)
    raise
actual_rate_d0 = demod.configure_demodulator(
    hfi,
    DemodulatorConfig(
        DEMOD0_IDX,
        True,
        DEMOD0_RATE,
        0,
        DEMOD0_OSC_IDX,
        1,
        DEMOD0_TC_MEAS,
        DEMOD0_ORDER,
        float(calibrated_phase_0),
    ),
)

print(
    f"分通道校相完成: X={X_CARRIER_PHASE:.2f} deg, "
    f"Y={Y_CARRIER_PHASE:.2f} deg, dXY={(Y_CARRIER_PHASE - X_CARRIER_PHASE) % 360:.2f} deg"
)
print(f"HF2 Demod 0 测量配置: actual_rate={actual_rate_d0:.1f} Sa/s, phase={calibrated_phase_0:.2f} deg")

# %% Cell 6
timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
raw_dir = run_dir / "raw"
results_dir = run_dir / "results"
raw_dir.mkdir(parents=True, exist_ok=True)
results_dir.mkdir(parents=True, exist_ok=True)

experiment_config = {
    "experiment_type": EXPERIMENT_TYPE,
    "run_tag": RUN_TAG,
    "timestamp": timestamp,
    "purpose": "Check constant Omega_ctrl calibration by sweeping Z RF frequency.",
    "notes": [
        "Main field, laser powers, and cell temperature are assumed to be already prepared.",
        "Acquisition saves raw traces only; run the matching _plot.py for peak and residual analysis.",
    ],
    "parameters": {
        "CONVERSION_MODES": CONVERSION_MODES,
        "OMEGA_CONST_LIST_Hz": [float(v) for v in OMEGA_CONST_LIST_Hz],
        "A_ENV_FREQ_Hz": A_ENV_FREQ,
        "A_ENV_K_Hz_per_V": A_ENV_K,
        "A_ENV_B_Hz": A_ENV_B,
        "AM_ZERO_V_X": AM_ZERO_V_X,
        "AM_ZERO_V_Y": AM_ZERO_V_Y,
        "MOD_OFFSET_X_legacy": MOD_OFFSET_X,
        "MOD_OFFSET_Y_legacy": MOD_OFFSET_Y,
        "CONST_AW_POINTS": CONST_AW_POINTS,
        "CONST_AW_VPP": CONST_AW_VPP,
        "XY_CARRIER_FREQ_Hz": XY_CARRIER_FREQ,
        "X_CARRIER_AMPLITUDE_Vpp": X_CARRIER_AMPLITUDE,
        "Y_CARRIER_AMPLITUDE_Vpp": Y_CARRIER_AMPLITUDE,
        "X_CARRIER_PHASE_deg": X_CARRIER_PHASE,
        "Y_CARRIER_PHASE_deg": Y_CARRIER_PHASE,
        "X_AM_DEPTH_pct": X_AM_DEPTH,
        "Y_AM_DEPTH_pct": Y_AM_DEPTH,
        "ENABLE_XY_CARRIER_PHASE_CAL": ENABLE_XY_CARRIER_PHASE_CAL,
        "XY_PHASE_CAL_DC_X_V": XY_PHASE_CAL_DC_X,
        "XY_PHASE_CAL_DC_Y_V": XY_PHASE_CAL_DC_Y,
        "XY_PHASE_CAL_MAX_ITER_X": XY_PHASE_CAL_MAX_ITER_X,
        "XY_PHASE_CAL_MAX_ITER_Y": XY_PHASE_CAL_MAX_ITER_Y,
        "XY_PHASE_CAL_TOL_DEG": XY_PHASE_CAL_TOL_DEG,
        "XY_PHASE_CAL_SETTLE_s": XY_PHASE_CAL_SETTLE_s,
        "FINAL_X_CARRIER_PHASE_deg": float(X_CARRIER_PHASE),
        "FINAL_Y_CARRIER_PHASE_deg": float(Y_CARRIER_PHASE),
        "Z_RF_AMPLITUDE_Vpp": Z_RF_AMPLITUDE,
        "Z_SCAN_HALF_WIDTH_Hz": Z_SCAN_HALF_WIDTH_Hz,
        "Z_SCAN_STEP_Hz": Z_SCAN_STEP_Hz,
        "Z_SCAN_MIN_Hz": Z_SCAN_MIN_Hz,
        "FREQ_SETTLE_TIME_s": FREQ_SETTLE_TIME_s,
        "AW_SETTLE_TIME_s": AW_SETTLE_TIME_s,
        "TEMP_SWITCH_OFF_LEAD_s": TEMP_SWITCH_OFF_LEAD_s,
        "TEMP_SWITCH_ON_LAG_s": TEMP_SWITCH_ON_LAG_s,
        "DEMOD3_N_AVG": DEMOD3_N_AVG,
        "DEMOD3_AVG_INTERVAL_s": DEMOD3_AVG_INTERVAL_s,
        "FIXED_PARAMS_REFERENCE": FIXED_PARAMS,
    },
    "mapping_snapshot": MAPPING,
    "safety_limits_snapshot": LIMITS,
    "phase_calibration": phase_calibration,
    "constant_aw_scan": {
        "X_CARRIER_PHASE_deg": float(X_CARRIER_PHASE),
        "Y_CARRIER_PHASE_deg": float(Y_CARRIER_PHASE),
        "xy_phase_diff_deg": float((Y_CARRIER_PHASE - X_CARRIER_PHASE) % 360),
    },
    "actual_rates": {
        "demod0_actual_rate_Sa_s": float(actual_rate_d0),
        "demod3_actual_rate_Sa_s": [],
    },
    "data_files": [],
}

config_path = run_dir / "experiment_config.yaml"
save_config(config_path, experiment_config)
print(f"运行目录: {run_dir}")
print(f"实验配置已保存: {config_path}")

# %% Cell 7
raw_index_records = []

try:
    for mode in CONVERSION_MODES:
        for omega_hz in tqdm(OMEGA_CONST_LIST_Hz, desc=f"mode={mode}"):
            vx = omega_to_am_voltage(omega_hz, mode, "x")
            vy = omega_to_am_voltage(omega_hz, mode, "y")
            configure_const_aw(dg_am, vx, vy)
            time.sleep(AW_SETTLE_TIME_s)

            z_freqs = z_freq_list_for(omega_hz)
            x_means = []
            y_means = []
            x_stds = []
            y_stds = []
            r_values = []
            demod3_tc_values = []
            demod3_rate_values = []

            for z_freq in z_freqs:
                configure_z_rf(dg_sweep, float(z_freq))
                time.sleep(FREQ_SETTLE_TIME_s)

                demod.configure_oscillator(hfi, OscillatorConfig(DEMOD3_OSC_IDX, float(z_freq)))
                tc = max(DEMOD3_TC_MIN_s, DEMOD3_TC_PERIOD_FRACTION / max(float(z_freq), 1.0))
                actual_rate_d3 = demod.configure_demodulator(
                    hfi,
                    DemodulatorConfig(
                        DEMOD3_IDX,
                        True,
                        DEMOD3_RATE,
                        DEMOD3_ADC_SELECT,
                        DEMOD3_OSC_IDX,
                        1,
                        tc,
                        DEMOD3_ORDER,
                        0.0,
                    ),
                )

                set_temp_switch(dg_temp, False)
                time.sleep(TEMP_SWITCH_OFF_LEAD_s)
                try:
                    time.sleep(DEMOD3_READ_SETTLE_TIME_s)
                    xs, ys = [], []
                    for _ in range(DEMOD3_N_AVG):
                        sample = demod.read_demod_sample(hfi, DEMOD3_IDX)
                        xs.append(float(sample["x"]))
                        ys.append(float(sample["y"]))
                        time.sleep(DEMOD3_AVG_INTERVAL_s)
                    x_mean = float(np.mean(xs))
                    y_mean = float(np.mean(ys))
                    r_vec = float(math.hypot(x_mean, y_mean))
                finally:
                    set_temp_switch(dg_temp, True)
                    time.sleep(TEMP_SWITCH_ON_LAG_s)

                x_means.append(x_mean)
                y_means.append(y_mean)
                x_stds.append(float(np.std(xs)))
                y_stds.append(float(np.std(ys)))
                r_values.append(r_vec)
                demod3_tc_values.append(float(tc))
                demod3_rate_values.append(float(actual_rate_d3))

            raw_name = f"{mode}_{omega_hz:.0f}Hz.npz"
            raw_path = raw_dir / raw_name
            np.savez(
                raw_path,
                mode=np.array(mode),
                omega_const_Hz=np.float64(omega_hz),
                z_freq_Hz=np.asarray(z_freqs, dtype=float),
                r_V=np.asarray(r_values, dtype=float),
                x_mean_V=np.asarray(x_means, dtype=float),
                y_mean_V=np.asarray(y_means, dtype=float),
                x_std_V=np.asarray(x_stds, dtype=float),
                y_std_V=np.asarray(y_stds, dtype=float),
                am_voltage_x_V=np.float64(vx),
                am_voltage_y_V=np.float64(vy),
                demod3_tc_s=np.asarray(demod3_tc_values, dtype=float),
                demod3_actual_rate_Sa_s=np.asarray(demod3_rate_values, dtype=float),
                z_rf_amplitude_Vpp=np.float64(Z_RF_AMPLITUDE),
                const_aw_vpp=np.float64(CONST_AW_VPP),
                x_carrier_phase_deg=np.float64(X_CARRIER_PHASE),
                y_carrier_phase_deg=np.float64(Y_CARRIER_PHASE),
                calibrated_phase_0_deg=np.float64(calibrated_phase_0),
            )

            raw_index_records.append({
                "mode": mode,
                "omega_const_Hz": float(omega_hz),
                "file": raw_name,
                "am_voltage_x_V": float(vx),
                "am_voltage_y_V": float(vy),
                "z_freq_start_Hz": float(z_freqs[0]),
                "z_freq_stop_Hz": float(z_freqs[-1]),
                "z_freq_points": int(len(z_freqs)),
            })
            experiment_config["data_files"] = [str(raw_dir / r["file"]) for r in raw_index_records]
            experiment_config["actual_rates"]["demod3_actual_rate_Sa_s"] = [
                float(v) for v in demod3_rate_values
            ]
            save_config(config_path, experiment_config)

            print(
                f"{mode}, Omega={omega_hz:.1f} Hz -> raw saved: {raw_path.name}, "
                f"Vx={vx:+.5f} V, Vy={vy:+.5f} V"
            )

    with open(raw_dir / "raw_index.json", "w", encoding="utf-8") as f:
        json.dump(raw_index_records, f, indent=2, ensure_ascii=False)
    print(f"原始数据索引已保存: {raw_dir / 'raw_index.json'}")

finally:
    print("\n--- 恢复本实验负责的输出 ---")
    restore_experiment_outputs(devices)
    print("输出已恢复到安全状态，设备保持连接")

# %% Cell 8
print("正常结束：本脚本未连接 TEC，无需断开 TEC；其他设备保持连接。")
print(f"请运行 experiments/AW_ConstOmega_Calibration_Check_plot.py 分析: {run_dir}")
