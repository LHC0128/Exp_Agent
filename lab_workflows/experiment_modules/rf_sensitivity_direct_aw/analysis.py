"""DirectAW 分阶段离线分析；引擎在 ``lab_workflows.analysis.direct_aw_analysis``。

本模块只声明本实验的阶段清单、运行格式、算法模块与分析参数，并保留原有公开导入；
完整分析与流程图共用同一引擎和产物。
"""

from pathlib import Path

from ...analysis.direct_aw_analysis import ANALYSIS_OPTIONS, DirectAWAnalysis
from ...analysis.direct_aw_analysis import analyze as run_analysis
from ...analysis.direct_aw_analysis import main as run_main
from . import analysis_steps
from .analysis_steps import dispersive, fit_response_curve
from . import run_format
from .stages import MULTI_ANALYSIS, SINGLE_ANALYSIS

ANALYSIS_VERSION = "direct-aw-x-v4-sqrt-amplitude"
CHILD_ARTIFACTS = {stage.id: stage.artifacts for stage in SINGLE_ANALYSIS}


class AnalysisWorkflow(DirectAWAnalysis):
    """DirectAW 单控制/多控制离线分析；差异只在阶段清单与算法模块。"""

    stages = SINGLE_ANALYSIS
    identity = run_format
    analysis_version = ANALYSIS_VERSION
    steps_module = analysis_steps
    label = "RF 灵敏度分析"
    multi_stages = MULTI_ANALYSIS
    child_artifacts = CHILD_ARTIFACTS


def analyze(run_dir: Path, *, workflow_type=AnalysisWorkflow) -> dict:
    return run_analysis(run_dir, workflow_type=workflow_type)


def main(*, workflow_type=AnalysisWorkflow):
    run_main(workflow_type=workflow_type)


if __name__ == "__main__":
    main()
