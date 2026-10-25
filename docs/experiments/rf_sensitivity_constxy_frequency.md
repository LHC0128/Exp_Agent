---
title: 固定 XY 控制下的 RF 频率响应
experiment_id: rf-sensitivity-constxy-frequency
defaults_file: params/experiments/rf-sensitivity-constxy-frequency.yaml
type: RF_Field_Sensitivity_ConstXY_FreqSweep
scan_mode: point_by_point
mapping_keys:
  Z_magnetic_field:
    role: scan
  X_magnetic_field:
    role: fixed
  Y_magnetic_field:
    role: fixed
  X_magnetic_field_AM:
    role: fixed
  Y_magnetic_field_AM:
    role: fixed
  lockin_r:
    role: detection
required_devices: [GS200, 信号发生器, HF2, TEC103]
learned_notes:
  - 0 Hz 是 Z 扰动关闭的背景测量，XY 载波与 DC 包络保持开启。
  - 双主电压物理回环只允许 HF2，MFLI 必须在连接前拒绝。
  - 温控恢复为 5 V DC + ON 后才保存当前频点。
---

# 固定 XY 控制下的 RF 频率响应

## 目的与入口

固定 XY 外部 AM 载波及恒定 DC 包络，逐点扫描 Z 方向射频扰动频率，比较主解调 Y 的直接 FFT、同段波形的软件锁相，以及物理回环后二次硬件解调的 R 响应，提取离散 −3 dB 带宽并保留原 Lorentzian／Bloch 拟合诊断。这个实验测量频率响应，不把名称中的“灵敏度”解释为另行计算磁噪声灵敏度谱。

当前默认值只引用 [GUI 实际保存的参数文件](../../params/experiments/rf-sensitivity-constxy-frequency.yaml)；各次运行以自己的 `experiment_config.yaml` 为准。模型、节点清单和业务位于 [`lab_workflows/experiment_modules/rf_sensitivity_constxy_frequency/`](../../lab_workflows/experiment_modules/rf_sensitivity_constxy_frequency/)。

- 采集薄入口：[`RF_Field_Sensitivity_ConstXY_FreqSweep.py`](../../experiments/RF_Field_Sensitivity_ConstXY_FreqSweep.py)。直接运行会连接映射中的真实设备，先核对接线和预检。
- 离线薄入口：[`RF_Field_Sensitivity_ConstXY_FreqSweep_plot.py`](../../experiments/RF_Field_Sensitivity_ConstXY_FreqSweep_plot.py)，必须显式指定目录，不再自动选最新历史。
- GUI：实验中心稳定 ID `rf-sensitivity-constxy-frequency`，运行／历史两个主 Tab；运行页为采集／独立分析两列流程图。

```powershell
# 只运行假仪器验收，不连接硬件
agent_exp_env/Scripts/python.exe experiments/RF_Field_Sensitivity_ConstXY_FreqSweep.py --acquisition-test
# 离线示例：替换为明确的运行目录；对照测试应使用历史的临时副本
agent_exp_env/Scripts/python.exe experiments/RF_Field_Sensitivity_ConstXY_FreqSweep_plot.py "data/RF_Field_Sensitivity_ConstXY_FreqSweep/<run_id>"
```

## 接线与参数含义

实际设备、资源和通道以 `params/devices.yaml`、`params/mapping.yaml` 的当前解析结果为准，不按旧脚本中的序列号或固定 CH1/CH2 连接。连接按资源复用，不占用 `rf_coil` 别名；本次 Y 通道由 XY 载波独占。同次独立物理量占同一端点时预检拒绝，不自动换设备。

- X/Y 磁场通道输出正弦载波、外部 AM；对应 X/Y AM 通道输出相同的恒定 DC 包络。`XY_DC_VOLTAGE = (LARMOR_FREQ_HZ - A_ENV_B) / A_ENV_K`。标定斜率不能为零；包络、载波完整电压范围及外部 AM 输入限制都在连接前和写入前校验。
- Pump 载波保持原固定 100 MHz 正弦，脉冲门控由 `PUMP_MOD_FREQ`、`PUMP_MOD_DUTY`、`RF_GATE_AMPLITUDE`、`RF_GATE_OFFSET` 配置；门控 SYNC 保留。时序2保持原固定 10 Hz、0–5 V 方波。Z 正频率点为无限 Burst、外触发，不改成连续无 Burst。实际触发线必须按原接线人工核对；程序不能证明线缆已接好。
- 探测器接锁相主电压输入 0。主解调 Y → Aux 输出 → 物理 BNC 同轴线 → Signal Input 2 → RF 解调器。两输入统一高阻、DC、单端。Aux 写入复用带明确电压上下限检查的公共接口；关闭 `CONFIGURE_AUXOUT2_TO_DEMOD0_Y` 时只是不重写 Aux，现有路由仍必须通过回读。
- 双主电压回采仅支持 HF2；当前设备映射若选择 MFLI，预检明确拒绝，不能自动改输入、降频或回退到 HF2。主解调、RF 解调和各参考振荡器不能相互覆盖；主参考、XY 载波及 Pump 门控频率必须一致。
- `ACQUISITION_MODE` 保留 `daq_fft_y`、`demod_r`、`both`；`POINT_DURATION_s` 控制 Y 记录，`DEMOD3_N_AVG` 与 `DEMOD3_AVG_INTERVAL_s` 控制硬件采样。原脚本中因大小写未显示的实际采集参数现在由模型显式声明。
- `SYSTEM_SETTLE_S` 是唯一配置／温控门控稳定参数；旧 `FREQ_SETTLE_TIME`、`TEMP_SWITCH_OFF_LEAD` 取最大值迁移，显式新键优先。`TEMP_SWITCH_ON_LAG` 是温控恢复等待，独立保留。旧固定时序记录键仅兼容读取，不再作为实际可调输出展示；时序2的旧记录值不能冒充实际 0–5 V 方波。

## 0 Hz 背景的真实含义

当扫描轴包含 0 Hz 时，Z 通道设为 DC 0 V、Burst OFF、Output OFF；**XY 载波与 DC 包络继续开启**。因此它是相同 XY 控制条件下“没有 Z 射频扰动”的背景，不是直流 Z 场响应，也不是关闭所有控制场后的噪声。

`daq_fft_y`／`both` 保存这段 Y 波形，离线对去均值、Hann 窗单边振幅谱取 10–5000 Hz 中位数，作为原 FFT 背景估计。正频率 FFT 幅值减去该单一估计，负值截为零。0 Hz 点不参加响应峰值、带宽和拟合；不读取硬件 RF R。软件锁相和硬件 R 保留原行为，不做这项扣背景。

若扫描不含 0 Hz，保留原算法的缺失背景诊断：校正 FFT 不冒充已扣底的有效曲线，不自动以零填充背景；原始 FFT、软件锁相和硬件数据仍保留。不能把背景中位数称为 PSD／ASD 或逐频点噪声谱扣除。

## 运行流程与交互

清单、依赖、参数影响及展示归属以 [`stages.py`](../../lab_workflows/experiment_modules/rf_sensitivity_constxy_frequency/stages.py) 为唯一事实源；[可编辑原图](../../lab_workflows/experiment_modules/rf_sensitivity_constxy_frequency/workflow.excalidraw) 也可从运行页下载。

| 节点 | 职责与前置 |
|---|---|
| A01 | 无硬件参数、映射、采集能力与安全预检，无前置 |
| A02 | 连接、资源复用和锁相输入初始化，依赖 A01 |
| A03 | 全部已连接时钟按设备库同步并严格回读，依赖 A02 |
| A04 | 主场、Pump／Probe 光功率、温控开启及 TEC 目标温度，依赖 A03 |
| A05 | Pump 载波、门控、SYNC 和原时序2触发方波，依赖 A04 |
| A06 | 锁相主解调及物理回环准备，依赖 A05 |
| A07 | XY／Z 关闭时主解调校相，依赖 A06；读数有限、R 足够，最多5次达到1°容差 |
| A08 | 配置 XY 载波／DC 包络及可选载波校相，依赖 A07 |
| A09 | 切至测量时间常数并保存实际回读，依赖 A08 |
| A10 | 单层扫频、原子保存及本轮统一收尾，依赖 A09 |
| A11 | 无前置的安全结束会话；如与其它节点一起选择，必须放在最后 |
| B01 | 只读加载身份／格式／频点，FFT 与同段软件锁相，无前置 |
| B02 | 三路带宽、原拟合、硬件均值诊断及数值报告，依赖 B01 |
| B03 | 响应、比值、拟合残差、相位及典型谱图导出，依赖 B02 |

主解调校相使用公共校相步骤。XY 校相保留原“只开 X → X/Y 同开”的 theta 反馈与减相位迭代；Y 从原协议的 90° 开始，禁用校相时使用固定相位。载波迭代用尽仍保留原未收敛诊断，不新增“相位差必须等于90°”的科学门槛。

运行页顶部包含保存参数与布局、派生预览、预检、完整运行、取消及任务日志；参数在节点内按基础／高级编辑，可同时展开多个节点。共享的系统稳定时间、恢复等待等使用同一份值；初始化条件只在配置它的节点编辑。分类移动同步共享入口并可保存。空数值拟合参数表示自动，设置后可点击“恢复自动”回到空值。

存活会话的修改先形成草稿并预览失效范围；只有“应用参数到会话”成功后才更新基线，之后须显式勾选重跑。保存默认、预检和应用参数均不自动执行。缺前置时拒绝、不自动补选。分步等待保持设备、输出和全局硬件锁；刷新可恢复存活会话，后台重启不根据磁盘记录恢复硬件。

A10 完成统一收尾后封存当前目录，锁相／XY 准备链失效；再次扫描须从 A06 重新准备。每轮产生独立目录，参数应用和新一轮草稿不改写前轮 raw 或采集快照。完整运行调用同一组步骤，GUI 完整任务在采集完成后自动分析；采集薄脚本仅采集，离线入口独立调用。

独立分析默认操作“下一次实验”，其参数供完整任务使用；切换具体已结束目录后使用该目录独立的分析草稿和基线。多个目录切换及主 Tab 切换保留各自草稿；此时保存默认仍只保存下一次实验及统一分类。历史 Tab 提供本实验列表、真实参数快照、产物下载、填回和重新分析；浏览不会启动采集或自动重分析，填回不会保存或启动。

## 采集时序、故障与结束状态

普通点严格按“设置仪器 → 温控 DC 0 V + Output ON → SYSTEM_SETTLE_S → 采集到内存 → 温控 DC 5 V + Output ON → 保存”执行。设备与初始配置在门控前准备；关闭窗口内不写波形、元数据或配置。XY 校相中的组内换态／调相重新使用同一系统稳定参数；硬件 RF 读取保留其原等待及样本间隔。

TEC 可连接时只下发 UI 目标温度，不启停、不判稳；串口连接失败告警跳过，未知实际温度保存为 null，控制来源单独记录。扫描和等待有取消检查点。RF 读取后半段失败也抢救已读 Y 和部分 RF 样本；恢复失败先尝试恢复再尽力写盘，保留原异常并停止后继。原子替换失败保留旧文件，完整待替换数据另存 `*_rescue_*.npz`。

正常完成、异常、取消、stdin EOF 及显式结束均复用统一安全收尾：**XY 载波／AM 包络与 Z 归零关闭；温控恢复 5 V DC + ON；保持已执行主场、Pump／Probe 光功率、Pump 调制、时序和锁相设置，仅断开 TEC**。不能以断开通信代替关闭输出，也不按尚未执行的参数草稿重新设置保留输出。连接阶段失败释放已连接设备；时钟失败不进入后继。收尾或断开失败明确标记不安全，不报告正常完成。

## 数据、分析和历史兼容

```text
data/RF_Field_Sensitivity_ConstXY_FreqSweep/<run_id>/
  experiment_config.yaml
  acquisition_state.json
  raw/
    freq_*.npz
    freq_*.npz.metadata.json
    frequency_index.json
    frequency_index.npz
  analysis_state.json
  results/
    responses.json
    fit_data.json
    analysis.json
    analysis.yaml
    frequency_response.npz
    plot_manifest.json
    *.png / *.pdf
```

保留原频点文件名、Y／time／最终实际率、硬件 X/Y/R/theta 数组及矢量／标量统计字段，新增每点完整性标志和恢复后才写的采集元数据。新 raw 格式独立为 v1，不与参数 schema v2 混用；新索引以不含 pickle 的 JSON 为权威，NPZ 为旧工具兼容。明确识别旧未标记格式的实验身份、`freq_sweep`、`xy_const_field`、两路解调配置及原索引／波形结构；不把任意缺标记目录猜作新格式。五份既有 ConstXY 运行属于此兼容范围。旧手工配置由模型统一迁移，未记录的字段使用模型回退，不宣称已知当年未保存的实际设置。历史原件不重写。

科学算法保留整段 Hann FFT／原 Welch 提取选项、原频率误差可疑点、整数周期软件锁相，以及 `sqrt(mean(X)^2 + mean(Y)^2)` 主响应和 `mean(R)` 正偏置诊断。−3 dB 带宽保持原离散阈值范围，不新增插值。保留单 Lorentzian 和原 Bloch 双 Lorentzian 的公式、优化重启、固定随机种子、参数与不确定度及原图名；拟合失败仍保存原因，不等于科学质量通过。修复旧脚本序列化 helper 在首次调用后才定义的问题，以及关闭 Bloch offset 时的参数／协方差维数错误，不更换科学模型或搜索策略。

每个分析阶段保存内容 SHA 指纹、参数和修订；原始输入、配置或产物被改动即失效。重算只归档所选阶段和后继产物到 `results/before_*`，B03 重画不移动仍使用的 B01/B02 数值输入；缺失或坏输入在归档旧科学结果前拒绝。读取历史分析状态不写配置，覆盖值只写 `analysis_state.json`；采集仍在写入的目录拒绝分析，整次和分步重分析共享目录锁。

本次自动化验证使用假仪器和临时副本；真实触发线、时钟物理锁定、回环接线、断联后的物理输出和绝对增益仍需现场确认。
