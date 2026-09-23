# %% [markdown] Cell 0
# # 射频场灵敏度测量离线分析脚本
#
# 只读取 RF_Field_Sensitivity_AW.py 保存的新格式 raw/ 数据，无需连接任何仪器。

# %% Cell 1
from pathlib import Path
import sys

project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import json

import numpy as np
import yaml
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from lab_workflows.plotting import new_figure, save_figure, set_plot_style
set_plot_style("paper")
from scipy import signal as scipy_signal
from scipy.optimize import curve_fit


EXPERIMENT_TYPE = "RF_Field_Sensitivity_AW"
USE_LATEST = True
DATA_DIR = project_root / "data" / EXPERIMENT_TYPE / "MMDD_HHMM_rf_sens_aw"

# ---- 灵敏度报告频段：参考静磁场脚本，在前段平坦区取中位数 ----
SENS_FLAT_FMIN_HZ = 3.0
SENS_FLAT_FMAX_HZ = 1000.0

# %% Cell 2
def require_npz_fields(npz_data, required_fields, path):
    missing = [name for name in required_fields if name not in npz_data.files]
    if missing:
        raise KeyError(f"{path} 缺少字段: {missing}; 实际字段: {npz_data.files}")


def dispersive(x, amplitude, gamma, x0, offset):
    """Dispersive response model."""
    return amplitude * (x - x0) / ((x - x0) ** 2 + gamma ** 2) + offset


def estimate_slope_numeric(x_axis, y_axis):
    dy_dx = np.gradient(y_axis, x_axis)
    idx = int(np.nanargmax(np.abs(dy_dx)))
    return float(abs(dy_dx[idx])), float(x_axis[idx])


def fit_response_curve(field_nT, response_v):
    valid = np.isfinite(field_nT) & np.isfinite(response_v)
    x = np.asarray(field_nT[valid], dtype=float)
    y = np.asarray(response_v[valid], dtype=float)
    if len(x) < 5:
        slope, opt = estimate_slope_numeric(x, y)
        return {
            "success": False,
            "method": "numeric_gradient",
            "popt": np.full(4, np.nan),
            "perr": np.full(4, np.nan),
            "slope_V_per_nT": slope,
            "optimum_nT": opt,
            "fit_x_nT": np.array([]),
            "fit_y_V": np.array([]),
        }

    span = float(np.max(x) - np.min(x))
    gamma_guess = span / 4 if span > 0 else 1.0
    amp_guess = float((np.max(y) - np.min(y)) * gamma_guess)
    p0 = [amp_guess, gamma_guess, 0.0, float(np.nanmedian(y))]

    try:
        popt, pcov = curve_fit(dispersive, x, y, p0=p0, maxfev=20000)
        perr = np.sqrt(np.diag(pcov))
        amp_fit, gamma_fit, x0_fit, _ = popt
        slope = abs(amp_fit) / (gamma_fit ** 2) if gamma_fit != 0 else 0.0
        fit_x = np.linspace(float(np.min(x)), float(np.max(x)), 400)
        fit_y = dispersive(fit_x, *popt)
        return {
            "success": True,
            "method": "dispersive_curve_fit",
            "popt": popt,
            "perr": perr,
            "slope_V_per_nT": float(slope),
            "optimum_nT": float(x0_fit),
            "fit_x_nT": fit_x,
            "fit_y_V": fit_y,
        }
    except Exception:
        slope, opt = estimate_slope_numeric(x, y)
        return {
            "success": False,
            "method": "numeric_gradient",
            "popt": np.full(4, np.nan),
            "perr": np.full(4, np.nan),
            "slope_V_per_nT": slope,
            "optimum_nT": opt,
            "fit_x_nT": np.array([]),
            "fit_y_V": np.array([]),
        }


def json_default(obj):
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

def to_builtin(obj):
    """把 numpy 类型递归转换为 YAML/JSON 可保存的 Python 原生类型。"""
    if isinstance(obj, dict):
        return {key: to_builtin(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [to_builtin(value) for value in obj]
    if isinstance(obj, tuple):
        return [to_builtin(value) for value in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    return obj

# %% Cell 3
if USE_LATEST:
    base = project_root / "data" / EXPERIMENT_TYPE
    if base.exists():
        dirs = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
        DATA_DIR = dirs[0] if dirs else None
    else:
        DATA_DIR = None

if DATA_DIR is None or not DATA_DIR.exists():
    raise FileNotFoundError(
        f"未找到数据目录: {DATA_DIR}\n"
        f"请确认 data/{EXPERIMENT_TYPE}/ 下有实验数据，或设置 USE_LATEST=False 手动指定路径"
    )

raw_dir = DATA_DIR / "raw"
results_dir = DATA_DIR / "results"
results_dir.mkdir(parents=True, exist_ok=True)
print(f"数据目录: {DATA_DIR}")

# %% Cell 4
config_path = DATA_DIR / "experiment_config.yaml"
if not config_path.exists():
    raise FileNotFoundError(f"未找到实验配置: {config_path}")
with open(config_path, encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

response_path = raw_dir / "response_data.npz"
if not response_path.exists():
    raise FileNotFoundError(f"未找到响应曲线数据: {response_path}")
response_npz = np.load(response_path)
require_npz_fields(
    response_npz,
    ["amplitudes_V", "amplitudes_nT", "demod3_x_V", "demod3_y_V", "demod3_r_V"],
    response_path,
)

amplitudes_v = response_npz["amplitudes_V"]
amplitudes_nT = response_npz["amplitudes_nT"]
x3_v = response_npz["demod3_x_V"]
y3_v = response_npz["demod3_y_V"]
r3_v = response_npz["demod3_r_V"]
z_v_to_nT = float(response_npz["z_v_to_nT"]) if "z_v_to_nT" in response_npz.files else float(cfg["z_rf_field"]["Z_V_TO_NT"])

noise_files = sorted(raw_dir.glob("noise_D3_*.npz"))
if not noise_files:
    raise FileNotFoundError(f"未找到噪声数据: {raw_dir / 'noise_D3_*.npz'}")

print("数据加载完成")
print(f"  响应点数: {len(amplitudes_v)}")
print(f"  噪声文件: {len(noise_files)}")

# %% Cell 5
fit_result = fit_response_curve(amplitudes_nT, x3_v)
slope_V_per_nT = fit_result["slope_V_per_nT"]
slope_V_per_fT = slope_V_per_nT / 1_000_000.0

np.savez(
    results_dir / "response_fit.npz",
    method=fit_result["method"],
    success=np.uint8(int(fit_result["success"])),
    popt=fit_result["popt"],
    perr=fit_result["perr"],
    slope_V_per_nT=np.float64(slope_V_per_nT),
    slope_V_per_fT=np.float64(slope_V_per_fT),
    optimum_nT=np.float64(fit_result["optimum_nT"]),
    amplitudes_V=amplitudes_v,
    amplitudes_nT=amplitudes_nT,
    demod3_x_V=x3_v,
    demod3_y_V=y3_v,
    demod3_r_V=r3_v,
)

print("响应曲线拟合完成")
print(f"  method: {fit_result['method']}")
print(f"  slope: {slope_V_per_nT:.4e} V/nT = {slope_V_per_fT:.4e} V/fT")
print(f"  optimum: {fit_result['optimum_nT']:.3f} nT")

# %% Cell 6
psd_list = []
freq_axis = None
actual_rates = []

nperseg_cfg = int(cfg.get("noise_params", {}).get("NOISE_NPERSEG", 10000))
for path in noise_files:
    noise_npz = np.load(path)
    require_npz_fields(noise_npz, ["noise_x_V", "actual_rate_Sa_s"], path)
    waveform = np.asarray(noise_npz["noise_x_V"], dtype=float)
    if len(waveform) < 4:
        continue
    fs = float(noise_npz["actual_rate_Sa_s"])
    actual_rates.append(fs)
    waveform = waveform - np.nanmean(waveform)
    nperseg = min(nperseg_cfg, len(waveform))
    f, psd = scipy_signal.welch(
        waveform,
        fs=fs,
        nperseg=nperseg,
        noverlap=nperseg // 2,
        scaling="density",
    )
    if freq_axis is None:
        freq_axis = f
    elif len(f) != len(freq_axis) or not np.allclose(f, freq_axis):
        psd = np.interp(freq_axis, f, psd)
    psd_list.append(psd)

if not psd_list:
    raise ValueError("噪声数据为空，无法计算 PSD")

psd_matrix = np.asarray(psd_list)
psd_avg = np.nanmean(psd_matrix, axis=0)
actual_rate = float(np.nanmedian(actual_rates))

np.savez(
    results_dir / "psd_avg.npz",
    freq_Hz=freq_axis,
    psd_avg_V2_per_Hz=psd_avg,
    psd_matrix_V2_per_Hz=psd_matrix,
    n_avg=np.int32(len(psd_list)),
    actual_rate_Sa_s=np.float64(actual_rate),
)

print("噪声 PSD 计算完成")
print(f"  n_avg: {len(psd_list)}")
print(f"  freq range: {freq_axis[0]:.1f} - {freq_axis[-1]:.1f} Hz")

# %% Cell 7
if abs(slope_V_per_fT) <= 1e-30:
    raise ValueError("响应斜率过小，无法计算灵敏度")

sensitivity_fT_per_sqrtHz = np.sqrt(psd_avg) / abs(slope_V_per_fT)
flat_mask = (
    (freq_axis >= SENS_FLAT_FMIN_HZ)
    & (freq_axis <= SENS_FLAT_FMAX_HZ)
    & np.isfinite(sensitivity_fT_per_sqrtHz)
)
if not np.any(flat_mask):
    flat_mask = (freq_axis >= SENS_FLAT_FMIN_HZ) & np.isfinite(sensitivity_fT_per_sqrtHz)
flat_values = sensitivity_fT_per_sqrtHz[flat_mask]
flat_median = float(np.median(flat_values)) if len(flat_values) else float("nan")
flat_fmin_actual = float(np.min(freq_axis[flat_mask])) if np.any(flat_mask) else float("nan")
flat_fmax_actual = float(np.max(freq_axis[flat_mask])) if np.any(flat_mask) else float("nan")
best_median = flat_median

np.savez(
    results_dir / "sensitivity.npz",
    freq_Hz=freq_axis,
    sensitivity_fT_per_sqrtHz=sensitivity_fT_per_sqrtHz,
    psd_avg_V2_per_Hz=psd_avg,
    slope_V_per_nT=np.float64(slope_V_per_nT),
    slope_V_per_fT=np.float64(slope_V_per_fT),
    best_median_fT_per_sqrtHz=np.float64(best_median),
    flat_median_fT_per_sqrtHz=np.float64(flat_median),
    flat_fmin_Hz=np.float64(flat_fmin_actual),
    flat_fmax_Hz=np.float64(flat_fmax_actual),
    flat_mask=flat_mask.astype(np.uint8),
)

analysis = {
    "experiment_type": EXPERIMENT_TYPE,
    "data_dir": str(DATA_DIR),
    "response_fit": {
        "method": fit_result["method"],
        "success": bool(fit_result["success"]),
        "popt": fit_result["popt"],
        "perr": fit_result["perr"],
        "slope_V_per_nT": slope_V_per_nT,
        "slope_V_per_fT": slope_V_per_fT,
        "optimum_nT": fit_result["optimum_nT"],
        "z_v_to_nT": z_v_to_nT,
    },
    "noise_psd": {
        "n_avg": len(psd_list),
        "actual_rate_Sa_s": actual_rate,
        "nperseg": nperseg_cfg,
        "freq_min_Hz": float(freq_axis[0]),
        "freq_max_Hz": float(freq_axis[-1]),
    },
    "sensitivity": {
        "best_median_fT_per_sqrtHz": best_median,
        "flat_median_fT_per_sqrtHz": flat_median,
        "flat_fmin_Hz": flat_fmin_actual,
        "flat_fmax_Hz": flat_fmax_actual,
        "flat_fmin_requested_Hz": SENS_FLAT_FMIN_HZ,
        "flat_fmax_requested_Hz": SENS_FLAT_FMAX_HZ,
        "flat_n_points": int(np.count_nonzero(flat_mask)),
    },
}

with open(results_dir / "analysis.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(to_builtin(analysis), f, allow_unicode=True, sort_keys=False)

with open(results_dir / "analysis.json", "w", encoding="utf-8") as f:
    json.dump(to_builtin(analysis), f, indent=2, ensure_ascii=False, default=json_default)

print("灵敏度计算完成")
print(
    f"  flat median ({flat_fmin_actual:.1f}-{flat_fmax_actual:.1f} Hz): "
    f"{flat_median:.4e} fT/sqrt(Hz)"
)

# %% Cell 8
fig1, ax1 = new_figure(kind="wide", height_mm=65)
ax1.plot(amplitudes_nT, x3_v, "o", ms=4, label="Demod3 X")
if len(fit_result["fit_x_nT"]) > 0:
    ax1.plot(fit_result["fit_x_nT"], fit_result["fit_y_V"], "k--", lw=1.5, label="Dispersive fit")
ax1.axvline(fit_result["optimum_nT"], color="0.5", ls=":", lw=1, label="Optimum")
ax1.set_xlabel("Z RF Field (nT)")
ax1.set_ylabel("Demod 3 X (V)")
ax1.set_title("RF Field Response")
ax1.grid(False)
ax1.legend(fontsize=8)
fig1.set_layout_engine("constrained")
save_figure(fig1, results_dir / "response_curve.png", close=False)

fig2, ax2 = new_figure(kind="wide", height_mm=65)
for i, psd in enumerate(psd_matrix[: min(5, len(psd_matrix))]):
    ax2.loglog(freq_axis, psd, color="0.7", lw=0.7, alpha=0.6, label="Single PSD" if i == 0 else None)
ax2.loglog(freq_axis, psd_avg, "C0", lw=1.5, label="Average PSD")
ax2.set_xlabel("Frequency (Hz)")
ax2.set_ylabel("PSD (V^2/Hz)")
ax2.set_title("Demod 3 Noise PSD")
ax2.grid(False)
ax2.legend(fontsize=8)
fig2.set_layout_engine("constrained")
save_figure(fig2, results_dir / "noise_psd.png", close=False)

fig3, ax3 = new_figure(kind="wide", height_mm=65)
ax3.loglog(freq_axis, sensitivity_fT_per_sqrtHz, "C3", lw=1.2, label="Sensitivity")
if np.any(flat_mask):
    ax3.loglog(freq_axis[flat_mask], sensitivity_fT_per_sqrtHz[flat_mask], "C2", lw=1.8, label="Flat region")
if np.isfinite(flat_median):
    ax3.axhline(flat_median, color="0.4", ls="--", lw=1, label=f"Flat median: {flat_median:.2e}")
ax3.set_xlabel("Frequency (Hz)")
ax3.set_ylabel("Sensitivity (fT/sqrt(Hz))")
ax3.set_title("RF Field Sensitivity")
ax3.grid(False)
ax3.legend(fontsize=8)
fig3.set_layout_engine("constrained")
save_figure(fig3, results_dir / "sensitivity.png", close=False)

fig4, (ax4a, ax4b) = new_figure(nrows=2, ncols=1, kind="wide", height_mm=110, sharex=False)
ax4a.plot(amplitudes_nT, x3_v, "o", ms=4, label="Demod3 X")
if len(fit_result["fit_x_nT"]) > 0:
    ax4a.plot(fit_result["fit_x_nT"], fit_result["fit_y_V"], "k--", lw=1.4, label="Dispersive fit")
ax4a.set_xlabel("Z RF Field (nT)")
ax4a.set_ylabel("Demod 3 X (V)")
ax4a.set_title("Response Fit")
ax4a.grid(False)
ax4a.legend(fontsize=8)

ax4b.loglog(freq_axis, sensitivity_fT_per_sqrtHz, "C3", lw=1.2)
if np.any(flat_mask):
    ax4b.loglog(freq_axis[flat_mask], sensitivity_fT_per_sqrtHz[flat_mask], "C2", lw=1.8)
if np.isfinite(flat_median):
    ax4b.axhline(flat_median, color="0.4", ls="--", lw=1)
ax4b.set_xlabel("Frequency (Hz)")
ax4b.set_ylabel("Sensitivity (fT/sqrt(Hz))")
ax4b.set_title("Sensitivity Spectrum")
ax4b.grid(False)

fig4.set_layout_engine("constrained")
save_figure(fig4, results_dir / "full_analysis.png", close=False)

plt.show()
print(f"所有分析结果已保存至: {results_dir}")
