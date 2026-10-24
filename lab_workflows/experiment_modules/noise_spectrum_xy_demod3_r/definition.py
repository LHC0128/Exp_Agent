"""XY DirectAW Demod3 R 噪声谱实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import NoiseSpectrumXYDemod3RParams
from .preparation import preflight
from . import run_format


ADAPTER = TypedWorkflowAdapter(
    "noise-spectrum-xy-demod3-r",
    "Noise_Spectrum_XY_Demod3_R",
    NoiseSpectrumXYDemod3RParams,
    "lab_workflows.experiment_modules.noise_spectrum_xy_demod3_r.workflow",
    "lab_workflows.experiment_modules.noise_spectrum_xy_demod3_r.analysis",
)

def preflight_runner(values):
    try:
        preflight(ADAPTER._resolved(values), ADAPTER.root)
        return []
    except (OSError, KeyError, TypeError, ValueError, PermissionError) as exc:
        return [str(exc)]


DEFINITION = ExperimentDefinition(
    id="noise-spectrum-xy-demod3-r",
    title="XY 控制噪声谱（Demod3 R）",
    category="measurement",
    family="noise",
    variant="direct-aw-demod3-r",
    description="二维扫描 DirectAW 控制频率与 Demod3 解调频率，连续采集平均 R（V，非 PSD）。运行/历史双 Tab 提供 12 个采集和 3 个独立分析节点；保存、预览与预检位于流程容器，节点内编辑参数，应用后显式重跑。保留 Demod0 和 DirectAW 自动校相、批次恢复温控后保存及原脊线标定/洛伦兹拟合。双主电压物理回环仅支持 HF2，MFLI 预检拒绝；AuxOut 缺安全上下限或预计越界禁止写入。",
    data_type="Noise_Spectrum_XY_Demod3_R",
    required_devices=("GS200", "DG900", "DG4000", "lockin_amplifier"),
    required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow",
    acquisition_program="experiments/Noise_Spectrum_XY_Demod3_R.py",
    analysis_program="experiments/Noise_Spectrum_XY_Demod3_R_plot.py",
    wiring_notes=(
        "确认 Demod0 Y → AuxOut2 → Signal Input 2 DC → Demod3 物理回环。",
        "确认 DirectAW X/Y、双路外触发、Pump 门控和 HF2 输入接线。",
    ),
    safety_notes=(
        "控制频率必须能由当前 K/B 标定和固定 AW 输出安全表达。",
        "每批 Demod3 频点结束后恢复温控，取消和异常时执行统一安全收尾。",
    ),
    schema_provider=ADAPTER.schema, schema_version=ADAPTER.params_type.schema_version,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=preflight_runner,
    derive_runner=ADAPTER.derive,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
