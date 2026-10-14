"""XY 控制已知噪声注入实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.requirements import NOISE_SPECTRUM_XY
from ...experiments.typed import TypedWorkflowAdapter
from .models import NoiseSpectrumXYKnownNoiseParams
from .calibration_source import source_choices
from .preparation import preflight
import json


class KnownNoiseAdapter(TypedWorkflowAdapter):
    def schema(self):
        schema = super().schema()
        for field in schema["fields"]:
            if field["name"] == "ANALYSIS_DISPERSION_SOURCE_RUN":
                field["options"] = [dict(value="", label="待选择标定曲线"), *source_choices(self.root)]
        return schema

    def preflight(self, values):
        try:
            preflight(self._resolved(values), self.root)
            return []
        except (OSError, KeyError, TypeError, ValueError) as exc:
            return [str(exc)]

    def analyze(self, run_dir, progress, *, cancellation=None):
        result = super().analyze(run_dir, progress, cancellation=cancellation)
        summary = json.loads((run_dir / "results/analysis_summary.json").read_text(encoding="utf-8"))
        self._write_analysis_status(run_dir, status=summary["status"], error=summary.get("error"))
        return result | {"analysis_status": summary["status"]}

ADAPTER = KnownNoiseAdapter(
    "noise-spectrum-xy-known-noise",
    "Noise_Spectrum_XY_Ctrl_Known_Noise",
    NoiseSpectrumXYKnownNoiseParams,
    "lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.workflow",
    "lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.analysis",
)

DEFINITION = ExperimentDefinition(
    id="noise-spectrum-xy-known-noise",
    title="XY 控制测量已知可控噪声谱",
    category="verification",
    family="noise",
    variant="known-injection",
    description=(
        ('在 Z 小线圈注入已知周期伪噪声，K_Z 由 A11 手动输入（Hz/V），预览与采集不依赖 Z 标定结果目录。运行页以采集、分析两列节点编辑参数并按依赖执行；相邻控制点交替 ON/OFF 顺序，同一温控窗口配对并逐段保存。分析完成全控制轴谱分离后，选择静磁场 v3 直流逐点历史曲线拟合检测增益，与实际波形 PSD × 本轮保存的手动 K_Z² 真值比较；未选曲线时保留 B01～B04 并等待标定。历史目录保留独立分析草稿，保存默认值只影响下一次实验与统一布局。分步等待保持连接、输出和硬件锁；校相质量未通过禁止扫描。锁相输入采用高阻、DC 耦合。 解调时序复用设备库自动／DAQ／Scope：HF2 自动用 DAQ，MFLI 请求 >70 kSa/s 自动用 Scope；频谱按最终实际率计算。' + ' 配置完成后关闭温控并按 SYSTEM_SETTLE_S 稳定，采集至内存，恢复温控后保存；TEC 可连接时仅下发 UI 目标温度。')
    ),
    data_type="Noise_Spectrum_XY_Ctrl_Known_Noise",
    required_devices=("GS200", "DG900", "DG4000", "锁相放大器"),
    required_mapping_keys=NOISE_SPECTRUM_XY,
    execution_mode="typed_workflow",
    schema_version=ADAPTER.params_type.schema_version,
    acquisition_program="experiments/Noise_Spectrum_XY_Ctrl_Known_Noise.py",
    analysis_program="experiments/Noise_Spectrum_XY_Ctrl_Known_Noise_plot.py",
    wiring_notes=(
        "运行前人工确认 Z 链路的电压定义与 K_Z 标定一致。",
        "Time_sequence_2 CH2 经三通连接 dg_comp CH1/CH2 Ext Trig；Z 噪声由 Z_magnetic_field CH1 连续播放，不参与 Burst。",
    ),
    safety_notes=(
        "噪声波形输出极值和 X/Y 包络全部先过 validate_safety_limit 再下发，并检查任意波实际回读。",
        "每点在同一温控窗口内采集两态，相邻点交替先后顺序，每态等待稳定；"
        "正常结束、异常、取消和通信结束关闭 X/Y、Z 与共用触发，温控恢复 5 V DC + ON；保留主场、光功率和 Pump 门控。",
    ),
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
