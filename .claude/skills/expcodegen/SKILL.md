---
name: expcodegen
description: '根据自然语言描述的实验方案，自动生成实验 Jupyter Notebook 代码。支持多设备控制、参数扫描、安全限值保护。Use when: 用户希望生成实验代码、编写实验脚本、创建 notebook'
user-invocable: true

# ExpCodeGen Skill — 实验代码生成器 (v2)

## 概述

将自然语言描述的实验方案，自动转化为可执行的 Jupyter Notebook（存放在 `experiments/` 目录下）。

### 配置文件总览

| 文件 | 用途 | 变更频率 |
|------|------|----------|
| `params/mapping.yaml` | 物理量↔仪器通道映射 | 低（换设备/换接口时改） |
| `params/safety_limits.yaml` | 各物理量安全限值 | 低（设备更换时调整） |
| `docs/<experiment_type>.md` | 实验类型文档（含 YAML frontmatter） | 中（新增实验类型时创建） |

三个配置文件彼此独立，方便单独修改不影响其他配置。

### 数据目录结构

每次实验运行产生一个独立目录，自包含所有数据：

```
data/
  <experiment_type>/                  # 实验类型（如 magneto_optical_kerr）
    <MMDD>_<HHMM>_<tag>/              # 单次运行（简洁命名）
      params.yaml           # 运行时参数快照（含 mapping、限值、扫描参数）
      metadata.yaml         # 实验元数据（类型、目的、操作人）
      raw/                  # 原始数据
        scan_data.npz       # 扫描数据
        noise_raw/          # 噪声原始数据（逐次保存）
        scope_waveform.npz  # 示波器波形（如适用）
      results/              # 分析结果
        dispersion_curve.png
        full_analysis.png
        analysis.json       # 分析结果（如灵敏度值）
```

---

## 工作流程

### Step 1：理解实验描述

让用户用自然语言描述实验。引导用户说清楚以下要素：

- **实验类型**是什么？（如磁光 Kerr 效应、透射谱测量、静磁场灵敏度），这将作为 `data/` 下的目录名
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

### Step 2b：读取实验类型文档

检查 `docs/` 目录下是否存在对应的 `.md` 文档（文件名匹配实验类型，如 `static_mag_sens.md`）。

如果存在：
- **读取 YAML frontmatter**：获取 `defaults`、`required_devices`、`scan_mode` 等结构化参数
- **读取注意事项**：将 `注意事项` 章节中的修复经验写入生成的代码注释中
- **若无对应文档** → 回退到通用模板

实验类型文档规范（`docs/*.md`）：

```yaml
---
title: 实验名称
type: experiment_type
scan_mode: point_by_point | continuous_ramp
defaults:              # 默认参数，生成代码时填入
  PARAM_NAME: value
required_devices:      # 所需设备清单
  - instrument: gs200 | signal_generator | ...
    role: main_field | sweep | ...
    channels: [1]
learned_notes:         # 修复经验总结
  - 噪声测量后恢复解调器
  - grid_cols 使用 actual_rate
---
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

### Step 5：检测实验类型 → 选择代码模板

> v2 增强：支持 "点扫描" 和 "连续扫场" 两种模式。

在 Step 1 中判断用户的实验属于哪种模式，并选择对应模板。

#### 模式 A：点扫描 (point_by_point)
适合：透射谱、磁光 Kerr 等，逐点设置→稳定→读数

```python
for i in range(SCAN_NUM):
    set_value(scan_values[i])
    time.sleep(SETTLE_TIME)
    read_data()
```

#### 模式 B：连续扫场 (continuous_ramp)
适合：灵敏度测量等，使用 DG4000 RAMP 波形 + HF2 DAQ 触发同步

```python
# 1 次 RAMP 扫场 = 1 个完整周期 → DAQ 触发采集
dg_sweep.setup_ramp(...)
dg_sweep.set_sync_state(True)
daq_results = daq.acquire_data(...)
```

判断依据：实验类型文档中的 `scan_mode` 字段（如 `docs/*.md` 的 YAML frontmatter）。

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
# 自动定位项目根目录（以 params/ 目录为标记）
project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
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

# 扫描参数（点扫描模式）
# SCAN_VARIABLE = "{扫描物理量名}"
# SCAN_START = {起始值}
# SCAN_STOP = {终止值}
# SCAN_NUM = {点数}
# SETTLE_TIME = {等待时间}

# 连续扫场参数（连续扫场模式）
# RAMP_LOW = {值}
# RAMP_HIGH = {值}
# RAMP_FREQ = {值}
# RAMP_SYMMETRY = {值}
# DAQ_DURATION = {值}

# ========== 固定参数 ==========
# FIXED_PARAMS = {"{物理量名}": {值}, ...}

# ========== 运行目录命名 ==========
RUN_TAG = "{short_tag}"

# 安全边界检查函数
def validate_safety_limit(name, value):
    """检查数值是否在安全范围内，超出则报错"""
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
devices = {}  # 空字典

try:
    # ---- [按需] 连接 GS200 ----
    # if "gs200" in [MAPPING[k]["instrument"] for k in ...]:
    #     gs_cfg = MAPPING["{物理量名}"]
    #     gs = GS200Instrument(gs_cfg["resource"])
    #     gs.connect()
    #     print(f"GS200 已连接: {gs.idn()}")
    #     lim = LIMITS["{物理量名}"]
    #     if gs_cfg["source_function"] == "CURRent":
    #         gs.set_current_limit(lim["max"] / 1000.0)
    #     else:
    #         gs.set_voltage_limit(lim["max"])
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
    # devices["lockin"] = hfi

except Exception as e:
    print(f"设备连接失败: {e}")
    raise
```

### Cell 4：设置初始值并创建运行目录

```python
# 创建本次运行目录 (简洁命名: MMDD_HHMM_tag)
timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
run_dir.mkdir(parents=True, exist_ok=True)
(raw_dir := run_dir / "raw").mkdir(exist_ok=True)
(results_dir := run_dir / "results").mkdir(exist_ok=True)
print(f"运行目录: {run_dir}")

# 设置固定参数（按需）
# dg.set_amplitude(FIXED_PARAMS["laser_power"], channel=1)
# tec.set_target_temperature(FIXED_PARAMS["temperature"], channel=1)
# tec.set_enable(True, channel=1)

print("初始值设置完成")
```

### Cell 5：数据采集

根据模式选择：

#### 模式 A：逐点扫描 (point_by_point)

```python
scan_values = np.linspace(SCAN_START, SCAN_STOP, SCAN_NUM)
recorded_data = {"scan": scan_values}

for i, val in enumerate(scan_values):
    val = validate_safety_limit(SCAN_VARIABLE, val)
    # 设置扫描值 → 等待稳定 → 采集数据
    # ...
    recorded_data.setdefault("r", []).append(sample["r"])

np.savez(raw_dir / "scan_data.npz", **recorded_data)
```

#### 模式 B：连续扫场 (continuous_ramp)

```python
# 启动 RAMP 扫场
dg_sweep.setup_ramp(freq=RAMP_FREQ, amplitude=(RAMP_HIGH - RAMP_LOW),
                    offset=0.0, symmetry=RAMP_SYMMETRY, channel=1)
dg_sweep.set_sync_state(True, channel=1)

# DAQ 触发采集（注意: grid_cols 使用 actual_rate）
daq_cfg = DAQConfig(
    device=MAPPING["lockin_r"]["device_id"],
    trigger_type=1, duration=DAQ_DURATION,
    grid_cols=int(actual_rate * DAQ_DURATION),  # 用 actual_rate 而非常量！
    signal_paths=["sample.r", "sample.x", "sample.y"],
)
daq_results = daq.acquire_data(hfi, config=daq_cfg, demod_idx=0,
                               actual_rate=actual_rate, timeout=DAQ_DURATION + 10.0)

# 停止扫场
dg_sweep.set_output(False, channel=1)
dg_sweep.set_sync_state(False, channel=1)
```

### Cell 6：安全断开

```python
# 关闭所有设备输出
for name, dev in devices.items():
    try:
        if name == "dg_temp":  # 保持温度开关开启
            if hasattr(dev, "set_output"):
                dev.set_output(False, channel=1)
            continue
        if hasattr(dev, "all_off"):
            dev.all_off()                     # 关双通道
        elif hasattr(dev, "set_output"):
            dev.set_output(False)
    except Exception as e:
        print(f"{name} 关闭失败: {e}")

# 显式关闭 SYNC
for sync_name in ["dg_sweep", "dg_mod"]:
    dev = devices.get(sync_name)
    if dev and hasattr(dev, "set_sync_state"):
        dev.set_sync_state(False, channel=1)
        dev.set_sync_state(False, channel=2)

# 断开连接
for name, dev in devices.items():
    if hasattr(dev, "disconnect"):
        dev.disconnect()
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

### 修复经验（自动注入注释）

当实验类型文档的 `learned_notes` 字段有内容时，在对应位置自动生成带有 `# [经验]` 标记的注释：

```python
# [经验] grid_cols 必须使用 actual_rate 而非硬编码常量
grid_cols=int(actual_rate * DAQ_DURATION)
```
