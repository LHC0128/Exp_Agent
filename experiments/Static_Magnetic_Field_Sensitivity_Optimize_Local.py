# %% [markdown] Cell 0
# # 静磁场灵敏度局部优化扫描
#
# 围绕当前有效工作点，只扫描 Pump_laser_power、Probe_laser_power 和
# PUMP_MOD_DUTY，寻找更低的静磁场灵敏度。
#
# 本脚本保留 Static_Magnetic_Field_Sensitivity.py 不变。每个扫描点都会恢复到
# 基准参数，再只改变一个目标参数，随后重新校准 HF2 相位并执行完整测量。

# %% Cell 1
from pathlib import Path
import csv
import math
import sys
import time
from datetime import datetime

project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import signal as scipy_signal
import yaml

from gs200 import GS200Instrument
from lab_workflows.devices import create_signal_generator
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
from sensitivity_analysis import fit_dispersive, compute_sensitivity, write_run_record

print("库导入完成")

# %% Cell 2
with open(project_root / "params" / "mapping.yaml", encoding="utf-8") as f:
    MAPPING = yaml.safe_load(f)["mapping"]

with open(project_root / "params" / "safety_limits.yaml", encoding="utf-8") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]

EXPERIMENT_TYPE = "Static_Magnetic_Field_Sensitivity"
RUN_TAG_PREFIX = "opt_local"

BASELINE_PARAMS = {
    "Pump_laser_power": 0.3,
    "Probe_laser_power": 0.1,
    "PUMP_MOD_DUTY": 5,
    "temperature": 100,
    "Temp_Switch": 5.0,
    "Time_sequence": 10.0,
    "main_magnetic_field": 1.023,
    "X_magnetic_field": 0.0,
    "Y_magnetic_field": 0.0,
    "Time_sequence_2": 0.0,
}

OPTIMIZE_SEQUENCE = [
    {
        "parameter": "Pump_laser_power",
        "values": [0.20, 0.25, 0.30, 0.35, 0.40],
        "stabilize_time": 2.0,
    },
    {
        "parameter": "Probe_laser_power",
        "values": [0.05, 0.08, 0.10, 0.12, 0.15],
        "stabilize_time": 2.0,
    },
    {
        "parameter": "PUMP_MOD_DUTY",
        "values": [3, 4, 5, 6, 8],
        "stabilize_time": 2.0,
    },
]

PUMP_MOD_FREQ = 10e3
PUMP_MOD_AMPLITUDE = 0.18

RF_GATE_AMPLITUDE = 5.0
RF_GATE_OFFSET = 2.5

SCAN_VARIABLE = "Z_magnetic_field"
RAMP_LOW = -0.5
RAMP_HIGH = 0.5
RAMP_FREQ = 1.0
RAMP_SYMMETRY = 20
DAQ_DURATION = 1.0

HF2_DEMOD_IDX = 0
HF2_OSC_FREQ = PUMP_MOD_FREQ
HF2_DEMOD_ORDER = 4
HF2_SIGNAL_RANGE = 2.0
HF2_DEMOD_RATE = 1000
HF2_DEMOD_TC = 0.001
HF2_NOISE_RATE = 50000
HF2_NOISE_TC = 1e-6

NOISE_N_AVG = 20
NOISE_DURATION = 1.0
TEMP_TOLERANCE_C = 1.0
MAX_TEMP_WAIT_S = 600

GS200_CURRENT_RANGE_A = 0.001
GS200_RANGE_SETTLE_TIME = 1.0

Z_V_TO_NT = 3517
Z_V_TO_FT = Z_V_TO_NT * 1000000

DAQ_TRIGGER_CHANNEL = 0
DAQ_TRIGGER_LEVEL = 1
DAQ_TRIGGER_SLOPE = 0

QUALITY_THRESHOLDS = {
    "r_squared_min": 0.85,
    "relative_gamma_uncertainty_max": 0.5,
    "residual_sign_change_ratio_min": 0.0,
}

SUMMARY_PATH = (
    project_root / "data" / EXPERIMENT_TYPE / "run_summary.csv"
)
BATCH_TIMESTAMP = datetime.now().strftime("%m%d_%H%M")
BATCH_DIR = (
    project_root / "data" / EXPERIMENT_TYPE / f"{BATCH_TIMESTAMP}_{RUN_TAG_PREFIX}"
)
LOCAL_SUMMARY_CSV = BATCH_DIR / "optimization_local_summary.csv"
LOCAL_SUMMARY_PNG = BATCH_DIR / "optimization_local_summary.png"
LOCAL_SUMMARY_YAML = BATCH_DIR / "optimization_local_summary.yaml"


def validate_safety_limit(name, value):
    """检查输出量是否在安全范围内."""
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


def validate_all_parameters(params):
    """集中检查本脚本会写入硬件的全部输出参数."""
    for key in [
        "Pump_laser_power",
        "Probe_laser_power",
        "PUMP_MOD_DUTY",
        "temperature",
        "Temp_Switch",
        "Time_sequence",
        "main_magnetic_field",
        "X_magnetic_field",
        "Y_magnetic_field",
    ]:
        validate_safety_limit(key, params[key])
    validate_safety_limit(SCAN_VARIABLE, RAMP_LOW)
    validate_safety_limit(SCAN_VARIABLE, RAMP_HIGH)
    validate_safety_limit("Pump_modulation", PUMP_MOD_AMPLITUDE)


validate_all_parameters(BASELINE_PARAMS)
for seq in OPTIMIZE_SEQUENCE:
    for value in seq["values"]:
        validate_safety_limit(seq["parameter"], value)

print("配置和安全限值检查完成")
print(f"本轮批次目录: {BATCH_DIR}")

# %% Cell 3
def as_builtin(value):
    """将 numpy 标量和数组转换为 YAML/CSV 友好的 Python 对象."""
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {k: as_builtin(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [as_builtin(v) for v in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return str(value)
    return value


def format_value_for_tag(value):
    """生成适合目录名的参数值标签."""
    if isinstance(value, float):
        return f"{value:.4g}".replace("-", "m").replace(".", "p")
    return str(value).replace("-", "m").replace(".", "p")


def full_params_with_override(parameter=None, value=None):
    """返回基准参数加单个扫描参数覆盖后的完整参数字典."""
    params = dict(BASELINE_PARAMS)
    params["PUMP_MOD_AMPLITUDE"] = PUMP_MOD_AMPLITUDE
    params["PUMP_MOD_FREQ"] = PUMP_MOD_FREQ
    if parameter is not None:
        params[parameter] = value
    return params


def make_run_dir(iteration, parameter, value):
    """为单个扫描点创建独立运行目录."""
    timestamp = datetime.now().strftime("%m%d_%H%M")
    value_tag = format_value_for_tag(value)
    run_tag = f"{RUN_TAG_PREFIX}_{iteration:02d}_{parameter}_{value_tag}"
    run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{run_tag}"
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    raw_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    return timestamp, run_tag, run_dir, raw_dir, results_dir


def save_experiment_config(run_dir, run_tag, timestamp, params, actual_rates=None):
    """保存当前扫描点的配置快照."""
    config = {
        "experiment_type": EXPERIMENT_TYPE,
        "run_tag": run_tag,
        "timestamp": timestamp,
        "parameters": as_builtin(params),
        "optimize_sequence": as_builtin(OPTIMIZE_SEQUENCE),
        "baseline_params": as_builtin(BASELINE_PARAMS),
        "quality_thresholds": as_builtin(QUALITY_THRESHOLDS),
        "mapping_snapshot": MAPPING,
        "safety_limits_snapshot": LIMITS,
        "actual_rates": as_builtin(actual_rates or {}),
        "data_files": [],
    }
    with open(run_dir / "experiment_config.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)


def update_experiment_config(run_dir, **updates):
    """增量更新 experiment_config.yaml."""
    path = run_dir / "experiment_config.yaml"
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    config.update(as_builtin(updates))
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)


print("工具函数已定义")

# %% Cell 4
devices = {}

try:
    gs_cfg = MAPPING["main_magnetic_field"]
    gs = GS200Instrument(gs_cfg["resource"])
    gs.connect()
    gs.set_source_function(gs_cfg["source_function"])
    gs.set_current_limit(LIMITS["main_magnetic_field"]["max"] / 1000.0)
    devices["gs200"] = gs

    dg_sweep_cfg = MAPPING["Z_magnetic_field"]
    dg_sweep = create_signal_generator(dg_sweep_cfg["resource"], channel=1)
    dg_sweep.connect()
    dg_sweep.set_ref_clock_source("EXTernal")
    devices["dg_sweep"] = dg_sweep

    dg_laser_cfg = MAPPING["Pump_laser_power"]
    dg_laser = create_signal_generator(dg_laser_cfg["resource"], channel=1)
    dg_laser.connect()
    devices["dg_laser"] = dg_laser

    dg_comp_cfg = MAPPING["X_magnetic_field"]
    dg_comp = create_signal_generator(dg_comp_cfg["resource"], channel=1)
    dg_comp.connect()
    dg_comp.set_ref_clock_source("EXTernal")
    devices["dg_comp"] = dg_comp

    dg_mod_cfg = MAPPING["Pump_modulation"]
    dg_mod = create_signal_generator(dg_mod_cfg["resource"], channel=1)
    dg_mod.connect()
    devices["dg_mod"] = dg_mod

    dg_temp_cfg = MAPPING["Temp_Switch"]
    dg_temp = create_signal_generator(dg_temp_cfg["resource"], channel=2)
    dg_temp.connect()
    devices["dg_temp"] = dg_temp

    tec_cfg = MAPPING["temperature"]
    tec = TECInstrument(port=tec_cfg["resource"])
    tec.connect()
    devices["tec"] = tec

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

except Exception as e:
    print(f"设备连接失败: {e}")
    for dev in set(devices.values()):
        try:
            if hasattr(dev, "disconnect"):
                dev.disconnect()
        except Exception:
            pass
    raise

print(f"设备连接完成: {list(devices.keys())}")

# %% Cell 5
state = {
    "phase": 0.0,
    "actual_rate": float(HF2_DEMOD_RATE),
    "actual_rate_noise": float(HF2_NOISE_RATE),
}


def wait_for_temperature_stable(tec, target_c):
    """等待气室温度进入目标范围."""
    validate_safety_limit("temperature", target_c)
    start = time.time()
    while True:
        temp_now = tec.get_temperature(channel=1)
        delta = abs(temp_now - target_c)
        print(f"  温度: {temp_now:.2f} C, 目标: {target_c:.2f} C, 偏差: {delta:.2f} C")
        if delta <= TEMP_TOLERANCE_C:
            return temp_now
        if time.time() - start > MAX_TEMP_WAIT_S:
            raise TimeoutError(
                f"温度在 {MAX_TEMP_WAIT_S}s 内未稳定到 ±{TEMP_TOLERANCE_C} C"
            )
        time.sleep(5)


def setup_pump_modulation(dg_mod, duty_pct):
    """配置 RF 开关 Pump 调制."""
    validate_safety_limit("PUMP_MOD_DUTY", duty_pct)
    validate_safety_limit("Pump_modulation", PUMP_MOD_AMPLITUDE)
    dg_mod.setup_sine(
        freq=100e6,
        amplitude=PUMP_MOD_AMPLITUDE,
        offset=0.0,
        phase=0.0,
        channel=1,
    )
    pulse_width = (float(duty_pct) / 100.0) / PUMP_MOD_FREQ
    dg_mod.setup_pulse(
        freq=PUMP_MOD_FREQ,
        amplitude=RF_GATE_AMPLITUDE,
        offset=RF_GATE_OFFSET,
        width=pulse_width,
        channel=2,
    )
    print(
        f"  Pump 调制: {PUMP_MOD_FREQ/1e3:.0f} kHz, "
        f"duty={duty_pct}%, width={pulse_width*1e6:.2f} us"
    )


def apply_all_params(devs, params):
    """把完整参数写入硬件."""
    validate_all_parameters(params)
    dg_laser = devs["dg_laser"]
    dg_comp = devs["dg_comp"]
    dg_sweep = devs["dg_sweep"]
    dg_mod = devs["dg_mod"]
    dg_temp = devs["dg_temp"]
    gs = devs["gs200"]
    tec = devs["tec"]

    dg_laser.setup_dc(params["Pump_laser_power"], channel=1)
    dg_laser.setup_dc(params["Probe_laser_power"], channel=2)
    print(
        f"  光功率: Pump={params['Pump_laser_power']} V, "
        f"Probe={params['Probe_laser_power']} V"
    )

    dg_comp.setup_dc(params["X_magnetic_field"], channel=1)
    dg_comp.setup_dc(params["Y_magnetic_field"], channel=2)
    dg_comp.set_output(abs(params["X_magnetic_field"]) > 0, channel=1)
    dg_comp.set_output(abs(params["Y_magnetic_field"]) > 0, channel=2)

    gs.set_current(params["main_magnetic_field"] / 1000.0)
    gs.set_output(True)
    try:
        gs.set_current_range(GS200_CURRENT_RANGE_A)
        time.sleep(GS200_RANGE_SETTLE_TIME)
    except Exception as e:
        print(f"  GS200 量程设置跳过: {e}")
    print(f"  主磁场: {params['main_magnetic_field']} mA")

    tec.set_target_temperature(params["temperature"], channel=1)
    tec.set_enable(True, channel=1)

    dg_temp.setup_dc(params["Temp_Switch"], channel=2)
    dg_temp.set_output(True, channel=2)

    dg_sweep.setup_square(
        freq=10.0,
        amplitude=params["Time_sequence"],
        offset=0.0,
        dcycle=50.0,
        channel=2,
    )
    dg_sweep.set_output(True, channel=2)

    setup_pump_modulation(dg_mod, params["PUMP_MOD_DUTY"])


def restore_measurement_safe_state(devs):
    """恢复单次测量相关的危险输出和 HF2 解调器配置."""
    try:
        devs["dg_sweep"].set_output(False, channel=1)
        devs["dg_sweep"].set_sync_state(False, channel=1)
    except Exception as e:
        print(f"  Z 场恢复失败: {e}")
    try:
        devs["dg_temp"].set_output(True, channel=2)
    except Exception as e:
        print(f"  温度开关恢复失败: {e}")
    try:
        restore_cfg = DemodulatorConfig(
            demod_index=HF2_DEMOD_IDX,
            enable=True,
            rate=HF2_DEMOD_RATE,
            input_channel=0,
            osc_select=0,
            harmonic=1,
            time_constant=HF2_DEMOD_TC,
            order=HF2_DEMOD_ORDER,
            phase=state["phase"],
        )
        state["actual_rate"] = demod.configure_demodulator(
            devs["hf2"], restore_cfg
        )
    except Exception as e:
        print(f"  HF2 解调器恢复失败: {e}")


def calibrate_phase(devs):
    """执行 HF2 解调器相位校准."""
    hfi = devs["hf2"]
    dg_temp = devs["dg_temp"]
    dg_sweep = devs["dg_sweep"]

    print("  相位校准: 关闭温度开关和 Z 场")
    dg_temp.set_output(False, channel=2)
    dg_sweep.setup_dc(0.0, channel=1)
    dg_sweep.set_output(False, channel=1)

    sig_in_cfg = SignalInputConfig(
        input_index=0,
        range=HF2_SIGNAL_RANGE,
        ac_coupling=True,
        diff=False,
        impedance=50,
    )
    demod.configure_signal_input(hfi, sig_in_cfg)

    osc_cfg = OscillatorConfig(
        osc_index=0,
        frequency=HF2_OSC_FREQ,
        source="manual",
    )
    demod.configure_oscillator(hfi, osc_cfg)

    demod_cfg = DemodulatorConfig(
        demod_index=HF2_DEMOD_IDX,
        enable=True,
        rate=HF2_DEMOD_RATE,
        input_channel=0,
        osc_select=0,
        harmonic=1,
        time_constant=HF2_DEMOD_TC,
        order=HF2_DEMOD_ORDER,
        phase=0.0,
    )
    actual_rate = demod.configure_demodulator(hfi, demod_cfg)
    phase = demod.auto_calibrate_phase(
        hfi,
        demod_idx=HF2_DEMOD_IDX,
        tolerance_deg=1.0,
        max_attempts=5,
        settle_time=0.2,
    )
    sample = demod.read_demod_sample(hfi, demod_idx=HF2_DEMOD_IDX)
    dg_temp.set_output(True, channel=2)
    time.sleep(0.5)

    state["phase"] = float(phase)
    state["actual_rate"] = float(actual_rate)
    print(
        f"  相位={phase:.2f} deg, rate={actual_rate:.0f} Sa/s, "
        f"R={sample['r']:.6f}, X={sample['x']:.6f}, Y={sample['y']:.6f}"
    )
    return state["phase"], state["actual_rate"]


print("硬件设置函数已定义")

# %% Cell 6
def acquire_dispersion(devs, raw_dir):
    """执行 Z 场连续扫场并保存色散数据."""
    hfi = devs["hf2"]
    dg_sweep = devs["dg_sweep"]
    dg_temp = devs["dg_temp"]

    try:
        dg_sweep.setup_ramp(
            freq=RAMP_FREQ,
            amplitude=(RAMP_HIGH - RAMP_LOW),
            offset=0.0,
            symmetry=RAMP_SYMMETRY,
            channel=1,
        )
        dg_sweep.set_sync_state(True, channel=1)
        time.sleep(0.5)

        dg_temp.set_output(False, channel=2)
        time.sleep(1.0)

        aux_path = (
            f"/{MAPPING['lockin_r']['device_id']}/auxins/"
            f"{DAQ_TRIGGER_CHANNEL}/sample.AuxIn{DAQ_TRIGGER_CHANNEL}"
        )
        daq_cfg = DAQConfig(
            device=MAPPING["lockin_r"]["device_id"],
            trigger_type=1,
            trigger_channel=DAQ_TRIGGER_CHANNEL,
            trigger_level=DAQ_TRIGGER_LEVEL,
            trigger_slope=DAQ_TRIGGER_SLOPE,
            trigger_delay=0.0,
            duration=DAQ_DURATION,
            grid_cols=int(state["actual_rate"] * DAQ_DURATION),
            grid_rows=1,
            grid_mode=2,
            signal_paths=["sample.r", "sample.x", "sample.y"],
            extra_paths=[aux_path],
        )
        daq_results = daq.acquire_data(
            hfi,
            config=daq_cfg,
            demod_idx=HF2_DEMOD_IDX,
            actual_rate=state["actual_rate"],
            timeout=DAQ_DURATION + 10.0,
        )

    finally:
        dg_sweep.set_output(False, channel=1)
        dg_sweep.set_sync_state(False, channel=1)
        dg_temp.set_output(True, channel=2)

    recorded_data = {}
    for result in daq_results:
        if "auxin" in result.signal_name.lower():
            key = f"aux_{result.signal_name}"
        else:
            key = result.signal_name
        recorded_data[key] = result.values
        recorded_data[f"{key}_time"] = result.time

    if not daq_results:
        raise RuntimeError("HF2 DAQ 未返回任何色散数据")

    t = daq_results[0].time
    t_norm = t / (1.0 / RAMP_FREQ)
    t_cycle = t_norm % 1.0
    s = RAMP_SYMMETRY / 100.0
    if s >= 1.0:
        z_voltage = RAMP_LOW + (RAMP_HIGH - RAMP_LOW) * t_cycle
    elif s <= 0.0:
        z_voltage = RAMP_HIGH - (RAMP_HIGH - RAMP_LOW) * t_cycle
    else:
        rising = t_cycle < s
        z_voltage = np.where(
            rising,
            RAMP_LOW + (RAMP_HIGH - RAMP_LOW) * t_cycle / s,
            RAMP_HIGH - (RAMP_HIGH - RAMP_LOW) * (t_cycle - s) / (1.0 - s),
        )

    recorded_data["Z_voltage"] = z_voltage
    recorded_data["t_norm"] = t_cycle
    recorded_data["rising_mask"] = t_cycle < s
    recorded_data["effective_mask"] = (t_cycle > 0.1) & (t_cycle < 0.9)

    np.savez(raw_dir / "scan_data.npz", **recorded_data)
    return recorded_data


def fit_dispersion(recorded_data):
    """对下降段 Y 色散曲线做拟合."""
    s = RAMP_SYMMETRY / 100.0
    t_cycle = recorded_data["t_norm"]
    fall_start = s + (1.0 - s) * 0.1
    fall_end = s + (1.0 - s) * 0.9
    fall_mask = (t_cycle >= fall_start) & (t_cycle < fall_end)

    v_fit = recorded_data["Z_voltage"][fall_mask]
    y_raw = recorded_data["sample.y"][fall_mask]
    return fit_dispersive(
        v_fit,
        y_raw,
        Z_V_TO_NT,
        Z_V_TO_FT,
        quality_thresholds=QUALITY_THRESHOLDS,
    )


def acquire_noise(devs, raw_dir):
    """采集 Y 噪声并计算平均 PSD."""
    hfi = devs["hf2"]
    dg_sweep = devs["dg_sweep"]
    dg_temp = devs["dg_temp"]
    noise_root_dir = raw_dir / "noise_raw"
    noise_root_dir.mkdir(exist_ok=True)

    noise_demod_cfg = DemodulatorConfig(
        demod_index=HF2_DEMOD_IDX,
        enable=True,
        rate=HF2_NOISE_RATE,
        input_channel=0,
        osc_select=0,
        harmonic=1,
        time_constant=HF2_NOISE_TC,
        order=HF2_DEMOD_ORDER,
        phase=state["phase"],
    )
    state["actual_rate_noise"] = float(
        demod.configure_demodulator(hfi, noise_demod_cfg)
    )
    time.sleep(0.3)

    dg_sweep.setup_dc(0.0, channel=1)
    dg_sweep.set_output(False, channel=1)
    time.sleep(0.5)

    fs_noise = state["actual_rate_noise"]
    nperseg = int(fs_noise * NOISE_DURATION)

    try:
        from tqdm import tqdm
        iterator = tqdm(range(NOISE_N_AVG), desc="采集噪声")
    except ImportError:
        iterator = range(NOISE_N_AVG)

    try:
        for idx in iterator:
            dg_temp.set_output(False, channel=2)
            time.sleep(0.5)
            noise_cfg = DAQConfig(
                device=MAPPING["lockin_r"]["device_id"],
                trigger_type=0,
                duration=NOISE_DURATION,
                grid_cols=int(fs_noise * NOISE_DURATION),
                grid_rows=1,
                grid_mode=2,
                signal_paths=["sample.y"],
            )
            noise_results = daq.acquire_data(
                hfi,
                config=noise_cfg,
                demod_idx=HF2_DEMOD_IDX,
                actual_rate=fs_noise,
                timeout=NOISE_DURATION + 5.0,
            )
            dg_temp.set_output(True, channel=2)
            time.sleep(2)
            y = noise_results[0].values
            np.save(noise_root_dir / f"noise_{idx:04d}.npy", y)
            del y, noise_results

        psd_sum = None
        freq = None
        for idx in range(NOISE_N_AVG):
            y = np.load(noise_root_dir / f"noise_{idx:04d}.npy")
            freq_this, psd = scipy_signal.welch(
                y - np.mean(y),
                fs=fs_noise,
                nperseg=nperseg,
                scaling="density",
            )
            if psd_sum is None:
                freq = freq_this
                psd_sum = psd.copy()
            else:
                psd_sum += psd
            del y, psd

        psd_avg = psd_sum / NOISE_N_AVG
        freq_resolution = float(freq[1] - freq[0])
        np.savez(
            raw_dir / "noise_data.npz",
            psd_avg=psd_avg,
            freq=freq,
            fs=fs_noise,
            n_avg=NOISE_N_AVG,
        )
        return psd_avg, freq, freq_resolution

    finally:
        restore_measurement_safe_state(devs)


print("采集和分析函数已定义")

# %% Cell 7
def save_point_analysis(run_dir, params, fit_result, sens_result, freq_resolution):
    """保存单个扫描点的 YAML 和 NPZ 结果."""
    results_dir = run_dir / "results"
    analysis = {
        "parameters": as_builtin(params),
        "B0_fT": fit_result.B0_fT,
        "slope_V_per_fT": fit_result.slope_V_per_fT,
        "sens_flat_fT_per_sqrt_Hz": sens_result.sens_flat,
        "gamma_fT": fit_result.gamma_fT,
        "gamma_nT": fit_result.gamma_nT,
        "f_larmor_Hz": fit_result.f_larmor_Hz,
        "fit_r_squared": fit_result.r_squared,
        "fit_rmse": fit_result.rmse,
        "fit_is_valid": fit_result.is_valid,
        "rejection_reasons": fit_result.rejection_reasons,
        "relative_gamma_uncertainty": fit_result.relative_gamma_uncertainty,
        "n_avg": NOISE_N_AVG,
        "freq_resolution_Hz": freq_resolution,
        "phase_deg": state["phase"],
        "actual_rate": state["actual_rate"],
        "actual_rate_noise": state["actual_rate_noise"],
    }
    with open(results_dir / "analysis.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(as_builtin(analysis), f, allow_unicode=True, sort_keys=False)
    np.savez(
        results_dir / "sensitivity_data.npz",
        freq=sens_result.freq,
        sens_raw=sens_result.sens_raw,
        sens_corrected=sens_result.sens_corrected,
        flat_mask=sens_result.flat_mask,
        correction_factor=sens_result.correction_factor,
        sens_flat=sens_result.sens_flat,
    )


def run_single_point(iteration, parameter, value):
    """执行一个扫描点的完整测量."""
    timestamp, run_tag, run_dir, raw_dir, _ = make_run_dir(
        iteration, parameter, value
    )
    params = full_params_with_override(parameter, value)
    save_experiment_config(run_dir, run_tag, timestamp, params)

    result = {
        "iteration": iteration,
        "parameter": parameter,
        "value": value,
        "run_dir": str(run_dir),
        "run_tag": run_tag,
        "success": False,
        "error": "",
        "fit_is_valid": False,
        "fit_rejection_reasons": "",
        "sens_flat_fT_per_sqrt_Hz": float("nan"),
        "fit_r2": float("nan"),
        "gamma_nT": float("nan"),
        "slope_V_per_fT": float("nan"),
        "is_baseline": params["Pump_laser_power"] == BASELINE_PARAMS["Pump_laser_power"]
        and params["Probe_laser_power"] == BASELINE_PARAMS["Probe_laser_power"]
        and params["PUMP_MOD_DUTY"] == BASELINE_PARAMS["PUMP_MOD_DUTY"],
    }

    try:
        print(f"\n--- [{iteration:02d}] {parameter} = {value} ---")
        print("  恢复基准并应用扫描参数...")
        apply_all_params(devices, params)
        time.sleep(2.0)
        wait_for_temperature_stable(devices["tec"], params["temperature"])

        print("  重新校准 HF2 相位...")
        calibrate_phase(devices)

        print("  采集色散曲线...")
        recorded_data = acquire_dispersion(devices, raw_dir)
        fit_result = fit_dispersion(recorded_data)

        print("  采集噪声并计算 PSD...")
        psd_avg, freq, freq_resolution = acquire_noise(devices, raw_dir)
        sens_result = compute_sensitivity(
            psd_avg,
            freq,
            slope_V_per_fT=fit_result.slope_V_per_fT,
            f_larmor_Hz=fit_result.f_larmor_Hz,
        )

        save_point_analysis(run_dir, params, fit_result, sens_result, freq_resolution)
        write_run_record(
            run_dir,
            params,
            fit_result,
            sens_result,
            summary_path=SUMMARY_PATH,
            timestamp=timestamp,
            run_tag=run_tag,
            n_avg=NOISE_N_AVG,
            freq_resolution_Hz=freq_resolution,
        )

        update_experiment_config(
            run_dir,
            actual_rates={
                "hf2_demod_rate": state["actual_rate"],
                "hf2_noise_rate": state["actual_rate_noise"],
            },
            data_files=[
                "raw/scan_data.npz",
                "raw/noise_data.npz",
                "raw/noise_raw/*.npy",
                "results/analysis.yaml",
                "results/analysis.json",
                "results/sensitivity_data.npz",
            ],
        )

        result.update(
            {
                "success": fit_result.is_valid,
                "fit_is_valid": fit_result.is_valid,
                "fit_rejection_reasons": "; ".join(fit_result.rejection_reasons),
                "sens_flat_fT_per_sqrt_Hz": sens_result.sens_flat,
                "fit_r2": fit_result.r_squared,
                "gamma_nT": fit_result.gamma_nT,
                "slope_V_per_fT": fit_result.slope_V_per_fT,
            }
        )

        status = "OK" if fit_result.is_valid else "REJ"
        print(
            f"  [{status}] Sens={sens_result.sens_flat:.1f} fT/sqrtHz, "
            f"R^2={fit_result.r_squared:.3f}, "
            f"HWHM={fit_result.gamma_nT:.2f} nT"
        )

    except Exception as e:
        result["error"] = str(e)
        print(f"  [ERR] {e}")

    finally:
        restore_measurement_safe_state(devices)

    return result


def write_local_summary(results, decision):
    """保存本轮局部优化的 CSV/YAML 汇总."""
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "iteration",
        "parameter",
        "value",
        "is_baseline",
        "fit_is_valid",
        "success",
        "sens_flat_fT_per_sqrt_Hz",
        "fit_r2",
        "gamma_nT",
        "slope_V_per_fT",
        "fit_rejection_reasons",
        "error",
        "run_tag",
        "run_dir",
    ]
    with open(LOCAL_SUMMARY_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            writer.writerow({key: as_builtin(row.get(key, "")) for key in fieldnames})

    payload = {
        "batch_timestamp": BATCH_TIMESTAMP,
        "baseline_params": BASELINE_PARAMS,
        "optimize_sequence": OPTIMIZE_SEQUENCE,
        "decision": decision,
        "results": results,
    }
    with open(LOCAL_SUMMARY_YAML, "w", encoding="utf-8") as f:
        yaml.safe_dump(as_builtin(payload), f, allow_unicode=True, sort_keys=False)


def plot_local_summary(results, decision):
    """绘制本轮优化汇总图."""
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(3, 1, figsize=(8, 10), sharey=False)

    for ax, seq in zip(axes, OPTIMIZE_SEQUENCE):
        parameter = seq["parameter"]
        subset = [r for r in results if r["parameter"] == parameter]
        values = np.array([float(r["value"]) for r in subset], dtype=float)
        sens = np.array([r["sens_flat_fT_per_sqrt_Hz"] for r in subset], dtype=float)
        valid = np.array([bool(r["fit_is_valid"]) for r in subset], dtype=bool)

        ax.scatter(values[valid], sens[valid], color="tab:green", label="Valid")
        if np.any(~valid):
            ax.scatter(values[~valid], sens[~valid], color="tab:red", label="Rejected")
        if np.sum(valid) > 1:
            order = np.argsort(values[valid])
            ax.plot(values[valid][order], sens[valid][order], "g--", alpha=0.6)
        ax.axvline(BASELINE_PARAMS[parameter], color="gray", ls=":", alpha=0.6)
        if decision.get("baseline_median") is not None:
            ax.axhline(
                decision["baseline_median"],
                color="gray",
                ls="--",
                alpha=0.5,
                label="Baseline median",
            )
        ax.set_xlabel(parameter)
        ax.set_ylabel("Sensitivity (fT/sqrtHz)")
        ax.set_title(f"Sensitivity vs {parameter}")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)

    plt.tight_layout()
    fig.savefig(LOCAL_SUMMARY_PNG, dpi=150, bbox_inches="tight")
    plt.close(fig)


def decide_best_result(results):
    """根据有效点和基准重复点给出本轮优化结论."""
    valid_results = [
        r for r in results
        if r["fit_is_valid"] and np.isfinite(r["sens_flat_fT_per_sqrt_Hz"])
    ]
    baseline_valid = [r for r in valid_results if r["is_baseline"]]

    decision = {
        "baseline_valid_count": len(baseline_valid),
        "baseline_median": None,
        "baseline_drift_ratio": None,
        "baseline_drift_warning": False,
        "best_run": None,
        "best_improvement_ratio": None,
        "recommendation": "无有效结果，维持当前基准",
    }

    if baseline_valid:
        baseline_values = np.array(
            [r["sens_flat_fT_per_sqrt_Hz"] for r in baseline_valid],
            dtype=float,
        )
        baseline_median = float(np.median(baseline_values))
        decision["baseline_median"] = baseline_median
        if len(baseline_values) >= 2 and baseline_median > 0:
            drift = float((np.max(baseline_values) - np.min(baseline_values)) / baseline_median)
            decision["baseline_drift_ratio"] = drift
            decision["baseline_drift_warning"] = drift > 0.30

    if valid_results:
        best = min(valid_results, key=lambda r: r["sens_flat_fT_per_sqrt_Hz"])
        decision["best_run"] = best
        if decision["baseline_median"] and decision["baseline_median"] > 0:
            improvement = (
                decision["baseline_median"] - best["sens_flat_fT_per_sqrt_Hz"]
            ) / decision["baseline_median"]
            decision["best_improvement_ratio"] = float(improvement)
            if improvement >= 0.10:
                decision["recommendation"] = "采用最佳扫描点"
            else:
                decision["recommendation"] = "维持当前基准"
        else:
            decision["recommendation"] = "采用最佳有效点，但缺少有效基准重复点"

    return decision


print("单点测量和汇总函数已定义")

# %% Cell 8
print("=" * 60)
print("静磁场灵敏度局部优化开始")
print("=" * 60)

BATCH_DIR.mkdir(parents=True, exist_ok=True)
all_results = []
iteration = 0
t_start = datetime.now()

try:
    for seq in OPTIMIZE_SEQUENCE:
        parameter = seq["parameter"]
        stabilize_time = seq.get("stabilize_time", 2.0)
        print(f"\n扫描参数: {parameter}, values={seq['values']}")

        for value in seq["values"]:
            iteration += 1
            result = run_single_point(iteration, parameter, value)
            all_results.append(result)
            time.sleep(stabilize_time)

            valid_so_far = [
                r for r in all_results
                if r["fit_is_valid"] and np.isfinite(r["sens_flat_fT_per_sqrt_Hz"])
            ]
            if valid_so_far:
                best_so_far = min(
                    valid_so_far,
                    key=lambda r: r["sens_flat_fT_per_sqrt_Hz"],
                )
                print(
                    f"  当前最优: {best_so_far['parameter']}={best_so_far['value']}, "
                    f"Sens={best_so_far['sens_flat_fT_per_sqrt_Hz']:.1f} fT/sqrtHz"
                )

finally:
    restore_measurement_safe_state(devices)
    elapsed_min = (datetime.now() - t_start).total_seconds() / 60.0
    print(f"\n优化扫描结束，耗时 {elapsed_min:.1f} min")

decision = decide_best_result(all_results)
write_local_summary(all_results, decision)
plot_local_summary(all_results, decision)

print("=" * 60)
print("本轮优化结论")
print("=" * 60)
print(f"有效基准重复点: {decision['baseline_valid_count']}/3")
if decision["baseline_median"] is not None:
    print(f"基准中位灵敏度: {decision['baseline_median']:.1f} fT/sqrtHz")
if decision["baseline_drift_ratio"] is not None:
    print(f"基准漂移: {decision['baseline_drift_ratio']:.1%}")
    if decision["baseline_drift_warning"]:
        print("警告: 基准重复点漂移超过 30%，本轮优化可靠性偏低")
if decision["best_run"] is not None:
    best = decision["best_run"]
    print(
        f"最佳有效点: {best['parameter']}={best['value']}, "
        f"Sens={best['sens_flat_fT_per_sqrt_Hz']:.1f} fT/sqrtHz, "
        f"run={best['run_tag']}"
    )
    if decision["best_improvement_ratio"] is not None:
        print(f"相对基准改善: {decision['best_improvement_ratio']:.1%}")
print(f"建议: {decision['recommendation']}")
print(f"本轮 CSV: {LOCAL_SUMMARY_CSV}")
print(f"本轮图表: {LOCAL_SUMMARY_PNG}")

# %% Cell 9
print("正在断开 TEC...")
tec = devices.get("tec")
if tec and hasattr(tec, "disconnect"):
    try:
        tec.disconnect()
        print("  TEC 已断开")
    except Exception as e:
        print(f"  TEC 断开失败: {e}")
print("其他设备保持连接")
