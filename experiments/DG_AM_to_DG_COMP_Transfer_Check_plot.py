# %% [markdown] Cell 0
# # dg_am 到 dg_comp 外部 AM 传递曲线离线分析
#
# 无需连接任何仪器。读取采集脚本保存的 `raw/transfer_summary.npz`，
# 绘制 dg_comp 输出幅值随 dg_am DC 输入电压的关系，并估计真实饱和边界。

# %% Cell 1
from pathlib import Path
import sys

project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from lab_workflows.plotting import new_figure, save_figure, set_plot_style
set_plot_style("paper")
import numpy as np
import yaml


# %% Cell 2
# ========== 工具函数 ==========
EXPERIMENT_TYPE = "DG_AM_to_DG_COMP_Transfer_Check"
NOMINAL_AM_FULL_SCALE_V = 1.3


def require_npz_fields(npz_data, required_fields, path):
    """检查 NPZ 字段完整性。"""
    missing = [name for name in required_fields if name not in npz_data.files]
    if missing:
        raise KeyError(f"{path} 缺少字段: {missing}; 实际字段: {npz_data.files}")


def choose_latest_data_dir():
    """选择最新数据目录。"""
    base = project_root / "data" / EXPERIMENT_TYPE
    if not base.exists():
        return None
    dirs = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
    return dirs[0] if dirs else None


def nearest_zero_baseline(x, y):
    """用最接近 0 V 的点作为幅值基线。"""
    idx = int(np.argmin(np.abs(x)))
    return float(y[idx])


def estimate_side_boundary(x_abs, y, fraction=0.95):
    """估计达到平台 fraction 的输入电压绝对值。"""
    if len(x_abs) < 3:
        return np.nan
    order = np.argsort(x_abs)
    x_sorted = np.asarray(x_abs, dtype=float)[order]
    y_sorted = np.asarray(y, dtype=float)[order]
    y_max = float(np.max(y_sorted))
    if y_max <= 0:
        return np.nan
    threshold = fraction * y_max
    hit = np.where(y_sorted >= threshold)[0]
    if len(hit) == 0:
        return np.nan
    return float(x_sorted[hit[0]])


def fit_low_voltage_slope(x_abs, y, fit_limit=0.8):
    """拟合小信号区斜率和 R2。"""
    mask = (x_abs > 1e-9) & (x_abs <= fit_limit)
    if np.count_nonzero(mask) < 3:
        return np.nan, np.nan, np.nan
    x_fit = x_abs[mask]
    y_fit = y[mask]
    coeff = np.polyfit(x_fit, y_fit, 1)
    y_pred = np.polyval(coeff, x_fit)
    ss_res = float(np.sum((y_fit - y_pred) ** 2))
    ss_tot = float(np.sum((y_fit - np.mean(y_fit)) ** 2))
    r2 = np.nan if ss_tot <= 0 else 1.0 - ss_res / ss_tot
    return float(coeff[0]), float(coeff[1]), float(r2)


def analyze_channel(am_v, amp_v):
    """分析单通道正负 AM 输入侧传递曲线。"""
    baseline = nearest_zero_baseline(am_v, amp_v)
    y = np.maximum(np.asarray(amp_v, dtype=float) - baseline, 0.0)

    pos = am_v >= 0
    neg = am_v <= 0
    out = {
        "baseline_amp_V": baseline,
        "positive_boundary_95_V": estimate_side_boundary(np.abs(am_v[pos]), y[pos]),
        "negative_boundary_95_abs_V": estimate_side_boundary(np.abs(am_v[neg]), y[neg]),
    }

    for side_name, mask in [("positive", pos), ("negative", neg)]:
        slope, intercept, r2 = fit_low_voltage_slope(np.abs(am_v[mask]), y[mask])
        out[f"{side_name}_small_signal_slope_V_per_V"] = slope
        out[f"{side_name}_small_signal_intercept_V"] = intercept
        out[f"{side_name}_small_signal_r2"] = r2
    return y, out


def save_analysis_yaml(path, data):
    """保存可读 YAML。"""
    def convert(obj):
        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, dict):
            return {k: convert(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [convert(v) for v in obj]
        return obj

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(convert(data), f, allow_unicode=True, sort_keys=False)


# %% Cell 3
# ========== 选择数据目录 ==========
USE_LATEST = True

if USE_LATEST:
    DATA_DIR = choose_latest_data_dir()
else:
    DATA_DIR = project_root / "data" / EXPERIMENT_TYPE / "MMDD_HHMM_tag"

if DATA_DIR is None or not DATA_DIR.exists():
    raise FileNotFoundError(
        f"未找到数据目录: {DATA_DIR}\n"
        f"请确认 data/{EXPERIMENT_TYPE}/ 下有实验数据，或设置 USE_LATEST=False 手动指定路径"
    )

raw_dir = DATA_DIR / "raw"
results_dir = DATA_DIR / "results"
results_dir.mkdir(parents=True, exist_ok=True)
print(f"分析数据目录: {DATA_DIR}")

# %% Cell 4
# ========== 加载数据 ==========
summary_path = raw_dir / "transfer_summary.npz"
if not summary_path.exists():
    raise FileNotFoundError(f"未找到汇总文件: {summary_path}")

summary = np.load(summary_path, allow_pickle=True)
require_npz_fields(
    summary,
    [
        "am_dc_values_V",
        "ch_x_lockin_amp_V",
        "ch_y_lockin_amp_V",
        "ch_x_peak_amp_V",
        "ch_y_peak_amp_V",
    ],
    summary_path,
)

am_v = summary["am_dc_values_V"].astype(float)
ch_x = summary["ch_x_lockin_amp_V"].astype(float)
ch_y = summary["ch_y_lockin_amp_V"].astype(float)

cfg_path = DATA_DIR / "experiment_config.yaml"
if cfg_path.exists():
    with open(cfg_path, encoding="utf-8") as f:
        experiment_config = yaml.safe_load(f)
    NOMINAL_AM_FULL_SCALE_V = float(
        experiment_config.get("parameters", {}).get(
            "NOMINAL_AM_FULL_SCALE_V", NOMINAL_AM_FULL_SCALE_V
        )
    )
else:
    experiment_config = {}

# %% Cell 5
# ========== 分析 ==========
ch_x_corr, ch_x_analysis = analyze_channel(am_v, ch_x)
ch_y_corr, ch_y_analysis = analyze_channel(am_v, ch_y)

analysis = {
    "data_dir": str(DATA_DIR),
    "nominal_am_full_scale_V": NOMINAL_AM_FULL_SCALE_V,
    "channel_x": ch_x_analysis,
    "channel_y": ch_y_analysis,
    "interpretation": (
        "positive_boundary_95_V / negative_boundary_95_abs_V are estimated "
        "from the first input voltage where the baseline-subtracted output "
        "amplitude reaches 95% of the observed side maximum."
    ),
}
analysis_path = results_dir / "transfer_analysis.yaml"
save_analysis_yaml(analysis_path, analysis)
print(f"分析结果已保存: {analysis_path}")
print(json.dumps(analysis, ensure_ascii=False, indent=2))

# %% Cell 6
# ========== 绘图 ==========
fig, axes = new_figure(nrows=3, ncols=1, kind="wide", height_mm=165, sharex=True)

axes[0].plot(am_v, ch_x, "o-", label="X / C2 demod.")
axes[0].plot(am_v, ch_y, "o-", label="Y / C3 demod.")
axes[0].axvline(NOMINAL_AM_FULL_SCALE_V, color="black", ls="--", lw=0.8,
                label="Nominal +/-1.3 V")
axes[0].axvline(-NOMINAL_AM_FULL_SCALE_V, color="black", ls="--", lw=0.8)
axes[0].set_ylabel("Output peak amp. (V)")
axes[0].set_title("dg_comp Output vs dg_am DC")
axes[0].grid(False)
axes[0].legend(fontsize=8)

axes[1].plot(am_v, ch_x_corr, "o-", label="X baseline-subtracted")
axes[1].plot(am_v, ch_y_corr, "o-", label="Y baseline-subtracted")
axes[1].axvline(NOMINAL_AM_FULL_SCALE_V, color="black", ls="--", lw=0.8)
axes[1].axvline(-NOMINAL_AM_FULL_SCALE_V, color="black", ls="--", lw=0.8)
for ana, color in [(ch_x_analysis, "C0"), (ch_y_analysis, "C1")]:
    if np.isfinite(ana["positive_boundary_95_V"]):
        axes[1].axvline(ana["positive_boundary_95_V"], color=color, ls=":", lw=1.0)
    if np.isfinite(ana["negative_boundary_95_abs_V"]):
        axes[1].axvline(-ana["negative_boundary_95_abs_V"], color=color, ls=":", lw=1.0)
axes[1].set_ylabel("Baseline-subtracted amp. (V)")
axes[1].grid(False)
axes[1].legend(fontsize=8)

eps = 1e-12
axes[2].plot(np.abs(am_v), ch_x_corr / max(np.max(ch_x_corr), eps), "o", label="X")
axes[2].plot(np.abs(am_v), ch_y_corr / max(np.max(ch_y_corr), eps), "o", label="Y")
axes[2].axvline(NOMINAL_AM_FULL_SCALE_V, color="black", ls="--", lw=0.8)
axes[2].set_xlabel("|dg_am DC voltage| (V)")
axes[2].set_ylabel("Normalized amp.")
axes[2].set_title("Amplitude Saturation vs |AM Input|")
axes[2].grid(False)
axes[2].legend(fontsize=8)

fig.set_layout_engine("constrained")
fig_path = results_dir / "transfer_curve_analysis.png"
save_figure(fig, fig_path, close=False)
plt.close(fig)
print(f"传递曲线图已保存: {fig_path}")

# %% Cell 7
# ========== 保存处理后数据 ==========
np.savez_compressed(
    results_dir / "transfer_analysis_arrays.npz",
    am_dc_values_V=am_v,
    ch_x_lockin_amp_V=ch_x,
    ch_y_lockin_amp_V=ch_y,
    ch_x_baseline_subtracted_amp_V=ch_x_corr,
    ch_y_baseline_subtracted_amp_V=ch_y_corr,
)
print("离线分析完成")
