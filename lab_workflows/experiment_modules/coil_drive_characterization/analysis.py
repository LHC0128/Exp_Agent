"""完整/分步离线分析；骨架来自 StagedAnalysis，算法在 analysis_steps 与 processing。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from lab_workflows.staged_workflow import StagedAnalysis

from ...experiment_runtime import runtime_run_dir
from . import analysis_steps as steps
from .stages import ANALYSIS, ANALYSIS_KEYS


class AnalysisWorkflow(StagedAnalysis):
    stages, analysis_keys, steps, final_id = ANALYSIS, ANALYSIS_KEYS, steps, "B02"
    label = "线圈驱动分析"

    def run_step(self, item):
        if item == "B01":
            return steps.transfer(self.run_dir, self.params)
        b01 = json.loads(steps.output_path(self.run_dir, "results/transfer.json").read_text(encoding="utf-8"))
        return steps.pulses(self.run_dir, self.params, b01)


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
