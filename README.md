# 实验自动化平台

实验设计、数据采集、数据分析的自动化平台。

## 项目结构

```
├── src/
│   ├── sds_acquisition/       # 示波器控制 (已实现)
│   │   ├── instrument.py      # PyVISA 封装 + SCPI 命令
│   │   ├── acquire.py         # 采集编排器
│   │   ├── config.py          # 配置数据类
│   │   ├── waveform.py        # 波形解析与转换
│   │   ├── save.py            # 数据保存 (CSV/NPZ/MAT)
│   │   └── cli.py             # 命令行入口
│   │
│   ├── signal_generator/      # 信号发生器 — 预留 (DG4000 系列)
│   ├── lockin_amplifier/       # 锁相放大器 — 预留 (Zurich HF2LI)
│   └── experiments/           # 实验脚本 — 预留
│
├── examples/                  # Jupyter notebook 示例
├── data/                      # 原始数据
├── params/                    # 实验参数与配置文件
├── results/                   # 结果图表
├── manuals/                   # 设备编程手册
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
with SDSInstrument() as inst:
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
