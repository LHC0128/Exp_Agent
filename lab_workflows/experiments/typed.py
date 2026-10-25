"""显式参数模型驱动的实验适配器，不扫描或改写实验源码。"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from queue import Empty, Queue
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
from ..steps.run_directory import write_run_config


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
            from ..lockin_compatibility import lockin_preflight
            from ..instrument_config import validate_mapping_usage
            from .requirements import TYPED_MAPPING_REQUIREMENTS
            validate_mapping_usage(TYPED_MAPPING_REQUIREMENTS.get(self.id, ()), self.root)
            params = self._resolved(values)
            return params.validate(self.root) + lockin_preflight(self.id, params.to_external(), self.root)
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
        commands: Queue | None = None,
    ) -> Path | None:
        reported_runs: list[Path] = []
        job_dir = self.root / "data" / ".gui_jobs"
        job_dir.mkdir(parents=True, exist_ok=True)
        token = f"{self.id}_{os.getpid()}_{threading.get_ident()}"
        cancel_path = job_dir / f"{token}.cancel"
        environment = os.environ.copy()
        # 每次调用只使用显式传入的目录和参数，不继承上一次子任务的上下文。
        environment.pop("LAB_RUN_DIR", None)
        environment.pop("LAB_TYPED_PARAMETERS", None)
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
        # 无 GUI 回调时也需要读取显式运行结果，不能退回目录猜测。
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
            stdin=subprocess.PIPE if commands is not None else None,
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

        def send_commands() -> None:
            try:
                while process.poll() is None:
                    try:
                        payload = commands.get(timeout=0.1)
                    except Empty:
                        continue
                    if payload is None:
                        break
                    process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                    process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                cancel_path.touch(exist_ok=True)
            finally:
                process.stdin.close()

        if commands is not None:
            threading.Thread(target=send_commands, daemon=True).start()
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
                    if "run_directory" in event.data:
                        record = event.data["run_directory"]
                        if (not isinstance(record, dict) or not isinstance(record.get("path"), str)
                                or not record["path"] or not isinstance(record.get("data_type"), str)):
                            raise RuntimeError("子进程上报了无效的运行目录")
                        if record["data_type"] == self.data_type:
                            path = Path(record["path"])
                            if path not in reported_runs:
                                reported_runs.append(path)
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
            # 回调或父通信失败时，先让子进程收尾，再允许调用者释放硬件锁。
            if process.poll() is None:
                cancel_path.touch(exist_ok=True)
                if commands is not None:
                    commands.put(None)
                process.wait()
            process.stdout.close()
            cancel_path.unlink(missing_ok=True)
        if cancellation and cancellation.cancelled:
            raise WorkflowCancelled("实验已在安全检查点停止")
        if return_code:
            raise RuntimeError(f"模块 {module} 异常结束，退出码 {return_code}")
        if commands is None and run_dir is None and len(reported_runs) > 1:
            # 父子实验可以共用 data_type；只在显式上报的目录中按稳定 ID 选唯一父运行。
            candidates = set()
            for path in reported_runs:
                try:
                    candidates.add(self._validated_run_directory(path))
                except (RuntimeError, OSError, yaml.YAMLError):
                    continue
            if len(candidates) != 1:
                raise RuntimeError("子进程未上报唯一有效的本次运行目录；不会猜测或回退历史数据")
            return candidates.pop()
        return reported_runs[-1] if reported_runs else None

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

    def _validated_run_directory(self, run_dir: Path | None) -> Path:
        if run_dir is None:
            raise RuntimeError("采集完成后子进程未上报本次运行目录；不会回退分析历史数据")
        path = Path(run_dir)
        if not path.is_absolute():
            raise RuntimeError("子进程上报的运行目录必须是绝对路径")
        path = path.resolve()
        base = (self.root / "data" / self.data_type).resolve()
        if path.parent != base or not path.is_dir():
            raise RuntimeError(f"子进程上报的运行目录不属于本实验: {path}")
        config_path = path / "experiment_config.yaml"
        if not config_path.is_file():
            raise RuntimeError(f"本次运行目录缺少 experiment_config.yaml: {path}")
        with config_path.open(encoding="utf-8") as stream:
            config = yaml.safe_load(stream)
        if (not isinstance(config, dict) or config.get("experiment_id") not in (None, self.id)
                or config.get("data_type", config.get("experiment_type")) != self.data_type):
            raise RuntimeError(f"本次运行配置与实验身份不匹配: {path}")
        return path

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
                config = yaml.safe_load(stream)
            if not isinstance(config, dict):
                return
            config["analysis_status"] = status
            config["analysis_error"] = error
            write_run_config(config_path, config)
        except Exception:
            # 分析状态是诊断信息，不能覆盖原始分析异常。
            logging.getLogger(__name__).exception("分析状态保存失败，原配置保留: %s", config_path)
            return

    def run(
        self,
        values: dict[str, Any],
        progress: ProgressCallback | None,
        cancellation: CancellationToken | None,
    ) -> dict[str, Any]:
        params = self._resolved(values)
        errors = self.preflight(params.to_external())
        if errors:
            raise ValueError("；".join(errors))
        emit(progress, "start", f"启动 {self.id}", 0)
        reported_run = self._execute(
            self.acquisition_module,
            parameters=params.to_external(),
            progress=progress,
            cancellation=cancellation,
        )
        run_dir = self._validated_run_directory(reported_run)
        if self.analysis_module:
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
        errors = self.preflight(params.to_external())
        if errors:
            raise ValueError("；".join(errors))
        run_dir = self._execute(self.acquisition_module, parameters=params.to_external())
        self._validated_run_directory(run_dir)
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
