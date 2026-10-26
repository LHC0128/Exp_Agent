---
title: Mx Z 最优控制 XY 噪声谱
type: mx-z-optimal-control-xy-noise-spectrum
scan_mode: nested_scan
mapping_keys:
  X_magnetic_field:
    role: scan
  Y_magnetic_field:
    role: scan
  Z_magnetic_field:
    role: fixed
  lockin_r:
    role: detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - 锁相放大器
learned_notes:
  - XY 坐标为信号发生器电压，当前没有 X/Y 电压到磁场的独立标定。
experiment_id: mx-z-optimal-control-xy-noise-spectrum
defaults_file: params/experiments/mx-z-optimal-control-xy-noise-spectrum.yaml
---

# 实验目标

## 离线模块与运行目录

本实验清单显式声明 `run_step_id=A05`、`finish_step_id=A12`，不按编号或位置猜测角色。旧服务缺字段时阻止新分步执行，仍可恢复任务、取消和安全结束；“执行完成”不代表科学质量合格，诊断提示不新增硬件阻断。产物只按执行记录展示，不补造链接或回写历史元数据。共享参数失效、多参数合并及历史草稿隔离规则见 [GUI 分步契约](../../GUI/README.md#分步流程契约与兼容)。

分析模块可在仓库外、未设置运行上下文时导入，不创建运行目录或产物；原公开导出保留。以 `python -m lab_workflows.experiment_modules.mx_z_optimal_control_xy_noise_spectrum.analysis` 执行时，必须通过 `LAB_RUN_DIR` 指定具体目录；缺失或不存在时明确拒绝，不回退最新目录。薄脚本仍沿用各自的 CLI 参数规范。

## Y RF 共享步骤与安全校验

Y RF 包络校验复用[公共步骤](../../lab_workflows/steps/mx_z_optimal_control.py)，拒绝幅度、偏置及包络中的 NaN 和 ±Infinity；有限值仍按原安全限值处理，不修改默认参数。仅在公共工作点初始化时复用零 RF 的包络校验；后续仍为纯 X/Y DC 噪声扫描，不新增 RF 校相或幅度扫描。

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。校相、扫描和噪声共用该等待；组首配置前移，组内每次换点或换态后重新等待，保留原分组和 OFF/ON 配对边界。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](../acquisition_timing.md)。


## 锁相采集方式与实际速率

噪声使用 `NOISE_RATE_SA_S` 请求率、`NOISE_DURATION_S` 时长和 `DEMOD_IDX` 解调器，只读取 R 时序。`RESPONSE_*` 参数用于公共工作点的初始配置与收尾恢复；本实验不执行响应扫描、扫频或 RF 校相。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](../instruments/lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../../params/experiments/mx-z-optimal-control-xy-noise-spectrum.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

在所选 Z 最优控制波形（闭环冻结 `corrected_run` 或理论/标定来源 `theory`）保持输出的条件下，扫描 X/Y 直流补偿偏置，测量 锁相放大器 Demod0 的 R 噪声谱。主排序指标为指定频段内的 R ASD 中位数，单位为 `V/√Hz`；本实验不输出未经标定的 `nT/√Hz`。

# 接线与状态

- Z 方向使用 `Z_magnetic_field` 当前映射通道输出所选最优控制任意波，输出保持开启；`Time_sequence_2` 按同机映射提供共同触发。
- X/Y 方向分别使用 `X_magnetic_field`、`Y_magnetic_field` 当前映射通道输出 DC 偏置，不按旧型号或通道常量连接。
- Y 通道与 RF 线圈共用物理通道。噪声采集前关闭 Y Burst 和 Modulation，只保留当前 Y DC；Y 偏置为 0 V 时关闭该通道输出。
- 锁相放大器仅采集 `DEMOD_IDX` 指定解调器的 `R(t)`，不执行 RF 相位校准或 RF 幅值扫描。
- Pump/Probe 与 Pump 调制按 Mx Z 最优控制公共工作点配置。

# 采集时序

每个蛇形 XY 网格点设置 X/Y DC 后，重复 `NOISE_N_AVG` 次：确认 Y 纯 DC、将 `Temp_Switch` 设为 0 V DC 且输出 ON、等待唯一 `SYSTEM_SETTLE_S`、采集 `NOISE_DURATION_S` 的 R(t)，然后恢复 `Temp_Switch=5 V DC + ON`，等待 `TEMP_SWITCH_ON_LAG_S` 后保存本条记录。每条独立恢复温控，不把整个网格缓存在内存。每条记录保存实际采样率和 XY/RF/控制/温控状态元数据（`temperature_gate_state=off_during_acquire_then_on`）。取消、通信异常和异常退出均先尝试恢复温控，抢救本条已读数据后停止后继；保留原始错误与恢复错误。安全收尾恢复响应解调、关闭归零 X/Y、恢复 GS200 原态，保持 Z 与共同触发；失败不标记安全完成。

# 离线分析

结果图统一通过 `lab_workflows.plotting` 的 `paper` 配置导出同名 PNG（300 dpi）和 PDF；原 PNG 文件名继续用于历史预览。默认固定画布，英文标注，图幅和导出约定见[科研绘图规范](../plotting_style.md)。重新分析生成新图，原始数据和数值结果字段保持原有契约。

对每个点的所有噪声记录使用 Hann 窗、`detrend="constant"`、`scaling="density"` 的 Welch 方法计算 PSD，先对重复记录的 PSD 求平均，再开方得到 ASD。统计掩码为

```text
LOW_FREQ_SKIP_HZ <= frequency <= NOISE_BAND_MAX_HZ
```

掩码内全部频点（包括 100 Hz、150 Hz 等窄带谱线）参与中位数。分析同时保存该频段的最大 ASD 及其频率，用于诊断确定性谱线。

# 输出

```text
data/Mx_Z_Optimal_Control_XY_Noise_Spectrum/<run>/
  experiment_config.yaml
  raw/source_corrected_control_waveform.npz
  raw/source_manifest.yaml
  raw/applied_control_waveform.npz
  raw/xy_bias_grid.npz
  raw/point_manifest.yaml
  raw/points/x_###_y_###/noise_###.npz
  acquisition_state.json
  analysis_state.json
  results/noise_metrics.json
  results/noise_median_matrix.npz
  results/analysis.yaml
  results/analysis.json
  results/noise_median_heatmap.png
  results/noise_spectrum_best.png
  results/*.pdf
```

分析器也可通过 `analyze_legacy_xy_rf_noise_run()` 读取旧 `Mx_Z_Optimal_Control_XY_RF_Sensitivity` 运行目录的噪声子目录，用于预实验趋势分析；旧数据仍只代表原始 Demod0 R 噪声，不能证明控制改善，也不能换算 X/Y 物理磁场。

# 预实验提示

旧 `[-0.01,+0.01] V` 网格在 `3–200 Hz` 的最低点位于 `X=+0.010 V, Y=-0.010 V` 边界，因此后续扫描应向该边界外扩展；当前网格见参数文件。统计上限必须作为参数保存在运行配置中，因为不同截止频率可能给出不同最佳点。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](../instruments/lockin_amplifier.md)。


# 运行、分步执行与历史

运行页复用 XY 流程容器，顶部提供保存参数与布局、派生预览、预检、完整运行、取消与任务日志。
采集和分析各占一列，节点内展开基础/高级参数；可见参数完全来自模型，未使用的历史 RF 拟合键仅保留兼容读取。
`DEMOD_IDX`、`DEMOD_OSC_IDX` 在实际配置与噪声节点共享同值；Pump、温度、时钟及触发等初始化条件仅在配置节点编辑。
分类移动同步所有入口，保存布局不改变参数值。

| 节点 | 实际职责与前置 |
|---|---|
| A01 | 无硬件预检、理论/标定或闭环来源冻结 |
| A02 | A01 后创建本轮目录、校验复制内容 SHA、按映射连接并保存初态 |
| A03 | A02 后同步并严格回读时钟，只写 TEC 目标，配置零 XY DC 与 Z 控制工作点 |
| A04 | A03 后初始化 X 外层、Y 蛇形的网格和点清单 |
| A05 | A04 后逐点重复采集 RF-off R 噪声，每条恢复温控后落盘 |
| A06 | A05 后恢复响应模式并安全封存；恢复失败阻断完成状态 |
| A12 | 无前置安全结束会话，不额外采集 |
| B01 | 检查运行身份、raw 资格、网格坐标、完整记录与实际采样率 |
| B02 | B01 后执行原 Welch、频段中位数、谱线峰值、二维排名和科研图 |
| B03 | B02 后保存完整 JSON/YAML 汇总 |

未选择且未有效完成的前置不会自动运行。空闲会话继续持有全局硬件锁，刷新页面可恢复；
后台重启后不从磁盘恢复硬件。执行中用取消，空闲时用 A12；取消、通信 EOF 和后台退出自动安全收尾。
草稿修改只预览失效范围，应用参数后必须显式重跑；仅修改统计频段不失效采集。
每轮重跑使用新目录；上一轮 raw 与参数保留，收尾使用最近成功执行的配置，未执行草稿不改变恢复输出。

分析默认编辑下一次实验的统计上下限，可切换到具体已结束目录。
具体目录只支持 `LOW_FREQ_SKIP_HZ` 与 `NOISE_BAND_MAX_HZ` 独立覆盖，不写入历史采集参数；
切换对象或主 Tab 保留各自草稿，保存默认值只保存下一次实验参数和统一布局。
完整与分步分析共用科学函数，原始输入或产物内容变化会失效旧阶段，重算归档受影响产物。
读取历史、摘要、结果与分析快照均为只读，填回参数不保存、不运行；历史重新分析绑定明确目录。

新采集独立记录 `raw_format_version=1`，参数 schema 仍为 v2。没有 raw 标记的历史仅兼容已核验的
schema v1/v2 `xy_bias_noise_spectrum` 网格；缺记录的已完成点、越界坐标、未知身份或格式被拒绝。
取消网格未完成点保留 NaN，已完成点仍可离线分析；安全恢复失败或仍在写入的目录禁止分析。
`analyze_legacy_xy_rf_noise_run(old_run_dir, output_dir=...)` 保留旧 RF 网格噪声预分析入口，
其身份与格式按该实验已验证规则核对；跨实验兼容不会混入本实验历史列表。
无硬件验收和写入性分析均显式使用临时 `run_dir`，不使用默认 latest 数据。

离线命令需显式指定目录：`agent_exp_env/Scripts/python.exe experiments/Mx_Z_Optimal_Control_XY_Noise_Spectrum_plot.py <run_dir>`；缺目录时报错，不隐式选取 latest。分析不重新加载外部控制来源，只读取该目录的原始噪声与采集快照。
