# %% [markdown] Cell 0
# # 射频场频率响应 — 稳健数据分析与可视化
#
# 主幅频曲线不再使用 `R_max over phase`，默认使用 `R_median`；
# 同时保存 `R_mean`、`R_max` 和复数相干一阶相位分量，便于诊断相位扫描异常点。

# %% Cell 1
from pathlib import Path
import os
import sys

project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import json
import numpy as np
import yaml

from lab_workflows.experiment_runtime import runtime_run_dir

import matplotlib
matplotlib.use(os.environ.get("MPLBACKEND", "TkAgg"))
import matplotlib.pyplot as plt

print("库导入完成")

# %% Cell 2
# EXPERIMENT_TYPE = "RF_Field_Sensitivity_AW_FreqSweep"
EXPERIMENT_TYPE = "RF_Field_Sensitivity_AW_FreqSweep_DirectAW"
PRIMARY_RESPONSE_METHOD = "r_median"  # r_median, r_mean, coherent, r_max_legacy

DATA_DIR = runtime_run_dir()

if DATA_DIR is None or not DATA_DIR.exists():
    raise FileNotFoundError(f"未找到数据目录: {DATA_DIR}")

raw_dir = DATA_DIR / "raw"
results_dir = DATA_DIR / "results"
results_dir.mkdir(parents=True, exist_ok=True)
print(f"数据目录: {DATA_DIR}")

config_path = DATA_DIR / "experiment_config.yaml"
if config_path.exists():
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    print("实验配置已加载")
else:
    print("[WARN] 未找到 experiment_config.yaml")
    config = {}

freq_phase_path = raw_dir / "freq_phase_scan.npz"
if not freq_phase_path.exists():
    raise FileNotFoundError(f"未找到频率×相位扫描数据: {freq_phase_path}")

data = np.load(freq_phase_path, allow_pickle=True)
freqs = data["freq_Hz"]
phases = data["phase_deg"]
r_matrix = data["r_matrix_V"]
x_matrix = data["x_matrix_V"] if "x_matrix_V" in data.files else None
y_matrix = data["y_matrix_V"] if "y_matrix_V" in data.files else None

valid_freq_mask = np.any(np.isfinite(r_matrix), axis=1)
if not np.any(valid_freq_mask):
    raise ValueError("r_matrix_V 中没有任何有效 R 数据")

valid_freqs = freqs[valid_freq_mask]
valid_r_matrix = r_matrix[valid_freq_mask]
invalid_freqs = freqs[~valid_freq_mask]

print("频率×相位扫描数据已加载")
print(f"  频率点数:   {len(freqs)} ({freqs[0]:.1f} ~ {freqs[-1]:.1f} Hz)")
print(f"  相位点数:   {len(phases)} ({phases[0]:.1f} ~ {phases[-1]:.1f} deg)")
print(f"  R 矩阵形状: {r_matrix.shape}")
print(f"  R 范围:     [{np.nanmin(r_matrix):.4e}, {np.nanmax(r_matrix):.4e}] V")
if len(invalid_freqs) > 0:
    print(f"  跳过全 NaN 频点: {invalid_freqs.tolist()} Hz")

# %% Cell 3
def unwrap_phase_deg(phase_deg):
    if len(phase_deg) == 0:
        return phase_deg
    return np.rad2deg(np.unwrap(np.deg2rad(phase_deg)))


def bandwidth_from_response(freq, response):
    idx = int(np.nanargmax(response))
    peak_freq = float(freq[idx])
    peak_val = float(response[idx])
    half = peak_val / np.sqrt(2)
    above = np.where(response >= half)[0]
    if len(above) >= 2:
        f_low = float(freq[above[0]])
        f_high = float(freq[above[-1]])
        bw = f_high - f_low
        q = peak_freq / bw if bw > 0 else 0.0
    else:
        f_low = f_high = bw = q = None
    return {
        "freq_Hz": peak_freq,
        "R_V": peak_val,
        "f_low_Hz": f_low,
        "f_high_Hz": f_high,
        "bandwidth_Hz": bw,
        "Q_factor": q,
    }


r_mean = np.nanmean(valid_r_matrix, axis=1)
r_median = np.nanmedian(valid_r_matrix, axis=1)
r_std = np.nanstd(valid_r_matrix, axis=1)
r_cv = r_std / np.maximum(r_mean, 1e-30)
r_max_idx = np.nanargmax(valid_r_matrix, axis=1)
r_max_legacy = valid_r_matrix[np.arange(len(valid_freqs)), r_max_idx]
r_max_phase = phases[r_max_idx]
r_max_phase_unwrapped = unwrap_phase_deg(r_max_phase)

coherent = np.full_like(r_mean, np.nan, dtype=float)
coherent_phase_deg = np.full_like(r_mean, np.nan, dtype=float)
if x_matrix is not None and y_matrix is not None:
    valid_z = (x_matrix + 1j * y_matrix)[valid_freq_mask]
    phase_rad = np.deg2rad(phases)
    # 如果 phase_list 同时包含 0 和 360，去掉最后一个重复点，避免双计数。
    phase_keep = np.ones(len(phases), dtype=bool)
    if len(phases) > 1 and np.isclose((phases[-1] - phases[0]) % 360.0, 0.0):
        phase_keep[-1] = False
    z_use = valid_z[:, phase_keep]
    phi_use = phase_rad[phase_keep]
    comp_plus = np.nanmean(z_use * np.exp(-1j * phi_use), axis=1)
    comp_minus = np.nanmean(z_use * np.exp(1j * phi_use), axis=1)
    use_plus = np.abs(comp_plus) >= np.abs(comp_minus)
    comp = np.where(use_plus, comp_plus, comp_minus)
    coherent = np.abs(comp)
    coherent_phase_deg = np.rad2deg(np.angle(comp))

responses = {
    "r_median": r_median,
    "r_mean": r_mean,
    "coherent": coherent,
    "r_max_legacy": r_max_legacy,
}
if PRIMARY_RESPONSE_METHOD not in responses:
    raise ValueError(f"未知 PRIMARY_RESPONSE_METHOD: {PRIMARY_RESPONSE_METHOD}")
primary_response = responses[PRIMARY_RESPONSE_METHOD]

primary_peak = bandwidth_from_response(valid_freqs, primary_response)
legacy_peak = bandwidth_from_response(valid_freqs, r_max_legacy)

print("\n稳健频率响应结果")
print(f"  primary method: {PRIMARY_RESPONSE_METHOD}")
print(f"  primary peak: {primary_peak['freq_Hz']:.1f} Hz, R={primary_peak['R_V']:.4e} V")
print(f"  legacy R_max peak: {legacy_peak['freq_Hz']:.1f} Hz, R={legacy_peak['R_V']:.4e} V")
print(f"  median phase CV: {np.nanmedian(r_cv):.4f}, max phase CV: {np.nanmax(r_cv):.4f}")

# %% Cell 4
plt.rcParams.update({"figure.dpi": 120, "font.size": 11, "axes.labelsize": 12})

fig1, ax1 = plt.subplots(figsize=(10, 5.8))
ax1.plot(valid_freqs, r_median, ".-", color="C0", lw=1.4, ms=5,
         label="R median over phase")
ax1.set_xlabel("Z RF Frequency (Hz)")
ax1.set_ylabel("Demod 3 response (V)")
ax1.set_title("RF Field Frequency Response - Median Amplitude")
ax1.legend(fontsize=8)
ax1.grid(True, alpha=0.3)
plt.tight_layout()
fig1.savefig(results_dir / "freq_response_amplitude.png", dpi=150, bbox_inches="tight")
fig1.savefig(results_dir / "freq_response_amplitude_robust.png", dpi=150, bbox_inches="tight")
print(f"图已保存: {results_dir / 'freq_response_amplitude.png'}")
plt.show()

fig2, ax2 = plt.subplots(figsize=(10, 5.5))
ax2.plot(valid_freqs, r_max_phase, ".-", color="C2", lw=1.0, ms=4,
         label="Legacy phase at R_max")
ax2.plot(valid_freqs, r_max_phase_unwrapped, "-", color="C3", lw=1.0, alpha=0.7,
         label="Legacy phase unwrapped")
if np.any(np.isfinite(coherent_phase_deg)):
    ax2.plot(valid_freqs, unwrap_phase_deg(coherent_phase_deg), "-", color="C4", lw=1.0,
             label="Coherent component phase")
ax2.axvline(primary_peak["freq_Hz"], color="red", ls="--", lw=0.8, alpha=0.5)
ax2.set_xlabel("Z RF Frequency (Hz)")
ax2.set_ylabel("Phase (deg)")
ax2.set_title("RF Field Frequency Response - Phase Diagnostics")
ax2.legend(fontsize=8)
ax2.grid(True, alpha=0.3)
plt.tight_layout()
fig2.savefig(results_dir / "freq_response_phase.png", dpi=150, bbox_inches="tight")
print(f"图已保存: {results_dir / 'freq_response_phase.png'}")
plt.show()

fig3, ax3 = plt.subplots(figsize=(10, 6))
phase_ext = np.concatenate([phases - 360, phases, phases + 360])
r_ext = np.tile(r_matrix, (1, 3))
im = ax3.pcolormesh(phase_ext, freqs, r_ext, cmap="viridis", shading="auto")
if len(phases) == 1:
    ax3.set_xlim(float(phases[0]) - 1.0, float(phases[0]) + 1.0)
else:
    ax3.set_xlim(phases[0], phases[-1])
ax3.set_xlabel("Z RF burst phase (deg)")
ax3.set_ylabel("Z RF Frequency (Hz)")
ax3.set_title("R(freq, phase) Heatmap")
cb = fig3.colorbar(im, ax=ax3)
cb.set_label("R (V)")
plt.tight_layout()
fig3.savefig(results_dir / "freq_phase_heatmap.png", dpi=150, bbox_inches="tight")
print(f"图已保存: {results_dir / 'freq_phase_heatmap.png'}")
plt.show()

fig4, ax4 = plt.subplots(figsize=(10, 4.8))
ax4.plot(valid_freqs, r_cv, ".-", color="C5", lw=1.0, ms=4)
ax4.set_xlabel("Z RF Frequency (Hz)")
ax4.set_ylabel("std(R over phase) / mean(R)")
ax4.set_title("Phase Scan Stability Diagnostic")
ax4.grid(True, alpha=0.3)
plt.tight_layout()
fig4.savefig(results_dir / "phase_scan_cv.png", dpi=150, bbox_inches="tight")
print(f"图已保存: {results_dir / 'phase_scan_cv.png'}")
plt.show()

# %% Cell 5
analysis = {
    "experiment_type": EXPERIMENT_TYPE,
    "data_dir": str(DATA_DIR),
    "method": f"Primary response = {PRIMARY_RESPONSE_METHOD}; R_max is retained only as legacy diagnostic",
    "n_frequencies": int(len(freqs)),
    "n_valid_frequencies": int(len(valid_freqs)),
    "invalid_freq_Hz": invalid_freqs.tolist(),
    "n_phases": int(len(phases)),
    "fixed_Z_RF_amplitude_Vpp": config.get("freq_sweep", {}).get("Z_RF_AMPLITUDE_Vpp", None),
    "freq_range_Hz": [float(freqs[0]), float(freqs[-1])],
    "phase_range_deg": [float(phases[0]), float(phases[-1])],
    "primary_response_method": PRIMARY_RESPONSE_METHOD,
    "primary_peak_response": {
        "freq_Hz": primary_peak["freq_Hz"],
        "R_V": primary_peak["R_V"],
    },
    "primary_bandwidth": {
        "f_low_Hz": primary_peak["f_low_Hz"],
        "f_high_Hz": primary_peak["f_high_Hz"],
        "bandwidth_Hz": primary_peak["bandwidth_Hz"],
        "Q_factor": primary_peak["Q_factor"],
        "criterion": "-3dB (primary response / sqrt(2))",
    },
    "legacy_r_max_peak_response": {
        "freq_Hz": legacy_peak["freq_Hz"],
        "R_V": legacy_peak["R_V"],
        "phase_deg_raw": float(r_max_phase[int(np.nanargmax(r_max_legacy))]),
    },
    "phase_scan_diagnostic": {
        "median_cv": float(np.nanmedian(r_cv)),
        "max_cv": float(np.nanmax(r_cv)),
    },
    "freq_response": {
        "freq_Hz": valid_freqs.tolist(),
        "R_V_primary": primary_response.tolist(),
        "R_V_mean": r_mean.tolist(),
        "R_V_median": r_median.tolist(),
        "R_V_coherent": coherent.tolist(),
        "R_V_max_legacy": r_max_legacy.tolist(),
        "phase_deg_r_max_legacy_raw": r_max_phase.tolist(),
        "phase_deg_r_max_legacy_unwrapped": r_max_phase_unwrapped.tolist(),
        "phase_deg_coherent": coherent_phase_deg.tolist(),
        "phase_cv": r_cv.tolist(),
    },
}

analysis_path = results_dir / "analysis.yaml"
with open(analysis_path, "w", encoding="utf-8") as f:
    yaml.dump(analysis, f, default_flow_style=False, allow_unicode=True)
print(f"分析结果已保存: {analysis_path}")

analysis_json_path = results_dir / "analysis.json"
with open(analysis_json_path, "w", encoding="utf-8") as f:
    json.dump(analysis, f, indent=2, ensure_ascii=False)
print(f"分析结果 (JSON) 已保存: {analysis_json_path}")

print(f"\n{'='*50}")
print("频率响应分析摘要")
print(f"{'='*50}")
print(f"频点数:       {len(freqs)}")
print(f"有效频点数:   {len(valid_freqs)}")
print(f"相位点数:     {len(phases)}")
print(f"主分析方法:   {PRIMARY_RESPONSE_METHOD}")
print(f"主峰频率:     {primary_peak['freq_Hz']:.1f} Hz")
print(f"主峰响应:     {primary_peak['R_V']:.4e} V")
if primary_peak["bandwidth_Hz"] is not None:
    print(f"-3dB 带宽:    {primary_peak['bandwidth_Hz']:.1f} Hz "
          f"({primary_peak['f_low_Hz']:.1f} ~ {primary_peak['f_high_Hz']:.1f} Hz)")
print(f"Legacy R_max: {legacy_peak['freq_Hz']:.1f} Hz, {legacy_peak['R_V']:.4e} V")
print(f"{'='*50}")
print("\n[OK] 数据分析与可视化完成")
