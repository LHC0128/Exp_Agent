"""完整/分步离线分析；骨架来自 StagedAnalysis，算法在 analysis_steps、fitting 与 analysis_plotting。"""
import json
import os
from pathlib import Path

from lab_workflows.staged_workflow import StagedAnalysis

from ...experiment_runtime import runtime_run_dir
from . import analysis_plotting
from . import analysis_steps as steps
from .stages import ANALYSIS, ANALYSIS_KEYS


class AnalysisWorkflow(StagedAnalysis):
    stages, analysis_keys, steps, final_id = ANALYSIS, ANALYSIS_KEYS, steps, "B03"
    label = "BB 占空比分析"

    def prepare(self, selected):
        # 缺失、损坏和采样网格不一致先拒绝；不移动仍可查看的旧科学产物。
        self.data = steps.load_inputs(self.run_dir, self.params, compute="B01" in selected)
        for stage in self.stages:
            for name in stage.artifacts:
                steps.output_path(self.run_dir, "results/" + name)

    def run_step(self, item):
        if item == "B01":
            return steps.write_inputs(self.run_dir, self.data)
        if item == "B02":
            return steps.fit(self.run_dir, self.params, self.data)
        analysis_plotting.render(self.run_dir)
        return dict(self.summary)


def analyze(run_dir: Path, *, step_ids=None, analysis_parameters=None, expected_revision=None):
    return AnalysisWorkflow.analyze(run_dir, step_ids=step_ids, analysis_parameters=analysis_parameters,
                                    expected_revision=expected_revision)


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    analyze(runtime_run_dir(), step_ids=payload.get("step_ids"), analysis_parameters=payload.get("analysis_parameters"),
        expected_revision=payload.get("revision"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
