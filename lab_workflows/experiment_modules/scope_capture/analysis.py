"""完整时域记录的离线校验、Welch PSD 和显示数据；阶段化与分析入口共用职责函数。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import hashlib
import json
import os
import shutil

import numpy as np

from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress, runtime_run_dir
from ..noise_spectrum_xy.state_io import read_state, write_state
from .run_format import EXPERIMENT_ID, analysis_block_reason
from .stages import ANALYSIS, initial_states, invalidate, invalidated_stages, validate_selection

from .analysis_steps import (validate_waveform, calculate_psd, envelope_indices, vertical_metadata,
                             load_inputs, export_display, _json)

ANALYSIS_VERSION = "scope-capture-psd-display-v1"


class AnalysisWorkflow:
    """每次分析绑定具体运行目录；不读取当前默认值或连接仪器。"""

    stages = ANALYSIS
    identity = analysis_block_reason
    analysis_version = ANALYSIS_VERSION

    def __init__(self, run_dir, analysis_parameters=None):
        self.run_dir = Path(run_dir).resolve()
        self.results = self.run_dir / "results"
        self.state_path = self.run_dir / "analysis_state.json"
        import yaml
        config_path = self.run_dir / "experiment_config.yaml"
        self.config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        if reason := analysis_block_reason(self.config):
            raise ValueError(reason)
        if analysis_parameters:
            raise ValueError("本实验没有分析覆盖参数：" + "、".join(sorted(analysis_parameters)))
        self.options = {}
        previous = read_state(self.state_path) or {}
        self.states = initial_states(ANALYSIS) | deepcopy(previous.get("steps", {}))
        self.revision = previous.get("revision", 0)
        self.summary = deepcopy(previous.get("summary", {}))
        self.fingerprint = self.input_fingerprint()
        if previous and previous.get("fingerprint") != self.fingerprint:
            invalidate(ANALYSIS, self.states, "B01")
            self.revision += 1
        for stage in ANALYSIS:
            record = self.states[stage.id]
            if record["status"] == "running" or (record["status"] == "completed" and any(
                    self._file_signature(self.results / name) != record.get("files", {}).get(name)
                    for name in stage.artifacts)):
                invalidate(ANALYSIS, self.states, stage.id)
                self.revision += 1
                break
        if self.states["B03"]["status"] != "completed":
            self.summary["status"] = "stale" if previous else "not_started"

    def input_fingerprint(self):
        import yaml
        config_path = self.run_dir / "experiment_config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        if reason := analysis_block_reason(config):
            raise ValueError(reason)
        clean = {key: value for key, value in config.items()
                 if key not in ("analysis_status", "analysis_error")}
        return dict(analysis_version=ANALYSIS_VERSION,
            config_sha256=hashlib.sha256(json.dumps(clean, sort_keys=True,
                ensure_ascii=False, default=str).encode("utf-8")).hexdigest(),
            raw_files={path.name: self._file_signature(path)
                for path in sorted((self.run_dir / "raw").glob("*")) if path.is_file()})

    @staticmethod
    def _file_signature(path):
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        stat = path.stat()
        return [stat.st_size, stat.st_mtime_ns, *digest.digest()]

    def snapshot(self):
        return dict(run_id=self.run_dir.name, revision=self.revision, options=self.options,
                    steps=self.states, fingerprint=self.fingerprint, summary=self.summary)

    def persist(self):
        write_state(self.state_path, self.snapshot())
        import yaml
        config_path = self.run_dir / "experiment_config.yaml"
        if config_path.exists():
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            config.update(analysis_status=self.summary.get("status", "not_started"),
                          analysis_error=self.summary.get("error"))
            config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        report_runtime_progress("analysis", f"示波器分析：{self.summary.get('status', 'not_started')}",
                                data={"analysis_workflow": self.snapshot()})

    def _archive_from(self, item):
        names = {name for stage in invalidated_stages(ANALYSIS, item) for name in stage.artifacts}
        paths = [self.results / name for name in names if (self.results / name).is_file()]
        if not paths:
            return
        backup = self.results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        backup.mkdir()
        for path in paths:
            shutil.move(str(path), str(backup / path.name))
        self.summary["backup_directory"] = backup.name

    def execute(self, selected, expected_revision=None):
        fingerprint = self.input_fingerprint()
        if fingerprint != self.fingerprint:
            self.fingerprint = fingerprint
            invalidate(ANALYSIS, self.states, "B01")
            self.revision += 1
        for stage in ANALYSIS:
            record = self.states[stage.id]
            if record["status"] == "completed" and any(self._file_signature(self.results / name) !=
                    record.get("files", {}).get(name) for name in stage.artifacts):
                invalidate(ANALYSIS, self.states, stage.id)
                self.revision += 1
                break
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("分析修订已改变，请刷新后重试")
        selected = validate_selection(ANALYSIS, self.states, selected)
        # 归档旧结果之前校验当前 raw；损坏输入不能先移走仍可查看的科学结果。
        try:
            inputs = load_inputs(self.run_dir)
        except BaseException as exc:
            status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "quality_failed"
            self.states["B01"] = {"status": status, "error": str(exc)}
            self.summary.update(status=status, error=str(exc))
            self.revision += 1
            self.persist()
            raise
        self.results.mkdir(exist_ok=True)
        self.revision += 1
        psd = None
        for item in selected:
            check_cancelled()
            self._archive_from(item)
            invalidate(ANALYSIS, self.states, item)
            self.states[item] = {"status": "running", "error": ""}
            self.summary.update(status="running")
            self.summary.pop("error", None)
            self.persist()
            try:
                if item == "B01":
                    inputs = load_inputs(self.run_dir)
                    manifest = dict(run_id=self.run_dir.name, waveform="raw/waveform.npz",
                        sample_count=int(inputs["voltage_v"].size),
                        actual_rate_sa_s=inputs["actual_rate_sa_s"],
                        requested_rate_sa_s=inputs["requested_rate_sa_s"],
                        actual_duration_s=inputs["actual_duration_s"],
                        vertical=inputs["vertical"], fingerprint=self.fingerprint)
                    _json(self.results / "analysis_inputs.json", manifest)
                    self.summary = dict(experiment_id=EXPERIMENT_ID, run_dir=str(self.run_dir),
                        sample_count=manifest["sample_count"], status="partial")
                elif item == "B02":
                    if inputs is None:
                        inputs = load_inputs(self.run_dir)
                    frequency, density, metadata = calculate_psd(
                        inputs["voltage_v"], inputs["actual_rate_sa_s"])
                    np.savez(self.results / "psd.npz", frequency_hz=frequency, psd_v2_hz=density)
                    _json(self.results / "psd.json", {**metadata, "frequency_hz": frequency.tolist(),
                                                      "psd_v2_hz": density.tolist()})
                    psd = (frequency, density, metadata)
                    self.summary.update(status="partial", frequency_resolution_hz=metadata["frequency_resolution_hz"])
                elif item == "B03":
                    if inputs is None:
                        inputs = load_inputs(self.run_dir)
                    if psd is None:
                        psd_path = self.results / "psd.npz"
                        psd_json = json.loads((self.results / "psd.json").read_text(encoding="utf-8"))
                        with np.load(psd_path, allow_pickle=False) as data:
                            psd = (data["frequency_hz"], data["psd_v2_hz"],
                                   {key: value for key, value in psd_json.items()
                                    if key not in ("frequency_hz", "psd_v2_hz")})
                    display = export_display(self.run_dir, inputs, *psd)
                    self.summary.update(status="completed", sample_count=display["sample_count"],
                                        overrange=display["overrange"])
                    self.summary.pop("error", None)
                stage = next(stage for stage in ANALYSIS if stage.id == item)
                signatures = {name: self._file_signature(self.results / name) for name in stage.artifacts}
                if any(value is None for value in signatures.values()):
                    raise RuntimeError(f"{item} 缺少应当输出的分析产物")
                self.states[item] = {"status": "completed", "error": "", "files": signatures}
            except BaseException as exc:
                status = ("cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else
                          "quality_failed" if isinstance(exc, ValueError) else "failed")
                self.states[item] = {"status": status, "error": str(exc)}
                self.summary.update(status=status, error=str(exc))
                raise
            finally:
                try:
                    self.persist()
                except Exception:
                    if self.states[item]["status"] == "completed":
                        raise
        return self.summary


def analyze(run_dir: Path, *, step_ids=None, analysis_parameters=None, expected_revision=None) -> dict:
    """完整分析执行全部阶段；与分步模式共用同一实现。"""
    ctx = AnalysisWorkflow(run_dir, analysis_parameters)
    summary = ctx.execute(step_ids or [stage.id for stage in ANALYSIS], expected_revision)
    return {"run_dir": str(Path(run_dir).resolve()), "sample_count": summary.get("sample_count", 0)}


def main() -> int:
    command = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    if command.get("step_ids"):
        AnalysisWorkflow(runtime_run_dir(), command.get("analysis_parameters")).execute(
            command["step_ids"], command.get("revision"))
    else:
        result = analyze(runtime_run_dir())
        print(f"分析完成：{result['sample_count']} 点，结果位于 {result['run_dir']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkflowCancelled as exc:
        print(str(exc))
        raise SystemExit(130)
