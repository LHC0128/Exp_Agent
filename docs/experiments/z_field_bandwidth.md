---
title: Z 场频率响应与带宽
experiment_id: z-field-bandwidth
type: Z_Field_Bandwidth_Measurement
scan_mode: point_by_point
defaults_file: params/experiments/z-field-bandwidth.yaml
mapping_keys:
  Z_magnetic_field:
    role: scan
  main_magnetic_field:
    role: fixed
  lockin_r:
    role: detection
required_devices:
  - GS200
  - signal_generator
  - HF2
  - TEC103
learned_notes:
  - 0 Hz 是关闭 Z 的背景，不作为直流响应点。
  - 双主电压物理回环不支持 MFLI，禁止自动替换输入或回退设备。
---

# Z 场频率响应与带宽

固定小信号 Z 正弦驱动幅度，逐点扫描频率，比较主解调 Y 的直接 FFT、离线软件锁相与硬件 R 的响应，按原 −3 dB 准则计算带宽。X/Y 补偿只写预设值，输出保持关闭；本实验不加载 XY 任意波、外部 AM 控制或 Z Burst。

稳定 ID、`data/Z_Field_Bandwidth_Measurement/`、原 `freq_*.npz` 和科学字段保持不变。当前默认值唯一来源为[版本化参数文件](../../params/experiments/z-field-bandwidth.yaml)，GUI 保存默认值写回该文件；单次运行以自己的 `experiment_config.yaml` 为准，不用当前默认值替代历史参数。

## 接线、能力与安全

本实验清单显式声明 `run_step_id=A09`、`finish_step_id=A10`，不按编号或位置猜测角色。旧服务缺字段时阻止新分步执行，仍可恢复任务、取消和安全结束；“执行完成”不代表科学质量合格，诊断提示不新增硬件阻断。产物只按执行记录展示，不补造链接或回写历史元数据。共享参数失效、多参数合并及历史草稿隔离规则见 [GUI 分步契约](../../GUI/README.md#分步流程契约与兼容)。

- 主探测信号 → HF2 Signal Input 1 → 主解调器（参考为 Pump 门控频率）。主解调 Y → Aux 输出 → **物理同轴线** → Signal Input 2 → 硬件 R 解调器，参考随 Z 驱动频率变化。
- 两路主电压输入均为高阻、DC、单端。输入量程、Aux 路由、缩放、偏置和实际解调设置均回读；Aux 缺少明确安全电压上下限时拒绝写入。
- Pump 连续 100 MHz 载波经 RF 开关，`Time_sequence` 输出脉冲门控；占空比按频率换算为脉宽。`Time_sequence_2` 输出原协议的 10 Hz、零偏置方波。
- 历史键 `FIXED_PARAMS.Time_sequence` 仍控制这一路时序2方波幅度，实际仪器与通道由 `Time_sequence_2` 映射解析；`FIXED_PARAMS.Time_sequence_2` 和 `RF_GATE_DELAY` 原来未下发，继续只作隐藏兼容记录，不新增物理行为。
- 仪器按设备库与映射选择，同资源复用连接；独立物理量占同通道在预检拒绝。所有输出设置前检查安全限值。已连接时钟自动按设备库同步并严格回读，失败禁止启动输出；EXT 回读不替代现场锁定检查。
- 此双主电压回环仅允许 HF2。选择 MFLI 时连接前拒绝，不自动换输入、降频、回退仪器或改写设备库，即使只选择 `daq_fft_y` 也维持原实验的接线资格。
- TEC 串口不可用时告警跳过；可用时只写 UI 目标温度，不调用 enable、不等待判稳。未知实测温度为 `null`。

## 参数与采集

参数模型为 `lab_workflows/experiment_modules/z_field_bandwidth/models.py`，内部使用 snake_case，GUI/YAML 保持旧外部键；参数 schema 为 v2，独立 `raw_format_version=1`。`Z_FREQ_START`、`Z_FREQ_STOP`、`Z_FREQ_POINTS` 构成递增线性频率轴；`Z_DRIVE_AMPLITUDE` 为 Vpp。

三种 `ACQUISITION_MODE` 保留：

| 模式 | 采集内容 |
| --- | --- |
| `daq_fft_y` | 每点采集 `POINT_DURATION` 的主解调 `sample.y` 时域数据 |
| `demod_r` | 正频率多次读取硬件 X/Y/R/theta，保存逐次值及矢量/标量均值；0 Hz 仅记录背景身份 |
| `both` | 同一点先 Y 波形、后硬件 R；中途失败仍抢救已经读到的分量 |

每个频点严格执行：**配置实际 Z 驱动与解调 → 温控切到 0 V DC + ON → `SYSTEM_SETTLE_S` → 采集至内存 → 温控恢复 5 V DC + ON → 恢复等待 → 保存**。0 Hz 设置 Z 为 0 V DC + OFF，不更新硬件 R 的驱动参考。正频率使用连续正弦，硬件 R 的时间常数为 `max(DEMOD_R_TC_MIN, DEMOD_R_TC_PER_PERIOD / frequency)`，请求率保持 `DEMOD_R_RATE`，不静默随频率改速率。

旧 `FREQ_SETTLE_TIME`、`TEMP_SWITCH_OFF_LEAD`、`DEMOD_R_READ_SETTLE_TIME` 取最大值迁移为唯一 `SYSTEM_SETTLE_S`，显式新键优先；不再叠加第二次“读 R 前系统等待”。设备准备、校相迭代、样本读取间隔和温控恢复等待仍保留，均提供取消检查点。

目录及初始配置提前保存，关闭温控期间不写波形、元数据、扫描快照或分析结果。点文件和 JSON 索引原子替换，NPZ object 索引只保留历史兼容用途，新格式以无 pickle 的 JSON 索引为权威。恢复或后半段读数失败时先尝试恢复，再保存已读 Y/R 和未完整标记，保留原始异常；写盘失败保留旧完整文件及可用抢救文件，停止后继采集。

## 运行页流程图

入口 `/experiments/z-field-bandwidth` 提供「运行 / 历史」两个主 Tab。采集与独立分析两列来自同一[模块清单](../../lab_workflows/experiment_modules/z_field_bandwidth/stages.py)，[可编辑 Excalidraw 原图](../../lab_workflows/experiment_modules/z_field_bandwidth/workflow.excalidraw)可在页面下载。

| 模块 | 前置 | 职责及产物 |
| --- | --- | --- |
| A01 | 无 | 无硬件参数、映射、能力与安全预检 |
| A02 | A01 | 连接复用、输入初始化；部分连接失败也清理并记录失败 |
| A03 | A02 | 同步已连接仪器参考时钟并严格回读 |
| A04 | A03 | 主场、Pump/Probe 光功率、XY 预设但 OFF、TEC 目标温度 |
| A05 | A04 | Pump 连续载波、脉冲门控与时序2方波 |
| A06 | A05 | 主解调、Aux 物理回环与输入配置/实际值 |
| A07 | A06 | XYZ 关闭后主解调校相；有限读数、足够 R、5 次内达到 1°；质量失败保留诊断、阻断后继，可同连接重试 |
| A08 | A07 | 切换测量时间常数并冻结实际配置，模式漂移拒绝采集 |
| A09 | A08 | 逐点扫描、恢复后保存与本轮安全收尾，成功才封存目录 |
| A10 | 无 | 显式结束空闲会话，安全收尾后释放硬件锁 |
| B01 | 无 | 检查已结束目录的身份、格式、路径、波形和最终实际率，生成 FFT/软件锁相响应 |
| B02 | B01 | 原背景扣除、三路带宽、矢量/标量 R 与比值；导出 JSON/YAML/NPZ |
| B03 | B02 | 原幅频、归一化、比值、矢量/标量偏差、相位与典型频点谱图，PNG/PDF |

50 个当前可见参数在节点内编辑，基础名称常显、高级折叠。初始化条件只在 A04/A05 等真正设置节点展示；`SYSTEM_SETTLE_S`、温控恢复等待在 A07/A09 共享，主解调请求率在 A06/A08 共享值与基础/高级分类。保存参数与布局、派生预览、预检、完整运行、取消和任务日志统一位于流程图顶部，没有另一份参数总表。

存活会话改参先显示草稿与预计重跑范围；还原值清除预览，应用成功后仍需显式选择并重跑失效模块。缺前置列出缺项并拒绝，不自动补选或执行。A09 完成后 A06–A08 必须重新准备，再扫描创建新目录，不覆盖旧 raw/config。只应用但未执行的草稿不改变仪器或旧轮次快照。

**分步等待仍保持设备连接、输出与全局硬件锁。** 空闲点击「结束会话」；执行中用任务取消。刷新恢复存活会话，后端重启不从历史完成记录恢复硬件。EOF、后台关闭、取消及普通异常均自动收尾；安全恢复/断开失败不能标成安全完成，原始错误与清理结果分别保留。

### 独立分析与历史

分析对象默认为「下一次实验」，其设置用于完整运行的自动分析；选择具体已结束目录后，五个分析覆盖值和草稿按目录独立保存：`DAQ_RESPONSE_METHOD`、`LOCAL_PEAK_REL_WINDOW`、`MAX_PICK_FREQ_ERROR_HZ`、`OFFLINE_LOCKIN_USE_INTEGER_CYCLES`、`EXCLUDE_SUSPICIOUS_FOR_BANDWIDTH`。修改覆盖值按清单使 B01 或 B02 及后继失效，执行分析时应用，不改下一次实验参数或采集快照。保存默认值始终只保存下一次实验及统一布局，即使当前正在编辑历史分析。

历史页只读列表、快照、分析状态和文件，支持填回参数、重新分析、结果图与下载；填回不自动保存或启动。正在写入的目录拒绝分析。分步分析与旧重新分析共用目录互斥；输入/产物按内容指纹检查，重算归档受影响后继产物，不移动仍在使用的前置或原始文件。

## 数据、算法与兼容

```text
data/Z_Field_Bandwidth_Measurement/<run>/
  experiment_config.yaml
  acquisition_state.json
  analysis_state.json
  raw/
    freq_*.npz
    frequency_index.json
    frequency_index.npz
  results/
    responses.json
    bandwidth_data.json
    analysis.yaml
    analysis.json
    frequency_response.npz
    plot_manifest.json
    *.png / *.pdf
```

- 原 Hann 单边振幅 FFT、Welch 最近 bin／局部峰诊断、整数周期软件锁相算法保持。频谱使用每条记录的最终实际采样率，不用请求率或通信率替代。
- 只有 FFT 使用 0 Hz 背景振幅中位数扣除；软件锁相和硬件 R 不扣同一背景。硬件主响应为 `hypot(mean(X), mean(Y))`，`mean(R)` 和标准差仅作标量正偏差诊断，不互换。
- 带宽保留峰值除以 √2 的原离散点判据，首末超过阈值的点决定频带，不新增插值、拟合或截止模型。扫描边界仍在阈值以上时 `bandwidth_is_censored=true`，不能当作完整测得带宽。没有有效带宽仍导出空值诊断，不把流程执行完成等同科学质量通过。
- 原科学 NPZ 键、带宽别名、逐点掩码和比值统计保留。所有 Python 图使用公共 `paper` 配置和英文标注。
- 兼容已核实的未标记 schema v1/v2 手工快照及原索引；参数版本变化不改变同一 raw 格式的资格。不识别的身份、版本、索引路径、缺失/非有限波形或新格式未完整抢救点均拒绝，不修改历史来“修复”输入。
- 早期三份历史仅存 `demod_r_mean_V` 等标量字段、没有 X/Y 矢量均值。沿用原当前分析器的规则：仍分析其 Y/FFT/软件锁相，硬件矢量响应保持缺失，不把标量伪装成矢量；原文件可下载。后期两份历史包含完整矢量/标量链路。

## 结束输出与命令行

正常、异常、取消或通信结束均通过统一安全收尾：XYZ 归零关闭，温控恢复 **5 V DC + ON**；保持已执行的主磁场、Pump/Probe 光功率、Pump 载波/门控、时序及锁相配置，正常只断开 TEC。连接或时钟阶段失败则释放已建立连接；断开通信不代替关闭危险输出。

```powershell
# 正式采集会连接真实硬件，先核对设备映射和接线
agent_exp_env/Scripts/python.exe experiments/Z_Field_Bandwidth_Measurement.py
# 无硬件采集回归
agent_exp_env/Scripts/python.exe experiments/Z_Field_Bandwidth_Measurement.py --acquisition-test
# 纯离线分析必须显式给目录，不自动选择 latest
agent_exp_env/Scripts/python.exe experiments/Z_Field_Bandwidth_Measurement_plot.py data/Z_Field_Bandwidth_Measurement/<run>
```

采集入口和分析入口均为薄脚本；准备、激励/校相、内存采集/原子保存、科学计算、绘图和会话编排位于[实验包](../../lab_workflows/experiment_modules/z_field_bandwidth/)，核心流程不依赖 GUI。自动化验收只使用假仪器、合成数据和明确的历史临时副本，不包含真实硬件验证。
