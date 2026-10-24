---
title: XY DirectAW 恒定包络电压标定
type: XY_DirectAW_DC_Calibration
execution_mode: typed_workflow
scan_mode: nested_scan
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
  - HF2
learned_notes:
  - TEC103 为可选控制设备；设备库映射串口被外部温控软件占用时跳过设温和稳定等待。
  - DirectAW 由 dg_comp CH1/CH2 直接输出，不经过 dg_am 外部 AM 调制。
  - dg_trigger 在 DirectAW 校相前打开并同相初始化，之后持续输出；每轮先开 dg_comp Burst，再开 dg_comp Output，迭代期间不重启触发源。
  - Demod0 与 DirectAW 校相分别调用 lab_workflows.steps 中的共享步骤；DirectAW 的低 R 样本不参与相位更新。
  - 负包络电压等效于 X/Y 正交旋转场整体翻转 180 度，分析时应同时检查有符号和绝对值映射。
  - AW 满量程自动跟随包络峰值（vpp = 2 × max|V_env|，offset 恒 0），扫描只改变上传数组；零包络点上传全零波形并保持输出 OFF，自动幅度低于仪器最小 0.001 Vpp 时拒绝。
  - 0713 数据中 20 kHz 为固定相干杂散；离线标定通过配置的屏蔽频带处理该杂散，并将邻近峰标记为低可信度。
  - 电压增大使真实峰到达扫频上边界后，从该 |V_env| 起及更高电压点全部排除出标定拟合，避免误用边界或次峰。
experiment_id: xy-direct-aw-dc-calibration
defaults_file: params/experiments/xy-direct-aw-dc-calibration.yaml
---

# XY DirectAW 恒定包络电压标定

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。校相、扫描和噪声共用该等待；组首配置前移，组内每次换点或换态后重新等待，保留原分组和 OFF/ON 配对边界。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


## 锁相采集方式与实际速率

XY 电压标定使用 `DEMOD0_RATE_Sa_s` 请求率、`POINT_DURATION_s` 时长和 `DEMOD0_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/xy-direct-aw-dc-calibration.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

锁相设备在「仪器控制 → 设备库 → 锁相放大器」中全系统统一选择。**本实验当前接线需要两路独立主电压输入，暂时只支持 HF2；选用 MFLI 时预检禁止启动。**MFLI 的 Aux Input 回采尚未验证，程序不会自动改接线、降频或回退 HF2。该限制不影响已有 HF2 数据的离线分析。接口差异见 [锁相接口说明](lockin_amplifier.md)。

## 目的

在 `RF_Field_Sensitivity_AW_FreqSweep_DirectAW.py` 的物理链路下，标定 DirectAW
恒定包络电压与 Z 向小信号响应峰位之间的关系。实验区别于既有
`XY_DC_Voltage_Calibration.py`（现已归档于 `old/experiments/experiments/`）：既有脚本通过 `dg_am → dg_comp AM EXT` 施加控制，
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

- `V_env`：扫描变量；范围与点数读取参数文件。
- AW 满量程自动跟随包络峰值（`vpp = 2 × |V_env|`，offset 恒 0）；零包络点上传全零波形并保持输出 OFF，非零包络过小（自动幅度低于仪器最小 0.001 Vpp）时预检拒绝。
- Pump 调制频率：读取参数文件，可在实验中心的基础参数中设置；HF2 Demod0 参考和
  DirectAW 载波同步跟随该频率。
- AW 重复频率：读取参数文件；Pump/DirectAW 频率必须是其整数倍，每周期载波数由频率比确定。
- `dg_trigger`：100 Hz 同相方波外触发，两次触发之间恰好包含 5 个 AW 周期。
- 校相触发顺序：先打开并同相初始化 `dg_trigger`，之后保持连续输出；每次上传 DirectAW 后依次
  打开 `dg_comp` Burst 和 Output，等待后续外触发正沿，再读取 Demod0 theta。
  校相迭代期间不关闭或重新初始化 `dg_trigger`，避免人为制造额外触发沿。
- 相位算法：HF2 Demod0 使用共享 `calibrate_demod_phase()`；DirectAW 使用共享
  `calibrate_direct_aw_phase()`。DirectAW 只在 `R` 同时达到绝对下限和历史最佳值的
  10% 时接受 theta 并更新相位，避免弱信号下的随机相位把迭代带偏。相关阈值均在
  GUI 高级参数中可调。

若当前扫描范围超出 AW 幅度与 offset 可表达的范围，预检会阻止启动。运行前必须明确调整扫描范围或重新确认 AW 输出设置，不能绕过预检。

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
Z 场、X/Y DirectAW 和非时序触发通道，恢复温度开关；`Time_sequence`、`Time_sequence_2`
保持当前状态，只断开 TEC；主磁场、Pump/Probe 光功率、Pump 调制和全部 HF2 设置保持不变。
连接阶段失败则释放已建立的全部连接。
