"""R 标定的独立离线入口，复用分阶段缓存、PSD 和绘图。"""
from ..rf_sensitivity_direct_aw import analysis as shared
from . import run_format
from .stages import ANALYSIS


class AnalysisWorkflow(shared.AnalysisWorkflow):
    stages = ANALYSIS
    identity = run_format
    analysis_version = "direct-aw-r-stages-v1"


def analyze(run_dir):
    return shared.analyze(run_dir, workflow_type=AnalysisWorkflow)


def main():
    shared.main(workflow_type=AnalysisWorkflow)


if __name__ == "__main__":
    main()
