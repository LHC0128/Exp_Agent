---
title: Mx 高主场 Z 磁场频率标定
type: Mx_Z_Field_Calibration
scan_mode: nested_scan
mapping_keys:
  main_magnetic_field:
    role: fixed
  Z_magnetic_field:
    role: scan
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
required_devices:
  - GS200
  - DG900
  - DG4000
  - 锁相放大器
learned_notes:
  - TEC103 为可选控制设备；串口不可连接时告警跳过，可连接时只下发界面目标温度，不启停或判稳。
  - 主场与 Z 偏置取自参数文件；普通线性模型要求扫描不跨过总场零点。
  - 采集工作流不执行共振拟合；全部 Lorentzian 和线性标定均由离线分析器完成。
  - 每个频点独立关闭温控采集，随后恢复 5 V 并按配置等待。
experiment_id: mx-z-field-calibration
defaults_file: params/experiments/mx-z-field-calibration.yaml
---

# Mx 高主场 Z 磁场频率标定

## 采集时序与系统稳定

实际 Z DC、Y RF 频率/幅度和锁相振荡器配置完成后，将 `Temp_Switch` 设置为 **0 V DC + Output ON**，等待唯一的 `SYSTEM_SETTLE_S`，把 R 读入内存；随后恢复为 **5 V DC + Output ON**，完成 `TEMP_SWITCH_ON_LAG_S` 恢复等待后保存该尝试。初始化目录和参数/设备配置提前写入。每个频点独立执行一次门控，坏点重采也使用相同的六步顺序；不同 Z 电压的频扫汇总只包含本组完整采集的频点。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。参考时钟和设备准备在温控开启时完成。取消或异常先尝试恢复温控，恢复失败仍尽力保存已读的当前 payload；保存或恢复错误不覆盖更早的采集异常。已经写入的频点和已完成 Z 频扫保留，不用插值补齐未完成组。参数迁移和异常数据保护见[统一采集规范](../acquisition_timing.md)。


## 锁相采集方式与实际速率

扫频使用 `RESPONSE_RATE_SA_S` 请求率、`FREQUENCY_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](../instruments/lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../../params/experiments/mx-z-field-calibration.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](../instruments/lockin_amplifier.md)。

## 目标和构型

本实验在 Mx 构型下标定 `Z_magnetic_field` DC 电压与 Y RF 共振中心频率之间的关系；实际仪器型号和通道以当前设备库及物理量映射为准：

\[
f_0(V_Z)=K_Z V_Z+f_{0V}.
\]

主场与 Pump 光沿 Z，Probe 光沿 X；`rf_coil` 沿 Y，锁相放大器使用 `DEMOD_IDX` 所选解调器，只采集 R。主场、零偏预测中心和初始斜率均读取参数文件。`Z_INITIAL_HZ_PER_V` 只用于确定每个 Z 电压的局部扫频窗口，不作为最终标定结论。

`FIXED_PARAMS.main_magnetic_field` 与 `ZERO_BIAS_CENTER_FREQUENCY_HZ` 均允许设置为 `0`。当零偏预测中心为 `0 Hz` 时，采集前使用首个 Z 扫描点的预测中心初始化 Y RF 与 锁相放大器；所有实际扫频点仍必须大于 `0 Hz`，否则预检会拒绝运行。

## 扫描流程

Z 偏置按 `Z_BIAS_START_V`、`Z_BIAS_STOP_V` 与 `Z_BIAS_STEP_V` 生成包含端点的扫描轴；范围必须包含整数个步进。

GUI 必须明确选择正 Z 电压使预测中心升高或降低。每个 Z 点的预测中心为：

\[
f_\mathrm{pred}=f_\mathrm{GUI}+sK_\mathrm{initial}V_Z,
\]

其中 \(s=\pm1\)。每个 Z 点按配置的局部频率窗口和步进扫描。设置新的 Z DC 偏置时，DG4000 使用专用 `VOLTage:OFFSet` 命令；工作流只验证波形仍为 DC 且输出开关状态正确。DG4162 的内部电压查询不能作为物理输出回读，因此原始数据记录的是命令值 `z_bias_commanded_v`；实际链路是否响应应以示波器等外部测量为准。设置后不执行专用稳定等待。

每个 Y RF 频点依次执行：

1. 设置 Y RF 频率与配置的幅度，并同步 锁相放大器 振荡器。
2. 将温控开关设置为 0 V DC 且保持 Output ON。
3. 按参数配置等待温控关闭与频点稳定。
4. 按配置时长采集所选解调器的 R。
5. 在 `finally` 中恢复温控为 5 V DC + Output ON，并按配置等待。
6. 保存本次原始尝试和质量元数据；当前 Z 频扫完整后保存本组汇总，全部完成后保存总索引。

若单次记录超过配置的标准差门槛，完整保存该尝试并按配置的尝试次数重采。采集阶段不检查共振峰，不执行 Lorentzian 拟合，也不扩展扫频窗口。

## 运行页与可执行步骤

实验中心保持“运行 / 历史”两个主 Tab。运行页的流程容器顶部提供保存参数与布局、派生预览、仅预检、完整运行、取消以及任务状态/日志。完整运行与分步会话调用同一套准备、激励、采集和收尾业务；分步等待时设备连接、既有输出及全局硬件锁继续保持，需要主动结束或取消后才释放。

| 节点 | 实际职责 | 前置/产物或门槛 |
| --- | --- | --- |
| A01 | 无硬件预检 | 校验参数、物理通道冲突与锁相能力，不连接设备 |
| A02 | 设备连接、可选 TEC 与初态回读 | A01；按 resource 复用连接 |
| A03 | 参考时钟同步与严格回读 | A02；失败停止后继，收尾后释放连接 |
| A04 | Z/X 归零关闭、光功率、固定主场电流 | A03；输出前执行安全校验 |
| A05 | 下发 TEC 界面目标温度 | A04；不启停、不判稳 |
| A06 | Pump 连续载波与常开门控 | A05；保持原 Mx 构型 |
| A07 | Y RF 正弦及 R 解调准备 | A06；使用首个 Z 点预测频率并记录实际率 |
| A08 | Z 电压 × 局部 Y RF 扫频 | A07；R 标准差超过门槛时重采，耗尽后标记质量未通过并阻断 A09 |
| A09 | 本轮安全收尾与封存 | A08；失败不能标记安全完成 |
| A10 | 安全结束会话 | 无前置，可在尚未连接时执行；取消、异常、stdin EOF 和后端关闭也执行统一收尾 |
| B01 | 独立目录资格与原始频扫检查 | 采集已结束，身份/格式可识别，索引路径属于该运行目录 |
| B02 | 每条 Z 频扫的 Lorentzian 中心 | B01；`curve_fits.json` 保留接受掩码、NaN 与排除原因 |
| B03 | 加权直线与线性质量判断 | B02；`calibration_evaluation.json` |
| B04 | 原有三张科学图、JSON/YAML 和 NPZ | B03；质量未通过仍保存诊断，执行完成与标定通过分别记录 |

参数在节点内按基础/高级分类编辑，可同时展开多个节点。主场、光功率和温度只在实际设置它们的初始化节点出现；零偏预测中心、初始斜率、预测极性、起始 Z 电压与 Y RF 幅度由 A07/A08 共享同一份值。展示归属和失效范围取自本实验 `stages.definition()`，不从页面位置推断执行依赖。

存活会话修改参数后显示“草稿未应用”和预计需重跑的已完成节点；还原修改会清除预览。应用参数会重新全量预检并使实际使用节点及后继失效，需要显式选择重跑，保存默认值和仅预检不会执行步骤。Pump 保留输出使用最近成功 A06 的设置，安全结束不会悄然写入尚未重跑的新 Pump 参数。A09 会把 Y RF 恢复为零电压 DC，因此再次扫描前必须重新执行 A07。每轮重采建立独立目录，旧 `raw/`、参数快照和分析结果保留。已结束会话保留执行记录，后续编辑用于下一次实验。

分析列默认编辑“下一次实验”，与下一次采集参数共享 `LINEAR_R_SQUARED_MIN`。切换到具体已结束/历史运行目录后，只允许覆盖该键，其草稿与已应用基线按目录独立保存，不改变下一次参数或历史采集快照。切换目录或主 Tab 后草稿保留；保存默认值始终只保存下一次实验参数和统一分类偏好。正在写入的运行目录拒绝分析，分步分析与原重新分析入口对同一目录互斥。

可编辑原图位于 [workflow.excalidraw](../../lab_workflows/experiment_modules/mx_z_field_calibration/workflow.excalidraw)，也可从运行页下载。运行状态来自实际会话与阶段记录。

## 职责模块与兼容入口

`workflow.py` 编排完整/分步会话与统一结束；`preparation.py` 负责无硬件预检、连接、时钟、初始输出和安全恢复；`excitation.py` 负责 Z DC 验证、Pump 与 Y RF/R 准备；`acquisition.py` 负责真实扫描、重采和恢复温控后写盘。`analysis.py` 只编排阶段，科学算法位于 `analysis_steps.py`，原科学图位于 `analysis_plotting.py`。`stages.py`、`session.py` 与 `run_format.py` 分别维护节点清单、串行命令和原始格式资格。

稳定 ID `mx-z-field-calibration`、`data_type=Mx_Z_Field_Calibration`、外部参数键、薄脚本与原 NPZ 文件名保持。旧实验仍导入的 workflow helpers、`run_calibration()`、`analyze_calibration()` 和分析辅助函数继续可用；Bell Bloom 的频率轴标签与电流耦合实验的可选 SDS 支路保持其兼容调用。

## 离线分析

分析器从 `raw/z_scan_index.npz` 和各 `raw/z_*/frequency_scan.npz` 重建各 Z 偏置点的频率响应，对每条响应拟合带基线 Lorentzian。正式中心只执行拟合所需的基础有效性检查：

- 拟合正常收敛且输入中至少有 5 个有限频率点；
- HWHM 大于频率步进且小于扫描跨度的一半；
- 中心位于扫描窗口内，且距边缘不少于一个 HWHM。

单曲线 R²、中心标准不确定度和 HWHM 相对不确定度只作为诊断结果，不再参与中心取舍。离散最大值也只作为诊断。有效中心使用标准不确定度加权线性拟合；线性拟合在数学上至少需要 2 个有效中心，最终线性 R² 仍需达到配置阈值。不再计算或输出有效 Z 点数与有效 Z 跨度。

新运行写入独立的 `raw_format_version=1`，参数 `schema_version` 继续由模型管理。历史配置明确具有本实验 ID 或 data_type 时，允许缺少原始格式标记；中断运行缺少总索引时，只发现已经完整保存的 `z_*/frequency_scan.npz`。未知身份/版本拒绝，历史原始文件不重写。

`analysis_state.json` 记录阶段参数、修订、配置哈希与原始/产物文件的大小和修改时间指纹。输入或产物变化使旧阶段失效，前置不再有效时拒绝直接执行后继。重新计算将本阶段及后继的产物归档到 `results/before_*`，修改线性质量阈值只使 B03/B04 失效，保留仍使用的 B02 曲线输入；没有阶段记录的旧数据从 B01 重建。

结果写入：

```text
data/Mx_Z_Field_Calibration/<run>/
  experiment_config.yaml
  raw/
    z_scan_index.npz
    z_000/frequency_scan.npz
    ...
  results/
    analysis.yaml
    analysis.json
    calibration_results.npz
    frequency_response_fits.png
    z_frequency_calibration.png
    z_frequency_residuals.png
```

采集与离线分析入口分别为 `experiments/Mx_Z_Field_Calibration.py` 和 `experiments/Mx_Z_Field_Calibration_plot.py`。

## 安全结束状态

正常、取消和异常路径均归零关闭 Z DC 与 Y RF，保持 X 场关闭，将温控恢复为 5 V DC + Output ON。主场、Pump/Probe 光功率、Pump 载波/门控和 锁相放大器 配置按标准策略保留；正常结束只断开 TEC。
