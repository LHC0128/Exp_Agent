"""GUI 与命令行共享的实验契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from ..common import CancellationToken, ProgressCallback
from .requirements import (
    ARBITRARY_MAPPING_KEYS,
    INFINITE_BURST_MAPPING_KEYS,
    MAPPING_ENDPOINT_FIELDS,
    MAPPING_INSTRUMENTS,
    TYPED_MAPPING_REQUIREMENTS,
)


# Z 控制与触发必须同机且固定为 DG4000 的实验；生产预检与测试共同引用。
DG4000_ONLY_EXPERIMENT_IDS = frozenset({
    "z-aw-waveform-scope-check",
    "z-coil-inductance-frequency-response",
    "z-aw-current-waveform-scope-check",
    "z-aw-closed-loop-waveform-correction",
    "z-coil-current-frequency-response",
    "mx-z-optimal-control-dg4000-bias-xyz-balance",
    "mx-z-optimal-control-xy-rf-phase-response",
    "mx-z-optimal-control-xy-rf-sensitivity",
    "mx-z-optimal-control-xy-noise-spectrum",
})

SchemaProvider = Callable[[], dict[str, Any]]
DefaultsSaver = Callable[[dict[str, Any]], None]
PreflightRunner = Callable[[dict[str, Any]], list[str]]
DeriveRunner = Callable[[dict[str, Any]], dict[str, Any]]
ExperimentRunner = Callable[
    [dict[str, Any], ProgressCallback | None, CancellationToken | None],
    dict[str, Any],
]
AnalysisRunner = Callable[[Path, ProgressCallback | None], dict[str, Any]]
ExecutionMode = Literal["typed_workflow", "legacy_script"]


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
    required_mapping_keys: tuple[str, ...] = ()
    execution_mode: ExecutionMode = "typed_workflow"
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
    derive_runner: DeriveRunner | None = field(default=None, repr=False)
    experiment_runner: ExperimentRunner | None = field(default=None, repr=False)
    analysis_runner: AnalysisRunner | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.execution_mode == "typed_workflow" and not self.required_mapping_keys:
            self.required_mapping_keys = TYPED_MAPPING_REQUIREMENTS.get(self.id, ())

    def _mapping_errors(self) -> list[str]:
        if self.execution_mode != "typed_workflow":
            return []
        from ..common import load_mapping

        try:
            mapping = load_mapping()
        except Exception as exc:
            return [f"设备映射配置无效: {exc}"]
        missing = [key for key in self.required_mapping_keys if key not in mapping]
        errors = [f"实验缺少物理量映射: {key}" for key in missing]
        for key in self.required_mapping_keys:
            config = mapping.get(key)
            if not config:
                continue
            expected_instrument = MAPPING_INSTRUMENTS.get(
                key, "signal_generator"
            )
            actual_instrument = str(config.get("instrument", ""))
            if actual_instrument != expected_instrument:
                errors.append(
                    f"{key} 需要 {expected_instrument}，当前设备类型为 "
                    f"{actual_instrument or '未知'}"
                )
            endpoint_field = MAPPING_ENDPOINT_FIELDS.get(
                key,
                "channel" if expected_instrument == "signal_generator" else None,
            )
            if endpoint_field and config.get(endpoint_field) is None:
                errors.append(f"{key} 缺少所需端点 {endpoint_field}")
        for key in ARBITRARY_MAPPING_KEYS.get(self.id, ()):
            config = mapping.get(key, {})
            if not config.get("capabilities", {}).get("supports_arbitrary", False):
                errors.append(f"{key} 当前设备缺少任意波能力 supports_arbitrary")
        for key in INFINITE_BURST_MAPPING_KEYS.get(self.id, ()):
            config = mapping.get(key, {})
            if not config.get("capabilities", {}).get(
                "supports_infinite_burst", False
            ):
                errors.append(
                    f"{key} 当前设备缺少无限 Burst 能力 supports_infinite_burst"
                )
        if self.id in DG4000_ONLY_EXPERIMENT_IDS:
            z_config = mapping.get("Z_magnetic_field", {})
            trigger_config = mapping.get("Time_sequence_2", {})
            z_device = str(
                z_config.get("device_id") or z_config.get("resource") or ""
            )
            trigger_device = str(
                trigger_config.get("device_id")
                or trigger_config.get("resource")
                or ""
            )
            z_resource = str(z_config.get("resource") or "")
            trigger_resource = str(trigger_config.get("resource") or "")
            if (
                z_device
                and trigger_device
                and z_device != trigger_device
            ) or (
                z_resource
                and trigger_resource
                and z_resource != trigger_resource
            ):
                errors.append("Z_magnetic_field 与 Time_sequence_2 必须位于同一台 DG4000")
            for key in ("Z_magnetic_field", "Time_sequence_2"):
                model = str(mapping.get(key, {}).get("model") or "")
                if model and model.upper() != "DG4000":
                    errors.append(f"{key} 必须连接 DG4000，当前为 {model}")
            if self.id in {
                "z-aw-waveform-scope-check",
                "z-coil-inductance-frequency-response",
                "z-aw-current-waveform-scope-check",
                "z-aw-closed-loop-waveform-correction",
                "z-coil-current-frequency-response",
            } and not str(mapping.get("scope_waveform", {}).get("resource") or ""):
                errors.append("scope_waveform 缺少示波器 resource")
            z_channel = z_config.get("channel")
            trigger_channel = trigger_config.get("channel")
            if (
                z_channel is not None
                and trigger_channel is not None
                and int(z_channel) == int(trigger_channel)
            ):
                errors.append("Z_magnetic_field 与 Time_sequence_2 必须使用不同 DG 通道")
        return errors

    def _public_required_devices(self) -> list[str]:
        if not self.required_mapping_keys:
            return list(self.required_devices)
        from ..common import load_mapping

        try:
            mapping = load_mapping()
        except Exception:
            return list(self.required_devices)
        result: list[str] = []
        seen: set[str] = set()
        for key in self.required_mapping_keys:
            config = mapping.get(key, {})
            marker = str(
                config.get("device_id") or config.get("resource") or key
            )
            if marker in seen:
                continue
            seen.add(marker)
            label = str(config.get("label", key))
            model = str(config.get("model", config.get("instrument", "")))
            result.append(f"{label} ({model})" if model else label)
        return result

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "family": self.family,
            "variant": self.variant,
            "description": self.description,
            "data_type": self.data_type,
            "required_devices": self._public_required_devices(),
            "required_mapping_keys": list(self.required_mapping_keys),
            "execution_mode": self.execution_mode,
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
        errors = self._mapping_errors()
        if self.preflight_runner:
            errors.extend(self.preflight_runner(values))
        return errors

    def derive(self, values: dict[str, Any]) -> dict[str, Any]:
        if not self.derive_runner:
            return {}
        return self.derive_runner(values)

    def run(
        self,
        values: dict[str, Any],
        progress: ProgressCallback | None = None,
        cancellation: CancellationToken | None = None,
    ) -> dict[str, Any]:
        if not self.experiment_runner:
            raise RuntimeError(f"实验 {self.id} 尚未接入运行工作流")
        mapping_errors = self._mapping_errors()
        if mapping_errors:
            raise ValueError("；".join(mapping_errors))
        return self.experiment_runner(values, progress, cancellation)

    def analyze(
        self,
        run_dir: Path,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if not self.analysis_runner:
            raise RuntimeError(f"实验 {self.id} 没有离线分析器")
        return self.analysis_runner(run_dir, progress)
