---
title: Z 任意波实际电流响应滤波闭环校正
type: Z_AW_Closed_Loop_Waveform_Correction
scan_mode: point_by_point
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
  - schema v11 由独立 AW 频响标定构造周期逆滤波核；闭环内不做辨识测试。
  - 每帧只做整体时间平移评分，误差先换回命令坐标再逆滤波。
  - 理论采样电阻电压乘 1.5 倍余量选择 CH3 档位，居中后采集。
  - 误差增大仍继续，不回退或自动减小更新系数。
  - 最终命令必须对应实际采集轮次，不另行滤波。
experiment_id: z-aw-closed-loop-waveform-correction
defaults_file: params/experiments/z-aw-closed-loop-waveform-correction.yaml
---

# Z 任意波实际电流闭环校正（10 kHz 高通链路）

当前默认参数：[参数文件](../params/experiments/z-aw-closed-loop-waveform-correction.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

## 当前推荐方法：harmonic_jacobian

当 DG4000 CH1 与线圈之间串接 10 kHz 高通滤波器时，旧的静态逆滤波会把滤波器的幅度和相位当成未知延时，导致 v6/v7 的三次谐波相位错误（历史实测误差分别约 17.7% 和 11.1%）。选择 `CORRECTION_METHOD=harmonic_jacobian` 时，只用 SDS CH3 采样电阻电压和 CH4 共同触发：

1. 以 CH4 下降沿作为唯一时间零点，连续采集基线并关闭/重新上传检查重复性；不对每帧作目标对齐。
2. 从目标自动选出主要交流谐波（相对幅度门槛与数量上限取自参数文件），分别对 cos/sin 系数施加正负小扰动，中心差分得到复数 4×4、6×6 等局部响应矩阵。
3. 用 SVD 正则化逆计算候选命令；候选必须比已接受命令改善超过重复噪声的三倍标准误和最小改善门限，否则回退并将步长减半，最多 `MAXIMUM_BACKTRACKS` 次。
4. 周期性重新辨识，持续检查幅度、相位、完整波形和峰值电流/采样电阻功率。掉帧、触发丢失、重载后漂移会终止学习并保留原始数据。
5. 关闭输出后重新上传最佳命令，使用独立 5 帧验证；只有通过重载验证才生成可复用的 `format_version=4` 冻结文件。

高通链路不能产生直流。目标的平均电流超过 `1e-6 × RMS` 时预检直接失败，不能在软件中偷偷去均值；`oc_broadband_v2/v12` 的约 9 kHz 直流分量应改用独立直流线圈或暂不进行高通闭环。

CH3/CH4 接线保持：CH3 接低端采样电阻，CH4 接 DG4000 CH2 的共同触发。频响实验暂时没有 CH1 驱动参考时，不把 CH4 电压当作 I/V 分母；谐波辨识直接测量“命令→实际电流”的复响应，适用于 Burst 和高通均保持不变的条件。

稳定 ID 为 `z-aw-closed-loop-waveform-correction`，采用 `typed_workflow`。
入口为 `experiments/Z_AW_Closed_Loop_Waveform_Correction.py`，离线入口为同名 `_plot.py`。

## 2026-09-15 实测与离线恢复

运行 `0915_142015_z_aw_closed_loop_waveform_correction` 使用的实际目标阈值是 5%，该数值属于本次运行快照，不代表当前默认阈值。40 kHz 和 120 kHz 两个目标谐波的局部辨识闭环，从基线相对 RMS 误差 116.10% 降到最后三次同命令独立测量的 3.61%、3.43%、3.41%；关闭输出并重新上传后，最终验证为 3.56%（误差 RMS 2.788 mA，目标 RMS 78.420 mA）。因此按这次运行的 5% 阈值达标，但尚未达到 3% 更严格目标。

该次运行在写轮次 YAML 时因 NumPy 标量无法被 PyYAML 表示而中断，原始的 `raw/final_validation/validation.json` 还错误沿用了第 8 次测量的带内／带外等诊断值。今后的闭环记录已修复这两处问题；本次原始文件保持不变，`results/` 从已保存的反馈、辨识矩阵和命令离线恢复。准确的最终帧误差分解及恢复依据见 `results/recovery_provenance.yaml`，收尾时的设备关闭状态没有留存记录，不能由离线恢复推定。

## 数据与单位

理论波形继续从 `D:/Code/theory_agent/simulate/results/<CONTROL_SOURCE_SET>/<CONTROL_VERSION>/`
下的 `waveforms/optimal_control_waveform.csv` 和 `parameters/optimal_control_params.csv` 读取。
控制耦合强度单位为 Hz，时间单位按来源文件约定解析。

读取两个独立来源并校验一致性：

- `CURRENT_COUPLING_CALIBRATION_SOURCE_RUN` 的 `results/analysis.yaml`：
  全部 `curves` 的 `z_bias_v` 与 `current_a` 做带截距普通最小二乘拟合
  `I = g*u + b`，斜率保留符号，作为直流增益和截距。
- `CURRENT_FREQUENCY_RESPONSE_SOURCE_RUN` 的 `results/frequency_response.npz`：
  只接受对数扫频正弦协议（`output_protocol=coherent_swept_sine_current_response`、
  `format_version=4`、`command_voltage_reference=50_ohm`、
  `phase_reference=measured_drive_voltage`）的标定；
  两个来源的采样电阻阻值必须一致，否则预检失败。

目标电流 `I_target = CONTROL_SCALE * Omega_ctrl_Hz / K_Z_Hz_per_A`；
实测电流 `I_measured = V_CH3 / sense_resistor_ohm`。

## 周期滤波核

每次运行按当前周期点数 `N`、采样间隔 `dt` 和重复频率重建频域核：

```text
L(f) = 1/g                                   (f = 0)
L(f) = H*(f) / (|H(f)|^2 + lambda^2)         (0 < f <= 有效学习上限)
L(f) = 0                                     (f > 有效学习上限)
```

- 直流仅使用静态斜率 `g`，不从交流频点外推。
- 交流 `H[k]` 由独立扫频标定在目标波形周期网格的每个谐波上插值得到：
  只在该谐波两侧**相邻的可靠扫频点**之间按对数幅度与展开相位线性插值。
- 不可靠扫频点会切断插值区间；可靠覆盖之外既不插值也不外推，相关谐波不参与修正。
- `lambda = INVERSE_REGULARIZATION * max(|H|)`，最大值取直流增益与全部有效学习频点。
- 学习频带按可靠覆盖**自动收缩**：请求上限是 `ERROR_CUTOFF_HZ`，实际生效上限是被可靠
  覆盖的最高谐波频率；日志与运行配置会报告收缩后的频带、可靠覆盖范围和未覆盖谐波个数。
- 学习频带内没有任何被可靠覆盖的谐波时预检失败；带外 `H=L=0` 只表示不学习、不预测，
  带外误差仍完整计入评分。

预检同时展示目标带外 RMS，供用户判断所选学习带宽。运行把 `frequency_hz`、`H`、`L`、
正逆时域核和来源快照保存到 `raw/response_filter.npz`，支持离线精确复现。

## 对齐、评价和更新

CH4 下降沿定义每帧时间参考。截取完整周期重采样平均后：

1. 每帧对目标做整体平移对齐（评分定义不变：不缩放、不去均值），得到该帧的实测时移。
2. 将对齐后的误差按同一个实测时移反向移回命令坐标，逐帧换回后平均。

```text
error_command = shift_bandlimited(target - measured_aligned, -measured_shift, band)
correction = IRFFT(L * RFFT(mean(error_command)))
increment = alpha * correction
```

正向预测 `predicted = IRFFT(H * RFFT(command)) + b` 用于保存诊断，
不参与达标指标。完整波形相对 RMS 误差仍是唯一达标指标；
带内／带外误差拆分和预测残差只作诊断保存。

## 可执行迭代规则

固定步长主循环与 schema v11 相同：连续 `REQUIRED_PASSES` 轮达标、最大轮数、
下一轮候选电压越界（不上传）、取消和硬件失败是全部停止条件。
误差增大仍从当前命令继续，无回退、无减半、无恶化停止。
候选命令只在构造入口做一次数值与固定 DG 电压范围检查。

从已实测轮次中选相对 RMS 最小者导出；导出命令不再滤波、不重新对齐、不重新选相位，
没有独立 hold-out 采集。无有效轮次时不导出虚构波形。

## 文件与离线分析

```text
data/Z_AW_Closed_Loop_Waveform_Correction/<run>/
  experiment_config.yaml
  raw/
    theory_waveform.csv
    theory_parameters.csv
    static_calibration_source.yaml
    aw_frequency_response_source.npz
    response_filter.npz
    target.npz
    iteration_000/
      command.npz
      output_readback.json
      scope_capture_000_attempt_00.npz
      scope_capture_000.npz
      alignment_000.npz        # 含评分平移量与模型平移量
      feedback.npz             # 含命令坐标误差、修正电压、学习带掩码
      update.npz               # 只有实际提出下一轮命令时保存
  results/
    iteration_summary.yaml     # 含带内/带外误差 RMS
    corrected_control_waveform.npz
    corrected_control_waveform.csv
    convergence.png
    waveform_comparison.png
    final_waveform_report.yaml
```

离线分析直接使用保存的反馈和指标，不重新对齐、归一化或改写命令；按
`correction_method` 分派：`response_filtered_feedback` 走当前分析，
`static_current_feedback` 走 schema v10 分析，更早运行走 `legacy_analysis.py`。
图形统一使用 `lab_workflows.plotting` 的 paper 样式，标注为英文。

冻结 NPZ 使用 `format_version=3`，主波形数组含义不变，新增响应来源与哈希、
`error_cutoff_hz` 和 `inverse_regularization`；共享读取器集中兼容 v1/v2/v3，
使用冻结波形的实验不增加算法分支。

## 配置与历史兼容

schema v11 恢复 `CURRENT_FREQUENCY_RESPONSE_SOURCE_RUN` 为必填来源（只接受新 AW 协议
标定，未指定时明确提示补选），新增 `INVERSE_REGULARIZATION`（取值见参数文件）。
旧键迁移不再删除这两个字段；`ERROR_CUTOFF_HZ` 语义为最高学习频率，不固定限制为
80 kHz，但必须严格低于命令网格与实际采样奈奎斯特频率。其余迁移规则与 v10 一致：
正 `TIME_DOMAIN_CUTOFF_HZ` 迁为 `ERROR_CUTOFF_HZ`，阈值统一 `TARGET_RELATIVE_RMS=0.03`。

新运行不保留 `static/filtered` 模式选择和静态回退分支。历史原始数据与冻结文件不改写，
兼容处理集中在读取／分析入口。旧流程说明见
[历史 schema v9 文档](old/z_aw_closed_loop_waveform_correction_schema_v9.md)。

同一响应复用的前提是硬件链路与工作条件稳定且近似线性；记录中的设备与阻值可程序核对，
外部接线变化不能由文件自动识别。实机未达标时如实保存诊断，不通过调整评分宣称收敛。
