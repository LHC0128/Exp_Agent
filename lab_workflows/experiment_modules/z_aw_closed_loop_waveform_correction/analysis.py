"""实际电流闭环的分步分析；复用内容指纹、归档和目录隔离。"""

from pathlib import Path
import json
import math
import os

import numpy as np
import yaml

from ...analysis_execution import AnalysisRunState, file_signature, input_fingerprint
from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress, runtime_run_dir
from ...workflow_stages import DependencyRules
from ...workflow_state import write_state
from . import analysis_steps, run_format
from .stages import ANALYSIS

EXPERIMENT_ID = run_format.EXPERIMENT_ID


def _plain(value):
    """保留旧会话 JSON 对科学数值及非有限值的转换约定。"""
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_plain(item) for item in value]
    if isinstance(value, np.generic):
        return _plain(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class AnalysisWorkflow:
    stages = ANALYSIS

    def __init__(self, run_dir, analysis_parameters=None):
        run_dir = Path(run_dir).resolve()
        self.config = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
        if reason := run_format.analysis_block_reason(self.config):
            raise ValueError(reason)
        if analysis_parameters:
            raise ValueError("本实验没有可调分析覆盖参数")
        # 当前指纹显式传入，公共状态构造不回调尚未初始化的实验对象。
        fingerprint = input_fingerprint(run_dir, self.config, analysis_version="z-closed-loop-saved-v1",
            extra_inputs=("results/iteration_summary.yaml", "results/corrected_control_waveform.npz"))
        self._analysis = AnalysisRunState(run_dir, stages=self.stages,
            rules=DependencyRules(finish_id=None), fingerprint=fingerprint,
            encoder=_plain, after_persist=self._after_persist)
        if self.states["B02"]["status"] != "completed":
            self.summary["status"] = "stale" if self._analysis.restored else "not_started"

    @property
    def run_dir(self):
        return self._analysis.run_dir

    @property
    def results(self):
        return self._analysis.results

    @property
    def state_path(self):
        return self._analysis.state_path

    @property
    def config_path(self):
        return self.run_dir / "experiment_config.yaml"

    @property
    def options(self):
        return self._analysis.options

    @property
    def states(self):
        return self._analysis.states

    @property
    def revision(self):
        return self._analysis.revision

    @property
    def summary(self):
        return self._analysis.summary

    @property
    def fingerprint(self):
        return self._analysis.fingerprint

    _file_signature = staticmethod(file_signature)

    def input_fingerprint(self):
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        if reason := run_format.analysis_block_reason(self.config):
            raise ValueError(reason)
        return input_fingerprint(self.run_dir, self.config, analysis_version="z-closed-loop-saved-v1",
            extra_inputs=("results/iteration_summary.yaml", "results/corrected_control_waveform.npz"))

    def refresh_products(self):
        self._analysis.refresh_products()

    def snapshot(self):
        return self._analysis.snapshot()

    def persist(self):
        self._analysis.persist()

    def _after_persist(self, snapshot):
        # 只更新分析状态，不把历史目录的分析覆盖值写入采集快照。
        config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        config.update(analysis_status=self.summary.get("status", "not_started"), analysis_error=self.summary.get("error"))
        self.config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        report_runtime_progress("analysis", "离线分析状态已保存", data={"analysis_workflow": snapshot})

    def product_files(self, stage):
        names = stage.artifacts if stage.id == "B01" else (
            "final_waveform_report.yaml", "convergence.png", "convergence.pdf",
            "waveform_comparison.png", "waveform_comparison.pdf", "waveform_comparison.csv",
            "amplitude_bandwidth_diagnosis.png", "amplitude_bandwidth_diagnosis.pdf", "diagnosis.md")
        return [name for name in names if (self.results / name).is_file()]

    def _archive_from(self, item):
        # 轮次汇总、冻结 NPZ/CSV 和恢复溯源是既有输入，不能移走。
        affected = self.stages if item == "B01" else self.stages[1:]
        self._analysis.archive([name for stage in affected for name in self.product_files(stage)])

    def execute(self, selected, expected_revision=None):
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        if reason := run_format.analysis_block_reason(self.config):
            raise ValueError(reason)
        selected = self._analysis.prepare(selected, fingerprint=self.input_fingerprint(),
            expected_revision=expected_revision)
        for item in selected:
            check_cancelled()
            self._analysis.begin(item)
            failure = None
            try:
                self.persist()
                # 当前输入合格后才移动旧产物；坏数据不能搬走上次完整结果。
                analysis_steps.load_inputs(self.run_dir)
                if self.input_fingerprint() != self.fingerprint:
                    raise ValueError("分析输入在执行中改变，请从 B01 重新执行")
                self._archive_from(item)
                if item == "B01":
                    inputs = analysis_steps.load_inputs(self.run_dir)
                    write_state(self.results / "analysis_inputs.json", inputs)
                    self.summary.update(inputs)
                elif item == "B02":
                    self.summary.update(analysis_steps.analyze(self.run_dir))
                stage = next(stage for stage in self.stages if stage.id == item)
                files = self.product_files(stage)
                if missing := set(stage.artifacts) - set(files):
                    raise RuntimeError("分析阶段缺少产物：" + "、".join(sorted(missing)))
                self._analysis.complete(item, files, status="completed" if item == "B02" else "partial")
            except BaseException as exc:
                failure = exc
                status = ("cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else
                          "quality_failed" if isinstance(exc, ValueError) else "failed")
                self._analysis.fail(item, exc, status=status)
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
    return ctx.execute([stage.id for stage in ANALYSIS])


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    if payload.get("step_ids"):
        AnalysisWorkflow(runtime_run_dir(), payload.get("analysis_parameters")).execute(payload["step_ids"], payload.get("revision"))
    else:
        analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
