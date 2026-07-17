# %% [markdown] Cell 0
# # 静磁场灵敏度 — 参数优化扫描
#
# 对以下可调参数进行扫描，自动寻找最优灵敏度：
#
# | 参数 | 安全范围 | 单位 |
# |------|----------|------|
# | Pump_laser_power | 0~1.0 | V DC |
# | Probe_laser_power | 0~1.0 | V DC |
# | X_magnetic_field | -10~10 | V |
# | Y_magnetic_field | -10~10 | V |
# | main_magnetic_field | 0~10 | mA |
# | PUMP_MOD_DUTY | 1~50 | % |
#
# ## 使用说明
# 1. 在 Cell 2 的 `OPTIMIZE_SEQUENCE` 中配置要扫描的参数和取值
# 2. 逐个 Cell 运行（或全部运行）
# 3. 结果自动保存到 `data/Static_Magnetic_Field_Sensitivity/run_summary.csv`
# 4. Cell 9 自动生成汇总图表
#
# ## 质量判定
# 每次测量的色散拟合会经过质量门控，无效数据点的灵敏度值不可靠，
# 在汇总中会标记 `fit_is_valid=False`，但仍会记录供排查。

# %% Cell 1
from pathlib import Path
import sys
project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import numpy as np
import yaml
import time
from datetime import datetime
import matplotlib.pyplot as plt
from scipy import signal as scipy_signal

from gs200 import GS200Instrument
from lab_workflows.devices import create_signal_generator
from tec_controller import TECInstrument
from lockin_amplifier import (
    HF2Instrument, DAQConfig, DAQResult,
    SignalInputConfig, OscillatorConfig, DemodulatorConfig,
    demod, daq,
)
from sensitivity_analysis import (
    fit_dispersive, compute_sensitivity, write_run_record,
    SummaryLogger,
)

print("所有库导入成功")

# %% Cell 2
# ========== 加载配置 ==========
with open(project_root / "params" / "mapping.yaml", encoding="utf-8") as f:
    MAPPING = yaml.safe_load(f)["mapping"]

with open(project_root / "params" / "safety_limits.yaml", encoding="utf-8") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]

EXPERIMENT_TYPE = "Static_Magnetic_Field_Sensitivity"

# ========== 固定参数 (非扫描项) ==========
FIXED_PARAMS = {
    "Pump_laser_power": 0.2,
    "Probe_laser_power": 0.2,
    "temperature": 100,
    "Temp_Switch": 5.0,
    "Time_sequence": 10.0,
    "main_magnetic_field": 9.3,
    "X_magnetic_field": 0,
    "Y_magnetic_field": 0,
    "Time_sequence_2": 0.0,
}

PUMP_MOD_FREQ = 90e3
PUMP_MOD_AMPLITUDE = 0.18
PUMP_MOD_DUTY = 5

RF_GATE_AMPLITUDE = 5.0
RF_GATE_OFFSET = 2.5
RF_GATE_DELAY = 0.0

# ========== HF2 配置 ==========
HF2_DEMOD_IDX = 0
HF2_OSC_FREQ = PUMP_MOD_FREQ
HF2_DEMOD_ORDER = 4
HF2_SIGNAL_RANGE = 2.0
HF2_DEMOD_RATE = 1000
HF2_DEMOD_TC = 0.001
HF2_NOISE_RATE = 50000
HF2_NOISE_TC = 1e-6

# ========== 扫描配置 ==========
SCAN_VARIABLE = "Z_magnetic_field"
RAMP_LOW = -0.5
RAMP_HIGH = 0.5
RAMP_FREQ = 1.0
RAMP_SYMMETRY = 20
DAQ_DURATION = 1

NOISE_N_AVG = 5
NOISE_DURATION = 1.0

DAQ_TRIGGER_CHANNEL = 0
DAQ_TRIGGER_LEVEL = 1
DAQ_TRIGGER_SLOPE = 0

# ========== 磁场校准 ==========
Z_V_TO_NT = 3517
Z_V_TO_FT = Z_V_TO_NT * 1000000

# ========== 拟合质量阈值 ==========
QUALITY_THRESHOLDS = {
    "r_squared_min": 0.85,
    "relative_gamma_uncertainty_max": 0.5,
    "residual_sign_change_ratio_min": 0.0,  # 实测色散残差有平滑趋势，宽松门控
}

# =====================================================================
# ★ 在此配置优化扫描序列 ★
# 每个条目是一个参数维度, values 列表为要扫描的取值
# stabilize_time: 参数变化后等待时间 (s)
# recalibrate_phase: 是否在每次参数变化后重校准 HF2 相位
# =====================================================================
OPTIMIZE_SEQUENCE = [
    # 示例 1: 扫描 Pump 光功率
    {
        "parameter": "Pump_laser_power",
        "values": [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50],
        "stabilize_time": 2.0,
        "recalibrate_phase": True,
    },
    # 示例 2: 微调主磁场 (改变工作点, 可能改善线形对称性)
    {
        "parameter": "main_magnetic_field",
        "values": [8.8, 9.0, 9.15, 9.3, 9.45, 9.6, 9.8],
        "stabilize_time": 2.0,
        "recalibrate_phase": True,
    },
    # 示例 3: 扫描 Pump 调制占空比
    {
        "parameter": "PUMP_MOD_DUTY",
        "values": [2, 3, 5, 8, 10, 15, 20],
        "stabilize_time": 2.0,
        "recalibrate_phase": False,
    },
    # 示例 4: 扫描 Probe 光功率
    {
        "parameter": "Probe_laser_power",
        "values": [0.10, 0.15, 0.20, 0.30, 0.40],
        "stabilize_time": 2.0,
        "recalibrate_phase": True,
    },
    # 可按需添加/注释更多维度...
]

# 汇总 CSV 路径
SUMMARY_PATH = project_root / "data" / EXPERIMENT_TYPE / "run_summary.csv"

print(f"优化扫描配置已加载, 共 {len(OPTIMIZE_SEQUENCE)} 个维度")
total_iters = sum(len(s["values"]) for s in OPTIMIZE_SEQUENCE)
print(f"  预计总迭代次数: {total_iters}")
print(f"  汇总文件: {SUMMARY_PATH}")

# %% Cell 3
# ========== 安全边界检查 ==========
def validate_safety_limit(name, value):
    lim = LIMITS.get(name)
    if lim is None:
        return value
    lo, hi = lim["min"], lim["max"]
    if lo is not None and hi is not None:
        if value < lo or value > hi:
            raise ValueError(f"[安全拦截] {name}={value} 超出范围 [{lo}, {hi}]")
    return value

# ========== 连接所有设备 ==========
devices = {}

try:
    # GS200: 主磁场
    gs_cfg = MAPPING["main_magnetic_field"]
    gs = GS200Instrument(gs_cfg["resource"])
    gs.connect()
    print(f"GS200 已连接: {gs.idn()}")
    gs.set_source_function(gs_cfg["source_function"])
    gs.set_current_limit(LIMITS["main_magnetic_field"]["max"] / 1000.0)
    devices["gs200"] = gs

    # DG4000: Z 磁场扫描
    dg_sweep_cfg = MAPPING["Z_magnetic_field"]
    dg_sweep = create_signal_generator(dg_sweep_cfg["resource"], channel=1)
    dg_sweep.connect()
    print(f"Z 场 DG4000 已连接: {dg_sweep.idn()}")
    dg_sweep.set_ref_clock_source("EXTernal")
    devices["dg_sweep"] = dg_sweep

    # DG912 Pro: Pump/Probe 光功率
    dg_laser_cfg = MAPPING["Pump_laser_power"]
    dg_laser = create_signal_generator(dg_laser_cfg["resource"], channel=1)
    dg_laser.connect()
    print(f"光功率 DG912 Pro 已连接: {dg_laser.idn()}")
    devices["dg_laser"] = dg_laser

    # DG4000: X/Y 补偿磁场
    dg_comp_cfg = MAPPING["X_magnetic_field"]
    dg_comp = create_signal_generator(dg_comp_cfg["resource"], channel=1)
    dg_comp.connect()
    print(f"补偿场 DG4000 已连接: {dg_comp.idn()}")
    dg_comp.set_ref_clock_source("EXTernal")
    devices["dg_comp"] = dg_comp

    # DG4000: Pump 调制 + 时序
    dg_mod_cfg = MAPPING["Pump_modulation"]
    dg_mod = create_signal_generator(dg_mod_cfg["resource"], channel=1)
    dg_mod.connect()
    print(f"调制 DG4000 已连接: {dg_mod.idn()}")
    devices["dg_mod"] = dg_mod

    # DG912 Pro: 温度开关
    dg_temp_cfg = MAPPING["Temp_Switch"]
    dg_temp = create_signal_generator(dg_temp_cfg["resource"], channel=2)
    dg_temp.connect()
    print(f"温控 DG912 Pro 已连接: {dg_temp.idn()}")
    devices["dg_temp"] = dg_temp

    # TEC103: 温度控制器
    # tec_cfg = MAPPING["temperature"]
    # tec = TECInstrument(port=tec_cfg["resource"])
    # tec.connect()
    # print(f"TEC103 已连接")
    # devices["tec"] = tec

    # HF2: 锁相放大器
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

except Exception as e:
    print(f"设备连接失败: {e}")
    raise

print(f"\n所有设备连接完成, 共 {len(devices)} 个设备\n")

# %% Cell 4
# ========== 辅助函数：参数应用 ==========

def apply_parameters(devs, params_dict):
    """将参数字典中的值写入对应设备, 自动验证安全限值."""
    for name, val in params_dict.items():
        validate_safety_limit(name, val)

    dg_laser = devs["dg_laser"]
    dg_comp = devs["dg_comp"]
    gs = devs["gs200"]
    dg_mod = devs["dg_mod"]
    # tec = devs["tec"]

    if "Pump_laser_power" in params_dict:
        dg_laser.setup_dc(params_dict["Pump_laser_power"], channel=1)

    if "Probe_laser_power" in params_dict:
        dg_laser.setup_dc(params_dict["Probe_laser_power"], channel=2)

    if "X_magnetic_field" in params_dict:
        v = params_dict["X_magnetic_field"]
        dg_comp.setup_dc(v, channel=1)
        dg_comp.set_output(abs(v) > 0, channel=1)

    if "Y_magnetic_field" in params_dict:
        v = params_dict["Y_magnetic_field"]
        dg_comp.setup_dc(v, channel=2)
        dg_comp.set_output(abs(v) > 0, channel=2)

    if "main_magnetic_field" in params_dict:
        gs.set_current(params_dict["main_magnetic_field"] / 1000.0)

    # if "temperature" in params_dict:
    #     tec.set_target_temperature(params_dict["temperature"], channel=1)

    if "PUMP_MOD_DUTY" in params_dict:
        global PUMP_MOD_DUTY
        PUMP_MOD_DUTY = params_dict["PUMP_MOD_DUTY"]
        pulse_width = (PUMP_MOD_DUTY / 100.0) / PUMP_MOD_FREQ
        dg_mod.setup_pulse(freq=PUMP_MOD_FREQ, amplitude=RF_GATE_AMPLITUDE,
                           offset=RF_GATE_OFFSET, width=pulse_width, channel=2)


def setup_fixed_params(devs):
    """设置所有非扫描固定参数."""
    dg_laser = devs["dg_laser"]
    dg_comp = devs["dg_comp"]
    gs = devs["gs200"]
    # tec = devs["tec"]
    dg_temp = devs["dg_temp"]
    dg_sweep = devs["dg_sweep"]

    dg_laser.setup_dc(FIXED_PARAMS["Pump_laser_power"], channel=1)
    dg_laser.setup_dc(FIXED_PARAMS["Probe_laser_power"], channel=2)
    print(f"光功率: Pump={FIXED_PARAMS['Pump_laser_power']}V, Probe={FIXED_PARAMS['Probe_laser_power']}V")

    dg_comp.setup_dc(FIXED_PARAMS["X_magnetic_field"], channel=1)
    dg_comp.setup_dc(FIXED_PARAMS["Y_magnetic_field"], channel=2)
    dg_comp.set_output(abs(FIXED_PARAMS["X_magnetic_field"]) > 0, channel=1)
    dg_comp.set_output(abs(FIXED_PARAMS["Y_magnetic_field"]) > 0, channel=2)
    print(f"补偿磁场: X={FIXED_PARAMS['X_magnetic_field']}V, Y={FIXED_PARAMS['Y_magnetic_field']}V")

    gs.set_current(FIXED_PARAMS["main_magnetic_field"] / 1000.0)
    gs.set_output(True)
    print(f"主磁场: {FIXED_PARAMS['main_magnetic_field']} mA")

    # tec.set_target_temperature(FIXED_PARAMS["temperature"], channel=1)
    # tec.set_enable(True, channel=1)
    # print(f"温度设定: {FIXED_PARAMS['temperature']} °C")

    dg_temp.setup_dc(FIXED_PARAMS["Temp_Switch"], channel=2)
    print(f"温度开关: ON")

    dg_sweep.setup_square(freq=10.0, amplitude=FIXED_PARAMS["Time_sequence"],
                          offset=0.0, dcycle=50.0, channel=2)
    dg_sweep.set_output(True, channel=2)
    print(f"时序信号: 10Hz, dg_sweep CH2")

    print("固定参数设置完成\n")


def get_full_params(override=None):
    """获取完整的参数字典 (固定参数 + 覆盖)."""
    params = dict(FIXED_PARAMS)
    params["PUMP_MOD_DUTY"] = PUMP_MOD_DUTY
    params["PUMP_MOD_AMPLITUDE"] = PUMP_MOD_AMPLITUDE
    if override:
        params.update(override)
    return params


print("辅助函数已定义")

# %% Cell 5
# ========== RF 开关 Pump 调制配置 ==========

dg_mod = devices["dg_mod"]
dg_sweep = devices["dg_sweep"]

# CH1: 100MHz 连续正弦波
print("配置 Pump 调制 (RF 开关方案)...")
dg_mod.setup_sine(freq=100e6, amplitude=PUMP_MOD_AMPLITUDE, offset=0.0,
                  phase=0.0, channel=1)
print(f"  CH1: 100MHz, {PUMP_MOD_AMPLITUDE*1000:.0f} mVpp → RF 开关 IN")

# CH2: 脉冲门控
pulse_width = (PUMP_MOD_DUTY / 100.0) / PUMP_MOD_FREQ
dg_mod.setup_pulse(freq=PUMP_MOD_FREQ, amplitude=RF_GATE_AMPLITUDE,
                   offset=RF_GATE_OFFSET, width=pulse_width, channel=2)
print(f"  CH2: {PUMP_MOD_FREQ/1e3:.0f} kHz, duty={PUMP_MOD_DUTY}%, "
      f"{RF_GATE_AMPLITUDE:.1f}Vpp+{RF_GATE_OFFSET:.1f}V → RF 开关 CTRL")
print("RF 开关方案配置完成\n")


# ========== 相位校准函数 ==========
def calibrate_phase(hfi, dg_temp, dg_sweep):
    """执行 HF2 解调器 0 的相位校准, 返回 (calibrated_phase, actual_rate)."""
    print("  [校准] 关闭温控...")
    dg_temp.set_output(False, channel=2)

    print("  [校准] 关闭 Z 磁场...")
    dg_sweep.setup_dc(0.0, channel=1)
    dg_sweep.set_output(False, channel=1)

    sig_in_cfg = SignalInputConfig(
        input_index=0, range=HF2_SIGNAL_RANGE,
        ac_coupling=True, diff=False, impedance=50,
    )
    demod.configure_signal_input(hfi, sig_in_cfg)

    osc_cfg = OscillatorConfig(osc_index=0, frequency=HF2_OSC_FREQ, source="manual")
    demod.configure_oscillator(hfi, osc_cfg)

    demod_cfg = DemodulatorConfig(
        demod_index=0, enable=True, rate=HF2_DEMOD_RATE,
        input_channel=0, osc_select=0, harmonic=1,
        time_constant=HF2_DEMOD_TC, order=HF2_DEMOD_ORDER, phase=0.0,
    )
    actual_rate = demod.configure_demodulator(hfi, demod_cfg)

    phase = demod.auto_calibrate_phase(
        hfi, demod_idx=0, tolerance_deg=1.0, max_attempts=5, settle_time=0.2,
    )
    print(f"  [校准] 相位={phase:.2f}°, rate={actual_rate:.0f} Sa/s")

    sample = demod.read_demod_sample(hfi, demod_idx=0)
    print(f"  [校准] R={sample['r']:.6f}, X={sample['x']:.6f}, Y={sample['y']:.6f}")

    print("  [校准] 恢复温控...")
    dg_temp.set_output(True, channel=2)
    time.sleep(0.5)

    return phase, actual_rate


print("Pump 调制和校准函数已定义")

# %% Cell 6
# ========== 色散采集函数 ==========

def acquire_dispersion(devs, calibrated_phase, actual_rate, run_dir):
    """执行 Z 场扫场 + HF2 DAQ 触发采集, 返回 (daq_results, recorded_data)."""
    dg_sweep = devs["dg_sweep"]
    dg_temp = devs["dg_temp"]
    hfi = devs["hf2"]
    dg_mod = devs["dg_mod"]

    dg_sweep.setup_ramp(freq=RAMP_FREQ, amplitude=(RAMP_HIGH - RAMP_LOW),
                        offset=0.0, symmetry=RAMP_SYMMETRY, channel=1)
    dg_sweep.set_sync_state(True, channel=1)
    time.sleep(0.5)

    dg_temp.set_output(False, channel=2)
    time.sleep(1.0)

    aux_path = f"/{MAPPING['lockin_r']['device_id']}/auxins/{DAQ_TRIGGER_CHANNEL}/sample.AuxIn{DAQ_TRIGGER_CHANNEL}"
    daq_cfg = DAQConfig(
        device=MAPPING["lockin_r"]["device_id"],
        trigger_type=1, trigger_channel=DAQ_TRIGGER_CHANNEL,
        trigger_level=DAQ_TRIGGER_LEVEL, trigger_slope=DAQ_TRIGGER_SLOPE,
        trigger_delay=0.0, duration=DAQ_DURATION,
        grid_cols=int(actual_rate * DAQ_DURATION),
        grid_rows=1, grid_mode=2,
        signal_paths=["sample.r", "sample.x", "sample.y"],
        extra_paths=[aux_path],
    )

    daq_results = daq.acquire_data(
        hfi, config=daq_cfg, demod_idx=0,
        actual_rate=actual_rate, timeout=DAQ_DURATION + 10.0,
    )

    dg_sweep.set_output(False, channel=1)
    dg_sweep.set_sync_state(False, channel=1)
    dg_temp.set_output(True, channel=2)
    time.sleep(2)

    # 解析 recorded_data
    T_ramp = 1.0 / RAMP_FREQ
    recorded_data = {}
    for result in daq_results:
        if "auxin" in result.signal_name.lower():
            recorded_data[f"aux_{result.signal_name}"] = result.values
            recorded_data[f"aux_{result.signal_name}_time"] = result.time
        else:
            recorded_data[result.signal_name] = result.values
            recorded_data[f"{result.signal_name}_time"] = result.time

    if len(daq_results) > 0:
        t = daq_results[0].time
        s = RAMP_SYMMETRY / 100.0
        t_norm = t / T_ramp

        if s >= 1.0:
            V_z = RAMP_LOW + (RAMP_HIGH - RAMP_LOW) * t_norm
        elif s <= 0.0:
            V_z = RAMP_HIGH - (RAMP_HIGH - RAMP_LOW) * t_norm
        else:
            rising = t_norm % 1.0 < s
            tn = t_norm % 1.0
            V_z = np.where(rising,
                RAMP_LOW + (RAMP_HIGH - RAMP_LOW) * tn / s,
                RAMP_HIGH - (RAMP_HIGH - RAMP_LOW) * (tn - s) / (1.0 - s))

        recorded_data["Z_voltage"] = V_z
        recorded_data["t_norm"] = t_norm % 1.0
        recorded_data["rising_mask"] = (t_norm % 1.0) < s
        recorded_data["effective_mask"] = ((t_norm % 1.0) > 0.1) & ((t_norm % 1.0) < 0.9)

    np.savez(run_dir / "raw" / "scan_data.npz", **recorded_data)
    return daq_results, recorded_data


print("色散采集函数已定义")

# %% Cell 7
# ========== 噪声采集函数 ==========

def acquire_noise(devs, recorded_data, calibrated_phase, run_dir):
    """执行多次噪声采集 + PSD 平均, 返回 psd_avg, freq, n_avg, freq_resolution."""
    hfi = devs["hf2"]
    dg_sweep = devs["dg_sweep"]
    dg_temp = devs["dg_temp"]

    # 找零交叉点
    mask = recorded_data["effective_mask"] & recorded_data["rising_mask"]
    V_rising = recorded_data["Z_voltage"][mask]
    Y_rising = recorded_data["sample.y"][mask]

    sign_changes = np.where(np.diff(np.sign(Y_rising)))[0]
    if len(sign_changes) > 0:
        idx = sign_changes[len(sign_changes) // 2]
        v1, v2 = float(V_rising[idx]), float(V_rising[idx + 1])
        y1, y2 = float(Y_rising[idx]), float(Y_rising[idx + 1])
        V0 = v1 - y1 * (v2 - v1) / (y2 - y1)
    else:
        V0 = 0.0

    # 配置高速率解调器
    noise_demod_cfg = DemodulatorConfig(
        demod_index=HF2_DEMOD_IDX, enable=True,
        rate=HF2_NOISE_RATE, input_channel=0,
        osc_select=0, harmonic=1,
        time_constant=HF2_NOISE_TC, order=HF2_DEMOD_ORDER,
        phase=calibrated_phase,
    )
    actual_rate_noise = demod.configure_demodulator(hfi, noise_demod_cfg)
    time.sleep(0.3)

    # 关闭 Z 磁场
    dg_sweep.setup_dc(0.0, channel=1)
    dg_sweep.set_output(False, channel=1)
    time.sleep(0.5)

    fs_noise = float(actual_rate_noise)
    noise_dir = run_dir / "raw" / "noise_raw"
    noise_dir.mkdir(parents=True, exist_ok=True)

    try:
        from tqdm import tqdm
    except ImportError:
        tqdm = lambda x, **kw: x

    for i in tqdm(range(NOISE_N_AVG), desc="  Noise acq"):
        dg_temp.set_output(False, channel=2)
        noise_cfg = DAQConfig(
            device=MAPPING["lockin_r"]["device_id"],
            trigger_type=0, duration=NOISE_DURATION,
            grid_cols=int(fs_noise * NOISE_DURATION),
            grid_rows=1, grid_mode=2, signal_paths=["sample.y"],
        )
        noise_results = daq.acquire_data(
            hfi, config=noise_cfg, demod_idx=HF2_DEMOD_IDX,
            actual_rate=fs_noise, timeout=NOISE_DURATION + 5.0,
        )
        dg_temp.set_output(True, channel=2)
        time.sleep(2)

        y = noise_results[0].values
        np.save(noise_dir / f"noise_{i:04d}.npy", y)
        del y, noise_results

    # 累积 PSD
    psd_sum = None
    freq = None
    nperseg = int(fs_noise * NOISE_DURATION)

    for i in range(NOISE_N_AVG):
        y = np.load(noise_dir / f"noise_{i:04d}.npy")
        f, p = scipy_signal.welch(y - np.mean(y), fs=fs_noise,
                                   nperseg=nperseg, scaling="density")
        if psd_sum is None:
            freq = f
            psd_sum = p.copy()
        else:
            psd_sum += p
        del y, p

    psd_avg = psd_sum / NOISE_N_AVG
    freq_resolution = float(freq[1] - freq[0])

    np.savez(run_dir / "raw" / "noise_data.npz",
             psd_avg=psd_avg, freq=freq, fs=fs_noise, n_avg=NOISE_N_AVG)

    # 恢复解调器配置
    restore_cfg = DemodulatorConfig(
        demod_index=HF2_DEMOD_IDX, enable=True,
        rate=HF2_DEMOD_RATE, input_channel=0,
        osc_select=0, harmonic=1,
        time_constant=HF2_DEMOD_TC, order=HF2_DEMOD_ORDER,
        phase=calibrated_phase,
    )
    actual_rate_restored = demod.configure_demodulator(hfi, restore_cfg)
    time.sleep(0.2)

    return psd_avg, freq, NOISE_N_AVG, freq_resolution, actual_rate_restored


# ========== 单次完整测量函数 ==========
def run_single_measurement(devs, params_override, run_dir, calibrated_phase, actual_rate):
    """执行一次完整测量: 色散采集 → 噪声采集 → 拟合 → 灵敏度计算.

    返回 dict: {fit_result, sens_result, success, error}
    """
    hfi = devs["hf2"]
    dg_temp = devs["dg_temp"]
    dg_sweep = devs["dg_sweep"]

    result = {"success": False, "error": None,
              "fit_result": None, "sens_result": None,
              "run_dir": run_dir}

    try:
        # 1. 色散采集
        _, recorded_data = acquire_dispersion(devs, calibrated_phase,
                                              actual_rate, run_dir)

        # 2. 色散拟合
        s = RAMP_SYMMETRY / 100.0
        t_n = recorded_data["t_norm"]
        fall_start = s + (1.0 - s) * 0.1
        fall_end = s + (1.0 - s) * 0.9
        fall_mask = (t_n >= fall_start) & (t_n < fall_end)

        V_fit = recorded_data["Z_voltage"][fall_mask]
        Y_raw = recorded_data["sample.y"][fall_mask]

        fit_result = fit_dispersive(V_fit, Y_raw, Z_V_TO_NT, Z_V_TO_FT,
                                    quality_thresholds=QUALITY_THRESHOLDS)
        result["fit_result"] = fit_result

        # 3. 噪声采集
        psd_avg, freq_arr, n_avg, freq_res, actual_rate_restored = \
            acquire_noise(devs, recorded_data, calibrated_phase, run_dir)

        # 4. 灵敏度计算
        sens_result = compute_sensitivity(
            psd_avg, freq_arr,
            slope_V_per_fT=fit_result.slope_V_per_fT,
            f_larmor_Hz=fit_result.f_larmor_Hz,
            N_TOP=50,
        )
        result["sens_result"] = sens_result

        # 5. 写入记录
        full_params = get_full_params(params_override)
        write_run_record(
            run_dir, full_params, fit_result, sens_result,
            summary_path=SUMMARY_PATH,
            timestamp=datetime.now().strftime("%m%d_%H%M"),
            run_tag=run_dir.name,
            n_avg=n_avg, freq_resolution_Hz=freq_res,
        )

        result["success"] = fit_result.is_valid

    except Exception as e:
        result["error"] = str(e)

    return result


print("噪声采集和单次测量函数已定义")
print("优化函数已全部就绪, 可运行下一个 Cell 开始扫描")

# %% Cell 8
# =====================================================================
# ★ 主优化扫描循环 ★
# =====================================================================

print("=" * 60)
print("设置固定参数 + 初始相位校准")
print("=" * 60)

setup_fixed_params(devices)
time.sleep(1)

# 初始相位校准
calibrated_phase, actual_rate = calibrate_phase(
    devices["hf2"], devices["dg_temp"], devices["dg_sweep"])
time.sleep(1)

t_start = datetime.now()
print(f"\n优化扫描开始: {t_start.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"共 {len(OPTIMIZE_SEQUENCE)} 个维度")
total_iters = sum(len(s["values"]) for s in OPTIMIZE_SEQUENCE)
print(f"总迭代次数: {total_iters}\n")

all_results = []

try:
    iter_counter = 0

    for seq_idx, seq_config in enumerate(OPTIMIZE_SEQUENCE):
        param_name = seq_config["parameter"]
        values = seq_config["values"]
        stabilize = seq_config.get("stabilize_time", 2.0)
        recalibrate = seq_config.get("recalibrate_phase", True)

        print(f"\n{'=' * 60}")
        print(f"[维度 {seq_idx + 1}/{len(OPTIMIZE_SEQUENCE)}] 扫描参数: {param_name}")
        print(f"  取值列表: {values}")
        print(f"  稳定时间: {stabilize}s, 重校准: {recalibrate}")
        print(f"{'=' * 60}")

        for i, val in enumerate(values):
            iter_counter += 1
            ts = datetime.now().strftime("%m%d_%H%M")
            iter_tag = f"{param_name}_{val}"
            run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{ts}_{iter_tag}"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "raw").mkdir(exist_ok=True)
            (run_dir / "results").mkdir(exist_ok=True)

            print(f"\n--- [{iter_counter}/{total_iters}] {param_name} = {val} ---")

            # 应用参数
            apply_parameters(devices, {param_name: val})
            time.sleep(stabilize)

            # 可选重校准
            if recalibrate:
                print(f"  重校准相位...")
                calibrated_phase, actual_rate = calibrate_phase(
                    devices["hf2"], devices["dg_temp"], devices["dg_sweep"])
                time.sleep(0.5)

            # 执行测量
            result = run_single_measurement(
                devices, {param_name: val}, run_dir,
                calibrated_phase, actual_rate,
            )
            # 记录扫描参数
            result["param_name"] = param_name
            result["param_value"] = val
            all_results.append(result)

            # 实时输出
            if result["fit_result"] is not None:
                fr = result["fit_result"]
                sr = result["sens_result"]
                if result["success"]:
                    print(f"  [OK] Sens={sr.sens_flat:.1f} fT/√Hz, "
                          f"HWHM={fr.gamma_nT:.1f} nT, "
                          f"Slope={fr.slope_V_per_fT:.2e} V/fT, "
                          f"R^2={fr.r_squared:.3f}")
                else:
                    print(f"  [REJ] {fr.rejection_reasons}")
                    if sr is not None:
                        print(f"        Sens(raw)={sr.sens_flat:.1f} fT/√Hz (不可靠)")
            elif result["error"]:
                print(f"  [ERR] 采集异常: {result['error']}")

            # 实时最优
            valid = [r for r in all_results
                     if r["success"] and r["sens_result"] is not None]
            if valid:
                best = min(valid, key=lambda r: r["sens_result"].sens_flat)
                print(f"  >>> 当前最优: {best['param_name']}={best['param_value']}, "
                      f"Sens={best['sens_result'].sens_flat:.1f} fT/√Hz")

finally:
    t_end = datetime.now()
    elapsed = (t_end - t_start).total_seconds()
    print(f"\n优化扫描结束: {t_end.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"总耗时: {elapsed / 60:.1f} 分钟")
    print(f"有效结果: {sum(1 for r in all_results if r['success'])}/"
          f"{len(all_results)}")

# %% Cell 9
# ===== 扫描结果可视化 =====

if not all_results:
    print("无结果可绘图")
else:
    # 按参数分组
    param_groups = {}
    for r in all_results:
        pn = r.get("param_name", "unknown")
        param_groups.setdefault(pn, []).append(r)

    n_params = len(param_groups)
    fig, axes = plt.subplots(n_params, 2, figsize=(14, 3.5 * n_params))
    if n_params == 1:
        axes = axes.reshape(1, -1)

    for row, (param_name, subset) in enumerate(param_groups.items()):
        # 排序
        subset_sorted = sorted(subset, key=lambda r: r["param_value"])

        vals = [r["param_value"] for r in subset_sorted]
        sens_vals = [r["sens_result"].sens_flat if r["sens_result"] else np.nan
                      for r in subset_sorted]
        gamma_vals = [r["fit_result"].gamma_nT if r["fit_result"] else np.nan
                       for r in subset_sorted]
        slope_vals = [r["fit_result"].slope_V_per_fT if r["fit_result"] else np.nan
                       for r in subset_sorted]
        r2_vals = [r["fit_result"].r_squared if r["fit_result"] else np.nan
                    for r in subset_sorted]
        valid_flags = [r["success"] for r in subset_sorted]

        # 左图: 灵敏度
        ax = axes[row, 0]
        colors = ["green" if v else "red" for v in valid_flags]
        ax.scatter(vals, sens_vals, c=colors, s=40, zorder=5)
        # 只连接有效点
        valid_mask = np.array(valid_flags)
        if np.sum(valid_mask) > 1:
            vv = np.array(vals)[valid_mask]
            ss = np.array(sens_vals)[valid_mask]
            sort_idx = np.argsort(vv)
            ax.plot(vv[sort_idx], ss[sort_idx], "g--", lw=1, alpha=0.6)
        ax.set_xlabel(param_name)
        ax.set_ylabel("Sensitivity (fT/√Hz)")
        ax.set_title(f"Sensitivity vs {param_name}")
        ax.grid(True, alpha=0.3)
        ax.legend(["Valid", "Rejected"], loc="upper left", fontsize=7,
                  markerscale=0.6).set_visible(any(not v for v in valid_flags))

        # 右图: 线宽 (双Y轴共享斜率)
        ax2 = axes[row, 1]
        ax2_r2 = ax2.twinx()

        ax2.plot(vals, gamma_vals, "b-o", lw=1, markersize=5, label="HWHM (nT)")
        ax2.plot(vals, slope_vals, "r-s", lw=1, markersize=5, label="Slope (V/fT)")
        ax2_r2.plot(vals, r2_vals, "k^", lw=1, markersize=5, alpha=0.5,
                    label="R²")

        ax2.set_xlabel(param_name)
        ax2.set_ylabel("HWHM (nT) / Slope (V/fT)")
        ax2_r2.set_ylabel("R²", color="gray")
        ax2.set_title(f"Linewidth & Slope vs {param_name}")
        ax2.grid(True, alpha=0.3)

        lines1, labels1 = ax2.get_legend_handles_labels()
        lines2, labels2 = ax2_r2.get_legend_handles_labels()
        ax2.legend(lines1 + lines2, labels1 + labels2, fontsize=7, loc="best")

    plt.tight_layout()

    # 保存到第一个扫描维度的目录
    first_dir = all_results[0]["run_dir"]
    summary_fig_path = first_dir.parent / "optimization_summary.png"
    fig.savefig(summary_fig_path, dpi=150, bbox_inches="tight")
    print(f"汇总图已保存: {summary_fig_path}")

    # %% Cell 10
    # ===== 打印最优配置 =====

    valid_results = [r for r in all_results
                     if r["success"] and r["sens_result"] is not None]
    if valid_results:
        best = min(valid_results, key=lambda r: r["sens_result"].sens_flat)
        fr = best["fit_result"]
        sr = best["sens_result"]

        print("=" * 60)
        print("★ 最优配置 ★")
        print("=" * 60)
        print(f"  参数: {best['param_name']} = {best['param_value']}")
        print(f"  灵敏度: {sr.sens_flat:.1f} fT/√Hz")
        print(f"  线宽 HWHM: {fr.gamma_nT:.2f} nT ({fr.gamma_fT:.0f} fT)")
        print(f"  Larmor 频率 HWHM: {fr.f_larmor_Hz:.1f} Hz")
        print(f"  色散斜率: {fr.slope_V_per_fT:.2e} V/fT")
        print(f"  拟合 R²: {fr.r_squared:.4f}")
        print(f"  零交叉点 B0: {fr.B0_fT:.0f} fT")
        print(f"  数据目录: {best['run_dir']}")
        print("=" * 60)

        # 打印完整参数表
        print("\n完整参数:")
        full = get_full_params({best["param_name"]: best["param_value"]})
        for k, v in full.items():
            print(f"  {k}: {v}")
    else:
        print("=" * 60)
        print("所有测量均为无效, 无法确定最优配置。")
        print("请检查:")
        print("  1. 实验条件是否正常 (光路、磁场、温度)")
        print("  2. 扫描范围是否覆盖正常工作点")
        print("  3. 质量阈值是否过严 (调整 QUALITY_THRESHOLDS)")
        print("=" * 60)
        if all_results:
            # 打印失败原因汇总
            all_reasons = []
            for r in all_results:
                if r["fit_result"] and r["fit_result"].rejection_reasons:
                    all_reasons.extend(r["fit_result"].rejection_reasons)
            print(f"\n失败原因汇总 ({len(all_reasons)} 条):")
            for reason in all_reasons[:10]:
                print(f"  - {reason}")

    # %% Cell 11
    # ========== 安全断开 ==========
    # print("\n断开 TEC...")
    # tec = devices.get("tec")
    # if tec and hasattr(tec, "disconnect"):
    #     try:
    #         tec.disconnect()
    #         print("  TEC 已断开")
    #     except Exception as e:
    #         print(f"  TEC 断开失败: {e}")

    # print("其他设备保持连接, 输出状态不变")
    # print("优化脚本执行完毕")
