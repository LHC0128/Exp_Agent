# %% [markdown] Cell 0
# # 射频场频率响应 — 数据分析与可视化
#
# 加载频率响应采集数据（固定幅度、扫描频率 × 扫描相位），
# **第一步**：对每个频率点，找到 R 最大时所对应的相位（最佳解调相位）；
# **第二步**：用该最佳相位下的 R 绘制幅频特性曲线，并补充相频特性曲线。
#
# **无需连接任何仪器**，仅读取本地数据文件进行分析。

# %% Cell 1
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
import json

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt

print("库导入完成")

# %% Cell 2
# ========== 选择数据目录 ==========
EXPERIMENT_TYPE = "RF_Field_Sensitivity_AW_FreqSweep"

USE_LATEST = True
if USE_LATEST:
    base = project_root / "data" / EXPERIMENT_TYPE
    if base.exists():
        dirs = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
        DATA_DIR = dirs[0] if dirs else None
    else:
        DATA_DIR = None
else:
    # 手动指定数据目录
    DATA_DIR = project_root / "data" / EXPERIMENT_TYPE / "MMDD_HHMM_tag"

if DATA_DIR is None or not DATA_DIR.exists():
    raise FileNotFoundError(
        f"未找到数据目录: {DATA_DIR}\n"
        f"请确认 data/{EXPERIMENT_TYPE}/ 下有实验数据，或设置 USE_LATEST=False 手动指定路径"
    )

raw_dir = DATA_DIR / "raw"
results_dir = DATA_DIR / "results"
results_dir.mkdir(parents=True, exist_ok=True)
print(f"数据目录: {DATA_DIR}")

# ========== 加载实验配置 ==========
config_path = DATA_DIR / "experiment_config.yaml"
if config_path.exists():
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    print(f"实验配置已加载")
else:
    print(f"⚠ 未找到 experiment_config.yaml")
    config = {}

# ========== 加载频率×相位 嵌套扫描数据 ==========
freq_phase_path = raw_dir / "freq_phase_scan.npz"
if not freq_phase_path.exists():
    raise FileNotFoundError(f"未找到频率×相位扫描数据: {freq_phase_path}")

data = np.load(freq_phase_path, allow_pickle=True)
freqs = data["freq_Hz"]                  # 1D 数组: (N_freq,)
phases = data["phase_deg"]               # 1D 数组: (N_phase,)
r_matrix = data["r_matrix_V"]            # 2D 矩阵: (N_freq, N_phase)

print(f"频率×相位扫描数据已加载")
print(f"  频率点数:   {len(freqs)} ({freqs[0]:.1f} ~ {freqs[-1]:.1f} Hz)")
print(f"  相位点数:   {len(phases)} ({phases[0]:.1f} ~ {phases[-1]:.1f} deg)")
print(f"  R 矩阵形状: {r_matrix.shape}")
print(f"  R 范围:     [{np.nanmin(r_matrix):.4e}, {np.nanmax(r_matrix):.4e}] V")
print(f"  固定幅度:   {config.get('freq_sweep', {}).get('Z_RF_AMPLITUDE_Vpp', '?')} Vpp")

# %% Cell 3
# ============================================================
# 第一步：对每个频率点，找到 R 最大时所对应的相位
# ============================================================
# 对每个频率行，找到 R 最大值对应的列索引 → 最佳相位
best_r_idx = np.nanargmax(r_matrix, axis=1)        # (N_freq,)
best_r = r_matrix[np.arange(len(freqs)), best_r_idx]  # (N_freq,)
best_phase_raw = phases[best_r_idx]                 # (N_freq,) 原始相位

# 相位解包：消除 360° 跳变（np.unwrap 默认处理 rad，这里手动处理 deg）
# 累积偏移量，保证相邻频点相位连续
best_phase_unwrapped = np.zeros_like(best_phase_raw)
best_phase_unwrapped[0] = best_phase_raw[0]
offset = 0.0
for i in range(1, len(best_phase_raw)):
    delta = best_phase_raw[i] - best_phase_raw[i - 1]
    # 调整到 [-180, 180]
    if delta > 180:
        delta -= 360
    elif delta < -180:
        delta += 360
    unwrapped_val = best_phase_unwrapped[i - 1] + delta
    # 整体保持在 [-180, 180] 以方便显示
    best_phase_unwrapped[i] = ((unwrapped_val + 180) % 360) - 180

print(f"\n第一步结果：对每个频率点找 R 最大值对应的相位")
print(f"  最佳 R 范围:     [{np.min(best_r):.4e}, {np.max(best_r):.4e}] V")
print(f"  最佳相位范围:   [{np.min(best_phase_raw):.1f}, {np.max(best_phase_raw):.1f}] deg")
print(f"  最大 R 对应频点: {freqs[np.argmax(best_r)]:.1f} Hz "
      f"(R={np.max(best_r):.4e} V, phase={best_phase_raw[np.argmax(best_r)]:.1f}°)")

# %% Cell 4
# ============================================================
# 第二步：用最大 R 绘制频率响应
# ============================================================
plt.rcParams.update({"figure.dpi": 120, "font.size": 11, "axes.labelsize": 12})

# ---- 图 1: 幅频特性（R 最佳相位对应的 R vs 频率）----
fig1, ax1 = plt.subplots(figsize=(10, 5.5))

ax1.plot(freqs, best_r, ".-", color="C0", lw=1.2, markersize=5,
         label="R at optimal phase")

# 标注峰值
r_peak_idx = int(np.argmax(best_r))
r_peak_freq = float(freqs[r_peak_idx])
r_peak_val = float(best_r[r_peak_idx])
ax1.axvline(r_peak_freq, color="red", ls="--", lw=0.8, alpha=0.5,
            label=f"Peak: {r_peak_freq:.1f} Hz (R={r_peak_val:.3e} V)")

ax1.set_xlabel("Z RF Frequency (Hz)")
ax1.set_ylabel("Demod 3 R at optimal phase (V)")
ax1.set_title("RF Field Frequency Response — Amplitude (R_max over phase)")
ax1.legend(fontsize=9)
ax1.grid(True, alpha=0.3)

plt.tight_layout()
fig1.savefig(results_dir / "freq_response_amplitude.png",
             dpi=150, bbox_inches="tight")
print(f"图已保存: {results_dir / 'freq_response_amplitude.png'}")
plt.show()

# ---- 图 2: 相频特性（最佳相位 vs 频率）----
fig2, ax2 = plt.subplots(figsize=(10, 5.5))

ax2.plot(freqs, best_phase_raw, ".-", color="C2", lw=1.2, markersize=5,
         label="Optimal phase (raw)")
ax2.plot(freqs, best_phase_unwrapped, "-", color="C3", lw=1.0, alpha=0.7,
         label="Optimal phase (unwrapped)")

# 标注峰值位置
ax2.axvline(r_peak_freq, color="red", ls="--", lw=0.8, alpha=0.5,
            label=f"Peak freq: {r_peak_freq:.1f} Hz")

ax2.set_xlabel("Z RF Frequency (Hz)")
ax2.set_ylabel("Optimal Demod 3 Phase (deg)")
ax2.set_title("RF Field Frequency Response — Phase (phase at R_max)")
ax2.legend(fontsize=9)
ax2.grid(True, alpha=0.3)

plt.tight_layout()
fig2.savefig(results_dir / "freq_response_phase.png",
             dpi=150, bbox_inches="tight")
print(f"图已保存: {results_dir / 'freq_response_phase.png'}")
plt.show()

# ---- 图 3: 原始 R 矩阵等高线图（可选辅助视图）----
fig3, ax3 = plt.subplots(figsize=(10, 6))
# 将相位周期延拓至 [-180, 540) 以避免 0/360 边界处的色块断裂
phase_ext = np.concatenate([phases - 360, phases, phases + 360])
r_ext = np.tile(r_matrix, (1, 3))  # (N_freq, 3*N_phase)
im = ax3.pcolormesh(phase_ext, freqs, r_ext,
                    cmap="viridis", shading="auto")
ax3.set_xlim(phases[0], phases[-1])
ax3.set_xlabel("Demod 3 Phase (deg)")
ax3.set_ylabel("Z RF Frequency (Hz)")
ax3.set_title("R(freq, phase) Heatmap")
cb = fig3.colorbar(im, ax=ax3)
cb.set_label("R (V)")
plt.tight_layout()
fig3.savefig(results_dir / "freq_phase_heatmap.png",
             dpi=150, bbox_inches="tight")
print(f"图已保存: {results_dir / 'freq_phase_heatmap.png'}")
plt.show()

# %% Cell 5
# ========== 保存分析结果 ==========
# 找峰值和带宽（基于最佳相位对应的 R 曲线）
r_max_idx = int(np.argmax(best_r))
r_max_freq = float(freqs[r_max_idx])
r_max_val = float(best_r[r_max_idx])

r_half_max = r_max_val / np.sqrt(2)
above_half = np.where(best_r >= r_half_max)[0]
if len(above_half) >= 2:
    f_low = float(freqs[above_half[0]])
    f_high = float(freqs[above_half[-1]])
    bandwidth_hz = f_high - f_low
    q_factor = r_max_freq / bandwidth_hz if bandwidth_hz > 0 else 0.0
else:
    f_low = f_high = bandwidth_hz = q_factor = None

analysis = {
    "experiment_type": EXPERIMENT_TYPE,
    "data_dir": str(DATA_DIR),
    "method": "At each frequency, select R_max over the phase scan",
    "n_frequencies": int(len(freqs)),
    "n_phases": int(len(phases)),
    "fixed_Z_RF_amplitude_Vpp": config.get("freq_sweep", {}).get("Z_RF_AMPLITUDE_Vpp", None),
    "freq_range_Hz": [float(freqs[0]), float(freqs[-1])],
    "phase_range_deg": [float(phases[0]), float(phases[-1])],
    "peak_response": {
        "freq_Hz": r_max_freq,
        "R_V": r_max_val,
        "phase_deg_raw": float(best_phase_raw[r_max_idx]),
        "phase_deg_unwrapped": float(best_phase_unwrapped[r_max_idx]),
    },
    "bandwidth": {
        "f_low_Hz": f_low,
        "f_high_Hz": f_high,
        "bandwidth_Hz": bandwidth_hz,
        "Q_factor": q_factor,
        "criterion": "-3dB (R = R_max / √2)",
    },
    "freq_response": {
        "freq_Hz": freqs.tolist(),
        "R_V_optimal_phase": best_r.tolist(),
        "phase_deg_optimal_raw": best_phase_raw.tolist(),
        "phase_deg_optimal_unwrapped": best_phase_unwrapped.tolist(),
    },
}

analysis_path = results_dir / "analysis.yaml"
with open(analysis_path, "w", encoding="utf-8") as f:
    yaml.dump(analysis, f, default_flow_style=False, allow_unicode=True)
print(f"分析结果已保存: {analysis_path}")

# 也保存 JSON 版本（兼容）
analysis_json_path = results_dir / "analysis.json"
with open(analysis_json_path, "w", encoding="utf-8") as f:
    json.dump(analysis, f, indent=2, ensure_ascii=False)
print(f"分析结果 (JSON) 已保存: {analysis_json_path}")

# ---- 打印分析摘要 ----
print(f"\n{'='*50}")
print(f"频率响应分析摘要")
print(f"{'='*50}")
print(f"频点数:     {len(freqs)}")
print(f"相位点数:   {len(phases)}")
print(f"频率范围:   {freqs[0]:.1f} ~ {freqs[-1]:.1f} Hz")
print(f"峰值频率:   {r_max_freq:.1f} Hz")
print(f"峰值 R:     {r_max_val:.4e} V (phase={best_phase_raw[r_max_idx]:.1f}°)")
if bandwidth_hz is not None:
    print(f"-3dB 带宽:  {bandwidth_hz:.1f} Hz ({f_low:.1f} ~ {f_high:.1f} Hz)")
    print(f"Q 值:       {q_factor:.1f}")
print(f"{'='*50}")

print("\n✅ 数据分析与可视化完成")