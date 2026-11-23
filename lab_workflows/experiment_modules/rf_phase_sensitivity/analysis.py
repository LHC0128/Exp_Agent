"""射频相位灵敏度的完整/分步离线入口。"""
from pathlib import Path

from ...staged_workflow import StagedAnalysis
from . import analysis_steps
from .stages import ANALYSIS, ANALYSIS_KEYS


class AnalysisWorkflow(StagedAnalysis):
    """B01 发布离线产物，科学资格与分析执行状态分开记录。"""

    stages, analysis_keys, steps, final_id = ANALYSIS, ANALYSIS_KEYS, analysis_steps, "B01"
    label = "射频相位灵敏度分析"

    def prepare(self, selected):
        # 同一对象也允许重新执行；以此次指纹对应的配置快照为准。
        self.config, self.params = self.steps.load_config(self.run_dir)

    def run_step(self, item):
        return self.steps.analyze_frames(self.run_dir, self.config, self.params, self.fingerprint)

    def _refresh(self):
        super()._refresh()
        self._discard_old_metrics()

    def persist(self):
        self._discard_old_metrics()
        super().persist()

    def _discard_old_metrics(self):
        if self.states[self.final_id]["status"] != "completed":
            keys = {"status", "error", "backup_directory"}
            if self.states[self.final_id]["status"] == "quality_failed":
                keys.add("quality_passed")
            self.summary = {key: value for key, value in self.summary.items() if key in keys}


def analyze(run_dir: Path, *, step_ids=None, analysis_parameters=None, expected_revision=None):
    """分析指定的封存运行目录；不连接采集设备。"""
    return AnalysisWorkflow.analyze(run_dir, step_ids=step_ids, analysis_parameters=analysis_parameters,
                                    expected_revision=expected_revision)


def main() -> int:
    """按共享分析子进程协议运行 B01。"""
    return AnalysisWorkflow.main()


if __name__ == "__main__":
    raise SystemExit(main())
