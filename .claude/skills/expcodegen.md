---
name: expcodegen
description: 根据自然语言描述的实验方案，自动生成实验 Jupyter Notebook 代码。支持多设备控制、参数扫描、安全限值保护。
trigger: 用户希望生成实验代码、编写实验脚本、创建 notebook
---

# ExpCodeGen Skill — 实验代码生成器

## 概述

将自然语言描述的实验方案，自动转化为可执行的 Jupyter Notebook（存放在 `experiments/` 目录下）。

### 配置文件总览

| 文件 | 用途 | 变更频率 |
|------|------|----------|
| `params/mapping.yaml` | 物理量↔仪器通道映射 | 低（换设备/换接口时改） |
| `params/safety_limits.yaml` | 各物理量安全限值 | 低（设备更换时调整） |
| `params/experiment_types/<type>.yaml` | 某类实验的默认参数模板 | 中（新增实验类型时创建） |

三个配置文件彼此独立，方便单独修改不影响其他配置。

### 数据目录结构

每次实验运行产生一个独立目录，自包含所有数据：

```
data/
  <experiment_type>/                  # 实验类型（如 magneto_optical_kerr）
    <YYYYMMDD>_<HHMMSS>_<purpose>/    # 单次运行
      params.yaml           # 运行时参数快照（含 mapping、限值、扫描参数）
      metadata.yaml         # 实验元数据（类型、目的、操作人）
      raw/                  # 原始数据
        scan_data.npz       # 扫描数据
        scope_waveform.npz  # 示波器波形（如适用）
      results/              # 分析结果
        plots/*.png         # 图表
        analysis.npz        # 分析结果（可选）
```

---

## 工作流程

### Step 1：理解实验描述

让用户用自然语言描述实验。引导用户说清楚以下要素：

- **实验类型**是什么？（如磁光 Kerr 效应、透射谱测量），这将作为 `data/` 下的目录名
- **实验目的**（可选，用于 notebook 标题/说明）
- **涉及哪些物理量**（如磁场、光功率、温度、锁相信号、示波器波形）
- **哪些物理量是扫描变量**，范围多少？（如"磁场从 0 到 0.18 A，20 点"）
- **哪些物理量是固定值**，初值多少？（如"激光功率 1 Vpp"）
- **每个扫描点记录什么数据**？（如"锁相 R 值、示波器波形"）
- **实验时序**（每点等待稳定时间等）

> 如果用户描述不够清晰，通过提问引导补全。

### Step 2：读取物理量↔仪器映射

检查 `params/mapping.yaml`，看用户描述的物理量是否都已定义。

- **已定义** → 从中提取各物理量对应的仪器类型、连接地址、通道等信息
- **缺少某个物理量** → 询问用户该物理量的仪器信息，然后更新 `params/mapping.yaml`

```python
# 映射文件字段说明
magnetic_field:              # 物理量名 (snake_case)，代码中作为变量标识
  instrument: gs200         # 仪器类型: gs200 | signal_generator | tec_controller | lockin_amplifier | sds_acquisition
  resource: "USB0::..."     # VISA 资源 / COM 口；HF2 无需此项
  channel: 1                # 通道号（无通道概念的仪器填 null）
  source_function: "CURRent" # [仅 GS200] CURRent 或 VOLTage
  device_id: "dev18246"     # [仅 HF2] LabOne 设备 ID
  host: "127.0.0.1"         # [仅 HF2] LabOne 服务器地址
  port: 8005                # [仅 HF2] LabOne 服务器端口
  demod_idx: 0              # [仅 HF2] 解调器索引
  description: "..."        # 文字说明
```

### Step 3：读取安全限值

从 `params/safety_limits.yaml` 加载限值。

- 如果实验中涉及的物理量在限值文件中**都已定义** → 直接使用
- 如果**缺少某个物理量** → 询问用户安全范围，更新 `params/safety_limits.yaml`

```yaml
# 限值文件字段说明
magnetic_field:
  min: 0.0                  # 最小值
  max: 0.2                  # 最大值
  ramp_rate: 0.01           # 爬升速率（物理量单位/秒），null 表示不限制
  output_off_on_error: true  # 出错时自动关闭输出
```

对于 GS200，除限值检查外，还需在代码中额外调用 `set_current_limit()` / `set_voltage_limit()` 做硬件级保护。

### Step 4：分析实验流程

根据 Step 1-3 的信息，生成实验流程：

1. **扫描参数**：哪个物理量是扫描变量？起止范围？点数/步长？
2. **固定参数**：哪些物理量设固定初值？
3. **记录数据**：每个扫描点读什么？（锁相解调值、示波器波形……）
4. **实验时序**：扫描前延迟、设置后等待稳定时间、点间间隔
5. **结果目录**：`data/<实验类型>/<时间戳>_<目的>/`

如果用户没有明确，根据常见实验类型给出合理默认值。也可检查 `params/experiment_types/` 下是否存在对应模板文件。

### Step 5：生成 Jupyter Notebook

在 `experiments/` 目录下创建 notebook，文件名 `experiments/<实验类型>_<YYYYMMDD>.ipynb`。

生成代码时遵循以下模板，根据实际涉及设备动态填充各单元内容。

---

## Notebook 万能模板 ↓

以下模板是设备无关的通用结构。每个单元根据实验中实际涉及的设备动态生成具体代码。

### Cell 0：标题

```markdown
# {实验标题}

{实验目的/描述}

## 涉及设备
- {物理量} → {仪器类型} @ {resource}

## 安全限值
- {物理量}: [{min}, {max}] {unit}

## 实验参数
- 扫描: {变量} {start} → {stop}
- 固定: {变量} = {value}
```

### Cell 1：路径与导入

```python
from pathlib import Path
import sys
project_root = Path.cwd().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import numpy as np
import yaml
import time
import json
from datetime import datetime
import matplotlib.pyplot as plt

# ---- 按需导入设备库 ----
# [GS200 相关]
# from gs200 import GS200Instrument
#
# [DG4000 相关]
# from signal_generator import DG4000Instrument
#
# [TEC103 相关]
# from tec_controller import TECInstrument
#
# [HF2 相关]
# from lockin_amplifier import (HF2Instrument, demod, DAQConfig, ...)
#
# [SDS 相关]
# from sds_acquisition import (SDSInstrument, SDSAcquisition, AcquisitionConfig, ...)
```

### Cell 2：加载配置

```python
# 加载物理量→仪器映射
with open(project_root / "params" / "mapping.yaml") as f:
    MAPPING = yaml.safe_load(f)["mapping"]

# 加载安全限值
with open(project_root / "params" / "safety_limits.yaml") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]

# ========== 实验参数（用户填写） ==========
EXPERIMENT_TYPE = "{实验类型}"
PURPOSE = "{实验目的}"

# 扫描参数
SCAN_VARIABLE = "{扫描物理量名}"     # 对应 mapping.yaml 中的 key
SCAN_START = {起始值}
SCAN_STOP = {终止值}
SCAN_NUM = {点数}
SETTLE_TIME = {等待时间}            # 每点等待稳定 (s)

# 固定参数（注释掉不需要的）
# FIXED_PARAMS = {{"{物理量名}": {值}, ...}}

# HF2 需要单独显示的配置参数（如有）
# HF2_DEMOD_CFG = {{
#     "demod_idx": 0, "rate": 20e3, "time_constant": 0.001, ...
# }}
# HF2_DAQ_CFG = {{"duration": 0.1, "signal_paths": [..., ...]}}
# ==========================================

# 安全边界检查函数
def clamp_value(name, value):
    """将数值限制在安全范围内，超出则报错"""
    lim = LIMITS.get(name)
    if lim is None:
        return value
    lo, hi = lim["min"], lim["max"]
    if value < lo or value > hi:
        raise ValueError(
            f"[安全拦截] {name}={value} 超出范围 [{lo}, {hi}]"
        )
    return value
```

### Cell 3：连接设备

```python
devices = {{}}

try:
    # ---- [按需] 连接 GS200 ----
    # if "gs200" in [MAPPING[k]["instrument"] for k in ...]:
    #     gs_cfg = MAPPING["{物理量名}"]
    #     gs = GS200Instrument(gs_cfg["resource"])
    #     gs.connect()
    #     print(f"GS200 已连接: {gs.idn()}")
    #     # 设置硬件保护
    #     lim = LIMITS["{物理量名}"]
    #     if gs_cfg["source_function"] == "CURRent":
    #         gs.set_current_limit(lim["max"])
    #     else:
    #         gs.set_voltage_limit(lim["max"])
    #     if lim.get("ramp_rate"):
    #         print(f"  注意: 爬升速率限制 {lim['ramp_rate']} {gs_cfg.get('source_function','')}/s")
    #     devices["{物理量名}"] = gs

    # ---- [按需] 连接 DG4000 ----
    # dg = DG4000Instrument(cfg["resource"], channel=cfg["channel"])
    # dg.connect()
    # devices["{物理量名}"] = dg

    # ---- [按需] 连接 TEC103 ----
    # tec = TECInstrument(port=cfg["resource"])
    # tec.connect()
    # devices["{物理量名}"] = tec

    # ---- [按需] 连接 HF2 ----
    # hf2_cfg = MAPPING["{物理量名}"]
    # hfi = HF2Instrument(
    #     host=hf2_cfg.get("host", "127.0.0.1"),
    #     port=hf2_cfg.get("port", 8005),
    #     api_level=1,
    #     device_id=hf2_cfg["device_id"],
    # )
    # hfi.connect()
    # # 配置解调器（按需）
    # from lockin_amplifier import SignalInputConfig, OscillatorConfig, DemodulatorConfig
    # demod.configure_signal_input(hfi, SignalInputConfig(...))
    # demod.configure_oscillator(hfi, OscillatorConfig(...))
    # demod.configure_demodulator(hfi, DemodulatorConfig(...))
    # devices["lockin"] = hfi

    # ---- [按需] 连接 SDS ----
    # scope_cfg = MAPPING["{物理量名}"]
    # from sds_acquisition import SDSAcquisition, SDSInstrument, AcquisitionConfig
    # sds_instr = SDSInstrument(scope_cfg["resource"])
    # sds_instr.connect()
    # acquirer = SDSAcquisition(sds_instr)
    # devices["scope"] = (sds_instr, acquirer)

except Exception as e:
    print(f"设备连接失败: {{e}}")
    raise
```

### Cell 4：设置初始值并创建运行目录

```python
# 创建本次运行目录
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{{timestamp}}_{PURPOSE}"
run_dir.mkdir(parents=True, exist_ok=True)
(raw_dir := run_dir / "raw").mkdir(exist_ok=True)
(results_dir := run_dir / "results").mkdir(exist_ok=True)
print(f"运行目录: {{run_dir}}")

# 设置固定参数（按需）
# dg.set_amplitude(FIXED_PARAMS["laser_power"], channel=1)
# tec.set_target_temperature(FIXED_PARAMS["temperature"], channel=1)
# tec.set_enable(True, channel=1)

# 设置扫描起始值
# scan_cfg = MAPPING[SCAN_VARIABLE]
# ...
# 安全边界检查
# clamp_value(SCAN_VARIABLE, SCAN_START)

print("初始值设置完成")
```

### Cell 5：保存初始参数快照

```python
# 将所有配置快照到 params.yaml（自包含，独立于全局配置）
initial_snapshot = {{
    "experiment_type": EXPERIMENT_TYPE,
    "purpose": PURPOSE,
    "timestamp": timestamp,
    "scan_variable": SCAN_VARIABLE,
    "scan_start": SCAN_START,
    "scan_stop": SCAN_STOP,
    "scan_num": SCAN_NUM,
    "settle_time": SETTLE_TIME,
    "fixed_params": {{}},  # 如有固定参数，记录在此
    "mapping_snapshot": MAPPING,
    "safety_limits_snapshot": LIMITS,
    # 如有 HF2/示波器等其他配置也一并记录
}}

with open(run_dir / "params.yaml", "w", encoding="utf-8") as f:
    yaml.dump(initial_snapshot, f, default_flow_style=False)

# 保存元数据
with open(run_dir / "metadata.yaml", "w", encoding="utf-8") as f:
    yaml.dump({{
        "experiment_type": EXPERIMENT_TYPE,
        "purpose": PURPOSE,
        "timestamp": timestamp,
    }}, f)

print(f"参数已保存至: {{run_dir / 'params.yaml'}}")
```

### Cell 6：实验主循环

```python
# 扫描数组
scan_values = np.linspace(SCAN_START, SCAN_STOP, SCAN_NUM)
num_points = len(scan_values)

# 数据容器
# recorded_data = {{"scan": scan_values, "物理量1": [], "物理量2": []}}
recorded_data = {{"scan": scan_values}}

time.sleep(PRE_SCAN_DELAY)  # 扫描前等待（如有）

for i, val in enumerate(scan_values):
    print(f"[{{i+1}}/{{num_points}}] {{SCAN_VARIABLE}} = {{val:.6f}}", end="")

    # ---- 1. 安全边界检查 ----
    val = clamp_value(SCAN_VARIABLE, val)

    # ---- 2. 设置扫描值（按实际设备生成） ----
    # 【GS200 电流模式】gs.set_current(val)
    # 【GS200 电压模式】gs.set_voltage(val)
    # 【DG4000 幅度】dg.set_amplitude(val, channel=...)
    # 【DG4000 频率】dg.set_frequency(val, channel=...)
    # 【TEC103 温度】tec.set_target_temperature(val, channel=...)

    # ---- 3. 等待稳定 ----
    time.sleep(SETTLE_TIME)

    # ---- 4. 采集数据（按实际设备生成） ----
    #
    # 【锁相单点读数】sample = demod.read_demod_sample(hfi, demod_idx=0)
    #   → recorded_data.setdefault("r", []).append(sample["r"])
    #   → recorded_data.setdefault("x", []).append(sample["x"])
    #
    # 【锁相 DAQ 采集】daq_results = daq.acquire_data(hfi, daq_cfg, demod_idx=0)
    #   → 每个 daq_result.values 记录平均或原始值
    #
    # 【示波器波形】scope_results = acquirer.acquire_all(scope_cfg)
    #   → 每次扫描点保存一个波形文件
    #   → np.savez(raw_dir / f"scope_{{i:03d}}.npz", ...)
    #
    # 【温度读数】temp_now = tec.get_temperature(channel=1)
    #   → recorded_data.setdefault("temperature", []).append(temp_now)
    #

    print(f"  ✓")

# 将列表转为 numpy 数组
for key in recorded_data:
    if key != "scan":
        recorded_data[key] = np.array(recorded_data[key])

print("扫描完成!")
```

### Cell 7：保存扫描数据

```python
# 保存主要扫描数据
np.savez(raw_dir / "scan_data.npz", **recorded_data)

# 保存 CSV 便于人类阅读
try:
    import pandas as pd
    df = pd.DataFrame({{k: v for k, v in recorded_data.items()}})
    df.to_csv(raw_dir / "scan_log.csv", index=False)
except ImportError:
    # 无 pandas 时用纯文本保存
    header = ",".join(recorded_data.keys())
    rows = np.column_stack(list(recorded_data.values()))
    np.savetxt(raw_dir / "scan_log.csv", rows,
               delimiter=",", header=header, comments="")

print(f"数据已保存至: {{raw_dir}}")
```

### Cell 8：绘制结果（可选）

```python
fig, ax = plt.subplots(figsize=(8, 5))

# 绘制扫描曲线
# ax.plot(recorded_data["scan"], recorded_data["r"], "o-", label="R")

ax.set_xlabel("{扫描变量} ({单位})")
ax.set_ylabel("{测量量} ({单位})")
ax.set_title("{实验标题}")
ax.legend()
ax.grid(True, alpha=0.3)
plt.tight_layout()

plot_path = results_dir / "scan_result.png"
fig.savefig(plot_path, dpi=150, bbox_inches="tight")
plt.show()
print(f"图表已保存: {{plot_path}}")
```

### Cell 9：安全断开

```python
# 安全关闭所有输出设备（按实际设备生成）
# for name, dev in devices.items():
#     if isinstance(dev, GS200Instrument) and dev.connected:
#         dev.set_output(False)
#     if isinstance(dev, TECInstrument) and dev.connected:
#         dev.set_enable(False)

# 断开所有连接
# for name, dev in devices.items():
#     if hasattr(dev, "disconnect"):
#         try:
#             dev.disconnect()
#             print(f"{{name}} 已断开")
#         except Exception as e:
#             print(f"{{name}} 断开失败: {{e}}")

print("设备已安全断开")
```

---

## 代码生成规则

### 引用 `src/` 中的库

所有本地包已通过 `pip install -e .` 安装到 `agent_exp_env`，可直接 import：

```python
from gs200 import GS200Instrument
from signal_generator import DG4000Instrument
from tec_controller import TECInstrument
from lockin_amplifier import HF2Instrument, demod, daq, ...
from sds_acquisition import SDSInstrument, SDSAcquisition, AcquisitionConfig, ...
```

实验中用到了哪些设备就导入对应的库，不要全部导入。

### 设备 API 速查

**GS200（直流电压/电流源）：**
- `set_source_function("CURRent"|"VOLTage")`
- `set_current(A)` / `get_current()` / `set_voltage(V)` / `get_voltage()`
- `set_output(bool)` / `get_output()`
- `set_current_limit(A)` / `set_voltage_limit(V)` ← **安全防护，必须调用**

**DG4000（信号发生器）：**
- `setup_sine(freq, ampl, offset)` / `setup_dc(offset)` 等快捷方法（自动打开输出）
- `set_frequency(Hz)` / `set_amplitude(Vpp)` / `set_offset(V)` / `set_high_level(V)`
- `set_output(bool, channel=1)` / `all_off()`

**TEC103（温控器）：**
- `set_target_temperature(°C, channel=1)` / `get_temperature(channel=1)`
- `set_enable(bool, channel=1)` / `get_enable(channel=1)`
- `set_output_mode(mode, channel=1)` — 0=双向, 1=制冷, 2=加热, 3=手动
- `set_pid(kp, ki, kd, channel=1)` / `set_slope(°C/s, channel=1)`
- `get_all_temperatures() → dict` — 返回 TC1, TC2, 内部温度

**HF2（锁相放大器）：**
- `demod.read_demod_sample(instr, demod_idx=0) → dict` — 返回 x, y, r, theta, freq, phase
- `daq.acquire_data(instr, config, demod_idx=0) → List[DAQResult]` — DAQ 采集
- `demod.configure_signal_input(instr, config)` / `configure_oscillator(instr, config)`
- `demod.configure_demodulator(instr, config) → float` — 返回实际采样率
- `demod.configure_signal_output(instr, config)` / `auto_calibrate_phase(instr, ...)`

**SDS（示波器）：**
- `SDSInstrument(resource)` → `connect()` → 底层通信
- `acquire = SDSAcquisition(instr)` → 采集控制器
- `acquire.acquire_all(AcquisitionConfig) → List[AcquisitionResult]`
- 每个 `AcquisitionResult` 有 `.voltage`, `.time`, `.channel` 属性
- 保存函数：`save_to_npz(path, results)`, `save_to_csv(path, results)`, `save_to_mat(path, results)`

### 安全守则（代码生成强制规则）

1. **所有输出类设备在设置前做 `clamp_value()` 边界检查**
2. **GS200 必须调用 `set_current_limit()` / `set_voltage_limit()` 做硬件保护**
3. **GS200/DG4000 扫描结束必须关闭输出（`set_output(False)`）**
4. **TEC103 结束必须关闭输出（`set_enable(False)`）**
5. **HF2 结束断开 LabOne 连接**
6. **SDS 结束断开 VISA 连接**
7. **使用 `try/finally` 确保异常时也能断开所有设备**
8. **检测到 NaN/inf 等异常值时停止扫描并关闭所有输出**
9. **扫描结束后即使正常完成，也要关闭所有输出设备**

### Notebook 格式规则

- 所有图表标注使用**英文**，代码注释使用**中文**
- Markdown 单元使用**中文**
- 时间戳格式：`YYYYMMDD_HHMMSS`
- 变量名使用 `snake_case`

---

## 与用户的交互模板

### 初次交互

我将帮你生成实验代码。请逐步告诉我以下信息：

1. **实验类型**是什么？（如 `magneto_optical_kerr`，将作为 `data/` 下的目录名）
2. **实验目的**？（一句话描述）
3. **涉及哪些物理量**？哪些是**扫描变量**（范围？点数？）？哪些是**固定值**？
4. **每个扫描点记录什么数据**？
5. 对应的 `params/mapping.yaml` 中是否已定义了所需物理量？（我会检查）
6. `params/safety_limits.yaml` 中限值是否齐全？

### 映射缺失时

物理量 `{name}` 在 `params/mapping.yaml` 中未定义。请提供：

- 使用哪台仪器控制？（GS200 / DG4000 / TEC103 / HF2 / SDS）
- 仪器的连接地址？（VISA 资源串 / COM 口 / LabOne 参数）
- 通道号？（如适用）
- GS200 的话，电流模式还是电压模式？

我会更新 `params/mapping.yaml`，后续实验可复用。

### 安全限值缺失时

物理量 `{name}` 在 `params/safety_limits.yaml` 中未定义。请提供：

- 最小值：____
- 最大值：____（超过会损坏设备？）
- 爬升速率限制（如需要）：____

我会更新 `params/safety_limits.yaml`。

### 实验类型模板复用

`params/experiment_types/` 下已有模板 `{type}.yaml`，是否参考其中的默认参数？或者自定义本次运行参数？
