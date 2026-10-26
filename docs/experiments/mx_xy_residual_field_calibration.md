---
title: Mx XY 剩磁二维校准
type: Mx_XY_Residual_Field_Calibration
execution_mode: typed_workflow
scan_mode: nested_scan
mapping_keys:
  main_magnetic_field:
    role: fixed_scan
  X_magnetic_field:
    role: scan
  Y_magnetic_field:
    role: scan
  Z_magnetic_field:
    role: fixed_off
  scope_waveform:
    role: detection
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  Temp_Switch:
    role: point_gate
required_devices:
  - GS200
  - DG900
  - DG4000
  - SDS
learned_notes:
  - TEC103 为可选设备；串口不可用时告警并跳过设温，普通采集始终不启停或判稳。
  - 平衡探测器输出定义为 SDS CH1 DC 波形的算术平均电压。
  - 不采集强主场基准，直接拟合原始 PD 均值及线性时间漂移。
  - 主平衡值来自 Bz 近似为零时的二维稳态 Bloch 全局拟合。
  - 固定 X 的 Y 色散拟合保留为独立交叉验证。
experiment_id: mx-xy-residual-field-calibration
defaults_file: params/experiments/mx-xy-residual-field-calibration.yaml
---

# Mx XY 剩磁二维校准

## 离线模块与运行目录

分析模块可在仓库外、未设置运行上下文时导入，不创建运行目录或产物；原公开导出保留。以 `python -m lab_workflows.experiment_modules.mx_xy_residual_field_calibration.analysis` 执行时，必须通过 `LAB_RUN_DIR` 指定具体目录；缺失或不存在时明确拒绝，不回退最新目录。薄脚本仍沿用各自的 CLI 参数规范。

## 采集时序与系统稳定

实际 X/Y 配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，示波器波形先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。目录及初始配置提前保存。本实验无锁相校相、强主场参考或 OFF/ON 配对；每个网格点的每次重复和量程重采均独立执行完整六步时序。后续量程尝试、恢复或保存失败时，先尝试恢复温控，再尽力抢救已读帧并停止后续点。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](../acquisition_timing.md)。


当前默认参数：[参数文件](../../params/experiments/mx-xy-residual-field-calibration.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

## 目标

本实验在 GS200 主场设为配置的扫描电流且 Output ON 时，直接二维扫描
`X_magnetic_field` 和 `Y_magnetic_field`，不采集强主场基准。分析器在 `Bz` 近似
为零的前提下，将原始 PD 均值、线性时间漂移和二维稳态 Bloch 响应一起拟合，以连续
拟合中心确定 X/Y 补偿设置，并同时给出离拟合中心最近的实际网格点。

采集直接使用 SDS CH1，不连接或配置 HF2。Z 辅助场固定为 0 V、Output OFF。

## 扫描流程

1. 初始化时将 X/Y 写为 0 V 并关闭输出，GS200 设为扫描主场电流并保持 Output ON。
2. 按 X 外层、Y 从起点到终点的同向顺序扫描完整笛卡尔网格。扫描阶段两路 X/Y
   Output 始终开启，包括设定值严格等于 0 V 的网格点。

X/Y 网格范围、点数、等待、SDS 采样率和记录时长均读取参数文件。每次采集前关闭
温控并等待，采集后恢复温控为 5 V ON 并按配置等待。二维 Bloch 拟合要求 X 轴至少 3 点；Y 轴至少需要 7 点，以同时支持二维拟合
和逐 X 色散交叉验证。自动量程需要重采时，每次尝试都重复完整温控门控。

## 模块化与交互流程图

本实验清单显式声明 `run_step_id=A08`、`finish_step_id=A10`，不按编号或位置猜测角色。旧服务缺字段时阻止新分步执行，仍可恢复任务、取消和安全结束；“执行完成”不代表科学质量合格，诊断提示不新增硬件阻断。产物只按执行记录展示，不补造链接或回写历史元数据。共享参数失效、多参数合并及历史草稿隔离规则见 [GUI 分步契约](../../GUI/README.md#分步流程契约与兼容)。

运行页采用 XY 风格的采集／分析两列，复用全局硬件队列、串行子进程会话和历史容器；不连接或预检锁相。保存参数与布局、派生预览（有派生字段时）、预检、完整运行、取消及任务日志位于流程容器顶部。全部 20 个可见参数在节点内按基础／高级分类编辑。

| 节点 | 职责 | 前置 |
| --- | --- | --- |
| A01 | 参数、安全限值与实际映射预检，不连接硬件 | 无 |
| A02 | 连接复用，回读 GS200 运行前模式、电流、输出、量程和限流 | A01 |
| A03 | 已连接参考时钟同步与严格回读 | A02 |
| A04 | 扫描主场、XYZ 归零、Pump/Probe 光功率和温控开启 | A03 |
| A05 | 可选 TEC 仅写目标温度 | A04 |
| A06 | Pump 连续载波和常开门控 | A05 |
| A07 | SDS DC、FSRate、固定零 offset 与采样/量程回读 | A06 |
| A08 | X 外层、Y 正向网格，重复与自适应量程逐次门控保存 | A07 |
| A09 | 本轮安全恢复与封存，恢复失败不得报告完成 | A08 |
| A10 | 安全结束会话、释放硬件锁 | 无，必须最后执行 |
| B01 | 原始格式、完整网格、重复次数、波形与统计量资格 | 无 |
| B02 | 原二维 Bloch／线性漂移拟合和逐 X 色散交叉验证 | B01 |
| B03 | 从 B02 数值产物生成原科研图和 JSON/YAML 报告，不重拟合 | B02 |

执行清单、依赖、参数归属和质量门槛以 `stages.py` 为准，可下载同目录可编辑 `workflow.excalidraw`。缺少显式前置时拒绝执行，不自动补选。SDS 记录时长在 A07/A08 共享；修改从 A07 起失效，必须重新准备 SDS。初始化条件仅在实际设置节点编辑；分类移动同步各入口，不改变参数值。

空闲编辑先形成草稿及预计重跑范围，“应用参数到会话”后才采用后端状态，仍需显式重跑，不自动设置输出。保存默认值、预检均不会替用户执行步骤。每次重跑扫描新建目录，保留前轮 raw 和配置；A09 恢复改变仪器模式，再次扫描必须从 A04 重新准备。收尾采用最近实际执行的 Pump 设置，不采信尚未重跑的草稿。

分步等待保持连接与输出并占用硬件锁；空闲时用 A10／结束会话，执行中用取消。异常、Ctrl+C、父进程 EOF 和后端关闭均自动安全收尾，完成收尾后才释放锁。GUI 完整运行继续使用同一业务步骤，自动收尾并自动离线分析；Python 采集脚本仍只采集，离线分析另行显式指定目录。

“历史”读取对应运行快照与结果，可填回参数、重新分析和下载；填回不自动保存或启动。分析列可选择下一次实验或具体已结束目录；本实验没有额外分析覆盖参数，历史参数和 raw 保持只读，当前默认值不替代历史快照。输入／产物内容指纹变化使相关阶段失效；重算只归档受影响产物，B03 重绘不移动 B02 数值输入。无有效 Bloch 中心时保存质量失败诊断并阻断 B03，不伪造补偿值。

## 原始数据

```text
data/Mx_XY_Residual_Field_Calibration/<run>/
  experiment_config.yaml
  raw/
    xy_residual_scan_axes.npz
    grid_X000_Y000_R000.npz
    ...
  results/
```

每个采集文件保存完整时间轴和电压波形、实际采样率、PD 均值与标准差、主场及 X/Y
设定、输出状态、采集时间、顺序和自动量程诊断。新运行独立记录 `raw_format_version: 1`，参数 `schema_version: 3` 与稳定外部键不变。无 raw 标记的参数 v1/v2/v3 历史经身份、文件结构和完整网格检查后兼容，不改写原快照。故障帧保存为 `recovered_X..._attempt....npz`，与正式 `grid_...` 隔离，不作为有效拟合输入。

## 离线分析

采集入口保持 `experiments/Mx_XY_Residual_Field_Calibration.py`；离线入口必须显式指定运行目录，禁止隐式选择最新历史：

```powershell
agent_exp_env/Scripts/python.exe experiments/Mx_XY_Residual_Field_Calibration_plot.py data/Mx_XY_Residual_Field_Calibration/<run>
```

准备、网格采集、科学拟合、绘图分别位于实验包的 `preparation.py`、`acquisition.py`、`fitting.py`、`analysis_plotting.py`；`workflow.py` 和 `analysis.py` 只负责编排与阶段记录。B02 保存 `calibration_results.npz`、两份 CSV 和 `xy_residual_fit.json`；B03 导出原科学图及 `analysis.json/yaml`。

结果图统一通过 `lab_workflows.plotting` 的 `paper` 配置导出同名 PNG（300 dpi）和 PDF；原 PNG 文件名继续用于历史预览。默认固定画布，英文标注，图幅和导出约定见[科研绘图规范](../plotting_style.md)。重新分析生成新图，原始数据和数值结果字段保持原有契约。

分析器直接使用每个网格点的原始 PD 均值及实际采集时间：

```text
PD_mean(X,Y,t) = C + D*(t-t0) + A*v/(1+u^2+v^2)
```

其中 `D` 是全局线性时间漂移，`t0` 是全部网格采集时间的平均值。该干扰项用于降低
无强磁基准时缓慢零点漂移对 X/Y 曲面的偏置。分析器同时保存原始均值、拟合漂移、
漂移修正均值、Bloch 空间拟合面、总拟合面和残差。

SDS CH1 的 offset 在整个实验中固定为 0 V，不执行自动 offset 居中。每个网格点及
每次重复采集都根据当前波形自适应缩小或放大 scale；若量程改变，则在同一网格点重新
执行完整温控门控并采集，直到量程合适或达到最大尝试次数。

Pump 沿 Z、Probe 测量 `Px`。在 `Bz≈0`、各向同性稳态近似下，空间部分采用：

```text
PD_spatial(X,Y) = C + A*v/(1 + u^2 + v^2)
u = (X-X*)/Wx
v = (Y-Y*)/Wy
```

`X*`、`Y*` 是连续补偿中心，`Wx`、`Wy` 分别吸收有效弛豫尺度和两路线圈的
电压-磁场换算。分析使用多初值 `soft_l1` 稳健最小二乘；主结果为连续的 `X*`、
`Y*`，`best_x_field_v` 和 `best_y_field_v` 则是离该中心最近的实测网格点，不进行
网格外推。结果显式保存 `bz_assumption: approximately_zero`、二维拟合面、残差面、
RMSE、决定系数和边界诊断。拟合中心靠近扫描边界、宽度触及约束或 `R²<0.8` 时，
`quality_warnings` 会给出提示。

作为交叉验证，分析器还会对每个固定 X 用稳健最小二乘拟合 Y 色散线：

```text
delta(Y) = offset + amplitude * 2*gamma*(Y-Y0) / ((Y-Y0)^2 + gamma^2)
```

其中 `Y0` 是 Y 平衡中心，`2*gamma` 是正负峰间距，中心斜率为
`2*abs(amplitude)/gamma`。逐 X 结果按中心斜率最大选择交叉验证点；幅度最大和
线宽最小用于检查其是否指向同一 X 条件。二维 Bloch 与逐 X 方法的 X/Y 差值会写入
结果，所有逐 X 参数和拟合质量保存至 CSV 和 NPZ，但不再决定主平衡点。

结果保存扁平 CSV、原始和漂移修正均值矩阵、最佳 X/Y、线性漂移率及拟合质量。
`pd_mean_surface.png` 显示原始 PD 均值三维曲面，不再包含强磁基准面；
`bloch_2d_fit.png` 并列显示漂移修正测量面与二维 Bloch 拟合面，
`bloch_2d_residual_map.png` 显示总模型残差，`fitted_time_drift.png` 显示拟合的线性漂移。

## 安全结束状态

正常完成、取消、异常和 Ctrl+C 均恢复 GS200 的源模式、电流、输出、量程和限流；
X/Y/Z 归零关闭，SDS 停止并恢复 DC 耦合，温控恢复为 5 V DC + Output ON。最佳
补偿值只写入结果，不留在硬件输出上。Pump/Probe 光功率与最近实际执行的 Pump 连续载波／常开门控保留；不因未执行草稿重配输出。正常结束仅断开 TEC；时钟或部分连接失败时释放已连接设备。恢复或断开失败单独记录，不能冒充安全成功，也不能覆盖原始采集异常。
