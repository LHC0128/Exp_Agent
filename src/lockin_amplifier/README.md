# HF2 / MFLI 锁相放大器源码阅读指南

本包通过 LabOne 的 `zhinst.core` Python Core API 控制 HF2 和 MFLI。
两款设备分别管理型号行为，公共通信、解调配置和采集流程继续复用。

## 文件分工

| 文件 | 职责 |
|---|---|
| [hf2.py](hf2.py) | HF2 连接默认值、硬件能力、输入来源、整数 clockbase、输出量程换算，以及固定长度 Scope 的设置与缩放 |
| [mfli.py](mfli.py) | MFLI 连接校验、选件能力、输入来源、浮点 clockbase、直接电压单位，以及可变长度 Scope 的设置 |
| [base.py](base.py) | 公共 `LockinInstrument`、连接与异常清理、节点读写、索引和频率校验、订阅、参考时钟及统一型号分派 |
| [instrument.py](instrument.py) | 旧导入入口，重新导出两款驱动、公共基类和能力查询函数 |
| [config.py](config.py) | 输入、振荡器、解调器、输出、Aux 和 DAQ 的配置与结果数据类 |
| [demod.py](demod.py) | 公共配置与单参数操作、校相和物理电压转换入口 |
| [daq.py](daq.py) | DAQ 配置、触发、等待与取消、记录边界、时间轴和模块清理 |
| [auxout.py](auxout.py) | Aux Output 的解调器路由、信号选择、缩放与偏置 |
| [scope.py](scope.py) | Scope 参数校验、采集、时间轴、数据完整性检查，以及异常或取消后的设置恢复 |
| [__init__.py](__init__.py) | 包级公共导出 |

阅读某款设备时先从对应的 `hf2.py` 或 `mfli.py` 开始，再看 `base.py` 的公共通信。
排查采集问题时查看 `daq.py` 或 `scope.py`；它们将输入映射、节点单位和 Scope 设置委托给型号驱动。

## 导入与创建

推荐使用包级导入，也可直接定位型号模块：

```python
from lockin_amplifier import HF2Instrument, MFLIInstrument
from lockin_amplifier.hf2 import HF2Instrument
from lockin_amplifier.mfli import MFLIInstrument
```

旧写法 `from lockin_amplifier.instrument import HF2Instrument, MFLIInstrument`
继续有效，导出的类与包级入口相同。`LockinInstrument` 的原导入路径和直接构造时的
HF2 默认行为也保留；新代码优先使用具体驱动或设备工厂。

下面只创建未连接的实例，不访问仪器：

```python
hf2 = HF2Instrument(device_id="dev18246")
mfli = MFLIInstrument(device_id="dev30661")
print(hf2.model, mfli.model, hf2.connected, mfli.connected)
```

| 驱动 | 默认服务器 | 默认 API Level | 默认接口 | 设备 ID |
|---|---|---|---|---|
| HF2 | `127.0.0.1:8005` | `1` | `PCIe` | 旧默认值为 `dev18246`，使用时应核对实际设备 |
| MFLI | `127.0.0.1:8004` | `6` | `1GbE` | 必须显式提供；API Level 必须为 `6` |

`interface` 表示数据服务器到仪器测量单元的连接。访问 MFLI 内置的数据服务器时，
即使电脑通过网线访问仪器，接口仍为内部 `PCIe`；当前 `dev30661` 的
`192.168.124.109:8004` 配置显式使用 `PCIe`。独立 PC 上的数据服务器可以使用
其报告的 `1GbE` 或 `USB`。接口以 `/zi/devices` 的 `INTERFACE` 为准，
设备库扫描直接读取该值；直接构造驱动时按实际服务器显式传入接口。

正式实验按当前物理量映射创建实例：

```python
from lab_workflows.common import load_mapping
from lab_workflows.devices import create_lockin

instr = create_lockin(load_mapping()["lockin_r"])
print(instr.model, instr.device_id)
```

该工厂也只构造实例。设备定义来自 [devices.yaml](../../params/devices.yaml)，
物理量绑定来自 [mapping.yaml](../../params/mapping.yaml)。`connect()` 或上下文管理器
才会连接硬件；其他通信方法要求已连接。连接时会核对实际型号并回读安装选件，失败时清理连接。

## 型号差异如何维护

- **能力与选件**：统一入口为 `lockin_capabilities(model, options=None)`。
  HF2 能力由 `hf2.py` 声明；MFLI 的 MD、F5M、DIG 选件处理位于 `mfli.py`。
  未连接时 MFLI 实例使用基础能力，连接后的实际能力以设备回读为准。
- **输入来源**：具体驱动声明 `demod_input_sources` 和 `scope_input_sources`。
  MFLI 的第二个主电压输入会被拒绝，电流或 Aux 输入需要显式选择，不能自动替换接线。
- **输出单位**：公共 setter 使用峰值电压和偏置电压；HF2 的底层节点按当前量程比例换算，
  MFLI 直接使用 V。写入、回读和上层安全检查复用同一换算入口。
- **Scope**：各驱动提供记录长度要求、节点配置和模块缩放参数。
  `scope.py` 统一处理等待、取消、独立记录、错误标志和设置恢复；HF2 固定 2048 点，
  MFLI 的记录长度及通道数按实际选件能力校验。

修改某一型号时，将这些差异放在对应驱动中，并运行两种型号的回归测试。
公共函数签名、配置参数键、能力字典和 `DAQResult` / `ScopeResult` 格式保持兼容。

## 安全与工作流边界

正式实验连接后调用 `lab_workflows.steps.initialize_lockin_inputs()`，关闭当前型号全部主电压输入
的 AC 耦合和 50 Ω 终端，保留量程与差分设置。`SignalInputConfig` 默认使用 DC 耦合和高阻；
后续实验配置复用默认值，旧参数键保留兼容并归一到关闭值，GUI 不再提供开启选项。

底层 setter 负责节点操作、参数校验和单位转换。正式输出设置由
[lockin_control.py](../../lab_workflows/lockin_control.py) 复用实验安全限值检查；
型号特有的 Aux 安全策略也保留在该层。连接后的参考时钟同步和安全收尾复用
[公共硬件步骤](../../lab_workflows/steps)。

`disconnect()` 释放通信连接，关闭输出使用对应输出接口和安全收尾。
双主电压回采等实验的型号兼容性由
[lockin_compatibility.py](../../lab_workflows/lockin_compatibility.py) 检查。

## 无硬件验证

在仓库根目录、已安装本地可编辑包的环境中执行：

```powershell
.\agent_exp_env\Scripts\python.exe -m unittest lab_workflows.test_lockin GUI.backend.test_lockin_api
.\agent_exp_env\Scripts\python.exe -m unittest lab_workflows.experiment_modules.noise_spectrum_xy.test_sine_mode lab_workflows.experiment_modules.rf_sensitivity_direct_aw.test_workflow lab_workflows.experiment_modules.static_sensitivity.test_dc_scan
```

这些回归使用假仪器或依赖注入，覆盖导入兼容、选件能力、单位换算、记录边界、
Scope 来源与设置恢复、取消和安全拦截。修改的 Python 文件还需运行 `py_compile`。

## 进一步阅读

- [完整模块说明、采集示例与实机验收](../../docs/lockin_amplifier.md)
- [设备库与物理量映射规则](../../docs/instrument_mapping.md)
- [旧模式锁相实验与兼容性](../../docs/legacy_lockin_experiments.md)
- [HF2 / MFLI Notebook 示例](../../examples/lockin_amplifier_demo.ipynb)
