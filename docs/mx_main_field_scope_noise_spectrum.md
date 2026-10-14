---
title: Mx 主磁场示波器噪声谱
type: Mx_Main_Field_Scope_Noise_Spectrum
scan_mode: point_by_point
mapping_keys:
  main_magnetic_field:
    role: scan
  X_magnetic_field:
    role: fixed
  Y_magnetic_field:
    role: fixed
  scope_waveform:
    role: detection
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  temperature:
    role: fixed
required_devices:
  - GS200
  - DG900
  - DG4000
  - SDS
learned_notes:
  - TEC103 为可选控制设备；设备库映射串口被外部温控软件占用时跳过设温和稳定等待。
  - 主场标定公式在本实验中允许外推，但所有电流写入仍受 -10 到 10 mA 全局安全限值保护。
  - 示波器使用 AUTO 基础配置，PD 接 CH1；每帧由 FTRIG 强制采集，不使用 CH4 外部触发，也不等待 CH1 边沿。
experiment_id: mx-main-field-scope-noise-spectrum
defaults_file: params/experiments/mx-main-field-scope-noise-spectrum.yaml
---

# Mx 主磁场示波器噪声谱

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。校相、扫描和噪声共用该等待；组首配置前移，组内每次换点或换态后重新等待，保留原分组和 OFF/ON 配对边界。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


当前默认参数：[参数文件](../params/experiments/mx-main-field-scope-noise-spectrum.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

## 实验入口

- 采集：`experiments/Mx_Main_Field_Scope_Noise_Spectrum.py`
- 离线分析：`experiments/Mx_Main_Field_Scope_Noise_Spectrum_plot.py <run_dir>`
- 稳定实验 ID：`mx-main-field-scope-noise-spectrum`

该实验保持 Mx 主场噪声谱的连续 Pump、Probe、温控门控和辅助场安全策略，将检测链路从 HF2 Demod0 R 改为 SDS CH1 上的 PD 原始电压波形。扫描期间可保持固定的 X/Y DC 磁场，用于平衡剩磁。

## X/Y DC 剩磁补偿

- `FIXED_PARAMS.X_magnetic_field`：X 方向 DC 补偿电压，单位 V。
- `FIXED_PARAMS.Y_magnetic_field`：Y 方向 DC 补偿电压，单位 V。
- 任一参数严格等于 `0 V` 时，对应通道写入 `0 V` 后关闭输出。
- 任一参数为非零值时，对应通道切换为 DC 模式、写入该电压并在整段扫描期间保持输出开启。
- X/Y 两路分别使用 `X_magnetic_field`、`Y_magnetic_field` 安全限值校验，当前允许范围均为 `-10–10 V`。
- `schema_version <= 5` 的历史配置迁移时仍使用 `0 V / 0 V`，保持旧运行配置的 XY 关闭行为。

## 扫描定义

控制频率就是目标 Larmor 进动频率，起点、终点和点数读取参数文件，扫描轴严格递增。GS200 电流使用下式计算：

```text
f_Larmor = 9671.91741380711 × I_mA + 196.65637261343872
I_mA = (f_Larmor - 196.65637261343872) / 9671.91741380711
```

标定来源为 `Mx_Main_Field_Calibration/0720_124208_mx_main_field_cal`。本实验明确允许将该线性关系外推到低电流和负电流，但不会绕过 `main_magnetic_field=-10…10 mA` 的全局安全校验。

## 示波器采集与自动量程

SDS 使用固定采样率存储模式（SCPI `:ACQuire:MMANagement FSRate`），请求采样率与时间窗由参数文件指定；存储深度由示波器随时基自动调整。`SCOPE_AC_COUPLING` 打开时使用 AC 耦合，关闭时使用 DC 耦合。配置后会回读存储模式、实际采样率和实际点数，分析始终使用真实时间轴反算的实际采样率。示波器保持 `AUTO` 基础配置，只启用 PD 所在的 CH1；每次读取执行 `RUN → FTRIG 强制提交一帧 → 等待 FTRIG 完成 → STOP 并确认停止 → 读取波形`，因此不要求 CH1 出现满足条件的输入边沿。SDS1204X HD 实机在温控开关关闭条件下验证：`1 s`、500,000 点记录连续三次均完整返回，FTRIG 帧提交约需 `1.1–1.35 s`。读取前还会先把波形传输起点复位为 0，避免沿用上一轮分片读取状态。若 preamble 声明存在完整记录但 `DATA?` 返回零长度块，流程会对 SDS 执行至多一次软复位，等待恢复后重放当前采集配置和自动量程 scale/offset，再重新采集；该恢复只操作示波器，不改变磁场、光路或温控输出。若返回点数少于 preamble 声明值、Welch 最低点数或记录时长不足，会自动重新采集，连续三次失败才终止实验。

示波器垂直设置使用量程与 offset 联合自适应：

- 初始量程和偏置读取参数文件。
- 每次波形的中心取 `(waveform_min + waveform_max) / 2`；SDS offset 的符号与输入中心相反，因此目标偏置为该中心值的负数。
- 以半峰峰值 `(waveform_max - waveform_min) / 2` 作为相对中心的峰值偏差，避免 PD 直流基线占用量程判断空间。
- `SCOPE_VERTICAL_DIVISIONS` 表示垂直总格数，单侧范围为 `V/div × SCOPE_VERTICAL_DIVISIONS / 2`；峰值偏差按配置的高低比例阈值控制量程加倍或减半。
- offset 目标与当前值的差异超过 `0.05 × full_scale` 时才重新居中，避免噪声导致每个扫描点反复微调。
- 若波形已靠近当前显示边界且需要调整 offset，会先完成居中而不缩小量程，避免把可能截顶后的偏小峰峰值误判为量程过大。
- 量程上下限和尝试上限读取参数文件。
- scale 与 offset 的实际回读值沿用到下一个扫描点；若最后一次尝试仍建议调整，新设置供下一点使用，同时逐点文件会区分本次采集使用值和下一点状态。

每次示波器尝试前关闭温控并按配置等待，采集后在 `finally` 中恢复 `5 V ON` 并按配置等待。这些等待和扫描循环都响应取消。

## 原始数据

运行目录为：

```text
data/Mx_Main_Field_Scope_Noise_Spectrum/<run>/
  experiment_config.yaml
  raw/
    main_field_scope_noise_scan_axes.npz
    waveform_I0000.npz
    ...
  results/
```

逐点文件保存 PD 电压波形、真实时间轴、请求/实际采样率、实际记录时长、控制频率、主场电流、采集使用及下一点继承的 scale/offset，以及每次自适应尝试的 scale、offset、波形上下界、中心和峰值偏差。500 点、每点约 20 万样本时，原始波形总量约为一亿个样本。

## 离线分析

结果图统一通过 `lab_workflows.plotting` 的 `paper` 配置导出同名 PNG（300 dpi）和 PDF；原 PNG 文件名继续用于历史预览。默认固定画布，英文标注，图幅和导出约定见[科研绘图规范](plotting_style.md)。重新分析生成新图，原始数据和数值结果字段保持原有契约。

二维分数残差以原始残差数组绘图，仅掩蔽无效值，不对数组作数值截断。显示色限保持 −0.5 至 +0.5，色条两端箭头提示超范围值；颜色饱和不表示残差本身等于边界值。

分析器只读取指定 `run_dir`：

1. 从每点真实时间轴计算实际采样率。
2. 使用 配置的 `WELCH_NPERSEG` 计算 PSD 矩阵。
3. 保存从 DC 到实际 Nyquist 的完整 PSD 矩阵。
   `noise_spectrum_2d.png` 的横轴和纵轴都固定为实际控制频率扫描起点至终点，便于直接比较 PSD 频率与控制频率。色标只按该显示矩形内 `log10(PSD)` 的 `1%–99.5%` 分位数稳健缩放，显示范围外的孤立点不会压缩主体结构的对比度。保存的 PSD 数组仍保留完整 Nyquist 频段和原始数值。
4. 保留逐 PSD 频率的局部四参数 Lorentzian 作为诊断分析：排除 DC，只拟合中心附近 `±5 kHz` 的控制频率数据，并从控制频率扫描起点至终点内输出直接拟合/插值掩码和最多 10 张可信直接拟合示例。
5. 定量结果优先使用两阶段二维分解：先在 `2–45 kHz` 核心频段确定全局线宽、控制频率偏移和 `H(Omega)`，再固定这些全局量，将 `N_S1` 和 `C_S_beta` 投影计算扩展到 `500 Hz–45 kHz`：

   ```text
   P(Omega, omega) = H(Omega) * N_S1(omega)
                     + L(Omega, omega; gamma, offset) * C_S_beta(omega)
   C_S_beta = 4 * G^2 * S_2^2 * S_beta
   ```

   `gamma`、控制频率偏移和 `H(Omega)` 只由核心频段的二维共振脊线共同约束；`H(Omega)` 是归一化到高控制频率为 1 的秩一背景轮廓。固定这些全局量后，以稳健非负变量投影逐频率求 `N_S1` 和 `C_S_beta`，避免边界处的单侧共振峰拉偏全局参数。低频投影仍应用共振覆盖、交叉验证和残差判据；未通过定量判据的低频点会保留为灰色诊断点，不计入定量频段。
6. 二维分析使用近似 Gamma/Whittle 偏差评价 PSD，执行 5 折控制频率留出验证，并将背景脊线排除半宽改变为本次配置值的 `0.8/1.0/1.2` 倍。交叉验证离散度与背景模型敏感性按平方和组成内部相对不确定度；未通过阈值的频率保留数值但从 `quantitative_valid_mask` 排除。
   `global_2d_fit_examples.png` 从控制频率扫描起点至终点内的实际 PSD 频点中近似等间隔选择 4 个示例。
7. `global_2d_noise_spectra.csv` 和 `global_2d_noise_separation.npz` 是首选输出。`global_2d_noise_spectra.png` 的横轴使用从 `0 Hz` 开始的线性坐标，投影起点按本次分析参数确定，但只有通过 `quantitative_valid_mask` 的点可作定量解释。由于实验尚未独立给出 `4G^2S_2^2`，当前只能确定乘积 `C_S_beta`，不能把它直接解释为绝对 `S_beta`；报告的不确定度也不包含该标定误差。

## 安全结束状态

- 正常、异常、取消和 Ctrl+C 均恢复 GS200 的源模式、电流、输出、量程和限流。
- Z/X/Y 场归零并关闭输出；扫描期间使用的 X/Y DC 补偿不会在实验结束后继续保持。
- SDS 停止采集，并将 PD 通道统一恢复为 DC 耦合（无论本次测量选择 AC 还是 DC）。
- 温控恢复为 `5 V DC + Output ON`。
- Pump、Probe、Pump 载波和门控按标准策略保持，只断开 TEC 通信。
