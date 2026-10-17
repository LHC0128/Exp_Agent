---
title: Mx 主磁场频率标定
type: Mx_Main_Field_Calibration
scan_mode: nested_scan
mapping_keys:
  main_magnetic_field:
    role: scan
  Z_magnetic_field:
    role: fixed_off
  rf_coil:
    role: detection_drive
  X_magnetic_field:
    role: fixed_off
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  Pump_modulation:
    role: fixed
  Time_sequence:
    role: fixed
  Temp_Switch:
    role: point_gate
  lockin_r:
    role: detection
  temperature:
    role: fixed
required_devices:
  - GS200
  - DG900
  - DG4000
  - 锁相放大器
learned_notes:
  - TEC103 为可选控制设备；串口连接失败时告警跳过设温，本实验不执行温度判稳。
  - 扫描范围应不跨越总场零点，使用普通线性模型。
  - 设置主场电流后不单独等待；首个频点的温控关闭统一等待同时覆盖主场稳定。
  - 频率到磁场使用可配置的 GYROMAGNETIC_RATIO_HZ_PER_NT，取值见参数文件。
experiment_id: mx-main-field-calibration
defaults_file: params/experiments/mx-main-field-calibration.yaml
---

# Mx 主磁场频率标定

## 采集时序与系统稳定

实际仪器配置完成后，将 `Temp_Switch` 设为 0 V DC 并保持 Output ON，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控为 5 V DC、Output ON 后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。本实验每个 Y RF 频点独立门控，每次重采重新稳定；首频点的稳定等待同时覆盖主场电流变更，不增加校相或噪声测量。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


## 锁相采集方式与实际速率

扫频使用 `RESPONSE_RATE_SA_S` 请求率、`FREQUENCY_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/mx-main-field-calibration.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 目标和构型

本实验在 Mx 构型下标定 GS200 `main_magnetic_field` 电流与 Y RF 共振中心频率、主磁场大小之间的关系：

\[
f(I)=K_f I+f_0,
\qquad
B(I)=\frac{f(I)}{\gamma}=K_B I+B_0.
\]

主场与 Pump 光沿 Z，Probe 光沿 X，`rf_coil` 沿 Y。DG4000 `Z_magnetic_field` 在整个实验中固定为 0 V 且 Output OFF，避免辅助 Z 场混入 GS200 标定。

## 扫描和采集

GS200 电流轴由 `MAIN_FIELD_START_MA`、`MAIN_FIELD_STOP_MA` 与 `MAIN_FIELD_STEP_MA` 生成，单次递增扫描并包含两端；区间必须包含整数个步进。

每个电流点的局部扫频预测中心为：

\[
f_\mathrm{pred}(I)=f_\mathrm{ref}+K_\mathrm{initial}(I-I_\mathrm{ref}).
\]

局部频率窗口与步进分别读取参数文件的 `FREQUENCY_HALF_WIDTH_HZ` 和 `FREQUENCY_STEP_HZ`。`MAIN_FIELD_INITIAL_HZ_PER_MA` 只用于定位局部扫频窗口，不作为最终标定结果。

每个 Y RF 频点依次执行：

1. 设置 Y RF 频率和配置的幅度，并同步 锁相放大器 振荡器。
2. 将温控开关设置为 0 V DC、Output ON。
3. 只等待一次 `SYSTEM_SETTLE_S`，同时覆盖温控关闭、RF 频点稳定以及每个电流点首频点的主场稳定。
4. 按 `FREQUENCY_DURATION_S` 采集 Demod0 R。
5. 在 `finally` 中恢复温控为 5 V DC、Output ON，并按 `TEMP_SWITCH_ON_LAG_S` 等待。
6. 保存本次原始尝试。恢复温控失败时停止后继频点，仍尽力保存已经读入内存的数据；采集错误和收尾错误分别保留。

若单次记录超过配置的标准差门槛，完整保存该尝试并按 `R_POINT_MAX_ATTEMPTS` 重采。采集阶段不执行共振拟合。

## 离线分析

分析器从 `raw/main_field_scan_index.npz` 和各 `raw/current_*/frequency_scan.npz` 重建频率响应。每条曲线拟合带基线 Lorentzian，再对有效中心执行标准不确定度加权线性拟合。单曲线中心需位于扫描窗口内且距边缘不少于一个 HWHM，最终线性 R² 需满足配置的质量阈值。

频率换算磁场时使用 `GYROMAGNETIC_RATIO_HZ_PER_NT`；其值读取参数配置，并作为配置常数处理，不额外传播其系统不确定度。

结果目录为：

```text
data/Mx_Main_Field_Calibration/<run>/
  experiment_config.yaml
  raw/
    main_field_scan_index.npz
    current_000/frequency_scan.npz
    ...
  results/
    analysis.yaml
    analysis.json
    calibration_results.npz
    frequency_response_fits.png
    main_field_frequency_calibration.png
    main_field_frequency_residuals.png
```

采集与离线分析入口分别为 `experiments/Mx_Main_Field_Calibration.py` 和 `experiments/Mx_Main_Field_Calibration_plot.py`。

## 模块与可执行流程

`lab_workflows/experiment_modules/mx_main_field_calibration/` 中，`preparation.py` 负责预检、连接、时钟、固定条件与恢复，`excitation.py` 负责 Pump 激励和 Y RF/解调准备，`acquisition.py` 负责逐电流、逐频点采集与写盘。`workflow.py` 只编排完整运行及分步会话；两种入口调用同一业务。`analysis_steps.py` 保留原 Lorentzian 与加权线性算法，`analysis_plotting.py` 负责论文图，`analysis.py` 编排独立分析。

| 节点 | 操作与前置 |
| --- | --- |
| A01 | 无硬件启动预检；参数、映射端点及锁相能力不通过时不连接 |
| A02 | A01 后连接设备，回读 GS200 的完整运行前状态，保存本轮参数快照 |
| A03 | A02 后统一写入并严格回读参考时钟 |
| A04 | A03 后辅助场归零、设置 Pump/Probe 光功率与 GS200 限流 |
| A05 | A04 后只下发 TEC UI 目标温度，连接失败告警跳过 |
| A06 | A05 后设置 Pump 连续载波与常开门控 |
| A07 | A06 后准备 Y RF 正弦及 R 解调，记录实际采样率 |
| A08 | A07 后执行电流×局部扫频；每次尝试完整保存，标准差门槛用尽则停止后继 |
| A09 | A08 后归零关闭辅助场与 RF，恢复 GS200 初态并封存本轮 |
| A10 | 无前置的安全结束；结束会话并断开 TEC，其他连接按原策略保留 |

A09 恢复 GS200 模式、量程和限流后，A04 及后继标记为需要重新准备。每轮重采创建新目录，保留之前的参数和 `raw/`，不会复用旧目录写入。等待下一步期间仍持有全局硬件锁并保持已写入输出；结束会话、取消、通信 EOF 和后台关闭都会执行安全收尾。恢复错误保存在会话与运行配置，不宣称安全完成。

## GUI 运行与历史

运行页保留「运行 / 历史」两个主 Tab。流程容器顶部提供保存参数与布局、派生预览、预检、完整运行、取消及任务状态；采集与独立分析按两列显示，窄屏自动单列。参数在实际设置或使用它的节点内按基础/高级分类编辑；共享的 Y RF 幅度及预测参考频率在 A07/A08 使用同一份值，光功率和 TEC 目标温度只在初始化节点编辑。

空闲会话修改参数后显示未应用草稿及预计失效步骤，应用只更新参数和失效状态，必须显式选择重跑。还原草稿可清除失效预览；保存默认值同时保存统一分类偏好。刷新页面恢复存活会话，后台重启不从磁盘恢复硬件。可从流程容器下载实验包内的可编辑 [Excalidraw 原图](../lab_workflows/experiment_modules/mx_main_field_calibration/workflow.excalidraw)。

独立分析依次为 B01 配置与原始频扫资格、B02 逐电流 Lorentzian 中心、B03 加权线性标定与磁场换算、B04 报告与结果图。B03 可覆盖 `GYROMAGNETIC_RATIO_HZ_PER_NT` 与 `LINEAR_R_SQUARED_MIN`，不改写采集参数；重算只归档受影响阶段及后继，仍使用的 B02 曲线输入保持原位。标定质量是否通过与分析是否完成分别记录，质量未通过也保存 NaN、排除原因和诊断图。

分析对象默认是下一次实验的自动分析设置，也可选择已结束的具体运行目录。每个目录保存独立分析草稿与阶段状态，切换对象不会覆盖下一次实验参数；编辑历史目录时保存默认值，仍只保存下一次实验参数和分类。历史列表读取本实验目录中的参数快照与实际 `results/` 文件，支持查看、填回和重新分析；填回不会自动保存或启动。正在写入的目录拒绝分析。

新增运行记录 `raw_format_version=1`，参数 `schema_version` 独立管理。原 ID、`data_type`、目录和结果文件名保留；已记录本实验 ID 或 `data_type` 的旧目录支持缺少原始格式标记，以及缺少总索引时从已完成 `current_*/frequency_scan.npz` 重建。未知身份与未知格式明确拒绝，不改写历史原始数据。

## 安全结束状态

运行前记录 GS200 的源模式、电流设定、输出开关、量程和限流。正常、取消或异常结束后均恢复该状态；Z 辅助场、X 场与 Y RF 归零关闭，温控恢复为 5 V DC、Output ON。Pump/Probe 光功率、Pump 载波/门控和 锁相放大器 配置按标准策略保留，正常结束只断开 TEC。

Pump 收尾沿用最近成功执行 A06 的载波与门控参数；应用参数但尚未重跑 A06 时，结束会话不会将新值写入仪器。实际重跑 A06 后才更新该保留基线；尚未执行 A06 时沿用会话初始参数的原有收尾策略。
