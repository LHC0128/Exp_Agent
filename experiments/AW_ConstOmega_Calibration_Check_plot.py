# %% [markdown] Cell 0
# # AW 常数 Omega_ctrl 标定 sanity check — 离线分析
#
# 无需连接任何仪器。读取 `AW_ConstOmega_Calibration_Check.py` 保存的 raw 数据，
# 计算每个换算公式和 Omega_const 下的 Z RF 峰值、残差，并保存图表与 `analysis.yaml`。

# %% Cell 1
from pathlib import Path
import sys

project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import csv
import json

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import numpy as np
import yaml

print("库导入完成")

# %% Cell 2
def require_npz_fields(npz_data, required_fields, path):
    missing = [name for name in required_fields if name not in npz_data.files]
    if missing:
        raise KeyError(f"{path} 缺少字段: {missing}; 实际字段: {npz_data.files}")


def scalar(value):
    arr = np.asarray(value)
    if arr.shape == ():
        return arr.item()
    return arr.tolist()


def contiguous_peak_width(freq_hz, response_v, peak_idx):
    """用半高阈值估计峰宽，只作为通过阈值的粗略参考。"""
    freq_hz = np.asarray(freq_hz, dtype=float)
    response_v = np.asarray(response_v, dtype=float)
    finite = np.isfinite(response_v)
    if np.count_nonzero(finite) < 2:
        return None

    baseline = float(np.nanmin(response_v))
    peak = float(response_v[peak_idx])
    if not np.isfinite(peak) or peak <= baseline:
        return None
    threshold = baseline + 0.5 * (peak - baseline)

    left = int(peak_idx)
    while left > 0 and response_v[left - 1] >= threshold:
        left -= 1
    right = int(peak_idx)
    while right < len(response_v) - 1 and response_v[right + 1] >= threshold:
        right += 1
    if right <= left:
        return None
    return float(freq_hz[right] - freq_hz[left])


def analyze_single_trace(path):
    with np.load(path, allow_pickle=True) as data:
        require_npz_fields(
            data,
            [
                "mode",
                "omega_const_Hz",
                "z_freq_Hz",
                "r_V",
                "am_voltage_x_V",
                "am_voltage_y_V",
            ],
            path,
        )
        mode = str(scalar(data["mode"]))
        omega = float(scalar(data["omega_const_Hz"]))
        freq = np.asarray(data["z_freq_Hz"], dtype=float)
        response = np.asarray(data["r_V"], dtype=float)
        vx = float(scalar(data["am_voltage_x_V"]))
        vy = float(scalar(data["am_voltage_y_V"]))

    if len(freq) != len(response):
        raise ValueError(f"{path} 的 z_freq_Hz 与 r_V 长度不一致")
    if not np.any(np.isfinite(response)):
        raise ValueError(f"{path} 的 r_V 没有有效数据")

    peak_idx = int(np.nanargmax(response))
    peak_freq = float(freq[peak_idx])
    peak_response = float(response[peak_idx])
    residual = float(peak_freq - omega)
    peak_width = contiguous_peak_width(freq, response, peak_idx)
    tolerance = max(100.0, 0.1 * peak_width) if peak_width is not None else 100.0

    return {
        "path": path,
        "file": path.name,
        "mode": mode,
        "omega_const_Hz": omega,
        "z_freq_Hz": freq,
        "r_V": response,
        "am_voltage_x_V": vx,
        "am_voltage_y_V": vy,
        "peak_index": peak_idx,
        "peak_freq_Hz": peak_freq,
        "peak_R_V": peak_response,
        "residual_Hz": residual,
        "peak_width_half_height_Hz": peak_width,
        "acceptance_tolerance_Hz": tolerance,
        "pass_acceptance": bool(abs(residual) <= tolerance),
    }


# %% Cell 3
EXPERIMENT_TYPE = "AW_ConstOmega_Calibration_Check"

USE_LATEST = True
if USE_LATEST:
    base = project_root / "data" / EXPERIMENT_TYPE
    if base.exists():
        dirs = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
        DATA_DIR = dirs[0] if dirs else None
    else:
        DATA_DIR = None
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
print(f"数据目录: {DATA_DIR}")

# %% Cell 4
config_path = DATA_DIR / "experiment_config.yaml"
if config_path.exists():
    with open(config_path, encoding="utf-8") as f:
        experiment_config = yaml.safe_load(f)
    print("实验配置已加载")
else:
    experiment_config = {}
    print("[WARN] 未找到 experiment_config.yaml")

raw_index_path = raw_dir / "raw_index.json"
if raw_index_path.exists():
    with open(raw_index_path, encoding="utf-8") as f:
        raw_index = json.load(f)
    raw_paths = [raw_dir / rec["file"] for rec in raw_index]
else:
    raw_paths = sorted(raw_dir.glob("*.npz"))

raw_paths = [path for path in raw_paths if path.exists()]
if not raw_paths:
    raise FileNotFoundError(f"未找到 raw npz 数据: {raw_dir}")

traces = [analyze_single_trace(path) for path in raw_paths]
traces = sorted(traces, key=lambda r: (r["mode"], r["omega_const_Hz"]))
modes = sorted({r["mode"] for r in traces})
omega_values = sorted({r["omega_const_Hz"] for r in traces})

print(f"已加载 {len(traces)} 条曲线")
print(f"模式: {modes}")
print(f"Omega_const: {omega_values}")

# %% Cell 5
plt.rcParams.update({"figure.dpi": 120, "font.size": 11, "axes.labelsize": 12})

n_rows = max(1, len(omega_values))
fig1, axes = plt.subplots(n_rows, 1, figsize=(9.0, max(3.2, 2.4 * n_rows)), sharex=False)
if n_rows == 1:
    axes = [axes]

for ax, omega in zip(axes, omega_values):
    for mode in modes:
        matches = [r for r in traces if r["mode"] == mode and np.isclose(r["omega_const_Hz"], omega)]
        if not matches:
            continue
        rec = matches[0]
        ax.plot(rec["z_freq_Hz"], rec["r_V"], ".-", ms=4, lw=1.1, label=mode)
        ax.axvline(rec["peak_freq_Hz"], color="0.35", ls=":", lw=0.8, alpha=0.6)
    ax.axvline(omega, color="red", ls="--", lw=0.8, alpha=0.7, label="Target Omega")
    ax.set_title(f"Omega_const = {omega:.1f} Hz")
    ax.set_xlabel("Z RF Frequency (Hz)")
    ax.set_ylabel("Demod 3 R (V)")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)

fig1.tight_layout()
response_path = results_dir / "const_aw_response_curves.png"
fig1.savefig(response_path, dpi=150, bbox_inches="tight")
print(f"响应曲线已保存: {response_path}")
plt.show()

fig2, ax2 = plt.subplots(figsize=(8.8, 5.2))
for mode in modes:
    rows = [r for r in traces if r["mode"] == mode]
    rows = sorted(rows, key=lambda r: r["omega_const_Hz"])
    ax2.plot(
        [r["omega_const_Hz"] for r in rows],
        [r["residual_Hz"] for r in rows],
        "o-",
        lw=1.2,
        ms=5,
        label=mode,
    )
ax2.axhline(0.0, color="black", lw=0.8)
ax2.axhline(100.0, color="0.5", lw=0.8, ls="--", alpha=0.6)
ax2.axhline(-100.0, color="0.5", lw=0.8, ls="--", alpha=0.6)
ax2.set_xlabel("Omega_const (Hz)")
ax2.set_ylabel("Peak Frequency - Omega_const (Hz)")
ax2.set_title("Constant AW Omega Calibration Residual")
ax2.grid(True, alpha=0.3)
ax2.legend(fontsize=9)
fig2.tight_layout()
residual_path = results_dir / "const_aw_residuals.png"
fig2.savefig(residual_path, dpi=150, bbox_inches="tight")
print(f"残差图已保存: {residual_path}")
plt.show()

# %% Cell 6
table_rows = []
for rec in traces:
    table_rows.append({
        "mode": rec["mode"],
        "omega_const_Hz": float(rec["omega_const_Hz"]),
        "am_voltage_x_V": float(rec["am_voltage_x_V"]),
        "am_voltage_y_V": float(rec["am_voltage_y_V"]),
        "peak_freq_Hz": float(rec["peak_freq_Hz"]),
        "peak_R_V": float(rec["peak_R_V"]),
        "residual_Hz": float(rec["residual_Hz"]),
        "peak_width_half_height_Hz": rec["peak_width_half_height_Hz"],
        "acceptance_tolerance_Hz": float(rec["acceptance_tolerance_Hz"]),
        "pass_acceptance": bool(rec["pass_acceptance"]),
        "file": rec["file"],
    })

analysis = {
    "experiment_type": EXPERIMENT_TYPE,
    "data_dir": str(DATA_DIR),
    "method": "Peak is selected by max Demod 3 vector R for each Omega_const and conversion mode.",
    "acceptance": "abs(residual) <= max(100 Hz, 10% half-height peak width)",
    "n_traces": len(traces),
    "modes": modes,
    "omega_const_Hz": [float(v) for v in omega_values],
    "records": table_rows,
    "figures": {
        "response_curves": str(response_path),
        "residuals": str(residual_path),
    },
    "config_snapshot": experiment_config,
}

analysis_yaml_path = results_dir / "analysis.yaml"
with open(analysis_yaml_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(analysis, f, allow_unicode=True, sort_keys=False)
print(f"分析结果已保存: {analysis_yaml_path}")

analysis_json_path = results_dir / "analysis.json"
with open(analysis_json_path, "w", encoding="utf-8") as f:
    json.dump(analysis, f, indent=2, ensure_ascii=False)
print(f"分析结果 JSON 已保存: {analysis_json_path}")

summary_json_path = results_dir / "const_aw_omega_check_summary.json"
with open(summary_json_path, "w", encoding="utf-8") as f:
    json.dump({"records": table_rows}, f, indent=2, ensure_ascii=False)
print(f"兼容 summary 已保存: {summary_json_path}")

csv_path = results_dir / "const_aw_peak_table.csv"
with open(csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(table_rows[0].keys()))
    writer.writeheader()
    writer.writerows(table_rows)
print(f"峰值表已保存: {csv_path}")

print("\n分析摘要")
for rec in table_rows:
    ok = "PASS" if rec["pass_acceptance"] else "FAIL"
    print(
        f"{ok} {rec['mode']}: Omega={rec['omega_const_Hz']:.1f} Hz, "
        f"peak={rec['peak_freq_Hz']:.1f} Hz, residual={rec['residual_Hz']:+.1f} Hz"
    )
