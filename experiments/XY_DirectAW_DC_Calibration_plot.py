# %% [markdown] Cell 0
# # XY DirectAW DC 标定 — 离线分析
#
# 无需连接任何仪器。对每个 DirectAW 恒定包络电压下的 Z 射频响应进行：
#
# 1. Demod 0 `sample.y` 离线锁相与 nearest-bin FFT；
# 2. 优先使用 Demod 3 硬件 R 追踪标定峰；
# 3. 拟合前屏蔽已确认的固定相干杂散频带；
# 4. 分别拟合正电压、负电压、有符号全局关系和 `|V|` 对称关系；
# 5. 保存 YAML/JSON/NPZ 与英文标注图。

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

import matplotlib
matplotlib.use(os.environ.get("MPLBACKEND", "TkAgg"))
import matplotlib.pyplot as plt
import numpy as np
from scipy import optimize as scipy_optimize
from scipy import signal as scipy_signal
import yaml

print("离线分析库导入完成")

# %% Cell 2
# ========== 数据选择与分析参数 ==========
EXPERIMENT_TYPE = "XY_DirectAW_DC_Calibration"
USE_LATEST = True
DATA_DIR_OVERRIDE = None
# DATA_DIR_OVERRIDE = "data/XY_DirectAW_DC_Calibration/MMDD_HHMM_direct_aw_dc_cal"

if DATA_DIR_OVERRIDE:
    DATA_DIR = project_root / DATA_DIR_OVERRIDE
elif USE_LATEST:
    base = project_root / "data" / EXPERIMENT_TYPE
    dirs = sorted([path for path in base.iterdir() if path.is_dir()], reverse=True) if base.exists() else []
    DATA_DIR = dirs[0] if dirs else None
else:
    DATA_DIR = project_root / "data" / EXPERIMENT_TYPE / "MMDD_HHMM_direct_aw_dc_cal"

if DATA_DIR is None or not DATA_DIR.exists():
    raise FileNotFoundError(f"未找到 DirectAW DC 标定数据目录: {DATA_DIR}")

raw_dir = DATA_DIR / "raw"
results_dir = DATA_DIR / "results"
results_dir.mkdir(parents=True, exist_ok=True)

with open(DATA_DIR / "experiment_config.yaml", encoding="utf-8") as f:
    config = yaml.safe_load(f)

index_json = raw_dir / "frequency_index.json"
index_npz = raw_dir / "frequency_index.npz"
if index_json.exists():
    with open(index_json, encoding="utf-8") as f:
        global_index = json.load(f)
elif index_npz.exists():
    global_index = np.load(index_npz, allow_pickle=True)["records"].tolist()
else:
    raise FileNotFoundError(f"未找到频点索引: {raw_dir}")

OFFLINE_LOCKIN_USE_INTEGER_CYCLES = True
FIT_MIN_POINTS = 6
ABS_FIT_MIN_V = 0.25
EXAMPLE_VOLTAGES_V = [-2.0, -1.0, 0.0, 1.0, 2.0]
# 0713 数据确认 20 kHz 为固定相干杂散。屏蔽一个完整的 19--21 kHz
# 窄带，避免单个异常点及其相邻泄漏被识别成标定峰。
FIXED_SPUR_BANDS_HZ = [(19000.0, 21000.0)]
# 拟合中心接近屏蔽带时仍可能受杂散尾部影响，只标记为低可信度，
# 不自动从电压标定中删除。
SPUR_PROXIMITY_MARGIN_HZ = 2000.0

print(f"数据目录: {DATA_DIR}")
print(f"索引记录: {len(global_index)}")

# %% Cell 3
# ========== 频点提取与拟合函数 ==========
def require_npz_fields(data, fields, path):
    missing = [field for field in fields if field not in data.files]
    if missing:
        raise KeyError(f"{path} 缺少字段 {missing}; 实际字段: {data.files}")


def offline_lockin_r(y, fs, drive_freq, use_integer_cycles=True):
    """对时域信号做离线正交锁相，返回 X/Y/R/phase。"""
    y = np.asarray(y, dtype=float)
    if len(y) < 4 or fs <= 0 or drive_freq <= 0:
        return {"X": np.nan, "Y": np.nan, "R": np.nan, "phase_deg": np.nan}
    n_use = len(y)
    if use_integer_cycles:
        n_cycles = int(np.floor(len(y) * drive_freq / fs))
        if n_cycles >= 1:
            n_use = int(np.floor(n_cycles * fs / drive_freq))
    n_use = min(max(n_use, 4), len(y))
    y_use = y[:n_use] - np.mean(y[:n_use])
    t = np.arange(n_use, dtype=float) / fs
    phase = 2.0 * np.pi * drive_freq * t
    x = float(2.0 * np.mean(y_use * np.cos(phase)))
    q = float(2.0 * np.mean(y_use * np.sin(phase)))
    return {
        "X": x,
        "Y": q,
        "R": float(np.hypot(x, q)),
        "phase_deg": float(np.degrees(np.arctan2(q, x))),
    }


def nearest_bin_fft_amp(y, fs, drive_freq):
    """Hann 窗单边振幅谱在最近驱动频点处的幅值。"""
    y = np.asarray(y, dtype=float)
    if len(y) < 4 or fs <= 0:
        return np.nan
    y0 = y - np.mean(y)
    window = scipy_signal.windows.hann(len(y0), sym=False)
    coherent_gain = float(np.mean(window))
    spectrum = np.fft.rfft(y0 * window)
    freqs = np.fft.rfftfreq(len(y0), d=1.0 / fs)
    amplitude = 2.0 * np.abs(spectrum) / (len(y0) * coherent_gain)
    idx = int(np.argmin(np.abs(freqs - drive_freq)))
    return float(amplitude[idx])


def frequency_keep_mask(freqs, excluded_bands_Hz):
    """返回排除固定杂散频带后的频点掩码。"""
    freqs = np.asarray(freqs, dtype=float)
    keep = np.isfinite(freqs)
    for lower_Hz, upper_Hz in excluded_bands_Hz:
        lower_Hz = float(lower_Hz)
        upper_Hz = float(upper_Hz)
        if lower_Hz > upper_Hz:
            raise ValueError(f"无效杂散频带: {lower_Hz}--{upper_Hz} Hz")
        keep &= ~((freqs >= lower_Hz) & (freqs <= upper_Hz))
    return keep


def show_or_close(figure):
    """交互运行时显示图形，GUI/Agg 模式下保存后直接释放。"""
    if "agg" in matplotlib.get_backend().lower():
        plt.close(figure)
    else:
        plt.show()


def raw_maximum(freqs, response):
    """返回未屏蔽响应的最大采样点，供杂散诊断与结果追溯。"""
    freqs = np.asarray(freqs, dtype=float)
    response = np.asarray(response, dtype=float)
    valid = np.isfinite(freqs) & np.isfinite(response)
    if not np.any(valid):
        return {"frequency_Hz": np.nan, "response_V": np.nan}
    f = freqs[valid]
    r = response[valid]
    idx = int(np.argmax(r))
    return {"frequency_Hz": float(f[idx]), "response_V": float(r[idx])}


def frequency_in_bands(freq_Hz, bands_Hz, margin_Hz=0.0):
    """判断频率是否位于屏蔽带内或其指定邻域。"""
    if not np.isfinite(freq_Hz):
        return False
    return any(
        float(lower_Hz) - margin_Hz <= freq_Hz <= float(upper_Hz) + margin_Hz
        for lower_Hz, upper_Hz in bands_Hz
    )


def fit_response_peak_excluding_spurs(freqs, response):
    """屏蔽固定杂散后拟合响应峰，并记录实际删除的频点。"""
    freqs = np.asarray(freqs, dtype=float)
    response = np.asarray(response, dtype=float)
    keep = frequency_keep_mask(freqs, FIXED_SPUR_BANDS_HZ)
    result = dict(fit_response_peak(freqs[keep], response[keep]))
    result["excluded_frequency_points_Hz"] = [
        float(value) for value in freqs[~keep]
    ]
    return result


def fit_lorentzian(freqs, response):
    """拟合 C + A*g²/((f-f0)²+g²)，失败时保留最大点。"""
    freqs = np.asarray(freqs, dtype=float)
    response = np.asarray(response, dtype=float)
    valid = np.isfinite(freqs) & np.isfinite(response)
    f = freqs[valid]
    r = response[valid]
    out = {
        "success": False,
        "center_Hz": np.nan,
        "center_unc_Hz": np.nan,
        "fwhm_Hz": np.nan,
        "max_bin_Hz": np.nan,
        "max_response_V": np.nan,
        "rmse_V": np.nan,
        "fit_freq_Hz": np.array([]),
        "fit_response_V": np.array([]),
        "message": "insufficient points",
    }
    if len(f) == 0:
        return out
    max_idx = int(np.argmax(r))
    out["max_bin_Hz"] = float(f[max_idx])
    out["max_response_V"] = float(r[max_idx])
    out["center_Hz"] = float(f[max_idx])
    if len(f) < FIT_MIN_POINTS:
        return out

    span = float(f.max() - f.min())
    step = float(np.median(np.diff(np.sort(f)))) if len(f) > 1 else 1.0
    baseline0 = float(np.percentile(r, 10))
    amplitude0 = max(float(r[max_idx] - baseline0), np.finfo(float).eps)
    gamma0 = max(span / 10.0, step)

    def model(freq, baseline, amplitude, center, hwhm):
        return baseline + amplitude * hwhm**2 / ((freq - center) ** 2 + hwhm**2)

    try:
        popt, pcov = scipy_optimize.curve_fit(
            model,
            f,
            r,
            p0=[baseline0, amplitude0, float(f[max_idx]), gamma0],
            bounds=(
                [-np.inf, 0.0, float(f.min()), max(step * 0.1, 1e-3)],
                [np.inf, 20.0 * max(float(np.ptp(r)), amplitude0), float(f.max()), max(span, step)],
            ),
            maxfev=30000,
        )
        predicted = model(f, *popt)
        grid = np.linspace(float(f.min()), float(f.max()), 800)
        perr = np.sqrt(np.diag(pcov))
        out.update(
            {
                "success": True,
                "center_Hz": float(popt[2]),
                "center_unc_Hz": float(perr[2]),
                "fwhm_Hz": float(2.0 * popt[3]),
                "rmse_V": float(np.sqrt(np.mean((r - predicted) ** 2))),
                "fit_freq_Hz": grid,
                "fit_response_V": model(grid, *popt),
                "message": "ok",
            }
        )
    except Exception as exc:
        out["message"] = f"fit failed: {type(exc).__name__}: {exc}"
    return out


def fit_bloch_response(freqs, response):
    """拟合恒定旋转控制场的 Bloch 稳态幅频响应。"""
    freqs = np.asarray(freqs, dtype=float)
    response = np.asarray(response, dtype=float)
    valid = np.isfinite(freqs) & np.isfinite(response) & (freqs > 0)
    f = freqs[valid]
    r = response[valid]
    out = {
        "success": False,
        "center_Hz": np.nan,
        "center_unc_Hz": np.nan,
        "fwhm_Hz": np.nan,
        "max_bin_Hz": np.nan,
        "max_response_V": np.nan,
        "rmse_V": np.nan,
        "fit_freq_Hz": np.array([]),
        "fit_response_V": np.array([]),
        "message": "insufficient points",
        "model": "bloch_steady_state",
    }
    if len(f) == 0:
        return out
    max_idx = int(np.argmax(r))
    out["max_bin_Hz"] = float(f[max_idx])
    out["max_response_V"] = float(r[max_idx])
    out["center_Hz"] = float(f[max_idx])
    if len(f) < FIT_MIN_POINTS:
        return out

    f_min = float(f.min())
    f_max = float(f.max())
    span = f_max - f_min
    step = float(np.median(np.diff(np.sort(f))))
    baseline0 = float(np.percentile(r, 10))
    gamma0 = max(step, span / 20.0)
    omega0 = float(f[max_idx])
    p0 = max(2.0 * gamma0 * (float(r[max_idx]) - baseline0), 1e-9)

    def model(freq, baseline, amplitude, gamma, omega_ctrl):
        numerator = np.sqrt(gamma**2 + freq**2)
        denominator = np.sqrt(
            (gamma**2 - freq**2 + omega_ctrl**2) ** 2
            + 4.0 * gamma**2 * freq**2
        )
        return baseline + amplitude * numerator / np.maximum(denominator, 1e-15)

    bounds = (
        [-np.inf, 0.0, max(step * 0.05, 1e-3), f_min],
        [np.inf, np.inf, max(span, step), f_max],
    )
    best = None
    for gamma_factor in (0.25, 0.5, 1.0, 2.0, 4.0):
        for omega_factor in (0.9, 1.0, 1.1):
            initial = [
                baseline0,
                p0,
                min(max(gamma0 * gamma_factor, bounds[0][2]), bounds[1][2]),
                min(max(omega0 * omega_factor, f_min), f_max),
            ]
            try:
                popt, pcov = scipy_optimize.curve_fit(
                    model,
                    f,
                    r,
                    p0=initial,
                    bounds=bounds,
                    maxfev=30000,
                )
                predicted = model(f, *popt)
                rmse = float(np.sqrt(np.mean((r - predicted) ** 2)))
                if best is None or rmse < best[0]:
                    best = (rmse, popt, pcov)
            except Exception:
                continue
    if best is None:
        out["message"] = "all Bloch fit restarts failed"
        return out

    rmse, popt, pcov = best
    perr = np.sqrt(np.diag(pcov))
    grid = np.linspace(f_min, f_max, 800)
    out.update(
        {
            "success": True,
            "center_Hz": float(popt[3]),
            "center_unc_Hz": float(perr[3]),
            "fwhm_Hz": float(2.0 * popt[2]),
            "rmse_V": rmse,
            "fit_freq_Hz": grid,
            "fit_response_V": model(grid, *popt),
            "message": "ok",
        }
    )
    return out


def fit_response_peak(freqs, response):
    """优先使用物理 Bloch 模型，并以 Lorentzian/最大点作为稳健回退。"""
    bloch = fit_bloch_response(freqs, response)
    lorentz = fit_lorentzian(freqs, response)
    lorentz["model"] = "single_lorentzian"
    if bloch["success"] and lorentz["success"]:
        # Bloch 是主模型；若其残差显著更差，则使用经验 Lorentzian。
        selected = bloch if bloch["rmse_V"] <= 1.5 * lorentz["rmse_V"] else lorentz
    elif bloch["success"]:
        selected = bloch
    else:
        selected = lorentz
    selected = dict(selected)
    selected["bloch_fit_success"] = bool(bloch["success"])
    selected["bloch_rmse_V"] = float(bloch["rmse_V"]) if np.isfinite(bloch["rmse_V"]) else np.nan
    selected["lorentzian_fit_success"] = bool(lorentz["success"])
    selected["lorentzian_rmse_V"] = (
        float(lorentz["rmse_V"]) if np.isfinite(lorentz["rmse_V"]) else np.nan
    )
    return selected


def linear_fit(x, y, min_points=3):
    """普通最小二乘直线拟合及基本质量指标。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    valid = np.isfinite(x) & np.isfinite(y)
    x_v = x[valid]
    y_v = y[valid]
    out = {
        "success": False,
        "slope_Hz_per_V": np.nan,
        "intercept_Hz": np.nan,
        "slope_unc_Hz_per_V": np.nan,
        "intercept_unc_Hz": np.nan,
        "r_squared": np.nan,
        "rmse_Hz": np.nan,
        "n_points": int(len(x_v)),
        "residual_Hz": np.full(len(x), np.nan),
    }
    if len(x_v) < min_points or np.ptp(x_v) <= 0:
        return out
    coeff, cov = np.polyfit(x_v, y_v, 1, cov=True)
    slope, intercept = float(coeff[0]), float(coeff[1])
    pred_v = slope * x_v + intercept
    residual_v = y_v - pred_v
    ss_res = float(np.sum(residual_v**2))
    ss_tot = float(np.sum((y_v - np.mean(y_v)) ** 2))
    residual_all = np.full(len(x), np.nan)
    residual_all[valid] = residual_v
    out.update(
        {
            "success": True,
            "slope_Hz_per_V": slope,
            "intercept_Hz": intercept,
            "slope_unc_Hz_per_V": float(np.sqrt(cov[0, 0])),
            "intercept_unc_Hz": float(np.sqrt(cov[1, 1])),
            "r_squared": float(1.0 - ss_res / ss_tot) if ss_tot > 0 else np.nan,
            "rmse_Hz": float(np.sqrt(np.mean(residual_v**2))),
            "residual_Hz": residual_all,
        }
    )
    return out


def clean_fit(fit):
    """提取可写入 YAML/JSON 的线性拟合标量。"""
    return {
        key: (bool(value) if isinstance(value, (bool, np.bool_)) else
              int(value) if isinstance(value, (int, np.integer)) else
              None if isinstance(value, float) and not np.isfinite(value) else
              float(value) if isinstance(value, (float, np.floating)) else value)
        for key, value in fit.items()
        if key != "residual_Hz"
    }

# %% Cell 4
# ========== 加载并按包络电压分组 ==========
groups = {}
for record in global_index:
    envelope_v = float(record["xy_envelope_voltage_V"])
    groups.setdefault(envelope_v, []).append(record)

envelope_values = np.array(sorted(groups), dtype=float)
per_voltage = {}

for envelope_v in envelope_values:
    records = sorted(groups[float(envelope_v)], key=lambda item: item["z_freq_Hz"])
    z_freqs = np.asarray([record["z_freq_Hz"] for record in records], dtype=float)
    lockin_r = np.full(len(records), np.nan)
    fft_r = np.full(len(records), np.nan)
    hardware_r = np.full(len(records), np.nan)

    for idx, record in enumerate(records):
        path = raw_dir / record["subdir"] / record["file"]
        data = np.load(path, allow_pickle=True)
        require_npz_fields(data, ["xy_envelope_voltage_V", "z_freq_Hz"], path)
        if "y_V" in data.files:
            fs = float(data["actual_rate_Sa_s"])
            y = np.asarray(data["y_V"], dtype=float)
            lockin_r[idx] = offline_lockin_r(
                y,
                fs,
                z_freqs[idx],
                use_integer_cycles=OFFLINE_LOCKIN_USE_INTEGER_CYCLES,
            )["R"]
            fft_r[idx] = nearest_bin_fft_amp(y, fs, z_freqs[idx])
        if "demod3_r_vector_mean_V" in data.files:
            hardware_r[idx] = float(data["demod3_r_vector_mean_V"])

    # Demod3 直接给出随 Z 扫频的硬件复矢量均值，对当前数据中的固定
    # 20 kHz 相干线更容易诊断，因此作为标定主响应；离线锁相仅作回退。
    primary_method = "hardware_demod3_spur_masked"
    primary_response = hardware_r
    if not np.any(np.isfinite(primary_response)):
        primary_method = "offline_lockin_spur_masked"
        primary_response = lockin_r
    if not np.any(np.isfinite(primary_response)):
        raise ValueError(f"envelope={envelope_v:+.4f} V 没有可用响应数据")

    unfiltered_primary_max = raw_maximum(z_freqs, primary_response)
    primary_fit = fit_response_peak_excluding_spurs(z_freqs, primary_response)
    lockin_fit = fit_response_peak_excluding_spurs(z_freqs, lockin_r)
    fft_fit = fit_response_peak_excluding_spurs(z_freqs, fft_r)
    hardware_fit = fit_response_peak_excluding_spurs(z_freqs, hardware_r)
    excluded_spur_was_raw_max = frequency_in_bands(
        unfiltered_primary_max["frequency_Hz"], FIXED_SPUR_BANDS_HZ
    )
    near_excluded_band = frequency_in_bands(
        primary_fit["center_Hz"],
        FIXED_SPUR_BANDS_HZ,
        margin_Hz=SPUR_PROXIMITY_MARGIN_HZ,
    )
    per_voltage[float(envelope_v)] = {
        "z_freq_Hz": z_freqs,
        "lockin_R_V": lockin_r,
        "fft_R_V": fft_r,
        "hardware_R_V": hardware_r,
        "primary_method": primary_method,
        "primary_response_V": primary_response,
        "primary_fit": primary_fit,
        "unfiltered_primary_max": unfiltered_primary_max,
        "excluded_spur_was_raw_max": excluded_spur_was_raw_max,
        "near_excluded_band": near_excluded_band,
        "lockin_fit": lockin_fit,
        "fft_fit": fft_fit,
        "hardware_fit": hardware_fit,
    }
    print(
        f"V={envelope_v:+.4f} V: center={primary_fit['center_Hz']:.2f} Hz "
        f"({primary_method}, model={primary_fit.get('model', 'max-bin')}, "
        f"fit={'OK' if primary_fit['success'] else 'max-bin fallback'}, "
        f"raw_max={unfiltered_primary_max['frequency_Hz']:.1f} Hz, "
        f"near_spur={'YES' if near_excluded_band else 'no'})"
    )

# %% Cell 5
# ========== 电压到峰位映射与正负对称性 ==========
peak_freq_Hz = np.array(
    [per_voltage[float(v)]["primary_fit"]["center_Hz"] for v in envelope_values],
    dtype=float,
)
peak_unc_Hz = np.array(
    [per_voltage[float(v)]["primary_fit"]["center_unc_Hz"] for v in envelope_values],
    dtype=float,
)
fit_success = np.array(
    [per_voltage[float(v)]["primary_fit"]["success"] for v in envelope_values],
    dtype=bool,
)
near_excluded_band = np.array(
    [per_voltage[float(v)]["near_excluded_band"] for v in envelope_values],
    dtype=bool,
)
unfiltered_peak_freq_Hz = np.array(
    [
        per_voltage[float(v)]["unfiltered_primary_max"]["frequency_Hz"]
        for v in envelope_values
    ],
    dtype=float,
)

signed_fit = linear_fit(envelope_values, peak_freq_Hz)
positive_mask = envelope_values >= ABS_FIT_MIN_V
negative_mask = envelope_values <= -ABS_FIT_MIN_V
abs_mask = np.abs(envelope_values) >= ABS_FIT_MIN_V
positive_fit = linear_fit(envelope_values[positive_mask], peak_freq_Hz[positive_mask])
# 负支路以 |V| 为自变量，使斜率可直接和正支路比较。
negative_abs_fit = linear_fit(
    np.abs(envelope_values[negative_mask]), peak_freq_Hz[negative_mask]
)
absolute_fit = linear_fit(np.abs(envelope_values[abs_mask]), peak_freq_Hz[abs_mask])

pair_rows = []
for magnitude in sorted(set(np.abs(envelope_values))):
    if magnitude < ABS_FIT_MIN_V:
        continue
    pos = np.where(np.isclose(envelope_values, magnitude))[0]
    neg = np.where(np.isclose(envelope_values, -magnitude))[0]
    if len(pos) and len(neg):
        f_pos = float(peak_freq_Hz[pos[0]])
        f_neg = float(peak_freq_Hz[neg[0]])
        pair_rows.append(
            {
                "abs_voltage_V": float(magnitude),
                "f_positive_Hz": f_pos,
                "f_negative_Hz": f_neg,
                "positive_minus_negative_Hz": f_pos - f_neg,
                "pair_mean_Hz": 0.5 * (f_pos + f_neg),
            }
        )

# %% Cell 6
# ========== 绘图 ==========
plt.rcParams.update({"figure.dpi": 120, "font.size": 10, "axes.labelsize": 11})

fig1, ax1 = plt.subplots(figsize=(9, 5.5))
ax1.errorbar(
    envelope_values,
    peak_freq_Hz,
    yerr=np.where(np.isfinite(peak_unc_Hz), peak_unc_Hz, 0.0),
    fmt="o",
    color="C0",
    capsize=3,
    label="Spur-masked peak center",
)
if np.any(near_excluded_band):
    ax1.scatter(
        envelope_values[near_excluded_band],
        peak_freq_Hz[near_excluded_band],
        s=75,
        facecolors="none",
        edgecolors="C1",
        linewidths=1.4,
        label="Near excluded band",
        zorder=4,
    )
x_grid = np.linspace(float(envelope_values.min()), float(envelope_values.max()), 500)
if signed_fit["success"]:
    ax1.plot(
        x_grid,
        signed_fit["slope_Hz_per_V"] * x_grid + signed_fit["intercept_Hz"],
        "--",
        color="0.35",
        label=f"Signed linear fit (R²={signed_fit['r_squared']:.3f})",
    )
if absolute_fit["success"]:
    ax1.plot(
        x_grid,
        absolute_fit["slope_Hz_per_V"] * np.abs(x_grid) + absolute_fit["intercept_Hz"],
        "-",
        color="C3",
        label=f"Absolute-voltage fit (R²={absolute_fit['r_squared']:.3f})",
    )
    fit_annotation = (
        r"$f_{peak}=K|V_{env}|+B$"
        "\n"
        f"K = {absolute_fit['slope_Hz_per_V']:.3f} Hz/V"
        "\n"
        f"B = {absolute_fit['intercept_Hz']:+.3f} Hz"
        "\n"
        f"R² = {absolute_fit['r_squared']:.5f}"
    )
    ax1.text(
        0.98,
        0.50,
        fit_annotation,
        transform=ax1.transAxes,
        ha="right",
        va="center",
        fontsize=9,
        bbox={
            "boxstyle": "round,pad=0.35",
            "facecolor": "white",
            "edgecolor": "C3",
            "alpha": 0.88,
        },
    )
ax1.axvline(0.0, color="black", lw=0.8, alpha=0.5)
ax1.set_xlabel("DirectAW constant envelope voltage (V)")
ax1.set_ylabel("Response peak frequency (Hz)")
ax1.set_title("DirectAW DC voltage calibration")
ax1.grid(True, alpha=0.3)
ax1.legend(fontsize=8)
fig1.tight_layout()
fig1.savefig(results_dir / "direct_aw_dc_calibration_curve.png", dpi=150, bbox_inches="tight")
show_or_close(fig1)

fig2, ax2 = plt.subplots(figsize=(9, 4.8))
if absolute_fit["success"]:
    abs_prediction = (
        absolute_fit["slope_Hz_per_V"] * np.abs(envelope_values)
        + absolute_fit["intercept_Hz"]
    )
    abs_residual = peak_freq_Hz - abs_prediction
    ax2.axhline(0.0, color="black", lw=0.8)
    ax2.plot(envelope_values, abs_residual, "o-", color="C3")
else:
    abs_residual = np.full_like(peak_freq_Hz, np.nan)
ax2.set_xlabel("DirectAW constant envelope voltage (V)")
ax2.set_ylabel("Absolute-fit residual (Hz)")
ax2.set_title("DirectAW calibration residual")
ax2.grid(True, alpha=0.3)
fig2.tight_layout()
fig2.savefig(results_dir / "direct_aw_dc_calibration_residual.png", dpi=150, bbox_inches="tight")
show_or_close(fig2)

available_examples = []
for target in EXAMPLE_VOLTAGES_V:
    idx = int(np.argmin(np.abs(envelope_values - target)))
    value = float(envelope_values[idx])
    if value not in available_examples:
        available_examples.append(value)
fig3, axes3 = plt.subplots(
    len(available_examples), 1, figsize=(9, 2.6 * len(available_examples)), sharex=True
)
axes3 = np.atleast_1d(axes3)
for ax, voltage in zip(axes3, available_examples):
    result = per_voltage[voltage]
    ax.plot(result["z_freq_Hz"], result["primary_response_V"], "o", ms=3, label=result["primary_method"])
    keep = frequency_keep_mask(result["z_freq_Hz"], FIXED_SPUR_BANDS_HZ)
    if np.any(~keep):
        ax.plot(
            result["z_freq_Hz"][~keep],
            result["primary_response_V"][~keep],
            "x",
            color="C3",
            ms=5,
            label="Excluded fixed spur",
        )
    for lower_Hz, upper_Hz in FIXED_SPUR_BANDS_HZ:
        ax.axvspan(lower_Hz, upper_Hz, color="C3", alpha=0.08)
    fit = result["primary_fit"]
    if fit["success"]:
        ax.plot(
            fit["fit_freq_Hz"],
            fit["fit_response_V"],
            "-",
            lw=1.2,
            label=fit.get("model", "Selected fit").replace("_", " ").title(),
        )
    ax.axvline(fit["center_Hz"], color="C3", ls="--", lw=0.9)
    ax.set_ylabel("Response (V)")
    ax.set_title(f"Envelope = {voltage:+.2f} V, center = {fit['center_Hz']:.1f} Hz")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)
axes3[-1].set_xlabel("Z RF frequency (Hz)")
fig3.tight_layout()
fig3.savefig(results_dir / "direct_aw_frequency_response_examples.png", dpi=150, bbox_inches="tight")
show_or_close(fig3)

fig4, (ax4a, ax4b) = plt.subplots(2, 1, figsize=(8.5, 8), sharex=True)
if pair_rows:
    pair_abs_v = np.array([row["abs_voltage_V"] for row in pair_rows])
    pair_pos = np.array([row["f_positive_Hz"] for row in pair_rows])
    pair_neg = np.array([row["f_negative_Hz"] for row in pair_rows])
    pair_delta = pair_pos - pair_neg
    ax4a.plot(pair_abs_v, pair_pos, "o-", label="Positive envelope")
    ax4a.plot(pair_abs_v, pair_neg, "s-", label="Negative envelope")
    ax4b.axhline(0.0, color="black", lw=0.8)
    ax4b.plot(pair_abs_v, pair_delta, "o-", color="C4")
ax4a.set_ylabel("Peak frequency (Hz)")
ax4a.set_title("DirectAW sign symmetry")
ax4a.grid(True, alpha=0.3)
ax4a.legend(fontsize=8)
ax4b.set_xlabel("Absolute envelope voltage (V)")
ax4b.set_ylabel("f(+V) - f(-V) (Hz)")
ax4b.grid(True, alpha=0.3)
fig4.tight_layout()
fig4.savefig(results_dir / "direct_aw_sign_symmetry.png", dpi=150, bbox_inches="tight")
show_or_close(fig4)

# %% Cell 7
# ========== 结果保存 ==========
def fit_summary_for_voltage(voltage, result):
    fit = result["primary_fit"]
    return {
        "envelope_voltage_V": float(voltage),
        "primary_method": result["primary_method"],
        "center_Hz": float(fit["center_Hz"]),
        "center_unc_Hz": (
            float(fit["center_unc_Hz"]) if np.isfinite(fit["center_unc_Hz"]) else None
        ),
        "fwhm_Hz": float(fit["fwhm_Hz"]) if np.isfinite(fit["fwhm_Hz"]) else None,
        "max_bin_Hz": float(fit["max_bin_Hz"]),
        "unfiltered_max_bin_Hz": float(
            result["unfiltered_primary_max"]["frequency_Hz"]
        ),
        "unfiltered_max_response_V": float(
            result["unfiltered_primary_max"]["response_V"]
        ),
        "excluded_spur_was_raw_max": bool(result["excluded_spur_was_raw_max"]),
        "near_excluded_band": bool(result["near_excluded_band"]),
        "excluded_frequency_points_Hz": fit["excluded_frequency_points_Hz"],
        "fit_success": bool(fit["success"]),
        "fit_model": fit.get("model", "max_bin"),
        "fit_message": fit["message"],
    }


analysis = {
    "experiment_type": EXPERIMENT_TYPE,
    "data_dir": str(DATA_DIR),
    "primary_response_method": "hardware Demod3 with fixed-spur masking; offline lock-in fallback",
    "fixed_spur_filter": {
        "excluded_bands_Hz": [list(band) for band in FIXED_SPUR_BANDS_HZ],
        "proximity_margin_Hz": SPUR_PROXIMITY_MARGIN_HZ,
        "reason": (
            "0713 数据确认 20 kHz 分量在不同 Z 驱动频率下持续存在，"
            "属于固定相干杂散，不作为标定峰。"
        ),
    },
    "n_voltage_points": int(len(envelope_values)),
    "voltage_range_V": [float(envelope_values.min()), float(envelope_values.max())],
    "fixed_aw_output": config.get("xy_direct_aw_scan", {}),
    "per_voltage": [
        fit_summary_for_voltage(float(voltage), per_voltage[float(voltage)])
        for voltage in envelope_values
    ],
    "calibration_models": {
        "signed_global": {
            "formula": "f_peak_Hz = K_signed * V_env + B_signed",
            **clean_fit(signed_fit),
        },
        "positive_branch": {
            "formula": "f_peak_Hz = K_pos * V_env + B_pos, V_env >= 0.25 V",
            **clean_fit(positive_fit),
        },
        "negative_branch_abs_axis": {
            "formula": "f_peak_Hz = K_neg_abs * abs(V_env) + B_neg, V_env <= -0.25 V",
            **clean_fit(negative_abs_fit),
        },
        "absolute_voltage_primary": {
            "formula": "f_peak_Hz = K_abs * abs(V_env) + B_abs",
            "fit_min_abs_voltage_V": ABS_FIT_MIN_V,
            **clean_fit(absolute_fit),
        },
    },
    "sign_symmetry": pair_rows,
    "interpretation_note": (
        "负包络只将正交旋转场整体翻转 180 度；若物理链路线性且无偏置，"
        "峰位通常更接近 |V_env| 的函数。主标定已屏蔽固定 20 kHz 杂散；"
        "靠近屏蔽带的点仍标记为低可信度，最终标定应结合背景扣除实验复核。"
    ),
}

with open(results_dir / "analysis.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(analysis, f, allow_unicode=True, sort_keys=False)
with open(results_dir / "analysis.json", "w", encoding="utf-8") as f:
    json.dump(analysis, f, indent=2, ensure_ascii=False)

np.savez(
    results_dir / "direct_aw_dc_calibration_results.npz",
    envelope_voltage_V=envelope_values,
    peak_frequency_Hz=peak_freq_Hz,
    peak_frequency_unc_Hz=peak_unc_Hz,
    peak_fit_success=fit_success,
    peak_near_excluded_band=near_excluded_band,
    unfiltered_peak_frequency_Hz=unfiltered_peak_freq_Hz,
    fixed_spur_bands_Hz=np.asarray(FIXED_SPUR_BANDS_HZ, dtype=float),
    absolute_fit_residual_Hz=abs_residual,
    K_abs_Hz_per_V=(absolute_fit["slope_Hz_per_V"] if absolute_fit["success"] else np.nan),
    B_abs_Hz=(absolute_fit["intercept_Hz"] if absolute_fit["success"] else np.nan),
    R2_abs=(absolute_fit["r_squared"] if absolute_fit["success"] else np.nan),
    K_positive_Hz_per_V=(positive_fit["slope_Hz_per_V"] if positive_fit["success"] else np.nan),
    B_positive_Hz=(positive_fit["intercept_Hz"] if positive_fit["success"] else np.nan),
    K_negative_abs_Hz_per_V=(negative_abs_fit["slope_Hz_per_V"] if negative_abs_fit["success"] else np.nan),
    B_negative_Hz=(negative_abs_fit["intercept_Hz"] if negative_abs_fit["success"] else np.nan),
)

print("\nDirectAW DC 标定结果")
print("=" * 60)
if absolute_fit["success"]:
    print(
        f"PRIMARY |V| model: f_peak = {absolute_fit['slope_Hz_per_V']:+.3f} * |V| "
        f"+ {absolute_fit['intercept_Hz']:+.3f} Hz"
    )
    print(
        f"R^2={absolute_fit['r_squared']:.5f}, "
        f"RMSE={absolute_fit['rmse_Hz']:.2f} Hz"
    )
if positive_fit["success"]:
    print(
        f"Positive branch: K={positive_fit['slope_Hz_per_V']:+.3f} Hz/V, "
        f"B={positive_fit['intercept_Hz']:+.3f} Hz, R^2={positive_fit['r_squared']:.5f}"
    )
if negative_abs_fit["success"]:
    print(
        f"Negative branch on |V|: K={negative_abs_fit['slope_Hz_per_V']:+.3f} Hz/V, "
        f"B={negative_abs_fit['intercept_Hz']:+.3f} Hz, "
        f"R^2={negative_abs_fit['r_squared']:.5f}"
    )
print(f"结果已保存到: {results_dir}")
