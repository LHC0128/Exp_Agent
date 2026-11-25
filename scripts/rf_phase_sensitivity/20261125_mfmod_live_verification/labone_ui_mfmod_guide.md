# LabOne UI 手动设置 MFLI `MF-MOD` 相干边带相位调制指南

> **为什么不再用多振荡器叠加？**  
> 实验需要在不打断 5 kHz 载波的前提下做 0–360° 平稳移相、同时施加固定 **2° 峰值**相位调制（PM），且尽量降低寄生调幅（AM）。MFLI 的 **`MF-MOD` 选件**把载波（`Osc 2`）与调制源（`Osc 3`）做相位相干合成，并自带严格的调制深度与边带控制，远比手拼多个独立振荡器更准、更易维护。

---

## 一、核心硬件机理：为什么原子读出必须从 `Demod 1` 换到 `Demod 4`

开启 **`MOD 1`** 后，MFLI 硬件会强制分配并使用前三个解调器：

| 解调器通道 | LabOne / 代码路径 | `MOD 1` 开启后的硬件行为 | 能否用于原子读出 |
| --- | --- | --- | --- |
| **`Demod 1`** | `demods/0` | 被 `MOD 1` 载波占用，**其 `phaseshift` 会自动锁死跟随 `MOD 1 Carrier Phase Shift`** | ❌ **不能**（旋转 Y 载波相位时会拖着读出相位一起转） |
| **`Demod 2`** | `demods/1` | 被 `MOD 1` 边带 1（下/上边带）占用 | ❌ **不能** |
| **`Demod 3`** | `demods/2` | 被 `MOD 1` 边带 2 占用 | ❌ **不能** |
| **`Demod 4`** | **`demods/3`** | **完全独立**；不与 `MOD 1` 耦合，可独立选定 `Sig In 1`、`Osc 2` 与任意解调相角 | ✅ **必须用这路** |

**实机验证结论**（见 `_mfli_mod_verification/verification_summary.json`）：  
在 `MOD 1` 开启且 `Carrier Phase Shift = 0.0°` 的状态下，`Demod 4`（`demods/3`）独立设置为 `oscselect = 1 (Osc 2, 5 kHz)`、`adcselect = 0 (Sig In 1)`、`phaseshift = -42.5°`，`daq.acquire_data` 稳定以 `1674.11 Sa/s` 采集正交分量 `sample.x / sample.y`，与输出端载波移相互不串扰。

---

## 二、LabOne UI 逐步操作步骤

请按以下顺序在 LabOne Web UI 中配置（建议先确认物理接线：`Out 1` 接 Y 方向高阻 RF 回路与示波器 CH4，`Sig In 1` 接探测器输出，MFLI 背面 `Clk In 10 MHz` 接实验室公共 10 MHz 时钟）：

### 第 1 步：时钟同步（`Config` 选项卡）

1. 打开 **Config** → **Clock / Reference** 区域。
2. 将 **Reference Clock** 切换为 **`External`**（对应节点 `system/extclk = 1`）。
3. 确认右侧指示灯显示已锁定到外部 **10 MHz** 时钟。  
   *作用*：保证 MFLI 与 Z 脉冲触发源 `Time_sequence_2`（同接 10 MHz 外部参考）严格同频，避免载波相对于 Z 脉冲触发发生连续相位漂移。

### 第 2 步：频率分配（`Oscillators` 选项卡）

1. 找到 **`Osc 2`**（代码 `oscs/1`），将频率设为 **`5000 Hz`**（**必须与载波和 `Demod 4` 共用此振荡器**）。
2. 找到 **`Osc 3`**（代码 `oscs/2`），将频率设为所需的基带相位调制频率 $f_m$（例如 **`20 Hz`**）。

### 第 3 步：配置 `MOD 1` 相位调制（`MOD` 选项卡）

> 先保持界面右上角 **`MOD 1` Enable** 处于关闭状态，把下面全部参数填好后再打开。

1. **Mode 与调制深度**：
   - **Mode** 下拉选择 **`FM`**；
   - 关闭 **Freq Dev Enable**（切换为 **Modulation Index** 模式，即 `freqdevenable = 0`）；
   - 将 **Index** 设为 `2°` 对应的弧度值 **`0.0349066 rad`**（若 UI 允许单位选 `deg`，直接填 **`2 deg`**，界面会自动换算为窄带近似下的相干边带系数）。
2. **Carrier 行**：
   - **Oscillator** 选 **`Osc 2`（5 kHz）**；
   - **Amplitude** 填 Y 向正弦射频的**峰值电压** $V_{\mathrm{pk}} = V_{\mathrm{pp}} / 2$（注意不是峰峰值！例如 `Y_RF_VPP = 0.05 Vpp` 时这里填 **`0.025 Vpk`**；Stage 2 验证 `0.2 Vpp` 时填 **`0.10 Vpk`**）；
   - **Phase Shift**：这里就是 Y 射频的连续载波相位 $\varphi_{\mathrm{RF}}$（修改它即可 `0–360°` 平滑移相，且不切档位几乎零非线性误差）；
   - 勾选 Carrier 行的 **Enable**。
3. **Sideband 1 / Sideband 2 行**：
   - 两行的 **Oscillator** 都选择 **`Osc 3`（$f_m$）**；
   - 做**无调制纯载波**（静态扫工作点、`K0` 标定、无调制噪声基线）时：**取消勾选** Sideband 1 与 Sideband 2 的 Enable；
   - 做**2° 峰值相位调制**（对准、频响扫频）时：**同时勾选** Sideband 1 与 Sideband 2 的 Enable。  
   *关键优势*：整个过程中保持 Carrier Enable 与 `Out 1` ON 不中断，只开关两个 Sideband Enable，载波中心相位跳变实测仅约 `+0.0027°`（在 `0.03°` 以内）。
4. **Output 与总使能**：
   - 在 **Output** 下拉选择 **`Out 1`**；
   - 最后打开 **`MOD 1` 总开关 Enable**。

### 第 4 步：输出端与直流偏置（`In / Out` → `Out 1`）

1. 在 **`Out 1` Range** 选择覆盖总包络 $|V_{\mathrm{offset}}| + (1 + \beta) V_{\mathrm{pk}}$ 的离散量程档位（MFLI 可选 `10 mV / 100 mV / 1 V / 10 V`）。  
   *注意*：切换量程或大幅改动幅值会带来轻微静态硬件相移（例如从 `0.1 Vpk` 切换到 `0.25 Vpk` 实测偏移 `+0.308°`），手动实验时请先定好量程并固定，不要在扫工作点与扫频响中途更换量程档位。
2. **Offset** 填入 Y 方向直流偏置 **`Y_BIAS_V`**（不加偏置设为 `0 V`）。
3. 将 `Out 1` 总开关拨为 **On**。

### 第 5 步：原子信号独立解调（`Lock-In` 选项卡）

1. 忽略已被占用的 `Demod 1–3`，只启用 **`Demod 4`**：
2. **Input Signal** 选 **`Sig In 1`**，**Oscillator** 选 **`Osc 2`（5 kHz）**；
3. 按实验要求设置输入量程（如 `3 V`）、滤波阶数（`Order = 4`）、时间常数（`TC = 1 ms`）与传输采样率（`2000 Sa/s`，MFLI 实际档位约为 `1674.11 Sa/s`），勾选输出 **Enable**；
4. 需要把原子响应切线转入 `sample.y`（实现 `sample.x` 斜率归零）时，**只旋转 `Demod 4` 的 `Phase`**，不要再动 `MOD 1 Carrier Phase Shift`。

---

## 三、UI 设置与正式实验程序节点对照表

| 实验作用 | LabOne UI 位置 | 程序底层节点（`dev30661`） |
| --- | --- | --- |
| 10 MHz 外部时钟锁定 | `Config → Reference Clock = External` | `/dev30661/system/extclk` (`1`) |
| 5 kHz 载波振荡器 | `Oscillators → Osc 2 Frequency` | `/dev30661/oscs/1/freq` (`5000.0`) |
| 基带调制频率 $f_m$ | `Oscillators → Osc 3 Frequency` | `/dev30661/oscs/2/freq` |
| 调制模式与指数 | `MOD 1 → FM / Index (rad)` | `mods/0/mode=1`, `freqdevenable=0`, `index=rad(2°)` |
| 载波峰幅 `Vpk` 与载波相位 | `MOD 1 → Carrier Amp / Phase Shift` | `mods/0/carrier/amplitude`, `mods/0/carrier/phaseshift` |
| 相干调制开关 | `MOD 1 → Sideband 1 & 2 Enable` | `mods/0/sidebands/0,1/enable` (`0`/`1`) |
| 叠加输出到 Out 1 | `MOD 1 → Output` & `MOD 1 Enable` | `mods/0/output=1`, `mods/0/enable=1` |
| 输出量程 / Y 直流偏置 / 开关 | `In/Out → Out 1` | `sigouts/0/range`, `sigouts/0/offset`, `sigouts/0/on` |
| 独立原子读出（第 4 路） | `Lock-In → Demod 4` (`Osc 2`, `Sig In 1`) | `demods/3/oscselect=1`, `adcselect=0`, `phaseshift`, `rate` |

---

## 四、手动安全检查与常见误区

1. **安全开关顺序**：关闭输出时，先将 **`Out 1` 切回 `Off`**（`sigouts/0/on = 0`），再清零 `Carrier Amplitude`、`Offset` 并关闭 `Sidebands` 与 `MOD 1 Enable`；直接点 `MOD Enable Off` 时若 `Out 1` 还保留较大 DC `Offset`，线圈上仍会留直流。
2. **输入物理极限**：[safety_limits.yaml](../../../params/safety_limits.yaml) 当前将 `mfli_dev30661_signal_output_0` 设在 `±10.0 V` 宽松边界；但原子气室在 `5 kHz` 实机已证 **`0.5 Vpp`（`0.25 Vpk`）严重过饱和成方波**，线性工作区约在 **`0.04–0.06 Vpp`（`0.02–0.03 Vpk`）**，建议手调时先从低幅度起步。
3. **误用 `Demod 1` 的典型现象**：如果在 `MOD 1` 开启后仍读取 `Demod 1`，会出现“每次调 Y 相移工作的同时，解调器自身相位跟着跳”，导致 `X/Y` 轨迹无法在固定实验室正交坐标系下拟合工作点；只要确认切到 **`Demod 4`** 即立即消除。
