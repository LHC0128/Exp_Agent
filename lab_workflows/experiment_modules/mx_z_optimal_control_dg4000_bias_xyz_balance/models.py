"""Mx Z 最优控制 DG4000 偏置 XYZ 平衡实验参数。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...common import find_project_root, validate_safety_limit
from ...experiment_params import parameter
from ...current_feedback import load_corrected_control_waveform
from ..mx_z_optimal_control_xyz_balance.models import (
    MxZOptimalControlXYZBalanceParams,
)
from ...control_sources import (
    build_applied_control,
    corrected_control_contract,
    load_theory_control,
    load_z_calibration,
)


@dataclass(slots=True)
class MxZOptimalControlDG4000BiasXYZBalanceParams(
    MxZOptimalControlXYZBalanceParams
):
    """固定 Z 周期最优控制并扫描 DG4000 额外 Z 偏置。"""

    schema_version = 2

    run_tag: str = parameter(
        default="mx_z_optimal_control_dg4000_bias_xyz_balance",
        external_name="RUN_TAG",
        label="运行标签",
        group="basic",
    )
    z_field_start_ma: float = parameter(
        default=-0.05,
        external_name="Z_BIAS_START_V",
        label="DG4000 Z 偏置起点",
        unit="V",
        group="basic",
        safety_key="Z_magnetic_field",
    )
    z_field_stop_ma: float = parameter(
        default=0.05,
        external_name="Z_BIAS_STOP_V",
        label="DG4000 Z 偏置终点",
        unit="V",
        group="basic",
        safety_key="Z_magnetic_field",
    )
    z_field_points: int = parameter(
        default=11,
        external_name="Z_BIAS_POINTS",
        label="DG4000 Z 偏置点数",
        group="basic",
        minimum=1,
    )
    control_waveform_source: str = parameter(
        default="corrected_run",
        external_name="CONTROL_WAVEFORM_SOURCE",
        label="控制波形来源",
        group="basic",
        options=(("theory", "理论换算"), ("corrected_run", "闭环冻结波形")),
    )
    corrected_control_source_run: str = parameter(
        default="0824_094157_z_aw_closed_loop_waveform_correction",
        external_name="CORRECTED_CONTROL_SOURCE_RUN",
        label="闭环校正运行",
        group="basic",
        description="CONTROL_WAVEFORM_SOURCE=corrected_run 时使用的闭环冻结波形运行。",
    )
    confirm_gs200_disconnected: bool = parameter(
        default=False,
        external_name="CONFIRM_GS200_DISCONNECTED",
        label="确认 GS200 已与 Z 线圈断开",
        group="basic",
        description=(
            "该实验使用 DG4000 给同一 Z 线圈提供偏置；运行前必须物理断开 GS200。"
        ),
    )
    bz_gamma_hz_per_nt: float = parameter(
        default=7.0,
        external_name="BZ_GAMMA_HZ_PER_NT",
        label="Z 场回报换算系数",
        unit="Hz/nT",
        group="advanced",
        minimum=1e-12,
        visible=False,
    )

    @property
    def z_bias_start_v(self) -> float:
        return float(self.z_field_start_ma)

    @property
    def z_bias_stop_v(self) -> float:
        return float(self.z_field_stop_ma)

    @property
    def z_bias_points(self) -> int:
        return int(self.z_field_points)

    @staticmethod
    def _validate_axis(
        name: str,
        start: float,
        stop: float,
        points: int,
    ) -> list[str]:
        if points == 1:
            return [] if start == stop else [f"{name} 点数为 1 时必须满足 START=STOP"]
        return [] if start < stop else [f"{name} 点数大于 1 时必须满足 START<STOP"]

    def _validate_z_envelope(self) -> list[str]:
        errors: list[str] = []
        root = find_project_root()
        try:
            calibration = load_z_calibration(root, self.z_calibration_source_run)
            if self.control_waveform_source == "corrected_run":
                corrected = load_corrected_control_waveform(
                    root,
                    self.corrected_control_source_run,
                )
                _, applied = corrected_control_contract(corrected)
            else:
                theory = load_theory_control(
                    Path(self.control_results_root),
                    self.control_version,
                )
                applied = build_applied_control(
                    theory,
                    calibration,
                    self.control_scale,
                    output_vpp=self.z_aw_output_vpp,
                    output_offset_v=self.z_aw_output_offset_v,
                )
            for bias in (self.z_bias_start_v, self.z_bias_stop_v):
                output_vpp = float(applied.amplitude_vpp)
                for value in (
                    float(applied.minimum_v) + bias,
                    float(applied.maximum_v) + bias,
                    float(applied.offset_v) + bias - output_vpp / 2.0,
                    float(applied.offset_v) + bias + output_vpp / 2.0,
                ):
                    validate_safety_limit("Z_magnetic_field", value)
        except (OSError, TypeError, ValueError, KeyError) as exc:
            errors.append(str(exc))
        return errors

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        if not self.confirm_gs200_disconnected:
            errors.append("CONFIRM_GS200_DISCONNECTED 必须为 true")
        for name, start, stop, points in (
            ("X", self.x_field_start_v, self.x_field_stop_v, self.x_field_points),
            ("Y", self.y_field_start_v, self.y_field_stop_v, self.y_field_points),
            ("Z 偏置", self.z_bias_start_v, self.z_bias_stop_v, self.z_bias_points),
        ):
            errors.extend(self._validate_axis(name, start, stop, points))
        for safety_key, values in (
            ("X_magnetic_field", (self.x_field_start_v, self.x_field_stop_v)),
            ("Y_magnetic_field", (self.y_field_start_v, self.y_field_stop_v)),
        ):
            for value in values:
                try:
                    validate_safety_limit(safety_key, value)
                except ValueError as exc:
                    errors.append(str(exc))
        if self.control_waveform_source not in {"theory", "corrected_run"}:
            errors.append("CONTROL_WAVEFORM_SOURCE 必须是 theory 或 corrected_run")
        if self.control_waveform_source == "theory" and self.control_scale <= 0:
            errors.append("CONTROL_SCALE 必须大于 0")
        trigger_low = self.trigger_offset_v - self.trigger_amplitude_vpp / 2.0
        trigger_high = self.trigger_offset_v + self.trigger_amplitude_vpp / 2.0
        for value in (trigger_low, trigger_high):
            try:
                validate_safety_limit("Time_sequence_2", value)
            except ValueError as exc:
                errors.append(str(exc))
        errors.extend(self._validate_z_envelope())
        return errors

    @classmethod
    def migrate_external(
        cls,
        values: dict[str, object],
        schema_version: int,
    ) -> dict[str, object]:
        """旧 v1 配置保持原有理论波形语义。"""
        migrated = super(
            MxZOptimalControlDG4000BiasXYZBalanceParams,
            cls,
        ).migrate_external(values, schema_version)
        if schema_version < 2:
            migrated.setdefault("CONTROL_WAVEFORM_SOURCE", "theory")
            migrated.setdefault("CORRECTED_CONTROL_SOURCE_RUN", "")
        return migrated
