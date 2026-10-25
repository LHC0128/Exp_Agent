# 历史 Python 分析绘图入口

这些入口已接入 `lab_workflows.plotting` 的 `paper` 配置，统一创建画布和导出，
默认生成同名 PNG（300 dpi）与 PDF。它们仍保留原有脚本参数和数据格式，
不代表已迁移为 `typed_workflow`。统一规则见[科研绘图规范](plotting_style.md)。

## 读取与运行

多数旧 `*_plot.py` 在脚本顶部设置 `DATA_DIR`，指向实际的
`data/<实验类型>/<运行目录>/`，从其 `raw/` 读取数据，向同级 `results/` 写入结果。
运行前检查脚本的参数区：有的保留具体历史目录，有的使用 `MMDD_HHMM_*` 占位符，
不能默认它们会自动选择最新一次实验，也不能默认都接受命令行运行目录参数。

光子散粒噪声已迁移为强类型模块化流程，不再修改 `DATA_DIR` 或自动选择 latest；
在仓库根目录显式传入运行目录：

```powershell
agent_exp_env/Scripts/python.exe experiments/Photon_shot_noise_plot.py data/Photon_shot_noise/<run_id>
```

独立 Bloch 工具支持明确指定输入和独立输出目录：

```powershell
agent_exp_env\Scripts\python.exe experiments\Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance_Bloch_fit.py <运行目录> --output-dir <输出目录>
```

上述离线入口无需仪器连接。重分析会覆盖目标目录中的同名结果；人工编辑的图需另存。
原始数据不应为改变图形外观而重写。不要运行采集脚本来代替已有数据的离线重绘。

## 入口及图类

除已标注完整归档路径的入口外，以下文件位于 `experiments/`；其它既有归档位置见[归档清单](../old/experiments/README.md)。恒定 Omega／AWScale 标定检查、XY DC／AW 电压标定、全局静磁场优化、DG AM 传递检查及温控开关 PID 周期测试的 GUI 仅保留卡片，专属脚本移走但原 `data/` 与 `results/` 不动；下表保留其离线分析用途。

| 离线入口 | 主要图类与用途 |
|---|---|
| `old/experiments/experiments/AW_ConstOmega_Calibration_Check_plot.py`、`old/experiments/experiments/AW_ConstOmega_Calibration_Check_AWScale_plot.py`（均已归档） | 控制频率标定曲线、响应峰与换算残差 |
| `BB_Rpump_DutyScan_Burst_plot.py`（已迁移，须显式 run_dir） | 原 Hilbert 建立/振荡暗衰减拟合、有效泵浦率及五组图；分阶段重算归档，见[独立说明](experiments/bb_duty_scan_burst.md) |
| `BB_Rpump_PowerScan_Burst_plot.py`（已迁移，须显式 run_dir） | 保留原 Hilbert 建立/全振荡暗衰减拟合、有效泵浦率及五组图；3 阶段独立重算与产物归档，见[独立说明](experiments/bb_power_scan_burst.md) |
| `old/experiments/experiments/DG_AM_to_DG_COMP_Transfer_Check_plot.py`（已归档） | 外部 AM 输入电压与输出幅值关系、饱和边界 |
| `Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance_Bloch_fit.py` | 复响应面拟合、简化 Bloch 对比；支持已完成 Z 平面的中断运行数据 |
| `Noise_Spectrum_XY_Ctrl_v2_plot.py` | PSD 矩阵、控制频率切片与噪声拟合 |
| `RF_Field_Sensitivity_AW_plot.py` | AW 射频响应、噪声与等效灵敏度 |
| `RF_Field_Sensitivity_ConstXY_FreqSweep_plot.py` | FFT、软件锁相与硬件解调频响对比 |
| `Photon_shot_noise_plot.py`（已迁移，须显式 run_dir） | ASD 标定、白噪声 PSD 与 AC RMS 对照，详见[光散粒噪声](experiments/Photon_shot_noise.md) |
| `T1_Calibration_plot.py`（已迁移，须显式 run_dir） | T₁、倒数弛豫率和衰减概览；3 阶段分析、指纹与产物归档，兼容无波形旧扫描，详见[T₁ 标定](experiments/T1_calibration.md) |
| `old/experiments/experiments/Temperature_Switch_PID_Cycle_Test_plot.py`（已归档） | 温度、偏差、开关状态及周期相位平均 |
| `old/experiments/experiments/XY_AW_Voltage_Calibration_plot.py`、`old/experiments/experiments/XY_DC_Voltage_Calibration_plot.py`（均已归档） | 响应峰位、电压标定及拟合残差 |
| `Static_Magnetic_Field_Sensitivity_Optimize_Local_plot.py`（已迁移，须显式批次目录） | 只读冻结单点结论，重建基准漂移、排名与原建议规则；旧 RAMP 批次不重新拟合，见[局部优化说明](experiments/static_sensitivity_optimize_local.md) |
| `Z_Field_Bandwidth_Measurement_plot.py`（已迁移，须显式 run_dir） | 保留 Z 方向 FFT／软件锁相／硬件 R、−3 dB 带宽及原诊断图；按阶段重算归档受影响结果，详见[Z 场带宽](experiments/z_field_bandwidth.md) |

## 采集中的诊断图

`old/experiments/experiments/AW_Control_Waveform_Scope_Check.py`（已归档，无独立离线分析入口）、
`old/experiments/experiments/DG_AM_to_DG_COMP_Transfer_Check.py`（已归档）、
`old/experiments/experiments/Temperature_Switch_PID_Cycle_Test.py`（已归档）和 `old/experiments/experiments/Static_Magnetic_Field_Sensitivity_Optimize.py`（已归档，无独立分析入口）内的诊断图也已使用公共接口。
这些归档脚本包含仪器操作；绘图接入没有把它们变为纯离线工具。局部优化现已分离采集与批次汇总薄入口，每个 DC 子运行安全恢复后即时调用标准离线分析，父图与报告由独立批次分析生成。
静磁场主实验现已改为直流逐点采集，图形生成统一移到离线分析器和
`Static_Magnetic_Field_Sensitivity_plot.py`，详见[当前实验说明](experiments/static_mag_sens_v2.md)。
`src/sds_acquisition/cli.py --plot` 同样是采集后的快速预览，详见
[示波器模块](instruments/sds_acquisition.md)。

## 图形解释与现有限制

- BB 的低质量负有效泵浦率保留原值，不再为了显示补零；残差显示使用完整对称色限。
  占空比分析仍在上游将速率误差限制到速率绝对值的 100%，且该值参与后续权重和结果输出。
  本轮移除了绘图层的额外误差限制，但没有更改上游统计方法；不能把现有误差条直接宣称为
  未截断的标准不确定度。投稿前需审定这一估计方法。
- 物理含义不同的量采用独立坐标轴。静磁场参数优化将灵敏度、HWHM、斜率、R² 分成
  四个面板，详见[静磁场灵敏度](experiments/static_mag_sens_v2.md)。
- 全扫描概览保留全部条件，可能产生长图；投稿需按科学问题选面板或分页，不能靠缩小字体容纳。
- BB 占空比已补[独立实验说明](experiments/bb_duty_scan_burst.md)，入口改为显式 run_dir；其余 BB 功率、AW 检查、DG 传递和温控等旧入口的历史说明仍有限。本文补足绘图
  入口和输出契约，不替代接线、安全、物理模型的完整实验文档。

本轮没有修改 Notebook，也未全量执行每一种历史分析。实际验证范围见
[绘图检查报告](plotting_audit_20260920.md)。
