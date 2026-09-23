# %% [markdown] Cell 0
# # 温度开关周期响应与 PID 优化数据采集
#
# 周期开断 Temp_Switch，同时读取 TEC103 实际温度，记录温度与设定值的偏差。
# 用于比较不同 PID 参数下的温度纹波、滞后和恢复过程。
#
# ## 信号链路
# - TEC103: 设定并读取 Cell 温度
# - DG912 Pro CH2: Temp_Switch，5 V 表示温控开关 ON，0 V 表示 OFF
#
# ## 数据输出
# `data/Temperature_Switch_PID_Cycle_Test/MMDD_HHMM_tag/`
# - `experiment_config.yaml`
# - `raw/temperature_cycle.npz`
# - `raw/temperature_cycle.csv`

# %% Cell 1
from pathlib import Path
import csv
import sys
import time
from datetime import datetime

project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from lab_workflows.plotting import new_figure, save_figure, set_plot_style
set_plot_style("paper")
import numpy as np
import yaml

from lab_workflows.devices import create_signal_generator
from lab_workflows.common import load_mapping
from tec_controller import TECInstrument

print("库导入完成")

# %% Cell 2
MAPPING = load_mapping(project_root)

with open(project_root / "params" / "safety_limits.yaml", encoding="utf-8") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]

EXPERIMENT_TYPE = "Temperature_Switch_PID_Cycle_Test"
RUN_TAG = "pid_cycle_on2_off1"

# ========== 温控与周期参数 ==========
TARGET_TEMPERATURE_C = 100.0
TEMP_SWITCH_ON_V = 5.0
TEMP_SWITCH_OFF_V = 0.0
ON_DURATION_S = 2.0
OFF_DURATION_S = 1.0
TOTAL_DURATION_S = 600.0
SAMPLE_INTERVAL_S = 0.2

# 默认只记录当前 PID；如需由脚本写入 PID，把 APPLY_PID_PARAMS 改为 True 并填写三个参数。
APPLY_PID_PARAMS = False
PID_KP = None
PID_KI = None
PID_KD = None

# 开始周期前是否先等温度稳定在目标附近。
WAIT_INITIAL_STABLE = True
INITIAL_STABLE_TOLERANCE_C = 0.2
INITIAL_STABLE_READS = 5
INITIAL_STABLE_POLL_S = 2.0
INITIAL_STABLE_TIMEOUT_S = 1800.0

# 实时观察开关；如果只想采集数据后离线画图，可改为 False。
LIVE_PLOT = True
LIVE_PLOT_UPDATE_EVERY = 5
PRINT_INTERVAL_S = 5.0


def validate_safety_limit(name, value):
    """检查数值是否在安全范围内，超出则报错."""
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


def validate_parameters():
    """集中检查本脚本会写入硬件的参数."""
    validate_safety_limit("temperature", TARGET_TEMPERATURE_C)
    validate_safety_limit("Temp_Switch", TEMP_SWITCH_ON_V)
    validate_safety_limit("Temp_Switch", TEMP_SWITCH_OFF_V)
    if ON_DURATION_S <= 0 or OFF_DURATION_S <= 0:
        raise ValueError("ON_DURATION_S 和 OFF_DURATION_S 必须大于 0")
    if TOTAL_DURATION_S <= 0:
        raise ValueError("TOTAL_DURATION_S 必须大于 0")
    if SAMPLE_INTERVAL_S <= 0:
        raise ValueError("SAMPLE_INTERVAL_S 必须大于 0")
    if APPLY_PID_PARAMS:
        if PID_KP is None or PID_KI is None or PID_KD is None:
            raise ValueError("APPLY_PID_PARAMS=True 时必须填写 PID_KP/PID_KI/PID_KD")
        for name, value in {"PID_KP": PID_KP, "PID_KI": PID_KI, "PID_KD": PID_KD}.items():
            if int(value) < 0 or int(value) > 9000000:
                raise ValueError(f"{name}={value} 超出 TEC103 PID 范围 0~9000000")


validate_parameters()
print("参数和安全限值检查完成")

# %% Cell 3
def as_builtin(value):
    """将 numpy 类型转换为 YAML 友好的 Python 类型."""
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {k: as_builtin(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [as_builtin(v) for v in value]
    return value


def safe_call(default, func, *args, **kwargs):
    """仪器可选查询失败时返回默认值，避免影响主采集."""
    try:
        return func(*args, **kwargs)
    except Exception as exc:
        print(f"  可选查询失败: {func.__name__}: {exc}")
        return default


def read_pid_snapshot(tec):
    """读取当前 TEC PID 与输出状态快照."""
    return {
        "kp": safe_call(None, tec.get_kp, channel=1),
        "ki": safe_call(None, tec.get_ki, channel=1),
        "kd": safe_call(None, tec.get_kd, channel=1),
        "enable": safe_call(None, tec.get_enable, channel=1),
        "target_temperature_C": safe_call(None, tec.get_target_temperature, channel=1),
        "max_duty_percent": safe_call(None, tec.get_max_duty, channel=1),
    }


def set_temp_switch(dg_temp, enabled):
    """Temp_Switch 保持输出 ON，通过 5 V/0 V DC 表示开/关."""
    voltage = TEMP_SWITCH_ON_V if enabled else TEMP_SWITCH_OFF_V
    validate_safety_limit("Temp_Switch", float(voltage))
    dg_temp.setup_dc(float(voltage), channel=2)
    dg_temp.set_output(True, channel=2)
    return float(voltage)


def wait_for_initial_stable(tec):
    """周期测试前等待温度接近目标，减少起始漂移影响."""
    stable_count = 0
    start = time.perf_counter()
    while True:
        temp_c = tec.get_temperature(channel=1)
        if not np.isfinite(temp_c):
            raise RuntimeError("TEC 返回无效温度，请检查传感器连接")
        error_c = temp_c - TARGET_TEMPERATURE_C
        if abs(error_c) <= INITIAL_STABLE_TOLERANCE_C:
            stable_count += 1
        else:
            stable_count = 0
        print(
            f"初始稳定等待: T={temp_c:.3f} C, error={error_c:+.3f} C, "
            f"count={stable_count}/{INITIAL_STABLE_READS}"
        )
        if stable_count >= INITIAL_STABLE_READS:
            print("初始温度已稳定，开始周期测试")
            return temp_c
        if time.perf_counter() - start > INITIAL_STABLE_TIMEOUT_S:
            raise TimeoutError("初始温度稳定等待超时")
        time.sleep(INITIAL_STABLE_POLL_S)


def write_experiment_config(run_dir, config):
    """保存配置快照."""
    with open(run_dir / "experiment_config.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(as_builtin(config), f, allow_unicode=True, sort_keys=False)


def save_records(raw_dir, records):
    """保存温度周期响应原始数据."""
    fieldnames = [
        "time_s",
        "temperature_C",
        "target_temperature_C",
        "error_C",
        "temp_switch_state",
        "temp_switch_voltage_V",
        "cycle_index",
        "cycle_phase_s",
    ]
    csv_path = raw_dir / "temperature_cycle.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in records:
            writer.writerow(row)

    arrays = {name: np.array([row[name] for row in records], dtype=float) for name in fieldnames}
    np.savez(raw_dir / "temperature_cycle.npz", **arrays)
    return ["raw/temperature_cycle.npz", "raw/temperature_cycle.csv"]


print("工具函数已定义")

# %% Cell 4
devices = {}

try:
    temp_cfg = MAPPING["Temp_Switch"]
    dg_temp = create_signal_generator(temp_cfg["resource"], channel=temp_cfg["channel"])
    dg_temp.connect()
    devices["dg_temp"] = dg_temp
    print(f"dg_temp 已连接: {dg_temp.idn()}")

    tec_cfg = MAPPING["temperature"]
    tec = TECInstrument(port=tec_cfg["resource"])
    tec.connect()
    devices["tec"] = tec
    print(f"TEC103 已连接: {tec_cfg['resource']}")

except Exception as exc:
    print(f"设备连接失败: {exc}")
    for dev in set(devices.values()):
        try:
            if hasattr(dev, "disconnect"):
                dev.disconnect()
        except Exception:
            pass
    raise

print(f"设备连接完成: {list(devices.keys())}")

# %% Cell 5
tec = devices["tec"]
dg_temp = devices["dg_temp"]

if APPLY_PID_PARAMS:
    tec.set_pid(int(PID_KP), int(PID_KI), int(PID_KD), channel=1)
    print(f"已写入 TEC PID: KP={PID_KP}, KI={PID_KI}, KD={PID_KD}")

validate_safety_limit("temperature", TARGET_TEMPERATURE_C)
tec.set_target_temperature(TARGET_TEMPERATURE_C, channel=1)
tec.set_enable(True, channel=1)
set_temp_switch(dg_temp, True)

pid_snapshot_before = read_pid_snapshot(tec)
print("TEC PID 快照:")
for key, value in pid_snapshot_before.items():
    print(f"  {key}: {value}")

if WAIT_INITIAL_STABLE:
    wait_for_initial_stable(tec)
else:
    temp_now = tec.get_temperature(channel=1)
    print(f"跳过初始稳定等待: T={temp_now:.3f} C")

# %% Cell 6
timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
raw_dir = run_dir / "raw"
results_dir = run_dir / "results"
raw_dir.mkdir(parents=True, exist_ok=True)
results_dir.mkdir(parents=True, exist_ok=True)

parameters = {
    "target_temperature_C": TARGET_TEMPERATURE_C,
    "temp_switch_on_V": TEMP_SWITCH_ON_V,
    "temp_switch_off_V": TEMP_SWITCH_OFF_V,
    "on_duration_s": ON_DURATION_S,
    "off_duration_s": OFF_DURATION_S,
    "total_duration_s": TOTAL_DURATION_S,
    "sample_interval_s": SAMPLE_INTERVAL_S,
    "apply_pid_params": APPLY_PID_PARAMS,
    "pid_kp": PID_KP,
    "pid_ki": PID_KI,
    "pid_kd": PID_KD,
    "wait_initial_stable": WAIT_INITIAL_STABLE,
    "initial_stable_tolerance_C": INITIAL_STABLE_TOLERANCE_C,
    "initial_stable_reads": INITIAL_STABLE_READS,
}

experiment_config = {
    "experiment_type": EXPERIMENT_TYPE,
    "run_tag": RUN_TAG,
    "timestamp": timestamp,
    "parameters": parameters,
    "pid_snapshot_before": pid_snapshot_before,
    "mapping_snapshot": MAPPING,
    "safety_limits_snapshot": LIMITS,
    "data_files": [],
}
write_experiment_config(run_dir, experiment_config)
print(f"运行目录: {run_dir}")

# %% Cell 7
records = []
cycle_period_s = ON_DURATION_S + OFF_DURATION_S
last_state = None
last_print_s = -PRINT_INTERVAL_S
completed = False

if LIVE_PLOT:
    plt.ion()
    fig, (ax_temp, ax_err) = new_figure(nrows=2, ncols=1, kind="wide", height_mm=110, sharex=True)
    ax_temp_step = ax_temp.twinx()
else:
    fig = None
    ax_temp_step = None

print("=" * 60)
print("开始 Temp_Switch 周期测试")
print(f"周期: ON {ON_DURATION_S:.3f}s / OFF {OFF_DURATION_S:.3f}s, 总时长 {TOTAL_DURATION_S:.1f}s")
print("按 Ctrl+C 可提前结束；脚本会保存已采集数据并恢复 Temp_Switch=ON")
print("=" * 60)

t0 = time.perf_counter()
next_sample_t = t0

try:
    while True:
        now = time.perf_counter()
        elapsed_s = now - t0
        if elapsed_s >= TOTAL_DURATION_S:
            completed = True
            break

        cycle_phase_s = elapsed_s % cycle_period_s
        switch_enabled = cycle_phase_s < ON_DURATION_S
        if switch_enabled != last_state:
            voltage = set_temp_switch(dg_temp, switch_enabled)
            state_label = "ON" if switch_enabled else "OFF"
            print(f"[{elapsed_s:8.3f}s] Temp_Switch -> {state_label} ({voltage:.1f} V)")
            last_state = switch_enabled
        else:
            voltage = TEMP_SWITCH_ON_V if switch_enabled else TEMP_SWITCH_OFF_V

        temp_c = tec.get_temperature(channel=1)
        if not np.isfinite(temp_c):
            raise RuntimeError("TEC 返回无效温度，请检查传感器连接")
        error_c = temp_c - TARGET_TEMPERATURE_C
        cycle_index = int(elapsed_s // cycle_period_s)

        records.append({
            "time_s": float(elapsed_s),
            "temperature_C": float(temp_c),
            "target_temperature_C": float(TARGET_TEMPERATURE_C),
            "error_C": float(error_c),
            "temp_switch_state": float(1 if switch_enabled else 0),
            "temp_switch_voltage_V": float(voltage),
            "cycle_index": float(cycle_index),
            "cycle_phase_s": float(cycle_phase_s),
        })

        if elapsed_s - last_print_s >= PRINT_INTERVAL_S:
            print(
                f"[{elapsed_s:8.1f}s] T={temp_c:.3f} C, "
                f"error={error_c:+.3f} C, switch={'ON' if switch_enabled else 'OFF'}"
            )
            last_print_s = elapsed_s

        if LIVE_PLOT and len(records) % LIVE_PLOT_UPDATE_EVERY == 0:
            t_arr = np.array([row["time_s"] for row in records], dtype=float)
            temp_arr = np.array([row["temperature_C"] for row in records], dtype=float)
            err_arr = np.array([row["error_C"] for row in records], dtype=float)
            state_arr = np.array([row["temp_switch_state"] for row in records], dtype=float)
            ax_temp.clear()
            ax_err.clear()
            ax_temp_step.clear()
            ax_temp.plot(t_arr, temp_arr, lw=1.1, label="Temperature")
            ax_temp.axhline(TARGET_TEMPERATURE_C, color="gray", ls="--", lw=1.0, label="Setpoint")
            ax_temp_step.step(t_arr, state_arr, where="post", color="tab:orange", alpha=0.35, label="Switch")
            ax_temp_step.set_ylim(-0.1, 1.1)
            ax_temp_step.set_yticks([0, 1])
            ax_temp_step.set_yticklabels(["OFF", "ON"])
            ax_temp.set_ylabel("Temperature (C)")
            ax_temp.set_title("Temperature response")
            ax_temp.grid(False)
            ax_temp.legend(loc="upper left")

            ax_err.plot(t_arr, err_arr, lw=1.1, color="tab:red", label="Error")
            ax_err.axhline(0.0, color="gray", ls="--", lw=1.0)
            ax_err.set_xlabel("Time (s)")
            ax_err.set_ylabel("Error (C)")
            ax_err.set_title("Temperature error")
            ax_err.grid(False)
            ax_err.legend(loc="upper left")
            plt.pause(0.001)

        next_sample_t += SAMPLE_INTERVAL_S
        sleep_s = max(0.0, next_sample_t - time.perf_counter())
        time.sleep(sleep_s)

except KeyboardInterrupt:
    print("收到 Ctrl+C，提前结束并保存已采集数据")

finally:
    try:
        set_temp_switch(dg_temp, True)
        print("Temp_Switch 已恢复为 ON")
    except Exception as exc:
        print(f"Temp_Switch 恢复失败: {exc}")

    data_files = save_records(raw_dir, records)
    pid_snapshot_after = read_pid_snapshot(tec)
    experiment_config.update({
        "completed": completed,
        "n_samples": len(records),
        "pid_snapshot_after": pid_snapshot_after,
        "data_files": data_files,
    })
    write_experiment_config(run_dir, experiment_config)
    print(f"数据已保存: {run_dir}")

if records:
    err_arr = np.array([row["error_C"] for row in records], dtype=float)
    print(
        f"误差统计: mean={np.mean(err_arr):+.4f} C, "
        f"std={np.std(err_arr):.4f} C, "
        f"max_abs={np.max(np.abs(err_arr)):.4f} C, "
        f"peak_to_peak={np.ptp(err_arr):.4f} C"
    )

# %% Cell 8
print("正在断开 TEC...")
tec = devices.get("tec")
if tec and hasattr(tec, "disconnect"):
    try:
        tec.disconnect()
        print("  TEC 已断开")
    except Exception as exc:
        print(f"  TEC 断开失败: {exc}")
print("其他设备保持连接")
