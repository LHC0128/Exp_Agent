"""GUI 与命令行共享的实验契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..common import CancellationToken, ProgressCallback


SchemaProvider = Callable[[], dict[str, Any]]
DefaultsSaver = Callable[[dict[str, Any]], None]
PreflightRunner = Callable[[dict[str, Any]], list[str]]
ExperimentRunner = Callable[
    [dict[str, Any], ProgressCallback | None, CancellationToken | None],
    dict[str, Any],
]
AnalysisRunner = Callable[[Path, ProgressCallback | None], dict[str, Any]]


@dataclass(slots=True)
class ExperimentDefinition:
    id: str
    title: str
    category: str
    family: str
    variant: str
    description: str
    data_type: str
    required_devices: tuple[str, ...]
    acquisition_program: str = ""
    analysis_program: str | None = None
    wiring_notes: tuple[str, ...] = ()
    safety_notes: tuple[str, ...] = ()
    schema_version: int = 1
    supports_cancel: bool = True
    auto_analyze: bool = True
    schema_provider: SchemaProvider | None = field(default=None, repr=False)
    defaults_saver: DefaultsSaver | None = field(default=None, repr=False)
    preflight_runner: PreflightRunner | None = field(default=None, repr=False)
    experiment_runner: ExperimentRunner | None = field(default=None, repr=False)
    analysis_runner: AnalysisRunner | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "family": self.family,
            "variant": self.variant,
            "description": self.description,
            "data_type": self.data_type,
            "required_devices": list(self.required_devices),
            "acquisition_program": self.acquisition_program,
            "analysis_program": self.analysis_program,
            "wiring_notes": list(self.wiring_notes),
            "safety_notes": list(self.safety_notes),
            "schema_version": self.schema_version,
            "supports_cancel": self.supports_cancel,
            "auto_analyze": self.auto_analyze and self.analysis_runner is not None,
            "can_analyze": self.analysis_runner is not None,
        }

    def schema(self) -> dict[str, Any]:
        if not self.schema_provider:
            return {"experiment": self.id, "schema_version": self.schema_version, "fields": []}
        schema = self.schema_provider()
        schema["experiment"] = self.id
        schema["schema_version"] = self.schema_version
        return schema

    def save_defaults(self, values: dict[str, Any]) -> None:
        if not self.defaults_saver:
            raise RuntimeError(f"实验 {self.id} 不支持保存默认参数")
        self.defaults_saver(values)

    def preflight(self, values: dict[str, Any]) -> list[str]:
        return self.preflight_runner(values) if self.preflight_runner else []

    def run(
        self,
        values: dict[str, Any],
        progress: ProgressCallback | None = None,
        cancellation: CancellationToken | None = None,
    ) -> dict[str, Any]:
        if not self.experiment_runner:
            raise RuntimeError(f"实验 {self.id} 尚未接入运行工作流")
        return self.experiment_runner(values, progress, cancellation)

    def analyze(
        self,
        run_dir: Path,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if not self.analysis_runner:
            raise RuntimeError(f"实验 {self.id} 没有离线分析器")
        return self.analysis_runner(run_dir, progress)
