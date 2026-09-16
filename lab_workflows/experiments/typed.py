"""显式参数模型驱动的实验适配器，不扫描或改写实验源码。"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Generic, TypeVar

import yaml

from ..common import (
    CancellationToken,
    ProgressCallback,
    ProgressEvent,
    WorkflowCancelled,
    emit,
    find_project_root,
)
from ..experiment_params import ExperimentParams
from ..experiment_runtime import PROGRESS_PREFIX, PROGRESS_PROTOCOL_ENV, format_eta


ParamsT = TypeVar("ParamsT", bound=ExperimentParams)


class TypedWorkflowAdapter(Generic[ParamsT]):
    """将强类型参数、默认 YAML、采集模块和分析模块接入统一契约。"""

    def __init__(
        self,
        experiment_id: str,
        data_type: str,
        params_type: type[ParamsT],
        acquisition_module: str,
        analysis_module: str | None = None,
    ) -> None:
        self.root = find_project_root()
        self.id = experiment_id
        self.data_type = data_type
        self.params_type = params_type
        self.acquisition_module = acquisition_module
        self.analysis_module = analysis_module
        self.defaults_path = self.root / "params" / "experiments" / f"{experiment_id}.yaml"

    def defaults(self) -> ParamsT:
        return self.params_type.from_yaml(self.defaults_path)  # type: ignore[return-value]

    def _resolved(self, values: dict[str, Any]) -> ParamsT:
        merged = self.defaults().to_external()
        merged.update(values)
        return self.params_type.from_external(merged)  # type: ignore[return-value]

    def schema(self) -> dict[str, Any]:
        return self.defaults().schema(self.root)

    def derive(self, values: dict[str, Any]) -> dict[str, Any]:
        """按当前表单值计算只读派生显示值；失败时返回空映射。"""
        merged = self.defaults().to_external()
        merged.update(values)
        try:
            return self.params_type.derive_external(merged)
        except Exception:
            return {}

    def save_defaults(self, values: dict[str, Any]) -> None:
        params = self._resolved(values)
        errors = params.validate(self.root)
        if errors:
            raise ValueError("；".join(errors))
        params.save_yaml(self.defaults_path)

    def preflight(self, values: dict[str, Any]) -> list[str]:
        try:
            return self._resolved(values).validate(self.root)
        except (TypeError, ValueError) as exc:
            return [str(exc)]

    def _execute(
        self,
        module: str,
        *,
        parameters: dict[str, Any] | None = None,
        run_dir: Path | None = None,
        progress: ProgressCallback | None = None,
        cancellation: CancellationToken | None = None,
    ) -> None:
        job_dir = self.root / "data" / ".gui_jobs"
        job_dir.mkdir(parents=True, exist_ok=True)
        token = f"{self.id}_{os.getpid()}_{threading.get_ident()}"
        cancel_path = job_dir / f"{token}.cancel"
        environment = os.environ.copy()
        environment.update({
            "LAB_EXPERIMENT_ID": self.id,
            "LAB_CANCEL_FILE": str(cancel_path),
            "MPLBACKEND": "Agg",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        })
        if parameters is not None:
            environment["LAB_TYPED_PARAMETERS"] = json.dumps(parameters, ensure_ascii=False)
        if run_dir is not None:
            environment["LAB_RUN_DIR"] = str(run_dir.resolve())
        if progress is not None:
            environment[PROGRESS_PROTOCOL_ENV] = "jsonl"
        command = [
            str(self.root / "agent_exp_env" / "Scripts" / "python.exe"),
            "-u",
            "-m",
            module,
        ]
        process = subprocess.Popen(
            command,
            cwd=self.root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        def watch_cancel() -> None:
            while cancellation and process.poll() is None:
                if cancellation.wait(0.1):
                    cancel_path.touch(exist_ok=True)
                    return

        watcher = threading.Thread(target=watch_cancel, daemon=True)
        watcher.start()
        assert process.stdout is not None
        try:
            for line in process.stdout:
                message = line.rstrip()
                if not message:
                    continue
                if message.startswith(PROGRESS_PREFIX):
                    event = self._decode_progress(message[len(PROGRESS_PREFIX):])
                    if event is None:
                        continue
                    if progress is None:
                        eta = event.data.get("estimated_remaining_seconds")
                        print(f"[{event.stage}] {event.message}"
                              + (f"（预计剩余 {format_eta(eta)}）" if isinstance(eta, (int, float)) else ""),
                              flush=True)
                    else:
                        progress(event)
                elif progress is None:
                    print(message, flush=True)
                else:
                    emit(progress, "running", message)
            return_code = process.wait()
        finally:
            cancel_path.unlink(missing_ok=True)
        if cancellation and cancellation.cancelled:
            raise WorkflowCancelled("实验已在安全检查点停止")
        if return_code:
            raise RuntimeError(f"模块 {module} 异常结束，退出码 {return_code}")

    @staticmethod
    def _decode_progress(raw: str) -> ProgressEvent | None:
        """解码子进程 JSONL 进度行；畸形行按普通日志处理返回 None。"""
        try:
            payload = json.loads(raw)
            return ProgressEvent(
                stage=str(payload["stage"]),
                message=str(payload["message"]),
                percent=payload["percent"] if payload.get("percent") is None else float(payload["percent"]),
                level=str(payload.get("level", "info")),
                data=dict(payload.get("data") or {}),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _latest_run(self, before: set[Path]) -> Path | None:
        base = self.root / "data" / self.data_type
        if not base.exists():
            return None
        runs = [item for item in base.iterdir() if item.is_dir()]
        created = [item for item in runs if item not in before]
        candidates = created or runs
        return max(candidates, key=lambda item: item.stat().st_mtime) if candidates else None

    @staticmethod
    def _write_analysis_status(
        run_dir: Path,
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        """把自动分析状态写回运行配置，便于 GUI 和后续排查。"""
        config_path = run_dir / "experiment_config.yaml"
        if not config_path.is_file():
            return
        try:
            with config_path.open(encoding="utf-8") as stream:
                config = yaml.safe_load(stream) or {}
            if not isinstance(config, dict):
                return
            config["analysis_status"] = status
            config["analysis_error"] = error
            with config_path.open("w", encoding="utf-8") as stream:
                yaml.safe_dump(config, stream, allow_unicode=True, sort_keys=False)
        except Exception:
            # 分析状态是诊断信息，不能覆盖原始分析异常。
            return

    def run(
        self,
        values: dict[str, Any],
        progress: ProgressCallback | None,
        cancellation: CancellationToken | None,
    ) -> dict[str, Any]:
        params = self._resolved(values)
        errors = params.validate(self.root)
        if errors:
            raise ValueError("；".join(errors))
        base = self.root / "data" / self.data_type
        before = set(base.iterdir()) if base.exists() else set()
        emit(progress, "start", f"启动 {self.id}", 0)
        self._execute(
            self.acquisition_module,
            parameters=params.to_external(),
            progress=progress,
            cancellation=cancellation,
        )
        run_dir = self._latest_run(before)
        if self.analysis_module:
            if run_dir is None:
                raise RuntimeError(
                    f"自动分析失败：采集完成后未找到 {self.data_type} 运行目录"
                )
            try:
                self.analyze(run_dir, progress, cancellation=cancellation)
            except WorkflowCancelled:
                raise
            except Exception as exc:
                emit(progress, "analysis", f"自动分析失败: {exc}", 95, "error")
                raise RuntimeError(
                    f"自动分析失败（运行目录: {run_dir}）：{exc}"
                ) from exc
        artifacts = []
        if run_dir and (run_dir / "results").exists():
            artifacts = [str(path) for path in (run_dir / "results").iterdir() if path.is_file()]
        return {
            "experiment_id": self.id,
            "run_dir": str(run_dir) if run_dir else None,
            "artifacts": artifacts,
        }

    def analyze(
        self,
        run_dir: Path,
        progress: ProgressCallback | None,
        *,
        cancellation: CancellationToken | None = None,
    ) -> dict[str, Any]:
        if not self.analysis_module:
            raise RuntimeError("该实验没有离线分析器")
        run_dir = Path(run_dir).resolve()
        # 分析阶段不再提供 ETA，显式清空采集阶段留下的剩余时间。
        emit(progress, "analysis", f"分析 {run_dir.name}", 90, estimated_remaining_seconds=None)
        try:
            if cancellation and cancellation.cancelled:
                raise WorkflowCancelled("自动分析已取消，原始采集数据保留")
            self._execute(self.analysis_module, run_dir=run_dir, progress=progress,
                          cancellation=cancellation)
        except WorkflowCancelled as exc:
            self._write_analysis_status(run_dir, status="cancelled", error=str(exc))
            raise
        except Exception as exc:
            self._write_analysis_status(
                run_dir,
                status="failed",
                error=str(exc),
            )
            raise
        self._write_analysis_status(
            run_dir,
            status="completed",
            error=None,
        )
        results = run_dir / "results"
        return {
            "run_dir": str(run_dir),
            "artifacts": [str(path) for path in results.iterdir() if path.is_file()]
            if results.exists() else [],
        }

    def run_cli(self) -> int:
        params = self.defaults()
        errors = params.validate(self.root)
        if errors:
            raise ValueError("；".join(errors))
        self._execute(self.acquisition_module, parameters=params.to_external())
        return 0

    def analyze_cli(self, run_dir: Path | None = None) -> int:
        if not self.analysis_module:
            raise RuntimeError("该实验没有离线分析器")
        if run_dir is None:
            base = self.root / "data" / self.data_type
            candidates = sorted((path for path in base.glob("*") if path.is_dir()), reverse=True)
            if not candidates:
                raise FileNotFoundError(f"未找到数据目录: {base}")
            run_dir = candidates[0]
        self._execute(self.analysis_module, run_dir=run_dir)
        return 0
