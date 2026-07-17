# %% [markdown] Cell 0
# # dg_am 到 dg_comp 外部 AM 传递曲线检查
#
# 目的：
# - 将 dg_am CH1/CH2 设置为一系列 DC 电压；
# - dg_am CH1/CH2 分别接入 dg_comp CH1/CH2 的外部 AM 输入；
# - dg_comp CH1/CH2 输出 90 kHz AM EXT 载波，并接入示波器 C2/C3；
# - 采集每个 AM DC 点的 dg_comp 输出幅值，判断外部 AM 输入真实线性区和饱和边界。

# %% Cell 1
from pathlib import Path
import sys

project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import time
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import yaml

from lab_workflows.devices import create_signal_generator
from sds_acquisition import (
    SDSInstrument, SDSAcquisition, AcquisitionConfig,
    ChannelConfig, TriggerConfig, save_to_npz,
)

print("库导入完成")

# %% Cell 2
# ========== 加载配置 ==========
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


# ========== 顶部可调参数 ==========
EXPERIMENT_TYPE = "DG_AM_to_DG_COMP_Transfer_Check"
RUN_TAG = "am_dc_transfer"

# 扫描 dg_am DC 电压。默认覆盖当前 safety_limits.yaml 中允许的 AM 范围。
AM_DC_VALUES = np.round(np.arange(-5, 5.001, 0.1), 6)
AM_SETTLE_s = 0.15

XY_CARRIER_FREQ = 90e3
X_CARRIER_AMPLITUDE = 6.0
Y_CARRIER_AMPLITUDE = 6.0
X_CARRIER_PHASE_deg = 0.0
Y_CARRIER_PHASE_deg = 90.0
X_AM_DEPTH = 100.0
Y_AM_DEPTH = 100.0

# 示波器接线：C2 接 dg_comp CH1/X；C3 接 dg_comp CH2/Y。
SCOPE_CHANNEL_X = 2
SCOPE_CHANNEL_Y = 3
SCOPE_INPUT_IMPEDANCE = "ONEMeg"
SCOPE_COUPLING = "DC"
SCOPE_PROBE_ATTENUATION = 1.0
SCOPE_TRIGGER_MODE = "AUTO"
SCOPE_TRIGGER_SOURCE = "C2"
SCOPE_TRIGGER_LEVEL_V = 0.0
SCOPE_SAMPLE_RATE = 10.0e6
SCOPE_CAPTURE_TIME_s = 1.0e-3
SCOPE_ACQUIRE_DELAY_s = 0.20
SCOPE_SCALE_V_DIV = 1.0

NOMINAL_AM_FULL_SCALE_V = 1.3

for v in AM_DC_VALUES:
    validate_safety_limit("X_magnetic_field_AM", float(v))
    validate_safety_limit("Y_magnetic_field_AM", float(v))


# %% Cell 3
# ========== 工具函数 ==========
def configure_dg_comp_am(dg_comp):
    """配置 dg_comp 为 X/Y 90 kHz AM EXT 载波输出。"""
    for ch in (1, 2):
        dg_comp.set_burst_state(False, channel=ch)
        dg_comp.set_mod_state(False, channel=ch)

    dg_comp.setup_sine(
        freq=XY_CARRIER_FREQ,
        amplitude=X_CARRIER_AMPLITUDE,
        offset=0.0,
        phase=X_CARRIER_PHASE_deg,
        channel=1,
    )
    dg_comp.set_mod_type("AM", channel=1)
    dg_comp.set_mod_am_source("EXT", channel=1)
    dg_comp.set_mod_am_depth(X_AM_DEPTH, channel=1)
    dg_comp.set_mod_state(True, channel=1)

    dg_comp.setup_sine(
        freq=XY_CARRIER_FREQ,
        amplitude=Y_CARRIER_AMPLITUDE,
        offset=0.0,
        phase=Y_CARRIER_PHASE_deg,
        channel=2,
    )
    dg_comp.set_mod_type("AM", channel=2)
    dg_comp.set_mod_am_source("EXT", channel=2)
    dg_comp.set_mod_am_depth(Y_AM_DEPTH, channel=2)
    dg_comp.set_mod_state(True, channel=2)

    dg_comp.set_output(True, channel=1)
    dg_comp.set_output(True, channel=2)


def configure_dg_am_dc(dg_am, voltage):
    """设置 dg_am CH1/CH2 为同一个 DC AM 输入电压。"""
    validate_safety_limit("X_magnetic_field_AM", float(voltage))
    validate_safety_limit("Y_magnetic_field_AM", float(voltage))
    for ch in (1, 2):
        dg_am.set_burst_state(False, channel=ch)
        dg_am.set_mod_state(False, channel=ch)
    dg_am.setup_dc(float(voltage), channel=1)
    dg_am.setup_dc(float(voltage), channel=2)
    dg_am.set_output(True, channel=1)
    dg_am.set_output(True, channel=2)


def build_scope_config():
    """生成示波器采集配置。"""
    channels = [
        ChannelConfig(
            number=SCOPE_CHANNEL_X,
            enabled=True,
            scale=SCOPE_SCALE_V_DIV,
            offset=0.0,
            coupling=SCOPE_COUPLING,
            impedance=SCOPE_INPUT_IMPEDANCE,
            probe=SCOPE_PROBE_ATTENUATION,
        ),
        ChannelConfig(
            number=SCOPE_CHANNEL_Y,
            enabled=True,
            scale=SCOPE_SCALE_V_DIV,
            offset=0.0,
            coupling=SCOPE_COUPLING,
            impedance=SCOPE_INPUT_IMPEDANCE,
            probe=SCOPE_PROBE_ATTENUATION,
        ),
    ]
    for ch in (1, 2, 3, 4):
        if ch not in (SCOPE_CHANNEL_X, SCOPE_CHANNEL_Y):
            channels.append(ChannelConfig(number=ch, enabled=False))

    return AcquisitionConfig(
        sampling_rate=SCOPE_SAMPLE_RATE,
        sampling_time=SCOPE_CAPTURE_TIME_s,
        acquire_type="NORMal",
        acquire_type_param=None,
        acquire_delay=SCOPE_ACQUIRE_DELAY_s,
        channels=channels,
        trigger=TriggerConfig(
            mode=SCOPE_TRIGGER_MODE,
            source=SCOPE_TRIGGER_SOURCE,
            type="EDGE",
            slope="RISing",
            level=SCOPE_TRIGGER_LEVEL_V,
        ),
    )


def demod_amplitude(time_s, voltage):
    """用 90 kHz 数字解调估计载波峰值幅度。"""
    time_s = np.asarray(time_s, dtype=float)
    voltage = np.asarray(voltage, dtype=float)
    mixed = (voltage - np.mean(voltage)) * np.exp(-1j * 2.0 * np.pi * XY_CARRIER_FREQ * time_s)
    # 对整段捕获求平均，相当于窄带锁相；返回正弦峰值幅度。
    return float(2.0 * np.abs(np.mean(mixed)))


def waveform_metrics(result):
    """从单通道示波器数据计算幅值指标。"""
    voltage = np.asarray(result.voltage, dtype=float)
    centered = voltage - np.mean(voltage)
    peak_amp = 0.5 * (float(np.max(voltage)) - float(np.min(voltage)))
    rms_amp = float(np.sqrt(np.mean(centered ** 2)) * np.sqrt(2.0))
    lockin_amp = demod_amplitude(result.time, voltage)
    return {
        "channel": int(result.channel),
        "peak_amp_V": peak_amp,
        "rms_amp_V": rms_amp,
        "lockin_amp_V": lockin_amp,
        "mean_V": float(np.mean(voltage)),
        "v_min_V": float(np.min(voltage)),
        "v_max_V": float(np.max(voltage)),
    }


def plot_live_summary(summary, results_dir):
    """保存在线快速传递曲线图。"""
    am = summary["am_dc_values_V"]
    fig, axes = plt.subplots(2, 1, figsize=(8.5, 7.0), sharex=True)

    axes[0].plot(am, summary["ch_x_lockin_amp_V"], "o-", label=f"Scope C{SCOPE_CHANNEL_X} / X")
    axes[0].plot(am, summary["ch_y_lockin_amp_V"], "o-", label=f"Scope C{SCOPE_CHANNEL_Y} / Y")
    axes[0].axvline(NOMINAL_AM_FULL_SCALE_V, color="black", ls="--", lw=0.8,
                    label="Nominal +/-1.3 V")
    axes[0].axvline(-NOMINAL_AM_FULL_SCALE_V, color="black", ls="--", lw=0.8)
    axes[0].set_ylabel("Demodulated peak amplitude (V)")
    axes[0].set_title("dg_comp Output Amplitude vs dg_am DC")
    axes[0].grid(True, alpha=0.3)
    axes[0].legend(fontsize=9)

    axes[1].plot(am, summary["ch_x_peak_amp_V"], "o-", label=f"Scope C{SCOPE_CHANNEL_X} / X peak")
    axes[1].plot(am, summary["ch_y_peak_amp_V"], "o-", label=f"Scope C{SCOPE_CHANNEL_Y} / Y peak")
    axes[1].axvline(NOMINAL_AM_FULL_SCALE_V, color="black", ls="--", lw=0.8)
    axes[1].axvline(-NOMINAL_AM_FULL_SCALE_V, color="black", ls="--", lw=0.8)
    axes[1].set_xlabel("dg_am DC voltage (V)")
    axes[1].set_ylabel("Half peak-to-peak amplitude (V)")
    axes[1].grid(True, alpha=0.3)
    axes[1].legend(fontsize=9)

    fig.tight_layout()
    path = results_dir / "transfer_curve_live.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"快速传递曲线已保存: {path}")
    return path


def safe_outputs_off(devices):
    """关闭本脚本负责的 dg_am/dg_comp 输出。"""
    for name in ("dg_am", "dg_comp"):
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
                print(f"[WARN] {name} CH{ch} 关闭失败: {exc}")


# %% Cell 4
# ========== 连接设备 ==========
devices = {}

try:
    dg_comp_cfg = MAPPING["X_magnetic_field"]
    dg_comp = create_signal_generator(dg_comp_cfg["resource"], channel=1)
    dg_comp.connect()
    dg_comp.set_ref_clock_source("EXTernal")
    dg_comp.set_output(False, channel=1)
    dg_comp.set_output(False, channel=2)
    devices["dg_comp"] = dg_comp
    print(f"dg_comp 已连接: {dg_comp.idn()}")

    dg_am_cfg = MAPPING["X_magnetic_field_AM"]
    dg_am = create_signal_generator(dg_am_cfg["resource"], channel=1)
    dg_am.connect()
    dg_am.set_ref_clock_source("EXTernal")
    dg_am.set_output(False, channel=1)
    dg_am.set_output(False, channel=2)
    devices["dg_am"] = dg_am
    print(f"dg_am 已连接: {dg_am.idn()}")

    scope_cfg = MAPPING["scope_waveform"]
    scope = SDSInstrument(scope_cfg["resource"])
    scope.connect()
    devices["scope"] = scope
    print(f"示波器已连接: {scope.idn()}")

except Exception:
    safe_outputs_off(devices)
    for dev in devices.values():
        try:
            dev.disconnect()
        except Exception:
            pass
    raise

# %% Cell 5
# ========== 运行目录 + 配置快照 ==========
timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
raw_dir = run_dir / "raw"
results_dir = run_dir / "results"
raw_dir.mkdir(parents=True, exist_ok=True)
results_dir.mkdir(parents=True, exist_ok=True)

scope_config = build_scope_config()
experiment_config = {
    "experiment_type": EXPERIMENT_TYPE,
    "run_tag": RUN_TAG,
    "timestamp": timestamp,
    "parameters": {
        "AM_DC_VALUES": [float(v) for v in AM_DC_VALUES],
        "AM_SETTLE_s": AM_SETTLE_s,
        "XY_CARRIER_FREQ_Hz": XY_CARRIER_FREQ,
        "X_CARRIER_AMPLITUDE_Vpp": X_CARRIER_AMPLITUDE,
        "Y_CARRIER_AMPLITUDE_Vpp": Y_CARRIER_AMPLITUDE,
        "X_AM_DEPTH_pct": X_AM_DEPTH,
        "Y_AM_DEPTH_pct": Y_AM_DEPTH,
        "NOMINAL_AM_FULL_SCALE_V": NOMINAL_AM_FULL_SCALE_V,
    },
    "scope_config": scope_config.to_dict(),
    "mapping_snapshot": MAPPING,
    "safety_limits_snapshot": LIMITS,
    "data_files": [],
}
config_path = run_dir / "experiment_config.yaml"
with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(experiment_config, f, allow_unicode=True, sort_keys=False)
print(f"实验配置已保存: {config_path}")

# %% Cell 6
# ========== 扫描采集 ==========
configure_dg_comp_am(devices["dg_comp"])
acq = SDSAcquisition(devices["scope"])

records = []

try:
    for idx, am_v in enumerate(AM_DC_VALUES):
        print(f"[{idx + 1:02d}/{len(AM_DC_VALUES):02d}] dg_am DC = {am_v:+.3f} V")
        configure_dg_am_dc(devices["dg_am"], float(am_v))
        time.sleep(AM_SETTLE_s)

        results = acq.acquire_all(scope_config)
        if len(results) < 2:
            raise RuntimeError(f"示波器采集通道数不足: {len(results)}")

        npz_path = save_to_npz(
            str(raw_dir / f"scope_am_{idx:03d}_{am_v:+.3f}V.npz"),
            results,
            {
                "scope_config": scope_config.to_dict(),
                "am_dc_V": float(am_v),
                "index": int(idx),
            },
            save_mode="voltage_time",
        )

        metrics = [waveform_metrics(r) for r in results]
        record = {
            "index": int(idx),
            "am_dc_V": float(am_v),
            "npz_path": str(npz_path),
            "metrics": metrics,
        }
        records.append(record)

        mx = next(m for m in metrics if m["channel"] == SCOPE_CHANNEL_X)
        my = next(m for m in metrics if m["channel"] == SCOPE_CHANNEL_Y)
        print(
            f"  X lockin={mx['lockin_amp_V']:.4f} V, peak={mx['peak_amp_V']:.4f} V; "
            f"Y lockin={my['lockin_amp_V']:.4f} V, peak={my['peak_amp_V']:.4f} V"
        )

finally:
    safe_outputs_off(devices)

am_values = np.array([r["am_dc_V"] for r in records], dtype=float)
ch_x_lockin = np.array([
    next(m for m in r["metrics"] if m["channel"] == SCOPE_CHANNEL_X)["lockin_amp_V"]
    for r in records
], dtype=float)
ch_y_lockin = np.array([
    next(m for m in r["metrics"] if m["channel"] == SCOPE_CHANNEL_Y)["lockin_amp_V"]
    for r in records
], dtype=float)
ch_x_peak = np.array([
    next(m for m in r["metrics"] if m["channel"] == SCOPE_CHANNEL_X)["peak_amp_V"]
    for r in records
], dtype=float)
ch_y_peak = np.array([
    next(m for m in r["metrics"] if m["channel"] == SCOPE_CHANNEL_Y)["peak_amp_V"]
    for r in records
], dtype=float)

summary = {
    "am_dc_values_V": am_values,
    "ch_x_lockin_amp_V": ch_x_lockin,
    "ch_y_lockin_amp_V": ch_y_lockin,
    "ch_x_peak_amp_V": ch_x_peak,
    "ch_y_peak_amp_V": ch_y_peak,
}

summary_path = raw_dir / "transfer_summary.npz"
np.savez_compressed(
    summary_path,
    **summary,
    records=np.array(records, dtype=object),
)
experiment_config["data_files"] = [str(summary_path)] + [r["npz_path"] for r in records]
with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(experiment_config, f, allow_unicode=True, sort_keys=False)

plot_live_summary(summary, results_dir)
print(f"汇总数据已保存: {summary_path}")

# %% Cell 7
# ========== 断开设备 ==========
for name, dev in devices.items():
    try:
        dev.disconnect()
        print(f"{name} 已断开")
    except Exception as exc:
        print(f"[WARN] {name} 断开失败: {exc}")

print("实验结束")
