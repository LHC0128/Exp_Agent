"""XY Probe AOM 外部 AM 不可控噪声谱实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.requirements import PROBE_AM_NOISE_SPECTRUM_XY
from ...experiments.typed import TypedWorkflowAdapter
from .models import NoiseSpectrumXYProbeAMParams


EXPERIMENT_ID = "noise-spectrum-xy-uncontrolled-probe-am"
DATA_TYPE = "Noise_Spectrum_XY_Ctrl_Uncontrolled_Probe_AM"
ADAPTER = TypedWorkflowAdapter(
    EXPERIMENT_ID, DATA_TYPE, NoiseSpectrumXYProbeAMParams,
    "lab_workflows.experiment_modules.noise_spectrum_xy_probe_am.workflow",
    "lab_workflows.experiment_modules.noise_spectrum_xy_probe_am.analysis",
)

DEFINITION = ExperimentDefinition(
    id=EXPERIMENT_ID,
    title="XY 控制测量已知不可控 Probe AM 噪声谱",
    category="measurement",
    family="noise",
    variant="probe-am-noise",
    description=(
        ('以 Probe 光路 AOM 的 DG4000 外部 AM 输入叠加周期伪随机噪声，运行页以采集、分析两列节点编辑参数并按依赖执行；'
        '每个 XY 控制点在同一温控窗口内交替采集 AM 噪声关闭/开启状态；可选在两态采集期间持续向 Z 线圈注入已知可控噪声，'
        '检验较强可控噪声背景下能否继续提取 Probe AM 差谱。与正弦噪声谱、已知可控噪声实验共用全控制轴分析，'
        '优先独立提取背景 N_S1 和有符号差分 ΔN_S1 = ON − OFF；峰不可辨识时检验两侧远端平台，'
        '受控响应仅展示各态通过验收的部分；自动移动峰标定不足时按配置 K/B 输出暂定 PSD 图并标记质量未通过。'
        '结果为锁相测量端噪声谱，不换算为绝对 Probe 光功率谱；历史目录保留独立分析草稿，保存默认值只影响下一次实验；'
        '分步等待保持连接、输出和硬件锁，校相质量未通过禁止扫描。'
        '锁相设备在设备库中全系统统一选择；启动前检查输入通道和工作频率兼容性；'
        '锁相输入初始化统一关闭 50 Ω 与 AC 耦合，后续配置保持高阻和 DC 耦合。 解调时序复用设备库自动／DAQ／Scope：HF2 自动用 DAQ，MFLI 请求 >70 kSa/s 自动用 Scope；频谱按最终实际率计算。' + ' 配置完成后将温控设为 0 V DC、Output ON，并按 SYSTEM_SETTLE_S 稳定，采集至内存，恢复温控后保存；TEC 可连接时仅下发 UI 目标温度。 正常结束按最近成功校相配置恢复解调器，不下发尚未重跑的采样率或时间常数。')
    ),
    data_type=DATA_TYPE,
    required_devices=("GS200", "DG900", "DG4000", "HF2"),
    required_mapping_keys=PROBE_AM_NOISE_SPECTRUM_XY,
    execution_mode="typed_workflow",
    acquisition_program="experiments/Noise_Spectrum_XY_Ctrl_Uncontrolled_Probe_AM.py",
    analysis_program="experiments/Noise_Spectrum_XY_Ctrl_Uncontrolled_Probe_AM_plot.py",
    wiring_notes=(
        "Probe 光路 AOM 使用新 DG4000：CH1 正弦载波连接 AOM 射频输入；CH2 任意波连接同机背板 Mod/FSK/Trig 外部 AM 输入。",
        "Z_magnetic_field 与 Time_sequence_2 共用另一台 DG4000 的 CH1/CH2；选择可控噪声时，CH1 任意波连接 Z 小线圈并在 Probe AM 两态采集期间持续输出。",
        "确认 Probe AOM 的射频输入与调制极性后再运行；设备手册规定外部 AM 输入范围为 ±2.5 V。",
        "Time_sequence_2 CH2 经三通连接 XY 控制信号源 CH1/CH2 外触发。",
    ),
    safety_notes=(
        "载波幅度由 Probe_AOM_Carrier 限值约束；AM 任意波两个峰值均按 Probe_AOM_AM 限值校验。",
        "ON/OFF 配对只切换 AM 噪声输出；Z 可控噪声在校准后按选项开启并在整段主扫描中保持状态。采集结束关闭 Probe AOM 信号源 CH1、CH2 与 Z 通道，并执行共享安全收尾。",
    ),
    schema_provider=ADAPTER.schema, schema_version=ADAPTER.params_type.schema_version,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
