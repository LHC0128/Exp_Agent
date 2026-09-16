"""Mx Z 最优控制 RF 灵敏度实验参数。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ...common import find_project_root, validate_safety_limit
from ...current_feedback import load_corrected_control_waveform
from ...experiment_params import parameter
from ..mx_z_control_params import CommonMxZControlParams


@dataclass(slots=True)
class MxZOptimalControlRFParams(CommonMxZControlParams):
    """配置可选 GS200 主场，并叠加 Z 周期控制测量 Y RF 灵敏度。"""

    schema_version = 5

    corrected_control_source_run: str = parameter(
        default="osens20k",
        external_name="CORRECTED_CONTROL_SOURCE_RUN",
        label="闭环校正运行",
        group="basic",
        description="读取指定闭环校正运行的冻结波形。",
    )

    @classmethod
    def from_external(
        cls,
        values: dict[str, Any],
        *,
        schema_version: int = 5,
        strict: bool = True,
    ) -> "MxZOptimalControlRFParams":
        # GUI 和运行子进程传递无版本的参数字典，默认解释为当前版本；
        # YAML 和历史运行读取时传入文件中记录的版本。
        return super(MxZOptimalControlRFParams, cls).from_external(
            values,
            schema_version=schema_version,
            strict=strict,
        )

    @classmethod
    def migrate_external(
        cls,
        values: dict[str, Any],
        schema_version: int,
    ) -> dict[str, Any]:
        """本实验只接受 v5 配置，不迁移旧理论或旧闭环配置。

        遗留的来源参数不在这里删除，未知参数由 ``from_external``
        统一拒绝。
        """
        if schema_version != 5:
            raise ValueError("本实验仅支持 schema v5，请按当前参数重新配置")
        return dict(values)

    def validate_model(self) -> list[str]:
        errors = CommonMxZControlParams.validate_model(self)
        root = find_project_root()
        try:
            control = load_corrected_control_waveform(
                root,
                self.corrected_control_source_run,
            )
            for value in (
                float(np.min(control.voltage_v)),
                float(np.max(control.voltage_v)),
                control.offset_v - control.amplitude_vpp / 2.0,
                control.offset_v + control.amplitude_vpp / 2.0,
            ):
                validate_safety_limit("Z_magnetic_field", value)
        except (OSError, TypeError, ValueError, KeyError) as exc:
            errors.append(str(exc))
        return errors
