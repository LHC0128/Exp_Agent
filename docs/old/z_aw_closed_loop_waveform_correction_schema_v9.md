---
title: Z 任意波实际电流闭环波形校正
type: Z_AW_Closed_Loop_Waveform_Correction
scan_mode: point_by_point
defaults:
  CONTROL_SOURCE_SET: oc_sens
  CORRECTION_METHOD: time_domain
  TIME_DOMAIN_INITIALIZATION: amplitude_match
  MAX_ITERATIONS: 8
  ITERATION_REPEATS: 2
  HOLDOUT_REPEATS: 1
  ITERATION_DAMPING: 0.05
  INVERSE_REGULARIZATION: 0.02
  TIME_DOMAIN_CUTOFF_HZ: 300000.0
  TIME_DOMAIN_MAX_STEP_V: 0.05
  TIME_DOMAIN_MIN_DAMPING: 0.001
mapping_keys:
  Z_magnetic_field:
    role: iterative_arbitrary_output
  Time_sequence_2:
    role: common_trigger
  scope_waveform:
    role: CH3_current_feedback_and_CH4_trigger
required_devices:
  - DG4000
  - SDS
learned_notes:
  - 时域模式默认只学习 300 kHz 以下的周期误差并保留 DC，最终命令按同一截止频率低通。
  - 时域截止频率设为 0 时恢复完整采样带宽；标量、实测周期动态逆和最终命令使用同一频带。
  - schema v8 按用户要求移除电流/功率上限及预测阻断，仍保存实际电流。
  - 时域误差恶化时回退最佳波形，阻尼和单轮限幅均减半；电压极值超出采集量程立即停止。
---

# Z 任意波实际电流闭环波形校正

稳定实验 ID 为 `z-aw-closed-loop-waveform-correction`。基础参数中的
`CONTROL_SOURCE_SET` 可选择 `oc_sens` 或 `oc_broadband_v2`，程序固定从
`D:\Code\theory_agent\simulate\results\<CONTROL_SOURCE_SET>\<CONTROL_VERSION>\`
读取 `waveforms/optimal_control_waveform.csv` 和
`parameters/optimal_control_params.csv`。默认 `CORRECTION_METHOD=time_domain`，
以静态标定命令为基准匹配主谐波幅值，再按带限时域电流误差更新；选择 `frequency_domain` 时才做
初始频响预加重和正则化逆滤波。每轮保存原始采集、对齐反馈、命令哈希和独立 hold-out
采集，最终冻结最佳独立验证轮次，并对交付命令执行同一截止频率的低通。

只有包含完整任意波和参数 CSV 的版本可运行，预检会明确报告文件缺失。旧配置中的
`CONTROL_RESULTS_ROOT` 仅用于迁移识别固定结果集，不再作为 GUI 可选路径。

频域模式的 `INVERSE_REGULARIZATION` 是无量纲相对比例。程序先在可靠频点中计算
`H_ref = max(|H|)`，再使用 `lambda = INVERSE_REGULARIZATION * H_ref`
作为与 `H` 同单位的 Tikhonov 正则化尺度。默认 `0.02` 表示可靠频响最大增益的
2%，不能直接作为 `0.02 A/V` 代入。schema v2 会保留旧配置中的同名数值，
但修正了 schema v1 把它误当作绝对 `A/V` 数值的错误。

## 当前限制与历史兼容（schema v9）

按用户要求，闭环实验已删除电流上限、采样电阻功率上限以及全频谱电流/功率
预测阻断。目标预检、初始命令、候选更新、反馈、hold-out 和冻结保存均不再执行
这些检查。新快照记录 `current_power_limits_enforced: false`。

GUI 和默认 YAML 删除 `MAXIMUM_CURRENT_A`、`SENSE_RESISTOR_POWER_RATING_W`、
`SENSE_RESISTOR_POWER_DERATING`、`CURRENT_PREDICTION_GAIN_MARGIN`；旧 YAML 中的
同名键仍可加载，但会在迁移时丢弃，不再影响运行。历史运行目录及停止原因不改写。

`SENSE_RESISTOR_OHM` 仍用于电压换算电流，必须为有限正阻值，并与耦合标定一致。
DG 电压范围、幅度回读、CH3 量程极值、无效数据校验、迭代回退及结束关闭保持有效。
此变更只作用于本闭环实验，共享电流/功率校验及其他实验保持原有行为。

## 带限时域误差迭代（schema v9）

### 输出回读与动态逆学习（schema v7）

Z 输出在关闭状态明确设置高阻和 VPP，再配置任意波、偏置和频率。开启前回读
幅度、单位、偏置、频率、负载并检查错误队列；不一致即停止。每轮保存
`raw/iteration_*/output_readback.json`。高阻是命令电压的参考基准，不消除发生器
50 Ω 内阻和实际负载分压；历史没有回读的数据不追溯改写。

`TIME_DOMAIN_LEARNING_OPERATOR=scalar` 保留原逐点标量更新；`shift_inverse`
将本轮命令与实测周期分别循环移位形成最多 32 列时域矩阵，用相对正则化 0.002
求误差的逆修正，再以标量增益处理模型未解释的带内残差。两种算子都会先用周期 FFT
零相位低通限制学习频带，默认截止频率为 300 kHz。冻结最佳轮次后，程序还会按同一
截止频率低通最终交付命令，清除初始命令、续跑命令或迭代波形中已有的带外成分。
该方法依赖周期系统局部线性和稳定，独立验证仍负责选优、回退与停止。
单轮限幅和电压余量检查保留；电流/功率检查已在 schema v8 移除。旧 YAML 缺少该字段时保持 scalar。

`TIME_DOMAIN_PROJECT_VOLTAGE=true` 时，先整体限制单轮修正，再将越界命令样本
投影到允许电压区间，未触及边界的样本可继续校正；它会改变命令形状，但不修改测量。
默认 false 保持历史的整体缩小规则。`INITIAL_WAVEFORM_SOURCE_RUN` 可指定已核验
高阻基准、目标、时间轴和耦合标定均一致的冻结运行继续迭代，来源及文件哈希写入新快照。

时域标量更新使用 `u_next = u + alpha * LPF(target - measured) / g`。
采集仍以 CH4 为共同参考，对齐实测周期后逐点相减；低通保留 DC 和不高于
`TIME_DOMAIN_CUTOFF_HZ` 的离散频点，不做滑动平均或去均值。截止频率为 0 时
`LPF` 等于恒等变换，恢复完整采样带宽。

`g` 为正标量 A/V 增益：在基频处对可靠扫频的幅值进行插值；基频超出标定范围时，
使用最近端点的幅值作为换算尺度，不据此拒绝运行或屏蔽误差。标量增益不是完整动态
模型，不能保证各频率的更新都收敛。频响文件仍用于提供该增益，
读取时仍要求原始标定包含至少两个可靠扫频点；时域模式不再要求目标 FFT 点与其相交。

| 参数 | 默认值 | 作用 |
|---|---:|---|
| `ITERATION_DAMPING` | 0.05 | 初始学习率 |
| `TIME_DOMAIN_CUTOFF_HZ` | 300 kHz | 时域误差学习与最终命令低通截止频率；0 表示完整带宽 |
| `TIME_DOMAIN_MAX_STEP_V` | 0.05 V | 每轮每个样本的最大电压改变量 |
| `TIME_DOMAIN_MIN_DAMPING` | 0.001 | 回退降步长的停止下限 |

修正波形按同一比例整体缩小，以满足单轮限幅和固定 DG Vpp/offset 的电压余量。
不逐点截断，因此不会改变各误差分量的相对比例；若样本已到电压边界且修正还要向外，
整体修正仍可能变成零。迭代期间的低通只限制新增修正；冻结最佳轮次后，最终低通会删除
初始或继续运行命令中已有的带外分量。若低通产生电压过冲，程序会围绕固定 offset 整体缩小，
不会逐点裁剪并重新引入高频；它也不会自动解除电压限制。
hold-out 误差超过最佳值的 `MAXIMUM_ERROR_INCREASE_FRACTION` 时，回到最佳命令及其
对应反馈，同时将阻尼和单轮限幅减半。低于最小阻尼时停止；所有尝试计入最大轮数。

### 历史兼容

schema v4～v5 使用带限时域误差，`TIME_DOMAIN_CUTOFF_HZ=0` 当时表示自动取可靠上限，
DC 和带外误差不更新。schema v6 按用户要求改为完整误差更新：旧截止频率键仍可读取和
保存，但从 GUI 隐藏且不参与 schema v6～v8 的计算。schema v9 重新启用该字段并将默认值
设为 300 kHz；所有旧 schema 配置迁移时明确设为 0，避免重跑时静默改变原有完整带宽行为。
历史原始数据不迁移，离线分析仍按各次运行快照解释当时的学习方式。

新带限运行保存 `learning_mode=band_limited_time_domain`、实际学习频点、请求截止频率、
标量增益和增益参考频率；截止频率为 0 时仍保存 `full_time_domain` 且不枚举全部 FFT 点。
每轮记录原始、带内和拒绝带外误差 RMS。分析中的带外残差不参与命令更新，但仍计入原始
全带宽 NRMSE。最终文件中的 `voltage_v` 是低通后的交付命令，`iterated_voltage_v` 是滤波前
最佳迭代命令，`final_filter_report_json` 记录滤除量和电压缩放；运行快照明确记录最终命令
尚未经过额外硬件采集，最佳轮次的 hold-out 指标不能解释为最终低通命令的实测指标。
频域逆滤波模式保持原有可靠频点检查，最终命令不做该时域低通。

## 电压余量与实测数据检查

### 幅值初始化与可达性预检（schema v5）

`TIME_DOMAIN_INITIALIZATION=amplitude_match` 对目标中最强的可学习谐波使用标量
`g` 换算所需电压幅值，保留原命令相位和其他谐波。该操作只匹配幅值，不使用旧复数
频响相位求逆。随后执行 DG 电压范围检查，才允许连接仪器。

无论采用新初始化还是 `static` 历史初始化，预检与运行都会计算上述幅值匹配命令。
估算超出固定 Vpp/offset 范围时直接拒绝，并报告所需电压范围与 Vpp；不自动降低目标、
增大驱动或绕过安全限值。这是基于标定增益的初始化估算，不代表大幅度下已经实测验证。
schema v1～v4 配置迁移后保持 `static` 初始化方式，同时启用余量检查；当前默认 YAML
显式使用 schema v5 与 `amplitude_match`。

### 重分析结果

`waveform_comparison.png` 分别展示真实 mA 幅值、真实误差与独立归一化形状。
归一化面板仅诊断形状，不改写测量数据或冻结命令。绘图优先使用配置记录的冻结最佳轮次，
不因离线额外对齐重新选出与冻结文件不同的命令。

`amplitude_bandwidth_diagnosis.png` 和 `diagnosis.md` 报告谐波幅值、实测基波增益、
所需电压的线性外推，以及“学习频带完全匹配、其他分量保持不变”的条件残差。
旧运行只有快照记录了学习频点时才拆分带内和带外；新运行显式标记带限或完整时域模式，
不从当前默认参数推测历史频带。
`final_waveform_report.yaml` 的 `target_reached` 使用原采集指标与原目标阈值判断，
区分正常完成流程和达到误差目标。历史运行未记录阈值时返回空值。

schema v4～v7 曾采用全频谱最大标定增益乘裕量估算电流，并检查每帧电流和
RMS 功率。schema v8 不再执行这些预测和限制；历史报告中的功率拒绝原因仍代表
当时的真实运行行为。每次反馈和 hold-out 保留原始电压、电流换算、量程检查及落盘。
电压允许范围为该帧采集时的 `-offset ± scale_v_div * SCOPE_VERTICAL_DIVISIONS / 2`，
最大、最小值等于边界也通过；不再因连续相同极值或平顶形状终止闭环。
量程检查仅判断电压极值是否越界，不判定驱动器或其他模拟环节是否饱和。

连接仪器前，日志会显示 FFT 交集、更新频带、静态基准和实际命令电压范围、
DG 归一化峰值与输出回读。电压余量、动态相位和采集噪声仍可能使总 NRMSE 无法达到阈值。

冻结结果包含时间轴、目标耦合、目标电流、命令电压、DG 归一化波形、Vpp、offset、两类标定运行和哈希。正式 RF 灵敏度测量只加载该文件，不在线继续迭代。

运行期间会实时输出设备配置、每轮反馈与 hold-out 采集进度、NRMSE、最佳轮次、安全检查和最终关闭状态；命令行与 GUI 使用同一日志流。

入口为 `experiments/Z_AW_Closed_Loop_Waveform_Correction.py` 和 `experiments/Z_AW_Closed_Loop_Waveform_Correction_plot.py`。
