# 历史 Python 分析绘图入口

这些入口已接入 `lab_workflows.plotting` 的 `paper` 配置，统一创建画布和导出，
默认生成同名 PNG（300 dpi）与 PDF。它们仍保留原有脚本参数和数据格式，
不代表已迁移为 `typed_workflow`。统一规则见[科研绘图规范](plotting_style.md)。

## 读取与运行

多数旧 `*_plot.py` 在脚本顶部设置 `DATA_DIR`，指向实际的
`data/<实验类型>/<运行目录>/`，从其 `raw/` 读取数据，向同级 `results/` 写入结果。
运行前检查脚本的参数区：有的保留具体历史目录，有的使用 `MMDD_HHMM_*` 占位符，
不能默认它们会自动选择最新一次实验，也不能默认都接受命令行运行目录参数。

例如，将 `experiments/Photon_shot_noise_plot.py` 的 `DATA_DIR` 指向要分析的运行后，
在仓库根目录执行：

```powershell
agent_exp_env\Scripts\python.exe experiments\Photon_shot_noise_plot.py
```

独立 Bloch 工具支持明确指定输入和独立输出目录：

```powershell
agent_exp_env\Scripts\python.exe experiments\Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance_Bloch_fit.py <运行目录> --output-dir <输出目录>
```

上述离线入口无需仪器连接。重分析会覆盖目标目录中的同名结果；人工编辑的图需另存。
原始数据不应为改变图形外观而重写。不要运行采集脚本来代替已有数据的离线重绘。

## 入口及图类

以下文件均位于 `experiments/`；表中说明根据现有脚本的数据读取与绘图流程整理。

| 离线入口 | 主要图类与用途 |
|---|---|
| `AW_ConstOmega_Calibration_Check_plot.py`、`AW_ConstOmega_Calibration_Check_AWScale_plot.py` | 控制频率标定曲线、响应峰与换算残差 |
| `BB_Rpump_DutyScan_Burst_plot.py`、`BB_Rpump_PowerScan_Burst_plot.py` | 泵浦建立/衰减拟合、有效泵浦率、速率分量、残差诊断 |
| `DG_AM_to_DG_COMP_Transfer_Check_plot.py` | 外部 AM 输入电压与输出幅值关系、饱和边界 |
| `Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance_Bloch_fit.py` | 复响应面拟合、简化 Bloch 对比；支持已完成 Z 平面的中断运行数据 |
| `Noise_Spectrum_XY_Ctrl_v2_plot.py` | PSD 矩阵、控制频率切片与噪声拟合 |
| `RF_Field_Sensitivity_AW_plot.py` | AW 射频响应、噪声与等效灵敏度 |
| `RF_Field_Sensitivity_ConstXY_FreqSweep_plot.py` | FFT、软件锁相与硬件解调频响对比 |
| `Photon_shot_noise_plot.py` | ASD 标定、白噪声 PSD 与 AC RMS 对照，详见[光散粒噪声](Photon_shot_noise.md) |
| `T1_Calibration_plot.py` | T₁、倒数弛豫率和衰减概览，详见[T₁ 标定](T1_calibration.md) |
| `Temperature_Switch_PID_Cycle_Test_plot.py` | 温度、偏差、开关状态及周期相位平均 |
| `XY_AW_Voltage_Calibration_plot.py`、`XY_DC_Voltage_Calibration_plot.py` | 响应峰位、电压标定及拟合残差 |
| `Z_Field_Bandwidth_Measurement_plot.py` | Z 方向频响及 −3 dB 带宽 |

## 采集中的诊断图

`AW_Control_Waveform_Scope_Check.py`、`DG_AM_to_DG_COMP_Transfer_Check.py`、
`Temperature_Switch_PID_Cycle_Test.py`、`Static_Magnetic_Field_Sensitivity_Optimize.py` 和
`Static_Magnetic_Field_Sensitivity_Optimize_Local.py` 内的诊断图也已使用公共接口。
这些脚本包含仪器操作；绘图接入没有把它们变为纯离线工具。
静磁场主实验现已改为直流逐点采集，图形生成统一移到离线分析器和
`Static_Magnetic_Field_Sensitivity_plot.py`，详见[当前实验说明](static_mag_sens_v2.md)。
`src/sds_acquisition/cli.py --plot` 同样是采集后的快速预览，详见
[示波器模块](sds_acquisition.md)。

## 图形解释与现有限制

- BB 的低质量负有效泵浦率保留原值，不再为了显示补零；残差显示使用完整对称色限。
  占空比分析仍在上游将速率误差限制到速率绝对值的 100%，且该值参与后续权重和结果输出。
  本轮移除了绘图层的额外误差限制，但没有更改上游统计方法；不能把现有误差条直接宣称为
  未截断的标准不确定度。投稿前需审定这一估计方法。
- 物理含义不同的量采用独立坐标轴。静磁场参数优化将灵敏度、HWHM、斜率、R² 分成
  四个面板，详见[静磁场灵敏度](static_mag_sens_v2.md)。
- 全扫描概览保留全部条件，可能产生长图；投稿需按科学问题选面板或分页，不能靠缩小字体容纳。
- BB、AW 检查、DG 传递和温控等旧入口缺少与当前实现对应的完整独立实验说明；本文补足绘图
  入口和输出契约，不替代接线、安全、物理模型的完整实验文档。

本轮没有修改 Notebook，也未全量执行每一种历史分析。实际验证范围见
[绘图检查报告](plotting_audit_20260920.md)。
