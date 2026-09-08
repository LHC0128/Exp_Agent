"""Z 任意波闭环波形校正参数。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from ...common import find_project_root
from ...current_feedback import (
    load_current_coupling_calibration,
    load_current_frequency_response,
    validate_sense_resistor,
)
from ...experiment_params import parameter
from ..z_aw_waveform_scope_check.models import ZAWWaveformScopeCheckParams
from ..mx_z_optimal_control_rf_sensitivity.sources import (
    CONTROL_SOURCE_SET_ROOTS,
    resolve_control_results_root,
)


@dataclass(slots=True)
class ZAWClosedLoopWaveformCorrectionParams(ZAWWaveformScopeCheckParams):
    """用采样电阻反馈迭代更新 Z 任意波。"""

    schema_version = 3

    run_tag: str = parameter(
        default="z_aw_closed_loop_waveform_correction",
        external_name="RUN_TAG",
        label="运行标签",
        group="basic",
    )
    control_source_set: str = parameter(
        default="oc_sens",
        external_name="CONTROL_SOURCE_SET",
        label="最优控制结果集",
        group="basic",
        options=tuple((name, name) for name in CONTROL_SOURCE_SET_ROOTS),
        description="从固定理论结果集中选择任意波来源。",
    )
    correction_method: str = parameter(
        default="time_domain",
        external_name="CORRECTION_METHOD",
        label="闭环校正方法",
        group="basic",
        options=(
            ("frequency_domain", "频域逆滤波"),
            ("time_domain", "时域误差迭代"),
        ),
        description="时域方法直接使用相位对齐后的 target-实测电流误差更新命令。",
    )
    control_results_root: str = parameter(
        default=r"D:\Code\theory_agent\simulate\results\oc_sens",
        external_name="CONTROL_RESULTS_ROOT",
        label="最优控制结果根目录（兼容）",
        group="advanced",
        visible=False,
    )
    current_coupling_calibration_source_run: str = parameter(
        default="",
        external_name="CURRENT_COUPLING_CALIBRATION_SOURCE_RUN",
        label="电流耦合标定来源",
        group="basic",
    )
    current_frequency_response_source_run: str = parameter(
        default="",
        external_name="CURRENT_FREQUENCY_RESPONSE_SOURCE_RUN",
        label="电流频响标定来源",
        group="basic",
    )
    max_iterations: int = parameter(
        default=8,
        external_name="MAX_ITERATIONS",
        label="最大迭代轮数",
        group="basic",
        minimum=1,
    )
    iteration_repeats: int = parameter(
        default=2,
        external_name="ITERATION_REPEATS",
        label="每轮重复采集次数",
        group="basic",
        minimum=1,
    )
    holdout_repeats: int = parameter(
        default=1,
        external_name="HOLDOUT_REPEATS",
        label="每轮独立验证次数",
        group="advanced",
        minimum=1,
    )
    iteration_damping: float = parameter(
        default=0.35,
        external_name="ITERATION_DAMPING",
        label="迭代阻尼",
        group="advanced",
        minimum=0.001,
        maximum=1.0,
    )
    inverse_regularization: float = parameter(
        default=0.02,
        external_name="INVERSE_REGULARIZATION",
        label="逆传递函数相对正则化",
        group="advanced",
        minimum=0.000001,
        maximum=1.0,
        description="相对于可靠频点最大 |H| 的无量纲比例；0.02 表示 2%。",
    )
    target_shape_nrmse: float = parameter(
        default=0.03,
        external_name="TARGET_SHAPE_NRMSE",
        label="目标形状 NRMSE",
        group="basic",
        minimum=0.000001,
    )
    maximum_error_increase_fraction: float = parameter(
        default=0.05,
        external_name="MAXIMUM_ERROR_INCREASE_FRACTION",
        label="允许误差恶化比例",
        group="advanced",
        minimum=0.0,
        maximum=1.0,
    )
    sense_resistor_ohm: float = parameter(
        default=0.0,
        external_name="SENSE_RESISTOR_OHM",
        label="采样电阻实测阻值",
        unit="ohm",
        group="basic",
        minimum=0.0,
    )
    sense_resistor_power_rating_w: float = parameter(
        default=0.0,
        external_name="SENSE_RESISTOR_POWER_RATING_W",
        label="采样电阻额定功率",
        unit="W",
        group="basic",
        minimum=0.0,
    )
    sense_resistor_power_derating: float = parameter(
        default=0.5,
        external_name="SENSE_RESISTOR_POWER_DERATING",
        label="采样电阻功率降额系数",
        group="advanced",
        minimum=0.01,
        maximum=1.0,
    )
    maximum_current_a: float = parameter(
        default=0.0,
        external_name="MAXIMUM_CURRENT_A",
        label="线圈峰值电流安全上限",
        unit="A",
        group="basic",
        minimum=0.0,
    )

    @classmethod
    def migrate_external(
        cls,
        values: dict[str, object],
        schema_version: int,
    ) -> dict[str, object]:
        """迁移旧根目录并保留历史正则化参数语义。"""
        migrated = super(ZAWClosedLoopWaveformCorrectionParams, cls).migrate_external(
            values,
            schema_version,
        )
        if schema_version < 2:
            migrated.setdefault("INVERSE_REGULARIZATION", 0.02)
        if schema_version < 3 and "CONTROL_SOURCE_SET" not in migrated:
            raw_root = str(migrated.get("CONTROL_RESULTS_ROOT", "")).strip()
            normalized = raw_root.replace("\\", "/").rstrip("/").split("/")[-1]
            if not raw_root:
                migrated["CONTROL_SOURCE_SET"] = "oc_sens"
            elif normalized in CONTROL_SOURCE_SET_ROOTS:
                migrated["CONTROL_SOURCE_SET"] = normalized
            else:
                raise ValueError(
                    "旧 CONTROL_RESULTS_ROOT 不是受支持的固定结果集目录，"
                    "请改用 CONTROL_SOURCE_SET=oc_sens 或 oc_broadband_v2"
                )
        return migrated

    @classmethod
    def derive_external(cls, values: dict[str, object]) -> dict[str, object]:
        """按结果集选择加载理论周期，供 GUI 派生字段使用。"""
        merged = dict(values)
        try:
            merged["CONTROL_RESULTS_ROOT"] = str(
                resolve_control_results_root(str(merged.get("CONTROL_SOURCE_SET", "oc_sens")))
            )
        except ValueError:
            return {}
        return super().derive_external(merged)

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if self.correction_method not in {"frequency_domain", "time_domain"}:
            errors.append("CORRECTION_METHOD 必须是 frequency_domain 或 time_domain")
        try:
            resolved_root = resolve_control_results_root(self.control_source_set)
        except ValueError as exc:
            errors.append(str(exc))
            resolved_root = Path(self.control_results_root)
        effective = replace(self, control_results_root=str(resolved_root))
        errors.extend(ZAWWaveformScopeCheckParams.validate_model(effective))
        if self.scope_measured_channel != 3:
            errors.append("SCOPE_MEASURED_CHANNEL 必须固定为 CH3 采样电阻电压")
        if self.scope_trigger_channel != 4:
            errors.append("SCOPE_TRIGGER_CHANNEL 必须固定为 CH4 共同触发")
        if not self.current_coupling_calibration_source_run.strip():
            errors.append("CURRENT_COUPLING_CALIBRATION_SOURCE_RUN 不能为空")
        if not self.current_frequency_response_source_run.strip():
            errors.append("CURRENT_FREQUENCY_RESPONSE_SOURCE_RUN 不能为空")
        try:
            validate_sense_resistor(self.sense_resistor_ohm, self.sense_resistor_power_rating_w)
        except ValueError as exc:
            errors.append(str(exc))
        if self.maximum_current_a <= 0.0:
            errors.append("MAXIMUM_CURRENT_A 必须填写正安全上限")
        root = find_project_root()
        if self.current_coupling_calibration_source_run.strip():
            try:
                calibration = load_current_coupling_calibration(
                    root, self.current_coupling_calibration_source_run
                )
                if not np.isclose(
                    calibration.sense_resistor_ohm,
                    self.sense_resistor_ohm,
                    rtol=1e-6,
                    atol=1e-12,
                ):
                    errors.append("采样电阻与电流耦合标定来源不一致")
            except (OSError, TypeError, ValueError, KeyError) as exc:
                errors.append(str(exc))
        if self.current_frequency_response_source_run.strip():
            try:
                response = load_current_frequency_response(
                    root, self.current_frequency_response_source_run
                )
                if not np.isclose(
                    response.sense_resistor_ohm,
                    self.sense_resistor_ohm,
                    rtol=1e-6,
                    atol=1e-12,
                ):
                    errors.append("采样电阻与实际电流频响来源不一致")
            except (OSError, TypeError, ValueError, KeyError) as exc:
                errors.append(str(exc))
        return errors
