# %% 分阶段离线分析，不连接硬件
#
# 分析引擎在共享层 `lab_workflows.analysis.direct_aw_analysis`；本模块只声明本实验的
# 阶段清单、运行格式、算法模块与整轮多控制配置，并保留原有公开入口。
from ...analysis.direct_aw_analysis import DirectAWAnalysis
from ...analysis.direct_aw_analysis import analyze as run_analysis
from ...analysis.direct_aw_analysis import main as run_main
from ..rf_sensitivity_direct_aw.analysis import CHILD_ARTIFACTS
from ..rf_sensitivity_direct_aw.stages import MULTI_ANALYSIS
from . import run_format, analysis_steps, stages


class AnalysisWorkflow(DirectAWAnalysis):
    stages = stages.ANALYSIS
    identity = run_format
    analysis_version = "direct-aw-known-noise-v5-sqrt-amplitude"
    steps_module = analysis_steps
    label = "RF 灵敏度分析"
    multi_stages = MULTI_ANALYSIS
    child_artifacts = CHILD_ARTIFACTS


def analyze(run_dir):
    return run_analysis(run_dir, workflow_type=AnalysisWorkflow)


def main():
    run_main(workflow_type=AnalysisWorkflow)


if __name__ == "__main__":
    main()
