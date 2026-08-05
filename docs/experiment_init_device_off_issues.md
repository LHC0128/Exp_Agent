# 实验初始化时未关闭无关设备的核验与修复记录

> 记录日期：2026-07-29
>
> 核验状态：问题成立，已于 2026-07-29 修复

## 核验结论

四项问题均成立。残余输出不属于当前实验的有效工作点，且仓库中同类 Mx
工作流已经采用“关闭 Burst/调制、写入 0 V DC、关闭输出”的确定性初始化方式。
本次修复沿用该约定，并补充以下细节：

- 所有写入均先通过对应的全局安全限值校验。
- 新连接的信号源纳入统一参考时钟设置与回读。
- `projection_noise` 明确把 Pump 载波和门控保持为关闭状态，因此安全关闭报告不再把
  `Pump_modulation` 标记为保留输出。
- 三个 Mx Y RF 实验复用同一连接、初始化和安全关闭函数，一处修改同时覆盖三者。

## 问题概述

部分新模式实验在 `_configure_outputs()` 初始化阶段只配置了本实验需要的设备和通道，但**未将其余无关设备的输出显式置为 OFF**。如果上一个实验异常中断或安全关闭未完全执行，这些无关设备可能残留活跃输出，对当前实验产生物理干扰。

## 修复前问题清单

以下代码位置、设备状态和风险描述记录的是修复前实现。

### 1. `projection_noise` — 原子自旋投影噪声

- **workflow 文件：** [lab_workflows/experiment_modules/projection_noise/workflow.py](../lab_workflows/experiment_modules/projection_noise/workflow.py)
- **connect 函数：** `_connect_devices()` (L94-L141)
- **init 函数：** `_configure_outputs()` (L162-L211)
- **风险等级：** ⚠️ 高

| 未关闭的无关设备 | mapping key | 风险说明 |
|---|---|---|
| XY 磁场信号源 (DG4000) | `X_magnetic_field`, `Y_magnetic_field` / `rf_coil` | 上轮 Mx 实验可能残留 DC 或 RF 输出，产生横向磁场干扰 |
| Z 辅助磁场 (DG4000) | `Z_magnetic_field` | Z 方向残余 DC/RF 场 |
| Pump RF 调制源 (DG4000) | `Pump_modulation`, `Time_sequence` | 100MHz 载波或脉冲门控仍可能输出，产生不需要的 AOM 光调制 |

**根本原因：** `_connect_devices()` 仅连接 GS200、laser（Pump/Probe 光功率）、temp_switch、scope、TEC，完全没有连接 DG4000 系列磁场设备。该实验是纯光学 PD 波形采集（SDS 示波器），本不需要 RF 调制或横向磁场，但对它们的残余状态无感知、无控制。

**`_configure_outputs()` 实际执行的操作：**
- Pump 光功率 DC → output ON（如果 > 0V）
- Probe 光功率 DC → output ON
- 温控开关 → ON
- TEC 温控 → 设定温度并等待稳定
- GS200 → `set_output(False)`，设定电流限值

---

### 2. `mx_y_rf_sensitivity` — Mx Y 向 RF 场灵敏度

- **workflow 文件：** [lab_workflows/experiment_modules/mx_y_rf_sensitivity/workflow.py](../lab_workflows/experiment_modules/mx_y_rf_sensitivity/workflow.py)
- **connect 函数：** `_connect_devices()` (L225-L295)
- **init 函数：** `_configure_outputs()` (L390-L475)
- **风险等级：** ⚠️ 中

| 未关闭的无关设备 | mapping key | 风险说明 |
|---|---|---|
| Z 磁场信号源 (DG4000) | `Z_magnetic_field` | Z 方向 DC 或 RF 输出可能残留，干扰 Mx 共振条件 |

**根本原因：** `_connect_devices()` 连接了 GS200、xy_field（X + Y RF）、laser、pump_rf、temp_switch、HF2、TEC，但缺少 Z 磁场设备。`_configure_outputs()` 将 X 场设为 0V OFF、Y RF 配置正弦波（初始 OFF），但 Z 场完全未被触及。

**`_configure_outputs()` 已正确关闭的设备：**
- `X_magnetic_field` → 0V DC, output OFF ✓
- `Y RF` → 正弦波已配置，output 初始 OFF ✓

**遗漏的设备：**
- `Z_magnetic_field` → 未连接、未配置 ✗

---

### 3. `mx_y_rf_power_optimization` — Mx Y RF 光功率灵敏度优化

- **workflow 文件：** [lab_workflows/experiment_modules/mx_y_rf_power_optimization/workflow.py](../lab_workflows/experiment_modules/mx_y_rf_power_optimization/workflow.py)
- **风险等级：** ⚠️ 中

| 未关闭的无关设备 | mapping key |
|---|---|
| Z 磁场信号源 (DG4000) | `Z_magnetic_field` |

**根本原因：** 完全复用 `mx_y_rf_sensitivity` 的 `connect_mx_y_rf_devices()` 和 `configure_mx_y_rf_outputs()`，问题完全一致。

---

### 4. `mx_y_rf_probe_detuning_optimization` — Mx Y RF Probe 光功率与 PZT 失谐优化

- **workflow 文件：** [lab_workflows/experiment_modules/mx_y_rf_probe_detuning_optimization/workflow.py](../lab_workflows/experiment_modules/mx_y_rf_probe_detuning_optimization/workflow.py)
- **风险等级：** ⚠️ 中

| 未关闭的无关设备 | mapping key |
|---|---|
| Z 磁场信号源 (DG4000) | `Z_magnetic_field` |

**根本原因：** 同样复用 `mx_y_rf_sensitivity` 的设备连接和配置逻辑，额外连接了 DLC pro 激光控制器。

---

## 正确参考：已妥善处理所有设备的实验

以下实验在 init 阶段显式连接并关闭了全部无关设备：

| 参考实验 | 关键代码位置 | 做法 |
|---|---|---|
| `mx_main_field_noise_spectrum` | `_configure_outputs()` L188-L205 | 连接 Z 场 + XY 场，显式 `setup_dc(0.0)` + `set_output(False)` |
| `mx_z_noise_spectrum` | `_configure_outputs()` L154-L171 | 同上，Z + XY 场均 0V OFF |
| `mx_main_field_calibration` | `_configure_outputs()` L301-L324 | Z 0V OFF、X 0V OFF、Y RF 配置后 OFF |
| `mx_main_field_scope_noise_spectrum` | `_configure_outputs()` L380-L397 | Z 0V OFF、XY DC 按参数配置 |
| `mx_xy_residual_field_calibration` | `_configure_outputs()` L140-L147 | Z 0V OFF、XY 通过 `_set_xy_off_state()` 双通道关闭 |
| `mx_z_field_calibration` | `_configure_outputs()` L286-L309 | Z 0V OFF、X 0V OFF、Y RF 配置后 OFF |
| `t2_calibration` | `run()` L584-L590 | 循环 `x_field, y_field, z_field` 全部 `set_output(False)` |
| `mx_z_optimal_control_rf_sensitivity` | `_configure_common_outputs()` L330-L334 | GS200 显式 0A + output OFF，X 场 0V OFF |

## 汇总表

| # | 实验模块 | experiment_id | 未关闭的无关设备 |
|---|---------|--------------|-----------------|
| 1 | `projection_noise` | `projection-noise` | XY 磁场、Z 磁场、Pump RF 调制源 |
| 2 | `mx_y_rf_sensitivity` | `mx-y-rf-sensitivity` | Z 磁场 |
| 3 | `mx_y_rf_power_optimization` | `mx-y-rf-power-optimization` | Z 磁场（复用 #2） |
| 4 | `mx_y_rf_probe_detuning_optimization` | `mx-y-rf-probe-detuning-optimization` | Z 磁场（复用 #2） |

## 已实施修复

参考 `mx_main_field_noise_spectrum` 的模式，已完成：

1. `projection_noise` 新增 XY、Z 和 Pump RF 信号源连接；初始化和安全结束均关闭
   Burst/调制、写入 0 V DC 并关闭输出。
2. `mx_y_rf_sensitivity` 共享链路新增 Z 信号源连接、状态快照、时钟同步、初始化归零
   和 `DGChannelShutdown`；两个优化实验自动复用该修复。
3. 同步更新实验定义中的设备清单、安全说明和对应实验文档。
4. 新增回归测试，覆盖初始化状态和正常/异常共用的安全关闭状态。
