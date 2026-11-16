"""完整/分步离线分析；骨架来自 StagedAnalysis，算法在 analysis_steps。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from lab_workflows.staged_workflow import StagedAnalysis

from ...experiment_runtime import runtime_run_dir
from . import analysis_steps as steps
from .stages import ANALYSIS, ANALYSIS_KEYS


class AnalysisWorkflow(StagedAnalysis):
    stages, analysis_keys, steps, final_id = ANALYSIS, ANALYSIS_KEYS, steps, "B04"
    label = "线圈常数标定分析"

    def run_step(self, item):
        if item == "B01":
            inputs = steps.load_inputs(self.run_dir, self.config, self.params)
            return dict(scan_entry_count=len(inputs["entries"]),
                        near_zero_skipped=sum(entry["near_zero_skipped"] for entry in inputs["entries"]))
        if item == "B02":
            return steps.fit_curves(self.run_dir, self.params, self.config)
        if item == "B03":
            return steps.evaluate(self.run_dir, self.params, self.config)
        return steps.report(self.run_dir, self.params, self.config)


def analyze(run_dir: Path, *, step_ids=None, analysis_parameters=None, expected_revision=None):
    return AnalysisWorkflow.analyze(run_dir, step_ids=step_ids, analysis_parameters=analysis_parameters,
                                    expected_revision=expected_revision)


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    analyze(runtime_run_dir(), step_ids=payload.get("step_ids"),
            analysis_parameters=payload.get("analysis_parameters"), expected_revision=payload.get("revision"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
