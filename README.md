# 实验自动化平台

实验设计、数据采集、数据分析的自动化平台。

## 项目结构

```
├── src/
│   ├── sds_acquisition/       # 示波器控制 (SDS 系列, 已实现)
│   │   ├── instrument.py      # PyVISA 封装 + SCPI 命令
│   │   ├── acquire.py         # 采集编排器
│   │   ├── config.py          # 配置数据类
│   │   ├── waveform.py        # 波形解析与转换
│   │   ├── save.py            # 数据保存 (CSV/NPZ/MAT/HDF5/BIN)
│   │   └── cli.py             # 命令行入口
│   │
│   ├── signal_generator/      # 信号发生器 (DG4000 系列, 已实现)
│   │   ├── instrument.py      # PyVISA 封装 + SCPI 命令 (110+ 方法)
│   │   └── config.py          # 多仪器配置管理
│   │
│   ├── lockin_amplifier/      # 锁相放大器 — 预留
│   └── experiments/           # 实验脚本 — 预留
│
├── examples/                  # Jupyter notebook 示例
├── data/                      # 原始数据
├── params/                    # 实验参数与配置文件
│   ├── default_config.yaml    # 示波器默认配置
│   └── default_sg_config.yaml # 信号发生器默认配置 (多台)
├── results/                   # 结果图表
├── manuals/                   # 设备编程手册
│   └── DG4000_extracted/      # DG4000 编程手册 (已解压)
├── agent_exp_env/             # Python 虚拟环境
└── pyproject.toml             # 包安装配置
```

## 环境

- Python 虚拟环境：`agent_exp_env\`
- Python 可执行文件：`agent_exp_env\Scripts\python.exe`
- 安装包：`agent_exp_env\Scripts\pip install <package>`
- 导出依赖：`agent_exp_env\Scripts\pip freeze > requirements.txt`

所有仪器控制包已通过 `pip install -e .` 安装到虚拟环境，可直接 `import` 使用。

---

## SDS 示波器采集程序 (`sds_acquisition`)

### 主要功能

| 功能 | 说明 |
|---|---|
| 采样率设置 | `ACQ:SRAT` |
| 采样时间窗口 | `TIM:SCAL` (s/div × 10 格) |
| 量程 | `CHAN<n>:SCAL` (V/div) |
| 电压偏置 | `CHAN<n>:OFFS` |
| 多通道采集 | 单次触发后依次读取各通道 |
| 触发 / 无触发 | AUTO(无触发) / SINGle(单次) / NORMal(常态) |
| 耦合 / 阻抗 / 探头 | AC/DC/GND, 1MΩ/50Ω, 衰减比 |
| 采集类型 | 正常 / 峰值检测 / 平均 / ERES |
| 数据格式 | CSV / NPZ / MAT / HDF5 / BIN | 支持保存和加载 |
| 预览图 | 自动生成 PNG 波形图 |

### 数据格式对比

```
格式      依赖        大小/点     特点
──────────────────────────────────────────
CSV       —          ~25 B      文本, 可读性最高
MAT       scipy       ~18 B      MATLAB 兼容
NPZ (旧)  —           ~18 B      包含冗余 time/voltage
NPZ (紧凑) —           ~2 B      仅存 raw + 元数据 (推荐默认)
HDF5      h5py        ~2 B       gzip 压缩, 适合超大采集
BIN       —           ~2 B       原始二进制, 极致精简
```

紧凑格式（NPZ/HDF5/BIN）不存储 time 和 voltage 数组，改为存储 raw int16 和 preamble 参数，
加载时实时重建，存储体积可减少 **~90%**。

提供 `load_npz()` / `load_h5()` / `load_bin()` 函数可从压缩格式还原完整数据。

### 命令行使用

```bash
# 采集（示波器需已连接）
python -m sds_acquisition.cli

# 指定配置和输出格式
python -m sds_acquisition.cli -c params/my_config.yaml -f csv --plot

# 更多参数
python -m sds_acquisition.cli --help
```

### Python API 使用

```python
from sds_acquisition import SDSInstrument, SDSAcquisition, AcquisitionConfig

# 配置
config = AcquisitionConfig()
config.sampling_rate = 2.5e9       # 2.5 GSa/s
config.sampling_time = 100e-6      # 100 µs 总窗口
config.channels[0].scale = 0.5     # 500 mV/div
config.trigger.mode = "SINGle"     # 单次触发

# 采集
with SDSInstrument("USB0::...") as inst:
    acq = SDSAcquisition(inst)
    results = acq.acquire_all(config)

# 保存
from sds_acquisition import save_to_npz
save_to_npz("data/", results, config.to_dict())
```

### 配置示例 (`params/default_config.yaml`)

```yaml
sampling_rate: 500000         # 500 kSa/s
sampling_time: 0.01           # 10 ms 总采集窗口
channels:
  - number: 1
    enabled: true
    scale: 1.0           # V/div
    offset: 0.0
    coupling: "DC"
trigger:
  mode: "AUTO"           # AUTO=无触发 / SINGle / NORMal
  source: "C1"
  level: 0.0
```
> 总采样点数 = sampling_rate × sampling_time，时基 = sampling_time / 10。

详细说明见 [examples/data_acquisition_demo.ipynb](examples/data_acquisition_demo.ipynb)。

---

## DG4000 信号发生器控制程序 (`signal_generator`)

### 主要功能

| 功能 | SCPI 命令 | 说明 |
|---|---|---|
| 通道绑定 | — | 每个实例绑定固定通道，无需重复传参 |
| 波形输出 | `:APPLy:<shape>` | SINusoid / SQUare / RAMP / PULSe / NOISe / DC |
| 自定义波形 | `:TRACe:DATA:DATA` | 任意周期函数，归一化 [-1, +1] |
| 频率/周期 | `:FREQuency` / `:PERiod` | 1 µHz ~ 160 MHz (依波形类型) |
| 幅度/偏置 | `:VOLTage:AMPLitude/OFFSet` | Vpp / VRMS / DBM 单位可选 |
| 相位控制 | `:PHASe:ADJust` | 0° ~ 360°, 分辨率 0.01° |
| 方波占空比 | `:FUNCtion:SQUare:DCYCle` | 20% ~ 80% |
| 斜波对称度 | `:FUNCtion:RAMP:SYMMetry` | 0% ~ 100% |
| 脉冲参数 | `:PULSe:*` | 脉宽、占空比、延迟、边沿时间 |
| 调制 | `:MOD` | AM / FM / PM / FSK / PWM，内/外调制源可选 |
| 扫描 | `:SWEep` | 线性/对数扫描，起止频率可设 |
| 脉冲串 | `:BURSt` | 触发/门控模式，可设周期数 |
| 谐波输出 | `:HARMonic` | 偶/奇/全谐波，阶次和幅度可设 |
| 参考时钟 | `:ROSCillator:SOURce` | 内部 10 MHz / 外部同步 |
| 输出控制 | `:OUTPut` | 开/关、负载阻抗(1Ω~10kΩ)、极性、同步信号 |
| 状态管理 | `*SAV` / `*RCL` | 保存/恢复仪器状态 |

### 通道绑定模式

每个实例可绑定到固定通道，后续调用无需传 `channel=`：

```python
from signal_generator import DG4000Instrument

ch1 = DG4000Instrument("USB0::...", channel=1)
ch2 = DG4000Instrument("USB0::...", channel=2)

with ch1, ch2:
    ch1.setup_sine(1000, 2.0)    # 自动用 CH1
    ch2.setup_square(100, 3.0)   # 自动用 CH2
```

### 便捷方法

```python
dg.setup_sine(1000, 2.0)          # 正弦波: 1 kHz, 2 Vpp
dg.setup_square(100, 3.0, dcycle=30, phase=45)  # 方波: 占空比 30%, 相位 45°
dg.setup_ramp(10, 4.0, symmetry=30)
dg.setup_pulse(1000, 3.3, width=10e-6)
dg.setup_dc(1.5)                  # DC 输出
dg.setup_noise(1.0, offset=0.0)
```

### 自定义任意波

```python
import numpy as np

t = np.linspace(0, 2*np.pi, 1000, endpoint=False)
y = 0.7 * np.sin(t) + 0.3 * np.sin(3*t)

dg = DG4000Instrument("USB0::...", channel=1)
dg.setup_arbitrary(y, freq=50, amplitude=5.0)
```

### 调制示例

```python
dg.set_mod_type("AM")
dg.set_mod_am_depth(80)
dg.set_mod_am_internal_freq(100)
dg.set_mod_source("INTernal")     # 或 "EXTernal"
dg.set_mod_state(True)
```

### 多仪器配置管理

```python
from signal_generator import SignalGeneratorConfig, MultiGeneratorSetup

# 单台配置
cfg = SignalGeneratorConfig(resource="USB0::...", shape="SINusoid",
                            frequency=1000, amplitude=5.0)
cfg.to_yaml("params/sg1.yaml")

# 多台配置 (YAML 内用 resource 区分)
setup = MultiGeneratorSetup.from_yaml("params/default_sg_config.yaml")
setup.apply_all({inst.resource: inst for inst in [sg1, sg2]})
```

详细说明见 [examples/signal_generator_demo.ipynb](examples/signal_generator_demo.ipynb)。
