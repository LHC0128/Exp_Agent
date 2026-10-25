---
title: Mx Y 向 RF 频率响应
type: Mx_Y_RF_Frequency_Response
execution_mode: typed_workflow
scan_mode: nested_scan
mapping_keys:
  main_magnetic_field:
    role: fixed
  rf_coil:
    role: scan
  X_magnetic_field:
    role: fixed
  Z_magnetic_field:
    role: fixed
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  Pump_modulation:
    role: fixed
  Time_sequence:
    role: fixed
  Temp_Switch:
    role: fixed
  lockin_r:
    role: detection
  temperature:
    role: fixed
required_devices:
  - GS200
  - DG900
  - DG4000
  - 锁相放大器
learned_notes:
  - 工作点与 Mx Y RF 灵敏度实验完全一致：主场与 Pump 光沿 Z，Probe 光沿 X，待测 RF 场沿 Y。
  - 采集只使用 Demod0 R，振荡器跟随 Y RF 频率直接解调；R 是矢量幅度，与解调参考相位无关。
  - RF 幅度轴点数等于 1 且起止相等时只测单幅度（与模式 1 一致）；否则嵌套扫描多个幅度。
  - 弱驱动 S≤1 为单峰，gamma=1/(2πT2)；强驱动 S>1 为 Rabi 劈裂双峰，峰位 f0±gamma*sqrt(S-1)。
experiment_id: mx-y-rf-frequency-response
defaults_file: params/experiments/mx-y-rf-frequency-response.yaml
---

# Mx Y 向 RF 频率响应

## 采集时序与系统稳定

外层扫描 RF 幅度，内层扫描频率；每个 `(幅度, 频率)` 点及其重采尝试都是独立的采集窗口。本实验不做校相、噪声采集或 OFF/ON 配对，也不将多个频点合成温控关闭组。实际仪器配置完成后，`Temp_Switch` 置为 **0 V DC 且 Output ON**，等待唯一的 `SYSTEM_SETTLE_S`，读取 R 到内存，再恢复 **5 V DC 且 Output ON**、等待 `TEMP_SWITCH_ON_LAG_S`，最后保存当前尝试的波形、采集元数据和质量统计。

目录及初始参数在采集前创建；窗口内不写文件。超限尝试同样在恢复温控后保存，然后显式重采。取消、采集失败或恢复失败均停止后续频点；已读数据尽力保存，恢复和保存错误不覆盖原始采集异常。所有频点合格后才生成完整的 `raw/frequency_response.npz`，中断运行保留已有逐点文件，不生成虚假的完整矩阵。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 等设备准备等待在温控开启时完成；参数迁移、异常数据保护和适用边界见[统一采集规范](../acquisition_timing.md)。


## 锁相采集方式与实际速率

扫频使用 `RESPONSE_RATE_SA_S` 请求率、`FREQUENCY_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](../instruments/lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../../params/experiments/mx-y-rf-frequency-response.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

测量 Mx 工作点下 Y 向 RF 场的频率响应曲线 R(f)。采集方式参考
`Mx_Y_RF_Sensitivity` 实验的模式 1（固定幅度、步进扫频、锁相放大器 Demod0 直接解调、
std(R) 质量门控、温控开关时序），RF 幅度本身可作为可选的外层扫描轴，
一次运行即可得到弱驱动 Lorentzian 线形到强驱动 Rabi 劈裂双峰的完整演化。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](../instruments/lockin_amplifier.md)。

## 构型与信号链

- 主磁场沿 Z，Pump 光沿 Z，Probe 光沿 X，rf_coil 产生 Y 向 RF 场；
  X_magnetic_field 和 Z_magnetic_field 始终归零并关闭。
- Pump AOM 载波频率与幅度读取参数文件；Time_sequence 保持 5 V DC 且输出开启，使 Pump 光常开。
- 锁相放大器 Demod0 使用 Input 1、DC 耦合、高阻、配置的输入量程，振荡器跟随当前 Y RF 频率直接解调；
  时序采集只订阅 R，DAQ 或 Scope 由设备库能力与请求选择。
- 参考时钟遵循 `params/devices.yaml` 中当前绑定设备的 `reference_clock`，
  实验在任何输出配置前设置并回读验证，任一设备不一致即停止。

## 理论模型与线形

在旋波近似下，主场 B₀ 沿 Z（Larmor 角频率 ω₀=2πf₀），Y 向 RF 场
B₁cos(ωt)，失谐 Δ=ω₀−ω，驱动 Rabi 角频率 Ω₁=γB₁/2。绕 Z 以 ω 旋转的
坐标系中，布洛赫方程稳态解为

- u = M₀ ΔΩ₁T₂² / (1 + Δ²T₂² + Ω₁²T₁T₂)
- v = M₀ Ω₁T₂ / (1 + Δ²T₂² + Ω₁²T₁T₂)
- w = M₀ (1 + Δ²T₂²) / (1 + Δ²T₂² + Ω₁²T₁T₂)

其中 T₁ 包含光泵浦的贡献，T₂ 为横向弛豫时间。锁相放大器 Demod0 直接以 RF 频率解调
得到的矢量幅度与解调参考相位无关：

\[
R \propto \sqrt{u^2+v^2}
   = \frac{\Omega_1 T_2 M_0 \sqrt{1+\Delta^2 T_2^2}}{1+\Delta^2 T_2^2+\Omega_1^2 T_1 T_2}
\]

令 x=(f−f₀)/γ、γ=1/(2πT₂)（Hz）、S=Ω₁²T₁T₂，得到统一拟合模型

\[
R(f) = C + A\frac{\sqrt{1+x^2}}{1+x^2+S}
\]

两个极限：

- **弱驱动 S→0**：单峰，峰在 f₀，R≈C+A/√(1+x²)，远翼比 Lorentzian 衰减更慢。
  γ 始终表示 `1/(2πT₂)`；历史产物的 `hwhm_hz` 键沿用 γ 的旧命名与数值，
  并非 R 幅度曲线相对基线的几何半高宽（S=0 时后者为 √3γ）。本次不改历史数值或拟合公式。
- **强驱动 S>1**：中心 x=0 为局部极小（谷值 C+A/(1+S)），双峰位于
  f₀±γ√(S−1)，峰高 A/(2√S)，峰谷比 (1+S)/(2√S)；等效 Rabi 频率（Hz）为 γ√S。
  峰间距随驱动幅度近似线性增大，即 Rabi 劈裂。

驱动强度判据：S=(γB₁/2)²T₁T₂>1 时劈裂。由于 S∝B₁²∝Vpp²，多条幅度曲线的
S 对幅度平方应成线性关系，分析器用线性回归诊断并报告斜率
（正比于 (γ·c/2)²T₁T₂，c 为线圈 Vpp→B₁ 的标定系数）。

## 采集顺序

1. 连接设备并配置 Mx 工作点（与 Mx Y RF 灵敏度实验相同）。
2. 嵌套扫描：外层 RF 幅度轴（`RF_AMPLITUDE_START_VPP` 至
   `RF_AMPLITUDE_STOP_VPP`、`RF_AMPLITUDE_POINTS` 点；点数等于 1 时起止必须相等，
   即单幅度），内层频率轴（`FREQUENCY_START_HZ` 至 `FREQUENCY_STOP_HZ`、
   `FREQUENCY_POINTS` 点）。
3. 每个 (幅度, 频率) 点：DG 设置 Y RF 通道频率、相位 0°、幅度并开输出
   （初始正弦由工作点配置建立，之后只用 `set_frequency`/`set_amplitude`，
   不重绘波形），锁相放大器 振荡器跟随频率；然后按“温控关→等待稳定→采集 R→温控开→等待”
   执行，std(R) 达到参数配置的质量门槛才接受，超限时完整保存该次原始 R 并重新采集，
   重采达到配置上限仍失败则停止实验。
4. 汇总保存 `raw/frequency_response.npz`：频率轴、幅度轴、R 均值/标准差矩阵、
   每点接受的 attempt 索引与文件名、实际采样率。
5. 所有路径（正常、取消、异常）都关闭并归零 Z 辅助场和 Y RF，保持 X 场关闭并
   恢复温控 5 V；Pump 配置载波和 Time_sequence=5 V DC + ON 是本实验的显式
   安全例外；正常结束只断开 TEC。

## 离线分析

- 每条幅度曲线独立拟合统一布洛赫模型。拟合分两步：先固定 S=0 拟合 4 参数
  sqrt-Lorentzian（弱驱动解），再尝试多组 S 初值做 5 参数拟合；只有 5 参数解
  明确给出 S>1 时才采用强驱动解。S≈0 附近 S 与 γ 存在简并，直接 5 参数拟合
  会滑向 γ 偏小的局部解，因此弱驱动数据一律报告 S=0 的 4 参数解。
- 质量门控沿用 R²≥`FIT_R_SQUARED_MIN`，并拒绝 γ 不小于扫描跨度一半的解。
  γ 相对不确定度为有限值时才检查 `FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX` 上限。
  旧 S=0 四参数分支将协方差扩展为含 NaN 的五参数矩阵，使该分支的不确定度不可用；
  原规则不会据此拒绝曲线。本次保留这一科学边界，JSON 将不可用值保存为 `null`，历史 NaN 只读兼容。
- 至少一条幅度曲线通过门槛，B02 才标为完成并允许绘图报告；无合格曲线时保存
  `bloch_fit.npz` 和 `fit_diagnostics.yaml`，标为 `quality_failed`，禁止 B03，允许调整受支持的分析覆盖值后重试。
- 结果：`results/bloch_fit.npz`（拟合参数矩阵、S、峰位、劈裂标志）、`fit_diagnostics.yaml`（逐幅度拒绝原因）、
  `analysis.yaml/json`（每条曲线的 γ〔历史 `hwhm_hz` 键〕、中心、S、劈裂判定、等效 Rabi 频率，
  多幅度时的 S–Vpp² 线性诊断）、`frequency_response.png`（各幅度数据点与拟合
  曲线叠加，paper 配置，英文标注）、`s_vs_amplitude.png`（多幅度时）。

原始数据位于 `data/Mx_Y_RF_Frequency_Response/<run>/raw/`，结果位于同一运行目录的
`results/`。采集和离线分析入口分别为 `experiments/Mx_Y_RF_Frequency_Response.py`
与 `experiments/Mx_Y_RF_Frequency_Response_plot.py`。

## 安全例外

正常、取消和异常路径都关闭并归零 Z 辅助场和 Y RF，保持 X 场关闭并恢复温控 5 V。
Pump 载波和 Time_sequence=5 V DC + ON 是本实验的显式安全例外；安全收尾使用最近成功执行的 Pump 配置，
不会把刚应用但未执行的新参数写回仪器。正常结束只断开 TEC，其余保留输出与锁相设置延续。

## 模块与分步执行

`workflow.py` 只编排：`preparation.py` 负责预检与收尾，工作点、时钟和激励复用
`mx_y_rf_sensitivity` 的准备、激励模块；`acquisition_steps.py` 负责本实验嵌套扫描和恢复后写盘。
离线分析由 `analysis.py` 编排 `analysis_steps.py`、既有 `analysis_core.py` 和 `analysis_plotting.py`。
完整脚本与分步会话使用同一套实际函数，薄入口不复制流程。

| 节点 | 职责 | 前置 / 质量约束 |
| --- | --- | --- |
| A01 | 参数、映射与安全预检 | 无前置；锁相能力与输出限值有效 |
| A02 | 连接与初态回读 | A01 |
| A03 | 参考时钟同步 | A02；严格回读一致 |
| A04 | 主场、光功率与辅助场归零 | A03 |
| A05 | TEC 目标温度 | A04；仅设目标 |
| A06 | Pump 载波与门控 | A05 |
| A07 | Y RF 正弦和 R 解调准备 | A06 |
| A08 | RF 幅度 × 频率扫描 | A07；每点 std(R) 合格，重采用尽则阻断 |
| A09 | 本轮安全收尾、封存 | A08；收尾成功才封存；A07/A08 失效 |
| A10 | 安全结束会话 | 无前置，可独立结束 |
| B01 | 离线输入资格 | 身份、原始格式、有限递增轴及矩阵有效，至少一条曲线有 6 个有效点，运行停止写入 |
| B02 | 布洛赫拟合与饱和度诊断 | B01；至少一条曲线满足 R²、扫描跨度及有限时的 γ 相对不确定度约束 |
| B03 | 绘图与报告 | B02；保留实际前置拟合缓存 |

参数编辑归属与影响范围以 `stages.definition()` 为准。主场、光功率、目标温度和 Pump 只在其设置节点编辑；
`R_BAD_POINT_STD_THRESHOLD_V` 在 A08 与“下一次实验”的 B02 共用一份值。
`FIT_R_SQUARED_MIN`、`FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX` 只影响离线拟合；采样和扫描参数使真实使用节点及后继失效。

## GUI 运行与历史

实验中心使用“运行 / 历史”两个主 Tab。运行页流程容器顶部提供保存参数与布局、派生预览、预检、完整运行和取消；
节点展开后按基础/高级分类编辑，分类以参数键同步。修改存活会话参数会显示未应用草稿与预计重跑范围；
应用只使相关步骤失效，必须显式勾选重跑。还原草稿清除预览，失败保留草稿和原执行记录。

分步等待期间设备连接、保留输出和全局硬件锁继续占用；刷新恢复存活会话，后台重启不会从磁盘重新连接仪器。
空闲时执行 A10 结束，执行中使用安全停止；EOF、取消和后台关闭均完成收尾后释放锁。
A09 只封存当前轮，后续再采集需重新执行 A07/A08，并使用独立新目录，旧原始文件和参数快照不覆盖。

分析列默认编辑“下一次实验”，也能选择已停止写入的当前或历史运行；具体目录的三项分析覆盖值独立保存，
切换目录或主 Tab 保留各自草稿。保存默认值只保存下一次参数和统一分类，不写历史覆盖值。
历史页展示本次参数、采集/分析状态、响应与饱和度图、逐幅度拟合诊断和文件下载；填回只编辑下一次参数，
不自动保存或启动，重新分析绑定所选目录。
诊断统一通过只读分析接口读取：无阶段记录的旧运行从原 `results/analysis.json` 展示逐幅度结果，
不可用不确定度的 NaN 仅在接口响应中转换为 `null`，原文件下载仍保留原始字节，响应图与饱和度图继续显示。
浏览不会创建阶段记录或启动重算；旧运行的分步分析仍从 B01 开始，已有阶段记录（包括质量失败诊断）优先于旧报告。
旧科学产物已存在但没有新分步记录时，状态显示“未开始分步分析”，表示尚未开始新的分步流程，不影响查看已有诊断与结果图。

## 原始格式与历史兼容

新增运行显式记录 `raw_format_version: 1`，与参数 `schema_version` 独立。已有无格式标记的 schema 1/2 运行，
只接受已验证的实验身份或 `experiment_type: Mx_Y_RF_Frequency_Response`，并核对真实汇总文件。
显式 raw 1 不因参数版本较新而自动拒绝；未知原始格式、错误身份、坏矩阵或缺失汇总均在归档前拒绝。
历史短等待按模型取最大值迁移，显式 `SYSTEM_SETTLE_S` 优先，不重写历史原始数据。

阶段记录保存参数、修订、原始输入及产物的内容 SHA。原始文件新增、删除或内容变化使 B01 及后继失效，
拟合缓存变化使 B02/B03 失效，仅图片变化使 B03 失效；重算归档受影响产物至 `results/before_<time>/`，
输入资格失败时保全旧科学产物，不把不再有效的前置当作完成。可编辑原图位于本实验包的
`workflow.excalidraw`，GUI 提供下载入口，与真实节点和质量门槛同步。
