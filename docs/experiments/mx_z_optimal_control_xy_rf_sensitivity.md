---
title: Mx Z 最优控制 XY 补偿偏置 RF 灵敏度
type: Mx_Z_Optimal_Control_XY_RF_Sensitivity
execution_mode: typed_workflow
scan_mode: nested_scan
mapping_keys:
  main_magnetic_field:
    role: configured_current_and_restored
  Z_magnetic_field:
    role: optimal_control_preserved_on_exit
  Time_sequence_2:
    role: common_trigger
  X_magnetic_field:
    role: x_dc_compensation_scan
  Y_magnetic_field:
    role: y_dc_compensation_scan_and_rf_offset
  rf_coil:
    role: per_point_rf_sensitivity_drive
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
    role: detection_r
required_devices:
  - GS200
  - DG900
  - DG4000
  - 锁相放大器
learned_notes:
  - RF 相位只在 X/Y 扫描轴算术中心校准一次，并复用于全部 XY 点。
  - X 外层、Y 内层使用蛇形顺序；Y 坐标直接作为 rf_coil 的 DC offset。
  - 每个 XY 点独立完成带符号 RF 幅度扫描和 RF-off 噪声采集。
  - R 点质量重试耗尽后该点标记无效并继续，硬件和安全错误立即终止。
  - 分析只报告最佳 XY 点，不自动切换或保持最佳补偿输出。
experiment_id: mx-z-optimal-control-xy-rf-sensitivity
defaults_file: params/experiments/mx-z-optimal-control-xy-rf-sensitivity.yaml
---

# Mx Z 最优控制 XY 补偿偏置 RF 灵敏度

## 离线模块与运行目录

分析模块可在仓库外、未设置运行上下文时导入，不创建运行目录或产物；原公开导出保留。以 `python -m lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity.analysis` 执行时，必须通过 `LAB_RUN_DIR` 指定具体目录；缺失或不存在时明确拒绝，不回退最新目录。薄脚本仍沿用各自的 CLI 参数规范。

## Y RF 共享步骤与安全校验

Y RF 包络校验复用[公共步骤](../../lab_workflows/steps/mx_z_optimal_control.py)，拒绝幅度、偏置及包络中的 NaN 和 ±Infinity；有限值仍按原安全限值处理，不修改默认参数。每个 XY 点仍复用原响应采集；噪声后的显式 Burst 恢复使用共享步骤，完成恢复时输出保持关闭，再由原采集步骤重触发。

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。校相、扫描和噪声共用该等待；组首配置前移，组内每次换点或换态后重新等待，保留原分组和 OFF/ON 配对边界。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](../acquisition_timing.md)。


## 锁相采集方式与实际速率

响应使用 `RESPONSE_RATE_SA_S` 请求率、`RESPONSE_DURATION_S` 时长和 `DEMOD_IDX` 解调器；噪声使用 `NOISE_RATE_SA_S` 请求率、`NOISE_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](../instruments/lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../../params/experiments/mx-z-optimal-control-xy-rf-sensitivity.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](../instruments/lockin_amplifier.md)。

## 目标与构型

稳定实验 ID 为 `mx-z-optimal-control-xy-rf-sensitivity`。实验沿用
`Mx Z 最优控制 RF 灵敏度`的 Z 周期最优控制、共同触发、主场、光功率、温控
和 锁相放大器 工作点，在 X/Y DC 补偿偏置上建立二维网格。

X 由 `X_magnetic_field` 输出 DC；Y 与 `rf_coil` 共用物理通道，Y 网格值作为
RF 波形的 DC offset。X/Y 坐标单位均为信号发生器电压 V，当前没有 XY 物理标定
时不转换为 nT。

## 校相与扫描

校相点固定为 X/Y 扫描起止值的算术中心。该点执行现有 Demod0 R/X/Y 复数正交
RF 校相，得到建设性 RF Burst 相位；相位校准失败时不进入灵敏度网格。
`PHASE_CAL_RF_AMPLITUDE_VPP` 必须大于零；本实验不使用单点实验的零 RF 剩磁相位模式。

正式网格采用 X 外层、Y 内层蛇形顺序。每个点先设置 X DC 和 Y offset，再使用
统一校准相位执行带符号 RF 幅度扫描。负幅度由 RF 相位增加 180° 表示，零幅度
切换为该点的纯 Y DC 补偿。RF-off 噪声采集时保持 Y DC offset。

每个响应 R 点按 `R_POINT_MAX_ATTEMPTS` 重试；重试仍不满足标准差门槛时，保留
已有原始记录，将该 XY 点标记为 `invalid_quality` 并继续。设备通信、参数配置或
安全校验错误会终止整个运行。

## 运行 / 历史与分步操作

本实验清单显式声明 `run_step_id=A04`、`finish_step_id=A12`，不按编号或位置猜测角色。旧服务缺字段时阻止新分步执行，仍可恢复任务、取消和安全结束；“执行完成”不代表科学质量合格，诊断提示不新增硬件阻断。产物只按执行记录展示，不补造链接或回写历史元数据。共享参数失效、多参数合并及历史草稿隔离规则见 [GUI 分步契约](../../GUI/README.md#分步流程契约与兼容)。

运行页沿用采集/分析两列流程容器；顶部保存参数与布局、派生预览、预检、完整运行及任务状态。
参数在实际使用的节点编辑；初始化条件仅在准备节点出现。`SYSTEM_SETTLE_S`、温控恢复等待、
响应时长及 R 点质量规则在校相和网格间共享，同一键保持同步。基础/高级分类按键统一保存。
草稿只预览失效范围，应用参数后显式重跑；保存默认值和预检均不会自动重跑。

| 节点 | 输入与前置 | 结果与门槛 |
|---|---|---|
| A01 预检与双来源冻结 | 模型参数、设备库、映射及安全限值 | 理论波形/标定或闭环冻结来源；校验完整输出包络和内容指纹，不连接硬件 |
| A02 本轮目录、连接与初态 | A01 | 新目录、来源副本、实际 Z 波形和 GS200 原态；副本 SHA 不符拒绝输出 |
| A03 中心 Mx 工作点 | A02、扫描轴和固定条件 | 严格同步时钟、只写 TEC 目标，配置中心 XY、主场、光功率、Z 控制和共同触发 |
| A04 中心 RF 正交校相 | A03、相位轴和质量参数 | 一次正交校相及诊断；质量不通过阻断网格，保留原始数据供重试 |
| A05 蛇形 XY 网格 | A04、幅度轴和噪声参数 | 每点完整响应、RF-off 噪声、点清单与实际率；R 重试耗尽仅跳过该点，通信/恢复/写盘错误停止后继 |
| A06 响应模式恢复与安全封存 | A05 | 使用已执行配置恢复响应解调、X/Y、温控及 GS200；恢复失败不能记为完成 |
| A12 安全结束会话 | 无前置 | 未封存时先收尾，完成后释放会话与硬件锁；已封存时只断开 TEC |
| B01 数据资格 | 具体运行目录 | 身份、格式、网格索引、坐标和已有数组的资格检查 |
| B02 原科学分析 | B01 | 逐点原拟合、Welch、主/零点灵敏度热图、最佳点及拒绝原因；全无效仍导出诊断并阻断 B03 |
| B03 完整汇总 | B02 | 保留中心相位、控制来源和标定信息的 `analysis.yaml/json` |

等待下一步时会话保持连接、输出和全局硬件锁；刷新可恢复存活会话，后台重启不会从旧记录恢复硬件。
噪声模式后重跑采集须显式包含 A03；重跑新建目录，保留原运行的参数和 raw。应用后尚未重跑的
Pump/响应参数不用于收尾。取消、异常、通信结束和后台关闭复用同一安全恢复协议。

分析列默认编辑下一次实验的分析参数，并与采集参数共享；切换到具体历史目录后使用该目录快照，
不支持额外分析覆盖值。保存默认值始终只保存下一次实验参数与统一布局。历史 Tab 可查看本实验
运行、原参数、热图、完整最佳点图和逐点下载；填回仅更改下一次实验草稿，不自动保存或启动。
分步分析和重新分析共用目录互斥。已有科学图与分析状态分别展示，最新失败诊断优先于旧成功报告。

## 数据与分析

```text
data/Mx_Z_Optimal_Control_XY_RF_Sensitivity/<run>/
  experiment_config.yaml
  raw/
    source_optimal_control_waveform.csv
    source_optimal_control_params.csv
    source_z_calibration_analysis.yaml
    source_manifest.yaml
    source_corrected_control_waveform.npz  # 闭环来源时替代理论波形/参数/标定副本
    applied_control_waveform.npz
    phase_scan.npz
    point_manifest.yaml
    xy_bias_grid.npz
    points/
      x_###_y_###/
        amplitude_scan.npz
        amplitude_*_attempt_*.npz
        noise_*.npz
  results/
    phase_calibration.yaml
    phase_calibration.png
    optimization.npz
    optimization.yaml
    optimization.json
    analysis.yaml
    analysis.json
    sensitivity_heatmap.png
    slope_heatmap.png
    zero_point_sensitivity_heatmap.png
    zero_point_slope_heatmap.png
    hwhm_heatmap.png
    best_point_full_analysis.png
    best_zero_point_full_analysis.png
    points/x_###_y_###/analysis.yaml
    points/x_###_y_###/full_analysis.png
  acquisition_state.json
  analysis_state.json
```

离线分析逐点复用 Mx Z 最优控制 RF 灵敏度的幅度分析约定，以及 Mx Y RF 的
等效 HWHM、Welch PSD、平坦频段检测和零点局部斜率实现。幅度拟合固定从
`V0=0 Vpp` 开始，避免把扫描端点的最低 R 值误当成共振中心；`gamma` 相对
不确定度仍写入诊断，但不作为 XY 网格点的硬拒绝条件。主判据为最小化
`flat_median_ft_per_sqrt_hz`；无效点保留失败原因并从排名中排除。最佳点只写入
分析结果，不改变仪器状态。

采集与分析共用 `run_format.py`：新运行写入独立的 `raw_format_version=1`，不随参数 schema 升级。
无格式标记的旧网格仅兼容已验证的参数 schema v1/v2，保留 `r_mean_v`/早期 `r_scalar_mean_v` 读取。
每次分析检查配置、递归 raw 和中心校相 YAML 的内容指纹；逐点产物也记录指纹。
重算只归档受影响分析阶段及后继产物，保留中心校相输入；坏输入不移动旧科学图。
缺失单点噪声按原规则形成无效点诊断，不伪造有效排名。JSON 中非有限诊断值写为 `null`，
NPZ 科学矩阵继续保留 NaN。原始历史文件不自动重写。

离线入口要求明确运行目录；任何写入性验证都只对临时运行副本执行，禁止默认 latest。
自动化回归使用假仪器并检查真实文件写入，无需连接实验硬件。

## 安全结束状态

正常、取消和异常结束均保留共同触发当前状态，关闭归零 X 场和 Y RF，保持 Z 最优控制波形及
Output 状态，恢复 GS200 运行前完整状态，并将温控恢复为 `5 V DC + Output ON`。
Pump/Probe、Pump 载波/门控和 锁相放大器 设置保留，正常结束只断开 TEC。
