---
title: 示波器单通道采集
type: experiment_type
scan_mode: point_by_point
defaults:
  mode: time
  sampling_rate_sa_s: 500000.0
  duration_s: 1.0
  channel: 1
  trigger_channel: 1
  trigger_mode: AUTO
  trigger_slope: RISing
  trigger_level_v: 0.0
  vertical_scale_v_div: 1.0
  vertical_offset_v: 0.0
  disable_temperature_control: false
  temperature_switch_off_settle_s: 0.1
  temperature_switch_on_settle_s: 1.0
  run_tag: scope
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
---

# 示波器单通道采集

实验中心的“示波器采集”打开专用页面，地址为 /experiments/scope-capture。
左侧为时间/频域绘图区，右侧为采集设置与历史列表；第一版不提供高级和数学面板。

## 采集

- 选择 C1--C4 中的一个通道，设置采样率（Sa/s）、采集时间（s）和运行标签。
- 可单独选择 C1--C4 触发通道，设置 AUTO/NORMAL/SINGLE 模式、上升/下降边沿和触发电平（V）。缺省触发通道与采集通道相同；默认 AUTO、上升沿、0 V。
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
- 勾选“关闭温控采集”后，流程会通过 `Temp_Switch` 映射通道输出 0 V（output ON），等待关闭稳定时间，采集完整波形，
  波形读取完成后立即恢复 5 V（output ON）。正常结束、异常和取消清理阶段都会继续尝试恢复；未勾选时不连接温度开关。

## PSD 和显示

时域量程按示波器 8 个垂直格计算：`offset ± 4 × V/div`。离线结果在 `display.json` 中记录实际读回的
V/div、偏置、上下量程边界、`overrange` 和 `overrange_fraction`；任一原始电压超出边界即判定为超量程，
时域图以边界线和红色区域标出。原始电压和 PSD 不做裁剪或缩放。

参数 schema 版本为 2，旧 v1 配置自动补齐触发与垂直默认值（1 V/div、0 V 偏置）。
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
- results/waveform_psd.png：采用 paper 绘图风格的离线结果图。

采集入口：agent_exp_env\Scripts\python.exe experiments\Scope_Capture.py。
离线入口：agent_exp_env\Scripts\python.exe experiments\Scope_Capture_plot.py <具体运行目录>。

CLI 采集与分析独立；GUI 采集完成后自动分析。默认参数位于 params/experiments/scope-capture.yaml，
共享实现位于 lab_workflows/experiment_modules/scope_capture/，预检不连接硬件。
