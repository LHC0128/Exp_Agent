# %% 分阶段离线分析，不连接硬件
from ..rf_sensitivity_direct_aw import analysis as shared
from . import run_format, analysis_steps, stages


class AnalysisWorkflow(shared.AnalysisWorkflow):
    stages = stages.ANALYSIS
    identity = run_format
    analysis_version = "direct-aw-known-noise-v4-noise-quality"
    steps_module = analysis_steps


def analyze(run_dir):
    return shared.analyze(run_dir, workflow_type=AnalysisWorkflow)


def main():
    shared.main(workflow_type=AnalysisWorkflow)


if __name__ == "__main__":
    main()
