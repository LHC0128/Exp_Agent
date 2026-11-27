"""标准 Mx 相图与噪声路径的完整/分步离线分析；骨架来自 StagedAnalysis。"""
import json
import os
from pathlib import Path

from lab_workflows.staged_workflow import StagedAnalysis

from ...experiment_runtime import runtime_run_dir
from . import analysis_steps
from .stages import ANALYSIS, ANALYSIS_KEYS


class AnalysisWorkflow(StagedAnalysis):
    stages, analysis_keys, steps, final_id = ANALYSIS, ANALYSIS_KEYS, analysis_steps, "B01"
    label = "标准 Mx 相图与噪声路径分析"

    def run_step(self, item):
        return analysis_steps.analyze_data(self.run_dir, self.config)


def analyze(run_dir: Path, *, step_ids=None, analysis_parameters=None, expected_revision=None):
    return AnalysisWorkflow.analyze(run_dir, step_ids=step_ids, analysis_parameters=analysis_parameters,
                                    expected_revision=expected_revision)


def main() -> None:
    """读取子进程运行目录与分步选择并调用离线分析。"""
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    analyze(runtime_run_dir(), step_ids=payload.get("step_ids"),
            analysis_parameters=payload.get("analysis_parameters"), expected_revision=payload.get("revision"))


if __name__ == "__main__":
    main()
