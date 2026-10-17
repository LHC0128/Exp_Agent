---
title: 示波器单通道采集
type: experiment_type
scan_mode: point_by_point
mapping_keys:
  scope_waveform:
    role: detection
  Temp_Switch:
    role: fixed
    optional_when: disable_temperature_control
required_devices:
  - SDS
learned_notes:
  - 频域来自完整时域电压的软件 Welch PSD，不使用仪器 FFT。
experiment_id: scope-capture
defaults_file: params/experiments/scope-capture.yaml
---

# 示波器单通道采集

## 采集时序与系统稳定

本实验每轮只采集一个完整 SDS 通道帧，不扫描、不配对，也不连接 TEC、GS200、6221 或锁相放大器。实际仪器配置完成后，启用门控时按“`Temp_Switch` 置 0 V DC 且 Output ON → 唯一的 `SYSTEM_SETTLE_S` → 采集至内存 → 恢复 5 V DC 且 Output ON → 保存波形”执行。未启用门控时仍在采集前等待 `SYSTEM_SETTLE_S`，不连接或写入温度开关。初始化目录与初始配置提前保存；关闭窗口内不写波形、配置或分析结果。

完整帧读完后先冻结保存参数，再响应取消。取消或恢复失败时先尝试恢复温控，再尽力保存已读完整帧；保留原始异常并停止后续采集。NPZ 先写同目录临时文件再原子替换，失败删除半写临时文件，不覆盖前一轮数据。恢复等待与 SDS 触发协议等待保留，统一约定见[采集规范](acquisition_timing.md)。


当前默认参数：[参数文件](../params/experiments/scope-capture.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

实验中心的“示波器采集”打开 `/experiments/scope-capture`，主界面为“运行 / 历史”双 Tab。运行页为采集与独立分析两列可执行流程，顶部集成参数与布局保存、预检、完整运行及任务日志；历史页保留时域/频域绘图、叠加比较、超量程诊断及原有历史操作。

## 可执行流程与参数

完整 CLI/GUI 采集与分步会话使用相同的准备、配置、完整帧捕获及保存函数。采集模块和参数影响如下；括号中的输出位于当前运行目录。

| 模块 | 前置 | 参数与职责 | 质量门槛 |
| --- | --- | --- | --- |
| A01 参数与接线预检 | 无 | 校验当前参数及映射，不连接硬件 | SDS 资源有效；门控启用时 Temp_Switch 为有效信号源通道 |
| A02 连接 SDS 与温度开关 | A01 | `disable_temperature_control` 决定是否接入门控；准备失败也安全清理 | 门控设备按设备库参考时钟严格回读，再恢复 5 V ON |
| A03 示波器采集配置与回读校验 | A02 | 采样率、时长、采集/触发通道、触发模式/边沿/电平、垂直档位/偏置 | FSRate、实际采样率、点数与垂直读回有效 |
| A04 温控门控采集与波形保存 | A03 | `SYSTEM_SETTLE_S`、`temperature_switch_on_settle_s`、`run_tag`；原子保存 `raw/waveform.npz`；`mode` 只选择显示方式 | 完整帧与 preamble 点数一致，实际时长足够；恢复先于写盘 |
| A05 安全收尾 / 结束会话 | 无 | 停止并断开 SDS；已连接温度开关恢复后断开 | 清理错误写入快照；不应用尚未重跑的新硬件参数 |
| B01 加载配置与原始波形 | 无 | 读取所选目录，生成 `results/analysis_inputs.json` | 实验身份与 raw 格式合格，等间隔递增时间轴、有限电压与请求元数据有效 |
| B02 Welch PSD 计算 | B01 | 固定科学算法，保存 `results/psd.npz` 与 `results/psd.json` | 实际采样率为正有限数，PSD 与元数据完整 |
| B03 显示数据与诊断绘图 | B02 | 生成 `results/display.json` 及 PNG/PDF | 保留窄脉冲与量程诊断，完成后发布显示文件；超量程仍允许导出诊断 |

参数在节点内按统一的基础/高级分类编辑，可同时展开多个节点。全部可见字段均有编辑入口；显示方式 `mode` 与历史页时域/频域按钮共用一个值，不使硬件或分析节点失效。本实验没有跨阶段反复使用的可调过程参数，也没有可调分析覆盖值，不人为复制参数入口或增加科学选项。

空闲会话修改参数先显示草稿及预计重跑范围；“应用参数到会话”只更新修订并使有关节点失效，不执行硬件步骤。门控开关参数影响 A02–A04，SDS 配置参数影响 A03–A04，稳定等待、恢复等待和标签影响 A04。还原修改则清除草稿预览；保存参数与布局不启动采集。每次重跑 A04 原子占用新目录，保留前一轮原始数据和参数。

分步等待保持连接与输出并占用全局硬件队列；缺前置拒绝，质量失败阻止后继。A05 无前置，空闲可直接结束；执行中使用任务取消，父进程 EOF 或后台关闭也先收尾再释放锁。刷新恢复仍存活的会话，后台重启不从磁盘记录恢复硬件。结束后的节点只显示执行记录，新参数用于下一次实验。

独立分析绑定所选具体运行目录，选“下一次实验”时没有分析覆盖项。完整分析与 B01–B03 分步分析共用算法；历史采集参数不被分析覆盖。输入与各阶段产物记录 SHA-256、大小和修改时间指纹，变化时前置失效。重算只归档当前阶段及后继产物，保留仍在使用的前置；归档前先验证 raw，损坏数据不会先移走原科学结果。旧重新分析与分步分析共享目录互斥，分析不占硬件锁。

## 采集

- 选择 C1--C4 中的一个通道，设置采样率（Sa/s）、采集时间（s）和运行标签。
- 可单独选择 C1--C4 触发通道，设置 AUTO/NORMAL/SINGLE 模式、上升/下降边沿和触发电平（V）。缺省触发通道与采集通道相同；模式、边沿和电平取自参数文件。
- 垂直档位 `vertical_scale_v_div` 直接设置示波器 V/div，垂直偏置 `vertical_offset_v` 直接设置通道偏置。
- scope_waveform 从当前设备库和物理映射解析；不写死资源地址。
- “关闭温控采集”启用时还需要 `Temp_Switch` 映射；该映射为可选项，未启用温控门控时不连接。
- 采集通道使用请求的 V/div 和垂直偏置；耦合、输入阻抗和探头倍率沿用该通道当前设置。自动启用采集通道和触发通道；独立触发通道保留自身输入设置，原始波形仅导出采集通道。
- 每次采集和重试都重新按用户模式布防。AUTO 发送一次 FTRIG 并等待动作结束后回到 AUTO，保证无有效边沿时也提交完整深存储帧；该模式不保证按设定边沿对齐。NORMAL 等待触发后留足记录窗口；SINGLE 等待触发完成（自动停止）。NORMAL/SINGLE 等待超时（记录窗口加 2.5 s）则报错，不切换到强制触发。
- 确认 STOP 后再分片读取完整记录。帧点数与 preamble 不一致时重试，连续三次失败则报错；不复位仪器、不接受触发超时后的旧帧。
- SDS1204X HD 的 AUTO 自由运行在无边沿时可能返回空数据，即使 STOP 已确认也不代表完成了深存储采集；固定等待不能替代 FTRIG 完成检查。失败运行的 `scope_configuration.capture_attempts` 保存每次导出/preamble 点数与失败原因，`capture_strategy` 标记采用的触发流程。
- 界面输入值是请求值；显示和 PSD 使用实际时间轴推算的采样率。实际记录不足请求时长则报错，不裁剪到请求点数。
- 第一版限制请求与实际记录为 2--50,000,000 点，这是软件内存边界，不代表所有采样率/时基组合都受硬件支持。
- 结束、异常和取消均停止并断开 SDS；等待和分片间检查取消。当前 VISA 事务须等待完成或超时后才响应取消。
- 勾选“关闭温控采集”后，流程会通过 `Temp_Switch` 映射通道输出 0 V（output ON），等待 `SYSTEM_SETTLE_S`，采集完整波形，
  波形读取完成后立即恢复 5 V（output ON）。正常结束、异常和取消清理阶段都会继续尝试恢复；未勾选时不连接温度开关。

## PSD 和显示

时域量程按示波器 8 个垂直格计算：`offset ± 4 × V/div`。离线结果在 `display.json` 中记录实际读回的
V/div、偏置、上下量程边界、`overrange` 和 `overrange_fraction`；任一原始电压超出边界即判定为超量程，
时域图以边界线和红色区域标出。原始电压和 PSD 不做裁剪或缩放。

参数 schema 当前为 3；旧 v1 配置按已发布规则补齐触发与垂直字段，旧 `temperature_switch_off_settle_s` 迁移为 `SYSTEM_SETTLE_S`，显式新键优先。raw 格式独立为 1，不跟随参数 schema 升级；历史无 raw 标记时仅接受已验证的 `scope-capture` 身份或 `experiment_type: Scope_Capture` 配置，并校验必需 NPZ 字段。错误身份、未知 raw 格式和损坏配置不进入可用历史列表；合法采集中目录可查看状态，但拒绝分析。
实际读回保存在 `scope_configuration` 和原始 NPZ 的 `configuration_json` 中，包含
`actual_vertical_scale_v_div`、`actual_vertical_offset_v`、`vertical_divisions` 和 `actual_trigger`。
分析优先使用运行配置中的实际读回值，缺失时使用原始快照；旧快照的 `input_settings.scale/offset` 也可读取。
完全没有量程元数据的历史记录仍可分析，量程及超量程字段为 `null`，界面状态显示“未知”。

`display.json` 的版本为 2，新增字段如下：

| 字段 | 含义 |
| --- | --- |
| `vertical_scale_v_div` | 实际 V/div |
| `vertical_offset_v` | 实际偏置，V |
| `vertical_range_min_v` / `vertical_range_max_v` | `offset ± 4 × V/div` 的上下边界，V |
| `overrange` | 任一原始样本严格超出边界时为 true，等于边界不算超量程 |
| `overrange_fraction` | 超界原始样本数 / 完整原始样本数 |

时域读数区显示档位、偏置、范围、正常/超量程状态及比例；频域图不显示量程标记。

Welch 单边功率谱密度，单位 V²/Hz，固定周期 Hann 窗、逐段去均值，
nperseg=min(10000, N)、noverlap=nperseg//2，scaling="density"。
PSD 使用完整原始电压和实际采样率，频率范围为 DC 至 Nyquist；频率间隔为实际采样率除以分段长度。
时间模式保留电压的原始直流分量，去直流仅作用于 PSD 计算。

每次自动分析同时生成时域显示数据和 PSD，历史数据可直接切换两种模式。
时域显示最多 6000 点，按时间桶保留极大/极小值以保留窄脉冲；原始数据仍完整保存。
频域模式可分别将频率轴和 PSD 轴切换为以 10 为底的 log 坐标；非正值在对应 log 轴上自动隐藏。图中可缩放和查看数据，重置按钮恢复视图。

## 历史

勾选多条历史可叠加；眼睛按钮独立控制显示/隐藏；色块设置曲线颜色并保存在本浏览器。
垃圾桶按钮经确认后永久删除选中运行的完整目录（含 raw 和 results），不等同于隐藏曲线。
一次选中一条数据可重新执行离线分析；失败或取消留下的记录也能选择并删除。
列表分页加载，展示仅限 Scope_Capture，不将其他实验格式冒充可用波形。

后端 DELETE /api/runs/{experiment_id}/{run_id} 拒绝路径穿越、链接和 Windows 联接点。
删除检查与任务注册共用锁；同类采集（包括自动分析）或该目录的分析任务未结束时拒绝删除。
此互斥覆盖同一 GUI 后端进程。不要同时从独立 CLI 进程写入正在删除的目录。

## 数据和入口

运行目录为 data/Scope_Capture/MMDD_HHMMSS_tag/，包含：

- experiment_config.yaml：参数、设备和映射快照、实际记录信息及清理报告。
- raw/waveform.npz：完整 time_s、voltage_v、ADC 码、通道、时间戳、实际/请求采样率、请求时长、preamble 和配置 JSON。
- results/display.json：时域显示数据与实际采样信息。
- results/psd.npz、results/psd.json：完整单边 PSD 与计算参数。
- results/waveform_psd.png、results/waveform_psd.pdf：采用 paper 绘图风格的离线结果图，分别用于预览和矢量排版；默认固定画布、不显示网格，遵循[科研绘图规范](plotting_style.md)。GUI 继续引用 PNG，PSD 数值与分析方法不变。

采集入口：agent_exp_env\Scripts\python.exe experiments\Scope_Capture.py。
离线入口：agent_exp_env\Scripts\python.exe experiments\Scope_Capture_plot.py <具体运行目录>。

CLI 采集与分析独立；GUI 采集完成后自动分析。默认参数位于 params/experiments/scope-capture.yaml，
共享实现位于 lab_workflows/experiment_modules/scope_capture/，预检不连接硬件。
