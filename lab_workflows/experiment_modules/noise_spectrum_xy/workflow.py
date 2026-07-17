# %% [markdown] Cell 0
# # X/Y 交流控制噪声谱测量采集脚本
#
# 从 Noise_Spectrum_XY_Ctrl.ipynb 拆分出来的采集部分。
# 离线 PSD 计算、拟合和绘图请运行 Noise_Spectrum_XY_Ctrl_plot.py。

# %% Cell 3
# ========== 交互式绘图模式 ==========
import matplotlib
matplotlib.use("TkAgg")
from pathlib import Path
import sys
# 自动定位项目根目录（以 params/ 目录为标记）
project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import numpy as np
import yaml
import time
import json
import math
from datetime import datetime
import matplotlib.pyplot as plt
from tqdm import tqdm

# 设备库
from gs200 import GS200Instrument
from lab_workflows.devices import create_signal_generator
from lab_workflows.experiment_runtime import (
    apply_runtime_params,
    check_cancelled,
    load_runtime_params,
)
from lab_workflows.experiment_modules.noise_spectrum_xy.models import (
    NoiseSpectrumXYParams,
)
from lab_workflows.steps import (
    ArbitraryWaveformSpec,
    DGChannelShutdown,
    DirectAWPhaseCalibrationConfig,
    DisconnectTarget,
    STANDARD_PRESERVED_OUTPUTS,
    PhaseCalibrationConfig,
    TemperatureSwitchRestore,
    calibrate_demod_phase,
    calibrate_direct_aw_phase,
    disconnect_device_mapping,
    run_safety_shutdown,
    set_temperature_switch,
    synchronize_connected_clocks,
    upload_arbitrary,
)
from tec_controller import TECInstrument
from lockin_amplifier import (
    HF2Instrument, DAQConfig, DAQResult,
    SignalInputConfig, OscillatorConfig, DemodulatorConfig,
    demod, daq,
)

# 数据分析库
# ========== 实验参数 ==========
EXPERIMENT_TYPE = "Noise_Spectrum_XY_Ctrl"
PURPOSE = "noise_spectroscopy"

print("所有库导入成功")

# %% Cell 4
# 加载物理量→仪器映射
with open(project_root / "params" / "mapping.yaml", encoding="utf-8") as f:
    MAPPING = yaml.safe_load(f)["mapping"]

# 加载安全限值
with open(project_root / "params" / "safety_limits.yaml", encoding="utf-8") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]

# ========== 实验参数 ==========
EXPERIMENT_TYPE = "Noise_Spectrum_XY_Ctrl"
PURPOSE = "noise_spectroscopy"

# ---- 目标噪声谱频率扫描 ----
TARGET_NOISE_FREQ_START_HZ = 0.0
TARGET_NOISE_FREQ_STOP_HZ = 50000.0
TARGET_NOISE_FREQ_POINTS = 500
XY_SETTLE_TIME = 0.5       # 每点等待稳定时间 (s)

# ---- Pump 调制参数 ----
PUMP_MOD_FREQ = 10e3       # Pump 调制频率 (Hz)
PUMP_MOD_AMPLITUDE = 0.18  # 100MHz 正弦波幅度 (V)
PUMP_MOD_DUTY = 5          # 脉冲占空比 (%)
RF_GATE_AMPLITUDE = 5.0    # 脉冲门控幅度 (Vpp)
RF_GATE_OFFSET = 2.5       # 脉冲门控偏置 (V)

# ---- X/Y 交流控制信号 ----
# Bell-Bloom 原理: 原子以 Larmor 频率绕 B0 进动，XY 控制场须沿原子极化方向施加
#   XY_CTRL_PHASE:  XY 整体相对 Pump 调制信号的相位延迟 (deg)，需实验校准
#   XY_CTRL_QUAD:   X 与 Y 之间的正交相位差 (deg)，初始值 90°，后续由校准修正
#   XY_CALIB_ENVELOPE_V: DirectAW 整体相位校准时的恒定包络电压
XY_CTRL_FREQ = PUMP_MOD_FREQ    # X/Y 交流控制频率 (Hz)，等于 Larmor 频率
XY_CTRL_PHASE = 90         # XY 控制场相对 Pump 调制信号的相位延迟 (deg)
XY_CTRL_QUAD = 90          # X 与 Y 之间的正交相位差 (deg)
XY_CALIB_ENVELOPE_V = 1.5
XY_PHASE_CAL_TOL_DEG = 1.0
XY_PHASE_CAL_MAX_ITER = 10
XY_PHASE_CAL_MIN_R_V = 1e-12
XY_PHASE_CAL_MIN_R_RATIO = 0.1

# 手动填写 DirectAW 标定：Omega_ctrl = K * |V_env| + B。
# 当前默认值来自 0713 标定；完成 8 Vpp 重标定后必须同步更新 K/B 和有效电压范围。
XY_CTRL_K_HZ_PER_V = 15075.784562638912
XY_CTRL_B_HZ = -218.47506313463893
XY_CALIBRATION_MIN_ENVELOPE_V = 0.0
XY_CALIBRATION_MAX_ENVELOPE_V = 4.0

# DirectAW 固定输出参数。扫描过程中只改变上传数组，不改变 Vpp/Offset。
XY_AW_OUTPUT_VPP = 8.0
XY_AW_OUTPUT_OFFSET_V = 0.0
XY_AW_REPEAT_FREQ_HZ = 500.0
XY_AW_POINTS = 10000

XY_TRIGGER_FREQ = 100.0    # dg_am 固定基准方波频率 (Hz)
XY_TRIGGER_AMPLITUDE = 5.0 # dg_am 方波触发幅度 (Vpp)
XY_TRIGGER_OFFSET = 2.5    # dg_am 方波触发偏置 (V)
XY_TRIGGER_DUTY = 50.0     # dg_am 方波触发占空比 (%)
XY_TRIGGER_PHASE = 0.0     # dg_am 方波基准相位 (deg)，校准过程中保持不变

# ---- HF2 解调配置（相位校准用） ----
HF2_DEMOD_IDX = 0          # 解调器索引
HF2_OSC_FREQ = PUMP_MOD_FREQ    # 振荡器频率 (Hz)，与 Pump 调制频率一致
HF2_SIGNAL_RANGE = 2.0     # 信号输入量程 (V)
HF2_DEMOD_ORDER = 8        # 解调滤波器阶数
HF2_DEMOD_TC = 0.000692    # 解调时间常数 (s) —— 相位校准时使用，较低的带宽确保稳定相位
HF2_DEMOD_RATE = 100000    # 解调输出数据速率 (Sa/s)

# ---- HF2 DAQ 采集配置（噪声采集用） ----
HF2_DAQ_DURATION = 1.0     # DAQ 采集时长 (s)
HF2_DAQ_TC = 7.85e-07      # 解调时间常数 (s) —— 噪声采集时使用，更高的带宽保证频谱平坦
HF2_DAQ_RATE = 100000      # 解调输出数据速率 (Sa/s)，与 DEMOD_RATE 一致
HF2_NPERSEG = 10000        # Welch PSD 每段点数

# ---- 固定参数 ----
FIXED_PARAMS = {
    "Pump_laser_power": 0.1,         "Probe_laser_power": 0.1,
    "temperature": 100,              "Temp_Switch": 5.0,
    "main_magnetic_field": 1.03,
}

# ---- 运行目录命名 ----
RUN_TAG = "noise"

# 强类型模型是唯一 GUI/默认配置入口；大写名称仅作为旧实验主体的局部兼容别名。
PARAMS = load_runtime_params(NoiseSpectrumXYParams)
apply_runtime_params(globals(), PARAMS)

print("配置已加载")

# %% Cell 5
# 安全边界检查函数
def validate_safety_limit(name, value):
    """检查数值是否在安全范围内，超出则报错."""
    lim = LIMITS.get(name)
    if lim is None:
        return value
    lo, hi = lim["min"], lim["max"]
    if lo is not None and hi is not None:
        if value < lo or value > hi:
            raise ValueError(
                f"[安全拦截] {name}={value} 超出范围 [{lo}, {hi}]"
            )
    return value


def validate_target_scan_parameters():
    """校验目标频率、手动标定和固定 DirectAW 参数。"""
    if TARGET_NOISE_FREQ_POINTS < 2:
        raise ValueError("TARGET_NOISE_FREQ_POINTS 必须至少为 2")
    if TARGET_NOISE_FREQ_START_HZ < 0:
        raise ValueError("目标噪声谱起始频率不能为负")
    if TARGET_NOISE_FREQ_START_HZ >= TARGET_NOISE_FREQ_STOP_HZ:
        raise ValueError("目标噪声谱起始频率必须小于终止频率")
    if XY_CTRL_K_HZ_PER_V <= 0:
        raise ValueError("XY_CTRL_K_HZ_PER_V 必须大于 0")
    if (
        XY_CALIBRATION_MIN_ENVELOPE_V < 0
        or XY_CALIBRATION_MIN_ENVELOPE_V >= XY_CALIBRATION_MAX_ENVELOPE_V
    ):
        raise ValueError("DirectAW 标定有效电压范围无效")
    if XY_AW_OUTPUT_VPP <= 0:
        raise ValueError("XY_AW_OUTPUT_VPP 必须大于 0")
    if XY_AW_REPEAT_FREQ_HZ <= 0:
        raise ValueError("XY_AW_REPEAT_FREQ_HZ 必须大于 0")
    if XY_TRIGGER_FREQ <= 0:
        raise ValueError("XY_TRIGGER_FREQ 必须大于 0")
    if not 2 <= XY_AW_POINTS <= 16384:
        raise ValueError("XY_AW_POINTS 必须位于 2~16384")
    if not (
        XY_CALIBRATION_MIN_ENVELOPE_V
        <= XY_CALIB_ENVELOPE_V
        <= XY_CALIBRATION_MAX_ENVELOPE_V
    ):
        raise ValueError(
            f"校相包络 XY_CALIB_ENVELOPE_V={XY_CALIB_ENVELOPE_V:.6f} V "
            "超出 DirectAW 标定有效电压范围"
        )
    output_low = XY_AW_OUTPUT_OFFSET_V - XY_AW_OUTPUT_VPP / 2.0
    output_high = XY_AW_OUTPUT_OFFSET_V + XY_AW_OUTPUT_VPP / 2.0
    if (
        -XY_CALIB_ENVELOPE_V < output_low - 1e-12
        or XY_CALIB_ENVELOPE_V > output_high + 1e-12
    ):
        raise ValueError(
            f"校相包络 XY_CALIB_ENVELOPE_V={XY_CALIB_ENVELOPE_V:.6f} V "
            f"超出固定 AW 可表达范围 [{output_low:.6f}, {output_high:.6f}] V"
        )


def build_target_scan_axes():
    """由目标 Omega_ctrl 轴反算 DirectAW 恒定包络电压轴。"""
    validate_target_scan_parameters()
    target_freq_hz = np.linspace(
        TARGET_NOISE_FREQ_START_HZ,
        TARGET_NOISE_FREQ_STOP_HZ,
        TARGET_NOISE_FREQ_POINTS,
    )
    envelope_v = (target_freq_hz - XY_CTRL_B_HZ) / XY_CTRL_K_HZ_PER_V
    if np.any(envelope_v < 0):
        raise ValueError(
            "目标频率反算得到负的 |V_env|；请修改目标频率范围或标定 B"
        )
    envelope_min = float(np.min(envelope_v))
    envelope_max = float(np.max(envelope_v))
    if (
        envelope_min < XY_CALIBRATION_MIN_ENVELOPE_V - 1e-12
        or envelope_max > XY_CALIBRATION_MAX_ENVELOPE_V + 1e-12
    ):
        raise ValueError(
            f"目标频率范围反算得到 V_env=[{envelope_min:.6f}, "
            f"{envelope_max:.6f}] V，超出 DirectAW 标定有效范围 "
            f"[{XY_CALIBRATION_MIN_ENVELOPE_V:.6f}, "
            f"{XY_CALIBRATION_MAX_ENVELOPE_V:.6f}] V；"
            "请修改目标噪声谱频率范围或更新标定参数"
        )

    half_output = XY_AW_OUTPUT_VPP / 2.0
    output_low = XY_AW_OUTPUT_OFFSET_V - half_output
    output_high = XY_AW_OUTPUT_OFFSET_V + half_output
    if -envelope_max < output_low - 1e-12 or envelope_max > output_high + 1e-12:
        raise ValueError(
            f"最大 V_env={envelope_max:.6f} V 超出固定 AW "
            f"{XY_AW_OUTPUT_VPP:.6f} Vpp / {XY_AW_OUTPUT_OFFSET_V:.6f} V "
            f"可表达范围 [{output_low:.6f}, {output_high:.6f}] V；"
            "请修改目标噪声谱频率范围或固定 AW 参数"
        )

    for key in ("X_magnetic_field", "Y_magnetic_field"):
        validate_safety_limit(key, output_low)
        validate_safety_limit(key, output_high)
        validate_safety_limit(key, -envelope_max)
        validate_safety_limit(key, envelope_max)

    carrier_cycles = XY_CTRL_FREQ / XY_AW_REPEAT_FREQ_HZ
    trigger_ratio = XY_AW_REPEAT_FREQ_HZ / XY_TRIGGER_FREQ
    if not np.isclose(carrier_cycles, round(carrier_cycles)):
        raise ValueError("单个 AW 周期必须包含整数个 X/Y 载波周期")
    if not np.isclose(trigger_ratio, round(trigger_ratio)):
        raise ValueError("AW 重复频率必须是外部触发频率的整数倍")
    return target_freq_hz, envelope_v, output_low, output_high


(
    TARGET_NOISE_FREQ_LIST_HZ,
    XY_ENVELOPE_VOLTAGE_LIST_V,
    XY_AW_OUTPUT_LOW_V,
    XY_AW_OUTPUT_HIGH_V,
) = build_target_scan_axes()

print("安全校验函数已定义")

# %% Cell 6
devices = {}

try:
    # ---- GS200: 主磁场 ----
    gs_cfg = MAPPING["main_magnetic_field"]
    gs = GS200Instrument(gs_cfg["resource"])
    gs.connect()
    print(f"GS200 已连接: {gs.idn()}")
    gs.set_source_function(gs_cfg["source_function"])
    # [经验] GS200 需硬件级电流保护
    gs.set_current_limit(LIMITS["main_magnetic_field"]["max"] / 1000.0)
    devices["gs200"] = gs

    # ---- DG4000: X/Y 补偿磁场（Burst 载波输出） ----
    dg_comp_cfg = MAPPING["X_magnetic_field"]
    dg_comp = create_signal_generator(dg_comp_cfg)
    dg_comp.connect()
    print(f"补偿场 DG4000 已连接: {dg_comp.idn()}")
    # Y 通道: 通过同一设备 CH2 控制
    devices["dg_comp"] = dg_comp

    # ---- DG4000: dg_comp 外触发方波基准 (dg_am CH1/CH2 同相) ----
    dg_am_cfg = MAPPING["X_magnetic_field_AM"]
    dg_am = create_signal_generator(dg_am_cfg)
    dg_am.connect()
    print(f"dg_am 已连接: {dg_am.idn()}")
    for ch in (1, 2):
        dg_am.set_burst_state(False, channel=ch)
        dg_am.set_mod_state(False, channel=ch)
        dg_am.setup_dc(0.0, channel=ch)
        dg_am.set_output(False, channel=ch)
    devices["dg_am"] = dg_am

    # ---- DG4000: Z 磁场扫描（本实验用不到，仅连接并关闭输出）----
    dg_sweep_cfg = MAPPING["Z_magnetic_field"]
    dg_sweep = create_signal_generator(dg_sweep_cfg)
    dg_sweep.connect()
    print(f"Z 场 DG4000 已连接: {dg_sweep.idn()}")
    dg_sweep.setup_dc(0.0, channel=1)
    dg_sweep.set_output(False, channel=1)
    dg_sweep.set_output(False, channel=2)
    print(f"  -> Z 场输出: OFF (本实验不使用)")
    devices["dg_sweep"] = dg_sweep

    # ---- DG4000: Pump 调制 ----
    dg_mod_cfg = MAPPING["Pump_modulation"]
    dg_mod = create_signal_generator(dg_mod_cfg)
    dg_mod.connect()
    print(f"调制 DG4000 已连接: {dg_mod.idn()}")
    devices["dg_mod"] = dg_mod

    # ---- DG900: 温度开关 ----
    dg_temp_cfg = MAPPING["Temp_Switch"]
    dg_temp = create_signal_generator(dg_temp_cfg)
    dg_temp.connect()
    print(f'温控设备已连接: {dg_temp.idn()}')
    devices["dg_temp"] = dg_temp

    # ---- TEC103: 温度控制器 ----
    tec_cfg = MAPPING["temperature"]
    tec = TECInstrument(port=tec_cfg["resource"])
    tec.connect()
    print(f"TEC103 已连接")
    devices["tec"] = tec

    # ---- DG900: Pump/Probe 光功率 ----
    dg_laser_cfg = MAPPING["Pump_laser_power"]
    dg_probe_cfg = MAPPING["Probe_laser_power"]
    if (
        dg_laser_cfg["resource"] != dg_probe_cfg["resource"]
        or dg_laser_cfg["model"] != dg_probe_cfg["model"]
    ):
        raise ValueError('Pump/Probe 光功率未映射到同一台信号源')
    dg_laser = create_signal_generator(dg_laser_cfg)
    dg_laser.connect()
    print(f'光功率 {dg_laser_cfg["model"]} 已连接: {dg_laser.idn()}')
    devices["dg_laser"] = dg_laser

    # ---- HF2: 锁相放大器 ----
    hf2_cfg = MAPPING["lockin_r"]
    hfi = HF2Instrument(
        host=hf2_cfg.get("host", "127.0.0.1"),
        port=hf2_cfg.get("port", 8005),
        api_level=1,
        device_id=hf2_cfg["device_id"],
    )
    hfi.connect()
    print(f"HF2 已连接: {hfi.idn}")
    devices["hf2"] = hfi

    clock_sources = synchronize_connected_clocks(
        devices,
        MAPPING,
        {
            "dg_comp": "X_magnetic_field",
            "dg_am": "X_magnetic_field_AM",
            "dg_sweep": "Z_magnetic_field",
            "dg_mod": "Pump_modulation",
            "dg_temp": "Temp_Switch",
            "dg_laser": "Pump_laser_power",
            "hf2": "lockin_r",
        },
    )

except Exception as e:
    print(f"设备连接失败: {e}")
    disconnect_errors = disconnect_device_mapping(devices)
    if disconnect_errors:
        print("设备断开警告: " + "；".join(disconnect_errors))
    raise

print(f"\n所有设备连接完成，共 {len(devices)} 个设备")

# %% Cell 7
hfi = devices["hf2"]
tec = devices["tec"]
gs = devices["gs200"]
dg_laser = devices["dg_laser"]
dg_comp = devices["dg_comp"]
dg_am = devices["dg_am"]
dg_sweep = devices["dg_sweep"]
dg_mod = devices["dg_mod"]
dg_temp = devices["dg_temp"]


def set_temp_switch(enabled):
    return set_temperature_switch(
        dg_temp,
        enabled,
        channel=int(dg_temp_cfg["channel"]),
        on_voltage=FIXED_PARAMS["Temp_Switch"],
    )


# ---- 1. Pump 光功率 ----
validate_safety_limit("Pump_laser_power", FIXED_PARAMS["Pump_laser_power"])
dg_laser.setup_dc(
    FIXED_PARAMS["Pump_laser_power"],
    channel=int(dg_laser_cfg["channel"]),
)
print(f"Pump 光功率: {FIXED_PARAMS['Pump_laser_power']} V DC")

# ---- 2. Probe 光功率 ----
validate_safety_limit("Probe_laser_power", FIXED_PARAMS["Probe_laser_power"])
dg_laser.setup_dc(
    FIXED_PARAMS["Probe_laser_power"],
    channel=int(dg_probe_cfg["channel"]),
)
print(f"Probe 光功率: {FIXED_PARAMS['Probe_laser_power']} V DC")

# ---- 3. 主磁场 ----
validate_safety_limit("main_magnetic_field", FIXED_PARAMS["main_magnetic_field"])
gs.set_current(FIXED_PARAMS["main_magnetic_field"] / 1000.0)
gs.set_output(True)
print(f"主磁场: {FIXED_PARAMS['main_magnetic_field']} mA")

# ---- 4. 温度开关 (ON) ----
validate_safety_limit("Temp_Switch", FIXED_PARAMS["Temp_Switch"])
set_temp_switch(True)
print(f"温度开关: ON ({FIXED_PARAMS['Temp_Switch']} V)")

# ---- 5. 温度控制 ----
validate_safety_limit("temperature", FIXED_PARAMS["temperature"])
tec.set_target_temperature(FIXED_PARAMS["temperature"], channel=1)
tec.set_enable(True, channel=1)
temp_now = tec.get_temperature(channel=1)
print(f"温度设定: {FIXED_PARAMS['temperature']} °C, 当前: {temp_now:.1f} °C")
print("等待温度稳定...")
while True:
    time.sleep(5)
    t = tec.get_temperature(channel=1)
    print(f"  当前温度: {t:.2f} °C")
    try:
        t2 = tec.get_temperature(channel=1)
        if abs(t2 - FIXED_PARAMS['temperature']) < 1:
            print(f"温度已稳定: {t:.2f} °C")
            break
    except:
        pass

# ---- 6. Z 磁场输出确认关闭 ----
validate_safety_limit("Z_magnetic_field", 0.0)
dg_sweep.setup_dc(0.0, channel=1)
dg_sweep.set_output(False, channel=1)
dg_sweep.set_output(False, channel=2)
print("Z 磁场: DC 0V, 输出 OFF (本实验不使用)")

# ---- 创建运行目录 ----
timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
run_dir.mkdir(parents=True, exist_ok=True)
(raw_dir := run_dir / "raw").mkdir(exist_ok=True)
(results_dir := run_dir / "results").mkdir(exist_ok=True)
print(f"运行目录: {run_dir}")

# ---- 保存实验配置到运行目录 ----
config = {
    "experiment_id": "noise-spectrum-xy",
    "schema_version": PARAMS.schema_version,
    "execution_mode": "typed_workflow",
    "clock_sources": clock_sources,
    "parameters": PARAMS.to_external(),
    "experiment_type": EXPERIMENT_TYPE,
    "purpose": PURPOSE,
    "timestamp": timestamp,
    "scan_params": {
        "TARGET_NOISE_FREQ_START_HZ": TARGET_NOISE_FREQ_START_HZ,
        "TARGET_NOISE_FREQ_STOP_HZ": TARGET_NOISE_FREQ_STOP_HZ,
        "TARGET_NOISE_FREQ_POINTS": TARGET_NOISE_FREQ_POINTS,
        "target_noise_frequency_Hz": TARGET_NOISE_FREQ_LIST_HZ.tolist(),
        "xy_envelope_voltage_V": XY_ENVELOPE_VOLTAGE_LIST_V.tolist(),
        "XY_SETTLE_TIME_s": XY_SETTLE_TIME,
    },
    "xy_ctrl": {
        "XY_CTRL_FREQ_Hz": XY_CTRL_FREQ,
        "XY_CTRL_PHASE_deg": XY_CTRL_PHASE,
        "XY_CTRL_QUAD_deg": XY_CTRL_QUAD,
        "XY_CALIB_ENVELOPE_V": XY_CALIB_ENVELOPE_V,
        "XY_PHASE_CAL_TOL_DEG": XY_PHASE_CAL_TOL_DEG,
        "XY_PHASE_CAL_MAX_ITER": XY_PHASE_CAL_MAX_ITER,
        "XY_PHASE_CAL_MIN_R_V": XY_PHASE_CAL_MIN_R_V,
        "XY_PHASE_CAL_MIN_R_RATIO": XY_PHASE_CAL_MIN_R_RATIO,
        "XY_CTRL_K_HZ_PER_V": XY_CTRL_K_HZ_PER_V,
        "XY_CTRL_B_HZ": XY_CTRL_B_HZ,
        "calibration_envelope_range_V": [
            XY_CALIBRATION_MIN_ENVELOPE_V,
            XY_CALIBRATION_MAX_ENVELOPE_V,
        ],
        "XY_AW_OUTPUT_VPP": XY_AW_OUTPUT_VPP,
        "XY_AW_OUTPUT_OFFSET_V": XY_AW_OUTPUT_OFFSET_V,
        "XY_AW_OUTPUT_RANGE_V": [XY_AW_OUTPUT_LOW_V, XY_AW_OUTPUT_HIGH_V],
        "XY_AW_REPEAT_FREQ_HZ": XY_AW_REPEAT_FREQ_HZ,
        "XY_AW_POINTS": XY_AW_POINTS,
        "XY_TRIGGER_FREQ_Hz": XY_TRIGGER_FREQ,
        "XY_TRIGGER_AMPLITUDE_Vpp": XY_TRIGGER_AMPLITUDE,
        "XY_TRIGGER_OFFSET_V": XY_TRIGGER_OFFSET,
        "XY_TRIGGER_DUTY_pct": XY_TRIGGER_DUTY,
        "XY_TRIGGER_PHASE_deg": XY_TRIGGER_PHASE,
        "trigger_source": "dg_am CH1/CH2 fixed-phase 100 Hz square -> dg_comp Ext Trig",
        "trigger_policy": (
            "dg_am outputs ON and phase init once -> "
            "dg_comp CH1/CH2 upload -> burst ON -> outputs ON"
        ),
    },
    "fixed_params": FIXED_PARAMS,
    "pump_modulation": {
        "pump_mod_freq_Hz": PUMP_MOD_FREQ,
        "pump_mod_amplitude_V": PUMP_MOD_AMPLITUDE,
        "pump_mod_duty_pct": PUMP_MOD_DUTY,
    },
    "hf2_demod": {
        "demod_idx": HF2_DEMOD_IDX,
        "osc_freq_Hz": HF2_OSC_FREQ,
        "signal_range_V": HF2_SIGNAL_RANGE,
        "demod_rate_Sa_s": HF2_DEMOD_RATE,
        "demod_TC_s": HF2_DEMOD_TC,
    },
    "hf2_daq": {
        "DAQ_duration_s": HF2_DAQ_DURATION,
        "DAQ_TC_s": HF2_DAQ_TC,
        "DAQ_rate_Sa_s": HF2_DAQ_RATE,
        "nperseg": HF2_NPERSEG,
    },
}
config_path = run_dir / "experiment_config.yaml"
with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
print(f"实验配置已保存: {config_path}")

print("\n初始值设置完成")

# %% Cell 9
# ---- 配置 RF 开关方案（Pump 调制） ----
print("CH1: 配置 100MHz 连续正弦波...")
dg_mod.setup_sine(freq=100e6, amplitude=PUMP_MOD_AMPLITUDE,
                  offset=0.0, phase=0.0, channel=1)
print(f"  100MHz, 幅度 {PUMP_MOD_AMPLITUDE*1000:.0f} mVpp → RF 开关 IN")

pulse_width = (PUMP_MOD_DUTY / 100.0) / PUMP_MOD_FREQ
print(f"\nCH2: 配置 {PUMP_MOD_FREQ/1e3:.0f} kHz 脉冲门控...")
print(f"  脉宽: {pulse_width*1e6:.2f} μs ({PUMP_MOD_DUTY}% duty)")
dg_mod.setup_pulse(freq=PUMP_MOD_FREQ, amplitude=RF_GATE_AMPLITUDE,
                   offset=RF_GATE_OFFSET, width=pulse_width, channel=2)
print(f"  {PUMP_MOD_FREQ/1e3:.0f} kHz → RF 开关 CTRL")

# 启用 Pump 调制输出；dg_comp 外触发改由 dg_am CH1/CH2 同相 100 Hz 方波提供。
dg_mod.set_output(True, channel=1)
dg_mod.set_output(True, channel=2)
dg_mod.set_sync_state(False, channel=2)
print("  dg_mod 输出: CH1(100MHz) ON, CH2(脉冲) ON")
print("  dg_mod CH2 SYNC 未作为 dg_comp 触发源")

XY_TRIGGER_CHANNELS = (1, 2)

def validate_trigger_square_levels():
    """检查 dg_am 两路方波触发电平是否在 AM 安全范围内。"""
    half_amp = XY_TRIGGER_AMPLITUDE / 2.0
    low_level = XY_TRIGGER_OFFSET - half_amp
    high_level = XY_TRIGGER_OFFSET + half_amp
    validate_safety_limit("X_magnetic_field_AM", low_level)
    validate_safety_limit("X_magnetic_field_AM", high_level)
    validate_safety_limit("Y_magnetic_field_AM", low_level)
    validate_safety_limit("Y_magnetic_field_AM", high_level)


def configure_dg_am_reference_trigger():
    """设置 dg_am CH1/CH2 为 100 Hz 同相固定方波基准；校准过程中不再改变。"""
    validate_trigger_square_levels()
    trigger_phase = XY_TRIGGER_PHASE % 360
    for ch in XY_TRIGGER_CHANNELS:
        dg_am.set_burst_state(False, channel=ch)
        dg_am.set_mod_state(False, channel=ch)
        dg_am.setup_square(
            freq=XY_TRIGGER_FREQ,
            amplitude=XY_TRIGGER_AMPLITUDE,
            offset=XY_TRIGGER_OFFSET,
            dcycle=XY_TRIGGER_DUTY,
            phase=trigger_phase,
            channel=ch,
        )
        dg_am.set_output(True, channel=ch)

    # 使用 DG4000 相位初始化功能，让两路方波使用同一个相位基准。
    for ch in XY_TRIGGER_CHANNELS:
        dg_am.phase_init(channel=ch)

    print(
        f"  dg_am CH1/CH2: fixed SQUARE {XY_TRIGGER_FREQ} Hz, "
        f"phase={trigger_phase:.2f}°, outputs ON -> dg_comp Ext Trig reference"
    )


configure_dg_am_reference_trigger()

print("\n✅ RF 开关方案配置完成")

# ---- HF2 解调器相位校准（设置 XY 磁场前执行） ----
print("\n关闭温度开关（相位校准前，消除温控磁场干扰）...")
set_temp_switch(False)

# 确认 X/Y 补偿磁场处于关闭状态（尚未配置）
print("确认 X/Y 补偿磁场关闭...")
dg_comp.set_output(False, channel=1)
dg_comp.set_output(False, channel=2)

# 配置信号输入
sig_in_cfg = SignalInputConfig(
    input_index=0, range=HF2_SIGNAL_RANGE,
    ac_coupling=True, diff=False, impedance=50,
)
demod.configure_signal_input(hfi, sig_in_cfg)
print(f"信号输入已配置: {HF2_SIGNAL_RANGE} V range, AC coupled")

# 配置振荡器
osc_cfg = OscillatorConfig(
    osc_index=0, frequency=HF2_OSC_FREQ, source="manual",
)
demod.configure_oscillator(hfi, osc_cfg)
print(f"振荡器已配置: {HF2_OSC_FREQ/1e3:.0f} kHz")

# 配置解调器
demod_cfg = DemodulatorConfig(
    demod_index=0, enable=True, rate=HF2_DEMOD_RATE,
    input_channel=0, osc_select=0, harmonic=1,
    time_constant=HF2_DEMOD_TC, order=HF2_DEMOD_ORDER, phase=0.0,
)
actual_rate = demod.configure_demodulator(hfi, demod_cfg)
print(f"解调器 0 已配置: rate={actual_rate:.0f} Sa/s, TC={HF2_DEMOD_TC*1000:.3f} ms")

# 自动相位校准
print("正在进行相位校准...")
demod0_phase_result = calibrate_demod_phase(
    hfi,
    PhaseCalibrationConfig(
        demod_idx=HF2_DEMOD_IDX,
        tolerance_deg=1.0,
        max_attempts=5,
        settle_time=0.2,
    ),
)
calibrated_phase = demod0_phase_result.phase_shift_deg
print(f"相位校准完成: {calibrated_phase:.2f}°")

# 读一组样本验证
sample = demod0_phase_result.after_sample
print(f"校准后样本: R={sample['r']:.6f}, X={sample['x']:.6f}, Y={sample['y']:.6f}")

time.sleep(0.5)

# 恢复温控
print("恢复温度开关...")
set_temp_switch(True)
time.sleep(0.5)

# %% Cell 11
# ---- X/Y 控制信号配置（Burst 模式，dg_am 外触发同步）----
# Bell-Bloom 磁力仪物理原理:
#   原子以 Larmor 频率绕主磁场 B0 进动，Pump 光脉冲与之同步。
#   X/Y 控制磁场必须沿原子极化方向施加，因此:
#     1. X 和 Y 相差 90° 相位（正交控制，沿极化方向旋转）
#     2. XY 整体需相位延迟 XY_CTRL_PHASE 与 Pump 调制对齐
# 同步机制:
#   dg_am CH1/CH2 同相 100 Hz 方波 -> dg_comp Ext Trig（需硬件连接）
#   dg_am 两路方波保持 100 Hz 同相常开，作为 dg_comp 外触发相位基准。
print("配置 X/Y 交流控制信号（Burst 模式，dg_am 固定基准外触发）...")


def build_aw_params(waveform_v):
    """把物理电压波形转换为固定 Vpp/Offset 下的归一化任意波。"""
    waveform_v = np.asarray(waveform_v, dtype=float)
    half_range = XY_AW_OUTPUT_VPP / 2.0
    normalized = (waveform_v - XY_AW_OUTPUT_OFFSET_V) / half_range
    max_abs = float(np.max(np.abs(normalized)))
    if max_abs > 1.0 + 1e-9:
        raise ValueError(
            f"DirectAW 物理电压 [{np.min(waveform_v):+.6f}, "
            f"{np.max(waveform_v):+.6f}] V 超出固定输出范围 "
            f"[{XY_AW_OUTPUT_LOW_V:+.6f}, {XY_AW_OUTPUT_HIGH_V:+.6f}] V"
        )
    return {
        "waveform_V": waveform_v,
        "normalized": np.clip(normalized, -1.0, 1.0),
        "max_abs_normalized": max_abs,
    }


def build_direct_aw(envelope_v, phase_deg):
    """生成恒定包络的 X/Y 正交 DirectAW。"""
    time_s = np.arange(XY_AW_POINTS, dtype=float) / (
        XY_AW_POINTS * XY_AW_REPEAT_FREQ_HZ
    )
    carrier_phase = (
        2.0 * np.pi * XY_CTRL_FREQ * time_s
        + math.radians(float(phase_deg))
    )
    quad_rad = math.radians(float(XY_CTRL_QUAD))
    x_aw = build_aw_params(float(envelope_v) * np.cos(carrier_phase))
    y_aw = build_aw_params(
        float(envelope_v) * np.cos(carrier_phase + quad_rad)
    )
    return time_s, x_aw, y_aw


def upload_direct_aw(envelope_v, phase_deg, outputs_on=True):
    """保持 dg_am 连续运行，重传两路 DirectAW 后打开 Burst 和输出。"""
    time_s, x_aw, y_aw = build_direct_aw(envelope_v, phase_deg)
    for key, aw in (
        ("X_magnetic_field", x_aw),
        ("Y_magnetic_field", y_aw),
    ):
        validate_safety_limit(key, float(np.min(aw["waveform_V"])))
        validate_safety_limit(key, float(np.max(aw["waveform_V"])))

    for ch, aw in ((1, x_aw), (2, y_aw)):
        dg_comp.set_output(False, channel=ch)
        upload_arbitrary(
            dg_comp,
            ArbitraryWaveformSpec(
                values=aw["normalized"].copy(),
                frequency=XY_AW_REPEAT_FREQ_HZ,
                amplitude=XY_AW_OUTPUT_VPP,
                offset=XY_AW_OUTPUT_OFFSET_V,
                phase=0.0,
                channel=ch,
                output=False,
            ),
        )

    for ch in (1, 2):
        dg_comp.set_burst_state(True, channel=ch)
        dg_comp.set_burst_mode("INFinity", channel=ch)
        dg_comp.set_burst_ncycles(50000, channel=ch)
        dg_comp.set_burst_trigger_source("EXTernal", channel=ch)
        dg_comp.set_burst_trigger_slope("POSitive", channel=ch)
        dg_comp.set_burst_phase(0.0, channel=ch)
        dg_comp.set_output(bool(outputs_on), channel=ch)
    return time_s, x_aw, y_aw


upload_direct_aw(XY_CALIB_ENVELOPE_V, XY_CTRL_PHASE, outputs_on=False)
print(
    f"  DirectAW 固定输出: {XY_AW_OUTPUT_VPP:.3f} Vpp, "
    f"offset={XY_AW_OUTPUT_OFFSET_V:.3f} V"
)
print(
    f"  校相包络: {XY_CALIB_ENVELOPE_V:.3f} V, "
    f"X/Y 正交相位差={XY_CTRL_QUAD:.2f}°"
)
print("  X/Y 输出 OFF (等待 DirectAW 整体相位校准)")
print("硬件同步要求: dg_am CH1/CH2 同相 100 Hz 固定方波 -> dg_comp Ext Trig 需物理连接")
print(
    f"相位关系: dg_am trigger={XY_TRIGGER_PHASE:.2f}° @ "
    f"{XY_TRIGGER_FREQ:.1f} Hz, DirectAW phase={XY_CTRL_PHASE:.2f}°"
)

# %% Cell 12
# ============================================================
# XY_CTRL_PHASE 校准：保持 dg_am 基准不变，迭代 dg_comp Burst 触发相位
# ============================================================
print("=" * 60)
print("校准 XY_CTRL_PHASE（dg_am 固定基准 + dg_comp Burst 相位迭代）")
print("=" * 60)

try:
    def apply_noise_phase_and_measure(phase_deg):
        upload_direct_aw(
            XY_CALIB_ENVELOPE_V,
            phase_deg,
            outputs_on=True,
        )
        set_temp_switch(False)
        try:
            time.sleep(1.0)
            sample_data = demod.read_demod_sample(hfi, demod_idx=HF2_DEMOD_IDX)
            phase_offset = float(math.degrees(sample_data["theta"]))
            print(f"  phase={phase_deg:.2f}°, theta={phase_offset:+.2f}°")
            print(
                f"  样本: R={sample_data['r']:.6f}, "
                f"X={sample_data['x']:.6f}, Y={sample_data['y']:.6f}"
            )
            return sample_data
        finally:
            set_temp_switch(True)
            time.sleep(1.0)

    direct_aw_phase_result = calibrate_direct_aw_phase(
        XY_CTRL_PHASE,
        apply_noise_phase_and_measure,
        DirectAWPhaseCalibrationConfig(
            tolerance_deg=XY_PHASE_CAL_TOL_DEG,
            max_measurements=XY_PHASE_CAL_MAX_ITER,
            minimum_r_v=XY_PHASE_CAL_MIN_R_V,
            minimum_r_ratio=XY_PHASE_CAL_MIN_R_RATIO,
        ),
        cancellation_check=check_cancelled,
    )
    XY_CTRL_PHASE = direct_aw_phase_result.final_phase_deg
    if not direct_aw_phase_result.converged:
        print(f"\n达到最大测量次数 {XY_PHASE_CAL_MAX_ITER}，校准未收敛")

finally:
    set_temp_switch(True)
    upload_direct_aw(
        XY_CALIB_ENVELOPE_V,
        XY_CTRL_PHASE,
        outputs_on=True,
    )
    print("\n温度开关: ON (已恢复)")

print("\n✅ XY_CTRL_PHASE 校准完成")
print(f"  XY_CTRL_PHASE = {XY_CTRL_PHASE:.2f}°")
print(f"  XY_CTRL_QUAD = {XY_CTRL_QUAD:.2f}°")
print(f"  Y_CTRL_PHASE = {(XY_CTRL_PHASE + XY_CTRL_QUAD) % 360:.2f}°")

# 将最终 Burst 相位写回配置，便于复现实验。
with open(config_path, encoding="utf-8") as f:
    config_saved = yaml.safe_load(f)
config_saved["xy_ctrl"]["XY_CTRL_PHASE_deg"] = float(XY_CTRL_PHASE)
config_saved["xy_ctrl"]["XY_CTRL_QUAD_deg"] = float(XY_CTRL_QUAD)
config_saved["xy_ctrl"]["Y_CTRL_PHASE_deg"] = float((XY_CTRL_PHASE + XY_CTRL_QUAD) % 360)
config_saved["xy_ctrl"]["phase_cal_tolerance_deg"] = float(XY_PHASE_CAL_TOL_DEG)
config_saved["xy_ctrl"]["phase_cal_success"] = bool(direct_aw_phase_result.converged)
config_saved["xy_ctrl"]["phase_cal_best_r_V"] = float(direct_aw_phase_result.best_r_v)
config_saved["xy_ctrl"]["phase_cal_history"] = [
    item.to_dict() for item in direct_aw_phase_result.history
]
config_saved["xy_ctrl"]["trigger_square_freq_Hz"] = float(XY_TRIGGER_FREQ)
config_saved["xy_ctrl"]["trigger_square_phase_deg"] = float(XY_TRIGGER_PHASE)
config_saved["xy_ctrl"]["trigger_source"] = "dg_am CH1/CH2 fixed-phase 100 Hz square -> dg_comp Ext Trig"
with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(config_saved, f, allow_unicode=True, sort_keys=False)
print("最终 X/Y Burst 相位已写入 experiment_config.yaml")

# %% Cell 15
# ===== 逐点扫描目标噪声频率 + HF2 DAQ 采集 =====

print("=" * 60)
print("目标噪声谱频率扫描")
print("=" * 60)
print(
    f"目标范围: {TARGET_NOISE_FREQ_START_HZ:.1f} Hz -> "
    f"{TARGET_NOISE_FREQ_STOP_HZ:.1f} Hz, {TARGET_NOISE_FREQ_POINTS} 点"
)
print(
    f"反算 V_env: {XY_ENVELOPE_VOLTAGE_LIST_V[0]:.6f} V -> "
    f"{XY_ENVELOPE_VOLTAGE_LIST_V[-1]:.6f} V"
)
print(f"DAQ 采集: 时长 {HF2_DAQ_DURATION}s, TC={HF2_DAQ_TC*1e6:.2f}μs, rate={HF2_DAQ_RATE:.0f} Sa/s")
total_est = TARGET_NOISE_FREQ_POINTS * (
    XY_SETTLE_TIME + HF2_DAQ_DURATION + 2.0
)
print(f"预计耗时: {total_est:.0f}s ≈ {total_est/3600:.1f}h")
print()

np.savez(
    raw_dir / "control_scan_axes.npz",
    target_noise_frequency_Hz=TARGET_NOISE_FREQ_LIST_HZ,
    xy_envelope_voltage_V=XY_ENVELOPE_VOLTAGE_LIST_V,
    calibration_K_Hz_per_V=np.float64(XY_CTRL_K_HZ_PER_V),
    calibration_B_Hz=np.float64(XY_CTRL_B_HZ),
)

grid_cols = int(actual_rate * HF2_DAQ_DURATION)

# 切换到噪声采集用解调器参数（更短 TC，更高带宽）
print(f"切换到 DAQ 采集解调器参数: TC={HF2_DAQ_TC*1e6:.2f} μs, Rate={HF2_DAQ_RATE} Sa/s")
daq_demod_cfg = DemodulatorConfig(
    demod_index=0, enable=True,
    rate=HF2_DAQ_RATE, input_channel=0,
    osc_select=0, harmonic=1,
    time_constant=HF2_DAQ_TC, order=HF2_DEMOD_ORDER,
    phase=calibrated_phase,
)
actual_rate_daq = demod.configure_demodulator(hfi, daq_demod_cfg)
grid_cols = int(actual_rate_daq * HF2_DAQ_DURATION)
time.sleep(0.2)

t_start = time.time()

# try/finally 确保异常时恢复温度开关
pbar = tqdm(total=TARGET_NOISE_FREQ_POINTS, desc="Scanning", unit="pt")


def safe_scan_outputs_off():
    """声明本实验需要关闭和保留的输出，交给共享安全步骤执行。"""
    return run_safety_shutdown(
        dg_channels=(
            DGChannelShutdown(dg_comp, 1, "X_magnetic_field", "X DirectAW"),
            DGChannelShutdown(dg_comp, 2, "Y_magnetic_field", "Y DirectAW"),
            DGChannelShutdown(dg_am, 1, "X_magnetic_field_AM", "X 触发"),
            DGChannelShutdown(dg_am, 2, "Y_magnetic_field_AM", "Y 触发"),
            DGChannelShutdown(dg_sweep, 1, "Z_magnetic_field", "Z 场"),
            DGChannelShutdown(dg_sweep, 2, "Time_sequence_2", "时序通道 2"),
            DGChannelShutdown(dg_mod, 2, "Time_sequence", "Pump 门控"),
        ),
        temperature_switch=TemperatureSwitchRestore(dg_temp, 2),
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),),
        preserved_outputs=STANDARD_PRESERVED_OUTPUTS,
    )


try:
    for i, (target_freq_hz, envelope_v) in enumerate(
        zip(
            TARGET_NOISE_FREQ_LIST_HZ,
            XY_ENVELOPE_VOLTAGE_LIST_V,
            strict=True,
        )
    ):
        check_cancelled()
        pbar.set_postfix_str(
            f"Omega={target_freq_hz/1e3:.2f}kHz, V={envelope_v:.3f}"
        )

        upload_direct_aw(envelope_v, XY_CTRL_PHASE, outputs_on=True)
        time.sleep(0.3)

        # 关闭温度开关（消除温控磁场干扰）
        set_temp_switch(False)

        # 等待系统稳定
        time.sleep(XY_SETTLE_TIME)

        # 读取当前温度
        try:
            temp_now = tec.get_temperature(channel=1)
            pbar.set_postfix_str(
                f"Omega={target_freq_hz/1e3:.2f}kHz, "
                f"V={envelope_v:.3f}, T={temp_now:.1f}°C"
            )
        except:
            pbar.set_postfix_str(
                f"Omega={target_freq_hz/1e3:.2f}kHz, V={envelope_v:.3f}"
            )

        # 配置 HF2 DAQ 采集（每点独立配置）
        daq_cfg = DAQConfig(
            device=MAPPING["lockin_r"]["device_id"],
            trigger_type=0,                  # 连续模式
            duration=HF2_DAQ_DURATION,
            grid_cols=grid_cols,
            grid_rows=1,
            grid_mode=2,
            signal_paths=["sample.y"],       # 采集幅值信号
        )

        # 采集解调时域信号
        try:
            results = daq.acquire_data(
                hfi, daq_cfg, demod_idx=0,
                actual_rate=actual_rate_daq, timeout=HF2_DAQ_DURATION + 10.0,
            )
            waveform = results[0].values
        except Exception as e:
            tqdm.write(
                f"  [{i:4d}/{TARGET_NOISE_FREQ_POINTS}] "
                f"Omega={target_freq_hz:.3f}Hz, V_env={envelope_v:.6f}V: "
                f"DAQ 采集失败: {e}"
            )
            waveform = np.array([np.nan])

        # 保存原始波形（.npy 二进制格式）
        np.save(raw_dir / f"waveform_C{i:04d}.npy", waveform)

        # 恢复温度开关
        set_temp_switch(True)
        time.sleep(2)

        pbar.update(1)

except Exception as e:
    print(f"\n❌ 扫描出错: {e}")
    print("正在执行异常安全恢复...")
    raise

finally:
    pbar.close()
    shutdown_report = safe_scan_outputs_off()
    if shutdown_report.errors:
        print("安全关闭警告: " + "；".join(shutdown_report.errors))
    else:
        print("安全关闭完成，温度开关已恢复 ON")

elapsed_total = time.time() - t_start
print(
    f"\n✅ 扫描完成！共 {TARGET_NOISE_FREQ_POINTS} 点, "
    f"用时 {elapsed_total:.0f}s"
)
print(f"原始波形保存在: {raw_dir}")

# 保存实际采样率和原始文件列表到 experiment_config.yaml
with open(config_path, encoding="utf-8") as f:
    config_saved = yaml.safe_load(f)
config_saved["hf2_daq"]["actual_rate_Sa_s"] = float(actual_rate_daq)
config_saved["data_files"] = [
    "raw/control_scan_axes.npz",
    *[
        f"raw/waveform_C{i:04d}.npy"
        for i in range(TARGET_NOISE_FREQ_POINTS)
    ],
]
with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(config_saved, f, allow_unicode=True, sort_keys=False)
print(f"实际 DAQ 采样率: {actual_rate_daq} Sa/s (已写入 experiment_config.yaml)")

# 恢复解调器至相位校准参数
print("恢复解调器 TC...")
restore_demod_cfg = DemodulatorConfig(
    demod_index=0, enable=True,
    rate=HF2_DEMOD_RATE, input_channel=0,
    osc_select=0, harmonic=1,
    time_constant=HF2_DEMOD_TC, order=HF2_DEMOD_ORDER,
    phase=calibrated_phase,
)
demod.configure_demodulator(hfi, restore_demod_cfg)
time.sleep(0.2)
print(f"解调器已恢复: TC={HF2_DEMOD_TC*1e6:.0f} μs, Rate={HF2_DEMOD_RATE} Sa/s")

print("其他设备保持连接")
