---
title: XY DirectAW 恒定包络电压标定
type: XY_DirectAW_DC_Calibration
execution_mode: typed_workflow
scan_mode: nested_scan
defaults:
  XY_ENV_VOLTAGE_START_V: -4.0
  XY_ENV_VOLTAGE_STOP_V: 4.0
  XY_ENV_VOLTAGE_POINTS: 17
  XY_AW_OUTPUT_VPP: 4.0
  XY_AW_OUTPUT_OFFSET_V: 0.0
  Z_RF_FREQ_START_Hz: 500.0
  Z_RF_FREQ_STOP_Hz: 35000.0
  Z_RF_FREQ_STEP_Hz: 500.0
  ACQUISITION_MODE: both
  PUMP_MOD_FREQ_Hz: 10000.0
  XY_PHASE_CAL_TOL_deg: 0.5
  XY_PHASE_CAL_MAX_ITER: 8
  XY_PHASE_CAL_MIN_R_V: 1.0e-12
  XY_PHASE_CAL_MIN_R_RATIO: 0.1
mapping_keys:
  X_magnetic_field:
    role: scan
  Y_magnetic_field:
    role: scan
  Z_magnetic_field:
    role: detection
  X_magnetic_field_AM:
    role: fixed
  Y_magnetic_field_AM:
    role: fixed
required_devices:
  - GS200
  - DG4000
  - DG900
  - TEC103
  - HF2
learned_notes:
  - DirectAW 由 dg_comp CH1/CH2 直接输出，不经过 dg_am 外部 AM 调制。
  - dg_trigger 在 DirectAW 校相前打开并同相初始化，之后持续输出；每轮先开 dg_comp Burst，再开 dg_comp Output，迭代期间不重启触发源。
  - Demod0 与 DirectAW 校相分别调用 lab_workflows.steps 中的共享步骤；DirectAW 的低 R 样本不参与相位更新。
  - 负包络电压等效于 X/Y 正交旋转场整体翻转 180 度，分析时应同时检查有符号和绝对值映射。
  - 任意波的 amplitude 与 offset 固定为 4 Vpp 和 0 V，扫描只改变上传数组。
  - 0713 数据中 20 kHz 为固定相干杂散；离线标定默认屏蔽 19--21 kHz，并将邻近峰标记为低可信度。
  - 电压增大使真实峰到达扫频上边界后，从该 |V_env| 起及更高电压点全部排除出标定拟合，避免误用边界或次峰。
---

# XY DirectAW 恒定包络电压标定

## 目的

在 `RF_Field_Sensitivity_AW_FreqSweep_DirectAW.py` 的物理链路下，标定 DirectAW
恒定包络电压与 Z 向小信号响应峰位之间的关系。实验区别于既有
`XY_DC_Voltage_Calibration.py`：既有脚本通过 `dg_am → dg_comp AM EXT` 施加控制，
本实验由 `dg_comp` 直接输出 X/Y 正交任意波。

本实验已迁移到强类型新模式。参数模型、采集工作流和离线分析器分别位于
`lab_workflows/experiment_modules/xy_direct_aw_dc_calibration/` 的 `models.py`、
`workflow.py` 和 `analysis.py`；`experiments/` 中两个同名脚本仅为命令行薄入口。
GUI/YAML 继续使用原有大写参数键。

## DirectAW 定义

```text
X(t) = V_env cos(2π f_L t + φ)
Y(t) = V_env cos(2π f_L t + φ + 90°)
```

- `V_env`：扫描变量；当前保存值为 −4 V 到 +4 V，共 17 点。
- AW 固定输出：4 Vpp、0 V offset。
- Pump 调制频率：默认 10 kHz，可在实验中心的基础参数中设置；HF2 Demod0 参考和
  DirectAW 载波同步跟随该频率。
- AW 重复频率：500 Hz；Pump/DirectAW 频率必须是 500 Hz 的整数倍，默认每周期包含
  20 个 10 kHz 载波周期。
- `dg_trigger`：100 Hz 同相方波外触发，两次触发之间恰好包含 5 个 AW 周期。
- 校相触发顺序：先打开并同相初始化 `dg_trigger`，之后保持连续输出；每次上传 DirectAW 后依次
  打开 `dg_comp` Burst 和 Output，等待后续外触发正沿，再读取 Demod0 theta。
  校相迭代期间不关闭或重新初始化 `dg_trigger`，避免人为制造额外触发沿。
- 相位算法：HF2 Demod0 使用共享 `calibrate_demod_phase()`；DirectAW 使用共享
  `calibrate_direct_aw_phase()`。DirectAW 只在 `R` 同时达到绝对下限和历史最佳值的
  10% 时接受 theta 并更新相位，避免弱信号下的随机相位把迭代带偏。相关阈值均在
  GUI 高级参数中可调。

当前扫描保存值超出 `4 Vpp / 0 V offset` 可表达的 `[-2 V, +2 V]`，因此新模式
预检会阻止启动。运行前必须明确调整扫描范围或重新确认 AW 输出设置，不能绕过预检。

## 数据与分析

每个包络电压下扫描 Z RF 频率并同时保存：

- Demod 0 `sample.y` 时域数据，用于离线锁相和 FFT；
- Demod 3 的 X/Y/R 采样，用于交叉验证；
- 当前上传的 DirectAW X/Y 波形快照。

离线分析输出四类模型：正电压支路、负电压支路、有符号全局直线以及
`f_peak = K_abs |V_env| + B_abs`。应根据 R²、残差和 `f(+V)-f(-V)` 的对称性判断
最终用于波形换算的模型，不应只根据单一线性拟合下结论。

当前主标定优先使用 Demod3 硬件复矢量均值，并在峰值拟合前屏蔽已确认的
19--21 kHz 固定相干杂散。屏蔽前最大频点、实际删除频点和邻近屏蔽带标记会同步
保存到 `analysis.yaml` / `analysis.json`，以便追溯。该处理可用于当前数据的初步标定，
但真实峰经过屏蔽带时仍会丢失信息，最终标定应增加 Z-RF OFF 背景并对复数
`X+iY` 做 ON-OFF 扣除。

## 入口

- 采集：`experiments/XY_DirectAW_DC_Calibration.py`
- 分析：`experiments/XY_DirectAW_DC_Calibration_plot.py`

## 统一安全收尾

正常完成、异常、取消和 Ctrl+C 均调用共享 `run_safety_shutdown()`：归零并关闭
Z 场、X/Y DirectAW 和触发通道，恢复温度开关，只断开 TEC；主磁场、Pump/Probe
光功率、Pump 调制和全部 HF2 设置保持不变。连接阶段失败则释放已建立的全部连接。
