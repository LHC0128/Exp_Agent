# 信号发生器代码阅读指南

本包通过 PyVISA 控制 RIGOL DG4000 与 DG900 Pro 信号发生器。DG900 Pro
沿用 `DG900Instrument` 类名和设备库中的 `DG900` 型号键，当前新增文件能力面向 DG912 Pro。
两款驱动是独立实现，共同功能采用相同 Python 接口，内部使用各自型号的 SCPI 指令。

## 文件职责

```text
signal_generator/
  __init__.py        # 包级公开导出
  dg4000.py          # DG4000Instrument：DG4000 专属指令与驱动实现
  dg900.py           # DG900Instrument：DG900 Pro 专属指令与驱动实现
  types.py           # 波形、调制、Burst、单位等公共参数类型
  protocol.py        # 工作流使用的共同接口协议与能力描述
  config.py          # 单台配置、多台配置及 YAML 读写和应用
  _visa.py           # 完整截图读取与显式通信恢复的公共工具
  instrument.py      # 旧 DG4000 导入路径的兼容入口
  test_interfaces.py # 假仪器接口、导入兼容、截图、文件与恢复回归
  README.md          # 本阅读指南
```

`types.py` 提供 `Literal` 类型别名和共用的 `normalize_output_load()`，不用于运行时能力检测；类型中出现的选项不代表
两款型号都支持。`protocol.py` 声明工作流需要的共同接口，型号专属文件接口保留在
`dg900.py`。两款驱动均使用 `_visa.py`，DG900 不依赖 DG4000 实现模块。

## 建议阅读顺序

1. 阅读 [__init__.py](__init__.py)，了解公开导出的类；阅读
   [protocol.py](protocol.py) 与 [types.py](types.py)，了解共同接口和参数类型。
2. 在 [dg4000.py](dg4000.py) 或 [dg900.py](dg900.py) 中先看构造函数、
   `_ch()`、`connect()`、`_ensure_connected()`、`write()` 和 `query()`，
   理解资源、默认通道和通信路径，再查目标功能的方法。
3. 阅读 [config.py](config.py) 的 `SignalGeneratorConfig.apply_to()`，
   查看配置如何调用具体驱动。`MultiGeneratorSetup.apply_all()` 按 resource
   匹配调用方提供的实例，不负责发现设备或选择型号。
4. 阅读 [../../lab_workflows/devices.py](../../lab_workflows/devices.py) 的
   `create_signal_generator()`，了解实验如何按物理量绑定选择驱动。
5. 需要理解截图完整性、超时恢复或文件上传失败时，查看 [_visa.py](_visa.py)
   和 [test_interfaces.py](test_interfaces.py) 中的假仪器及回归用例。

设备选择的调用路径是：

```text
物理量键 / 配置字典 / 已映射的 resource
  → lab_workflows.devices.create_signal_generator()
  → 当前设备库与物理量映射
  → DG4000Instrument 或 DG900Instrument
  → 本型号 SCPI → PyVISA 资源
```

设备库位于 [../../params/devices.yaml](../../params/devices.yaml)，物理量绑定位于
[../../params/mapping.yaml](../../params/mapping.yaml)。USB 和 IP 都使用 VISA resource；
型号由配置决定，不靠地址格式猜测。`network_resources` 是扫描候选列表，实际设备需
确认身份后入库，不能直接把候选地址当成已确认的设备。

## 导入与创建入口

通常使用包级导入；需要定位型号源码时使用对应模块：

```python
from signal_generator import DG4000Instrument, DG900Instrument
from signal_generator import SignalGeneratorConfig, MultiGeneratorSetup
from signal_generator.dg4000 import DG4000Instrument
from signal_generator.dg900 import DG900Instrument
```

旧写法 `from signal_generator.instrument import DG4000Instrument` 继续有效。
该兼容模块也保留原公共类型别名和 `DEFAULT_TIMEOUT` 导出；新代码从
`signal_generator.types` 导入公共类型。

正式实验优先按当前绑定创建驱动：

```python
from lab_workflows.devices import create_signal_generator

sg = create_signal_generator("Z_magnetic_field")
print(type(sg).__name__, sg.resource_string, sg.channel)
```

该示例只创建未连接的实例并读取本地属性。工厂也兼容配置字典及已映射的 resource
字符串。`connect()`、上下文管理器或首个需要通信的方法会连接硬件；
`_ensure_connected()` 会在未连接时自动连接，连接失败直接报错，不自动切换设备。
实例构造参数 `channel` 是默认通道，方法显式传入的 `channel` 优先。

工厂默认在连接时应用本次物理量及联动组的实验负载策略，避免跨实验复用通道时互相覆盖。
批量时钟同步入口显式关闭自动负载策略，先向全部设备写入时钟源再统一回读；
GUI 的“输出负载初始化”通过独立流程设置并验证负载。专门的独立调试可用
`create_signal_generator(..., apply_load_policy=False)` 跳过自动负载策略。
DG900 Pro 的 `set_output_load()` 用 `INF` 设置高阻并回读验证；高阻数值约为
`9.9E+37`，数值负载也必须匹配目标。回读不一致即报错，连接阶段失败会释放通信会话。
两款驱动接受高阻数值回读（`>= 9e37`）并转换成各自的高阻关键字，避免将查询标记
误写为普通阻抗。普通负载必须为正数；原始负载查询字符串保持不变。

## 按功能查找代码

| 功能 | 主要方法与入口 |
|---|---|
| 基础波形与输出 | 两款驱动的 `apply_wave()`、`setup_*()`、`set_output()`、`set_amplitude()` |
| 调制 | 两款驱动的 `set_mod_type()`、`set_mod_source()`、`set_mod_state()` 及对应参数方法 |
| Burst 与参考时钟 | 两款驱动的 `set_burst_*()`、`get_burst_*()`、`set_ref_clock_source()` |
| 计数器 | 两款驱动的 `set_counter()`、`get_counter_measure()`；测量结果保留原始字符串 |
| 截图 | 两款驱动的 `get_screenshot()` → `_visa.get_screenshot()`；返回完整图片字节 |
| 通信恢复 | `recover_connection()`、`recover_connection_with_reset()`、`reset_stuck_resource()` |
| 易失性任意波 | 两款驱动的 `send_arbitrary_waveform()`、`setup_arbitrary()` |
| 内置任意波文件 | 仅 DG900 Pro：`upload_arb_wave()`、`get_arb_wave_points()`、`config_arb_wave()`、`list_files()`、`delete_file()` |
| AM 载波抑制 | 仅 DG900 Pro：`set_mod_am_dssc()` |

阅读和调用时注意：

- `apply_wave(shape, channel=None, **kwargs)` 使用 `freq/amp/offset/phase` 等关键字，
  本身不发送输出开关命令；`setup_*()` 的便捷配置通常会开启输出。
- `set_mod_source(source, channel=None)` 使用当前选择的调制类型，应先调用
  `set_mod_type()`；旧 DG900 签名迁移说明见下方完整驱动文档。
- `disconnect()` 只释放通信连接；关闭输出需要调用输出接口。正式实验的输出限值
  和安全收尾由上层工作流处理，参见 [../../lab_workflows/steps](../../lab_workflows/steps)。
- `recover_connection()` 不复位。仅显式调用带 reset 的恢复接口才发送 `*RST`。
- DG4000 截图仅支持 BMP，DG912 Pro 支持 BMP/PNG。DG4000 没有本次新增的文件接口。
- Pro 文件点数只记录当前实例成功上传的数据；未知文件需显式提供实际 `points`，
  不按文件字节数推算。上传默认拒绝覆盖，失败后仪器上可能仍留有部分文件。

## 无硬件验证

在仓库根目录、已安装本地可编辑包的环境中执行：

```powershell
.\agent_exp_env\Scripts\python.exe -m unittest signal_generator.test_interfaces
.\agent_exp_env\Scripts\python.exe -m unittest lab_workflows.test_instrument_config lab_workflows.test_output_policies
```

上述用例使用假仪器或依赖注入，不进行真实硬件采集。修改驱动后还应对修改的
Python 文件运行 `py_compile`，并执行受影响的工作流回归。

## 进一步阅读

- [完整驱动说明、使用示例与旧签名迁移](../../docs/instruments/signal_generator.md)
- [设备库、扫描候选和物理量映射规则](../../docs/instrument_mapping.md)
- [信号发生器交互示例](../../examples/signal_generator_demo.ipynb)

本次目录重组仅调整实现位置、公共类型依赖和导入入口，DG4000 既有方法的名称、
签名与内容保持原样。后续维护应在对应型号模块中修改实现，并同步检查共同接口和调用方。
