"""泄露响应分阶段分析；共用内容指纹、归档和历史参数隔离。"""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import os
from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, runtime_run_dir
from lab_workflows.workflow_state import read_state
from ..mx_z_optimal_control_xy_rf_sensitivity.analysis import AnalysisWorkflow as GridAnalysisWorkflow
from . import analysis_steps, run_format
from .stages import ANALYSIS, invalidate, validate_selection
from lab_workflows.workflow_stages import initial_states

EXPERIMENT_ID = run_format.EXPERIMENT_ID


class AnalysisWorkflow(GridAnalysisWorkflow):
    stages = ANALYSIS

    def __init__(self, run_dir, analysis_parameters=None):
        self.run_dir = Path(run_dir).resolve()
        self.results = self.run_dir / "results"
        self.state_path = self.run_dir / "analysis_state.json"
        self.config_path = self.run_dir / "experiment_config.yaml"
        self.config = analysis_steps._load_yaml(self.config_path)
        if reason := run_format.analysis_block_reason(self.config):
            raise ValueError(reason)
        if analysis_parameters:
            raise ValueError("本实验没有可调分析覆盖参数")
        previous = read_state(self.state_path) or {}
        self.options = {}
        self.states = deepcopy(previous.get("steps") or initial_states(self.stages))
        self.revision = previous.get("revision", 0)
        self.summary = deepcopy(previous.get("summary", {}))
        self.fingerprint = self.input_fingerprint()
        if previous and previous.get("fingerprint") != self.fingerprint:
            invalidate(self.stages, self.states, "B01")
            self.revision += 1
        self.refresh_products()

    def input_fingerprint(self):
        return super().input_fingerprint() | {"analysis_version": "mx-z-xy-leakage-response-v1"}

    def execute(self, selected, expected_revision=None):
        self.config = analysis_steps._load_yaml(self.config_path)
        if reason := run_format.analysis_block_reason(self.config):
            raise ValueError(reason)
        fingerprint = self.input_fingerprint()
        if fingerprint != self.fingerprint:
            invalidate(self.stages, self.states, "B01")
            self.fingerprint = fingerprint
            self.revision += 1
        self.refresh_products()
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("分析修订已改变，请刷新后重试")
        selected = validate_selection(self.stages, self.states, selected)
        self.revision += 1
        for item in selected:
            check_cancelled()
            invalidate(self.stages, self.states, item)
            self.states[item] = {"status": "running", "error": ""}
            self.summary = {"status": "running", "options": {}}
            failure = None
            try:
                self.persist()
                # 当前输入合格后才移动旧产物；坏数据不能搬走上次完整结果。
                analysis_steps.load_inputs(self.run_dir)
                self._archive_from(item)
                if item == "B02":
                    self.summary.update(analysis_steps.evaluate(self.run_dir))
                elif item == "B03":
                    self.summary.update(analysis_steps.write_report(self.run_dir))
                stage = next(stage for stage in self.stages if stage.id == item)
                files = self.product_files(stage)
                if missing := set(stage.artifacts) - set(files):
                    raise RuntimeError("分析阶段缺少产物：" + "、".join(sorted(missing)))
                self.states[item] = {"status": "completed", "error": "", "artifacts": files,
                    "files": {name: self._file_signature(self.results / name) for name in files},
                    "input_fingerprint": deepcopy(self.fingerprint), "parameters": {},
                    "parameter_fingerprint": hashlib.sha256(b"{}").hexdigest(), "revision": self.revision}
                self.summary["status"] = "completed" if item == "B03" else "partial"
            except BaseException as exc:
                failure = exc
                status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "failed"
                self.states[item] = {"status": status, "error": str(exc)}
                self.summary.update(status=status, error=str(exc))
                raise
            finally:
                try:
                    self.persist()
                except Exception:
                    if failure is None:
                        raise
        return self.summary


def analyze(run_dir):
    ctx = AnalysisWorkflow(run_dir)
    ctx.execute([stage.id for stage in ANALYSIS])
    return json.loads((ctx.results / "analysis.json").read_text(encoding="utf-8"))


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    if payload.get("step_ids"):
        AnalysisWorkflow(runtime_run_dir(), payload.get("analysis_parameters")).execute(payload["step_ids"], payload.get("revision"))
    else:
        result = analyze(runtime_run_dir())
        print(f"Mx Z 最优控制 XY 泄露响应分析完成: {result['run_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
