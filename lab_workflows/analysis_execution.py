"""离线分析的内容指纹、阶段记录和产物归档；不负责实验执行顺序。"""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from .workflow_stages import DependencyRules, Stage, initial_states
from .workflow_state import read_state, write_state


def file_signature(path: Path | str) -> list[int | str] | None:
    """按内容识别文件，保持已发布的 ``[字节数, SHA256]`` 格式。"""
    path = Path(path)
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return [path.stat().st_size, digest.hexdigest()]


def input_fingerprint(
    run_dir: Path | str, config: Mapping[str, Any], *, analysis_version: str,
    extra_inputs: Sequence[str] = (),
) -> dict[str, Any]:
    """指纹覆盖本轮 raw 和显式额外输入；额外输入缺失时仍记录 None。"""
    run_dir = Path(run_dir)
    config = {key: value for key, value in config.items()
              if key not in ("analysis_status", "analysis_error")}
    files = {path.relative_to(run_dir).as_posix(): file_signature(path)
             for path in sorted((run_dir / "raw").rglob("*")) if path.is_file()}
    files.update({name: file_signature(run_dir / name) for name in extra_inputs})
    return dict(config_sha256=hashlib.sha256(json.dumps(config, sort_keys=True,
        ensure_ascii=False, default=str).encode()).hexdigest(),
        analysis_version=analysis_version, raw_files=files)


class AnalysisRunState:
    """完整拥有一个分析会话的共享状态；资格、数值计算与异常分类留给实验。"""

    def __init__(
        self, run_dir: Path | str, *, stages: Sequence[Stage], rules: DependencyRules,
        fingerprint: dict[str, Any], options: Mapping[str, Any] | None = None,
        encoder: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        after_persist: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.run_dir = Path(run_dir).resolve()
        self.results = self.run_dir / "results"
        self.state_path = self.run_dir / "analysis_state.json"
        self.stages = stages
        self.rules = rules
        self.options = dict(options or {})
        self._encoder = encoder
        self._after_persist = after_persist
        previous = read_state(self.state_path) or {}
        self.restored = bool(previous)
        self.states = deepcopy(previous.get("steps") or initial_states(stages))
        self.revision = previous.get("revision", 0)
        self.summary = deepcopy(previous.get("summary", {}))
        self.fingerprint = fingerprint
        if previous and previous.get("fingerprint") != fingerprint:
            self.rules.invalidate(self.stages, self.states, self.stages[0].id)
            self.revision += 1
        self.refresh_products()

    def refresh_products(self) -> None:
        """恢复中断或产物漂移的最早节点；一次刷新只增加一次修订。"""
        for stage in self.stages:
            record = self.states[stage.id]
            if record["status"] == "running" or (record["status"] == "completed" and any(
                file_signature(self.results / name) != signature
                for name, signature in record.get("files", {}).items()
            )):
                self.rules.invalidate(self.stages, self.states, stage.id)
                self.revision += 1
                break

    def prepare(
        self, selected: Sequence[str], *, fingerprint: dict[str, Any],
        expected_revision: int | None = None,
    ) -> list[str]:
        """刷新当前指纹及产物，再校验修订和依赖；成功后增加批次修订。"""
        if fingerprint != self.fingerprint:
            self.rules.invalidate(self.stages, self.states, self.stages[0].id)
            self.fingerprint = fingerprint
            self.revision += 1
        self.refresh_products()
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("分析修订已改变，请刷新后重试")
        selected = self.rules.validate_selection(self.stages, self.states, selected)
        self.revision += 1
        return selected

    def begin(self, stage_id: str) -> None:
        """开始一个已选阶段；取消检查和首次保存由调用方维持原来的位置。"""
        self.rules.invalidate(self.stages, self.states, stage_id)
        self.states[stage_id] = {"status": "running", "error": ""}
        self.summary = {"status": "running", "options": dict(self.options)}

    def _product_record(self, files: Sequence[str]) -> dict[str, Any]:
        return dict(artifacts=list(files),
            files={name: file_signature(self.results / name) for name in files},
            input_fingerprint=deepcopy(self.fingerprint), parameters=dict(self.options),
            parameter_fingerprint=hashlib.sha256(json.dumps(self.options, sort_keys=True).encode()).hexdigest(),
            revision=self.revision)

    def complete(self, stage_id: str, files: Sequence[str], *, status: str) -> None:
        """记录下载文件和当前输入/参数修订；摘要完成程度由实验明确给出。"""
        self.states[stage_id] = {"status": "completed", "error": "", **self._product_record(files)}
        self.summary["status"] = status

    def fail(
        self, stage_id: str, error: BaseException, *, status: str,
        files: Sequence[str] | None = None,
    ) -> None:
        """不重分类或吞掉异常；仅在调用方要求时记录失败阶段的现有产物。"""
        record = {"status": status, "error": str(error)}
        if files is not None:
            record.update(self._product_record(files))
        self.states[stage_id] = record
        self.summary.update(status=status, error=str(error))

    def archive(self, names: Sequence[str]) -> Path | None:
        """只移动调用方明确列出的现有产物，不推断输入或阶段范围。"""
        files = {self.results / name for name in names if (self.results / name).exists()}
        if not files:
            return None
        backup = self.results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        backup.mkdir()
        for path in sorted(files):
            path.replace(backup / path.name)
        return backup

    def snapshot(self) -> dict[str, Any]:
        return dict(run_id=self.run_dir.name, revision=self.revision, options=dict(self.options),
            steps=deepcopy(self.states), fingerprint=self.fingerprint, summary=deepcopy(self.summary))

    def persist(self) -> None:
        """先原子保存分析 JSON，再执行实验的配置/进度接线；异常直接交还调用方。"""
        snapshot = self.snapshot()
        if self._encoder is not None:
            snapshot = self._encoder(snapshot)
        write_state(self.state_path, snapshot)
        if self._after_persist is not None:
            self._after_persist(snapshot)
