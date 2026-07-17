"""将现有 Python 实验安全接入统一契约的兼容适配器。"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

import numpy as np
import yaml

from ..common import (
    CancellationToken,
    ProgressCallback,
    WorkflowCancelled,
    emit,
    find_project_root,
    load_safety_limits,
    validate_safety_limit,
)


_EXCLUDED_CONSTANTS = {
    "MAPPING", "LIMITS", "PROJECT_ROOT", "EXPERIMENT_TYPE", "PURPOSE",
    "DATA_DIR", "USE_LATEST", "QUALITY_THRESHOLDS",
}
_BASIC_HINTS = (
    "POWER", "TEMPERATURE", "MAGNETIC", "FIELD", "FREQ", "AMPLITUDE",
    "DUTY", "DURATION", "SCAN", "RAMP", "RUN_TAG", "N_AVG",
)

_NUMPY_SEQUENCE_BUILDERS = {
    "array": np.asarray,
    "arange": np.arange,
    "linspace": np.linspace,
}


def _safe_numpy_sequence(node: ast.AST) -> list[Any] | None:
    """只解析参数均为字面量的 NumPy 一维序列构造。"""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if not isinstance(node.func.value, ast.Name) or node.func.value.id != "np":
        return None
    builder = _NUMPY_SEQUENCE_BUILDERS.get(node.func.attr)
    if builder is None:
        return None
    try:
        args = [ast.literal_eval(arg) for arg in node.args]
        kwargs = {
            keyword.arg: ast.literal_eval(keyword.value)
            for keyword in node.keywords
            if keyword.arg is not None
        }
        value = np.asarray(builder(*args, **kwargs))
    except (TypeError, ValueError, OverflowError):
        return None
    if value.ndim != 1 or value.size == 0:
        return None
    result = value.tolist()
    if not all(isinstance(item, (str, int, float, bool)) for item in result):
        return None
    return result


def _literal_assignments(script: Path) -> dict[str, Any]:
    tree = ast.parse(script.read_text(encoding="utf-8-sig"), filename=str(script))
    values: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if len(node.targets) == 1 else None
        )
        value_node = node.value
        if not isinstance(target, ast.Name) or not target.id.isupper():
            continue
        if target.id.startswith("_") or target.id in _EXCLUDED_CONSTANTS or value_node is None:
            continue
        try:
            value = ast.literal_eval(value_node)
        except (ValueError, TypeError):
            value = _safe_numpy_sequence(value_node)
            if value is None:
                continue
        if isinstance(value, (str, int, float, bool, type(None))):
            values[target.id] = value
        elif isinstance(value, (list, tuple)) and all(
            isinstance(item, (str, int, float, bool)) for item in value
        ):
            values[target.id] = list(value)
        elif target.id == "FIXED_PARAMS" and isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str) and isinstance(item, (str, int, float, bool)):
                    values[f"FIXED_PARAMS.{key}"] = item
    return values


def _field_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    return "string"


class LegacyScriptAdapter:
    def __init__(
        self,
        experiment_id: str,
        script_name: str,
        data_type: str,
        plot_name: str | None = None,
        field_metadata: dict[str, dict[str, Any]] | None = None,
        preflight_validator: Callable[[dict[str, Any]], list[str]] | None = None,
    ) -> None:
        root = find_project_root()
        self.root = root
        self.id = experiment_id
        self.script = root / "experiments" / script_name
        self.plot = root / "experiments" / plot_name if plot_name else None
        self.data_type = data_type
        self.defaults_path = root / "params" / "experiments" / f"{experiment_id}.yaml"
        self.field_metadata = field_metadata or {}
        self.preflight_validator = preflight_validator

    def _field_options(self, metadata: dict[str, Any]) -> list[Any]:
        """返回静态选项，或实时扫描仓库内指定目录生成文件选项。"""
        if "options" in metadata:
            return metadata["options"]
        source = metadata.get("options_from_directory")
        if not source:
            return []
        directory = (self.root / source).resolve()
        if directory != self.root and self.root not in directory.parents:
            raise ValueError(f"选项目录必须位于项目内: {source}")
        pattern = metadata.get("options_pattern", "*")
        return [
            {"value": path.name, "label": path.name}
            for path in sorted(directory.glob(pattern), key=lambda item: item.name.lower())
            if path.is_file()
        ] if directory.is_dir() else []

    def defaults(self) -> dict[str, Any]:
        values = _literal_assignments(self.script)
        if self.defaults_path.exists():
            with self.defaults_path.open(encoding="utf-8") as stream:
                saved = yaml.safe_load(stream) or {}
            values.update(saved.get("parameters", saved))
        return values

    def schema(self) -> dict[str, Any]:
        values = self.defaults()
        limits = load_safety_limits()
        fields = []
        for name, value in values.items():
            safety_key = name.removeprefix("FIXED_PARAMS.")
            rule = limits.get(safety_key, {})
            metadata = self.field_metadata.get(name, {})
            if metadata.get("hidden"):
                continue
            field = {
                "name": name,
                "label": metadata.get("label", safety_key.replace("_", " ")),
                "type": _field_type(value),
                "default": value,
                "unit": metadata.get("unit", ""),
                "group": metadata.get(
                    "group",
                    "basic" if any(hint in name for hint in _BASIC_HINTS) else "advanced",
                ),
                "minimum": metadata.get("minimum", rule.get("min")),
                "maximum": metadata.get("maximum", rule.get("max")),
                "description": metadata.get(
                    "description", rule.get("description", "")
                ),
            }
            options = self._field_options(metadata)
            if options or "options" in metadata or "options_from_directory" in metadata:
                field["options"] = options
            fields.append(field)
        return {"experiment": self.id, "fields": fields}

    def save_defaults(self, values: dict[str, Any]) -> None:
        errors = self.preflight(values)
        if errors:
            raise ValueError("；".join(errors))
        self.defaults_path.parent.mkdir(parents=True, exist_ok=True)
        with self.defaults_path.open("w", encoding="utf-8") as stream:
            yaml.safe_dump(
                {"schema_version": 1, "parameters": values},
                stream,
                allow_unicode=True,
                sort_keys=False,
            )

    def preflight(self, values: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        defaults = self.defaults()
        unknown = sorted(set(values) - set(defaults))
        if unknown:
            errors.append(f"未知参数: {unknown}")
        limits = load_safety_limits()
        for name, value in values.items():
            metadata = self.field_metadata.get(name, {})
            options = self._field_options(metadata)
            allowed = [
                option.get("value") if isinstance(option, dict) else option
                for option in options
            ]
            if "options_from_directory" in metadata and not allowed:
                errors.append(
                    f"{metadata.get('label', name)}没有可选文件，请检查目录"
                )
            elif allowed and value not in allowed:
                errors.append(
                    f"{metadata.get('label', name)}必须是 {', '.join(map(str, allowed))}"
                )
            safety_key = name.removeprefix("FIXED_PARAMS.")
            if safety_key not in limits or not isinstance(value, (int, float)):
                continue
            try:
                validate_safety_limit(safety_key, float(value), limits)
            except ValueError as exc:
                errors.append(str(exc))
        if self.preflight_validator:
            resolved = dict(defaults)
            resolved.update(values)
            errors.extend(self.preflight_validator(resolved))
        return errors

    @staticmethod
    def _nested_overrides(values: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
        result = {key: value for key, value in values.items() if "." not in key}
        fixed = {
            key.split(".", 1)[1]: value
            for key, value in defaults.items()
            if key.startswith("FIXED_PARAMS.")
        }
        fixed.update({
            key.split(".", 1)[1]: value
            for key, value in values.items()
            if key.startswith("FIXED_PARAMS.")
        })
        if fixed:
            result["FIXED_PARAMS"] = fixed
        return result

    def _execute(
        self,
        script: Path,
        overrides: dict[str, Any],
        progress: ProgressCallback | None,
        cancellation: CancellationToken | None = None,
    ) -> None:
        root = find_project_root()
        job_dir = root / "data" / ".gui_jobs"
        job_dir.mkdir(parents=True, exist_ok=True)
        token = f"{self.id}_{os.getpid()}_{threading.get_ident()}"
        overrides_path = job_dir / f"{token}.json"
        cancel_path = job_dir / f"{token}.cancel"
        overrides_path.write_text(json.dumps(overrides, ensure_ascii=False), encoding="utf-8")
        environment = os.environ.copy()
        environment.update({
            "MPLBACKEND": "Agg",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        })
        command = [
            str(root / "agent_exp_env" / "Scripts" / "python.exe"),
            "-u", "-m", "lab_workflows.script_runner",
            "--script", str(script),
            "--overrides", str(overrides_path),
            "--cancel-file", str(cancel_path),
        ]
        process = subprocess.Popen(
            command,
            cwd=root,
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
                if message:
                    emit(progress, "running", message)
            return_code = process.wait()
        finally:
            overrides_path.unlink(missing_ok=True)
            cancel_path.unlink(missing_ok=True)
        if cancellation and cancellation.cancelled:
            raise WorkflowCancelled("实验已在安全检查点停止")
        if return_code:
            raise RuntimeError(f"脚本 {script.name} 异常结束，退出码 {return_code}")

    def _latest_run(self, before: set[Path]) -> Path | None:
        base = find_project_root() / "data" / self.data_type
        if not base.exists():
            return None
        runs = [item for item in base.iterdir() if item.is_dir()]
        created = [item for item in runs if item not in before]
        candidates = created or runs
        return max(candidates, key=lambda item: item.stat().st_mtime) if candidates else None

    def run(
        self,
        values: dict[str, Any],
        progress: ProgressCallback | None,
        cancellation: CancellationToken | None,
    ) -> dict[str, Any]:
        errors = self.preflight(values)
        if errors:
            raise ValueError("；".join(errors))
        base = find_project_root() / "data" / self.data_type
        before = set(base.iterdir()) if base.exists() else set()
        overrides = self._nested_overrides(values, self.defaults())
        emit(progress, "start", f"启动 {self.script.name}", 0)
        self._execute(self.script, overrides, progress, cancellation)
        run_dir = self._latest_run(before)
        analysis_error = None
        if self.plot and run_dir:
            try:
                self.analyze(run_dir, progress)
            except Exception as exc:
                analysis_error = str(exc)
                emit(progress, "analysis", f"自动分析失败: {exc}", 95, "warning")
        artifacts = []
        if run_dir and (run_dir / "results").exists():
            artifacts = [str(item) for item in (run_dir / "results").iterdir() if item.is_file()]
        return {
            "experiment_id": self.id,
            "run_dir": str(run_dir) if run_dir else None,
            "artifacts": artifacts,
            "analysis_error": analysis_error,
        }

    def analyze(
        self,
        run_dir: Path,
        progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        if not self.plot:
            raise RuntimeError("该实验没有离线分析脚本")
        emit(progress, "analysis", f"分析 {run_dir.name}", 90)
        self._execute(
            self.plot,
            {"USE_LATEST": False, "DATA_DIR": str(run_dir), "RUN_DIR": str(run_dir)},
            progress,
        )
        results = run_dir / "results"
        return {
            "run_dir": str(run_dir),
            "artifacts": [str(item) for item in results.iterdir() if item.is_file()]
            if results.exists() else [],
        }
