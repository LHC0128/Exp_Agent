# Aigtek 1200B 基础控制库

本包负责 1200B 放大器的串口与已核验指令，不生成波形，不把磁场换算成电压或电流；实验经映射 `coil_drive_amplifier` 显式接入。任意波仍由上游信号发生器生成。

- 设备库：[设备定义](../../params/devices.yaml)，稳定 ID `aigtek_1200b_cp210x_0001`，类型 `power_amplifier`，型号 `1200B`。
- 已知串口：`COM3`，适配器名称 `Silicon Labs CP210x USB to UART Bridge`，VID/PID `10C4:EA60`，适配器序列号 `0001`。这些是现场连接记录，不是放大器身份回读。
- 协议及现场核验依据：[编程手册](../../docs/instruments/aigtek_1200b_programming.md)。

## 接口与返回语义

```python
from aigtek_1200b import Aigtek1200BInstrument, AmplifierCommandError
```

| 接口 | 行为 |
| --- | --- |
| `Aigtek1200BInstrument(port, *, serial_number="0001", response_timeout=3.0, write_timeout=1.0, safety_check=None)` | 仅构造对象，无通信。端口显式传入，不自动选择其他设备。 |
| `connect()` | 先执行 `require_hardware_access()`，再枚举并核对指定 CP210x 适配器，最后打开 9600/8N1、无流控串口；打开前设置 DTR/RTS 为 False。不发送初始化命令。 |
| `disconnect()` / 上下文退出 | 只释放通信，不关闭输出、不保存。 |
| `connected` | 仅表示本地主机串口句柄处于打开状态，不是设备身份、在线探测或输出状态。 |
| `set_output(enabled: bool)` | `False` 发送 AOF，`True` 发送 AON。只接受 bool。 |
| `set_gain_db(gain_db)` | 0～20 dB、0.5 dB 步进，越界或非半步拒绝，不自动取整。**0 dB 不是零输出。** |
| `set_input_impedance(ohms)` | 只接受 `50` 或 `1_000_000` Ω。50 Ω 会增加上游负载，即使放大器输出 OFF。 |
| `set_output_impedance(ohms)` | 只接受 `1` 或 `50` Ω；这是档位，不是线圈电阻。 |
| `save_settings()` | 显式发送 SAV，不随设置、连接、退出或异常自动保存。 |

设置函数返回不可变的 `CommandReceipt(command, response, execution_confirmed=False)`：

- `command` 为完整的大写 ASCII 命令，只以分号终止，不附加 CR/LF。
- `response` 为本次有界收集的原始字节；默认时间上限 3 秒、长度上限 4096 字节。
- **没有厂家定义的成功应答，`execution_confirmed` 始终为 False。空返回不是失败，完整写入也不证明命令已执行。**
- `OVER RANGE ERR!`（含分片）或返回达到长度上限抛 `AmplifierCommandError`，其 `receipt` 保留命令和原始字节。短写、串口超时、读写错误原样报告，不自动重发、不恢复输出、不更换端口。
- **短写或写异常可能在设备端遗留半帧，本实例随后所有命令（包括 OFF）均拒绝发送。** 不补发分号或重复命令，避免把 OFF 拼到残帧里；断开仍可用，重连不会清除发送故障。仅重新创建对象不能证明硬件已恢复，应先人工核验输出并按厂家方法安全恢复。不把正常静默或读取错误当作写入半帧故障。
- 没有 `get_output()`、增益回读、伪造身份或“最后请求值即当前状态”的缓存。面板状态与实际波形须由操作者或独立测量确认。迟到返回不能可靠归属于某条命令。

同一实例的命令串行执行；它不替代 GUI 全局硬件锁、跨进程队列或协作式取消。当前实例单条命令最长会占用默认 3 秒的返回收集窗口。

## 安全边界

`set_output(False)` 不要求风险校验，便于缺少线圈规则或参数校验失败时尝试关闭。写入流已故障时它也会拒绝，防止关闭命令与遗留半帧拼接；此时输出未知，必须人工核验。

开启、增益、输入阻抗、输出阻抗和保存操作必须在构造时提供 `safety_check(operation, value)`，且每次实际写入前调用。回调抛出的异常原样上抛，命令不发送；缺少回调时这些操作全部拒绝。`operation` 分别为 `output`、`gain_db`、`input_impedance_ohms`、`output_impedance_ohms`、`save_settings`。

回调由工作流/授权测试调用方负责，不在驱动中导入上层模块。它必须：

1. 核对实际映射和**明确存在的上下界**，输出包络写入前执行 `lab_workflows.common.validate_safety_limit()`。该公共函数对缺失规则不报错，因此不能只给一个不存在的 key 调它。
2. 依据已确认的上游幅度/偏置、增益、阻抗、线圈电压电流功耗与频带判断；设备协议量程不是负载安全范围。
3. 改增益或阻抗前，关闭并人工确认放大器输出；改输入阻抗前还需关闭上游信号或断开输入线。发送过 AOF 不能替代面板确认。
4. 保存前确认当前设置可以持久化；SAV 的具体范围及开机输出状态仍待厂家/现场验证。

本包不提供空操作安全回调或带载自动开启示例。物理量映射 `coil_drive_amplifier` 与线圈电流限值 `z_pulse_coil_current` 由实验 `coil-drive-characterization` 使用：其回调只放行 `output=True` 并在开启前重做整次预检，不修改增益与阻抗。没有 GUI 实时控制。

## 仅关闭输出的调用示例

以下代码**会尝试停止该路输出**，仅在获准的运行目录且获准停止这一路后执行。本轮开发未执行此示例，未连接 COM3。

```python
from aigtek_1200b import Aigtek1200BInstrument
from lab_workflows.steps.safety_shutdown import (
    DisconnectTarget,
    ShutdownAction,
    run_safety_shutdown,
)

amp = Aigtek1200BInstrument("COM3", serial_number="0001")
amp.connect()
receipts = []
try:
    # 此处没有开启或设置操作。
    pass
finally:
    report = run_safety_shutdown(
        extra_actions=[ShutdownAction(
            "发送 1200B 关闭命令",
            lambda: receipts.append(amp.set_output(False)),
        )],
        disconnect_targets=[DisconnectTarget("1200B", amp)],
    )
    print("软件收尾错误：", report.errors)
    print("关闭命令发送记录：", receipts)
    print("输出状态未回读；请检查面板和实际负载。")
```

公共收尾的 `report.completed=True` 只代表软件动作没有抛异常；对 1200B 不代表已确认 OFF 或线圈已安全。关闭失败时其他独立安全动作及断开仍会尝试。`disconnect()`、上下文退出、程序结束都不是关闭输出的替代品。

## 开发验证及与 T9 并行

开发目录不放门控标记，不打开串口。`connect()` 复用已落地的硬件门控，新通信入口登记在[门控测试](../hardware_gate/test_gate.py)。本包不修改 T9 的 GUI 任务策略、开发端口、数据根或运行目录，也不依赖后续模拟仪器。

新包已登记到[打包配置](../../pyproject.toml)。已有可编辑安装需经授权刷新一次本项目登记；本轮开发环境使用 `--no-deps --no-build-isolation`，不升级第三方包。运行目录的安装刷新与发布仍需单独授权。

在仓库根使用现有环境执行无硬件测试：

```powershell
.\agent_exp_env\Scripts\python.exe -m pytest -q src/aigtek_1200b/test_instrument.py src/hardware_gate/test_gate.py lab_workflows/test_aigtek_1200b.py --basetemp=.cache/pytest-tmp/aigtek-1200b
```

[驱动测试](test_instrument.py)覆盖全部半 dB 编码、合法/非法命令、串口身份与门控、静默设备、有界原始返回、短写/超时、无自动保存/恢复、异常清理。[接线测试](../../lab_workflows/test_aigtek_1200b.py)覆盖设备库无硬件读写、真实安全校验回调、缺限值拒绝、公共 finally 收尾及关闭/断开双重故障。

## 待查

- 放大器后级线圈的安全电压、电流、功耗、热限制和目标磁场标定；当前信号源限值不能直接沿用。
- 所接上游信号源/通道、目标线圈与开始/结束时保持策略。
- SAV 保存范围、参数断电保持、开机输出状态，以及厂家是否提供额外查询/应答。
- 真实增益、带载波形、频率响应与握手线物理效果；假串口测试不能证明这些硬件行为。
