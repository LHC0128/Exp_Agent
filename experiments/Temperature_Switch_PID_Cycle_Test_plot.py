# %% [markdown] Cell 0
# # 温度开关周期响应离线分析
#
# 读取 `Temperature_Switch_PID_Cycle_Test` 采集数据，绘制温度、偏差、开关状态和周期相位平均结果。
# 本脚本不连接任何仪器。

# %% Cell 1
from pathlib import Path
import sys

project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from lab_workflows.plotting import new_figure, save_figure, set_plot_style
set_plot_style("paper")
import numpy as np
import yaml

print("库导入完成")

# %% Cell 2
EXPERIMENT_TYPE = "Temperature_Switch_PID_Cycle_Test"
USE_LATEST = True
DATA_DIR = project_root / "data" / EXPERIMENT_TYPE / "MMDD_HHMM_tag"


def require_npz_fields(npz_data, required_fields, path):
    """检查 npz 字段完整性."""
    missing = [name for name in required_fields if name not in npz_data.files]
    if missing:
        raise KeyError(f"{path} 缺少字段: {missing}; 实际字段: {npz_data.files}")


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
with open(DATA_DIR / "experiment_config.yaml", encoding="utf-8") as f:
    config = yaml.safe_load(f)

npz_path = raw_dir / "temperature_cycle.npz"
data = np.load(npz_path)
required = [
    "time_s",
    "temperature_C",
    "target_temperature_C",
    "error_C",
    "temp_switch_state",
    "cycle_phase_s",
]
require_npz_fields(data, required, npz_path)

time_s = data["time_s"]
temperature_C = data["temperature_C"]
target_temperature_C = data["target_temperature_C"]
error_C = data["error_C"]
switch_state = data["temp_switch_state"]
cycle_phase_s = data["cycle_phase_s"]

params = config.get("parameters", {})
on_duration_s = float(params.get("on_duration_s", np.nan))
off_duration_s = float(params.get("off_duration_s", np.nan))
cycle_period_s = on_duration_s + off_duration_s

print(f"样本数: {len(time_s)}, 时长: {time_s[-1] - time_s[0]:.1f} s")

# %% Cell 5
analysis = {
    "n_samples": int(len(time_s)),
    "duration_s": float(time_s[-1] - time_s[0]) if len(time_s) else 0.0,
    "target_temperature_C": float(np.nanmedian(target_temperature_C)),
    "mean_temperature_C": float(np.nanmean(temperature_C)),
    "mean_error_C": float(np.nanmean(error_C)),
    "std_error_C": float(np.nanstd(error_C)),
    "max_abs_error_C": float(np.nanmax(np.abs(error_C))),
    "peak_to_peak_error_C": float(np.nanmax(error_C) - np.nanmin(error_C)),
    "min_error_C": float(np.nanmin(error_C)),
    "max_error_C": float(np.nanmax(error_C)),
    "on_duration_s": on_duration_s,
    "off_duration_s": off_duration_s,
}

with open(results_dir / "analysis.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(analysis, f, allow_unicode=True, sort_keys=False)

for key, value in analysis.items():
    if isinstance(value, float):
        print(f"{key}: {value:.6g}")
    else:
        print(f"{key}: {value}")

# %% Cell 6
fig, axes = new_figure(nrows=3, ncols=1, kind="wide", height_mm=165, sharex=False)
ax_temp, ax_error, ax_phase = axes

ax_temp.plot(time_s, temperature_C, lw=1.0, label="Temperature")
ax_temp.plot(time_s, target_temperature_C, "--", color="gray", lw=1.0, label="Setpoint")
ax_temp_state = ax_temp.twinx()
ax_temp_state.step(time_s, switch_state, where="post", color="tab:orange", alpha=0.35, label="Switch")
ax_temp_state.set_ylim(-0.1, 1.1)
ax_temp_state.set_yticks([0, 1])
ax_temp_state.set_yticklabels(["OFF", "ON"])
ax_temp.set_ylabel("Temperature (C)")
ax_temp.set_title("Temperature vs time")
ax_temp.grid(False)
ax_temp.legend(loc="upper left")

ax_error.plot(time_s, error_C, lw=1.0, color="tab:red", label="Error")
ax_error.axhline(0.0, color="gray", ls="--", lw=1.0)
ax_error.set_xlabel("Time (s)")
ax_error.set_ylabel("Error (C)")
ax_error.set_title("Temperature error vs time")
ax_error.grid(False)
ax_error.legend(loc="upper left")

if np.isfinite(cycle_period_s) and cycle_period_s > 0:
    bins = np.linspace(0.0, cycle_period_s, 80)
    centers = 0.5 * (bins[:-1] + bins[1:])
    mean_err = np.full_like(centers, np.nan, dtype=float)
    std_err = np.full_like(centers, np.nan, dtype=float)
    for idx in range(len(centers)):
        mask = (cycle_phase_s >= bins[idx]) & (cycle_phase_s < bins[idx + 1])
        if np.any(mask):
            mean_err[idx] = np.nanmean(error_C[mask])
            std_err[idx] = np.nanstd(error_C[mask])

    ax_phase.plot(centers, mean_err, color="tab:purple", lw=1.3, label="Mean error")
    ax_phase.fill_between(
        centers,
        mean_err - std_err,
        mean_err + std_err,
        color="tab:purple",
        alpha=0.18,
        label="Std",
    )
    ax_phase.axhline(0.0, color="gray", ls="--", lw=1.0)
    ax_phase.axvspan(0.0, on_duration_s, color="tab:orange", alpha=0.12, label="Switch ON")
    ax_phase.axvspan(on_duration_s, cycle_period_s, color="tab:blue", alpha=0.08, label="Switch OFF")
    ax_phase.set_xlim(0.0, cycle_period_s)
else:
    ax_phase.text(0.5, 0.5, "Cycle period unavailable", ha="center", va="center")

ax_phase.set_xlabel("Cycle phase (s)")
ax_phase.set_ylabel("Error (C)")
ax_phase.set_title("Cycle-averaged temperature error")
ax_phase.grid(False)
ax_phase.legend(loc="best", fontsize=8)

plt.gcf().set_layout_engine("constrained")
save_figure(fig, results_dir / "temperature_cycle_analysis.png", close=False)
plt.show()
print(f"图表已保存: {results_dir / 'temperature_cycle_analysis.png'}")